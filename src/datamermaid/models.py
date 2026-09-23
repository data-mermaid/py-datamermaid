"""Typed representations of MERMAID API resources.

Models are frozen dataclasses.  Every field is optional and every unrecognised key from
the API lands in [`APIModel.extra`][datamermaid.models.APIModel.extra], so a server-side
addition can never break parsing.  Subclasses only need to declare fields; parsing and
conversion are inherited from [`APIModel`][datamermaid.models.APIModel].
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone
from typing import Any, ClassVar, TypeVar

__all__ = [
    "APIModel",
    "AggregatedRecord",
    "BeltFishMethod",
    "BeltInvertMethod",
    "BenthicAttribute",
    "BenthicLITMethod",
    "BenthicPITMethod",
    "BenthicPhotoQuadratTransectMethod",
    "BenthicTransect",
    "BleachingQuadratCollectionMethod",
    "FishBeltTransect",
    "FishFamily",
    "FishGenus",
    "FishSize",
    "FishSpecies",
    "HabitatComplexityMethod",
    "InvertAttribute",
    "InvertBeltTransect",
    "InvertSpecies",
    "LabelMapping",
    "Management",
    "Me",
    "Observer",
    "Project",
    "ProjectMembership",
    "ProjectProfile",
    "ProjectTag",
    "QuadratCollection",
    "QuadratTransect",
    "SampleEvent",
    "SampleUnit",
    "SampleUnitMethod",
    "Site",
    "SummarySampleEvent",
    "Transect",
    "ZonalStatsResult",
    "parse_date",
    "parse_datetime",
]

M = TypeVar("M", bound="APIModel")

#: ``dataclasses.field`` metadata key holding the API's name for a field.
API_FIELD = "api_field"
#: ``dataclasses.field`` metadata key holding a value converter.
CONVERTER = "converter"


def parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp as returned by the API.

    ``None`` passes straight through, so an explicit JSON null yields a null
    timestamp instead of an unparseable value.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError(f"expected an ISO 8601 string, got {type(value).__name__}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_date(value: Any) -> date | None:
    """Parse a calendar date (``YYYY-MM-DD``) as returned by the API."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError(f"expected an ISO 8601 date string, got {type(value).__name__}")
    return date.fromisoformat(value.strip())


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise TypeError("expected a sequence of strings")
    return tuple(str(item) for item in value)


