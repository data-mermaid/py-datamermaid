"""Search expansion and streaming stay bounded independently of job size."""

import json
import threading

import httpx
import pytest
import respx

from datamermaid import BatchFailure, BatchStream
from datamermaid.batch import READAHEAD

from .conftest import ZONAL_STATS_URL


def item(index=0):
    return {
        "id": f"item-{index}",
        "collection": "temperature",
        "properties": {"datetime": "2026-01-01T00:00:00Z"},
        "assets": {"data": {"href": f"https://data.test/{index}.tif?token=signed"}},
    }


class Search:
    def __init__(self, count=2):
        self.count = count
        self.calls = 0

    def items_as_dicts(self):
        self.calls += 1
        return (item(i) for i in range(self.count))


def test_stream_is_lazy_bounded_and_closes():
    consumed = []

    def inputs():
        for i in range(1_000_000):
            consumed.append(i)
            yield i

    stream = BatchStream(inputs(), lambda i: i * 2, max_workers=3)
    assert consumed == []
    with stream:
        assert next(stream) == 0
        assert next(stream) == 2
        assert len(consumed) <= 3 * READAHEAD
    assert list(stream) == []
    assert consumed == list(range(len(consumed)))
    assert len(consumed) <= 3 * READAHEAD


def test_stream_preserves_order_with_concurrent_workers():
    released = threading.Event()

    def compute(i):
        if i == 0:
            assert released.wait(5)
        else:
            released.set()
        return i

    assert list(BatchStream(range(10), compute, max_workers=2)) == list(range(10))


@pytest.mark.parametrize("errors", ["return", "raise"])
def test_stream_errors_keep_input_or_raise_original(errors):
    original = ValueError("bad")

    def compute(i):
        raise original

    with BatchStream([42, 43], compute, errors=errors, max_workers=1) as stream:
        if errors == "raise":
            with pytest.raises(ValueError) as raised:
                next(stream)
            assert raised.value is original
        else:
            failure = next(stream)
            assert isinstance(failure, BatchFailure)
            assert failure.item == 42
            assert failure.error is original
            assert len(list(stream)) == 1


def test_close_before_start_consumes_nothing():
    def inputs():
        pytest.fail("closed stream must not consume inputs")
        yield

    stream = BatchStream(inputs(), lambda i: i)
    stream.close()
    assert list(stream) == []


@respx.mock
def test_million_pair_job_is_prepared_without_requests(client):
    search = Search(1000)
    job = client.zonal_stats.raster_stac.prepare(
        [(1, 2)] * 1000, search=search, asset="data", stats=["mean"], radius=500
    )
    assert job.request_count == 1_000_000
    assert search.calls == 1
    route = respx.post(f"{ZONAL_STATS_URL}raster").respond(200, json={"band_1": {"mean": 28}})
    with job.run(stream=True, max_workers=2) as stream:
        result = next(stream)
        assert result["band_1"]["mean"] == 28
        assert result.stac["item_id"] == "item-0"
        assert result.to_dict()["stac_asset"] == "data"
        assert result.to_dict()["stac_datetime"] == "2026-01-01T00:00:00Z"
        assert result.to_records()[0]["stac_collection"] == "temperature"
    assert 1 <= route.call_count <= 2
    assert search.calls == 1


@respx.mock
def test_search_cartesian_order_and_signed_urls(client):
    route = respx.post(f"{ZONAL_STATS_URL}raster").respond(200, json={"band_1": {"mean": 28}})
    with client.zonal_stats.raster_stac.batch(
        [(1, 2), (3, 4)],
        search=Search(),
        asset="data",
        labels=["a", "b"],
        stats=["mean"],
        stream=True,
    ) as stream:
        results = list(stream)
    assert [(r.label, r.stac["item_id"]) for r in results] == [
        ("a", "item-0"),
        ("a", "item-1"),
        ("b", "item-0"),
        ("b", "item-1"),
    ]
    for call in route.calls:
        body = json.loads(call.request.content)
        assert body["url"].endswith("?token=signed")
        assert body["stats"] == ["mean"]
        assert "asset" not in body


