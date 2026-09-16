from __future__ import annotations

import httpx
import pytest
import respx

from datamermaid import PaginatedList, Project
from datamermaid.exceptions import NotFoundError

from .conftest import BASE_URL, page, project_payload


@respx.mock
def test_list_is_lazy_and_crosses_pages_on_demand(client):
    # Registered most specific first: respx matches routes in registration order.
    second = respx.get(f"{BASE_URL}projects/", params={"limit": "1", "page": "2"}).mock(
        return_value=httpx.Response(200, json=page([project_payload(2)], count=2))
    )
    first = respx.get(f"{BASE_URL}projects/", params={"limit": "1"}).mock(
        return_value=httpx.Response(
            200,
            json=page(
                [project_payload(1)],
                next_url=f"{BASE_URL}projects/?limit=1&page=2",
                count=2,
            ),
        )
    )

    projects = client.projects.list(limit=1)
    assert isinstance(projects, PaginatedList)
    assert first.call_count == 0

    iterator = iter(projects)
    assert next(iterator).name == "Project 1"
    assert first.call_count == 1
    assert second.call_count == 0

    assert next(iterator).name == "Project 2"
    assert second.call_count == 1

    with pytest.raises(StopIteration):
        next(iterator)
    assert first.call_count == 1
    assert second.call_count == 1


@respx.mock
def test_list_passes_filters_as_query_parameters(client):
    route = respx.get(f"{BASE_URL}projects/").mock(return_value=httpx.Response(200, json=page([])))
    list(client.projects.list(showall=True, name="Reef Watch", ignored=None))
    request = route.calls.last.request
    assert request.url.params["showall"] == "true"
    assert request.url.params["name"] == "Reef Watch"
    assert "ignored" not in request.url.params


@respx.mock
def test_len_uses_the_reported_count(client):
    respx.get(f"{BASE_URL}projects/").mock(
        return_value=httpx.Response(
            200,
            json=page([project_payload(1)], next_url=f"{BASE_URL}projects/?page=2", count=3195),
        )
    )
    assert len(client.projects.list()) == 3195
    assert respx.calls.call_count == 1


@respx.mock
def test_get_fetches_a_single_project(client):
    project_id = project_payload(7)["id"]
    respx.get(f"{BASE_URL}projects/{project_id}/").mock(
        return_value=httpx.Response(200, json=project_payload(7))
    )
    project = client.projects.get(project_id)
    assert isinstance(project, Project)
    assert project.name == "Project 7"


@respx.mock
def test_get_raises_not_found(client):
    respx.get(f"{BASE_URL}projects/missing/").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )
    with pytest.raises(NotFoundError):
        client.projects.get("missing")


@respx.mock
def test_bare_list_responses_are_supported(client):
    respx.get(f"{BASE_URL}projects/").mock(
        return_value=httpx.Response(200, json=[project_payload(1), project_payload(2)])
    )
    assert len(client.projects.list()) == 2


@respx.mock
def test_to_df_over_pages(client):
    pandas = pytest.importorskip("pandas")
    respx.get(f"{BASE_URL}projects/", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=page([project_payload(2)], count=2))
    )
    respx.get(f"{BASE_URL}projects/").mock(
        return_value=httpx.Response(
            200,
            json=page([project_payload(1)], next_url=f"{BASE_URL}projects/?page=2", count=2),
        )
    )
    frame = client.projects.list().to_df()
    assert isinstance(frame, pandas.DataFrame)
    assert len(frame) == 2
    assert list(frame["name"]) == ["Project 1", "Project 2"]
