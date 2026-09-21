"""The Zonal Stats service: another host, no credentials, dynamic response keys."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from datamermaid import (
    DEFAULT_ZONAL_STATS_URL,
    ZONAL_STATS_URL_ENV_VAR,
    MermaidAPIError,
    MermaidClient,
    RateLimitError,
    Site,
    ZonalStatsResult,
    to_aoi,
)
from datamermaid.client import resolve_zonal_stats_url
from datamermaid.pagination import to_dataframe
from datamermaid.resources import RasterStatsEndpoint, ZonalStatsEndpoint, ZonalStatsResource

from .conftest import ZONAL_STATS_URL, load_fixture, project_scoped_payload

RASTER_URL = f"{ZONAL_STATS_URL}raster"
COG = "https://example.test/cogs/depth.tif"
POINT = {"type": "Point", "coordinates": [178.4, -18.1]}
POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [[178.0, -18.0], [178.0, -18.2], [178.4, -18.2], [178.4, -18.0], [178.0, -18.0]]
    ],
}


def zonal_payload(name):
    return load_fixture("zonal_stats_responses")[name]


def body_of(route):
    return json.loads(route.calls.last.request.content)


# -- URL resolution ---------------------------------------------------------


def test_default_zonal_stats_url():
    assert resolve_zonal_stats_url() == DEFAULT_ZONAL_STATS_URL
    assert DEFAULT_ZONAL_STATS_URL == ZONAL_STATS_URL
    assert ZONAL_STATS_URL_ENV_VAR == "MERMAID_ZONAL_STATS_URL"


def test_zonal_stats_url_from_environment(monkeypatch):
    monkeypatch.setenv("MERMAID_ZONAL_STATS_URL", "https://zs.example.test/api/v1/zonal-stats/")
    with MermaidClient() as client:
        assert client.zonal_stats_url == "https://zs.example.test/api/v1/zonal-stats/"


def test_explicit_zonal_stats_url_wins_over_environment(monkeypatch):
    monkeypatch.setenv("MERMAID_ZONAL_STATS_URL", "https://env.example.test/zonal-stats/")
    with MermaidClient(zonal_stats_url="https://kw.example.test/zonal-stats/") as client:
        assert client.zonal_stats_url == "https://kw.example.test/zonal-stats/"


def test_zonal_stats_url_gets_a_trailing_slash():
    assert resolve_zonal_stats_url("https://zs.example.test/zonal-stats") == (
        "https://zs.example.test/zonal-stats/"
    )


def test_zonal_stats_url_is_independent_of_the_base_url():
    with MermaidClient(base_url="https://dev-api.datamermaid.org/v1") as client:
        assert client.zonal_stats_url == DEFAULT_ZONAL_STATS_URL


# -- resource wiring --------------------------------------------------------


def test_zonal_stats_resource_is_cached(client):
    assert isinstance(client.zonal_stats, ZonalStatsResource)
    assert client.zonal_stats is client.zonal_stats
    assert client.zonal_stats.raster is client.zonal_stats.raster
    assert isinstance(client.zonal_stats.raster, RasterStatsEndpoint)
    assert isinstance(client.zonal_stats.raster, ZonalStatsEndpoint)


def test_resource_path_is_the_absolute_service_root(client):
    assert client.zonal_stats.path == ZONAL_STATS_URL
    assert client.zonal_stats.raster.url == RASTER_URL
    assert not client.zonal_stats.raster.url.endswith("/")
    assert repr(client.zonal_stats.raster) == f"RasterStatsEndpoint(url={RASTER_URL!r})"


def test_endpoint_follows_a_custom_service_url():
    with MermaidClient(zonal_stats_url="http://localhost:8000/api/v1/zonal-stats") as client:
        assert client.zonal_stats.raster.url == "http://localhost:8000/api/v1/zonal-stats/raster"


# -- the request ------------------------------------------------------------


@respx.mock
def test_raster_stats_posts_the_documented_body(client):
    route = respx.post(RASTER_URL).mock(
        return_value=httpx.Response(200, json=zonal_payload("raster"))
    )

    result = client.zonal_stats.raster.stats(POINT, url=COG, stats=["mean"], radius=500)

    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == "POST"
    assert str(request.url) == RASTER_URL
    assert request.headers["Content-Type"] == "application/json"
    assert body_of(route) == {
        "aoi": {"type": "Point", "coordinates": [178.4, -18.1], "radius": 500},
        "url": COG,
        "stats": ["mean"],
        "approx_stats": False,
    }
    assert isinstance(result, ZonalStatsResult)


@respx.mock
def test_raster_stats_sends_every_option(client):
    route = respx.post(RASTER_URL).mock(
        return_value=httpx.Response(200, json=zonal_payload("raster_two_bands"))
    )

    client.zonal_stats.raster.stats(
        POLYGON, url=COG, stats=["mean", "count"], bands=[1, 2], approx_stats=True
    )

    assert body_of(route) == {
        "aoi": POLYGON,
        "url": COG,
        "stats": ["mean", "count"],
        "bands": [1, 2],
        "approx_stats": True,
    }


@respx.mock
def test_omitted_options_are_left_out_of_the_body(client):
    route = respx.post(RASTER_URL).mock(
        return_value=httpx.Response(200, json=zonal_payload("raster"))
    )

    client.zonal_stats.raster.stats(POINT, url=COG)

    body = body_of(route)
    assert body == {"aoi": POINT, "url": COG, "approx_stats": False}
    assert "stats" not in body
    assert "bands" not in body
    assert "radius" not in body["aoi"]


@respx.mock
def test_calling_the_endpoint_is_the_same_as_stats(client):
    route = respx.post(RASTER_URL).mock(
        return_value=httpx.Response(200, json=zonal_payload("raster"))
    )

    called = client.zonal_stats.raster(POINT, url=COG, stats=["mean"], radius=500, bands=[1])
    explicit = client.zonal_stats.raster.stats(
        POINT, url=COG, stats=["mean"], radius=500, bands=[1]
    )

    assert route.call_count == 2
    assert json.loads(route.calls[0].request.content) == json.loads(route.calls[1].request.content)
    assert called == explicit


@respx.mock
def test_no_authorization_header_is_sent_to_the_service():
    route = respx.post(RASTER_URL).mock(
        return_value=httpx.Response(200, json=zonal_payload("raster"))
    )
    with MermaidClient(api_key="mmd_key.secret") as client:
        client.zonal_stats.raster.stats(POINT, url=COG)
        request = route.calls.last.request
        assert "Authorization" not in request.headers
        assert "authorization" not in {name.lower() for name in request.headers}
        # The same client still authenticates against the MERMAID API itself.
        me = respx.get("https://api.datamermaid.org/v1/me/").mock(
            return_value=httpx.Response(200, json={"id": "1"})
        )
        client.me()
        assert me.calls.last.request.headers["Authorization"] == "Bearer mmd_key.secret"


@respx.mock
def test_the_user_agent_and_extra_headers_still_apply(client):
    route = respx.post(RASTER_URL).mock(
        return_value=httpx.Response(200, json=zonal_payload("raster"))
    )
    client.zonal_stats.raster.stats(POINT, url=COG)
    assert route.calls.last.request.headers["User-Agent"].startswith("datamermaid/")
    assert route.calls.last.request.headers["Accept"] == "application/json"


@respx.mock
def test_the_input_aoi_is_not_mutated(client):
    respx.post(RASTER_URL).mock(return_value=httpx.Response(200, json=zonal_payload("raster")))
    aoi = dict(POINT)
    client.zonal_stats.raster.stats(aoi, url=COG, radius=250)
    assert aoi == POINT


@respx.mock
def test_a_site_and_a_geo_interface_object_reach_the_wire_as_geojson(client):
    """The AOI is normalised by ``to_aoi`` before it is sent."""

    route = respx.post(RASTER_URL).mock(
        return_value=httpx.Response(200, json=zonal_payload("raster"))
    )
    site = Site.from_api(project_scoped_payload("sites"))
    assert site.location == {"type": "Point", "coordinates": [179.2251, -17.97855]}

    client.zonal_stats.raster.stats(site, url=COG, radius=500)
    assert body_of(route)["aoi"] == {
        "type": "Point",
        "coordinates": [179.2251, -17.97855],
        "radius": 500,
    }

    class Shape:
        @property
        def __geo_interface__(self):
            return {
                "type": "Feature",
                "properties": {"name": "reef"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [tuple(map(tuple, POLYGON["coordinates"][0]))],
                },
            }

    client.zonal_stats.raster.stats(Shape(), url=COG)
    assert body_of(route)["aoi"] == POLYGON

    client.zonal_stats.raster.stats((178.4, -18.1), url=COG)
    assert body_of(route)["aoi"] == POINT


# -- argument validation ----------------------------------------------------


@pytest.mark.parametrize("bands", [[], (), [0.5], ["1"], [True]])
def test_bad_bands_are_rejected_before_any_request(client, bands):
    with respx.mock, pytest.raises((TypeError, ValueError)):
        client.zonal_stats.raster.stats(POINT, url=COG, bands=bands)
    assert respx.calls.call_count == 0


def test_bands_must_be_a_sequence(client):
    with pytest.raises(TypeError, match="bands"):
        client.zonal_stats.raster.stats(POINT, url=COG, bands="1")


def test_stats_must_be_a_sequence_not_a_string(client):
    with pytest.raises(TypeError, match="stats"):
        client.zonal_stats.raster.stats(POINT, url=COG, stats="mean")


def test_url_must_be_a_non_empty_string(client):
    with pytest.raises(ValueError, match="url"):
        client.zonal_stats.raster.stats(POINT, url="")


@pytest.mark.parametrize(
    ("aoi", "error"),
    [
        ("POINT(1 2)", TypeError),
        ([178.4, -18.1], TypeError),
        (None, TypeError),
        ({"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, ValueError),
        ({"coordinates": [178.4, -18.1]}, ValueError),
        ({"type": "Point"}, ValueError),
    ],
)
def test_bad_aoi_is_rejected(client, aoi, error):
    with pytest.raises(error):
        client.zonal_stats.raster.stats(aoi, url=COG)


def test_radius_only_applies_to_a_point():
    with pytest.raises(ValueError, match="radius"):
        to_aoi(POLYGON, radius=100)
    with pytest.raises(ValueError, match="radius"):
        to_aoi(POINT, radius=-1)


def test_to_aoi_merges_radius_into_a_copy():
    geometry = to_aoi(POINT, radius=500)
    assert geometry == {"type": "Point", "coordinates": [178.4, -18.1], "radius": 500}
    assert "radius" not in POINT
    assert to_aoi(POINT) == POINT
    assert to_aoi(POLYGON) == POLYGON
    # A radius already on the geometry is kept when none is passed.
    assert to_aoi({**POINT, "radius": 10})["radius"] == 10


# -- the result -------------------------------------------------------------


@respx.mock
def test_result_reads_like_a_mapping(client):
    payload = zonal_payload("raster")
    respx.post(RASTER_URL).mock(return_value=httpx.Response(200, json=payload))

    result = client.zonal_stats.raster.stats(POINT, url=COG, radius=500, label="site-1")

    assert result["band_1"]["mean"] == 12.3
    assert result["band_1"]["count"] == 40
    assert result.stats == payload
    assert list(result) == ["band_1"]
    assert list(result.keys()) == ["band_1"]
    assert result.items() == [("band_1", payload["band_1"])]
    assert len(result) == 1
    assert "band_1" in result
    assert "band_2" not in result
    assert result.source == COG
    assert result.label == "site-1"
    assert result.aoi == {"type": "Point", "coordinates": [178.4, -18.1], "radius": 500}
    with pytest.raises(KeyError):
        result["band_9"]


def test_from_api_parses_a_body():
    payload = zonal_payload("raster_two_bands")
    result = ZonalStatsResult.from_api(payload, aoi=POINT, source=COG)
    assert list(result) == ["band_1", "band_2"]
    assert result["band_2"]["mean"] == 0.42
    assert result.label is None


@pytest.mark.parametrize("data", [[], "band_1", None, {"band_1": 12.3}, {"band_1": [1, 2]}])
def test_from_api_rejects_a_body_that_is_not_stats_per_band(data):
    with pytest.raises(TypeError):
        ZonalStatsResult.from_api(data, aoi=POINT, source=COG)


def test_to_dict_is_one_wide_row():
    result = ZonalStatsResult.from_api(
        zonal_payload("raster_two_bands"), aoi=POINT, source=COG, label="site-1"
    )
    assert result.to_dict() == {
        "label": "site-1",
        "source": COG,
        "band_1_mean": 12.3,
        "band_1_count": 40,
        "band_1_aoi_area": 785398.16,
        "band_1_data_area": 781250.0,
        "band_2_mean": 0.42,
        "band_2_count": 40,
        "band_2_aoi_area": 785398.16,
        "band_2_data_area": 781250.0,
    }


def test_to_dict_always_carries_a_label_column():
    result = ZonalStatsResult.from_api(zonal_payload("raster"), aoi=POINT, source=COG)
    row = result.to_dict()
    assert list(row)[:2] == ["label", "source"]
    assert row["label"] is None


def test_to_records_is_long():
    result = ZonalStatsResult.from_api(
        {"band_1": {"mean": 12.3, "count": 40}}, aoi=POINT, source=COG, label="site-1"
    )
    assert result.to_records() == [
        {"label": "site-1", "band": "band_1", "stat": "mean", "value": 12.3},
        {"label": "site-1", "band": "band_1", "stat": "count", "value": 40},
    ]


def test_result_is_frozen():
    result = ZonalStatsResult.from_api(zonal_payload("raster"), aoi=POINT, source=COG)
    with pytest.raises(AttributeError):
        result.label = "x"


def test_to_dataframe_gives_one_row_per_result():
    pandas = pytest.importorskip("pandas")
    result = ZonalStatsResult.from_api(zonal_payload("raster"), aoi=POINT, source=COG, label="a")

    frame = to_dataframe([result])

    assert isinstance(frame, pandas.DataFrame)
    assert len(frame) == 1
    assert "band_1_mean" in frame.columns
    assert list(frame.columns[:2]) == ["label", "source"]
    assert frame.loc[0, "band_1_mean"] == 12.3
    assert frame.loc[0, "label"] == "a"


# -- errors -----------------------------------------------------------------


@respx.mock
def test_a_422_summarises_the_validation_errors(client):
    payload = zonal_payload("validation_error")
    respx.post(RASTER_URL).mock(return_value=httpx.Response(422, json=payload))

    with pytest.raises(MermaidAPIError) as excinfo:
        client.zonal_stats.raster.stats(POINT, url=COG)

    error = excinfo.value
    assert error.status_code == 422
    assert error.url == RASTER_URL
    assert "body.aoi.PointGeometry.coordinates: Field required" in str(error)
    assert "body.url: Field required" in str(error)
    assert "; " in str(error)
    assert error.body == payload
    assert isinstance(error.body["detail"], list)


@respx.mock
def test_a_400_carries_the_string_detail(client):
    payload = zonal_payload("raster_error")
    route = respx.post(RASTER_URL).mock(return_value=httpx.Response(400, json=payload))

    with pytest.raises(MermaidAPIError) as excinfo:
        client.zonal_stats.raster.stats(POINT, url=COG)

    assert excinfo.value.status_code == 400
    assert "Error opening raster file: HTTP response code: 404" in str(excinfo.value)
    assert excinfo.value.body == payload
    assert route.call_count == 1


@respx.mock
def test_a_429_is_retried_then_succeeds():
    route = respx.post(RASTER_URL).mock(
        side_effect=[
            httpx.Response(429, json=zonal_payload("gateway_throttled")),
            httpx.Response(200, json=zonal_payload("raster")),
        ]
    )
    with MermaidClient(backoff_factor=0.0) as client:
        result = client.zonal_stats.raster.stats(POINT, url=COG)

    assert result["band_1"]["mean"] == 12.3
    assert route.call_count == 2


@respx.mock
def test_a_persistent_429_raises_the_gateway_message():
    route = respx.post(RASTER_URL).mock(
        return_value=httpx.Response(429, json=zonal_payload("gateway_throttled"))
    )
    with (
        MermaidClient(backoff_factor=0.0, max_retries=1) as client,
        pytest.raises(RateLimitError, match="Too Many Requests"),
    ):
        client.zonal_stats.raster.stats(POINT, url=COG)
    assert route.call_count == 2
