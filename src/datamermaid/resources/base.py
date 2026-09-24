"""Shared plumbing for endpoint wrappers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, TypeVar
from urllib.parse import parse_qsl, quote, urlsplit

from ..exceptions import MermaidConnectionError
from ..models import APIModel
from ..pagination import Page, PaginatedList

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..client import MermaidClient

__all__ = ["BaseResource", "ReadOnlyResource", "Resource", "project_path"]

M = TypeVar("M", bound=APIModel)
T = TypeVar("T")


def _normalize_id(value: str | None, *, name: str = "record id") -> str:
    if value is None:
        raise ValueError(f"a {name} is required")
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip().strip("/").strip()
    if not normalized:
        raise ValueError(f"a {name} is required")
    if normalized in (".", ".."):
        raise ValueError(f"{name} must not be a dot path segment")
    return normalized


def project_path(project_id: str, route: str) -> str:
    """Compose ``projects/<project_id>/<route>``.

    The id is percent-encoded, so a stray ``/`` or ``..`` in it cannot send the
    request to a different endpoint.
    """

    return f"projects/{quote(_normalize_id(project_id, name='project id'), safe='')}/{route}"


class BaseResource:
    """Anything reachable through the client: a path and a way to request it.

    Endpoints whose responses are not lists of models (``/choices/``) extend
    this directly; everything else extends [`Resource`][datamermaid.resources.base.Resource].
    """

    #: Endpoint path relative to the API root, e.g. ``"projects/"``.
    path: str

    def __init__(self, client: MermaidClient) -> None:
        self._client = client

    def __repr__(self) -> str:
        return f"{type(self).__name__}(path={self.path!r})"

    def _url(self, *parts: str) -> str:
        """Build an endpoint URL, percent-encoding each path segment.

        Encoding keeps a stray ``/``, ``?`` or ``..`` in an id from redirecting
        the request to a different endpoint.
        """

        suffix = "".join(quote(_normalize_id(part), safe="") + "/" for part in parts)
        return f"{self.path}{suffix}"

    def _decode_response(self, data: Any, parser: Callable[[Any], T], url: str) -> T:
        """Translate invalid server payloads without intercepting request/input errors."""
        try:
            return parser(data)
        except (TypeError, ValueError) as exc:
            raise MermaidConnectionError(f"GET {url} returned an invalid response: {exc}") from exc


class Resource(BaseResource, Generic[M]):
    """Base class for endpoints returning records of one model.

    Subclasses set [`path`][datamermaid.resources.base.Resource.path] and
    [`model`][datamermaid.resources.base.Resource.model] and expose whatever public
    methods make sense for the endpoint, built on ``_list`` and ``_get``.
    """

    #: Model the endpoint's records are parsed into.
    model: type[M]

    def _parse(self, data: Any) -> M:
        return self.model.from_api(data)

    def _page(self, data: Any) -> Page[M]:
        """Turn a DRF list response (or a bare JSON array) into one page.

        See [`Page`][datamermaid.pagination.Page].
        """

        if isinstance(data, list):
            return Page(items=[self._parse(item) for item in data], count=len(data))
        if not isinstance(data, dict):
            raise TypeError(f"expected a list response, got {type(data).__name__}")
        rows = data.get("results")
        if not isinstance(rows, list):
            raise TypeError("expected a results array in the paginated response")
        next_url = data.get("next")
        if next_url is not None and (not isinstance(next_url, str) or not next_url):
            raise TypeError("next must be a non-empty URL string or null")
        count = data.get("count")
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or count < 0
        ):
            raise TypeError("count must be a non-negative integer or null")
        return Page(items=[self._parse(item) for item in rows], next_url=next_url, count=count)

    def _next_request(self, first_url: str, next_url: str) -> tuple[str, dict[str, Any] | None]:
        """Resolve a ``next`` link into a request the client may safely make.

        A link on the API's own host is followed as-is.  A link pointing
        anywhere else contributes only its query parameters, so credentials are
        never sent to a host the caller did not configure.
        """

        parsed = urlsplit(next_url)
        if not parsed.scheme and not parsed.netloc:
            return next_url, None
        base = urlsplit(self._client.base_url)
        if (parsed.scheme.lower(), parsed.netloc.lower()) == (
            base.scheme.lower(),
            base.netloc.lower(),
        ):
            return next_url, None
        return first_url, dict(parse_qsl(parsed.query)) or None

    def _list(
        self,
        url: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> PaginatedList[M]:
        """Return a lazy list over a paginated endpoint."""

        first_url = self.path if url is None else url
        query = {key: value for key, value in (params or {}).items() if value is not None}

        def fetch(next_url: str | None) -> Page[M]:
            if next_url is None:
                page_url = first_url
                data = self._client.request_json("GET", page_url, params=query or None)
            else:
                # `next` already carries the pagination and filter parameters.
                page_url, page_params = self._next_request(first_url, next_url)
                data = self._client.request_json("GET", page_url, params=page_params)
            return self._decode_response(data, self._page, page_url)

        return PaginatedList(fetch)

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> M:
        """Fetch and parse a single record."""

        data = self._client.request_json("GET", url, params=params or None)
        return self._decode_response(data, self._parse, url)


class ReadOnlyResource(Resource[M]):
    """A read-only endpoint: a paginated list plus a ``/<id>/`` detail route.

    Reference endpoints differ only in their path and model, so they subclass
    this and declare those two attributes.
    """

    def list(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        search: str | None = None,
        ordering: str | None = None,
        fields: str | None = None,
        **filters: Any,
    ) -> PaginatedList[M]:
        """List records, lazily fetching pages as they are consumed.

        Keyword arguments become query parameters, so anything the endpoint
        filters on can be passed straight through, alongside the parameters
        every list route understands: ``limit``, ``search``, ``ordering`` and
        ``fields``.  An argument whose value is ``None`` is left out.
        """

        return self._list(
            params={
                **filters,
                "limit": limit,
                "offset": offset,
                "search": search,
                "ordering": ordering,
                "fields": fields,
            }
        )

    def get(self, record_id: str, **params: Any) -> M:
        """Fetch a single record by id.

        Keyword arguments become query parameters, as for
        [`list`][datamermaid.resources.base.ReadOnlyResource.list].
        """

        query = {key: value for key, value in params.items() if value is not None}
        return self._get(self._url(record_id), params=query)
