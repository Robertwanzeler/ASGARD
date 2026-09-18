import json
import tempfile
import unittest
from pathlib import Path

from src.csv_to_metrics import ExtendedMetricsCollector
from src.rapp_data_lake import DataLake
from src.greenran_paths import get_fixed_service_imsis


class TestV7EconomicEvidence(unittest.TestCase):
    def test_collector_calculates_loss_and_keeps_vehicle_pdcp_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector = ExtendedMetricsCollector(
                tmp,
                str(Path(tmp) / "metrics.json"),
                str(Path(tmp) / "extended_metrics.json"),
                1.0,
            )
            rows = []
            for imsi in range(1, 21):
                rows.append({
                    "imsi": imsi,
                    "cell_id": 2 if imsi <= 6 else 3 if imsi <= 9 else 4,
                    "device_type": collector.get_device_type(imsi),
                    "time_start": 10.0,
                    "time_end": 20.0,
                    "n_tx_pdus": 100,
                    "n_rx_pdus": 95,
                    "tx_bytes": 10000,
                    "rx_bytes": 9500,
                    "delay_us": 1000.0,
                    "delay_min_us": 900.0,
                    "delay_max_us": 1100.0,
                    "delay_stddev_us": 10.0,
                    "pdu_size": 100.0,
                })
            result = collector.aggregate_metrics(rows, [])
            self.assertEqual(len(result["ue_metrics"]), 20)
            self.assertTrue(all(item["pdcp_provenance"] == "pdcp_real" for item in result["ue_metrics"].values()))
            self.assertTrue(all(item["packet_loss_percent"] == 5.0 for item in result["ue_metrics"].values()))
            self.assertEqual({item["device_type"] for item in result["ue_metrics"].values()}, {"camera", "sensor", "vehicle"})

            collector.app3_monitoring_file = Path(tmp) / "app3" / "monitoring_snapshot.json"
            self.assertTrue(collector.export_app3_metrics(result))
            snapshot = json.loads(collector.app3_monitoring_file.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["schema"], "greenran.app3.pdcp_real_snapshot.v1")
            self.assertEqual(snapshot["vehicles"]["total_vehicles"], 5)
            self.assertEqual(set(snapshot["vehicle_imsis"]), {"16", "17", "18", "19", "20"})

    def test_datalake_requires_complete_canonical_pdcp_window(self):
        services = get_fixed_service_imsis()
        with tempfile.TemporaryDirectory() as tmp:
            lake = DataLake(str(Path(tmp) / "rapp.db"))
            payload = {
                "global_metrics": {
                    "total_active_ues": 20,
                    "total_active_cameras": 3,
                    "global_worst_latency_us": 1000,
                },
                "sim_time_range": {"end": 20.0},
                "ue_metrics": {
                    str(imsi): {
                        "device_type": service,
                        "packet_loss_percent": 5.0,
                        "pdcp_provenance": "pdcp_real",
                        "latency_is_proxy": False,
                        "tx_pdus": 100,
                        "rx_pdus": 95,
                    }
                    for service, imsies in services.items()
                    if service != "all"
                    for imsi in imsies
                },
            }
            metric_id = lake.record_extended_from_json(payload)
            coverage = lake.real_pdcp_loss_coverage(metric_id)
            self.assertTrue(coverage["valid"])
            self.assertEqual(coverage["groups"], {"camera": True, "sensor": True, "vehicle": True})
            self.assertEqual(len(coverage["expected_imsis"]), 20)

            lake.conn.execute("DELETE FROM ue_metrics WHERE imsi = 20")
            lake.conn.commit()
            incomplete = lake.real_pdcp_loss_coverage(metric_id)
            self.assertFalse(incomplete["valid"])
            self.assertEqual(incomplete["reason"], "canonical_topology_mismatch")

    def test_delayed_feedback_updates_original_decision_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake = DataLake(str(Path(tmp) / "rapp.db"))
            decision_id = lake.record_decision({"timestamp": 1000, "energy_saver": "ALLOWED"})
            coverage = {"valid": True, "topology_valid": True, "groups": {"camera": True, "sensor": True, "vehicle": True}}
            lake.record_judge_outcome(
                {"timestamp": 1000, "decision_id": decision_id},
                {
                    "economic_transition_eligible": True,
                    "economic_invalid_reason": "",
                    "topology_valid": True,
                    "pdcp_loss_coverage": coverage,
                    "economic_action": {"contract": "applied_action_v2"},
                },
                observation={"timestamp": 1001},
                observed_timestamp=1001,
            )
            row = lake.conn.execute(
                "SELECT economic_transition_eligible, topology_valid, pdcp_coverage_json, economic_action_json FROM decisions_history WHERE id=?",
                (decision_id,),
            ).fetchone()
            self.assertEqual(row[0], 1)
            self.assertEqual(row[1], 1)
            self.assertTrue(json.loads(row[2])["valid"])
            self.assertEqual(json.loads(row[3])["contract"], "applied_action_v2")


if __name__ == "__main__":
    unittest.main()
