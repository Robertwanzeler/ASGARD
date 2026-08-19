import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_tasam_true_online_real.py"
SPEC = importlib.util.spec_from_file_location("run_tasam_true_online_real", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class TrueOnlineTasamRealTests(unittest.TestCase):
    def test_should_train_bootstrap(self):
        ready, reason = MODULE.should_train(
            current_snapshot_count=1200,
            previous_snapshot_count=0,
            min_new_snapshots=300,
            target_epochs=25,
        )
        self.assertTrue(ready)
        self.assertIn("bootstrap", reason)

    def test_should_train_waits_for_new_snapshots(self):
        ready, reason = MODULE.should_train(
            current_snapshot_count=1250,
            previous_snapshot_count=1200,
            min_new_snapshots=300,
            target_epochs=35,
        )
        self.assertFalse(ready)
        self.assertIn("+50 < +300", reason)

    def test_compute_target_epochs_grows_incrementally(self):
        self.assertEqual(MODULE.compute_target_epochs(0, 25, 10), 25)
        self.assertEqual(MODULE.compute_target_epochs(25, 25, 10), 35)

    def test_default_online_thresholds_match_real_collection_policy(self):
        parser = MODULE.build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.min_new_snapshots, 500)
        self.assertEqual(args.min_trainable_transitions, 1500)
        self.assertEqual(args.bootstrap_epochs, 5)
        self.assertEqual(args.epochs_per_update, 2)

    def test_build_train_command_enables_resume(self):
        parser = MODULE.build_parser()
        args = parser.parse_args([])
        args = MODULE.normalize_args(args)
        cmd = MODULE.build_train_command(args, 45)
        self.assertEqual(cmd[0], str(MODULE.DEFAULT_TRAIN_PYTHON if MODULE.DEFAULT_TRAIN_PYTHON.exists() else MODULE.sys.executable))
        self.assertIn("train_tasam_marl.py", cmd[1])
        self.assertIn("--resume", cmd)
        self.assertIn("--resume-ignore-early-stop", cmd)
        self.assertIn("45", cmd)

    def test_normalize_args_derives_paths_from_output_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parser = MODULE.build_parser()
            args = parser.parse_args(["--output-root", str(tmp_path / "canonical")])
            args = MODULE.normalize_args(args)
            self.assertEqual(args.trace_jsonl, tmp_path / "canonical" / "tasam_true_online_real_trace.jsonl")
            self.assertEqual(args.export_summary_json, tmp_path / "canonical" / "tasam_true_online_real_export_summary.json")
            self.assertEqual(args.model_dir, tmp_path / "canonical" / "tasam_selective")
            self.assertEqual(args.status_json, tmp_path / "canonical" / "true_online_status.json")
            self.assertEqual(args.state_json, tmp_path / "canonical" / "true_online_state.json")

    def test_normalize_args_migrates_legacy_default_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            original_default = MODULE.DEFAULT_OUTPUT_ROOT
            original_legacy = MODULE.LEGACY_OUTPUT_ROOT
            try:
                MODULE.DEFAULT_OUTPUT_ROOT = tmp_path / "collection" / "tasam_true_online_real"
                MODULE.LEGACY_OUTPUT_ROOT = tmp_path / "legacy"
                (MODULE.LEGACY_OUTPUT_ROOT / "tasam_selective").mkdir(parents=True, exist_ok=True)
                (MODULE.LEGACY_OUTPUT_ROOT / "true_online_status.json").write_text(
                    json.dumps({"status": "trained"}) + "\n",
                    encoding="utf-8",
                )
                (MODULE.LEGACY_OUTPUT_ROOT / "tasam_selective" / "tasam_marl_summary.json").write_text(
                    json.dumps({"completed_epochs": 5}) + "\n",
                    encoding="utf-8",
                )
                parser = MODULE.build_parser()
                args = parser.parse_args(["--output-root", str(MODULE.DEFAULT_OUTPUT_ROOT)])
                args = MODULE.normalize_args(args)
                self.assertTrue((MODULE.DEFAULT_OUTPUT_ROOT / "true_online_status.json").exists())
                self.assertTrue((MODULE.DEFAULT_OUTPUT_ROOT / "tasam_selective" / "tasam_marl_summary.json").exists())
                self.assertFalse((MODULE.LEGACY_OUTPUT_ROOT / "true_online_status.json").exists())
                self.assertEqual(args.status_json, MODULE.DEFAULT_OUTPUT_ROOT / "true_online_status.json")
            finally:
                MODULE.DEFAULT_OUTPUT_ROOT = original_default
                MODULE.LEGACY_OUTPUT_ROOT = original_legacy

    def test_run_once_stays_idle_when_not_enough_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parser = MODULE.build_parser()
            args = parser.parse_args(
                [
                    "--db",
                    str(tmp_path / "missing.db"),
                    "--output-root",
                    str(tmp_path / "out"),
                    "--status-json",
                    str(tmp_path / "out" / "status.json"),
                    "--state-json",
                    str(tmp_path / "out" / "state.json"),
                    "--once",
                    "--dry-run",
                ]
            )
            args = MODULE.normalize_args(args)
            payload = MODULE.run_once(args)
            self.assertEqual(payload["status"], "idle")
            self.assertIn("no runtime snapshots", payload["reason"])


if __name__ == "__main__":
    unittest.main()