def _mapping_tuple(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise TypeError("expected a sequence of objects")
    return tuple(dict(item) for item in value)


def _mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("expected an object")
    return dict(value)


def _model_tuple(model: type[M]) -> Callable[[Any], tuple[M, ...]]:
    """Build a converter turning a JSON array into a tuple of ``model``."""

    def convert(value: Any) -> tuple[M, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise TypeError("expected a sequence of objects")
        return tuple(model.from_api(item) for item in value)

    return convert


def _model(model: type[M]) -> Callable[[Any], M | None]:
    """Build a converter turning a nested JSON object into a ``model``."""

    def convert(value: Any) -> M | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise TypeError("expected an object")
        return model.from_api(value)

    return convert


def _api_meta(
    api_field: str | None = None, converter: Callable[[Any], Any] | None = None
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if api_field is not None:
        meta[API_FIELD] = api_field
    if converter is not None:
        meta[CONVERTER] = converter
    return meta


@dataclass(frozen=True)
class APIModel:
    """Base class providing lossless construction from an API payload."""

    extra: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_api(cls: type[M], data: Mapping[str, Any]) -> M:
        """Build an instance from a decoded JSON object.

        Keys the model does not declare are preserved in
        [`extra`][datamermaid.models.APIModel.extra].  A value that fails conversion is
        left in [`extra`][datamermaid.models.APIModel.extra] under its original key and
        the declared field keeps its default, so nothing is silently dropped.  Converters
        also run for explicit JSON nulls, so a null collection becomes an empty tuple
        rather than ``None``.
        """

        if not isinstance(data, Mapping):
            raise TypeError(f"expected a JSON object, got {type(data).__name__}")

        payload = dict(data)
        kwargs: dict[str, Any] = {}
        for model_field in fields(cls):
            if model_field.name == "extra" or not model_field.init:
                continue
            key = model_field.metadata.get(API_FIELD, model_field.name)
            if key not in payload:
                continue
            value = payload[key]
            converter = model_field.metadata.get(CONVERTER)
            if converter is not None:
                try:
                    value = converter(value)
                except (TypeError, ValueError):
                    continue  # unparseable: leave the raw value in `extra`
            kwargs[model_field.name] = value
            del payload[key]

        return cls(extra=payload, **kwargs)

    def to_dict(self, *, include_extra: bool = True) -> dict[str, Any]:
        """Flatten the model into a plain dict, suitable for a DataFrame row.

        With ``include_extra``, a field whose value failed conversion exports
        the raw value [`from_api`][.from_api] kept in ``extra``, not the
        field's default, so the export loses nothing the model holds.
        """

        data: dict[str, Any] = {}
        extra = dict(self.extra) if include_extra else {}
        for model_field in fields(self):
            if model_field.name == "extra":
                continue
            key = model_field.metadata.get(API_FIELD, model_field.name)
            if key in extra:
                # `from_api` only leaves a declared key in `extra` when its
                # conversion failed, so the raw value is the one to keep.
                data[model_field.name] = extra.pop(key)
            else:
                data[model_field.name] = getattr(self, model_field.name)
        for key, value in extra.items():
            data.setdefault(key, value)
        return data


@dataclass(frozen=True)
class ProjectMembership(APIModel):
    """A project a profile belongs to, as returned by ``GET /me/``."""

    id: str | None = None
    name: str | None = None
    role: int | None = None
    num_active_sample_units: int | None = None


@dataclass(frozen=True)
class Me(APIModel):
    """The profile owning the credentials in use (``GET /me/``)."""

    id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email: str | None = None
    picture: str | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    projects: tuple[ProjectMembership, ...] = field(
        default=(), metadata=_api_meta(converter=_model_tuple(ProjectMembership))
    )


@dataclass(frozen=True)
class Project(APIModel):
    """A MERMAID project (``GET /projects/``)."""

    id: str | None = None
    name: str | None = None
    notes: str | None = None
    status: int | None = None
    countries: tuple[str, ...] = field(default=(), metadata=_api_meta(converter=_string_tuple))
    tags: tuple[str, ...] = field(default=(), metadata=_api_meta(converter=_string_tuple))
    members: tuple[str, ...] = field(default=(), metadata=_api_meta(converter=_string_tuple))
    project_admins: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )
    num_sites: int | None = None
    num_sample_units: float | None = None
    num_active_sample_units: int | None = None
    is_demo: bool | None = None
    includes_gfcr: bool | None = None
    suggested_citation: str | None = None
    bbox: Any = None
    data_policy_beltfish: int | None = None
    data_policy_benthiclit: int | None = None
    data_policy_benthicpit: int | None = None
    data_policy_benthicpqt: int | None = None
    data_policy_bleachingqc: int | None = None
    data_policy_habitatcomplexity: int | None = None
    data_policy_macroinvertebrate: int | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None

    @property
    def data_policies(self) -> dict[str, int | None]:
        """The per-protocol data sharing policies, keyed by protocol name."""

        prefix = "data_policy_"
        return {
            model_field.name[len(prefix) :]: getattr(self, model_field.name)
            for model_field in fields(self)
            if model_field.name.startswith(prefix)
        }


@dataclass(frozen=True)
class Site(APIModel):
    """A reef site (``GET /sites/``).

    The choice fields (``country``, ``reef_type``, ``reef_zone``, ``exposure``)
    hold ids; their ``*_name`` companions are only returned when the request
    asks for them with ``include_fields``.
    """

    id: str | None = None
    name: str | None = None
    project: str | None = None
    project_name: str | None = None
    country: str | None = None
    country_name: str | None = None
    reef_type: str | None = None
    reef_type_name: str | None = None
    reef_zone: str | None = None
    reef_zone_name: str | None = None
    exposure: str | None = None
    exposure_name: str | None = None
    location: Any = None
    notes: str | None = None
    predecessor: str | None = None
    validations: Any = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None

    @property
    def __geo_interface__(self) -> Mapping[str, Any]:
        """The ``location`` as GeoJSON, so a site can be handed to shapely, geopandas or
        [`to_aoi`][datamermaid.geometry.to_aoi] directly.

        Raises:
            ValueError: If the site has no ``location``.
            TypeError: If ``location`` is not a GeoJSON mapping.
        """

        if self.location is None:
            raise ValueError(f"site {self.name or self.id!r} has no location")
        if not isinstance(self.location, Mapping):
            raise TypeError(
                f"site {self.name or self.id!r} location is not a GeoJSON mapping: "
                f"{type(self.location).__name__}"
            )
        return self.location


@dataclass(frozen=True)
class Management(APIModel):
    """A management regime (``GET /managements/``).

    ``rules`` is the API's comma-separated summary of the seven rule flags below it;
    [`rule_flags`][datamermaid.models.Management.rule_flags] turns those flags back into
    a mapping.
    """

    id: str | None = None
    name: str | None = None
    name_secondary: str | None = None
    project: str | None = None
    project_name: str | None = None
    parties: tuple[str, ...] = field(default=(), metadata=_api_meta(converter=_string_tuple))
    compliance: str | None = None
    est_year: int | None = None
    predecessor: str | None = None
    notes: str | None = None
    boundary: Any = None
    size: float | None = None
    rules: str | None = None
    no_take: bool | None = None
    periodic_closure: bool | None = None
    open_access: bool | None = None
    size_limits: bool | None = None
    gear_restriction: bool | None = None
    species_restriction: bool | None = None
    access_restriction: bool | None = None
    validations: Any = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None

    #: The boolean fields summarised by [`rules`][datamermaid.models.Management.rules].
    RULE_FIELDS = (
        "no_take",
        "periodic_closure",
        "open_access",
        "size_limits",
        "gear_restriction",
        "species_restriction",
        "access_restriction",
    )

    @property
    def rule_flags(self) -> dict[str, bool | None]:
        """The individual management rules, keyed by name."""

        return {name: getattr(self, name) for name in self.RULE_FIELDS}

    @property
    def __geo_interface__(self) -> Mapping[str, Any]:
        """The ``boundary`` as GeoJSON, for shapely, geopandas and the like.

        Raises:
            ValueError: If the management regime has no ``boundary``.
            TypeError: If ``boundary`` is not a GeoJSON mapping.
        """

        if self.boundary is None:
            raise ValueError(f"management {self.name or self.id!r} has no boundary")
        if not isinstance(self.boundary, Mapping):
            raise TypeError(
                f"management {self.name or self.id!r} boundary is not a GeoJSON mapping: "
                f"{type(self.boundary).__name__}"
            )
        return self.boundary


@dataclass(frozen=True)
class ProjectTag(APIModel):
    """An organisation tag projects can be labelled with (``GET /projecttags/``)."""

    id: str | None = None
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    status: int | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class FishSize(APIModel):
    """One bin of a fish size bin set (``GET /fishsizes/``)."""

    id: str | None = None
    name: str | None = None
    val: float | None = None
    min_val: float | None = None
    max_val: float | None = None
    fish_bin_size: str | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class FishFamily(APIModel):
    """A fish family (``GET /fishfamilies/``)."""

    id: str | None = None
    name: str | None = None
    status: int | None = None
    biomass_constant_a: float | None = None
    biomass_constant_b: float | None = None
    biomass_constant_c: float | None = None
    regions: tuple[str, ...] = field(default=(), metadata=_api_meta(converter=_string_tuple))
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class FishGenus(APIModel):
    """A fish genus (``GET /fishgenera/``)."""

    id: str | None = None
    name: str | None = None
    status: int | None = None
    family: str | None = None
    biomass_constant_a: float | None = None
    biomass_constant_b: float | None = None
    biomass_constant_c: float | None = None
    regions: tuple[str, ...] = field(default=(), metadata=_api_meta(converter=_string_tuple))
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class FishSpecies(APIModel):
    """A fish species (``GET /fishspecies/``).

    ``name`` is the specific epithet only; ``display_name`` is the binomial the
    API composes from the genus.
    """

    id: str | None = None
    name: str | None = None
    display_name: str | None = None
    status: int | None = None
    genus: str | None = None
    biomass_constant_a: float | None = None
    biomass_constant_b: float | None = None
    biomass_constant_c: float | None = None
    vulnerability: float | None = None
    max_length: float | None = None
    max_length_type: str | None = None
    trophic_level: float | None = None
    climate_score: float | None = None
    group_size: str | None = None
    trophic_group: str | None = None
    functional_group: str | None = None
    regions: tuple[str, ...] = field(default=(), metadata=_api_meta(converter=_string_tuple))
    notes: str | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class BenthicAttribute(APIModel):
    """A benthic attribute (``GET /benthicattributes/``).

    Attributes form a tree: ``parent`` points at the enclosing attribute and
    ``top_level_category`` at the root of that tree.
    """

    id: str | None = None
    name: str | None = None
    status: int | None = None
    parent: str | None = None
    top_level_category: str | None = None
    regions: tuple[str, ...] = field(default=(), metadata=_api_meta(converter=_string_tuple))
    life_histories: tuple[str, ...] = field(default=(), metadata=_api_meta(converter=_string_tuple))
    growth_form_life_histories: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )
    notes: str | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class InvertAttribute(APIModel):
    """A macroinvertebrate attribute (``GET /invertattributes/``).

    One row per taxon at any rank, flattened: ``taxonomic_rank`` says which
    rank this is, and the fields that only exist further down the tree (a
    species' ``max_length_source``, say) are null for the ranks above it.
    """

    id: str | None = None
    name: str | None = None
    status: int | None = None
    taxonomic_rank: str | None = None
    parent: str | None = None
    group_of_interest: str | None = None
    max_length: float | None = None
    max_length_type: str | None = None
    max_length_source: str | None = None
    max_length_url: str | None = None
    notes: str | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class InvertSpecies(APIModel):
    """A macroinvertebrate species (``GET /invertspecies/``)."""

    id: str | None = None
    name: str | None = None
    display_name: str | None = None
    status: int | None = None
    genus: str | None = None
    max_length: float | None = None
    max_length_type: str | None = None
    max_length_source: str | None = None
    max_length_url: str | None = None
    notes: str | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class SummarySampleEvent(APIModel):
    """A public summary of one sample event (``GET /summarysampleevents/``).

    The per-protocol aggregates stay in [`protocols`][.protocols], a mapping of protocol
    name (``beltfish``, ``benthicpit``, ...) to that protocol's summary statistics, which
    vary by protocol and grow over time.
    """

    sample_event_id: str | None = None
    sample_date: date | None = field(default=None, metadata=_api_meta(converter=parse_date))
    site_id: str | None = None
    site_name: str | None = None
    site_notes: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    country_id: str | None = None
    country_name: str | None = None
    reef_type: str | None = None
    reef_zone: str | None = None
    reef_exposure: str | None = None
    depth_avg: float | None = None
    depth_sd: float | None = None
    project_id: str | None = None
    project_name: str | None = None
    project_notes: str | None = None
    project_includes_gfcr: bool | None = None
    project_admins: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )
    tags: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )
    observers: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )
    management_id: str | None = None
    management_name: str | None = None
    management_est_year: int | None = None
    management_size: float | None = None
    management_parties: tuple[str, ...] = field(
        default=(), metadata=_api_meta(converter=_string_tuple)
    )
    management_compliance: str | None = None
    management_rules: tuple[str, ...] = field(
        default=(), metadata=_api_meta(converter=_string_tuple)
    )
    management_notes: str | None = None
    protocols: Mapping[str, Any] = field(
        default_factory=dict, metadata=_api_meta(converter=_mapping)
    )
    contact_link: str | None = None
    suggested_citation: str | None = None
    data_policy_beltfish: str | None = None
    data_policy_benthiclit: str | None = None
    data_policy_benthicpit: str | None = None
    data_policy_benthicpqt: str | None = None
    data_policy_bleachingqc: str | None = None
    data_policy_habitatcomplexity: str | None = None
    data_policy_macroinvertebrate: str | None = None

    @property
    def data_policies(self) -> dict[str, str | None]:
        """The per-protocol data sharing policies, keyed by protocol name."""

        prefix = "data_policy_"
        return {
            model_field.name[len(prefix) :]: getattr(self, model_field.name)
            for model_field in fields(self)
            if model_field.name.startswith(prefix)
        }


