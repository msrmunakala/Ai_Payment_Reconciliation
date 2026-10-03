"""Application entrypoint.

Security and lifecycle changes from the original ``main.py``:

* ``init_db()`` no longer runs at module import time. Importing the app had a
  database side effect, which made testing and migrations awkward.
* The API key is compared with ``hmac.compare_digest`` instead of ``!=``, and the
  app refuses to start in production while the publicly known demo key is in use.
* ``require_api_key`` previously accepted a *missing* header
  (``if x_api_key is not None and x_api_key != API_KEY``) and was only saved by
  the middleware. It now rejects a missing key as well.
* CORS is an explicit allow-list from configuration. ``allow_origins=["*"]``
  together with ``allow_credentials=True`` is rejected by browsers anyway.
* A simple fixed-window rate limit protects the write endpoints.
* ``os.popen("date /t")`` in a request handler is gone.
* Unhandled exceptions return a scrubbed JSON error instead of leaking a stack
  trace, and are logged server-side with their correlation id.
"""

from __future__ import annotations

import csv
import hmac
import io
import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Deque, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.dashboard import router as dashboard_router
from app.api.forecast import router as forecast_router
from app.api.fx import router as fx_router
from app.api.mojaloop import router as mojaloop_router
from app.api.reconciliation import router as reconciliation_router
from app.api.scheduler import router as scheduler_router
from app.api.settlement import router as settlement_router
from app.api.transactions import router as transactions_router
from app.core.config import settings
from app.core.constants import AnomalyStatus, MatchStatus, SEVERITY_ORDER
from app.database.db import get_db, session_scope
from app.models.reconciliation import Anomaly, Reconciliation
from app.models.transaction import Transaction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("app.main")

PUBLIC_PATHS = {"/", "/health", "/ready", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _key_matches(candidate: Optional[str]) -> bool:
    """Constant-time comparison, so a timing side channel cannot leak the key."""
    if not candidate:
        return False
    return hmac.compare_digest(candidate, settings.security.api_key)


def require_api_key(x_api_key: Optional[str] = Header(None)) -> str:
    """Dependency form of the API key check. Rejects missing *and* wrong keys."""
    if not _key_matches(x_api_key):
        raise HTTPException(status_code=401, detail="A valid X-API-Key is required.")
    return x_api_key


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

_request_log: Dict[str, Deque[float]] = defaultdict(deque)


def _rate_limited(client_key: str) -> bool:
    """Fixed-window in-process limiter.

    Adequate for a single instance. A multi-replica deployment needs a shared
    counter (Redis) because this state is per-process.
    """
    if not settings.security.rate_limit_enabled:
        return False

    window = settings.security.rate_limit_window_seconds
    allowance = settings.security.rate_limit_requests
    now = time.monotonic()
    bucket = _request_log[client_key]

    while bucket and now - bucket[0] > window:
        bucket.popleft()

    if len(bucket) >= allowance:
        return True

    bucket.append(now)
    return False


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate configuration, reconcile the schema, seed, and start the scheduler."""
    for warning in settings.validate():
        logger.warning("CONFIG: %s", warning)

    from app.database.init_db import init_db
    from app.database.schema_sync import auto_sync_if_enabled

    init_db()
    report = auto_sync_if_enabled()
    if report is not None and report.errors:
        logger.error("Schema reconciliation reported errors: %s", report.errors)

    try:
        with session_scope() as db:
            has_data = db.query(Transaction.id).first() is not None

            if not has_data and settings.seed_on_startup:
                from app.services.reconciliation_service import seed_demo_data

                logger.info("Empty database detected; seeding synthetic demo data")
                await run_in_threadpool(seed_demo_data, db, 150, 42, True, 90)

            elif has_data:
                # A database that predates the normalisation columns has NULL
                # amount_base, which makes every aggregate fall back to raw
                # amounts and silently sum mixed currencies. Backfill once so the
                # dashboard is correct immediately after an upgrade rather than
                # only after the first reconciliation run.
                from sqlalchemy import func

                pending = (
                    db.query(func.count(Transaction.id))
                    .filter(Transaction.amount_base.is_(None))
                    .scalar()
                    or 0
                )
                if pending:
                    from app.services.reconciliation_service import normalize_amounts

                    logger.info(
                        "Backfilling FX-normalised amounts for %d transaction(s) and "
                        "their ledger rows",
                        pending,
                    )
                    await run_in_threadpool(normalize_amounts, db, None)
    except Exception:
        logger.exception("Startup data preparation failed; continuing")

    from app.services.scheduler_service import shutdown as scheduler_shutdown
    from app.services.scheduler_service import start_if_enabled

    start_if_enabled()

    logger.info(
        "%s v%s ready (env=%s, db=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
        "sqlite" if settings.is_sqlite else "server",
    )

    yield

    scheduler_shutdown()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    description=(
        "AI-powered payment reconciliation, anomaly detection, cash-flow forecasting "
        "and treasury reporting for Mojaloop payment rails."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.security.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization", "X-Signature"],
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Correlation id, API key enforcement, rate limiting and error scrubbing."""
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    path = request.url.path

    if request.method != "OPTIONS" and path not in PUBLIC_PATHS:
        if not _key_matches(request.headers.get("X-API-Key")):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "A valid X-API-Key is required.",
                    "correlation_id": correlation_id,
                },
            )

        client_host = request.client.host if request.client else "unknown"
        if _rate_limited(client_host):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded: more than "
                        f"{settings.security.rate_limit_requests} requests in "
                        f"{settings.security.rate_limit_window_seconds}s."
                    ),
                    "correlation_id": correlation_id,
                },
            )

    try:
        response = await call_next(request)
    except Exception:
        # Log the detail, return a scrubbed body. A stack trace in an HTTP
        # response is an information disclosure.
        logger.exception("Unhandled error on %s %s [%s]", request.method, path, correlation_id)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error.",
                "correlation_id": correlation_id,
            },
        )

    response.headers["X-Correlation-Id"] = correlation_id
    return response


