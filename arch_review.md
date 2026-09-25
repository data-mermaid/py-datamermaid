# Architecture Review: datamermaid (py-datamermaid)

Repository root: `/home/dustin/projects/mermaid/py-datamermaid`. All paths below are relative to it. Reviewed at `39914184` (main), with the untracked `examples/covariates.py` and `sst-results.jsonl` also present.

## Summary

`datamermaid` is a synchronous Python SDK (3.10+, httpx) for the MERMAID coral reef API. It also covers two public side services: the Zonal Stats service and, new in this cycle, a STAC covariates catalog read through pystac-client. It is aimed at reef scientists and analysts who want typed records, lazy pagination and pandas export. The core is unusually careful for a 0.1 library:

- The auth seam is clean.
- Credentials are kept away from third-party hosts.
- Retries use a shared cooperative throttle.
- The models are lossless.
- Strict typing passes, and 1193 tests run in about 8 seconds.

The weak spots are all at the edges added most recently:

- **Two HTTP stacks.** The covariates module runs its own HTTP stack that skips the client's timeout, retry, throttle and error contract.
- **Unbounded fan-out.** `CovariateCollection.zonal_stats` can launch a huge number of requests from one innocent call.
- **Idle workers.** The ordered batch executor stops refilling while it waits on one slow request.
- **Install instructions fail.** They point at a PyPI package that does not exist.

The highest-payoff changes:

1. Fix the install story and reserve the name.
2. Route covariates I/O through the client's policies and error types.
3. Add a request-count guard plus a non-blocking refill window to the batch engine.

### Scorecard

| Area | Rating (1-5) | One-line verdict |
|------|--------------|------------------|
| Code structure | 4 | Sensible layering and lazy imports; `zonal_stats.py` is swollen by signatures, and covariates reaches into private internals |
| Code patterns | 4 | Consistent validation, errors and thread safety in the core; covariates breaks the error and transport contract |
| Developer experience | 3 | Excellent ergonomics once installed, but the README install fails and large covariate jobs are easy to trigger by accident |
| Language idioms | 4 | Idiomatic typed Python, context managers, `py.typed`, PEP 723 examples; heavy overloads and `type: ignore[override]` are the main smell |
| Performance | 3 | Good connection reuse and throttling; head-of-line blocking in `_execute` and the default cache size undercut large jobs |
| Documentation | 4 | Thorough guides, mkdocstrings with `--strict`, snippet tests; a few gaps (config table, time-series output shape, install) |

## Strengths

- **The auth seam is the right shape.** The client only calls `Auth.apply` through `HTTPXAuthAdapter` (`src/datamermaid/auth/base.py:95-107`). `resolve_auth` has a documented precedence: explicit, then key, then env, then cached login, then anonymous (`auth/base.py:110-141`). Implicitly picked-up OAuth is forced to `interactive=False` so a data call never opens a browser (`auth/base.py:144-165`). `OAuth` lets tests inject the browser, prompt, printer, clock and HTTP client (`auth/oauth.py:109-171`). The token cache uses file locking, `0o600` permissions and atomic `os.replace` (`auth/token_cache.py:32-33, 66-93, 261-273`).
- **Credential isolation is designed in.** Requests to the side services go through an allowlist, not a denylist (`PUBLIC_HEADERS` and `_send_public`, `src/datamermaid/client.py:67-80, 526-531`). A cross-host `next` link contributes only its query string (`resources/base.py:114-131`). Ids are percent-encoded and dot segments rejected, so an id cannot redirect a request (`resources/base.py:20-45, 62-71`).
- **Rate limiting across threads is well thought out.** One client-wide `_throttled_until` deadline is re-read after every sleep, and the lock is never held while sleeping (`client.py:456-480`). `Retry-After` accepts both RFC 9110 forms (`exceptions.py:156-182`), backoff has jitter and is capped at 30 seconds (`client.py:448-454`).
- **The models are lossless without being loose.** A value that fails conversion stays in `extra` under its original key, and `to_dict` exports the raw value rather than a default (`models.py:159-216`). This is a thoughtful answer to API drift.
- **`to_aoi` is a model input normaliser.** It accepts GeoJSON, Features, `__geo_interface__`, tuples and `Site`. It rejects MultiPolygon on purpose, returns fresh JSON-safe dicts, and its error messages say exactly what is wrong (`geometry.py:98-218`).
- **Optional dependencies stay optional.** pandas and pystac-client are imported only under `TYPE_CHECKING` or on first use. `import datamermaid` takes about 80 ms and loads neither pandas, pystac, requests nor numpy (measured with `-X importtime`).
- **The tooling is solid:**
  - Strict-leaning mypy passes on `src`, and ruff runs with `ANN`, `B`, `RUF` and `SIM`.
  - The test suite runs on a 3.10 to 3.13 matrix.
  - `tests/test_docs.py` executes guide snippets against mocked HTTP.
  - `mkdocs build --strict` fails a PR that breaks cross-references.
  - Examples are PEP 723 scripts that can be run directly.
