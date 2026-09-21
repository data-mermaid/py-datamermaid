"""Areas of interest for the Zonal Stats service.

The service accepts one GeoJSON ``Point`` (optionally buffered by a ``radius`` in
metres) or one ``Polygon`` per request, and answers with one set of area-weighted
statistics.  [`to_aoi`][datamermaid.geometry.to_aoi] turns any reasonable
description of a place into exactly that: a GeoJSON geometry or ``Feature``
mapping, an object with a ``__geo_interface__`` (a shapely geometry, a
GeoDataFrame row, a pystac item), a ``(lon, lat)`` tuple, or a MERMAID
[`Site`][datamermaid.models.Site].

```python
from datamermaid import to_aoi

to_aoi((178.4, -18.1), radius=500)
# {'type': 'Point', 'coordinates': [178.4, -18.1], 'radius': 500.0}

to_aoi(site)               # a Site's location
to_aoi(shapely_polygon)    # anything with a __geo_interface__
```

A ``MultiPolygon`` is rejected rather than split into its parts because the
service returns one set of statistics per request; the caller decides whether to
buffer, dissolve or iterate.  ``LineString``, ``FeatureCollection`` and
``GeometryCollection`` are rejected for the same reason.  The returned dict is
always fresh, holds plain ``list[float]`` coordinates (numpy scalars and tuples
are converted) and serialises with ``json.dumps`` as it is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any, Protocol, TypeAlias, runtime_checkable

from .models import Site

__all__ = ["AOI_TYPES", "GeometryLike", "HasGeoInterface", "to_aoi"]

#: The GeoJSON geometry types the service accepts as an area of interest.
AOI_TYPES = frozenset({"Point", "Polygon"})

#: A GeoJSON position, ``[lon, lat]``.
Position: TypeAlias = list[float]


@runtime_checkable
class HasGeoInterface(Protocol):
    """Anything exposing the `__geo_interface__` protocol.

    Shapely geometries, GeoDataFrame rows, pystac items and MERMAID
    [`Site`][datamermaid.models.Site] and [`Management`][datamermaid.models.Management]
    records all do.  The mapping may be a geometry or a ``Feature`` wrapping one.
    """

    @property
    def __geo_interface__(self) -> Mapping[str, Any]: ...  # pragma: no cover - protocol


#: Anything [`to_aoi`][datamermaid.geometry.to_aoi] accepts.
GeometryLike: TypeAlias = Mapping[str, Any] | HasGeoInterface | tuple[float, float] | Site


def _accepted() -> str:
    return " or ".join(sorted(AOI_TYPES))


def _unwrap(geometry: Any) -> Mapping[str, Any]:
    """Find the GeoJSON mapping inside ``geometry``, whatever it is.

    A ``__geo_interface__`` wins (a [`Site`][datamermaid.models.Site] has one
    returning its ``location``); then a ``(lon, lat)`` tuple becomes a Point;
    then a mapping is used as it is.  Anything else is a ``TypeError``.
    """

    if hasattr(geometry, "__geo_interface__"):
        # A `Site` without a location raises ValueError from the property itself,
        # naming the site; `hasattr` only swallows AttributeError so it surfaces.
        unwrapped = geometry.__geo_interface__
        if not isinstance(unwrapped, Mapping):
            raise TypeError(
                "__geo_interface__ must return a GeoJSON mapping, "
                f"got {type(unwrapped).__name__} from {type(geometry).__name__}"
            )
        return unwrapped
    if isinstance(geometry, tuple):
        return {"type": "Point", "coordinates": _point(geometry)}
    if isinstance(geometry, Mapping):
        return geometry
    raise TypeError(
        "aoi must be a GeoJSON mapping, an object with a __geo_interface__, a (lon, lat) "
        f"tuple or a Site, got {type(geometry).__name__}"
    )


def _geometry_of(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    """Unwrap a ``Feature`` to its ``geometry``; other mappings pass through."""

    if mapping.get("type") != "Feature":
        return mapping
    geometry = mapping.get("geometry")
    if not isinstance(geometry, Mapping):
        raise ValueError("Feature has no geometry")
    return geometry


def _position(value: Any, *, what: str) -> Position:
    """Copy one position into a fresh ``[lon, lat]`` list of plain floats."""

    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{what} must be a [lon, lat] position, got {value!r}")
    if len(value) != 2:
        raise ValueError(
            f"{what} must be a [lon, lat] position with exactly two values, got {len(value)}"
        )
    position: Position = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, Real):
            raise ValueError(f"{what} coordinates must be numbers, got {component!r}")
        position.append(float(component))
    return position


def _point(coordinates: Any) -> Position:
    return _position(coordinates, what="a Point")


def _ring(ring: Any, *, index: int) -> list[Position]:
    what = f"Polygon ring {index}"
    if isinstance(ring, (str, bytes)) or not isinstance(ring, Sequence):
        raise ValueError(f"{what} must be a sequence of positions, got {ring!r}")
    positions = [_position(position, what=what) for position in ring]
    if len(positions) < 4:
        raise ValueError(f"{what} must have at least four positions, got {len(positions)}")
    if positions[0] != positions[-1]:
        raise ValueError(
            f"{what} is not closed: it starts at {positions[0]} and ends at {positions[-1]}"
        )
    return positions


def _polygon(coordinates: Any) -> list[list[Position]]:
    if isinstance(coordinates, (str, bytes)) or not isinstance(coordinates, Sequence):
        raise ValueError(f"Polygon coordinates must be a sequence of rings, got {coordinates!r}")
    if not coordinates:
        raise ValueError("Polygon must have at least one ring")
    return [_ring(ring, index=index) for index, ring in enumerate(coordinates)]


def to_aoi(geometry: GeometryLike, *, radius: float | None = None) -> dict[str, Any]:
    """Normalise ``geometry`` into the GeoJSON ``Point`` or ``Polygon`` the service accepts.

    Args:
        geometry: A GeoJSON ``Point`` or ``Polygon`` mapping, a ``Feature``
            holding one, an object with a ``__geo_interface__`` (which may itself
            return a ``Feature``), a ``(lon, lat)`` tuple, or a
            [`Site`][datamermaid.models.Site] with a ``location``.
        radius: Buffer around a ``Point``, in metres.  Overrides a ``radius``
            already on the input mapping; when omitted, one on the input is kept.

    Returns:
        A new dict with ``type``, ``coordinates`` (plain ``list[float]``
        positions) and, for a buffered Point, ``radius``.  It never shares
        structure with the input and round-trips through ``json.dumps``.

    Raises:
        TypeError: If ``geometry`` is none of the accepted kinds (a string, a
            list, ``None``), or a ``__geo_interface__`` returns a non-mapping.
        ValueError: If the geometry type is not ``Point`` or ``Polygon`` (a
            ``MultiPolygon``, ``LineString``, ``FeatureCollection``, ...), a
            Point is not ``[lon, lat]``, a Polygon ring is unclosed or has fewer
            than four positions, ``radius`` is given for a Polygon or is
            negative, or a [`Site`][datamermaid.models.Site] has no ``location``.

    Example:
        ```python
        to_aoi({"type": "Point", "coordinates": (178.4, -18.1)}, radius=500)
        # {'type': 'Point', 'coordinates': [178.4, -18.1], 'radius': 500.0}
        ```
    """

    mapping = _geometry_of(_unwrap(geometry))
    geometry_type = mapping.get("type")
    if geometry_type not in AOI_TYPES:
        raise ValueError(
            f"aoi geometry type {geometry_type!r} is not supported: "
            f"the Zonal Stats service accepts a {_accepted()}"
        )
    if "coordinates" not in mapping:
        raise ValueError(f"{geometry_type} is missing 'coordinates'")

    aoi: dict[str, Any] = {"type": geometry_type}
    if geometry_type == "Point":
        aoi["coordinates"] = _point(mapping["coordinates"])
    else:
        aoi["coordinates"] = _polygon(mapping["coordinates"])

    if radius is None:
        radius = mapping.get("radius")
    if radius is not None:
        if geometry_type != "Point":
            raise ValueError(f"radius only applies to a Point aoi, not a {geometry_type}")
        if isinstance(radius, bool) or not isinstance(radius, Real):
            raise TypeError(f"radius must be a number of metres, got {radius!r}")
        if radius < 0:
            raise ValueError(f"radius must be >= 0, got {radius!r}")
        aoi["radius"] = float(radius)
    return aoi
