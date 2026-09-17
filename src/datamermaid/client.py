"""The MERMAID API client."""

from __future__ import annotations

import os
import platform
import random
import time
from types import TracebackType
from typing import TYPE_CHECKING, Any, TypeVar, cast, overload

import httpx

from ._version import __version__
from .auth import Auth, HTTPXAuthAdapter, resolve_auth
from .exceptions import MermaidConnectionError, parse_retry_after, raise_for_status

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import Me
    from .resources.base import BaseResource
    from .resources.projects import ProjectsResource
    from .resources.reference import (
        BenthicAttributesResource,
        FishFamiliesResource,
        FishGeneraResource,
        FishSizesResource,
        FishSpeciesResource,
        InvertAttributesResource,
        InvertSpeciesResource,
        LabelMappingsResource,
        ManagementsResource,
        ProjectTagsResource,
        SitesResource,
        SummarySampleEventsResource,
    )

__all__ = ["BASE_URL_ENV_VAR", "DEFAULT_BASE_URL", "DEV_BASE_URL", "MermaidClient"]

DEFAULT_BASE_URL = "https://api.datamermaid.org/v1/"
DEV_BASE_URL = "https://dev-api.datamermaid.org/v1/"
BASE_URL_ENV_VAR = "MERMAID_API_URL"

