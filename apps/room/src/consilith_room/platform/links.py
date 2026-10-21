"""One-time secrets: 32 random bytes, kept only as a SHA-256 digest.

Sign-in links, OAuth state, sign-in tickets and invite links all use this. The store sees
the digest; the secret itself exists in the emailed URL or the cookie and nowhere else.
"""

from __future__ import annotations

import hashlib
import secrets


def new_secret() -> str:
    """32 random bytes, URL-safe."""
    return secrets.token_urlsafe(32)


def digest_of(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
