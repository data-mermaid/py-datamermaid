from __future__ import annotations

import httpx
import respx

from datamermaid import DEFAULT_BASE_URL, MermaidClient
from datamermaid.client import DEV_BASE_URL, default_user_agent, resolve_base_url

from .conftest import BASE_URL


def test_default_base_url():
    assert resolve_base_url() == DEFAULT_BASE_URL


def test_base_url_from_environment(monkeypatch):
    monkeypatch.setenv("MERMAID_API_URL", DEV_BASE_URL)
    assert MermaidClient().base_url == DEV_BASE_URL


def test_explicit_base_url_wins_over_environment(monkeypatch):
    monkeypatch.setenv("MERMAID_API_URL", DEV_BASE_URL)
    assert MermaidClient(base_url="https://example.test/v1/").base_url == "https://example.test/v1/"


def test_base_url_gets_a_trailing_slash():
    assert resolve_base_url("https://example.test/v1") == "https://example.test/v1/"


def test_user_agent_mentions_the_sdk_and_httpx():
    agent = default_user_agent()
    assert agent.startswith("datamermaid/")
    assert "httpx/" in agent


@respx.mock
def test_requests_use_the_configured_base_url():
    route = respx.get("https://example.test/v1/me/").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    with MermaidClient(base_url="https://example.test/v1", api_key="mmd_x.y") as client:
        client.me()
    assert route.called


@respx.mock
def test_me_parses_the_profile(client):
    respx.get(f"{BASE_URL}me/").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "33333333-3333-3333-3333-333333333333",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "full_name": "Ada Lovelace",
                "email": "ada@example.test",
                "picture": "https://example.test/ada.png",
                "created_on": "2024-01-02T03:04:05.678901Z",
                "updated_on": "2024-02-02T03:04:05.678901Z",
                "projects": [
                    {
                        "id": "44444444-4444-4444-4444-444444444444",
                        "name": "Reef Watch",
                        "role": 90,
                        "num_active_sample_units": 2,
                    }
                ],
            },
        )
    )
    me = client.me()
    assert me.full_name == "Ada Lovelace"
    assert me.email == "ada@example.test"
    assert me.created_on is not None
    assert me.created_on.year == 2024
    assert len(me.projects) == 1
    assert me.projects[0].name == "Reef Watch"
    assert me.projects[0].role == 90


@respx.mock
def test_custom_headers_are_sent():
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, json={"id": "1"}))
    with MermaidClient(api_key="mmd_x.y", headers={"X-Trace": "abc"}) as client:
        client.me()
    assert route.calls.last.request.headers["X-Trace"] == "abc"


def test_context_manager_closes_the_transport():
    with MermaidClient(api_key="mmd_x.y") as client:
        assert not client._http.is_closed
    assert client._http.is_closed


def test_projects_resource_is_cached():
    with MermaidClient(api_key="mmd_x.y") as client:
        assert client.projects is client.projects
