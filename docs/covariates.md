# Covariates

`client.covariates` shows the environmental and human-pressure datasets that
MERMAID publishes, and computes statistics from them for your sites. Examples are
daily sea surface temperature, coral bleaching heat stress, reef habitat maps,
market gravity and coastal population.

The datasets are in the
[MERMAID covariates catalog](https://mermaid.prescient.earth/stac), a public
STAC API. Each dataset is a STAC collection. Each collection holds one or more
items, and each item has a `data` asset: a Cloud Optimized GeoTIFF for a raster
or a GeoParquet file for a vector. The catalog takes no credentials, and the
client never sends yours to it.

## See what is available

`collections()` returns every dataset in the catalog. The client fetches the
list once and keeps it, so later calls cost nothing:

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    for collection in client.covariates.collections():
        print(collection.id, collection.kind, collection.title)
```

With pandas installed, `to_df()` gives one row per dataset, with its id, title,
kind, time range, keywords and licence:

```python
frame = client.covariates.to_df()
```

Pass `refresh=True` to `collections()` to fetch the list again.

## Find a dataset

`search_collections()` finds datasets whose id or title contains a word. The
match ignores case:

```python
client.covariates.search_collections("sst")  # daily_sst, by id
client.covariates.search_collections("bleaching")  # daily_baa and daily_dhw, by title
client.covariates.to_df("fishing")  # the same filter, as a table
```

If you know the id, `collection()` returns that dataset. An unknown id raises
[`NotFoundError`][datamermaid.exceptions.NotFoundError]:

```python
sst = client.covariates.collection("daily_sst")
```

## Read what a dataset holds

A [`CovariateCollection`][datamermaid.resources.covariates.CovariateCollection]
has the collection's own metadata, and the details of its data file:

| Attribute | What it gives |
| --- | --- |
| `id`, `title`, `description`, `keywords` | what the dataset is |
| `kind` | `"raster"` or `"vector"`: which zonal stats route reads it |
| `temporal_extent` | the first and last moments covered, as `datetime` values |
| `bbox` | the spatial extent, `(west, south, east, north)` |
| `license`, `providers`, `citation` | where it comes from and how to cite it |
| `data_asset` | the asset with the `data` role, as a [`CovariateAsset`][datamermaid.resources.covariates.CovariateAsset] |
| `bands` | for a raster: the unit, scale, offset and nodata value of each band |
| `classes` | for a categorical raster: class value to label |
| `columns` | for a vector: the name, type and description of each column |

The file details are only on the items, so the first access to `data_asset`,
`bands`, `classes` or `columns` fetches one item. `describe()` prints all of it:

```python
print(sst.describe())
# Daily Global 5km Satellite Sea Surface Temperature (CoralTemp) (daily_sst)
#   kind:      raster
#   time:      1985-01-01 to 2026-07-12
#   items:     15168
#   asset:     'data' (image/tiff; application=geotiff; profile=cloud-optimized)
#   band_1:    unit=degrees_Celsius, scale=0.01, offset=0.0, nodata=-32768.0, data_type=int16
#   ...
```

The SDK does not rescale values. The `scale` and `offset` of a band are
metadata. The Zonal Stats service applies them when it reads the raster, so a
CoralTemp mean comes back in degrees Celsius.

## Compute statistics for your sites

`zonal_stats()` on a collection computes statistics for each area of interest
against each matching item. It returns a completed
[`Batch`][datamermaid.batch.Batch], the same as the zonal stats `batch` methods:

```python
sites = client.projects(project_id).sites.list()

batch = sst.zonal_stats(sites, datetime="2026-05", stats=["mean"], radius=500)
frame = batch.to_df()  # one row per site and day
```

The collection sets these for you:

- The route. A GeoTIFF goes to `client.zonal_stats.raster`, and GeoParquet goes
  to `client.zonal_stats.vector`.
- The asset. The collection uses the asset with the `data` role, not the
  thumbnail. Pass `asset=` to use a different one.
- For a vector, `geometry_column` comes from the item's `table:columns`. If you
  leave out `columns`, every numeric column is summarised.

```python
gravity = client.covariates.collection("market_gravity")
result = gravity.zonal_stats(sites, radius=5000, stats=["mean"]).results()[0]
result["grav_NC"]["mean"]
```

Each result keeps the item it came from in `result.stac`: the item id, the
collection, the datetime and the asset key. The other arguments are the same as
for [`BaseZonalStats.batch`][datamermaid.resources.zonal_stats.BaseZonalStats.batch]:
`labels`, `max_workers`, `cache`, `errors="return"` and `stream=True`. Route
options such as `bands`, `approx_stats` and `weighting_method` pass through.

If no item matches, `zonal_stats()` raises `ValueError` and names the time range
the collection covers.

### Check the size of a job first

A daily dataset over a long period gives many requests: 100 sites over 365 days
is 36,500. `prepare_zonal_stats()` takes the same arguments, fetches the items,
and sends no statistics requests:

```python
job = sst.prepare_zonal_stats(sites, datetime=("2025-01-01", "2025-12-31"), stats=["mean"])
print(job.request_count)
with job.run(stream=True, errors="return") as results:
    for result in results:
        ...
```

## Choose dates

`datetime=` selects items by date. Date-only values are widened to whole days,
because the catalog does not accept them as they are:

| You pass | It selects |
| --- | --- |
| `"2026"` | all of 2026 |
| `"2026-05"` | all of May 2026 |
| `"2026-05-03"` or `date(2026, 5, 3)` | that day |
| `"2026-05-01/2026-05-15"` | those days, inclusive |
| `("2026-05", None)` or `"2026-05-01/.."` | May 2026 onwards |
| `"2026-05-03T12:00:00Z"` | that moment |

Leave `datetime` out for a dataset with one item, such as `market_gravity`. For
a daily dataset, leaving it out selects every day since 1985. Use `max_items` to
set a limit.

## Search items directly

`client.covariates.search()` returns a lazy
[`CovariateSearch`][datamermaid.resources.covariates.CovariateSearch]. It sends
no request until you read from it, and it follows the catalog's pages for you:

```python
search = client.covariates.search(
    "daily_dhw", datetime="2026-06", bbox=(177.0, -19.5, 180.0, -16.0), max_items=10
)
search.count()  # how many items match, from one small request
for item in search:
    print(item.id, item.datetime, item.data_asset.href)
```

It also takes `intersects=` (a geometry or a site), `ids=`, a CQL2 JSON
`filter=` and `sortby=`. `collection.search()` does the same for one dataset.

A `CovariateSearch` works as `search=` on any zonal stats endpoint, in place of
a pystac-client `ItemSearch`:

```python
batch = client.zonal_stats.raster.batch(sites, search=search, stats=["max"], radius=500)
```

See [STAC searches and large jobs](zonal_stats.md#stac-searches-and-large-jobs)
for how a search expands into requests.

## Point the client at another catalog

The client reads the catalog at `https://mermaid.prescient.earth/stac/`. To use a
different one, set `MERMAID_COVARIATES_URL` or pass `covariates_url=`:

```python
client = MermaidClient(covariates_url="https://staging.example.test/stac")
```
