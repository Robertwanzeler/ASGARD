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


if __name__ == "__main__":
    unittest.main()
