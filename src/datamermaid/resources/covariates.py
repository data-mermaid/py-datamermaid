"""The MERMAID covariates catalog, a STAC API at ``https://mermaid.prescient.earth/stac/``.

Covariates are the environmental and human-pressure datasets that sit next to
MERMAID survey data: daily sea surface temperature and heat stress, reef
habitat maps, market gravity, coastal population and more.  Each dataset is a
STAC collection, and each collection holds one or more items whose ``data``
asset is a Cloud Optimized GeoTIFF or a GeoParquet file.

Like the Zonal Stats service, the catalog is a separate public host, resolved
independently of the client's ``base_url`` (see
[`MermaidClient`][datamermaid.client.MermaidClient]'s ``covariates_url``).
Requests to it carry no MERMAID credentials.

See what is available:

```python
for collection in client.covariates.collections():
    print(collection.id, collection.title)

client.covariates.search_collections("temperature")  # matches id or title
sst = client.covariates.collection("daily_sst")
print(sst.describe())
```

Compute statistics for many sites without choosing a route, an asset or a
geometry column yourself:

```python
batch = sst.zonal_stats(sites, datetime="2026-05", stats=["mean"], radius=500)
frame = batch.to_df()
```

A [`CovariateSearch`][datamermaid.resources.covariates.CovariateSearch] is
also accepted anywhere the zonal stats endpoints take ``search=``.

Values are never rescaled here.  A band's ``scale``, ``offset`` and ``nodata``
are shown as metadata; the service reading the raster applies them.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import threading
from collections.abc import Iterable, Iterator, Mapping, MutableMapping, Sequence
from functools import cached_property
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, overload

from ..batch import DEFAULT_MAX_WORKERS, Batch, BatchFailure, BatchStream
from ..exceptions import MermaidConnectionError
from ..geometry import _geometry_of, _unwrap
from ..models import ZonalStatsResult, parse_datetime
from ..pagination import to_dataframe
from .base import BaseResource, _normalize_id
from .zonal_job import ZonalJob, ZonalSource, ZonalTask, default_asset, source_from_item

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas

    from ..client import MermaidClient
    from .zonal_stats import Stat

__all__ = [
    "CovariateAsset",
    "CovariateCollection",
    "CovariateItem",
    "CovariateSearch",
    "Covariates",
    "DatetimeLike",
]

#: A STAC datetime filter: an RFC 3339 string or interval (``"2026-05-01/2026-05-31"``),
#: a partial date (``"2026"``, ``"2026-05"``, ``"2026-05-01"``), a ``date``, a
#: ``datetime``, or a ``(start, end)`` pair where ``None`` leaves that end open.
DatetimeLike: TypeAlias = (
    str
    | dt.date
    | dt.datetime
    | tuple[str | dt.date | dt.datetime | None, str | dt.date | dt.datetime | None]
)

#: Items requested per search page.
DEFAULT_PAGE_SIZE = 100

_PARTIAL_DATE = re.compile(r"^(?P<year>\d{4})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2}))?)?$")

Kind = Literal["raster", "vector"]


# -- datetime filters --------------------------------------------------------


def _format_instant(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bound(value: str | dt.date | dt.datetime | None, *, end: bool) -> str:
    """One end of an interval, with a partial date widened to the whole period."""

    if value is None:
        return ".."
    if isinstance(value, dt.datetime):
        return _format_instant(value)
    if isinstance(value, dt.date):
        return f"{value.isoformat()}T23:59:59Z" if end else f"{value.isoformat()}T00:00:00Z"
    if not isinstance(value, str):
        raise TypeError(f"datetime bounds must be strings, dates or datetimes, got {value!r}")
    text = value.strip()
    if text in ("", ".."):
        return ".."
    match = _PARTIAL_DATE.match(text)
    if match is None:
        return text  # a full RFC 3339 timestamp, sent as given
    year = int(match["year"])
    month = int(match["month"] or (12 if end else 1))
    last = calendar.monthrange(year, month)[1] if end else 1
    day = int(match["day"]) if match["day"] else last
    moment = dt.date(year, month, day)  # raises ValueError for 2026-13 or 2026-02-30
    return f"{moment.isoformat()}T23:59:59Z" if end else f"{moment.isoformat()}T00:00:00Z"


def stac_datetime(value: DatetimeLike | None) -> str | None:
    """Normalise ``value`` to the RFC 3339 form a STAC ``/search`` POST requires.

    The catalog rejects date-only strings, so ``"2026-05"`` becomes
    ``"2026-05-01T00:00:00Z/2026-05-31T23:59:59Z"``.  A full timestamp is left
    as it is.
    """

    if value is None:
        return None
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError("a datetime interval must be a (start, end) pair")
        return f"{_bound(value[0], end=False)}/{_bound(value[1], end=True)}"
    if isinstance(value, dt.datetime):
        return _format_instant(value)
    if isinstance(value, dt.date):
        return f"{_bound(value, end=False)}/{_bound(value, end=True)}"
    if not isinstance(value, str) or not value.strip():
        raise ValueError("datetime must be a non-empty string, a date, a datetime or a pair")
    if "/" in value:
        start, _, stop = value.partition("/")
        return f"{_bound(start, end=False)}/{_bound(stop, end=True)}"
    if _PARTIAL_DATE.match(value.strip()):
        return f"{_bound(value, end=False)}/{_bound(value, end=True)}"
    return value.strip()


# -- assets and items ----------------------------------------------------------


def _media_kind(media_type: str | None, href: str | None = None) -> Kind | None:
    text = (media_type or "").lower()
    if "tiff" in text:
        return "raster"
    if "parquet" in text:
        return "vector"
    path = (href or "").lower().split("?", 1)[0]
    if path.endswith((".tif", ".tiff")):
        return "raster"
    if path.endswith(".parquet"):
        return "vector"
    return None


def _mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return ()
    return tuple(dict(entry) for entry in value if isinstance(entry, Mapping))


class CovariateAsset:
    """One asset of a STAC item: a file and what the catalog says about it."""

    def __init__(self, key: str, data: Mapping[str, Any]) -> None:
        #: The asset's key in the item, e.g. ``"data"``.
        self.key = key
        #: The asset as the catalog sent it.
        self.raw: Mapping[str, Any] = dict(data)
        #: The file's URL.
        self.href: str | None = data.get("href")
        #: The media type, e.g. ``"application/geoparquet"``.
        self.type: str | None = data.get("type")
        self.title: str | None = data.get("title")
        #: STAC roles, ``("data",)`` for the file statistics are computed from.
        self.roles: tuple[str, ...] = tuple(
            str(role) for role in data.get("roles") or () if isinstance(role, str)
        )
        #: ``raster:bands``: unit, scale, offset, nodata and data type per band.
        self.bands = _mappings(data.get("raster:bands"))
        #: ``classification:classes``: the value and label of each class of a
        #: categorical raster.
        self.classes = _mappings(data.get("classification:classes"))
        #: ``table:columns``: name, type and description of each GeoParquet column.
        self.columns = _mappings(data.get("table:columns"))

    def __repr__(self) -> str:
        return f"CovariateAsset(key={self.key!r}, type={self.type!r})"

    @property
    def kind(self) -> Kind | None:
        """``"raster"`` for a GeoTIFF, ``"vector"`` for GeoParquet, else ``None``."""

        return _media_kind(self.type, self.href)

    @property
    def geometry_column(self) -> str | None:
        """The GeoParquet column typed ``geometry``, from ``table:columns``."""

        for column in self.columns:
            if column.get("type") == "geometry":
                return str(column.get("name"))
        return None

    @property
    def numeric_columns(self) -> list[str]:
        """The GeoParquet columns holding numbers, the ones worth summarising."""

        numeric = ("int", "uint", "float", "double", "decimal")
        return [
            str(column["name"])
            for column in self.columns
            if isinstance(column.get("name"), str)
            and str(column.get("type", "")).lower().startswith(numeric)
        ]


class CovariateItem:
    """One STAC item: a single date or version of a covariate dataset.

    [`to_dict`][.to_dict] returns the item as the catalog sent it, so a list of
    items can be passed as ``sources=`` to any zonal stats endpoint.
    """

    def __init__(self, data: Mapping[str, Any]) -> None:
        if not isinstance(data, Mapping):
            raise TypeError(f"expected a STAC item object, got {type(data).__name__}")
        self._data = dict(data)
        self.id: str | None = data.get("id")
        self.collection: str | None = data.get("collection")
        properties = data.get("properties")
        self.properties: Mapping[str, Any] = (
            dict(properties) if isinstance(properties, Mapping) else {}
        )
        assets = data.get("assets")
        self.assets: dict[str, CovariateAsset] = (
            {
                str(key): CovariateAsset(str(key), value)
                for key, value in assets.items()
                if isinstance(value, Mapping)
            }
            if isinstance(assets, Mapping)
            else {}
        )

    def __repr__(self) -> str:
        return f"CovariateItem(id={self.id!r}, collection={self.collection!r})"

    @property
    def datetime(self) -> dt.datetime | None:
        """The item's ``datetime``, or its ``start_datetime`` when that is null."""

        value = self.properties.get("datetime") or self.properties.get("start_datetime")
        try:
            return parse_datetime(value)
        except (TypeError, ValueError):
            return None

    @property
    def data_asset(self) -> CovariateAsset | None:
        """The asset with the ``data`` role, else the first asset."""

        key = default_asset(self._data.get("assets") or {})
        return self.assets.get(key) if key is not None else None

    def to_dict(self) -> dict[str, Any]:
        """The item as the catalog sent it."""

        return dict(self._data)


