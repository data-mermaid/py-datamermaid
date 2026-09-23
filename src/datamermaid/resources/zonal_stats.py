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

Many areas of interest go through ``batch``, which answers with a lazy
[`LazyBatch`][datamermaid.batch.LazyBatch] that issues one request per AOI on a
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

from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, TypeVar, cast

from ..batch import DEFAULT_MAX_WORKERS, LazyBatch
from ..exceptions import MermaidConnectionError
from ..geometry import GeometryLike, to_aoi
from ..models import Site, ZonalStatsResult
from .base import BaseResource

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..client import MermaidClient

__all__ = [
    "ZONAL_STATS_ENDPOINTS",
    "BatchItem",
    "RasterStacStatsEndpoint",
    "RasterStatsEndpoint",
    "Stat",
    "VectorStacStatsEndpoint",
    "VectorStatsEndpoint",
    "WeightingMethod",
    "ZonalStatsEndpoint",
    "ZonalStatsResource",
]

#: An endpoint wrapper cached on the resource, for `ZonalStatsResource._endpoint`.
E = TypeVar("E", bound="ZonalStatsEndpoint")


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
    """One area of interest of a batch, with the label its result will carry."""

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


def _check_url(url: Any) -> str:
    if not isinstance(url, str) or not url:
        raise ValueError("url must be a non-empty string")
    return url


class ZonalStatsEndpoint:
    """One route of the Zonal Stats service.

    Subclasses set [`route`][.route] (the segment after ``.../zonal-stats/``) and
    extend [`stats`][.stats] with the options that route accepts.  Calling the
    endpoint is the same as calling [`stats`][.stats].
    """

    #: Route segment under the service root, e.g. ``"raster"``.
    route: ClassVar[str]

    def __init__(self, resource: ZonalStatsResource) -> None:
        self._resource = resource
        self._client = resource._client

    def __repr__(self) -> str:
        return f"{type(self).__name__}(url={self.url!r})"

    @property
    def url(self) -> str:
        """The absolute URL POSTed to.  No trailing slash: the service has none."""

        return f"{self._resource.path}{self.route}"

    def _body(
        self,
        aoi: GeometryLike,
        *,
        url: str,
        stats: Iterable[Stat | str] | None,
        radius: float | None,
        **options: Any,
    ) -> dict[str, Any]:
        """Compose the request body, leaving out every option that is ``None``."""

        body: dict[str, Any] = {"aoi": to_aoi(aoi, radius=radius), "url": _check_url(url)}
        names = _stats(stats)
        if names is not None:
            body["stats"] = names
        for key, value in options.items():
            if value is not None:
                body[key] = value
        return body

    def _post(self, body: dict[str, Any], *, label: Any) -> ZonalStatsResult:
        # The service is public and lives on another host, so `public=True`
        # sends it neither the client's auth nor its extra headers.
        data = self._client.request_json("POST", self.url, json=body, public=True)
        try:
            return ZonalStatsResult.from_api(data, aoi=body["aoi"], source=body["url"], label=label)
        except TypeError as exc:
            # An empty body or the wrong JSON shape is as unusable as a
            # non-JSON body, which `request_json` already reports this way.
            raise MermaidConnectionError(
                f"POST {self.url} returned a body that is not a zonal stats response: {exc}"
            ) from exc

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
        """One statistics request: the single seam ``stats`` and ``batch`` both go through."""

        body = self._body(aoi, url=url, stats=stats, radius=radius, **options)
        return self._post(body, label=label)

    def _batch(
        self,
        aois: Any,
        *,
        labels: Iterable[Any] | None,
        max_workers: int,
        errors: Literal["raise", "return"],
        url: str,
        stats: Iterable[Stat | str] | None,
        radius: float | None,
        **options: Any,
    ) -> LazyBatch[BatchItem, ZonalStatsResult]:
        """Build the lazy batch behind ``batch``: expand the AOIs, fix the labels, defer the rest.

        The URL, the statistic names and the labels are checked here, before
        anything runs; each AOI is validated when its own request is composed,
        so a bad one fails at its position without spoiling the others.
        """

        _check_url(url)
        names = _stats(stats)
        expanded = _expand_aois(aois)
        resolved = _resolve_labels(expanded, labels)
        items = [BatchItem(aoi, label) for aoi, label in zip(expanded, resolved, strict=True)]

        def compute(item: BatchItem) -> ZonalStatsResult:
            return self._request(
                item.aoi, label=item.label, url=url, stats=names, radius=radius, **options
            )

        return LazyBatch(
            items,
            compute,
            max_workers=max_workers,
            errors=errors,
            label=lambda item: item.label,
        )

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

    def batch(
        self,
        aois: Any,
        *,
        url: str,
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        **options: Any,
    ) -> LazyBatch[BatchItem, ZonalStatsResult]:
        """One statistics request per area of interest, run lazily on a thread pool.

        No statistics request is sent until the batch is iterated, indexed or
        materialised; see [`LazyBatch`][datamermaid.batch.LazyBatch].  ``aois``
        itself is read in full here, so a lazy list such as ``sites.list()``
        fetches all its pages at this call.  Each result carries a
        ``label``, so ``batch.to_df()`` is one wide row per AOI.

        Args:
            aois: An iterable of areas of interest (anything
                [`stats`][..stats] accepts), or one object whose
                ``__geo_interface__`` is a ``FeatureCollection``, such as a
                GeoDataFrame, which expands into its features.
            url: The data source the route reads.
            labels: One label per AOI, in order.  Omitted, a
                [`Site`][datamermaid.models.Site] is labelled by its ``id``, a
                ``Feature`` by its ``id``, and anything else by its position.
            max_workers: Most requests in flight at once.
            errors: ``"raise"`` re-raises a failed request's exception at its
                position; ``"return"`` yields the exception object instead.
            stats: Statistic names, as for [`stats`][..stats].
            radius: Buffer around a ``Point``, in metres.
            **options: Route-specific body fields; a ``None`` value is left out.

        Raises:
            ValueError: If ``url`` is empty, ``labels`` has the wrong length, or
                ``max_workers`` is below ``1``.
            TypeError: If ``aois`` is not iterable, or is a single geometry.
        """

        return self._batch(
            aois,
            labels=labels,
            max_workers=max_workers,
            errors=errors,
            url=url,
            stats=stats,
            radius=radius,
            **options,
        )

    def __call__(self, aoi: GeometryLike, *, url: str, **kwargs: Any) -> ZonalStatsResult:
        """Same as [`stats`][..stats], so ``client.zonal_stats.raster(aoi, url=...)`` works."""

        return self.stats(aoi, url=url, **kwargs)


