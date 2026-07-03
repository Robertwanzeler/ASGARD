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
    build_evaluation_summary,
    load_trace_data,
    normalize_training_frame,
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


if __name__ == "__main__":
    unittest.main()
