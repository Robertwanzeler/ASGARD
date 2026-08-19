#!/usr/bin/env python3

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


class App3DataLakeIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="greenran_app3_datalake_")
        os.environ["GREENRAN_STATE_DIR"] = self.temp_dir

        project_root = Path(__file__).resolve().parents[1]
        src_dir = project_root / "src"
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))

        for module_name in ["greenran_paths", "rapp_data_lake"]:
            if module_name in sys.modules:
                del sys.modules[module_name]

        from rapp_data_lake import DataLake

        self.DataLake = DataLake
        self.db_path = Path(self.temp_dir) / "app3_test.db"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("GREENRAN_STATE_DIR", None)

    def _sample_snapshot(self):
        return {
            "simulation": {
                "source": "carla_ns3_combined",
                "timestamp_iso": "2026-05-14 00:04:29",
            },
            "vehicles": {
                "total_vehicles": 5,
                "ego_present": True,
                "high_risk_vehicles": 1,
                "medium_risk_vehicles": 2,
                "degraded_autonomy_vehicles": 1,
                "max_latency_ms": 88.0,
                "max_packet_loss_percent": 3.4,
                "max_speed_mps": 11.2,
            },
            "network": {
                "max_latency_ms": 88.0,
                "max_packet_loss_percent": 3.4,
                "max_speed_mps": 11.2,
            },
            "sla": {
                "ready": True,
                "runtime_status": "warning",
                "proposal_status": "warning",
                "reason": "latência veicular 88ms >= 50ms",
            },
        }

    def test_record_app3_snapshot_persists_aggregated_vehicle_state(self):
        dl = self.DataLake(db_path=str(self.db_path))
        dl.record_app3_snapshot(self._sample_snapshot())

        cursor = dl.conn.cursor()
        row = cursor.execute(
            """
            SELECT total_vehicles, ego_present, high_risk_vehicles,
                   medium_risk_vehicles, degraded_autonomy_vehicles,
                   max_latency_ms, max_packet_loss_percent, max_speed_mps
            FROM app3_snapshots
            """
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["total_vehicles"], 5)
        self.assertEqual(row["ego_present"], 1)
        self.assertEqual(row["high_risk_vehicles"], 1)
        self.assertEqual(row["medium_risk_vehicles"], 2)
        self.assertEqual(row["degraded_autonomy_vehicles"], 1)
        self.assertEqual(row["max_latency_ms"], 88.0)
        self.assertEqual(row["max_packet_loss_percent"], 3.4)
        self.assertEqual(row["max_speed_mps"], 11.2)
        dl.close()
