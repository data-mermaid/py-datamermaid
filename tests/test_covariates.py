"""`client.covariates`: the STAC catalog, its searches, and zonal stats over its datasets.

pystac-client reads the catalog with ``requests``, so ``requests_mock`` stands in
for it; the Zonal Stats service is reached with ``httpx`` and mocked with respx.
"""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
import respx
from pystac_client import ItemSearch
from pystac_client.exceptions import APIError

from datamermaid import CovariateCollection, MermaidClient, ZonalSource
from datamermaid.client import DEFAULT_COVARIATES_URL
from datamermaid.resources.zonal_job import resolve_sources

from .conftest import COVARIATES_URL, ZONAL_STATS_URL

COLLECTIONS_URL = f"{COVARIATES_URL}collections"
SEARCH_URL = f"{COVARIATES_URL}search"
RASTER_URL = f"{ZONAL_STATS_URL}raster"
VECTOR_URL = f"{ZONAL_STATS_URL}vector"
COG_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"

LANDING = {
    "type": "Catalog",
    "id": "stac-fastapi",
    "description": "MERMAID STAC API",
    "stac_version": "1.0.0",
    "conformsTo": [
        "https://api.stacspec.org/v1.0.0/core",
        "https://api.stacspec.org/v1.0.0/collections",
        "https://api.stacspec.org/v1.0.0/item-search",
        "https://api.stacspec.org/v1.0.0/item-search#filter",
        "https://api.stacspec.org/v1.0.0/item-search#sort",
    ],
    "links": [
        {"rel": "self", "href": COVARIATES_URL},
        {"rel": "root", "href": COVARIATES_URL},
        {"rel": "data", "href": COLLECTIONS_URL},
        {"rel": "search", "href": SEARCH_URL, "type": "application/geo+json", "method": "POST"},
    ],
}


def collection(collection_id, title, **fields):
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": collection_id,
        "title": title,
        "description": title,
        "license": "other",
        "extent": {
            "spatial": {"bbox": [[-180, -90, 180, 90]]},
            "temporal": {"interval": [["2021-12-28T00:00:00Z", None]]},
        },
        "links": [
            {"rel": "self", "href": f"{COLLECTIONS_URL}/{collection_id}"},
            {"rel": "root", "href": COVARIATES_URL},
        ],
        **fields,
    }


SST = collection(
    "daily_sst",
    "Daily Global 5km Satellite Sea Surface Temperature (CoralTemp)",
    keywords=["SST", "NOAA"],
    providers=[{"name": "NOAA Coral Reef Watch"}],
    extent={
        "spatial": {"bbox": [[-180, -90, 180, 90]]},
        "temporal": {"interval": [["1985-01-01T12:00:00Z", "2026-07-12T12:00:00Z"]]},
    },
    item_assets={
        "data": {"type": COG_TYPE, "roles": ["data"]},
        "thumbnail": {"type": "image/png", "roles": ["thumbnail"]},
    },
    **{"sci:doi": "10.3390/rs12233856"},
)
BAA = collection("daily_baa", "Daily Global 5km Satellite Coral Bleaching Alert Area")
GRAVITY = collection(
    "market_gravity",
    "Market gravity (fishing pressure)",
    summaries={
        "assets": {
            "data": {"type": "application/geoparquet", "roles": ["data"]},
            "thumbnail": {"type": "image/png", "roles": ["thumbnail"]},
        }
    },
)
LULC = collection(
    "lulc",
    "GPW Land Use and Land Cover",
    summaries={
        "label:classes": [{"label": "Bare Ground", "value": 1}, {"label": "Woodland", "value": 6}]
    },
)


def item(collection_id, item_id, when, assets):
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "collection": collection_id,
        "geometry": {"type": "Point", "coordinates": [0, 0]},
        "bbox": [0, 0, 0, 0],
        "properties": {"datetime": when},
        "links": [{"rel": "self", "href": f"{COLLECTIONS_URL}/{collection_id}/items/{item_id}"}],
        "assets": assets,
    }