app.include_router(mojaloop_router)
app.include_router(transactions_router)
app.include_router(reconciliation_router)
app.include_router(dashboard_router)
app.include_router(forecast_router)
app.include_router(fx_router)
app.include_router(scheduler_router)
app.include_router(settlement_router)


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------


@app.get("/", tags=["Meta"])
def home():
    return {
        "project": "AI Payment Reconciliation on Mojaloop",
        "category": "AI-on-DPI (Bracket 1)",
        "version": settings.app_version,
        "environment": settings.environment,
        "base_currency": settings.fx.base_currency,
        "api_docs": "/docs",
        "capabilities": [
            "Mojaloop transfers ingested by webhook push",
            "Settlement windows pulled from the central settlement API",
            "Reconciliation scoped to a settlement window",
            "Candidate-generation fuzzy matching engine",
            "Rule-based and statistical anomaly detection",
            "Prophet cash-flow forecasting",
            "Multi-currency FX normalisation with rate history",
            "Configurable automated reconciliation scheduling",
        ],
    }


@app.get("/health", tags=["Meta"])
def health():
    """Liveness: the process is up. Intentionally does not touch the database."""
    return {"status": "healthy", "version": settings.app_version}


@app.get("/ready", tags=["Meta"])
def ready(db: Session = Depends(get_db)):
    """Readiness: the database is reachable and the schema is queryable."""
    from sqlalchemy import text

    try:
        db.execute(text("SELECT 1"))
        transactions = db.query(Transaction.id).limit(1).count()
        return {
            "status": "ready",
            "database": "reachable",
            "has_data": transactions > 0,
        }
    except Exception as exc:
        logger.exception("Readiness probe failed")
        return JSONResponse(
            status_code=503,
            content={"status": "not-ready", "database": "unreachable", "detail": str(exc)[:200]},
        )


# ---------------------------------------------------------------------------
# Demo and reporting
# ---------------------------------------------------------------------------


@app.post("/demo/reset", dependencies=[Depends(require_api_key)], tags=["Demo"])
async def reset_demo(
    count: int = Query(150, ge=10, le=5000, description="Number of transactions to generate"),
    seed: Optional[int] = Query(None, description="Set for a reproducible dataset"),
    history_days: int = Query(90, ge=7, le=365, description="Days of history to span"),
    db: Session = Depends(get_db),
):
    """Regenerate the synthetic dataset and reconcile it."""
    from app.services.reconciliation_service import seed_demo_data

    return await run_in_threadpool(seed_demo_data, db, count, seed, True, history_days)


