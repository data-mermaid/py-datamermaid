"""Typed representations of MERMAID API resources.

Models are frozen dataclasses.  Every field is optional and every unrecognised
key from the API lands in :attr:`APIModel.extra`, so a server-side addition can
never break parsing.  Subclasses only need to declare fields; parsing and
conversion are inherited from :class:`APIModel`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, TypeVar

__all__ = [
    "APIModel",
    "Me",
    "Project",
    "ProjectMembership",
    "parse_datetime",
]

M = TypeVar("M", bound="APIModel")

#: ``dataclasses.field`` metadata key holding the API's name for a field.
API_FIELD = "api_field"
#: ``dataclasses.field`` metadata key holding a value converter.
CONVERTER = "converter"


def parse_datetime(value: Any) -> datetime:
    """Parse an ISO 8601 timestamp as returned by the API."""

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
            if converter is not None and value is not None:
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
        default=(),
        metadata=_api_meta(
            converter=lambda value: tuple(ProjectMembership.from_api(item) for item in value)
        ),
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
