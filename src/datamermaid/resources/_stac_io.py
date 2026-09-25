"""pystac-client I/O sent through a ``MermaidClient``.

Imported only once pystac-client is known to be installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pystac_client.exceptions import APIError
from pystac_client.stac_api_io import StacApiIO

from ..exceptions import MermaidConnectionError, NotFoundError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..client import MermaidClient


class CatalogNotFoundError(NotFoundError, APIError):  # type: ignore[misc]
    """A 404 from the covariates catalog.

    Also a pystac-client ``APIError``, so pystac-client code that turns a 404
    into ``None`` (``CollectionClient.get_item``) keeps working.
    """


class ClientStacIO(StacApiIO):
    """Catalog reads with the client's timeout, retries, throttle and errors.

    Every request is sent with ``public=True``, so it carries none of the
    client's MERMAID credentials or extra headers.  Failures raise the usual
    [`MermaidError`][datamermaid.exceptions.MermaidError] subclasses.
    """

    def __init__(self, client: MermaidClient) -> None:
        super().__init__(max_retries=None)
        self._client = client

    def request(
        self,
        href: str,
        method: str | None = None,
        headers: dict[str, str] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> str:
        method = method or "GET"
        if method == "POST":
            options: dict[str, Any] = {"json": parameters}
        else:
            options = {"params": parameters or None}
        try:
            response = self._client.request(method, href, headers=headers, public=True, **options)
        except NotFoundError as exc:
            error = CatalogNotFoundError(
                str(exc),
                status_code=exc.status_code,
                url=exc.url,
                body=exc.body,
                response=exc.response,
            )
            raise error from exc
        try:
            return response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MermaidConnectionError(f"{method} {href} returned a non-UTF-8 body") from exc
