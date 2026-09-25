"""Auth0 tenant configuration for the OAuth flows.

The defaults point at MERMAID's production Auth0 tenant.  Every one of them
can be overridden per call or through the environment, so the SDK can also be
pointed at the development tenant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

__all__ = [
    "AUDIENCE_ENV_VAR",
    "AUTH0_DOMAIN_ENV_VAR",
    "CLIENT_ID_ENV_VAR",
    "DEFAULT_AUDIENCE",
    "DEFAULT_AUTH0_DOMAIN",
    "DEFAULT_CLIENT_ID",
    "DEFAULT_SCOPE",
    "Auth0Config",
]

#: Auth0 tenant that issues MERMAID access tokens.
DEFAULT_AUTH0_DOMAIN = "datamermaid.auth0.com"
#: Public (PKCE-capable) client id shared by the MERMAID community clients.
DEFAULT_CLIENT_ID = "6q1XvYG0n75ZaLbFko0gUV4xGud4uPyG"
#: API identifier the issued access token must be valid for.
DEFAULT_AUDIENCE = "https://api.datamermaid.org"
#: ``offline_access`` is what makes a refresh token available.
DEFAULT_SCOPE = "openid profile email offline_access"

AUTH0_DOMAIN_ENV_VAR = "MERMAID_AUTH0_DOMAIN"
CLIENT_ID_ENV_VAR = "MERMAID_CLIENT_ID"
AUDIENCE_ENV_VAR = "MERMAID_AUDIENCE"


def _normalise_domain(domain: str) -> str:
    """Reduce ``https://tenant.auth0.com/`` and friends to ``tenant.auth0.com``."""

    text = domain.strip()
    if "//" in text:
        text = urlsplit(text).netloc or text
    return text.strip("/")


@dataclass(frozen=True)
class Auth0Config:
    """Where to send OAuth requests and on whose behalf."""

    domain: str = DEFAULT_AUTH0_DOMAIN
    client_id: str = DEFAULT_CLIENT_ID
    audience: str = DEFAULT_AUDIENCE
    scope: str = DEFAULT_SCOPE

    def __post_init__(self) -> None:
        normalised = _normalise_domain(self.domain)
        if not normalised:
            raise ValueError("domain must be a non-empty Auth0 domain")
        if not self.client_id.strip():
            raise ValueError("client_id must be a non-empty string")
        object.__setattr__(self, "domain", normalised)
        object.__setattr__(self, "client_id", self.client_id.strip())

    @classmethod
    def resolve(
        cls,
        *,
        domain: str | None = None,
        client_id: str | None = None,
        audience: str | None = None,
        scope: str | None = None,
        env: bool = True,
    ) -> Auth0Config:
        """Build a config from explicit arguments, then the environment, then defaults."""

        def pick(value: str | None, var: str, default: str) -> str:
            if value is not None and value.strip():
                return value.strip()
            if env:
                from_env = os.environ.get(var, "").strip()
                if from_env:
                    return from_env
            return default

        return cls(
            domain=pick(domain, AUTH0_DOMAIN_ENV_VAR, DEFAULT_AUTH0_DOMAIN),
            client_id=pick(client_id, CLIENT_ID_ENV_VAR, DEFAULT_CLIENT_ID),
            audience=pick(audience, AUDIENCE_ENV_VAR, DEFAULT_AUDIENCE),
            scope=(scope or DEFAULT_SCOPE).strip(),
        )

    # -- endpoints --------------------------------------------------------

    @property
    def issuer(self) -> str:
        return f"https://{self.domain}/"

    @property
    def authorize_url(self) -> str:
        return f"https://{self.domain}/authorize"

    @property
    def token_url(self) -> str:
        return f"https://{self.domain}/oauth/token"

    @property
    def device_code_url(self) -> str:
        return f"https://{self.domain}/oauth/device/code"

    @property
    def cache_key(self) -> str:
        """Identity of the credentials this config produces, for the token cache."""

        return f"{self.domain}|{self.client_id}|{self.audience}"

    def scopes(self, *, offline_access: bool = True) -> str:
        """The scope string, optionally with ``offline_access`` removed.

        The implicit grant never issues refresh tokens, and Auth0 rejects the
        scope outright for it.
        """

        parts = [part for part in self.scope.split() if part]
        if not offline_access:
            parts = [part for part in parts if part != "offline_access"]
        return " ".join(parts)
