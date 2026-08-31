import argparse
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_tasam_article_dataset import (
    collection_quality,
    compute_reward,
    fetch_metrics,
    fetch_judge_outcome,
    scenario_stage_from_shadow,
    transition_record,
)


class TestExportTASAMArticleDataset(unittest.TestCase):
    def test_fetch_judge_outcome_accepts_delayed_six_second_feedback(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "create table judge_outcome_history (decision_timestamp integer, observed_timestamp integer, observed integer, feedback_json text)"
        )
        conn.execute(
            "insert into judge_outcome_history values (105, 110, 1, '{}')"
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        outcome = fetch_judge_outcome(conn.cursor(), 100)
        self.assertEqual(outcome["judge_alignment"]["source_timestamp"], 105)
        self.assertEqual(outcome["judge_alignment"]["skew_s"], 5)

    def test_fetch_judge_outcome_rejects_feedback_above_six_seconds(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "create table judge_outcome_history (decision_timestamp integer, observed_timestamp integer, observed integer, feedback_json text)"
        )
        conn.execute(
            "insert into judge_outcome_history values (107, 112, 1, '{}')"
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        self.assertEqual(fetch_judge_outcome(conn.cursor(), 100), {})

    def test_fetch_metrics_accepts_exact_and_six_second_nearest_match(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("create table extended_metrics (timestamp integer, collector_mode text, real_latency_sample_count integer, proxy_latency_sample_count integer)")
        conn.executemany(
            "insert into extended_metrics values (?, ?, ?, ?)",
            [(100, "pdcp_real", 10, 0), (106, "pdcp_real", 10, 0)],
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        metrics, alignment = fetch_metrics(conn.cursor(), 100)
        self.assertEqual(metrics["collector_mode"], "pdcp_real")
        self.assertEqual(alignment["source_timestamp"], 100)
        metrics, alignment = fetch_metrics(conn.cursor(), 106)
        self.assertEqual(alignment["skew_s"], 0)
        metrics, alignment = fetch_metrics(conn.cursor(), 101)
        self.assertEqual(alignment["source_timestamp"], 100)
        self.assertEqual(alignment["skew_s"], 1)

    def test_fetch_metrics_rejects_match_above_six_seconds(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("create table extended_metrics (timestamp integer, collector_mode text, real_latency_sample_count integer, proxy_latency_sample_count integer)")
        conn.execute("insert into extended_metrics values (100, 'pdcp_real', 10, 0)")
        conn.commit()
        conn.row_factory = sqlite3.Row
        metrics, alignment = fetch_metrics(conn.cursor(), 107)
        self.assertEqual(metrics, {})
        self.assertEqual(alignment, {})

    def test_authoritative_collection_stage_wins_over_text_fallbacks(self):
        decision = {
            "collection_event_stage_name": "camera_blocked",
            "collection_event_stage_authoritative": 1,
            "reason": "vehicle guard also active",
            "priority_violation": "VEHICLE_CRITICAL",
        }
        self.assertEqual(
            scenario_stage_from_shadow({}, decision, {}, {}),
            "camera_blocked",
        )

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

    def test_transition_uses_delayed_tasam_judge_credit_for_training(self):
        args = argparse.Namespace(allow_proxy=False, max_step_gap_s=20, max_sim_reset_gap_s=1.0)
        current = {
            "timestamp": 100,
            "datetime": "2026-06-10T12:00:00",
            "topology_id": "test",
            "scenario_stage": "vehicle_blocked",
            "global_state": {"state_vector": [0.1, 0.2]},
            "slice_state": {
                "eMBB": {"completion_ratio": 1.0, "qos_pressure": 0.0, "min_qos_met": 1.0},
                "mMTC": {"completion_ratio": 1.0, "qos_pressure": 0.0, "min_qos_met": 1.0},
                "URLLC": {"completion_ratio": 1.0, "qos_pressure": 0.0, "min_qos_met": 1.0},
            },
            "du_states": [{"du_id": "du0", "state_vector": [0.1, 0.2], "slice_mix": {"eMBB": 1.0}}],
            "action": {"usable_budget": 1.0, "r_ran": 0.5, "r_ai": 0.4, "d_ran": 0.4, "d_ai": 0.3},
            "decision": {"decision": "BLOCKED", "selected_assistant": "armd"},
            "metrics": {
                "sim_time_s": 10.0,
                "cvar_per_ue_us": 10000.0,
                "global_packet_loss_rate": 0.0,
                "collector_mode": "pdcp_real",
                "real_latency_sample_count": 1.0,
            },
            "judge_outcome": {
                "observed": 1,
                "feedback": {
                    "outcome_observed": True,
                    "tasam_credit": -0.5,
                    "tasam_state_credit": -0.5,
                    "tasam_resource_credit": 0.0,
                    "outcome_reward": 0.5,
                    "credit_assignment": "individual_correctness",
                },
                "observation": {"correct_verdict": "BLOCKED"},
            },
            "armd_context": {"enabled": True},
            "conflict_context": {"recent_count": 0},
            "shadow_comparison": {"shadow_ready": True},
        }
        nxt = dict(current)
        nxt["timestamp"] = 105
        nxt["metrics"] = {"sim_time_s": 15.0, "cvar_per_ue_us": 10000.0}
        record = transition_record(current, nxt, args)
        self.assertEqual(record["reward_hint"], -0.5)
        self.assertEqual(record["reward_source"], "rapp_judge_tasam_credit")
        self.assertTrue(record["judge_feedback_observed"])
        self.assertEqual(record["credit_assignment"], "individual_correctness")

    def test_transition_prefers_continuous_observed_reward(self):
        args = argparse.Namespace(allow_proxy=False, max_step_gap_s=20, max_sim_reset_gap_s=1.0)
        current = {
            "timestamp": 100,
            "datetime": "2026-06-10T12:00:00",
            "topology_id": "test",
            "scenario_stage": "allowed",
            "global_state": {"state_vector": [0.1]},
            "slice_state": {}, "du_states": [],
            "action": {}, "decision": {"selected_assistant": "ta_sam"},
            "metrics": {"sim_time_s": 10.0, "cvar_per_ue_us": 10000.0, "collector_mode": "pdcp_real", "real_latency_sample_count": 1.0},
            "judge_outcome": {"observed": 1, "feedback": {
                "outcome_observed": True, "tasam_credit": -0.5,
                "tasam_observed_error": 0.2, "tasam_continuous_reward": 0.8,
                "tasam_reward_source": "observed_real_metrics",
                "tasam_category_credit": 1.0,
                "tasam_error_components": {"service_error": 0.2},
            }, "observation": {"correct_verdict": "ALLOWED"}},
            "armd_context": {}, "conflict_context": {}, "shadow_comparison": {},
        }
        nxt = dict(current)
        nxt["timestamp"] = 105
        nxt["metrics"] = {"sim_time_s": 15.0, "cvar_per_ue_us": 10000.0}
        record = transition_record(current, nxt, args)
        self.assertEqual(record["reward_hint"], 0.8)
        self.assertEqual(record["reward_source"], "observed_real_metrics_with_categorical_penalty")
        self.assertEqual(record["tasam_observed_error"], 0.2)

    def test_transition_categorical_error_overrides_positive_continuous_reward(self):
        args = argparse.Namespace(allow_proxy=False, max_step_gap_s=20, max_sim_reset_gap_s=1.0)
        current = {
            "timestamp": 100, "datetime": "2026-06-10T12:00:00", "topology_id": "test",
            "scenario_stage": "conditional", "global_state": {"state_vector": [0.1]},
            "slice_state": {}, "du_states": [], "action": {},
            "decision": {"selected_assistant": "ta_sam"},
            "metrics": {"sim_time_s": 10.0, "collector_mode": "pdcp_real", "real_latency_sample_count": 1.0},
            "judge_outcome": {"observed": 1, "feedback": {
                "outcome_observed": True, "tasam_continuous_reward": 0.9,
                "tasam_reward_source": "observed_real_metrics", "tasam_category_credit": -0.5,
                "tasam_category_penalty": 0.5, "tasam_category_error": True,
            }, "observation": {"correct_verdict": "CONDITIONAL"}},
            "armd_context": {}, "conflict_context": {}, "shadow_comparison": {},
        }
        nxt = dict(current)
        nxt["timestamp"] = 105
        nxt["metrics"] = {"sim_time_s": 15.0}
        record = transition_record(current, nxt, args)
        self.assertEqual(record["reward_hint"], -0.5)
        self.assertEqual(record["reward_source"], "observed_real_metrics_with_categorical_penalty")

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
