# datamermaid

Python SDK for the [MERMAID](https://datamermaid.org/) coral reef monitoring API.

- Typed models with a lossless catch-all for fields the API adds later
- Lazy pagination: pages are fetched only as you consume them
- One-line export to a pandas DataFrame
- Pluggable authentication: API key, or an OAuth login that also works over SSH
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
then anonymous access.

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

Pass `cache=False` to keep tokens in memory only, or `cache="/path/to.json"`
to move the file.

#### Auth0 configuration

| Argument | Environment variable | Default |
| --- | --- | --- |
| `domain` | `MERMAID_AUTH0_DOMAIN` | `datamermaid.auth0.com` |
| `client_id` | `MERMAID_CLIENT_ID` | `6q1XvYG0n75ZaLbFko0gUV4xGud4uPyG` |
| `audience` | `MERMAID_AUDIENCE` | `https://api.datamermaid.org` |
| `scope` | | `openid profile email offline_access` |

Keyword arguments win over the environment, which wins over the defaults. The
loopback redirect can be pinned with `redirect_port=` and `redirect_host=` if
the Auth0 application only allows a fixed callback URL.

## Development

See [CLAUDE.md](CLAUDE.md) for build and test commands.

## License

MIT, see [LICENSE](LICENSE).