# -- searching -----------------------------------------------------------------


class CovariateSearch:
    """A lazy STAC item search: nothing is fetched until it is iterated.

    Follows the catalog's ``next`` links page by page.  It has an
    ``items_as_dicts()`` method, so it can be passed as ``search=`` to any
    zonal stats endpoint, the same as a pystac-client ``ItemSearch``.
    """

    def __init__(
        self,
        resource: Covariates,
        body: Mapping[str, Any],
        *,
        max_items: int | None = None,
    ) -> None:
        if max_items is not None and (
            isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1
        ):
            raise ValueError("max_items must be a positive integer")
        self._resource = resource
        #: The JSON body POSTed to ``/search``.
        self.body: dict[str, Any] = dict(body)
        self.max_items = max_items

    def __repr__(self) -> str:
        return f"CovariateSearch(body={self.body!r}, max_items={self.max_items!r})"

    @property
    def url(self) -> str:
        return f"{self._resource.path}search"

    def _page(self, method: str, url: str, body: Mapping[str, Any] | None) -> Mapping[str, Any]:
        client = self._resource._client
        if method == "GET":
            data = client.request_json("GET", url, public=True)
        else:
            data = client.request_json("POST", url, json=dict(body or {}), public=True)
        if not isinstance(data, Mapping) or not isinstance(data.get("features"), list):
            raise MermaidConnectionError(f"{method} {url} did not return a STAC FeatureCollection")
        return data

    def items_as_dicts(self) -> Iterator[dict[str, Any]]:
        """Every matching item as a plain dict, following pagination."""

        method, url, body = "POST", self.url, dict(self.body)
        seen = 0
        while True:
            page = self._page(method, url, body)
            for feature in page["features"]:
                if not isinstance(feature, Mapping):
                    raise MermaidConnectionError(f"{url} returned a feature that is not an object")
                yield dict(feature)
                seen += 1
                if self.max_items is not None and seen >= self.max_items:
                    return
            link = next(
                (
                    entry
                    for entry in page.get("links") or ()
                    if isinstance(entry, Mapping) and entry.get("rel") == "next"
                ),
                None,
            )
            if link is None or not page["features"] or not isinstance(link.get("href"), str):
                return
            url = link["href"]
            method = str(link.get("method", "GET")).upper()
            next_body = link.get("body")
            if method == "POST":
                if not isinstance(next_body, Mapping):
                    body = dict(body)
                elif link.get("merge"):
                    body = {**body, **next_body}
                else:
                    body = dict(next_body)

    def items(self) -> Iterator[CovariateItem]:
        """Every matching item, following pagination."""

        for data in self.items_as_dicts():
            yield CovariateItem(data)

    def __iter__(self) -> Iterator[CovariateItem]:
        return self.items()

    def count(self) -> int | None:
        """How many items match, from one small request, or ``None`` if not reported.

        ``max_items`` caps the answer, since that is how many would be read.
        """

        page = self._page("POST", self.url, {**self.body, "limit": 1})
        matched = page.get("numberMatched")
        if matched is None:
            context = page.get("context")
            matched = context.get("matched") if isinstance(context, Mapping) else None
        if not isinstance(matched, int) or isinstance(matched, bool):
            return None
        return min(matched, self.max_items) if self.max_items is not None else matched


