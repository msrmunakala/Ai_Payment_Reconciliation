import csv
import io
import uuid
from datetime import date, datetime, time

import pandas as pd
from sqlalchemy.orm import Session

from app.models.transaction import Transaction
from app.models.bank_transaction import BankTransaction, ERPRecord


def _value(row, *keys, default=None):
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        return value
    return default


def _text(row, *keys, default):
    return str(_value(row, *keys, default=default))


def _amount(row, *keys):
    value = _value(row, *keys, default=0)
    return float(value)


def _record_date(data_date):
    if data_date is None:
        return datetime.utcnow()
    if isinstance(data_date, datetime):
        return data_date
    if isinstance(data_date, date):
        return datetime.combine(data_date, time.min)
    return datetime.fromisoformat(str(data_date))


def _read_rows(file_content, is_excel):
    if is_excel:
        return pd.read_excel(io.BytesIO(file_content)).to_dict(orient="records")
    text_data = file_content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text_data)))


def _insert_new(db, model, key_name, records):
    if not records:
        return 0

    key_column = getattr(model, key_name)
    ids = [record[key_name] for record in records]
    existing = set()
    for offset in range(0, len(ids), 5000):
        batch_ids = ids[offset:offset + 5000]
        existing.update(
            row[0]
            for row in db.query(key_column).filter(key_column.in_(batch_ids)).all()
        )
    seen = set()
    new_records = []
    for record in records:
        record_id = record[key_name]
        if record_id in existing or record_id in seen:
            continue
        seen.add(record_id)
        new_records.append(model(**record))

    if new_records:
        db.add_all(new_records)
        db.commit()
    return len(new_records)


def _ingest_transactions(rows, db, data_date):
    record_date = _record_date(data_date)
    records = []
    for row in rows:
        records.append({
            "transaction_id": _text(row, "transaction_id", "Transaction ID", default=f"TX-{uuid.uuid4().hex[:8].upper()}"),
            "reference_id": _text(row, "reference_id", "Reference ID", default=f"REF-{uuid.uuid4().hex[:6].upper()}"),
            "payer": _text(row, "payer", "Payer Name", default="Unknown Payer"),
            "payee": _text(row, "payee", "Payee Name", default="Unknown Payee"),
            "payer_fsp": _text(row, "payer_fsp", "Payer FSP", default="BankA"),
            "payee_fsp": _text(row, "payee_fsp", "Payee FSP", default="BankB"),
            "amount": _amount(row, "amount", "Amount"),
            "currency": _text(row, "currency", "Currency", default="USD"),
            "status": "SUCCESS",
            "transfer_state": "COMMITTED",
            "created_at": record_date,
        })
    return _insert_new(db, Transaction, "transaction_id", records)


def _ingest_bank(rows, db, data_date):
    record_date = _record_date(data_date)
    records = []
    for row in rows:
        records.append({
            "bank_statement_id": _text(row, "bank_statement_id", "Statement ID", default=f"BS-{uuid.uuid4().hex[:8].upper()}"),
            "account_number": _text(row, "account_number", "Account Number", default="ACC-10098"),
            "counterparty": _text(row, "counterparty", "Counterparty", "Payer", default="Unknown"),
            "amount": _amount(row, "amount", "Amount"),
            "currency": _text(row, "currency", "Currency", default="USD"),
            "transaction_type": _text(row, "transaction_type", "Type", default="CREDIT"),
            "reference_number": _text(row, "reference_number", "Reference Number", "ref_id", default=""),
            "value_date": record_date,
        })
    return _insert_new(db, BankTransaction, "bank_statement_id", records)


def _ingest_erp(rows, db, data_date):
    record_date = _record_date(data_date)
    records = []
    for row in rows:
        records.append({
            "erp_id": _text(row, "erp_id", "ERP ID", default=f"ERP-{uuid.uuid4().hex[:8].upper()}"),
            "invoice_number": _text(row, "invoice_number", "Invoice Number", default=""),
            "customer_vendor_name": _text(row, "customer_vendor_name", "Vendor/Customer", default="Vendor Inc"),
            "expected_amount": _amount(row, "expected_amount", "Amount", "amount"),
            "currency": _text(row, "currency", "Currency", default="USD"),
            "ledger_account": _text(row, "ledger_account", "Ledger Account", default="1100-RECEIVABLES"),
            "posting_date": record_date,
        })
    return _insert_new(db, ERPRecord, "erp_id", records)


def _ingest(file_content, db, data_date, is_excel, source):
    rows = _read_rows(file_content, is_excel)
    if source == "ledger":
        count = _ingest_transactions(rows, db, data_date)
        return {"message": f"Successfully ingested {count} ledger records"}
    if source == "bank":
        count = _ingest_bank(rows, db, data_date)
        return {"message": f"Successfully ingested {count} bank statement records"}
    count = _ingest_erp(rows, db, data_date)
    return {"message": f"Successfully ingested {count} ERP records"}


def ingest_transactions_excel(file_content: bytes, db: Session, data_date=None):
    return _ingest(file_content, db, data_date, True, "ledger")


def ingest_bank_excel(file_content: bytes, db: Session, data_date=None):
    return _ingest(file_content, db, data_date, True, "bank")


def ingest_erp_excel(file_content: bytes, db: Session, data_date=None):
    return _ingest(file_content, db, data_date, True, "erp")


def ingest_transactions_csv(file_content: bytes, db: Session, data_date=None):
    return _ingest(file_content, db, data_date, False, "ledger")


def ingest_bank_csv(file_content: bytes, db: Session, data_date=None):
    return _ingest(file_content, db, data_date, False, "bank")


def ingest_erp_csv(file_content: bytes, db: Session, data_date=None):
    return _ingest(file_content, db, data_date, False, "erp")


def ingest_uploaded_file(source: str, filename: str, file_content: bytes, db: Session, data_date=None):
    """Bulk-ingest one ledger, bank, or ERP file and assign its selected data date."""
    name = (filename or "").lower()
    source = (source or "ledger").lower()
    is_excel = name.endswith((".xlsx", ".xls"))

    if source in ("ledger", "mojaloop", "hub"):
        return _ingest(file_content, db, data_date, is_excel, "ledger")
    if source in ("bank", "settlement", "statement"):
        return _ingest(file_content, db, data_date, is_excel, "bank")
    if source in ("erp", "invoice"):
        return _ingest(file_content, db, data_date, is_excel, "erp")
    raise ValueError(f"Unknown file source '{source}'. Use ledger, bank, or erp.")
