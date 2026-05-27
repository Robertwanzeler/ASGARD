import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapp_a1_interface import A1PolicyInterface
from rapp_alerts import AlertManager
from rapp_policy_consumer import load_current_policies, summarize_armd_policy


class ARMDPolicyConsumerTests(unittest.TestCase):
    def test_summarize_armd_policy_detects_critical_energy_context(self):
        energy_policy = {
            "decision": {"energy_saver": "BLOCKED"},
            "armd": {
                "scenario": "app1_throughput",
                "domain": "app1",
                "source": "protocol",
                "confidence": 1.0,
                "override_applied": False,
            },
        }
        summary = summarize_armd_policy(energy_policy, {})
        self.assertTrue(summary["present"])
        self.assertEqual(summary["scenario"], "app1_throughput")
        self.assertEqual(summary["attention_level"], "critical")
        self.assertEqual(summary["policy_scope"], ["energy"])

    def test_load_current_policies_extracts_armd_block(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            a1 = A1PolicyInterface(policy_dir=tmpdir)
            a1.send_energy_policy(
                {
                    "energy_saver": "BLOCKED",
                    "action": "FULL_POWER",
                    "reason": "ARMD(app1_throughput)",
                    "confidence": 1.0,
                    "armd_enabled": True,
                    "armd_mode": "assist",
                    "armd_scenario": "app1_throughput",
                    "armd_domain": "app1",
                    "armd_source": "protocol",
                    "armd_confidence": 1.0,
                    "armd_override_applied": False,
                    "armd_expected_energy_saver": "BLOCKED",
                }
            )
            current = load_current_policies(Path(tmpdir))
            self.assertEqual(current["armd"]["scenario"], "app1_throughput")
            self.assertEqual(current["armd"]["attention_level"], "critical")

    def test_alert_manager_generates_armd_attention_alert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policy_dir = Path(tmpdir) / "policies"
            policy_dir.mkdir(parents=True, exist_ok=True)
            a1 = A1PolicyInterface(policy_dir=str(policy_dir))
            a1.send_energy_policy(
                {
                    "energy_saver": "BLOCKED",
                    "action": "FULL_POWER",
                    "reason": "ARMD(vehicle_critical)",
                    "confidence": 1.0,
                    "armd_enabled": True,
                    "armd_mode": "assist",
                    "armd_scenario": "vehicle_critical",
                    "armd_domain": "app3",
                    "armd_source": "protocol",
                    "armd_confidence": 1.0,
                    "armd_override_applied": True,
                    "armd_expected_energy_saver": "BLOCKED",
                }
            )
            manager = AlertManager(
                alert_log=str(Path(tmpdir) / "alerts.log"),
                policy_dir=policy_dir,
            )
            alerts = manager.check_armd_policy_attention()
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["type"], "ARMD_POLICY_ATTENTION")


if __name__ == "__main__":
    unittest.main()
