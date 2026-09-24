# Batches

[`Batch`][datamermaid.batch.Batch] executes independent requests on a bounded
thread pool and stores their results in input order.
`client.zonal_stats.raster.batch(...)` waits for completion and returns one.
Reading or exporting the results makes no additional requests.

Eager batches and streams share the same bounded executor: neither queues the
whole workload up front. Eager batches still retain all completed inputs and
results; streams release consumed results. With `errors="raise"`, an eager batch
finishes all calculations before raising its first failure in input order, while
a stream stops scheduling work when that failure is encountered.

Both modes return `BatchFailure` for failed calculations under `errors="return"`.
The failure retains `.item` and the original `.error`; `to_df()` keeps this same
failure object in its `error` column. Zonal batch inputs are always `ZonalTask`,
whether the source was supplied as a URL, a source list, or a STAC search.

::: datamermaid.batch

`stream=True` returns a single-pass `BatchStream` with bounded pending work.
Use its context manager when stopping early. `prepare()` returns a `ZonalJob`
whose `request_count` can be inspected before calling `run(stream=True)`.
See [STAC searches and large jobs](../zonal_stats.md#stac-searches-and-large-jobs).

::: datamermaid.resources.zonal_job

The second type parameter is the yielded value: default execution returns
`Batch[ZonalTask, ZonalStatsResult]` (or `BatchStream`), while `errors="return"`
includes `BatchFailure[ZonalTask]` in that value type. The same rule applies to
standalone `Batch` and `BatchStream` construction. A variable error policy keeps
the union, since either behavior is possible.
