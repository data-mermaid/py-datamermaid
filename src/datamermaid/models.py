"""Typed representations of MERMAID API resources.

Models are frozen dataclasses.  Every field is optional and every unrecognised
key from the API lands in :attr:`APIModel.extra`, so a server-side addition can
never break parsing.  Subclasses only need to declare fields; parsing and
conversion are inherited from :class:`APIModel`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone
from typing import Any, TypeVar

__all__ = [
    "APIModel",
    "BenthicAttribute",
    "FishFamily",
    "FishGenus",
    "FishSize",
    "FishSpecies",
    "InvertAttribute",
    "InvertSpecies",
    "LabelMapping",
    "Management",
    "Me",
    "Project",
    "ProjectMembership",
    "ProjectTag",
    "Site",
    "SummarySampleEvent",
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


def _model_tuple(model: type[M]) -> Callable[[Any], tuple[M, ...]]:
    """Build a converter turning a JSON array into a tuple of ``model``."""

    def convert(value: Any) -> tuple[M, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise TypeError("expected a sequence of objects")
        return tuple(model.from_api(item) for item in value)

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

        Keys the model does not declare are preserved in :attr:`extra`.  A value
        that fails conversion is left in :attr:`extra` under its original key and
        the declared field keeps its default, so nothing is silently dropped.
        Converters also run for explicit JSON nulls, so a null collection becomes
        an empty tuple rather than ``None``.
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
        """Flatten the model into a plain dict, suitable for a DataFrame row."""

        data: dict[str, Any] = {}
        for model_field in fields(self):
            if model_field.name == "extra":
                continue
            data[model_field.name] = getattr(self, model_field.name)
        if include_extra:
            for key, value in self.extra.items():
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


@dataclass(frozen=True)
class Management(APIModel):
    """A management regime (``GET /managements/``).

    ``rules`` is the API's comma-separated summary of the seven rule flags
    below it; :attr:`rule_flags` turns those flags back into a mapping.
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

    #: The boolean fields summarised by :attr:`rules`.
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

    The per-protocol aggregates stay in :attr:`protocols`, a mapping of
    protocol name (``beltfish``, ``benthicpit``, ...) to that protocol's
    summary statistics, which vary by protocol and grow over time.
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
    management_parties: str | None = None
    management_compliance: str | None = None
    management_rules: tuple[str, ...] = field(
        default=(), metadata=_api_meta(converter=_string_tuple)
    )
    management_notes: str | None = None
    protocols: Mapping[str, Any] = field(default_factory=dict)
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