def sst_item(day):
    item_id = f"coraltemp_v3.1_202605{day:02d}"
    # The thumbnail comes first, as it does for some catalog items.
    return item(
        "daily_sst",
        item_id,
        f"2026-05-{day:02d}T12:00:00Z",
        {
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
    )


GRAVITY_ITEM = item(
    "market_gravity",
    "market_gravity",
    "2021-12-28T00:00:00Z",
    {
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
)


def features(*items, matched=None):
    body = {"type": "FeatureCollection", "features": list(items), "links": []}
    if matched is not None:
        body["numberMatched"] = matched
    return body


@pytest.fixture
def stac(requests_mock):
    """The catalog's landing page and collection list."""

    requests_mock.get(COVARIATES_URL, json=LANDING)
    requests_mock.get(COLLECTIONS_URL, json={"collections": [SST, BAA, GRAVITY], "links": []})
    for entry in (SST, BAA, GRAVITY, LULC):
        requests_mock.get(f"{COLLECTIONS_URL}/{entry['id']}", json=entry)
    return requests_mock


def mock_search(stac, *responses):
    return stac.post(SEARCH_URL, [{"json": body} for body in responses])


def search_bodies(route):
    return [call.json() for call in route.request_history]


def zonal_ok(key="band_1"):
    return httpx.Response(200, json={key: {"mean": 28.1, "aoi_area": 1.0, "data_area": 1.0}})


def zonal_bodies(route):
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


# -- listing and finding collections -------------------------------------------


def test_collections_are_fetched_once_and_carry_no_credentials(client, stac):
    first = client.covariates.collections()
    second = client.covariates.collections()

    assert [c.id for c in first] == ["daily_sst", "daily_baa", "market_gravity"]
    assert [c.id for c in second] == ["daily_sst", "daily_baa", "market_gravity"]
    listing = [call for call in stac.request_history if call.url == COLLECTIONS_URL]
    assert len(listing) == 1
    assert all("authorization" not in call.headers for call in stac.request_history)

    client.covariates.collections(refresh=True)
    assert sum(call.url == COLLECTIONS_URL for call in stac.request_history) == 2


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
def test_search_collections_matches_id_or_title(client, stac, query, expected):
    assert [c.id for c in client.covariates.search_collections(query)] == expected


def test_search_collections_needs_a_query(client):
    with pytest.raises(ValueError):
        client.covariates.search_collections("  ")


def test_collection_uses_the_cached_list_then_the_api(client, stac):
    single = stac.get(f"{COLLECTIONS_URL}/lulc", json=LULC)
    cached = stac.get(f"{COLLECTIONS_URL}/daily_sst", json=SST)
    stac.get(f"{COLLECTIONS_URL}/nope", status_code=404, json={"detail": "not found"})

    client.covariates.collections()
    assert client.covariates.collection("daily_sst").title == SST["title"]
    assert not cached.called
    assert not single.called
    assert client.covariates.collection("lulc").id == "lulc"
    assert single.call_count == 1
    with pytest.raises(APIError):
        client.covariates.collection("nope")


def test_to_df_lists_every_dataset(client, stac):
    pytest.importorskip("pandas")

    frame = client.covariates.to_df()
    assert list(frame["id"]) == ["daily_sst", "daily_baa", "market_gravity"]
    assert frame["kind"][0] == "raster"
    assert frame["kind"][2] == "vector"
    assert list(client.covariates.to_df("gravity")["id"]) == ["market_gravity"]


# -- describing a collection -------------------------------------------------


def test_collection_metadata_reads_through_to_pystac(client, stac):
    sst = client.covariates.collection("daily_sst")

    assert sst.kind == "raster"
    assert sst.keywords == ["SST", "NOAA"]  # from the pystac collection
    assert sst.temporal_extent == (
        dt.datetime(1985, 1, 1, 12, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 7, 12, 12, tzinfo=dt.timezone.utc),
    )
    assert sst.citation == "https://doi.org/10.3390/rs12233856"
    row = sst.to_dict()
    assert row["id"] == "daily_sst"
    assert row["kind"] == "raster"
    assert client.covariates.collection("market_gravity").to_dict()["end_datetime"] is None
    with pytest.raises(AttributeError):
        sst.no_such_attribute  # noqa: B018


def test_details_come_from_one_sample_item(client, stac):
    route = mock_search(stac, features(sst_item(1)), features(matched=15168))
    sst = client.covariates.collection("daily_sst")

    assert sst.data_asset.href == "https://cdn.test/coraltemp_v3.1_20260501.tif"  # not the PNG
    assert sst.bands == [{"unit": "degrees_Celsius", "scale": 0.01, "nodata": -32768.0}]
    assert route.call_count == 1
    assert search_bodies(route)[0]["collections"] == ["daily_sst"]

    text = sst.describe()
    assert "kind:      raster" in text
    assert "items:     15168" in text
    assert "asset:     'data'" in text
    assert "unit=degrees_Celsius, scale=0.01" in text


def test_vector_details(client, stac):
    mock_search(stac, features(GRAVITY_ITEM), features(matched=1))
    gravity = client.covariates.collection("market_gravity")

    assert gravity.kind == "vector"
    assert [column["name"] for column in gravity.columns] == ["grav_NC", "name", "geom"]
    assert "grav_NC (float64): percentile" in gravity.describe()


def test_classes_fall_back_to_collection_summaries(client, stac):
    mock_search(stac, features())
    lulc = client.covariates.collection("lulc")

    assert lulc.sample_item is None
    assert lulc.classes == {1: "Bare Ground", 6: "Woodland"}


# -- searching items ---------------------------------------------------------


def test_search_is_a_pystac_item_search(client, stac):
    route = mock_search(stac, features(sst_item(1), sst_item(2)))
    site_like = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}}

    search = client.covariates.search(
        [client.covariates.collection("daily_sst"), "daily_baa"],
        datetime="2026-05",
        intersects=site_like,
        bbox=None,
    )

    assert isinstance(search, ItemSearch)
    assert [entry.id for entry in search.items()] == [
        "coraltemp_v3.1_20260501",
        "coraltemp_v3.1_20260502",
    ]
    body = search_bodies(route)[0]
    assert body["collections"] == ["daily_sst", "daily_baa"]
    assert body["datetime"] == "2026-05-01T00:00:00Z/2026-05-31T23:59:59Z"
    assert body["intersects"] == {"type": "Point", "coordinates": [1, 2]}
    assert "bbox" not in body


