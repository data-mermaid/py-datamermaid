# Zonal statistics

`client.zonal_stats` computes summary statistics for a place against a raster or
vector dataset: the mean depth within 500 m of a site, the habitat classes a
survey polygon covers, the sea surface temperature at a reef.

The statistics come from the MERMAID Zonal Stats service, which is a separate
public host from the MERMAID API. It takes no credentials, and the client never
sends yours to it. You bring the data source as a URL, so the service reads any
Cloud Optimized GeoTIFF, GeoParquet file or STAC Item it can reach.

## One request

The service is public, so this runs without credentials:

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    result = client.zonal_stats.raster.stats(
        {"type": "Point", "coordinates": [178.4, -18.1]},
        url="https://example.test/depth.tif",
        stats=["mean", "count"],
        radius=500,
    )

print(result["band_1"]["mean"])
print(result.source)
```

The answer is a [`ZonalStatsResult`][datamermaid.models.ZonalStatsResult]. It
reads like a mapping from band name to statistics, and it also remembers the
area of interest and the source URL it was computed from:

| Expression | What it gives |
| --- | --- |
| `result["band_1"]` | the statistics for the first band, as the service returned them |
| `result.keys()` | the band or column names in the response |
| `result.stats` | the whole response, band name to statistic name to value |
| `result.aoi` | the GeoJSON geometry that was sent |
| `result.source` | the URL the statistics came from |
| `result.label` | whatever identifier you attached, `None` if you attached none |

Calling the endpoint is the same as calling its `stats` method, so
`client.zonal_stats.raster(aoi, url=...)` works too.

## Areas of interest

One request covers one area: a GeoJSON `Point`, optionally buffered by a radius
in metres, or a `Polygon`. [`to_aoi`][datamermaid.geometry.to_aoi] gets there
from whatever you have:

| You pass | It becomes |
| --- | --- |
| a GeoJSON `Point` or `Polygon` mapping | itself, with the coordinates copied |
| a GeoJSON `Feature` | its `geometry` |
| an object with a `__geo_interface__`, such as a shapely geometry or a GeoDataFrame row | the mapping that property returns |
| a `(lon, lat)` tuple | a `Point` |
| a [`Site`][datamermaid.models.Site] | the site's `location` |

The endpoints call it for you. Call it yourself to see what will be sent:

```python
from datamermaid import to_aoi

to_aoi((178.4, -18.1), radius=500)
# {'type': 'Point', 'coordinates': [178.4, -18.1], 'radius': 500.0}
```

A MERMAID site is a point, so pass it with a radius to get a circle around it:

```python
site = client.projects(project_id).sites.get(site_id)

result = client.zonal_stats.raster.stats(
    site, url="https://example.test/depth.tif", stats=["mean", "max"], radius=500
)
print(site.name, result["band_1"]["mean"])
```

Anything else is rejected before a request is made. A `MultiPolygon`, a
`LineString`, a `FeatureCollection` and a `GeometryCollection` all raise
`ValueError`, because the service answers with one set of statistics per
request. You decide whether to buffer, dissolve or iterate.

## Raster sources

`client.zonal_stats.raster` reads a Cloud Optimized GeoTIFF over `https://` or
`s3://` and keys the answer by band, `band_1` first. With no `stats` it returns
`min`, `max`, `mean` and `count`; `aoi_area` and `data_area` are always
included.

```python
result = client.zonal_stats.raster.stats(
    site,
    url="https://example.test/sst.tif",
    stats=["mean", "std", "median"],
    bands=[1, 2],
    radius=1000,
    approx_stats=True,
)
print(result.keys())  # ['band_1', 'band_2']
```

`approx_stats=True` reads the raster's overviews instead of its full resolution,
which is faster and approximate.

The statistic names are the
[`Stat`][datamermaid.resources.zonal_stats.Stat] vocabulary. Members compare
equal to their wire value, so `stats=["mean"]` and `stats=[Stat.MEAN]` are the
same request, and an unknown name raises `ValueError` before anything is sent.

```python
from datamermaid import Stat

Stat.MEAN == "mean"  # True
[Stat.MEAN, Stat.COUNT, Stat.MEDIAN, Stat.FREQ_HIST]
```

