import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from csv_to_metrics import ExtendedMetricsCollector
from greenran_paths import get_fixed_vehicle_base_imsi


class TestCsvToMetricsProxyLatency(unittest.TestCase):
    def _collector(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        base = Path(temp_dir.name)
        collector = ExtendedMetricsCollector(
            input_dir=str(base / "ns3"),
            output_file=str(base / "metrics.json"),
            extended_output_file=str(base / "extended_metrics.json"),
            poll_interval=1.0,
        )
        collector.scenario_control_path = base / "article00_scenario_control.json"
        return collector

    def test_no_pdcp_fallback_publishes_explicit_proxy_latency(self):
        collector = self._collector()
        result = collector.aggregate_metrics_without_pdcp(
            mac_metrics=[
                {"time": 1.0, "imsi": "1", "cell_id": 1, "mcs_tb1": 12, "size_tb1": 1200, "size_tb2": 0},
                {"time": 2.0, "imsi": "1", "cell_id": 1, "mcs_tb1": 11, "size_tb1": 1000, "size_tb2": 0},
            ],
            rlc_metrics=[],
            cu_up_metrics={"per_ue": {}, "total_throughput_kbps": 0.0},
        )

        gm = result["global_metrics"]
        self.assertEqual(gm["collector_mode"], "no_pdcp_proxy_latency")
        self.assertGreater(gm["latency_p95_us"], 0.0)
        self.assertGreater(gm["cvar_per_ue_us"], 0.0)
        self.assertGreater(gm["proxy_latency_sample_count"], 0)
        self.assertEqual(gm["real_latency_sample_count"], 0)
        self.assertIn("embb_mac_proxy", gm["latency_sample_source_counts"])
        self.assertTrue(result["ue_metrics"]["1"]["latency_is_proxy"])

    def test_no_pdcp_fallback_expands_app2_sensors_and_app3_vehicles(self):
        collector = self._collector()
        result = collector.aggregate_metrics_without_pdcp(
            mac_metrics=[
                {"time": 1.0, "imsi": "4", "cell_id": 1, "mcs_tb1": 8, "size_tb1": 80, "size_tb2": 0},
            ],
            rlc_metrics=[],
            cu_up_metrics={"per_ue": {}, "total_throughput_kbps": 0.0},
        )

        gm = result["global_metrics"]
        self.assertTrue(gm["proxy_expanded_sensors"])
        self.assertTrue(gm["proxy_virtual_vehicles"])
        self.assertGreaterEqual(gm["total_active_sensors"], 9)
        self.assertGreater(gm["total_active_vehicles"], 0)
        self.assertIn("app2_sensor_proxy", gm["latency_sample_source_counts"])
        self.assertIn("vehicle_state_proxy", gm["latency_sample_source_counts"])

        vehicle_imsi = str(get_fixed_vehicle_base_imsi())
        vehicle = result["ue_metrics"][vehicle_imsi]
        self.assertEqual(vehicle["device_type"], "vehicle")
        self.assertTrue(vehicle["latency_is_proxy"])
        self.assertEqual(vehicle["latency_source"], "vehicle_state_proxy")
        self.assertIn(vehicle["risk_state"], {"low", "medium", "high"})

    def test_no_pdcp_fallback_uses_app1_app2_and_vehicle_overrides_for_proxy_latencies(self):
        collector = self._collector()
        collector.scenario_control_path.write_text(json.dumps({
            "app1_camera_override": {
                "enabled": True,
                "latency_ms": 14.0,
            },
            "app2_sensor_override": {
                "enabled": True,
                "connected_sensors": 25,
                "avg_latency_ms": 118.0,
            },
            "vehicle_override": {
                "enabled": True,
                "high_risk_vehicles": 0,
                "medium_risk_vehicles": 0,
                "degraded_autonomy_vehicles": 0,
                "ego_latency_ms": 12.0,
                "traffic_latency_ms": 8.0,
                "ego_packet_loss_percent": 0.05,
                "traffic_packet_loss_percent": 0.02,
                "max_speed_mps": 5.0,
            },
        }))

        result = collector.aggregate_metrics_without_pdcp(
            mac_metrics=[
                {"time": 1.0, "imsi": "1", "cell_id": 1, "mcs_tb1": 8, "size_tb1": 80, "size_tb2": 0},
                {"time": 1.0, "imsi": "4", "cell_id": 1, "mcs_tb1": 8, "size_tb1": 80, "size_tb2": 0},
            ],
            rlc_metrics=[],
            cu_up_metrics={"per_ue": {}, "total_throughput_kbps": 0.0},
        )

        camera = result["ue_metrics"]["1"]
        sensor = result["ue_metrics"]["4"]
        vehicle = result["ue_metrics"][str(get_fixed_vehicle_base_imsi())]

        self.assertEqual(camera["latency_source"], "app1_camera_override")
        self.assertEqual(camera["latency_us"], 14000.0)
        self.assertEqual(sensor["latency_source"], "app2_sensor_override")
        self.assertEqual(sensor["latency_us"], 118000.0)
        self.assertEqual(vehicle["latency_source"], "vehicle_state_proxy")
        self.assertEqual(vehicle["latency_us"], 12000.0)

    def test_trace_status_marks_old_pdcp_file_stale(self):
        collector = self._collector()
        base = Path(collector.output_file).parent
        pdcp = base / "DlPdcpStats.txt"
        pdcp.write_text("header\n")
        old_mtime = time.time() - 90.0
        os.utime(pdcp, (old_mtime, old_mtime))

        status = collector._trace_status(
            pdcp,
            metrics=[{"time_start": 1.0, "time_end": 46.0}],
            stale_threshold_s=30.0,
        )

        self.assertTrue(status["exists"])
        self.assertTrue(status["stale"])
        self.assertGreaterEqual(status["age_s"], 30.0)
        self.assertEqual(status["latest_sim_time_s"], 46.0)

    def test_attach_trace_status_can_mark_pdcp_stale_mode(self):
        collector = self._collector()
        metrics = {"global_metrics": {"collector_mode": "pdcp_real"}}

        collector._attach_trace_status(
            metrics,
            {
                "pdcp": {"age_s": 91.0, "stale": True, "latest_sim_time_s": 46.0},
                "rlc": {"age_s": 91.0, "stale": True},
                "mac": {"age_s": 0.2, "stale": False},
            },
            collector_mode="pdcp_stale",
        )

        gm = metrics["global_metrics"]
        self.assertEqual(gm["collector_mode"], "pdcp_stale")
        self.assertTrue(gm["pdcp_stale"])
        self.assertTrue(gm["rlc_stale"])
        self.assertFalse(gm["mac_stale"])
        self.assertEqual(gm["pdcp_latest_sim_time_s"], 46.0)

    def test_pdcp_path_marks_real_latency_source(self):
        collector = self._collector()
        result = collector.aggregate_metrics(
            pdcp_metrics=[
                {
                    "time_start": 1.0,
                    "time_end": 2.0,
                    "cell_id": 1,
                    "imsi": "1",
                    "tx_bytes": 1500,
                    "rx_bytes": 1400,
                    "n_tx_pdus": 2,
                    "n_rx_pdus": 2,
                    "delay_us": 42000.0,
                    "delay_stddev_us": 1500.0,
                    "delay_min_us": 39000.0,
                    "delay_max_us": 46000.0,
                    "pdu_size": 700,
                    "device_type": "camera",
                }
            ],
            mac_metrics=[],
            rlc_metrics=[],
            cu_up_metrics={"per_ue": {}, "total_throughput_kbps": 0.0},
        )

        gm = result["global_metrics"]
        self.assertEqual(gm["collector_mode"], "pdcp_real")
        self.assertEqual(gm["real_latency_sample_count"], 1)
        self.assertEqual(gm["proxy_latency_sample_count"], 0)
        self.assertEqual(gm["latency_sample_source_counts"], {"pdcp_real": 1})
        self.assertFalse(result["ue_metrics"]["1"]["latency_is_proxy"])
        self.assertEqual(result["ue_metrics"]["1"]["latency_source"], "pdcp_real")

    def test_pdcp_vehicle_latency_is_not_overridden_by_carla_state(self):
        collector = self._collector()
        base = Path(collector.output_file).parent
        carla_dir = base / "carla_state"
        carla_dir.mkdir(parents=True, exist_ok=True)
        collector.carla_vehicle_map_path = carla_dir / "vehicle_network_map.json"
        collector.carla_vehicles_path = carla_dir / "vehicles.json"
        collector.carla_vehicle_map_path.write_text(json.dumps({
            "roles": {
                "16": {
                    "device_type": "vehicle",
                    "vehicle_id": "veh-01",
                    "vehicle_role": "ego",
                    "connectivity": "5g_native",
                    "gateway_id": "GW-VEH-01",
                    "domain": "vehicular",
                    "mobility_profile": "vehicle",
                }
            }
        }))
        collector.carla_vehicles_path.write_text(json.dumps({
            "vehicles": [{"vehicle_id": "veh-01", "latency_ms": 18.0, "risk_state": "low"}]
        }))

        result = collector.aggregate_metrics(
            pdcp_metrics=[
                {
                    "time_start": 1.0,
                    "time_end": 2.0,
                    "cell_id": 1,
                    "imsi": "16",
                    "tx_bytes": 1500,
                    "rx_bytes": 1400,
                    "n_tx_pdus": 2,
                    "n_rx_pdus": 2,
                    "delay_us": 42000.0,
                    "delay_stddev_us": 1500.0,
                    "delay_min_us": 39000.0,
                    "delay_max_us": 46000.0,
                    "pdu_size": 700,
                    "device_type": "vehicle",
                }
            ],
            mac_metrics=[],
            rlc_metrics=[],
            cu_up_metrics={"per_ue": {}, "total_throughput_kbps": 0.0},
        )

        vehicle = result["ue_metrics"]["16"]
        self.assertEqual(vehicle["device_type"], "vehicle")
        self.assertEqual(vehicle["latency_source"], "pdcp_real")
        self.assertFalse(vehicle["latency_is_proxy"])
        self.assertEqual(vehicle["latency_us"], 42000.0)
        self.assertEqual(result["global_metrics"]["latency_sample_source_counts"], {"pdcp_real": 1})

    def test_vehicle_scenario_override_preserves_real_pdcp_latency(self):
        collector = self._collector()
        base = Path(collector.output_file).parent
        collector.scenario_control_path = base / "article00_scenario_control.json"
        collector.scenario_control_path.write_text(json.dumps({
            "vehicle_override": {
                "enabled": True,
                "total_vehicles": 5,
                "high_risk_vehicles": 0,
                "medium_risk_vehicles": 0,
                "degraded_autonomy_vehicles": 0,
                "ego_latency_ms": 12.0,
                "traffic_latency_ms": 8.0,
                "ego_packet_loss_percent": 0.05,
                "traffic_packet_loss_percent": 0.02,
                "max_speed_mps": 5.0,
                "mode": "allowed_stable",
            }
        }))

        result = collector.aggregate_metrics(
            pdcp_metrics=[
                {
                    "time_start": 1.0,
                    "time_end": 2.0,
                    "cell_id": 1,
                    "imsi": "16",
                    "tx_bytes": 1500,
                    "rx_bytes": 1400,
                    "n_tx_pdus": 2,
                    "n_rx_pdus": 2,
                    "delay_us": 120000.0,
                    "delay_stddev_us": 1500.0,
                    "delay_min_us": 110000.0,
                    "delay_max_us": 130000.0,
                    "pdu_size": 700,
                    "device_type": "vehicle",
                }
            ],
            mac_metrics=[],
            rlc_metrics=[],
            cu_up_metrics={"per_ue": {}, "total_throughput_kbps": 0.0},
        )

        vehicle = result["ue_metrics"]["16"]
        self.assertEqual(vehicle["device_type"], "vehicle")
        self.assertEqual(vehicle["vehicle_role"], "ego")
        self.assertEqual(vehicle["risk_state"], "low")
        self.assertEqual(vehicle["autonomy_state"], "normal")
        self.assertEqual(vehicle["latency_source"], "pdcp_real")
        self.assertFalse(vehicle["latency_is_proxy"])
        self.assertEqual(vehicle["latency_us"], 120000.0)
        # Real PDCP TX/RX counters are authoritative; the scenario override
        # may enrich vehicle risk/state metadata but cannot replace measured
        # loss with a synthetic value.
        self.assertAlmostEqual(vehicle["packet_loss_percent"], 0.0)
        self.assertEqual(result["global_metrics"]["latency_sample_source_counts"], {"pdcp_real": 1})

    def test_pdcp_path_merges_carla_vehicles_missing_from_pdcp(self):
        collector = self._collector()
        base = Path(collector.output_file).parent
        carla_dir = base / "carla_state"
        carla_dir.mkdir(parents=True, exist_ok=True)
        collector.carla_vehicle_map_path = carla_dir / "vehicle_network_map.json"
        collector.carla_vehicles_path = carla_dir / "vehicles.json"
        collector.carla_vehicle_map_mtime = None
        collector.carla_vehicle_state_mtime = None

        collector.carla_vehicle_map_path.write_text(json.dumps({
            "roles": {
                "16": {
                    "device_type": "vehicle",
                    "vehicle_id": "veh-01",
                    "vehicle_role": "ego",
                    "connectivity": "5g_native",
                    "gateway_id": "GW-VEH-01",
                    "domain": "vehicular",
                    "mobility_profile": "vehicle",
                },
                "17": {
                    "device_type": "vehicle",
                    "vehicle_id": "veh-02",
                    "vehicle_role": "traffic",
                    "connectivity": "5g_native",
                    "gateway_id": "GW-VEH-01",
                    "domain": "vehicular",
                    "mobility_profile": "vehicle",
                },
            }
        }))
        collector.carla_vehicles_path.write_text(json.dumps({
            "vehicles": [
                {
                    "vehicle_id": "veh-01",
                    "latency_ms": 18.0,
                    "packet_loss_percent": 0.2,
                    "speed_mps": 7.0,
                    "risk_state": "low",
                    "autonomy_state": "normal",
                    "position": {"x": 1.0, "y": 2.0, "z": 0.0},
                },
                {
                    "vehicle_id": "veh-02",
                    "latency_ms": 12.0,
                    "packet_loss_percent": 0.1,
                    "speed_mps": 6.5,
                    "risk_state": "low",
                    "autonomy_state": "normal",
                    "position": {"x": 3.0, "y": 4.0, "z": 0.0},
                },
            ]
        }))

        result = collector.aggregate_metrics(
            pdcp_metrics=[
                {
                    "time_start": 1.0,
                    "time_end": 2.0,
                    "cell_id": 1,
                    "imsi": "1",
                    "tx_bytes": 1500,
                    "rx_bytes": 1400,
                    "n_tx_pdus": 2,
                    "n_rx_pdus": 2,
                    "delay_us": 42000.0,
                    "delay_stddev_us": 1500.0,
                    "delay_min_us": 39000.0,
                    "delay_max_us": 46000.0,
                    "pdu_size": 700,
                    "device_type": "camera",
                }
            ],
            mac_metrics=[],
            rlc_metrics=[],
            cu_up_metrics={"per_ue": {}, "total_throughput_kbps": 0.0},
        )

        gm = result["global_metrics"]
        self.assertEqual(gm["collector_mode"], "pdcp_real")
        self.assertTrue(gm["proxy_virtual_vehicles"])
        self.assertEqual(gm["total_active_vehicles"], 2)
        self.assertEqual(gm["total_active_ues"], 3)
        self.assertEqual(gm["real_latency_sample_count"], 1)
        self.assertEqual(gm["proxy_latency_sample_count"], 2)
        self.assertEqual(gm["latency_sample_source_counts"], {"pdcp_real": 1, "vehicle_state_proxy": 2})

        vehicle = result["ue_metrics"]["16"]
        self.assertEqual(vehicle["device_type"], "vehicle")
        self.assertEqual(vehicle["vehicle_id"], "veh-01")
        self.assertEqual(vehicle["latency_us"], 18000.0)
        self.assertTrue(vehicle["latency_is_proxy"])
        self.assertEqual(vehicle["latency_source"], "vehicle_state_proxy")
        self.assertEqual(vehicle["throughput_source"], "carla_context_only")

    def test_strict_real_only_mode_skips_snapshot_when_proxy_latency_is_present(self):
        collector = self._collector()
        collector.require_real_pdcp = True
        collector.running = True
        collector._resolve_trace_file = mock.Mock(
            side_effect=lambda kind: Path("/tmp/DlPdcpStats.txt") if kind == "pdcp" else Path("/tmp/DlRlcStats.txt")
        )
        collector.process_pdcp_stats = mock.Mock(side_effect=lambda *_args, **_kwargs: [{"time_start": 1.0, "time_end": 2.0}])
        collector.process_mac_stats = mock.Mock(return_value=[])
        collector.process_rlc_stats = mock.Mock(return_value=[])
        collector.process_mmwave_sched_stats = mock.Mock(return_value={})
        collector.process_cu_up_stats = mock.Mock(return_value={"per_ue": {}, "total_throughput_kbps": 0.0})
        collector._trace_status = mock.Mock(return_value={"stale": False, "age_s": 0.1, "latest_sim_time_s": 2.0})
        collector.aggregate_metrics = mock.Mock(return_value={
            "timestamp": 1,
            "sim_time_range": {"start": 1.0, "end": 2.0, "window_s": 1.0},
            "ue_metrics": {
                "1": {
                    "latency_is_proxy": False,
                    "device_type": "camera",
                    "latency_us": 42000.0,
                    "throughput_kbps": 1000.0,
                    "packet_count": 1,
                },
                "2": {
                    "latency_is_proxy": True,
                    "device_type": "sensor",
                    "latency_us": 118000.0,
                    "throughput_kbps": 32.0,
                    "packet_count": 1,
                },
            },
            "global_metrics": {
                "collector_mode": "pdcp_real",
                "proxy_latency_sample_count": 1,
                "real_latency_sample_count": 1,
                "global_worst_latency_us": 118000.0,
                "global_avg_latency_us": 80000.0,
                "total_active_ues": 2,
                "total_active_sensors": 1,
                "throughput_kbps": 1032.0,
            },
            "active_cameras": 1,
            "critical_cameras": 0,
        })

        written_targets = []

        def fake_write_metrics(payload, path):
            written_targets.append(path)

        collector.write_metrics = fake_write_metrics
        collector.export_device_roles_snapshot = mock.Mock()
        collector.export_app2_metrics = mock.Mock()
        collector.write_standard_metrics = mock.Mock(return_value={"timestamp": 1})

        sleep_calls = {"count": 0}

        def fake_sleep(_seconds):
            sleep_calls["count"] += 1
            collector.running = False

        with mock.patch("csv_to_metrics.time.sleep", side_effect=fake_sleep):
            collector.run()

        self.assertEqual(written_targets, [])
        collector.export_device_roles_snapshot.assert_not_called()
        collector.export_app2_metrics.assert_not_called()
        collector.write_standard_metrics.assert_not_called()
        self.assertEqual(sleep_calls["count"], 1)

    def test_strict_real_only_mode_clears_stale_json_when_pdcp_is_missing(self):
        collector = self._collector()
        collector.require_real_pdcp = True
        collector.running = True
        Path(collector.output_file).write_text('{"stale": true}\n', encoding="utf-8")
        Path(collector.extended_output_file).write_text('{"stale": true}\n', encoding="utf-8")
        collector._resolve_trace_file = mock.Mock(
            side_effect=lambda kind: Path("/tmp/DlPdcpStats.txt") if kind == "pdcp" else Path("/tmp/DlRlcStats.txt")
        )
        collector.process_pdcp_stats = mock.Mock(return_value=[])
        collector.process_mac_stats = mock.Mock(return_value=[])
        collector.process_rlc_stats = mock.Mock(return_value=[])
        collector.process_mmwave_sched_stats = mock.Mock(return_value={})
        collector.process_cu_up_stats = mock.Mock(return_value={"per_ue": {}, "total_throughput_kbps": 0.0})
        collector._trace_status = mock.Mock(return_value={"stale": False, "age_s": 0.1, "latest_sim_time_s": 0.0})

        sleep_calls = {"count": 0}

        def fake_sleep(_seconds):
            sleep_calls["count"] += 1
            collector.running = False

        with mock.patch("csv_to_metrics.time.sleep", side_effect=fake_sleep):
            collector.run()

        self.assertFalse(Path(collector.output_file).exists())
        self.assertFalse(Path(collector.extended_output_file).exists())
        self.assertEqual(sleep_calls["count"], 1)


if __name__ == "__main__":
    unittest.main()
