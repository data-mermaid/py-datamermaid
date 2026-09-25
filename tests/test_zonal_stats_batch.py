"""`batch()` on the zonal stats endpoints: one POST per AOI, labels, and the throttle gate."""

from __future__ import annotations

import json
import threading

import httpx
import pytest
import respx

from datamermaid import Batch, MermaidAPIError, MermaidClient, Site, Stat, WeightingMethod
from datamermaid.resources.zonal_stats import (
    BatchItem,
    ResponseCache,
    _expand_aois,
    _resolve_labels,
)

from .conftest import ZONAL_STATS_URL, load_fixture

RASTER_URL = f"{ZONAL_STATS_URL}raster"
VECTOR_STAC_URL = f"{ZONAL_STATS_URL}vector/stac"
COG = "https://example.test/cogs/depth.tif"
STAC_ITEM = "https://example.test/stac/items/habitat.json"
POINTS = [{"type": "Point", "coordinates": [178.0 + index / 10, -18.1]} for index in range(3)]
POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [[178.0, -18.0], [178.0, -18.2], [178.4, -18.2], [178.4, -18.0], [178.0, -18.0]]
    ],
}


def zonal_payload(name):
    return load_fixture("zonal_stats_responses")[name]


def bodies(route):
    """The request bodies, in the order the AOIs were given.

    Requests run in parallel, so the wire order is not the input order; sorting
    by the AOI's first coordinate (the test points are evenly spaced) puts them
    back.
    """

    def key(body):
        coordinates = body["aoi"]["coordinates"]
        return coordinates[0] if isinstance(coordinates[0], float) else float("inf")

    return sorted((json.loads(call.request.content) for call in route.calls), key=key)


def ok():
    return httpx.Response(200, json=zonal_payload("raster"))


class FeatureCollectionLike:
    """Stands in for a GeoDataFrame: only ``__geo_interface__`` is looked at."""

    def __init__(self, features):
        self.features = features

    @property
    def __geo_interface__(self):
        return {"type": "FeatureCollection", "features": self.features}

    def __iter__(self):
        raise AssertionError("a FeatureCollection is expanded, never iterated")


class GeometryLike:
    """Stands in for a shapely geometry."""

    def __init__(self, geometry):
        self.geometry = geometry

    @property
    def __geo_interface__(self):
        return self.geometry


def feature(index, **extra):
    return {"type": "Feature", "geometry": POINTS[index], "properties": {}, **extra}


# -- the requests -----------------------------------------------------------


@respx.mock
def test_batch_issues_one_post_per_aoi_in_order(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())

    batch = client.zonal_stats.raster.batch(POINTS, url=COG, stats=["mean"])
    assert isinstance(batch, Batch)
    assert route.call_count == 3
    assert len(batch) == 3

    results = batch.results()

    assert route.call_count == 3
    assert [result.label for result in results] == [0, 1, 2]
    assert [result.aoi for result in results] == POINTS
    assert all(result.source == COG for result in results)
    assert all(result["band_1"]["mean"] == 12.3 for result in results)
    for body, point in zip(bodies(route), POINTS, strict=True):
        assert body == {"aoi": point, "url": COG, "stats": ["mean"], "approx_stats": False}


@respx.mock
def test_batch_sends_every_raster_option(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())

    client.zonal_stats.raster.batch(
        POINTS[:1], url=COG, stats=["mean", "count"], radius=500, bands=[1, 2], approx_stats=True
    ).results()

    assert bodies(route) == [
        {
            "aoi": {**POINTS[0], "radius": 500},
            "url": COG,
            "stats": ["mean", "count"],
            "bands": [1, 2],
            "approx_stats": True,
        }
    ]


@respx.mock
def test_batch_sends_every_vector_stac_option(client):
    """The other routes share the machinery; the vector STAC one has the most options."""

    route = respx.post(VECTOR_STAC_URL).mock(
        return_value=httpx.Response(200, json=zonal_payload("vector"))
    )

    batch = client.zonal_stats.vector_stac.batch(
        POINTS,
        url=STAC_ITEM,
        asset="data",
        columns=["depth"],
        stats=[Stat.MEAN],
        weighting_method=WeightingMethod.RATIO,
        radius=250,
    )
    results = batch.results()

    assert route.call_count == len(POINTS)
    assert bodies(route) == [
        {
            "aoi": {**point, "radius": 250},
            "url": STAC_ITEM,
            "stats": ["mean"],
            "asset": "data",
            "columns": ["depth"],
            "weighting_method": "ratio",
            "approx_stats": False,
        }
        for point in POINTS
    ]
    assert [result["depth"]["mean"] for result in results] == [11.2, 11.2, 11.2]


