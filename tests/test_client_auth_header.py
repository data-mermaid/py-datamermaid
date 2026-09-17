from __future__ import annotations

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
