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

Many areas of interest go through ``batch``, which answers with a lazy
[`LazyBatch`][datamermaid.batch.LazyBatch] that issues one request per AOI on a
bounded thread pool and labels each result so ``to_df()`` gives one row per AOI:

```python
batch = client.zonal_stats.raster.batch(sites, url="https://example.test/depth.tif")
frame = batch.to_df()  # one wide row per site, keyed by `label` (the site id)
```

Because the requests go to another host, they are sent with ``auth=None`` so the
MERMAID credentials the client holds are never disclosed to it.  Retries,
backoff and error mapping are the client's own.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, TypeVar, cast

from ..batch import DEFAULT_MAX_WORKERS, LazyBatch
from ..models import Site, ZonalStatsResult
from .base import BaseResource

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..client import MermaidClient

__all__ = ["BatchItem", "RasterStatsEndpoint", "ZonalStatsEndpoint", "ZonalStatsResource"]

#: The GeoJSON geometry types the service accepts as an area of interest.
AOI_TYPES = frozenset({"Point", "Polygon"})

#: An endpoint wrapper cached on the resource, for `ZonalStatsResource._endpoint`.
E = TypeVar("E", bound="ZonalStatsEndpoint")


def _geometry_of(aoi: Any) -> Any:
    """Find the GeoJSON geometry inside whatever ``aoi`` is.

    A [`Site`][datamermaid.models.Site] contributes its ``location``; anything
    with a ``__geo_interface__`` (a shapely geometry, a GeoDataFrame row)
    contributes that; a GeoJSON ``Feature`` contributes its ``geometry``.  A
    plain geometry mapping passes through.
    """

    if isinstance(aoi, Site):
        if aoi.location is None:
            raise ValueError(f"site {aoi.id!r} has no location")
        aoi = aoi.location
    elif not isinstance(aoi, Mapping) and hasattr(aoi, "__geo_interface__"):
        aoi = aoi.__geo_interface__
    if isinstance(aoi, Mapping) and aoi.get("type") == "Feature":
        aoi = aoi.get("geometry")
    return aoi


