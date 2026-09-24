# Examples

The repository's [`examples/`](https://github.com/data-mermaid/py-datamermaid/tree/main/examples)
directory holds runnable versions of everything in these guides: five scripts
and two [marimo](https://marimo.io) notebooks.

Each file carries its own [PEP 723](https://peps.python.org/pep-0723/) inline
metadata, so `uv run` builds the environment it needs (including the SDK, from
your checkout) and nothing has to be installed first:

```bash
git clone https://github.com/data-mermaid/py-datamermaid
cd py-datamermaid
uv run examples/reference_data.py
```

Every example degrades gracefully: with no usable credentials it prints how to
set them instead of raising.

## Credentials

Reference data and the public summaries need none. The rest wants either an API
key:

```bash
export MERMAID_API_KEY='mmd_<key_id>.<secret>'
```

or a browser login, which caches a token that every later `MermaidClient()`
picks up on its own:

```bash
uv run examples/oauth_login.py
```

See [Authentication](authentication.md) for the details.

## Scripts

| Example | What it shows | Run it |
| --- | --- | --- |
| [`quickstart_api_key.py`](https://github.com/data-mermaid/py-datamermaid/blob/main/examples/quickstart_api_key.py) | Authenticate with an API key, read `/me/`, list the projects it can see | `uv run examples/quickstart_api_key.py` |
| [`oauth_login.py`](https://github.com/data-mermaid/py-datamermaid/blob/main/examples/oauth_login.py) | Interactive login and token cache, including the device flow for SSH sessions | `uv run examples/oauth_login.py` |
| [`reference_data.py`](https://github.com/data-mermaid/py-datamermaid/blob/main/examples/reference_data.py) | Public fish and benthic taxonomies as DataFrames, joined to the `/choices/` vocabularies | `uv run examples/reference_data.py` |
| [`project_data.py`](https://github.com/data-mermaid/py-datamermaid/blob/main/examples/project_data.py) | One project's sites, sample events and fish belt observations as DataFrames | `uv run examples/project_data.py --project-id <uuid>` |
| [`zonal_stats.py`](https://github.com/data-mermaid/py-datamermaid/blob/main/examples/zonal_stats.py) | Raster statistics around every site of one project, batched in parallel | `uv run examples/zonal_stats.py --url <cog-url>` |
| [`zonal_stats_sst.py`](https://github.com/data-mermaid/py-datamermaid/blob/main/examples/zonal_stats_sst.py) | Daily CoralTemp means around project sites, streamed to JSONL | `uv run examples/zonal_stats_sst.py --project-id <uuid>` |

`project_data.py` takes `--project-id` (defaulting to `$MERMAID_PROJECT_ID`, and
otherwise to the first project the credentials can see), `--limit` and
`--sample-date-after`. `oauth_login.py` takes `--flow`, `--force` and `--logout`.

`zonal_stats.py` takes the same `--project-id`, plus a required `--url` (a Cloud
Optimized GeoTIFF the [Zonal Stats service](zonal_stats.md) can read), `--stats`,
`--radius`, `--max-workers` and `--limit`. That service is public, so only the
project's sites need credentials.

## Marimo notebooks

[marimo](https://marimo.io) notebooks are plain Python files, so they diff and
review like any other source. Both of these serve themselves: running one starts
the marimo editor and prints a URL to open.

| Notebook | What it shows | Run it |
| --- | --- | --- |
| [`marimo/explore_projects.py`](https://github.com/data-mermaid/py-datamermaid/blob/main/examples/marimo/explore_projects.py) | Filter the public sample event summaries and chart them by year and reef type | `uv run examples/marimo/explore_projects.py` |
| [`marimo/fish_observations.py`](https://github.com/data-mermaid/py-datamermaid/blob/main/examples/marimo/fish_observations.py) | Fish biomass per family, from the public summaries and from a project's own observation rows | `uv run examples/marimo/fish_observations.py` |

`explore_projects.py` needs no credentials at all: `/summarysampleevents/` is
public. `fish_observations.py` starts public and then reads one project's
observations, which does need credentials.

Marimo itself comes from the notebook's inline metadata, so it does not need to
be installed globally. The equivalent explicit invocation is:

```bash
uvx marimo edit examples/marimo/explore_projects.py   # offers to sandbox it
uvx marimo edit --sandbox examples/marimo/explore_projects.py
```

To execute the cells once and exit rather than serving them, clear marimo's
switch:

```bash
MARIMO_SCRIPT_EDIT= uv run examples/marimo/explore_projects.py
```

!!! note

    Saving a notebook from the marimo editor rewrites its `__main__` block with
    marimo's own, which drops that switch. Put the two lines back to keep the
    notebook self-serving.

## How they stay correct

The inline metadata resolves `datamermaid` from the checkout
(`[tool.uv.sources]`, path-relative to the example), so the examples always
exercise the working tree rather than a published release.

`tests/test_examples.py` compiles every example and checks its metadata and its
entry in the index, and `tests/test_docs.py` parses every Python block on this
site and resolves it against the real SDK objects. Neither makes a network call.

## Sea surface temperature and sites

```bash
uv run examples/zonal_stats_sst.py --project-id <uuid> \
  --datetime 2024-01-01/2024-01-03 --limit 5 --max-items 3 \
  --output sst-results.jsonl
```

The example reads NOAA CoralTemp (`daily_sst`) from the
[MERMAID covariates catalog](https://mermaid.prescient.earth/stac) through
`client.covariates`, prepares one mean calculation
per site and day, and streams success and failure rows to JSONL. It defaults to
five sites, three daily items, a 500 m radius, and four workers. Use `--stac-url`
to override the catalog URL; `--project-id` defaults to `$MERMAID_PROJECT_ID`,
then the first visible project. The STAC catalog and statistics service are
public; project sites require credentials.

Rows include the site ID and name, STAC item/date, source URL, and
`sst_mean_celsius` (or an `error`). The service applies the raster scale factor;
do not multiply the mean by 0.01 again. CoralTemp is a roughly 5 km grid, so a
500 m buffer does not imply 500 m source resolution. This is a spatial mean for
each daily item, not an average across dates. Land or nodata pixels can produce
no valid mean. The output file is overwritten on a new run; incremental writes
preserve completed rows if interrupted, but there is no automatic resume.
