import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from package_selected_tasam_checkpoints import package_checkpoint, write_manifest  # noqa: E402


def _write_checkpoint_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name in (
        "tasam_marl_actors.pt",
        "tasam_marl_checkpoint_meta.json",
        "tasam_marl_critic1.pt",
        "tasam_marl_critic2.pt",
        "tasam_marl_target_critic1.pt",
        "tasam_marl_target_critic2.pt",
    ):
        (path / name).write_text("x\n", encoding="utf-8")


class PackageSelectedTasamCheckpointsTests(unittest.TestCase):
    def test_package_checkpoint_rebuilds_summary_at_selected_epoch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / "seed_0045" / "tasam_selective"
            checkpoint_dir = run_dir / "checkpoints" / "epoch_0280"
            _write_checkpoint_dir(checkpoint_dir)
            summary = {
                "epochs": 350,
                "completed_epochs": 350,
                "target_epochs": 400,
                "stopped_early": True,
                "stop_reason": "plateau_after_epoch_350",
                "history": [
                    {"epoch": 270, "eval_return": 49.3, "critic_loss": 0.22, "selected_fraction": 0.03, "alpha": 0.038},
                    {"epoch": 280, "eval_return": 50.3, "critic_loss": 0.26, "selected_fraction": 0.02, "alpha": 0.044},
                    {"epoch": 350, "eval_return": 38.4, "critic_loss": 0.38, "selected_fraction": 0.41, "alpha": 0.085},
                ],
                "checkpoint_records": [
                    {"epoch": 270, "checkpoint_dir": "/tmp/270", "summary_path": "/tmp/270/tasam_marl_summary.json", "metrics": {"eval_return": 49.3, "critic_loss": 0.22}},
                    {"epoch": 280, "checkpoint_dir": "/tmp/280", "summary_path": "/tmp/280/tasam_marl_summary.json", "metrics": {"eval_return": 50.3, "critic_loss": 0.26}},
                    {"epoch": 350, "checkpoint_dir": "/tmp/350", "summary_path": "/tmp/350/tasam_marl_summary.json", "metrics": {"eval_return": 38.4, "critic_loss": 0.38}},
                ],
                "best_checkpoint": {"epoch": 350, "metrics": {"eval_return": 38.4}},
                "final_metrics": {"epoch": 350, "eval_return": 38.4, "critic_loss": 0.38},
            }
            (run_dir / "tasam_marl_summary.json").mkdir(parents=True, exist_ok=True) if False else None
            (run_dir / "tasam_marl_summary.json").write_text(json.dumps(summary), encoding="utf-8")

            packaged = package_checkpoint(checkpoint_dir, output_root=tmp_path / "out", role="primary", usage_profile="balanced_use")
            packaged_summary = json.loads(Path(packaged["summary_path"]).read_text(encoding="utf-8"))

            self.assertEqual(packaged_summary["completed_epochs"], 280)
            self.assertEqual(packaged_summary["target_epochs"], 280)
            self.assertFalse(packaged_summary["stopped_early"])
            self.assertEqual(packaged_summary["final_metrics"]["epoch"], 280)
            self.assertEqual(len(packaged_summary["history"]), 2)
            self.assertEqual(packaged_summary["best_checkpoint"]["epoch"], 280)
            self.assertEqual(packaged_summary["selection_role"], "primary")

    def test_write_manifest_records_primary_and_secondary(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            primary = {"package_dir": "/tmp/primary", "usage_profile": "balanced_use", "eval_return": 10.0, "critic_loss": 0.2, "selected_fraction": 0.1}
            secondary = {"package_dir": "/tmp/secondary", "usage_profile": "aggressive_benchmark", "eval_return": 12.0, "critic_loss": 0.4, "selected_fraction": 0.8}
            json_path, md_path = write_manifest(out, primary, secondary)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "greenran.tasam_selected_checkpoint_manifest.v1")
            self.assertEqual(payload["primary"]["package_dir"], "/tmp/primary")
            self.assertEqual(payload["secondary"]["package_dir"], "/tmp/secondary")
            self.assertTrue(md_path.exists())


if __name__ == "__main__":
    unittest.main()
