"""The Zonal Stats service, ``https://api.zonalstats.datamermaid.org/``.

A separate FastAPI service, not a route of the MERMAID API: it lives on its own
host, resolved independently of the client's ``base_url`` (see
[`MermaidClient`][datamermaid.client.MermaidClient]'s ``zonal_stats_url``), takes
**no authentication**, and its routes have no trailing slash.  Each endpoint
takes a GeoJSON area of interest plus the URL of a data source and answers with
statistics keyed by band or column:

```python
result = client.zonal_stats.raster.stats(
    {"type": "Point", "coordinates": [178.4, -18.1]},
    url="https://example.test/depth.tif",
    stats=["mean", "count"],
    radius=500,
)
result["band_1"]["mean"]
```

There are four routes, one per kind of source, listed in
[`ZONAL_STATS_ENDPOINTS`][datamermaid.resources.zonal_stats.ZONAL_STATS_ENDPOINTS]:

| Property      | Route          | Source                                 | Keys          |
| ------------- | -------------- | -------------------------------------- | ------------- |
| `raster`      | `raster`       | a Cloud Optimized GeoTIFF              | `band_1`, ... |
| `raster_stac` | `raster/stac`  | a STAC Item whose asset is a GeoTIFF   | `band_1`, ... |
| `vector`      | `vector`       | a GeoParquet file                      | column names  |
| `vector_stac` | `vector/stac`  | a STAC Item whose asset is GeoParquet  | column names  |

```python
result = client.zonal_stats.raster_stac.stats(
    aoi, url="https://example.test/item.json", asset="visual", bands=[1, 2]
)
result = client.zonal_stats.vector.stats(
    aoi, url="https://example.test/habitat.parquet", columns=["depth"], weighting_method="ratio"
)
result["depth"]["mean"]
```

The statistic names and the vector weighting methods are fixed vocabularies,
[`Stat`][datamermaid.resources.zonal_stats.Stat] and
[`WeightingMethod`][datamermaid.resources.zonal_stats.WeightingMethod].  Plain
strings are accepted too; an unknown one raises ``ValueError`` before any request
is made.

The area of interest goes through [`to_aoi`][datamermaid.geometry.to_aoi], so a
[`Site`][datamermaid.models.Site], a shapely geometry or a ``(lon, lat)`` tuple
work just as well as a GeoJSON mapping:

```python
site = client.projects(project_id).sites.get(site_id)
result = client.zonal_stats.raster.stats(site, url="https://example.test/depth.tif", radius=500)
```

Many areas of interest go through ``batch``, which returns a completed
[`Batch`][datamermaid.batch.Batch] that issues one request per AOI on a
bounded thread pool and labels each result so ``to_df()`` gives one row per AOI:

```python
batch = client.zonal_stats.raster.batch(sites, url="https://example.test/depth.tif")
frame = batch.to_df()  # one wide row per site, keyed by `label` (the site id)
```

Because the requests go to another host, they are sent without the client's
auth and without the headers passed to it with ``headers=``, so the MERMAID
credentials the client holds are never disclosed to it.  Retries,
backoff and error mapping are the client's own.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import replace
from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, overload

from ..batch import DEFAULT_MAX_WORKERS, Batch, BatchFailure, BatchStream, _validate_execution
from ..exceptions import MermaidConnectionError
from ..geometry import GeometryLike, _validate_radius, to_aoi
from ..models import Site, ZonalStatsResult
from .base import BaseResource
from .zonal_job import (
    SourceInput,
    StacSearchLike,
    ZonalJob,
    ZonalSource,
    ZonalTask,
    resolve_sources,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..client import MermaidClient

__all__ = [
    "ZONAL_STATS_ENDPOINTS",
    "BaseZonalStats",
    "BatchItem",
    "RasterStacStats",
    "RasterStats",
    "ResponseCache",
    "Stat",
    "VectorStacStats",
    "VectorStats",
    "WeightingMethod",
    "ZonalStats",
]


class Stat(str, Enum):
    """A statistic the service can compute: the ``StatType`` vocabulary.

    Members compare equal to their wire value, so ``Stat.MEAN == "mean"``, and
    ``stats=`` takes members and plain strings alike.
    """

    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    COUNT = "count"
    SUM = "sum"
    STD = "std"
    MEDIAN = "median"
    MAJORITY = "majority"
    MINORITY = "minority"
    UNIQUE = "unique"
    RANGE = "range"
    NODATA = "nodata"
    AOI_AREA = "aoi_area"
    DATA_AREA = "data_area"
    FREQ_HIST = "freq_hist"
    DENSITY = "density"


class WeightingMethod(str, Enum):
    """How the vector routes weight each feature that intersects the AOI."""

    #: Weight by the intersection area.
    AREA = "area"
    #: Weight by the intersection area over the feature's own area.
    RATIO = "ratio"


def _valid_names(vocabulary: type[Enum]) -> str:
    return ", ".join(sorted(member.value for member in vocabulary))


def _stats(stats: Iterable[Stat | str] | None) -> list[str] | None:
    """Coerce ``stats`` to wire values, rejecting a bare string and unknown names."""

    if stats is None:
        return None
    if isinstance(stats, (str, bytes)):
        raise TypeError("stats must be a sequence of statistic names")
    names: list[str] = []
    for stat in stats:
        try:
            names.append(Stat(stat).value)
        except ValueError:
            raise ValueError(
                f"unknown statistic {stat!r}; valid names are: {_valid_names(Stat)}"
            ) from None
    return names


def _weighting_method(method: WeightingMethod | str | None) -> str | None:
    if method is None:
        return None
    try:
        return WeightingMethod(method).value
    except ValueError:
        raise ValueError(
            f"unknown weighting_method {method!r}; valid names are: {_valid_names(WeightingMethod)}"
        ) from None


def _columns(columns: Sequence[str]) -> list[str]:
    if isinstance(columns, (str, bytes)) or not isinstance(columns, Sequence):
        raise TypeError("columns must be a sequence of column names")
    if not columns:
        raise ValueError("columns must not be empty")
    for column in columns:
        if not isinstance(column, str) or not column:
            raise TypeError(f"column names must be non-empty strings, got {column!r}")
    return list(columns)


def _asset(asset: str | None) -> str | None:
    if asset is None:
        return None
    if not isinstance(asset, str) or not asset:
        raise ValueError("asset must be a non-empty string")
    return asset


def _geometry_column(geometry_column: str | None) -> str | None:
    if geometry_column is None:
        return None
    if not isinstance(geometry_column, str):
        raise TypeError("geometry_column must be a string")
    return geometry_column


def _bands(bands: Sequence[int] | None) -> list[int] | None:
    if bands is None:
        return None
    if isinstance(bands, (str, bytes)) or not isinstance(bands, Sequence):
        raise TypeError("bands must be a sequence of band indices")
    if not bands:
        raise ValueError("bands must not be empty")
    for band in bands:
        if isinstance(band, bool) or not isinstance(band, int):
            raise TypeError(f"band indices must be integers, got {band!r}")
    return list(bands)


class BatchItem(NamedTuple):
    """Legacy input tuple; current zonal batches expose ZonalTask inputs."""

    aoi: Any
    label: Any


def _expand_aois(aois: Any) -> list[Any]:
    """Turn the ``aois`` argument of ``batch`` into a list of areas of interest.

    A single object whose ``__geo_interface__`` (or which itself) is a GeoJSON
    ``FeatureCollection``, such as a GeoDataFrame, expands into its features.
    Anything else is iterated.
    """

    collection = getattr(aois, "__geo_interface__", aois)
    if isinstance(collection, Mapping):
        if collection.get("type") == "FeatureCollection":
            return list(collection.get("features") or ())
        raise TypeError(
            "aois must be an iterable of areas of interest or a FeatureCollection, "
            f"got a {collection.get('type') or 'mapping'!s}"
        )
    if isinstance(aois, (str, bytes)) or not isinstance(aois, Iterable):
        raise TypeError(f"aois must be an iterable of areas of interest, got {type(aois).__name__}")
    return list(aois)


def _default_label(aoi: Any, position: int) -> Any:
    """The label an AOI carries when none is given: site id, feature id, or its position."""

    if isinstance(aoi, Site):
        return aoi.id
    feature = aoi if isinstance(aoi, Mapping) else getattr(aoi, "__geo_interface__", None)
    if isinstance(feature, Mapping) and feature.get("type") == "Feature" and "id" in feature:
        return feature["id"]
    return position


def _resolve_labels(aois: Sequence[Any], labels: Iterable[Any] | None) -> list[Any]:
    if labels is None:
        return [_default_label(aoi, position) for position, aoi in enumerate(aois)]
    if isinstance(labels, (str, bytes)):
        raise TypeError("labels must be a sequence with one label per aoi")
    resolved = list(labels)
    if len(resolved) != len(aois):
        raise ValueError(f"labels has {len(resolved)} entries for {len(aois)} aois")
    return resolved


#: Responses ``client.zonal_stats.cache`` keeps.  A response is a few hundred
#: bytes, so a full cache is tens of megabytes.
DEFAULT_CACHE_SIZE = 100_000


class ResponseCache(MutableMapping[str, Any]):
    """A thread-safe, size-bounded, in-memory cache of zonal stats responses.

    Each [`ZonalStats`][datamermaid.resources.zonal_stats.ZonalStats] resource
    holds one as ``client.zonal_stats.cache``, and ``batch`` and ``prepare``
    use it unless told otherwise.  When full, the least recently used entry is
    dropped, so a rerun of a job larger than ``maxsize`` sends some requests
    again.  For those, or to keep responses between sessions, pass a
    persistent mapping such as a ``diskcache.Cache`` as ``cache=``.
    """

    def __init__(self, maxsize: int = DEFAULT_CACHE_SIZE) -> None:
        if isinstance(maxsize, bool) or not isinstance(maxsize, int) or maxsize < 1:
            raise ValueError("maxsize must be a positive integer")
        self.maxsize = maxsize
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(size={len(self)}, maxsize={self.maxsize})"

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            value = self._data[key]
            self._data.move_to_end(key)
            return value

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def __delitem__(self, key: str) -> None:
        with self._lock:
            del self._data[key]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._data))

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


def _resolve_cache(
    cache: MutableMapping[str, Any] | bool | None, default: MutableMapping[str, Any]
) -> MutableMapping[str, Any] | None:
    """``True`` is the resource's own cache; ``False`` or ``None`` turns caching off."""
    if cache is True:
        return default
    if cache is False or cache is None:
        return None
    if not isinstance(cache, MutableMapping):
        raise TypeError(
            f"cache must be True, False, None or a mutable mapping, got {type(cache).__name__}"
        )
    return cache


