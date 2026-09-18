import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapp_orchestrator import RappResourceOptimizer


class RappOrchestratorCameraMetricsTests(unittest.TestCase):
    def test_economic_projected_power_uses_actuator_level(self):
        optimizer = RappResourceOptimizer.__new__(RappResourceOptimizer)

        self.assertEqual(optimizer._canonical_energy_command_power(50.0), 60.0)
        self.assertEqual(optimizer._canonical_energy_command_power(25.0), 25.0)
        self.assertEqual(optimizer._canonical_energy_command_power(100.0), 100.0)

    def test_camera_sla_uses_best_real_throughput_signal_when_rx_is_zero(self):
        optimizer = RappResourceOptimizer.__new__(RappResourceOptimizer)
        optimizer.interval = 1
        optimizer.read_extended_metrics = lambda: {
            "sim_time_range": {"end": 7.9},
            "ue_metrics": {
                "1": {
                    "device_type": "camera",
                    "latency_us": 0.0,
                    "has_latency_samples": False,
                    "packet_count": 0,
                    "rx_bytes": 0,
                    "tx_bytes": 53650980,
                    "throughput_kbps": 0.0,
                    "rx_throughput_kbps": 0.0,
                    "tx_throughput_kbps": 35471.72,
                    "total_pdcp_throughput_kbps": 35471.72,
                    "cu_up_throughput_kbps": 0.0,
                    "throughput_source": "pdcp_rx_window",
                },
                "2": {
                    "device_type": "camera",
                    "latency_us": 0.0,
                    "has_latency_samples": False,
                    "packet_count": 0,
                    "rx_bytes": 0,
                    "tx_bytes": 53000000,
                    "throughput_kbps": 0.0,
                    "rx_throughput_kbps": 0.0,
                    "tx_throughput_kbps": 33000.0,
                    "total_pdcp_throughput_kbps": 33000.0,
                    "cu_up_throughput_kbps": 0.0,
                    "throughput_source": "pdcp_rx_window",
                },
            },
        }

        metrics = optimizer._get_camera_sla_metrics()

        self.assertEqual(metrics["active_cameras"], 2)
        self.assertEqual(metrics["observed_cameras"], 2)
        self.assertTrue(metrics["throughput_ready"])
        self.assertAlmostEqual(metrics["throughput_mbps"], 33.0, places=2)
        self.assertEqual(metrics["sim_time_s"], 7.9)


if __name__ == "__main__":
    unittest.main()
