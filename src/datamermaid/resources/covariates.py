"""The MERMAID covariates catalog, a STAC API at ``https://mermaid.prescient.earth/stac/``.

Covariates are the environmental and human-pressure datasets that sit next to
MERMAID survey data: daily sea surface temperature and heat stress, reef
habitat maps, market gravity, coastal population and more.  Each dataset is a
STAC collection, and each collection holds one or more items whose ``data``
asset is a Cloud Optimized GeoTIFF or a GeoParquet file.

The catalog is read with [pystac-client](https://pystac-client.readthedocs.io),
installed by the ``covariates`` extra (``pip install 'datamermaid[covariates]'``).
Collections wrap a ``pystac.Collection`` and searches are plain pystac-client
``ItemSearch`` objects, so everything pystac offers is still there.  The catalog
is a public host, resolved independently of the client's ``base_url`` (see
[`MermaidClient`][datamermaid.client.MermaidClient]'s ``covariates_url``).
pystac-client sends its requests through the client, so they use the client's
timeout, retries and throttle, raise
[`MermaidError`][datamermaid.exceptions.MermaidError] subclasses, and carry no
MERMAID credentials.

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

Values are never rescaled here.  A band's ``scale``, ``offset`` and ``nodata``
are shown as metadata; the service reading the raster applies them.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from functools import cached_property
from typing import TYPE_CHECKING, Any, Literal, overload

from ..batch import DEFAULT_MAX_WORKERS, Batch, BatchFailure, BatchStream
from ..geometry import _geometry_of, _unwrap
from ..models import ZonalStatsResult
from ..pagination import to_dataframe
from .base import BaseResource
from .zonal_job import ZonalJob, ZonalSource, ZonalTask, default_asset, source_from_item
from .zonal_stats import _expand_aois

if TYPE_CHECKING:  # pragma: no cover - typing only
    import datetime as dt

    import pandas
    import pystac
    from pystac_client import Client, ItemSearch
    from pystac_client.item_search import DatetimeLike

    from ..client import MermaidClient
    from .zonal_stats import Stat

__all__ = ["CovariateCollection", "Covariates"]

#: Most statistics requests ``CovariateCollection.zonal_stats`` sends unless told otherwise.
DEFAULT_MAX_REQUESTS = 10_000

PYSTAC_INSTALL_HINT = (
    "client.covariates needs pystac-client; install it with `pip install 'datamermaid[covariates]'`"
)

Kind = Literal["raster", "vector"]


def _require_pystac_client() -> Any:
    try:
        import pystac_client
    except ImportError as exc:  # pragma: no cover - exercised with pystac-client absent
        raise ImportError(PYSTAC_INSTALL_HINT) from exc
    return pystac_client


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


def _data_asset_key(item: pystac.Item, key: str | None = None) -> str | None:
    """``key`` if the item has it, else the asset with the ``data`` role, else the first."""

    if key is not None:
        return key if key in item.assets else None
    return default_asset({name: asset.to_dict() for name, asset in item.assets.items()})


def _geometry_column(columns: Sequence[Mapping[str, Any]]) -> str | None:
    for column in columns:
        if column.get("type") == "geometry":
            return str(column.get("name"))
    return None


def _numeric_columns(columns: Sequence[Mapping[str, Any]]) -> list[str]:
    numeric = ("int", "uint", "float", "double", "decimal")
    return [
        str(column["name"])
        for column in columns
        if isinstance(column.get("name"), str)
        and str(column.get("type", "")).lower().startswith(numeric)
    ]


def _mappings(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return []
    return [dict(entry) for entry in value if isinstance(entry, Mapping)]


class CovariateCollection:
    """One covariate dataset: a ``pystac.Collection`` and what it holds.

    Attributes of the underlying [`stac`][.stac] collection (``title``,
    ``description``, ``keywords``, ``license``, ``providers``, ``extent``,
    ``summaries``, ...) read straight through.  The details of its data file
    (band units and scaling, class labels, GeoParquet columns) are only on its
    items, so [`sample_item`][.sample_item] fetches one item the first time any
    of them is asked for.
    """

    def __init__(self, resource: Covariates, collection: pystac.Collection) -> None:
        self._resource = resource
        #: The collection as pystac parsed it.
        self.stac = collection

    def __getattr__(self, name: str) -> Any:
        # Only reached for names this class does not define.
        if name.startswith("_") or name == "stac":
            raise AttributeError(name)
        return getattr(self.stac, name)

    def __repr__(self) -> str:
        return f"CovariateCollection(id={self.id!r}, title={self.stac.title!r})"

    @property
    def id(self) -> str:
        """The collection id, e.g. ``"daily_sst"``."""

        return self.stac.id

    # -- what the collection says -------------------------------------------

    @property
    def temporal_extent(self) -> tuple[dt.datetime | None, dt.datetime | None]:
        """The first and last moments covered, ``None`` for an open end."""

        start, end = self.stac.extent.temporal.intervals[0]
        return start, end

    @property
    def citation(self) -> str | None:
        """The ``cite-as`` link, else a DOI URL from ``sci:doi``."""

        link = self.stac.get_single_link("cite-as")
        if link is not None:
            return link.href
        doi = self.stac.extra_fields.get("sci:doi")
        return f"https://doi.org/{doi}" if isinstance(doi, str) else None

    def _hinted_kind(self) -> Kind | None:
        """The kind from ``item_assets`` or ``summaries.assets``, without a request."""

        raw = self.stac.to_dict(include_self_link=False, transform_hrefs=False)
        for hints in (raw.get("item_assets"), (raw.get("summaries") or {}).get("assets")):
            if isinstance(hints, Mapping) and hints:
                key = default_asset(hints)
                if key is not None and isinstance(hints[key], Mapping):
                    return _media_kind(hints[key].get("type"))
        return None

    @property
    def kind(self) -> Kind | None:
        """``"raster"`` or ``"vector"``: which zonal stats route reads this dataset.

        Read from the collection's asset descriptions when it has them, so
        listing kinds needs no extra request; otherwise from
        [`data_asset`][..data_asset].
        """

        kind = self._hinted_kind()
        if kind is not None:
            return kind
        asset = self.data_asset
        return _media_kind(asset.media_type, asset.href) if asset is not None else None

    # -- what its items say -------------------------------------------------

    @cached_property
    def sample_item(self) -> pystac.Item | None:
        """The collection's first item, fetched once, or ``None`` if it has none."""

        return next(self.search(max_items=1).items(), None)

    @property
    def data_asset(self) -> pystac.Asset | None:
        """The sample item's asset with the ``data`` role."""

        item = self.sample_item
        key = _data_asset_key(item) if item is not None else None
        return item.assets[key] if item is not None and key is not None else None

    def _asset_field(self, name: str) -> list[dict[str, Any]]:
        asset = self.data_asset
        return _mappings(asset.extra_fields.get(name)) if asset is not None else []

    @property
    def bands(self) -> list[dict[str, Any]]:
        """``raster:bands`` of the data asset: unit, scale, offset, nodata per band."""

        return self._asset_field("raster:bands")

    @property
    def classes(self) -> dict[Any, str]:
        """Class value to label, for a categorical raster such as habitat or land cover."""

        classes = self._asset_field("classification:classes") or _mappings(
            self.stac.summaries.get_list("label:classes")
        )
        return {
            entry.get("value"): str(entry.get("description") or entry.get("label") or "")
            for entry in classes
        }

    @property
    def columns(self) -> list[dict[str, Any]]:
        """``table:columns`` of a GeoParquet data asset: name, type, description."""

        return self._asset_field("table:columns")

    # -- export ---------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """One summary row: id, title, kind, time range, keywords and licence.

        Built from the collection document alone, so a table of every dataset
        costs one request.
        """

        start, end = self.temporal_extent
        return {
            "id": self.id,
            "title": self.stac.title,
            "kind": self._hinted_kind(),
            "start_datetime": start,
            "end_datetime": end,
            "keywords": list(self.stac.keywords or ()),
            "license": self.stac.license,
        }

    def describe(self) -> str:
        """A readable summary of the dataset, its data file and its provenance.

        Fetches one item (see [`sample_item`][..sample_item]) and counts the
        items.
        """

        start, end = self.temporal_extent
        lines = [f"{self.stac.title or self.id} ({self.id})"]
        lines.append(f"  kind:      {self.kind or 'unknown'}")
        span = f"{start:%Y-%m-%d}" if start else "open"
        span += f" to {end:%Y-%m-%d}" if end else " to open"
        lines.append(f"  time:      {span}")
        lines.append(f"  items:     {self.search().matched()}")
        item = self.sample_item
        key = _data_asset_key(item) if item is not None else None
        if item is not None and key is not None:
            lines.append(f"  asset:     {key!r} ({item.assets[key].media_type})")
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
        if self.stac.keywords:
            lines.append(f"  keywords:  {', '.join(self.stac.keywords)}")
        if self.stac.license:
            lines.append(f"  license:   {self.stac.license}")
        providers = [provider.name for provider in self.stac.providers or () if provider.name]
        if providers:
            lines.append(f"  providers: {', '.join(providers)}")
        if self.citation:
            lines.append(f"  cite:      {self.citation}")
        return "\n".join(lines)

    # -- items and statistics -------------------------------------------------

    def search(self, **parameters: Any) -> ItemSearch:
        """A pystac-client ``ItemSearch`` over this collection's items.

        Takes the keyword arguments of
        [`Covariates.search`][datamermaid.resources.covariates.Covariates.search].
        """

        return self._resource.search(self.id, **parameters)

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
        items = list(search.items())
        if not items:
            start, end = self.temporal_extent
            raise ValueError(
                f"no items in {self.id!r} match the search; the collection covers {start} to {end}"
            )
        key = _data_asset_key(items[0], asset)
        if key is None:
            raise ValueError(f"collection {self.id!r} has no asset {asset!r}")
        chosen = items[0].assets[key]
        kind = _media_kind(chosen.media_type, chosen.href)
        zonal = self._resource._client.zonal_stats
        if kind == "raster":
            if columns is not None:
                raise TypeError(f"collection {self.id!r} is a raster; columns= is for vectors")
            endpoint: Any = zonal.raster
        elif kind == "vector":
            endpoint = zonal.vector
            table = _mappings(chosen.extra_fields.get("table:columns"))
            if columns is None:
                columns = _numeric_columns(table)
                if not columns:
                    raise ValueError(
                        f"collection {self.id!r} lists no numeric columns; pass columns="
                    )
            options["columns"] = columns
            if options.get("geometry_column") is None:
                options["geometry_column"] = _geometry_column(table)
        else:
            raise ValueError(
                f"asset {key!r} of collection {self.id!r} is {chosen.media_type!r}, "
                "neither a GeoTIFF nor GeoParquet"
            )
        sources = [source_from_item(item, key) for item in items]
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
        cache: MutableMapping[str, Any] | bool | None = True,
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
            aois,
            sources=sources,
            labels=labels,
            stats=stats,
            radius=radius,
            cache=cache,
            **options,
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
        max_requests: int | None = DEFAULT_MAX_REQUESTS,
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
        max_requests: int | None = DEFAULT_MAX_REQUESTS,
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
        max_requests: int | None = DEFAULT_MAX_REQUESTS,
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
        max_requests: int | None = DEFAULT_MAX_REQUESTS,
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
        max_requests: int | None = DEFAULT_MAX_REQUESTS,
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
            datetime: Which items to use, in any form pystac-client accepts:
                ``"2026-05"``, ``"2026-05-01/2026-05-15"``, a ``datetime`` or a
                ``(start, end)`` pair.  Leave it out for a dataset with one item.
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
            max_requests: Refuse to start when the AOIs times the matching
                items exceed this many requests; ``None`` removes the limit.
                The item search stops once the limit is certain to be passed,
                so an unbounded search costs few catalog requests.  Use
                [`prepare_zonal_stats`][..prepare_zonal_stats] to see the count
                first.
            **options: Other route options, e.g. ``bands`` and ``approx_stats``
                for rasters or ``weighting_method`` for vectors.

        Raises:
            ValueError: If no item matches, the asset is neither GeoTIFF nor
                GeoParquet, a vector dataset has no numeric column to default
                to, or the job would send more than ``max_requests`` requests.
        """

        aois = _expand_aois(aois)
        if max_requests is not None:
            if isinstance(max_requests, bool) or not isinstance(max_requests, int):
                raise TypeError("max_requests must be an integer or None")
            if max_requests < 1:
                raise ValueError("max_requests must be >= 1")
            # One item past what the limit allows is enough to know it is passed.
            cap = max_requests // max(len(aois), 1) + 1
            max_items = cap if max_items is None else min(max_items, cap)
        endpoint, sources, options = self._zonal_plan(
            datetime=datetime,
            bbox=bbox,
            ids=ids,
            max_items=max_items,
            asset=asset,
            columns=columns,
            options=options,
        )
        if max_requests is not None and len(aois) * len(sources) > max_requests:
            raise ValueError(
                f"{len(aois)} AOI{'s' if len(aois) != 1 else ''} against "
                f"{'at least ' if len(sources) == max_items else ''}"
                f"{len(sources)} items of {self.id!r} is more than max_requests={max_requests} "
                "requests; narrow datetime=, bbox= or ids=, set max_items=, or raise "
                "max_requests= (prepare_zonal_stats shows the count without sending any)"
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


class Covariates(BaseResource):
    """The covariates catalog, as ``client.covariates``.

    [`path`][datamermaid.resources.base.BaseResource.path] is the absolute STAC
    API root, and [`catalog`][.catalog] is the pystac-client ``Client`` opened
    on it.  The list of collections is fetched once per client and kept; pass
    ``refresh=True`` to fetch it again.
    """

    def __init__(self, client: MermaidClient) -> None:
        super().__init__(client)
        self.path = client.covariates_url
        self._lock = threading.Lock()
        self._collections: list[CovariateCollection] | None = None

    @cached_property
    def catalog(self) -> Client:
        """The pystac-client ``Client`` for the catalog, opened on first use.

        Its requests go through the owning client, so they share its timeout,
        retries and ``429`` throttle, and failures raise
        [`MermaidError`][datamermaid.exceptions.MermaidError] subclasses.
        """

        pystac_client = _require_pystac_client()
        from ._stac_io import ClientStacIO

        catalog: Client = pystac_client.Client.open(self.path, stac_io=ClientStacIO(self._client))
        return catalog

    def collections(self, *, refresh: bool = False) -> list[CovariateCollection]:
        """Every dataset in the catalog, in the catalog's order."""

        with self._lock:
            if self._collections is None or refresh:
                self._collections = [
                    CovariateCollection(self, collection)
                    for collection in self.catalog.get_collections()
                ]
            return list(self._collections)

    def collection(self, collection_id: str) -> CovariateCollection:
        """One dataset by its exact id, e.g. ``"daily_sst"``.

        Raises:
            NotFoundError: If the catalog has no such collection.
        """

        with self._lock:
            cached = self._collections
        for collection in cached or ():
            if collection.id == collection_id:
                return collection
        return CovariateCollection(self, self.catalog.get_collection(collection_id))

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
            if needle in collection.id.casefold()
            or needle in (collection.stac.title or "").casefold()
        ]

    def to_df(self, query: str | None = None, **kwargs: Any) -> pandas.DataFrame:
        """A table of datasets, one row per collection (optionally filtered by ``query``)."""

        chosen = self.collections() if query is None else self.search_collections(query)
        return to_dataframe([collection.to_dict() for collection in chosen], **kwargs)

    def search(
        self,
        collections: str | CovariateCollection | Sequence[str | CovariateCollection] | None = None,
        **parameters: Any,
    ) -> ItemSearch:
        """A pystac-client ``ItemSearch``, usable as ``search=`` on any zonal stats endpoint.

        Args:
            collections: A collection id or
                [`CovariateCollection`][datamermaid.resources.covariates.CovariateCollection],
                or several.
            **parameters: The other keyword arguments of
                ``pystac_client.Client.search``: ``datetime``, ``bbox``,
                ``ids``, ``filter``, ``sortby``, ``max_items``, ``limit``, ...
                ``intersects`` also takes a [`Site`][datamermaid.models.Site]
                or any ``__geo_interface__`` object.  ``None`` values are left out.
        """

        if isinstance(collections, (str, CovariateCollection)):
            collections = [collections]
        if collections is not None:
            parameters["collections"] = [
                entry.id if isinstance(entry, CovariateCollection) else entry
                for entry in collections
            ]
        if parameters.get("intersects") is not None:
            parameters["intersects"] = dict(_geometry_of(_unwrap(parameters["intersects"])))
        parameters = {key: value for key, value in parameters.items() if value is not None}
        search: ItemSearch = self.catalog.search(**parameters)
        return search
