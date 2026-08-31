import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.consume_per_ue_policy import consume_once
from src.rapp_data_lake import DataLake


def _policy(allocated=0.25):
    return {
        "policy_id": "slice_test_1",
        "per_ue_resource_policy": {
            "policy_id": "slice_test_1",
            "application_status": "pending_ack",
            "r_ran": allocated,
            "r_ai": 0.0,
            "floor_violation_count": 0,
            "allocations": [
                {
                    "ue_id": "camera-1",
                    "service": "camera",
                    "domain": "ran",
                    "floor_share": 0.10,
                    "allocated_share": allocated,
                    "floor_met": True,
                }
            ],
        },
    }


class TestPerUePolicyAck(unittest.TestCase):
    def test_consumer_acknowledges_and_updates_datalake(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            policies = state / "rapp_policies"
            policies.mkdir()
            (policies / "slice_policy.json").write_text(json.dumps(_policy()), encoding="utf-8")

            lake = DataLake(str(state / "rapp_data_lake.db"))
            lake.record_resource_allocation_snapshot(
                {
                    "per_ue_policy_id": "slice_test_1",
                    "per_ue_application_status": "pending_ack",
                    "per_ue_allocation": _policy()["per_ue_resource_policy"]["allocations"],
                    "per_ue_floor": [],
                    "floor_feasible": True,
                    "r_ran": 0.25,
                    "r_ai": 0.0,
                    "allocation_state": "ALLOWED",
                },
                timestamp=123,
            )
            lake.conn.close()

            result = consume_once(state)
            self.assertEqual(result["status"], "acknowledged")
            ack = json.loads((policies / "slice_policy_ack.json").read_text())
            self.assertTrue(ack["acknowledged"])

            conn = sqlite3.connect(str(state / "rapp_data_lake.db"))
            row = conn.execute(
                "SELECT per_ue_application_status, per_ue_ack_reason FROM resource_allocation_history"
            ).fetchone()
            conn.close()
            self.assertEqual(row[0], "acknowledged")
            self.assertIn("pisos", row[1])

    def test_consumer_rejects_floor_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            policies = state / "rapp_policies"
            policies.mkdir()
            payload = _policy(allocated=0.05)
            payload["per_ue_resource_policy"]["floor_violation_count"] = 1
            (policies / "slice_policy.json").write_text(json.dumps(payload), encoding="utf-8")

            result = consume_once(state)
            self.assertEqual(result["status"], "rejected")
            ack = json.loads((policies / "slice_policy_ack.json").read_text())
            self.assertFalse(ack["acknowledged"])
            self.assertIn("violação", ack["reason"])


if __name__ == "__main__":
    unittest.main()