- **The release workflow is careful.** It checks that the tag matches `pyproject.toml`, runs the tests, builds, and imports the wheel in an isolated environment before publishing (`.github/workflows/release.yml:27-54`).
- **Covariates builds on the ecosystem.** Replacing the hand-written STAC client with pystac-client (commit `39914184`) was the right call. Exposing `ItemSearch` unchanged keeps all of pystac usable. Choosing the asset by its `data` role rather than taking the first asset (`zonal_job.py:46-56`) prevents computing stats over a thumbnail. `prepare_zonal_stats` and `ZonalJob.request_count` give users a way to count requests before sending any.
- **`ResponseCache` is correct and small.** It is a thread-safe LRU, it never stores failures, and its key hashes the route plus the normalised body, so labels never pollute it (`resources/zonal_stats.py:271-331, 390-423`).

## Issues

### High

**H1. The install instructions point to a package that is not on PyPI** (developer experience, security)
- Where:
  - `README.md:21-36`
  - `docs/index.md:24-34`
  - `.github/workflows/release.yml:9-11`
- What: The README and docs tell users to run `uv add datamermaid` and `pip install 'datamermaid[pandas]'`. `https://pypi.org/pypi/datamermaid/json` returns 404. The release workflow only attaches wheels to a GitHub Release.
- Why it matters:
  - Every new user fails in the first minute.
  - The name is unclaimed. Anyone can register `datamermaid` on PyPI, and every user who follows the README would then install that package instead. This is a real supply-chain exposure for a science audience that copies install lines verbatim.
- Recommendation:
  - Reserve the name now and publish to PyPI with trusted publishing (`pypa/gh-action-pypi-publish` with `id-token: write`) from the existing `release` job.
  - Until then, change the docs to the GitHub Release wheel URL or `pip install "datamermaid @ git+https://github.com/data-mermaid/py-datamermaid@v0.1.1"`.

**H2. Covariates skips the client's HTTP policies and error contract** (code patterns, developer experience)
- Where:
  - `src/datamermaid/resources/covariates.py:594-599` (`Client.open(self.path)` with no timeout, headers or `stac_io`)
  - `covariates.py:612-624`
  - `covariates.py:617` (docstring promises `pystac_client.exceptions.APIError`)
  - `README.md:401-428`
- What: pystac-client uses its own `requests` session. Its installed version (0.9.0) defaults to `timeout=None` and `max_retries=5` with urllib3's policy (checked with `inspect.signature(StacApiIO.__init__)`). As a result, covariates requests:
  - Can hang forever.
  - Ignore `MermaidClient(timeout=..., max_retries=..., backoff_factor=...)`.
  - Do not take part in the shared throttle.
  - Send pystac's User-Agent rather than `datamermaid/...`.
  - Raise `pystac_client.exceptions.APIError`, which is not a `MermaidError`.
  - Are not closed by `client.close()`.
  - Cannot be driven by `transport=`, so tests need `requests-mock` alongside `respx`.
- Why it matters:
  - The README says request errors derive from `MermaidError`, so `except MermaidError` silently misses every catalog failure.
  - A stalled catalog request blocks a notebook indefinitely.
  - The library now has two HTTP stacks, with two retry policies to explain and maintain.
