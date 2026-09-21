import csv
import io
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.database.db import Base, engine, get_db
from app.models.reconciliation import Anomaly, CashFlowForecast, Reconciliation
from app.models.transaction import Transaction
from app.models.bank_transaction import BankTransaction, ERPRecord
from app.services.reconciliation_service import run_reconciliation, seed_demo_data
from app.services.forecasting_service import generate_forecast

from app.api.mojaloop import router as mojaloop_router
from app.api.transactions import router as transactions_router
from app.api.reconciliation import router as reconciliation_router
from app.api.dashboard import router as dashboard_router
from app.api.forecast import router as forecast_router

API_KEY = os.getenv("API_KEY", "review2-demo-key")


def require_api_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key is not None and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="A valid X-API-Key is required.")
    return x_api_key


from app.database.init_db import init_db

# Initialize database tables on module load
init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = next(get_db())
    try:
        if not db.query(Transaction).first():
            seed_demo_data(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="AI Payment Reconciliation Engine - Mojaloop (Review-2)",
    version="2.0.0",
    lifespan=lifespan,
    description="AI-Powered Payment Reconciliation, Anomaly Detection, Cash Flow Forecasting & Treasury Dashboard."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def protect_financial_endpoints(request: Request, call_next):
    """Keep health checks and API documentation public, but protect all financial data routes."""
    public_paths = {"/", "/health", "/docs", "/openapi.json", "/redoc"}
    if request.method != "OPTIONS" and request.url.path not in public_paths:
        if request.headers.get("X-API-Key") != API_KEY:
            return JSONResponse(status_code=401, content={"detail": "A valid X-API-Key is required."})
    return await call_next(request)

# Register Sub-Routers
app.include_router(mojaloop_router)
app.include_router(transactions_router)
app.include_router(reconciliation_router)
app.include_router(dashboard_router)
app.include_router(forecast_router)


@app.get("/")
def home():
    return {
        "project": "AI Payment Reconciliation on Mojaloop",
        "category": "AI-on-DPI (Bracket 1)",
        "mode": "synthetic-demo",
        "api_docs": "/docs",
        "review": "Review-2"
    }


@app.get("/health")
def health():
    return {"status": "healthy", "engine": "running"}


@app.post("/demo/reset", dependencies=[Depends(require_api_key)])
def reset_demo(count: int = Query(50, ge=10, le=500), db: Session = Depends(get_db)):
    return seed_demo_data(db, count=count)


@app.get("/demo/export-json", dependencies=[Depends(require_api_key)])
def export_generated_data_json(db: Session = Depends(get_db)):
    txs = db.query(Transaction).all()
    bank_txs = db.query(BankTransaction).all()
    erp_recs = db.query(ERPRecord).all()
    reconciliations = db.query(Reconciliation).all()
    anomalies = db.query(Anomaly).all()

    payload = {
        "metadata": {
            "title": "AI Payment Reconciliation Generated Dataset",
            "total_transactions": len(txs),
            "total_bank_records": len(bank_txs),
            "total_erp_records": len(erp_recs),
            "generated_at": os.popen("date /t").read().strip() if os.name == "nt" else ""
        },
        "transactions": [
            {
                "transaction_id": t.transaction_id,
                "reference_id": t.reference_id,
                "payer": t.payer,
                "payee": t.payee,
                "payer_fsp": t.payer_fsp,
                "payee_fsp": t.payee_fsp,
                "amount": t.amount,
                "currency": t.currency,
                "status": t.status,
                "transfer_state": t.transfer_state,
                "created_at": str(t.created_at) if t.created_at else None
            }
            for t in txs
        ],
        "bank_transactions": [
            {
                "bank_statement_id": b.bank_statement_id,
                "account_number": b.account_number,
                "counterparty": b.counterparty,
                "amount": b.amount,
                "currency": b.currency,
                "transaction_type": b.transaction_type,
                "reference_number": b.reference_number,
                "bank_name": b.bank_name
            }
            for b in bank_txs
        ],
        "erp_records": [
            {
                "erp_id": e.erp_id,
                "invoice_number": e.invoice_number,
                "customer_vendor_name": e.customer_vendor_name,
                "expected_amount": e.expected_amount,
                "currency": e.currency,
                "ledger_account": e.ledger_account
            }
            for e in erp_recs
        ],
        "reconciliations": [
            {
                "transaction_id": r.transaction_id,
                "ledger_id": r.ledger_id,
                "ledger_type": r.ledger_type,
                "status": r.status,
                "amount_difference": r.amount_difference,
                "match_confidence": r.match_confidence,
                "confidence_tier": r.confidence_tier,
                "remarks": r.remarks
            }
            for r in reconciliations
        ],
        "anomalies": [
            {
                "transaction_id": a.transaction_id,
                "anomaly_type": a.anomaly_type,
                "severity": a.severity,
                "risk_score": a.risk_score,
                "explanation": a.explanation
            }
            for a in anomalies
        ]
    }

    output = io.StringIO()
    import json
    json.dump(payload, output, indent=2)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=generated-dataset.json"}
    )


