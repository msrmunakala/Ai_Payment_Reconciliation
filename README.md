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
---

# Changelog — what was changed and why

Everything below was done across two work sessions: first completing the half-built features, then migrating to PostgreSQL and attaching the Testing Toolkit. Git commit: `60b66f3` on branch `Production`.

**Verification across the whole changelog:** 29 unit/integration tests and 111 end-to-end API assertions pass on **both** SQLite and PostgreSQL; 56 endpoints; the frontend builds clean; the toolkit collection was confirmed driving the backend end to end.

## 1. Defects fixed

These were real bugs, not style changes. Each is listed with its symptom.

| # | Defect | Effect before the fix |
| --- | --- | --- |
| 1 | **Match scoring could never reach `matched`.** `confidence = name*0.4 + amount*0.4 + 40 if reference matched` capped a no-reference pair at **80** against a **90** threshold. Weights did not sum to 100. | A perfect match on amount, counterparty and date was classified `partial` unless the reference matched exactly. The headline match rate was structurally understated. |
| 2 | **Amount agreement alone was accepted as a match.** No corroboration was required. | Two unrelated payments that happened to share a value scored ~72 and were auto-matched. This is the most dangerous false positive in reconciliation. |
| 3 | **`timing_anomaly` and `unrecognized_account` were never produced**, despite being declared in the model's own comment. There was no date comparison in the engine at all. | Settlement lag and unknown counterparties were undetectable. |
| 4 | **Reconciliation deleted the whole `reconciliations` and `anomalies` tables** and committed the deletion *before* writing replacements. | A crash mid-run left the system with **no results at all**. Resolved anomalies were silently re-raised. Manual matches were destroyed by the next run. |
| 5 | **`convert_to_usd(amount, currency, db)` ignored its `db` argument.** The `fx_rates` table was written once during seeding and never read. | Rates could only be changed by editing a dict in source. An unrecognised currency was returned unconverted, so 1,000,000 UGX was treated as 1,000,000 USD. |
| 6 | **Mixed currencies were summed without conversion** in the dashboard total and in the forecast input. | INR and USD were added together to produce the headline "reconciled value". |
| 7 | **The forecast was fitted to invented data.** 30 days of `22000 + 4000*sin(2πi/7) + np.random.normal(0,1500)` were generated and real amounts merely added on top. Fresh noise was drawn every call. | Two identical requests returned different forecasts. Nothing was reproducible. The brief specifies Prophet; a single-feature Ridge on a day index was used. |
| 8 | **`GET /forecast` deleted the whole forecast table and retrained on every call.** | A read request was destructive, non-idempotent, and slow. |
| 9 | **Every webhook triggered a full re-reconciliation of the entire database**, synchronously, on the event loop. | One inbound transfer re-matched every record and retrained the forecast model. |
| 10 | **`require_api_key` accepted a missing header** (`if x_api_key is not None and x_api_key != API_KEY`) and compared keys with `!=`. | The dependency was decorative — only the middleware actually blocked. Key comparison was not constant-time. |
| 11 | **`allow_origins=["*"]` with `allow_credentials=True`.** | Browsers reject this combination outright; it was also an open CORS policy. |
| 12 | **Webhooks had no signature verification and no idempotency key.** | Anyone who could reach the endpoint could inject transactions. |
| 13 | **No `db.rollback()` anywhere in the backend.** | A malformed cell mid-ingest left the session dirty and lost the whole batch with no report of what failed. |
| 14 | **Ingestion issued one `SELECT` per input row** and could not see duplicates within the same file. A missing ID column minted a fresh UUID. | N+1 on upload; re-uploading the same file silently doubled the data. |
| 15 | **`pandas` and `openpyxl` were imported but absent from `requirements.txt`.** | A clean `pip install -r requirements.txt` produced an app whose transactions router could not import. |
| 16 | **The only existing test failed.** `tests/test_reconciliation.py` asserted `count() == 50` while `seed_demo_data` defaulted to 150. | `150 != 50` on a clean checkout. |
| 17 | **`os.popen("date /t")` ran inside a request handler** to get a timestamp. | Shelling out per request. |
| 18 | **Ledger `status` columns never changed from their defaults.** | No way to tell which bank or ERP lines were still open. |
| 19 | **Results depended on table row order.** Matches were assigned first-come inside the loop. | Re-importing the same data in a different order produced different results. |
| 20 | **`GET /transactions/{id}` relied on an operator-precedence accident** — `... if cond else False` bound over the whole right operand of `\|`. | The numeric-id branch evaluated to `\| False`. |

