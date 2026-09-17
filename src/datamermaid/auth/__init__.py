"""Authentication strategies for :class:`~datamermaid.client.MermaidClient`.

The client never inspects credentials directly: it only ever asks an
:class:`Auth` instance to stamp an outgoing request.  That seam is what lets
an API key, an OAuth login and anonymous access be swapped for one another
without the client knowing the difference.
"""

from __future__ import annotations

from .base import (
    API_KEY_ENV_VAR,
    AnonymousAuth,
    APIKeyAuth,
    Auth,
    HTTPXAuthAdapter,
    resolve_auth,
)
from .callback_server import LoopbackCallbackServer, parse_fragment, parse_redirect
from .config import (
    AUDIENCE_ENV_VAR,
    AUTH0_DOMAIN_ENV_VAR,
    CLIENT_ID_ENV_VAR,
    DEFAULT_AUDIENCE,
    DEFAULT_AUTH0_DOMAIN,
    DEFAULT_CLIENT_ID,
    DEFAULT_SCOPE,
    Auth0Config,
)
from .flows import (
    DeviceFlow,
    Flow,
    FlowContext,
    ImplicitFlow,
    ManualPasteFlow,
    PkceFlow,
    code_challenge,
    generate_code_verifier,
)
from .jwt import decode_payload, token_expires_at
from .oauth import FLOWS, OAuth, is_headless, login, logout
from .token_cache import TokenCache, TokenSet, default_cache_path

__all__ = [
    "API_KEY_ENV_VAR",
    "AUDIENCE_ENV_VAR",
    "AUTH0_DOMAIN_ENV_VAR",
    "CLIENT_ID_ENV_VAR",
    "DEFAULT_AUDIENCE",
    "DEFAULT_AUTH0_DOMAIN",
    "DEFAULT_CLIENT_ID",
    "DEFAULT_SCOPE",
    "FLOWS",
    "APIKeyAuth",
    "AnonymousAuth",
    "Auth",
    "Auth0Config",
    "DeviceFlow",
    "Flow",
    "FlowContext",
    "HTTPXAuthAdapter",
    "ImplicitFlow",
    "LoopbackCallbackServer",
    "ManualPasteFlow",
    "OAuth",
    "PkceFlow",
    "TokenCache",
    "TokenSet",
    "code_challenge",
    "decode_payload",
    "default_cache_path",
    "generate_code_verifier",
    "is_headless",
    "login",
    "logout",
    "parse_fragment",
    "parse_redirect",
    "resolve_auth",
    "token_expires_at",
]
