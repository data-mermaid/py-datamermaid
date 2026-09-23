# datamermaid


## [[ **Active Development** ]]


Python SDK for the [MERMAID](https://datamermaid.org/) coral reef monitoring API.

**[Documentation](https://data-mermaid.github.io/py-datamermaid/)** -
[Quickstart](https://data-mermaid.github.io/py-datamermaid/#quickstart) -
[Authentication](https://data-mermaid.github.io/py-datamermaid/authentication/) -
[API reference](https://data-mermaid.github.io/py-datamermaid/reference/)

- Typed models with a lossless catch-all for fields the API adds later
- Lazy pagination: pages are fetched only as you consume them
- One-line export to a pandas DataFrame
- Pluggable authentication: API key, or an OAuth login that also works over SSH
- Zonal statistics for a site or a polygon, batched over a whole project in parallel
- Automatic retries on rate limits and server errors

## Install

```bash
uv add datamermaid
# with the optional pandas export
uv add 'datamermaid[pandas]'
```

Or with pip:

```bash
pip install 'datamermaid[pandas]'
```

Python 3.10 or newer is required.

## Quickstart

Create an API key in MERMAID, then either pass it explicitly or export it as
`MERMAID_API_KEY`:

```bash
export MERMAID_API_KEY='mmd_<key_id>.<secret>'
```

```python
from datamermaid import MermaidClient

with MermaidClient() as client:  # or MermaidClient(api_key="mmd_<key_id>.<secret>")
    me = client.me()
    print(me.full_name, me.email)

    for project in client.projects.list():
        print(project.name, project.countries, project.num_sites)

    project = client.projects.get("d5491b25-4a5f-401b-a50f-bb80fd1df78f")
    print(project.data_policies)
```

The key is sent as `Authorization: Bearer mmd_<key_id>.<secret>`.

### Project data

Everything recorded under one project hangs off a project handle, which you get
by calling `client.projects` with a project id (or a `Project` you already
fetched). `client.projects.get(id)` fetches the project record itself;
`client.projects(id)` opens what is recorded under it:

```python
project = client.projects("d5491b25-4a5f-401b-a50f-bb80fd1df78f")

for site in project.sites.list():
    print(site.name, site.reef_type)

survey = project.beltfish_methods.get(method_id)
print(survey.sample_event.sample_date, survey.fishbelt_transect.len_surveyed)
for observation in survey.observations["obs_belt_fishes"]:
    print(observation["size"], observation["count"])
```

Building the handle issues no request, and each collection below it is the same
lazy `PaginatedList` as everywhere else, with `.list(**filters)`, `.get(id)` and
`.to_df()`:

| Attribute | Route under `/projects/{id}/` | Model |
| --- | --- | --- |
| `project.sites` | `sites/` | `Site` |
| `project.managements` | `managements/` | `Management` |
| `project.observers` | `observers/` | `Observer` |
| `project.project_profiles` | `project_profiles/` | `ProjectProfile` |
| `project.sample_events` | `sampleevents/` | `SampleEvent` |
| `project.fishbelt_transects` | `fishbelttransects/` | `FishBeltTransect` |
| `project.benthic_transects` | `benthictransects/` | `BenthicTransect` |
| `project.beltfish_methods` | `beltfishtransectmethods/` | `BeltFishMethod` |
| `project.benthiclit_methods` | `benthiclittransectmethods/` | `BenthicLITMethod` |
| `project.benthicpit_methods` | `benthicpittransectmethods/` | `BenthicPITMethod` |
| `project.benthicpqt_methods` | `benthicphotoquadrattransectmethods/` | `BenthicPhotoQuadratTransectMethod` |
| `project.habitatcomplexity_methods` | `habitatcomplexitytransectmethods/` | `HabitatComplexityMethod` |
| `project.bleachingqc_methods` | `bleachingquadratcollectionmethods/` | `BleachingQuadratCollectionMethod` |
| `project.beltinvert_methods` | `beltinverttransectmethods/` | `BeltInvertMethod` |

The `*_methods` collections are the sample units with their observations: one
record per survey, carrying the `SampleEvent`, the transect or quadrat
collection it was recorded on, the `Observer`s who recorded it, and the
observation rows. Those rows stay as plain dictionaries, since their columns
differ per protocol and run to thousands of rows per survey:

```python
survey.sample_unit  # the transect or quadrat collection, whatever the protocol
survey.observations  # {"obs_belt_fishes": ({...}, {...}), ...}
survey.observers[0].profile_name

# One flat table of every fish belt survey in the project.
project.beltfish_methods.list().to_df()
```

All of these need credentials with access to the project.

### Observation data

The `*_methods` collections above are shaped for editing one survey at a time.
For analysis the API also publishes each protocol denormalized, as flat rows
with the site, management regime and project already joined in. That is the
four-line path from a project id to a DataFrame:

```python
from datamermaid import MermaidClient

with MermaidClient(api_key="mmd_abc.def") as client:
    project = client.projects("d5491b25-4a5f-401b-a50f-bb80fd1df78f")
    observations = project.beltfishes.observations(sample_date_after="2018-01-01").to_df()
    observations[["site_name", "sample_date", "fish_taxon", "size", "count", "biomass_kgha"]]
```

Every family answers the same three methods, each a lazy `PaginatedList` of
`AggregatedRecord` rows: `.observations()` (one row per observation),
`.sample_units()` (one row per transect or quadrat collection, with that unit's
aggregates) and `.sample_events()` (one row per site visit, averaged over its
sample units).

| Attribute | Observations | Sample units | Sample events |
| --- | --- | --- | --- |
| `project.beltfishes` | `beltfishes/obstransectbeltfishes/` | `beltfishes/sampleunits/` | `beltfishes/sampleevents/` |
| `project.benthiclits` | `benthiclits/obstransectbenthiclits/` | `benthiclits/sampleunits/` | `benthiclits/sampleevents/` |
| `project.benthicpits` | `benthicpits/obstransectbenthicpits/` | `benthicpits/sampleunits/` | `benthicpits/sampleevents/` |
| `project.benthicpqts` | `benthicpqts/obstransectbenthicpqts/` | `benthicpqts/sampleunits/` | `benthicpqts/sampleevents/` |
| `project.habitatcomplexities` | `habitatcomplexities/obshabitatcomplexities/` | `habitatcomplexities/sampleunits/` | `habitatcomplexities/sampleevents/` |
| `project.bleachingqcs` | `bleachingqcs/obscoloniesbleacheds/` and `bleachingqcs/obsquadratbenthicpercents/` | `bleachingqcs/sampleunits/` | `bleachingqcs/sampleevents/` |
| `project.beltinverts` | `beltinverts/obstransectbeltinverts/` | `beltinverts/sampleunits/` | `beltinverts/sampleevents/` |

Bleaching records two kinds of observation, so it has two observation views,
`.colonies_bleached()` and `.quadrat_benthic_percent()`; `.observations()` is an
alias of the first.

```python
project.bleachingqcs.colonies_bleached().to_df()
project.bleachingqcs.quadrat_benthic_percent().to_df()
```

Keyword arguments are the API's own query parameters: the shared filters
(`sample_date_after`, `sample_date_before`, `site_id`, `site_name`,
`management_id`, `country_name`, `depth_min`, `depth_max`, `label`,
`observers`, ...), each protocol's own (`fish_family`, `benthic_category`,
`biomass_kgha_min`, ...), and the parameters every list route understands
(`limit`, `ordering`, `fields`):

```python
project.beltfishes.sample_events(
    sample_date_after="2018-01-01",
    site_name="Namena South",
    ordering="sample_date",
    limit=200,
)
```

These rows are wide and their columns differ per protocol and per view, so
`AggregatedRecord` declares only what every view shares (`id`, `project_id`,
`project_name`, `site_id`, `site_name`, `sample_date`, `management_id`,
`sample_event_id`) and keeps the rest in `extra`. `to_dict()` and `to_df()`
flatten both, so each field the API sent is one DataFrame column:

```python
row = project.benthicpits.observations(limit=1)[0]
row.site_name, row.sample_date  # declared
row.extra["benthic_category"]  # protocol column, as the API sent it
```

The sample unit views carry no `id` (they report `sample_unit_ids` instead), so
`row.id` is `None` on those. The `/csv/` and `/geojson/` variants of these
routes are not wrapped; the SDK reads the JSON views only.

### Reference data and summaries

Beyond `/projects/`, every top-level route is reachable as a client attribute
returning the same lazy `PaginatedList` of typed models:

| Attribute | Route | Model | Auth |
| --- | --- | --- | --- |
| `client.sites` | `/sites/` | `Site` | required |
| `client.managements` | `/managements/` | `Management` | required |
| `client.project_tags` | `/projecttags/` | `ProjectTag` | public |
| `client.fish_sizes` | `/fishsizes/` | `FishSize` | public |
| `client.fish_families` | `/fishfamilies/` | `FishFamily` | public |
| `client.fish_genera` | `/fishgenera/` | `FishGenus` | public |
| `client.fish_species` | `/fishspecies/` | `FishSpecies` | public |
| `client.benthic_attributes` | `/benthicattributes/` | `BenthicAttribute` | public |
| `client.invert_attributes` | `/invertattributes/` | `InvertAttribute` | public |
| `client.invert_species` | `/invertspecies/` | `InvertSpecies` | public |
| `client.summary_sample_events` | `/summarysampleevents/` | `SummarySampleEvent` | public |
| `client.label_mappings` | `/classification/labelmappings/` | `LabelMapping` | public |

Each has `.list(**filters)` and `.get(id)`. Load the fish taxonomy into a
DataFrame:

```python
from datamermaid import MermaidClient

with MermaidClient() as client:  # taxonomy is public, no credentials needed
    species = client.fish_species.list(limit=1000).to_df()
    species[["display_name", "max_length", "trophic_level"]].head()

    # Filters are the API's own, passed through as query parameters.
    acanthuridae = client.fish_families.list(search="Acanthuridae")[0]
    for genus in client.fish_genera.list(family=acanthuridae.id):
        print(genus.name, len(client.fish_species.list(genus=genus.id)))
```

Site-level summaries of every public sample event, with the per-protocol
aggregates kept as a nested mapping:

```python
summaries = client.summary_sample_events.list(project_name="Fiji", sample_date_after="2018-01-01")
for summary in summaries:
    print(summary.site_name, summary.sample_date, summary.protocols.keys())
```

`client.sites` and `client.managements` return the sites and management
regimes of every project the credentials can see, so they need an API key or a
login.

### Choices

`/choices/` is the API's set of controlled vocabularies, and the one list route
that is not paginated, so it comes back as plain dictionaries:

```python
choices = client.choices()  # every set, keyed by name
choices.keys()  # 'countries', 'reeftypes', 'managementparties', ...
[reef_type["name"] for reef_type in choices["reeftypes"]]

client.choices("reeftypes")  # just one set, from /choices/reeftypes/
```

### Zonal statistics

`client.zonal_stats` computes raster and vector statistics for a place: the mean
depth within 500 m of a site, the habitat classes a survey polygon covers. They
come from the MERMAID Zonal Stats service, a separate public host that takes no
credentials, and you bring the data source as a URL. There are four endpoints,
`raster` and `vector` for a Cloud Optimized GeoTIFF or a GeoParquet file, and
`raster_stac` and `vector_stac` for an asset of a STAC Item:

```python
from datamermaid import MermaidClient

with MermaidClient() as client:  # the service is public, no credentials needed
    result = client.zonal_stats.raster.stats(
        {"type": "Point", "coordinates": [178.4, -18.1]},
        url="https://example.test/depth.tif",
        stats=["mean", "count"],
        radius=500,
    )
    print(result["band_1"]["mean"])
```

The area of interest can be a GeoJSON mapping, anything with a
`__geo_interface__` (a shapely geometry, a GeoDataFrame row), a `(lon, lat)`
tuple or a `Site`, and `to_aoi` is exported if you want to normalise one
yourself. `batch()` runs one request per area on a thread pool, lazily, so a
whole project becomes one table:

```python
with MermaidClient() as client:
    project = client.projects("d5491b25-4a5f-401b-a50f-bb80fd1df78f")
    # A site without a location has no area to measure, so leave it out.
    sites = [site for site in project.sites.list() if site.location]
    batch = client.zonal_stats.raster.batch(
        sites,
        url="https://example.test/depth.tif",
        stats=["mean"],
        radius=500,
        max_workers=4,
    )
    depth = batch.to_df()  # one wide row per site: label, source, band_1_mean, ...
```

The sites are fetched when `batch(...)` is called, but no statistics request is
sent until the batch is iterated, indexed or exported. The workers share the
client's rate-limit backoff, and `errors="return"` keeps a partly failing batch
usable. See the
[zonal statistics guide](https://data-mermaid.github.io/py-datamermaid/zonal_stats/)
for the STAC routes, the weighting methods and the long-form export.

### Pagination

`client.projects.list()` returns a `PaginatedList`. It issues no request until
you use it, then fetches one page at a time and caches what it has seen:

```python
projects = client.projects.list(limit=100)

len(projects)  # total reported by the API, from the first page only
projects[0]  # fetches page 1
projects[:10]  # fetches only as far as it needs to
for project in projects:
    ...  # page 2 is requested when iteration passes the end of page 1
```

Any keyword argument is passed through as a query parameter, e.g.
`client.projects.list(showall=True, limit=100)`.

### DataFrames

```python
df = client.projects.list().to_df()  # requires datamermaid[pandas]
df[["name", "num_sites", "countries"]].head()
```

`to_df()` materialises every page, so narrow the query first on large
collections.

### Configuration

| Argument | Environment variable | Default |
| --- | --- | --- |
| `api_key` | `MERMAID_API_KEY` | none (anonymous) |
| `base_url` | `MERMAID_API_URL` | `https://api.datamermaid.org/v1/` |
| `zonal_stats_url` | `MERMAID_ZONAL_STATS_URL` | `https://api.zonalstats.datamermaid.org/api/v1/zonal-stats/` |
| `timeout` | | `30.0` seconds |
| `max_retries` | | `3` (429 and 5xx, with exponential backoff honouring `Retry-After` up to 30 s) |

The development instance is `https://dev-api.datamermaid.org/v1/`, exported as
`datamermaid.DEV_BASE_URL`. The Zonal Stats service is a separate public
host, so it has its own setting and is sent no credentials.

### Errors

All errors derive from `MermaidError`:

| Status | Exception |
| --- | --- |
| 401, 403 | `AuthenticationError` |
| 404 | `NotFoundError` |
| 429 | `RateLimitError` (with `.retry_after`) |
| 5xx | `ServerError` |
| other 4xx | `MermaidAPIError` |
| network failure | `MermaidConnectionError` |

An interactive login that cannot be completed raises `AuthFlowError` (or
`AuthTimeoutError` when the user simply never finished), which is separate
from the `AuthenticationError` the API raises for credentials it rejects.

```python
from datamermaid import MermaidError, NotFoundError

try:
    client.projects.get("does-not-exist")
except NotFoundError:
    ...
except MermaidError as exc:
    print(exc)
```

### Unknown fields

Models never drop data. Anything the SDK does not declare is kept in `.extra`,
and `.to_dict()` merges it back in:

```python
project.extra  # {"some_new_api_field": ...}
project.to_dict()  # declared fields plus extras, one flat row
```

## Authentication

Credentials are picked in this order: an explicit `auth=`, then `api_key=`,
then `MERMAID_API_KEY`, then a token left behind by `datamermaid.login()`,
then anonymous access. A cached token is only picked up implicitly while it is
still usable (unexpired, or refreshable without asking you anything): an
ordinary data call never opens a browser on its own, so a stale login leaves
the client anonymous until you run `datamermaid.login()` again.

### Signing in without an API key

`datamermaid.login()` runs an OAuth 2.0 login against MERMAID's Auth0 tenant
and caches the resulting token, so it only has to be done once:

```python
import datamermaid

datamermaid.login()  # opens your browser

with datamermaid.MermaidClient() as client:  # picks the cached token up
    print(client.me().full_name)

datamermaid.logout()  # forget the cached token
```

The token is sent as `Authorization: Bearer <access_token>` and is refreshed
automatically while a refresh token is available. To keep the credentials on a
single client instead, pass the auth object explicitly:

```python
from datamermaid import MermaidClient, OAuth

with MermaidClient(auth=OAuth(flow="device")) as client:
    ...  # the first request triggers the login
```

#### Flows

`OAuth(flow=...)` and `datamermaid.login(flow=...)` accept:

| Flow | What happens |
| --- | --- |
| `"auto"` (default) | `pkce` on a desktop, `device` (then `manual`) when the terminal looks headless |
| `"pkce"` | Authorization code with PKCE. Starts a one-shot HTTP server on an ephemeral loopback port, opens your browser, exchanges the code for tokens. |
| `"device"` | Device authorization grant. Prints a short code and a URL to open on any other device, then polls until you approve. Nothing is opened locally. |
| `"implicit"` | Legacy implicit grant, kept for parity with the older MERMAID clients. The token arrives in the redirect fragment and there is no refresh token. |
| `"manual"` | No server at all: the authorization URL is printed, and you paste the redirect URL (or just the code) back into the terminal. |

A session is treated as headless when `SSH_CONNECTION`/`SSH_TTY` is set, or on
Unix when neither `DISPLAY` nor `WAYLAND_DISPLAY` is. The device grant is
probed first and `manual` is used if the tenant has it disabled.

**Remote terminal, local browser.** Over SSH, `auto` gives you the device
flow: open the printed URL on your laptop, type the code, and the terminal
picks the token up. If that grant is unavailable, you are asked to paste the
redirect URL - the browser will land on a `localhost` page that cannot load,
and its address bar holds the code.

#### Token cache

Tokens live in `$XDG_CONFIG_HOME/datamermaid/tokens.json` (defaulting to
`~/.config/datamermaid/tokens.json`), written with mode `0600`. One entry is
kept per tenant/client/audience, so the production and development tenants can
be logged in to side by side. Expiry is read from the access token's `exp`
claim (decoded, never verified locally) with a 60 second margin; an expired
token is refreshed silently, and only a failed refresh prompts a new login.
The file is written by rename, so an interrupted save never truncates the
tokens of the tenants it was not touching.

Pass `cache=False` to keep tokens in memory only, or `cache="/path/to.json"`
to move the file.

#### Auth0 configuration

| Argument | Environment variable | Default |
| --- | --- | --- |
| `domain` | `MERMAID_AUTH0_DOMAIN` | `datamermaid.auth0.com` |
| `client_id` | `MERMAID_CLIENT_ID` | `6q1XvYG0n75ZaLbFko0gUV4xGud4uPyG` |
| `audience` | `MERMAID_AUDIENCE` | `https://api.datamermaid.org` |
| `scope` | | `openid profile email offline_access` |

Keyword arguments win over the environment, which wins over the defaults.

The `pkce` and `implicit` flows redirect to `http://localhost:<ephemeral
port>/`. Auth0 matches Allowed Callback URLs literally, so a tenant that has
not registered port-agnostic loopback URLs will answer with "Callback URL
mismatch"; pin the redirect with `redirect_port=` (the R client, `mermaidr`,
registers `1410`) and `redirect_host=` to match what the application allows.
The socket itself always listens on `127.0.0.1` only. Use
`redirect_host="127.0.0.1"` if the registered URL uses the literal address
that RFC 8252 recommends rather than the `localhost` name. Neither setting
matters for the `device` flow, which has no redirect at all.

## Examples

[`examples/`](examples/README.md) holds runnable versions of everything above:
five scripts (API key quickstart, OAuth login, reference data, project data,
zonal statistics) and two [marimo](https://marimo.io) notebooks that serve
themselves. Each file
carries PEP 723 inline metadata, so no setup is needed beyond `uv`:

```bash
uv run examples/reference_data.py                   # public data, no credentials
uv run examples/marimo/explore_projects.py          # serves the notebook
```

## AI-friendly repo context

The SDK - source, tests and the docs in this file - can be packed into a single
file for LLMs and coding agents with [RepoMix](https://repomix.com/):

```bash
npx repomix
```

That picks up `repomix.config.json` and writes `repomix-output.xml`: one XML
document with a directory summary and every included file, scanned for secrets
before it is written. The output is git-ignored, since it is a generated
artifact - regenerate it rather than committing it.

On every push to `main` the *Docs* workflow regenerates it and publishes it
with the documentation site, so agents can fetch the current pack directly:

<https://data-mermaid.github.io/py-datamermaid/repomix-output.xml>

## Documentation

The full guides and the generated API reference live at
<https://data-mermaid.github.io/py-datamermaid/>, built with
[MkDocs](https://www.mkdocs.org/) from [`docs/`](docs/) and published by the
*Docs* workflow on every push to `main`. To work on them locally:

```bash
uv run --group docs mkdocs serve          # live-reloading preview on :8000
uv run --group docs mkdocs build --strict # what CI runs; warnings are failures
```

`tests/test_docs.py` resolves the attribute chains in every Python code block on
the site against the real SDK objects, so a renamed attribute fails the ordinary
test run rather than shipping as confident-sounding prose.

## Development

The project is managed with [uv](https://docs.astral.sh/uv/) and targets
Python 3.10+. CI (`.github/workflows/ci.yml`) runs the same commands on Python
3.10 to 3.13.

```bash
uv sync --all-extras       # create .venv with the runtime, extra and dev deps
uv run pytest              # the test suite; all HTTP is mocked with respx
uv run ruff check .        # lint
uv run ruff format .       # format (CI runs it with --check)
uv run mypy src            # type check
```

The source is in `src/datamermaid/`:

- `client.py`: `MermaidClient`. It wraps an `httpx.Client` and owns the base
  URL, the Zonal Stats URL (`zonal_stats_url`), timeouts, retry with backoff on
  429 and 5xx, the shared throttle deadline and JSON decoding.
- `auth/`: the `Auth` interface and its implementations: API keys, OAuth flows,
  the Auth0 config, the loopback callback server and the token cache.
- `exceptions.py`: the `MermaidError` hierarchy and the status-code mapping.
- `models.py`: frozen dataclasses. `APIModel.from_api()` fills the declared
  fields and keeps everything else in `extra`. `ZonalStatsResult` is here too.
- `pagination.py`: `PaginatedList`, a lazy, caching view over list responses.
- `batch.py`: `LazyBatch`, lazy and parallel results of one computation per
  input, used by the zonal stats `batch()` methods.
- `geometry.py`: `to_aoi`, which normalises an area of interest to GeoJSON.
- `resources/`: one module per endpoint group. `zonal_stats.py` holds the four
  Zonal Stats endpoints, which talk to the separate public service host.

## License

MIT, see [LICENSE](LICENSE).