### Defects found during this work and fixed

| # | Defect | How it surfaced |
| --- | --- | --- |
| 21 | **Webhook signature verification ran *inside* the handler**, so Pydantic rejected a malformed body with 422 before the signature was ever checked. | The e2e suite expected 401 and got 422. Moved to a route-level dependency. |
| 22 | **My own inflow/outflow split broke `net = inflow − outflow`** for negative predictions. | A test assertion caught it. Fixed by solving gross and net simultaneously rather than scaling each leg independently. |
| 23 | **ERP invoice dates triggered false `timing_anomaly`** against the 2-day bank tolerance. | 16 spurious anomalies in a 150-row dataset. ERP now has its own wider window — invoices legitimately precede payment. |
| 24 | **The settlement API was called with FSPIOP vendor `Accept` headers.** | Every settlement call returned 404 while direct `curl` worked. The settlement API is plain JSON REST; only the transfer API is FSPIOP. |
| 25 | **FSPIOP `GET` requests carried `Content-Type`.** | FSPIOP validation rejects a `Content-Type` on a request with no body. |
| 26 | **The hub health probe hit `/health`**, which the toolkit does not expose. | A reachable hub was reported as unreachable. Now probes `GET /settlementWindows`. |
| 27 | **Zero-width settlement windows scoped a run to nothing.** | Every window reported 0 transactions. A nominal window length is now used when the hub reports identical open/close timestamps. |
| 28 | **SQLite WAL sidecars (`*.db-wal`, `*.db-shm`) were not gitignored.** | They were staged for commit. `.gitignore` extended. |

## 2. Architecture changes

### Matching engine — extracted and rewritten
`app/services/matching_engine.py` **(new)**

The original compared every transaction against every bank row and then every ERP row, with 1–2 fuzzy string calls per pair — O(n×m) Python work, 100 million comparisons at 10k×10k.

Replaced with **candidate generation**:

```
transaction ──┬── exact reference        → dict lookup
              ├── amount window ±tol     → binary search over a sorted array
              └── fuzzy name (fallback)  → rapidfuzz batch scan, C-level
                       ↓
                 score the survivors
                       ↓
          global assignment (sorted edges, deterministic)
                       ↓
          matched / partial / flag_for_review / unmatched
```

- Scoring is an explainable 0–100 breakdown over amount, counterparty name, reference and date, with **applicable-weight renormalisation**: if neither side carries a reference, that weight is removed from the denominator rather than scored zero. A pair is never punished for data that does not exist on either record.
- Both ledgers are normalised into one `LedgerCandidate` shape, collapsing the two near-duplicate loops into one scoring path.
- Assignment sorts all scored edges and assigns greedily, so results are independent of row order.
- `MatchingStats` reports the reduction factor, making the improvement measurable rather than claimed. **Observed: 17–18x fewer pair comparisons.**

### Anomaly detection — separated from matching
`app/services/anomaly_service.py` **(new)**

Split along the line where the problem actually divides:

- **Rule-based** for every case where "wrong" is known in advance: duplicate charge (now time-windowed, plus reference reuse), amount mismatch, missing settlement, timing anomaly, currency mismatch, unrecognised account, weak candidate. Auditable and cannot drift.
- **Isolation Forest** for the residual: transactions that break no rule but do not resemble the population. Runs only with enough samples, rather than producing confident noise.
- **Risk scores are computed from evidence** (relative amount, distance outside tolerance, population percentile) instead of the previous hardcoded `0.92` / `0.55` / `0.88`, so ranking an exception queue actually orders the work.

### Reconciliation orchestration
`app/services/reconciliation_service.py` **(rewritten)**

