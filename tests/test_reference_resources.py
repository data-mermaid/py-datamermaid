"""The top-level reference, lookup and summary endpoints.

The payloads in ``tests/fixtures/reference_responses.json`` are real records
from ``https://api.datamermaid.org/v1/``, trimmed; ``sites`` and ``managements``
need credentials, so those two were written from the API's models instead.
"""

from __future__ import annotations

import datetime

import httpx
import pytest
import respx

from datamermaid import PaginatedList
from datamermaid.resources import REFERENCE_RESOURCES

from .conftest import BASE_URL, page, reference_payload

# (attribute on the client, resource class), as the client exposes them.
RESOURCES = list(REFERENCE_RESOURCES)
IDS = [attribute for attribute, _ in RESOURCES]


def record_id(payload):
    """The id the endpoint's detail route is keyed by."""

    # /summarysampleevents/ drops `id` and identifies a row by its sample event.
    return payload.get("id") or payload["sample_event_id"]


@pytest.mark.parametrize(("attribute", "resource_class"), RESOURCES, ids=IDS)
@respx.mock
def test_list_returns_typed_models(client, attribute, resource_class):
    payload = reference_payload(attribute)
    route = respx.get(f"{BASE_URL}{resource_class.path}").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    resource = getattr(client, attribute)
    records = resource.list()
    assert isinstance(records, PaginatedList)
    assert route.call_count == 0  # nothing is fetched until the list is used

    record = records[0]
    assert isinstance(record, resource_class.model)
    assert record_id(record.to_dict()) == record_id(payload)
    # Every key the API sent is either a declared field or kept in `extra`.
    assert set(payload) <= set(record.to_dict())
    assert route.call_count == 1


@pytest.mark.parametrize(("attribute", "resource_class"), RESOURCES, ids=IDS)
@respx.mock
def test_get_fetches_one_record_by_id(client, attribute, resource_class):
    payload = reference_payload(attribute)
    identifier = record_id(payload)
    route = respx.get(f"{BASE_URL}{resource_class.path}{identifier}/").mock(
        return_value=httpx.Response(200, json=payload)
    )

    record = getattr(client, attribute).get(identifier)
    assert isinstance(record, resource_class.model)
    assert record_id(record.to_dict()) == identifier
    assert route.call_count == 1


@pytest.mark.parametrize(("attribute", "resource_class"), RESOURCES, ids=IDS)
def test_resources_are_cached_on_the_client(client, attribute, resource_class):
    resource = getattr(client, attribute)
    assert isinstance(resource, resource_class)
    assert getattr(client, attribute) is resource


@respx.mock
def test_list_forwards_filters_as_query_parameters(client):
    route = respx.get(f"{BASE_URL}fishspecies/").mock(
        return_value=httpx.Response(200, json=page([]))
    )

    genus = "f5263c54-ea12-4a18-8200-d52967376d1a"
    list(client.fish_species.list(genus=genus, status=90, limit=500, ignored=None))

    params = route.calls.last.request.url.params
    assert params["genus"] == genus
    assert params["status"] == "90"
    assert params["limit"] == "500"
    assert "ignored" not in params


@respx.mock
def test_get_forwards_filters_as_query_parameters(client):
    payload = reference_payload("fish_species")
    route = respx.get(f"{BASE_URL}fishspecies/{payload['id']}/").mock(
        return_value=httpx.Response(200, json=payload)
    )

    client.fish_species.get(payload["id"], fields="id,name", ignored=None)

    params = route.calls.last.request.url.params
    assert params["fields"] == "id,name"
    assert "ignored" not in params


@respx.mock
def test_label_mappings_use_the_nested_classification_path(client):
    route = respx.get(f"{BASE_URL}classification/labelmappings/").mock(
        return_value=httpx.Response(200, json=page([reference_payload("label_mappings")]))
    )

    mapping = client.label_mappings.list(provider="ReefCloud")[0]
    assert route.call_count == 1
    assert mapping.provider == "ReefCloud"
    assert mapping.benthic_attribute_name == "Abyla"


@respx.mock
def test_fish_species_are_parsed_in_full(client):
    payload = reference_payload("fish_species")
    respx.get(f"{BASE_URL}fishspecies/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    species = client.fish_species.list()[0]
    assert species.display_name == "Abalistes filamentosus"
    assert species.name == "filamentosus"
    assert species.genus == payload["genus"]
    assert species.max_length == pytest.approx(39.65)
    assert species.regions == tuple(payload["regions"])
    assert species.created_on.year == 2018
    assert species.extra == {}


@respx.mock
def test_benthic_attribute_collections_survive_a_null(client):
    payload = reference_payload("benthic_attributes")
    assert payload["life_histories"] is None  # the API sends null, not []
    respx.get(f"{BASE_URL}benthicattributes/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    attribute = client.benthic_attributes.list()[0]
    assert attribute.life_histories == ()
    assert attribute.growth_form_life_histories == ()
    assert attribute.regions == tuple(payload["regions"])
    assert attribute.top_level_category == payload["top_level_category"]


@respx.mock
def test_summary_sample_events_expose_dates_policies_and_protocols(client):
    payload = reference_payload("summary_sample_events")
    respx.get(f"{BASE_URL}summarysampleevents/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    summary = client.summary_sample_events.list()[0]
    assert summary.sample_date == datetime.date(2008, 11, 25)
    assert summary.management_rules == ("open access",)
    assert summary.protocols["benthicpit"]["sample_unit_count"] == 3
    assert summary.data_policies["beltfish"] == "private"
    assert [tag["name"] for tag in summary.tags] == ["Marine Ecology Consulting Fiji"]


@respx.mock
def test_management_rules_are_available_as_flags(client):
    payload = reference_payload("managements")
    respx.get(f"{BASE_URL}managements/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    management = client.managements.list()[0]
    assert management.rules == "Open Access"
    assert management.rule_flags["open_access"] is True
    assert management.rule_flags["no_take"] is False


@respx.mock
def test_unknown_fields_are_kept_in_extra(client):
    payload = {**reference_payload("fish_families"), "some_new_api_field": 42}
    respx.get(f"{BASE_URL}fishfamilies/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    family = client.fish_families.list()[0]
    assert family.extra == {"some_new_api_field": 42}
    assert family.to_dict()["some_new_api_field"] == 42


@respx.mock
def test_to_df_over_a_taxonomy_endpoint(client):
    pandas = pytest.importorskip("pandas")
    first = reference_payload("fish_species")
    second = {**first, "id": "11111111-2222-3333-4444-555555555555", "name": "stellatus"}
    second["display_name"] = "Abalistes stellatus"

    respx.get(f"{BASE_URL}fishspecies/", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=page([second], count=2))
    )
    respx.get(f"{BASE_URL}fishspecies/").mock(
        return_value=httpx.Response(
            200,
            json=page([first], next_url=f"{BASE_URL}fishspecies/?page=2", count=2),
        )
    )

    frame = client.fish_species.list(limit=1000).to_df()
    assert isinstance(frame, pandas.DataFrame)
    assert list(frame["display_name"]) == ["Abalistes filamentosus", "Abalistes stellatus"]
    assert "trophic_level" in frame.columns
