"""The MERMAID API client."""

from __future__ import annotations

import math
import os
import platform
import random
import threading
import time
from functools import cached_property
from types import TracebackType
from typing import TYPE_CHECKING, Any, overload

import httpx

from ._version import __version__
from .auth import Auth, HTTPXAuthAdapter, resolve_auth
from .exceptions import MermaidConnectionError, parse_retry_after, raise_for_status

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import Me
    from .resources.covariates import Covariates
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
    from .resources.zonal_stats import ZonalStats

__all__ = [
    "BASE_URL_ENV_VAR",
    "COVARIATES_URL_ENV_VAR",
    "DEFAULT_BASE_URL",
    "DEFAULT_COVARIATES_URL",
    "DEFAULT_ZONAL_STATS_URL",
    "DEV_BASE_URL",
    "ZONAL_STATS_URL_ENV_VAR",
    "MermaidClient",
]

DEFAULT_BASE_URL = "https://api.datamermaid.org/v1/"
DEV_BASE_URL = "https://dev-api.datamermaid.org/v1/"
BASE_URL_ENV_VAR = "MERMAID_API_URL"

DEFAULT_ZONAL_STATS_URL = "https://api.zonalstats.datamermaid.org/api/v1/zonal-stats/"
ZONAL_STATS_URL_ENV_VAR = "MERMAID_ZONAL_STATS_URL"

DEFAULT_COVARIATES_URL = "https://mermaid.prescient.earth/stac/"
COVARIATES_URL_ENV_VAR = "MERMAID_COVARIATES_URL"

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_FACTOR = 0.5
MAX_BACKOFF = 30.0

#: The only headers a ``public=True`` request carries.  Everything else the
#: client would add, such as an ``Authorization`` header from ``headers=`` or a
#: cookie, is dropped, so no credential reaches a host other than the API.
PUBLIC_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "connection",
        "content-length",
        "content-type",
        "host",
        "user-agent",
    }
)


def default_user_agent() -> str:
    """The ``User-Agent`` sent with every request."""

    return f"datamermaid/{__version__} httpx/{httpx.__version__} python/{platform.python_version()}"


def resolve_base_url(base_url: str | None = None) -> str:
    """Resolve the API root: explicit argument, then env var, then default."""

    candidate = base_url or os.environ.get(BASE_URL_ENV_VAR, "").strip() or DEFAULT_BASE_URL
    return candidate if candidate.endswith("/") else f"{candidate}/"


def resolve_zonal_stats_url(url: str | None = None) -> str:
    """Resolve the Zonal Stats service root: argument, then env var, then default."""

    candidate = (
        url or os.environ.get(ZONAL_STATS_URL_ENV_VAR, "").strip() or DEFAULT_ZONAL_STATS_URL
    )
    return candidate if candidate.endswith("/") else f"{candidate}/"


