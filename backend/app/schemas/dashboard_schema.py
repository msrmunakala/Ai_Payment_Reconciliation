"""Response models for the dashboard and forecasting endpoints.

The fields the existing React dashboard reads are kept required so a regression
in the service layer surfaces as a validation error rather than silently
rendering blanks. Everything added later is optional with a default.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CurrencyExposure(BaseModel):
    currency: str
    transactions: int
    value_original: float
    value_base: float


class AnomalyTypeCount(BaseModel):
    type: str
    count: int
    average_risk: float


class AnomalyBreakdown(BaseModel):
    by_type: List[AnomalyTypeCount] = Field(default_factory=list)
    by_severity: Dict[str, int] = Field(default_factory=dict)
    by_status: Dict[str, int] = Field(default_factory=dict)


class LedgerCoverage(BaseModel):
    bank_records: int = 0
    bank_reconciled: int = 0
    bank_open: int = 0
    bank_coverage_pct: float = 0.0
    erp_records: int = 0
    erp_cleared: int = 0
    erp_open: int = 0
    erp_coverage_pct: float = 0.0


class RunSummary(BaseModel):
    run_id: int
    trigger: Optional[str] = None
    status: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    transactions_examined: Optional[int] = None
    candidates_evaluated: Optional[int] = None
    manual_overrides_applied: Optional[int] = None
    error_message: Optional[str] = None


class DashboardSummary(BaseModel):
    # --- consumed by the existing frontend ---
    total_transactions: int
    matched_transactions: int
    partial_matched: int
    unmatched_transactions: int
    match_rate: float
    anomaly_count: int
    total_reconciled_value: float
    synthetic_data_notice: str

    # --- additions ---
    flagged_for_review: int = 0
    settled_rate: float = 0.0
    base_currency: str = "USD"
    value_at_risk: float = 0.0
    manual_matches: int = 0
    transactions_last_24h: int = 0
    unnormalised_transactions: int = 0
    currency_exposure: List[CurrencyExposure] = Field(default_factory=list)
    anomalies: Optional[AnomalyBreakdown] = None
    ledger_coverage: Optional[LedgerCoverage] = None
    confidence_distribution: Dict[str, int] = Field(default_factory=dict)
    last_run: Optional[RunSummary] = None


class CashFlowForecastPoint(BaseModel):
    date: str
    predicted_amount: float
    lower_bound: float
    upper_bound: float
    inflow: float = 0.0
    outflow: float = 0.0
    model: Optional[str] = None
    currency: Optional[str] = None
    is_synthetic: bool = False


class ForecastResponse(BaseModel):
    """Envelope used by the metadata endpoint; the list endpoint stays a list."""

    points: List[CashFlowForecastPoint] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
