"""The [`Auth`][datamermaid.auth.base.Auth] seam and the non-interactive credential providers.

The client never inspects credentials directly: it only ever asks an
[`Auth`][datamermaid.auth.base.Auth] instance to stamp an outgoing request.  The OAuth grants live
in [`datamermaid.auth.oauth`][datamermaid.auth.oauth], which builds on the same seam.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Generator

import httpx

__all__ = [
    "API_KEY_ENV_VAR",
    "APIKeyAuth",
    "AnonymousAuth",
    "Auth",
    "HTTPXAuthAdapter",
    "resolve_auth",
]

API_KEY_ENV_VAR = "MERMAID_API_KEY"


class Auth(ABC):
    """Base class for credential providers.

    Subclasses implement [`apply`][datamermaid.auth.base.Auth.apply].  Implementations
    that hold expiring credentials additionally override
    [`refresh`][datamermaid.auth.base.Auth.refresh] and
    [`.should_refresh`][], which together give
    them a single retry after an authentication failure.
    """

    @abstractmethod
    def apply(self, request: httpx.Request) -> None:
        """Add credentials to ``request`` in place."""

    def refresh(self) -> None:
        """Obtain fresh credentials.  No-op for static credentials."""

        return None

    def should_refresh(self, response: httpx.Response) -> bool:
        """Whether ``response`` warrants a
        [`refresh`][datamermaid.auth.base.Auth.refresh] and one retry.

        The response body has not been read at this point, so implementations
        must decide based on the status code and headers only.
        """

        return False


class AnonymousAuth(Auth):
    """Send requests without credentials (public endpoints only)."""

    def apply(self, request: httpx.Request) -> None:
        return None

    def __repr__(self) -> str:
        return "AnonymousAuth()"


class APIKeyAuth(Auth):
    """Authenticate with a MERMAID API key.

    The key has the form ``mmd_<key_id>.<secret>`` and is sent as
    ``Authorization: Bearer <key>``.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("api_key must be a non-empty string")
        self.api_key = api_key.strip()

    @property
    def key_id(self) -> str | None:
        """The public identifier portion of the key, if it is well formed."""

        prefix, separator, _secret = self.api_key.partition(".")
        return prefix if separator else None

    def apply(self, request: httpx.Request) -> None:
        request.headers["Authorization"] = f"Bearer {self.api_key}"

    def __repr__(self) -> str:
        return f"APIKeyAuth(key_id={self.key_id!r})"


class HTTPXAuthAdapter(httpx.Auth):
    """Bridge an [`Auth`][datamermaid.auth.base.Auth] into the ``httpx`` authentication protocol."""

    def __init__(self, auth: Auth) -> None:
        self.auth = auth

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        self.auth.apply(request)
        response = yield request
        if response.status_code in (401, 403) and self.auth.should_refresh(response):
            self.auth.refresh()
            self.auth.apply(request)
            yield request


def resolve_auth(
    auth: Auth | None = None,
    api_key: str | None = None,
    *,
    env: bool = True,
    cache: bool = True,
) -> Auth:
    """Pick the credential provider to use.

    Precedence: explicit ``auth``, then ``api_key``, then the
    ``MERMAID_API_KEY`` environment variable, then tokens left behind by
    [`datamermaid.login`][datamermaid.auth.oauth.login], then anonymous access.
    """

    if auth is not None:
        if api_key is not None:
            raise ValueError("pass either auth= or api_key=, not both")
        return auth
    if api_key is not None:
        return APIKeyAuth(api_key)
    if env:
        env_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
        if env_key:
            return APIKeyAuth(env_key)
    if cache:
        cached = _cached_oauth(env=env)
        if cached is not None:
            return cached
    return AnonymousAuth()


def _cached_oauth(*, env: bool) -> Auth | None:
    """An [`OAuth`][datamermaid.auth.oauth.OAuth] bound to cached tokens, if any.

    Only a token that can still be used without asking the user anything
    counts: credentials picked up implicitly must never turn an ordinary data
    call into a browser prompt, so the login is left for the caller to run.
    Hence ``interactive=False`` as well, in case the token expires mid-session.

    Imported late: the OAuth machinery pulls in the flows, and nothing here
    needs them until someone has actually logged in.
    """

    from .oauth import OAuth

    oauth = OAuth(env=env, interactive=False)
    tokens = oauth.tokens
    if tokens is None:
        return None
    if tokens.refresh_token is None and tokens.is_expired():
        return None
    return oauth
