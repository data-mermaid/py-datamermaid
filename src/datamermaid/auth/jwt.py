"""Just enough JWT handling to know when an access token has expired.

The SDK never *trusts* a token it decodes here: the API verifies signatures.
All that is needed locally is the ``exp`` claim, so the payload is base64
decoded without any signature check and without a crypto dependency.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

__all__ = ["decode_payload", "token_expires_at"]


def _decode_segment(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_payload(token: str) -> dict[str, Any]:
    """Return the claims of ``token``, or ``{}`` if it is not a readable JWT.

    The signature is neither present nor checked; opaque tokens simply yield
    an empty mapping.
    """

    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        claims = json.loads(_decode_segment(parts[1]))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def token_expires_at(token: str) -> float | None:
    """The ``exp`` claim as a POSIX timestamp, or ``None`` if there is none."""

    exp = decode_payload(token).get("exp")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        return float(exp)
    return None
