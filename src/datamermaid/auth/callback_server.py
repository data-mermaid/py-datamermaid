"""A single-shot loopback HTTP server for OAuth redirects.

The browser is sent to ``http://localhost:<port>/`` after the user approves
the login.  For the authorization code grant the parameters arrive in the
query string and can be read directly; for the implicit grant they arrive in
the URL fragment, which never reaches the server, so the page served there
bounces them back as a query string.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import TracebackType
from typing import Literal, cast
from urllib.parse import parse_qsl, urlsplit

from ..exceptions import AuthTimeoutError

__all__ = ["FRAGMENT_PATH", "CallbackMode", "LoopbackCallbackServer", "parse_fragment"]

CallbackMode = Literal["query", "fragment"]

#: Where the implicit-flow page re-posts the fragment it was handed.
FRAGMENT_PATH = "/fragment"

BIND_HOST = "127.0.0.1"
DEFAULT_REDIRECT_HOST = "localhost"
DEFAULT_TIMEOUT = 300.0
_POLL_INTERVAL = 0.5

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>MERMAID</title></head>
<body style="font-family: system-ui, sans-serif; margin: 3rem">
<h1>{heading}</h1><p>{body}</p>
{script}
</body></html>
"""

_DONE_PAGE = _PAGE.format(
    heading="Signed in to MERMAID",
    body="You can close this tab and return to your terminal.",
    script="",
)

_ERROR_PAGE = _PAGE.format(
    heading="Nothing to do here",
    body="This page only handles the MERMAID sign-in redirect.",
    script="",
)

# `location.hash` is client side only, so hand it back as a query string.
_FRAGMENT_PAGE = _PAGE.format(
    heading="Finishing sign-in",
    body="One moment...",
    script=(
        "<script>window.location.replace("
        f"'{FRAGMENT_PATH}?' + window.location.hash.substring(1));</script>"
    ),
)


def parse_fragment(value: str) -> dict[str, str]:
    """Parse the parameters of an OAuth redirect fragment.

    Accepts a whole redirect URL, a bare ``#access_token=...`` fragment, or
    the fragment's contents.
    """

    text = value.strip()
    if "#" in text:
        text = text.split("#", 1)[1]
    text = text.lstrip("?")
    return dict(parse_qsl(text, keep_blank_values=True))


def parse_redirect(value: str) -> dict[str, str]:
    """Parse the parameters of a pasted redirect URL, from either half.

    The authorization code grant answers in the query string and the implicit
    grant in the fragment, so both are considered; the fragment wins when a
    URL somehow carries both.
    """

    text = value.strip()
    parsed = urlsplit(text)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    fragment = parsed.fragment or (text if not parsed.query and "=" in text else "")
    params.update(parse_fragment(fragment) if fragment else {})
    return params


class _CallbackServer(HTTPServer):
    """An :class:`HTTPServer` that remembers the one callback it received."""

    mode: CallbackMode = "query"
    callback_path: str = "/"
    result: dict[str, str] | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "datamermaid"

    def do_GET(self) -> None:
        server = cast("_CallbackServer", self.server)
        parsed = urlsplit(self.path)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))

        if parsed.path == FRAGMENT_PATH:
            server.result = params
            self._respond(200, _DONE_PAGE)
        elif parsed.path != server.callback_path:
            # Favicon and other stray requests must not end the wait.
            self._respond(404, _ERROR_PAGE)
        elif server.mode == "fragment":
            self._respond(200, _FRAGMENT_PAGE)
        else:
            server.result = params
            self._respond(200, _DONE_PAGE)

    def _respond(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        """Keep the stdlib's request log off the user's terminal."""


class LoopbackCallbackServer:
    """Serve exactly one OAuth redirect on ``127.0.0.1``.

    The socket is bound as soon as the object is created so that
    :attr:`redirect_uri` (and therefore the ephemeral port) is known before
    the authorization URL is built.
    """

    def __init__(
        self,
        *,
        mode: CallbackMode = "query",
        port: int = 0,
        redirect_host: str = DEFAULT_REDIRECT_HOST,
        path: str = "/",
        timeout: float = DEFAULT_TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout = timeout
        self.redirect_host = redirect_host
        self.path = path if path.startswith("/") else f"/{path}"
        self._clock = clock
        self._server = _CallbackServer((BIND_HOST, port), _CallbackHandler)
        self._server.mode = mode
        self._server.callback_path = self.path
        self._server.timeout = _POLL_INTERVAL

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def redirect_uri(self) -> str:
        return f"http://{self.redirect_host}:{self.port}{self.path}"

    def wait(self) -> dict[str, str]:
        """Block until the browser hits the callback, or time out."""

        deadline = self._clock() + self.timeout
        while self._server.result is None:
            if self._clock() >= deadline:
                raise AuthTimeoutError(
                    f"timed out after {self.timeout:.0f}s waiting for the browser redirect"
                )
            self._server.handle_request()
        return self._server.result

    def close(self) -> None:
        self._server.server_close()

    def __enter__(self) -> LoopbackCallbackServer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
