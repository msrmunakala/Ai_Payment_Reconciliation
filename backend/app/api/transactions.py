"""Transaction and ledger endpoints, including file ingestion.

Changes from the original router:

* All list endpoints are paginated. Previously each one returned the whole table.
* The upload routes run their blocking parse/insert work in a worker thread.
  They were declared ``async def`` and then called synchronous pandas and
  SQLAlchemy code directly, which blocked the event loop for the whole upload.
* Uploads validate the file extension and report per-row rejections instead of
  failing the whole batch on one malformed cell.
* ``GET /{transaction_id}`` no longer relies on an operator-precedence accident
  (``... if cond else False`` binding over the whole right operand of ``|``).
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.db import get_db
from app.models.bank_transaction import BankTransaction, ERPRecord
from app.models.transaction import Transaction
from app.schemas.transaction_schema import (
    PaginatedBankTransactions,
    PaginatedERPRecords,
    PaginatedTransactions,
    TransactionCreate,
    TransactionResponse,
)
from app.services.ingestion_service import (
    IngestionError,
    ingest_bank_csv,
    ingest_bank_excel,
    ingest_erp_csv,
    ingest_erp_excel,
    ingest_transactions_csv,
    ingest_transactions_excel,
)

router = APIRouter(prefix="/transactions", tags=["Transactions"])

CSV_EXTENSIONS = {".csv", ".txt"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}


def _validate_upload(file: UploadFile, allowed: set[str]) -> None:
    name = (file.filename or "").lower()
    if not any(name.endswith(ext) for ext in allowed):
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type for '{file.filename}'. "
                f"Expected one of: {', '.join(sorted(allowed))}"
            ),
        )


async def _handle_upload(file: UploadFile, allowed: set[str], handler, db: Session):
    """Validate, read, then parse and insert off the event loop."""
    _validate_upload(file, allowed)
    content = await file.read()
    try:
        return await run_in_threadpool(handler, content, db)
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@router.post("/", response_model=TransactionResponse, status_code=201)
def create_transaction(transaction: TransactionCreate, db: Session = Depends(get_db)):
    """Create a single transaction, normalising its amount on the way in."""
    from datetime import datetime

    from app.services.fx_service import get_converter

    existing = (
        db.query(Transaction)
        .filter(Transaction.transaction_id == transaction.transaction_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Transaction '{transaction.transaction_id}' already exists",
        )

    payload = transaction.model_dump()
    converter = get_converter(db)
    conversion = converter.convert(payload["amount"], payload.get("currency"))

    row = Transaction(
        **payload,
        amount_base=conversion.amount,
        base_currency=conversion.to_currency,
        fx_rate_used=conversion.rate,
        normalized_at=datetime.utcnow(),
        source="api",
        ingested_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/", response_model=PaginatedTransactions)
def get_transactions(
    currency: Optional[str] = Query(None, min_length=3, max_length=3),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None, description="api / mojaloop / upload / seed"),
    search: Optional[str] = Query(None, description="Match payer, payee or reference"),
    limit: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Transaction)
    if currency:
        query = query.filter(Transaction.currency == currency.upper())
    if status:
        query = query.filter(Transaction.status == status.upper())
    if source:
        query = query.filter(Transaction.source == source)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                Transaction.payer.ilike(pattern),
                Transaction.payee.ilike(pattern),
                Transaction.reference_id.ilike(pattern),
                Transaction.transaction_id.ilike(pattern),
            )
        )

    total = query.count()
    items = query.order_by(Transaction.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(items),
        "items": items,
    }


@router.get("/bank", response_model=PaginatedBankTransactions)
def get_bank_transactions(
    status: Optional[str] = Query(None),
    limit: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(BankTransaction)
    if status:
        query = query.filter(BankTransaction.status == status.upper())
    total = query.count()
    items = query.order_by(BankTransaction.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(items),
        "items": items,
    }


@router.get("/erp", response_model=PaginatedERPRecords)
def get_erp_records(
    status: Optional[str] = Query(None),
    limit: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(ERPRecord)
    if status:
        query = query.filter(ERPRecord.status == status.upper())
    total = query.count()
    items = query.order_by(ERPRecord.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(items),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


@router.post("/ingest/transactions-csv")
async def upload_transactions_csv(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    return await _handle_upload(file, CSV_EXTENSIONS, ingest_transactions_csv, db)


@router.post("/ingest/bank-csv")
async def upload_bank_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await _handle_upload(file, CSV_EXTENSIONS, ingest_bank_csv, db)


@router.post("/ingest/erp-csv")
async def upload_erp_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await _handle_upload(file, CSV_EXTENSIONS, ingest_erp_csv, db)


@router.post("/ingest/excel")
async def upload_transactions_excel(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    return await _handle_upload(file, EXCEL_EXTENSIONS, ingest_transactions_excel, db)


@router.post("/ingest/bank-excel")
async def upload_bank_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await _handle_upload(file, EXCEL_EXTENSIONS, ingest_bank_excel, db)


@router.post("/ingest/erp-excel")
async def upload_erp_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return await _handle_upload(file, EXCEL_EXTENSIONS, ingest_erp_excel, db)


# ---------------------------------------------------------------------------
# Single transaction
# ---------------------------------------------------------------------------


@router.get("/{transaction_id}", response_model=TransactionResponse)
def get_transaction(transaction_id: str, db: Session = Depends(get_db)):
    """Look up by business id, or by numeric surrogate key if the path is numeric."""
    query = db.query(Transaction).filter(Transaction.transaction_id == transaction_id)
    transaction = query.first()

    if transaction is None and transaction_id.isdigit():
        transaction = (
            db.query(Transaction).filter(Transaction.id == int(transaction_id)).first()
        )

    if transaction is None:
        raise HTTPException(
            status_code=404, detail=f"Transaction '{transaction_id}' not found"
        )
    return transaction


@router.delete("/{transaction_id}")
def delete_transaction(transaction_id: str, db: Session = Depends(get_db)):
    """Delete a transaction and the reconciliation artefacts that reference it."""
    from app.models.reconciliation import Anomaly, ManualMatch, Reconciliation

    transaction = (
        db.query(Transaction).filter(Transaction.transaction_id == transaction_id).first()
    )
    if transaction is None:
        raise HTTPException(
            status_code=404, detail=f"Transaction '{transaction_id}' not found"
        )

    # There are no database-level foreign keys between these tables, so the
    # dependent rows must be cleaned up explicitly or they become orphans.
    anomalies = (
        db.query(Anomaly).filter(Anomaly.transaction_id == transaction_id).delete(
            synchronize_session=False
        )
    )
    results = (
        db.query(Reconciliation)
        .filter(Reconciliation.transaction_id == transaction_id)
        .delete(synchronize_session=False)
    )
    overrides = (
        db.query(ManualMatch)
        .filter(ManualMatch.transaction_id == transaction_id)
        .delete(synchronize_session=False)
    )

    db.delete(transaction)
    db.commit()

    return {
        "message": f"Transaction '{transaction_id}' deleted.",
        "reconciliation_rows_removed": results,
        "anomalies_removed": anomalies,
        "manual_overrides_removed": overrides,
    }