# -- zonal stats ---------------------------------------------------------------


@respx.mock
def test_a_search_plugs_into_zonal_stats_and_picks_the_data_asset(client, stac):
    mock_search(stac, features(sst_item(1)))
    zonal = respx.post(RASTER_URL).mock(return_value=zonal_ok())

    search = client.covariates.search("daily_sst", datetime="2026-05-01")
    results = client.zonal_stats.raster.batch([(178.4, -18.1)], search=search).results()

    assert zonal_bodies(zonal)[0]["url"] == "https://cdn.test/coraltemp_v3.1_20260501.tif"
    assert results[0].stac["asset"] == "data"


def test_resolve_sources_passes_zonal_sources_through():
    source = ZonalSource("https://cdn.test/a.tif", {"item_id": "a"})

    assert resolve_sources(None, [source, "https://cdn.test/b.tif"], None, None) == (
        source,
        ZonalSource("https://cdn.test/b.tif"),
    )


@respx.mock
def test_raster_zonal_stats(client, stac):
    route = mock_search(stac, features(sst_item(1), sst_item(2)))
    zonal = respx.post(RASTER_URL).mock(return_value=zonal_ok())
    sst = client.covariates.collection("daily_sst")

    batch = sst.zonal_stats(
        [(178.4, -18.1)], datetime="2026-05", stats=["mean"], radius=500, labels=["site-a"]
    )
    results = batch.results()

    assert search_bodies(route)[0]["datetime"] == "2026-05-01T00:00:00Z/2026-05-31T23:59:59Z"
    assert sorted(body["url"] for body in zonal_bodies(zonal)) == [
        "https://cdn.test/coraltemp_v3.1_20260501.tif",
        "https://cdn.test/coraltemp_v3.1_20260502.tif",
    ]
    assert all(body["stats"] == ["mean"] for body in zonal_bodies(zonal))
    assert {result.label for result in results} == {"site-a"}
    assert sorted(result.stac["item_id"] for result in results) == [
        "coraltemp_v3.1_20260501",
        "coraltemp_v3.1_20260502",
    ]


