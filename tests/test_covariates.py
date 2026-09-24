"""`client.covariates`: the STAC catalog, its searches, and zonal stats over its datasets."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
import respx

from datamermaid import (
    CovariateCollection,
    CovariateItem,
    CovariateSearch,
    MermaidClient,
    MermaidConnectionError,
    NotFoundError,
    ZonalSource,
)
from datamermaid.client import DEFAULT_COVARIATES_URL
from datamermaid.resources.covariates import stac_datetime
from datamermaid.resources.zonal_job import resolve_sources

from .conftest import COVARIATES_URL, ZONAL_STATS_URL

COLLECTIONS_URL = f"{COVARIATES_URL}collections"
SEARCH_URL = f"{COVARIATES_URL}search"
RASTER_URL = f"{ZONAL_STATS_URL}raster"
VECTOR_URL = f"{ZONAL_STATS_URL}vector"
COG_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"

SST = {
    "id": "daily_sst",
    "type": "Collection",
    "title": "Daily Global 5km Satellite Sea Surface Temperature (CoralTemp)",
    "keywords": ["SST", "NOAA"],
    "license": "other",
    "sci:doi": "10.3390/rs12233856",
    "providers": [{"name": "NOAA Coral Reef Watch"}],
    "extent": {
        "spatial": {"bbox": [[-180, -90, 180, 90]]},
        "temporal": {"interval": [["1985-01-01T12:00:00+00:00", "2026-07-12T12:00:00+00:00"]]},
    },
    "item_assets": {
        "data": {"type": COG_TYPE, "roles": ["data"]},
        "thumbnail": {"type": "image/png", "roles": ["thumbnail"]},
    },
    "links": [],
}
BAA = {
    **SST,
    "id": "daily_baa",
    "title": "Daily Global 5km Satellite Coral Bleaching Alert Area",
}
GRAVITY = {
    "id": "market_gravity",
    "type": "Collection",
    "title": "Market gravity (fishing pressure)",
    "extent": {"temporal": {"interval": [["2021-12-28T00:00:00Z", None]]}},
    "summaries": {
        "assets": {
            "data": {"type": "application/geoparquet", "roles": ["data"]},
            "thumbnail": {"type": "image/png", "roles": ["thumbnail"]},
        }
    },
    "links": [],
}
LULC = {
    "id": "lulc",
    "type": "Collection",
    "title": "GPW Land Use and Land Cover",
    "summaries": {
        "label:classes": [{"label": "Bare Ground", "value": 1}, {"label": "Woodland", "value": 6}]
    },
    "links": [],
}


def sst_item(day):
    item_id = f"coraltemp_v3.1_202605{day:02d}"
    return {
        "type": "Feature",
        "id": item_id,
        "collection": "daily_sst",
        "properties": {"datetime": f"2026-05-{day:02d}T12:00:00Z"},
        "links": [{"rel": "self", "href": f"{COLLECTIONS_URL}/daily_sst/items/{item_id}"}],
        # The thumbnail comes first, as it does for some catalog items.
        "assets": {
            "thumbnail": {
                "href": f"https://cdn.test/{item_id}.png",
                "type": "image/png",
                "roles": ["thumbnail"],
            },
            "data": {
                "href": f"https://cdn.test/{item_id}.tif",
                "type": COG_TYPE,
                "roles": ["data"],
                "raster:bands": [{"unit": "degrees_Celsius", "scale": 0.01, "nodata": -32768.0}],
            },
        },
    }


GRAVITY_ITEM = {
    "type": "Feature",
    "id": "market_gravity",
    "collection": "market_gravity",
    "properties": {"datetime": "2021-12-28T00:00:00Z"},
    "links": [],
    "assets": {
        "data": {
            "href": "https://cdn.test/market_gravity.parquet",
            "type": "application/geoparquet",
            "roles": ["data"],
            "table:columns": [
                {"name": "grav_NC", "type": "float64", "description": "percentile"},
                {"name": "name", "type": "str"},
                {"name": "geom", "type": "geometry"},
            ],
        }
    },
}


def features(*items, next_link=None, matched=None):
    body = {"type": "FeatureCollection", "features": list(items), "links": []}
    if next_link is not None:
        body["links"].append({"rel": "next", **next_link})
    if matched is not None:
        body["numberMatched"] = matched
    return httpx.Response(200, json=body)


def catalog(*collections, links=()):
    return httpx.Response(200, json={"collections": list(collections), "links": list(links)})


def zonal_ok(key="band_1"):
    return httpx.Response(200, json={key: {"mean": 28.1, "aoi_area": 1.0, "data_area": 1.0}})


def request_bodies(route):
    return [json.loads(call.request.content) for call in route.calls]


# -- configuration ------------------------------------------------------------


def test_default_url_and_trailing_slash(monkeypatch):
    with MermaidClient() as client:
        assert client.covariates_url == DEFAULT_COVARIATES_URL
        assert client.covariates.path == DEFAULT_COVARIATES_URL
    monkeypatch.setenv("MERMAID_COVARIATES_URL", "https://stac.test/api")
    with MermaidClient() as client:
        assert client.covariates_url == "https://stac.test/api/"
    with MermaidClient(covariates_url="https://other.test/stac") as client:
        assert client.covariates_url == "https://other.test/stac/"


# -- datetime normalisation ---------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("2026", "2026-01-01T00:00:00Z/2026-12-31T23:59:59Z"),
        ("2026-02", "2026-02-01T00:00:00Z/2026-02-28T23:59:59Z"),
        ("2026-05-03", "2026-05-03T00:00:00Z/2026-05-03T23:59:59Z"),
        ("2026-05-01/2026-05-30", "2026-05-01T00:00:00Z/2026-05-30T23:59:59Z"),
        ("2026-05-01/..", "2026-05-01T00:00:00Z/.."),
        ("../2026-05", "../2026-05-31T23:59:59Z"),
        ("2026-05-01T06:00:00Z", "2026-05-01T06:00:00Z"),
        (dt.date(2026, 5, 3), "2026-05-03T00:00:00Z/2026-05-03T23:59:59Z"),
        (dt.datetime(2026, 5, 3, 6, 30), "2026-05-03T06:30:00Z"),
        ((dt.date(2026, 5, 1), None), "2026-05-01T00:00:00Z/.."),
        (("2026-05", "2026-06"), "2026-05-01T00:00:00Z/2026-06-30T23:59:59Z"),
    ],
)
def test_stac_datetime(value, expected):
    assert stac_datetime(value) == expected


@pytest.mark.parametrize("value", ["", "2026-13", "2026-02-30", ("2026",)])
def test_stac_datetime_rejects_malformed(value):
    with pytest.raises(ValueError):
        stac_datetime(value)


# -- listing and finding collections -------------------------------------------


@respx.mock
def test_collections_are_fetched_once_and_carry_no_credentials(client):
    route = respx.get(COLLECTIONS_URL).mock(return_value=catalog(SST, GRAVITY))

    first = client.covariates.collections()
    second = client.covariates.collections()

    assert [collection.id for collection in first] == ["daily_sst", "market_gravity"]
    assert [collection.id for collection in second] == ["daily_sst", "market_gravity"]
    assert route.call_count == 1
    assert "authorization" not in route.calls[0].request.headers

    client.covariates.collections(refresh=True)
    assert route.call_count == 2


@respx.mock
def test_collections_follow_next_links(client):
    respx.get(COLLECTIONS_URL, params={"token": "2"}).mock(return_value=catalog(GRAVITY))
    respx.get(COLLECTIONS_URL).mock(
        return_value=catalog(SST, links=[{"rel": "next", "href": f"{COLLECTIONS_URL}?token=2"}])
    )

    assert [c.id for c in client.covariates.collections()] == ["daily_sst", "market_gravity"]


@respx.mock
def test_collections_reject_a_body_that_is_not_a_catalog(client):
    respx.get(COLLECTIONS_URL).mock(return_value=httpx.Response(200, json={"oops": 1}))

    with pytest.raises(MermaidConnectionError):
        client.covariates.collections()


@respx.mock
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("sst", ["daily_sst"]),  # id, ignoring case
        ("Bleaching", ["daily_baa"]),  # title
        ("daily", ["daily_sst", "daily_baa"]),
        ("  FISHING ", ["market_gravity"]),
        ("nothing", []),
    ],
)
def test_search_collections_matches_id_or_title(client, query, expected):
    respx.get(COLLECTIONS_URL).mock(return_value=catalog(SST, BAA, GRAVITY))

    assert [c.id for c in client.covariates.search_collections(query)] == expected


def test_search_collections_needs_a_query(client):
    with pytest.raises(ValueError):
        client.covariates.search_collections("  ")


@respx.mock
def test_collection_uses_the_cached_list_then_the_api(client):
    listing = respx.get(COLLECTIONS_URL).mock(return_value=catalog(SST))
    single = respx.get(f"{COLLECTIONS_URL}/lulc").mock(return_value=httpx.Response(200, json=LULC))
    missing = respx.get(f"{COLLECTIONS_URL}/nope").mock(return_value=httpx.Response(404))

    client.covariates.collections()
    assert client.covariates.collection("daily_sst").title == SST["title"]
    assert listing.call_count == 1
    assert client.covariates.collection("lulc").id == "lulc"
    assert single.call_count == 1
    with pytest.raises(NotFoundError):
        client.covariates.collection("nope")
    assert missing.call_count == 1


# -- describing a collection -------------------------------------------------


def test_collection_metadata(client):
    sst = CovariateCollection(client.covariates, SST)

    assert sst.kind == "raster"
    assert sst.temporal_extent == (
        dt.datetime(1985, 1, 1, 12, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 7, 12, 12, tzinfo=dt.timezone.utc),
    )
    assert sst.bbox == (-180.0, -90.0, 180.0, 90.0)
    assert sst.citation == "https://doi.org/10.3390/rs12233856"
    row = sst.to_dict()
    assert row["id"] == "daily_sst"
    assert row["kind"] == "raster"
    assert CovariateCollection(client.covariates, GRAVITY).to_dict()["end_datetime"] is None


@respx.mock
def test_details_come_from_one_sample_item(client):
    route = respx.post(SEARCH_URL).mock(return_value=features(sst_item(1), matched=15168))
    sst = CovariateCollection(client.covariates, SST)

    assert sst.data_asset.key == "data"  # not the thumbnail listed first
    assert sst.bands == ({"unit": "degrees_Celsius", "scale": 0.01, "nodata": -32768.0},)
    assert route.call_count == 1
    assert request_bodies(route)[0] == {"limit": 1, "collections": ["daily_sst"]}

    text = sst.describe()
    assert "kind:      raster" in text
    assert "items:     15168" in text
    assert "unit=degrees_Celsius, scale=0.01" in text


@respx.mock
def test_vector_details_and_classes(client):
    respx.post(SEARCH_URL).mock(return_value=features(GRAVITY_ITEM))
    gravity = CovariateCollection(client.covariates, GRAVITY)

    assert gravity.kind == "vector"
    assert gravity.data_asset.geometry_column == "geom"
    assert gravity.data_asset.numeric_columns == ["grav_NC"]
    assert "grav_NC (float64): percentile" in gravity.describe()


@respx.mock
def test_classes_fall_back_to_collection_summaries(client):
    respx.post(SEARCH_URL).mock(return_value=features())
    lulc = CovariateCollection(client.covariates, LULC)

    assert lulc.sample_item is None
    assert lulc.classes == {1: "Bare Ground", 6: "Woodland"}


# -- searching items ---------------------------------------------------------


@respx.mock
def test_search_body_and_post_pagination(client):
    route = respx.post(SEARCH_URL).mock(
        side_effect=[
            features(
                sst_item(1),
                next_link={
                    "href": SEARCH_URL,
                    "method": "POST",
                    "body": {"collections": ["daily_sst"], "limit": 100, "token": "next:2"},
                },
            ),
            features(sst_item(2)),
        ]
    )

    search = client.covariates.search(
        "daily_sst", datetime="2026-05", bbox=(170, -20, 180, -10), ids=["a"]
    )
    assert isinstance(search, CovariateSearch)
    assert route.call_count == 0  # lazy
    items = list(search)

    assert [item.id for item in items] == ["coraltemp_v3.1_20260501", "coraltemp_v3.1_20260502"]
    assert all(isinstance(item, CovariateItem) for item in items)
    first, second = request_bodies(route)
    assert first == {
        "limit": 100,
        "collections": ["daily_sst"],
        "datetime": "2026-05-01T00:00:00Z/2026-05-31T23:59:59Z",
        "bbox": [170.0, -20.0, 180.0, -10.0],
        "ids": ["a"],
    }
    assert second["token"] == "next:2"


@respx.mock
def test_search_follows_get_links_and_stops_at_max_items(client):
    respx.post(SEARCH_URL).mock(
        return_value=features(sst_item(1), next_link={"href": f"{SEARCH_URL}?token=2"})
    )
    later = respx.get(SEARCH_URL).mock(return_value=features(sst_item(2), sst_item(3)))

    ids = [item.id for item in client.covariates.search("daily_sst", max_items=2)]

    assert ids == ["coraltemp_v3.1_20260501", "coraltemp_v3.1_20260502"]
    assert later.call_count == 1


@respx.mock
def test_search_intersects_and_filter(client):
    route = respx.post(SEARCH_URL).mock(return_value=features())
    feature = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}}
    cql = {"op": "=", "args": [{"property": "id"}, "x"]}

    list(client.covariates.search(["daily_sst", "daily_baa"], intersects=feature, filter=cql))

    body = request_bodies(route)[0]
    assert body["collections"] == ["daily_sst", "daily_baa"]
    assert body["intersects"] == {"type": "Point", "coordinates": [1, 2]}
    assert body["filter"] == cql
    assert body["filter-lang"] == "cql2-json"


def test_search_validates_arguments(client):
    with pytest.raises(ValueError):
        client.covariates.search("daily_sst", bbox=(1, 2, 3))
    with pytest.raises(ValueError):
        client.covariates.search("daily_sst", bbox=(1, 2, 3, 4), intersects=(1, 2))
    with pytest.raises(ValueError):
        client.covariates.search("daily_sst", max_items=0)
    with pytest.raises(TypeError):
        client.covariates.search("daily_sst", ids="abc")


@respx.mock
def test_count_reads_number_matched(client):
    route = respx.post(SEARCH_URL).mock(return_value=features(matched=30))

    assert client.covariates.search("daily_sst").count() == 30
    assert client.covariates.search("daily_sst", max_items=5).count() == 5
    assert request_bodies(route)[0]["limit"] == 1


@respx.mock
def test_search_rejects_a_body_that_is_not_a_feature_collection(client):
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"detail": "x"}))

    with pytest.raises(MermaidConnectionError):
        list(client.covariates.search("daily_sst"))


# -- zonal stats ---------------------------------------------------------------


@respx.mock
def test_a_search_plugs_into_zonal_stats_and_picks_the_data_asset(client):
    respx.post(SEARCH_URL).mock(return_value=features(sst_item(1)))
    zonal = respx.post(RASTER_URL).mock(return_value=zonal_ok())

    search = client.covariates.search("daily_sst", datetime="2026-05-01")
    results = client.zonal_stats.raster.batch([(178.4, -18.1)], search=search).results()

    assert request_bodies(zonal)[0]["url"] == "https://cdn.test/coraltemp_v3.1_20260501.tif"
    assert results[0].stac["asset"] == "data"


def test_resolve_sources_passes_zonal_sources_through():
    source = ZonalSource("https://cdn.test/a.tif", {"item_id": "a"})

    assert resolve_sources(None, [source, "https://cdn.test/b.tif"], None, None) == (
        source,
        ZonalSource("https://cdn.test/b.tif"),
    )


@respx.mock
def test_raster_zonal_stats(client):
    respx.post(SEARCH_URL).mock(return_value=features(sst_item(1), sst_item(2)))
    zonal = respx.post(RASTER_URL).mock(return_value=zonal_ok())
    sst = CovariateCollection(client.covariates, SST)

    batch = sst.zonal_stats(
        [(178.4, -18.1)], datetime="2026-05", stats=["mean"], radius=500, labels=["site-a"]
    )
    results = batch.results()

    urls = sorted(body["url"] for body in request_bodies(zonal))
    assert urls == [
        "https://cdn.test/coraltemp_v3.1_20260501.tif",
        "https://cdn.test/coraltemp_v3.1_20260502.tif",
    ]
    assert all(body["stats"] == ["mean"] for body in request_bodies(zonal))
    assert {result.label for result in results} == {"site-a"}
    assert sorted(result.stac["item_id"] for result in results) == [
        "coraltemp_v3.1_20260501",
        "coraltemp_v3.1_20260502",
    ]


@respx.mock
def test_vector_zonal_stats_fills_columns_and_geometry_column(client):
    respx.post(SEARCH_URL).mock(return_value=features(GRAVITY_ITEM))
    zonal = respx.post(VECTOR_URL).mock(return_value=zonal_ok("grav_NC"))
    gravity = CovariateCollection(client.covariates, GRAVITY)

    result = gravity.zonal_stats([(178.4, -18.1)], radius=5000).results()[0]

    body = request_bodies(zonal)[0]
    assert body["url"] == "https://cdn.test/market_gravity.parquet"
    assert body["columns"] == ["grav_NC"]
    assert body["geometry_column"] == "geom"
    assert result["grav_NC"]["mean"] == 28.1


@respx.mock
def test_vector_zonal_stats_respects_explicit_columns(client):
    respx.post(SEARCH_URL).mock(return_value=features(GRAVITY_ITEM))
    zonal = respx.post(VECTOR_URL).mock(return_value=zonal_ok("name"))
    gravity = CovariateCollection(client.covariates, GRAVITY)

    gravity.zonal_stats(
        [(178.4, -18.1)], columns=["name"], geometry_column="other", stats=["majority"]
    ).results()

    body = request_bodies(zonal)[0]
    assert body["columns"] == ["name"]
    assert body["geometry_column"] == "other"


@respx.mock
def test_prepare_zonal_stats_counts_without_computing(client):
    respx.post(SEARCH_URL).mock(return_value=features(sst_item(1), sst_item(2), sst_item(3)))
    zonal = respx.post(RASTER_URL).mock(return_value=zonal_ok())
    sst = CovariateCollection(client.covariates, SST)

    job = sst.prepare_zonal_stats([(178.4, -18.1), (178.5, -18.1)], datetime="2026-05")

    assert job.request_count == 6
    assert zonal.call_count == 0


@respx.mock
def test_zonal_stats_errors(client):
    respx.post(SEARCH_URL).mock(side_effect=[features(), features(sst_item(1))])
    sst = CovariateCollection(client.covariates, SST)

    with pytest.raises(ValueError, match="no items"):
        sst.zonal_stats([(178.4, -18.1)], datetime="1900")
    with pytest.raises(TypeError, match="raster"):
        sst.zonal_stats([(178.4, -18.1)], columns=["x"])


@respx.mock
def test_zonal_stats_rejects_an_asset_it_cannot_read(client):
    respx.post(SEARCH_URL).mock(return_value=features(sst_item(1)))
    sst = CovariateCollection(client.covariates, SST)

    with pytest.raises(ValueError, match="neither"):
        sst.zonal_stats([(178.4, -18.1)], asset="thumbnail")
    with pytest.raises(ValueError, match="no asset"):
        sst.zonal_stats([(178.4, -18.1)], asset="missing")
