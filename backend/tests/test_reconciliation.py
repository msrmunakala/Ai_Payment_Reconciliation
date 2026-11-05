import os,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1])); os.environ["DATABASE_URL"]="sqlite:///./test_reconciliation.db"
from app.database.db import Base,SessionLocal,engine
from app.models.reconciliation import Anomaly, Reconciliation
from app.services.reconciliation_service import _score_unique_names, seed_demo_data
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
if __name__=="__main__": unittest.main()
