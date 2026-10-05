"""Cash-flow forecasting endpoints.

``GET /forecast`` previously deleted the entire forecast table and retrained the
model on every call, which made a read request destructive, non-idempotent and
slow. It now serves a cached forecast and regenerates only when the cache is
stale or does not cover the requested horizon. Regeneration is also available
explicitly via ``POST /forecast/run``.
"""

from typing import List

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.db import get_db
from app.schemas.dashboard_schema import CashFlowForecastPoint
from app.services.forecasting_service import (
    build_daily_history,
    forecast_metadata,
    generate_forecast,
    get_forecast_points,
)
from app.services.fx_service import get_converter

router = APIRouter(prefix="/forecast", tags=["Forecasting"])


@router.get("/", response_model=List[CashFlowForecastPoint])
def get_forecast(
    days: int = Query(
        settings.forecast.default_horizon_days,
        ge=1,
        le=settings.forecast.max_horizon_days,
        description="Forecast horizon in days",
    ),
    refresh: bool = Query(False, description="Force a retrain instead of using the cache"),
    db: Session = Depends(get_db),
):
    """Daily predicted net cash flow with a 95% interval, in the base currency."""
    return get_forecast_points(db, days=days, force=refresh)


@router.get("/metadata")
def get_forecast_metadata(db: Session = Depends(get_db)):
    """Which model produced the stored forecast, over how much history, how old."""
    return forecast_metadata(db)


@router.get("/history")
def get_cash_flow_history(
    lookback_days: int = Query(
        90, ge=1, le=730, description="How far back to aggregate observed movement"
    ),
    db: Session = Depends(get_db),
):
    """Observed daily cash movement, in the base currency.

    This is the *actual* series the forecast is fitted to, exposed so a client
    can plot history and forecast on one axis and keep them visually distinct.
    Read-only: it aggregates existing ledger rows and trains nothing.
    """
    converter = get_converter(db)
    series, notes = build_daily_history(db, converter, lookback_days=lookback_days)

    return {
        "currency": converter.base_currency,
        "lookback_days": lookback_days,
        "days": len(series),
        "notes": notes,
        "points": [
            {
                "date": point.day.isoformat(),
                "inflow": round(point.inflow, 2),
                "outflow": round(point.outflow, 2),
                "net": round(point.net, 2),
            }
            for point in series
        ],
    }


@router.post("/run")
async def run_forecast(
    days: int = Query(
        settings.forecast.default_horizon_days,
        ge=1,
        le=settings.forecast.max_horizon_days,
    ),
    db: Session = Depends(get_db),
):
    """Retrain and persist the forecast.

    Fitting Prophet is CPU-bound and takes seconds, so it runs in a worker thread
    rather than on the event loop.
    """
    await run_in_threadpool(generate_forecast, db, days, True)
    return {
        "status": "success",
        "horizon_days": days,
        "metadata": forecast_metadata(db),
    }
