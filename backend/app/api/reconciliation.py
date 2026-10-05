"""Reconciliation endpoints.

Changes from the original router:

* ``/results`` is paginated and validates the ``status``/``tier`` filters against
  the known value sets. Previously an unpaginated query returned the entire table
  and a typo in ``status`` silently produced an empty list instead of a 422.
* ``/manual-match`` records the override in ``manual_matches`` so it survives the
  next reconciliation run. Previously it wrote straight into ``reconciliations``,
  where the next run's ``DELETE`` destroyed it.
* ``/run`` executes in a worker thread, because a full run is CPU-bound and would
  otherwise block the event loop.
* Anomalies can be resolved or ignored, and that triage is respected by later runs.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    AnomalyStatus,
    AnomalyType,
    ConfidenceTier,
    LedgerType,
    MatchStatus,
)
from app.database.db import get_db
from app.models.bank_transaction import BankTransaction, ERPRecord
from app.models.reconciliation import Anomaly, ManualMatch, Reconciliation
from app.models.transaction import Transaction
from app.schemas.reconciliation_schema import (
    AnomalyResolutionRequest,
    ManualMatchRequest,
    PaginatedReconciliationResults,
    ReconciliationResultResponse,
)
from app.services.reconciliation_service import run_reconciliation

router = APIRouter(prefix="/reconciliation", tags=["Reconciliation"])


@router.post("/run")
async def trigger_reconciliation(
    statistical_detection: bool = Query(
        True, description="Include the Isolation Forest outlier detector"
    ),
    refresh_forecast: bool = Query(True, description="Refresh the cash-flow forecast after the run"),
    db: Session = Depends(get_db),
):
    """Reconcile every transaction against the bank and ERP ledgers."""
    return await run_in_threadpool(
        run_reconciliation,
        db,
        "manual",
        statistical_detection,
        refresh_forecast,
    )


@router.get("/results", response_model=PaginatedReconciliationResults)
def get_reconciliation_results(
    status: Optional[str] = Query(
        None, description=f"Filter by status: {', '.join(MatchStatus.values())}"
    ),
    tier: Optional[str] = Query(
        None, description=f"Filter by confidence tier: {', '.join(ConfidenceTier.values())}"
    ),
    ledger_type: Optional[str] = Query(None, description="Filter by matched ledger type"),
    min_confidence: Optional[float] = Query(None, ge=0, le=100),
    max_confidence: Optional[float] = Query(None, ge=0, le=100),
    manual_only: bool = Query(False, description="Only human-confirmed matches"),
    settlement_window_id: Optional[str] = Query(
        None, description="Only results for transactions inside this settlement window"
    ),
    limit: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Paginated reconciliation results with validated filters."""
    if status is not None and not MatchStatus.has_value(status):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status '{status}'. Expected one of: {MatchStatus.values()}",
        )
    if tier is not None and not ConfidenceTier.has_value(tier):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown tier '{tier}'. Expected one of: {ConfidenceTier.values()}",
        )
    if ledger_type is not None and not LedgerType.has_value(ledger_type):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown ledger_type '{ledger_type}'. Expected one of: {LedgerType.values()}",
        )

    query = db.query(Reconciliation)
    if status:
        query = query.filter(Reconciliation.status == status)
    if tier:
        query = query.filter(Reconciliation.confidence_tier == tier)
    if ledger_type:
        query = query.filter(Reconciliation.ledger_type == ledger_type)
    if min_confidence is not None:
        query = query.filter(Reconciliation.match_confidence >= min_confidence)
    if max_confidence is not None:
        query = query.filter(Reconciliation.match_confidence <= max_confidence)
    if manual_only:
        query = query.filter(Reconciliation.is_manual == 1)

    if settlement_window_id:
        from app.services import settlement_service

        window = settlement_service.get_window(db, settlement_window_id)
        if window is None:
            raise HTTPException(
                status_code=404,
                detail=f"Settlement window '{settlement_window_id}' is not held locally",
            )
        in_window = settlement_service.transaction_ids_in_window(db, window)
        if not in_window:
            return {
                "total": 0,
                "limit": limit,
                "offset": offset,
                "returned": 0,
                "items": [],
            }
        query = query.filter(Reconciliation.transaction_id.in_(in_window))

    total = query.count()
    items = query.order_by(Reconciliation.id).offset(offset).limit(limit).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(items),
        "items": items,
    }


@router.get("/results/{transaction_id}", response_model=ReconciliationResultResponse)
def get_single_result(transaction_id: str, db: Session = Depends(get_db)):
    """The full scored breakdown for one transaction."""
    row = (
        db.query(Reconciliation)
        .filter(Reconciliation.transaction_id == transaction_id)
        .order_by(Reconciliation.id.desc())
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No reconciliation result for '{transaction_id}'"
        )
    return row


