"""Shared plumbing for endpoint wrappers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

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
        suffix = "".join(part.strip("/") + "/" for part in parts)
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
                data = self._client.request_json("GET", next_url)
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
