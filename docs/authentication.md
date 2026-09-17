# Authentication

Reference data (fish and benthic taxonomies, size bins, project tags, the public
sample event summaries and the `/choices/` vocabularies) is open. Everything
recorded in a project needs credentials, as do `/me/`, `/sites/` and
`/managements/`.

There are two ways to get them: an **API key**, best for scripts and scheduled
jobs, and an **OAuth login**, best at an interactive prompt.

## Credential precedence

[`MermaidClient`][datamermaid.client.MermaidClient] picks a credential provider
in this order:

1. an explicit `auth=` object,
2. an explicit `api_key=`,
3. the `MERMAID_API_KEY` environment variable,
4. a token left behind by [`datamermaid.login()`][datamermaid.auth.oauth.login],
5. anonymous access.

A cached token is only picked up implicitly while it is still usable, meaning
unexpired or refreshable without asking you anything. An ordinary data call
never opens a browser on its own, so a stale login leaves the client anonymous
until you run `datamermaid.login()` again.

## API keys

Create a key in the MERMAID web app (**Profile → API keys** at
[app.datamermaid.org](https://app.datamermaid.org/)). It looks like
`mmd_<key_id>.<secret>`, and the secret half is shown only once.

Export it and the client finds it:

```bash
export MERMAID_API_KEY='mmd_<key_id>.<secret>'
```

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    print(client.me().full_name)
```

Or pass it explicitly, which is what you want when one process talks to two
accounts:

```python
from datamermaid import MermaidClient

with MermaidClient(api_key="mmd_<key_id>.<secret>") as client:
    print(client.me().email)
```

The key is sent as `Authorization: Bearer mmd_<key_id>.<secret>`. The
[`APIKeyAuth`][datamermaid.auth.base.APIKeyAuth] object never prints the secret:
its `repr` shows only the public key id.

```python
from datamermaid import APIKeyAuth

auth = APIKeyAuth("mmd_abc123.s3cret")
print(auth.key_id)  # 'mmd_abc123'
print(repr(auth))  # APIKeyAuth(key_id='mmd_abc123')
```

!!! tip "Forcing anonymous access"

    A stray `MERMAID_API_KEY` in your environment applies to every client. Pass
    [`AnonymousAuth`][datamermaid.auth.base.AnonymousAuth] to ignore it, which is
    the honest way to check that a route really is public:

    ```python
    from datamermaid import AnonymousAuth, MermaidClient

    with MermaidClient(auth=AnonymousAuth()) as client:
        print(len(client.fish_families.list()))
    ```

## Logging in through the browser

[`datamermaid.login()`][datamermaid.auth.oauth.login] runs an OAuth 2.0 login
against MERMAID's Auth0 tenant and caches the result, so it only has to be done
once per machine:

```python
import datamermaid

datamermaid.login()  # opens your browser

with datamermaid.MermaidClient() as client:  # picks the cached token up
    print(client.me().full_name)
```

The token is sent as `Authorization: Bearer <access_token>` and is refreshed
silently while a refresh token is available. To keep the credentials on a single
client instead of leaning on the cache, build the auth object yourself:

```python
from datamermaid import MermaidClient, OAuth

with MermaidClient(auth=OAuth()) as client:
    print(client.me().full_name)  # the first request triggers the login
```

Log out by discarding the cached token:

```python
import datamermaid

datamermaid.logout()  # True if a token was found and removed
```

## Flows

[`OAuth(flow=...)`][datamermaid.auth.oauth.OAuth] and
`datamermaid.login(flow=...)` accept:

| Flow | What happens |
| --- | --- |
| `"auto"` (default) | `pkce` on a desktop; `device` (falling back to `manual`) when the terminal looks headless |
| `"pkce"` | Authorization code with PKCE. Starts a one-shot HTTP server on an ephemeral loopback port, opens your browser, exchanges the code for tokens. |
| `"device"` | Device authorization grant (RFC 8628). Prints a short code and a URL to open on any other device, then polls until you approve. Nothing is opened locally. |
| `"implicit"` | Legacy implicit grant, kept for parity with the older MERMAID clients. The token arrives in the redirect fragment and there is no refresh token. |
| `"manual"` | No server at all: the authorization URL is printed and you paste the redirect URL (or just the code) back into the terminal. |

A session is treated as headless when `SSH_CONNECTION` or `SSH_TTY` is set, or
on Unix when neither `DISPLAY` nor `WAYLAND_DISPLAY` is; see
[`is_headless()`][datamermaid.auth.oauth.is_headless].

### On a remote terminal

Over SSH there is no browser to open locally, and the loopback redirect the PKCE
flow relies on would land on the wrong machine. `auto` handles this: it detects
the SSH session and runs the device grant.

```console
$ ssh reef-server
$ python -c "import datamermaid; datamermaid.login()"
To sign in to MERMAID, open this URL on any device:

    https://datamermaid.auth0.com/activate

and enter the code: WDJB-MJHT

Or open https://datamermaid.auth0.com/activate?user_code=WDJB-MJHT to skip entering it.
```

Open that URL on your laptop, type the code, and the remote terminal picks the
token up and caches it. Force the flow rather than relying on detection with:

```python
import datamermaid

datamermaid.login(flow="device")
```

If the Auth0 tenant has the device grant disabled, `auto` falls back to the
manual paste flow, which prints an authorization URL instead. Open it on your
laptop; the browser is redirected to a `http://localhost:<port>/` page that
cannot load, and its address bar holds the code. Paste that whole URL (or just
the `code=` value) back into the terminal.

```python
import datamermaid

datamermaid.login(flow="manual")
```

### Callback URL mismatches

The `pkce` and `implicit` flows redirect to `http://localhost:<ephemeral port>/`.
Auth0 matches Allowed Callback URLs literally, so a tenant that has not
registered port-agnostic loopback URLs answers with *Callback URL mismatch*. Pin
the redirect to what the application allows:

```python
import datamermaid

# `mermaidr`, the R client, registers port 1410.
datamermaid.login(flow="pkce", redirect_port=1410)
```

The socket itself always listens on `127.0.0.1` only. Use
`redirect_host="127.0.0.1"` if the registered URL uses the literal address RFC
8252 recommends rather than the `localhost` name. Neither setting matters for
the `device` flow, which has no redirect at all.

## The token cache

Tokens live in `$XDG_CONFIG_HOME/datamermaid/tokens.json`, defaulting to
`~/.config/datamermaid/tokens.json`, written with mode `0600`. One entry is kept
per tenant/client/audience, so the production and development Auth0 tenants can
be logged in to side by side.

```python
from datamermaid import TokenCache
from datamermaid.auth import default_cache_path

print(default_cache_path())  # ~/.config/datamermaid/tokens.json
print(TokenCache().path)
```

Expiry is read from the access token's `exp` claim, decoded but never verified
locally, with a 60 second margin. An expired token is refreshed silently and
only a failed refresh prompts a new login. The file is written by rename, so an
interrupted save never truncates the tokens of the tenants it was not touching.

Keep tokens in memory only, or move the file:

```python
from datamermaid import OAuth

OAuth(cache=False)  # nothing is written to disk
OAuth(cache="/run/secrets/mermaid-tokens.json")
```

Inspect what is cached without triggering a login:

```python
from datamermaid import OAuth

oauth = OAuth(interactive=False)
tokens = oauth.tokens  # None if nothing is cached
if tokens is not None:
    print(tokens.is_expired(), tokens.refresh_token is not None)
```

`interactive=False` makes the object raise
[`AuthFlowError`][datamermaid.exceptions.AuthFlowError] instead of prompting,
which is what you want in a cron job or a container:

```python
from datamermaid import AuthFlowError, MermaidClient, OAuth

try:
    with MermaidClient(auth=OAuth(interactive=False)) as client:
        client.me()
except AuthFlowError as exc:
    print(f"no usable cached token: {exc}")
```

## Auth0 configuration

The defaults point at MERMAID's production tenant and match what the R client,
[`mermaidr`](https://github.com/data-mermaid/mermaidr), uses.

| Argument | Environment variable | Default |
| --- | --- | --- |
| `domain` | `MERMAID_AUTH0_DOMAIN` | `datamermaid.auth0.com` |
| `client_id` | `MERMAID_CLIENT_ID` | `6q1XvYG0n75ZaLbFko0gUV4xGud4uPyG` |
| `audience` | `MERMAID_AUDIENCE` | `https://api.datamermaid.org` |
| `scope` | | `openid profile email offline_access` |

Keyword arguments win over the environment, which wins over the defaults, as
resolved by [`Auth0Config.resolve()`][datamermaid.auth.config.Auth0Config.resolve].
`offline_access` is the scope that makes a refresh token available.

Point the whole stack at the development instance:

```python
import datamermaid
from datamermaid import DEV_BASE_URL, MermaidClient

auth = datamermaid.login(domain="dev-datamermaid.auth0.com")
with MermaidClient(auth=auth, base_url=DEV_BASE_URL) as client:
    print(client.me().full_name)
```

## Errors

Two failure modes are deliberately distinct:

- [`AuthenticationError`][datamermaid.exceptions.AuthenticationError] is the API
  rejecting credentials that were sent (HTTP 401 or 403).
- [`AuthFlowError`][datamermaid.exceptions.AuthFlowError] is an interactive login
  that could not be completed, with
  [`AuthTimeoutError`][datamermaid.exceptions.AuthTimeoutError] for the case
  where you simply never finished it.

```python
from datamermaid import AuthenticationError, AuthFlowError, MermaidClient

try:
    with MermaidClient() as client:
        print(client.me().full_name)
except AuthenticationError:
    print("the key or token was rejected; create a new API key")
except AuthFlowError as exc:
    print(f"login could not be completed: {exc}")
```

## Writing your own credential provider

The client never inspects credentials directly; it only asks an
[`Auth`][datamermaid.auth.base.Auth] instance to stamp an outgoing request. A
provider that reads a key out of a secret manager is one method:

```python
import httpx

from datamermaid import Auth, MermaidClient


class VaultAuth(Auth):
    """Fetch the API key from a secret store on every request."""

    def __init__(self, read_secret):
        self._read_secret = read_secret

    def apply(self, request: httpx.Request) -> None:
        request.headers["Authorization"] = f"Bearer {self._read_secret()}"


with MermaidClient(auth=VaultAuth(lambda: "mmd_abc.def")) as client:
    print(client.me().full_name)
```

Override [`refresh()`][datamermaid.auth.base.Auth.refresh] and
[`should_refresh()`][datamermaid.auth.base.Auth.should_refresh] as well if the
credentials expire; together they buy one automatic retry after a 401 or 403.