- New `ReconciliationRun` record per execution: parameters, counts, duration, scope, outcome. Runs are comparable and auditable.
- Resolved and ignored anomalies **survive** a re-run.
- Manual matches moved to their own `manual_matches` table and re-applied after every run, so a re-run cannot discard human decisions.
- `reconcile_transaction()` matches a single transfer using a SQL amount window — a webhook no longer re-reconciles the database.
- Ledger statuses are updated when a row is consumed.
- Base-currency amounts are persisted once per run instead of FX being recomputed inside the loops.
- `try/except` with `rollback` and a failed-run record.

### Settlement-window scoping
`app/models/settlement.py`, `app/services/settlement_service.py`, `app/api/settlement.py` **(new)**

- `SettlementWindow` and `Settlement` models, populated from the hub.
- `run_reconciliation` accepts `scope` / `settlement_window_id` / `scope_from` / `scope_to`.
- **A scoped run deletes only the rows belonging to transactions inside its window.** Verified: two consecutive scoped runs left all 304 rows intact. A full-table delete here would mean reconciling one window destroyed another's results.
- `_update_ledger_statuses(reset_all=False)` for scoped runs, so one window does not reopen another's matched ledger lines.
- The scheduler now reconciles **newly closed windows** in preference to a full run.

### Mojaloop client — corrected to the real APIs
`app/services/mojaloop_service.py` **(rewritten)**

Previously 32 lines returning two hardcoded dictionaries with random UUIDs, performing no network I/O and persisting nothing. There was no HTTP client anywhere in the backend.

- Real `httpx` client with FSPIOP headers, bounded retries with exponential backoff on 5xx/429/timeout (but not 4xx), and configurable timeouts.
- **Removed `GET /transfers?limit=N`** — not a real Mojaloop endpoint. It only ever worked against a stub.
- Added `fetch_transfer(id)` and `verify_transfer()`, which compares our stored copy against the hub's and **reports disagreement rather than silently correcting it** — a divergence is itself a finding.
- Tolerant payload normalisation: FSPIOP party structures, `personalInfo.complexName`, nested amount objects, and several collection wrappers.
- Idempotent persistence keyed on `transactionId`.
- The offline sample uses deterministic `uuid5` ids instead of a fresh `uuid4` per call, and is always labelled `simulated: true`.

### FX — completed
`app/services/fx_service.py` **(rewritten)**

- `FXConverter` snapshot loaded **once per run** instead of converting inside nested loops.
- Database-backed rates with `valid_from` / `valid_to` / `source`, so a historical run can be reproduced against the rate that applied then (`as_of` lookup).
- Cross-currency conversion via the base currency as a pivot; Decimal arithmetic internally.
- Unknown currencies are reported through `unknown_currencies`, with a strict mode available.
- `/fx` endpoints: list rates (with history), convert, refresh, override a single rate.

### Forecasting — completed
`app/services/forecasting_service.py` **(rewritten)**

- Aggregates **real** FX-normalised daily cash movement from the ledgers, using bank credit/debit direction for inflow and outflow.
- **Prophet** when there is enough history, falling back to Ridge with weekday seasonality, then a per-weekday mean — and it reports honestly which one ran.
- Deterministic; cached with a TTL so a GET is no longer destructive. `POST /forecast/run` retrains explicitly.
- Inflow/outflow derived from observed averages, replacing the hardcoded `×1.25` / `×0.25`.
- Each stored point records its model, version, currency, history length and whether any synthetic padding was involved.
- A domain constraint floors negative predictions at zero when the observed series never went negative.

### Ingestion — completed
`app/services/ingestion_service.py` **(rewritten)**

- One **bulk existence query** replaces the per-row SELECT.
- De-duplicates within a file; derives a **content-hash natural key** when no ID column is present, so re-uploading the same file is idempotent.
- Collects per-row errors instead of aborting the batch; rolls back cleanly on insert failure.
- Tolerant parsing: thousands separators, currency symbols, parenthesised negatives, seven date formats, case-insensitive column aliases.
- File size and row limits, extension validation, and amounts normalised at ingest.

### Scheduling — implemented from nothing
`app/services/scheduler_service.py`, `app/api/scheduler.py` **(new)**

The brief lists "automated reconciliation scheduling with configurable run frequencies". No scheduler, background job or cron integration existed.