- Recommendation: Keep pystac-client, but configure it from the client and translate errors at the boundary.
  ```python
  from pystac_client.stac_api_io import StacApiIO

  io = StacApiIO(
      headers={"User-Agent": default_user_agent()},
      timeout=self._client.timeout_seconds,
      max_retries=self._client.max_retries,
  )
  catalog = Client.open(self.path, stac_io=io)
  ```
  - Wrap `APIError` in `MermaidAPIError` when it carries a status code, or in `MermaidConnectionError` when it does not. Do this in one `_catalog_call` helper used by `collections`, `collection`, `search` and `sample_item`.
  - Close `io.session` from `MermaidClient.close()`. A small `_closers` list on the client works.

**H3. `CovariateCollection.zonal_stats` has no guard against massive fan-out** (developer experience, performance)
- Where:
  - `src/datamermaid/resources/covariates.py:339-340` (`list(search.items())` with no bound)
  - `covariates.py:552-576` (runs straight away)
  - `docs/covariates.md` ("For a daily dataset, leaving it out selects every day since 1985", 15,168 items)
- What: When `datetime` and `max_items` are omitted, the call first pages through every item in the collection. It then starts `len(aois) * len(items)` requests right away. For 100 sites on `daily_sst` that is about 1.5 million POSTs to a shared public service from one line that looks harmless. The docs warn about this, but the code does not.
- Why it matters:
  - It is easy to hit by accident, the most natural call is the dangerous one, and the cost lands on the MERMAID Zonal Stats service.
  - With `errors="raise"` the user may wait hours before seeing any result (see M3).
- Recommendation: Put a request ceiling on the one-shot convenience path, and point users to the explicit path when they exceed it.
  ```python
  def zonal_stats(self, aois, *, ..., max_requests: int | None = 10_000, ...):
      job = self.prepare_zonal_stats(...)
      if max_requests is not None and job.request_count > max_requests:
          raise ValueError(
              f"{job.request_count} requests exceed max_requests={max_requests}; "
              "narrow datetime=/max_items=, or pass max_requests=None"
          )
  ```
  Also require `datetime` or `max_items` when the collection's temporal extent spans more than one item. `sample_item` plus `search().matched()` can tell you this cheaply.

**H4. Head-of-line blocking in the ordered executor leaves workers idle** (performance)
- Where: `src/datamermaid/batch.py:58-69`
- What: `_execute` keeps exactly `max_workers` futures in flight, and submits a new one only after the oldest has finished and been yielded. While the head task is slow (a 30 s timeout, or a 429 or 5xx retry with backoff), the other workers finish and sit idle. Measured: 80 tasks at 50 ms each plus one 1 s task, with `max_workers=8`, took 1.45 s. The ideal is about 1.0 s, and the same batch without the slow task took 0.5 s. For each slow head, concurrency effectively drops to 1.
- Why it matters: Slow heads are exactly the retry and backoff case, which is common against the Zonal Stats service. Large covariate jobs therefore run well below the configured concurrency.
- Recommendation: Separate the submission window from the order results are yielded in.
  - Keep submitting as futures complete, using `add_done_callback` and a bounded semaphore. Keep a reorder buffer of up to, say, `4 * max_workers` completed results for in-order yield.
  - Alternatively, offer `ordered=False` on `BatchStream` and `ZonalJob.run(stream=True)`, which yields in completion order. `ZonalTask.label` already identifies each row, so order is rarely needed for streamed output.

### Medium

**M1. Overload explosion and LSP-violating subclasses in `zonal_stats.py`** (code structure, language idioms)
- Where:
  - `src/datamermaid/resources/zonal_stats.py:581-771`, `863-1031`, `1125-1292`, `1400-1575`, `1687-1870`
  - `covariates.py:411-493`
  - `resources/zonal_job.py:173-228`
  - `batch.py:98-132`