class RasterStatsEndpoint(ZonalStatsEndpoint):
    """``POST .../zonal-stats/raster``: statistics from a Cloud Optimized GeoTIFF."""

    route = "raster"

    # The base signature takes ``**options`` so any route can be described; this
    # one names the raster options instead, which mypy reads as a narrowing.
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
            bands=_bands(bands),
            approx_stats=approx_stats,
        )

    def batch(  # type: ignore[override]
        self,
        aois: Any,
        *,
        url: str,
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        bands: Sequence[int] | None = None,
        approx_stats: bool = False,
    ) -> LazyBatch[BatchItem, ZonalStatsResult]:
        """One raster statistics request per area of interest, run lazily in parallel.

        Takes the same options as [`stats`][..stats]; see
        [`ZonalStatsEndpoint.batch`][datamermaid.resources.zonal_stats.ZonalStatsEndpoint.batch]
        for how ``aois``, ``labels``, ``max_workers`` and ``errors`` behave.  The
        options are validated before any request is made.

        Example:
            ```python
            sites = client.projects(project_id).sites.list()
            batch = client.zonal_stats.raster.batch(
                sites, url="https://example.test/depth.tif", stats=["mean"], radius=500
            )
            frame = batch.to_df()  # columns: label (the site id), source, band_1_mean
            ```
        """

        return self._batch(
            aois,
            labels=labels,
            max_workers=max_workers,
            errors=errors,
            url=url,
            stats=stats,
            radius=radius,
            bands=_bands(bands),
            approx_stats=approx_stats,
        )


class RasterStacStatsEndpoint(ZonalStatsEndpoint):
    """``POST .../zonal-stats/raster/stac``: statistics from a raster asset of a STAC Item."""

    route = "raster/stac"

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
            asset=_asset(asset),
            bands=_bands(bands),
            approx_stats=approx_stats,
        )

    def batch(  # type: ignore[override]
        self,
        aois: Any,
        *,
        url: str,
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        asset: str | None = None,
        bands: Sequence[int] | None = None,
        approx_stats: bool = False,
    ) -> LazyBatch[BatchItem, ZonalStatsResult]:
        """One STAC raster statistics request per area of interest, run lazily in parallel.

        Takes the same options as [`stats`][..stats]; see
        [`ZonalStatsEndpoint.batch`][datamermaid.resources.zonal_stats.ZonalStatsEndpoint.batch]
        for how ``aois``, ``labels``, ``max_workers`` and ``errors`` behave.  The
        options are validated before any request is made.
        """

        return self._batch(
            aois,
            labels=labels,
            max_workers=max_workers,
            errors=errors,
            url=url,
            stats=stats,
            radius=radius,
            asset=_asset(asset),
            bands=_bands(bands),
            approx_stats=approx_stats,
        )


