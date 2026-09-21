from typing import List, Dict, Any
from pydantic import BaseModel


class DashboardSummary(BaseModel):
    total_transactions: int
    matched_transactions: int
    partial_matched: int
    unmatched_transactions: int
    match_rate: float
    anomaly_count: int
    total_reconciled_value: float
    synthetic_data_notice: str = "ERP records are synthetic demo data, generated for prototype validation."


class CashFlowForecastPoint(BaseModel):
    date: str
    predicted_amount: float
    lower_bound: float
    upper_bound: float
    inflow: float = 0.0
    outflow: float = 0.0
