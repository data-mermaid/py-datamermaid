# Changelog

All notable changes to `datamermaid` are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/). Before 1.0, a minor release can
change behavior.

## [0.2.0] - 2026-09-25

### Added

- `client.covariates`, the MERMAID covariates STAC catalog. It lists, finds and
  describes datasets, searches their items, and runs zonal statistics over a
  collection with `CovariateCollection.zonal_stats()`. It needs the new
  `covariates` extra: `pip install 'datamermaid[covariates]'`.
- `CovariateCollection.zonal_stats(max_requests=10_000)`. A job with more
  requests raises `ValueError` before it sends any. `max_requests=None` removes
  the limit.
- `prepare()` on every zonal stats endpoint, and `prepare_zonal_stats()`, take
  `cache=`. A prepared job uses the client's response cache by default, so a
  rerun sends only the requests that failed.
- Logging to the `datamermaid` logger: each response at `DEBUG`, and retries,
  throttling, token refreshes and zonal job sizes at `INFO`. The package adds a
  `NullHandler` and nothing else.
- A CI job that runs the tests without the optional extras.

### Changed

- The covariates catalog is read through the `MermaidClient`, so catalog
  requests use its timeout, retries and `429` throttle. A failed catalog request
  raises a `MermaidError` subclass. An unknown collection raises
  `NotFoundError`, which is also a pystac-client `APIError`.
- `Batch` and `BatchStream` start a new input as soon as any worker finishes.
  One slow or retrying request no longer leaves the other workers idle. A
  stream starts at most `READAHEAD * max_workers` (4 x `max_workers`) inputs
  ahead of the next result it yields.
- With `errors="raise"`, a batch stops at the first failure. It starts no more
  inputs, waits for the running ones, and raises the original exception. Before,
  it ran every input first. The exception has a note that names the failed
  input's position, label and source.
- STAC provenance in `ZonalStatsResult.to_dict()`, `to_records()` and failed
  `to_df()` rows is now flat `stac_<field>` columns, such as `stac_item_id` and
  `stac_datetime`. Before, it was one nested `stac` dict.
- The default `ResponseCache` holds 100,000 responses, up from 10,000.
- Item searches pick the asset with the `data` role, not the first asset.
- `batch()` is defined once, on `BaseZonalStats`. Route options such as `bands`
  and `columns` pass through `**options`, so type checkers no longer check them
  on `batch()`. `prepare()` and `stats()` still type them.

## [0.1.1] - 2026-09-24

### Added

- A response cache for zonal stats batches, `client.zonal_stats.cache`.

## [0.1.0] - 2026-09-24

- First release.

[0.2.0]: https://github.com/data-mermaid/py-datamermaid/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/data-mermaid/py-datamermaid/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/data-mermaid/py-datamermaid/releases/tag/v0.1.0
