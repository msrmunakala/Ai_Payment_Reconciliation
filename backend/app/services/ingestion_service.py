import csv
import io
import uuid
import pandas as pd
from sqlalchemy.orm import Session
from app.models.transaction import Transaction
from app.models.bank_transaction import BankTransaction, ERPRecord


def ingest_transactions_excel(file_content: bytes, db: Session):
    df = pd.read_excel(io.BytesIO(file_content))
    count = 0

    for _, row in df.iterrows():
        tx_id = str(row.get("transaction_id") or row.get("Transaction ID") or f"TX-{uuid.uuid4().hex[:8].upper()}")
        existing = db.query(Transaction).filter(Transaction.transaction_id == tx_id).first()

        if not existing:
            tx = Transaction(
                transaction_id=tx_id,
                reference_id=str(row.get("reference_id") or row.get("Reference ID") or f"REF-{uuid.uuid4().hex[:6].upper()}"),
                payer=str(row.get("payer") or row.get("Payer Name") or "Unknown Payer"),
                payee=str(row.get("payee") or row.get("Payee Name") or "Unknown Payee"),
                payer_fsp=str(row.get("payer_fsp") or row.get("Payer FSP") or "BankA"),
                payee_fsp=str(row.get("payee_fsp") or row.get("Payee FSP") or "BankB"),
                amount=float(row.get("amount") or row.get("Amount") or 0.0),
                currency=str(row.get("currency") or row.get("Currency") or "USD"),
                status="SUCCESS",
                transfer_state="COMMITTED"
            )
            db.add(tx)
            count += 1

    db.commit()
    return {"message": f"Successfully ingested {count} Mojaloop transaction records from Excel sheet"}


def ingest_bank_excel(file_content: bytes, db: Session):
    df = pd.read_excel(io.BytesIO(file_content))
    count = 0

    for _, row in df.iterrows():
        b_id = str(row.get("bank_statement_id") or row.get("Statement ID") or f"BS-{uuid.uuid4().hex[:8].upper()}")
        existing = db.query(BankTransaction).filter(BankTransaction.bank_statement_id == b_id).first()

        if not existing:
            bank_tx = BankTransaction(
                bank_statement_id=b_id,
                account_number=str(row.get("account_number") or row.get("Account Number") or "ACC-10098"),
                counterparty=str(row.get("counterparty") or row.get("Counterparty") or row.get("Payer") or "Unknown"),
                amount=float(row.get("amount") or row.get("Amount") or 0.0),
                currency=str(row.get("currency") or row.get("Currency") or "USD"),
                transaction_type=str(row.get("transaction_type") or row.get("Type") or "CREDIT"),
                reference_number=str(row.get("reference_number") or row.get("Reference Number") or "")
            )
            db.add(bank_tx)
            count += 1

    db.commit()
    return {"message": f"Successfully ingested {count} Bank Statement records from Excel sheet"}


def ingest_erp_excel(file_content: bytes, db: Session):
    df = pd.read_excel(io.BytesIO(file_content))
    count = 0

    for _, row in df.iterrows():
        e_id = str(row.get("erp_id") or row.get("ERP ID") or f"ERP-{uuid.uuid4().hex[:8].upper()}")
        existing = db.query(ERPRecord).filter(ERPRecord.erp_id == e_id).first()

        if not existing:
            erp_rec = ERPRecord(
                erp_id=e_id,
                invoice_number=str(row.get("invoice_number") or row.get("Invoice Number") or ""),
                customer_vendor_name=str(row.get("customer_vendor_name") or row.get("Vendor/Customer") or "Vendor Inc"),
                expected_amount=float(row.get("expected_amount") or row.get("Amount") or 0.0),
                currency=str(row.get("currency") or row.get("Currency") or "USD"),
                ledger_account=str(row.get("ledger_account") or row.get("Ledger Account") or "1100-RECEIVABLES")
            )
            db.add(erp_rec)
            count += 1

    db.commit()
    return {"message": f"Successfully ingested {count} ERP records from Excel sheet"}


def ingest_bank_csv(file_content: bytes, db: Session):
    text_data = file_content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text_data))
    count = 0

    for row in reader:
        b_id = row.get("bank_statement_id") or f"BS-{uuid.uuid4().hex[:8].upper()}"
        existing = db.query(BankTransaction).filter(BankTransaction.bank_statement_id == b_id).first()
        if not existing:
            bank_tx = BankTransaction(
                bank_statement_id=b_id,
                account_number=row.get("account_number", "ACC-10098"),
                counterparty=row.get("counterparty", "Unknown"),
                amount=float(row.get("amount", 0.0)),
                currency=row.get("currency", "USD"),
                transaction_type=row.get("transaction_type", "CREDIT"),
                reference_number=row.get("reference_number") or row.get("ref_id")
            )
            db.add(bank_tx)
            count += 1

    db.commit()
    return {"message": f"Successfully ingested {count} bank statement records from CSV"}


def ingest_erp_csv(file_content: bytes, db: Session):
    text_data = file_content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text_data))
    count = 0

    for row in reader:
        e_id = row.get("erp_id") or f"ERP-{uuid.uuid4().hex[:8].upper()}"
        existing = db.query(ERPRecord).filter(ERPRecord.erp_id == e_id).first()
        if not existing:
            erp_rec = ERPRecord(
                erp_id=e_id,
                invoice_number=row.get("invoice_number"),
                customer_vendor_name=row.get("customer_vendor_name", "Vendor Inc"),
                expected_amount=float(row.get("expected_amount") or row.get("amount", 0.0)),
                currency=row.get("currency", "USD"),
                ledger_account=row.get("ledger_account", "1100-RECEIVABLES")
            )
            db.add(erp_rec)
            count += 1

    db.commit()
    return {"message": f"Successfully ingested {count} ERP records from CSV"}