APScheduler job with start / stop / pause / resume / change-interval at runtime, overlap protection, and a stated limitation: it is single-process, so multiple API replicas would need a shared broker.

### Dashboard — completed
`app/services/dashboard_service.py` **(rewritten)**

Replaced full-table Python loops with SQL `GROUP BY` / `SUM`. Added FX-normalised totals, per-currency exposure, exception mix, ledger coverage, confidence distribution, value at risk, and run provenance.

### Security hardening
`app/main.py` **(rewritten)**

- `hmac.compare_digest` for the API key; refuses to start in production on the demo key.
- Explicit CORS allow-list from configuration.
- HMAC-SHA256 webhook verification as a **route-level dependency** so 401 precedes body validation.
- Fixed-window rate limit on write endpoints.
- Correlation ids, and unhandled exceptions return a scrubbed body instead of a stack trace.
- `init_db()` no longer runs at import time.

## 3. PostgreSQL migration

- **PostgreSQL 18** as the primary database via `psycopg[binary]`, with pooling, `pool_pre_ping` and `pool_recycle` for server databases. SQLite retained for the fast unit suite.
- **Alembic** owns the schema. `alembic/env.py` reads the URL from application settings so no credential is duplicated into a tracked file. Two revisions: `596a0b2597c1` (baseline) and `77a9a48e8b3e` (settlement windows + run scoping).
- Configuration centralised in `app/core/config.py`, loaded from a gitignored `backend/.env`; `backend/.env.example` is tracked as the template.
- `app/database/schema_sync.py` **(new)** — an additive-only reconciler for SQLite development databases that predate a model change. It adds missing nullable columns, tables and indexes, **never** drops or retypes anything, and reports what it refused to do. Defaults to SQLite only so Alembic solely owns the PostgreSQL schema.
- SQLite connections get WAL journalling, enforced foreign keys and a busy timeout.
- `get_db` rolls back on exception; new `session_scope` for non-request callers.
- Verified: the existing SQLite database was migrated in place (48 columns, 40 indexes, 2 tables added, 0 errors) with all 150 transactions and 445 ledger rows preserved; `amount_base` is backfilled at startup so dashboard aggregates are correct immediately after an upgrade.

## 4. Testing Toolkit attached

Transaction data now originates from Mojaloop rather than the Python generator.

- `ml-testing-toolkit/docker-compose.override.yml` **(new)** — uses the published `v18.19.2` image instead of compiling the Node project, and adds `host.docker.internal` so toolkit callbacks reach the backend on the host.
- `spec_files/collections/reconciliation/01_transfers_to_backend.json` **(new)** — 8 cases chosen to exercise specific reconciliation outcomes: clean match, fuzzy counterparty variant, amount mismatch, duplicate charge, missing settlement, idempotent replay, RESERVED-state transfer, and a dashboard assertion.
- `spec_files/collections/reconciliation/environment.json` **(new)** — backend URL and API key inputs.
- `spec_files/rules_response/reconciliation.json` **(new)** — mocks `GET /settlementWindows` and `GET /settlements/{id}` with **current-dated** windows via the toolkit's `{$function.generic.curDateISO}`.

**Confirmed working, not assumed:** `TTK-TX-0003` and `TTK-TX-0004` are in the database with `source=mojaloop`. Those two were never pushed by hand — the collection POSTed them, visible in the toolkit's logs as outbound calls to `/webhooks/transfers`.

## 5. Tests

- `backend/tests/test_reconciliation.py` **(rewritten)** — from 1 failing test to **29** covering FX conversion and cross-rates, name/reference normalisation, the scoring-weight invariant, run auditability, candidate-generation efficiency, determinism, resolved-anomaly survival, manual-match survival, ingestion dedup and error isolation, HMAC round-trip, FSPIOP payload mapping, and forecast determinism.
- `backend/test_api.py` **(rewritten)** — from a 9-step script to **111 assertions** across 14 sections, including auth enforcement, pagination, filter validation, settlement-window scoping, and the out-of-scope-preservation invariant. Runs against SQLite or PostgreSQL via `E2E_DATABASE_URL`.

