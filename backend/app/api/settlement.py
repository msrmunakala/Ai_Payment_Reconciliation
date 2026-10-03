"""Settlement window and settlement endpoints.

These expose the *pull* half of the Mojaloop integration: windows and
settlements are read from the central settlement API, stored, and then used to
scope reconciliation runs.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import SettlementWindowState
from app.database.db import get_db
from app.models.settlement import Settlement
from app.services import settlement_service

router = APIRouter(prefix="/settlement", tags=["Settlement"])


@router.post("/sync")
async def sync_settlement_windows(
    state: Optional[str] = Query(
        None, description=f"Filter by state: {', '.join(SettlementWindowState.values())}"
    ),
    since_hours: int = Query(168, ge=1, le=8760),
    db: Session = Depends(get_db),
):
    """Pull settlement windows from the hub and upsert them. Idempotent."""
    if state is not None and not SettlementWindowState.has_value(state.upper()):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown state. Expected one of: {SettlementWindowState.values()}",
        )
    return await run_in_threadpool(
        settlement_service.sync_windows, db, state.upper() if state else None, since_hours
    )


@router.post("/settlements/sync")
async def sync_settlements(
    settlement_ids: List[str] = Query(
        ..., description="One or more settlement ids to fetch, e.g. ?settlement_ids=7001"
    ),
    db: Session = Depends(get_db),
):
    """Fetch specific settlements by id and upsert them.

    The Mojaloop settlement API has no ``GET /settlements`` list endpoint
    (``POST /settlements`` creates one), so settlements are requested by id.
    """
    return await run_in_threadpool(
        settlement_service.sync_settlements, db, settlement_ids
    )


@router.get("/windows")
def list_windows(
    state: Optional[str] = Query(None),
    reconciled: Optional[bool] = Query(
        None, description="true = already reconciled, false = awaiting reconciliation"
    ),
    limit: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Stored settlement windows with their reconciliation scope and status."""
    return settlement_service.list_windows(
        db, state=state, reconciled=reconciled, limit=limit, offset=offset
    )


@router.get("/windows/pending")
def list_pending_windows(db: Session = Depends(get_db)):
    """Closed windows awaiting reconciliation — what the scheduler picks up."""
    rows = settlement_service.pending_windows(db)
    return {
        "count": len(rows),
        "items": [
            {
                "window_id": row.window_id,
                "state": row.state,
                "created_date": row.created_date.isoformat() if row.created_date else None,
                "changed_date": row.changed_date.isoformat() if row.changed_date else None,
                "transactions_in_window": row.transactions_in_window,
            }
            for row in rows
        ],
    }


@router.get("/windows/{window_id}")
def get_window(window_id: str, db: Session = Depends(get_db)):
    """One settlement window, including the interval it scopes."""
    row = settlement_service.get_window(db, window_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Settlement window '{window_id}' is not held locally"
        )
    start, end = settlement_service.window_scope(row)
    return {
        "window_id": row.window_id,
        "state": row.state,
        "reason": row.reason,
        "created_date": row.created_date.isoformat() if row.created_date else None,
        "changed_date": row.changed_date.isoformat() if row.changed_date else None,
        "scope_from": start.isoformat(),
        "scope_to": end.isoformat(),
        "transactions_in_window": settlement_service.count_transactions_in_window(db, row),
        "is_reconcilable": row.state in settlement_service.RECONCILABLE_STATES,
        "reconciled_at": row.reconciled_at.isoformat() if row.reconciled_at else None,
        "last_run_id": row.last_run_id,
    }


@router.post("/windows/{window_id}/reconcile")
async def reconcile_window(
    window_id: str,
    refresh_forecast: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Reconcile only the transactions inside this settlement window.

    Results for transactions outside the window are left untouched.
    """
    try:
        return await run_in_threadpool(
            settlement_service.reconcile_window,
            db,
            window_id,
            "manual-settlement-window",
            refresh_forecast,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/windows/reconcile-pending")
async def reconcile_pending(
    limit: int = Query(10, ge=1, le=100),
    refresh_forecast: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Reconcile every closed window that has not been reconciled yet."""
    return await run_in_threadpool(
        settlement_service.reconcile_pending_windows, db, limit, refresh_forecast
    )


@router.get("/settlements")
def list_settlements(
    limit: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Stored settlements."""
    query = db.query(Settlement)
    total = query.count()
    rows = (
        query.order_by(Settlement.created_date.desc().nullslast(), Settlement.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(rows),
        "items": [
            {
                "settlement_id": row.settlement_id,
                "state": row.state,
                "settlement_model": row.settlement_model,
                "window_ids": (row.window_ids or "").split(",") if row.window_ids else [],
                "participant_count": row.participant_count,
                "created_date": row.created_date.isoformat() if row.created_date else None,
                "synced_at": row.synced_at.isoformat() if row.synced_at else None,
            }
            for row in rows
        ],
    }
