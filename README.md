# AI Payment Reconciliation Engine on Mojaloop (Review-2)

An explainable, AI-powered payment reconciliation, anomaly detection, and cash flow liquidity forecasting system for Mojaloop hub transactions, Central Bank statements, and ERP invoices.

## Features

- **Mojaloop Payment Ingestion & Webhook Handler**: Asynchronous ingestion of Mojaloop transfer state notifications.
- **Explainable AI Matching**: Composite confidence scoring (0–100%) incorporating rapid fuzzy matching, amount delta evaluation, and reference ID verification.
- **Anomaly Detection**: Automated detection of duplicate charges, missing central bank settlements, and amount discrepancies with risk scoring.
- **ML Cash Flow Forecasting**: Scikit-Learn Ridge Regression model with 95% confidence intervals and 7-day/14-day liquidity prediction.
- **Treasury Dashboard & Export**: Interactive Vite + React frontend dashboard with JSON export, CSV report generation, and embedded generator script viewer.

## Setup & Running Locally

### Backend (FastAPI + SQLAlchemy + Scikit-Learn)

```bash
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend (Vite + React)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser. Demo API key is `review2-demo-key`.
