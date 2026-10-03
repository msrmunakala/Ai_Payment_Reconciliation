# AI Payment Reconciliation Engine on Mojaloop

An explainable, AI-powered reconciliation, anomaly detection and cash-flow forecasting engine for Mojaloop hub transfers, bank settlement statements and ERP invoices.

Stack: FastAPI + SQLAlchemy on **PostgreSQL**, Alembic migrations, Prophet forecasting, and the **Mojaloop Testing Toolkit** as the source of transaction data.

## Features

- **Mojaloop integration** — transfers arrive by **push** on a signed FSPIOP webhook; **settlement windows are pulled** from the central settlement API and used to scope reconciliation. Single transfers can be verified against the hub individually.
- **Candidate-generation matching engine** — blocking on exact reference, a binary-searched amount window and a fuzzy-name fallback, then an explainable 0–100 score over amount, counterparty name, reference and date, resolved with a deterministic global assignment. Measured ~17–18x fewer pair comparisons than a cross product.
- **Anomaly detection** — deterministic rules for duplicate charges, amount mismatches, missing settlements, timing anomalies, currency mismatches and unrecognised accounts, plus an Isolation Forest for statistical outliers. Risk scores are derived from evidence, not hardcoded.
- **Cash-flow forecasting** — Prophet fitted on real FX-normalised daily aggregates, with Ridge and seasonal-naive fallbacks. Deterministic, cached, and every point records the model that produced it.
- **Multi-currency FX** — database-backed rate table with validity windows, so a historical reconciliation can be reproduced against the rate that applied at the time.
- **Settlement-window-scoped runs** — reconcile "the window that closed at 02:00" rather than the whole book. A scoped run only replaces results for transactions inside its window.
- **Automated scheduling** — APScheduler job that pulls settlement windows and reconciles any that have newly closed, with a run frequency changeable at runtime.
- **Treasury dashboard** — Vite + React UI with SQL-aggregated metrics, FX exposure, exception mix, ledger coverage, run history, and CSV/JSON export.

## Quick start

### 1. PostgreSQL

```sql
CREATE ROLE recon_app LOGIN PASSWORD 'choose-a-password';
CREATE DATABASE reconciliation OWNER recon_app ENCODING 'UTF8';
\c reconciliation
GRANT ALL ON SCHEMA public TO recon_app;
ALTER SCHEMA public OWNER TO recon_app;
```

### 2. Backend

```bash
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1      # PowerShell;  source venv/bin/activate on Unix
pip install -r requirements.txt

cp .env.example .env             # then set DATABASE_URL
alembic upgrade head             # create the schema
uvicorn app.main:app --reload
```

Interactive API docs at `http://localhost:8000/docs`.

### 3. Mojaloop Testing Toolkit

```bash
cd ml-testing-toolkit
docker compose up -d
```

| Service | URL |
| --- | --- |
| Toolkit UI | http://localhost:6060 |
| Toolkit API (test runner) | http://localhost:5050 |
| Mocked Mojaloop API surface | http://localhost:4040 |

`docker-compose.override.yml` pulls the published `mojaloop/ml-testing-toolkit:v18.19.2` image instead of building from source (the upstream file builds the whole Node project), and adds `host.docker.internal` so toolkit callbacks can reach the backend on the host.

### 4. Drive the backend from the toolkit

The collection in `ml-testing-toolkit/spec_files/collections/reconciliation/` POSTs transfers into the backend's webhook. It replaces the Python synthetic transaction generator: transaction data now originates from Mojaloop.

Open the UI at `:6060`, load the collection, and run it — or submit it directly:

```bash
curl -X POST http://localhost:5050/api/outbound/template/$(uuidgen) \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{ "name": "...", "test_cases": [ ... ], "inputValues": { ... } }
JSON
```

The cases are chosen to exercise specific outcomes: a clean match, a fuzzy counterparty variant, an amount mismatch, a duplicate charge, a missing settlement, an idempotent replay, and a RESERVED-state transfer.

### 5. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL (default `http://localhost:5173`).

## How the Mojaloop integration actually works

This matters, because two plausible-sounding designs are not possible against the real APIs.

```
Toolkit collection ──POST /transfers──▶ /webhooks/transfers   (push, signed)
                                               │
                                               ▼
                                     incremental reconciliation
                                     of just that transfer

Backend ──GET /settlementWindows──▶ central settlement API    (pull)
        ──GET /settlements/{id}──▶
        ──GET /transfers/{id}────▶ single-transfer verification
```

**There is no bulk transfer pull.** FSPIOP defines only `GET /transfers/{ID}`; there is no collection endpoint. Transfers therefore reach the system by push, and the client offers `fetch_transfer(id)` for verifying one transfer rather than a `fetch_transfers()` list.

**The settlement API is narrower than it looks.** Both `settlements_1.0` and `settlements_2.0` define:

| Operation | Method | Supported |
| --- | --- | --- |
| `/settlementWindows` | GET | yes — list windows |
| `/settlementWindows/{id}` | POST | close a window (no GET exists) |
| `/settlements` | POST | create a settlement (no GET list exists) |
| `/settlements/{id}` | GET | yes — one settlement |