- What:
  - The package has 48 `@overload` declarations, 30 of them in `zonal_stats.py`.
  - Each endpoint's `batch` has six overloads, one per combination of `errors` and `stream`.
  - Each subclass narrows `**options` into named parameters, which needs `# type: ignore[override]` on `_options`, `prepare`, `stats` and `batch`.
  - About 1,100 of the file's 1,919 lines are repeated signatures.
  - `covariates.py:355` has to type the endpoint as `Any` because the base class is not substitutable.
- Why it matters:
  - Adding one route option means editing roughly seven signatures per class, across four classes, plus the covariates wrapper, and these edits drift.
  - The `ZONAL_STATS_ENDPOINTS: tuple[tuple[str, type[BaseZonalStats]], ...]` registry cannot be used polymorphically in a type-safe way.
- Recommendation: Break the change into three smaller changes, each of which removes a dimension of the overload grid.
  1. Make streaming its own method, so the `stream` flag no longer changes the return type: `endpoint.batch(...) -> Batch`, `endpoint.stream(...) -> BatchStream`, `job.run()` and `job.stream()`.
  2. Always return `Batch[ZonalTask, ZonalStatsResult]`, and expose failures as `batch.failures: list[BatchFailure]` instead of mixing them into the result type. `errors` then only controls whether the constructor raises, not the type.
  3. Describe route options with `TypedDict` plus `Unpack` (PEP 692, from `typing_extensions` on 3.10), so the base class and subclasses share one signature shape.

  This leaves at most two overloads per method. It is an API break, so it is best done before 1.0.

**M2. Caching is inconsistent exactly where it matters most** (code patterns, performance)
- Where:
  - `src/datamermaid/resources/zonal_stats.py:443-471` (`prepare` takes no `cache`)
  - `zonal_stats.py:483` (`_prepare` defaults to `None`)
  - `zonal_stats.py:425-441` (`stats` never caches)
  - `covariates.py:376-409` (`prepare_zonal_stats` takes no `cache`)
  - `zonal_stats.py:279` (default `maxsize=10_000`)
- What:
  - The docs recommend the `prepare(...).run(stream=True)` path for large jobs, and that path never uses the cache. Only `batch` does.
  - Even on `batch`, the default LRU holds 10,000 responses. The guide's own example is 36,500 requests (100 sites over 365 days). A failed run therefore keeps only the last 10k results, so "a rerun only sends the requests that failed" (`zonal_stats.py:733-735`) silently stops being true for large jobs.
- Why it matters: Users will rerun a large covariate job expecting it to resume, and it will quietly repeat most of the work against a shared service.
- Recommendation:
  - Add `cache=` to `prepare` and `prepare_zonal_stats`, with the same default as `batch`.
  - When `job.request_count > cache.maxsize` and the default cache is in use, emit a `warnings.warn` that points to `diskcache.Cache(...)`.
  - Consider `stats(..., cache=False)` for symmetry.

**M3. `errors="raise"` runs the whole job before it raises, then loses context** (developer experience)
- Where:
  - `src/datamermaid/batch.py:152-162`
  - `zonal_job.py:244-251`
- What:
  - The `Batch` constructor computes every task and then re-raises the first failure's bare exception.
  - Any successful results are only reachable through the cache (see M2). The raised exception does not say which task failed: `MermaidAPIError` carries a URL but not a label or item.
  - With the service down, 36,500 tasks times three retries all run before the first error surfaces.
- Why it matters: This is the default error mode. It combines the worst latency (no fail-fast) with the worst diagnostics (no label).
- Recommendation:
  - Raise `BatchFailure(item, error)` from `error` so the task is attached. The class already exists (`batch.py:211-217`). On 3.11+ you can also use `exc.add_note(f"label={task.label!r}")`.
  - Add `errors="fail_fast"`, or make `"raise"` stop submitting after the first failure. The cancellation machinery in `_execute`'s `finally` already supports this.

**M4. Covariate time-series output nests the datetime inside a dict** (developer experience)
- Where:
  - `src/datamermaid/models.py:1131-1144`
  - `README.md:341`
  - the untracked `sst-results.jsonl`
- What:
  - `ZonalStatsResult.to_dict` stores provenance as `row["stac"] = {...}`. In `batch.to_df()` this becomes one object column holding dicts, and there is no `datetime` column.
  - The README promises "one row per site and day". To make a time series, users must `pd.json_normalize` the column and parse strings themselves.
  - `sst-results.jsonl` shows exactly this shape.
