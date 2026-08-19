#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from apps.app3_veicular.backend.services import VehicleStateStore, evaluate_vehicle_sla


class App3ServicesTestCase(unittest.TestCase):
    def test_evaluate_vehicle_sla_warning(self):
        result = evaluate_vehicle_sla(
            {
                "total_vehicles": 5,
                "high_risk_vehicles": 0,
                "medium_risk_vehicles": 1,
                "degraded_autonomy_vehicles": 0,
                "max_latency_ms": 60.0,
                "max_packet_loss_percent": 0.1,
            }
        )
        self.assertEqual(result["runtime_status"], "warning")

    def test_vehicle_store_handles_missing_metrics(self):
        with tempfile.TemporaryDirectory(prefix="greenran_app3_test_") as tmp_dir:
            store = VehicleStateStore(Path(tmp_dir))
            vehicles = store.list_vehicles()
            self.assertEqual(vehicles, [])
            snapshot = store.refresh_snapshot()
            self.assertEqual(snapshot["vehicles"]["total_vehicles"], 0)


if __name__ == "__main__":
    unittest.main()