@dataclass(frozen=True)
class LabelMapping(APIModel):
    """A provider label mapped onto a MERMAID benthic attribute.

    ``GET /classification/labelmappings/``: how an image classification
    provider's own label lines up with a MERMAID attribute and growth form.
    """

    id: str | None = None
    benthic_attribute_id: str | None = None
    benthic_attribute_name: str | None = None
    growth_form_id: str | None = None
    growth_form_name: str | None = None
    provider: str | None = None
    provider_id: str | None = None
    provider_label: str | None = None


# -- project-scoped records -------------------------------------------------
#
# The models below are only reachable under ``/projects/{project_id}/``.  The
# sample unit and sample unit method families mirror the API's own class
# hierarchy: a sample unit is a transect or a quadrat collection, and a sample
# unit method is one protocol recorded on one sample unit.


@dataclass(frozen=True)
class SampleEvent(APIModel):
    """A site visited on a date under a management regime.

    ``GET /projects/{project_id}/sampleevents/``.  Every sample unit belongs to
    one of these; ``site`` and ``management`` hold ids of records listed by
    the project's [`ProjectContext`][datamermaid.resources.project_context.ProjectContext].
    """

    id: str | None = None
    site: str | None = None
    management: str | None = None
    sample_date: date | None = field(default=None, metadata=_api_meta(converter=parse_date))
    notes: str | None = None
    validations: Any = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class Observer(APIModel):
    """A profile credited with one sample unit method.

    ``GET /projects/{project_id}/observers/``.  ``transectmethod`` is the id of
    the method record the observer worked on.
    """

    id: str | None = None
    transectmethod: str | None = None
    profile: str | None = None
    profile_name: str | None = None
    rank: int | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class ProjectProfile(APIModel):
    """A profile's membership of one project.

    ``GET /projects/{project_id}/project_profiles/``.  ``role`` is the numeric role (90
    admin, 50 collector, 10 read-only), summarised by
    [`is_admin`][datamermaid.models.ProjectProfile.is_admin] and
    [`is_collector`][.is_collector].
    """

    id: str | None = None
    project: str | None = None
    profile: str | None = None
    profile_name: str | None = None
    email: str | None = None
    role: int | None = None
    is_admin: bool | None = None
    is_collector: bool | None = None
    picture: str | None = None
    num_active_sample_units: int | None = None
    num_account_connections: int | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class SampleUnit(APIModel):
    """What every transect and quadrat collection has in common.

    The choice fields (``visibility``, ``current``, ``relative_depth``,
    ``tide``) hold ids from ``/choices/``; ``sample_event`` holds the id of the
    [`SampleEvent`][datamermaid.models.SampleEvent] the unit was recorded under.
    """

    id: str | None = None
    sample_event: str | None = None
    label: str | None = None
    depth: float | None = None
    sample_time: str | None = None
    visibility: str | None = None
    current: str | None = None
    relative_depth: str | None = None
    tide: str | None = None
    notes: str | None = None
    collect_record_id: str | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class Transect(SampleUnit):
    """A numbered transect of a surveyed length."""

    number: int | None = None
    len_surveyed: float | None = None
    reef_slope: str | None = None


