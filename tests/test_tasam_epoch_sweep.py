import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_tasam_article_epoch_sweep import (  # noqa: E402
    build_sweep_summary,
    build_train_command,
)


def _write_checkpoint(mode_dir: Path, epoch: int, metrics: dict) -> None:
    checkpoint_dir = mode_dir / "checkpoints" / f"epoch_{epoch:04d}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "epochs": epoch,
        "completed_epochs": epoch,
        "target_epochs": 200,
        "final_metrics": {"epoch": epoch, **metrics},
        "history": [{"epoch": epoch, **metrics}],
    }
    (checkpoint_dir / "tasam_marl_summary.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_seed_run(root: Path, seed: int, checkpoints: list[tuple[int, dict]], *, stopped_early=False, stop_reason="") -> None:
    mode_dir = root / f"seed_{seed:04d}" / "tasam_selective"
    mode_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_records = []
    best_checkpoint = None
    for epoch, metrics in checkpoints:
        _write_checkpoint(mode_dir, epoch, metrics)
        checkpoint_records.append(
            {
                "epoch": epoch,
                "checkpoint_dir": str((mode_dir / "checkpoints" / f"epoch_{epoch:04d}").resolve()),
                "summary_path": str((mode_dir / "checkpoints" / f"epoch_{epoch:04d}" / "tasam_marl_summary.json").resolve()),
                "metrics": {"epoch": epoch, **metrics},
            }
        )
        candidate = checkpoint_records[-1]
        if best_checkpoint is None or float(candidate["metrics"]["eval_return"]) > float(best_checkpoint["metrics"]["eval_return"]):
            best_checkpoint = candidate

    final_epoch, final_metrics = checkpoints[-1]
    summary = {
        "epochs": final_epoch,
        "completed_epochs": final_epoch,
        "target_epochs": 200,
        "stopped_early": stopped_early,
        "stop_reason": stop_reason,
        "best_checkpoint": best_checkpoint,
        "checkpoint_records": checkpoint_records,
        "final_metrics": {"epoch": final_epoch, **final_metrics},
        "history": [{"epoch": epoch, **metrics} for epoch, metrics in checkpoints],
    }
    (mode_dir / "tasam_marl_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )


class TestTasamEpochSweep(unittest.TestCase):
    def test_build_train_command_carries_resume_and_early_stop_options(self):
        class Args:
            output_root = str(ROOT / "runs" / "tasam_sweep")
            mode = "tasam_selective"
            trace_jsonl = str(ROOT / "runs" / "trace.jsonl")
            train_python = "/usr/bin/python3"
            epochs = 25
            max_epochs = 200
            trainer_backend = "article_sac"
            lr = 1e-4
            alpha_lr = 1e-4
            td_var_threshold = 0.01
            min_selected_fraction = 0.10
            warmup_epochs = 2
            bc_weight = 0.0
            value_weight = 0.0
            gamma = 0.99
            tau = 0.01
            alpha_init = 0.03
            target_entropy_scale = 1.0
            batch_size = 128
            actor_update_interval = 1
            activation = "tanh"
            actor_sam_rho = 0.5
            actor_sam_rho_final = 0.01
            critic_sam_rho = 0.5
            critic_sam_rho_final = 0.01
            checkpoint_every = 10
            milestone_epochs = "25,50,100,150,200"
            resume_ignore_early_stop = True
            early_stop_patience_checkpoints = 2
            early_stop_min_epoch = 50
            early_stop_min_improvement_pct = 2.0
            article_hidden = True

        cmd = build_train_command(Args(), 42)
        self.assertIn("--resume", cmd)
        self.assertIn("--resume-ignore-early-stop", cmd)
        self.assertIn("--max-epochs", cmd)
        self.assertIn("--checkpoint-every", cmd)
        self.assertIn("--early-stop-patience-checkpoints", cmd)
        self.assertIn("seed_0042", cmd[cmd.index("--output-dir") + 1])

    def test_build_sweep_summary_aggregates_best_epoch_and_global_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "tasam_article_trace.jsonl"
            trace.write_text('{"x":1}\n', encoding="utf-8")
            _write_seed_run(
                root,
                42,
                [
                    (25, {"eval_return": 2.0, "cumulative_return": 100.0, "critic_loss": 0.20, "action_var_mean": 0.10, "selected_fraction": 1.0}),
                    (50, {"eval_return": 4.0, "cumulative_return": 140.0, "critic_loss": 0.18, "action_var_mean": 0.12, "selected_fraction": 1.0}),
                ],
            )
            _write_seed_run(
                root,
                43,
                [
                    (25, {"eval_return": 1.5, "cumulative_return": 90.0, "critic_loss": 0.21, "action_var_mean": 0.09, "selected_fraction": 1.0}),
                    (50, {"eval_return": 4.5, "cumulative_return": 150.0, "critic_loss": 0.19, "action_var_mean": 0.11, "selected_fraction": 1.0}),
                ],
                stopped_early=True,
                stop_reason="plateau_after_epoch_50",
            )

            summary = build_sweep_summary(root, trace, "tasam_selective", (42, 43))

            self.assertEqual(summary["seed_count"], 2)
            self.assertEqual(summary["best_epoch_by_mean"]["epoch"], 50)
            self.assertEqual(summary["best_global_checkpoint"]["seed"], 43)
            self.assertEqual(summary["best_global_checkpoint"]["epoch"], 50)
            epoch50 = next(item for item in summary["epoch_aggregate"] if item["epoch"] == 50)
            self.assertAlmostEqual(epoch50["eval_return_mean"], 4.25, places=5)


if __name__ == "__main__":
    unittest.main()
