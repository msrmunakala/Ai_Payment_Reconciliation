import sys
import os
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.main import app

client = TestClient(app)
headers = {"X-API-Key": "review2-demo-key"}


def test_backend_end_to_end():
    print("==================================================")
    print("STARTING BACKEND END-TO-END VERIFICATION")
    print("==================================================")

    # 1. Health & Root Endpoint
    res = client.get("/")
    assert res.status_code == 200, f"Root endpoint failed: {res.text}"
    print("[PASS] Root Endpoint OK:", res.json()["project"])

    res = client.get("/health")
    assert res.status_code == 200, f"Health endpoint failed: {res.text}"
    print("[PASS] Health Endpoint OK:", res.json())

    # 2. Reset Demo & Seed Data (150 records)
    res = client.post("/demo/reset?count=150", headers=headers)
    assert res.status_code == 200, f"Demo reset failed: {res.text}"
    print("[PASS] Seed Demo Data OK:", res.json()["message"])

    # 3. Dashboard Summary
    res = client.get("/dashboard/summary", headers=headers)
    assert res.status_code == 200, f"Dashboard summary failed: {res.text}"
    summary = res.json()
    print("[PASS] Dashboard Summary OK:")
    print("       - Total Transactions:", summary["total_transactions"])
    print("       - Matched Transactions:", summary["matched_transactions"])
    print("       - Match Rate:", f"{summary['match_rate']}%")
    print("       - Anomaly Count:", summary["anomaly_count"])

    # 4. Trigger AI Reconciliation Run
    res = client.post("/reconciliation/run", headers=headers)
    assert res.status_code == 200, f"Reconciliation run failed: {res.text}"
    rec_run = res.json()
    print("[PASS] Reconciliation Run OK:")
    print("       - Total Reconciled:", rec_run["total_reconciled"])
    print("       - Matched Count:", rec_run["matched_count"])

    # 5. Fetch Reconciliation Results
    res = client.get("/reconciliation/results", headers=headers)
    assert res.status_code == 200, f"Reconciliation results failed: {res.text}"
    results = res.json()
    assert len(results) > 0, "No reconciliation results returned!"
    print(f"[PASS] Reconciliation Results OK ({len(results)} items fetched)")

    # 6. Fetch Anomalies
    res = client.get("/anomalies", headers=headers)
    assert res.status_code == 200, f"Anomalies endpoint failed: {res.text}"
    anomalies = res.json()
    print(f"[PASS] Anomalies Endpoint OK ({len(anomalies)} anomalies detected)")
    if anomalies:
        print("       Top Anomaly:", anomalies[0]["type"], "| Severity:", anomalies[0]["severity"])

    # 7. Cash Flow Forecast
    res = client.get("/forecast?days=7", headers=headers)
    assert res.status_code == 200, f"Forecast endpoint failed: {res.text}"
    forecasts = res.json()
    assert len(forecasts) == 7, "Forecast should return 7 daily points!"
    print("[PASS] ML Cash Flow Forecast OK (7-day horizon generated)")
    print("       - Day 1 Forecast:", forecasts[0]["predicted_amount"], "USD")

    # 8. Webhook Transfer Ingestion
    webhook_payload = {
        "transactionId": "TX-9999",
        "payer": "Test Payer Inc",
        "payee": "Test Payee LLC",
        "payerFsp": "BankA",
        "payeeFsp": "BankB",
        "amount": 5000.0,
        "currency": "USD",
        "transferState": "COMMITTED"
    }
    res = client.post("/webhooks/transfers", json=webhook_payload, headers=headers)
    assert res.status_code == 200, f"Webhook ingestion failed: {res.text}"
    print("[PASS] Mojaloop Webhook Transfer Ingestion OK:", res.json()["data"]["message"])

    # 9. CSV Report Export
    res = client.get("/reports/export.csv", headers=headers)
    assert res.status_code == 200, f"Export CSV failed: {res.text}"
    assert "transaction_id,ledger_id" in res.text, "CSV header missing"
    print("[PASS] Reconciliation CSV Export OK (Report generated)")

    print("==================================================")
    print("ALL 9 BACKEND VERIFICATION TESTS PASSED SUCCESSFULLY!")
    print("==================================================")


if __name__ == "__main__":
    test_backend_end_to_end()