- Why it matters: A time series is the main reason to call `sst.zonal_stats(...)`. The most common next step, grouping by site and date, needs extra code that every user will write.
- Recommendation: Flatten provenance into typed columns and keep the nested dict only on the object.
  ```python
  if self.stac is not None:
      row["item_id"] = self.stac.get("item_id")
      row["collection"] = self.stac.get("collection")
      row["datetime"] = parse_datetime(self.stac.get("datetime"))
      row["asset"] = self.stac.get("asset")
  ```
  This changes the output schema, so note it in the changelog.

**M5. The untracked files break CI and one pollutes the repository root** (tooling)
- Where:
  - `examples/covariates.py`
  - `tests/test_examples.py:59`
  - `examples/zonal_stats_sst.py:78-81`
  - `.gitignore`
- What: With `examples/covariates.py` present:
  - `pytest` fails 3 tests: the hard-coded `len(SCRIPTS) == 6`, the missing PEP 723 block, and the missing entry in `examples/README.md`.
  - `ruff check` fails on F401 (unused `import json`).
  - `ruff format --check` fails.

  The script also contains commented-out code, and passes `max_workers=30` against a public service. `sst-results.jsonl` is the default output of `zonal_stats_sst.py` (`default=Path("sst-results.jsonl")`), written to the working directory and not ignored.
- Why it matters: Committing the example as it stands would turn main red. Result files at the root are easy to commit by accident, and a local `uv build` would include them in the sdist, because hatch builds from the working tree minus `.gitignore`.
- Recommendation:
  - Before committing the example, give it a PEP 723 header, index it in `examples/README.md`, drop the unused import and the dead code, lower `max_workers` to the default, and run `ruff format`.
  - Replace the hard-coded count in `test_examples.py` with a check that each discovered script is indexed.
  - Add `*.jsonl` (or `sst-results.jsonl`) to `.gitignore` and delete the local file.

**M6. The minimal install is never tested** (tooling)
- Where:
  - `.github/workflows/ci.yml:25-26, 59-60` (`uv sync --all-extras`)
  - `pyproject.toml:37-46` (the dev group includes `pystac-client`)
  - `covariates.py:77`, `pagination.py:156` (`pragma: no cover` on the ImportError paths)
  - `tests/test_batch.py:195` (skipped because pandas is installed)
- What: Every CI job installs every extra. The "extra missing" hints, and any accidental top-level import of pandas or pystac, are never exercised.
- Why it matters: The laziness of the optional dependencies is a key strength (see Strengths) and has no regression test.
- Recommendation: Add one CI job that runs `uv sync --no-dev --no-extras`, installs `pytest respx`, and runs a small marker-selected suite. That suite should check `import datamermaid`, that `client.covariates.collections()` raises `ImportError` with `PYSTAC_INSTALL_HINT`, and that `to_df()` raises with the pandas hint. Alternatively, monkeypatch `sys.modules["pystac_client"] = None` in a unit test.

**M7. Release hygiene: unbumped version, no changelog, stray tags** (developer experience, tooling)
- Where:
  - `pyproject.toml:3` (`version = "0.1.1"`)
  - `.github/workflows/release.yml:88-93`
  - local git tags
- What:
  - `v0.1.1` points at `08c3e057`, and two feature commits (covariates) have landed since without a version bump or changelog entry.
  - Release notes come from `--generate-notes`, which is thin when commits go straight to main.
  - The local clone has 208 tags. The remote has 2 (`v0.1.0`, `v0.1.1`). The rest come from another repository: for example, `v2.28.0` resolves to "Merge pull request #734 ... merge migrations".
- Why it matters:
  - Users cannot tell what changed between installs.
  - A single `git push --tags` would publish about 206 foreign tags. Each one matching `vX.Y.Z` would start `release.yml`, which fails the version check, but noisily and with tags that are hard to clean up.