@respx.mock
def test_vector_zonal_stats_fills_columns_and_geometry_column(client, stac):
    mock_search(stac, features(GRAVITY_ITEM))
    zonal = respx.post(VECTOR_URL).mock(return_value=zonal_ok("grav_NC"))
    gravity = client.covariates.collection("market_gravity")

    result = gravity.zonal_stats([(178.4, -18.1)], radius=5000).results()[0]

    body = zonal_bodies(zonal)[0]
    assert body["url"] == "https://cdn.test/market_gravity.parquet"
    assert body["columns"] == ["grav_NC"]
    assert body["geometry_column"] == "geom"
    assert result["grav_NC"]["mean"] == 28.1


@respx.mock
def test_vector_zonal_stats_respects_explicit_columns(client, stac):
    mock_search(stac, features(GRAVITY_ITEM))
    zonal = respx.post(VECTOR_URL).mock(return_value=zonal_ok("name"))
    gravity = client.covariates.collection("market_gravity")

    gravity.zonal_stats(
        [(178.4, -18.1)], columns=["name"], geometry_column="other", stats=["majority"]
    ).results()

    body = zonal_bodies(zonal)[0]
    assert body["columns"] == ["name"]
    assert body["geometry_column"] == "other"


@respx.mock
def test_prepare_zonal_stats_counts_without_computing(client, stac):
    mock_search(stac, features(sst_item(1), sst_item(2), sst_item(3)))
    zonal = respx.post(RASTER_URL).mock(return_value=zonal_ok())
    sst = client.covariates.collection("daily_sst")

    job = sst.prepare_zonal_stats([(178.4, -18.1), (178.5, -18.1)], datetime="2026-05")

    assert job.request_count == 6
    assert zonal.call_count == 0


def test_zonal_stats_errors(client, stac):
    mock_search(stac, features(), features(sst_item(1)))
    sst = client.covariates.collection("daily_sst")

    with pytest.raises(ValueError, match="no items"):
        sst.zonal_stats([(178.4, -18.1)], datetime="1900")
    with pytest.raises(TypeError, match="raster"):
        sst.zonal_stats([(178.4, -18.1)], columns=["x"])


def test_zonal_stats_rejects_an_asset_it_cannot_read(client, stac):
    mock_search(stac, features(sst_item(1)))
    sst = client.covariates.collection("daily_sst")

    with pytest.raises(ValueError, match="neither"):
        sst.zonal_stats([(178.4, -18.1)], asset="thumbnail")
    with pytest.raises(ValueError, match="no asset"):
        sst.zonal_stats([(178.4, -18.1)], asset="missing")


def test_collection_wraps_a_pystac_collection(client, stac):
    sst = client.covariates.collection("daily_sst")

    assert isinstance(sst, CovariateCollection)
    assert sst.stac.id == "daily_sst"
    assert repr(sst).startswith("CovariateCollection(id='daily_sst'")
