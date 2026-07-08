import os
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rapp_rl_policy import HeuristicResourcePolicy, build_runtime_rl_policy


class RuntimeRLPolicySelectionTest(unittest.TestCase):
    def setUp(self):
        self.previous_policy = os.environ.pop("GREENRAN_RL_POLICY", None)

    def tearDown(self):
        if self.previous_policy is not None:
            os.environ["GREENRAN_RL_POLICY"] = self.previous_policy
        else:
            os.environ.pop("GREENRAN_RL_POLICY", None)

    def test_default_policy_is_heuristic_not_legacy(self):
        policy = build_runtime_rl_policy()
        self.assertIsInstance(policy, HeuristicResourcePolicy)
        self.assertEqual(policy.metadata.policy_id, "heuristic_resource_allocator")
        self.assertFalse(policy.metadata.legacy_runtime_compatible)

    def test_tasam_shadow_alias_keeps_heuristic_live_allocator(self):
        policy = build_runtime_rl_policy("ta_sam_shadow")
        self.assertIsInstance(policy, HeuristicResourcePolicy)
        result = policy.predict({"resource_allocation_baseline": {"r_ran": 0.7, "r_ai": 0.2}})
        self.assertEqual(result["final_decision"], "FALLBACK_HEURISTIC")
        self.assertEqual(result["resource_allocation"]["r_ran"], 0.7)

    def test_non_tasam_policy_names_are_rejected(self):
        for name in ("legacy", "legacy_a3c", "a3c", "eedrl", "sac", "awac"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "Deprecated GREENRAN_RL_POLICY"):
                    build_runtime_rl_policy(name)


if __name__ == "__main__":
    unittest.main()