def _search_body(
    collections: Sequence[str] | None,
    *,
    datetime: DatetimeLike | None,
    bbox: Sequence[float] | None,
    intersects: Any,
    ids: Sequence[str] | None,
    filter: Mapping[str, Any] | None,
    sortby: Sequence[Mapping[str, str]] | None,
    limit: int,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    body: dict[str, Any] = {"limit": limit}
    if collections:
        body["collections"] = list(collections)
    stamp = stac_datetime(datetime)
    if stamp is not None:
        body["datetime"] = stamp
    if bbox is not None and intersects is not None:
        raise ValueError("pass bbox or intersects, not both")
    if bbox is not None:
        if isinstance(bbox, (str, bytes)) or len(bbox) not in (4, 6):
            raise ValueError("bbox must be (west, south, east, north)")
        body["bbox"] = [float(value) for value in bbox]
    if intersects is not None:
        body["intersects"] = dict(_geometry_of(_unwrap(intersects)))
    if ids is not None:
        if isinstance(ids, (str, bytes)):
            raise TypeError("ids must be a sequence of item ids")
        body["ids"] = list(ids)
    if filter is not None:
        body["filter"] = dict(filter)
        body["filter-lang"] = "cql2-json"
    if sortby is not None:
        body["sortby"] = [dict(entry) for entry in sortby]
    return body


# -- collections ---------------------------------------------------------------


def _collection_id(value: str | CovariateCollection) -> str:
    if isinstance(value, CovariateCollection):
        return value.id
    return _normalize_id(value, name="collection id")


class CovariateCollection:
    """One covariate dataset: a STAC collection and what it holds.

    The collection document says what the dataset is.  The details of its data
    file (band units and scaling, class labels, GeoParquet columns) are only on
    its items, so [`sample_item`][.sample_item] fetches one item the first time
    any of them is asked for.
    """

    def __init__(self, resource: Covariates, data: Mapping[str, Any]) -> None:
        if not isinstance(data, Mapping) or not isinstance(data.get("id"), str):
            raise TypeError("expected a STAC collection object with an id")
        self._resource = resource
        #: The collection as the catalog sent it.
        self.raw: Mapping[str, Any] = dict(data)
        self.id: str = data["id"]
        self.title: str | None = data.get("title")
        self.description: str | None = data.get("description")
        self.keywords: tuple[str, ...] = tuple(
            str(word) for word in data.get("keywords") or () if isinstance(word, str)
        )
        self.license: str | None = data.get("license")
        self.providers = _mappings(data.get("providers"))
        self.links = _mappings(data.get("links"))

    def __repr__(self) -> str:
        return f"CovariateCollection(id={self.id!r}, title={self.title!r})"

    # -- what the collection says -------------------------------------------

    @property
    def temporal_extent(self) -> tuple[dt.datetime | None, dt.datetime | None]:
        """The first and last moments covered, ``None`` for an open end."""

        try:
            interval = self.raw["extent"]["temporal"]["interval"][0]
            return parse_datetime(interval[0]), parse_datetime(interval[1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None, None

    @property
    def bbox(self) -> tuple[float, ...] | None:
        """The overall spatial extent, ``(west, south, east, north)``."""

        try:
            return tuple(float(value) for value in self.raw["extent"]["spatial"]["bbox"][0])
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    @property
    def citation(self) -> str | None:
        """The ``cite-as`` link, else a DOI URL from ``sci:doi``."""

        for link in self.links:
            if link.get("rel") == "cite-as" and isinstance(link.get("href"), str):
                return str(link["href"])
        doi = self.raw.get("sci:doi")
        return f"https://doi.org/{doi}" if isinstance(doi, str) else None

    def _asset_hints(self) -> Mapping[str, Any]:
        """Asset descriptions from ``item_assets`` or ``summaries.assets``, if any."""

        for hints in (self.raw.get("item_assets"), (self.raw.get("summaries") or {}).get("assets")):
            if isinstance(hints, Mapping) and hints:
                return hints
        return {}

    @property
    def kind(self) -> Kind | None:
        """``"raster"`` or ``"vector"``: which zonal stats route reads this dataset.

        Read from the collection's asset descriptions when it has them, so
        listing kinds needs no extra request; otherwise from
        [`data_asset`][..data_asset].
        """

        hints = self._asset_hints()
        key = default_asset(hints)
        if key is not None and isinstance(hints[key], Mapping):
            kind = _media_kind(hints[key].get("type"))
            if kind is not None:
                return kind
        asset = self.data_asset
        return asset.kind if asset is not None else None

    # -- what its items say -------------------------------------------------

    @cached_property
    def sample_item(self) -> CovariateItem | None:
        """The collection's first item, fetched once, or ``None`` if it has none."""

        for item in self.search(max_items=1, limit=1):
            return item
        return None

    @property
    def data_asset(self) -> CovariateAsset | None:
        """The sample item's asset with the ``data`` role."""

        item = self.sample_item
        return item.data_asset if item is not None else None

    @property
    def bands(self) -> tuple[Mapping[str, Any], ...]:
        """``raster:bands`` of the data asset: unit, scale, offset, nodata per band."""

        asset = self.data_asset
        return asset.bands if asset is not None else ()

    @property
    def classes(self) -> dict[Any, str]:
        """Class value to label, for a categorical raster such as habitat or land cover."""

        asset = self.data_asset
        classes = asset.classes if asset is not None else ()
        if not classes:
            summary = (self.raw.get("summaries") or {}).get("label:classes")
            classes = _mappings(summary)
        return {
            entry.get("value"): str(entry.get("description") or entry.get("label") or "")
            for entry in classes
        }

    @property
    def columns(self) -> tuple[Mapping[str, Any], ...]:
        """``table:columns`` of a GeoParquet data asset: name, type, description."""

        asset = self.data_asset
        return asset.columns if asset is not None else ()

    # -- export ---------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """One summary row: id, title, kind, time range, keywords and licence.

        Built from the collection document alone, so a table of every dataset
        costs one request.
        """

        start, end = self.temporal_extent
        hints = self._asset_hints()
        key = default_asset(hints)
        kind = (
            _media_kind(hints[key].get("type"))
            if key is not None and isinstance(hints[key], Mapping)
            else None
        )
        return {
            "id": self.id,
            "title": self.title,
            "kind": kind,
            "start_datetime": start,
            "end_datetime": end,
            "keywords": list(self.keywords),
            "license": self.license,
        }

    def describe(self) -> str:
        """A readable summary of the dataset, its data file and its provenance.

        Fetches one item (see [`sample_item`][..sample_item]) and counts the
        items.
        """

        start, end = self.temporal_extent
        lines = [f"{self.title or self.id} ({self.id})"]
        lines.append(f"  kind:      {self.kind or 'unknown'}")
        span = f"{start:%Y-%m-%d}" if start else "open"
        span += f" to {end:%Y-%m-%d}" if end else " to open"
        lines.append(f"  time:      {span}")
        count = self.search().count()
        if count is not None:
            lines.append(f"  items:     {count}")
        asset = self.data_asset
        if asset is not None:
            lines.append(f"  asset:     {asset.key!r} ({asset.type})")
        for index, band in enumerate(self.bands, start=1):
            details = ", ".join(
                f"{name}={band[name]}"
                for name in ("unit", "scale", "offset", "nodata", "data_type")
                if band.get(name) is not None
            )
            lines.append(f"  band_{index}:    {details}")
        classes = self.classes
        if classes:
            preview = ", ".join(f"{value}={label}" for value, label in list(classes.items())[:6])
            more = f", ... ({len(classes)} classes)" if len(classes) > 6 else ""
            lines.append(f"  classes:   {preview}{more}")
        for column in self.columns:
            if column.get("type") == "geometry":
                continue
            text = f"  column:    {column.get('name')} ({column.get('type')})"
            if column.get("description"):
                text += f": {column['description']}"
            lines.append(text)
        if self.keywords:
            lines.append(f"  keywords:  {', '.join(self.keywords)}")
        if self.license:
            lines.append(f"  license:   {self.license}")
        providers = [str(entry.get("name")) for entry in self.providers if entry.get("name")]
        if providers:
            lines.append(f"  providers: {', '.join(providers)}")
        if self.citation:
            lines.append(f"  cite:      {self.citation}")
        return "\n".join(lines)

    # -- items and statistics -------------------------------------------------

    def search(
        self,
        *,
        datetime: DatetimeLike | None = None,
        bbox: Sequence[float] | None = None,
        intersects: Any = None,
        ids: Sequence[str] | None = None,
        filter: Mapping[str, Any] | None = None,
        sortby: Sequence[Mapping[str, str]] | None = None,
        max_items: int | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> CovariateSearch:
        """Search this collection's items.

        See [`Covariates.search`][datamermaid.resources.covariates.Covariates.search].
        """

        return self._resource.search(
            self.id,
            datetime=datetime,
            bbox=bbox,
            intersects=intersects,
            ids=ids,
            filter=filter,
            sortby=sortby,
            max_items=max_items,
            limit=limit,
        )

    def _zonal_plan(
        self,
        *,
        datetime: DatetimeLike | None,
        bbox: Sequence[float] | None,
        ids: Sequence[str] | None,
        max_items: int | None,
        asset: str | None,
        columns: Sequence[str] | None,
        options: dict[str, Any],
    ) -> tuple[Any, list[ZonalSource], dict[str, Any]]:
        """Pick the route, resolve every matching item's asset, fill vector defaults."""

        search = self.search(datetime=datetime, bbox=bbox, ids=ids, max_items=max_items)
        items = [CovariateItem(data) for data in search.items_as_dicts()]
        if not items:
            start, end = self.temporal_extent
            raise ValueError(
                f"no items in {self.id!r} match the search; the collection covers {start} to {end}"
            )
        first = items[0]
        chosen = first.assets.get(asset) if asset is not None else first.data_asset
        if chosen is None:
            raise ValueError(f"collection {self.id!r} has no asset {asset!r}")
        kind = chosen.kind
        zonal = self._resource._client.zonal_stats
        if kind == "raster":
            endpoint: Any = zonal.raster
        elif kind == "vector":
            endpoint = zonal.vector
            if columns is None:
                columns = chosen.numeric_columns
                if not columns:
                    raise ValueError(
                        f"collection {self.id!r} lists no numeric columns; pass columns="
                    )
            options["columns"] = columns
            if options.get("geometry_column") is None and chosen.geometry_column:
                options["geometry_column"] = chosen.geometry_column
        else:
            raise ValueError(
                f"asset {chosen.key!r} of collection {self.id!r} is {chosen.type!r}, "
                "neither a GeoTIFF nor GeoParquet"
            )
        if kind == "raster" and columns is not None:
            raise TypeError(f"collection {self.id!r} is a raster; columns= is for vectors")
        sources = [source_from_item(item.to_dict(), chosen.key) for item in items]
        return endpoint, sources, options

    def prepare_zonal_stats(
        self,
        aois: Any,
        *,
        datetime: DatetimeLike | None = None,
        bbox: Sequence[float] | None = None,
        ids: Sequence[str] | None = None,
        max_items: int | None = None,
        asset: str | None = None,
        columns: Sequence[str] | None = None,
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        **options: Any,
    ) -> ZonalJob:
        """Resolve the matching items and return a [`ZonalJob`][datamermaid.ZonalJob] to run.

        Takes the same arguments as [`zonal_stats`][..zonal_stats] and sends no
        statistics requests, so ``job.request_count`` can be checked first.
        """

        endpoint, sources, options = self._zonal_plan(
            datetime=datetime,
            bbox=bbox,
            ids=ids,
            max_items=max_items,
            asset=asset,
            columns=columns,
            options=options,
        )
        job: ZonalJob = endpoint.prepare(
            aois, sources=sources, labels=labels, stats=stats, radius=radius, **options
        )
        return job

    @overload
    def zonal_stats(
        self,
        aois: Any,
        *,
        datetime: DatetimeLike | None = None,
        bbox: Sequence[float] | None = None,
        ids: Sequence[str] | None = None,
        max_items: int | None = None,
        asset: str | None = None,
        columns: Sequence[str] | None = None,
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise"] = "raise",
        stream: Literal[False] = False,
        **options: Any,
    ) -> Batch[ZonalTask, ZonalStatsResult]: ...

    @overload
    def zonal_stats(
        self,
        aois: Any,
        *,
        datetime: DatetimeLike | None = None,
        bbox: Sequence[float] | None = None,
        ids: Sequence[str] | None = None,
        max_items: int | None = None,
        asset: str | None = None,
        columns: Sequence[str] | None = None,
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise"] = "raise",
        stream: Literal[True],
        **options: Any,
    ) -> BatchStream[ZonalTask, ZonalStatsResult]: ...

    @overload
    def zonal_stats(
        self,
        aois: Any,
        *,
        datetime: DatetimeLike | None = None,
        bbox: Sequence[float] | None = None,
        ids: Sequence[str] | None = None,
        max_items: int | None = None,
        asset: str | None = None,
        columns: Sequence[str] | None = None,
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise", "return"] = "raise",
        stream: Literal[False] = False,
        **options: Any,
    ) -> Batch[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]: ...

    @overload
    def zonal_stats(
        self,
        aois: Any,
        *,
        datetime: DatetimeLike | None = None,
        bbox: Sequence[float] | None = None,
        ids: Sequence[str] | None = None,
        max_items: int | None = None,
        asset: str | None = None,
        columns: Sequence[str] | None = None,
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise", "return"] = "raise",
        stream: Literal[True],
        **options: Any,
    ) -> BatchStream[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]: ...

    def zonal_stats(
        self,
        aois: Any,
        *,
        datetime: DatetimeLike | None = None,
        bbox: Sequence[float] | None = None,
        ids: Sequence[str] | None = None,
        max_items: int | None = None,
        asset: str | None = None,
        columns: Sequence[str] | None = None,
        labels: Iterable[Any] | None = None,
        stats: Iterable[Stat | str] | None = None,
        radius: float | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cache: MutableMapping[str, Any] | bool | None = True,
        errors: Literal["raise", "return"] = "raise",
        stream: bool = False,
        **options: Any,
    ) -> (
        Batch[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
        | BatchStream[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
    ):
        """Statistics for every AOI against every matching item of this dataset.

        Chooses the route from the data asset's type (``raster`` for a GeoTIFF,
        ``vector`` for GeoParquet), picks the asset with the ``data`` role, and
        for GeoParquet fills ``geometry_column`` and, when ``columns`` is left
        out, every numeric column from the item's ``table:columns``.  Each
        result carries the item's id and datetime in ``result.stac``.

        Args:
            aois: One area of interest or many, as the zonal stats ``batch``
                methods take them: sites, GeoJSON, a GeoDataFrame.
            datetime: Which items to use; see
                [`DatetimeLike`][datamermaid.resources.covariates.DatetimeLike].
                Leave it out for a dataset with a single item.
            bbox: Only items intersecting ``(west, south, east, north)``.
            ids: Only these item ids.
            max_items: Read at most this many items.
            asset: The asset key, when the ``data`` role is not the one wanted.
            columns: GeoParquet columns to summarise.  Vector datasets only.
            labels: One identifier per AOI; defaults as for
                [`BaseZonalStats.batch`][datamermaid.resources.zonal_stats.BaseZonalStats.batch].
            stats: Statistic names; the service picks defaults when omitted.
            radius: Buffer around each ``Point``, in metres.
            max_workers: Maximum concurrent requests.
            cache: Response cache, as for the zonal stats ``batch`` methods.
            errors: ``"return"`` keeps failures as ``BatchFailure`` results.
            stream: ``True`` returns a ``BatchStream`` with bounded memory.
            **options: Other route options, e.g. ``bands`` and ``approx_stats``
                for rasters or ``weighting_method`` for vectors.

        Raises:
            ValueError: If no item matches, the asset is neither GeoTIFF nor
                GeoParquet, or a vector dataset has no numeric column to default to.
        """

        endpoint, sources, options = self._zonal_plan(
            datetime=datetime,
            bbox=bbox,
            ids=ids,
            max_items=max_items,
            asset=asset,
            columns=columns,
            options=options,
        )
        result: (
            Batch[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
            | BatchStream[ZonalTask, ZonalStatsResult | BatchFailure[ZonalTask]]
        ) = endpoint.batch(
            aois,
            sources=sources,
            labels=labels,
            stats=stats,
            radius=radius,
            max_workers=max_workers,
            cache=cache,
            errors=errors,
            stream=stream,
            **options,
        )
        return result


# -- the catalog ---------------------------------------------------------------


class Covariates(BaseResource):
    """The covariates catalog, as ``client.covariates``.

    [`path`][datamermaid.resources.base.BaseResource.path] is the absolute STAC
    API root.  The list of collections is fetched once per client and kept;
    pass ``refresh=True`` to fetch it again.
    """

    def __init__(self, client: MermaidClient) -> None:
        super().__init__(client)
        self.path = client.covariates_url
        self._lock = threading.Lock()
        self._collections: list[CovariateCollection] | None = None

    def collections(self, *, refresh: bool = False) -> list[CovariateCollection]:
        """Every dataset in the catalog, in the catalog's order."""

        with self._lock:
            if self._collections is None or refresh:
                self._collections = self._fetch_collections()
            return list(self._collections)

    def _fetch_collections(self) -> list[CovariateCollection]:
        url: str | None = f"{self.path}collections"
        found: list[CovariateCollection] = []
        while url is not None:
            data = self._client.request_json("GET", url, public=True)
            entries = data.get("collections") if isinstance(data, Mapping) else None
            if not isinstance(entries, list):
                raise MermaidConnectionError(f"GET {url} did not return a list of collections")
            found.extend(
                self._decode_response(entry, lambda raw: CovariateCollection(self, raw), url)
                for entry in entries
            )
            url = next(
                (
                    link["href"]
                    for link in data.get("links") or ()
                    if isinstance(link, Mapping)
                    and link.get("rel") == "next"
                    and isinstance(link.get("href"), str)
                ),
                None,
            )
            if not entries:
                break
        return found

    def collection(self, collection_id: str) -> CovariateCollection:
        """One dataset by its exact id, e.g. ``"daily_sst"``.

        Raises:
            NotFoundError: If the catalog has no such collection.
        """

        wanted = _collection_id(collection_id)
        with self._lock:
            cached = self._collections
        for collection in cached or ():
            if collection.id == wanted:
                return collection
        url = self._url("collections", wanted).rstrip("/")
        data = self._client.request_json("GET", url, public=True)
        return self._decode_response(data, lambda raw: CovariateCollection(self, raw), url)

    def search_collections(self, query: str) -> list[CovariateCollection]:
        """Datasets whose id or title contains ``query``, ignoring case.

        ``search_collections("sst")`` finds ``daily_sst``, and
        ``search_collections("bleaching")`` finds the heat stress products by title.
        """

        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        needle = query.strip().casefold()
        return [
            collection
            for collection in self.collections()
            if needle in collection.id.casefold() or needle in (collection.title or "").casefold()
        ]

    def to_df(self, query: str | None = None, **kwargs: Any) -> pandas.DataFrame:
        """A table of datasets, one row per collection (optionally filtered by ``query``)."""

        chosen = self.collections() if query is None else self.search_collections(query)
        return to_dataframe([collection.to_dict() for collection in chosen], **kwargs)

    def search(
        self,
        collections: str | CovariateCollection | Sequence[str | CovariateCollection] | None = None,
        *,
        datetime: DatetimeLike | None = None,
        bbox: Sequence[float] | None = None,
        intersects: Any = None,
        ids: Sequence[str] | None = None,
        filter: Mapping[str, Any] | None = None,
        sortby: Sequence[Mapping[str, str]] | None = None,
        max_items: int | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> CovariateSearch:
        """A lazy item search, usable as ``search=`` on any zonal stats endpoint.

        Args:
            collections: A collection id or
                [`CovariateCollection`][datamermaid.resources.covariates.CovariateCollection],
                or several.
            datetime: A date, a partial date such as ``"2026-05"``, an RFC 3339
                interval or a ``(start, end)`` pair.  Date-only values are
                widened to whole days, which the catalog requires.
            bbox: ``(west, south, east, north)``.
            intersects: A geometry the items must intersect: GeoJSON, a
                ``__geo_interface__`` object or a [`Site`][datamermaid.models.Site].
            ids: Item ids.
            filter: A CQL2 JSON filter.
            sortby: STAC sort fields, e.g. ``[{"field": "datetime", "direction": "asc"}]``.
            max_items: Stop after this many items.
            limit: Items per page.
        """

        if collections is None:
            names: list[str] | None = None
        elif isinstance(collections, (str, CovariateCollection)):
            names = [_collection_id(collections)]
        else:
            names = [_collection_id(entry) for entry in collections]
        body = _search_body(
            names,
            datetime=datetime,
            bbox=bbox,
            intersects=intersects,
            ids=ids,
            filter=filter,
            sortby=sortby,
            limit=limit,
        )
        return CovariateSearch(self, body, max_items=max_items)
