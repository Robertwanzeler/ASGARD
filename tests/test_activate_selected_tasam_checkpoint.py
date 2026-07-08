import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from activate_selected_tasam_checkpoint import activate_selected_checkpoint  # noqa: E402


def _write_checkpoint_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "tasam_marl_actors.pt").write_text("actors\n", encoding="utf-8")
    (path / "tasam_marl_checkpoint_meta.json").write_text(
        json.dumps({"du_count": 3, "du_state_dim": 10}), encoding="utf-8"
    )
    (path / "tasam_marl_summary.json").write_text(
        json.dumps(
            {
                "final_metrics": {
                    "critic_loss": 0.25,
                    "bc_loss": 0.0,
                    "action_var_mean": 0.08,
                    "selected_fraction": 0.03,
                },
                "history": [
                    {"epoch": 1, "warmup": False, "selected_agents": 3.0},
                    {"epoch": 2, "warmup": False, "selected_agents": 2.0},
                ],
            }
        ),
        encoding="utf-8",
    )


class ActivateSelectedTasamCheckpointTests(unittest.TestCase):
    def test_activation_updates_best_run_and_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            primary_dir = tmp_path / "primary_balanced_use_seed_0045_epoch_0280"
            _write_checkpoint_dir(primary_dir)

            selected_manifest = tmp_path / "selected.json"
            selected_manifest.write_text(
                json.dumps(
                    {
                        "primary": {
                            "package_dir": str(primary_dir),
                            "usage_profile": "balanced_use",
                        }
                    }
                ),
                encoding="utf-8",
            )

            active_eval = tmp_path / "tasam_candidate_evaluation_latest.json"
            active_eval.write_text(
                json.dumps(
                    {
                        "runs_root": str(tmp_path),
                        "evaluated_runs": [{"run_dir": "/tmp/old"}],
                        "best_run": {"run_dir": "/tmp/old", "readiness": "shadow_ready"},
                    }
                ),
                encoding="utf-8",
            )
            runtime_eval = tmp_path / "marl_shadow_runtime_eval_latest.json"
            runtime_eval.write_text(json.dumps({"readiness": "not_beating_live", "summary": {"latest_policy_id": ""}}), encoding="utf-8")
            gate_output = tmp_path / "marl_control_gate_latest.json"
            receipt = tmp_path / "activation_receipt_latest.json"
            backup_dir = tmp_path / "backups"

            result = activate_selected_checkpoint(
                selected_manifest_path=selected_manifest,
                role="primary",
                readiness="shadow_ready",
                active_eval_path=active_eval,
                gate_output_path=gate_output,
                runtime_eval_path=runtime_eval,
                manual_approval_path=tmp_path / "manual.json",
                backup_dir=backup_dir,
                receipt_path=receipt,
                force_noncritical=True,
                verify_shadow_load=False,
            )

            active_payload = json.loads(active_eval.read_text(encoding="utf-8"))
            gate_payload = json.loads(gate_output.read_text(encoding="utf-8"))

            self.assertEqual(active_payload["best_run"]["run_dir"], str(primary_dir.resolve()))
            self.assertEqual(active_payload["selected_checkpoint_activation"]["selected_role"], "primary")
            self.assertEqual(gate_payload["gate"]["status"], "shadow_only")
            self.assertTrue(result["backup_path"])
            self.assertTrue(Path(result["backup_path"]).exists())
            self.assertTrue(receipt.exists())

    def test_activation_requires_force_for_noncritical_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            primary_dir = tmp_path / "primary_balanced_use_seed_0045_epoch_0280"
            _write_checkpoint_dir(primary_dir)
            selected_manifest = tmp_path / "selected.json"
            selected_manifest.write_text(json.dumps({"primary": {"package_dir": str(primary_dir)}}), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "validation errors require --force-noncritical"):
                activate_selected_checkpoint(
                    selected_manifest_path=selected_manifest,
                    role="primary",
                    readiness="shadow_ready",
                    active_eval_path=tmp_path / "active.json",
                    gate_output_path=tmp_path / "gate.json",
                    runtime_eval_path=tmp_path / "runtime.json",
                    manual_approval_path=tmp_path / "manual.json",
                    backup_dir=tmp_path / "backups",
                    receipt_path=tmp_path / "receipt.json",
                    force_noncritical=False,
                    verify_shadow_load=False,
                )


if __name__ == "__main__":
    unittest.main()
