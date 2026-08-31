import json
import tempfile
import unittest
from pathlib import Path

from src.rapp_a1_interface import A1PolicyInterface
from src.rapp_policy_consumer import load_current_policies


class TestPerUePolicyContract(unittest.TestCase):
    def test_a1_emits_per_ue_policy_and_consumer_reads_it(self):
        allocations = [
            {
                "ue_id": "camera-1",
                "service": "camera",
                "domain": "ran",
                "floor_share": 0.05,
                "allocated_share": 0.05,
                "reinforcement_share": 0.0,
                "floor_met": True,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            interface = A1PolicyInterface(policy_dir=tmp)
            policy = interface.send_slice_policy(
                "ALLOWED",
                per_ue_allocation=allocations,
            )
            self.assertEqual(policy["per_ue_resource_policy"]["application_status"], "pending_ack")
            self.assertEqual(policy["per_ue_resource_policy"]["allocation_count"], 1)
            loaded = load_current_policies(Path(tmp))
            summary = loaded["per_ue_resource"]
            self.assertTrue(summary["present"])
            self.assertEqual(summary["allocation_count"], 1)
            self.assertEqual(summary["floor_violation_count"], 0)
            self.assertEqual(summary["allocations"][0]["ue_id"], "camera-1")

            persisted = json.loads((Path(tmp) / "slice_policy.json").read_text())
            self.assertTrue(persisted["per_ue_resource_policy"]["ack_required"])


if __name__ == "__main__":
    unittest.main()
