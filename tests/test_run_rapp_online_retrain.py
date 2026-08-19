import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_rapp_online_retrain import evaluate_readiness, evaluate_training_report, main  # noqa: E402


def write_good_trace(path: Path, cycles: int = 10) -> None:
    sequence = [
        ("allowed_stable", "ALLOWED"),
        ("camera_conditional", "CONDITIONAL"),
        ("camera_blocked", "BLOCKED"),
        ("vehicle_conditional", "CONDITIONAL"),
        ("app2_blocked", "BLOCKED"),
        ("allowed_recovery", "ALLOWED"),
    ]
    rows = []
    timestamp = 1783107000
    for cycle in range(cycles):
        for offset, (stage, decision) in enumerate(sequence):
            index = cycle * len(sequence) + offset
            latency_us = 8000.0 + (offset * 4500.0) + index
            cvar_us = 10000.0 + (offset * 5500.0) + index
            rows.append(
                {
                    "timestamp": timestamp + index * 5,
                    "scenario_stage": stage,
                    "collection_quality": {
                        "collector_mode": "pdcp_real",
                        "real_latency_sample_count": 10.0,
                        "proxy_latency_sample_count": 0.0,
                        "pdcp_real": True,
                        "valid_for_training": True,
                    },
                    "metrics": {
                        "cvar_per_ue_us": cvar_us,
                        "latency_p95_us": latency_us,
                        "latency_p95_per_ue_us": latency_us,
                        "global_avg_latency_us": max(1000.0, latency_us - 1200.0),
                        "variance_per_ue_us2": 1_000_000.0 + (index * 125_000.0),
                        "total_active_cameras": 1 + (index % 5),
                        "total_active_ues": 10 + (index % 7),
                        "total_critical_ues": index % 3,
                        "throughput_kbps": 12_000.0 + (index * 220.0),
                        "global_packet_loss_rate": 0.001 * ((index % 5) + 1),
                        "global_jitter_us": 80.0 * (index % 6),
                        "total_tx_bytes": 1000.0 + (index * 31.0),
                        "total_rx_bytes": 900.0 + (index * 23.0),
                    },
                    "decision": {
                        "decision": decision,
                        "confidence": 0.95,
                    },
                }
            )
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def write_low_information_trace(path: Path, rows: int = 60) -> None:
    payload = {
        "scenario_stage": "allowed_stable",
        "collection_quality": {
            "collector_mode": "pdcp_real",
            "real_latency_sample_count": 10.0,
            "proxy_latency_sample_count": 0.0,
            "pdcp_real": True,
            "valid_for_training": True,
        },
        "metrics": {
            "cvar_per_ue_us": 12000.0,
            "latency_p95_us": 9000.0,
            "latency_p95_per_ue_us": 9000.0,
            "global_avg_latency_us": 7800.0,
            "variance_per_ue_us2": 1_000_000.0,
            "total_active_cameras": 4,
            "total_active_ues": 10,
            "total_critical_ues": 0,
            "throughput_kbps": 12000.0,
            "global_packet_loss_rate": 0.001,
            "global_jitter_us": 0.0,
            "total_tx_bytes": 1000.0,
            "total_rx_bytes": 1000.0,
        },
        "decision": {
            "decision": "ALLOWED",
            "confidence": 0.95,
        },
    }
    lines = []
    timestamp = 1783107000
    for index in range(rows):
        row = dict(payload)
        row["timestamp"] = timestamp + index * 5
        lines.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestRunRappOnlineRetrain(unittest.TestCase):
    def test_evaluate_training_report_rejects_weak_metrics(self):
        status, reasons, checks = evaluate_training_report(
            {
                "feature_profile": "no_stage",
                "classifier": {
                    "random_forest_accuracy": 0.58,
                    "healthy_allowed_recall": float("nan"),
                },
                "regressor": {
                    "r2": -1.86,
                },
            },
            min_rf_accuracy=0.70,
            min_r2=0.0,
            min_healthy_allowed_recall=0.70,
            expected_feature_profile="no_stage",
        )
        self.assertEqual(status, "reject_metrics")
        self.assertTrue(any("rf_accuracy below gate" in reason for reason in reasons))
        self.assertTrue(any("regressor_r2 below gate" in reason for reason in reasons))
        self.assertAlmostEqual(checks["rf_accuracy"], 0.58, places=6)

    def test_evaluate_training_report_promotes_strong_metrics(self):
        status, reasons, checks = evaluate_training_report(
            {
                "evaluation": {
                    "valid": True,
                    "reasons": [],
                },
                "feature_profile": "no_stage",
                "features": ["latency_p95_ms", "throughput_mbps"],
                "classifier": {
                    "random_forest_accuracy": 0.84,
                    "healthy_allowed_recall": 0.92,
                },
                "regressor": {
                    "r2": 0.61,
                },
            },
            min_rf_accuracy=0.70,
            min_r2=0.0,
            min_healthy_allowed_recall=0.70,
            expected_feature_profile="no_stage",
        )
        self.assertEqual(status, "promote")
        self.assertEqual(reasons, [])
        self.assertAlmostEqual(checks["regressor_r2"], 0.61, places=6)

    def test_evaluate_training_report_rejects_baseline_regression(self):
        status, reasons, checks = evaluate_training_report(
            {
                "evaluation": {
                    "valid": True,
                    "reasons": [],
                },
                "feature_profile": "no_stage",
                "features": ["latency_p95_ms", "throughput_mbps"],
                "classifier": {
                    "random_forest_accuracy": 0.84,
                    "healthy_allowed_recall": 0.92,
                },
                "regressor": {
                    "r2": 0.61,
                },
            },
            min_rf_accuracy=0.70,
            min_r2=0.0,
            min_healthy_allowed_recall=0.70,
            expected_feature_profile="no_stage",
            baseline_comparison={
                "available": True,
                "status": "ready",
                "delta": {"rf_accuracy": 0.005},
                "recall_drop_by_class": {
                    "CONDITIONAL": 0.04,
                    "BLOCKED": 0.01,
                },
            },
            min_accuracy_gain=0.01,
            max_class_recall_drop=0.03,
        )
        self.assertEqual(status, "reject_metrics")
        self.assertTrue(any("rf_accuracy gain below baseline gate" in reason for reason in reasons))
        self.assertTrue(any("CONDITIONAL recall regressed beyond gate" in reason for reason in reasons))
        self.assertTrue(checks["baseline_comparison_available"])

    def test_evaluate_training_report_promotes_safe_accuracy_tie_vs_baseline(self):
        status, reasons, checks = evaluate_training_report(
            {
                "evaluation": {
                    "valid": True,
                    "reasons": [],
                },
                "feature_profile": "no_stage",
                "features": ["latency_p95_ms", "throughput_mbps"],
                "classifier": {
                    "random_forest_accuracy": 1.0,
                    "healthy_allowed_recall": 1.0,
                },
                "regressor": {
                    "r2": 0.61,
                },
            },
            min_rf_accuracy=0.70,
            min_r2=0.0,
            min_healthy_allowed_recall=0.70,
            expected_feature_profile="no_stage",
            baseline_comparison={
                "available": True,
                "status": "ready",
                "delta": {
                    "rf_accuracy": 0.0,
                    "healthy_allowed_recall": 0.0,
                },
                "recall_drop_by_class": {
                    "CONDITIONAL": 0.0,
                    "BLOCKED": 0.0,
                },
            },
            min_accuracy_gain=0.01,
            max_class_recall_drop=0.03,
        )
        self.assertEqual(status, "promote")
        self.assertEqual(reasons, [])
        self.assertTrue(checks["baseline_comparison_available"])

    def test_evaluate_training_report_rejects_stage_leakage_profile(self):
        status, reasons, checks = evaluate_training_report(
            {
                "evaluation": {
                    "valid": True,
                    "reasons": [],
                },
                "feature_profile": "full",
                "features": ["stage_allowed_stable", "latency_p95_ms"],
                "classifier": {
                    "random_forest_accuracy": 1.0,
                    "healthy_allowed_recall": 1.0,
                },
                "regressor": {
                    "r2": 0.86,
                },
            },
            min_rf_accuracy=0.70,
            min_r2=0.0,
            min_healthy_allowed_recall=0.70,
            expected_feature_profile="no_stage",
        )
        self.assertEqual(status, "invalid_evaluation")
        self.assertTrue(any("feature_profile mismatch" in reason for reason in reasons))
        self.assertEqual(checks["feature_profile"], "full")

    def test_evaluate_training_report_marks_invalid_evaluation_window(self):
        status, reasons, checks = evaluate_training_report(
            {
                "evaluation": {
                    "valid": False,
                    "reasons": [
                        "holdout feature diversity too low: 1/171 unique rows",
                    ],
                },
                "feature_profile": "no_stage",
                "features": ["latency_p95_ms"],
                "classifier": {
                    "random_forest_accuracy": 0.30,
                },
                "regressor": {
                    "r2": -1.0,
                },
            },
            min_rf_accuracy=0.70,
            min_r2=0.0,
            min_healthy_allowed_recall=0.70,
            expected_feature_profile="no_stage",
        )
        self.assertEqual(status, "invalid_evaluation")
        self.assertTrue(any("feature diversity too low" in reason for reason in reasons))
        self.assertFalse(checks["evaluation_valid"])

    def test_evaluate_readiness_blocks_when_classes_missing(self):
        status, reasons = evaluate_readiness(
            {
                "rows_after": 57,
                "decision_counts_after": {"ALLOWED": 21, "CONDITIONAL": 19, "BLOCKED": 1},
            },
            min_trainable=50,
            min_class_count=5,
            required_classes=["ALLOWED", "CONDITIONAL", "BLOCKED"],
        )
        self.assertEqual(status, "blocked")
        self.assertTrue(any("class BLOCKED below minimum" in reason for reason in reasons))

    def test_main_dry_run_writes_manifest_when_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            export_dir = state / "tasam_article_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            write_good_trace(export_dir / "rapp_online_trainable_trace.jsonl", cycles=10)
            (export_dir / "rapp_online_trainable_summary.json").write_text(
                json.dumps(
                    {
                        "rows_after": 60,
                        "decision_counts_after": {
                            "ALLOWED": 20,
                            "CONDITIONAL": 20,
                            "BLOCKED": 20,
                        },
                    }
                ) + "\n",
                encoding="utf-8",
            )
            manifest = export_dir / "rapp_online_retrain_latest.json"
            argv = [
                "run_rapp_online_retrain.py",
                "--state-dir",
                str(state),
                "--models-dir",
                str(root / "models"),
                "--dry-run",
            ]
            with mock.patch.object(sys, "argv", argv):
                rc = main()
            self.assertEqual(rc, 0)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "dry_run")
            self.assertEqual(payload["target_trainable"], 1000)
            self.assertEqual(payload["last_trigger_rows"], 0)
            self.assertEqual(payload["new_rows_since_trigger"], 60)
            self.assertEqual(payload["next_retrain_rows"], 100)
            self.assertEqual(payload["feature_profile"], "no_stage_with_slice_state")
            self.assertEqual(payload["promotion_gate"]["feature_profile"], "no_stage_with_slice_state")
            self.assertIn("train_ml_model.py", " ".join(payload["command"]))
            self.assertIn("--feature-profile", payload["command"])
            self.assertIn("no_stage_with_slice_state", payload["command"])
            self.assertEqual(payload["collection_quality_gate"]["status"], "ready")
            self.assertEqual(payload["collection_quality"]["collection_purity"]["proxy_rows"], 0)

    def test_main_blocks_when_new_rows_do_not_reach_retrain_cadence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            export_dir = state / "tasam_article_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            write_good_trace(export_dir / "rapp_online_trainable_trace.jsonl", cycles=30)
            (export_dir / "rapp_online_trainable_summary.json").write_text(
                json.dumps(
                    {
                        "rows_after": 180,
                        "decision_counts_after": {
                            "ALLOWED": 60,
                            "CONDITIONAL": 60,
                            "BLOCKED": 60,
                        },
                    }
                ) + "\n",
                encoding="utf-8",
            )
            (export_dir / "rapp_online_retrain_latest.json").write_text(
                json.dumps(
                    {
                        "status": "promoted",
                        "summary": {"rows_after": 150},
                    }
                ) + "\n",
                encoding="utf-8",
            )

            argv = [
                "run_rapp_online_retrain.py",
                "--state-dir",
                str(state),
                "--models-dir",
                str(root / "models"),
            ]
            with mock.patch.object(sys, "argv", argv):
                rc = main()
            self.assertEqual(rc, 0)
            payload = json.loads((export_dir / "rapp_online_retrain_latest.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "blocked_new_rows")
            self.assertEqual(payload["last_trigger_rows"], 150)
            self.assertEqual(payload["new_rows_since_trigger"], 30)
            self.assertEqual(payload["next_retrain_rows"], 250)
            self.assertTrue(any("below cadence" in reason for reason in payload["reasons"]))

    def test_main_blocks_low_information_collection_before_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            export_dir = state / "tasam_article_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            write_low_information_trace(export_dir / "rapp_online_trainable_trace.jsonl", rows=60)
            (export_dir / "rapp_online_trainable_summary.json").write_text(
                json.dumps(
                    {
                        "rows_after": 60,
                        "decision_counts_after": {
                            "ALLOWED": 60,
                            "CONDITIONAL": 0,
                            "BLOCKED": 0,
                        },
                    }
                ) + "\n",
                encoding="utf-8",
            )

            argv = [
                "run_rapp_online_retrain.py",
                "--state-dir",
                str(state),
                "--models-dir",
                str(root / "models"),
                "--min-class-count",
                "0",
                "--required-classes",
                "ALLOWED",
            ]
            with mock.patch.object(sys, "argv", argv):
                rc = main()
            self.assertEqual(rc, 0)
            payload = json.loads((export_dir / "rapp_online_retrain_latest.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "blocked_quality")
            self.assertTrue(any("dominant decision class above gate" in reason for reason in payload["reasons"]))
            self.assertTrue(any("constant feature ratio above gate" in reason for reason in payload["reasons"]))
            self.assertTrue(any("required scenario families missing" in reason for reason in payload["reasons"]))


if __name__ == "__main__":
    unittest.main()