@dataclass(frozen=True)
class FishBeltTransect(Transect):
    """A fish belt transect (``/projects/{project_id}/fishbelttransects/``).

    ``width`` and ``size_bin`` hold ids of the belt width and fish size bin the
    transect was surveyed with.
    """

    width: str | None = None
    size_bin: str | None = None


@dataclass(frozen=True)
class BenthicTransect(Transect):
    """A benthic transect (``/projects/{project_id}/benthictransects/``).

    Shared by the benthic LIT, benthic PIT and habitat complexity protocols.
    """


@dataclass(frozen=True)
class InvertBeltTransect(Transect):
    """A macroinvertebrate belt transect, as nested in a method payload."""

    width: str | None = None
    size_bin: str | None = None


@dataclass(frozen=True)
class QuadratTransect(Transect):
    """A benthic photo quadrat transect, as nested in a method payload."""

    quadrat_size: float | None = None
    num_quadrats: int | None = None
    num_points_per_quadrat: int | None = None
    quadrat_number_start: int | None = None


@dataclass(frozen=True)
class QuadratCollection(SampleUnit):
    """A bleaching quadrat collection, as nested in a method payload."""

    quadrat_size: float | None = None


@dataclass(frozen=True)
class SampleUnitMethod(APIModel):
    """One protocol recorded on one sample unit.

    Subclasses name the protocol's sample unit and its observation lists; the
    observations themselves stay as plain dictionaries, since they carry a row per fish,
    point or colony and their columns differ by protocol.
    [`sample_unit`][.sample_unit] and [`observations`][.observations] reach both without
    knowing which protocol is in hand.
    """

    id: str | None = None
    sample_event: SampleEvent | None = field(
        default=None, metadata=_api_meta(converter=_model(SampleEvent))
    )
    observers: tuple[Observer, ...] = field(
        default=(), metadata=_api_meta(converter=_model_tuple(Observer))
    )
    collect_record_id: str | None = None
    created_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    updated_on: datetime | None = field(default=None, metadata=_api_meta(converter=parse_datetime))
    created_by: str | None = None
    updated_by: str | None = None

    #: Name of the field holding this protocol's transect or quadrat collection.
    SAMPLE_UNIT_FIELD: ClassVar[str] = ""

    @property
    def sample_unit(self) -> SampleUnit | None:
        """The transect or quadrat collection the protocol was recorded on."""

        if not self.SAMPLE_UNIT_FIELD:
            return None
        unit = getattr(self, self.SAMPLE_UNIT_FIELD, None)
        return unit if isinstance(unit, SampleUnit) else None

    @property
    def observations(self) -> dict[str, tuple[Mapping[str, Any], ...]]:
        """The protocol's observation lists, keyed by their ``obs_*`` field name.

        Lists the API adds later are picked up too, since they land in
        [`extra`][datamermaid.models.APIModel.extra].
        """

        found: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for key, value in sorted(self.to_dict().items()):
            if not key.startswith("obs_"):
                continue
            found[key] = _mapping_tuple(value) if value else ()
        return found