#: A resource wrapper cached on the client, for `MermaidClient._resource`.
R = TypeVar("R", bound="BaseResource")

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

    Owns the base URL, the timeouts, the ``User-Agent``, the retry-with-backoff
    policy and the JSON decoding.  Credentials are never inspected here: the
    client only ever asks an [`Auth`][datamermaid.auth.base.Auth] to stamp an
    outgoing request.

    Each endpoint group is a property returning a wrapper whose ``list()`` gives
    a lazy [`PaginatedList`][datamermaid.pagination.PaginatedList].  Reference data needs
    no credentials; ``/me/``, ``/projects/``, ``/sites/`` and ``/managements/``
    do.

    Use it as a context manager so the connection pool is closed.

    Args:
        auth: Credential provider.  Mutually exclusive with ``api_key``.
        api_key: A MERMAID API key, ``mmd_<key_id>.<secret>``.  Defaults to
            ``MERMAID_API_KEY``, then to tokens left by
            [`datamermaid.login`][datamermaid.auth.oauth.login], then to anonymous access.
        base_url: API root.  Defaults to ``MERMAID_API_URL``, then to
            [`DEFAULT_BASE_URL`][datamermaid.client.DEFAULT_BASE_URL].  A trailing slash
            is added if missing.
        timeout: Seconds, or an ``httpx.Timeout`` for per-phase control.
        max_retries: Extra attempts after a 429 or 5xx.  ``0`` disables retrying.
        backoff_factor: Base delay of the exponential backoff, in seconds.
            A ``Retry-After`` header wins over it.
        headers: Extra headers sent with every request.
        user_agent: Overrides [`..default_user_agent`][].
        transport: An ``httpx`` transport, mainly for tests.

    Raises:
        ValueError: If ``max_retries`` is negative, or both ``auth`` and
            ``api_key`` are given.

    Attributes:
        auth: The credential provider in use.
        base_url: The resolved API root, always ending in ``/``.
        max_retries: Extra attempts made after a retryable status code.
        backoff_factor: Base delay of the exponential backoff, in seconds.

    Example:
        ```python
        from datamermaid import MermaidClient

        with MermaidClient(api_key="mmd_abc.def") as client:
            me = client.me()
            for project in client.projects.list():
                print(project.name)
        ```

        Reference data is public, so it needs no credentials at all:

        ```python
        with MermaidClient() as client:
            species = client.fish_species.list(genus=genus_id)
            reef_types = client.choices("reeftypes")
        ```
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
        self._resources: dict[type[Any], Any] = {}

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

    def _resource(self, resource_class: type[R]) -> R:
        """Return the client's single instance of ``resource_class``."""

        resource = self._resources.get(resource_class)
        if resource is None:
            resource = resource_class(self)
            self._resources[resource_class] = resource
        return cast("R", resource)

    def me(self) -> Me:
        """Return the profile that owns the credentials in use (``GET /me/``)."""

        from .resources.me import MeResource

        return self._resource(MeResource).get()

    @property
    def projects(self) -> ProjectsResource:
        """Access to the ``/projects/`` endpoints.

        Call it with a project id (``client.projects(project_id)``) for that
        project's own sites, sample events and survey collections.
        """

        from .resources.projects import ProjectsResource

        return self._resource(ProjectsResource)

    @property
    def sites(self) -> SitesResource:
        """Reef sites, ``/sites/`` (requires authentication)."""

        from .resources.reference import SitesResource

        return self._resource(SitesResource)

    @property
    def managements(self) -> ManagementsResource:
        """Management regimes, ``/managements/`` (requires authentication)."""

        from .resources.reference import ManagementsResource

        return self._resource(ManagementsResource)

    @property
    def project_tags(self) -> ProjectTagsResource:
        """Organisation tags, ``/projecttags/``."""

        from .resources.reference import ProjectTagsResource

        return self._resource(ProjectTagsResource)

    @property
    def fish_sizes(self) -> FishSizesResource:
        """Fish size bins, ``/fishsizes/``."""

        from .resources.reference import FishSizesResource

        return self._resource(FishSizesResource)

    @property
    def fish_families(self) -> FishFamiliesResource:
        """Fish families, ``/fishfamilies/``."""

        from .resources.reference import FishFamiliesResource

        return self._resource(FishFamiliesResource)

    @property
    def fish_genera(self) -> FishGeneraResource:
        """Fish genera, ``/fishgenera/``."""

        from .resources.reference import FishGeneraResource

        return self._resource(FishGeneraResource)

    @property
    def fish_species(self) -> FishSpeciesResource:
        """Fish species, ``/fishspecies/``."""

        from .resources.reference import FishSpeciesResource

        return self._resource(FishSpeciesResource)

    @property
    def benthic_attributes(self) -> BenthicAttributesResource:
        """Benthic attributes, ``/benthicattributes/``."""

        from .resources.reference import BenthicAttributesResource

        return self._resource(BenthicAttributesResource)

    @property
    def invert_attributes(self) -> InvertAttributesResource:
        """Macroinvertebrate attributes, ``/invertattributes/``."""

        from .resources.reference import InvertAttributesResource

        return self._resource(InvertAttributesResource)

    @property
    def invert_species(self) -> InvertSpeciesResource:
        """Macroinvertebrate species, ``/invertspecies/``."""

        from .resources.reference import InvertSpeciesResource

        return self._resource(InvertSpeciesResource)

    @property
    def summary_sample_events(self) -> SummarySampleEventsResource:
        """Public sample event summaries, ``/summarysampleevents/``."""

        from .resources.reference import SummarySampleEventsResource

        return self._resource(SummarySampleEventsResource)

    @property
    def label_mappings(self) -> LabelMappingsResource:
        """Classifier label mappings, ``/classification/labelmappings/``."""

        from .resources.reference import LabelMappingsResource

        return self._resource(LabelMappingsResource)

    @overload
    def choices(self) -> dict[str, list[dict[str, Any]]]: ...

    @overload
    def choices(self, name: str) -> list[dict[str, Any]]: ...

    def choices(self, name: str | None = None) -> Any:
        """Return the API's controlled vocabularies (``GET /choices/``).

        Without an argument, every choice set keyed by name (``countries``,
        ``reeftypes``, ``managementparties``, ...).  With one, just that set's
        rows, fetched from ``/choices/<name>/``.

        Unlike the list endpoints this one is not paginated, so it returns
        plain dictionaries rather than a ``PaginatedList`` of models.

        Args:
            name: A single choice set to fetch.  Omit it for all of them.

        Returns:
            Every choice set keyed by name, or one set's rows.

        Example:
            ```pycon
            >>> client.choices()["reeftypes"]
            [{'id': '...', 'name': 'atoll', 'updated_on': '...'}, ...]
            ```
        """

        from .resources.choices import ChoicesResource

        resource = self._resource(ChoicesResource)
        if name is None:
            return resource.fetch()
        return resource.get(name)

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
