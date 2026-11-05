import os,sys,unittest
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1])); os.environ["DATABASE_URL"]="sqlite:///./test_reconciliation.db"
from app.database.db import Base,SessionLocal,engine
from app.models.reconciliation import Anomaly, Reconciliation
from app.services.reconciliation_service import _find_unusual_amounts, _score_unique_names, seed_demo_data
from app.services.fx_service import convert_to_inr
from rapidfuzz import fuzz, process
class ReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    def test_seeded_demo_reconciles_and_flags_exceptions(self):
        db=SessionLocal()
        try:
            result=seed_demo_data(db)
            self.assertIn("successfully seeded", result["message"])
            self.assertEqual(db.query(Reconciliation).count(), 150)
            self.assertGreaterEqual(db.query(Anomaly).count(), 1)
        finally: db.close()
    def test_unique_name_scoring_preserves_fuzzy_scores(self):
        queries = ["acme corporation", "apex retailers", "acme corporation"]
        choices = ["acme corp", "apex retailers ltd"]
        scores, query_indices = _score_unique_names(queries, choices)
        expected = process.cdist(
            queries, choices, scorer=fuzz.token_sort_ratio, dtype="float64"
        )
        self.assertEqual(scores.shape, (2, 2))
        for index, query in enumerate(queries):
            self.assertEqual(
                scores[query_indices[query]].tolist(), expected[index].tolist()
            )
    def test_forecast_currency_conversion_targets_inr(self):
        self.assertEqual(convert_to_inr(1, "USD"), 83.5)
        self.assertEqual(convert_to_inr(83.5, "INR"), 83.5)
    def test_unusual_amount_detection_is_currency_scoped_and_conservative(self):
        transactions = [
            SimpleNamespace(
                transaction_id=f"TX-{index}",
                currency="USD",
                amount=100 + index,
                payer="Regular payer",
                payee="Regular payee",
            )
            for index in range(30)
        ]
        transactions.append(SimpleNamespace(
            transaction_id="TX-OUTLIER",
            currency="USD",
            amount=1_000_000,
            payer="Rare payer",
            payee="Rare payee",
        ))
        transactions.extend(
            SimpleNamespace(
                transaction_id=f"EUR-{index}",
                currency="EUR",
                amount=amount,
                payer="EUR payer",
                payee="EUR payee",
            )
            for index, amount in enumerate([1, 100000])
        )
        unusual = _find_unusual_amounts(transactions)
        self.assertIn("TX-OUTLIER", unusual)
        self.assertEqual(unusual["TX-OUTLIER"][0], "USD")
        self.assertNotIn("EUR-0", unusual)
        self.assertNotIn("EUR-1", unusual)

    def test_reconciliation_creates_unusual_amount_anomaly(self):
        db = SessionLocal()
        try:
            seed_demo_data(db)
            from app.models.transaction import Transaction
            db.add(Transaction(
                transaction_id="TX-UNUSUAL-AMOUNT",
                reference_id="REF-UNUSUAL-AMOUNT",
                payer="Anomaly Test Sender",
                payee="Anomaly Test Recipient",
                payer_fsp="BankA",
                payee_fsp="BankB",
                amount=1_000_000_000,
                currency="USD",
            ))
            db.commit()
            from app.services.reconciliation_service import run_reconciliation
            run_reconciliation(db)
            anomaly = db.query(Anomaly).filter(
                Anomaly.transaction_id == "TX-UNUSUAL-AMOUNT",
                Anomaly.anomaly_type == "unusual_amount",
            ).first()
            self.assertIsNotNone(anomaly)
            self.assertIn("Isolation Forest flagged", anomaly.explanation)
        finally:
            db.close()
if __name__=="__main__": unittest.main()
