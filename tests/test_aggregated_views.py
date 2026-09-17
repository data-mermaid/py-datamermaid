"""The aggregated views, ``/projects/{project_id}/<family>/...``.

The sub-route of every view is asserted against the name spelled out in
``ROUTES`` below, which was read off ``mermaid-api``'s ``src/api/urls.py`` on
the ``dev`` branch, so a typo in an :class:`AggregatedViewFamily` fails a test
rather than quietly moving the mock.

The payloads in ``tests/fixtures/aggregated_responses.json`` need credentials,
so they are written from the API's serializers (``BaseSUViewAPISerializer`` and
the belt fish ``*ObsSerializer`` / ``*SUSerializer`` / ``*SESerializer``) rather
than captured from a live response.
"""

from __future__ import annotations

import datetime

import httpx
import pytest
import respx

from datamermaid import AggregatedRecord, PaginatedList
from datamermaid.resources import AGGREGATED_FAMILIES, AggregatedFamilyResource
from datamermaid.resources.aggregated import BLEACHINGQCS, BleachingQCFamilyResource

from .conftest import BASE_URL, PROJECT_ID, aggregated_payload, page

# The full route each family/view pair must hit, under `/projects/{id}/`.
ROUTES = {
    ("beltfishes", "observations"): "beltfishes/obstransectbeltfishes/",
    ("beltfishes", "sample_units"): "beltfishes/sampleunits/",
    ("beltfishes", "sample_events"): "beltfishes/sampleevents/",
    ("benthiclits", "observations"): "benthiclits/obstransectbenthiclits/",
    ("benthiclits", "sample_units"): "benthiclits/sampleunits/",
    ("benthiclits", "sample_events"): "benthiclits/sampleevents/",
    ("benthicpits", "observations"): "benthicpits/obstransectbenthicpits/",
    ("benthicpits", "sample_units"): "benthicpits/sampleunits/",
    ("benthicpits", "sample_events"): "benthicpits/sampleevents/",
    ("benthicpqts", "observations"): "benthicpqts/obstransectbenthicpqts/",
    ("benthicpqts", "sample_units"): "benthicpqts/sampleunits/",
    ("benthicpqts", "sample_events"): "benthicpqts/sampleevents/",
    ("habitatcomplexities", "observations"): "habitatcomplexities/obshabitatcomplexities/",
    ("habitatcomplexities", "sample_units"): "habitatcomplexities/sampleunits/",
    ("habitatcomplexities", "sample_events"): "habitatcomplexities/sampleevents/",
    ("bleachingqcs", "observations"): "bleachingqcs/obscoloniesbleacheds/",
    ("bleachingqcs", "colonies_bleached"): "bleachingqcs/obscoloniesbleacheds/",
    ("bleachingqcs", "quadrat_benthic_percent"): "bleachingqcs/obsquadratbenthicpercents/",
    ("bleachingqcs", "sample_units"): "bleachingqcs/sampleunits/",
    ("bleachingqcs", "sample_events"): "bleachingqcs/sampleevents/",
    ("beltinverts", "observations"): "beltinverts/obstransectbeltinverts/",
    ("beltinverts", "sample_units"): "beltinverts/sampleunits/",
    ("beltinverts", "sample_events"): "beltinverts/sampleevents/",
}

# (family attribute, view method, route), for every view the SDK exposes.
VIEWS = [(family, view, route) for (family, view), route in ROUTES.items()]
IDS = [f"{family}.{view}" for family, view, _ in VIEWS]

# The fixture standing in for each view's records.
PAYLOADS = {
    "observations": "observations",
    "colonies_bleached": "observations",
    "quadrat_benthic_percent": "quadrat_benthic_percent",
    "sample_units": "sample_units",
    "sample_events": "sample_events",
}

PROJECT_URL = f"{BASE_URL}projects/{PROJECT_ID}/"


@pytest.fixture
def project(client):
    """The documented handle: `client.projects(<project id>)`."""

    return client.projects(PROJECT_ID)


@pytest.mark.parametrize(("family", "view", "route"), VIEWS, ids=IDS)
@respx.mock
def test_each_view_lists_rows_from_its_own_route(project, family, view, route):
    payload = aggregated_payload(PAYLOADS[view])
    url = f"{PROJECT_URL}{route}"
    mocked = respx.get(url).mock(return_value=httpx.Response(200, json=page([payload])))

    rows = getattr(getattr(project, family), view)()
    assert isinstance(rows, PaginatedList)
    assert mocked.call_count == 0  # nothing is fetched until the list is used

    row = rows[0]
    assert isinstance(row, AggregatedRecord)
    # Every key the API sent is either a declared field or kept in `extra`.
    assert set(payload) <= set(row.to_dict())
    assert mocked.call_count == 1
    assert str(mocked.calls.last.request.url) == url


@pytest.mark.parametrize(("family", "view", "route"), VIEWS, ids=IDS)
def test_every_view_is_reachable_without_a_request(project, family, view, route):
    """Building a view must not confirm it exists, or laziness is lost."""

    with respx.mock:
        mocked = respx.get(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=page([]))
        )
        rows = getattr(getattr(project, family), view)(limit=10)
        assert isinstance(rows, PaginatedList)
        assert mocked.call_count == 0


@pytest.mark.parametrize("family", [family.family for family in AGGREGATED_FAMILIES])
def test_families_are_built_once_per_context(project, family):
    resource = getattr(project, family)
    assert isinstance(resource, AggregatedFamilyResource)
    assert resource.family.family == family
    assert resource.project_id == PROJECT_ID
    assert resource.path == f"projects/{PROJECT_ID}/{family}/"
    assert getattr(project, family) is resource


