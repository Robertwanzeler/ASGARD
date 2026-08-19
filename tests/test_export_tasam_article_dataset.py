import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_tasam_article_dataset import collection_quality, compute_reward, transition_record


class TestExportTASAMArticleDataset(unittest.TestCase):
    def test_compute_reward_contains_article_components(self):
        record = {
            "slice_state": {
                "eMBB": {"completion_ratio": 1.0, "qos_pressure": 0.0, "min_qos_met": 1.0},
                "mMTC": {"completion_ratio": 0.8, "qos_pressure": 0.1, "min_qos_met": 1.0},
                "URLLC": {"completion_ratio": 0.9, "qos_pressure": 0.2, "min_qos_met": 1.0},
            },
            "metrics": {"cvar_per_ue_us": 20000.0, "global_packet_loss_rate": 0.0},
            "conflict_context": {"recent_count": 0},
            "action": {"usable_budget": 1.0, "r_ran": 0.4, "r_ai": 0.4, "d_ran": 0.3, "d_ai": 0.3},
        }
        reward = compute_reward(record)
        self.assertGreater(reward["reward"], 0.5)
        for key in ("embb_qos", "mmtc_qos", "urllc_qos", "shortage_penalty", "min_qos_penalty"):
            self.assertIn(key, reward["components"])

    def test_transition_keeps_armd_read_only_context(self):
        args = argparse.Namespace(allow_proxy=False, max_step_gap_s=20, max_sim_reset_gap_s=1.0)
        current = {
            "timestamp": 100,
            "datetime": "2026-06-10T12:00:00",
            "topology_id": "test",
            "scenario_stage": "borderline_vehicle_warning",
            "global_state": {"state_vector": [0.1, 0.2]},
            "slice_state": {
                "eMBB": {"completion_ratio": 1.0, "qos_pressure": 0.0, "min_qos_met": 1.0},
                "mMTC": {"completion_ratio": 1.0, "qos_pressure": 0.0, "min_qos_met": 1.0},
                "URLLC": {"completion_ratio": 1.0, "qos_pressure": 0.0, "min_qos_met": 1.0},
            },
            "du_states": [{"du_id": "du0", "state_vector": [0.1, 0.2], "slice_mix": {"eMBB": 1.0}}],
            "action": {"usable_budget": 1.0, "r_ran": 0.5, "r_ai": 0.4, "d_ran": 0.4, "d_ai": 0.3},
            "decision": {"decision": "ALLOWED"},
            "metrics": {
                "sim_time_s": 10.0,
                "cvar_per_ue_us": 10000.0,
                "global_packet_loss_rate": 0.0,
                "collector_mode": "pdcp_real",
                "real_latency_sample_count": 1.0,
            },
            "armd_context": {"enabled": True, "mode": "shadow"},
            "conflict_context": {"recent_count": 0},
            "shadow_comparison": {"shadow_ready": True, "snapshot": {"marl_shadow": {"scenario_stage": "borderline_vehicle_warning"}}},
        }
        nxt = dict(current)
        nxt["timestamp"] = 105
        nxt["metrics"] = {"sim_time_s": 15.0, "cvar_per_ue_us": 10000.0}
        record = transition_record(current, nxt, args)
        self.assertEqual(record["schema"], "greenran.tasam_article_transition.v1")
        self.assertEqual(record["armd_context"]["mode"], "shadow")
        self.assertEqual(record["scenario_stage"], "borderline_vehicle_warning")
        self.assertTrue(record["collection_quality"]["valid_for_training"])

    def test_collection_quality_rejects_suspected_stale_proxy_snapshot(self):
        args = argparse.Namespace(allow_proxy=False, max_step_gap_s=20, max_sim_reset_gap_s=1.0)
        current = {
            "timestamp": 100,
            "metrics": {
                "sim_time_s": 1.0,
                "throughput_kbps": 0.0,
                "total_tx_bytes": 0.0,
                "total_rx_bytes": 0.0,
                "cvar_per_ue_us": 100200.0,
                "variance_per_ue_us2": 0.0,
            },
        }
        nxt = {
            "timestamp": 105,
            "metrics": {"sim_time_s": 1.0},
        }

        quality = collection_quality(current["metrics"], current, nxt, args)

        self.assertEqual(quality["collector_mode"], "suspected_stale_proxy")
        self.assertFalse(quality["pdcp_real"])
        self.assertTrue(quality["has_proxy"])
        self.assertFalse(quality["valid_for_training"])


if __name__ == "__main__":
    unittest.main()
