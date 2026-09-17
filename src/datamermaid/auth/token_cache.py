"""On-disk storage for OAuth tokens.

Tokens live in ``$XDG_CONFIG_HOME/datamermaid/tokens.json`` (defaulting to
``~/.config``), readable only by the owner.  One file holds one entry per
tenant/client/audience combination, so the production and development Auth0
tenants can be logged into side by side.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .jwt import token_expires_at

__all__ = ["CACHE_FILE_MODE", "DEFAULT_LEEWAY", "TokenCache", "TokenSet", "default_cache_path"]

CACHE_VERSION = 1
CACHE_FILE_MODE = 0o600
CACHE_DIR_MODE = 0o700
#: Treat a token that expires within this many seconds as already expired.
DEFAULT_LEEWAY = 60.0


def default_cache_path() -> Path:
    """``$XDG_CONFIG_HOME/datamermaid/tokens.json``, falling back to ``~/.config``."""

    root = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(root) if root else Path.home() / ".config"
    return base / "datamermaid" / "tokens.json"


@dataclass(frozen=True)
class TokenSet:
    """The credentials returned by a successful OAuth flow."""

    access_token: str
    refresh_token: str | None = None
    id_token: str | None = None
    token_type: str = "Bearer"
    scope: str | None = None
    #: POSIX timestamp the access token stops being valid at, if known.
    expires_at: float | None = None

    def __post_init__(self) -> None:
        # An access token is normally a JWT, so its own `exp` claim is a good
        # answer whenever the caller did not supply one.
        if self.expires_at is None:
            object.__setattr__(self, "expires_at", token_expires_at(self.access_token))

    @classmethod
    def from_response(cls, payload: dict[str, Any], *, now: float | None = None) -> TokenSet:
        """Build a token set from an Auth0 token endpoint response."""

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("token response did not contain an access_token")

        expires_at: float | None = None
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
            expires_at = (time.time() if now is None else now) + float(expires_in)

        def optional(key: str) -> str | None:
            value = payload.get(key)
            return value if isinstance(value, str) and value else None

        return cls(
            access_token=access_token,
            refresh_token=optional("refresh_token"),
            id_token=optional("id_token"),
            token_type=optional("token_type") or "Bearer",
            scope=optional("scope"),
            expires_at=expires_at,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenSet:
        expires_at = data.get("expires_at")
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=data.get("refresh_token"),
            id_token=data.get("id_token"),
            token_type=data.get("token_type") or "Bearer",
            scope=data.get("scope"),
            expires_at=float(expires_at) if isinstance(expires_at, (int, float)) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "access_token": self.access_token,
            "token_type": self.token_type,
        }
        for key in ("refresh_token", "id_token", "scope", "expires_at"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data

    def is_expired(self, *, leeway: float = DEFAULT_LEEWAY, now: float | None = None) -> bool:
        """Whether the access token is past (or about to pass) its expiry.

        A token with no known expiry is assumed valid: the API is the
        authority, and a 401 triggers a refresh anyway.
        """

        if self.expires_at is None:
            return False
        return (time.time() if now is None else now) + leeway >= self.expires_at

    def merged_with(self, other: TokenSet) -> TokenSet:
        """``other`` on top of this set, keeping a refresh token it omits.

        Auth0 does not return the refresh token again when rotation is off,
        so a naive replace would throw away the only way to renew silently.
        """

        if other.refresh_token is not None:
            return other
        return replace(other, refresh_token=self.refresh_token)

    def __repr__(self) -> str:
        expiry = "unknown" if self.expires_at is None else f"{self.expires_at:.0f}"
        return (
            f"TokenSet(token_type={self.token_type!r}, expires_at={expiry}, "
            f"refreshable={self.refresh_token is not None})"
        )


class TokenCache:
    """A JSON file of token sets, keyed by :attr:`Auth0Config.cache_key`."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_cache_path()

    def __repr__(self) -> str:
        return f"TokenCache(path={str(self.path)!r})"

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            # A corrupt cache is not worth failing a login over.
            return {}
        if not isinstance(data, dict):
            return {}
        entries = data.get("tokens")
        return entries if isinstance(entries, dict) else {}

    def _write(self, entries: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=CACHE_DIR_MODE)
        payload = json.dumps({"version": CACHE_VERSION, "tokens": entries}, indent=2)
        # O_CREAT with an explicit mode never widens permissions on creation;
        # chmod afterwards fixes a file that already existed.
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, CACHE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(self.path, CACHE_FILE_MODE)

    def load(self, key: str) -> TokenSet | None:
        """The cached tokens for ``key``, or ``None``."""

        entry = self._read().get(key)
        if not isinstance(entry, dict) or "access_token" not in entry:
            return None
        try:
            return TokenSet.from_dict(entry)
        except (KeyError, TypeError, ValueError):
            return None

    def save(self, key: str, tokens: TokenSet) -> None:
        """Store ``tokens`` under ``key`` with 0600 permissions."""

        entries = self._read()
        entries[key] = tokens.to_dict()
        self._write(entries)

    def clear(self, key: str | None = None) -> bool:
        """Forget ``key`` (or every entry when it is ``None``).

        Returns whether anything was removed.
        """

        if key is None:
            try:
                self.path.unlink()
            except OSError:
                return False
            return True

        entries = self._read()
        if entries.pop(key, None) is None:
            return False
        if entries:
            self._write(entries)
        else:
            self.path.unlink(missing_ok=True)
        return True