def resolve_covariates_url(url: str | None = None) -> str:
    """Resolve the covariates STAC API root: argument, then env var, then default."""

    candidate = url or os.environ.get(COVARIATES_URL_ENV_VAR, "").strip() or DEFAULT_COVARIATES_URL
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

    The client is safe to share between threads, which is how a
    [`Batch`][datamermaid.batch.Batch] fans requests out.  Throttling is
    cooperative: a ``429`` seen on any thread sets a client-wide deadline
    (``Retry-After``, or the computed backoff, capped at 30 seconds) that every
    request waits on before it is sent, so the workers back off together instead
    of one at a time.  The deadline is shared by both hosts: a ``429`` from the
    Zonal Stats service also pauses MERMAID API calls on the same client, and the
    reverse.

    Args:
        auth: Credential provider.  Mutually exclusive with ``api_key``.
        api_key: A MERMAID API key, ``mmd_<key_id>.<secret>``.  Defaults to
            ``MERMAID_API_KEY``, then to tokens left by
            [`datamermaid.login`][datamermaid.auth.oauth.login], then to anonymous access.
        base_url: API root.  Defaults to ``MERMAID_API_URL``, then to
            [`DEFAULT_BASE_URL`][datamermaid.client.DEFAULT_BASE_URL].  A trailing slash
            is added if missing.
        zonal_stats_url: Root of the separate Zonal Stats service.  Defaults to
            ``MERMAID_ZONAL_STATS_URL``, then to
            [`DEFAULT_ZONAL_STATS_URL`][datamermaid.client.DEFAULT_ZONAL_STATS_URL].  A
            trailing slash is added if missing.
        covariates_url: Root of the covariates STAC API.  Defaults to
            ``MERMAID_COVARIATES_URL``, then to
            [`DEFAULT_COVARIATES_URL`][datamermaid.client.DEFAULT_COVARIATES_URL].  A
            trailing slash is added if missing.
        timeout: Seconds, or an ``httpx.Timeout`` for per-phase control.
        max_retries: Extra attempts after a 429 or 5xx.  ``0`` disables retrying.
        backoff_factor: Base delay of the exponential backoff, in seconds.
            A ``Retry-After`` header wins over it.
        headers: Extra headers sent with every request to the MERMAID API.
            Requests to the Zonal Stats service and the covariates catalog
            carry none of them.
        user_agent: Overrides [`default_user_agent`][..default_user_agent].
        transport: An ``httpx`` transport, mainly for tests.

    Raises:
        ValueError: If ``max_retries`` is negative, or both ``auth`` and
            ``api_key`` are given.

    Attributes:
        auth: The credential provider in use.
        base_url: The resolved API root, always ending in ``/``.
        zonal_stats_url: The resolved Zonal Stats service root, always ending in ``/``.
        covariates_url: The resolved covariates STAC API root, always ending in ``/``.
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
        zonal_stats_url: str | None = None,
        covariates_url: str | None = None,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        headers: dict[str, str] | None = None,
        user_agent: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if isinstance(max_retries, bool) or not isinstance(max_retries, int):
            raise TypeError("max_retries must be an integer")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")

        if isinstance(backoff_factor, bool) or not isinstance(backoff_factor, (int, float)):
            raise TypeError("backoff_factor must be a number")
        if not math.isfinite(backoff_factor) or backoff_factor < 0:
            raise ValueError("backoff_factor must be finite and >= 0")

        self.auth = resolve_auth(auth, api_key)
        self.base_url = resolve_base_url(base_url)
        self.zonal_stats_url = resolve_zonal_stats_url(zonal_stats_url)
        self.covariates_url = resolve_covariates_url(covariates_url)
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
        # Cooperative throttle: a `time.monotonic()` deadline before which no
        # request may be sent, shared by every thread using this client.
        self._throttle_lock = threading.Lock()
        self._throttled_until = 0.0

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

    def me(self) -> Me:
        """Return the profile that owns the credentials in use (``GET /me/``)."""

        from .resources.me import MeResource

        return MeResource(self).get()

    @cached_property
    def projects(self) -> ProjectsResource:
        """Access to the ``/projects/`` endpoints.

        Call it with a project id (``client.projects(project_id)``) for that
        project's own sites, sample events and survey collections.
        """

        from .resources.projects import ProjectsResource

        return ProjectsResource(self)

    @cached_property
    def sites(self) -> SitesResource:
        """Reef sites, ``/sites/`` (requires authentication)."""

        from .resources.reference import SitesResource

        return SitesResource(self)

    @cached_property
    def managements(self) -> ManagementsResource:
        """Management regimes, ``/managements/`` (requires authentication)."""

        from .resources.reference import ManagementsResource

        return ManagementsResource(self)

    @cached_property
    def project_tags(self) -> ProjectTagsResource:
        """Organisation tags, ``/projecttags/``."""

        from .resources.reference import ProjectTagsResource

        return ProjectTagsResource(self)

    @cached_property
    def fish_sizes(self) -> FishSizesResource:
        """Fish size bins, ``/fishsizes/``."""

        from .resources.reference import FishSizesResource

        return FishSizesResource(self)

    @cached_property
    def fish_families(self) -> FishFamiliesResource:
        """Fish families, ``/fishfamilies/``."""

        from .resources.reference import FishFamiliesResource

        return FishFamiliesResource(self)

    @cached_property
    def fish_genera(self) -> FishGeneraResource:
        """Fish genera, ``/fishgenera/``."""

        from .resources.reference import FishGeneraResource

        return FishGeneraResource(self)

    @cached_property
    def fish_species(self) -> FishSpeciesResource:
        """Fish species, ``/fishspecies/``."""

        from .resources.reference import FishSpeciesResource

        return FishSpeciesResource(self)

    @cached_property
    def benthic_attributes(self) -> BenthicAttributesResource:
        """Benthic attributes, ``/benthicattributes/``."""

        from .resources.reference import BenthicAttributesResource

        return BenthicAttributesResource(self)

    @cached_property
    def invert_attributes(self) -> InvertAttributesResource:
        """Macroinvertebrate attributes, ``/invertattributes/``."""

        from .resources.reference import InvertAttributesResource

        return InvertAttributesResource(self)

    @cached_property
    def invert_species(self) -> InvertSpeciesResource:
        """Macroinvertebrate species, ``/invertspecies/``."""

        from .resources.reference import InvertSpeciesResource

        return InvertSpeciesResource(self)

    @cached_property
    def summary_sample_events(self) -> SummarySampleEventsResource:
        """Public sample event summaries, ``/summarysampleevents/``."""

        from .resources.reference import SummarySampleEventsResource

        return SummarySampleEventsResource(self)

    @cached_property
    def label_mappings(self) -> LabelMappingsResource:
        """Classifier label mappings, ``/classification/labelmappings/``."""

        from .resources.reference import LabelMappingsResource

        return LabelMappingsResource(self)

    @cached_property
    def zonal_stats(self) -> ZonalStats:
        """The Zonal Stats service, a separate public host (``client.zonal_stats.raster``).

        Requests to it carry no MERMAID credentials.  See
        [`ZonalStats`][datamermaid.resources.zonal_stats.ZonalStats].
        """

        from .resources.zonal_stats import ZonalStats

        return ZonalStats(self)

    @cached_property
    def covariates(self) -> Covariates:
        """The covariates catalog, a public STAC API (``client.covariates.collections()``).

        Requests to it carry no MERMAID credentials.  See
        [`Covariates`][datamermaid.resources.covariates.Covariates].
        """

        from .resources.covariates import Covariates

        return Covariates(self)

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
            >>> client.choices()["reeftypes"]  # doctest: +SKIP
            [{'id': '...', 'name': 'atoll', 'updated_on': '...'}, ...]
            ```
        """

        from .resources.choices import ChoicesResource

        resource = ChoicesResource(self)
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

    def _wait_if_throttled(self) -> None:
        """Sleep until the client-wide throttle deadline has passed, if it has not.

        Another thread may push the deadline back while this one sleeps, so the
        deadline is read again after every sleep.  The lock is never held while
        sleeping.
        """

        while True:
            with self._throttle_lock:
                remaining = self._throttled_until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(remaining)

    def _note_throttle(self, delay: float) -> None:
        """Record that the service asked for ``delay`` seconds of quiet.

        Every request on every thread waits for the deadline before sending.  A
        later deadline wins over an earlier one; a shorter one never brings it
        forward.
        """

        with self._throttle_lock:
            self._throttled_until = max(self._throttled_until, time.monotonic() + delay)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        public: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue a request, retrying transient failures and mapping errors.

        ``url`` may be relative to the base URL or absolute (as in the ``next``
        link of a paginated response).  ``public=True`` is for another host: the
        request is sent without the client's auth, and with only the headers in
        [`PUBLIC_HEADERS`][datamermaid.client.PUBLIC_HEADERS].
        """

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_if_throttled()
            response: httpx.Response | None = None
            try:
                if public:
                    response = self._send_public(method, url, params=params, json=json, **kwargs)
                else:
                    response = self._http.request(method, url, params=params, json=json, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise MermaidConnectionError(f"{method} {url} failed: {exc}") from exc
            else:
                if not (self._should_retry(response.status_code) and attempt < self.max_retries):
                    raise_for_status(response)
                    return response

            delay = self._retry_delay(attempt, response)
            if response is not None and response.status_code == 429:
                self._note_throttle(delay)
            time.sleep(delay)

        # Unreachable: the final attempt either returns or raises above.
        raise MermaidConnectionError(f"{method} {url} failed") from last_error

    def _send_public(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        request = self._http.build_request(method, url, **kwargs)
        for name in list(request.headers):
            if name.lower() not in PUBLIC_HEADERS:
                del request.headers[name]
        return self._http.send(request, auth=None)

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        public: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Issue a request and decode the JSON body (``None`` when empty)."""

        response = self.request(method, url, params=params, json=json, public=public, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise MermaidConnectionError(
                f"{method} {url} returned a non-JSON body: {response.text[:200]!r}"
            ) from exc
