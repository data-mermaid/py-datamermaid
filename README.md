# datamermaid

Python SDK for the [MERMAID](https://datamermaid.org/) coral reef monitoring API.

- Typed models with a lossless catch-all for fields the API adds later
- Lazy pagination: pages are fetched only as you consume them
- One-line export to a pandas DataFrame
- Pluggable authentication (API key today, OAuth later) and automatic retries

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
| `timeout` | | `30.0` seconds |
| `max_retries` | | `3` (429 and 5xx, with exponential backoff honouring `Retry-After`) |

The development instance is `https://dev-api.datamermaid.org/v1/`, exported as
`datamermaid.DEV_BASE_URL`.

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

## Development

See [CLAUDE.md](CLAUDE.md) for build and test commands.

## License

MIT
