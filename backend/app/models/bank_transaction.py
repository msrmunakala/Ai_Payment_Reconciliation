from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime
from app.database.db import Base


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True, index=True)
    bank_statement_id = Column(String, unique=True, index=True, nullable=False)
    account_number = Column(String, nullable=False)
    counterparty = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="USD")
    transaction_type = Column(String, default="CREDIT")  # CREDIT / DEBIT
    reference_number = Column(String, index=True, nullable=True)
    value_date = Column(DateTime, default=datetime.utcnow)
    bank_name = Column(String, default="Central Settlement Bank")
    status = Column(String, default="UNRECONCILED")


class ERPRecord(Base):
    __tablename__ = "erp_records"

    id = Column(Integer, primary_key=True, index=True)
    erp_id = Column(String, unique=True, index=True, nullable=False)
    invoice_number = Column(String, index=True, nullable=True)
    customer_vendor_name = Column(String, nullable=False)
    expected_amount = Column(Float, nullable=False)
    currency = Column(String, default="USD")
    posting_date = Column(DateTime, default=datetime.utcnow)
    ledger_account = Column(String, nullable=False)
    status = Column(String, default="OPEN")
