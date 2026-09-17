"""Endpoint wrappers exposed through :class:`~datamermaid.client.MermaidClient`."""

from __future__ import annotations

from .base import BaseResource, ReadOnlyResource, Resource
from .choices import ChoicesResource
from .me import MeResource
from .projects import ProjectsResource
from .reference import (
    REFERENCE_RESOURCES,
    BenthicAttributesResource,
    FishFamiliesResource,
    FishGeneraResource,
    FishSizesResource,
    FishSpeciesResource,
    InvertAttributesResource,
    InvertSpeciesResource,
    LabelMappingsResource,
    ManagementsResource,
    ProjectTagsResource,
    SitesResource,
    SummarySampleEventsResource,
)

__all__ = [
    "REFERENCE_RESOURCES",
    "BaseResource",
    "BenthicAttributesResource",
    "ChoicesResource",
    "FishFamiliesResource",
    "FishGeneraResource",
    "FishSizesResource",
    "FishSpeciesResource",
    "InvertAttributesResource",
    "InvertSpeciesResource",
    "LabelMappingsResource",
    "ManagementsResource",
    "MeResource",
    "ProjectTagsResource",
    "ProjectsResource",
    "ReadOnlyResource",
    "Resource",
    "SitesResource",
    "SummarySampleEventsResource",
]
