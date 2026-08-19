import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_tasam_article_offline_rounds import (  # noqa: E402
    _build_round_command,
    _evaluate_rounds,
)


def _write_round(root: Path, index: int, mode_payloads: dict[str, dict], written_transitions: int = 3753) -> Path:
    round_dir = root / f"round_{index:04d}"
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "tasam_article_export_summary.json").write_text(
        json.dumps(
            {
                "written_transitions": written_transitions,
                "candidate_snapshots": written_transitions + 3,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for mode, final_metrics in mode_payloads.items():
        mode_dir = round_dir / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        (mode_dir / "tasam_marl_summary.json").write_text(
            json.dumps(
                {
                    "trainer_backend": "article_sac",
                    "article_faithful_algorithm": True,
                    "epochs": 25,
                    "du_count": 6,
                    "sam_rho": 0.5,
                    "sam_rho_final": 0.01,
                    "actor_sam_rho": 0.5,
                    "actor_sam_rho_final": 0.01,
                    "critic_sam_rho": 0.5,
                    "critic_sam_rho_final": 0.01,
                    "activation": "tanh",
                    "actor_hidden_dims": [300, 400, 400],
                    "critic_hidden_dims": [300, 400, 400],
                    "final_metrics": final_metrics,
                    "history": [final_metrics],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return round_dir


class TestTASAMOfflineRounds(unittest.TestCase):
    def test_build_round_command_targets_single_round_output(self):
        class Args:
            db = "/tmp/tasam_article.db"
            epochs = 25
            modes = "tasam_selective,both_sam"
            seed = 42
            limit = 100
            allow_proxy = True
            train_python = "/tmp/venv/bin/python"

        cmd = _build_round_command(Args(), ROOT / "runs" / "tasam_article_reproduction" / "offline_rounds" / "round_0001")
        self.assertIn("run_tasam_article_reproduction.py", cmd[1])
        self.assertIn("--output-root", cmd)
        self.assertIn("round_0001", cmd[cmd.index("--output-root") + 1])
        self.assertIn("--epochs", cmd)
        self.assertIn("--limit", cmd)
        self.assertIn("--allow-proxy", cmd)
        self.assertIn("--train-python", cmd)

    def test_evaluate_rounds_marks_converged_after_three_plateaus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_round(
                root,
                1,
                {
                    "tasam_selective": {
                        "actor_loss": -1.0,
                        "critic_loss": 0.0002,
                        "eval_return": 10.0,
                        "cumulative_return": 100.0,
                        "selected_fraction": 0.10,
                        "action_var_mean": 0.10,
                    }
                },
            )
            _write_round(
                root,
                2,
                {
                    "tasam_selective": {
                        "actor_loss": -1.1,
                        "critic_loss": 0.00021,
                        "eval_return": 10.1,
                        "cumulative_return": 101.0,
                        "selected_fraction": 0.10,
                        "action_var_mean": 0.095,
                    }
                },
            )
            _write_round(
                root,
                3,
                {
                    "tasam_selective": {
                        "actor_loss": -1.1,
                        "critic_loss": 0.00019,
                        "eval_return": 10.15,
                        "cumulative_return": 101.5,
                        "selected_fraction": 0.10,
                        "action_var_mean": 0.094,
                    }
                },
            )
            _write_round(
                root,
                4,
                {
                    "tasam_selective": {
                        "actor_loss": -1.09,
                        "critic_loss": 0.00018,
                        "eval_return": 10.18,
                        "cumulative_return": 101.8,
                        "selected_fraction": 0.10,
                        "action_var_mean": 0.093,
                    }
                },
            )

            payload = _evaluate_rounds(root, ("tasam_selective",), improvement_threshold_pct=2.0, plateau_rounds=3)
            self.assertEqual(payload["round_count"], 4)
            self.assertTrue(payload["converged"])
            self.assertEqual(payload["plateau_streak"], 3)
            self.assertEqual(payload["rounds"][-1]["status"], "converged")
            self.assertEqual(payload["best_round"]["round_id"], "round_0004")

    def test_evaluate_rounds_marks_unstable_when_metrics_degrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_round(
                root,
                1,
                {
                    "tasam_selective": {
                        "actor_loss": -1.0,
                        "critic_loss": 0.0002,
                        "eval_return": 10.0,
                        "cumulative_return": 100.0,
                        "selected_fraction": 0.10,
                        "action_var_mean": 0.10,
                    }
                },
            )
            _write_round(
                root,
                2,
                {
                    "tasam_selective": {
                        "actor_loss": -0.8,
                        "critic_loss": 0.0005,
                        "eval_return": 10.05,
                        "cumulative_return": 100.5,
                        "selected_fraction": 0.10,
                        "action_var_mean": 0.03,
                    }
                },
            )

            payload = _evaluate_rounds(root, ("tasam_selective",), improvement_threshold_pct=2.0, plateau_rounds=3)
            self.assertFalse(payload["converged"])
            self.assertEqual(payload["rounds"][1]["status"], "unstable")
            self.assertFalse(payload["rounds"][1]["stable_vs_previous"])


if __name__ == "__main__":
    unittest.main()
