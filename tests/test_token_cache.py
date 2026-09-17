from __future__ import annotations

import json
import time

from datamermaid.auth import TokenCache, TokenSet, default_cache_path
from datamermaid.auth.jwt import decode_payload, token_expires_at
from datamermaid.auth.token_cache import CACHE_FILE_MODE

from .conftest import make_jwt

KEY = "datamermaid.auth0.com|client|https://api.datamermaid.org"


def test_default_path_follows_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_cache_path() == tmp_path / "datamermaid" / "tokens.json"


def test_default_path_falls_back_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    assert default_cache_path() == tmp_path / ".config" / "datamermaid" / "tokens.json"


def test_round_trip(tmp_path):
    cache = TokenCache(tmp_path / "tokens.json")
    tokens = TokenSet(access_token="a", refresh_token="r", expires_at=123.0, scope="openid")
    cache.save(KEY, tokens)
    assert cache.load(KEY) == tokens


def test_saved_file_is_only_readable_by_the_owner(tmp_path):
    cache = TokenCache(tmp_path / "nested" / "tokens.json")
    cache.save(KEY, TokenSet(access_token="a"))
    assert cache.path.stat().st_mode & 0o777 == CACHE_FILE_MODE


def test_permissions_are_tightened_on_a_pre_existing_file(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text("{}")
    path.chmod(0o644)
    TokenCache(path).save(KEY, TokenSet(access_token="a"))
    assert path.stat().st_mode & 0o777 == CACHE_FILE_MODE


def test_entries_for_other_tenants_are_preserved(tmp_path):
    cache = TokenCache(tmp_path / "tokens.json")
    cache.save(KEY, TokenSet(access_token="production"))
    cache.save("other", TokenSet(access_token="development"))
    assert cache.load(KEY).access_token == "production"
    assert cache.load("other").access_token == "development"


def test_missing_and_unknown_entries_load_as_none(tmp_path):
    cache = TokenCache(tmp_path / "tokens.json")
    assert cache.load(KEY) is None
    cache.save("other", TokenSet(access_token="x"))
    assert cache.load(KEY) is None


def test_a_corrupt_cache_is_ignored_rather_than_raised(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text("not json at all")
    cache = TokenCache(path)
    assert cache.load(KEY) is None
    cache.save(KEY, TokenSet(access_token="a"))
    assert cache.load(KEY).access_token == "a"


def test_clear_removes_one_entry_and_keeps_the_rest(tmp_path):
    cache = TokenCache(tmp_path / "tokens.json")
    cache.save(KEY, TokenSet(access_token="a"))
    cache.save("other", TokenSet(access_token="b"))

    assert cache.clear(KEY) is True
    assert cache.load(KEY) is None
    assert cache.load("other") is not None
    assert cache.clear(KEY) is False


def test_clearing_the_last_entry_removes_the_file(tmp_path):
    cache = TokenCache(tmp_path / "tokens.json")
    cache.save(KEY, TokenSet(access_token="a"))
    assert cache.clear(KEY) is True
    assert not cache.path.exists()


def test_clear_without_a_key_wipes_the_file(tmp_path):
    cache = TokenCache(tmp_path / "tokens.json")
    cache.save(KEY, TokenSet(access_token="a"))
    assert cache.clear() is True
    assert cache.clear() is False


def test_expiry_is_taken_from_expires_in(tmp_path):
    tokens = TokenSet.from_response({"access_token": "a", "expires_in": 100}, now=1000.0)
    assert tokens.expires_at == 1100.0
    assert tokens.is_expired(now=1000.0) is False
    assert tokens.is_expired(now=1090.0) is True  # within the default 60s leeway
    assert tokens.is_expired(now=1090.0, leeway=0) is False


def test_expiry_falls_back_to_the_jwt_exp_claim():
    token = make_jwt(expires_in=3600)
    tokens = TokenSet.from_response({"access_token": token})
    assert tokens.expires_at == token_expires_at(token)
    assert tokens.is_expired() is False


def test_a_token_without_any_expiry_is_assumed_valid():
    tokens = TokenSet.from_response({"access_token": "opaque-token"})
    assert tokens.expires_at is None
    assert tokens.is_expired() is False


def test_an_expired_jwt_is_reported_as_expired():
    tokens = TokenSet.from_response({"access_token": make_jwt(expires_in=-10)})
    assert tokens.is_expired() is True


def test_refresh_token_survives_a_response_that_omits_it():
    old = TokenSet(access_token="old", refresh_token="keep-me")
    new = TokenSet.from_response({"access_token": "new", "expires_in": 60}, now=0.0)
    merged = old.merged_with(new)
    assert (merged.access_token, merged.refresh_token) == ("new", "keep-me")


def test_a_rotated_refresh_token_replaces_the_old_one():
    old = TokenSet(access_token="old", refresh_token="first")
    new = TokenSet(access_token="new", refresh_token="second")
    assert old.merged_with(new).refresh_token == "second"


def test_repr_does_not_leak_the_tokens():
    text = repr(TokenSet(access_token="supersecret", refresh_token="alsosecret"))
    assert "supersecret" not in text
    assert "alsosecret" not in text
    assert "refreshable=True" in text


def test_the_cache_file_is_json_with_a_version(tmp_path):
    cache = TokenCache(tmp_path / "tokens.json")
    cache.save(KEY, TokenSet(access_token="a"))
    data = json.loads(cache.path.read_text())
    assert data["version"] == 1
    assert data["tokens"][KEY]["access_token"] == "a"


def test_jwt_payload_decodes_without_a_signature_check():
    claims = decode_payload(make_jwt(expires_in=60, email="diver@example.org"))
    assert claims["email"] == "diver@example.org"
    assert claims["exp"] > time.time()


def test_opaque_tokens_decode_to_nothing():
    assert decode_payload("not-a-jwt") == {}
    assert decode_payload("a.b.c") == {}
    assert token_expires_at("not-a-jwt") is None