## Vector sources

`client.zonal_stats.vector` reads a GeoParquet file. `columns` names the columns
to summarise, is required, and becomes the keys of the answer:

```python
result = client.zonal_stats.vector.stats(
    site,
    url="https://example.test/habitat.parquet",
    columns=["depth_m", "coral_cover"],
    stats=["mean", "count"],
    weighting_method="ratio",
    radius=1000,
)
print(result["depth_m"]["mean"], result["coral_cover"]["mean"])
```

Features that only partly intersect the area are weighted, and
`weighting_method` says how. The two members of
[`WeightingMethod`][datamermaid.resources.zonal_stats.WeightingMethod] are
`area`, which weights by the intersection area, and `ratio`, which weights by the
intersection area over the feature's own area. The service weights by `area`
when you leave it out.

```python
from datamermaid import WeightingMethod

WeightingMethod.AREA == "area"
WeightingMethod.RATIO == "ratio"
```

The service finds the geometry column in the file's metadata. Pass
`geometry_column` to name a different one. The STAC route below does not do
this.

## STAC items

Two more endpoints take the URL of a STAC Item instead of the data file, and
read one of its assets. `asset` picks the asset key; leave it out and the
service reads the Item's first asset.

```python
result = client.zonal_stats.raster_stac.stats(
    site,
    url="https://example.test/items/sst-2024-03.json",
    asset="data",
    stats=["mean"],
    radius=500,
)

result = client.zonal_stats.vector_stac.stats(
    site,
    url="https://example.test/items/habitat-2024.json",
    asset="habitat",
    columns=["depth_m"],
    radius=500,
)
```

Otherwise they behave like the raster and vector endpoints above:
`raster_stac` takes `bands` and `approx_stats`, `vector_stac` takes `columns`,
`geometry_column` and `weighting_method`. One default differs: `vector_stac`
does not read the geometry column from the file's metadata. Without
`geometry_column` it uses the column named `geometry`, so pass
`geometry_column` when the asset names it differently.

| Endpoint | Route | Source | Answer keyed by |
| --- | --- | --- | --- |
| `client.zonal_stats.raster` | `raster` | a Cloud Optimized GeoTIFF | `band_1`, `band_2`, ... |
| `client.zonal_stats.raster_stac` | `raster/stac` | a raster asset of a STAC Item | `band_1`, `band_2`, ... |
| `client.zonal_stats.vector` | `vector` | a GeoParquet file | the column names you asked for |
| `client.zonal_stats.vector_stac` | `vector/stac` | a GeoParquet asset of a STAC Item | the column names you asked for |

