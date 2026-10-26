from typing import List
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.transaction import Transaction
from app.models.bank_transaction import BankTransaction, ERPRecord
from app.schemas.transaction_schema import (
    TransactionCreate,
    TransactionResponse,
    BankTransactionResponse,
    ERPRecordResponse,
)
from app.services.ingestion_service import (
    ingest_bank_csv,
    ingest_erp_csv,
    ingest_transactions_csv,
    ingest_transactions_excel,
    ingest_bank_excel,
    ingest_erp_excel,
    ingest_uploaded_file,
)

router = APIRouter(
    prefix="/transactions",
    tags=["Transactions"]
)


@router.post("/", response_model=TransactionResponse)
def create_transaction(
    transaction: TransactionCreate,
    db: Session = Depends(get_db)
):
    existing = db.query(Transaction).filter(
        Transaction.transaction_id == transaction.transaction_id
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Transaction already exists"
        )

    new_transaction = Transaction(**transaction.model_dump())
    db.add(new_transaction)
    db.commit()
    db.refresh(new_transaction)

    return new_transaction


@router.get("/", response_model=List[TransactionResponse])
def get_transactions(db: Session = Depends(get_db)):
    return db.query(Transaction).order_by(Transaction.id.desc()).all()


@router.get("/bank", response_model=List[BankTransactionResponse])
def get_bank_transactions(db: Session = Depends(get_db)):
    return db.query(BankTransaction).all()


@router.get("/erp", response_model=List[ERPRecordResponse])
def get_erp_records(db: Session = Depends(get_db)):
    return db.query(ERPRecord).all()


@router.post("/ingest")
async def upload_reconciliation_file(
    file: UploadFile = File(...),
    source: str = Form("ledger"),
    db: Session = Depends(get_db),
):
    """Upload a ledger, bank settlement, or ERP file for later matching."""
    content = await file.read()
    try:
        result = ingest_uploaded_file(source, file.filename or "", content, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}") from exc
    result["filename"] = file.filename
    result["source"] = source
    return result


# CSV Ingestion Routes
@router.post("/ingest/ledger-csv")
async def upload_ledger_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    return ingest_transactions_csv(content, db)


@router.post("/ingest/bank-csv")
async def upload_bank_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    return ingest_bank_csv(content, db)


@router.post("/ingest/erp-csv")
async def upload_erp_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    return ingest_erp_csv(content, db)


# Excel (.xlsx / .xls) Ingestion Routes
@router.post("/ingest/excel")
async def upload_transactions_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    return ingest_transactions_excel(content, db)


@router.post("/ingest/bank-excel")
async def upload_bank_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    return ingest_bank_excel(content, db)


@router.post("/ingest/erp-excel")
async def upload_erp_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    return ingest_erp_excel(content, db)


@router.get("/{transaction_id}", response_model=TransactionResponse)
def get_transaction(
    transaction_id: str,
    db: Session = Depends(get_db)
):
    transaction = db.query(Transaction).filter(
        (Transaction.transaction_id == transaction_id) | (Transaction.id == int(transaction_id) if transaction_id.isdigit() else False)
    ).first()

    if not transaction:
        raise HTTPException(
            status_code=404,
            detail="Transaction not found"
        )

    return transaction


@router.delete("/{transaction_id}")
def delete_transaction(
    transaction_id: str,
    db: Session = Depends(get_db)
):
    transaction = db.query(Transaction).filter(
        Transaction.transaction_id == transaction_id
    ).first()

    if not transaction:
        raise HTTPException(
            status_code=404,
            detail="Transaction not found"
        )

    db.delete(transaction)
    db.commit()

    return {"message": "Transaction deleted successfully"}