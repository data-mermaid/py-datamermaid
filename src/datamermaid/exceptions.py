"""Exception hierarchy for the MERMAID SDK.

Every error raised by this package derives from :class:`MermaidError`, so
``except MermaidError`` is always enough to catch SDK failures.  HTTP failures
are mapped to dedicated subclasses by :func:`raise_for_status`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

__all__ = [
    "AuthFlowError",
    "AuthTimeoutError",
    "AuthenticationError",
    "MermaidAPIError",
    "MermaidConnectionError",
    "MermaidError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "raise_for_status",
]


class MermaidError(Exception):
    """Base class for every error raised by ``datamermaid``."""


class MermaidConnectionError(MermaidError):
    """The request never produced a response (DNS, TLS, timeout, reset)."""


class AuthFlowError(MermaidError):
    """An interactive login could not be completed.

    Distinct from :class:`AuthenticationError`, which is the API rejecting
    credentials that were sent.  ``payload`` holds the OAuth error body when
    the provider supplied one.
    """

    def __init__(self, message: str, *, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload: dict[str, Any] = payload or {}


class AuthTimeoutError(AuthFlowError):
    """The user did not finish the login before the flow gave up."""


class MermaidAPIError(MermaidError):
    """The API returned an unsuccessful HTTP status code."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        url: str | None = None,
        body: Any = None,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.body = body
        self.response = response


class AuthenticationError(MermaidAPIError):
    """The credentials are missing, invalid, or insufficient (401/403)."""


class NotFoundError(MermaidAPIError):
    """The requested resource does not exist (404)."""


class RateLimitError(MermaidAPIError):
    """The client is being throttled (429)."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServerError(MermaidAPIError):
    """The API failed to handle the request (5xx)."""


def _decode_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _detail(body: Any) -> str | None:
    if isinstance(body, dict):
        for key in ("detail", "message", "error"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
        return None
    if isinstance(body, str) and body.strip():
        return body.strip()[:200]
    return None


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header into a delay in seconds.

    Both forms allowed by RFC 9110 are understood: delta-seconds and an HTTP
    date, which is converted into the seconds remaining from now.  Anything
    unparseable (or already in the past) yields ``None``.
    """

    if not value:
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        return seconds if seconds >= 0 else None

    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delay = (when - datetime.now(timezone.utc)).total_seconds()
    return max(delay, 0.0)


def raise_for_status(response: httpx.Response) -> None:
    """Raise the exception matching ``response``'s status code.

    Successful (2xx) responses return ``None``.
    """

    status = response.status_code
    if status < 400:
        return

    body = _decode_body(response)
    detail = _detail(body)
    url = str(response.request.url) if response.request is not None else None
    message = f"{status} {response.reason_phrase or 'Error'} for {url}"
    if detail:
        message = f"{message}: {detail}"

    kwargs: dict[str, Any] = {
        "status_code": status,
        "url": url,
        "body": body,
        "response": response,
    }

    if status in (401, 403):
        raise AuthenticationError(message, **kwargs)
    if status == 404:
        raise NotFoundError(message, **kwargs)
    if status == 429:
        raise RateLimitError(
            message,
            retry_after=parse_retry_after(response.headers.get("Retry-After")),
            **kwargs,
        )
    if status >= 500:
        raise ServerError(message, **kwargs)
    raise MermaidAPIError(message, **kwargs)
