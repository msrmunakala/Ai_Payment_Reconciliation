"""FX rate management endpoints.

The brief lists "multi-currency transaction handling with FX rate management".
Previously the rate table was written once during seeding and never read or
exposed; rates could only be changed by editing a dict in source.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.services import fx_service

router = APIRouter(prefix="/fx", tags=["FX"])


@router.get("/rates")
def get_rates(
    include_history: bool = Query(
        False, description="Include superseded rates with their validity windows"
    ),
    db: Session = Depends(get_db),
):
    """Stored rates. Only currently effective rows unless history is requested."""
    return {
        "base_currency": fx_service.BASE_CURRENCY,
        "rates": fx_service.list_rates(db, include_history=include_history),
    }


@router.get("/convert")
def convert_amount(
    amount: float = Query(..., description="Amount in the source currency"),
    from_currency: str = Query(..., min_length=3, max_length=3),
    to_currency: Optional[str] = Query(None, min_length=3, max_length=3),
    as_of: Optional[datetime] = Query(
        None, description="Use the rate effective at this instant (ISO 8601)"
    ),
    db: Session = Depends(get_db),
):
    """Convert an amount, returning the rate and its provenance.

    ``as_of`` resolves the rate that was in force at a past instant, which is how
    a historical reconciliation figure is reproduced.
    """
    converter = fx_service.get_converter(db, as_of=as_of)
    try:
        result = converter.convert(amount, from_currency, to_currency)
    except fx_service.UnknownCurrencyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "amount": result.amount,
        "original_amount": result.original_amount,
        "from_currency": result.from_currency,
        "to_currency": result.to_currency,
        "rate": result.rate,
        "rate_source": result.rate_source,
        "is_fallback": result.is_fallback,
        "as_of": as_of.isoformat() if as_of else None,
    }


@router.post("/refresh")
def refresh_rates(db: Session = Depends(get_db)):
    """Re-apply the configured provider's rates, preserving prior rates as history."""
    return fx_service.refresh_rates_from_provider(db)


@router.put("/rates/{from_currency}")
def set_rate(
    from_currency: str,
    units_per_base: float = Query(
        ..., gt=0, description="Units of this currency per 1 unit of the base currency"
    ),
    db: Session = Depends(get_db),
):
    """Override a single rate.

    The previous rate is closed off rather than overwritten, so earlier runs stay
    reproducible.
    """
    try:
        row = fx_service.upsert_rate(
            db,
            from_currency,
            rate_to_base=1.0 / units_per_base,
            source="manual-api",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "from_currency": row.from_currency,
        "to_currency": row.to_currency,
        "rate": row.rate,
        "units_per_base": units_per_base,
        "source": row.source,
        "valid_from": row.valid_from.isoformat() if row.valid_from else None,
    }