@respx.mock
def test_vector_search_uses_direct_asset_and_preserves_stac_default(client):
    route = respx.post(f"{ZONAL_STATS_URL}vector").respond(200, json={"depth": {"mean": 3}})
    results = client.zonal_stats.vector_stac.batch(
        [(1, 2)], search=Search(1), columns=["depth"], stream=True
    )
    assert next(results)["depth"]["mean"] == 3
    results.close()
    assert json.loads(route.calls[0].request.content)["geometry_column"] == "geometry"


@respx.mock
def test_eager_and_streamed_failure_provenance(client):
    respx.post(f"{ZONAL_STATS_URL}raster").respond(200, json={"band_1": {"mean": 1}})
    job = client.zonal_stats.raster_stac.prepare(["invalid"], search=Search(1))
    eager = job.run(errors="return")
    with job.run(stream=True, errors="return") as stream:
        failed = next(stream)
    for failure in (eager[0], failed):
        assert isinstance(failure, BatchFailure)
        assert failure.item.label == 0
        assert failure.item.source.stac["item_id"] == "item-0"
    pytest.importorskip("pandas")
    row = eager.to_df().iloc[0]
    assert row["label"] == 0
    assert row["source"].endswith("?token=signed")


@respx.mock
def test_multiple_urls_and_single_url_stream(client):
    respx.post(f"{ZONAL_STATS_URL}raster").respond(200, json={})
    eager = client.zonal_stats.raster.batch([(1, 2)], sources=["https://a", "https://b"])
    assert [r.source for r in eager] == ["https://a", "https://b"]
    with client.zonal_stats.raster.batch([(1, 2)], url="https://a", stream=True) as stream:
        assert next(stream).source == "https://a"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"url": "https://a", "search": Search()},
        {"sources": "https://a"},
        {"search": Search(), "asset": "missing"},
        {"url": ""},
        {"search": Search(), "stats": ["wrong"]},
    ],
)
def test_invalid_preparation(client, kwargs):
    with pytest.raises((ValueError, TypeError)):
        client.zonal_stats.raster_stac.prepare([(1, 2)], **kwargs)


def test_empty_search_and_relative_asset(client):
    assert client.zonal_stats.raster_stac.prepare([(1, 2)], search=Search(0)).request_count == 0
    source = item()
    source["assets"]["data"]["href"] = "../data.tif"
    with pytest.raises(ValueError, match="Relative"):
        client.zonal_stats.raster_stac.prepare([(1, 2)], sources=[source])
    source["links"] = [{"rel": "self", "href": "https://example.test/items/one.json"}]
    job = client.zonal_stats.raster_stac.prepare([(1, 2)], sources=[source])
    assert job.sources[0].url == "https://example.test/data.tif"


@pytest.mark.parametrize("name", ["raster", "raster_stac", "vector", "vector_stac"])
@respx.mock
def test_all_execution_modes_preserve_requests_values_and_provenance(client, name):
    endpoint = getattr(client.zonal_stats, name)
    route = respx.post(endpoint.url).respond(200, json={"band_1": {"mean": 28}})
    options = {"columns": ["depth"]} if name.startswith("vector") else {"bands": [1]}
    options.update(url="https://data.test/source", stats=["mean"], radius=500)
    aois = [(1, 2), (3, 4)]
    labels = ["reef-a", "reef-b"]
    eager = endpoint.batch(aois, labels=labels, max_workers=1, cache=False, **options)
    job = endpoint.prepare(aois, labels=labels, cache=False, **options)
    prepared = job.run(max_workers=1)
    with endpoint.batch(
        aois, labels=labels, max_workers=1, stream=True, cache=False, **options
    ) as stream:
        streamed = list(stream)
    with job.run(stream=True, max_workers=1) as stream:
        prepared_streamed = list(stream)
    assert list(eager) == list(prepared) == streamed == prepared_streamed
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert len(bodies) == 8
    assert bodies[:2] == bodies[2:4] == bodies[4:6] == bodies[6:8]


