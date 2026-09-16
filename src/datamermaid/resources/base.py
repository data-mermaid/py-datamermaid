"""Shared plumbing for endpoint wrappers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar
from urllib.parse import parse_qsl, quote, urlsplit

from ..models import APIModel
from ..pagination import Page, PaginatedList

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..client import MermaidClient

__all__ = ["Resource"]

M = TypeVar("M", bound=APIModel)


class Resource(Generic[M]):
    """Base class for endpoint wrappers.

    Subclasses set :attr:`path` and :attr:`model` and expose whatever public
    methods make sense for the endpoint, built on :meth:`_list` and
    :meth:`_get`.
    """

    #: Endpoint path relative to the API root, e.g. ``"projects/"``.
    path: str
    #: Model the endpoint's records are parsed into.
    model: type[M]

    def __init__(self, client: MermaidClient) -> None:
        self._client = client

    def __repr__(self) -> str:
        return f"{type(self).__name__}(path={self.path!r})"

    def _url(self, *parts: str) -> str:
        """Build an endpoint URL, percent-encoding each path segment.

        Encoding keeps a stray ``/``, ``?`` or ``..`` in an id from redirecting
        the request to a different endpoint.
        """

        suffix = "".join(quote(part.strip("/"), safe="") + "/" for part in parts)
        return f"{self.path}{suffix}"

    def _parse(self, data: Any) -> M:
        return self.model.from_api(data)

    def _page(self, data: Any) -> Page[M]:
        """Turn a DRF list response (or a bare JSON array) into a :class:`Page`."""

        if isinstance(data, list):
            return Page(items=[self._parse(item) for item in data], count=len(data))
        if not isinstance(data, dict):
            raise TypeError(f"expected a list response, got {type(data).__name__}")
        return Page(
            items=[self._parse(item) for item in data.get("results") or []],
            next_url=data.get("next"),
            count=data.get("count"),
        )

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
                data = self._client.request_json("GET", first_url, params=query or None)
            else:
                # `next` already carries the pagination and filter parameters.
                page_url, page_params = self._next_request(first_url, next_url)
                data = self._client.request_json("GET", page_url, params=page_params)
            return self._page(data)

        return PaginatedList(fetch)

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> M:
        """Fetch and parse a single record."""

        data = self._client.request_json("GET", url, params=params or None)
        return self._parse(data)
