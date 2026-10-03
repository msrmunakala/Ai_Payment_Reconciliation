"""Treasury dashboard endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.schemas.dashboard_schema import DashboardSummary
from app.services.dashboard_service import get_dashboard_summary, get_reconciliation_runs

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(db: Session = Depends(get_db)):
    """Headline metrics, FX exposure, exception mix and ledger coverage."""
    return get_dashboard_summary(db)


@router.get("/runs")
def reconciliation_run_history(
    limit: int = Query(20, ge=1, le=200, description="How many runs to return"),
    db: Session = Depends(get_db),
):
    """Reconciliation run history, for auditability and run-to-run comparison."""
    return {"runs": get_reconciliation_runs(db, limit=limit)}
