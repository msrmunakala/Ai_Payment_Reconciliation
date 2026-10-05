"""Webhook signature helpers.

Only HMAC signing and verification live here. Earlier revisions carried a
currency formatter, timestamp helpers and small maths utilities that no caller
ever used; they were removed rather than left as decoration.
"""

from __future__ import annotations

import hashlib
import hmac


def verify_hmac_signature(
    body: bytes, provided_signature: str, secret: str, algorithm: str = "sha256"
) -> bool:
    """Constant-time HMAC verification for inbound webhooks.

    Accepts the signature either bare (``<hex>``) or prefixed
    (``sha256=<hex>``), which covers the common conventions.
    """
    if not provided_signature or not secret:
        return False

    candidate = provided_signature.strip()
    if "=" in candidate:
        prefix, _, remainder = candidate.partition("=")
        if prefix.strip().lower() in {"sha256", "sha1", "sha512"}:
            algorithm = prefix.strip().lower()
            candidate = remainder.strip()

    try:
        digestmod = getattr(hashlib, algorithm)
    except AttributeError:
        return False

    expected = hmac.new(secret.encode("utf-8"), body, digestmod).hexdigest()
    return hmac.compare_digest(expected, candidate.lower())


def compute_hmac_signature(body: bytes, secret: str, algorithm: str = "sha256") -> str:
    """Produce a signature in the form the verifier accepts. Used by the tests."""
    digestmod = getattr(hashlib, algorithm)
    digest = hmac.new(secret.encode("utf-8"), body, digestmod).hexdigest()
    return f"{algorithm}={digest}"


__all__ = ["compute_hmac_signature", "verify_hmac_signature"]