@dataclass(frozen=True)
class BeltFishMethod(SampleUnitMethod):
    """A fish belt survey (``/projects/{project_id}/beltfishtransectmethods/``)."""

    fishbelt_transect: FishBeltTransect | None = field(
        default=None, metadata=_api_meta(converter=_model(FishBeltTransect))
    )
    obs_belt_fishes: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )

    SAMPLE_UNIT_FIELD = "fishbelt_transect"


@dataclass(frozen=True)
class BenthicLITMethod(SampleUnitMethod):
    """A benthic LIT survey (``/projects/{project_id}/benthiclittransectmethods/``)."""

    benthic_transect: BenthicTransect | None = field(
        default=None, metadata=_api_meta(converter=_model(BenthicTransect))
    )
    obs_benthic_lits: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )

    SAMPLE_UNIT_FIELD = "benthic_transect"


@dataclass(frozen=True)
class BenthicPITMethod(SampleUnitMethod):
    """A benthic PIT survey (``/projects/{project_id}/benthicpittransectmethods/``).

    ``interval_size`` and ``interval_start`` are in metres along the transect.
    """

    benthic_transect: BenthicTransect | None = field(
        default=None, metadata=_api_meta(converter=_model(BenthicTransect))
    )
    interval_size: float | None = None
    interval_start: float | None = None
    obs_benthic_pits: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )

    SAMPLE_UNIT_FIELD = "benthic_transect"