- Recommendation:
  - Delete the stray local tags: `git tag -l | grep -Ev '^v0\.1\.[01]$' | xargs git tag -d`.
  - Push release tags one at a time.
  - Add a `CHANGELOG.md` (Keep a Changelog format) and fail the release job if it has no section for the tag.
  - State a pre-1.0 policy in the README, for example "minor versions may break".

**M8. No logging anywhere in the library** (code patterns)
- Where:
  - `src/datamermaid/client.py:500-521`
  - the whole package (`grep` finds no `logging` usage)
- What: Retries, throttle sleeps of up to 30 s, cache hits and refreshes all happen silently.
- Why it matters: In a long covariate job, a user sees minutes-long stalls with no explanation, and a maintainer has nothing to ask for in a bug report.
- Recommendation: Add `logger = logging.getLogger("datamermaid")` and debug or info messages for:
  - each retry, with status and delay
  - a throttle deadline being set
  - OAuth refresh
  - batch start and end, with the request count and cache hits

  Add no handlers, following library convention.

### Low

**L1. `__getattr__` pass-through on `CovariateCollection`** (developer experience)
- Where: `src/datamermaid/resources/covariates.py:143-147`
- What: Pass-through attributes are invisible to IDEs and typed as `Any`. Worse, an `AttributeError` raised inside one of the class's own properties (`kind`, `bands`, `data_asset`) falls through to `__getattr__`, and the user sees a misleading "`Collection` has no attribute 'kind'" error.
- Recommendation: Define explicit properties for the documented attributes (`title`, `description`, `keywords`, `license`, `providers`, `extent`, `summaries`, `bbox`), and drop `__getattr__` or keep it only for backward compatibility.

**L2. Properties that make network calls, and an inconsistent `kind`** (developer experience, performance)
- Where: `covariates.py:188-245` and `covariates.py:249-265`
- What:
  - Reading `kind`, `data_asset`, `bands`, `classes` or `columns` can trigger a search request, so looping `collection.kind` over the catalog, as `examples/covariates.py` does, can be N+1.
  - `to_dict()` uses only `_hinted_kind()`, so `to_df()["kind"]` can be `None` for a collection whose `.kind` is `"raster"`.
- Recommendation: Either document these as lazy fetches in each property's docstring, or add an explicit `fetch_sample()` method. Make `to_dict` either call `kind` or name the column `kind_hint`.

**L3. Items are fully parsed and then turned back into dicts** (performance)
- Where: `covariates.py:340` together with `zonal_job.py:61-65`
- What: Each item is parsed into a `pystac.Item`, then turned back into a dict by `item.to_dict()` so its asset can be resolved. For about 15k items this is wasted CPU.
- Recommendation: Use `search.items_as_dicts()` in `_zonal_plan`. Only `items[0]` needs a pystac object, for its media type and `table:columns`.

**L4. Private imports across modules, and reaching into `_client`** (code structure)
- Where:
  - `covariates.py:48` (`_geometry_of`, `_unwrap`)
  - `covariates.py:351` (`self._resource._client.zonal_stats`)
  - `zonal_stats.py:82, 84` (`_validate_execution`, `_validate_radius`)
- Recommendation: Move shared helpers into a `datamermaid/_internal.py` (or drop the underscores within the package), and give `BaseResource` a `client` accessor so sibling resources do not reach into private attributes.

**L5. Legacy code and a broad public surface in an alpha release** (code structure)
- Where:
  - `zonal_stats.py:221-225` (`BatchItem`, described as "Legacy")
  - `src/datamermaid/__init__.py:118-226` (about 90 exported names)
- What: There is nothing to be backward compatible with at 0.1.x. The top-level namespace exports implementation classes (`BaseZonalStats`, `AggregatedFamilyResource`, `BleachingQCFamilyResource`) and six URL and env-var constants.
- Recommendation: Remove `BatchItem`. Keep implementation classes importable from `datamermaid.resources` but out of the top-level `__all__`.

**L6. Documentation gaps** (documentation)
- Where:
  - `README.md:387-395` (configuration table)
  - `docs/covariates.md` (the "Compute statistics" example)
