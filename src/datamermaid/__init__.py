"""Python SDK for the MERMAID coral reef monitoring API.

Quickstart:
    >>> from datamermaid import MermaidClient
    >>> with MermaidClient(api_key="mmd_abc.def") as client:  # doctest: +SKIP
    ...     print(client.me().full_name)
    ...     projects = client.projects.list()

Without an API key, log in through the browser once and the token is reused:
    >>> import datamermaid
    >>> datamermaid.login()  # doctest: +SKIP
    >>> with datamermaid.MermaidClient() as client:  # doctest: +SKIP
    ...     print(client.me().full_name)
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
from .resources import ProjectContext

__all__ = [
    "BASE_URL_ENV_VAR",
    "DEFAULT_BASE_URL",
    "DEV_BASE_URL",
    "APIKeyAuth",
    "APIModel",
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
