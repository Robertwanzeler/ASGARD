import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.rapp_policy_source import RAppPolicySource
from rapp_orchestrator import RappResourceOptimizer


def _policy_payload():
    return {
        "schema": "greenran.rapp.policy.v1",
        "policy_id": "test-policy",
        "mode": "shadow_only",
        "data_source": "real_only",
        "arbitration": {
            "enabled": True,
            "tie_margin": 0.02,
            "low_confidence_margin": 0.70,
            "last_resort": {
                "energy": "armd",
                "resources": "ta_sam",
                "unresolved": "live_allocator",
            },
        },
        "network_policy": {"priorities": {"camera": 1}},
        "safety": {"allow_control": False, "use_proxy": False},
    }


class TestRAppPolicySource(unittest.TestCase):
    def test_loads_valid_policy_and_keeps_last_valid_on_invalid_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps(_policy_payload()), encoding="utf-8")
            source = RAppPolicySource(path)

            first = source.refresh()
            self.assertTrue(first["available"])
            self.assertEqual(first["policy_id"], "test-policy")

            path.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
            second = source.refresh()
            self.assertTrue(second["available"])
            self.assertEqual(second["policy_id"], "test-policy")
            self.assertIn("schema", second["error"])

    def test_rejects_control_and_proxy_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            payload = _policy_payload()
            payload["safety"]["allow_control"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            source = RAppPolicySource(path)
            result = source.refresh()
            self.assertFalse(result["available"])
            self.assertIn("cannot enable control", result["error"])

    def test_external_authority_defaults_to_last_resort(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps(_policy_payload()), encoding="utf-8")
            source = RAppPolicySource(path)

            result = source.refresh()

            self.assertTrue(result["available"])
            self.assertEqual(result["authority_kind"], "operator_editable_json")
            self.assertEqual(result["authority_precedence"], "last_resort_only")
            self.assertEqual(result["policy"]["authority"]["primary_decision_makers"], ["armd", "ta_sam"])

    def test_rejects_external_policy_that_can_precede_advisors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            payload = _policy_payload()
            payload["authority"] = {"precedence": "primary"}
            path.write_text(json.dumps(payload), encoding="utf-8")
            source = RAppPolicySource(path)

            result = source.refresh()

            self.assertFalse(result["available"])
            self.assertIn("last_resort_only", result["error"])


class TestRAppAdvisorArbitration(unittest.TestCase):
    def test_external_policy_is_used_only_for_energy_tie(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps(_policy_payload()), encoding="utf-8")
            with mock.patch.dict(os.environ, {"GREENRAN_RAPP_POLICY_FILE": str(path)}, clear=False):
                optimizer = object.__new__(RappResourceOptimizer)
                optimizer.armd_runtime = SimpleNamespace(min_confidence=0.85)
                optimizer.rapp_policy_source = RAppPolicySource()
                decision = {
                    "armd_analysis": {
                        "available": True,
                        "confidence": 0.80,
                        "expected_energy_saver": "CONDITIONAL",
                        "evidence": ["kpi"],
                    },
                    "tasam_advisor": {
                        "valid": True,
                        "confidence": 0.80,
                        "arbitration_score": 0.50,
                        "energy_advice": {"decision": "CONDITIONAL", "action": "MONITOR"},
                        "resource_advice": {"enabled": True, "score": 0.10},
                        "evidence_flags": {"ready": True},
                    },
                    "resource_allocation": {},
                }
                result = optimizer._build_advisor_arbitration(decision)

            self.assertEqual(result["energy_winner"], "ta_sam")
            self.assertEqual(result["resource_winner"], "ta_sam")
            self.assertFalse(result["energy_external_used"])
            self.assertFalse(result["would_apply"])

    def test_external_policy_resolves_valid_energy_tie(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            payload = _policy_payload()
            payload["arbitration"]["last_resort"]["energy"] = "armd"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.dict(os.environ, {"GREENRAN_RAPP_POLICY_FILE": str(path)}, clear=False):
                optimizer = object.__new__(RappResourceOptimizer)
                optimizer.armd_runtime = SimpleNamespace(min_confidence=0.50)
                optimizer.rapp_policy_source = RAppPolicySource()
                decision = {
                    "armd_analysis": {
                        "available": True,
                        "confidence": 0.80,
                        "expected_energy_saver": "CONDITIONAL",
                        "evidence": ["kpi"],
                    },
                    "tasam_advisor": {
                        "valid": True,
                        "confidence": 0.80,
                        "arbitration_score": 0.40,
                        "energy_advice": {"decision": "CONDITIONAL", "action": "MONITOR"},
                        "resource_advice": {"enabled": True, "score": 0.10},
                        "evidence_flags": {"ready": True},
                    },
                    "resource_allocation": {},
                }
                result = optimizer._build_advisor_arbitration(decision)

            self.assertTrue(result["external_policy_available"])
            self.assertTrue(result["energy_external_used"])
            self.assertEqual(result["energy_winner"], "armd")
            self.assertFalse(result["would_apply"])

    def test_arbitration_records_complete_assistant_pair_and_rapp_final(self):
        optimizer = object.__new__(RappResourceOptimizer)
        optimizer.armd_runtime = SimpleNamespace(min_confidence=0.85)
        decision = {
            "energy_saver": "ALLOWED",
            "action": "MONITOR",
            "armd_analysis": {
                "available": True,
                "proposal_present": True,
                "proposal_valid": True,
                "proposal_kind": "neutral_noop",
                "confidence": 0.0,
                "expected_energy_saver": "ALLOWED",
                "evidence": [],
            },
            "armd_proposal_present": True,
            "armd_proposal_valid": True,
            "armd_proposal_kind": "neutral_noop",
            "tasam_advisor": {
                "enabled": True,
                "valid": True,
                "confidence": 0.80,
                "energy_advice": {"decision": "ALLOWED", "action": "MONITOR"},
                "evidence_flags": {"ready": True},
            },
            "tasam_proposal_present": True,
            "tasam_proposal_valid": True,
            "tasam_proposal_kind": "checkpoint",
        }
        result = optimizer._build_advisor_arbitration(decision)

        self.assertTrue(result["arbitration_present"])
        self.assertTrue(result["proposal_pair_complete"])
        self.assertEqual(result["judge"], "rapp")
        self.assertEqual(result["rapp_final_decision"], "ALLOWED")


if __name__ == "__main__":
    unittest.main()
