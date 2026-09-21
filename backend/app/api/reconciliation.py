from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.reconciliation import Reconciliation, Anomaly
from app.schemas.reconciliation_schema import ReconciliationResultResponse, AnomalyResponse, ManualMatchRequest
from app.services.reconciliation_service import run_reconciliation

router = APIRouter(
    prefix="/reconciliation",
    tags=["Reconciliation"]
)


@router.post("/run")
def trigger_reconciliation(db: Session = Depends(get_db)):
    return run_reconciliation(db)


@router.get("/results", response_model=List[ReconciliationResultResponse])
def get_reconciliation_results(
    status: Optional[str] = Query(None, description="Filter by status: matched, partial, unmatched"),
    tier: Optional[str] = Query(None, description="Filter by confidence tier: high, medium, low"),
    db: Session = Depends(get_db)
):
    query = db.query(Reconciliation)
    if status:
        query = query.filter(Reconciliation.status == status)
    if tier:
        query = query.filter(Reconciliation.confidence_tier == tier)
    return query.order_by(Reconciliation.id).all()


@router.post("/manual-match")
def manual_match(req: ManualMatchRequest, db: Session = Depends(get_db)):
    rec = db.query(Reconciliation).filter(
        Reconciliation.transaction_id == req.transaction_id
    ).first()

    if not rec:
        rec = Reconciliation(
            transaction_id=req.transaction_id,
            ledger_id=req.ledger_id,
            ledger_type=req.ledger_type,
            status="matched",
            match_confidence=100.0,
            confidence_tier="high",
            remarks=req.remarks
        )
        db.add(rec)
    else:
        rec.ledger_id = req.ledger_id
        rec.ledger_type = req.ledger_type
        rec.status = "matched"
        rec.match_confidence = 100.0
        rec.confidence_tier = "high"
        rec.remarks = req.remarks

    # Resolve any open anomalies for this transaction
    anomalies = db.query(Anomaly).filter(
        Anomaly.transaction_id == req.transaction_id,
        Anomaly.status == "OPEN"
    ).all()
    for a in anomalies:
        a.status = "RESOLVED"

    db.commit()
    return {"message": "Transaction manually matched successfully", "transaction_id": req.transaction_id}
