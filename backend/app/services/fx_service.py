"""Multi-currency normalisation with FX rate management.

What was incomplete before:

* ``convert_to_usd(amount, currency, db)`` accepted a ``db`` argument and never
  used it, so the ``fx_rates`` table was written once during seeding and never
  read. Rates lived only in a module-level dict.
* An unrecognised currency code was returned unconverted, silently treating e.g.
  1,000,000 UGX as 1,000,000 USD and corrupting every amount comparison.
* There was no way to see which rate produced a converted figure, so a
  reconciliation result could not be explained or reproduced.

What this module now provides:

* Database-backed rate lookup with a static fallback, so operators can override
  rates without a code change.
* ``FXConverter``, a snapshot that loads the rate table once per reconciliation
  run instead of re-querying inside the matching loops.
* Cross-currency conversion via the configured base currency as a pivot.
* Explicit, reportable handling of unknown currency codes.
* Decimal arithmetic internally (the storage columns remain ``Float`` for now;
  moving money to ``NUMERIC`` is a schema change tracked separately).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.reconciliation import FXRate

logger = logging.getLogger(__name__)

BASE_CURRENCY = settings.fx.base_currency

# Units of each currency per 1 unit of base currency (USD).
# Indicative mid-market levels used as the offline fallback.
DEFAULT_RATES: Dict[str, float] = {
    "USD": 1.0,
    "INR": 83.50,
    "EUR": 0.92,
    "GBP": 0.78,
    "KES": 129.50,
    "TZS": 2680.0,
    "UGX": 3720.0,
    "NGN": 1550.0,
    "ZAR": 18.30,
    "GHS": 15.20,
    "XOF": 603.50,
    "RWF": 1300.0,
}

# Money is quantised to 4 decimal places; conversion factors to 10.
_MONEY_EXP = Decimal("0.0001")
_RATE_EXP = Decimal("0.0000000001")


class UnknownCurrencyError(ValueError):
    """Raised when a currency has no rate and strict mode is enabled."""


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_EXP, rounding=ROUND_HALF_UP)


def normalize_currency(currency: Optional[str]) -> str:
    """Upper-case and trim a currency code, defaulting to the base currency."""
    if not currency:
        return BASE_CURRENCY
    return str(currency).strip().upper()


@dataclass(frozen=True)
class ConversionResult:
    """A conversion plus the provenance needed to explain or reproduce it."""

    amount: float
    original_amount: float
    from_currency: str
    to_currency: str
    rate: float
    rate_source: str
    is_fallback: bool

    @property
    def is_identity(self) -> bool:
        return self.from_currency == self.to_currency


class FXConverter:
    """An immutable snapshot of the rate table.

    Built once per reconciliation run so that converting thousands of amounts
    costs no additional database round trips. The previous implementation called
    a conversion helper once per candidate pair inside nested loops.
    """

    def __init__(
        self,
        rates_to_base: Dict[str, Decimal],
        sources: Dict[str, str],
        base_currency: str = BASE_CURRENCY,
        loaded_at: Optional[datetime] = None,
        as_of: Optional[datetime] = None,
    ) -> None:
        self._rates_to_base = rates_to_base
        self._sources = sources
        self.base_currency = base_currency
        self.loaded_at = loaded_at or datetime.utcnow()
        # The point in time these rates represent (None == current).
        self.as_of = as_of
        # Currency codes seen at conversion time that had no rate available.
        self._unknown_seen: set[str] = set()

    # -- construction ----------------------------------------------------

    @classmethod
    def from_db(
        cls,
        db: Optional[Session],
        base_currency: str = BASE_CURRENCY,
        as_of: Optional[datetime] = None,
    ) -> "FXConverter":
        """Load rates from ``fx_rates``, falling back to the static table.

        When ``as_of`` is supplied the rate that was in force at that instant is
        selected using the validity window, which is what allows a historical
        reconciliation result to be recomputed to the same figures. Otherwise the
        currently effective rate (``valid_to IS NULL``) is used.
        """
        rates: Dict[str, Decimal] = {}
        sources: Dict[str, str] = {}

        if db is not None:
            try:
                query = db.query(FXRate).filter(FXRate.to_currency == base_currency)

                if as_of is not None:
                    query = query.filter(
                        (FXRate.valid_from.is_(None)) | (FXRate.valid_from <= as_of)
                    ).filter((FXRate.valid_to.is_(None)) | (FXRate.valid_to > as_of))
                else:
                    query = query.filter(FXRate.valid_to.is_(None))

                rows: Iterable[FXRate] = query.order_by(FXRate.valid_from.asc()).all()
                for row in rows:
                    code = normalize_currency(row.from_currency)
                    if row.rate is None or row.rate <= 0:
                        logger.warning(
                            "Ignoring non-positive FX rate for %s->%s: %r",
                            code,
                            base_currency,
                            row.rate,
                        )
                        continue
                    rates[code] = Decimal(str(row.rate))
                    sources[code] = row.source or "database"
            except Exception:  # pragma: no cover - defensive
                # A missing table or a broken connection must not take down
                # reconciliation; fall through to the static table.
                logger.exception("Failed to load FX rates from database; using defaults")

        for code, units_per_base in DEFAULT_RATES.items():
            code = normalize_currency(code)
            if code in rates:
                continue
            if units_per_base <= 0:
                continue
            rates[code] = Decimal("1") / Decimal(str(units_per_base))
            sources[code] = "static-default"

        rates[base_currency] = Decimal("1")
        sources.setdefault(base_currency, "identity")

        return cls(rates, sources, base_currency=base_currency, as_of=as_of)

    # -- lookups ---------------------------------------------------------

    @property
    def unknown_currencies(self) -> List[str]:
        """Currency codes encountered that had no rate (reported, not hidden)."""
        return sorted(self._unknown_seen)

    def has_rate(self, currency: str) -> bool:
        return normalize_currency(currency) in self._rates_to_base

    def rate_to_base(self, currency: str) -> Optional[Decimal]:
        return self._rates_to_base.get(normalize_currency(currency))

    def rate(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """Multiplier that converts ``from_currency`` into ``to_currency``."""
        src = normalize_currency(from_currency)
        dst = normalize_currency(to_currency)
        if src == dst:
            return Decimal("1")

        src_rate = self._rates_to_base.get(src)
        dst_rate = self._rates_to_base.get(dst)
        if src_rate is None or dst_rate is None or dst_rate == 0:
            return None
        return (src_rate / dst_rate).quantize(_RATE_EXP, rounding=ROUND_HALF_UP)

    # -- conversion ------------------------------------------------------

    def convert(
        self,
        amount: Optional[float],
        from_currency: str,
        to_currency: Optional[str] = None,
    ) -> ConversionResult:
        """Convert an amount, recording the rate and its provenance."""
        target = normalize_currency(to_currency or self.base_currency)
        src = normalize_currency(from_currency)
        original = Decimal(str(amount if amount is not None else 0))

        if src == target:
            value = _q_money(original)
            return ConversionResult(
                amount=float(value),
                original_amount=float(original),
                from_currency=src,
                to_currency=target,
                rate=1.0,
                rate_source="identity",
                is_fallback=False,
            )

        multiplier = self.rate(src, target)
        if multiplier is None:
            self._unknown_seen.add(src)
            if settings.fx.strict_unknown_currency:
                raise UnknownCurrencyError(
                    f"No FX rate available for {src}->{target}. "
                    "Load a rate or disable FX_STRICT_UNKNOWN_CURRENCY."
                )
            logger.warning(
                "No FX rate for %s->%s; treating amount as already in %s. "
                "This figure is not comparable across currencies.",
                src,
                target,
                target,
            )
            value = _q_money(original)
            return ConversionResult(
                amount=float(value),
                original_amount=float(original),
                from_currency=src,
                to_currency=target,
                rate=1.0,
                rate_source="unresolved-fallback",
                is_fallback=True,
            )

        value = _q_money(original * multiplier)
        return ConversionResult(
            amount=float(value),
            original_amount=float(original),
            from_currency=src,
            to_currency=target,
            rate=float(multiplier),
            rate_source=self._sources.get(src, "unknown"),
            is_fallback=False,
        )

    def to_base(self, amount: Optional[float], currency: str) -> float:
        """Convenience: convert to the base currency and return the amount."""
        return self.convert(amount, currency, self.base_currency).amount

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        """Serialisable view of the loaded rates, for audit and the API."""
        return {
            code: {
                "to_base_multiplier": float(rate),
                "units_per_base": float(Decimal("1") / rate) if rate else None,
                "source": self._sources.get(code, "unknown"),
            }
            for code, rate in sorted(self._rates_to_base.items())
        }


# ---------------------------------------------------------------------------
# Rate table management
# ---------------------------------------------------------------------------


def init_fx_rates(db: Session, overwrite: bool = False) -> int:
    """Ensure a currently-effective ``CCY -> base`` row exists for every currency.

    ``rate`` is the multiplier that converts one unit of ``from_currency`` into
    ``to_currency``, which is the convention the original seeding code used
    (``1 / units_per_usd``). Returns the number of rows written.
    """
    now = datetime.utcnow()
    existing: Dict[str, FXRate] = {}
    for row in (
        db.query(FXRate)
        .filter(FXRate.to_currency == BASE_CURRENCY, FXRate.valid_to.is_(None))
        .all()
    ):
        existing[normalize_currency(row.from_currency)] = row

    written = 0
    for code, units_per_base in DEFAULT_RATES.items():
        code = normalize_currency(code)
        if units_per_base <= 0:
            continue
        multiplier = float(Decimal("1") / Decimal(str(units_per_base)))

        row = existing.get(code)
        if row is None:
            db.add(
                FXRate(
                    from_currency=code,
                    to_currency=BASE_CURRENCY,
                    rate=multiplier,
                    updated_at=now,
                    valid_from=now,
                    valid_to=None,
                    source="static-default",
                )
            )
            written += 1
        elif overwrite:
            row.rate = multiplier
            row.updated_at = now
            if row.valid_from is None:
                row.valid_from = now
            row.source = row.source or "static-default"
            written += 1

    db.commit()
    if written:
        logger.info("Initialised %d FX rate rows against base %s", written, BASE_CURRENCY)
    return written


def upsert_rate(
    db: Session,
    from_currency: str,
    rate_to_base: float,
    to_currency: str = BASE_CURRENCY,
    source: str = "manual",
    commit: bool = True,
) -> FXRate:
    """Record a new effective rate, preserving the previous one as history.

    If the rate has changed, the currently effective row is closed off by
    setting ``valid_to`` and a new row is opened. This is what lets an older
    reconciliation run be recomputed against the rate that applied at the time
    instead of today's rate.
    """
    if rate_to_base <= 0:
        raise ValueError("rate_to_base must be positive")

    src = normalize_currency(from_currency)
    dst = normalize_currency(to_currency)
    now = datetime.utcnow()

    current = (
        db.query(FXRate)
        .filter(
            FXRate.from_currency == src,
            FXRate.to_currency == dst,
            FXRate.valid_to.is_(None),
        )
        .order_by(FXRate.id.desc())
        .first()
    )

    if current is not None and current.rate == rate_to_base:
        current.updated_at = now
        current.source = source or current.source
        if commit:
            db.commit()
            db.refresh(current)
        return current

    if current is not None:
        current.valid_to = now
        current.updated_at = now

    row = FXRate(
        from_currency=src,
        to_currency=dst,
        rate=rate_to_base,
        updated_at=now,
        valid_from=now,
        valid_to=None,
        source=source,
    )
    db.add(row)

    if commit:
        db.commit()
        db.refresh(row)
    return row


def refresh_rates_from_provider(db: Session) -> Dict[str, object]:
    """Pull live rates from an external provider when one is configured.

    Default configuration is ``FX_PROVIDER=static``, which simply re-applies the
    built-in table. Setting ``FX_PROVIDER=http`` plus ``FX_PROVIDER_URL`` fetches
    a JSON document shaped ``{"rates": {"INR": 83.5, ...}}`` where values are
    units per base currency.
    """
    provider = settings.fx.provider.strip().lower()

    if provider == "static" or not settings.fx.provider_url:
        written = init_fx_rates(db, overwrite=True)
        return {
            "provider": "static",
            "updated": written,
            "base_currency": BASE_CURRENCY,
            "fetched_at": datetime.utcnow().isoformat(),
        }

    # Imported lazily so the module has no hard dependency on httpx.
    import httpx

    try:
        with httpx.Client(timeout=settings.fx.refresh_timeout_seconds) as client:
            response = client.get(settings.fx.provider_url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.error("FX provider refresh failed: %s", exc)
        return {
            "provider": provider,
            "updated": 0,
            "error": str(exc),
            "base_currency": BASE_CURRENCY,
        }

    raw_rates = payload.get("rates") if isinstance(payload, dict) else None
    if not isinstance(raw_rates, dict):
        return {
            "provider": provider,
            "updated": 0,
            "error": "Provider response did not contain a 'rates' object",
            "base_currency": BASE_CURRENCY,
        }

    updated = 0
    skipped: List[str] = []
    for code, units_per_base in raw_rates.items():
        try:
            units = Decimal(str(units_per_base))
        except Exception:
            skipped.append(str(code))
            continue
        if units <= 0:
            skipped.append(str(code))
            continue
        upsert_rate(
            db,
            code,
            float(Decimal("1") / units),
            source=f"provider:{provider}",
            commit=False,
        )
        updated += 1

    db.commit()

    return {
        "provider": provider,
        "url": settings.fx.provider_url,
        "updated": updated,
        "skipped": skipped,
        "base_currency": BASE_CURRENCY,
        "fetched_at": datetime.utcnow().isoformat(),
    }


def list_rates(db: Session, include_history: bool = False) -> List[Dict[str, object]]:
    """Stored rates for the API and audit views.

    By default only the currently effective rate per pair is returned; pass
    ``include_history=True`` to see superseded rows with their validity windows.
    """
    query = db.query(FXRate)
    if not include_history:
        query = query.filter(FXRate.valid_to.is_(None))

    rows = query.order_by(FXRate.from_currency.asc(), FXRate.valid_from.asc()).all()
    return [
        {
            "from_currency": row.from_currency,
            "to_currency": row.to_currency,
            "rate": row.rate,
            "units_per_base": (1.0 / row.rate) if row.rate else None,
            "source": row.source,
            "valid_from": row.valid_from.isoformat() if row.valid_from else None,
            "valid_to": row.valid_to.isoformat() if row.valid_to else None,
            "is_current": row.valid_to is None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Backwards-compatible helpers
# ---------------------------------------------------------------------------


def get_converter(
    db: Optional[Session] = None, as_of: Optional[datetime] = None
) -> FXConverter:
    """Build a converter snapshot. Preferred entry point for services.

    Pass ``as_of`` to reproduce the rates that applied at a past instant.
    """
    return FXConverter.from_db(db, as_of=as_of)


def convert_to_usd(amount: float, currency: str, db: Optional[Session] = None) -> float:
    """Convert a single amount into the base currency.

    Signature preserved for existing callers, but the ``db`` argument is now
    honoured. Prefer ``get_converter(db)`` when converting many amounts, since
    this function rebuilds the rate snapshot on every call.
    """
    return get_converter(db).to_base(amount, currency)


def convert(
    amount: float,
    from_currency: str,
    to_currency: str = BASE_CURRENCY,
    db: Optional[Session] = None,
) -> ConversionResult:
    """Convert between any two currencies, returning full provenance."""
    return get_converter(db).convert(amount, from_currency, to_currency)


__all__ = [
    "BASE_CURRENCY",
    "DEFAULT_RATES",
    "ConversionResult",
    "FXConverter",
    "UnknownCurrencyError",
    "convert",
    "convert_to_usd",
    "get_converter",
    "init_fx_rates",
    "list_rates",
    "normalize_currency",
    "refresh_rates_from_provider",
    "upsert_rate",
]
