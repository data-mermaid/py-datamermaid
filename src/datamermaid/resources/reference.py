"""The top-level reference, lookup and summary endpoints.

Every endpoint here is a read-only, paginated list with a ``/<id>/`` detail
route, so each wrapper is just a path and a model on top of
[`ReadOnlyResource`][datamermaid.resources.base.ReadOnlyResource].  The filters named in
each docstring are the ones the API declares; any other keyword argument is
forwarded as a query parameter too, as are ``limit``, ``search``, ``ordering``
and ``fields``.

Most of these are public.  ``/sites/`` and ``/managements/`` are the exception:
they hold project data and need credentials.
"""

from __future__ import annotations

from typing import Any

from ..models import (
    BenthicAttribute,
    FishFamily,
    FishGenus,
    FishSize,
    FishSpecies,
    InvertAttribute,
    InvertSpecies,
    LabelMapping,
    Management,
    ProjectTag,
    Site,
    SummarySampleEvent,
)
from .base import ReadOnlyResource

__all__ = [
    "REFERENCE_RESOURCES",
    "BenthicAttributesResource",
    "FishFamiliesResource",
    "FishGeneraResource",
    "FishSizesResource",
    "FishSpeciesResource",
    "InvertAttributesResource",
    "InvertSpeciesResource",
    "LabelMappingsResource",
    "ManagementsResource",
    "ProjectTagsResource",
    "SitesResource",
    "SummarySampleEventsResource",
]


class SitesResource(ReadOnlyResource[Site]):
    """Reef sites across every project the credentials can see (``/sites/``).

    Requires authentication.  Filters: ``project``, ``country``, ``reef_type``,
    ``reef_zone``, ``exposure``, ``exclude_projects``, ``unique``.
    """

    path = "sites/"
    model = Site


class ManagementsResource(ReadOnlyResource[Management]):
    """Management regimes (``/managements/``).

    Requires authentication.  Filters: ``project``, ``predecessor``,
    ``parties``, ``compliance``, ``est_year``, the seven rule flags
    (``no_take``, ``periodic_closure``, ``open_access``, ``size_limits``,
    ``gear_restriction``, ``species_restriction``, ``access_restriction``),
    ``unique`` and ``exclude_projects``.
    """

    path = "managements/"
    model = Management


class ProjectTagsResource(ReadOnlyResource[ProjectTag]):
    """Organisation tags projects can be labelled with (``/projecttags/``).

    Filters: ``name``, ``status``.
    """

    path = "projecttags/"
    model = ProjectTag


class FishSizesResource(ReadOnlyResource[FishSize]):
    """The bins of every fish size bin set (``/fishsizes/``).

    Filters: ``val``.
    """

    path = "fishsizes/"
    model = FishSize


class FishFamiliesResource(ReadOnlyResource[FishFamily]):
    """Fish families (``/fishfamilies/``).

    Filters: ``status``, ``regions`` (a comma-separated list of region ids).
    """

    path = "fishfamilies/"
    model = FishFamily


class FishGeneraResource(ReadOnlyResource[FishGenus]):
    """Fish genera (``/fishgenera/``).

    Filters: ``family``, ``status``, ``regions``.
    """

    path = "fishgenera/"
    model = FishGenus


class FishSpeciesResource(ReadOnlyResource[FishSpecies]):
    """Fish species (``/fishspecies/``).

    Filters: ``genus``, ``genus__family``, ``status``, ``regions``.
    """

    path = "fishspecies/"
    model = FishSpecies


class BenthicAttributesResource(ReadOnlyResource[BenthicAttribute]):
    """Benthic attributes (``/benthicattributes/``).

    Filters: ``parent`` (pass an empty string for the top-level attributes),
    ``life_history``, ``region``.
    """

    path = "benthicattributes/"
    model = BenthicAttribute


class InvertAttributesResource(ReadOnlyResource[InvertAttribute]):
    """Macroinvertebrate attributes at every rank (``/invertattributes/``)."""

    path = "invertattributes/"
    model = InvertAttribute


class InvertSpeciesResource(ReadOnlyResource[InvertSpecies]):
    """Macroinvertebrate species (``/invertspecies/``).

    Filters: ``genus``, ``genus__family``, ``genus__group_of_interest``,
    ``status``.
    """

    path = "invertspecies/"
    model = InvertSpecies


class SummarySampleEventsResource(ReadOnlyResource[SummarySampleEvent]):
    """Public per-sample-event summaries (``/summarysampleevents/``).

    Records are keyed by [`sample_event_id`][datamermaid.models.SummarySampleEvent.sample_event_id],
    which is also what [`get`][datamermaid.resources.base.ReadOnlyResource.get]
    takes.  Filters: ``project_id``, ``project_name``, ``project_admins``,
    ``sample_date`` (as a range, ``sample_date_after`` / ``sample_date_before``)
    and the seven ``data_policy_*`` fields.
    """

    path = "summarysampleevents/"
    model = SummarySampleEvent


class LabelMappingsResource(ReadOnlyResource[LabelMapping]):
    """Image classifier labels mapped onto MERMAID attributes.

    ``/classification/labelmappings/``.  Filters: ``benthic_attribute``,
    ``benthic_attribute__name``, ``growth_form``, ``growth_form__name``,
    ``provider``, ``provider_id``, ``provider_label``.
    """

    path = "classification/labelmappings/"
    model = LabelMapping


#: Client attribute name -> resource class, for the endpoints above.
REFERENCE_RESOURCES: tuple[tuple[str, type[ReadOnlyResource[Any]]], ...] = (
    ("sites", SitesResource),
    ("managements", ManagementsResource),
    ("project_tags", ProjectTagsResource),
    ("fish_sizes", FishSizesResource),
    ("fish_families", FishFamiliesResource),
    ("fish_genera", FishGeneraResource),
    ("fish_species", FishSpeciesResource),
    ("benthic_attributes", BenthicAttributesResource),
    ("invert_attributes", InvertAttributesResource),
    ("invert_species", InvertSpeciesResource),
    ("summary_sample_events", SummarySampleEventsResource),
    ("label_mappings", LabelMappingsResource),
)
