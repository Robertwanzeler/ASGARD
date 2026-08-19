import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from status import (  # noqa: E402
    load_online_retrain_status,
    load_online_training_export,
    load_training_readiness,
    load_true_online_status,
)


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

    def test_load_true_online_status_reads_runner_status_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            online_dir = state / "tasam_true_online_real"
            model_dir = online_dir / "tasam_selective"
            model_dir.mkdir(parents=True, exist_ok=True)

            (online_dir / "true_online_status.json").write_text(
                json.dumps(
                    {
                        "status": "training",
                        "reason": "bootstrap update",
                        "written_transitions": 12761,
                        "target_epochs": 5,
                        "updates_completed": 2,
                        "model_dir": str(model_dir),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (online_dir / "true_online_state.json").write_text(
                json.dumps(
                    {
                        "updates_completed": 2,
                        "last_written_transitions": 12761,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (model_dir / "tasam_marl_summary.json").write_text(
                json.dumps(
                    {
                        "completed_epochs": 3,
                        "target_epochs": 5,
                        "final_metrics": {
                            "eval_return": 0.4264,
                            "cumulative_return": 5424.52,
                        },
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = load_true_online_status(state)

            self.assertTrue(payload["exists"])
            self.assertEqual(payload["status"], "training")
            self.assertEqual(payload["written_transitions"], 12761)
            self.assertEqual(payload["target_epochs"], 5)
            self.assertEqual(payload["completed_epochs"], 3)
            self.assertAlmostEqual(payload["eval_return"], 0.4264, places=6)
            self.assertAlmostEqual(payload["cumulative_return"], 5424.52, places=6)

    def test_load_training_readiness_reads_retrain_and_quality_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            export_dir = state / "tasam_article_export"
            export_dir.mkdir(parents=True, exist_ok=True)

            latest_export = {
                "raw_export": {"summary": {"written_transitions": 12000}},
                "trainable_export": {
                    "summary": {
                        "profile": "rapp_online_trainable",
                        "rows_after": 10900,
                        "drop_ratio": 0.1,
                        "decision_counts_after": {
                            "ALLOWED": 4308,
                            "CONDITIONAL": 3333,
                            "BLOCKED": 3259,
                        },
                    }
                },
            }
            (export_dir / "latest_export.json").write_text(
                json.dumps(latest_export, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            collection_quality = {
                "trace_exists": True,
                "rows": 10900,
                "collection_purity": {
                    "proxy_rows": 0,
                    "non_pdcp_real_rows": 0,
                    "valid_for_training_ratio": 1.0,
                },
                "class_balance": {
                    "dominant_class": "ALLOWED",
                    "dominant_ratio": 0.395,
                },
                "scenario_families": {
                    "counts": {"allowed": 4308, "camera": 2714, "vehicle": 1925, "app2": 1953},
                },
                "decision_transitions": {
                    "counts": {
                        "ALLOWED->CONDITIONAL": 252,
                        "CONDITIONAL->BLOCKED": 751,
                        "BLOCKED->CONDITIONAL": 500,
                        "BLOCKED->ALLOWED": 250,
                    },
                    "total": 1754,
                    "distinct": 4,
                    "ratio": 0.16,
                },
                "feature_constancy": {
                    "constant_feature_ratio": 0.22,
                    "constant_features": [],
                },
            }
            retrain_manifest = {
                "status": "blocked_new_rows",
                "reasons": ["new real trainable rows below cadence: 0 < 100"],
                "target_trainable": 1000,
                "last_trigger_rows": 10900,
                "new_rows_since_trigger": 0,
                "next_retrain_rows": 11000,
                "retrain_cadence": {"min_new_rows": 100},
                "collection_quality": collection_quality,
                "collection_quality_gate": {
                    "status": "ready",
                    "reasons": [],
                    "checks": {
                        "rows": 10900,
                        "dominant_ratio": 0.395,
                        "decision_transition_total": 1754,
                        "distinct_decision_transitions": 4,
                        "valid_for_training_ratio": 1.0,
                    },
                },
            }
            (export_dir / "rapp_online_retrain_latest.json").write_text(
                json.dumps(retrain_manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            payload = load_training_readiness(state)

            self.assertTrue(payload["exists"])
            self.assertEqual(payload["status"], "blocked_new_rows")
            self.assertEqual(payload["status_label"], "AGUARDANDO_CADENCIA")
            self.assertEqual(payload["rows_to_cadence"], 100)
            self.assertEqual(payload["quality_status"], "ready")
            self.assertEqual(payload["decision_transition_total"], 1754)
            self.assertEqual(payload["scenario_family_counts"]["vehicle"], 1925)
            self.assertIn("aguardar +100 linhas trainable reais", payload["action"])


if __name__ == "__main__":
    unittest.main()
