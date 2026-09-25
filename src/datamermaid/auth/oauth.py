"""OAuth 2.0 credentials for [`MermaidClient`][datamermaid.client.MermaidClient].

[`OAuth`][datamermaid.auth.oauth.OAuth] owns the boring half of logging in - picking a
grant, caching the result, noticing expiry and refreshing - and delegates the
interactive half to the flows in [`datamermaid.auth.flows`][datamermaid.auth.flows].
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import httpx

from .._version import __version__
from ..exceptions import AuthFlowError
from .base import Auth
from .callback_server import LoopbackCallbackServer
from .config import Auth0Config
from .flows import (
    DeviceFlow,
    Flow,
    FlowContext,
    ImplicitFlow,
    ManualPasteFlow,
    PkceFlow,
    open_browser,
    post_form,
)
from .token_cache import TokenCache, TokenSet

logger = logging.getLogger(__name__)

__all__ = ["FLOWS", "FlowName", "OAuth", "is_headless", "login", "logout"]

FlowName = Literal["auto", "pkce", "implicit", "device", "manual"]

FLOWS: dict[str, type[Flow]] = {
    flow.name: flow for flow in (PkceFlow, ImplicitFlow, DeviceFlow, ManualPasteFlow)
}

DEFAULT_FLOW_TIMEOUT = 300.0


def is_headless() -> bool:
    """Guess whether this terminal can reach a browser on the same machine.

    True for an SSH session, and for a Unix desktop with no display server.
    """

    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return True
    if sys.platform.startswith(("linux", "freebsd", "openbsd", "netbsd")):
        return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return False


class OAuth(Auth):
    """Log in to MERMAID through Auth0 and keep the token fresh.

    The first request triggers a login unless a usable token is already cached.
    Renewal is single-flight: when several threads sharing one client find the
    token expired, one of them refreshes it (or logs in) and the others wait
    for, then use, the result.  Pass ``interactive=False`` to raise
    [`AuthFlowError`][datamermaid.exceptions.AuthFlowError] instead of prompting, which
    is what an unattended job wants.

    Args:
        flow: Which grant to run, or a [`Flow`][datamermaid.auth.flows.Flow]
            instance.  ``"auto"`` picks ``pkce`` on a desktop and ``device``
            (falling back to ``manual``) on a headless terminal.
        domain: Auth0 tenant, e.g. ``datamermaid.auth0.com``.
        client_id: Public Auth0 client id.
        audience: API identifier the access token must be valid for.
        scope: Space-separated OAuth scopes.  ``offline_access`` is what makes
            a refresh token available.
        cache: A [`TokenCache`][datamermaid.auth.token_cache.TokenCache], a path to
            use instead of the default one, or ``False`` to keep tokens in
            memory only.
        redirect_port: Loopback port for the redirect-based grants.  ``0``
            picks an ephemeral one, which needs the tenant to allow
            port-agnostic callback URLs.
        redirect_host: Host name used to build the redirect URI; the socket
            always listens on ``127.0.0.1``.
        timeout: Seconds to wait for the user to finish the login.
        interactive: Whether a login may prompt.
        env: Whether the ``MERMAID_*`` environment variables are consulted.

    Raises:
        ValueError: If ``flow`` names a grant that does not exist.

    Attributes:
        config: The resolved [`Auth0Config`][datamermaid.auth.config.Auth0Config].
        cache: The token cache in use, or ``None``.

    Example:
        ```python
        from datamermaid import MermaidClient, OAuth

        with MermaidClient(auth=OAuth(flow="device")) as client:
            print(client.me().full_name)
        ```
    """

    def __init__(
        self,
        *,
        flow: FlowName | Flow = "auto",
        domain: str | None = None,
        client_id: str | None = None,
        audience: str | None = None,
        scope: str | None = None,
        cache: TokenCache | Path | str | bool = True,
        redirect_port: int = 0,
        redirect_host: str = "localhost",
        timeout: float = DEFAULT_FLOW_TIMEOUT,
        interactive: bool = True,
        env: bool = True,
        browser_opener: Callable[[str], bool] | None = None,
        prompt: Callable[[str], str] | None = None,
        printer: Callable[[str], None] | None = None,
        http_client: httpx.Client | None = None,
        server_factory: Callable[..., LoopbackCallbackServer] | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(flow, str) and flow != "auto" and flow not in FLOWS:
            raise ValueError(f"unknown flow {flow!r}; choose from auto, {', '.join(FLOWS)}")

        self.config = Auth0Config.resolve(
            domain=domain,
            client_id=client_id,
            audience=audience,
            scope=scope,
            env=env,
        )
        self.flow = flow
        self.interactive = interactive
        self.timeout = timeout
        self.redirect_port = redirect_port
        self.redirect_host = redirect_host

        if isinstance(cache, TokenCache):
            self.cache: TokenCache | None = cache
        elif cache is False:
            self.cache = None
        elif cache is True:
            self.cache = TokenCache()
        else:
            self.cache = TokenCache(cache)

        self._tokens: TokenSet | None = None
        # Guards every read and renewal of `_tokens`.  Re-entrant because
        # `access_token` calls `login`, which calls `_refresh`.
        self._lock = threading.RLock()
        # The access token this thread last put on a request, so `refresh` can
        # tell whether the rejected token has already been replaced.
        self._applied = threading.local()
        self._browser_opener = browser_opener or open_browser
        self._prompt = prompt
        self._printer = printer
        self._http = http_client
        self._server_factory = server_factory
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._now = now or time.time

    def __repr__(self) -> str:
        flow = self.flow if isinstance(self.flow, str) else self.flow.name
        return (
            f"OAuth(flow={flow!r}, domain={self.config.domain!r}, "
            f"authenticated={self._tokens is not None})"
        )

    # -- credentials ------------------------------------------------------

    @property
    def tokens(self) -> TokenSet | None:
        """The token set in memory or in the cache, without logging in."""

        with self._lock:
            if self._tokens is None and self.cache is not None:
                self._tokens = self.cache.load(self.config.cache_key)
            return self._tokens

    def access_token(self) -> str:
        """A valid access token, refreshing or logging in if need be."""

        with self._lock:
            tokens = self.tokens
            if tokens is not None and not tokens.is_expired(now=self._now()):
                return tokens.access_token
            # `login` refreshes when it can and only prompts as a last resort.
            return self.login().access_token

    def apply(self, request: httpx.Request) -> None:
        token = self.access_token()
        self._applied.token = token
        request.headers["Authorization"] = f"Bearer {token}"

    def should_refresh(self, response: httpx.Response) -> bool:
        """Retry a rejected request once, but only if renewal could help."""

        tokens = self.tokens
        if tokens is None:
            return False
        return tokens.refresh_token is not None or tokens.is_expired(now=self._now())

    def refresh(self) -> None:
        """Renew the credentials, falling back to a fresh login.

        If another thread renewed them after this thread's request was sent,
        the new token is used as it is, without renewing again.
        """

        with self._lock:
            tokens = self.tokens
            rejected = getattr(self._applied, "token", None)
            if (
                tokens is not None
                and rejected is not None
                and tokens.access_token != rejected
                and not tokens.is_expired(now=self._now())
            ):
                return
            if tokens is not None and tokens.refresh_token is not None:
                try:
                    self._refresh(tokens)
                except AuthFlowError as exc:
                    logger.info("token refresh failed, logging in again: %s", exc)
                    self._tokens = None
                else:
                    return
            self.login(force=True)

    # -- flows ------------------------------------------------------------

    def login(self, *, force: bool = False) -> TokenSet:
        """Obtain tokens interactively and cache them.

        A cached token that is still valid (or refreshable) is reused unless
        ``force`` is set.  Only one login or refresh runs at a time; a thread
        that arrives while one is running waits for it.
        """

        with self._lock:
            if not force:
                tokens = self.tokens
                if tokens is not None and not tokens.is_expired(now=self._now()):
                    return tokens
                if tokens is not None and tokens.refresh_token is not None:
                    try:
                        return self._refresh(tokens)
                    except AuthFlowError:
                        self._tokens = None

            if not self.interactive:
                raise AuthFlowError(
                    "no usable cached MERMAID token and interactive login is disabled; "
                    "run datamermaid.login() or set MERMAID_API_KEY"
                )

            with self._session() as ctx:
                tokens = self._select_flow(ctx).authorize(self.config, ctx)
            return self._store(tokens)

    def logout(self) -> bool:
        """Forget the cached tokens for this tenant."""

        with self._lock:
            self._tokens = None
            if self.cache is None:
                return False
            return self.cache.clear(self.config.cache_key)

    def _select_flow(self, ctx: FlowContext) -> Flow:
        """Turn ``flow=`` into the object that will run."""

        if isinstance(self.flow, Flow):
            return self.flow
        if self.flow != "auto":
            return FLOWS[self.flow]()
        if not is_headless():
            return PkceFlow()

        # No local browser: the device grant is the pleasant option, but the
        # tenant may not have it enabled for this client.  The probe doubles as
        # the flow's first request, so the user only ever sees one code.
        try:
            authorization = DeviceFlow().request_device_code(self.config, ctx)
        except AuthFlowError:
            return ManualPasteFlow()
        return DeviceFlow(authorization)

    def _refresh(self, tokens: TokenSet) -> TokenSet:
        """Exchange the refresh token for a new access token."""

        if tokens.refresh_token is None:  # pragma: no cover - guarded by callers
            raise AuthFlowError("no refresh token is available")
        logger.info("refreshing the MERMAID access token")
        with self._session() as ctx:
            payload = post_form(
                ctx,
                self.config.token_url,
                {
                    "grant_type": "refresh_token",
                    "client_id": self.config.client_id,
                    "refresh_token": tokens.refresh_token,
                },
                expect="token refresh",
            )
        return self._store(tokens.merged_with(TokenSet.from_response(payload, now=self._now())))

    def _store(self, tokens: TokenSet) -> TokenSet:
        self._tokens = tokens
        if self.cache is not None:
            self.cache.save(self.config.cache_key, tokens)
        return tokens

    @contextmanager
    def _session(self) -> Iterator[FlowContext]:
        """A flow context, with an HTTP client that lives only as long as needed."""

        if self._http is not None:
            yield self._context(self._http)
            return
        headers = {"User-Agent": f"datamermaid/{__version__}"}
        with httpx.Client(timeout=self.timeout, headers=headers) as http:
            yield self._context(http)

    def _context(self, http: httpx.Client) -> FlowContext:
        ctx = FlowContext(
            http=http,
            open_browser=self._browser_opener,
            clock=self._clock,
            sleep=self._sleep,
            now=self._now,
            redirect_port=self.redirect_port,
            redirect_host=self.redirect_host,
            timeout=self.timeout,
        )
        if self._server_factory is not None:
            ctx.server_factory = self._server_factory
        if self._prompt is not None:
            ctx.prompt = self._prompt
        if self._printer is not None:
            ctx.printer = self._printer
        return ctx


def login(*, force: bool = False, **kwargs: Any) -> OAuth:
    """Log in to MERMAID and cache the tokens.

    Any keyword [`OAuth`][datamermaid.auth.oauth.OAuth] accepts may be passed through, e.g.
    ``datamermaid.login(flow="device")``.  The returned object can be handed
    to ``MermaidClient(auth=...)``, though a plain ``MermaidClient()`` will
    also pick the cached tokens up.
    """

    auth = OAuth(**kwargs)
    auth.login(force=force)
    return auth


def logout(**kwargs: Any) -> bool:
    """Discard the cached MERMAID tokens.  Returns whether any were found."""

    return OAuth(**kwargs).logout()
