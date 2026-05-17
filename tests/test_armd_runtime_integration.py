import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapp_armd_runtime import ARMDRuntimeAdvisor
from rapp_data_lake import DataLake


class ARMDIntegrationTests(unittest.TestCase):
    def test_hybrid_summary_loads_frozen_scenarios(self):
        advisor = ARMDRuntimeAdvisor()
        self.assertTrue(advisor.loaded)
        self.assertEqual(advisor.threshold, 0.5)
        self.assertEqual(advisor.subset_size, 450)
        self.assertIn("vehicle_critical", advisor.scenarios)
        self.assertEqual(advisor.scenarios["vehicle_critical"].source, "protocol_vehicle_clean")

    def test_app1_throughput_is_classified_and_escalates(self):
        advisor = ARMDRuntimeAdvisor(mode="assist")
        decision = {"energy_saver": "ALLOWED", "action": "POWER_DOWN", "confidence": 0.4}
        advice = advisor.advise(
            decision=decision,
            camera_metrics={
                "active_cameras": 3,
                "throughput_ready": True,
                "throughput_mbps": 24.0,
                "latency_ms": 22.0,
            },
            vehicle_metrics={},
            app2_metrics={},
            network_health={"cvar_us": 20000, "p95_us": 30000},
        )
        self.assertEqual(advice["scenario"], "app1_throughput")
        self.assertEqual(advice["expected_energy_saver"], "BLOCKED")
        updated = advisor.apply(decision, advice)
        self.assertEqual(updated["energy_saver"], "BLOCKED")
        self.assertTrue(updated["armd_override_applied"])

    def test_vehicle_warning_under_healthy_global_is_promoted_to_implicito(self):
        advisor = ARMDRuntimeAdvisor(mode="assist")
        advice = advisor.advise(
            decision={"energy_saver": "CONDITIONAL", "action": "FULL_POWER_GUARD"},
            camera_metrics={},
            vehicle_metrics={
                "available": True,
                "medium_risk_vehicles": 1,
                "high_risk_vehicles": 0,
                "degraded_autonomy_vehicles": 0,
                "max_latency_ms": 58.0,
                "max_packet_loss_percent": 2.6,
            },
            app2_metrics={},
            network_health={"cvar_us": 18000, "p95_us": 25000},
        )
        self.assertEqual(advice["scenario"], "vehicle_implicito")
        self.assertEqual(advice["expected_energy_saver"], "CONDITIONAL")

    def test_data_lake_exposes_armd_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "armd_test.db"
            lake = DataLake(db_path=str(db_path))
            columns = {
                row[1] for row in lake.conn.execute("PRAGMA table_info(decisions_history)").fetchall()
            }
            self.assertIn("armd_scenario", columns)
            self.assertIn("armd_source", columns)
            self.assertIn("armd_override_applied", columns)


if __name__ == "__main__":
    unittest.main()