@router.post("/manual-match")
def manual_match(request: ManualMatchRequest, db: Session = Depends(get_db)):
    """Record a human-confirmed match.

    Validates that both sides exist, then stores the override durably so it is
    re-applied by every later run instead of being wiped.
    """
    if not LedgerType.has_value(request.ledger_type):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown ledger_type. Expected one of: {LedgerType.values()}",
        )

    transaction = (
        db.query(Transaction)
        .filter(Transaction.transaction_id == request.transaction_id)
        .first()
    )
    if transaction is None:
        raise HTTPException(
            status_code=404, detail=f"Transaction '{request.transaction_id}' not found"
        )

    if request.ledger_type == LedgerType.BANK.value:
        exists = (
            db.query(BankTransaction)
            .filter(BankTransaction.bank_statement_id == request.ledger_id)
            .first()
        )
    elif request.ledger_type == LedgerType.ERP.value:
        exists = db.query(ERPRecord).filter(ERPRecord.erp_id == request.ledger_id).first()
    else:
        exists = True  # MOJALOOP / NONE are not ledger rows we hold

    if not exists:
        raise HTTPException(
            status_code=404,
            detail=f"{request.ledger_type} record '{request.ledger_id}' not found",
        )

    override = (
        db.query(ManualMatch)
        .filter(ManualMatch.transaction_id == request.transaction_id)
        .first()
    )
    if override is None:
        override = ManualMatch(transaction_id=request.transaction_id)
        db.add(override)

    override.ledger_id = request.ledger_id
    override.ledger_type = request.ledger_type
    override.remarks = request.remarks
    override.created_by = request.created_by or "treasury-user"
    override.is_active = 1

    db.commit()

    # Apply it immediately so the dashboard reflects the decision without a run.
    from app.services.reconciliation_service import apply_manual_overrides

    applied = apply_manual_overrides(db)
    db.commit()

    return {
        "message": "Manual match recorded; it will persist across reconciliation runs.",
        "transaction_id": request.transaction_id,
        "ledger_id": request.ledger_id,
        "ledger_type": request.ledger_type,
        "overrides_applied": applied,
    }


@router.delete("/manual-match/{transaction_id}")
def remove_manual_match(transaction_id: str, db: Session = Depends(get_db)):
    """Withdraw a manual override so the engine's own result applies again."""
    override = (
        db.query(ManualMatch).filter(ManualMatch.transaction_id == transaction_id).first()
    )
    if override is None:
        raise HTTPException(
            status_code=404, detail=f"No manual match recorded for '{transaction_id}'"
        )

    db.delete(override)
    row = (
        db.query(Reconciliation)
        .filter(Reconciliation.transaction_id == transaction_id)
        .order_by(Reconciliation.id.desc())
        .first()
    )
    if row is not None:
        row.is_manual = 0
        row.remarks = (
            "Manual override withdrawn; re-run reconciliation to refresh the automatic result."
        )
    db.commit()

    return {
        "message": "Manual override removed. Re-run reconciliation to recompute this transaction.",
        "transaction_id": transaction_id,
    }


@router.get("/anomalies")
def list_anomalies(
    status: Optional[str] = Query(AnomalyStatus.OPEN.value),
    anomaly_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Paginated exception queue, worst first."""
    if status is not None and not AnomalyStatus.has_value(status):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status. Expected one of: {AnomalyStatus.values()}",
        )
    if anomaly_type is not None and not AnomalyType.has_value(anomaly_type):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown anomaly_type. Expected one of: {AnomalyType.values()}",
        )

    query = db.query(Anomaly)
    if status:
        query = query.filter(Anomaly.status == status)
    if anomaly_type:
        query = query.filter(Anomaly.anomaly_type == anomaly_type)
    if severity:
        query = query.filter(Anomaly.severity == severity)

    total = query.count()
    rows = (
        query.order_by(Anomaly.risk_score.desc(), Anomaly.id)
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
                "id": row.id,
                "transaction_id": row.transaction_id,
                "type": row.anomaly_type,
                "severity": row.severity,
                "risk_score": row.risk_score,
                "explanation": row.explanation,
                "status": row.status,
                "detector": row.detector,
                "run_id": row.run_id,
                "detected_at": row.detected_at.isoformat() if row.detected_at else None,
                "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
                "resolution_note": row.resolution_note,
            }
            for row in rows
        ],
    }


@router.post("/anomalies/{anomaly_id}/resolve")
def resolve_anomaly(
    anomaly_id: int, request: AnomalyResolutionRequest, db: Session = Depends(get_db)
):
    """Close or ignore an exception. The decision survives later runs."""
    if request.status not in {AnomalyStatus.RESOLVED.value, AnomalyStatus.IGNORED.value}:
        raise HTTPException(
            status_code=422,
            detail=(
                f"status must be '{AnomalyStatus.RESOLVED.value}' or "
                f"'{AnomalyStatus.IGNORED.value}'"
            ),
        )

    row = db.query(Anomaly).filter(Anomaly.id == anomaly_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Anomaly {anomaly_id} not found")

    from datetime import datetime

    row.status = request.status
    row.resolved_at = datetime.utcnow()
    row.resolution_note = request.note
    db.commit()

    return {
        "message": f"Anomaly {anomaly_id} marked {request.status}.",
        "id": row.id,
        "transaction_id": row.transaction_id,
        "status": row.status,
    }
