from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends
from fastapi import Query
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.dashboard_schema import DashboardSummary
from app.services.dashboard_service import get_dashboard_summary

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"]
)


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(
    target_date: Optional[date] = Query(None, alias="date"),
    db: Session = Depends(get_db),
):
    return get_dashboard_summary(db, target_date)