@respx.mock
def test_a_prepared_job_reruns_from_the_cache(client):
    route = respx.post(f"{ZONAL_STATS_URL}raster").mock(
        side_effect=[
            httpx.Response(200, json={"band_1": {"mean": 28}}),
            httpx.Response(400, json={"detail": "unreadable"}),
            httpx.Response(200, json={"band_1": {"mean": 29}}),
        ]
    )
    job = client.zonal_stats.raster.prepare([(1, 2), (3, 4)], url="https://data.test/source")

    first = job.run(max_workers=1, errors="return")
    assert isinstance(first[1], BatchFailure)
    second = job.run(max_workers=1)

    assert [result["band_1"]["mean"] for result in second] == [28, 29]
    assert route.call_count == 3  # the rerun only sent the request that failed


@respx.mock
def test_job_raise_stops_at_the_first_failure_and_names_it(client):
    route = respx.post(f"{ZONAL_STATS_URL}raster").respond(200, json={"band_1": {"mean": 28}})
    job = client.zonal_stats.raster.prepare(
        ["invalid", (1, 2)], labels=["bad-site", "good-site"], url="https://data.test/source"
    )
    with pytest.raises(TypeError) as raised:
        job.run(max_workers=1)
    assert route.call_count == 0
    assert raised.value.__notes__[-1] == (
        "batch input 0 failed: label='bad-site', source='https://data.test/source'"
    )
    with job.run(max_workers=1, stream=True) as results, pytest.raises(TypeError) as raised:
        next(results)
    assert route.call_count == 0
    assert "label='bad-site'" in raised.value.__notes__[-1]


@pytest.mark.parametrize("mode", ["stats", "batch", "stream", "prepare"])
@pytest.mark.parametrize(
    ("name", "options"),
    [
        ("raster", {"bands": []}),
        ("raster_stac", {"bands": "1"}),
        ("raster_stac", {"asset": ""}),
        ("vector", {"columns": []}),
        ("vector", {"columns": ["depth"], "geometry_column": 123}),
        ("vector_stac", {"columns": ["depth"], "weighting_method": "unknown"}),
        ("vector_stac", {"columns": ["depth"], "asset": ""}),
    ],
)
@respx.mock
def test_invalid_options_are_rejected_in_every_mode(client, mode, name, options):
    endpoint = getattr(client.zonal_stats, name)
    with pytest.raises((TypeError, ValueError)):
        if mode == "stats":
            endpoint.stats((1, 2), url="https://data.test/source", **options)
        elif mode == "prepare":
            endpoint.prepare([(1, 2)], url="https://data.test/source", **options)
        else:
            endpoint.batch(
                [(1, 2)], url="https://data.test/source", stream=mode == "stream", **options
            )
    assert not respx.calls


@pytest.mark.parametrize("name", ["raster", "raster_stac", "vector", "vector_stac"])
def test_prepare_rejects_unknown_options_before_consuming_search(client, name):
    search = Search()
    options = {"columns": ["depth"]} if name.startswith("vector") else {}
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        getattr(client.zonal_stats, name).prepare([(1, 2)], search=search, typo=True, **options)
    assert search.calls == 0


@respx.mock
def test_prepared_options_are_a_snapshot_and_search_is_consumed_once(client):
    route = respx.post(f"{ZONAL_STATS_URL}raster").respond(200, json={"band_1": {"mean": 28}})
    bands = [1]
    stats = ["mean"]
    search = Search(1)
    job = client.zonal_stats.raster_stac.prepare(
        [(1, 2), (3, 4)],
        search=search,
        asset="data",
        bands=bands,
        stats=iter(stats),
        cache=False,
    )
    bands.clear()
    stats.clear()
    job.run()
    with job.run(stream=True) as results:
        assert len(list(results)) == 2
    assert search.calls == 1
    assert len(route.calls) == 4
    for call in route.calls:
        body = json.loads(call.request.content)
        assert body["bands"] == [1]
        assert body["stats"] == ["mean"]