def _to_aoi(aoi: Any, radius: float | None = None) -> dict[str, Any]:
    """Normalise ``aoi`` into the GeoJSON object the service expects.

    ``aoi`` is a GeoJSON ``Point`` or ``Polygon`` mapping, a ``Feature`` holding
    one, a [`Site`][datamermaid.models.Site] or an object with a
    ``__geo_interface__``; ``radius`` (metres) is merged into a Point.  The
    endpoints never look inside an AOI themselves.
    """

    aoi = _geometry_of(aoi)
    if not isinstance(aoi, Mapping):
        raise TypeError(f"aoi must be a GeoJSON mapping, got {type(aoi).__name__}")
    geometry_type = aoi.get("type")
    if geometry_type not in AOI_TYPES:
        raise ValueError(f"aoi type must be one of {sorted(AOI_TYPES)}, got {geometry_type!r}")
    if "coordinates" not in aoi:
        raise ValueError("aoi is missing 'coordinates'")

    geometry = dict(aoi)
    if radius is not None:
        if geometry_type != "Point":
            raise ValueError("radius only applies to a Point aoi")
        if radius < 0:
            raise ValueError("radius must be >= 0")
        geometry["radius"] = radius
    return geometry


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
        aoi: Any,
        *,
        url: str,
        stats: Sequence[str] | None,
        radius: float | None,
        **options: Any,
    ) -> dict[str, Any]:
        """Compose the request body, leaving out every option that is ``None``."""

        body: dict[str, Any] = {"aoi": _to_aoi(aoi, radius), "url": _check_url(url)}
        if stats is not None:
            if isinstance(stats, str):
                raise TypeError("stats must be a sequence of statistic names")
            body["stats"] = [str(stat) for stat in stats]
        for key, value in options.items():
            if value is not None:
                body[key] = value
        return body

    def _post(self, body: dict[str, Any], *, label: Any) -> ZonalStatsResult:
        # `auth=None` switches the client's credentials off for this request:
        # the service is public and lives on another host.
        data = self._client.request_json("POST", self.url, json=body, auth=None)
        return ZonalStatsResult.from_api(data, aoi=body["aoi"], source=body["url"], label=label)

    def _request(
        self,
        aoi: Any,
        *,
        label: Any,
        url: str,
        stats: Sequence[str] | None,
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
        stats: Sequence[str] | None,
        radius: float | None,
        **options: Any,
    ) -> LazyBatch[BatchItem, ZonalStatsResult]:
        """Build the lazy batch behind ``batch``: expand the AOIs, fix the labels, defer the rest.

        The URL and the labels are checked here, before anything runs; each AOI
        is validated when its own request is composed, so a bad one fails at its
        position without spoiling the others.
        """

        _check_url(url)
        expanded = _expand_aois(aois)
        resolved = _resolve_labels(expanded, labels)
        items = [BatchItem(aoi, label) for aoi, label in zip(expanded, resolved, strict=True)]

        def compute(item: BatchItem) -> ZonalStatsResult:
            return self._request(
                item.aoi, label=item.label, url=url, stats=stats, radius=radius, **options
            )

        return LazyBatch(items, compute, max_workers=max_workers, errors=errors)

    def stats(
        self,
        aoi: Any,
        *,
        url: str,
        stats: Sequence[str] | None = None,
        radius: float | None = None,
        label: Any = None,
        **options: Any,
    ) -> ZonalStatsResult:
        """Compute statistics for ``aoi`` against the source at ``url``.

        Args:
            aoi: A GeoJSON ``Point`` or ``Polygon`` mapping, a ``Feature`` holding
                one, a [`Site`][datamermaid.models.Site], or any object with a
                ``__geo_interface__``.
            url: The data source the route reads, e.g. a Cloud Optimized GeoTIFF.
            stats: Statistic names (``mean``, ``count``, ``median``, ...).  When
                omitted the service picks its defaults.
            radius: Buffer around a ``Point``, in metres.
            label: An identifier carried onto the result unchanged.
            **options: Route-specific body fields; a ``None`` value is left out.

        Raises:
            TypeError: If ``aoi`` is not a mapping.
            ValueError: If ``aoi`` is not a Point or Polygon, or an option is
                malformed.
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
        stats: Sequence[str] | None = None,
        radius: float | None = None,
        **options: Any,
    ) -> LazyBatch[BatchItem, ZonalStatsResult]:
        """One statistics request per area of interest, run lazily on a thread pool.

        Nothing is sent until the batch is iterated, indexed or materialised;
        see [`LazyBatch`][datamermaid.batch.LazyBatch].  Each result carries a
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

    def __call__(self, aoi: Any, *, url: str, **kwargs: Any) -> ZonalStatsResult:
        """Same as [`stats`][..stats], so ``client.zonal_stats.raster(aoi, url=...)`` works."""

        return self.stats(aoi, url=url, **kwargs)


class RasterStatsEndpoint(ZonalStatsEndpoint):
    """``POST .../zonal-stats/raster``: statistics from a Cloud Optimized GeoTIFF."""

    route = "raster"

    # The base signature takes ``**options`` so any route can be described; this
    # one names the raster options instead, which mypy reads as a narrowing.
    def stats(  # type: ignore[override]
        self,
        aoi: Any,
        *,
        url: str,
        stats: Sequence[str] | None = None,
        radius: float | None = None,
        bands: Sequence[int] | None = None,
        approx_stats: bool = False,
        label: Any = None,
    ) -> ZonalStatsResult:
        """Compute statistics for ``aoi`` over the raster at ``url``.

        Args:
            aoi: A GeoJSON ``Point`` or ``Polygon`` mapping, a ``Feature``, a
                [`Site`][datamermaid.models.Site], or an object with a
                ``__geo_interface__``.
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
        stats: Sequence[str] | None = None,
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