## 6. Frontend

`frontend/src/main.jsx`

- Handles the paginated `/reconciliation/results` envelope (accepts either shape).
- **Currency formatter now follows the backend's base currency.** It was hardcoded to format USD figures with a `₹` symbol.
- Anomaly risk displayed correctly — scores are 0–1 and were being rendered as `0.92/100`.
- Model name, currency and synthetic flag shown from forecast data instead of a hardcoded "Ridge Regression" label.
- Removed a hardcoded "Weekend volume reduction applied (-35%)" caption that described the old implementation's multiplier; Prophet learns weekly seasonality from data.
- `flag_for_review` added to the status filter; API URL and key read from Vite env.

## 7. Removed

| File | Reason |
| --- | --- |
| `backend/app/services/reconciliation.py` | Superseded duplicate of `process_transfer`; nothing imported it. |
| `backend/app/database/base.py` | Unused re-export shim. |
| `backend/app/database/session.py` | Unused re-export shim. |
| `backend/app/webhooks/__init__.py` | Empty placeholder package. |
| `backend/_tmp_check_ingest.py` | Scratch script whose own docstring said it should have been deleted. |
| Embedded seed-script string literal in `main.py` | A stale hand-maintained copy of `seed_demo_data` that had already drifted. `/demo/generator-script` now reads the real source via `inspect.getsource`. |

## 8. Dead-code audit and cleanup

Audited with `ruff` and AST analysis before adding the settlement UI, so new code was not layered on top of unused code. All of the following were zero-risk removals — nothing referenced them.

**Unused imports (11)** — `api/mojaloop.py`, `api/reconciliation.py`, `api/transactions.py`, `services/mojaloop_service.py`, `tests/test_reconciliation.py`, plus one dead local variable.

**`app/utils/helpers.py` was ~85% dead.** Only 2 of its 9 public names were imported anywhere. Removed `CURRENCY_SYMBOLS`, `format_currency`, `utc_now`, `utc_now_iso`, `safe_divide`, `percentage` and `truncate`; kept `verify_hmac_signature` and `compute_hmac_signature`. (`percentage` and `truncate` looked used on a first pass, but those were false positives — the word "percentage" appears in a model *comment*, and `truncate` matched `buffer.truncate(0)` in `main.py`.)

**4 unused Pydantic models:**

| Removed | Why it was dead |
| --- | --- |
| `ForecastResponse` | an envelope that was built, then the endpoint returned a bare list |
| `AnomalyResponse` | the anomaly endpoints return plain dicts |
| `BankTransactionCreate` | no POST endpoint for bank rows — only CSV/XLSX upload |
| `ERPRecordCreate` | same |

**Other removals:** `mojaloop_service.fetch_transactions()` (a backwards-compatibility alias nothing called), `MatchingSettings.amount_bucket_fraction` (left over from a bucketing approach replaced by binary search), `MojaloopSettings.toolkit_api_url` (added, never used), and `scipy` from `requirements.txt` (never imported; `scikit-learn` pulls it in transitively). The obsolete SQLite database files were deleted now that PostgreSQL is primary.

`ruff check --select F` is clean across `app`, `tests`, `test_api.py` and `alembic`.

### Status badge CSS was broken, not just stale

This was a visible defect rather than dead code. `styles.css` styled an **older status vocabulary** the backend no longer emits, and had **no rule for any current status**:

```css
/* before */
.tag.amount_mismatch, .tag.missing_in_erp, .tag.duplicate_erp, .severity.high { ... }
.tag.pending_settlement, .tag.delayed_settlement, .severity.medium { ... }
```

| Value the backend emits | Before | After |
| --- | --- | --- |
| `matched`, `partial`, `unmatched`, `flag_for_review` | no rule — fell through to the grey base `.tag` | styled |
| `severity: low` | no rule | styled |
| 5 classes for states that no longer exist | present | removed |

Every status badge in the results table had been rendering unstyled.

## 9. Settlement windows in the dashboard

Previously the settlement feature was backend-only: fully working through the API and Swagger, but with no screen. That gap is now closed.

### New panel