class VectorStatsEndpoint(ZonalStatsEndpoint):
    """``POST .../zonal-stats/vector``: area-weighted statistics from a GeoParquet file.

    ``columns`` names the numeric columns to summarise and is required; the
    result is keyed by those names.
    """

    route = "vector"

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
            columns=_columns(columns),
            geometry_column=_geometry_column(geometry_column),
            weighting_method=_weighting_method(weighting_method),
            approx_stats=approx_stats,
        )

    def batch(  # type: ignore[override]
        self,
        aois: Any,
        *,
        url: str,
        columns: Sequence[str],
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        geometry_column: str | None = None,
        weighting_method: WeightingMethod | str | None = None,
        approx_stats: bool = False,
    ) -> LazyBatch[BatchItem, ZonalStatsResult]:
        """One vector statistics request per area of interest, run lazily in parallel.

        Takes the same options as [`stats`][..stats]; see
        [`ZonalStatsEndpoint.batch`][datamermaid.resources.zonal_stats.ZonalStatsEndpoint.batch]
        for how ``aois``, ``labels``, ``max_workers`` and ``errors`` behave.  The
        options are validated before any request is made.
        """

        return self._batch(
            aois,
            labels=labels,
            max_workers=max_workers,
            errors=errors,
            url=url,
            stats=stats,
            radius=radius,
            columns=_columns(columns),
            geometry_column=_geometry_column(geometry_column),
            weighting_method=_weighting_method(weighting_method),
            approx_stats=approx_stats,
        )


class VectorStacStatsEndpoint(VectorStatsEndpoint):
    """``POST .../zonal-stats/vector/stac``: statistics from a GeoParquet asset of a STAC Item.

    The vector route's options plus ``asset``, the key of the GeoParquet asset
    to read.
    """

    route = "vector/stac"

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
            asset=_asset(asset),
            columns=_columns(columns),
            geometry_column=_geometry_column(geometry_column),
            weighting_method=_weighting_method(weighting_method),
            approx_stats=approx_stats,
        )

    def batch(  # type: ignore[override]
        self,
        aois: Any,
        *,
        url: str,
        columns: Sequence[str],
        labels: Iterable[Any] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        asset: str | None = None,
        geometry_column: str | None = None,
        weighting_method: WeightingMethod | str | None = None,
        approx_stats: bool = False,
    ) -> LazyBatch[BatchItem, ZonalStatsResult]:
        """One STAC vector statistics request per area of interest, run lazily in parallel.

        Takes the same options as [`stats`][..stats]; see
        [`ZonalStatsEndpoint.batch`][datamermaid.resources.zonal_stats.ZonalStatsEndpoint.batch]
        for how ``aois``, ``labels``, ``max_workers`` and ``errors`` behave.  The
        options are validated before any request is made.
        """

        return self._batch(
            aois,
            labels=labels,
            max_workers=max_workers,
            errors=errors,
            url=url,
            stats=stats,
            radius=radius,
            asset=_asset(asset),
            columns=_columns(columns),
            geometry_column=_geometry_column(geometry_column),
            weighting_method=_weighting_method(weighting_method),
            approx_stats=approx_stats,
        )


#: Every route of the service, as ``(property name, endpoint class)``, in the
#: order they appear on [`ZonalStatsResource`][.ZonalStatsResource].
ZONAL_STATS_ENDPOINTS: tuple[tuple[str, type[ZonalStatsEndpoint]], ...] = (
    ("raster", RasterStatsEndpoint),
    ("raster_stac", RasterStacStatsEndpoint),
    ("vector", VectorStatsEndpoint),
    ("vector_stac", VectorStacStatsEndpoint),
)


class ZonalStatsResource(BaseResource):
    """The Zonal Stats service, as ``client.zonal_stats``.

    [`path`][datamermaid.resources.base.BaseResource.path] is the absolute service
    root, so the endpoints bypass the client's ``base_url`` altogether.
    """

    def __init__(self, client: MermaidClient) -> None:
        super().__init__(client)
        self.path = client.zonal_stats_url
        self._endpoints: dict[type[ZonalStatsEndpoint], ZonalStatsEndpoint] = {}

    def _endpoint(self, endpoint_class: type[E]) -> E:
        endpoint = self._endpoints.get(endpoint_class)
        if endpoint is None:
            endpoint = endpoint_class(self)
            self._endpoints[endpoint_class] = endpoint
        return cast("E", endpoint)

    @property
    def raster(self) -> RasterStatsEndpoint:
        """Statistics from a Cloud Optimized GeoTIFF, ``POST .../raster``."""

        return self._endpoint(RasterStatsEndpoint)

    @property
    def raster_stac(self) -> RasterStacStatsEndpoint:
        """Statistics from a raster asset of a STAC Item, ``POST .../raster/stac``."""

        return self._endpoint(RasterStacStatsEndpoint)

    @property
    def vector(self) -> VectorStatsEndpoint:
        """Statistics from a GeoParquet file, ``POST .../vector``."""

        return self._endpoint(VectorStatsEndpoint)

    @property
    def vector_stac(self) -> VectorStacStatsEndpoint:
        """Statistics from a GeoParquet asset of a STAC Item, ``POST .../vector/stac``."""

        return self._endpoint(VectorStacStatsEndpoint)