@dataclass(frozen=True)
class HabitatComplexityMethod(SampleUnitMethod):
    """A habitat complexity survey.

    ``/projects/{project_id}/habitatcomplexitytransectmethods/``.
    """

    benthic_transect: BenthicTransect | None = field(
        default=None, metadata=_api_meta(converter=_model(BenthicTransect))
    )
    interval_size: float | None = None
    interval_start: float | None = None
    obs_habitat_complexities: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )

    SAMPLE_UNIT_FIELD = "benthic_transect"


@dataclass(frozen=True)
class BenthicPhotoQuadratTransectMethod(SampleUnitMethod):
    """A benthic photo quadrat survey.

    ``/projects/{project_id}/benthicphotoquadrattransectmethods/``.  When the
    observations came from an image classifier, ``image_classification`` is
    true and ``images`` describes the classified photos.
    """

    quadrat_transect: QuadratTransect | None = field(
        default=None, metadata=_api_meta(converter=_model(QuadratTransect))
    )
    image_classification: bool | None = None
    images: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )
    obs_benthic_photo_quadrats: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )

    SAMPLE_UNIT_FIELD = "quadrat_transect"


@dataclass(frozen=True)
class BleachingQuadratCollectionMethod(SampleUnitMethod):
    """A bleaching quadrat collection survey.

    ``/projects/{project_id}/bleachingquadratcollectionmethods/``.  This is the
    one protocol with two observation lists: the bleached colony counts and the
    per-quadrat benthic percentages.
    """

    quadrat_collection: QuadratCollection | None = field(
        default=None, metadata=_api_meta(converter=_model(QuadratCollection))
    )
    obs_colonies_bleached: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )
    obs_quadrat_benthic_percent: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )

    SAMPLE_UNIT_FIELD = "quadrat_collection"


