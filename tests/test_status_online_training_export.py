import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from status import load_online_retrain_status, load_online_training_export  # noqa: E402


class TestStatusOnlineTrainingExport(unittest.TestCase):
    def test_load_online_training_export_reads_manifest_summaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            export_dir = state / "tasam_article_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "raw_export": {
                    "summary": {
                        "written_transitions": 120,
                    }
                },
                "trainable_export": {
                    "summary": {
                        "profile": "rapp_online_trainable",
                        "rows_after": 48,
                        "drop_ratio": 0.6,
                        "decision_counts_after": {
                            "ALLOWED": 20,
                            "CONDITIONAL": 18,
                            "BLOCKED": 10,
                        },
                        "stage_counts_after": {
                            "allowed_stable": 20,
                            "camera_conditional": 18,
                            "camera_blocked": 10,
                        },
                        "drop_reasons": {
                            "unexpected_stage": 40,
                        },
                    }
                },
            }
            (export_dir / "latest_export.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            payload = load_online_training_export(state)

            self.assertTrue(payload["manifest_exists"])
            self.assertEqual(payload["profile"], "rapp_online_trainable")
            self.assertEqual(payload["raw_rows"], 120)
            self.assertEqual(payload["trainable_rows"], 48)
            self.assertAlmostEqual(payload["drop_ratio"], 0.6, places=6)
            self.assertEqual(payload["decision_counts"]["BLOCKED"], 10)
            self.assertEqual(payload["drop_reasons"]["unexpected_stage"], 40)

    def test_load_online_retrain_status_reads_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            export_dir = state / "tasam_article_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "status": "rejected_metrics",
                "metric_gate_status": "reject_metrics",
                "metric_gate_checks": {
                    "regressor_r2": -1.2,
                    "evaluation_valid": False,
                },
                "target_trainable": 1000,
                "last_trigger_rows": 200,
                "new_rows_since_trigger": 35,
                "next_retrain_rows": 300,
                "retrain_cadence": {
                    "min_new_rows": 100,
                },
                "reasons": [],
                "training_report": {
                    "classifier": {
                        "selected_classifier": "random_forest",
                        "random_forest_accuracy": 0.82,
                    }
                },
            }
            (export_dir / "rapp_online_retrain_latest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            payload = load_online_retrain_status(state)

            self.assertTrue(payload["exists"])
            self.assertEqual(payload["status"], "rejected_metrics")
            self.assertEqual(payload["selected_classifier"], "random_forest")
            self.assertEqual(payload["metric_gate_status"], "reject_metrics")
            self.assertEqual(payload["target_trainable"], 1000)
            self.assertEqual(payload["last_trigger_rows"], 200)
            self.assertEqual(payload["new_rows_since_trigger"], 35)
            self.assertEqual(payload["next_retrain_rows"], 300)
            self.assertEqual(payload["min_new_rows"], 100)
            self.assertFalse(payload["evaluation_valid"])


if __name__ == "__main__":
    unittest.main()