def test_vector_batch_rejects_bad_options_before_any_request(client):
    with respx.mock:
        with pytest.raises(ValueError, match="columns"):
            client.zonal_stats.vector.batch(POINTS, url=STAC_ITEM, columns=[])
        with pytest.raises(ValueError, match="weighting_method"):
            client.zonal_stats.vector.batch(
                POINTS, url=STAC_ITEM, columns=["depth"], weighting_method="foo"
            )
        with pytest.raises(ValueError, match="average"):
            client.zonal_stats.vector_stac.batch(
                POINTS, url=STAC_ITEM, columns=["depth"], stats=["average"]
            )
        with pytest.raises(ValueError, match="average"):
            client.zonal_stats.raster.batch(POINTS, url=COG, stats=["average"])
        assert respx.calls.call_count == 0


@respx.mock
def test_batch_finishes_before_returning_and_reads_make_no_requests(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())
    batch = client.zonal_stats.raster.batch(POINTS, url=COG)

    assert batch[1].label == 1
    assert route.call_count == 3

    assert [result.label for result in batch] == [0, 1, 2]
    assert route.call_count == 3
    batch.results()
    assert route.call_count == 3


@respx.mock
def test_batch_carries_no_credentials():
    route = respx.post(RASTER_URL).mock(return_value=ok())
    with MermaidClient(api_key="mmd_key.secret", backoff_factor=0.0) as client:
        client.zonal_stats.raster.batch(POINTS, url=COG).results()
    for call in route.calls:
        assert "Authorization" not in call.request.headers


@respx.mock
def test_batch_matches_stats_body_for_body(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())
    endpoint = client.zonal_stats.raster

    single = endpoint.stats(POINTS[0], url=COG, stats=["mean"], radius=250, bands=[1], label=0)
    (batched,) = endpoint.batch(POINTS[:1], url=COG, stats=["mean"], radius=250, bands=[1])

    assert bodies(route)[0] == bodies(route)[1]
    assert single == batched


@respx.mock
def test_base_endpoint_batch_forwards_options(client):
    """The generic endpoint's ``batch`` takes ``**options`` like its ``stats``."""

    from datamermaid.resources.zonal_stats import BaseZonalStats

    class VectorStats(BaseZonalStats):
        route = "vector"

    route = respx.post(f"{ZONAL_STATS_URL}vector").mock(return_value=ok())
    endpoint = VectorStats(client.zonal_stats)

    endpoint.batch(POINTS[:1], url=COG, columns=["depth"], missing=None).results()

    assert bodies(route) == [{"aoi": POINTS[0], "url": COG, "columns": ["depth"]}]


@respx.mock
def test_batch_uses_max_workers(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())
    batch = client.zonal_stats.raster.batch(POINTS, url=COG, max_workers=2)
    assert batch.max_workers == 2
    assert repr(batch) == "<Batch results=3 max_workers=2>"
    batch.results()
    assert route.call_count == 3


def test_batch_rejects_bad_options_before_any_request(client):
    with respx.mock:
        with pytest.raises(ValueError, match="max_workers"):
            client.zonal_stats.raster.batch(POINTS, url=COG, max_workers=0)
        with pytest.raises(ValueError, match="url"):
            client.zonal_stats.raster.batch(POINTS, url="")
        with pytest.raises(ValueError, match="bands"):
            client.zonal_stats.raster.batch(POINTS, url=COG, bands=[])
        with pytest.raises(ValueError, match="errors"):
            client.zonal_stats.raster.batch(POINTS, url=COG, errors="ignore")
        assert respx.calls.call_count == 0


def test_a_misspelled_or_missing_option_names_batch_not_prepare(client):
    with respx.mock:
        with pytest.raises(
            TypeError, match=r"batch\(\) got an unexpected keyword argument 'bandz'"
        ):
            client.zonal_stats.raster.batch(POINTS, url=COG, bandz=[1])
        with pytest.raises(TypeError, match=r"batch\(\) missing 1 required keyword-only argument"):
            client.zonal_stats.vector.batch(POINTS, url=COG)
        assert respx.calls.call_count == 0


