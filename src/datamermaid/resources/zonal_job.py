"""Prepared Cartesian workloads without materializing site-source pairs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias, overload
from urllib.parse import urljoin, urlparse

from ..batch import DEFAULT_MAX_WORKERS, Batch, BatchFailure, BatchStream
from ..models import ZonalStatsResult, _stac_columns

logger = logging.getLogger(__name__)


class StacItemLike(Protocol):
    """A STAC item exposing its dictionary representation."""

    def to_dict(self) -> Mapping[str, Any]: ...


class StacSearchLike(Protocol):
    """A search that yields STAC item dictionaries."""

    def items_as_dicts(self) -> Iterable[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class ZonalSource:
    """A resolved URL and optional STAC provenance."""

    url: str
    stac: Mapping[str, Any] | None = None


SourceInput: TypeAlias = str | Mapping[str, Any] | StacItemLike | ZonalSource


@dataclass(frozen=True)
class ZonalTask:
    """One AOI-source pair, also retained on streamed failures."""

    aoi: Any
    label: Any
    source: ZonalSource


def default_asset(assets: Mapping[str, Any]) -> str | None:
    """The key of the first asset with the ``data`` role, else the first key.

    Many items list a thumbnail before their data, so taking the first key
    alone could compute statistics over a PNG.
    """
    for key, entry in assets.items():
        roles = entry.get("roles") if isinstance(entry, Mapping) else None
        if isinstance(roles, Sequence) and not isinstance(roles, str) and "data" in roles:
            return str(key)
    return next(iter(assets), None)


def source_from_item(item: Any, asset: str | None) -> ZonalSource:
    """Resolve one STAC Item (mapping or ``to_dict()`` object) to its asset URL."""
    if not isinstance(item, Mapping):
        to_dict = getattr(item, "to_dict", None)
        if not callable(to_dict):
            raise TypeError("each source must be a URL, STAC Item, or item mapping")
        item = to_dict()
    if not isinstance(item, Mapping):
        raise TypeError("a STAC Item's to_dict() must return a mapping")
    assets = item.get("assets", {})
    if not isinstance(assets, Mapping):
        raise TypeError("STAC assets must be a mapping")
    key = asset if asset is not None else default_asset(assets)
    if key is None or key not in assets:
        raise ValueError(f"STAC item {item.get('id')!r} has no asset {key!r}")
    if not isinstance(assets[key], Mapping):
        raise TypeError("STAC asset entries must be mappings")
    href = assets[key].get("href")
    if not isinstance(href, str) or not href.strip():
        raise ValueError(f"STAC item {item.get('id')!r} has no asset href")
    links = item.get("links", [])
    if isinstance(links, (str, bytes)) or not isinstance(links, Sequence):
        raise TypeError("STAC links must be a sequence of mappings")
    base = ""
    for link in links:
        if not isinstance(link, Mapping):
            raise TypeError("STAC links must contain mappings")
        if link.get("rel") == "self":
            self_href = link.get("href")
            if not isinstance(self_href, str) or not self_href.strip():
                raise ValueError("STAC self links must have a non-empty href")
            base = self_href
            break
    href = urljoin(base, href)
    if not urlparse(href).scheme:
        raise ValueError("Relative asset URLs require an absolute STAC self link")
    properties = item.get("properties", {})
    if not isinstance(properties, Mapping):
        raise TypeError("STAC properties must be a mapping")
    return ZonalSource(
        href,
        {
            "item_id": item.get("id"),
            "collection": item.get("collection"),
            "datetime": properties.get("datetime"),
            "start_datetime": properties.get("start_datetime"),
            "end_datetime": properties.get("end_datetime"),
            "asset": key,
        },
    )


def resolve_sources(
    url: str | None, sources: Any, search: Any, asset: str | None
) -> tuple[ZonalSource, ...]:
    if sum(value is not None for value in (url, sources, search)) != 1:
        raise ValueError("Supply exactly one of url, sources, or search")
    if url is not None:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")
        return (ZonalSource(url),)
    if search is not None:
        items = getattr(search, "items_as_dicts", None)
        if not callable(items):
            raise TypeError("search must provide an items_as_dicts() method")
        sources = items()
    if isinstance(sources, (str, bytes, Mapping)) or not isinstance(sources, Iterable):
        raise TypeError("sources must be an iterable of URLs or STAC Items")
    resolved = []
    for item in sources:
        if isinstance(item, ZonalSource):
            resolved.append(item)
            continue
        if isinstance(item, str):
            if not item.strip():
                raise ValueError("source URL must not be empty")
            resolved.append(ZonalSource(item))
            continue
        resolved.append(source_from_item(item, asset))
    return tuple(resolved)


def _task_label(task: ZonalTask) -> Any:
    return task.label


def _task_context(task: ZonalTask) -> dict[str, Any]:
    return {"source": task.source.url, **_stac_columns(task.source.stac)}


class ZonalJob:
    """Resolved AOIs and sources; pairs are generated only as workers need them.

    Constructed by an endpoint with resolved sources and a bound computation;
    jobs only generate pairs and execute them.

    Preparing fetches search results but sends no statistics requests. Running
    again repeats calculations; this is not a durable checkpoint or resume store.
    """

    def __init__(
        self,
        aois: list[Any],
        labels: list[Any],
        sources: tuple[ZonalSource, ...],
        compute: Callable[[ZonalTask], ZonalStatsResult],
    ) -> None:
        self._aois = tuple(aois)
        self._labels = tuple(labels)
        self.sources = sources
        self._compute = compute

    @property
    def request_count(self) -> int:
        """Exact number of AOI-source pairs (before retries)."""
        return len(self._aois) * len(self.sources)

    def _tasks(self) -> Iterator[ZonalTask]:
        for aoi, label in zip(self._aois, self._labels, strict=True):
            for source in self.sources:
                yield ZonalTask(aoi, label, source)

    @overload
    def run(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise"] = "raise",
        stream: Literal[False] = False,
    ) -> Batch[ZonalTask, ZonalStatsResult]: ...

    @overload
    def run(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise"] = "raise",
        stream: Literal[True],
    ) -> BatchStream[ZonalTask, ZonalStatsResult]: ...

    @overload
    def run(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise"] = "raise",
        stream: bool = False,
    ) -> Batch[ZonalTask, ZonalStatsResult] | BatchStream[ZonalTask, ZonalStatsResult]: ...

    @overload
    def run(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        stream: Literal[False] = False,
    ) -> Batch[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]: ...

    @overload
    def run(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        stream: Literal[True],
    ) -> BatchStream[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]: ...

    @overload
    def run(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        stream: bool = False,
    ) -> (
        Batch[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
        | BatchStream[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
    ): ...

    def run(
        self,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        stream: bool = False,
    ) -> (
        Batch[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
        | BatchStream[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
    ):
        """Execute eagerly, or stream with bounded memory using stream=True."""
        logger.info(
            "running %d zonal stats requests (%d AOIs x %d sources) on %d workers",
            self.request_count,
            len(self._aois),
            len(self.sources),
            max_workers,
        )
        if stream:
            return BatchStream(
                self._tasks(),
                self._compute,
                max_workers=max_workers,
                errors=errors,
                label=_task_label,
                error_context=_task_context,
            )

        return Batch(
            self._tasks(),
            self._compute,
            max_workers=max_workers,
            errors=errors,
            label=_task_label,
            error_context=_task_context,
        )
