"""Lazy collections over Django REST Framework style list endpoints."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar, overload

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas

__all__ = ["Page", "PaginatedList", "to_dataframe"]

T = TypeVar("T")

PANDAS_INSTALL_HINT = (
    "pandas is required for .to_df(); install it with "
    "`uv add 'datamermaid[pandas]'` or `pip install 'datamermaid[pandas]'`"
)


@dataclass(frozen=True)
class Page(Generic[T]):
    """One page of results from a list endpoint."""

    items: list[T] = field(default_factory=list)
    next_url: str | None = None
    count: int | None = None


class PaginatedList(Generic[T]):
    """An iterable view over a paginated endpoint that fetches pages on demand.

    Nothing is requested until the list is iterated, indexed, or measured.
    Fetched items are cached, so re-iterating or indexing backwards never issues
    another request.
    """

    def __init__(self, fetch_page: Callable[[str | None], Page[T]]) -> None:
        self._fetch_page = fetch_page
        self._items: list[T] = []
        self._next_url: str | None = None
        self._count: int | None = None
        self._started = False
        self._exhausted = False

    # -- fetching ---------------------------------------------------------

    def _fetch_one(self) -> None:
        page = self._fetch_page(None if not self._started else self._next_url)
        self._started = True
        if page.count is not None:
            self._count = page.count
        self._items.extend(page.items)
        self._next_url = page.next_url
        if page.next_url is None:
            self._exhausted = True

    def _advance(self) -> bool:
        """Fetch pages until at least one new item is cached.

        Returns ``False`` once the endpoint has no more items.
        """

        before = len(self._items)
        while not self._exhausted and len(self._items) == before:
            self._fetch_one()
        return len(self._items) > before

    def _fetch_all(self) -> None:
        while self._advance():
            pass

    def _ensure(self, size: int) -> None:
        """Cache at least ``size`` items, if the endpoint has that many."""

        while len(self._items) < size and self._advance():
            pass

    # -- introspection ----------------------------------------------------

    @property
    def count(self) -> int | None:
        """Total number of matching records, as reported by the API."""

        if self._count is None and not self._started:
            self._advance()
        return self._count

    @property
    def fetched(self) -> list[T]:
        """The items fetched so far, without triggering a request."""

        return list(self._items)

    def __len__(self) -> int:
        count = self.count
        if count is not None:
            return count
        self._fetch_all()
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        index = 0
        while True:
            while index >= len(self._items):
                if not self._advance():
                    return
            yield self._items[index]
            index += 1

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> list[T]: ...

    def __getitem__(self, index: int | slice) -> T | list[T]:
        if isinstance(index, slice):
            stop = index.stop
            step = index.step
            if (
                stop is None
                or stop < 0
                or (index.start is not None and index.start < 0)
                or (step is not None and step < 0)
            ):
                self._fetch_all()
            else:
                self._ensure(stop)
            return self._items[index]

        if index < 0:
            self._fetch_all()
        else:
            self._ensure(index + 1)
        try:
            return self._items[index]
        except IndexError:
            raise IndexError("PaginatedList index out of range") from None

    def __repr__(self) -> str:
        state = "exhausted" if self._exhausted else "lazy"
        return f"<PaginatedList fetched={len(self._items)} count={self._count} {state}>"

    # -- export -----------------------------------------------------------

    def to_df(self, **kwargs: Any) -> pandas.DataFrame:
        """Materialise every page and return the results as a DataFrame.

        Requires the optional ``pandas`` extra.
        """

        return to_dataframe(list(self), **kwargs)


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - exercised with pandas absent
        raise ImportError(PANDAS_INSTALL_HINT) from exc
    return pd


def _as_record(item: Any) -> Any:
    to_dict = getattr(item, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return item


def to_dataframe(items: list[Any], **kwargs: Any) -> pandas.DataFrame:
    """Convert models (or plain dicts) into a ``pandas.DataFrame``."""

    pd = _require_pandas()
    records = [_as_record(item) for item in items]
    frame = pd.DataFrame.from_records(records, **kwargs)
    return frame
