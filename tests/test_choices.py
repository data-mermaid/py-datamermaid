"""``/choices/``: the one list route that is not a DRF page."""

from __future__ import annotations

import httpx
import pytest
import respx

from datamermaid.exceptions import MermaidConnectionError, NotFoundError

from .conftest import BASE_URL, load_fixture

CHOICES = load_fixture("choices_response")


@respx.mock
def test_choices_returns_a_mapping_of_name_to_rows(client):
    route = respx.get(f"{BASE_URL}choices/").mock(return_value=httpx.Response(200, json=CHOICES))

    choices = client.choices()

    assert route.call_count == 1
    assert set(choices) == {entry["name"] for entry in CHOICES}
    countries = choices["countries"]
    assert isinstance(countries, list)
    assert countries[0]["name"] == "Afghanistan"
    assert set(countries[0]) == {"id", "name", "updated_on"}


@respx.mock
def test_choices_keeps_choice_sets_the_sdk_knows_nothing_about(client):
    payload = [*CHOICES, {"name": "somethingnew", "data": [{"id": "1", "name": "first"}]}]
    respx.get(f"{BASE_URL}choices/").mock(return_value=httpx.Response(200, json=payload))

    assert client.choices()["somethingnew"] == [{"id": "1", "name": "first"}]


@respx.mock
def test_choices_handles_an_empty_choice_set(client):
    respx.get(f"{BASE_URL}choices/").mock(
        return_value=httpx.Response(200, json=[{"name": "reeftypes", "data": None}])
    )

    assert client.choices() == {"reeftypes": []}


@respx.mock
def test_a_single_choice_set_uses_the_detail_route(client):
    entry = next(choice for choice in CHOICES if choice["name"] == "reeftypes")
    listed = respx.get(f"{BASE_URL}choices/").mock(return_value=httpx.Response(200, json=CHOICES))
    route = respx.get(f"{BASE_URL}choices/reeftypes/").mock(
        return_value=httpx.Response(200, json=entry)
    )

    assert client.choices("reeftypes") == entry["data"]
    assert route.call_count == 1
    assert listed.call_count == 0


@respx.mock
def test_an_unknown_choice_set_raises_not_found(client):
    respx.get(f"{BASE_URL}choices/nope/").mock(
        return_value=httpx.Response(404, json={"detail": "nope choice not found."})
    )

    with pytest.raises(NotFoundError):
        client.choices("nope")


@respx.mock
def test_a_nameless_choice_set_is_rejected(client):
    respx.get(f"{BASE_URL}choices/").mock(
        return_value=httpx.Response(200, json=[{"data": [{"id": "1", "name": "first"}]}])
    )

    with pytest.raises(MermaidConnectionError, match="without a name"):
        client.choices()


@respx.mock
def test_duplicate_choice_set_names_are_rejected(client):
    payload = [
        {"name": "reeftypes", "data": [{"id": "1", "name": "atoll"}]},
        {"name": "reeftypes", "data": [{"id": "2", "name": "barrier"}]},
    ]
    respx.get(f"{BASE_URL}choices/").mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(MermaidConnectionError, match="two choice sets named 'reeftypes'"):
        client.choices()


@respx.mock
def test_a_paginated_response_would_be_rejected(client):
    """The endpoint answering with a DRF page means the SDK's shape is wrong."""

    respx.get(f"{BASE_URL}choices/").mock(
        return_value=httpx.Response(200, json={"count": 1, "results": CHOICES})
    )

    with pytest.raises(MermaidConnectionError, match="list of choice sets"):
        client.choices()
