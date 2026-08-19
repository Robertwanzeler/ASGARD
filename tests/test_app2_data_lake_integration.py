#!/usr/bin/env python3

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


class App2DataLakeIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="greenran_app2_datalake_")
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
        self.db_path = Path(self.temp_dir) / "app2_test.db"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("GREENRAN_STATE_DIR", None)

    def _sample_snapshot(self):
        return {
            "timestamp": "2026-04-28T12:34:56",
            "sensors": {
                "total": 3,
                "active": 2,
                "connected": 2,
                "error": 1,
                "low_battery": 1,
                "gateways": ["GW-5G-01", "GW-LORA-01"],
                "connectivity_modes": ["5g_redcap", "lora_fwa"],
            },
            "readings": {
                "avg_temperature_c": 28.4,
                "avg_humidity_percent": 76.2,
                "avg_soil_conductivity": 1.3,
                "avg_battery_percent": 61.5,
                "avg_power_mw": 132.4,
            },
            "network": {
                "packet_loss_percent": 3.4,
                "tx_packets": 120,
                "rx_packets": 114,
                "lost_packets": 6,
                "avg_latency_ms": 88.5,
                "avg_rssi_dbm": -92.1,
                "network_utilization_percent": 41.7,
                "delivery_success_percent": 95.0,
            },
            "alerts": [
                {"level": "warning", "message": "Packet loss elevado"},
            ],
        }

    def _sample_sensors(self):
        return [
            {
                "sensor_id": 1,
                "type": "temperature",
                "unit": "C",
                "value": 30.2,
                "status": "ok",
                "domain": "environmental",
                "connectivity": "5g_redcap",
                "gateway_id": "GW-5G-01",
                "battery_percent": 84.0,
                "latency_ms": 64.0,
                "packet_loss_percent": 1.4,
                "rssi_dbm": -80.0,
                "power_mw": 160.0,
                "tx_interval_s": 5,
                "packets_tx": 22,
            },
            {
                "sensor_id": 2,
                "type": "soil_moisture",
                "unit": "%",
                "value": 47.5,
                "status": "error",
                "domain": "soil",
                "connectivity": "lora_fwa",
                "gateway_id": "GW-LORA-01",
                "battery_percent": 12.0,
                "latency_ms": 220.0,
                "packet_loss_percent": 7.2,
                "rssi_dbm": -110.0,
                "power_mw": 48.0,
                "tx_interval_s": 30,
                "packets_tx": 8,
            },
        ]

    def test_record_app2_snapshot_persists_snapshot_and_sensor_rows(self):
        dl = self.DataLake(db_path=str(self.db_path))
        dl.record_app2_snapshot(self._sample_snapshot(), self._sample_sensors())

        latest = dl.get_latest_app2_snapshot()
        self.assertEqual(latest["sensors"]["total"], 3)
        self.assertEqual(latest["sensors"]["connected"], 2)
        self.assertEqual(latest["network"]["packet_loss_percent"], 3.4)
        self.assertEqual(len(latest["alerts"]), 1)

        cursor = dl.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM app2_snapshots")
        self.assertEqual(cursor.fetchone()[0], 1)
        cursor.execute("SELECT COUNT(*) FROM app2_sensor_readings")
        self.assertEqual(cursor.fetchone()[0], 2)
        dl.close()

    def test_database_stats_include_app2_counts(self):
        dl = self.DataLake(db_path=str(self.db_path))
        dl.record_app2_snapshot(self._sample_snapshot(), self._sample_sensors())

        stats = dl.get_database_stats()
        self.assertEqual(stats["app2_snapshots_count"], 1)
        self.assertEqual(stats["app2_sensor_readings_count"], 2)
        dl.close()

    def test_app2_report_and_history_are_derived_from_snapshots(self):
        dl = self.DataLake(db_path=str(self.db_path))
        dl.record_app2_snapshot(self._sample_snapshot(), self._sample_sensors())

        history = dl.get_recent_app2_snapshots(hours=24, limit=10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["connected_sensors"], 2)

        report = dl.get_app2_report(hours=24)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["samples"], 1)
        self.assertEqual(report["network"]["avg_packet_loss_percent"], 3.4)
        dl.close()


if __name__ == "__main__":
    unittest.main()
