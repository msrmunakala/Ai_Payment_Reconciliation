"""Transaction, bank and ERP request/response models."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.constants import TransactionStatus, TransactionType, TransferState


class _NormalisedMixin(BaseModel):
    """Fields shared by every ledger row after FX normalisation."""

    amount_base: Optional[float] = None
    base_currency: Optional[str] = None
    fx_rate_used: Optional[float] = None
    source: Optional[str] = None
    source_batch_id: Optional[str] = None


class TransactionCreate(BaseModel):
    transaction_id: str = Field(..., min_length=1, max_length=128)
    reference_id: Optional[str] = Field(None, max_length=128)
    payer: str = Field(..., min_length=1, max_length=256)
    payee: str = Field(..., min_length=1, max_length=256)
    payer_fsp: str = Field(..., min_length=1, max_length=128)
    payee_fsp: str = Field(..., min_length=1, max_length=128)
    amount: float = Field(..., gt=0, description="Must be positive")
    currency: str = Field("USD", min_length=3, max_length=3)
    status: str = TransactionStatus.SUCCESS.value
    transfer_state: str = TransferState.COMMITTED.value
    created_at: Optional[datetime] = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("status", "transfer_state")
    @classmethod
    def _upper_state(cls, value: str) -> str:
        return value.strip().upper()


class TransactionResponse(_NormalisedMixin):
    id: int
    transaction_id: str
    reference_id: Optional[str] = None
    payer: str
    payee: str
    payer_fsp: str
    payee_fsp: str
    amount: float
    currency: str
    status: str
    transfer_state: str
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BankTransactionCreate(BaseModel):
    bank_statement_id: str = Field(..., min_length=1)
    account_number: str = Field(..., min_length=1)
    counterparty: str = Field(..., min_length=1)
    amount: float
    currency: str = Field("USD", min_length=3, max_length=3)
    transaction_type: str = TransactionType.CREDIT.value
    reference_number: Optional[str] = None
    bank_name: str = "Central Settlement Bank"


class BankTransactionResponse(_NormalisedMixin):
    id: int
    bank_statement_id: str
    account_number: str
    counterparty: str
    amount: float
    currency: str
    transaction_type: str
    reference_number: Optional[str] = None
    bank_name: Optional[str] = None
    value_date: Optional[datetime] = None
    status: str

    model_config = ConfigDict(from_attributes=True)


class ERPRecordCreate(BaseModel):
    erp_id: str = Field(..., min_length=1)
    invoice_number: Optional[str] = None
    customer_vendor_name: str = Field(..., min_length=1)
    expected_amount: float
    currency: str = Field("USD", min_length=3, max_length=3)
    ledger_account: str = Field(..., min_length=1)


class ERPRecordResponse(_NormalisedMixin):
    id: int
    erp_id: str
    invoice_number: Optional[str] = None
    customer_vendor_name: str
    expected_amount: float
    currency: str
    ledger_account: Optional[str] = None
    posting_date: Optional[datetime] = None
    status: str

    model_config = ConfigDict(from_attributes=True)


class _Page(BaseModel):
    total: int
    limit: int
    offset: int
    returned: int


class PaginatedTransactions(_Page):
    items: List[TransactionResponse] = Field(default_factory=list)


class PaginatedBankTransactions(_Page):
    items: List[BankTransactionResponse] = Field(default_factory=list)


class PaginatedERPRecords(_Page):
    items: List[ERPRecordResponse] = Field(default_factory=list)
