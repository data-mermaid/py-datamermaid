"""The ``/projects/`` endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Project
from .base import ReadOnlyResource
from .project_context import ProjectContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..client import MermaidClient

__all__ = ["ProjectsResource"]


class ProjectsResource(ReadOnlyResource[Project]):
    """Read access to MERMAID projects.

    Filters: ``showall`` (include projects the credentials are not a member
    of), ``name``, ``country``, ``tags``, ``status``.

    Calling the resource with a project id (or a
    :class:`~datamermaid.models.Project`) returns a
    :class:`~datamermaid.resources.project_context.ProjectContext`, the handle
    on everything recorded under that project:

        >>> client.projects(project_id).sites.list()  # doctest: +SKIP
        >>> client.projects(client.projects.get(project_id))  # doctest: +SKIP
    """

    path = "projects/"
    model = Project

    def __init__(self, client: MermaidClient) -> None:
        super().__init__(client)
        self._contexts: dict[str, ProjectContext] = {}

    def __call__(self, project: str | Project) -> ProjectContext:
        """Return the handle on one project's nested collections.

        The handle is cached, so the same project id always yields the same
        object.  Building one issues no request.
        """

        context = ProjectContext(self._client, project)
        return self._contexts.setdefault(context.project_id, context)
