import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from network_quality import evaluate_network_quality  # noqa: E402


class TestNetworkQuality(unittest.TestCase):
    def test_evaluate_network_quality_scores_real_only_good_snapshot(self):
        metrics = {
            "global_metrics": {
                "collector_mode": "pdcp_real",
                "real_latency_sample_count": 12,
                "proxy_latency_sample_count": 0,
                "total_active_cameras": 2,
                "total_active_sensors": 3,
                "total_active_vehicles": 1,
                "global_packet_loss_rate": 0.002,
                "throughput_kbps": 54000.0,
                "latency_p95_us": 6500.0,
                "cvar_per_ue_us": 7200.0,
            },
            "ue_metrics": {
                "1": {
                    "device_type": "camera",
                    "has_latency_samples": True,
                    "latency_us": 62000.0,
                    "throughput_kbps": 36000.0,
                    "rx_throughput_kbps": 36000.0,
                    "tx_throughput_kbps": 38000.0,
                    "total_pdcp_throughput_kbps": 37000.0,
                },
                "2": {
                    "device_type": "camera",
                    "has_latency_samples": True,
                    "latency_us": 68000.0,
                    "throughput_kbps": 34000.0,
                    "rx_throughput_kbps": 34000.0,
                    "tx_throughput_kbps": 35000.0,
                    "total_pdcp_throughput_kbps": 34500.0,
                },
                "3": {
                    "device_type": "vehicle",
                    "has_latency_samples": True,
                    "latency_us": 8500.0,
                    "packet_loss_percent": 0.4,
                    "throughput_kbps": 2400.0,
                },
            },
        }

        quality = evaluate_network_quality(metrics)

        self.assertTrue(quality["available"])
        self.assertTrue(quality["real_only"])
        self.assertGreaterEqual(quality["score"], 75.0)
        self.assertIn(quality["label"], {"BOA", "EXCELENTE"})
        self.assertEqual(quality["camera_status"], "OK")
        self.assertEqual(quality["vehicle_status"], "OK")
        self.assertEqual(quality["sensor_status"], "OK")

    def test_evaluate_network_quality_penalizes_bad_snapshot(self):
        metrics = {
            "global_metrics": {
                "collector_mode": "pdcp_real",
                "real_latency_sample_count": 4,
                "proxy_latency_sample_count": 0,
                "total_active_cameras": 1,
                "total_active_sensors": 0,
                "total_active_vehicles": 1,
                "global_packet_loss_rate": 0.12,
                "throughput_kbps": 4000.0,
                "latency_p95_us": 180000.0,
                "cvar_per_ue_us": 220000.0,
            },
            "ue_metrics": {
                "1": {
                    "device_type": "camera",
                    "has_latency_samples": False,
                    "latency_us": 0.0,
                    "throughput_kbps": 0.0,
                    "rx_throughput_kbps": 0.0,
                    "tx_throughput_kbps": 0.0,
                    "total_pdcp_throughput_kbps": 0.0,
                },
                "2": {
                    "device_type": "vehicle",
                    "has_latency_samples": True,
                    "latency_us": 150000.0,
                    "packet_loss_percent": 6.0,
                    "throughput_kbps": 0.0,
                },
            },
        }

        quality = evaluate_network_quality(metrics)

        self.assertTrue(quality["available"])
        self.assertTrue(quality["real_only"])
        self.assertLess(quality["score"], 60.0)
        self.assertEqual(quality["label"], "CRITICA")
        self.assertEqual(quality["camera_status"], "CRITICA")
        self.assertEqual(quality["sensor_status"], "INATIVA")
        self.assertEqual(quality["vehicle_status"], "CRITICA")
        self.assertTrue(any("câmeras" in reason for reason in quality["reasons"]))


if __name__ == "__main__":
    unittest.main()
