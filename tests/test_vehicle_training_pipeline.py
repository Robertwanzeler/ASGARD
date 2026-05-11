#!/usr/bin/env python3

import importlib.util
import sys
import unittest
from pathlib import Path


class VehicleTrainingPipelineTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.src_dir = cls.project_root / "src"
        if str(cls.src_dir) not in sys.path:
            sys.path.insert(0, str(cls.src_dir))
        cls.export_module = cls._load_module(
            "export_conflict_dataset_vehicle_test",
            "scripts/export_conflict_dataset.py",
        )

    @classmethod
    def _load_module(cls, module_name: str, relative_path: str):
        module_path = cls.project_root / relative_path
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def test_vehicle_focus_keeps_only_vehicular_rows(self):
        rows = [
            {
                "affected_service": "App3-Veicular",
                "affected_kpi": "vehicle_high_risk_count",
                "parameter": "vehicle_priority_policy",
                "source_agent": "xApp-VehicleSafety",
                "target_agent": "App3-Veicular",
                "total_active_vehicles": "5",
                "ego_vehicle_count": "1",
                "vehicle_high_risk_count": "1",
                "vehicle_medium_risk_count": "0",
                "vehicle_degraded_autonomy_count": "1",
                "vehicle_max_latency_ms": "110.0",
                "vehicle_max_packet_loss_percent": "4.5",
                "vehicle_avg_speed_mps": "9.0",
            },
            {
                "affected_service": "App1-Vigilancia",
                "affected_kpi": "camera_latency_ms",
                "parameter": "slice_allocation",
                "source_agent": "xApp-Slicer",
                "target_agent": "App1-Vigilancia",
                "total_active_vehicles": "0",
                "ego_vehicle_count": "0",
                "vehicle_high_risk_count": "0",
                "vehicle_medium_risk_count": "0",
                "vehicle_degraded_autonomy_count": "0",
                "vehicle_max_latency_ms": "0.0",
                "vehicle_max_packet_loss_percent": "0.0",
                "vehicle_avg_speed_mps": "0.0",
            },
        ]

        filtered = self.export_module.filter_rows(rows, "vehicle")

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["affected_service"], "App3-Veicular")

    def test_vehicle_row_rejects_non_vehicle_conflict_with_vehicle_context(self):
        row = {
            "affected_service": "App2-Monitoramento",
            "affected_kpi": "sensor_latency_ms",
            "parameter": "global_health_policy",
            "source_agent": "rApp-CVaR/ML-Arbiter",
            "target_agent": "App2-Monitoramento",
            "total_active_vehicles": "3",
            "ego_vehicle_count": "1",
            "vehicle_high_risk_count": "0",
            "vehicle_medium_risk_count": "1",
            "vehicle_degraded_autonomy_count": "0",
            "vehicle_max_latency_ms": "58.0",
            "vehicle_max_packet_loss_percent": "1.0",
            "vehicle_avg_speed_mps": "11.0",
        }

        self.assertFalse(self.export_module.is_vehicle_row(row))


if __name__ == "__main__":
    unittest.main()