def _check_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a non-empty string")
    return url


class BaseZonalStats:
    """One route of the Zonal Stats service.

    Subclasses set [`route`][.route] (the segment after ``.../zonal-stats/``) and
    extend [`stats`][.stats] with the options that route accepts.  Calling the
    endpoint is the same as calling [`stats`][.stats].
    """

    #: Route segment under the service root, e.g. ``"raster"``.
    route: ClassVar[str]

    def __init__(self, resource: ZonalStats) -> None:
        self._resource = resource
        self._client = resource._client

    def __repr__(self) -> str:
        return f"{type(self).__name__}(url={self.url!r})"

    @property
    def url(self) -> str:
        """The absolute URL POSTed to.  No trailing slash: the service has none."""

        return f"{self._resource.path}{self.route}"

    def _options(self, **options: Any) -> dict[str, Any]:
        """Validate route options once; generic endpoints accept arbitrary fields."""
        return options

    def _bind_source(
        self,
        source: ZonalSource,
        names: list[str] | None,
        radius: float | None,
        options: dict[str, Any],
        cache: MutableMapping[str, Any] | None = None,
    ) -> Callable[[ZonalTask], ZonalStatsResult]:
        """Bind a resolved source to its request target and prepared body template."""
        template = {key: value for key, value in options.items() if value is not None}
        template["url"] = source.url
        if names is not None:
            template["stats"] = names
        post = self._post

        def compute(task: ZonalTask) -> ZonalStatsResult:
            body = {**template, "aoi": to_aoi(task.aoi, radius=radius)}
            result = post(body, label=task.label, cache=cache)
            return replace(result, stac=source.stac) if source.stac is not None else result

        return compute

    def _cache_key(self, body: dict[str, Any]) -> str:
        """Hash the route and the full request body; equal bodies give equal answers."""
        canonical = json.dumps(
            {"endpoint": self.url, "body": body}, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _post(
        self,
        body: dict[str, Any],
        *,
        label: Any,
        cache: MutableMapping[str, Any] | None = None,
    ) -> ZonalStatsResult:
        key = self._cache_key(body) if cache is not None else ""
        data = cache.get(key) if cache is not None else None
        if data is None:
            # The service is public and lives on another host, so `public=True`
            # sends it neither the client's auth nor its extra headers.
            data = self._client.request_json("POST", self.url, json=body, public=True)
        try:
            result = ZonalStatsResult.from_api(
                data, aoi=body["aoi"], source=body["url"], label=label
            )
        except TypeError as exc:
            # An empty body or the wrong JSON shape is as unusable as a
            # non-JSON body, which `request_json` already reports this way.
            raise MermaidConnectionError(
                f"POST {self.url} returned a body that is not a zonal stats response: {exc}"
            ) from exc
        # Only a usable response is stored, so failures are retried next time.
        if cache is not None:
            cache[key] = data
        return result

    def _request(
        self,
        aoi: GeometryLike,
        *,
        label: Any,
        url: str,
        stats: Iterable[Stat | str] | None,
        radius: float | None,
        **options: Any,
    ) -> ZonalStatsResult:
        """Validate and execute a single statistics request."""

        radius = _validate_radius(radius)
        validated = self._options(**options)
        source = ZonalSource(_check_url(url))
        compute = self._bind_source(source, _stats(stats), radius, validated)
        return compute(ZonalTask(aoi, label, source))

    def prepare(
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        cache: MutableMapping[str, Any] | bool | None = True,
        **options: Any,
    ) -> ZonalJob:
        """Resolve sources and count pairs without submitting statistics requests.

        Supply exactly one of url, sources (URLs or STAC Items), or a
        pystac-client ItemSearch via search. Item assets resolve to data URLs;
        string URLs retain this endpoint's usual meaning. AOIs and sources are
        retained, but their Cartesian product is generated lazily at execution.

        ``cache`` works as for [`batch`][..batch], so running the job again
        after a failure only sends the requests that did not succeed.
        """
        return self._prepare(
            aois,
            url=url,
            sources=sources,
            search=search,
            labels=labels,
            stats=stats,
            radius=radius,
            cache=cache,
            **options,
        )

    def _prepare(
        self,
        aois: Any,
        *,
        url: str | None,
        sources: Iterable[SourceInput] | None,
        search: StacSearchLike | None,
        labels: Iterable[Any] | None,
        stats: Iterable[Stat | str] | None,
        radius: float | None,
        cache: MutableMapping[str, Any] | bool | None = True,
        **options: Any,
    ) -> ZonalJob:
        """Validate options and bind every source before constructing the job."""
        store = _resolve_cache(cache, self._resource.cache)
        radius = _validate_radius(radius)
        options = self._options(**options)
        names = _stats(stats)
        expanded = _expand_aois(aois)
        resolved_labels = _resolve_labels(expanded, labels)
        resolved_sources = resolve_sources(url, sources, search, options.get("asset"))
        # Identity keys preserve distinct items even when their asset URLs match.
        requests = {
            id(source): self._bind_source(source, names, radius, options, store)
            for source in resolved_sources
        }

        def compute(task: ZonalTask) -> ZonalStatsResult:
            return requests[id(task.source)](task)

        return ZonalJob(expanded, resolved_labels, resolved_sources, compute)

    def stats(
        self,
        aoi: GeometryLike,
        *,
        url: str,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        label: Any = None,
        **options: Any,
    ) -> ZonalStatsResult:
        """Compute statistics for ``aoi`` against the source at ``url``.

        Args:
            aoi: Anything [`to_aoi`][datamermaid.geometry.to_aoi] accepts: a
                GeoJSON ``Point`` or ``Polygon`` mapping, a ``Feature`` holding
                one, an object with a ``__geo_interface__``, a ``(lon, lat)``
                tuple, or a [`Site`][datamermaid.models.Site].
            url: The data source the route reads, e.g. a Cloud Optimized GeoTIFF.
            stats: [`Stat`][datamermaid.resources.zonal_stats.Stat] members or
                their names (``mean``, ``count``, ``median``, ...).  When omitted
                the service picks its defaults.
            radius: Buffer around a ``Point``, in metres.
            label: An identifier carried onto the result unchanged.
            **options: Route-specific body fields; a ``None`` value is left out.

        Raises:
            TypeError: If ``aoi`` is none of the kinds
                [`to_aoi`][datamermaid.geometry.to_aoi] accepts.
            ValueError: If ``aoi`` is not a Point or Polygon, a statistic name
                is unknown, or an option is malformed.
            MermaidAPIError: If the service rejects the request (422 for a
                validation error, 400 for an unreadable source).
        """

        return self._request(aoi, label=label, url=url, stats=stats, radius=radius, **options)

    @overload
    def batch(
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        stream: Literal[False] = False,
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        **options: Any,
    ) -> Batch[ZonalTask, ZonalStatsResult]: ...

    @overload
    def batch(
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        stream: Literal[True],
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        **options: Any,
    ) -> BatchStream[ZonalTask, ZonalStatsResult]: ...

    @overload
    def batch(
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        stream: bool = False,
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        **options: Any,
    ) -> Batch[ZonalTask, ZonalStatsResult] | BatchStream[ZonalTask, ZonalStatsResult]: ...

    @overload
    def batch(
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        stream: Literal[False] = False,
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise", "return"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        **options: Any,
    ) -> Batch[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]: ...

    @overload
    def batch(
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        stream: Literal[True],
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise", "return"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        **options: Any,
    ) -> BatchStream[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]: ...

    @overload
    def batch(
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        stream: bool = False,
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise", "return"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        **options: Any,
    ) -> (
        Batch[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
        | BatchStream[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
    ): ...

    def batch(
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        stream: bool = False,
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise", "return"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        **options: Any,
    ) -> (
        Batch[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
        | BatchStream[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
    ):
        """One statistics request per area of interest, run on a bounded thread pool.

        By default this call waits for all requests and returns a completed
        [`Batch`][datamermaid.batch.Batch] in input order. ``aois`` is read in
        full, so a lazy list such as ``sites.list()`` fetches all its pages.
        Each result carries a ``label``, so ``batch.to_df()`` is one row per AOI.

        Args:
            aois: An iterable of areas of interest (anything
                [`stats`][..stats] accepts), or one object whose
                ``__geo_interface__`` is a ``FeatureCollection``, such as a
                GeoDataFrame, which expands into its features.
            url: The data source the route reads; exclusive with sources/search.
            sources: Iterable of source URLs or STAC Items.
            search: A pystac-client ItemSearch, resolved once before execution.
            stream: Return a single-pass BatchStream instead of retaining results.
                Use as a context manager and keep the client open while iterating.
            labels: One label per AOI, in order.  Omitted, a
                [`Site`][datamermaid.models.Site] is labelled by its ``id``, a
                ``Feature`` by its ``id``, and anything else by its position.
            max_workers: Most requests in flight at once.
            cache: Where successful responses are kept, keyed by a hash of the
                route and the request body.  A request already in the cache is
                answered without calling the service, so a rerun only sends the
                requests that failed or are new.  ``True`` (the default) uses
                ``client.zonal_stats.cache``, an in-memory
                [`ResponseCache`][datamermaid.resources.zonal_stats.ResponseCache]
                shared by every batch on the client.  ``False`` or ``None`` sends
                every request and stores nothing.  Any other mutable mapping,
                such as a ``dict`` or a ``diskcache.Cache``, is used in its place.
                Failures are never stored.  The key does not cover the source's
                contents, so clear the cache if the data at a URL changes.  Two
                workers asking for the same uncached body at once both send it.
            errors: ``"raise"`` raises the first error in input order after all
                requests finish; ``"return"`` retains BatchFailure objects with the
                input task and original exception.
            stats: Statistic names, as for [`stats`][..stats].
            radius: Buffer around a ``Point``, in metres.
            **options: The route's own options, the same as its ``stats`` and
                ``prepare`` take: ``bands`` and ``approx_stats`` for rasters,
                ``columns``, ``geometry_column`` and ``weighting_method`` for
                vectors, ``asset`` for the STAC routes.

        Raises:
            ValueError: If ``url`` is empty, ``labels`` has the wrong length, or
                ``max_workers`` is below ``1``.
            TypeError: If ``aois`` is not iterable, or is a single geometry, or
                ``cache`` is not a bool, ``None`` or a mutable mapping.
        """

        _validate_execution(max_workers, errors)
        job = self.prepare(
            aois,
            url=url,
            sources=sources,
            search=search,
            labels=labels,
            stats=stats,
            radius=radius,
            cache=cache,
            **options,
        )
        return job.run(max_workers=max_workers, errors=errors, stream=stream)

    def __call__(self, aoi: GeometryLike, *, url: str, **kwargs: Any) -> ZonalStatsResult:
        """Same as [`stats`][..stats], so ``client.zonal_stats.raster(aoi, url=...)`` works."""

        return self.stats(aoi, url=url, **kwargs)


class RasterStats(BaseZonalStats):
    """``POST .../zonal-stats/raster``: statistics from a Cloud Optimized GeoTIFF."""

    route = "raster"

    def _options(  # type: ignore[override]
        self,
        *,
        bands: Sequence[int] | None = None,
        approx_stats: bool = False,
    ) -> dict[str, Any]:
        return {"bands": _bands(bands), "approx_stats": approx_stats}

    # The base signature takes ``**options`` so any route can be described; this
    # one names the raster options instead, which mypy reads as a narrowing.

    def prepare(  # type: ignore[override]
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        bands: Sequence[int] | None = None,
        approx_stats: bool = False,
        cache: MutableMapping[str, Any] | bool | None = True,
    ) -> ZonalJob:
        """Resolve sources for [`batch`][..batch] without sending statistics requests.

        Takes the options of [`stats`][..stats], plus ``url``, ``sources``,
        ``search``, ``labels`` and ``cache`` as for
        [`BaseZonalStats.batch`][datamermaid.resources.zonal_stats.BaseZonalStats.batch].
        """
        return self._prepare(
            aois,
            labels=labels,
            url=url,
            sources=sources,
            search=search,
            stats=stats,
            radius=radius,
            bands=bands,
            approx_stats=approx_stats,
            cache=cache,
        )

    def stats(  # type: ignore[override]
        self,
        aoi: GeometryLike,
        *,
        url: str,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        bands: Sequence[int] | None = None,
        approx_stats: bool = False,
        label: Any = None,
    ) -> ZonalStatsResult:
        """Compute statistics for ``aoi`` over the raster at ``url``.

        Args:
            aoi: Anything [`to_aoi`][datamermaid.geometry.to_aoi] accepts: a
                GeoJSON ``Point`` or ``Polygon`` mapping, a ``Feature``, an object
                with a ``__geo_interface__``, a ``(lon, lat)`` tuple, or a
                [`Site`][datamermaid.models.Site].
            url: URL of the Cloud Optimized GeoTIFF (``https://`` or ``s3://``).
            stats: Statistic names.  Omitted, the service returns ``min``,
                ``max``, ``mean`` and ``count``; ``aoi_area`` and ``data_area``
                are always included.
            radius: Buffer around a ``Point``, in metres.
            bands: 1-based band indices to read.  Omitted, the service reads
                band 1.
            approx_stats: Read overviews for faster, approximate values.
            label: An identifier carried onto the result unchanged.

        Returns:
            The statistics keyed by band, ``band_1`` for the first band.
        """

        return self._request(
            aoi,
            label=label,
            url=url,
            stats=stats,
            radius=radius,
            bands=bands,
            approx_stats=approx_stats,
        )


class RasterStacStats(RasterStats):
    """``POST .../zonal-stats/raster/stac``: statistics from a raster asset of a STAC Item."""

    route = "raster/stac"

    def _options(self, *, asset: str | None = None, **options: Any) -> dict[str, Any]:
        return {**super()._options(**options), "asset": _asset(asset)}

    def _bind_source(
        self,
        source: ZonalSource,
        names: list[str] | None,
        radius: float | None,
        options: dict[str, Any],
        cache: MutableMapping[str, Any] | None = None,
    ) -> Callable[[ZonalTask], ZonalStatsResult]:
        if source.stac is not None:
            options = {key: value for key, value in options.items() if key != "asset"}
            return self._resource.raster._bind_source(source, names, radius, options, cache)
        return super()._bind_source(source, names, radius, options, cache)

    def prepare(  # type: ignore[override]
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        asset: str | None = None,
        bands: Sequence[int] | None = None,
        approx_stats: bool = False,
        cache: MutableMapping[str, Any] | bool | None = True,
    ) -> ZonalJob:
        """Resolve sources for [`batch`][..batch] without sending statistics requests.

        Takes the options of [`stats`][..stats], plus ``url``, ``sources``,
        ``search``, ``labels`` and ``cache`` as for
        [`BaseZonalStats.batch`][datamermaid.resources.zonal_stats.BaseZonalStats.batch].
        """
        return self._prepare(
            aois,
            labels=labels,
            url=url,
            sources=sources,
            search=search,
            stats=stats,
            radius=radius,
            asset=asset,
            bands=bands,
            approx_stats=approx_stats,
            cache=cache,
        )

    def stats(  # type: ignore[override]
        self,
        aoi: GeometryLike,
        *,
        url: str,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        asset: str | None = None,
        bands: Sequence[int] | None = None,
        approx_stats: bool = False,
        label: Any = None,
    ) -> ZonalStatsResult:
        """Compute statistics for ``aoi`` over a raster asset of the STAC Item at ``url``.

        Args:
            aoi: Anything [`to_aoi`][datamermaid.geometry.to_aoi] accepts.
            url: URL of the STAC Item JSON.
            stats: [`Stat`][datamermaid.resources.zonal_stats.Stat] members or
                their names.  Omitted, the service picks its defaults.
            radius: Buffer around a ``Point``, in metres.
            asset: Key of the asset to read.  Omitted, the service reads the
                Item's first asset.
            bands: 1-based band indices to read.  Omitted, the service reads
                band 1.
            approx_stats: Read overviews for faster, approximate values.
            label: An identifier carried onto the result unchanged.

        Returns:
            The statistics keyed by band, ``band_1`` for the first band.
        """

        return self._request(
            aoi,
            label=label,
            url=url,
            stats=stats,
            radius=radius,
            asset=asset,
            bands=bands,
            approx_stats=approx_stats,
        )


class VectorStats(BaseZonalStats):
    """``POST .../zonal-stats/vector``: area-weighted statistics from a GeoParquet file.

    ``columns`` names the numeric columns to summarise and is required; the
    result is keyed by those names.
    """

    route = "vector"

    def _options(  # type: ignore[override]
        self,
        *,
        columns: Sequence[str],
        geometry_column: str | None = None,
        weighting_method: WeightingMethod | str | None = None,
        approx_stats: bool = False,
    ) -> dict[str, Any]:
        return {
            "columns": _columns(columns),
            "geometry_column": _geometry_column(geometry_column),
            "weighting_method": _weighting_method(weighting_method),
            "approx_stats": approx_stats,
        }

    def prepare(  # type: ignore[override]
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        columns: Sequence[str],
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        geometry_column: str | None = None,
        weighting_method: WeightingMethod | str | None = None,
        approx_stats: bool = False,
        cache: MutableMapping[str, Any] | bool | None = True,
    ) -> ZonalJob:
        """Resolve sources for [`batch`][..batch] without sending statistics requests.

        Takes the options of [`stats`][..stats], plus ``url``, ``sources``,
        ``search``, ``labels`` and ``cache`` as for
        [`BaseZonalStats.batch`][datamermaid.resources.zonal_stats.BaseZonalStats.batch].
        """
        return self._prepare(
            aois,
            labels=labels,
            url=url,
            sources=sources,
            search=search,
            stats=stats,
            radius=radius,
            columns=columns,
            geometry_column=geometry_column,
            weighting_method=weighting_method,
            approx_stats=approx_stats,
            cache=cache,
        )

    def stats(  # type: ignore[override]
        self,
        aoi: GeometryLike,
        *,
        url: str,
        columns: Sequence[str],
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        geometry_column: str | None = None,
        weighting_method: WeightingMethod | str | None = None,
        approx_stats: bool = False,
        label: Any = None,
    ) -> ZonalStatsResult:
        """Compute statistics for ``aoi`` over the GeoParquet file at ``url``.

        Args:
            aoi: Anything [`to_aoi`][datamermaid.geometry.to_aoi] accepts.
            url: URL of the GeoParquet file.
            columns: Numeric columns to summarise.  Required and non-empty.
            stats: [`Stat`][datamermaid.resources.zonal_stats.Stat] members or
                their names.  Omitted, the service picks its defaults.
            radius: Buffer around a ``Point``, in metres.
            geometry_column: Name of the geometry column.  Omitted, the service
                finds it from the file's metadata.
            weighting_method: How intersecting features are weighted, a
                [`WeightingMethod`][datamermaid.resources.zonal_stats.WeightingMethod]
                or its name.  Omitted, the service weights by ``area``.
            approx_stats: Reserved by the service for a future optimisation.
            label: An identifier carried onto the result unchanged.

        Returns:
            The statistics keyed by column name.

        Raises:
            ValueError: If ``columns`` is empty, or a statistic or weighting
                method name is unknown.
            TypeError: If ``columns`` is a bare string or holds non-strings.
        """

        return self._request(
            aoi,
            label=label,
            url=url,
            stats=stats,
            radius=radius,
            columns=columns,
            geometry_column=geometry_column,
            weighting_method=weighting_method,
            approx_stats=approx_stats,
        )


class VectorStacStats(VectorStats):
    """``POST .../zonal-stats/vector/stac``: statistics from a GeoParquet asset of a STAC Item.

    The vector route's options plus ``asset``, the key of the GeoParquet asset
    to read.
    """

    route = "vector/stac"

    def _options(self, *, asset: str | None = None, **options: Any) -> dict[str, Any]:
        return {**super()._options(**options), "asset": _asset(asset)}

    def _bind_source(
        self,
        source: ZonalSource,
        names: list[str] | None,
        radius: float | None,
        options: dict[str, Any],
        cache: MutableMapping[str, Any] | None = None,
    ) -> Callable[[ZonalTask], ZonalStatsResult]:
        if source.stac is not None:
            options = {key: value for key, value in options.items() if key != "asset"}
            if options.get("geometry_column") is None:
                options["geometry_column"] = "geometry"
            return self._resource.vector._bind_source(source, names, radius, options, cache)
        return super()._bind_source(source, names, radius, options, cache)

    def prepare(  # type: ignore[override]
        self,
        aois: Any,
        *,
        url: str | None = None,
        sources: Iterable[SourceInput] | None = None,
        search: StacSearchLike | None = None,
        columns: Sequence[str],
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        asset: str | None = None,
        geometry_column: str | None = None,
        weighting_method: WeightingMethod | str | None = None,
        approx_stats: bool = False,
        cache: MutableMapping[str, Any] | bool | None = True,
    ) -> ZonalJob:
        """Resolve sources for [`batch`][..batch] without sending statistics requests.

        Takes the options of [`stats`][..stats], plus ``url``, ``sources``,
        ``search``, ``labels`` and ``cache`` as for
        [`BaseZonalStats.batch`][datamermaid.resources.zonal_stats.BaseZonalStats.batch].
        """
        return self._prepare(
            aois,
            labels=labels,
            url=url,
            sources=sources,
            search=search,
            stats=stats,
            radius=radius,
            asset=asset,
            columns=columns,
            geometry_column=geometry_column,
            weighting_method=weighting_method,
            approx_stats=approx_stats,
            cache=cache,
        )

    def stats(  # type: ignore[override]
        self,
        aoi: GeometryLike,
        *,
        url: str,
        columns: Sequence[str],
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        asset: str | None = None,
        geometry_column: str | None = None,
        weighting_method: WeightingMethod | str | None = None,
        approx_stats: bool = False,
        label: Any = None,
    ) -> ZonalStatsResult:
        """Compute statistics for ``aoi`` over a GeoParquet asset of the STAC Item at ``url``.

        Args:
            aoi: Anything [`to_aoi`][datamermaid.geometry.to_aoi] accepts.
            url: URL of the STAC Item JSON.
            columns: Numeric columns to summarise.  Required and non-empty.
            stats: [`Stat`][datamermaid.resources.zonal_stats.Stat] members or
                their names.  Omitted, the service picks its defaults.
            radius: Buffer around a ``Point``, in metres.
            asset: Key of the GeoParquet asset to read.  Omitted, the service
                reads the Item's first asset.
            geometry_column: Name of the geometry column.  Omitted, the service
                uses ``geometry``.
            weighting_method: How intersecting features are weighted, a
                [`WeightingMethod`][datamermaid.resources.zonal_stats.WeightingMethod]
                or its name.  Omitted, the service weights by ``area``.
            approx_stats: Reserved by the service for a future optimisation.
            label: An identifier carried onto the result unchanged.

        Returns:
            The statistics keyed by column name.
        """

        return self._request(
            aoi,
            label=label,
            url=url,
            stats=stats,
            radius=radius,
            asset=asset,
            columns=columns,
            geometry_column=geometry_column,
            weighting_method=weighting_method,
            approx_stats=approx_stats,
        )


#: Every route of the service, as ``(property name, endpoint class)``, in the
#: order they appear on [`ZonalStats`][.ZonalStats].
ZONAL_STATS_ENDPOINTS: tuple[tuple[str, type[BaseZonalStats]], ...] = (
    ("raster", RasterStats),
    ("raster_stac", RasterStacStats),
    ("vector", VectorStats),
    ("vector_stac", VectorStacStats),
)


class ZonalStats(BaseResource):
    """The Zonal Stats service, as ``client.zonal_stats``.

    [`path`][datamermaid.resources.base.BaseResource.path] is the absolute service
    root, so the endpoints bypass the client's ``base_url`` altogether.
    """

    def __init__(self, client: MermaidClient) -> None:
        super().__init__(client)
        self.path = client.zonal_stats_url
        #: Responses kept for ``batch`` and ``prepare`` calls that leave ``cache``
        #: at its default.
        #: Call ``client.zonal_stats.cache.clear()`` if a source changes.
        self.cache: MutableMapping[str, Any] = ResponseCache()

    @cached_property
    def raster(self) -> RasterStats:
        """Statistics from a Cloud Optimized GeoTIFF, ``POST .../raster``."""

        return RasterStats(self)

    @cached_property
    def raster_stac(self) -> RasterStacStats:
        """Statistics from a raster asset of a STAC Item, ``POST .../raster/stac``."""

        return RasterStacStats(self)

    @cached_property
    def vector(self) -> VectorStats:
        """Statistics from a GeoParquet file, ``POST .../vector``."""

        return VectorStats(self)

    @cached_property
    def vector_stac(self) -> VectorStacStats:
        """Statistics from a GeoParquet asset of a STAC Item, ``POST .../vector/stac``."""

        return VectorStacStats(self)