# -- to_df ------------------------------------------------------------------


@respx.mock
def test_to_df_is_one_wide_row_per_aoi(client):
    pandas = pytest.importorskip("pandas")
    respx.post(RASTER_URL).mock(return_value=ok())

    frame = client.zonal_stats.raster.batch(POINTS, url=COG, stats=["mean"]).to_df()

    assert isinstance(frame, pandas.DataFrame)
    assert len(frame) == 3
    assert frame["label"].tolist() == [0, 1, 2]
    assert "band_1_mean" in frame.columns
    assert frame["band_1_mean"].tolist() == [12.3, 12.3, 12.3]


@respx.mock
def test_to_df_keeps_the_label_of_a_failed_aoi(client):
    pytest.importorskip("pandas")
    respx.post(RASTER_URL).mock(return_value=ok())
    aois = [POINTS[0], {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, POINTS[2]]

    frame = client.zonal_stats.raster.batch(
        aois, url=COG, labels=["a", "b", "c"], errors="return"
    ).to_df()

    assert frame["label"].tolist() == ["a", "b", "c"]
    assert frame.loc[1, "error_type"] == "ValueError"
    assert frame["error"].isna().tolist() == [True, False, True]


# -- labels -----------------------------------------------------------------


@respx.mock
def test_sites_are_labelled_by_id(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())
    sites = [
        Site(id=f"site-{index}", name=f"Site {index}", location=point)
        for index, point in enumerate(POINTS)
    ]

    results = client.zonal_stats.raster.batch(sites, url=COG, radius=100).results()

    assert [result.label for result in results] == ["site-0", "site-1", "site-2"]
    assert [body["aoi"] for body in bodies(route)] == [{**point, "radius": 100} for point in POINTS]


@respx.mock
def test_a_site_without_a_location_fails_at_its_position(client):
    respx.post(RASTER_URL).mock(return_value=ok())
    sites = [Site(id="a", location=POINTS[0]), Site(id="b"), Site(id="c", location=POINTS[2])]

    results = client.zonal_stats.raster.batch(sites, url=COG, errors="return").results()

    assert results[0].label == "a"
    assert isinstance(results[1].error, ValueError)
    assert "'b'" in str(results[1])
    assert results[2].label == "c"


@respx.mock
def test_a_feature_collection_expands_to_its_features(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())
    frame = FeatureCollectionLike([feature(0, id="0"), feature(1, id="1"), feature(2, id="2")])

    results = client.zonal_stats.raster.batch(frame, url=COG).results()

    assert route.call_count == 3
    assert [result.label for result in results] == ["0", "1", "2"]
    assert [body["aoi"] for body in bodies(route)] == POINTS


@respx.mock
def test_a_raw_geojson_feature_collection_is_accepted_too(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())
    collection = {"type": "FeatureCollection", "features": [feature(0, id="x"), feature(1)]}

    results = client.zonal_stats.raster.batch(collection, url=COG).results()

    assert route.call_count == 2
    assert [result.label for result in results] == ["x", 1]


@respx.mock
def test_features_without_ids_fall_back_to_their_position(client):
    respx.post(RASTER_URL).mock(return_value=ok())
    results = client.zonal_stats.raster.batch([feature(0), feature(1, id="b")], url=COG).results()
    assert [result.label for result in results] == [0, "b"]


@respx.mock
def test_geo_interface_objects_are_accepted(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())
    shapes = [GeometryLike(POINTS[0]), GeometryLike(POLYGON), GeometryLike(feature(2, id="f"))]

    results = client.zonal_stats.raster.batch(shapes, url=COG, max_workers=1).results()

    sent = [json.loads(call.request.content)["aoi"] for call in route.calls]
    assert sent == [POINTS[0], POLYGON, POINTS[2]]
    assert [result.label for result in results] == [0, 1, "f"]
    assert [result.aoi for result in results] == [POINTS[0], POLYGON, POINTS[2]]


@respx.mock
def test_explicit_labels_win(client):
    respx.post(RASTER_URL).mock(return_value=ok())
    sites = [Site(id="a", location=POINTS[0]), Site(id="b", location=POINTS[1])]

    results = client.zonal_stats.raster.batch(sites, url=COG, labels=["first", "second"]).results()

    assert [result.label for result in results] == ["first", "second"]


def test_wrong_length_labels_raise_before_any_request(client):
    with respx.mock:
        with pytest.raises(ValueError, match="labels has 2 entries for 3 aois"):
            client.zonal_stats.raster.batch(POINTS, url=COG, labels=["a", "b"])
        with pytest.raises(TypeError, match="labels"):
            client.zonal_stats.raster.batch(POINTS, url=COG, labels="abc")
        assert respx.calls.call_count == 0


def test_a_single_geometry_is_not_a_batch(client):
    with pytest.raises(TypeError, match="FeatureCollection"):
        client.zonal_stats.raster.batch(POINTS[0], url=COG)
    with pytest.raises(TypeError, match="aois"):
        client.zonal_stats.raster.batch("POINT(1 2)", url=COG)
    with pytest.raises(TypeError, match="aois"):
        client.zonal_stats.raster.batch(42, url=COG)


def test_expand_and_resolve_helpers():
    assert _expand_aois(iter(POINTS)) == POINTS
    assert _expand_aois({"type": "FeatureCollection"}) == []
    assert _resolve_labels(POINTS, None) == [0, 1, 2]
    assert _resolve_labels(POINTS, iter("abc")) == ["a", "b", "c"]
    assert BatchItem(POINTS[0], "x") == (POINTS[0], "x")


@respx.mock
def test_batch_inputs_are_items(client):
    respx.post(RASTER_URL).mock(return_value=ok())
    batch = client.zonal_stats.raster.batch(POINTS[:1], url=COG, labels=["only"])
    assert [(task.aoi, task.label, task.source.url) for task in batch.inputs] == [
        (POINTS[0], "only", COG)
    ]


# -- cache ------------------------------------------------------------------


@respx.mock
def test_a_cached_batch_is_answered_without_requests(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())
    cache: dict = {}

    first = client.zonal_stats.raster.batch(POINTS, url=COG, cache=cache).results()
    second = client.zonal_stats.raster.batch(POINTS, url=COG, cache=cache).results()

    assert route.call_count == 3
    assert len(cache) == 3
    assert [result.stats for result in second] == [result.stats for result in first]


@respx.mock
def test_cached_results_take_the_labels_of_the_new_batch(client):
    respx.post(RASTER_URL).mock(return_value=ok())
    cache: dict = {}

    client.zonal_stats.raster.batch(POINTS, url=COG, labels=["a", "b", "c"], cache=cache)
    results = client.zonal_stats.raster.batch(
        POINTS, url=COG, labels=["x", "y", "z"], cache=cache
    ).results()

    assert [result.label for result in results] == ["x", "y", "z"]


@respx.mock
def test_a_rerun_only_sends_the_requests_that_failed(client):
    failing = {"on": True}
    route = respx.post(RASTER_URL).mock(
        side_effect=lambda request: (
            httpx.Response(400, json=zonal_payload("raster_error"))
            if failing["on"] and json.loads(request.content)["aoi"] == POINTS[1]
            else ok()
        )
    )
    cache: dict = {}

    first = client.zonal_stats.raster.batch(POINTS, url=COG, errors="return", cache=cache)
    assert isinstance(first.results()[1].error, MermaidAPIError)
    assert len(cache) == 2

    failing["on"] = False
    sent_before = route.call_count
    second = client.zonal_stats.raster.batch(POINTS, url=COG, cache=cache).results()

    resent = [json.loads(call.request.content)["aoi"] for call in route.calls[sent_before:]]
    assert resent == [POINTS[1]]
    assert [result.label for result in second] == [0, 1, 2]


@respx.mock
def test_different_options_do_not_share_a_cache_entry(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())
    cache: dict = {}

    client.zonal_stats.raster.batch(POINTS[:1], url=COG, stats=["mean"], cache=cache)
    client.zonal_stats.raster.batch(POINTS[:1], url=COG, stats=["max"], cache=cache)
    client.zonal_stats.raster.batch(POINTS[:1], url=COG, stats=["mean"], radius=100, cache=cache)

    assert route.call_count == 3
    assert len(cache) == 3


@respx.mock
@pytest.mark.parametrize("disabled", [False, None])
def test_cache_false_or_none_sends_every_request(client, disabled):
    route = respx.post(RASTER_URL).mock(return_value=ok())

    client.zonal_stats.raster.batch(POINTS, url=COG, cache=disabled)
    client.zonal_stats.raster.batch(POINTS, url=COG, cache=disabled)

    assert route.call_count == 6
    assert len(client.zonal_stats.cache) == 0


@respx.mock
def test_batches_use_the_client_cache_by_default(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())

    client.zonal_stats.raster.batch(POINTS, url=COG)
    client.zonal_stats.raster.batch(POINTS, url=COG)

    assert route.call_count == 3
    assert len(client.zonal_stats.cache) == 3


@respx.mock
def test_clearing_the_client_cache_sends_the_requests_again(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())

    client.zonal_stats.raster.batch(POINTS, url=COG)
    client.zonal_stats.cache.clear()
    client.zonal_stats.raster.batch(POINTS, url=COG)

    assert route.call_count == 6


def test_a_bad_cache_is_rejected_before_any_request(client):
    with pytest.raises(TypeError, match="cache must be"):
        client.zonal_stats.raster.batch(POINTS, url=COG, cache="yes")


def test_response_cache_drops_the_least_recently_used_entry():
    cache = ResponseCache(maxsize=2)
    cache["a"] = 1
    cache["b"] = 2
    assert cache["a"] == 1
    cache["c"] = 3

    assert list(cache) == ["a", "c"]
    assert cache.get("b") is None
    assert repr(cache) == "ResponseCache(size=2, maxsize=2)"


@pytest.mark.parametrize("maxsize", [0, -1, 1.5, True])
def test_response_cache_needs_a_positive_integer_size(maxsize):
    with pytest.raises(ValueError, match="maxsize"):
        ResponseCache(maxsize=maxsize)


# -- errors -----------------------------------------------------------------


@respx.mock
def test_a_failing_aoi_does_not_spoil_the_others(client):
    respx.post(RASTER_URL).mock(
        side_effect=lambda request: (
            httpx.Response(400, json=zonal_payload("raster_error"))
            if json.loads(request.content)["aoi"] == POINTS[1]
            else ok()
        )
    )

    with pytest.raises(MermaidAPIError, match="Error opening raster file"):
        client.zonal_stats.raster.batch(POINTS, url=COG, max_workers=1)

    returned = client.zonal_stats.raster.batch(POINTS, url=COG, errors="return").results()
    assert returned[0].label == 0
    assert isinstance(returned[1].error, MermaidAPIError)
    assert returned[2].label == 2


@respx.mock
def test_a_bad_aoi_fails_at_its_position_without_a_request(client):
    route = respx.post(RASTER_URL).mock(return_value=ok())
    aois = [POINTS[0], {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, POINTS[2]]

    results = client.zonal_stats.raster.batch(aois, url=COG, errors="return").results()

    assert route.call_count == 2
    assert results[0].label == 0
    assert isinstance(results[1].error, ValueError)
    assert results[2].label == 2


# -- the throttle gate ------------------------------------------------------


@respx.mock
def test_a_429_on_one_worker_holds_back_the_others(monkeypatch):
    """The gate is shared: after one 429 every later request waits out the delay."""

    clock = {"now": 1000.0}
    sleeps = []
    sends = []
    lock = threading.Lock()

    def monotonic():
        with lock:
            return clock["now"]

    def sleep(seconds):
        with lock:
            sleeps.append(seconds)
            clock["now"] += seconds

    monkeypatch.setattr("datamermaid.client.time.monotonic", monotonic)
    monkeypatch.setattr("datamermaid.client.time.sleep", sleep)

    throttled = {"done": False}

    def respond(request):
        aoi = json.loads(request.content)["aoi"]
        with lock:
            sends.append((aoi["coordinates"][0], clock["now"]))
            first_time = aoi == POINTS[0] and not throttled["done"]
            if first_time:
                throttled["done"] = True
        if first_time:
            return httpx.Response(
                429, headers={"Retry-After": "2"}, json=zonal_payload("gateway_throttled")
            )
        return ok()

    route = respx.post(RASTER_URL).mock(side_effect=respond)

    with MermaidClient(backoff_factor=0.0) as client:
        # Serial so the order of events is deterministic: the first request is
        # throttled, then the retry and every later request must wait.
        results = client.zonal_stats.raster.batch(POINTS, url=COG, max_workers=1).results()

    assert [result.label for result in results] == [0, 1, 2]
    assert route.call_count == 4
    assert 2.0 in sleeps
    first_send = sends[0][1]
    for _, sent_at in sends[1:]:
        assert sent_at >= first_send + 2.0
