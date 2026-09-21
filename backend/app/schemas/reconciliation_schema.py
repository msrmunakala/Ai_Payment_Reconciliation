from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class ReconciliationResultResponse(BaseModel):
    transaction_id: str
    ledger_id: Optional[str] = None
    ledger_type: str = "BANK"
    status: str
    amount_difference: float
    match_confidence: float
    confidence_tier: str
    remarks: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AnomalyResponse(BaseModel):
    transaction_id: str
    type: str
    severity: str
    risk_score: float
    explanation: str
    status: str = "OPEN"

    model_config = ConfigDict(from_attributes=True)


class ManualMatchRequest(BaseModel):
    transaction_id: str
    ledger_id: str
    ledger_type: str = "BANK"
    remarks: Optional[str] = "Manual reconciliation by treasury manager"
