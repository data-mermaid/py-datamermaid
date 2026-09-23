from __future__ import annotations

import dataclasses
from datetime import timezone

import pytest

from datamermaid import Management, Me, Project, Site
from datamermaid.models import parse_datetime

from .conftest import project_payload, project_scoped_payload


def test_known_fields_are_typed():
    project = Project.from_api(project_payload())
    assert project.name == "Project 1"
    assert project.countries == ("Fiji",)
    assert project.members == ("11111111-1111-1111-1111-111111111111",)
    assert project.num_sites == 31
    assert project.num_sample_units == 279.0
    assert project.is_demo is False
    assert project.project_admins[0]["name"] == "A Person"
    assert project.created_on is not None
    assert project.created_on.tzinfo is not None


def test_unknown_fields_land_in_extra():
    project = Project.from_api(project_payload(brand_new_field=42))
    assert project.extra == {"brand_new_field": 42}
    assert project.to_dict()["brand_new_field"] == 42


def test_missing_fields_fall_back_to_defaults():
    project = Project.from_api({"id": "abc"})
    assert project.id == "abc"
    assert project.name is None
    assert project.countries == ()
    assert project.extra == {}


def test_unparseable_values_are_preserved_in_extra():
    project = Project.from_api(project_payload(created_on="not a date"))
    assert project.created_on is None
    assert project.extra["created_on"] == "not a date"


def test_a_failed_date_conversion_is_exported_raw():
    me = Me.from_api({"id": "abc", "created_on": "not-a-date"})
    assert me.created_on is None
    assert me.to_dict()["created_on"] == "not-a-date"
    assert me.to_dict(include_extra=False)["created_on"] is None


def test_a_failed_collection_conversion_is_exported_raw():
    project = Project.from_api(project_payload(countries="Fiji", tags=7))
    assert project.countries == ()
    exported = project.to_dict()
    assert exported["countries"] == "Fiji"
    assert exported["tags"] == 7
    assert list(exported).count("countries") == 1


def test_a_failed_nested_conversion_is_exported_raw():
    me = Me.from_api({"id": "abc", "projects": "oops"})
    assert me.projects == ()
    assert me.to_dict()["projects"] == "oops"


def test_null_collections_become_empty_tuples():
    project = Project.from_api(project_payload(created_on=None, countries=None))
    assert project.created_on is None
    assert project.countries == ()
    assert "countries" not in project.extra
    assert ", ".join(project.countries) == ""


def test_null_nested_collection_becomes_an_empty_tuple():
    me = Me.from_api({"id": "abc", "projects": None})
    assert me.projects == ()
    assert len(me.projects) == 0


def test_models_are_frozen():
    project = Project.from_api(project_payload())
    with pytest.raises(dataclasses.FrozenInstanceError):
        project.name = "nope"


def test_data_policies_property():
    project = Project.from_api(project_payload(data_policy_beltfish=10))
    assert project.data_policies["beltfish"] == 10
    assert project.data_policies["benthicpit"] == 50
    assert len(project.data_policies) == 7


def test_to_dict_can_omit_extra():
    project = Project.from_api(project_payload(mystery=1))
    assert "mystery" not in project.to_dict(include_extra=False)


def test_from_api_rejects_non_objects():
    with pytest.raises(TypeError):
        Project.from_api([1, 2, 3])


def test_nested_models_are_parsed():
    me = Me.from_api({"id": "1", "projects": [{"id": "2", "name": "Reef", "role": 90}]})
    assert me.projects[0].name == "Reef"
    assert me.projects[0].num_active_sample_units is None


def test_parse_datetime_handles_z_suffix_and_offsets():
    assert parse_datetime("2024-01-02T03:04:05Z").tzinfo == timezone.utc
    assert parse_datetime("2024-01-02T03:04:05+02:00").utcoffset().total_seconds() == 7200
    assert parse_datetime("2024-01-02T03:04:05").tzinfo == timezone.utc


def test_parse_datetime_rejects_other_types():
    with pytest.raises(TypeError):
        parse_datetime(12345)


def test_site_geo_interface_is_its_location():
    location = {"type": "Point", "coordinates": [179.2251, -17.97855]}
    site = Site.from_api(project_scoped_payload("sites"))
    assert site.__geo_interface__ == location
    assert Site(location=location).__geo_interface__ is location


def test_site_geo_interface_without_a_location_names_the_site():
    with pytest.raises(ValueError, match="Anthias Avenue"):
        _ = Site(id="s1", name="Anthias Avenue").__geo_interface__
    with pytest.raises(TypeError, match="GeoJSON mapping"):
        _ = Site(id="s1", location="POINT(1 2)").__geo_interface__


def test_management_geo_interface_is_its_boundary():
    boundary = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}
    assert Management(boundary=boundary).__geo_interface__ is boundary
    with pytest.raises(ValueError, match="Gau_open"):
        _ = Management.from_api(project_scoped_payload("managements")).__geo_interface__
    with pytest.raises(ValueError, match="'m1'"):
        _ = Management(id="m1").__geo_interface__
