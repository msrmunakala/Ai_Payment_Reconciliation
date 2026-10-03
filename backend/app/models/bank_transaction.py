from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from app.core.constants import ERPStatus, LedgerEntryStatus, TransactionType
from app.database.db import Base


class BankTransaction(Base):
    """A line from a bank settlement statement: one ledger we reconcile *against*."""

    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True, index=True)
    bank_statement_id = Column(String, unique=True, index=True, nullable=False)
    account_number = Column(String, nullable=False)
    counterparty = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="USD", index=True)
    transaction_type = Column(String, default=TransactionType.CREDIT.value)
    reference_number = Column(String, index=True, nullable=True)
    value_date = Column(DateTime, default=datetime.utcnow, index=True)
    bank_name = Column(String, default="Central Settlement Bank")
    status = Column(String, default=LedgerEntryStatus.UNRECONCILED.value, index=True)

    # --- normalised (unified-schema) values ---
    amount_base = Column(Float, nullable=True, index=True)
    base_currency = Column(String, nullable=True)
    fx_rate_used = Column(Float, nullable=True)
    normalized_at = Column(DateTime, nullable=True)

    # --- ingestion provenance ---
    source = Column(String, default="upload", index=True)
    source_batch_id = Column(String, nullable=True, index=True)
    raw_payload = Column(Text, nullable=True)
    ingested_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_bank_base_amount_date", "amount_base", "value_date"),
        Index("ix_bank_status_date", "status", "value_date"),
    )


class ERPRecord(Base):
    """An ERP invoice / expected receivable: the second ledger we match against."""

    __tablename__ = "erp_records"

    id = Column(Integer, primary_key=True, index=True)
    erp_id = Column(String, unique=True, index=True, nullable=False)
    invoice_number = Column(String, index=True, nullable=True)
    customer_vendor_name = Column(String, nullable=False)
    expected_amount = Column(Float, nullable=False)
    currency = Column(String, default="USD", index=True)
    posting_date = Column(DateTime, default=datetime.utcnow, index=True)
    ledger_account = Column(String, nullable=False)
    status = Column(String, default=ERPStatus.OPEN.value, index=True)

    # --- normalised (unified-schema) values ---
    amount_base = Column(Float, nullable=True, index=True)
    base_currency = Column(String, nullable=True)
    fx_rate_used = Column(Float, nullable=True)
    normalized_at = Column(DateTime, nullable=True)

    # --- ingestion provenance ---
    source = Column(String, default="upload", index=True)
    source_batch_id = Column(String, nullable=True, index=True)
    raw_payload = Column(Text, nullable=True)
    ingested_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_erp_base_amount_date", "amount_base", "posting_date"),
        Index("ix_erp_status_date", "status", "posting_date"),
    )
