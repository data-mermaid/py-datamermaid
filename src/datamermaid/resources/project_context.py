"""The project-scoped endpoints, ``/projects/{project_id}/...``.

Call the projects resource with a project id (or a
[`Project`][datamermaid.models.Project]) to get a
[`ProjectContext`][.ProjectContext], the handle every nested route hangs off:

```python
client.projects(project_id).sites.list()
client.projects(project_id).beltfish_methods.get(method_id)
```

Each collection behaves exactly like a top-level one: a lazy, filterable
[`PaginatedList`][datamermaid.pagination.PaginatedList] from ``.list(**filters)`` and a
single record from ``.get(id)``.  They differ only in their path, so each is a
[`ProjectResource`][.ProjectResource] declaring its route and model.

The context also carries the aggregated views of
[`datamermaid.resources.aggregated`][datamermaid.resources.aggregated]
(``project.beltfishes.observations()`` and its siblings), which are list-only and answer
with flat rows rather than nested models.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar, cast

from ..models import (
    APIModel,
    BeltFishMethod,
    BeltInvertMethod,
    BenthicLITMethod,
    BenthicPhotoQuadratTransectMethod,
    BenthicPITMethod,
    BenthicTransect,
    BleachingQuadratCollectionMethod,
    FishBeltTransect,
    HabitatComplexityMethod,
    Management,
    Observer,
    Project,
    ProjectProfile,
    SampleEvent,
    Site,
)
from .aggregated import (
    BELTFISHES,
    BELTINVERTS,
    BENTHICLITS,
    BENTHICPITS,
    BENTHICPQTS,
    BLEACHINGQCS,
    HABITATCOMPLEXITIES,
    AggregatedFamilyResource,
    AggregatedViewFamily,
    BleachingQCFamilyResource,
)
from .base import ReadOnlyResource, project_path

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..client import MermaidClient

__all__ = [
    "PROJECT_RESOURCES",
    "ProjectBeltFishMethodsResource",
    "ProjectBeltInvertMethodsResource",
    "ProjectBenthicLITMethodsResource",
    "ProjectBenthicPITMethodsResource",
    "ProjectBenthicPQTMethodsResource",
    "ProjectBenthicTransectsResource",
    "ProjectBleachingQCMethodsResource",
    "ProjectContext",
    "ProjectFishBeltTransectsResource",
    "ProjectHabitatComplexityMethodsResource",
    "ProjectManagementsResource",
    "ProjectObserversResource",
    "ProjectProfilesResource",
    "ProjectResource",
    "ProjectSampleEventsResource",
    "ProjectSitesResource",
    "project_path",
]

M = TypeVar("M", bound=APIModel)
#: A resource wrapper cached on a context, for `ProjectContext._resource`.
R = TypeVar("R", bound="ProjectResource[Any]")
#: An aggregated view family, for `ProjectContext._aggregated`.
A = TypeVar("A", bound=AggregatedFamilyResource)


class ProjectResource(ReadOnlyResource[M]):
    """A read-only collection nested under one project.

    Subclasses declare [`route`][.route] and
    [`model`][datamermaid.resources.base.Resource.model]; the project id turns the route
    into a path when the resource is built.
    """

    #: Path segment below the project, e.g. ``"sites/"``.
    route: str

    def __init__(self, client: MermaidClient, project_id: str) -> None:
        super().__init__(client)
        self.project_id = project_id
        self.path = project_path(project_id, self.route)


class ProjectSitesResource(ProjectResource[Site]):
    """The project's reef sites (``sites/``).

    Filters: ``name``, ``country``, ``reef_type``, ``reef_zone``, ``exposure``.
    """

    route = "sites/"
    model = Site


class ProjectManagementsResource(ProjectResource[Management]):
    """The project's management regimes (``managements/``).

    Filters: ``name``, ``est_year``, ``parties``, ``compliance`` and the seven
    rule flags (``no_take``, ``periodic_closure``, ...).
    """

    route = "managements/"
    model = Management


class ProjectObserversResource(ProjectResource[Observer]):
    """Who recorded the project's sample unit methods (``observers/``).

    Filters: ``transectmethod``, ``profile``, ``rank``.
    """

    route = "observers/"
    model = Observer


class ProjectProfilesResource(ProjectResource[ProjectProfile]):
    """The project's members and their roles (``project_profiles/``).

    Filters: ``profile``, ``role``.
    """

    route = "project_profiles/"
    model = ProjectProfile


class ProjectSampleEventsResource(ProjectResource[SampleEvent]):
    """Site-and-date visits the project's sample units belong to (``sampleevents/``).

    Filters: ``site``, ``management`` and the ``sample_date`` range
    (``sample_date_after`` / ``sample_date_before``).
    """

    route = "sampleevents/"
    model = SampleEvent


class ProjectFishBeltTransectsResource(ProjectResource[FishBeltTransect]):
    """The project's fish belt transects (``fishbelttransects/``).

    The transects alone, without the observations recorded on them; for those use
    [`ProjectBeltFishMethodsResource`][..ProjectBeltFishMethodsResource].  Filters:
    ``sample_event``, ``len_surveyed``, ``width``, ``depth``.
    """

    route = "fishbelttransects/"
    model = FishBeltTransect


class ProjectBenthicTransectsResource(ProjectResource[BenthicTransect]):
    """The project's benthic transects (``benthictransects/``).

    Shared by the benthic LIT, benthic PIT and habitat complexity protocols.
    Filters: ``sample_event``, ``len_surveyed``, ``depth``.
    """

    route = "benthictransects/"
    model = BenthicTransect


class ProjectBeltFishMethodsResource(ProjectResource[BeltFishMethod]):
    """Fish belt surveys with their observations (``beltfishtransectmethods/``)."""

    route = "beltfishtransectmethods/"
    model = BeltFishMethod


class ProjectBenthicLITMethodsResource(ProjectResource[BenthicLITMethod]):
    """Benthic LIT surveys with their observations (``benthiclittransectmethods/``)."""

    route = "benthiclittransectmethods/"
    model = BenthicLITMethod


class ProjectBenthicPITMethodsResource(ProjectResource[BenthicPITMethod]):
    """Benthic PIT surveys with their observations (``benthicpittransectmethods/``)."""

    route = "benthicpittransectmethods/"
    model = BenthicPITMethod


class ProjectBenthicPQTMethodsResource(ProjectResource[BenthicPhotoQuadratTransectMethod]):
    """Benthic photo quadrat surveys (``benthicphotoquadrattransectmethods/``)."""

    route = "benthicphotoquadrattransectmethods/"
    model = BenthicPhotoQuadratTransectMethod


class ProjectHabitatComplexityMethodsResource(ProjectResource[HabitatComplexityMethod]):
    """Habitat complexity surveys (``habitatcomplexitytransectmethods/``)."""

    route = "habitatcomplexitytransectmethods/"
    model = HabitatComplexityMethod


class ProjectBleachingQCMethodsResource(ProjectResource[BleachingQuadratCollectionMethod]):
    """Bleaching quadrat collection surveys (``bleachingquadratcollectionmethods/``)."""

    route = "bleachingquadratcollectionmethods/"
    model = BleachingQuadratCollectionMethod


class ProjectBeltInvertMethodsResource(ProjectResource[BeltInvertMethod]):
    """Macroinvertebrate belt surveys (``beltinverttransectmethods/``)."""

    route = "beltinverttransectmethods/"
    model = BeltInvertMethod


#: Context attribute name -> resource class, for every project-scoped route.
PROJECT_RESOURCES: tuple[tuple[str, type[ProjectResource[Any]]], ...] = (
    ("sites", ProjectSitesResource),
    ("managements", ProjectManagementsResource),
    ("observers", ProjectObserversResource),
    ("project_profiles", ProjectProfilesResource),
    ("sample_events", ProjectSampleEventsResource),
    ("fishbelt_transects", ProjectFishBeltTransectsResource),
    ("benthic_transects", ProjectBenthicTransectsResource),
    ("beltfish_methods", ProjectBeltFishMethodsResource),
    ("benthiclit_methods", ProjectBenthicLITMethodsResource),
    ("benthicpit_methods", ProjectBenthicPITMethodsResource),
    ("benthicpqt_methods", ProjectBenthicPQTMethodsResource),
    ("habitatcomplexity_methods", ProjectHabitatComplexityMethodsResource),
    ("bleachingqc_methods", ProjectBleachingQCMethodsResource),
    ("beltinvert_methods", ProjectBeltInvertMethodsResource),
)


class ProjectContext:
    """One project's data, as the collections nested under it.

    Built by calling the projects resource with an id or a
    [`Project`][datamermaid.models.Project].  Nothing is requested when the context
    is built, and each collection below it is a lazy list like every other
    endpoint in the SDK.

    Args:
        client: The client the nested requests are made through.
        project: The project's id, or a [`Project`][datamermaid.models.Project].

    Raises:
        ValueError: If the project id is empty.

    Attributes:
        project_id: The id every route below this handle is built from.

    Example:
        ```python
        project = client.projects(project_id)

        for site in project.sites.list():
            print(site.name)

        # The seven protocols' aggregated views hang off the same handle.
        project.beltfishes.observations().to_df()
        ```
    """

    def __init__(self, client: MermaidClient, project: str | Project) -> None:
        project_id = project.id if isinstance(project, Project) else project
        if not project_id or not str(project_id).strip():
            raise ValueError("a project id is required")

        self._client = client
        self.project_id = str(project_id).strip()
        self._resources: dict[type[Any], Any] = {}
        self._families: dict[str, AggregatedFamilyResource] = {}

    def __repr__(self) -> str:
        return f"ProjectContext(project_id={self.project_id!r})"

    def _resource(self, resource_class: type[R]) -> R:
        """Return this context's single instance of ``resource_class``."""

        resource = self._resources.get(resource_class)
        if resource is None:
            resource = resource_class(self._client, self.project_id)
            self._resources[resource_class] = resource
        return cast("R", resource)

    def _aggregated(self, family: AggregatedViewFamily, resource_class: type[A]) -> A:
        """Return this context's single resource for one aggregated family."""

        resource = self._families.get(family.family)
        if resource is None:
            resource = resource_class(self._client, self.project_id, family)
            self._families[family.family] = resource
        return cast("A", resource)

    @property
    def sites(self) -> ProjectSitesResource:
        """The project's reef sites, ``/projects/{id}/sites/``."""

        return self._resource(ProjectSitesResource)

    @property
    def managements(self) -> ProjectManagementsResource:
        """The project's management regimes, ``/projects/{id}/managements/``."""

        return self._resource(ProjectManagementsResource)

    @property
    def observers(self) -> ProjectObserversResource:
        """Observers credited on the project's surveys, ``/projects/{id}/observers/``."""

        return self._resource(ProjectObserversResource)

    @property
    def project_profiles(self) -> ProjectProfilesResource:
        """The project's members, ``/projects/{id}/project_profiles/``."""

        return self._resource(ProjectProfilesResource)

    @property
    def sample_events(self) -> ProjectSampleEventsResource:
        """The project's sample events, ``/projects/{id}/sampleevents/``."""

        return self._resource(ProjectSampleEventsResource)

    @property
    def fishbelt_transects(self) -> ProjectFishBeltTransectsResource:
        """Fish belt transects, ``/projects/{id}/fishbelttransects/``."""

        return self._resource(ProjectFishBeltTransectsResource)

    @property
    def benthic_transects(self) -> ProjectBenthicTransectsResource:
        """Benthic transects, ``/projects/{id}/benthictransects/``."""

        return self._resource(ProjectBenthicTransectsResource)

    @property
    def beltfish_methods(self) -> ProjectBeltFishMethodsResource:
        """Fish belt surveys, ``/projects/{id}/beltfishtransectmethods/``."""

        return self._resource(ProjectBeltFishMethodsResource)

    @property
    def benthiclit_methods(self) -> ProjectBenthicLITMethodsResource:
        """Benthic LIT surveys, ``/projects/{id}/benthiclittransectmethods/``."""

        return self._resource(ProjectBenthicLITMethodsResource)

    @property
    def benthicpit_methods(self) -> ProjectBenthicPITMethodsResource:
        """Benthic PIT surveys, ``/projects/{id}/benthicpittransectmethods/``."""

        return self._resource(ProjectBenthicPITMethodsResource)

    @property
    def benthicpqt_methods(self) -> ProjectBenthicPQTMethodsResource:
        """Benthic photo quadrat surveys, ``/projects/{id}/benthicphotoquadrattransectmethods/``."""

        return self._resource(ProjectBenthicPQTMethodsResource)

    @property
    def habitatcomplexity_methods(self) -> ProjectHabitatComplexityMethodsResource:
        """Habitat complexity surveys, ``/projects/{id}/habitatcomplexitytransectmethods/``."""

        return self._resource(ProjectHabitatComplexityMethodsResource)

    @property
    def bleachingqc_methods(self) -> ProjectBleachingQCMethodsResource:
        """Bleaching surveys, ``/projects/{id}/bleachingquadratcollectionmethods/``."""

        return self._resource(ProjectBleachingQCMethodsResource)

    @property
    def beltinvert_methods(self) -> ProjectBeltInvertMethodsResource:
        """Macroinvertebrate surveys, ``/projects/{id}/beltinverttransectmethods/``."""

        return self._resource(ProjectBeltInvertMethodsResource)

    # -- aggregated views -------------------------------------------------
    #
    # The denormalized observation / sample unit / sample event routes; see
    # datamermaid/resources/aggregated.py.

    @property
    def beltfishes(self) -> AggregatedFamilyResource:
        """Aggregated fish belt data, ``/projects/{id}/beltfishes/``."""

        return self._aggregated(BELTFISHES, AggregatedFamilyResource)

    @property
    def benthiclits(self) -> AggregatedFamilyResource:
        """Aggregated benthic LIT data, ``/projects/{id}/benthiclits/``."""

        return self._aggregated(BENTHICLITS, AggregatedFamilyResource)

    @property
    def benthicpits(self) -> AggregatedFamilyResource:
        """Aggregated benthic PIT data, ``/projects/{id}/benthicpits/``."""

        return self._aggregated(BENTHICPITS, AggregatedFamilyResource)

    @property
    def benthicpqts(self) -> AggregatedFamilyResource:
        """Aggregated benthic photo quadrat data, ``/projects/{id}/benthicpqts/``."""

        return self._aggregated(BENTHICPQTS, AggregatedFamilyResource)

    @property
    def habitatcomplexities(self) -> AggregatedFamilyResource:
        """Aggregated habitat complexity data, ``/projects/{id}/habitatcomplexities/``."""

        return self._aggregated(HABITATCOMPLEXITIES, AggregatedFamilyResource)

    @property
    def bleachingqcs(self) -> BleachingQCFamilyResource:
        """Aggregated bleaching data, ``/projects/{id}/bleachingqcs/``.

        Two observation views,
        [`colonies_bleached`][datamermaid.resources.aggregated.BleachingQCFamilyResource.colonies_bleached]
        and
        [`quadrat_benthic_percent`][datamermaid.resources.aggregated.BleachingQCFamilyResource.quadrat_benthic_percent].
        """

        return self._aggregated(BLEACHINGQCS, BleachingQCFamilyResource)

    @property
    def beltinverts(self) -> AggregatedFamilyResource:
        """Aggregated macroinvertebrate belt data, ``/projects/{id}/beltinverts/``."""

        return self._aggregated(BELTINVERTS, AggregatedFamilyResource)
