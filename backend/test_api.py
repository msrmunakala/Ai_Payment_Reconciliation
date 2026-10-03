"""End-to-end API verification against an in-process instance of the app.

Exercises every endpoint the React dashboard calls, plus the endpoints added
while completing the Mojaloop, FX, scheduling and forecasting features. Uses a
throwaway database so it never touches ``reconciliation.db``.

Run with:  python test_api.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Must be set before app.* is imported: the engine is built from configuration
# at import time.
#
# Defaults to a throwaway SQLite file. Set E2E_DATABASE_URL to run the same
# suite against PostgreSQL, e.g.
#   $env:E2E_DATABASE_URL="postgresql+psycopg://user:pw@127.0.0.1:5432/reconciliation_test"
TEST_DB = Path(__file__).resolve().parent / "_e2e_api.db"
_OVERRIDE = os.environ.get("E2E_DATABASE_URL")
os.environ["DATABASE_URL"] = _OVERRIDE or f"sqlite:///./{TEST_DB.name}"
USING_SQLITE = not _OVERRIDE
os.environ["SEED_ON_STARTUP"] = "true"
# Alembic owns a server schema; let the app create tables itself for this run.
os.environ.setdefault("AUTO_SCHEMA_SYNC", "true")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["API_KEY"] = "e2e-test-key"
os.environ["WEBHOOK_SECRET"] = "e2e-webhook-secret"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.utils.helpers import compute_hmac_signature  # noqa: E402

HEADERS = {"X-API-Key": "e2e-test-key"}

passed = 0
failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {label}" + (f" :: {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  [FAIL] {label}" + (f" :: {detail}" if detail else ""))


def section(title):
    print(f"\n{title}")
    print("-" * len(title))


def main():
    if USING_SQLITE and TEST_DB.exists():
        TEST_DB.unlink()
    else:
        # Server database: start from a clean schema so assertions about row
        # counts hold regardless of what a previous run left behind.
        from app.database.init_db import drop_db, init_db

        drop_db()
        init_db()

    print(f"database: {'sqlite (throwaway file)' if USING_SQLITE else 'server (E2E_DATABASE_URL)'}")

    with TestClient(app) as client:
        section("1. Public endpoints and auth")
        response = client.get("/")
        check("GET / is public", response.status_code == 200)
        check("GET /health is public", client.get("/health").status_code == 200)
        check("GET /ready reports readiness", client.get("/ready").status_code == 200)

        check(
            "protected route rejects a missing key",
            client.get("/dashboard/summary").status_code == 401,
        )
        check(
            "protected route rejects a wrong key",
            client.get("/dashboard/summary", headers={"X-API-Key": "nope"}).status_code
            == 401,
        )
        check(
            "protected route accepts the correct key",
            client.get("/dashboard/summary", headers=HEADERS).status_code == 200,
        )

        section("2. Seed a reproducible dataset")
        response = client.post(
            "/demo/reset?count=200&seed=7&history_days=90", headers=HEADERS
        )
        check("POST /demo/reset", response.status_code == 200, response.text[:120])
        seeded = response.json()
        run = seeded.get("reconciliation", {})
        check("seed reconciled 200 transactions", run.get("total_reconciled") == 200,
              f"matched={run.get('matched_count')} partial={run.get('partial_count')} "
              f"flagged={run.get('flagged_count')} unmatched={run.get('unmatched_count')}")
        stats = run.get("matching_stats", {})
        naive = stats.get("transactions", 0) * stats.get("ledger_records", 0)
        check(
            "candidate generation beat the cross product",
            0 < stats.get("pairs_scored", 0) < naive,
            f"{stats.get('pairs_scored')} pairs vs {naive} naive "
            f"({stats.get('reduction_factor')}x reduction)",
        )

        section("3. Dashboard (frontend contract)")
        summary = client.get("/dashboard/summary", headers=HEADERS).json()
        for key in (
            "total_transactions",
            "matched_transactions",
            "partial_matched",
            "unmatched_transactions",
            "match_rate",
            "anomaly_count",
            "total_reconciled_value",
            "synthetic_data_notice",
        ):
            check(f"summary carries '{key}'", key in summary)
        check(
            "FX-normalised total is positive",
            summary["total_reconciled_value"] > 0,
            f"{summary['total_reconciled_value']:,.2f} {summary.get('base_currency')}",
        )
        check(
            "currency exposure is broken out",
            len(summary.get("currency_exposure", [])) > 1,
            ", ".join(c["currency"] for c in summary.get("currency_exposure", [])),
        )
        check(
            "ledger coverage is reported",
            summary.get("ledger_coverage", {}).get("bank_records", 0) > 0,
        )
        check("run provenance is attached", summary.get("last_run") is not None)

        response = client.get("/dashboard/runs?limit=5", headers=HEADERS)
        check("GET /dashboard/runs", response.status_code == 200,
              f"{len(response.json().get('runs', []))} run(s)")

        section("4. Reconciliation results and filters")
        response = client.get("/reconciliation/results?limit=50", headers=HEADERS)
        check("GET /reconciliation/results", response.status_code == 200)
        page = response.json()
        check("results are paginated", page.get("total", 0) == 200 and page.get("returned") == 50,
              f"total={page.get('total')} returned={page.get('returned')}")
        first = page["items"][0]
        for key in ("transaction_id", "status", "match_confidence", "amount_difference", "remarks"):
            check(f"result row carries '{key}'", key in first)
        check(
            "score breakdown is exposed",
            first.get("score_amount") is not None and first.get("score_name") is not None,
        )
        check(
            "an invalid status filter is rejected with 422",
            client.get("/reconciliation/results?status=bogus", headers=HEADERS).status_code
            == 422,
        )
        check(
            "a valid status filter works",
            client.get("/reconciliation/results?status=matched", headers=HEADERS).status_code
            == 200,
        )

        section("5. Anomalies and triage")
        anomalies = client.get("/anomalies?limit=200", headers=HEADERS).json()
        check("GET /anomalies returns a list", isinstance(anomalies, list), f"{len(anomalies)} items")
        types = {a["type"] for a in anomalies}
        check("multiple anomaly types detected", len(types) >= 3, ", ".join(sorted(types)))
        check(
            "risk scores are within 0-1 and not all identical",
            len({a["risk_score"] for a in anomalies}) > 1
            and all(0.0 <= a["risk_score"] <= 1.0 for a in anomalies),
        )
        check("explanations are populated", all(a["explanation"] for a in anomalies))
        check("detector is attributed", all(a.get("detector") for a in anomalies))

        if anomalies:
            target = anomalies[0]
            listing = client.get("/reconciliation/anomalies?limit=5", headers=HEADERS)
            check("GET /reconciliation/anomalies", listing.status_code == 200)
            anomaly_id = listing.json()["items"][0]["id"]
            resolve = client.post(
                f"/reconciliation/anomalies/{anomaly_id}/resolve",
                headers=HEADERS,
                json={"status": "RESOLVED", "note": "verified in e2e"},
            )
            check("an anomaly can be resolved", resolve.status_code == 200)

        section("6. Manual match survives a re-run")
        unmatched = client.get(
            "/reconciliation/results?status=flag_for_review&limit=1", headers=HEADERS
        ).json()
        if not unmatched["items"]:
            unmatched = client.get(
                "/reconciliation/results?status=partial&limit=1", headers=HEADERS
            ).json()

        if unmatched["items"]:
            tx_id = unmatched["items"][0]["transaction_id"]
            bank_id = client.get("/transactions/bank?limit=1", headers=HEADERS).json()[
                "items"
            ][0]["bank_statement_id"]
            response = client.post(
                "/reconciliation/manual-match",
                headers=HEADERS,
                json={
                    "transaction_id": tx_id,
                    "ledger_id": bank_id,
                    "ledger_type": "BANK",
                    "remarks": "e2e override",
                },
            )
            check("POST /reconciliation/manual-match", response.status_code == 200, response.text[:120])

            client.post("/reconciliation/run?refresh_forecast=false", headers=HEADERS)
            after = client.get(f"/reconciliation/results/{tx_id}", headers=HEADERS).json()
            check(
                "override persisted through a full re-run",
                after["status"] == "matched" and after["is_manual"] == 1,
                f"status={after['status']} is_manual={after['is_manual']}",
            )
            check(
                "a non-existent manual match is rejected",
                client.post(
                    "/reconciliation/manual-match",
                    headers=HEADERS,
                    json={"transaction_id": "NOPE", "ledger_id": bank_id},
                ).status_code
                == 404,
            )
        else:
            print("  [SKIP] no non-matched row available to override")

        section("7. Forecasting")
        response = client.get("/forecast?days=7", headers=HEADERS)
        check("GET /forecast", response.status_code == 200)
        points = response.json()
        check("7 points returned", len(points) == 7)
        for key in ("date", "predicted_amount", "lower_bound", "upper_bound", "inflow", "outflow"):
            check(f"forecast point carries '{key}'", key in points[0])
        check(
            "net equals inflow minus outflow",
            all(
                abs(p["predicted_amount"] - round(p["inflow"] - p["outflow"], 2)) < 0.05
                for p in points
            ),
        )
        meta = client.get("/forecast/metadata", headers=HEADERS).json()
        check(
            "forecast is not synthetic and names its model",
            meta.get("available") and not meta.get("is_synthetic"),
            f"model={meta.get('model')} history={meta.get('history_days')}d "
            f"prophet_installed={meta.get('prophet_installed')}",
        )
        repeat = client.get("/forecast?days=7", headers=HEADERS).json()
        check(
            "repeated GET is stable (cached, non-destructive)",
            [p["predicted_amount"] for p in points] == [p["predicted_amount"] for p in repeat],
        )
        check(
            "POST /forecast/run retrains",
            client.post("/forecast/run?days=14", headers=HEADERS).status_code == 200,
        )
        check(
            "14-day horizon honoured",
            len(client.get("/forecast?days=14", headers=HEADERS).json()) == 14,
        )

        section("8. FX management")
        rates = client.get("/fx/rates", headers=HEADERS).json()
        check("GET /fx/rates", len(rates.get("rates", [])) > 0, f"{len(rates['rates'])} pairs")
        conversion = client.get(
            "/fx/convert?amount=83500&from_currency=INR", headers=HEADERS
        ).json()
        check(
            "83,500 INR converts to 1,000 USD",
            abs(conversion["amount"] - 1000.0) < 0.01,
            f"{conversion['amount']} via rate {conversion['rate']}",
        )
        check(
            "PUT /fx/rates records an override",
            client.put("/fx/rates/INR?units_per_base=84.0", headers=HEADERS).status_code == 200,
        )
        history = client.get("/fx/rates?include_history=true", headers=HEADERS).json()
        check(
            "the superseded rate is retained as history",
            any(r["valid_to"] for r in history["rates"]),
        )
        check("POST /fx/refresh", client.post("/fx/refresh", headers=HEADERS).status_code == 200)

        section("9. Mojaloop webhooks and sync")
        payload = {
            "transactionId": "TX-E2E-0001",
            "payer": "E2E Payer Inc",
            "payee": "E2E Payee LLC",
            "payerFsp": "BankA",
            "payeeFsp": "BankB",
            "amount": 5000.0,
            "currency": "USD",
            "transferState": "COMMITTED",
        }
        import json as _json

        body = _json.dumps(payload).encode()
        signature = compute_hmac_signature(body, "e2e-webhook-secret")

        check(
            "an unsigned webhook is rejected",
            client.post("/webhooks/transfers", headers=HEADERS, json=payload).status_code
            == 401,
        )
        check(
            "a mis-signed webhook is rejected",
            client.post(
                "/webhooks/transfers",
                headers={**HEADERS, "X-Signature": "sha256=deadbeef"},
                content=body,
            ).status_code
            == 401,
        )
        response = client.post(
            "/webhooks/transfers",
            headers={**HEADERS, "X-Signature": signature, "Content-Type": "application/json"},
            content=body,
        )
        check("a correctly signed webhook is accepted", response.status_code == 200,
              response.text[:160])
        if response.status_code == 200:
            data = response.json()["data"]
            check("webhook created the transaction", data.get("created") is True)
            check(
                "webhook reconciled incrementally, not the whole database",
                data["reconciliation"].get("candidates_evaluated", 0) < 500,
                f"candidates_evaluated={data['reconciliation'].get('candidates_evaluated')}",
            )

        replay = client.post(
            "/webhooks/transfers",
            headers={**HEADERS, "X-Signature": signature, "Content-Type": "application/json"},
            content=body,
        )
        check(
            "replaying the same transfer is idempotent",
            replay.status_code == 200 and replay.json()["data"]["created"] is False,
        )

        # There is deliberately no bulk transfer pull: FSPIOP exposes no
        # collection endpoint for transfers. Verification is per transfer, and
        # the offline injector is clearly labelled as simulated.
        response = client.get(
            "/webhooks/transfers/TX-E2E-0001/verify", headers=HEADERS
        )
        check("GET /webhooks/transfers/{id}/verify", response.status_code == 200)
        verification = response.json()
        check(
            "verification reports its outcome explicitly",
            "verified" in verification,
            f"verified={verification.get('verified')} "
            f"error={(verification.get('error') or '')[:60]}",
        )

        response = client.post("/webhooks/simulate-inbound", headers=HEADERS)
        check("POST /webhooks/simulate-inbound", response.status_code == 200)
        injected = response.json()
        check(
            "offline injection is labelled simulated",
            injected.get("simulated") is True,
            f"created={injected.get('created')} updated={injected.get('updated')}",
        )

        check("GET /webhooks/status", client.get("/webhooks/status", headers=HEADERS).status_code == 200)
        check(
            "GET /webhooks/simulate-fetch is marked simulated",
            client.get("/webhooks/simulate-fetch", headers=HEADERS).json().get("simulated")
            is True,
        )

        section("9b. Settlement windows and window-scoped reconciliation")
        response = client.post("/settlement/sync", headers=HEADERS)
        check("POST /settlement/sync", response.status_code == 200)
        window_sync = response.json()
        hub_live = window_sync.get("fetched", 0) > 0
        check(
            "settlement sync reports its outcome",
            "enabled" in window_sync,
            f"enabled={window_sync.get('enabled')} fetched={window_sync.get('fetched')}"
            + ("" if hub_live else "  (hub not reachable; scoping checks skipped)"),
        )

        response = client.get("/settlement/windows", headers=HEADERS)
        check("GET /settlement/windows", response.status_code == 200)
        check(
            "GET /settlement/windows/pending",
            client.get("/settlement/windows/pending", headers=HEADERS).status_code == 200,
        )
        check(
            "an invalid window state is rejected",
            client.post("/settlement/sync?state=NOPE", headers=HEADERS).status_code == 422,
        )
        check(
            "reconciling an unknown window returns 404",
            client.post(
                "/settlement/windows/does-not-exist/reconcile", headers=HEADERS
            ).status_code
            == 404,
        )
        check(
            "settlements require explicit ids (no list endpoint exists)",
            client.post("/settlement/settlements/sync", headers=HEADERS).status_code == 422,
        )

        if hub_live:
            windows = client.get("/settlement/windows", headers=HEADERS).json()["items"]
            target = next((w for w in windows if w["is_reconcilable"]), None)
            if target:
                before_page = client.get(
                    "/reconciliation/results?limit=1000", headers=HEADERS
                ).json()
                before_total = before_page["total"]
                before_ids = {item["transaction_id"] for item in before_page["items"]}

                response = client.post(
                    f"/settlement/windows/{target['window_id']}/reconcile",
                    headers=HEADERS,
                )
                check("POST window reconcile", response.status_code == 200, response.text[:120])
                scoped = response.json()
                check(
                    "run is recorded as window-scoped",
                    scoped.get("scope") == "settlement_window"
                    and scoped.get("settlement_window_id") == target["window_id"],
                    f"scope={scoped.get('scope')} window={scoped.get('settlement_window_id')} "
                    f"total={scoped.get('total_reconciled')}",
                )
                check(
                    "a scoped run covers fewer transactions than the whole book",
                    scoped.get("total_reconciled", 0) <= before_total,
                    f"{scoped.get('total_reconciled')} of {before_total}",
                )

                # The row count may legitimately *grow*: a transaction that is
                # inside the window but had no result yet gains one. What must
                # never happen is losing a result for a transaction outside the
                # scope, which is what a full-table DELETE would cause.
                after_page = client.get(
                    "/reconciliation/results?limit=1000", headers=HEADERS
                ).json()
                after_ids = {item["transaction_id"] for item in after_page["items"]}
                lost = before_ids - after_ids
                check(
                    "a scoped run does not discard out-of-scope results",
                    not lost,
                    f"{before_total} rows before, {after_page['total']} after, "
                    f"{len(lost)} lost",
                )
            else:
                print("  [SKIP] no reconcilable window returned by the hub")
        else:
            print("  [SKIP] hub unreachable; window-scoped reconciliation not exercised")

        section("10. Ingestion")
        csv_bytes = (
            b"bank_statement_id,account_number,counterparty,amount,currency,"
            b"transaction_type,reference_number,value_date\n"
            b"BS-E2E-1,ACC-5,E2E Vendor Ltd,1500.00,USD,CREDIT,REF-E2E-1,2026-09-15\n"
            b'BS-E2E-2,ACC-5,E2E Vendor Ltd,"2,000.50",EUR,CREDIT,REF-E2E-2,2026-09-16\n'
            b"BS-E2E-2,ACC-5,E2E Vendor Ltd,2000.50,EUR,CREDIT,REF-E2E-2,2026-09-16\n"
            b"BS-E2E-3,ACC-5,Broken,NOT_A_NUMBER,USD,CREDIT,REF-E2E-3,2026-09-17\n"
        )
        response = client.post(
            "/transactions/ingest/bank-csv",
            headers=HEADERS,
            files={"file": ("bank.csv", csv_bytes, "text/csv")},
        )
        check("POST /transactions/ingest/bank-csv", response.status_code == 200, response.text[:120])
        result = response.json()
        check(
            "ingestion isolates the bad row and the in-file duplicate",
            result["inserted"] == 2
            and result["failed"] == 1
            and result["skipped_duplicate_in_file"] == 1,
            f"inserted={result['inserted']} failed={result['failed']} "
            f"dupe={result['skipped_duplicate_in_file']}",
        )
        again = client.post(
            "/transactions/ingest/bank-csv",
            headers=HEADERS,
            files={"file": ("bank.csv", csv_bytes, "text/csv")},
        ).json()
        check(
            "re-uploading the same file inserts nothing",
            again["inserted"] == 0 and again["skipped_already_present"] == 2,
        )
        check(
            "an unsupported file type is rejected with 415",
            client.post(
                "/transactions/ingest/bank-csv",
                headers=HEADERS,
                files={"file": ("bad.pdf", b"%PDF-1.4", "application/pdf")},
            ).status_code
            == 415,
        )

        section("11. Transactions and pagination")
        response = client.get("/transactions/?limit=10&offset=0", headers=HEADERS)
        check("GET /transactions is paginated", response.status_code == 200)
        body_json = response.json()
        check("pagination metadata present", body_json["returned"] == 10 and body_json["total"] > 10,
              f"total={body_json['total']}")
        check(
            "search filter works",
            client.get("/transactions/?search=Acme&limit=5", headers=HEADERS).status_code == 200,
        )
        check(
            "lookup by business id",
            client.get("/transactions/TX-E2E-0001", headers=HEADERS).status_code == 200,
        )
        check(
            "unknown id returns 404",
            client.get("/transactions/DOES-NOT-EXIST", headers=HEADERS).status_code == 404,
        )
        check("GET /transactions/erp", client.get("/transactions/erp?limit=5", headers=HEADERS).status_code == 200)

        section("12. Scheduling")
        status = client.get("/scheduler/status", headers=HEADERS).json()
        check("GET /scheduler/status", "available" in status, f"available={status.get('available')}")
        check(
            "scheduler can be started",
            client.post("/scheduler/start?interval_minutes=30", headers=HEADERS).status_code == 200,
        )
        status = client.get("/scheduler/status", headers=HEADERS).json()
        check("job registered with the requested interval", status.get("interval_minutes") == 30.0,
              f"next_run={status.get('next_run_time')}")
        check(
            "interval can be changed at runtime",
            client.post("/scheduler/interval?interval_minutes=5", headers=HEADERS).json().get(
                "interval_minutes"
            )
            == 5.0,
        )
        check("scheduler can be paused", client.post("/scheduler/pause", headers=HEADERS).json().get("paused") is True)
        check("scheduler can be resumed", client.post("/scheduler/resume", headers=HEADERS).status_code == 200)
        check(
            "an invalid interval is rejected",
            client.post("/scheduler/interval?interval_minutes=0", headers=HEADERS).status_code == 422,
        )
        check("scheduler can be stopped", client.post("/scheduler/stop", headers=HEADERS).status_code == 200)

        section("13. Reporting and export")
        response = client.get("/reports/export.csv", headers=HEADERS)
        check("GET /reports/export.csv", response.status_code == 200)
        check("CSV header includes the score breakdown",
              "transaction_id,ledger_id" in response.text and "score_amount" in response.text)
        check("CSV has data rows", len(response.text.strip().splitlines()) > 1,
              f"{len(response.text.strip().splitlines()) - 1} rows")
        check(
            "an invalid export filter is rejected",
            client.get("/reports/export.csv?status=bogus", headers=HEADERS).status_code == 422,
        )

        export = client.get("/demo/export-json?limit=50", headers=HEADERS)
        check("GET /demo/export-json", export.status_code == 200)
        payload = export.json()
        check("export is bounded by the limit", len(payload["transactions"]) <= 50)
        check("export reports true totals", payload["metadata"]["totals"]["transactions"] > 50)

        script = client.get("/demo/generator-script", headers=HEADERS)
        check("GET /demo/generator-script", script.status_code == 200)
        script_body = script.json()
        check(
            "generator script is read from real source",
            "def seed_demo_data" in script_body["code"],
            f"{script_body['line_count']} lines from {script_body['filename']}",
        )

        section("14. OpenAPI")
        spec = client.get("/openapi.json").json()
        check("OpenAPI spec generated", "paths" in spec, f"{len(spec['paths'])} paths")

    print("\n" + "=" * 62)
    print(f"RESULT: {passed} passed, {failed} failed")
    print("=" * 62)

    # Release the SQLite file handle before unlinking; on Windows an open pooled
    # connection makes the delete fail with WinError 32.
    from app.database.db import engine

    engine.dispose()

    if USING_SQLITE:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(TEST_DB) + suffix)
            if candidate.exists():
                try:
                    candidate.unlink()
                except OSError as exc:  # pragma: no cover - platform dependent
                    print(f"  [warn] could not remove {candidate.name}: {exc}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