A **Settlement windows** table showing, per window: id, state, the time interval it covers, how many transactions fall inside it, and whether it has been reconciled (with the run id). Plus:

- **Sync windows** — pulls the latest windows from the hub and reports what changed.
- **Reconcile** (per closed window) — runs reconciliation scoped to that window only.
- **Reconcile all pending** — processes every closed window still outstanding; disabled when there are none.
- **View** — filters the reconciliation results table to that window, with a dismissible chip showing the active scope.
- A **hub reachability indicator** so an empty table is distinguishable from an unreachable hub.
- A **Settlements** sub-table: id, state, model, covered windows and net settled value.

Window and settlement states get their own badge colours tracking `SettlementWindowState`.

### Supporting backend changes

**`GET /reconciliation/results` accepts `settlement_window_id`.** Results are filtered by resolving the window's time interval and matching transaction timestamps — the same mechanism a scoped run uses to select its work. No schema change was needed; an unknown window id returns 404. New helper: `settlement_service.transaction_ids_in_window()`.

**`Settlement.net_amount` and `Settlement.currency` are now populated** rather than dropped. The audit flagged them as never written, and dropping them would have required a migration. Instead `_net_position()` derives them from the hub's `participants[].accounts[].netSettlementAmount`. Two deliberate choices: the **positive** side is summed, because a settlement's net amounts cancel to roughly zero across participants and the useful treasury figure is gross value moved; and a **mixed-currency** settlement leaves both fields unset rather than adding currencies together, which is logged rather than hidden.

### Verified

Hub reachable with 4 windows; `net_amount` populated (152,400 USD, previously always null); a freshly pushed transfer moved a window's count from 0 → 1; scoping that window produced `total=1`; the results filter returned 1 of 307. 29 unit tests and 111 end-to-end assertions pass on both SQLite and PostgreSQL, and the frontend builds clean.

### One behaviour worth knowing

The toolkit mock stamps settlement windows with the **current** time on every request, so a window always covers "the last 24 hours". Seeded demo data that is a day or two old therefore falls outside every window and the panel shows `txns=0`. That is accurate rather than broken — on a real hub, windows and the transfers inside them are contemporaneous. Re-seed, or push a fresh transfer, to see non-zero counts.

## 10. Frontend rebuilt as an operations console

The dashboard was a single 870-line `main.jsx` with one screen, inline styles and a hand-rolled SVG forecast chart. It has been rebuilt as a navigable enterprise application. The backend was not restructured: two small read-only additions were made (below) and everything else consumes existing endpoints.

### Structure

```
src/
  main.jsx                    entry
  App.jsx                     shell, routing, shared summary/health loading
  api.js                      single API client (auth, errors, pagination)
  lib/format.js               currency, date, duration, risk formatting
  lib/useHashRoute.js         ~30-line hash router
  components/
    Sidebar.jsx  TopBar.jsx   application shell
    ui.jsx                    badges, KPI tiles, buttons, filters, pagination, notices
    DataTable.jsx             sticky-header table with expandable rows
    charts.jsx                status breakdown + cash-flow chart
  pages/                      9 pages, one per sidebar section
  styles.css                  light enterprise theme
```

No routing or charting library was added. Hash routing is ~30 lines and keeps the app deployable as static files with no server rewrite rules; the two charts are plain SVG rather than a ~400 kB dependency. React and React DOM remain the only runtime dependencies.

### Pages and the APIs they consume

| Page | Endpoints |
| --- | --- |
| Shell / top bar | `/dashboard/summary`, `/webhooks/status`, `/ready`, `/scheduler/status` |
| Dashboard | the above plus `/settlement/windows`, `/reconciliation/anomalies`, `/dashboard/runs`, `/forecast/`, `/forecast/history`, `/forecast/metadata` |
| Reconciliation | `/reconciliation/results` (status, tier, ledger, window, manual filters, paging), `/reconciliation/manual-match`, `/reports/export.csv` |
| Transactions | `/transactions/`, `/transactions/bank`, `/transactions/erp` |
| Settlement Windows | `/settlement/windows`, `/settlement/sync`, `/settlement/windows/{id}/reconcile`, `/settlement/windows/reconcile-pending`, `/settlement/settlements` |
| Exceptions | `/reconciliation/anomalies`, `/reconciliation/anomalies/{id}/resolve` |
| Forecast | `/forecast/`, `/forecast/history`, `/forecast/metadata`, `/forecast/run` |
| FX Rates | `/fx/rates`, `/fx/convert`, `/fx/refresh`, `/fx/rates/{ccy}` |
| Run History | `/dashboard/runs`, `/reconciliation/run` |
| Settings | `/scheduler/*`, `/webhooks/status`, `/demo/reset`, `/demo/export-json`, `/reports/export.csv` |

