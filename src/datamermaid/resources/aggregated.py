"""The aggregated observation views, ``/projects/{project_id}/<family>/...``.

Every protocol publishes its data three times over, denormalized: one row per
observation, one per sample unit, and one per sample event, each already joined
to its site, management regime and project.  These are the routes analysts pull
into a DataFrame:

```python
project = client.projects(project_id)
project.beltfishes.observations(sample_date_after="2018-01-01").to_df()
project.benthicpits.sample_events().to_df()
```

The seven families differ only in their route and their columns, so each is an
[`.AggregatedViewFamily`][]
describing the former, and all of them share one
[`.AggregatedFamilyResource`][]
and one light [`AggregatedRecord`][datamermaid.models.AggregatedRecord], whose declared
fields are the few every view has in common.  Bleaching is the exception: it publishes
two observation views, so it gets
[`.BleachingQCFamilyResource`][].

Sub-route names follow ``mermaid-api``'s ``src/api/urls.py`` (dev branch).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..models import AggregatedRecord
from .base import Resource, project_path

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..client import MermaidClient
    from ..pagination import PaginatedList

__all__ = [
    "AGGREGATED_FAMILIES",
    "BELTFISHES",
    "BELTINVERTS",
    "BENTHICLITS",
    "BENTHICPITS",
    "BENTHICPQTS",
    "BLEACHINGQCS",
    "HABITATCOMPLEXITIES",
    "AggregatedFamilyResource",
    "AggregatedViewFamily",
    "BleachingQCFamilyResource",
]


@dataclass(frozen=True)
class AggregatedViewFamily:
    """One protocol's aggregated views, as route segments under a project.

    [`.family`][] is both the
    first segment of the route and the attribute the
    [`ProjectContext`][datamermaid.resources.project_context.ProjectContext] exposes it
    as, e.g. ``beltfishes`` for ``/projects/{id}/beltfishes/``.
    """

    #: First route segment, and the context attribute, e.g. ``"beltfishes"``.
    family: str
    #: Sub-route of the observation view, e.g. ``"obstransectbeltfishes"``.
    observations: str
    #: Further observation views, as ``(method name, sub-route)`` pairs.
    extra_observations: tuple[tuple[str, str], ...] = ()
    #: Sub-route of the sample unit view.
    sample_units: str = "sampleunits"
    #: Sub-route of the sample event view.
    sample_events: str = "sampleevents"

    @property
    def views(self) -> dict[str, str]:
        """Resource method name -> sub-route, for every view of the family."""

        return {
            "observations": self.observations,
            **dict(self.extra_observations),
            "sample_units": self.sample_units,
            "sample_events": self.sample_events,
        }

    def route(self, sub_route: str) -> str:
        """``<family>/<sub_route>/``, the route below the project."""

        return f"{self.family}/{sub_route}/"


class AggregatedFamilyResource(Resource[AggregatedRecord]):
    """One protocol's aggregated views for one project.

    Each method returns the same lazy
    [`PaginatedList`][datamermaid.pagination.PaginatedList] as every other list route,
    over [`AggregatedRecord`][datamermaid.models.AggregatedRecord] rows.  Keyword arguments
    become query parameters, so the API's own filters pass straight through:
    ``sample_date_before`` / ``sample_date_after``, ``depth_min`` / ``depth_max``,
    ``site_id``, ``management_id``, ``label``, ``observers``, the protocol's own
    filters, and the parameters every list route understands (``limit``,
    ``ordering``, ``fields``).  An argument whose value is ``None`` is left out.
    """

    model = AggregatedRecord

    def __init__(
        self, client: MermaidClient, project_id: str, family: AggregatedViewFamily
    ) -> None:
        super().__init__(client)
        self.project_id = project_id
        self.family = family
        self.path = project_path(project_id, f"{family.family}/")

    def _view(self, sub_route: str, filters: dict[str, Any]) -> PaginatedList[AggregatedRecord]:
        return self._list(self._url(sub_route), params=filters)

    def observations(self, **filters: Any) -> PaginatedList[AggregatedRecord]:
        """One row per observation, with its sample unit and site joined in."""

        return self._view(self.family.observations, filters)

    def sample_units(self, **filters: Any) -> PaginatedList[AggregatedRecord]:
        """One row per sample unit, with the protocol's per-unit aggregates."""

        return self._view(self.family.sample_units, filters)

    def sample_events(self, **filters: Any) -> PaginatedList[AggregatedRecord]:
        """One row per sample event, averaged over the event's sample units."""

        return self._view(self.family.sample_events, filters)


class BleachingQCFamilyResource(AggregatedFamilyResource):
    """The bleaching quadrat collections, which publish two observation views.

    [`.colonies_bleached`][] is the per-colony view and
    [`.quadrat_benthic_percent`][] the per-quadrat benthic cover view;
    [`.observations`][] is an alias of the former, so the family still answers
    the same three methods as its siblings.
    """

    def colonies_bleached(self, **filters: Any) -> PaginatedList[AggregatedRecord]:
        """One row per bleached colony count (``obscoloniesbleacheds/``)."""

        return self._view(self.family.observations, filters)

    def quadrat_benthic_percent(self, **filters: Any) -> PaginatedList[AggregatedRecord]:
        """One row per quadrat's benthic percent cover (``obsquadratbenthicpercents/``)."""

        return self._view(self.family.views["quadrat_benthic_percent"], filters)

    def observations(self, **filters: Any) -> PaginatedList[AggregatedRecord]:
        """The colonies bleached view; see [`..colonies_bleached`][]."""

        return self.colonies_bleached(**filters)


#: Fish belt surveys, ``/projects/{id}/beltfishes/``.
BELTFISHES = AggregatedViewFamily("beltfishes", "obstransectbeltfishes")
#: Benthic LIT surveys, ``/projects/{id}/benthiclits/``.
BENTHICLITS = AggregatedViewFamily("benthiclits", "obstransectbenthiclits")
#: Benthic PIT surveys, ``/projects/{id}/benthicpits/``.
BENTHICPITS = AggregatedViewFamily("benthicpits", "obstransectbenthicpits")
#: Benthic photo quadrat surveys, ``/projects/{id}/benthicpqts/``.
BENTHICPQTS = AggregatedViewFamily("benthicpqts", "obstransectbenthicpqts")
#: Habitat complexity surveys, ``/projects/{id}/habitatcomplexities/``.
HABITATCOMPLEXITIES = AggregatedViewFamily("habitatcomplexities", "obshabitatcomplexities")
#: Bleaching quadrat collections, ``/projects/{id}/bleachingqcs/``.
BLEACHINGQCS = AggregatedViewFamily(
    "bleachingqcs",
    "obscoloniesbleacheds",
    extra_observations=(("quadrat_benthic_percent", "obsquadratbenthicpercents"),),
)
#: Macroinvertebrate belt surveys, ``/projects/{id}/beltinverts/``.
BELTINVERTS = AggregatedViewFamily("beltinverts", "obstransectbeltinverts")

#: Every aggregated family, in the order the context exposes them.
AGGREGATED_FAMILIES: tuple[AggregatedViewFamily, ...] = (
    BELTFISHES,
    BENTHICLITS,
    BENTHICPITS,
    BENTHICPQTS,
    HABITATCOMPLEXITIES,
    BLEACHINGQCS,
    BELTINVERTS,
)
