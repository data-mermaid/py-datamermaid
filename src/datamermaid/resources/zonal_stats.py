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

Because the requests go to another host, they are sent with ``auth=None`` so the
MERMAID credentials the client holds are never disclosed to it.  Retries,
backoff and error mapping are the client's own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar, cast

from ..models import ZonalStatsResult
from .base import BaseResource

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..client import MermaidClient

__all__ = ["RasterStatsEndpoint", "ZonalStatsEndpoint", "ZonalStatsResource"]

#: The GeoJSON geometry types the service accepts as an area of interest.
AOI_TYPES = frozenset({"Point", "Polygon"})

#: An endpoint wrapper cached on the resource, for `ZonalStatsResource._endpoint`.
E = TypeVar("E", bound="ZonalStatsEndpoint")


def _to_aoi(aoi: Any, radius: float | None = None) -> dict[str, Any]:
    """Normalise ``aoi`` into the GeoJSON object the service expects.

    Only a GeoJSON-like mapping is accepted for now; ``radius`` (metres) is
    merged into a Point.  The geometry helpers replace this seam later, so the
    endpoints never look inside an AOI themselves.
    """

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

        if not isinstance(url, str) or not url:
            raise ValueError("url must be a non-empty string")
        body: dict[str, Any] = {"aoi": _to_aoi(aoi, radius), "url": url}
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

    def stats(
        self,
        aoi: Mapping[str, Any],
        *,
        url: str,
        stats: Sequence[str] | None = None,
        radius: float | None = None,
        label: Any = None,
        **options: Any,
    ) -> ZonalStatsResult:
        """Compute statistics for ``aoi`` against the source at ``url``.

        Args:
            aoi: A GeoJSON ``Point`` or ``Polygon`` mapping.
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

        body = self._body(aoi, url=url, stats=stats, radius=radius, **options)
        return self._post(body, label=label)

    def __call__(self, aoi: Mapping[str, Any], *, url: str, **kwargs: Any) -> ZonalStatsResult:
        """Same as [`stats`][.stats], so ``client.zonal_stats.raster(aoi, url=...)`` works."""

        return self.stats(aoi, url=url, **kwargs)


class RasterStatsEndpoint(ZonalStatsEndpoint):
    """``POST .../zonal-stats/raster``: statistics from a Cloud Optimized GeoTIFF."""

    route = "raster"

    # The base signature takes ``**options`` so any route can be described; this
    # one names the raster options instead, which mypy reads as a narrowing.
    def stats(  # type: ignore[override]
        self,
        aoi: Mapping[str, Any],
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
            aoi: A GeoJSON ``Point`` or ``Polygon`` mapping.
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

        body = self._body(
            aoi,
            url=url,
            stats=stats,
            radius=radius,
            bands=_bands(bands),
            approx_stats=approx_stats,
        )
        return self._post(body, label=label)


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
