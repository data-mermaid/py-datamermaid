"""The interactive OAuth grants used to obtain a MERMAID access token.

Each flow is a small object with a single :meth:`Flow.authorize` method.
Everything that would otherwise touch the outside world - the browser, the
terminal, the clock, the HTTP client, the loopback server - arrives through a
:class:`FlowContext`, so the tests drive the whole thing without opening a
browser or a socket.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import webbrowser
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from ..exceptions import AuthFlowError, AuthTimeoutError, MermaidConnectionError
from .callback_server import LoopbackCallbackServer, parse_redirect
from .config import Auth0Config
from .token_cache import TokenSet

__all__ = [
    "DEVICE_GRANT_TYPE",
    "DeviceFlow",
    "Flow",
    "FlowContext",
    "ImplicitFlow",
    "ManualPasteFlow",
    "PkceFlow",
    "code_challenge",
    "generate_code_verifier",
    "open_browser",
    "token_set",
]

DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

#: RFC 7636 allows 43-128 characters; 32 random bytes encode to 43.
CODE_VERIFIER_BYTES = 32
DEFAULT_DEVICE_INTERVAL = 5.0
#: Auth0 asks clients that poll too eagerly to add this many seconds.
SLOW_DOWN_INCREMENT = 5.0


def generate_code_verifier(*, nbytes: int = CODE_VERIFIER_BYTES) -> str:
    """A fresh PKCE code verifier (unpadded base64url, RFC 7636 section 4.1)."""

    return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).rstrip(b"=").decode("ascii")


def code_challenge(verifier: str) -> str:
    """The ``S256`` challenge for ``verifier``."""

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def open_browser(url: str) -> bool:
    """Open ``url`` in the system browser, reporting whether that worked."""

    try:
        return webbrowser.open(url)
    except Exception:  # pragma: no cover - platform dependent
        return False


def _default_prompt(message: str) -> str:
    return input(message)


@dataclass
class FlowContext:
    """The outside world a flow is allowed to touch."""

    http: httpx.Client
    open_browser: Callable[[str], bool] = open_browser
    prompt: Callable[[str], str] = _default_prompt
    printer: Callable[[str], None] = print
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    #: Wall-clock source, used only to date token expiry.
    now: Callable[[], float] = time.time
    redirect_port: int = 0
    redirect_host: str = "localhost"
    timeout: float = 300.0
    server_factory: Callable[..., LoopbackCallbackServer] = LoopbackCallbackServer

    def open_server(self, *, mode: str = "query") -> LoopbackCallbackServer:
        return self.server_factory(
            mode=mode,
            port=self.redirect_port,
            redirect_host=self.redirect_host,
            timeout=self.timeout,
        )


@dataclass
class _Authorization:
    """The state a redirect-based grant has to carry across the browser trip."""

    url: str
    state: str
    code_verifier: str | None = None
    redirect_uri: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def post_form(
    ctx: FlowContext,
    url: str,
    data: dict[str, str],
    *,
    expect: str = "token",
) -> dict[str, Any]:
    """POST a form to an OAuth endpoint and return the decoded JSON body.

    Errors are raised as :class:`AuthFlowError` carrying Auth0's own
    ``error``/``error_description``; the caller sees the response only when it
    succeeded.
    """

    try:
        response = ctx.http.post(url, data=data)
    except httpx.TransportError as exc:
        raise MermaidConnectionError(f"POST {url} failed: {exc}") from exc

    payload = _decode_json(response)
    if response.is_success:
        return payload
    raise AuthFlowError(_error_message(payload, response.status_code, expect), payload=payload)


def _decode_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_message(payload: dict[str, Any], status_code: int, expect: str) -> str:
    error = payload.get("error") or f"HTTP {status_code}"
    description = payload.get("error_description")
    message = f"{expect} request failed: {error}"
    return f"{message}: {description}" if description else message


def token_set(payload: dict[str, Any], ctx: FlowContext) -> TokenSet:
    """Build a :class:`TokenSet`, reporting a malformed response as a flow error."""

    try:
        return TokenSet.from_response(payload, now=ctx.now())
    except ValueError as exc:
        raise AuthFlowError(str(exc), payload=payload) from exc


def _check_callback(params: dict[str, str], state: str) -> None:
    if params.get("error"):
        raise AuthFlowError(
            _error_message(dict(params), 400, "authorization"),
            payload=dict(params),
        )
    returned_state = params.get("state")
    if returned_state is not None and not secrets.compare_digest(returned_state, state):
        raise AuthFlowError("authorization state mismatch, discarding the response")


class Flow(ABC):
    """One way of turning a user's consent into a :class:`TokenSet`."""

    #: Value accepted by ``OAuth(flow=...)``.
    name: str

    @abstractmethod
    def authorize(self, config: Auth0Config, ctx: FlowContext) -> TokenSet:
        """Run the grant to completion and return the issued tokens."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    # -- shared helpers ---------------------------------------------------

    def _authorize_url(
        self,
        config: Auth0Config,
        *,
        redirect_uri: str,
        response_type: str,
        offline_access: bool,
    ) -> _Authorization:
        state = secrets.token_urlsafe(24)
        params = {
            "client_id": config.client_id,
            "response_type": response_type,
            "redirect_uri": redirect_uri,
            "scope": config.scopes(offline_access=offline_access),
            "audience": config.audience,
            "state": state,
        }
        verifier: str | None = None
        if response_type == "code":
            verifier = generate_code_verifier()
            params["code_challenge"] = code_challenge(verifier)
            params["code_challenge_method"] = "S256"
        else:
            params["nonce"] = secrets.token_urlsafe(16)
        return _Authorization(
            url=f"{config.authorize_url}?{urlencode(params)}",
            state=state,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
        )

    def _exchange_code(
        self,
        config: Auth0Config,
        ctx: FlowContext,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> TokenSet:
        payload = post_form(
            ctx,
            config.token_url,
            {
                "grant_type": "authorization_code",
                "client_id": config.client_id,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
        )
        return token_set(payload, ctx)

    def _announce(self, ctx: FlowContext, url: str, *, opened: bool) -> None:
        if opened:
            ctx.printer("Opened your browser to complete the MERMAID sign-in.")
        else:
            ctx.printer("Open this URL in a browser to sign in to MERMAID:\n")
            ctx.printer(f"    {url}\n")


class PkceFlow(Flow):
    """Authorization code grant with PKCE, redirected to a loopback server."""

    name = "pkce"

    def authorize(self, config: Auth0Config, ctx: FlowContext) -> TokenSet:
        with ctx.open_server(mode="query") as server:
            authorization = self._authorize_url(
                config,
                redirect_uri=server.redirect_uri,
                response_type="code",
                offline_access=True,
            )
            self._announce(ctx, authorization.url, opened=ctx.open_browser(authorization.url))
            params = server.wait()

        _check_callback(params, authorization.state)
        code = params.get("code")
        if not code:
            raise AuthFlowError("the authorization redirect carried no code")
        assert authorization.code_verifier is not None  # response_type="code"
        return self._exchange_code(
            config,
            ctx,
            code=code,
            verifier=authorization.code_verifier,
            redirect_uri=authorization.redirect_uri,
        )


class ImplicitFlow(Flow):
    """Legacy implicit grant: the token arrives in the redirect fragment.

    Kept for parity with the older MERMAID clients.  It issues no refresh
    token, so the user has to log in again when the access token expires.
    """

    name = "implicit"

    def authorize(self, config: Auth0Config, ctx: FlowContext) -> TokenSet:
        with ctx.open_server(mode="fragment") as server:
            authorization = self._authorize_url(
                config,
                redirect_uri=server.redirect_uri,
                response_type="token",
                offline_access=False,
            )
            self._announce(ctx, authorization.url, opened=ctx.open_browser(authorization.url))
            params = server.wait()

        _check_callback(params, authorization.state)
        return token_set(dict(params), ctx)


class DeviceFlow(Flow):
    """Device authorization grant (RFC 8628), for terminals without a browser.

    The user is given a short code and a URL to open on any other device; the
    terminal polls the token endpoint until they are done.
    """

    name = "device"

    def __init__(self, authorization: dict[str, Any] | None = None) -> None:
        #: A device code already obtained, e.g. while probing tenant support.
        self._authorization = authorization

    def request_device_code(self, config: Auth0Config, ctx: FlowContext) -> dict[str, Any]:
        """Ask Auth0 for a device and user code."""

        return post_form(
            ctx,
            config.device_code_url,
            {
                "client_id": config.client_id,
                "scope": config.scopes(offline_access=True),
                "audience": config.audience,
            },
            expect="device code",
        )

    def authorize(self, config: Auth0Config, ctx: FlowContext) -> TokenSet:
        authorization = self._authorization or self.request_device_code(config, ctx)
        device_code = authorization.get("device_code")
        user_code = authorization.get("user_code")
        verification_uri = authorization.get("verification_uri") or config.issuer
        complete_uri = authorization.get("verification_uri_complete") or verification_uri
        if not device_code or not user_code:
            raise AuthFlowError("the device code response was incomplete")

        ctx.printer("To sign in to MERMAID, open this URL on any device:\n")
        ctx.printer(f"    {verification_uri}\n")
        ctx.printer(f"and enter the code: {user_code}\n")
        if complete_uri != verification_uri:
            ctx.printer(f"Or open {complete_uri} to skip entering it.\n")

        return self._poll(config, ctx, device_code=str(device_code), authorization=authorization)

    def _poll(
        self,
        config: Auth0Config,
        ctx: FlowContext,
        *,
        device_code: str,
        authorization: dict[str, Any],
    ) -> TokenSet:
        interval = _positive_number(authorization.get("interval"), DEFAULT_DEVICE_INTERVAL)
        lifetime = _positive_number(authorization.get("expires_in"), ctx.timeout)
        deadline = ctx.clock() + lifetime
        data = {
            "grant_type": DEVICE_GRANT_TYPE,
            "client_id": config.client_id,
            "device_code": device_code,
        }

        while True:
            if ctx.clock() >= deadline:
                raise AuthTimeoutError(
                    f"timed out after {lifetime:.0f}s waiting for the device code to be approved"
                )
            ctx.sleep(interval)
            try:
                payload = post_form(ctx, config.token_url, data)
            except AuthFlowError as exc:
                error = str(exc.payload.get("error") or "")
                if error == "authorization_pending":
                    continue
                if error == "slow_down":
                    interval += SLOW_DOWN_INCREMENT
                    continue
                raise
            return token_set(payload, ctx)


class ManualPasteFlow(Flow):
    """PKCE without a reachable loopback port: the user pastes the redirect.

    The browser still lands on ``http://localhost:<port>/``, which fails to
    connect when the browser is on a different machine - but the address bar
    holds the authorization code, and pasting that URL back is enough.
    """

    name = "manual"

    #: Port used only to build the redirect URI; nothing listens on it.
    default_port = 1410

    def authorize(self, config: Auth0Config, ctx: FlowContext) -> TokenSet:
        port = ctx.redirect_port or self.default_port
        redirect_uri = f"http://{ctx.redirect_host}:{port}/"
        authorization = self._authorize_url(
            config,
            redirect_uri=redirect_uri,
            response_type="code",
            offline_access=True,
        )

        ctx.printer("Open this URL in a browser on your local machine:\n")
        ctx.printer(f"    {authorization.url}\n")
        ctx.printer(
            "Your browser will be redirected to a page that cannot load. "
            "Copy that address from the browser's address bar."
        )
        answer = ctx.prompt("Paste the redirect URL (or just the code): ").strip()
        if not answer:
            raise AuthFlowError("no redirect URL was pasted")

        params = parse_redirect(answer) if "=" in answer else {"code": answer}
        _check_callback(params, authorization.state)
        code = params.get("code")
        if not code:
            raise AuthFlowError("the pasted redirect URL carried no code")
        assert authorization.code_verifier is not None  # response_type="code"
        return self._exchange_code(
            config,
            ctx,
            code=code,
            verifier=authorization.code_verifier,
            redirect_uri=redirect_uri,
        )


def _positive_number(value: Any, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return default
