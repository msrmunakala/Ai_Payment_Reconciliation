from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.bank_transaction import BankTransaction
from app.models.reconciliation import AutomatedReconciliationRun
from app.models.transaction import Transaction
from app.services.forecasting import forecast_cash_positions, weekly_cash_positions
from app.services.scheduler import (
    configure_daily_job,
    configure_interval_job,
    get_scheduler_status,
)

router = APIRouter(prefix="/treasury", tags=["Treasury"])


class DailyScheduleRequest(BaseModel):
    hour: int = Field(default=0, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)


class IntervalScheduleRequest(BaseModel):
    hours: int = Field(ge=1, le=720)


@router.get("/forecast")
def get_cash_position_forecast(
    periods: int = Query(30, ge=1, le=365),
    frequency: Literal["daily", "weekly"] = Query("daily"),
    db: Session = Depends(get_db),
):
    settlements = db.query(
        BankTransaction.value_date.label("settlement_date"),
        BankTransaction.amount,
        BankTransaction.transaction_type,
    ).filter(BankTransaction.value_date.isnot(None)).all()

    if settlements:
        history = pd.DataFrame(
            [
                {
                    "settlement_date": row.settlement_date,
                    "amount": -abs(float(row.amount))
                    if (row.transaction_type or "").upper() == "DEBIT"
                    else abs(float(row.amount)),
                }
                for row in settlements
            ]
        )
    else:
        transactions = db.query(
            Transaction.created_at.label("timestamp"),
            Transaction.amount,
        ).filter(Transaction.created_at.isnot(None)).all()
        history = pd.DataFrame(
            [{"timestamp": row.timestamp, "amount": row.amount} for row in transactions]
        )

    forecast = forecast_cash_positions(history, periods)
    if frequency == "weekly":
        forecast = weekly_cash_positions(forecast)
    return {
        "frequency": frequency,
        "periods": periods,
        "method": forecast.attrs.get("method", "prophet"),
        "used_fallback": forecast.attrs.get("used_fallback", False),
        "forecast": [
            {
                "ds": row.ds.date().isoformat(),
                "yhat": float(row.yhat),
                "yhat_lower": float(row.yhat_lower),
                "yhat_upper": float(row.yhat_upper),
            }
            for row in forecast.itertuples(index=False)
        ],
    }


@router.get("/scheduler")
def scheduler_status(db: Session = Depends(get_db)):
    latest = db.query(AutomatedReconciliationRun).order_by(
        AutomatedReconciliationRun.started_at.desc()
    ).first()
    return {
        **get_scheduler_status(),
        "last_run": None if latest is None else {
            "id": latest.id,
            "trigger": latest.trigger,
            "started_at": latest.started_at.isoformat() if latest.started_at else None,
            "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
            "status": latest.status,
            "total_reconciled": latest.total_reconciled,
            "matched_count": latest.matched_count,
            "anomalies_detected": latest.anomalies_detected,
            "message": latest.message,
        },
    }


@router.get("/scheduler/runs")
def scheduler_runs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    runs = db.query(AutomatedReconciliationRun).order_by(
        AutomatedReconciliationRun.started_at.desc()
    ).limit(limit).all()
    return [
        {
            "id": run.id,
            "trigger": run.trigger,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "status": run.status,
            "total_reconciled": run.total_reconciled,
            "matched_count": run.matched_count,
            "anomalies_detected": run.anomalies_detected,
            "message": run.message,
        }
        for run in runs
    ]


@router.put("/scheduler/daily")
def set_daily_schedule(config: DailyScheduleRequest):
    try:
        return configure_daily_job(config.hour, config.minute, "UTC")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/scheduler/interval")
def set_interval_schedule(config: IntervalScheduleRequest):
    try:
        return configure_interval_job(config.hours)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
