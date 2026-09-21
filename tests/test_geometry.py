"""``to_aoi`` normalises every reasonable area of interest into plain GeoJSON.

Pure unit tests: no HTTP, no shapely.  Objects with a ``__geo_interface__`` are
small hand-written classes standing in for shapely geometries and GeoDataFrame
rows.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

import datamermaid
from datamermaid import GeometryLike, HasGeoInterface, Management, Site, to_aoi
from datamermaid.geometry import AOI_TYPES

POINT = {"type": "Point", "coordinates": [178.4, -18.1]}
RING = [[178.0, -18.0], [178.0, -18.2], [178.4, -18.2], [178.4, -18.0], [178.0, -18.0]]
POLYGON = {"type": "Polygon", "coordinates": [RING]}


class Shape:
    """Stands in for a shapely geometry: ``__geo_interface__`` is a geometry."""

    def __init__(self, geometry):
        self.geometry = geometry

    @property
    def __geo_interface__(self):
        return self.geometry


class Row:
    """Stands in for a GeoDataFrame row or pystac item: ``__geo_interface__`` is a Feature."""

    def __init__(self, geometry, **properties):
        self.geometry = geometry
        self.properties = properties

    @property
    def __geo_interface__(self):
        return {"type": "Feature", "properties": self.properties, "geometry": self.geometry}


class Scalar(float):
    """A float subclass, as numpy's ``float64`` is."""


def is_plain(value) -> bool:
    """Every container is a ``list`` and every leaf is exactly a ``float``."""

    if isinstance(value, list):
        return all(is_plain(item) for item in value)
    return type(value) is float


# -- accepted inputs --------------------------------------------------------


def test_a_point_mapping_passes_through_as_a_fresh_dict():
    aoi = to_aoi(POINT)
    assert aoi == POINT
    assert aoi is not POINT
    assert aoi["coordinates"] is not POINT["coordinates"]


def test_a_polygon_mapping_passes_through_as_a_fresh_dict():
    aoi = to_aoi(POLYGON)
    assert aoi == POLYGON
    assert aoi["coordinates"][0] is not RING


def test_a_feature_contributes_its_geometry():
    feature = {"type": "Feature", "id": "s1", "properties": {"name": "x"}, "geometry": POINT}
    assert to_aoi(feature) == POINT


def test_a_feature_without_a_geometry_is_rejected():
    with pytest.raises(ValueError, match="geometry"):
        to_aoi({"type": "Feature", "properties": {}, "geometry": None})


def test_a_geo_interface_returning_a_geometry():
    assert to_aoi(Shape(POLYGON)) == POLYGON
    assert isinstance(Shape(POLYGON), HasGeoInterface)


def test_a_geo_interface_returning_a_feature():
    assert to_aoi(Row(POINT, name="reef")) == POINT


def test_a_geo_interface_must_return_a_mapping():
    with pytest.raises(TypeError, match="__geo_interface__"):
        to_aoi(Shape("POINT(1 2)"))


def test_a_lon_lat_tuple_becomes_a_point():
    assert to_aoi((178.4, -18.1)) == POINT
    assert to_aoi((178, -18)) == {"type": "Point", "coordinates": [178.0, -18.0]}


def test_a_tuple_must_be_exactly_lon_lat():
    with pytest.raises(ValueError, match="two values"):
        to_aoi((178.4, -18.1, 5.0))
    with pytest.raises(ValueError, match="numbers"):
        to_aoi(("178.4", "-18.1"))


def test_a_site_contributes_its_location():
    site = Site(id="s1", name="Anthias Avenue", location=POINT)
    assert to_aoi(site) == POINT
    assert to_aoi(site, radius=250) == {**POINT, "radius": 250}


def test_a_site_without_a_location_names_the_site():
    with pytest.raises(ValueError, match="Anthias Avenue"):
        to_aoi(Site(id="s1", name="Anthias Avenue"))
    with pytest.raises(ValueError, match="'s1'"):
        to_aoi(Site(id="s1"))