@pytest.mark.parametrize("geometry_column", [None, "geom", ""])
@respx.mock
def test_mixed_sources_keep_their_route_defaults_and_identity(client, geometry_column):
    # Same URL deliberately appears as an item URL and as two items' asset URL.
    # Resolving by URL alone would lose the target route or item provenance.
    url = "https://data.test/shared"
    first = item(0)
    second = item(1)
    first["assets"]["data"]["href"] = url
    second["assets"]["data"]["href"] = url
    stac_route = respx.post(f"{ZONAL_STATS_URL}vector/stac").respond(
        200, json={"depth": {"mean": 1}}
    )
    asset_route = respx.post(f"{ZONAL_STATS_URL}vector").respond(200, json={"depth": {"mean": 2}})
    job = client.zonal_stats.vector_stac.prepare(
        [(1, 2)],
        sources=[url, first, second],
        asset="data",
        columns=["depth"],
        geometry_column=geometry_column,
    )
    assert not respx.calls
    results = job.run(max_workers=1).results()
    assert [result["depth"]["mean"] for result in results] == [1, 2, 2]
    assert results[0].stac is None
    assert results[1].stac["item_id"] == "item-0"
    assert results[2].stac["item_id"] == "item-1"
    stac_body = json.loads(stac_route.calls[0].request.content)
    assert stac_body["asset"] == "data"
    if geometry_column is None:
        assert "geometry_column" not in stac_body
    else:
        assert stac_body["geometry_column"] == geometry_column
    for call in asset_route.calls:
        body = json.loads(call.request.content)
        assert "asset" not in body
        assert body["geometry_column"] == (
            "geometry" if geometry_column is None else geometry_column
        )


@pytest.mark.parametrize("source_kind", ["url", "sources", "search"])
@respx.mock
def test_every_zonal_batch_has_the_same_failure_and_input_contract(client, source_kind):
    source = (
        {"url": "https://data.test/one"}
        if source_kind == "url"
        else (
            {"sources": ["https://data.test/one"]}
            if source_kind == "sources"
            else {"search": Search(1)}
        )
    )
    endpoint = client.zonal_stats.raster_stac
    eager = endpoint.batch(["invalid"], labels=["reef"], errors="return", **source)
    with endpoint.batch(
        ["invalid"], labels=["reef"], errors="return", stream=True, **source
    ) as stream:
        failure = next(stream)
    for result in (eager[0], failure):
        assert isinstance(result, BatchFailure)
        assert isinstance(result.error, TypeError)
        assert result.item.label == "reef"
        assert result.item.source.url
    assert eager[0].item == eager.inputs[0]
    assert type(eager.inputs[0]) is type(failure.item)


@pytest.mark.parametrize(
    "options, error",
    [
        ({"radius": float("nan")}, ValueError),
        ({"radius": float("inf")}, ValueError),
        ({"radius": True}, TypeError),
        ({"max_workers": True}, TypeError),
        ({"max_workers": 1.5}, TypeError),
    ],
)
def test_invalid_batch_options_fail_before_search(client, options, error):
    search = Search()
    with pytest.raises(error):
        client.zonal_stats.raster_stac.batch([(1, 2)], search=search, asset="data", **options)
    assert search.calls == 0


@pytest.mark.parametrize(
    "source",
    [
        42,
        object(),
        {"assets": []},
        {"assets": {"data": 1}},
        {**item(), "properties": []},
        {**item(), "links": [42]},
        {**item(), "links": "bad"},
    ],
)
def test_invalid_stac_source_shapes_raise_type_errors(client, source):
    with pytest.raises(TypeError):
        client.zonal_stats.raster_stac.prepare([(1, 2)], sources=[source], asset="data")


def test_invalid_stac_protocol_objects_raise_type_errors(client):
    class InvalidItem:
        def to_dict(self):
            return []

    with pytest.raises(TypeError, match="mapping"):
        client.zonal_stats.raster_stac.prepare([(1, 2)], sources=[InvalidItem()])
    with pytest.raises(TypeError, match="items_as_dicts"):
        client.zonal_stats.raster_stac.prepare([(1, 2)], search=object())


@pytest.mark.parametrize(
    "source",
    ["  ", {"assets": {"data": {"href": " "}}}, {**item(), "links": [{"rel": "self", "href": 42}]}],
)
def test_invalid_source_urls_raise_value_errors(client, source):
    with pytest.raises(ValueError):
        client.zonal_stats.raster_stac.prepare([(1, 2)], sources=[source], asset="data")
