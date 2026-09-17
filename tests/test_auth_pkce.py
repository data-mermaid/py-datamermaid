from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

import datamermaid
from datamermaid.auth import OAuth, code_challenge, generate_code_verifier
from datamermaid.auth.flows import PkceFlow, open_browser
from datamermaid.auth.token_cache import CACHE_FILE_MODE, default_cache_path
from datamermaid.exceptions import AuthFlowError

from .conftest import AUTHORIZE_URL, TOKEN_URL, FakeServer, Recorder, make_jwt

TOKEN_RESPONSE = {
    "access_token": "access-token",
    "refresh_token": "refresh-token",
    "id_token": "id-token",
    "token_type": "Bearer",
    "expires_in": 86400,
    "scope": "openid profile email offline_access",
}


def echoing_opener(server, opens=True):
    """A browser that immediately 'redirects' by handing the state back."""

    recorder = Recorder(opens=opens)

    def opener(url):
        server.params["state"] = parse_qs(urlsplit(url).query)["state"][0]
        return recorder.open(url)

    recorder.opener = opener
    return recorder


def pkce_auth(server, recorder, **kwargs):
    return OAuth(
        flow="pkce",
        server_factory=server,
        browser_opener=recorder.opener,
        printer=recorder.write,
        **kwargs,
    )


# -- PKCE primitives ------------------------------------------------------


def test_verifier_is_url_safe_and_long_enough():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9._~-]+", verifier)


def test_verifiers_are_not_reused():
    assert generate_code_verifier() != generate_code_verifier()


def test_challenge_matches_the_rfc_7636_test_vector():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_challenge_is_unpadded_base64url():
    challenge = code_challenge(generate_code_verifier())
    assert "=" not in challenge
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge)


# -- the flow -------------------------------------------------------------


@respx.mock
def test_authorization_url_carries_the_pkce_challenge_and_audience():
    server = FakeServer({"code": "the-code"})
    recorder = echoing_opener(server)
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    pkce_auth(server, recorder).login()

    url = recorder.opened[0]
    assert url.startswith(AUTHORIZE_URL)
    query = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
    assert query["response_type"] == "code"
    assert query["code_challenge_method"] == "S256"
    assert query["audience"] == "https://api.datamermaid.org"
    assert "offline_access" in query["scope"]
    assert query["redirect_uri"] == server.redirect_uri

    sent = dict(parse_qs(route.calls.last.request.content.decode(), keep_blank_values=True))
    assert code_challenge(sent["code_verifier"][0]) == query["code_challenge"]


@respx.mock
def test_code_is_exchanged_for_tokens():
    server = FakeServer({"code": "the-code"})
    recorder = echoing_opener(server)
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    tokens = pkce_auth(server, recorder).login()

    sent = {k: v[0] for k, v in parse_qs(route.calls.last.request.content.decode()).items()}
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "the-code"
    assert sent["redirect_uri"] == server.redirect_uri
    assert tokens.access_token == "access-token"
    assert tokens.refresh_token == "refresh-token"
    assert tokens.expires_at is not None


@respx.mock
def test_login_persists_the_tokens_with_owner_only_permissions():
    server = FakeServer({"code": "the-code"})
    recorder = echoing_opener(server)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    auth = datamermaid.login(
        flow="pkce",
        server_factory=server,
        browser_opener=recorder.opener,
        printer=recorder.write,
    )

    path = default_cache_path()
    assert path.exists()
    assert path.stat().st_mode & 0o777 == CACHE_FILE_MODE
    assert auth.tokens.access_token == "access-token"


@respx.mock
def test_a_second_login_reuses_the_cached_token():
    server = FakeServer({"code": "the-code"})
    recorder = echoing_opener(server)
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    datamermaid.login(
        flow="pkce",
        server_factory=server,
        browser_opener=recorder.opener,
        printer=recorder.write,
    )
    reused = OAuth(flow="pkce", server_factory=FakeServer({}), browser_opener=recorder.opener)

    assert reused.login().access_token == "access-token"
    assert route.call_count == 1


@respx.mock
def test_logout_forgets_the_cached_token():
    server = FakeServer({"code": "the-code"})
    recorder = echoing_opener(server)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    auth = pkce_auth(server, recorder)
    auth.login()

    assert datamermaid.logout() is True
    assert OAuth().tokens is None
    assert datamermaid.logout() is False


def test_the_url_is_printed_when_no_browser_could_be_opened():
    server = FakeServer({"code": "the-code"})
    recorder = echoing_opener(server, opens=False)
    with respx.mock:
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        pkce_auth(server, recorder).login()

    assert AUTHORIZE_URL in recorder.text


def test_a_mismatched_state_is_rejected():
    server = FakeServer({"code": "the-code", "state": "not-the-state"})
    recorder = Recorder()
    recorder.opener = recorder.open

    with pytest.raises(AuthFlowError, match="state mismatch"):
        pkce_auth(server, recorder).login()


def test_a_denied_authorization_is_reported():
    server = FakeServer({"error": "access_denied", "error_description": "User declined"})
    recorder = Recorder()
    recorder.opener = recorder.open

    with pytest.raises(AuthFlowError, match="access_denied: User declined"):
        pkce_auth(server, recorder).login()


def test_a_redirect_without_a_code_is_reported():
    server = FakeServer({})
    recorder = echoing_opener(server)

    with pytest.raises(AuthFlowError, match="no code"):
        pkce_auth(server, recorder).login()