- What:
  - The configuration table omits `covariates_url` and `MERMAID_COVARIATES_URL`.
  - The covariates guide passes `client.projects(project_id).sites.list()` unfiltered. A site without a location then fails the whole batch under `errors="raise"`. The README's zonal section filters with `if site.location`.
- Recommendation: Add the missing row, and use the same filter in the covariates guide.

**L7. `cached_property` has no lock on Python 3.12 and later** (code patterns)
- Where:
  - `client.py:275-405`
  - `covariates.py:594-599`
- What: The client is advertised as thread-safe. On first concurrent access, two `ZonalStats` instances can be created, each with its own `ResponseCache`, or the catalog can be opened twice.
- Recommendation: This is harmless in practice. If you want certainty, build `zonal_stats` and `covariates` eagerly in `__init__`, since both are cheap, or guard them with the existing `_throttle_lock`.

**L8. Pagination drops repeated query parameters** (correctness)
- Where: `resources/base.py:131`
- What: `dict(parse_qsl(parsed.query))` keeps only the last value of a repeated key such as `?status=1&status=2`. This only affects cross-host `next` links.
- Recommendation: Pass `parse_qsl(...)` as a list of tuples, which httpx accepts.

**L9. The default catalog lives on a vendor domain** (judgment call)
- Where: `client.py:59` (`https://mermaid.prescient.earth/stac/`)
- What: The other defaults live under `datamermaid.org`. If the vendor host moves, every installed version breaks.
- Recommendation: Consider a `stac.datamermaid.org` CNAME so the SDK default stays under MERMAID's control.

**L10. CI supply-chain polish** (tooling)
- Where: `.github/workflows/*.yml`
- What: Actions are pinned to major tags (`@v4`, `@v5`), not commit SHAs, and the release job does not publish build attestations.
- Recommendation: Pin to SHAs with Dependabot for updates, and add `actions/attest-build-provenance` once PyPI publishing exists.

## Suggested next steps

Quick wins, all non-breaking:

1. Delete the stray local tags. Add `*.jsonl` to `.gitignore` and remove `sst-results.jsonl`. Clean up `examples/covariates.py` (PEP 723 header, index entry, ruff fix, remove dead code), or keep it out of the commit (M5, M7).
2. Reserve `datamermaid` on PyPI and publish with trusted publishing. Until then, change the install lines to the GitHub Release or git URL (H1).
3. Pass `timeout`, `max_retries` and User-Agent into pystac-client through `StacApiIO`. Wrap `APIError` in `MermaidAPIError` or `MermaidConnectionError`, and close the session from `client.close()` (H2).
4. Add a `max_requests` guard to `CovariateCollection.zonal_stats`, and require `datetime` or `max_items` for multi-item collections (H3).
5. Add `cache=` to `prepare` and `prepare_zonal_stats`, and warn when `request_count` exceeds the default cache size (M2).
6. Add library logging for retries and throttling (M8), the missing configuration table row and the site filter in the covariates guide (L6), and remove `BatchItem` (L5).
7. Add a CI job that runs without extras (M6), plus a `CHANGELOG.md` and a version bump for the covariates release (M7).

Structural work:

8. Rework `_execute` so submission does not wait on in-order yield, and add `ordered=False` for streams (H4). The API does not change, only throughput and memory behaviour.
9. Flatten STAC provenance into `item_id`, `collection`, `datetime` and `asset` columns in `ZonalStatsResult.to_dict` (M4). **Breaks the output schema** of `to_df()` and `to_dict()`.
10. Make `errors="raise"` fail fast and raise `BatchFailure` with the task attached (M3). **Changes which exception type is raised**, although it still subclasses `Exception`.
11. Collapse the overload grid (M1). Split `stream` into its own method, move failures to `Batch.failures`, and type route options with `TypedDict` plus `Unpack`. **Breaks the public API** (`batch(stream=True)`, `run(stream=True)`, and the element type of `Batch` iteration). Schedule it with items 9 and 10 for a single 0.2.0 release, with migration notes.
12. After that, trim the top-level `__all__` and replace the `CovariateCollection.__getattr__` pass-through with explicit properties (L1, L5). **Minor break** for anyone importing implementation classes from the top level.
