"""The project-scoped endpoints, ``/projects/{project_id}/...``.

The payloads in ``tests/fixtures/project_responses.json`` need credentials, so
unlike the reference fixtures they are written from the API's serializers
(``mermaid-api``'s ``SampleEventSerializer``, ``ObserverSerializer``, the
sample unit serializers and the per-protocol ``*MethodSerializer``s) rather
than captured from a live response.
"""

from __future__ import annotations

import datetime

import httpx
import pytest
import respx

from datamermaid import PaginatedList, Project, ProjectContext
from datamermaid.exceptions import NotFoundError
from datamermaid.models import Observer, SampleEvent
from datamermaid.resources import PROJECT_RESOURCES
from datamermaid.resources.projects import ProjectsResource

from .conftest import BASE_URL, PROJECT_ID, page, project_payload, project_scoped_payload

# The route each context attribute must hit, spelled out so that a wrong
# `ProjectResource.route` fails a test instead of quietly moving the mock.
ROUTES = {
    "sites": "sites/",
    "managements": "managements/",
    "observers": "observers/",
    "project_profiles": "project_profiles/",
    "sample_events": "sampleevents/",
    "fishbelt_transects": "fishbelttransects/",
    "benthic_transects": "benthictransects/",
    "beltfish_methods": "beltfishtransectmethods/",
    "benthiclit_methods": "benthiclittransectmethods/",
    "benthicpit_methods": "benthicpittransectmethods/",
    "benthicpqt_methods": "benthicphotoquadrattransectmethods/",
    "habitatcomplexity_methods": "habitatcomplexitytransectmethods/",
    "bleachingqc_methods": "bleachingquadratcollectionmethods/",
    "beltinvert_methods": "beltinverttransectmethods/",
}

# (attribute on the context, resource class, route), as the context exposes them.
RESOURCES = [(attribute, cls, ROUTES[attribute]) for attribute, cls in PROJECT_RESOURCES]
IDS = [attribute for attribute, _, _ in RESOURCES]

PROJECT_URL = f"{BASE_URL}projects/{PROJECT_ID}/"


@pytest.fixture
def project(client):
    """The documented handle: `client.projects(<project id>)`."""

    return client.projects(PROJECT_ID)


@pytest.mark.parametrize(("attribute", "resource_class", "route"), RESOURCES, ids=IDS)
@respx.mock
def test_list_returns_typed_models_from_the_project_route(
    project, attribute, resource_class, route
):
    payload = project_scoped_payload(attribute)
    url = f"{PROJECT_URL}{route}"
    mocked = respx.get(url).mock(return_value=httpx.Response(200, json=page([payload])))

    records = getattr(project, attribute).list()
    assert isinstance(records, PaginatedList)
    assert mocked.call_count == 0  # nothing is fetched until the list is used

    record = records[0]
    assert isinstance(record, resource_class.model)
    assert record.id == payload["id"]
    # Every key the API sent is either a declared field or kept in `extra`.
    assert set(payload) <= set(record.to_dict())
    assert mocked.call_count == 1
    assert str(mocked.calls.last.request.url) == url


@pytest.mark.parametrize(("attribute", "resource_class", "route"), RESOURCES, ids=IDS)
@respx.mock
def test_get_fetches_one_record_from_the_project_route(project, attribute, resource_class, route):
    payload = project_scoped_payload(attribute)
    url = f"{PROJECT_URL}{route}{payload['id']}/"
    mocked = respx.get(url).mock(return_value=httpx.Response(200, json=payload))

    record = getattr(project, attribute).get(payload["id"])
    assert isinstance(record, resource_class.model)
    assert record.id == payload["id"]
    assert mocked.call_count == 1
    assert str(mocked.calls.last.request.url) == url


@pytest.mark.parametrize(("attribute", "resource_class", "route"), RESOURCES, ids=IDS)
def test_resources_are_built_once_per_context(project, attribute, resource_class, route):
    resource = getattr(project, attribute)
    assert isinstance(resource, resource_class)
    assert resource.route == route
    assert resource.path == f"projects/{PROJECT_ID}/{route}"
    assert resource.project_id == PROJECT_ID
    assert getattr(project, attribute) is resource