@respx.mock
def test_a_rejected_code_exchange_is_reported():
    server = FakeServer({"code": "stale"})
    recorder = echoing_opener(server)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            403, json={"error": "invalid_grant", "error_description": "code expired"}
        )
    )

    with pytest.raises(AuthFlowError, match="invalid_grant: code expired"):
        pkce_auth(server, recorder).login()


def test_a_connection_failure_during_the_exchange_is_reported():
    server = FakeServer({"code": "the-code"})
    recorder = echoing_opener(server)
    with respx.mock:
        respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("no route to host"))
        with pytest.raises(datamermaid.MermaidConnectionError):
            pkce_auth(server, recorder).login()


def test_the_callback_server_is_always_closed():
    server = FakeServer({})
    recorder = echoing_opener(server)
    with pytest.raises(AuthFlowError):
        pkce_auth(server, recorder).login()
    assert server.closed


# -- refresh --------------------------------------------------------------


@respx.mock
def test_an_expired_token_is_refreshed_instead_of_prompting_a_new_login(tmp_path):
    from datamermaid.auth import TokenCache, TokenSet

    cache = TokenCache(tmp_path / "tokens.json")
    auth = OAuth(flow="pkce", cache=cache, server_factory=FakeServer({}))
    cache.save(
        auth.config.cache_key,
        TokenSet(access_token=make_jwt(expires_in=-30), refresh_token="refresh-token"),
    )
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
    )

    assert auth.access_token() == "fresh"
    sent = {k: v[0] for k, v in parse_qs(route.calls.last.request.content.decode()).items()}
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "refresh-token"
    # The refresh token was not returned again, so the stored one is kept.
    assert cache.load(auth.config.cache_key).refresh_token == "refresh-token"


@respx.mock
def test_a_valid_cached_token_is_used_without_any_request(tmp_path):
    from datamermaid.auth import TokenCache, TokenSet

    cache = TokenCache(tmp_path / "tokens.json")
    auth = OAuth(cache=cache)
    cache.save(auth.config.cache_key, TokenSet(access_token=make_jwt(expires_in=3600)))
    route = respx.post(TOKEN_URL)

    assert auth.access_token()
    assert route.call_count == 0


@respx.mock
def test_a_rejected_refresh_token_falls_back_to_an_interactive_login(tmp_path):
    from datamermaid.auth import TokenCache, TokenSet

    server = FakeServer({"code": "the-code"})
    recorder = echoing_opener(server)
    cache = TokenCache(tmp_path / "tokens.json")
    auth = pkce_auth(server, recorder, cache=cache)
    cache.save(
        auth.config.cache_key,
        TokenSet(access_token=make_jwt(expires_in=-30), refresh_token="revoked"),
    )
    respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(403, json={"error": "invalid_grant"}),
            httpx.Response(200, json=TOKEN_RESPONSE),
        ]
    )

    assert auth.access_token() == "access-token"


def test_non_interactive_auth_refuses_to_prompt():
    auth = OAuth(interactive=False, cache=False)
    with pytest.raises(AuthFlowError, match="interactive login is disabled"):
        auth.login()


def test_an_unknown_flow_name_is_rejected():
    with pytest.raises(ValueError, match="unknown flow"):
        OAuth(flow="magic")


def test_a_flow_instance_may_be_passed_directly():
    flow = PkceFlow()
    assert OAuth(flow=flow).flow is flow


def test_repr_does_not_leak_the_token(tmp_path):
    from datamermaid.auth import TokenCache, TokenSet

    cache = TokenCache(tmp_path / "tokens.json")
    auth = OAuth(cache=cache)
    cache.save(auth.config.cache_key, TokenSet(access_token="supersecret"))
    auth.access_token()
    assert "supersecret" not in repr(auth)
    assert "authenticated=True" in repr(auth)


def test_a_redirect_without_a_state_is_rejected():
    server = FakeServer({"code": "the-code"})
    recorder = Recorder()
    recorder.opener = recorder.open

    with pytest.raises(AuthFlowError, match="no state"):
        pkce_auth(server, recorder).login()


def test_a_non_ascii_state_is_a_mismatch_rather_than_a_crash():
    # secrets.compare_digest refuses non-ASCII str operands.
    server = FakeServer({"code": "the-code", "state": "staéte"})
    recorder = Recorder()
    recorder.opener = recorder.open

    with pytest.raises(AuthFlowError, match="state mismatch"):
        pkce_auth(server, recorder).login()


# -- opening the browser --------------------------------------------------


def test_a_browser_that_cannot_start_is_reported_as_not_opened(monkeypatch):
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", _raise(webbrowser.Error("no runnable browser")))
    assert open_browser("https://example.test/") is False

    monkeypatch.setattr(webbrowser, "open", _raise(OSError("display refused")))
    assert open_browser("https://example.test/") is False


def test_open_browser_does_not_swallow_the_test_suite_guard(monkeypatch):
    # conftest replaces webbrowser.open with a guard that raises; catching it
    # here would turn "this test forgot to inject a browser" into a silent
    # hang on a login nobody can complete.
    with pytest.raises(AssertionError, match="tried to open a browser"):
        open_browser("https://example.test/")


def _raise(exc):
    def fail(*args, **kwargs):
        raise exc

    return fail
