"""Endpoint wrappers exposed through :class:`~datamermaid.client.MermaidClient`."""

from __future__ import annotations

from .base import Resource
from .me import MeResource
from .projects import ProjectsResource

__all__ = ["MeResource", "ProjectsResource", "Resource"]