def test_a_management_with_a_polygon_boundary_is_accepted():
    assert to_aoi(Management(id="m1", boundary=POLYGON)) == POLYGON


@pytest.mark.parametrize("bad", ["POINT(1 2)", [178.4, -18.1], None, 178.4, object()])
def test_anything_else_is_a_type_error(bad):
    with pytest.raises(TypeError, match="aoi must be"):
        to_aoi(bad)


# -- radius -----------------------------------------------------------------


def test_radius_is_added_to_a_point():
    aoi = to_aoi(POINT, radius=500)
    assert aoi == {"type": "Point", "coordinates": [178.4, -18.1], "radius": 500}
    assert "radius" not in POINT


def test_radius_on_the_input_is_kept_unless_overridden():
    buffered = {**POINT, "radius": 10}
    assert to_aoi(buffered)["radius"] == 10
    assert to_aoi(buffered, radius=20)["radius"] == 20
    assert to_aoi(Shape(buffered))["radius"] == 10
    assert buffered["radius"] == 10


def test_radius_only_applies_to_a_point():
    with pytest.raises(ValueError, match="radius only applies to a Point"):
        to_aoi(POLYGON, radius=500)
    with pytest.raises(ValueError, match="radius"):
        to_aoi({**POLYGON, "radius": 5})


def test_radius_must_be_a_non_negative_number():
    with pytest.raises(ValueError, match="radius must be >= 0"):
        to_aoi(POINT, radius=-1)
    with pytest.raises(TypeError, match="radius"):
        to_aoi(POINT, radius="500")
    assert to_aoi(POINT, radius=0)["radius"] == 0


# -- rejected geometry types -----------------------------------------------


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "MultiPolygon", "coordinates": [[RING]]},
        {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]},
        {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": POINT}]},
        {"type": "GeometryCollection", "geometries": [POINT]},
        {"type": "Hexagon", "coordinates": []},
    ],
    ids=lambda geometry: geometry["type"],
)
def test_other_geometry_types_are_rejected_by_name(geometry):
    with pytest.raises(ValueError) as caught:
        to_aoi(geometry)
    message = str(caught.value)
    assert repr(geometry["type"]) in message
    assert "Point" in message
    assert "Polygon" in message


def test_a_rejected_type_inside_a_geo_interface_is_named_too():
    multi = {"type": "MultiPolygon", "coordinates": [[RING]]}
    with pytest.raises(ValueError, match="'MultiPolygon'"):
        to_aoi(Shape(multi))
    with pytest.raises(ValueError, match="'MultiPolygon'"):
        to_aoi(Row(multi))


def test_a_mapping_without_a_type_or_coordinates_is_rejected():
    with pytest.raises(ValueError, match="None"):
        to_aoi({"coordinates": [178.4, -18.1]})
    with pytest.raises(ValueError, match="coordinates"):
        to_aoi({"type": "Point"})
    with pytest.raises(ValueError, match="coordinates"):
        to_aoi({"type": "Polygon"})


def test_the_accepted_types_are_point_and_polygon():
    assert sorted(AOI_TYPES) == ["Point", "Polygon"]


# -- coordinate validation -------------------------------------------------


def test_a_point_needs_exactly_lon_lat():
    with pytest.raises(ValueError, match="two values"):
        to_aoi({"type": "Point", "coordinates": [178.4, -18.1, 12.0]})
    with pytest.raises(ValueError, match="two values"):
        to_aoi({"type": "Point", "coordinates": [178.4]})
    with pytest.raises(ValueError, match="position"):
        to_aoi({"type": "Point", "coordinates": 178.4})


def test_point_coordinates_must_be_numbers():
    with pytest.raises(ValueError, match="numbers"):
        to_aoi({"type": "Point", "coordinates": ["178.4", "-18.1"]})
    with pytest.raises(ValueError, match="numbers"):
        to_aoi({"type": "Point", "coordinates": [True, False]})


