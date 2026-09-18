import os
import unittest

from scripts.pretrain_tasam_category_curriculum import build_curriculum_records
from src.greenran_marl_topology import build_du_state_snapshot
from src.rapp_sac_resource_model import compute_shared_resource_snapshot, infer_allocation_state


class TestTasamStateCategoryAlignment(unittest.TestCase):
    def setUp(self):
        self.config = {
            "camera_throughput_target_mbps": 25.0,
            "camera_throughput_guard_mbps": 30.0,
            "camera_latency_target_ms": 100.0,
            "camera_latency_warning_ms": 80.0,
            "vehicle_latency_target_ms": 20.0,
            "vehicle_latency_warning_ms": 10.0,
            "vehicle_loss_target_pct": 1.0,
            "vehicle_loss_warning_pct": 0.5,
            "app2_latency_target_ms": 1000.0,
            "cvar_target_ms": 120.0,
            "resource_cvar_critical_ms": 250.0,
            "p95_target_ms": 80.0,
            "resource_p95_critical_ms": 120.0,
        }

    def test_previous_blocked_allocation_does_not_contaminate_healthy_context(self):
        camera = {"active_cameras": 3, "throughput_mbps": 35.0, "throughput_ready": True, "latency_ms": 20.0}
        app2 = {
            "total_sensors": 25,
            "connected_ratio": 1.0,
            "delivery_success_percent": 99.0,
            "packet_loss_percent": 1.0,
            "avg_latency_ms": 100.0,
            "avg_battery_percent": 80.0,
            "low_battery_sensors": 0,
            "error_sensors": 0,
            "error_ratio": 0.0,
        }
        vehicle = {"total_vehicles": 5, "max_latency_ms": 5.0, "max_packet_loss_percent": 0.1}
        health = {"cvar_us": 20_000.0, "p95_us": 30_000.0}
        self.assertEqual(infer_allocation_state(camera, app2, vehicle, health, self.config), "ALLOWED")
        os.environ["GREENRAN_TASAM_EXPLICIT_STATE_FEATURE"] = "1"
        try:
            state = build_du_state_snapshot(camera, app2, vehicle, health, {"allocation_state": "BLOCKED"}, operating_state="ALLOWED")
        finally:
            os.environ.pop("GREENRAN_TASAM_EXPLICIT_STATE_FEATURE", None)
        self.assertEqual(state["global_state"]["state_vector"][-3:], [1.0, 0.0, 0.0])
        self.assertEqual(state["global_state"]["state_category"], "ALLOWED")

    def test_app2_warning_and_critical_are_visible_to_category_context(self):
        camera = {"active_cameras": 3, "throughput_mbps": 35.0, "throughput_ready": True, "latency_ms": 20.0}
        vehicle = {"total_vehicles": 5, "max_latency_ms": 5.0, "max_packet_loss_percent": 0.1}
        health = {"cvar_us": 20_000.0, "p95_us": 30_000.0}
        warning = {"total_sensors": 25, "connected_ratio": 24 / 25, "delivery_success_percent": 96.0, "packet_loss_percent": 5.5, "avg_latency_ms": 540.0, "avg_battery_percent": 68.0, "low_battery_sensors": 1, "error_sensors": 1, "error_ratio": 1 / 25}
        critical = {**warning, "connected_ratio": 20 / 25, "delivery_success_percent": 88.0, "packet_loss_percent": 8.0, "avg_latency_ms": 760.0, "error_sensors": 5, "error_ratio": 5 / 25}
        self.assertEqual(infer_allocation_state(camera, warning, vehicle, health, self.config), "CONDITIONAL")
        self.assertEqual(infer_allocation_state(camera, critical, vehicle, health, self.config), "BLOCKED")

    def test_curriculum_uses_runtime_thresholds_for_all_stages(self):
        train, validation = build_curriculum_records(samples_per_stage=5, seed=47)
        records = train + validation
        self.assertEqual(len(records), 45)
        self.assertEqual({row["stage_name"] for row in records}, {
            "allowed_bootstrap", "allowed_stable", "camera_conditional", "camera_blocked",
            "vehicle_conditional", "vehicle_blocked", "app2_conditional", "app2_blocked",
            "allowed_recovery",
        })
        self.assertTrue(all(row["state_category_source"] == "current_network_metrics" for row in records))
        self.assertTrue(all(len(row["global_state"]) == 13 for row in records))


if __name__ == "__main__":
    unittest.main()
