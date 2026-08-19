import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "training"))

import numpy as np
import pandas as pd

from train_ml_model import (  # noqa: E402
    build_temporal_evaluation_windows,
    build_evaluation_summary,
    engineer_features,
    load_trace_data,
    normalize_training_frame,
    select_feature_profile,
    summarize_temporal_window,
)


class TestTrainMlModelTrace(unittest.TestCase):
    def test_load_trace_data_extracts_training_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            record = {
                "timestamp": 1783005239,
                "scenario_stage": "allowed_stable",
                "metrics": {
                    "sim_time_s": 12.0,
                    "throughput_kbps": 21000.0,
                    "latency_p95_us": 24000.0,
                    "cvar_per_ue_us": 32000.0,
                    "total_active_ues": 180,
                    "total_active_cameras": 71,
                    "total_critical_ues": 5,
                    "global_packet_loss_rate": 0.02,
                },
                "global_state": {
                    "total_demand": 0.61,
                    "usable_budget": 0.73,
                },
                "slice_state": {
                    "eMBB": {
                        "ue_count": 83,
                        "demand": 0.225,
                        "allocation": 0.42,
                        "qos_pressure": 0.20,
                        "completion_ratio": 1.0,
                        "min_qos_met": 1.0,
                        "budget_share": 0.59,
                    },
                    "mMTC": {
                        "ue_count": 25,
                        "demand": 0.16,
                        "allocation": 0.15,
                        "qos_pressure": 0.09,
                        "completion_ratio": 0.98,
                        "min_qos_met": 1.0,
                        "budget_share": 0.22,
                    },
                    "URLLC": {
                        "ue_count": 5,
                        "demand": 0.13,
                        "allocation": 0.13,
                        "qos_pressure": 0.08,
                        "completion_ratio": 0.98,
                        "min_qos_met": 1.0,
                        "budget_share": 0.19,
                    },
                },
                "decision": {
                    "decision": "ALLOWED",
                    "reason": "ok",
                    "confidence": 0.91,
                },
            }
            path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

            df = load_trace_data(str(path))
            df = normalize_training_frame(df)

            self.assertEqual(len(df), 1)
            self.assertEqual(df.iloc[0]["decision"], "ALLOWED")
            self.assertEqual(float(df.iloc[0]["cvar_per_ue_us"]), 32000.0)
            self.assertEqual(float(df.iloc[0]["latency_p95_per_ue_us"]), 24000.0)
            self.assertEqual(df.iloc[0]["scenario_stage"], "allowed_stable")
            self.assertIn("global_avg_latency_us", df.columns)
            self.assertAlmostEqual(float(df.iloc[0]["global_total_demand"]), 0.61, places=6)
            self.assertAlmostEqual(float(df.iloc[0]["global_usable_budget"]), 0.73, places=6)
            self.assertAlmostEqual(float(df.iloc[0]["slice_embb_qos_pressure"]), 0.20, places=6)
            self.assertAlmostEqual(float(df.iloc[0]["slice_mmtc_completion_ratio"]), 0.98, places=6)
            self.assertAlmostEqual(float(df.iloc[0]["slice_urllc_budget_share"]), 0.19, places=6)

    def test_no_stage_with_slice_state_profile_selects_real_slice_features(self):
        df = pd.DataFrame(
            {
                "timestamp": [1, 2],
                "scenario_stage": ["allowed_stable", "camera_blocked"],
                "sim_time_s": [1.0, 2.0],
                "global_avg_latency_us": [8000.0, 12000.0],
                "global_worst_latency_us": [9000.0, 13000.0],
                "global_jitter_us": [0.0, 0.0],
                "global_packet_loss_rate": [0.001, 0.002],
                "throughput_kbps": [20000.0, 15000.0],
                "total_active_ues": [10, 10],
                "total_active_cameras": [4, 4],
                "total_critical_ues": [0, 0],
                "total_tx_bytes": [1000.0, 1200.0],
                "total_rx_bytes": [900.0, 1000.0],
                "cvar_per_ue_us": [12000.0, 18000.0],
                "variance_per_ue_us2": [1_000_000.0, 2_000_000.0],
                "latency_p95_per_ue_us": [9000.0, 11000.0],
                "latency_p95_us": [9000.0, 11000.0],
                "global_total_demand": [0.52, 0.89],
                "global_usable_budget": [0.72, 0.84],
                "slice_embb_ue_count": [83.0, 83.0],
                "slice_embb_demand": [0.22, 0.45],
                "slice_embb_allocation": [0.42, 0.35],
                "slice_embb_qos_pressure": [0.20, 0.65],
                "slice_embb_completion_ratio": [1.0, 0.71],
                "slice_embb_min_qos_met": [1.0, 0.0],
                "slice_embb_budget_share": [0.59, 0.44],
                "slice_mmtc_ue_count": [25.0, 25.0],
                "slice_mmtc_demand": [0.16, 0.25],
                "slice_mmtc_allocation": [0.15, 0.18],
                "slice_mmtc_qos_pressure": [0.09, 0.18],
                "slice_mmtc_completion_ratio": [0.98, 0.74],
                "slice_mmtc_min_qos_met": [1.0, 0.0],
                "slice_mmtc_budget_share": [0.22, 0.21],
                "slice_urllc_ue_count": [5.0, 5.0],
                "slice_urllc_demand": [0.13, 0.19],
                "slice_urllc_allocation": [0.13, 0.14],
                "slice_urllc_qos_pressure": [0.08, 0.17],
                "slice_urllc_completion_ratio": [0.98, 0.74],
                "slice_urllc_min_qos_met": [1.0, 0.0],
                "slice_urllc_budget_share": [0.19, 0.17],
                "decision": ["ALLOWED", "BLOCKED"],
                "energy_state": ["", ""],
                "confidence": [0.9, 0.9],
                "reason": ["ok", "bad"],
            }
        )
        df, feature_cols = engineer_features(df)
        no_stage = select_feature_profile(feature_cols, "no_stage")
        no_stage_with_slice = select_feature_profile(feature_cols, "no_stage_with_slice_state")

        self.assertNotIn("slice_mmtc_completion_ratio", no_stage)
        self.assertNotIn("global_usable_budget", no_stage)
        self.assertIn("slice_mmtc_completion_ratio", no_stage_with_slice)
        self.assertIn("global_usable_budget", no_stage_with_slice)
        self.assertNotIn("stage_allowed_stable", no_stage_with_slice)

    def test_summarize_temporal_window_flags_degenerate_holdout(self):
        df = pd.DataFrame(
            {
                "scenario_stage": ["app2_blocked"] * 6,
                "cvar_ms": [100.2] * 6,
                "latency_p95_ms": [93.5] * 6,
            }
        )
        summary = summarize_temporal_window(
            df,
            ["cvar_ms", "latency_p95_ms"],
            np.array(["ALLOWED", "BLOCKED", "CONDITIONAL", "ALLOWED", "BLOCKED", "CONDITIONAL"]),
            np.array([100.2] * 6),
        )

        self.assertFalse(summary["valid"])
        self.assertTrue(summary["degenerate_flags"]["low_feature_diversity"])
        self.assertTrue(summary["degenerate_flags"]["conflicting_duplicate_features"])
        self.assertTrue(summary["degenerate_flags"]["low_regression_target_variance"])

    def test_summarize_temporal_window_accepts_many_unique_rows_even_if_ratio_is_low(self):
        rows = 1037
        unique_rows = 51
        cvar_values = [10.0 + float(idx) for idx in range(unique_rows)]
        latency_values = [20.0 + float(idx * 2) for idx in range(unique_rows)]

        feature_cvar = [cvar_values[i % unique_rows] for i in range(rows)]
        feature_latency = [latency_values[i % unique_rows] for i in range(rows)]
        decision_labels = np.array(
            ["ALLOWED" if value < 27 else "CONDITIONAL" if value < 44 else "BLOCKED" for value in feature_cvar]
        )
        df = pd.DataFrame(
            {
                "scenario_stage": ["allowed_stable"] * rows,
                "cvar_ms": feature_cvar,
                "latency_p95_ms": feature_latency,
            }
        )

        summary = summarize_temporal_window(
            df,
            ["cvar_ms", "latency_p95_ms"],
            decision_labels,
            np.array(feature_cvar),
        )

        self.assertFalse(summary["degenerate_flags"]["low_feature_diversity"])
        self.assertTrue(summary["valid_for_classifier"])

    def test_build_evaluation_summary_uses_current_targets(self):
        df = pd.DataFrame(
            {
                "timestamp": [1, 2, 3, 4, 5, 6],
                "scenario_stage": [
                    "allowed_stable",
                    "allowed_stable",
                    "camera_blocked",
                    "camera_blocked",
                    "camera_blocked",
                    "camera_blocked",
                ],
                "decision": ["ALLOWED", "ALLOWED", "BLOCKED", "BLOCKED", "BLOCKED", "BLOCKED"],
                "cvar_ms": [12.0, 13.0, 91.0, 95.0, 98.0, 102.0],
                "latency_p95_ms": [10.0, 11.0, 87.0, 89.0, 92.0, 97.0],
            }
        )
        summary = build_evaluation_summary(
            df,
            ["cvar_ms", "latency_p95_ms"],
            [
                {
                    "index": 1,
                    "train_end": 2,
                    "test_start": 2,
                    "test_end": 6,
                    "train_size": 2,
                    "test_size": 4,
                }
            ],
        )

        self.assertTrue(summary["valid"])
        self.assertEqual(summary["decision_counts"], {"BLOCKED": 4})
        self.assertEqual(summary["stage_counts"], {"camera_blocked": 4})

    def test_build_evaluation_summary_allows_single_degenerate_regression_window(self):
        df = pd.DataFrame(
            {
                "timestamp": list(range(1, 17)),
                "scenario_stage": [
                    "allowed_stable",
                    "camera_conditional",
                    "camera_blocked",
                    "allowed_recovery",
                    "vehicle_conditional",
                    "vehicle_blocked",
                    "allowed_bootstrap",
                    "app2_conditional",
                    "app2_blocked",
                    "allowed_stable",
                    "camera_conditional",
                    "camera_blocked",
                    "allowed_recovery",
                    "vehicle_conditional",
                    "vehicle_blocked",
                    "app2_blocked",
                ],
                "decision": [
                    "ALLOWED",
                    "CONDITIONAL",
                    "BLOCKED",
                    "ALLOWED",
                    "CONDITIONAL",
                    "BLOCKED",
                    "ALLOWED",
                    "CONDITIONAL",
                    "BLOCKED",
                    "ALLOWED",
                    "CONDITIONAL",
                    "BLOCKED",
                    "ALLOWED",
                    "CONDITIONAL",
                    "BLOCKED",
                    "BLOCKED",
                ],
                "cvar_ms": [11.0, 17.0, 29.0, 13.0, 18.0, 31.0, 12.0, 19.0, 34.0, 14.0, 21.0, 33.0, 22.0, 22.0, 22.0, 22.0],
                "latency_p95_ms": [9.0, 15.0, 27.0, 11.0, 16.0, 29.0, 10.0, 17.0, 30.0, 12.0, 18.0, 31.0, 20.0, 21.0, 22.0, 23.0],
            }
        )
        summary = build_evaluation_summary(
            df,
            ["cvar_ms", "latency_p95_ms"],
            [
                {
                    "index": 1,
                    "train_end": 4,
                    "test_start": 4,
                    "test_end": 8,
                    "train_size": 4,
                    "test_size": 4,
                },
                {
                    "index": 2,
                    "train_end": 8,
                    "test_start": 8,
                    "test_end": 12,
                    "train_size": 8,
                    "test_size": 4,
                },
                {
                    "index": 3,
                    "train_end": 12,
                    "test_start": 12,
                    "test_end": 16,
                    "train_size": 12,
                    "test_size": 4,
                },
            ],
        )

        self.assertTrue(summary["valid"])
        self.assertEqual(summary["reasons"], [])
        self.assertEqual(summary["valid_classifier_window_count"], 3)
        self.assertEqual(summary["valid_regression_window_count"], 2)
        self.assertTrue(any("window 3: holdout regression target variance too low" in reason for reason in summary["warnings"]))

    def test_large_dataset_uses_more_temporal_windows(self):
        windows = build_temporal_evaluation_windows(6966)

        self.assertGreaterEqual(len(windows), 6)
        self.assertLessEqual(windows[0]["test_size"], 700)


if __name__ == "__main__":
    unittest.main()
