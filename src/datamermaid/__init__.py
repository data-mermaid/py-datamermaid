"""Python SDK for the MERMAID coral reef monitoring API.

Every endpoint is reached through `MermaidClient`, and every list route answers
with a lazy, filterable collection that can be exported to a DataFrame.

Example:
    ```python
    from datamermaid import MermaidClient

    with MermaidClient(api_key="mmd_abc.def") as client:
        print(client.me().full_name)
        for project in client.projects.list():
            print(project.name, project.num_sites)
    ```

    Without an API key, log in through the browser once and the token is reused
    by every later client:

    ```python
    import datamermaid

    datamermaid.login()
    with datamermaid.MermaidClient() as client:
        print(client.me().full_name)
    ```

See the guides at <https://data-mermaid.github.io/py-datamermaid/> for the rest.
"""

from __future__ import annotations

from ._version import __version__
from .auth import (
    AnonymousAuth,
    APIKeyAuth,
    Auth,
    Auth0Config,
    OAuth,
    TokenCache,
    TokenSet,
    login,
    logout,
)
from .batch import Batch, BatchFailure, BatchStream
from .client import (
    BASE_URL_ENV_VAR,
    COVARIATES_URL_ENV_VAR,
    DEFAULT_BASE_URL,
    DEFAULT_COVARIATES_URL,
    DEFAULT_ZONAL_STATS_URL,
    DEV_BASE_URL,
    ZONAL_STATS_URL_ENV_VAR,
    MermaidClient,
)
from .exceptions import (
    AuthenticationError,
    AuthFlowError,
    AuthTimeoutError,
    MermaidAPIError,
    MermaidConnectionError,
    MermaidError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from .geometry import GeometryLike, HasGeoInterface, to_aoi
from .models import (
    AggregatedRecord,
    APIModel,
    BeltFishMethod,
    BeltInvertMethod,
    BenthicAttribute,
    BenthicLITMethod,
    BenthicPhotoQuadratTransectMethod,
    BenthicPITMethod,
    BenthicTransect,
    BleachingQuadratCollectionMethod,
    FishBeltTransect,
    FishFamily,
    FishGenus,
    FishSize,
    FishSpecies,
    HabitatComplexityMethod,
    InvertAttribute,
    InvertBeltTransect,
    InvertSpecies,
    LabelMapping,
    Management,
    Me,
    Observer,
    Project,
    ProjectMembership,
    ProjectProfile,
    ProjectTag,
    QuadratCollection,
    QuadratTransect,
    SampleEvent,
    SampleUnit,
    SampleUnitMethod,
    Site,
    SummarySampleEvent,
    Transect,
    ZonalStatsResult,
)
from .pagination import PaginatedList
from .resources import (
    ZONAL_STATS_ENDPOINTS,
    AggregatedFamilyResource,
    AggregatedViewFamily,
    BaseZonalStats,
    BleachingQCFamilyResource,
    CovariateAsset,
    CovariateCollection,
    CovariateItem,
    Covariates,
    CovariateSearch,
    ProjectContext,
    RasterStacStats,
    RasterStats,
    ResponseCache,
    Stat,
    VectorStacStats,
    VectorStats,
    WeightingMethod,
    ZonalStats,
)
from .resources.zonal_job import (
    SourceInput,
    StacItemLike,
    StacSearchLike,
    ZonalJob,
    ZonalSource,
    ZonalTask,
)

__all__ = [
    "BASE_URL_ENV_VAR",
    "COVARIATES_URL_ENV_VAR",
    "DEFAULT_BASE_URL",
    "DEFAULT_COVARIATES_URL",
    "DEFAULT_ZONAL_STATS_URL",
    "DEV_BASE_URL",
    "ZONAL_STATS_ENDPOINTS",
    "ZONAL_STATS_URL_ENV_VAR",
    "APIKeyAuth",
    "APIModel",
    "AggregatedFamilyResource",
    "AggregatedRecord",
    "AggregatedViewFamily",
    "AnonymousAuth",
    "Auth",
    "Auth0Config",
    "AuthFlowError",
    "AuthTimeoutError",
    "AuthenticationError",
    "BaseZonalStats",
    "Batch",
    "BatchFailure",
    "BatchStream",
    "BeltFishMethod",
    "BeltInvertMethod",
    "BenthicAttribute",
    "BenthicLITMethod",
    "BenthicPITMethod",
    "BenthicPhotoQuadratTransectMethod",
    "BenthicTransect",
    "BleachingQCFamilyResource",
    "BleachingQuadratCollectionMethod",
    "CovariateAsset",
    "CovariateCollection",
    "CovariateItem",
    "CovariateSearch",
    "Covariates",
    "FishBeltTransect",
    "FishFamily",
    "FishGenus",
    "FishSize",
    "FishSpecies",
    "GeometryLike",
    "HabitatComplexityMethod",
    "HasGeoInterface",
    "InvertAttribute",
    "InvertBeltTransect",
    "InvertSpecies",
    "LabelMapping",
    "Management",
    "Me",
    "MermaidAPIError",
    "MermaidClient",
    "MermaidConnectionError",
    "MermaidError",
    "NotFoundError",
    "OAuth",
    "Observer",
    "PaginatedList",
    "Project",
    "ProjectContext",
    "ProjectMembership",
    "ProjectProfile",
    "ProjectTag",
    "QuadratCollection",
    "QuadratTransect",
    "RasterStacStats",
    "RasterStats",
    "RateLimitError",
    "ResponseCache",
    "SampleEvent",
    "SampleUnit",
    "SampleUnitMethod",
    "ServerError",
    "Site",
    "SourceInput",
    "StacItemLike",
    "StacSearchLike",
    "Stat",
    "SummarySampleEvent",
    "TokenCache",
    "TokenSet",
    "Transect",
    "VectorStacStats",
    "VectorStats",
    "WeightingMethod",
    "ZonalJob",
    "ZonalSource",
    "ZonalStats",
    "ZonalStatsResult",
    "ZonalTask",
    "__version__",
    "login",
    "logout",
    "to_aoi",
]
