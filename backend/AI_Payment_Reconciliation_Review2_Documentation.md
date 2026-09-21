# AI Payment Reconciliation Engine on Mojaloop (Review-2 Complete Technical Documentation)

---

## 1. Project Summary & Objectives

- **Project Title**: Intelligent Payment Reconciliation on Mojaloop
- **Category**: AI-on-DPI | Bracket 1
- **Objective**: Automate multi-ledger payment reconciliation, detect anomalies, forecast cash flow liquidity, and present real-time treasury analytics for enterprises operating on Mojaloop-based payment rails.

---

## 2. Technology Stack & Frameworks Used

| Layer | Framework / Library | Version | Purpose |
| :--- | :--- | :--- | :--- |
| **Backend Framework** | **FastAPI** | `0.139.0` | Asynchronous ASGI Web Framework for REST APIs & Webhooks |
| **Server** | **Uvicorn** | `0.51.0` | High-performance ASGI Web Server |
| **Database ORM** | **SQLAlchemy** | `2.0.51` | Object-Relational Mapping & SQLite Database engine |
| **Data Validation** | **Pydantic** | `2.13.4` | Input/Output schema definition & data validation |
| **Fuzzy Matching Model** | **RapidFuzz** | `3.14.6` | C++ accelerated Token-Sort & Partial Ratio string matching |
| **ML Cash Forecasting** | **Scikit-Learn** | `1.9.1` | `Ridge` Time-Series Linear Regression & Seasonality model |
| **Spreadsheet Ingestion**| **Pandas & OpenPyXL**| `3.0.3` / `3.1.5` | Multi-format Excel (`.xlsx`/`.xls`) & CSV file parser |
| **HTTP Testing** | **HTTPX & Requests** | `0.28.1` / `2.34.2` | Verification test client & Webhook simulation |
| **Frontend UI** | **React & Vite** | `18` / `8.3` | Interactive Enterprise Treasury Dashboard UI |

---

## 3. Database Models & Schemas

### 1. `Transaction` (`transactions` table)
Represents Mojaloop payment rail transfers.
- `transaction_id`: Unique transfer identifier (e.g. `TX-1001`)
- `reference_id`: Cross-ledger reference code (e.g. `REF-8801`)
- `payer`: Payer account/company name
- `payee`: Payee account/company name
- `payer_fsp` / `payee_fsp`: Financial Service Providers (FSPs)
- `amount`: Transaction numerical value
- `currency`: Multi-currency code (`USD`, `INR`, `EUR`, `KES`)
- `status`: Transfer status (`SUCCESS`)
- `transfer_state`: Mojaloop state (`COMMITTED`)
- `created_at`: Datetime timestamp

### 2. `BankTransaction` (`bank_transactions` table)
Represents Central Settlement Bank statement entries.
- `bank_statement_id`: Statement record identifier (e.g. `BS-9001`)
- `account_number`: Bank account number
- `counterparty`: Bank counterparty name
- `amount`: Credit/Debit amount
- `currency`: Currency code
- `reference_number`: Matching reference code
- `transaction_type`: `CREDIT` or `DEBIT`
- `bank_name`: Bank name

### 3. `ERPRecord` (`erp_records` table)
Represents Enterprise ERP invoices and ledgers.
- `erp_id`: ERP record identifier (e.g. `ERP-5001`)
- `invoice_number`: Invoice reference code
- `customer_vendor_name`: Vendor or customer name
- `expected_amount`: Expected invoice amount
- `currency`: Currency code
- `ledger_account`: General ledger account

### 4. `Reconciliation` (`reconciliations` table)
Stores multi-ledger fuzzy match output.
- `transaction_id`: Matched transaction ID
- `ledger_id`: Matched bank or ERP record ID
- `ledger_type`: `BANK` or `ERP`
- `status`: `matched`, `partial`, `unmatched`
- `amount_difference`: Numerical amount variance in USD
- `match_confidence`: Confidence percentage (0.0 to 100.0%)
- `confidence_tier`: `high`, `medium`, `low`
- `remarks`: Human-readable explanation of match result

### 5. `Anomaly` (`anomalies` table)
Stores flagged discrepancies and risk metrics.
- `transaction_id`: Transaction ID with anomaly
- `anomaly_type`: `duplicate_charge`, `amount_mismatch`, `missing_settlement`
- `severity`: `high`, `medium`, `low`
- `risk_score`: Risk score (0.0 to 1.0)
- `explanation`: AI explanation of anomaly
- `status`: `OPEN` or `RESOLVED`

### 6. `CashFlowForecast` (`cash_flow_forecasts` table)
Stores machine learning cash flow predictions.
- `forecast_date`: Prediction date
- `predicted_amount`: Predicted daily inflow value
- `lower_bound`: 95% confidence interval lower bound
- `upper_bound`: 95% confidence interval upper bound
- `inflow`: Inflow volume
- `outflow`: Outflow volume

---

## 4. AI & ML Engines & Matching Logic

### 1. AI Fuzzy Matching Engine (`RapidFuzz`)
Matches transaction records between payment rail transfers, bank statements, and ERP invoices.

Confidence Score Calculation:
$$\text{Confidence Score} = (0.4 \times \text{Name Similarity}) + (0.4 \times \text{Amount Score}) + \text{Reference ID Boost} (+40\%)$$

- **High Confidence ($\ge 90\%$)** $\rightarrow$ `matched` (Auto-reconciled)
- **Medium Confidence ($70\% - 89\%$)** $\rightarrow$ `partial` (Fee/amount discrepancy)
- **Low Confidence ($< 70\%$)** $\rightarrow$ `unmatched` (Flagged anomaly)

### 2. Anomaly Detection Engine
Evaluates records for:
- `duplicate_charge`: Identical amount & payer within time window.
- `amount_mismatch`: Intermediary fee variance or FX rounding error.
- `missing_settlement`: Rail transfer committed with missing bank credit.

### 3. ML Cash Flow Forecasting Engine (`Scikit-Learn` Ridge Regression)
Trains on 30-day historical time-series data with 7-day weekly seasonality to predict future cash positions with 95% confidence intervals.

---

## 5. Summary of Live Data (150 Dataset)

- **Total Processed Transactions**: **`150`**
- **Matched Transactions**: **`148`** (**98.67% Match Rate**)
- **Partial / Fee Discrepancies**: **`1`**
- **Unmatched Transactions**: **`1`**
- **Flagged Anomalies**: **`3`**
- **Total Reconciled Value**: **`$64,318,676.40 USD`**

---

## 6. How to Run the System

### 1. Start Backend API Server
```powershell
cd c:\Users\Samsung\Downloads\Projects\AI-Payment-Reconciliation\backend
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
- API Documentation & File Upload Web UI: **[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)**
- Health Endpoint: **[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)**

### 2. Start Frontend Treasury Dashboard
```powershell
cd c:\Users\Samsung\Downloads\Projects\AI-Payment-Reconciliation\frontend
npx vite --port 5173
```
- Treasury Dashboard UI: **[http://localhost:5173](http://localhost:5173)**

### 3. Run Automated 9-Step Test Suite
```powershell
cd c:\Users\Samsung\Downloads\Projects\AI-Payment-Reconciliation\backend
.\venv\Scripts\python.exe test_api.py
```
