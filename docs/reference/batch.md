# Batches

Many independent requests, run lazily on a thread pool: what
[`PaginatedList`](pagination.md) is to one paginated endpoint,
[`LazyBatch`][datamermaid.batch.LazyBatch] is to one request per area of
interest.  `client.zonal_stats.raster.batch(...)` returns one.

::: datamermaid.batch
