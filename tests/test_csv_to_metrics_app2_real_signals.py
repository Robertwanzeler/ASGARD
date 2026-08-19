import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from csv_to_metrics import ExtendedMetricsCollector


class CsvToMetricsApp2RealSignalsTests(unittest.TestCase):
    def test_app2_export_marks_sensor_connected_when_only_real_tx_signal_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            collector = ExtendedMetricsCollector(
                input_dir=str(tmp),
                output_file=str(tmp / "metrics.json"),
                extended_output_file=str(tmp / "extended_metrics.json"),
                poll_interval=1.0,
            )
            collector.app2_sensors_file = tmp / "sensors.json"
            collector.app2_snapshot_file = tmp / "snapshot.json"
            collector.device_role_map = {
                "9": {
                    "device_type": "sensor",
                    "sensor_type": "temperature",
                    "unit": "°C",
                    "nominal_value": 0.0,
                    "nominal_power_mw": 180.0,
                    "nominal_battery_percent": 100.0,
                    "nominal_rssi_dbm": -88.0,
                    "tx_interval_s": 10,
                    "mobility_profile": "stationary",
                }
            }
            collector.device_role_map_mtime = 1.0
            collector._refresh_device_role_map = lambda: None

            collector.export_app2_metrics(
                {
                    "timestamp_iso": "2026-07-04T23:10:00-03:00",
                    "global_metrics": {"global_packet_loss_rate": 0.001},
                    "ue_metrics": {
                        "9": {
                            "device_type": "sensor",
                            "has_latency_samples": False,
                            "latency_us": 0.0,
                            "tx_pdus": 10,
                            "rx_pdus": 0,
                            "throughput_kbps": 0.0,
                            "tx_throughput_kbps": 125.0,
                            "rx_throughput_kbps": 0.0,
                            "total_pdcp_throughput_kbps": 125.0,
                            "cu_up_throughput_kbps": 0.0,
                        }
                    },
                }
            )

            sensors = json.loads((tmp / "sensors.json").read_text())
            snapshot = json.loads((tmp / "snapshot.json").read_text())

            self.assertEqual(sensors[0]["status"], "ok")
            self.assertGreater(snapshot["network"]["network_utilization_percent"], 0.0)
            self.assertEqual(snapshot["sensors"]["connected"], 1)


if __name__ == "__main__":
    unittest.main()
