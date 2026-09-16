"""The ``/projects/`` endpoints."""

from __future__ import annotations

from typing import Any

from ..models import Project
from ..pagination import PaginatedList
from .base import Resource

__all__ = ["ProjectsResource"]


class ProjectsResource(Resource[Project]):
    """Read access to MERMAID projects."""

    path = "projects/"
    model = Project

    def list(self, **filters: Any) -> PaginatedList[Project]:
        """List projects, lazily fetching pages as they are consumed.

        Keyword arguments are passed through as query parameters, e.g.
        ``list(showall=True, limit=100)``.
        """

        return self._list(params=dict(filters))

    def get(self, project_id: str) -> Project:
        """Fetch a single project by id."""

        return self._get(self._url(project_id))
