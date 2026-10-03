"""CSV / XLSX ingestion for Mojaloop, bank and ERP records.

Problems in the original implementation:

* One ``SELECT`` per input row to test for duplicates (an N+1 on ingest). A
  10,000-row file issued 10,000 queries before inserting anything.
* Duplicates *within* a single file were not caught, because ``autoflush`` is
  off and the existence check could not see pending adds.
* ``float(row.get("amount", 0.0))`` raised mid-loop on a malformed cell, and with
  no ``rollback`` the session was left dirty and every prior row in the batch was
  lost with no report of what failed.
* A missing ID column minted a fresh UUID, which guaranteed the row could never
  be recognised as a duplicate on a re-upload of the same file.
* No file size limit and no content-type validation.

This version does a single bulk existence query, de-duplicates within the file,
derives a deterministic natural key when no ID column is present so re-uploading
the same file is idempotent, collects per-row errors instead of aborting, and
normalises amounts to the base currency as part of ingestion.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bank_transaction import BankTransaction, ERPRecord
from app.models.transaction import Transaction
from app.services.fx_service import FXConverter, get_converter

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_ROWS = 100_000


class IngestionError(ValueError):
    """Raised when a file cannot be parsed at all."""


@dataclass
class IngestionResult:
    """Outcome of one upload, including what was rejected and why."""

    record_type: str
    batch_id: str
    rows_read: int = 0
    inserted: int = 0
    duplicates_in_file: int = 0
    already_present: int = 0
    failed: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    unresolved_currencies: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "record_type": self.record_type,
            "batch_id": self.batch_id,
            "rows_read": self.rows_read,
            "inserted": self.inserted,
            "skipped_duplicate_in_file": self.duplicates_in_file,
            "skipped_already_present": self.already_present,
            "failed": self.failed,
            # Cap the error list so one badly formed file cannot produce a
            # multi-megabyte response.
            "errors": self.errors[:50],
            "error_count": len(self.errors),
            "unresolved_currencies": self.unresolved_currencies,
            "message": (
                f"Ingested {self.inserted} of {self.rows_read} {self.record_type} row(s). "
                f"{self.already_present} already present, "
                f"{self.duplicates_in_file} duplicated within the file, "
                f"{self.failed} rejected."
            ),
        }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _check_size(file_content: bytes) -> None:
    if not file_content:
        raise IngestionError("Uploaded file is empty.")
    if len(file_content) > MAX_UPLOAD_BYTES:
        raise IngestionError(
            f"Uploaded file is {len(file_content) / 1_048_576:.1f} MB, which exceeds the "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB limit."
        )


def _rows_from_csv(file_content: bytes) -> List[Dict[str, Any]]:
    _check_size(file_content)
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = file_content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 cannot fail
        raise IngestionError("Could not decode file as UTF-8 or Latin-1.")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise IngestionError("CSV file has no header row.")
    return [dict(row) for row in reader]


def _rows_from_excel(file_content: bytes) -> List[Dict[str, Any]]:
    _check_size(file_content)
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise IngestionError(
            "Excel ingestion requires pandas and openpyxl to be installed."
        ) from exc

    try:
        frame = pd.read_excel(io.BytesIO(file_content))
    except Exception as exc:
        raise IngestionError(f"Could not read the workbook: {exc}") from exc

    if frame.empty:
        return []
    # NaN -> None so downstream ``or`` fallbacks behave predictably.
    return frame.where(frame.notna(), None).to_dict(orient="records")


def _pick(row: Dict[str, Any], *names: str) -> Optional[Any]:
    """First non-empty value among a set of accepted column spellings."""
    for name in names:
        if name in row:
            value = row[name]
            if value is not None and str(value).strip() != "":
                return value
    # Case-insensitive second pass.
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for name in names:
        value = lowered.get(name.strip().lower())
        if value is not None and str(value).strip() != "":
            return value
    return None


def _as_str(value: Optional[Any], default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _as_float(value: Optional[Any], field_name: str) -> float:
    """Parse a money value, tolerating thousands separators and currency marks."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"'{field_name}' is required but was empty")
    text = str(value).strip()
    # Strip grouping separators, currency symbols and spaces.
    cleaned = (
        text.replace(",", "")
        .replace("\u00a0", "")
        .replace(" ", "")
        .replace("$", "")
        .replace("₹", "")
        .replace("€", "")
        .replace("£", "")
    )
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        parsed = float(cleaned)
    except ValueError as exc:
        raise ValueError(f"'{field_name}' value {text!r} is not a number") from exc
    return -parsed if negative else parsed


