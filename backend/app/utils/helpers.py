"""Small shared helpers."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Optional

CURRENCY_SYMBOLS = {
    "USD": "$",
    "INR": "₹",
    "EUR": "€",
    "GBP": "£",
    "KES": "KSh",
    "TZS": "TSh",
    "UGX": "USh",
    "NGN": "₦",
    "ZAR": "R",
}


def format_currency(amount: float, currency: str = "USD") -> str:
    code = (currency or "USD").strip().upper()
    symbol = CURRENCY_SYMBOLS.get(code, f"{code} ")
    return f"{symbol}{amount:,.2f}"


def utc_now() -> datetime:
    """Timezone-aware current time.

    ``datetime.utcnow()`` returns a naive value and is deprecated in Python 3.12.
    """
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


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
    """Produce a signature in the form the verifier accepts. Useful for tests."""
    digestmod = getattr(hashlib, algorithm)
    digest = hmac.new(secret.encode("utf-8"), body, digestmod).hexdigest()
    return f"{algorithm}={digest}"


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if denominator else default


def percentage(part: float, whole: float, digits: int = 2) -> float:
    if not whole:
        return 0.0
    return round(part / whole * 100, digits)


def truncate(text: Optional[str], length: int = 200) -> Optional[str]:
    if text is None:
        return None
    return text if len(text) <= length else text[: length - 1] + "…"


__all__ = [
    "CURRENCY_SYMBOLS",
    "compute_hmac_signature",
    "format_currency",
    "percentage",
    "safe_divide",
    "truncate",
    "utc_now",
    "utc_now_iso",
    "verify_hmac_signature",
]
