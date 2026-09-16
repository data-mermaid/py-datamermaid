"""Authentication strategies for :class:`~datamermaid.client.MermaidClient`.

The client never inspects credentials directly: it only ever asks an
:class:`Auth` instance to stamp an outgoing request.  That seam is what lets
OAuth (device code, client credentials, ...) be added later as another
:class:`Auth` subclass without changing the client.
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

    Subclasses implement :meth:`apply`.  Implementations that hold expiring
    credentials additionally override :meth:`refresh` and
    :meth:`should_refresh`, which together give them a single retry after an
    authentication failure.
    """

    @abstractmethod
    def apply(self, request: httpx.Request) -> None:
        """Add credentials to ``request`` in place."""

    def refresh(self) -> None:
        """Obtain fresh credentials.  No-op for static credentials."""

        return None

    def should_refresh(self, response: httpx.Response) -> bool:
        """Whether ``response`` warrants a :meth:`refresh` and one retry.

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
    """Bridge an :class:`Auth` into the ``httpx`` authentication protocol."""

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
) -> Auth:
    """Pick the credential provider to use.

    Precedence: explicit ``auth``, then ``api_key``, then the
    ``MERMAID_API_KEY`` environment variable, then anonymous access.
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
    return AnonymousAuth()
