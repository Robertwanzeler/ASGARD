import unittest
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vehicle_policy_runtime import build_vehicle_intent, evaluate_vehicle_policy


class VehiclePolicyRuntimeTests(unittest.TestCase):
    def test_critical_policy_maps_to_vehicle_intent(self):
        metrics = {
            "available": True,
            "total_vehicles": 5,
            "ego_present": True,
            "high_risk_vehicles": 1,
            "medium_risk_vehicles": 0,
            "degraded_autonomy_vehicles": 0,
            "max_latency_ms": 126.0,
            "max_packet_loss_percent": 5.8,
            "max_speed_mps": 6.8,
        }
        policy = evaluate_vehicle_policy(metrics)
        intent = build_vehicle_intent(metrics, policy)
        self.assertEqual(policy["severity"], "critical")
        self.assertEqual(intent["STATE"], "CRITICAL")
        self.assertEqual(intent["ACTION"], "FULL_POWER")
        self.assertEqual(intent["HIGH_RISK_VEHICLES"], 1)

    def test_warning_policy_maps_to_guard_intent(self):
        metrics = {
            "available": True,
            "total_vehicles": 5,
            "ego_present": True,
            "high_risk_vehicles": 0,
            "medium_risk_vehicles": 1,
            "degraded_autonomy_vehicles": 0,
            "max_latency_ms": 12.0,
            "max_packet_loss_percent": 0.6,
            "max_speed_mps": 6.8,
        }
        policy = evaluate_vehicle_policy(metrics)
        intent = build_vehicle_intent(metrics, policy)
        self.assertEqual(policy["severity"], "warning")
        self.assertEqual(intent["STATE"], "WARNING")
        self.assertEqual(intent["ACTION"], "FULL_POWER_GUARD")

    def test_short_real_pdcp_window_is_unknown_not_hard_violation(self):
        policy = evaluate_vehicle_policy({
            "available": True,
            "total_vehicles": 5,
            "ego_present": True,
            "max_latency_ms": 5.0,
            "max_packet_loss_percent": 4.3,
            "min_tx_pdus": 23,
            "min_rx_pdus": 22,
            "min_sample_window_s": 0.2,
            "vehicles": [
                {"tx_pdus": 23, "rx_pdus": 22, "sample_window_s": 0.2}
                for _ in range(5)
            ],
        })
        self.assertEqual(policy["severity"], "unknown")
        self.assertEqual(policy["violation"], "VEHICLE_WARMUP")
        self.assertFalse(policy["sla_violated"])
        self.assertFalse(policy["economic_replay_eligible"])

    def test_mature_real_pdcp_loss_still_enforces_vehicle_sla(self):
        policy = evaluate_vehicle_policy({
            "available": True,
            "total_vehicles": 5,
            "ego_present": True,
            "max_latency_ms": 5.0,
            "max_packet_loss_percent": 1.2,
            "min_tx_pdus": 120,
            "min_rx_pdus": 118,
            "min_sample_window_s": 1.2,
            "vehicles": [
                {"tx_pdus": 120, "rx_pdus": 118, "sample_window_s": 1.2}
                for _ in range(5)
            ],
        })
        self.assertEqual(policy["severity"], "critical")
        self.assertTrue(policy["sla_violated"])


if __name__ == "__main__":
    unittest.main()
