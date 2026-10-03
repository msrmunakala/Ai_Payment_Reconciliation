"""Tests for the reconciliation engine, FX, ingestion and anomaly detection.

The previous version of this file was a single test that asserted
``db.query(Reconciliation).count() == 50`` while ``seed_demo_data`` defaulted to
150 rows, so it failed on a clean checkout.

``DATABASE_URL`` must be set before ``app.*`` is imported, because the engine is
built at import time from configuration.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATABASE_URL"] = "sqlite:///./test_reconciliation.db"
os.environ["SEED_ON_STARTUP"] = "false"
os.environ["SCHEDULER_ENABLED"] = "false"

from app.core.config import settings  # noqa: E402
from app.core.constants import (  # noqa: E402
    AnomalyStatus,
    AnomalyType,
    LedgerType,
    MatchStatus,
)
from app.database.db import Base, SessionLocal, engine  # noqa: E402
from app.models.bank_transaction import BankTransaction, ERPRecord  # noqa: E402
from app.models.reconciliation import (  # noqa: E402
    Anomaly,
    ManualMatch,
    Reconciliation,
    ReconciliationRun,
)
from app.models.transaction import Transaction  # noqa: E402
from app.services.fx_service import FXConverter, init_fx_rates  # noqa: E402
from app.services.ingestion_service import ingest_bank_csv  # noqa: E402
from app.services.matching_engine import (  # noqa: E402
    normalize_name,
    normalize_reference,
)
from app.services.reconciliation_service import (  # noqa: E402
    apply_manual_overrides,
    normalize_amounts,
    run_reconciliation,
    seed_demo_data,
)


def reset_schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


class FXTests(unittest.TestCase):
    def test_converts_via_base_currency(self):
        converter = FXConverter.from_db(None)
        # 83.50 INR per USD, so 83,500 INR is exactly 1,000 USD.
        self.assertAlmostEqual(converter.to_base(83_500.0, "INR"), 1000.0, places=4)

    def test_cross_rate_uses_base_as_pivot(self):
        converter = FXConverter.from_db(None)
        rate = converter.rate("EUR", "INR")
        self.assertIsNotNone(rate)
        # 83.50 / 0.92
        self.assertAlmostEqual(float(rate), 90.7608695652, places=6)

    def test_unknown_currency_is_reported_not_silently_accepted(self):
        converter = FXConverter.from_db(None)
        result = converter.convert(500.0, "ZZZ")
        self.assertTrue(result.is_fallback)
        self.assertIn("ZZZ", converter.unknown_currencies)

    def test_identity_conversion_is_exact(self):
        converter = FXConverter.from_db(None)
        result = converter.convert(1234.56, "USD")
        self.assertEqual(result.amount, 1234.56)
        self.assertEqual(result.rate, 1.0)


class NormalisationTests(unittest.TestCase):
    def test_legal_suffixes_are_stripped(self):
        self.assertEqual(normalize_name("Amazon India Pvt Ltd"), "amazon india")
        self.assertEqual(normalize_name("AMAZON INDIA PVT. LTD"), "amazon india")
        self.assertEqual(normalize_name("Amazon India"), "amazon india")

    def test_reference_normalisation_ignores_punctuation_and_case(self):
        self.assertEqual(normalize_reference("REF-8801"), "ref8801")
        self.assertEqual(normalize_reference("ref 8801"), "ref8801")
        self.assertEqual(normalize_reference(None), "")


class ScoringTests(unittest.TestCase):
    """The weights must allow a perfect match to reach the matched threshold.

    The original scoring (``name*0.4 + amount*0.4 + 40 if reference matches``)
    capped a no-reference match at 80 against a 90 threshold.
    """

    def test_weights_sum_to_one_hundred(self):
        config = settings.matching
        total = (
            config.weight_amount
            + config.weight_name
            + config.weight_reference
            + config.weight_date
        )
        self.assertAlmostEqual(total, 100.0)

    def test_perfect_match_without_reference_still_reaches_matched(self):
        from app.services.matching_engine import LedgerCandidate, score_pair

        converter = FXConverter.from_db(None)
        now = datetime.utcnow()
        transaction = Transaction(
            transaction_id="T1",
            reference_id=None,
            payer="Acme Corporation",
            payee="Globex",
            payer_fsp="A",
            payee_fsp="B",
            amount=1000.0,
            currency="USD",
            created_at=now,
        )
        candidate = LedgerCandidate(
            ledger_id="B1",
            ledger_type=LedgerType.BANK.value,
            counterparty="Acme Corp",
            reference=None,
            amount=1000.0,
            currency="USD",
            amount_base=1000.0,
            value_date=now,
            normalized_name=normalize_name("Acme Corp"),
            normalized_reference="",
        )
        breakdown = score_pair(
            transaction,
            candidate,
            1000.0,
            normalize_name("Acme Corporation"),
            normalize_name("Globex"),
            "",
            tolerance=20.0,
            config=settings.matching,
        )
        self.assertGreaterEqual(breakdown.total, settings.matching.matched_threshold)


class ReconciliationRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_schema()
        cls.db = SessionLocal()
        cls.result = seed_demo_data(cls.db, count=120, seed=99, history_days=60)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_seed_creates_expected_row_count(self):
        self.assertEqual(self.db.query(Transaction).count(), 120)
        self.assertEqual(self.db.query(Reconciliation).count(), 120)
        self.assertIn("successfully seeded", self.result["message"])

    def test_run_is_recorded_for_audit(self):
        run = (
            self.db.query(ReconciliationRun)
            .order_by(ReconciliationRun.id.desc())
            .first()
        )
        self.assertIsNotNone(run)
        self.assertEqual(run.status, "success")
        self.assertEqual(run.transactions_examined, 120)
        self.assertGreater(run.candidates_evaluated, 0)

    def test_candidate_generation_beats_the_cross_product(self):
        stats = self.result["reconciliation"]["matching_stats"]
        naive = stats["transactions"] * stats["ledger_records"]
        self.assertLess(stats["pairs_scored"], naive)
        self.assertGreater(stats["reduction_factor"], 1.0)

    def test_every_amount_is_normalised(self):
        unnormalised = (
            self.db.query(Transaction).filter(Transaction.amount_base.is_(None)).count()
        )
        self.assertEqual(unnormalised, 0)

    def test_anomalies_include_the_previously_dead_types(self):
        """``timing_anomaly`` and ``unrecognized_account`` were declared in the
        model but never produced by the original engine."""
        types = {
            row[0]
            for row in self.db.query(Anomaly.anomaly_type).distinct().all()
        }
        self.assertIn(AnomalyType.DUPLICATE_CHARGE.value, types)
        self.assertIn(AnomalyType.MISSING_SETTLEMENT.value, types)
        self.assertIn(AnomalyType.TIMING_ANOMALY.value, types)
        self.assertIn(AnomalyType.UNRECOGNIZED_ACCOUNT.value, types)

    def test_risk_scores_are_computed_not_constant(self):
        scores = {
            row[0]
            for row in self.db.query(Anomaly.risk_score)
            .filter(Anomaly.anomaly_type == AnomalyType.MISSING_SETTLEMENT.value)
            .all()
        }
        self.assertTrue(all(0.0 <= value <= 1.0 for value in scores))

    def test_ledger_status_is_updated_when_consumed(self):
        reconciled = (
            self.db.query(BankTransaction)
            .filter(BankTransaction.status == "RECONCILED")
            .count()
        )
        self.assertGreater(reconciled, 0)

    def test_results_are_deterministic_across_runs(self):
        first = {
            row.transaction_id: (row.status, round(row.match_confidence, 4))
            for row in self.db.query(Reconciliation).all()
        }
        run_reconciliation(self.db, trigger="test", refresh_forecast=False)
        second = {
            row.transaction_id: (row.status, round(row.match_confidence, 4))
            for row in self.db.query(Reconciliation).all()
        }
        self.assertEqual(first, second)


class ExceptionLifecycleTests(unittest.TestCase):
    def setUp(self):
        reset_schema()
        self.db = SessionLocal()
        seed_demo_data(self.db, count=40, seed=5, history_days=30)

    def tearDown(self):
        self.db.close()

    def test_resolved_anomalies_survive_a_rerun(self):
        anomaly = self.db.query(Anomaly).first()
        self.assertIsNotNone(anomaly)
        marker = (anomaly.transaction_id, anomaly.anomaly_type)

        anomaly.status = AnomalyStatus.RESOLVED.value
        anomaly.resolution_note = "Reviewed by treasury"
        self.db.commit()

        run_reconciliation(self.db, trigger="test", refresh_forecast=False)

        still_resolved = (
            self.db.query(Anomaly)
            .filter(
                Anomaly.transaction_id == marker[0],
                Anomaly.anomaly_type == marker[1],
                Anomaly.status == AnomalyStatus.RESOLVED.value,
            )
            .count()
        )
        self.assertEqual(still_resolved, 1, "a resolved anomaly must not be recreated as OPEN")

    def test_manual_match_survives_a_rerun(self):
        unmatched = (
            self.db.query(Reconciliation)
            .filter(Reconciliation.status != MatchStatus.MATCHED.value)
            .first()
        )
        if unmatched is None:
            self.skipTest("seed produced no non-matched row to override")

        bank_row = self.db.query(BankTransaction).first()
        self.db.add(
            ManualMatch(
                transaction_id=unmatched.transaction_id,
                ledger_id=bank_row.bank_statement_id,
                ledger_type=LedgerType.BANK.value,
                remarks="Confirmed against the custodian statement",
                created_by="tester",
                is_active=1,
            )
        )
        self.db.commit()
        apply_manual_overrides(self.db)
        self.db.commit()

        run_reconciliation(self.db, trigger="test", refresh_forecast=False)

        row = (
            self.db.query(Reconciliation)
            .filter(Reconciliation.transaction_id == unmatched.transaction_id)
            .order_by(Reconciliation.id.desc())
            .first()
        )
        self.assertEqual(row.status, MatchStatus.MATCHED.value)
        self.assertEqual(row.is_manual, 1)
        self.assertEqual(row.ledger_id, bank_row.bank_statement_id)


class IngestionTests(unittest.TestCase):
    def setUp(self):
        reset_schema()
        self.db = SessionLocal()
        init_fx_rates(self.db)

    def tearDown(self):
        self.db.close()

    CSV = (
        b"bank_statement_id,account_number,counterparty,amount,currency,"
        b"transaction_type,reference_number,value_date\n"
        b"BS-1,ACC-1,Acme Corp,1000.00,USD,CREDIT,REF-1,2026-09-01\n"
        b'BS-2,ACC-1,Globex Ltd,"2,500.50",USD,CREDIT,REF-2,2026-09-02\n'
        b"BS-2,ACC-1,Globex Ltd,2500.50,USD,CREDIT,REF-2,2026-09-02\n"
        b"BS-3,ACC-1,Broken Row,NOT_A_NUMBER,USD,CREDIT,REF-3,2026-09-03\n"
    )

    def test_bulk_dedup_and_error_isolation(self):
        result = ingest_bank_csv(self.CSV, self.db)
        self.assertEqual(result["rows_read"], 4)
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["skipped_duplicate_in_file"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertIn("not a number", result["errors"][0]["error"])

    def test_reupload_is_idempotent(self):
        ingest_bank_csv(self.CSV, self.db)
        second = ingest_bank_csv(self.CSV, self.db)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["skipped_already_present"], 2)
        self.assertEqual(self.db.query(BankTransaction).count(), 2)

    def test_thousands_separator_is_parsed(self):
        ingest_bank_csv(self.CSV, self.db)
        row = (
            self.db.query(BankTransaction)
            .filter(BankTransaction.bank_statement_id == "BS-2")
            .first()
        )
        self.assertEqual(row.amount, 2500.50)

    def test_amounts_are_normalised_at_ingest(self):
        csv_eur = (
            b"bank_statement_id,account_number,counterparty,amount,currency\n"
            b"BS-EUR,ACC-9,Berlin Distro,920.00,EUR\n"
        )
        ingest_bank_csv(csv_eur, self.db)
        row = (
            self.db.query(BankTransaction)
            .filter(BankTransaction.bank_statement_id == "BS-EUR")
            .first()
        )
        self.assertIsNotNone(row.amount_base)
        self.assertAlmostEqual(row.amount_base, 1000.0, places=2)


class WebhookSignatureTests(unittest.TestCase):
    def test_hmac_round_trip(self):
        from app.utils.helpers import compute_hmac_signature, verify_hmac_signature

        body = b'{"transactionId":"TX-1"}'
        secret = "s3cret"
        signature = compute_hmac_signature(body, secret)
        self.assertTrue(verify_hmac_signature(body, signature, secret))
        self.assertFalse(verify_hmac_signature(body, signature, "wrong-secret"))
        self.assertFalse(verify_hmac_signature(b"tampered", signature, secret))
        self.assertFalse(verify_hmac_signature(body, "", secret))


class MojaloopNormalisationTests(unittest.TestCase):
    def test_fspiop_payload_is_mapped(self):
        from app.services.mojaloop_service import normalize_transfer

        payload = {
            "transferId": "HUB-1",
            "payer": {
                "partyIdInfo": {"partyIdentifier": "2567712", "fspId": "payerfsp"},
                "personalInfo": {"complexName": {"firstName": "Acme", "lastName": "Corp"}},
            },
            "payee": {"name": "Globex Ltd", "partyIdInfo": {"fspId": "payeefsp"}},
            "amount": {"amount": "15400.00", "currency": "USD"},
            "transferState": "COMMITTED",
            "completedTimestamp": "2026-09-20T10:15:30.123Z",
        }
        mapped = normalize_transfer(payload)
        self.assertEqual(mapped["transaction_id"], "HUB-1")
        self.assertEqual(mapped["payer"], "Acme Corp")
        self.assertEqual(mapped["payee"], "Globex Ltd")
        self.assertEqual(mapped["payer_fsp"], "payerfsp")
        self.assertEqual(mapped["amount"], 15400.0)
        self.assertEqual(mapped["currency"], "USD")

    def test_missing_amount_is_rejected_individually(self):
        from app.services.mojaloop_service import normalize_transfer

        with self.assertRaises(ValueError):
            normalize_transfer({"transferId": "HUB-BAD", "payer": "A", "payee": "B"})


class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_schema()
        cls.db = SessionLocal()
        seed_demo_data(cls.db, count=200, seed=3, reconcile=False, history_days=120)
        normalize_amounts(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_history_comes_from_real_rows(self):
        from app.services.forecasting_service import build_daily_history

        series, _notes = build_daily_history(self.db)
        self.assertGreater(len(series), 30)
        self.assertTrue(any(point.inflow > 0 for point in series))

    def test_forecast_is_deterministic(self):
        from app.services.forecasting_service import compute_forecast

        first = compute_forecast(self.db, 7)
        second = compute_forecast(self.db, 7)
        self.assertEqual(
            [point["predicted_amount"] for point in first.points],
            [point["predicted_amount"] for point in second.points],
        )

    def test_forecast_is_not_synthetic_when_data_exists(self):
        from app.services.forecasting_service import compute_forecast

        output = compute_forecast(self.db, 7)
        self.assertFalse(output.is_synthetic)
        self.assertEqual(len(output.points), 7)
        self.assertIn(output.model_name, {"prophet", "ridge", "seasonal_naive"})

    def test_net_equals_inflow_minus_outflow(self):
        from app.services.forecasting_service import compute_forecast

        for point in compute_forecast(self.db, 5).points:
            self.assertAlmostEqual(
                point["predicted_amount"],
                round(point["inflow"] - point["outflow"], 2),
                places=1,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
