import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_rapp_online_retrain import evaluate_readiness, evaluate_training_report, main  # noqa: E402


class TestRunRappOnlineRetrain(unittest.TestCase):
    def test_evaluate_training_report_rejects_weak_metrics(self):
        status, reasons, checks = evaluate_training_report(
            {
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
        )
        self.assertEqual(status, "promote")
        self.assertEqual(reasons, [])
        self.assertAlmostEqual(checks["regressor_r2"], 0.61, places=6)

    def test_evaluate_training_report_marks_invalid_evaluation_window(self):
        status, reasons, checks = evaluate_training_report(
            {
                "evaluation": {
                    "valid": False,
                    "reasons": [
                        "holdout feature diversity too low: 1/171 unique rows",
                    ],
                },
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
            (export_dir / "rapp_online_trainable_trace.jsonl").write_text("{}", encoding="utf-8")
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
            self.assertIn("train_ml_model.py", " ".join(payload["command"]))

    def test_main_blocks_when_new_rows_do_not_reach_retrain_cadence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            export_dir = state / "tasam_article_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            (export_dir / "rapp_online_trainable_trace.jsonl").write_text("{}", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
