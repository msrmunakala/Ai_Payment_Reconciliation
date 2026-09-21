# Review 2 - AI-Powered Intelligent Payment Reconciliation

## Demonstrable implementation

The prototype uses a four-layer design: Mojaloop/synthetic-ERP records, normalized SQLite storage, an explainable reconciliation engine, and a React dashboard over FastAPI. It runs locally without requiring a PostgreSQL server; configure `DATABASE_URL` to migrate to PostgreSQL.

The current review build seeds a small synthetic data set with matched, partial, unmatched, duplicate, and amount-difference cases. Re-running a reconciliation clears stale results and is therefore idempotent. The SRS's 106-record ground-truth validation set remains the next data-expansion task before a final accuracy claim is made.

## Review 2 coverage

| Criterion | Evidence |
| --- | --- |
| Design quality | Separate data models, service layer, secured API, and React dashboard. |
| Coding progress | Ingestion-ready normalized models, reconciliation, anomaly explanations, forecast, dashboard, and CSV export. |
| Innovation | Confidence scoring combines amount, reference, and date similarity; exceptions have explainable risk scores. |
| Testing and debugging | `backend/tests/test_reconciliation.py` verifies demo seeding, reconciliation persistence, and anomaly creation. |

## Run locally

1. Install Python 3.11+ and run `python -m pip install -r backend/requirements.txt`.
2. From `backend`, run `uvicorn app.main:app --reload`.
3. From `frontend`, run `npm install` then `npm run dev`.
4. Open the Vite URL. The demo API key is `review2-demo-key`; replace it with `API_KEY` before sharing outside the review.

## API evidence

Protected endpoints require `X-API-Key`: `/dashboard/summary`, `/reconciliation/results`, `/anomalies`, `/forecast`, `/reports/export.csv`, and `POST /reconciliation/run`. `/health` is intentionally public for deployment health checks.