@dataclass(frozen=True)
class BeltInvertMethod(SampleUnitMethod):
    """A macroinvertebrate belt survey.

    ``/projects/{project_id}/beltinverttransectmethods/``.
    """

    beltinvert_transect: InvertBeltTransect | None = field(
        default=None, metadata=_api_meta(converter=_model(InvertBeltTransect))
    )
    obs_belt_inverts: tuple[Mapping[str, Any], ...] = field(
        default=(), metadata=_api_meta(converter=_mapping_tuple)
    )

    SAMPLE_UNIT_FIELD = "beltinvert_transect"


#
# The aggregated (denormalized) views, ``/projects/{project_id}/<family>/...``.


@dataclass(frozen=True)
class AggregatedRecord(APIModel):
    """One row from an aggregated view, kept deliberately thin.

    The observation, sample unit and sample event views under
    ``/projects/{project_id}/beltfishes/``, ``/benthicpits/`` and their siblings return
    wide, flat records: one row per observation (or per sample unit, or per sample
    event) with every site, management and protocol column already joined in.  The
    columns differ per protocol, per view and per API release, so only the handful
    shared by all of them is declared here; the rest arrives in
    [`APIModel.extra`][datamermaid.models.APIModel.extra] and
    [`APIModel.to_dict`][datamermaid.models.APIModel.to_dict] flattens it back out,
    which is what ``to_df()`` turns into one DataFrame column per field.

    The sample unit views have no ``id`` (they carry ``sample_unit_ids`` in
    [`extra`][datamermaid.models.AggregatedRecord.extra] instead), so
    [`id`][datamermaid.models.AggregatedRecord.id] is ``None`` on those rows.
    """

    id: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    site_id: str | None = None
    site_name: str | None = None
    sample_date: date | None = field(default=None, metadata=_api_meta(converter=parse_date))
    management_id: str | None = None
    sample_event_id: str | None = None


