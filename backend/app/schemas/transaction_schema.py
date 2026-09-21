from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class TransactionCreate(BaseModel):
    transaction_id: str
    reference_id: Optional[str] = None
    payer: str
    payee: str
    payer_fsp: str
    payee_fsp: str
    amount: float
    currency: str = "USD"
    status: str = "SUCCESS"
    transfer_state: str = "COMMITTED"


class TransactionResponse(TransactionCreate):
    id: int
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BankTransactionCreate(BaseModel):
    bank_statement_id: str
    account_number: str
    counterparty: str
    amount: float
    currency: str = "USD"
    transaction_type: str = "CREDIT"
    reference_number: Optional[str] = None
    bank_name: str = "Central Settlement Bank"


class BankTransactionResponse(BankTransactionCreate):
    id: int
    value_date: Optional[datetime] = None
    status: str = "UNRECONCILED"

    model_config = ConfigDict(from_attributes=True)


class ERPRecordCreate(BaseModel):
    erp_id: str
    invoice_number: Optional[str] = None
    customer_vendor_name: str
    expected_amount: float
    currency: str = "USD"
    ledger_account: str


class ERPRecordResponse(ERPRecordCreate):
    id: int
    posting_date: Optional[datetime] = None
    status: str = "OPEN"

    model_config = ConfigDict(from_attributes=True)