@app.get("/demo/generator-script", dependencies=[Depends(require_api_key)], tags=["Demo"])
def generator_script():
    """Return the real source of the synthetic data generator.

    The original endpoint served a hand-maintained copy of the function pasted
    into ``main.py`` as a string literal, which had already drifted out of sync
    with the implementation (it documented a different default row count).
    Reading the source with ``inspect`` cannot drift.
    """
    import inspect

    from app.services import reconciliation_service
    from app.services.reconciliation_service import seed_demo_data

    try:
        source = inspect.getsource(seed_demo_data)
    except OSError:  # pragma: no cover - source unavailable in frozen builds
        source = "# Source not available in this deployment."

    return {
        "filename": "backend/app/services/reconciliation_service.py",
        "function": "seed_demo_data",
        "module": reconciliation_service.__name__,
        "code": source,
        "line_count": len(source.splitlines()),
        "note": (
            "Read directly from the running module with inspect.getsource, so it always "
            "matches the code that produced the current dataset."
        ),
    }


@app.get("/anomalies", dependencies=[Depends(require_api_key)], tags=["Reconciliation"])
def get_anomalies(
    status: Optional[str] = Query(AnomalyStatus.OPEN.value),
    limit: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Exception queue ordered worst-first, sorted and paginated in SQL.

    Kept at this path for the existing dashboard. The previous implementation
    loaded every anomaly row and sorted in Python.
    """
    severity_rank = tuple(
        sorted(SEVERITY_ORDER.items(), key=lambda item: item[1])
    )

    query = db.query(Anomaly)
    if status:
        if not AnomalyStatus.has_value(status):
            raise HTTPException(
                status_code=422,
                detail=f"Unknown status. Expected one of: {AnomalyStatus.values()}",
            )
        query = query.filter(Anomaly.status == status)

    from sqlalchemy import case

    rank = case(
        *[(Anomaly.severity == name, order) for name, order in severity_rank],
        else_=99,
    )

    rows = (
        query.order_by(rank.asc(), Anomaly.risk_score.desc(), Anomaly.id)
        .offset(offset)
        .limit(limit)
        .all()
    )

    return [
        {
            "id": row.id,
            "transaction_id": row.transaction_id,
            "type": row.anomaly_type,
            "severity": row.severity,
            "risk_score": row.risk_score,
            "explanation": row.explanation,
            "status": row.status,
            "detector": row.detector,
            "detected_at": row.detected_at.strftime("%Y-%m-%d %H:%M:%S")
            if row.detected_at
            else None,
        }
        for row in rows
    ]


@app.get("/reports/export.csv", dependencies=[Depends(require_api_key)], tags=["Reporting"])
def export_report(
    status: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
    db: Session = Depends(get_db),
):
    """Streamed reconciliation report.

    Rows are yielded in chunks rather than materialised into one string, so a
    large export does not have to fit in memory twice.
    """
    if status is not None and not MatchStatus.has_value(status):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status. Expected one of: {MatchStatus.values()}",
        )

    query = db.query(Reconciliation)
    if status:
        query = query.filter(Reconciliation.status == status)
    query = query.order_by(Reconciliation.id).limit(limit)

    fieldnames = [
        "transaction_id",
        "ledger_id",
        "ledger_type",
        "status",
        "confidence_tier",
        "match_confidence",
        "amount_difference",
        "base_currency",
        "transaction_amount_base",
        "ledger_amount_base",
        "date_difference_days",
        "score_amount",
        "score_name",
        "score_reference",
        "score_date",
        "is_manual",
        "reconciled_at",
        "remarks",
    ]

    def generate():
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)

        for row in query.yield_per(500):
            writer.writerow(
                {
                    "transaction_id": row.transaction_id,
                    "ledger_id": row.ledger_id or "",
                    "ledger_type": row.ledger_type,
                    "status": row.status,
                    "confidence_tier": row.confidence_tier,
                    "match_confidence": row.match_confidence,
                    "amount_difference": row.amount_difference,
                    "base_currency": row.base_currency or "",
                    "transaction_amount_base": row.transaction_amount_base
                    if row.transaction_amount_base is not None
                    else "",
                    "ledger_amount_base": row.ledger_amount_base
                    if row.ledger_amount_base is not None
                    else "",
                    "date_difference_days": row.date_difference_days
                    if row.date_difference_days is not None
                    else "",
                    "score_amount": row.score_amount,
                    "score_name": row.score_name,
                    "score_reference": row.score_reference,
                    "score_date": row.score_date,
                    "is_manual": row.is_manual,
                    "reconciled_at": row.reconciled_at.isoformat()
                    if row.reconciled_at
                    else "",
                    "remarks": row.remarks or "",
                }
            )
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="reconciliation-report-{stamp}.csv"'
            )
        },
    )


@app.get("/demo/export-json", dependencies=[Depends(require_api_key)], tags=["Demo"])
def export_generated_data_json(
    limit: int = Query(1000, ge=1, le=10000, description="Maximum rows per collection"),
    db: Session = Depends(get_db),
):
    """Export the current dataset.

    Bounded by ``limit`` per collection. The original version loaded five entire
    tables into one in-memory JSON document with no limit, and shelled out to
    ``date /t`` for a timestamp.
    """
    from sqlalchemy import func

    from app.models.bank_transaction import BankTransaction, ERPRecord

    def count(model) -> int:
        return int(db.query(func.count(model.id)).scalar() or 0)

    transactions = db.query(Transaction).order_by(Transaction.id).limit(limit).all()
    bank_rows = db.query(BankTransaction).order_by(BankTransaction.id).limit(limit).all()
    erp_rows = db.query(ERPRecord).order_by(ERPRecord.id).limit(limit).all()
    results = db.query(Reconciliation).order_by(Reconciliation.id).limit(limit).all()
    anomalies = db.query(Anomaly).order_by(Anomaly.id).limit(limit).all()

    return {
        "metadata": {
            "title": "AI Payment Reconciliation dataset export",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_currency": settings.fx.base_currency,
            "row_limit_per_collection": limit,
            "totals": {
                "transactions": count(Transaction),
                "bank_transactions": count(BankTransaction),
                "erp_records": count(ERPRecord),
                "reconciliations": count(Reconciliation),
                "anomalies": count(Anomaly),
            },
        },
        "transactions": [
            {
                "transaction_id": row.transaction_id,
                "reference_id": row.reference_id,
                "payer": row.payer,
                "payee": row.payee,
                "payer_fsp": row.payer_fsp,
                "payee_fsp": row.payee_fsp,
                "amount": row.amount,
                "currency": row.currency,
                "amount_base": row.amount_base,
                "base_currency": row.base_currency,
                "fx_rate_used": row.fx_rate_used,
                "status": row.status,
                "transfer_state": row.transfer_state,
                "source": row.source,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in transactions
        ],
        "bank_transactions": [
            {
                "bank_statement_id": row.bank_statement_id,
                "account_number": row.account_number,
                "counterparty": row.counterparty,
                "amount": row.amount,
                "currency": row.currency,
                "amount_base": row.amount_base,
                "transaction_type": row.transaction_type,
                "reference_number": row.reference_number,
                "bank_name": row.bank_name,
                "status": row.status,
                "value_date": row.value_date.isoformat() if row.value_date else None,
            }
            for row in bank_rows
        ],
        "erp_records": [
            {
                "erp_id": row.erp_id,
                "invoice_number": row.invoice_number,
                "customer_vendor_name": row.customer_vendor_name,
                "expected_amount": row.expected_amount,
                "currency": row.currency,
                "amount_base": row.amount_base,
                "ledger_account": row.ledger_account,
                "status": row.status,
                "posting_date": row.posting_date.isoformat() if row.posting_date else None,
            }
            for row in erp_rows
        ],
        "reconciliations": [
            {
                "transaction_id": row.transaction_id,
                "ledger_id": row.ledger_id,
                "ledger_type": row.ledger_type,
                "status": row.status,
                "confidence_tier": row.confidence_tier,
                "match_confidence": row.match_confidence,
                "amount_difference": row.amount_difference,
                "score_breakdown": {
                    "amount": row.score_amount,
                    "name": row.score_name,
                    "reference": row.score_reference,
                    "date": row.score_date,
                },
                "date_difference_days": row.date_difference_days,
                "is_manual": bool(row.is_manual),
                "remarks": row.remarks,
            }
            for row in results
        ],
        "anomalies": [
            {
                "transaction_id": row.transaction_id,
                "type": row.anomaly_type,
                "severity": row.severity,
                "risk_score": row.risk_score,
                "status": row.status,
                "detector": row.detector,
                "explanation": row.explanation,
            }
            for row in anomalies
        ],
    }
