"""Invalid server envelopes use the SDK error contract, never empty success."""

import httpx
import pytest
import respx

from datamermaid import MermaidAPIError, MermaidConnectionError

from .conftest import BASE_URL


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "unexpected",
        {},
        {"detail": "broken"},
        {"results": None},
        {"results": {}},
        {"results": [None]},
        {"results": [], "next": 42},
        {"results": [], "next": ""},
        {"results": [], "count": "2"},
        {"results": [], "count": -1},
        {"results": [], "count": True},
    ],
)
@respx.mock
def test_invalid_pages_are_connection_errors(client, payload):
    respx.get(f"{BASE_URL}projects/").respond(200, json=payload)
    with pytest.raises(MermaidConnectionError) as raised:
        list(client.projects.list())
    assert isinstance(raised.value.__cause__, (TypeError, ValueError))


@pytest.mark.parametrize("path", ["me/", "projects/project-1/"])
@pytest.mark.parametrize(
    "response",
    [httpx.Response(204), httpx.Response(200, json=[]), httpx.Response(200, text="not JSON")],
)
@respx.mock
def test_invalid_detail_responses_are_connection_errors(client, path, response):
    respx.get(f"{BASE_URL}{path}").mock(return_value=response)
    with pytest.raises(MermaidConnectionError):
        client.me() if path == "me/" else client.projects.get("project-1")


@pytest.mark.parametrize("payload", [{}, {"data": False}, {"data": {}}, {"data": [None]}])
@respx.mock
def test_invalid_choice_detail_is_a_connection_error(client, payload):
    respx.get(f"{BASE_URL}choices/countries/").respond(200, json=payload)
    with pytest.raises(MermaidConnectionError):
        client.choices("countries")


@pytest.mark.parametrize(
    "payload", [[], {"results": []}, {"results": [], "count": 0, "next": None}]
)
@respx.mock
def test_valid_empty_pages_remain_valid(client, payload):
    respx.get(f"{BASE_URL}projects/").respond(200, json=payload)
    assert list(client.projects.list()) == []


@respx.mock
def test_bad_second_page_does_not_silently_truncate_results(client):
    next_url = f"{BASE_URL}projects/?offset=1"
    respx.get(next_url).respond(200, json={"detail": "invalid page"})
    respx.get(f"{BASE_URL}projects/").respond(
        200, json={"results": [{"id": "first"}], "next": next_url, "count": 2}
    )
    rows = iter(client.projects.list())
    assert next(rows).id == "first"
    with pytest.raises(MermaidConnectionError):
        next(rows)


@respx.mock
def test_http_errors_keep_their_original_category(client):
    respx.get(f"{BASE_URL}projects/").respond(400, json={"detail": "bad filters"})
    with pytest.raises(MermaidAPIError):
        list(client.projects.list())
