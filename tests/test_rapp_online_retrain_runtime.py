#!/usr/bin/env python3

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from greenran_paths import MODELS_DIR  # noqa: E402
from rapp_online_retrain_runtime import (  # noqa: E402
    build_online_retrain_command,
    load_online_retrain_manifest,
    online_retrain_manifest_path,
    summarize_online_retrain_manifest,
)


class TestRappOnlineRetrainRuntime(unittest.TestCase):
    def test_build_online_retrain_command_targets_official_script(self):
        state_dir = Path("/tmp/greenran_state")
        command = build_online_retrain_command(
            python_bin="/usr/bin/python3",
            state_dir=state_dir,
            models_dir=MODELS_DIR,
        )

        self.assertEqual(command[0], "/usr/bin/python3")
        self.assertTrue(command[1].endswith("/scripts/run_rapp_online_retrain.py"))
        self.assertEqual(
            command[2:],
            ["--state-dir", str(state_dir), "--models-dir", str(MODELS_DIR)],
        )

    def test_online_retrain_manifest_path_uses_export_dir(self):
        state_dir = Path("/tmp/greenran_state")
        expected = state_dir / "tasam_article_export" / "rapp_online_retrain_latest.json"
        self.assertEqual(online_retrain_manifest_path(state_dir), expected)

    def test_load_online_retrain_manifest_reads_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps({"status": "promoted"}), encoding="utf-8")
            self.assertEqual(load_online_retrain_manifest(manifest_path), {"status": "promoted"})

    def test_summarize_online_retrain_manifest_extracts_metrics(self):
        summary = summarize_online_retrain_manifest(
            {
                "status": "promoted",
                "reasons": [],
                "feature_profile": "no_stage_with_slice_state",
                "promoted_files": ["best_classifier.joblib"],
                "collection_quality_gate": {
                    "status": "ready",
                    "reasons": [],
                },
                "training_report": {
                    "feature_profile": "no_stage_with_slice_state",
                    "classifier": {
                        "random_forest_accuracy": 0.99,
                        "healthy_allowed_recall": 0.97,
                        "classes": ["ALLOWED", "CONDITIONAL", "BLOCKED"],
                    },
                    "regressor": {
                        "r2": 0.88,
                    },
                },
                "next_retrain_rows": 2500,
                "new_rows_since_trigger": 120,
            }
        )

        self.assertEqual(summary["status"], "promoted")
        self.assertEqual(summary["feature_profile"], "no_stage_with_slice_state")
        self.assertEqual(summary["rf_accuracy"], 0.99)
        self.assertEqual(summary["healthy_allowed_recall"], 0.97)
        self.assertEqual(summary["regressor_r2"], 0.88)
        self.assertEqual(summary["classes"], ["ALLOWED", "CONDITIONAL", "BLOCKED"])
        self.assertEqual(summary["promoted_files"], ["best_classifier.joblib"])
        self.assertEqual(summary["collection_quality_status"], "ready")
        self.assertEqual(summary["next_retrain_rows"], 2500)
        self.assertEqual(summary["new_rows_since_trigger"], 120)


if __name__ == "__main__":
    unittest.main()