def test_an_unclosed_ring_is_rejected():
    unclosed = [[178.0, -18.0], [178.0, -18.2], [178.4, -18.2], [178.4, -18.0]]
    with pytest.raises(ValueError, match="not closed"):
        to_aoi({"type": "Polygon", "coordinates": [unclosed]})


def test_a_short_ring_is_rejected():
    triangle = [[178.0, -18.0], [178.0, -18.2], [178.0, -18.0]]
    with pytest.raises(ValueError, match="at least four positions"):
        to_aoi({"type": "Polygon", "coordinates": [triangle]})


def test_a_polygon_needs_at_least_one_ring():
    with pytest.raises(ValueError, match="at least one ring"):
        to_aoi({"type": "Polygon", "coordinates": []})
    # A ring passed without its enclosing list is caught position by position.
    with pytest.raises(ValueError, match="ring 0 must be a \\[lon, lat\\] position"):
        to_aoi({"type": "Polygon", "coordinates": RING})
    with pytest.raises(ValueError, match="sequence of rings"):
        to_aoi({"type": "Polygon", "coordinates": 178.0})


def test_a_bad_ring_is_named_by_its_index():
    hole = [[178.1, -18.1], [178.1, -18.15], [178.2, -18.15]]
    with pytest.raises(ValueError, match="ring 1"):
        to_aoi({"type": "Polygon", "coordinates": [RING, hole]})


def test_tuple_coordinates_come_back_as_lists_of_floats():
    aoi = to_aoi({"type": "Polygon", "coordinates": (tuple(tuple(p) for p in RING),)})
    assert aoi == POLYGON
    assert is_plain(aoi["coordinates"])


def test_numpy_like_scalars_come_back_as_plain_floats():
    aoi = to_aoi({"type": "Point", "coordinates": (Scalar(178.4), Scalar(-18.1))})
    assert aoi == POINT
    assert is_plain(aoi["coordinates"])
    assert not isinstance(aoi["coordinates"][0], Scalar)


def test_integer_coordinates_become_floats():
    aoi = to_aoi({"type": "Point", "coordinates": [178, -18]})
    assert aoi["coordinates"] == [178.0, -18.0]
    assert is_plain(aoi["coordinates"])


# -- the returned dict -----------------------------------------------------


def test_the_result_is_isolated_from_the_input():
    source = {"type": "Polygon", "coordinates": [[list(p) for p in RING]]}
    aoi = to_aoi(source)
    aoi["coordinates"][0][0][0] = 0.0
    aoi["radius"] = 5
    assert source == POLYGON
    assert to_aoi(source) == POLYGON


def test_the_result_only_carries_the_documented_keys():
    aoi = to_aoi({**POINT, "bbox": [0, 0, 1, 1], "crs": "EPSG:4326"}, radius=Scalar(5))
    assert set(aoi) == {"type", "coordinates", "radius"}
    assert type(aoi["radius"]) is float


def test_the_result_round_trips_through_json():
    aoi = to_aoi(
        {"type": "Polygon", "coordinates": (tuple((Scalar(x), Scalar(y)) for x, y in RING),)}
    )
    assert json.loads(json.dumps(aoi)) == aoi == POLYGON
    point = to_aoi((Scalar(178.4), Scalar(-18.1)), radius=Scalar(500))
    assert json.loads(json.dumps(point)) == point == {**POINT, "radius": 500}


def test_a_mapping_that_is_not_a_dict_is_accepted():
    class View(Mapping):
        def __init__(self, data):
            self._data = data

        def __getitem__(self, key):
            return self._data[key]

        def __iter__(self):
            return iter(self._data)

        def __len__(self):
            return len(self._data)

    assert to_aoi(View(POINT)) == POINT


# -- public surface ---------------------------------------------------------


def test_to_aoi_is_exported_from_the_package():
    assert datamermaid.to_aoi is to_aoi
    assert "to_aoi" in datamermaid.__all__
    assert "GeometryLike" in datamermaid.__all__
    assert "HasGeoInterface" in datamermaid.__all__
    assert GeometryLike is datamermaid.geometry.GeometryLike
