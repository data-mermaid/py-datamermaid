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
`geometry_column` to name a different one.

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

Otherwise they behave exactly like the raster and vector endpoints above:
`raster_stac` takes `bands` and `approx_stats`, `vector_stac` takes `columns`,
`geometry_column` and `weighting_method`.

| Endpoint | Route | Source | Answer keyed by |
| --- | --- | --- | --- |
| `client.zonal_stats.raster` | `raster` | a Cloud Optimized GeoTIFF | `band_1`, `band_2`, ... |
| `client.zonal_stats.raster_stac` | `raster/stac` | a raster asset of a STAC Item | `band_1`, `band_2`, ... |
| `client.zonal_stats.vector` | `vector` | a GeoParquet file | the column names you asked for |
| `client.zonal_stats.vector_stac` | `vector/stac` | a GeoParquet asset of a STAC Item | the column names you asked for |

## Many areas at once

Every endpoint also has a `batch` method: one request per area of interest, run
on a thread pool. Pass it any iterable of areas, such as a project's sites.

```python
sites = client.projects(project_id).sites.list()

batch = client.zonal_stats.raster.batch(
    sites,
    url="https://example.test/depth.tif",
    stats=["mean", "count"],
    radius=500,
    max_workers=4,
)
len(batch)  # the number of sites, with no request sent yet
batch[0].label  # one request: the first site's id
```

`batch` returns a [`LazyBatch`][datamermaid.batch.LazyBatch], which is to a list
of independent requests what
[`PaginatedList`][datamermaid.pagination.PaginatedList] is to a paginated
endpoint. Nothing runs until you iterate, index or export it. Indexing computes
one item, a slice computes what it covers, and iteration keeps at most
`max_workers` requests ahead of you, so a loop that stops early wastes at most
one window of work. Every result is cached by position, so nothing runs twice.

`max_workers` is how many requests are in flight at once, eight by default. The
options are checked before anything runs, so a misspelled statistic or an empty
URL fails at the call, not on a worker thread.

Each result carries a `label`, which is the site id for a
[`Site`][datamermaid.models.Site], the `id` for a GeoJSON `Feature`, and the
position for anything else. Pass `labels=` to choose your own. The label is what
tells the rows apart once the batch is flattened.

### Wide rows and long rows

`to_df()` computes every item and gives one wide row per area: `label`,
`source`, then one column per band and statistic, named `band_1_mean`,
`band_1_count` and so on. It needs the optional `pandas` extra.

```python
frame = batch.to_df()
frame[["label", "band_1_mean", "band_1_count"]].head()
```

For a tidy frame instead, `to_records()` on one result gives one row per
statistic, `{label, band, stat, value}`:

```python
import pandas as pd

rows = [record for result in batch.results() for record in result.to_records()]
long_frame = pd.DataFrame(rows)
long_frame.pivot(index="label", columns="stat", values="value")
```

### When one area fails

A failed request does not spoil the rest. With the default `errors="raise"` the
exception is re-raised when you reach that position, after everything before it
has been yielded. With `errors="return"` you get the exception object in its
place and iteration carries on:

```python
batch = client.zonal_stats.raster.batch(
    sites, url="https://example.test/depth.tif", stats=["mean"], errors="return"
)

for item in batch:
    if isinstance(item, Exception):
        print("failed:", item)
    else:
        print(item.label, item["band_1"]["mean"])
```

`to_df()` in that mode gives a row whose `error` column holds the exception and
whose other columns are empty, so a partly failing batch still produces a table.

`batch.fetched` shows what has run so far, keyed by position, without running
anything more. A failed item appears as its exception whichever error mode you
chose.

```python
batch.fetched  # {0: ZonalStatsResult(...), 1: MermaidAPIError(...)}
```

### The throttle is shared

The workers share the client's retry and throttle machinery. If one of them gets
a `429`, the client-wide deadline it sets pauses every worker, not just the
thread that saw it, and the wait honours the `Retry-After` header. A batch of a
hundred areas therefore backs off as one client rather than as a hundred
independent retriers. Raising `max_workers` past what the service allows buys
nothing.

## Errors

The Zonal Stats service raises the same exceptions as the rest of the SDK, from
[`MermaidError`][datamermaid.exceptions.MermaidError] down:

| What happened | Exception |
| --- | --- |
| the area of interest is not a Point or Polygon, or an option is malformed | `ValueError` or `TypeError`, raised locally, with no request sent |
| the service rejected the body (422) or could not read the source (400) | [`MermaidAPIError`][datamermaid.exceptions.MermaidAPIError] |
| the service is rate limiting you | [`RateLimitError`][datamermaid.exceptions.RateLimitError], after the retries are spent |
| the host is unreachable | [`MermaidConnectionError`][datamermaid.exceptions.MermaidConnectionError] |

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
- [Batches](reference/batch.md) for [`LazyBatch`][datamermaid.batch.LazyBatch]
- [Geometry](reference/geometry.md) for
  [`to_aoi`][datamermaid.geometry.to_aoi]
