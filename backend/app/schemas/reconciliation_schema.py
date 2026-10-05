"""Reconciliation request/response models."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import AnomalyStatus, LedgerType


class ReconciliationResultResponse(BaseModel):
    """A match outcome, including the score breakdown that justifies it.

    The original response exposed only the composite confidence. Returning the
    components makes the number explainable in a review rather than asserted.
    """

    transaction_id: str
    ledger_id: Optional[str] = None
    ledger_type: str = LedgerType.BANK.value
    status: str
    amount_difference: float
    match_confidence: float
    confidence_tier: str
    remarks: Optional[str] = None

    # Score breakdown and the normalised values used for the decision.
    score_amount: Optional[float] = None
    score_name: Optional[float] = None
    score_reference: Optional[float] = None
    score_date: Optional[float] = None
    base_currency: Optional[str] = None
    transaction_amount_base: Optional[float] = None
    ledger_amount_base: Optional[float] = None
    date_difference_days: Optional[float] = None
    is_manual: Optional[int] = 0
    reconciled_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PaginatedReconciliationResults(BaseModel):
    total: int
    limit: int
    offset: int
    returned: int
    items: List[ReconciliationResultResponse] = Field(default_factory=list)


class ManualMatchRequest(BaseModel):
    transaction_id: str = Field(..., min_length=1)
    ledger_id: str = Field(..., min_length=1)
    ledger_type: str = LedgerType.BANK.value
    remarks: Optional[str] = "Manual reconciliation by treasury manager"
    created_by: Optional[str] = "treasury-user"


class AnomalyResolutionRequest(BaseModel):
    status: str = AnomalyStatus.RESOLVED.value
    note: Optional[str] = None