def test_the_registry_lists_every_collection_on_the_context(project):
    """The context hand-writes its properties; they must match the registry."""

    exposed = {
        name: type(getattr(project, name))
        for name, attribute in vars(ProjectContext).items()
        if isinstance(attribute, property)
    }
    assert exposed == dict(PROJECT_RESOURCES)


def test_the_same_project_yields_the_same_handle(client):
    assert client.projects(PROJECT_ID) is client.projects(PROJECT_ID)
    assert client.projects("another-project") is not client.projects(PROJECT_ID)


def test_a_project_model_can_stand_in_for_its_id(client):
    model = Project.from_api(project_payload(1))
    assert client.projects(model).project_id == model.id
    assert client.projects(model) is client.projects(model.id)


def test_a_project_handle_needs_an_id(client):
    with pytest.raises(ValueError, match="project id"):
        client.projects("  ")
    with pytest.raises(ValueError, match="project id"):
        client.projects(Project.from_api({"name": "no id yet"}))


def test_repr_names_the_project(project):
    assert repr(project) == f"ProjectContext(project_id={PROJECT_ID!r})"


def test_building_a_handle_issues_no_request(client):
    """Opening a project must not confirm it exists, or laziness is lost."""

    with respx.mock:
        route = respx.get(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=page([]))
        )
        client.projects(PROJECT_ID).beltfish_methods.list(limit=10)
        assert route.call_count == 0


@respx.mock
def test_project_ids_are_percent_encoded_into_the_path(client):
    projects = respx.get(f"{BASE_URL}projects/").mock(
        return_value=httpx.Response(200, json=page([]))
    )
    traversal = respx.get(f"{BASE_URL}projects/..%2F..%2Fme/sites/").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )

    with pytest.raises(NotFoundError):
        client.projects("../../me").sites.list()[0]
    assert projects.call_count == 0
    assert traversal.call_count == 1


@respx.mock
def test_list_is_lazy_and_crosses_pages_on_demand(project):
    url = f"{PROJECT_URL}sampleevents/"
    first_payload = project_scoped_payload("sample_events")
    second_payload = {**first_payload, "id": "cccccccc-0000-0000-0000-000000000002"}

    second = respx.get(url, params={"limit": "1", "page": "2"}).mock(
        return_value=httpx.Response(200, json=page([second_payload], count=2))
    )
    first = respx.get(url, params={"limit": "1"}).mock(
        return_value=httpx.Response(
            200,
            json=page([first_payload], next_url=f"{url}?limit=1&page=2", count=2),
        )
    )

    sample_events = project.sample_events.list(limit=1)
    assert first.call_count == 0

    iterator = iter(sample_events)
    assert next(iterator).id == first_payload["id"]
    assert first.call_count == 1
    assert second.call_count == 0

    assert next(iterator).id == second_payload["id"]
    assert second.call_count == 1
    with pytest.raises(StopIteration):
        next(iterator)


@respx.mock
def test_list_forwards_filters_as_query_parameters(project):
    route = respx.get(f"{PROJECT_URL}sampleevents/").mock(
        return_value=httpx.Response(200, json=page([]))
    )

    site = project_scoped_payload("sample_events")["site"]
    list(project.sample_events.list(site=site, sample_date_after="2019-01-01", ignored=None))

    params = route.calls.last.request.url.params
    assert params["site"] == site
    assert params["sample_date_after"] == "2019-01-01"
    assert "ignored" not in params