So settlements are fetched **by id**, and a single window is read from the list. `POST /settlement/settlements/sync` requires explicit ids for this reason.

**The settlement API is plain JSON REST, not FSPIOP.** Sending it `Accept: application/vnd.interoperability.settlements+json` makes content negotiation fail and the request 404s. Only the transfer API gets FSPIOP vendor headers.

## Configuration

Everything is environment-driven via `backend/.env`; see `backend/.env.example` and `backend/app/core/config.py`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | — | `postgresql+psycopg://…`. SQLite still works and is what the unit tests use. |
| `API_KEY` | `review2-demo-key` | Shared API key. The app refuses to start in production while this is the default. |
| `CORS_ORIGINS` | localhost:5173 | Comma-separated allow-list. |
| `WEBHOOK_SECRET` | *(unset)* | Enables HMAC-SHA256 webhook verification. **Leave unset while the toolkit drives the backend** — the toolkit does not sign its callbacks. |
| `MOJALOOP_ENABLED` | `false` | Turn on settlement-window pulling and transfer verification. |
| `MOJALOOP_BASE_URL` | `http://localhost:4040` | Transfer API base. |
| `MOJALOOP_SETTLEMENT_BASE_URL` | *(falls back to base)* | Settlement API base. |
| `MOJALOOP_SETTLEMENT_API_PREFIX` | `""` | `""` = settlements 1.0 (root), `v2` = settlements 2.0. |
| `MOJALOOP_SETTLEMENT_WINDOW_HOURS` | `24` | Nominal window length, used when the hub reports a zero-width window. |
| `SCHEDULER_ENABLED` | `false` | Start the automated reconciliation job at boot. |
| `SCHEDULER_SYNC_MOJALOOP` | `false` | Pull settlement windows before each cycle. |
| `MATCH_THRESHOLD_MATCHED` | `90` | Score at or above which a pair is `matched`. |
| `MATCH_REQUIRE_CORROBORATION` | `true` | Refuse to auto-match on amount agreement alone. |
| `MATCH_ERP_DATE_TOLERANCE_DAYS` | `10` | Wider date tolerance for ERP invoices, which legitimately precede payment. |
| `FORECAST_PREFER_PROPHET` | `true` | Use Prophet when there is enough history. |
| `AUTO_SCHEMA_SYNC` | SQLite only | Additive column/table reconciliation at startup. Alembic owns the schema on PostgreSQL. |

## Schema management

**Alembic owns the PostgreSQL schema.**

```bash
cd backend
alembic upgrade head                            # apply
alembic revision --autogenerate -m "describe"   # after changing a model
alembic current
```

`alembic/env.py` reads the URL from application settings, so no credential is duplicated into a tracked file.

`app/database/schema_sync.py` is a narrow, additive-only fallback for **SQLite** development databases that predate a model change. It adds missing nullable columns, tables and indexes, never drops or retypes anything, and reports what it refused to do.

## Tests

```bash
cd backend
python -m unittest tests.test_reconciliation -v   # 29 unit/integration tests (SQLite)
python test_api.py                                # 111 end-to-end API assertions

# run the same e2e suite against PostgreSQL
$env:E2E_DATABASE_URL="postgresql+psycopg://recon_app:...@127.0.0.1:5432/reconciliation_test"
python test_api.py
```

Both suites use throwaway databases and never touch the working database.

## Project layout

```
backend/
  alembic/           migrations (initial schema, settlement windows + run scoping)
  app/
    core/            configuration and shared status constants
    api/             FastAPI routers
    services/        matching_engine, anomaly_service, reconciliation_service,
                     forecasting_service, fx_service, ingestion_service,
                     mojaloop_service, settlement_service, scheduler_service,
                     dashboard_service
    models/          SQLAlchemy models
    schemas/         Pydantic request/response models
    database/        engine, session scope, schema bootstrap and sync
ml-testing-toolkit/
  docker-compose.override.yml          published image + host networking
  spec_files/collections/reconciliation/  the collection that feeds the backend
  spec_files/rules_response/reconciliation.json  settlement API mock responses
frontend/src/        Vite + React dashboard
```

## Known limitations

- **Money is stored as `FLOAT`**, by project decision. `NUMERIC(20,4)` is the correct type for financial amounts: amount comparisons against a tolerance are therefore approximate at the cent boundary. Changing this is a schema migration plus a move to `Decimal` in the matching path.
- **`GET /transfers/{id}` cannot be served synchronously by the Testing Toolkit.** On a real hub the operation is asynchronous (`202 Accepted`, then a `PUT /transfers/{ID}` callback); the toolkit's FSPIOP mock rejects the lookup without additional callback wiring. `verify_transfer` handles the 202 case, the 404 case and the error case explicitly and reports what happened rather than guessing.
- There are **no database-level foreign keys** between transactions and their reconciliation artefacts; referential cleanup is done in application code.
- The scheduler and the rate limiter are **per-process**. Multiple API replicas need a shared broker and a shared counter.
- Settlement window timestamps from the mock are generated at request time, so windows share a close time; the nominal-window fallback (`MOJALOOP_SETTLEMENT_WINDOW_HOURS`) gives them a usable scope.
- ERP records in the demo dataset are synthetic. The dashboard says so.