Filtering, sorting and pagination are performed **by the backend**. The table never holds more than one page, and the counts shown are the API's totals rather than client-side estimates. Where the backend already aggregates something — status counts, FX exposure, ledger coverage, confidence distribution — that aggregate is displayed directly rather than recomputed.

### Two backend additions

Both are additive, read-only, and were needed to satisfy the design rather than to restructure anything.

- **`GET /forecast/history`** exposes the daily series the forecast is already fitted to (`build_daily_history`), so history and forecast can be plotted on one axis and kept visually distinct. Previously only future points were reachable.
- **Settlement window rows now carry their run's outcome** — matched, partial, flagged, unmatched, exception total, duration and status — read from the recorded `ReconciliationRun` rather than recomputed, so an operations table needs no query per row.

### Design decisions

- **Light neutral theme**, one accent colour for interactive elements. Green, amber and red are used *only* to carry status meaning, never decoration.
- **Tables, not cards.** Exceptions, windows, runs, transactions and FX rates are all tables with sticky headers, right-aligned monetary columns, row hover, clickable IDs and expandable detail rows.
- **Money is formatted per currency.** INR uses the lakh convention (`₹24,82,450.00`), USD and EUR use thousands. The previous build formatted every figure as INR regardless of the backend's base currency, labelling USD values with a rupee symbol.
- **One date format everywhere**: `YYYY-MM-DD HH:MM`.
- **The forecast is presented as a model output**, not a result to be taken on faith: the fitted model, history length and a synthetic-padding warning are all shown, and the forecast line is dashed and separated by a labelled divider.
- **Desktop density preserved.** Below 1024 px the sidebar collapses to an icon rail and panels stack; column counts are not reduced to suit phones.

### What was preserved

Every capability the old dashboard had still works: run reconciliation, view results, review exceptions, see the forecast, export CSV and JSON, view the generator script source, and regenerate the demo dataset. Authentication is unchanged (`X-API-Key`, `VITE_API_KEY`/`VITE_API_URL`). Nothing in the Mojaloop integration, the database models or the reconciliation engine was touched.

Added on top: per-ledger browsing of bank and ERP records, server-side filtering on every table, pagination, settlement-window drill-through, manual-override withdrawal, FX rate editing with history, scheduler control, and the run audit trail.

### Still hardcoded

- The **user identity** in the top bar ("Treasury Ops / Analyst") is a placeholder. The backend has no user model — authentication is a single shared API key — so there is no identity to display. Real users require authentication work on the backend first.
- The **"Regenerate demo dataset" parameters** (600 transactions, 30 days, seed 42) are fixed in the Settings page rather than being form inputs.
- **Sidebar section names** are static, as they describe fixed application areas.

Everything else on screen comes from the API.

### Verified

`vite build` is clean (33 modules, 296 kB / 88 kB gzipped) with no warnings. All 21 source modules transform and serve. All nine pages were server-rendered against the live backend to catch render-time crashes that a successful build would not reveal, and the formatting was spot-checked in the rendered output (`$7.89M` compact USD, per-currency symbols for EUR and INR, 94.8% rate). 29 endpoint calls across the nine pages all return 200. Backend suites remain green: 29 unit tests and 111 end-to-end assertions on both SQLite and PostgreSQL.

### Known issue

The **settlement window transaction counts read low** against the Testing Toolkit. Its mock stamps every window with the current time, so each covers roughly the last 24 hours while seeded demo data is older. The feature is correct — this is mock behaviour, documented in section 9 — but a demo looks better after regenerating the dataset with a short history (`POST /demo/reset?count=600&history_days=30`).