For a runnable example with actual SST data and MERMAID sites, see
[`zonal_stats_sst.py`](https://github.com/data-mermaid/py-datamermaid/blob/main/examples/zonal_stats_sst.py).
It reads `daily_sst` through [`client.covariates`](covariates.md) and streams each site–day mean in
Celsius to JSONL. See the [example instructions](examples.md#sea-surface-temperature-and-sites).

## STAC searches and large jobs

Pass a [pystac-client ItemSearch](https://pystac-client.readthedocs.io/en/latest/usage.html#itemsearch)
as `search=`, such as one from [`client.covariates.search()`](covariates.md),
or supply `sources=` with an iterable of STAC Items, item dictionaries, or URLs.
Supply exactly one of `url`, `sources`, or `search`. The zonal stats endpoints do
not need PySTAC; the adapter uses the search's `items_as_dicts()` method.
Strings retain the selected endpoint's URL meaning: item JSON URLs for STAC
endpoints, data URLs for ordinary raster/vector endpoints.

A search expands to one calculation per AOI per item. It does not mosaic,
merge overlapping scenes, or filter pairs by footprint. Choose `asset=` explicitly
on the STAC endpoints to select the same asset key in each item; otherwise the
first asset with the `data` role is selected, or the first asset if none has
that role. Item assets are resolved to data URLs and sent to the
ordinary raster/vector endpoint, preserving signed URLs and avoiding a second
fetch of the item JSON. The statistics service must be able to read those URLs;
credentials from the STAC search client are not forwarded. Vector STAC searches
retain the `geometry` column default.

Prepare a large job to inspect its size before sending statistics requests:

```python
job = client.zonal_stats.raster_stac.prepare(
    sites,
    search=search,
    asset="temperature",
    stats=["mean"],
    radius=500,
)
print(job.request_count)  # 1,000 sites × 1,000 items = 1,000,000
```

Preparation fetches the search once and retains the AOIs and resolved source
metadata. It does not allocate the Cartesian product. Each source is bound to its
request target during preparation, using the same option validation and defaults
as `stats()` and `batch()`. Search failures, missing assets, and invalid shared
options fail during preparation, before calculations start. Empty searches produce zero requests. Relative asset URLs require an
absolute item `self` link. Items without a single datetime retain their
`start_datetime` and `end_datetime` metadata as well.

For large jobs, consume a **single-pass stream** and write each result as it
arrives. Keep the client open throughout consumption:

```python
import json
from datamermaid import BatchFailure

with job.run(stream=True, max_workers=8, errors="return") as results:
    with open("zonal-results.jsonl", "w") as output:
        for result in results:
            if isinstance(result, BatchFailure):
                row = {
                    "label": result.item.label,
                    "source": result.item.source.url,
                    "stac": result.item.source.stac,
                    "error": str(result.error),
                }
            else:
                row = result.to_dict()
            output.write(json.dumps(row) + "\n")
```

The stream starts work on iteration and yields in AOI order, then source order
within each AOI. It keeps `max_workers` requests running: when one finishes, the
next starts, even while an earlier request is still running. A result that
finishes before an earlier one waits for it. The stream holds at most four times
`max_workers` pairs that are started but not yet yielded; past that, it waits
for the earliest one. Consumed results are not kept by the stream. Use the context manager when breaking early: it cancels queued
work and waits for requests already in flight. With `errors="raise"`, iteration
raises the original exception and stops scheduling more work. With
`errors="return"`, failures carry their input pair and original exception.

Successful results retain the AOI `label`, resolved `source` URL, and a `stac`
mapping containing item ID, collection, datetime, and selected asset. Both wide
and long exports include STAC provenance. Small jobs may use `job.run()` and
`batch.to_df()`; those retain all pairs and results in memory. A stream deliberately
has no `to_df()` method: collecting it into a list or DataFrame would consume
memory proportional to the full job.

You can also stream directly, without inspecting the count first:

```python
with client.zonal_stats.raster_stac.batch(
    sites,
    search=search,
    asset="temperature",
    stats=["mean"],
    radius=500,
    stream=True,
) as results:
    for result in results:
        print(result.label, result.stac, result["band_1"]["mean"])
```

`stream=True` also works with a single `url=` and with vector endpoints
(which still require `columns=`). Existing `batch(..., url=...)` calls remain
eager by default. Streaming does not reduce the number of requests, provide a
persistent checkpoint, or automatically resume an interrupted job. Running a
prepared job again repeats the calculations; saved JSONL rows can be used by
applications to track completed pairs. Signed asset URLs must remain valid for
the duration of execution.

## Many areas at once

Every endpoint also has a `batch` method: one request per area of interest, run
on a thread pool. Pass it any iterable of areas, such as a project's sites.

```python
sites = [site for site in client.projects(project_id).sites.list() if site.location]

batch = client.zonal_stats.raster.batch(
    sites,
    url="https://example.test/depth.tif",
    stats=["mean", "count"],
    radius=500,
    max_workers=4,
)
len(batch)  # all statistics requests have finished
batch[0].label  # the first site's id; no additional request
```

A site's `location` is optional, and a site without one has no area to
measure. The filter leaves those sites out. Without it, each such site fails
with a `ValueError` at its position, and with the default `errors="raise"` that
raises from `batch(...)`.

`batch(...)` reads all areas and waits for all statistics requests to finish.
A lazy list such as `sites.list()` therefore fetches every page at this call.
The returned [`Batch`][datamermaid.batch.Batch] holds results in input order.
Iteration, indexing, slicing, `results()`, and `to_df()` read completed results
without making additional requests. Pass a subset of areas to preview a batch.

`max_workers` is how many requests are in flight at once, eight by default. A
new request starts as soon as any request finishes, so one slow or retrying
request does not leave the other workers idle. The
options are checked before anything runs, so a misspelled statistic or an empty
URL fails at the call, not on a worker thread.

Each result carries a `label`, which is the site id for a
[`Site`][datamermaid.models.Site], the `id` for a GeoJSON `Feature`, and the
position for anything else. Pass `labels=` to choose your own. The label is what
tells the rows apart once the batch is flattened.

### Wide rows and long rows

`to_df()` gives one wide row per area: `label`,
`source`, then one column per band and statistic, named `band_1_mean`,
`band_1_count` and so on. It needs the optional `pandas` extra.

```python
frame = batch.to_df()
frame[["label", "band_1_mean", "band_1_count"]].head()
```

For a tidy frame instead, `to_records()` on one result gives one row per
statistic, `{label, source, band, stat, value}`. Every row includes `source`,
including results from plain URLs, so results for the same site remain distinguishable:

```python
import pandas as pd

rows = [record for result in batch.results() for record in result.to_records()]
long_frame = pd.DataFrame(rows)
long_frame.pivot(index="label", columns="stat", values="value")
```

### When one area fails

All areas are processed even if some fail. With the default `errors="raise"`,
`batch(...)` raises the first exception in input order after the work finishes.
With `errors="return"`, the completed batch contains a `BatchFailure` in place of
that area's result. Its `.item` identifies the site/source and `.error` holds the
original exception:

```python
from datamermaid import BatchFailure

batch = client.zonal_stats.raster.batch(
    sites, url="https://example.test/depth.tif", stats=["mean"], errors="return"
)

for item in batch:
    if isinstance(item, BatchFailure):
        print("failed:", item.item.label, item.item.source.url, item.error)
    else:
        print(item.label, item["band_1"]["mean"])
```

`to_df()` in that mode gives a row whose `error` column holds the `BatchFailure`.
The row keeps its `label`, `source`, and STAC metadata when available; statistic
columns are empty. A partly failing batch still produces a table.

### Caching results between batches

Batches keep each successful response in `client.zonal_stats.cache`, an
in-memory `ResponseCache` shared by every batch on the client. A later batch
reads matching requests from it and does not call the service again. Failed
requests are not stored, so if you run a batch again, only the failed and new
requests go to the service:

```python
batch = client.zonal_stats.raster.batch(
    sites, url="https://example.test/depth.tif", errors="return"
)
# Some requests failed. Run the batch again: only the failures are sent.
batch = client.zonal_stats.raster.batch(sites, url="https://example.test/depth.tif")
```

The key is a hash of the route and the full request body: the AOI (after
`radius` is applied), the source URL, `stats`, and the route options. If any of
these change, the request is sent again. Labels are not part of the key, so a
cached result gets the label of the batch that reads it.

The `cache=` argument controls this:

| Value                  | Behavior                                                   |
| ---------------------- | ---------------------------------------------------------- |
| `True` (the default)   | Use `client.zonal_stats.cache`.                            |
| `False` or `None`      | Send every request and store nothing.                      |
| A mutable mapping      | Use that mapping, for example a `dict` or `diskcache.Cache`. |

The default cache holds 10,000 responses and drops the least recently used one
when it is full. It lasts as long as the client. To keep results across
sessions, pass a mapping that stores to disk, such as
`diskcache.Cache("zonal-cache")`.

The key covers the source URL, not the data at that URL. If a file or STAC
Item is replaced at the same URL, call `client.zonal_stats.cache.clear()` or
pass `cache=False`. Two workers that request the same uncached body at the same
time both send it. Only `batch` uses the cache: `stats` and `prepare().run()`
always send their requests.

### Uniform batch failures and inputs

Every zonal batch now exposes `ZonalTask` inputs, including a single `url=` batch.
Use `task.aoi`, `task.label`, and `task.source`. The old `BatchItem` tuple remains
importable for compatibility but is no longer returned by batches.

Both `Batch` and `BatchStream` return `BatchFailure` with `errors="return"`.
Code that previously checked `isinstance(result, ValueError)` should check
`isinstance(result, BatchFailure)` and then inspect `result.error`. With
`errors="raise"`, the original exception is still raised. This is a change to
the returned failure/input shapes; calling syntax and successful results are unchanged.

### Migrating from lazy batches

`Batch` replaces `LazyBatch`. Requests and errors now occur during `batch(...)`;
put that call inside your error handler. The `fetched` property has been removed
because all results are complete. Use `results()` or iterate the batch instead.

### The throttle is shared

The workers share the client's retry and throttle machinery. If one of them gets
a `429`, the client-wide deadline it sets pauses every worker, not just the
thread that saw it. A batch of a hundred areas therefore backs off as one client
rather than as a hundred independent retriers. Raising `max_workers` past what
the service allows buys nothing.

The wait is the `Retry-After` header when the service sends one, but never more
than 30 seconds. If the service asks for a longer wait, the client retries after
30 seconds, and once its `max_retries` retries are spent it raises
`RateLimitError`.

The deadline belongs to the client, not to one host. A `429` from the Zonal
Stats service also pauses MERMAID API calls made on the same client, and a
`429` from the MERMAID API pauses the zonal stats workers. To keep them apart,
use a separate `MermaidClient` for each.

## Errors

The Zonal Stats service raises the same exceptions as the rest of the SDK, from
[`MermaidError`][datamermaid.exceptions.MermaidError] down:

| What happened | Exception |
| --- | --- |
| the area of interest is not a Point or Polygon, or an option is malformed | `ValueError` or `TypeError`, raised locally, with no request sent (see below for batches) |
| the service rejected the body (422) or could not read the source (400) | [`MermaidAPIError`][datamermaid.exceptions.MermaidAPIError] |
| the service is rate limiting you | [`RateLimitError`][datamermaid.exceptions.RateLimitError], after the retries are spent |
| the host is unreachable, or it answered with an empty body, a non-JSON body or JSON that is not a zonal stats response | [`MermaidConnectionError`][datamermaid.exceptions.MermaidConnectionError] |

So `except MermaidError` around `stats()` catches every failure from the
service. The local `ValueError` and `TypeError` are not `MermaidError`s.

In a batch, shared options are checked before requests start. Each area of
interest is validated by its worker. With `errors="raise"`, the first error in
input order is raised from `batch(...)` after all workers finish. With
`errors="return"`, an invalid area's `ValueError` or `TypeError` is returned in
place of its result.

## Configuration

The service root is resolved separately from `base_url`, because it is a
separate host:

| Argument | Environment variable | Default |
| --- | --- | --- |
| `zonal_stats_url` | `MERMAID_ZONAL_STATS_URL` | `https://api.zonalstats.datamermaid.org/api/v1/zonal-stats/` |

```bash
export MERMAID_ZONAL_STATS_URL='https://zonal-stats.example.test/api/v1/zonal-stats/'
```

```python
from datamermaid import DEFAULT_ZONAL_STATS_URL, MermaidClient

client = MermaidClient(zonal_stats_url="https://zonal-stats.example.test/api/v1/zonal-stats/")
print(client.zonal_stats_url, DEFAULT_ZONAL_STATS_URL)
```

The argument wins over the environment variable, which wins over the default.
A URL without a trailing slash gets one.

No credentials go to the zonal stats host. The client holds your API key or your
OAuth token for `base_url` only, and every zonal stats request is sent with
authentication switched off, so the key is never disclosed to a host that does
not need it. The timeout, the retry policy and the error mapping are the
client's own.

## Reference

- [Resources](reference/resources.md#zonal-statistics) for the endpoint classes,
  `Stat` and `WeightingMethod`
- [Models](reference/models.md) for
  [`ZonalStatsResult`][datamermaid.models.ZonalStatsResult]
- [Batches](reference/batch.md) for [`Batch`][datamermaid.batch.Batch]
- [Geometry](reference/geometry.md) for
  [`to_aoi`][datamermaid.geometry.to_aoi]

STAC inputs are structurally typed: `StacSearchLike` describes an object with
`items_as_dicts()`, and `StacItemLike` describes one with `to_dict()`. Real
pystac objects can be passed directly; the SDK does not require pystac to be
installed when working with URLs or item dictionaries. `SourceInput` names the
union of accepted source types. These types are exported from `datamermaid`.
