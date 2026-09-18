import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_tasam_paired_campaign import (
    EXPECTED_STAGES,
    _feedback_requirement_satisfied,
    stage_counts,
)


class TestEvaluateTasamPairedCampaign(unittest.TestCase):
    def test_stage_counts_fall_back_to_decisions_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            connection = sqlite3.connect(run_dir / "rapp_data_lake.db")
            connection.execute(
                "create table decisions_history (id integer primary key, collection_event_stage_name text)"
            )
            connection.executemany(
                "insert into decisions_history(collection_event_stage_name) values (?)",
                [(stage,) for stage in EXPECTED_STAGES],
            )
            connection.commit()
            connection.close()
            counts = stage_counts(run_dir)
        self.assertEqual(set(counts), set(EXPECTED_STAGES))

    def test_feedback_contract_allows_initial_pending_decision(self):
        self.assertTrue(_feedback_requirement_satisfied("train_no_armd", 298, 297))
        self.assertTrue(_feedback_requirement_satisfied("combined", 296, 295))
        self.assertFalse(_feedback_requirement_satisfied("combined", 296, 294))

    def test_rapp_only_does_not_require_tasam_feedback(self):
        self.assertTrue(_feedback_requirement_satisfied("rapp_only", 301, 0))
        self.assertFalse(_feedback_requirement_satisfied("rapp_only", 0, 0))


if __name__ == "__main__":
    unittest.main()
