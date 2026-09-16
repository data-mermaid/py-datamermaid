from __future__ import annotations

import httpx
import pytest
import respx

from datamermaid import AnonymousAuth, APIKeyAuth, MermaidClient
from datamermaid.auth import Auth, HTTPXAuthAdapter, resolve_auth

from .conftest import BASE_URL


def test_api_key_auth_sets_bearer_header():
    request = httpx.Request("GET", f"{BASE_URL}me/")
    APIKeyAuth("mmd_x.y").apply(request)
    assert request.headers["Authorization"] == "Bearer mmd_x.y"


@respx.mock
def test_client_sends_bearer_header():
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, json={"id": "1"}))
    with MermaidClient(api_key="mmd_x.y") as client:
        client.me()
    assert route.calls.last.request.headers["Authorization"] == "Bearer mmd_x.y"


@respx.mock
def test_client_reads_api_key_from_environment(monkeypatch):
    monkeypatch.setenv("MERMAID_API_KEY", "mmd_env.key")
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, json={"id": "1"}))
    with MermaidClient() as client:
        client.me()
    assert route.calls.last.request.headers["Authorization"] == "Bearer mmd_env.key"


@respx.mock
def test_client_without_credentials_sends_no_authorization_header():
    route = respx.get(f"{BASE_URL}projects/").mock(
        return_value=httpx.Response(200, json={"count": 0, "next": None, "results": []})
    )
    with MermaidClient() as client:
        list(client.projects.list())
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_client_sends_user_agent_with_version():
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, json={"id": "1"}))
    with MermaidClient(api_key="mmd_x.y") as client:
        client.me()
    assert route.calls.last.request.headers["User-Agent"].startswith("datamermaid/")


def test_explicit_auth_takes_precedence():
    auth = AnonymousAuth()
    assert resolve_auth(auth, None) is auth


def test_auth_and_api_key_are_mutually_exclusive():
    with pytest.raises(ValueError, match="not both"):
        resolve_auth(AnonymousAuth(), "mmd_x.y")


def test_resolve_auth_falls_back_to_anonymous():
    assert isinstance(resolve_auth(), AnonymousAuth)


def test_api_key_must_not_be_blank():
    with pytest.raises(ValueError, match="non-empty"):
        APIKeyAuth("   ")


def test_repr_does_not_leak_the_secret():
    text = repr(APIKeyAuth("mmd_public.supersecret"))
    assert "supersecret" not in text
    assert "mmd_public" in text


def test_client_repr_does_not_leak_the_secret():
    with MermaidClient(api_key="mmd_public.supersecret") as client:
        assert "supersecret" not in repr(client)


@respx.mock
def test_refreshing_auth_retries_once_after_401():
    class RefreshingAuth(Auth):
        def __init__(self):
            self.token = "old"
            self.refreshes = 0

        def apply(self, request):
            request.headers["Authorization"] = f"Bearer {self.token}"

        def should_refresh(self, response):
            return self.refreshes == 0

        def refresh(self):
            self.refreshes += 1
            self.token = "new"

    auth = RefreshingAuth()
    route = respx.get(f"{BASE_URL}me/").mock(
        side_effect=[httpx.Response(401, json={}), httpx.Response(200, json={"id": "1"})]
    )
    with MermaidClient(auth=auth) as client:
        client.me()

    assert auth.refreshes == 1
    assert [call.request.headers["Authorization"] for call in route.calls] == [
        "Bearer old",
        "Bearer new",
    ]


def test_adapter_exposes_the_wrapped_auth():
    auth = APIKeyAuth("mmd_x.y")
    assert HTTPXAuthAdapter(auth).auth is auth