def test_the_registry_lists_every_family_on_the_context(project):
    """The context hand-writes its properties; they must match the registry."""

    exposed = {
        family.family: getattr(project, family.family).family for family in AGGREGATED_FAMILIES
    }
    assert exposed == {family.family: family for family in AGGREGATED_FAMILIES}
    assert len(exposed) == 7


@pytest.mark.parametrize(("family", "view", "route"), VIEWS, ids=IDS)
def test_the_family_descriptor_agrees_with_the_route(family, view, route):
    descriptor = {item.family: item for item in AGGREGATED_FAMILIES}[family]
    # `observations` on bleaching is an alias, so it is not a view of its own.
    sub_route = descriptor.views["observations" if view == "colonies_bleached" else view]
    assert descriptor.route(sub_route) == route


def test_bleaching_exposes_both_observation_views(project):
    resource = project.bleachingqcs
    assert isinstance(resource, BleachingQCFamilyResource)
    assert sorted(BLEACHINGQCS.views) == [
        "observations",
        "quadrat_benthic_percent",
        "sample_events",
        "sample_units",
    ]


@respx.mock
def test_filters_are_forwarded_as_query_parameters(project):
    url = f"{PROJECT_URL}beltfishes/obstransectbeltfishes/"
    mocked = respx.get(url).mock(return_value=httpx.Response(200, json=page([])))

    list(
        project.beltfishes.observations(
            sample_date_after="2018-01-01",
            sample_date_before="2020-12-31",
            depth_min=3,
            fish_family="Acanthuridae",
            fields="site_name,biomass_kgha",
            ignored=None,
        )
    )

    params = mocked.calls.last.request.url.params
    assert params["sample_date_after"] == "2018-01-01"
    assert params["sample_date_before"] == "2020-12-31"
    assert params["depth_min"] == "3"
    assert params["fish_family"] == "Acanthuridae"
    assert params["fields"] == "site_name,biomass_kgha"
    assert "ignored" not in params


@respx.mock
def test_a_view_is_lazy_and_crosses_pages_on_demand(project):
    url = f"{PROJECT_URL}beltfishes/obstransectbeltfishes/"
    first_payload = aggregated_payload("observations")
    second_payload = {**first_payload, "id": "aaaaaaaa-0000-0000-0000-000000000002"}

    second = respx.get(url, params={"limit": "1", "page": "2"}).mock(
        return_value=httpx.Response(200, json=page([second_payload], count=2))
    )
    first = respx.get(url, params={"limit": "1"}).mock(
        return_value=httpx.Response(
            200,
            json=page([first_payload], next_url=f"{url}?limit=1&page=2", count=2),
        )
    )

    observations = project.beltfishes.observations(limit=1)
    assert first.call_count == 0

    iterator = iter(observations)
    assert next(iterator).id == first_payload["id"]
    assert first.call_count == 1
    assert second.call_count == 0

    assert next(iterator).id == second_payload["id"]
    assert second.call_count == 1
    with pytest.raises(StopIteration):
        next(iterator)


@respx.mock
def test_rows_keep_their_flat_columns(project):
    payload = aggregated_payload("observations")
    respx.get(f"{PROJECT_URL}beltfishes/obstransectbeltfishes/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    row = project.beltfishes.observations()[0]
    assert row.id == payload["id"]
    assert row.site_name == "Namena South"
    assert row.project_id == PROJECT_ID
    assert row.sample_date == datetime.date(2019, 4, 17)
    assert row.sample_event_id == payload["sample_event_id"]
    # Protocol columns are not declared; they stay in `extra` untouched.
    assert row.extra["fish_taxon"] == "Acanthurus lineatus"
    assert row.extra["biomass_kgha"] == pytest.approx(15.31)
    assert "sample_date" not in row.extra


@respx.mock
def test_sample_unit_rows_have_no_id(project):
    payload = aggregated_payload("sample_units")
    respx.get(f"{PROJECT_URL}beltfishes/sampleunits/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    row = project.beltfishes.sample_units()[0]
    assert row.id is None
    assert row.extra["sample_unit_ids"] == payload["sample_unit_ids"]
    assert row.extra["biomass_kgha"] == pytest.approx(1043.21)


@respx.mock
def test_to_df_gives_one_column_per_field(project):
    pandas = pytest.importorskip("pandas")
    first = aggregated_payload("observations")
    second = {**first, "id": "aaaaaaaa-0000-0000-0000-000000000002", "count": 7}
    url = f"{PROJECT_URL}beltfishes/obstransectbeltfishes/"

    respx.get(url, params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=page([second], count=2))
    )
    respx.get(url).mock(
        return_value=httpx.Response(200, json=page([first], next_url=f"{url}?page=2", count=2))
    )

    frame = project.beltfishes.observations().to_df()
    assert isinstance(frame, pandas.DataFrame)
    # Every field the API sent is a column, plus the declared fields it omitted.
    assert set(first) <= set(frame.columns)
    assert list(frame.columns[:3]) == ["id", "project_id", "project_name"]
    assert list(frame["id"]) == [first["id"], second["id"]]
    assert list(frame["count"]) == [3, 7]
    assert list(frame["fish_taxon"]) == ["Acanthurus lineatus"] * 2
    assert list(frame["sample_date"]) == [datetime.date(2019, 4, 17)] * 2


@respx.mock
def test_project_ids_are_percent_encoded_into_the_path(client):
    traversal = respx.get(
        f"{BASE_URL}projects/..%2F..%2Fme/beltfishes/obstransectbeltfishes/"
    ).mock(return_value=httpx.Response(200, json=page([])))

    list(client.projects("../../me").beltfishes.observations())
    assert traversal.call_count == 1