def _as_datetime(value: Optional[Any], default: Optional[datetime] = None) -> Optional[datetime]:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%d-%b-%Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text[: len(fmt) + 4], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return default


def _natural_key(*parts: Any) -> str:
    """Deterministic surrogate ID derived from a row's business content.

    Used when the file carries no identifier column. Because it is a hash of the
    content rather than a fresh UUID, re-uploading the same file is idempotent
    instead of silently doubling the data.
    """
    joined = "|".join(_as_str(part).lower() for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16].upper()


# ---------------------------------------------------------------------------
# Generic ingestion driver
# ---------------------------------------------------------------------------


def _ingest(
    db: Session,
    rows: Sequence[Dict[str, Any]],
    record_type: str,
    model,
    id_column,
    build: Callable[[Dict[str, Any], str, FXConverter, datetime], Tuple[str, Any]],
) -> IngestionResult:
    """Shared ingestion pipeline: parse, dedupe in bulk, insert, report.

    ``build`` returns ``(natural_id, orm_instance)`` for a row or raises
    ``ValueError`` with a human-readable reason.
    """
    batch_id = f"{record_type}-{uuid.uuid4().hex[:10]}"
    result = IngestionResult(record_type=record_type, batch_id=batch_id)
    result.rows_read = len(rows)

    if not rows:
        return result

    if len(rows) > MAX_ROWS:
        raise IngestionError(
            f"File contains {len(rows)} rows, which exceeds the {MAX_ROWS} row limit. "
            "Split the file and upload in batches."
        )

    converter = get_converter(db)
    now = datetime.utcnow()

    # Pass 1: build candidates and collect per-row failures.
    candidates: List[Tuple[str, Any]] = []
    seen_in_file: Set[str] = set()

    for offset, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            natural_id, instance = build(row, batch_id, converter, now)
        except ValueError as exc:
            result.failed += 1
            result.errors.append({"row": offset, "error": str(exc)})
            continue
        except Exception as exc:  # pragma: no cover - defensive
            result.failed += 1
            result.errors.append({"row": offset, "error": f"unexpected: {exc}"})
            continue

        if natural_id in seen_in_file:
            result.duplicates_in_file += 1
            continue
        seen_in_file.add(natural_id)
        candidates.append((natural_id, instance))

    if not candidates:
        result.unresolved_currencies = converter.unknown_currencies
        return result

    # Pass 2: one bulk existence query instead of one query per row.
    ids = [natural_id for natural_id, _instance in candidates]
    existing: Set[str] = set()
    chunk = 500
    for start in range(0, len(ids), chunk):
        batch = ids[start : start + chunk]
        existing.update(
            value
            for (value,) in db.query(id_column).filter(id_column.in_(batch)).all()
        )

    to_insert = []
    for natural_id, instance in candidates:
        if natural_id in existing:
            result.already_present += 1
            continue
        to_insert.append(instance)

    if not to_insert:
        result.unresolved_currencies = converter.unknown_currencies
        return result

    # Pass 3: insert as one unit of work, rolling back cleanly on failure.
    try:
        db.add_all(to_insert)
        db.commit()
        result.inserted = len(to_insert)
    except Exception as exc:
        db.rollback()
        logger.exception("Ingestion batch %s failed during insert", batch_id)
        raise IngestionError(
            f"Insert failed and the batch was rolled back; no rows were added: {exc}"
        ) from exc

    result.unresolved_currencies = converter.unknown_currencies
    logger.info(
        "Ingested batch %s: %d inserted, %d already present, %d in-file duplicates, %d failed",
        batch_id,
        result.inserted,
        result.already_present,
        result.duplicates_in_file,
        result.failed,
    )
    return result


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def _build_transaction(
    row: Dict[str, Any], batch_id: str, converter: FXConverter, now: datetime
) -> Tuple[str, Transaction]:
    amount = _as_float(_pick(row, "amount", "Amount", "transfer_amount"), "amount")
    currency = _as_str(_pick(row, "currency", "Currency"), settings.fx.base_currency).upper()
    payer = _as_str(_pick(row, "payer", "Payer Name", "payer_name", "from"), "Unknown Payer")
    payee = _as_str(_pick(row, "payee", "Payee Name", "payee_name", "to"), "Unknown Payee")
    reference = _as_str(_pick(row, "reference_id", "Reference ID", "reference", "reference_number"))
    raw_date = _pick(row, "created_at", "Created At", "date", "Date", "transaction_date")
    created_at = _as_datetime(raw_date, now)

    # The natural key is derived only from values present in the file. Including
    # a defaulted timestamp would make every re-upload produce a new key and
    # therefore a duplicate row.
    transaction_id = _as_str(
        _pick(row, "transaction_id", "Transaction ID", "transactionId", "id")
    ) or f"TX-{_natural_key(payer, payee, amount, currency, reference, raw_date)}"

    conversion = converter.convert(amount, currency)

    return transaction_id, Transaction(
        transaction_id=transaction_id,
        reference_id=reference or None,
        payer=payer,
        payee=payee,
        payer_fsp=_as_str(_pick(row, "payer_fsp", "Payer FSP", "payerFsp"), "UNKNOWN-FSP"),
        payee_fsp=_as_str(_pick(row, "payee_fsp", "Payee FSP", "payeeFsp"), "UNKNOWN-FSP"),
        amount=amount,
        currency=currency,
        status=_as_str(_pick(row, "status", "Status"), "SUCCESS").upper(),
        transfer_state=_as_str(
            _pick(row, "transfer_state", "transferState", "Transfer State"), "COMMITTED"
        ).upper(),
        created_at=created_at,
        amount_base=conversion.amount,
        base_currency=conversion.to_currency,
        fx_rate_used=conversion.rate,
        normalized_at=now,
        source="upload",
        source_batch_id=batch_id,
        ingested_at=now,
    )


