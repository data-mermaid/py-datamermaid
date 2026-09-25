# datamermaid

Python SDK for the [MERMAID](https://datamermaid.org/) coral reef monitoring API.

MERMAID is the data platform coral reef scientists use to record, clean and
share reef surveys: fish belt transects, benthic point intercepts, bleaching
quadrat collections and four more protocols, from thousands of sites worldwide.
Its [REST API](https://api.datamermaid.org/v1/) publishes that data, plus the
reference taxonomies every survey is coded against. This package is a thin,
typed wrapper over it.

- Typed models with a lossless catch-all for fields the API adds later
- Lazy pagination: pages are fetched only as you consume them
- One-line export to a `pandas` DataFrame
- Pluggable authentication: an API key, or an OAuth login that also works over SSH
- Zonal statistics for a site or a polygon, batched over a project in parallel
- Automatic retries on rate limits and server errors

## Install

=== "uv"

    ```bash
    uv add datamermaid
    # with the optional pandas export
    uv add 'datamermaid[pandas]'
    # with the covariates catalog (pystac-client)
    uv add 'datamermaid[covariates]'
    ```

=== "pip"

    ```bash
    pip install datamermaid
    pip install 'datamermaid[pandas]'
    pip install 'datamermaid[covariates]'
    ```

Python 3.10 or newer is required.

## Quickstart

The fish taxonomy is public, so the shortest useful program needs no
credentials at all:

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    surgeonfish = client.fish_families.list(search="Acanthuridae")[0]
    print(surgeonfish.name, surgeonfish.id)
```

Everything recorded in a project needs credentials. Create an API key in
MERMAID, export it, and the client picks it up:

```bash
export MERMAID_API_KEY='mmd_<key_id>.<secret>'
```

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    me = client.me()
    print(me.full_name, me.email)

    for project in client.projects.list():
        print(project.name, project.countries, project.num_sites)
```

No API key? [`datamermaid.login()`](authentication.md) opens a browser once and
caches a token that every later `MermaidClient()` reuses.

## From a project id to a DataFrame

The API publishes each protocol denormalized, as flat rows with the site,
management regime and project already joined in. That is four lines from a
project id to an analysis-ready table:

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    project = client.projects("d5491b25-4a5f-401b-a50f-bb80fd1df78f")
    observations = project.beltfishes.observations(sample_date_after="2018-01-01").to_df()
    print(observations[["site_name", "sample_date", "fish_taxon", "biomass_kgha"]].head())
```

## Where to go next

<div class="grid cards" markdown>

- **[Authentication](authentication.md)**

    API keys, the browser login, logging in from an SSH session, and where the
    token cache lives.

- **[Working with data](data.md)**

    Lazy pagination, filters, `to_df()`, the reference taxonomies and the
    `/choices/` vocabularies.

- **[Project data](projects.md)**

    Project-scoped collections, the aggregated observation views, and a worked
    biomass analysis ending in a `pandas` groupby.

- **[Zonal statistics](zonal_stats.md)**

    Raster and vector statistics for a site, a polygon or a whole project's
    sites, from the public Zonal Stats service.

- **[Examples](examples.md)**

    Runnable scripts and two marimo notebooks, each self-installing via `uv run`.

- **[API reference](reference/index.md)**

    Every public class and function, generated from the source.

</div>

## Configuration

| Argument | Environment variable | Default |
| --- | --- | --- |
| `api_key` | `MERMAID_API_KEY` | none (anonymous) |
| `base_url` | `MERMAID_API_URL` | `https://api.datamermaid.org/v1/` |
| `zonal_stats_url` | `MERMAID_ZONAL_STATS_URL` | `https://api.zonalstats.datamermaid.org/api/v1/zonal-stats/` |
| `timeout` | | `30.0` seconds |
| `max_retries` | | `3` (429 and 5xx, exponential backoff honouring `Retry-After` up to 30 s) |

The development instance is `https://dev-api.datamermaid.org/v1/`, exported as
[`datamermaid.DEV_BASE_URL`][datamermaid.client.DEV_BASE_URL]. The
[Zonal Stats service](zonal_stats.md) is a separate public host, so it has its
own setting and receives no credentials.

## Logging

The SDK logs to the `datamermaid` logger and its children, such as
`datamermaid.client`. It adds no handler of its own, so nothing is printed until
your application configures logging:

```python
import logging

logging.basicConfig(level=logging.INFO)  # retries, throttling, zonal job sizes
logging.getLogger("datamermaid").setLevel(logging.DEBUG)  # also every response
```

| Level | What is logged |
| --- | --- |
| `DEBUG` | each response, with its method, URL, status and time; zonal stats cache hits |
| `INFO` | each retry and its reason, a `429` throttle, a token refresh, the size of each zonal stats job |

Log records never include credentials, request headers or request bodies.

## License

GPLv3. See [LICENSE](https://github.com/data-mermaid/py-datamermaid/blob/main/LICENSE).
