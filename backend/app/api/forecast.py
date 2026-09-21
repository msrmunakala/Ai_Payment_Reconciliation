from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.reconciliation import CashFlowForecast
from app.schemas.dashboard_schema import CashFlowForecastPoint
from app.services.forecasting_service import generate_forecast

router = APIRouter(
    prefix="/forecast",
    tags=["Forecasting"]
)


@router.get("/", response_model=List[CashFlowForecastPoint])
def get_forecast(
    days: int = Query(7, ge=1, le=14, description="Forecast horizon in days"),
    db: Session = Depends(get_db)
):
    generate_forecast(db, days)
    forecasts = db.query(CashFlowForecast).order_by(CashFlowForecast.forecast_date.asc()).all()
    
    return [
        {
            "date": f.forecast_date.strftime("%Y-%m-%d"),
            "predicted_amount": f.predicted_amount,
            "lower_bound": f.lower_bound,
            "upper_bound": f.upper_bound,
            "inflow": f.inflow,
            "outflow": f.outflow
        }
        for f in forecasts
    ]