def _build_bank(
    row: Dict[str, Any], batch_id: str, converter: FXConverter, now: datetime
) -> Tuple[str, BankTransaction]:
    amount = _as_float(_pick(row, "amount", "Amount", "credit", "value"), "amount")
    currency = _as_str(_pick(row, "currency", "Currency"), settings.fx.base_currency).upper()
    counterparty = _as_str(
        _pick(row, "counterparty", "Counterparty", "Payer", "payer", "description"),
        "Unknown Counterparty",
    )
    reference = _as_str(
        _pick(row, "reference_number", "Reference Number", "ref_id", "reference")
    )
    account = _as_str(_pick(row, "account_number", "Account Number", "account"), "UNKNOWN-ACC")
    raw_date = _pick(row, "value_date", "Value Date", "date", "Date", "posting_date")
    value_date = _as_datetime(raw_date, now)

    statement_id = _as_str(
        _pick(row, "bank_statement_id", "Statement ID", "statement_id", "id")
    ) or f"BS-{_natural_key(account, counterparty, amount, currency, reference, raw_date)}"

    conversion = converter.convert(amount, currency)

    return statement_id, BankTransaction(
        bank_statement_id=statement_id,
        account_number=account,
        counterparty=counterparty,
        amount=amount,
        currency=currency,
        transaction_type=_as_str(
            _pick(row, "transaction_type", "Type", "dr_cr"), "CREDIT"
        ).upper(),
        reference_number=reference or None,
        value_date=value_date,
        bank_name=_as_str(
            _pick(row, "bank_name", "Bank Name", "bank"), "Central Settlement Bank"
        ),
        amount_base=conversion.amount,
        base_currency=conversion.to_currency,
        fx_rate_used=conversion.rate,
        normalized_at=now,
        source="upload",
        source_batch_id=batch_id,
        ingested_at=now,
    )