@app.get("/demo/generator-script", dependencies=[Depends(require_api_key)])
def get_generator_script():
    script_content = '''# Synthetic Data Generator Script (`seed_demo_data`)
# File Location: backend/app/services/reconciliation_service.py

def seed_demo_data(db: Session, count: int = 150):
    """
    Generates synthetic Mojaloop payment transfers, Bank statements, and ERP invoices.
    """
    # 1. Clear existing database tables
    db.query(Reconciliation).delete()
    db.query(Anomaly).delete()
    db.query(CashFlowForecast).delete()
    db.query(Transaction).delete()
    db.query(BankTransaction).delete()
    db.query(ERPRecord).delete()
    db.commit()

    init_fx_rates(db)

    # 2. Base Corporate Entity Pools & Currencies
    payers = ["Acme Corporation", "Apex Retailers", "Nairobi Enterprises", "Euro Import Co", ...]
    payees = ["Global Logistics Ltd", "TechSupply Pvt Ltd", "Safari Ventures", ...]
    fsp_list = ["BankA", "BankB", "MobileMoneyX", "PayCentral", "DFSP-Alpha"]
    currencies = ["USD", "INR", "EUR", "KES"]

    # 3. Dynamic record generation (Mojaloop Transfers, Central Bank Statements, ERP Invoices)
    for i in range(1, count + 1):
        amt = round(random.uniform(2000.0, 85000.0), 2)
        # Add Mojaloop transaction
        db.add(Transaction(transaction_id=f"TX-{1000+i}", amount=amt, ...))
        # Add Bank statement (90% exact match, 10% amount variation)
        db.add(BankTransaction(bank_statement_id=f"BS-{9000+i}", amount=amt if random.random() < 0.9 else round(amt*0.98, 2), ...))
        # Add ERP invoice record
        db.add(ERPRecord(erp_id=f"ERP-{5000+i}", expected_amount=amt, ...))

    db.commit()
    run_reconciliation(db)
    return {"message": f"Demo dataset of {count} transactions successfully seeded and reconciled."}
'''
    return {"filename": "backend/app/services/reconciliation_service.py", "function": "seed_demo_data", "code": script_content}


@app.get("/anomalies", dependencies=[Depends(require_api_key)])
def get_anomalies(db: Session = Depends(get_db)):
    order = {"high": 0, "medium": 1, "low": 2}
    rows = db.query(Anomaly).all()
    return sorted(
        [
            {
                "transaction_id": x.transaction_id,
                "type": x.anomaly_type,
                "severity": x.severity,
                "risk_score": x.risk_score,
                "explanation": x.explanation,
                "status": x.status,
                "detected_at": x.detected_at.strftime("%Y-%m-%d %H:%M:%S") if x.detected_at else None
            }
            for x in rows
        ],
        key=lambda x: (order.get(x["severity"], 3), -x["risk_score"])
    )


@app.get("/reports/export.csv", dependencies=[Depends(require_api_key)])
def export_report(status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Reconciliation)
    if status:
        query = query.filter(Reconciliation.status == status)
    rows = query.order_by(Reconciliation.id).all()

    data = [
        {
            "transaction_id": x.transaction_id,
            "ledger_id": x.ledger_id or "",
            "ledger_type": x.ledger_type,
            "status": x.status,
            "amount_difference": x.amount_difference,
            "match_confidence": x.match_confidence,
            "confidence_tier": x.confidence_tier,
            "remarks": x.remarks or ""
        }
        for x in rows
    ]

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["transaction_id", "ledger_id", "ledger_type", "status", "amount_difference", "match_confidence", "confidence_tier", "remarks"]
    )
    writer.writeheader()
    writer.writerows(data)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reconciliation-report.csv"}
    )

