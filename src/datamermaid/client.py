"""The MERMAID API client."""

from __future__ import annotations

import os
import platform
import random
import time
from types import TracebackType
from typing import TYPE_CHECKING, Any

import httpx

from ._version import __version__
from .auth import Auth, HTTPXAuthAdapter, resolve_auth
from .exceptions import MermaidConnectionError, parse_retry_after, raise_for_status

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import Me
    from .resources.projects import ProjectsResource

__all__ = ["BASE_URL_ENV_VAR", "DEFAULT_BASE_URL", "DEV_BASE_URL", "MermaidClient"]

DEFAULT_BASE_URL = "https://api.datamermaid.org/v1/"
DEV_BASE_URL = "https://dev-api.datamermaid.org/v1/"
BASE_URL_ENV_VAR = "MERMAID_API_URL"

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_FACTOR = 0.5
MAX_BACKOFF = 30.0


def default_user_agent() -> str:
    """The ``User-Agent`` sent with every request."""

    return f"datamermaid/{__version__} httpx/{httpx.__version__} python/{platform.python_version()}"


def resolve_base_url(base_url: str | None = None) -> str:
    """Resolve the API root: explicit argument, then env var, then default."""

    candidate = base_url or os.environ.get(BASE_URL_ENV_VAR, "").strip() or DEFAULT_BASE_URL
    return candidate if candidate.endswith("/") else f"{candidate}/"


class MermaidClient:
    """Synchronous client for the MERMAID API.

    Example:
        >>> with MermaidClient(api_key="mmd_abc.def") as client:  # doctest: +SKIP
        ...     me = client.me()
        ...     for project in client.projects.list():
        ...         print(project.name)
    """

    def __init__(
        self,
        *,
        auth: Auth | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        headers: dict[str, str] | None = None,
        user_agent: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")

        self.auth = resolve_auth(auth, api_key)
        self.base_url = resolve_base_url(base_url)
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        default_headers = {
            "Accept": "application/json",
            "User-Agent": user_agent or default_user_agent(),
        }
        if headers:
            default_headers.update(headers)

        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=default_headers,
            auth=HTTPXAuthAdapter(self.auth),
            follow_redirects=True,
            transport=transport,
        )
        self._projects: ProjectsResource | None = None

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection pool."""

        self._http.close()

    def __enter__(self) -> MermaidClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"MermaidClient(base_url={self.base_url!r}, auth={self.auth!r})"

    # -- resources --------------------------------------------------------

    @property
    def projects(self) -> ProjectsResource:
        """Access to the ``/projects/`` endpoints."""

        if self._projects is None:
            from .resources.projects import ProjectsResource

            self._projects = ProjectsResource(self)
        return self._projects

    def me(self) -> Me:
        """Return the profile that owns the credentials in use (``GET /me/``)."""

        from .resources.me import MeResource

        return MeResource(self).get()

    # -- transport --------------------------------------------------------

    def _should_retry(self, status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            if retry_after is not None:
                return min(retry_after, MAX_BACKOFF)
        backoff = self.backoff_factor * (2.0**attempt)
        return min(backoff + random.uniform(0, self.backoff_factor), MAX_BACKOFF)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue a request, retrying transient failures and mapping errors.

        ``url`` may be relative to the base URL or absolute (as in the ``next``
        link of a paginated response).
        """

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            response: httpx.Response | None = None
            try:
                response = self._http.request(method, url, params=params, json=json, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise MermaidConnectionError(f"{method} {url} failed: {exc}") from exc
            else:
                if not (self._should_retry(response.status_code) and attempt < self.max_retries):
                    raise_for_status(response)
                    return response

            time.sleep(self._retry_delay(attempt, response))

        # Unreachable: the final attempt either returns or raises above.
        raise MermaidConnectionError(f"{method} {url} failed") from last_error

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Issue a request and decode the JSON body (``None`` when empty)."""

        response = self.request(method, url, params=params, json=json, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise MermaidConnectionError(
                f"{method} {url} returned a non-JSON body: {response.text[:200]!r}"
            ) from exc
