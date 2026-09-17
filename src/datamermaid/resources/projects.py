"""The ``/projects/`` endpoints."""

from __future__ import annotations

from ..models import Project
from .base import ReadOnlyResource

__all__ = ["ProjectsResource"]


class ProjectsResource(ReadOnlyResource[Project]):
    """Read access to MERMAID projects.

    Filters: ``showall`` (include projects the credentials are not a member
    of), ``name``, ``country``, ``tags``, ``status``.
    """

    path = "projects/"
    model = Project
