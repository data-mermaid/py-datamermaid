"""The ``/choices/`` endpoint.

Unlike every other list route, ``/choices/`` is not paginated: it answers with
a bare JSON array of ``{"name": ..., "data": [...]}`` objects, one per choice
set (``countries``, ``reeftypes``, ``managementparties``, ...).  The detail
route, ``/choices/<name>/``, returns a single one of those objects.  So this
wrapper hands back plain dictionaries rather than models in a
:class:`~datamermaid.pagination.PaginatedList`.
"""

from __future__ import annotations

from typing import Any

from .base import BaseResource

__all__ = ["ChoicesResource"]

#: One choice set: a list of ``{"id": ..., "name": ..., "updated_on": ...}``.
ChoiceSet = list[dict[str, Any]]


def _choice_set(data: Any) -> ChoiceSet:
    """Pull the rows out of one ``{"name": ..., "data": [...]}`` object."""

    if not isinstance(data, dict):
        raise TypeError(f"expected a choice set object, got {type(data).__name__}")
    rows = data.get("data") or []
    if not isinstance(rows, list):
        raise TypeError(f"expected a list of choices, got {type(rows).__name__}")
    return [dict(row) for row in rows]


class ChoicesResource(BaseResource):
    """The controlled vocabularies the API validates submissions against."""

    path = "choices/"

    def fetch(self) -> dict[str, ChoiceSet]:
        """Fetch every choice set, keyed by name (``GET /choices/``)."""

        data = self._client.request_json("GET", self.path)
        if not isinstance(data, list):
            raise TypeError(f"expected a list of choice sets, got {type(data).__name__}")
        sets: dict[str, ChoiceSet] = {}
        for entry in data:
            # `_choice_set` rejects anything that is not a choice set object,
            # so reading `name` off it afterwards is safe.
            rows = _choice_set(entry)
            sets[str(entry.get("name"))] = rows
        return sets

    def get(self, name: str) -> ChoiceSet:
        """Fetch a single choice set by name (``GET /choices/<name>/``)."""

        return _choice_set(self._client.request_json("GET", self._url(name)))