def _build_erp(
    row: Dict[str, Any], batch_id: str, converter: FXConverter, now: datetime
) -> Tuple[str, ERPRecord]:
    amount = _as_float(
        _pick(row, "expected_amount", "Expected Amount", "amount", "Amount", "invoice_amount"),
        "expected_amount",
    )
    currency = _as_str(_pick(row, "currency", "Currency"), settings.fx.base_currency).upper()
    name = _as_str(
        _pick(row, "customer_vendor_name", "Vendor/Customer", "vendor", "customer", "name"),
        "Unknown Vendor",
    )
    invoice = _as_str(_pick(row, "invoice_number", "Invoice Number", "invoice", "reference"))
    raw_date = _pick(row, "posting_date", "Posting Date", "date", "Date", "invoice_date")
    posting_date = _as_datetime(raw_date, now)

    erp_id = _as_str(
        _pick(row, "erp_id", "ERP ID", "id", "document_number")
    ) or f"ERP-{_natural_key(name, amount, currency, invoice, raw_date)}"

    conversion = converter.convert(amount, currency)

    return erp_id, ERPRecord(
        erp_id=erp_id,
        invoice_number=invoice or None,
        customer_vendor_name=name,
        expected_amount=amount,
        currency=currency,
        posting_date=posting_date,
        ledger_account=_as_str(
            _pick(row, "ledger_account", "Ledger Account", "gl_account"), "1100-RECEIVABLES"
        ),
        amount_base=conversion.amount,
        base_currency=conversion.to_currency,
        fx_rate_used=conversion.rate,
        normalized_at=now,
        source="upload",
        source_batch_id=batch_id,
        ingested_at=now,
    )


# ---------------------------------------------------------------------------
# Public entry points (signatures preserved for the existing routes)
# ---------------------------------------------------------------------------


def ingest_transactions_rows(db: Session, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return _ingest(
        db, rows, "transaction", Transaction, Transaction.transaction_id, _build_transaction
    ).as_dict()


def ingest_bank_rows(db: Session, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return _ingest(
        db, rows, "bank", BankTransaction, BankTransaction.bank_statement_id, _build_bank
    ).as_dict()


def ingest_erp_rows(db: Session, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return _ingest(db, rows, "erp", ERPRecord, ERPRecord.erp_id, _build_erp).as_dict()


def ingest_transactions_excel(file_content: bytes, db: Session) -> Dict[str, Any]:
    return ingest_transactions_rows(db, _rows_from_excel(file_content))


def ingest_bank_excel(file_content: bytes, db: Session) -> Dict[str, Any]:
    return ingest_bank_rows(db, _rows_from_excel(file_content))


def ingest_erp_excel(file_content: bytes, db: Session) -> Dict[str, Any]:
    return ingest_erp_rows(db, _rows_from_excel(file_content))


def ingest_transactions_csv(file_content: bytes, db: Session) -> Dict[str, Any]:
    return ingest_transactions_rows(db, _rows_from_csv(file_content))


def ingest_bank_csv(file_content: bytes, db: Session) -> Dict[str, Any]:
    return ingest_bank_rows(db, _rows_from_csv(file_content))


def ingest_erp_csv(file_content: bytes, db: Session) -> Dict[str, Any]:
    return ingest_erp_rows(db, _rows_from_csv(file_content))


__all__ = [
    "IngestionError",
    "IngestionResult",
    "MAX_ROWS",
    "MAX_UPLOAD_BYTES",
    "ingest_bank_csv",
    "ingest_bank_excel",
    "ingest_bank_rows",
    "ingest_erp_csv",
    "ingest_erp_excel",
    "ingest_erp_rows",
    "ingest_transactions_csv",
    "ingest_transactions_excel",
    "ingest_transactions_rows",
]
