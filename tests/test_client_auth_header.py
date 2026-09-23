from __future__ import annotations

import threading
import time

import httpx
import pytest
import respx

from datamermaid import (
    AnonymousAuth,
    APIKeyAuth,
    AuthenticationError,
    MermaidClient,
    OAuth,
    TokenCache,
    TokenSet,
)
from datamermaid.auth import resolve_auth

from .conftest import BASE_URL, TOKEN_URL, FakeServer, Recorder, make_jwt

ME = {"id": "1", "full_name": "A Diver"}


def cached(tokens, path=None):
    """An :class:`OAuth` whose cache already holds ``tokens``."""

    auth = OAuth(cache=TokenCache(path) if path else True)
    (auth.cache).save(auth.config.cache_key, tokens)
    return auth


@respx.mock
def test_requests_carry_the_oauth_access_token():
    token = make_jwt(expires_in=3600)
    auth = cached(TokenSet(access_token=token, refresh_token="r"))
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, json=ME))

    with MermaidClient(auth=auth) as client:
        client.me()

    assert route.calls.last.request.headers["Authorization"] == f"Bearer {token}"


@respx.mock
def test_a_plain_client_picks_up_the_tokens_left_by_login():
    token = make_jwt(expires_in=3600)
    cached(TokenSet(access_token=token))
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, json=ME))

    with MermaidClient() as client:
        assert isinstance(client.auth, OAuth)
        client.me()

    assert route.calls.last.request.headers["Authorization"] == f"Bearer {token}"


@respx.mock
def test_an_expired_cached_token_is_refreshed_before_the_request():
    cached(TokenSet(access_token=make_jwt(expires_in=-60), refresh_token="refresh-token"))
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "renewed", "expires_in": 3600})
    )
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, json=ME))

    with MermaidClient() as client:
        client.me()

    assert route.calls.last.request.headers["Authorization"] == "Bearer renewed"


@respx.mock
def test_a_rejected_token_is_refreshed_and_the_request_retried_once():
    auth = cached(TokenSet(access_token=make_jwt(expires_in=3600), refresh_token="refresh-token"))
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "renewed", "expires_in": 3600})
    )
    route = respx.get(f"{BASE_URL}me/").mock(
        side_effect=[httpx.Response(401, json={}), httpx.Response(200, json=ME)]
    )

    with MermaidClient(auth=auth) as client:
        client.me()

    assert route.call_count == 2
    assert route.calls.last.request.headers["Authorization"] == "Bearer renewed"


@respx.mock
def test_a_rejected_token_that_cannot_be_renewed_is_not_retried():
    auth = cached(TokenSet(access_token=make_jwt(expires_in=3600)))
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(403, json={}))

    with MermaidClient(auth=auth) as client, pytest.raises(AuthenticationError):
        client.me()

    assert route.call_count == 1


@respx.mock
def test_logging_in_then_using_the_client(tmp_path):
    import datamermaid

    server = FakeServer({"code": "the-code"})
    recorder = Recorder()

    def opener(url):
        from urllib.parse import parse_qs, urlsplit

        server.params["state"] = parse_qs(urlsplit(url).query)["state"][0]
        return recorder.open(url)

    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
    )
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, json=ME))

    datamermaid.login(flow="pkce", server_factory=server, browser_opener=opener)
    with MermaidClient() as client:
        client.me()

    assert route.calls.last.request.headers["Authorization"] == "Bearer fresh"


def test_an_api_key_wins_over_a_cached_token(monkeypatch):
    cached(TokenSet(access_token=make_jwt(expires_in=3600)))
    monkeypatch.setenv("MERMAID_API_KEY", "mmd_env.key")
    assert isinstance(resolve_auth(), APIKeyAuth)


def test_an_explicit_auth_wins_over_a_cached_token():
    cached(TokenSet(access_token=make_jwt(expires_in=3600)))
    assert isinstance(resolve_auth(AnonymousAuth()), AnonymousAuth)


def test_without_a_cached_token_the_client_stays_anonymous():
    assert isinstance(resolve_auth(), AnonymousAuth)


def test_the_cache_lookup_can_be_switched_off():
    cached(TokenSet(access_token=make_jwt(expires_in=3600)))
    assert isinstance(resolve_auth(cache=False), AnonymousAuth)


@pytest.fixture
def _no_prompt(monkeypatch):
    """An ordinary request must never stop to ask the user anything."""

    def refuse(*args, **kwargs):
        raise AssertionError("an ordinary request tried to start an interactive login")

    monkeypatch.setattr("builtins.input", refuse)


@respx.mock
def test_a_stale_cached_token_is_not_adopted(_no_prompt):
    # Expired and not refreshable: picking it up would turn the next data call
    # into a browser prompt nobody asked for.
    cached(TokenSet(access_token=make_jwt(expires_in=-60)))
    assert isinstance(resolve_auth(), AnonymousAuth)

    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(401, json={}))
    with MermaidClient() as client, pytest.raises(AuthenticationError):
        client.me()

    assert "Authorization" not in route.calls.last.request.headers


def test_an_adopted_cached_login_never_prompts():
    cached(TokenSet(access_token=make_jwt(expires_in=3600)))
    auth = resolve_auth()
    assert isinstance(auth, OAuth)
    assert auth.interactive is False


@respx.mock
def test_an_adopted_token_that_cannot_be_renewed_fails_cleanly(_no_prompt):
    from datamermaid import AuthFlowError

    cached(TokenSet(access_token=make_jwt(expires_in=-60), refresh_token="stale"))
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(403, json={"error": "invalid_grant"}))
    respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, json=ME))

    with MermaidClient() as client, pytest.raises(AuthFlowError):
        client.me()


# -- concurrent renewal -------------------------------------------------------


def run_together(count, target):
    """Run ``target`` on ``count`` threads released at the same moment."""

    barrier = threading.Barrier(count)
    results, errors = [], []

    def worker():
        barrier.wait()
        try:
            results.append(target())
        except Exception as exc:  # pragma: no cover - surfaced by the assert
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors
    return results


@respx.mock
def test_concurrent_callers_refresh_an_expired_token_once(tmp_path):
    auth = cached(
        TokenSet(access_token=make_jwt(expires_in=-30), refresh_token="refresh-token"),
        tmp_path / "tokens.json",
    )

    def slow_refresh(request):
        time.sleep(0.05)  # widen the window a racing thread would slip into
        return httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})

    route = respx.post(TOKEN_URL).mock(side_effect=slow_refresh)

    assert run_together(4, auth.access_token) == ["fresh"] * 4
    assert route.call_count == 1


@respx.mock
def test_concurrent_rejections_renew_the_token_once(tmp_path):
    auth = cached(
        TokenSet(access_token="stale", refresh_token="refresh-token"),
        tmp_path / "tokens.json",
    )
    # `stale` is not a JWT, so it never looks expired: only the 401s renew it.
    token = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "renewed", "expires_in": 3600})
    )
    rejected = threading.Barrier(2)

    def me(request):
        if request.headers["Authorization"] == "Bearer stale":
            rejected.wait(timeout=5)  # both requests are rejected before either renews
            return httpx.Response(401, json={})
        return httpx.Response(200, json=ME)

    route = respx.get(f"{BASE_URL}me/").mock(side_effect=me)

    with MermaidClient(auth=auth) as client:
        run_together(2, client.me)

    assert token.call_count == 1
    assert route.call_count == 4
    assert [call.request.headers["Authorization"] for call in route.calls][2:] == [
        "Bearer renewed",
        "Bearer renewed",
    ]