#
# The Zonal Stats service, a separate host answering with dynamic keys.


@dataclass(frozen=True)
class ZonalStatsResult:
    """Statistics for one area of interest against one raster or vector source.

    The Zonal Stats API answers with an object keyed by band (``band_1``) or, for
    vector sources, by column name, each holding the requested statistics:

    ```json
    {"band_1": {"mean": 12.3, "count": 40, "aoi_area": 785398.2}}
    ```

    Those keys are dynamic, so this is a plain frozen dataclass rather than an
    [`APIModel`][datamermaid.models.APIModel]: [`stats`][.stats] holds the response
    as sent, and the result reads like a mapping (``result["band_1"]["mean"]``).
    [`aoi`][.aoi] and [`source`][.source] record what was asked for, and
    [`label`][.label] is whatever identifier the caller attached, so a batch of
    results can be told apart once flattened.

    [`to_dict`][.to_dict] gives one wide row (``band_1_mean``, ``band_1_count``,
    ...) so [`to_dataframe`][datamermaid.pagination.to_dataframe] works on a list of
    results, and [`to_records`][.to_records] gives long ``(label, band, stat, value)``
    rows for reshaping.
    """

    #: Band (or column) name -> statistic name -> value, as the API returned it.
    stats: Mapping[str, Mapping[str, Any]]
    #: The GeoJSON geometry sent as the area of interest.
    aoi: Mapping[str, Any]
    #: The raster or vector URL the statistics were computed from.
    source: str
    #: Caller-supplied identifier, carried through unchanged.
    label: Any = None

    @classmethod
    def from_api(
        cls,
        data: Mapping[str, Any],
        *,
        aoi: Mapping[str, Any],
        source: str,
        label: Any = None,
    ) -> ZonalStatsResult:
        """Build a result from a decoded response body.

        Every top-level value must itself be an object of statistics; anything
        else means the body is not a zonal stats response.
        """

        if not isinstance(data, Mapping):
            raise TypeError(f"expected a JSON object, got {type(data).__name__}")
        stats: dict[str, Mapping[str, Any]] = {}
        for band, values in data.items():
            if not isinstance(values, Mapping):
                raise TypeError(
                    f"expected an object of statistics for {band!r}, got {type(values).__name__}"
                )
            stats[str(band)] = dict(values)
        return cls(stats=stats, aoi=dict(aoi), source=source, label=label)

    # -- mapping interface --------------------------------------------------

    def __getitem__(self, band: str) -> Mapping[str, Any]:
        return self.stats[band]

    def __iter__(self) -> Iterator[str]:
        return iter(self.stats)

    def __len__(self) -> int:
        return len(self.stats)

    def __contains__(self, band: object) -> bool:
        return band in self.stats

    def keys(self) -> Sequence[str]:
        """The band or column names in the response, in order."""

        return list(self.stats)

    def items(self) -> Sequence[tuple[str, Mapping[str, Any]]]:
        """``(band, statistics)`` pairs, in response order."""

        return list(self.stats.items())

    # -- export -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Flatten into one wide row: ``label``, ``source``, then ``<band>_<stat>``.

        ``label`` is always present (``None`` when the caller gave none) so a
        DataFrame built from many results has a stable set of columns.
        """

        row: dict[str, Any] = {"label": self.label, "source": self.source}
        for band, values in self.stats.items():
            for stat, value in values.items():
                row[f"{band}_{stat}"] = value
        return row

    def to_records(self) -> list[dict[str, Any]]:
        """One long ``{label, band, stat, value}`` row per statistic."""

        return [
            {"label": self.label, "band": band, "stat": stat, "value": value}
            for band, values in self.stats.items()
            for stat, value in values.items()
        ]