@respx.mock
def test_sample_events_parse_dates(project):
    payload = project_scoped_payload("sample_events")
    respx.get(f"{PROJECT_URL}sampleevents/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    sample_event = project.sample_events.list()[0]
    assert sample_event.sample_date == datetime.date(2019, 4, 17)
    assert sample_event.site == payload["site"]
    assert sample_event.management == payload["management"]
    assert sample_event.extra == {}


@respx.mock
def test_transects_keep_their_measurements(project):
    payload = project_scoped_payload("fishbelt_transects")
    respx.get(f"{PROJECT_URL}fishbelttransects/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    transect = project.fishbelt_transects.list()[0]
    assert transect.number == 1
    assert transect.len_surveyed == pytest.approx(50.0)
    assert transect.depth == pytest.approx(5.0)
    assert transect.width == payload["width"]
    assert transect.size_bin == payload["size_bin"]
    assert transect.sample_event == payload["sample_event"]


@respx.mock
def test_method_payloads_expose_their_sample_unit_and_observations(project):
    payload = project_scoped_payload("beltfish_methods")
    respx.get(f"{PROJECT_URL}beltfishtransectmethods/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    method = project.beltfish_methods.list()[0]
    assert isinstance(method.sample_event, SampleEvent)
    assert method.sample_event.sample_date == datetime.date(2019, 4, 17)
    assert [type(observer) for observer in method.observers] == [Observer]
    assert method.observers[0].profile_name == "Ada Lovelace"

    assert method.sample_unit is method.fishbelt_transect
    assert method.sample_unit.len_surveyed == pytest.approx(50.0)
    assert method.observations == {"obs_belt_fishes": tuple(payload["obs_belt_fishes"])}
    # The observation rows stay as the API sent them.
    assert method.observations["obs_belt_fishes"][0]["count"] == 3


@respx.mock
def test_bleaching_methods_carry_both_observation_lists(project):
    payload = project_scoped_payload("bleachingqc_methods")
    respx.get(f"{PROJECT_URL}bleachingquadratcollectionmethods/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    method = project.bleachingqc_methods.list()[0]
    assert method.sample_unit is method.quadrat_collection
    assert method.quadrat_collection.quadrat_size == pytest.approx(1.0)
    assert sorted(method.observations) == ["obs_colonies_bleached", "obs_quadrat_benthic_percent"]
    assert method.observations["obs_colonies_bleached"][0]["count_normal"] == 4


@pytest.mark.parametrize(
    ("attribute", "sample_unit_field"),
    [
        ("beltfish_methods", "fishbelt_transect"),
        ("benthiclit_methods", "benthic_transect"),
        ("benthicpit_methods", "benthic_transect"),
        ("benthicpqt_methods", "quadrat_transect"),
        ("habitatcomplexity_methods", "benthic_transect"),
        ("bleachingqc_methods", "quadrat_collection"),
        ("beltinvert_methods", "beltinvert_transect"),
    ],
)
def test_every_protocol_names_its_sample_unit(attribute, sample_unit_field):
    model = dict(PROJECT_RESOURCES)[attribute].model
    method = model.from_api(project_scoped_payload(attribute))
    assert sample_unit_field == model.SAMPLE_UNIT_FIELD
    assert method.sample_unit is getattr(method, sample_unit_field)
    assert method.sample_unit.id is not None
    assert method.observations
    assert all(rows for rows in method.observations.values())


@respx.mock
def test_unknown_fields_are_kept_in_extra(project):
    payload = {**project_scoped_payload("project_profiles"), "some_new_api_field": 42}
    respx.get(f"{PROJECT_URL}project_profiles/").mock(
        return_value=httpx.Response(200, json=page([payload]))
    )

    membership = project.project_profiles.list()[0]
    assert membership.role == 90
    assert membership.is_admin is True
    assert membership.extra == {"some_new_api_field": 42}
    assert membership.to_dict()["some_new_api_field"] == 42


@respx.mock
def test_to_df_flattens_a_method_list(project):
    pandas = pytest.importorskip("pandas")
    first = project_scoped_payload("beltfish_methods")
    second = {**first, "id": "ffffffff-0000-0000-0000-00000000000a"}
    url = f"{PROJECT_URL}beltfishtransectmethods/"

    respx.get(url, params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=page([second], count=2))
    )
    respx.get(url).mock(
        return_value=httpx.Response(200, json=page([first], next_url=f"{url}?page=2", count=2))
    )

    frame = project.beltfish_methods.list().to_df()
    assert isinstance(frame, pandas.DataFrame)
    assert list(frame["id"]) == [first["id"], second["id"]]
    assert frame["sample_event"][0].sample_date == datetime.date(2019, 4, 17)
    assert len(frame["obs_belt_fishes"][0]) == 1


def test_the_projects_resource_stays_a_list_endpoint(client):
    """Calling the resource must not disturb `.list()` / `.get()`."""

    resource = client.projects
    assert isinstance(resource, ProjectsResource)
    assert resource.path == "projects/"
    assert client.projects is resource
