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
from .client import BASE_URL_ENV_VAR, DEFAULT_BASE_URL, DEV_BASE_URL, MermaidClient
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
)
from .pagination import PaginatedList
from .resources import (
    AggregatedFamilyResource,
    AggregatedViewFamily,
    BleachingQCFamilyResource,
    ProjectContext,
)

__all__ = [
    "BASE_URL_ENV_VAR",
    "DEFAULT_BASE_URL",
    "DEV_BASE_URL",
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
    "BeltFishMethod",
    "BeltInvertMethod",
    "BenthicAttribute",
    "BenthicLITMethod",
    "BenthicPITMethod",
    "BenthicPhotoQuadratTransectMethod",
    "BenthicTransect",
    "BleachingQCFamilyResource",
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
    "RateLimitError",
    "SampleEvent",
    "SampleUnit",
    "SampleUnitMethod",
    "ServerError",
    "Site",
    "SummarySampleEvent",
    "TokenCache",
    "TokenSet",
    "Transect",
    "__version__",
    "login",
    "logout",
]
