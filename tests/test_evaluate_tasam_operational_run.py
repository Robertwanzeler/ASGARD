import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_tasam_operational_run import _decision_integrity, _stage_counts


class TestEvaluateTasamOperationalRun(unittest.TestCase):
    def test_joint_selected_proposal_is_validated_by_its_own_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            connection = sqlite3.connect(run_dir / "rapp_data_lake.db")
            connection.execute(
                "create table decisions_history ("
                "id integer primary key, selected_assistant text, selected_proposal_id text, "
                "selected_proposal_json text, proposal_applied_exactly integer, "
                "tasam_proposal_present integer, tasam_proposal_valid integer, "
                "ta_sam_actuation_applied integer, armd_enabled integer, "
                "advisor_arbitration_present integer, advisor_proposal_pair_complete integer, "
                "collection_event_stage_name text)"
            )
            proposal_id = "joint:armd:a:tasam:t"
            connection.execute(
                "insert into decisions_history values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "joint", proposal_id, json.dumps({
                    "source": "joint", "proposal_id": proposal_id,
                    "valid": True, "available": True,
                }), 1, 1, 1, 1, 1, 1, 1, "vehicle_conditional"),
            )
            connection.commit()
            connection.close()
            integrity = _decision_integrity(run_dir)
            stages = _stage_counts(run_dir)
        self.assertEqual(integrity["exact_proposal_json_matches"], 1)
        self.assertEqual(stages, {"vehicle_conditional": 1})


if __name__ == "__main__":
    unittest.main()
