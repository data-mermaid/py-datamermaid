"""The ``/me/`` endpoint."""

from __future__ import annotations

from ..models import Me
from .base import Resource

__all__ = ["MeResource"]


class MeResource(Resource[Me]):
    """The profile owning the credentials in use."""

    path = "me/"
    model = Me

    def get(self) -> Me:
        """Fetch the authenticated profile (``GET /me/``)."""

        return self._get(self.path)
