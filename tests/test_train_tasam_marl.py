import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "drlexp" / "training"))
sys.path.insert(0, str(ROOT / "drlexp" / "src"))

import torch

import train_tasam_marl as train_module  # noqa: E402
from train_tasam_marl import (  # noqa: E402
    _checkpoint_record,
    _should_export_checkpoint,
    main,
    parse_epoch_points,
)
from drl.ta_sam_marl_sac import TASAMArticleSACTrainer  # noqa: E402


class TestTrainTasamMarlHelpers(unittest.TestCase):
    def test_parse_epoch_points_sorts_and_deduplicates(self):
        self.assertEqual(parse_epoch_points("50,25,100,25"), (25, 50, 100))

    def test_should_export_checkpoint_honors_cadence_milestones_and_final_epoch(self):
        self.assertTrue(_should_export_checkpoint(10, total_epochs=200, checkpoint_every=10, milestone_epochs=()))
        self.assertTrue(_should_export_checkpoint(25, total_epochs=200, checkpoint_every=10, milestone_epochs=(25, 50)))
        self.assertFalse(_should_export_checkpoint(26, total_epochs=200, checkpoint_every=10, milestone_epochs=(25, 50)))
        self.assertTrue(_should_export_checkpoint(200, total_epochs=200, checkpoint_every=10, milestone_epochs=(25, 50)))

    def test_checkpoint_record_marks_converged_after_plateau_patience(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_dir = Path(tmp) / "epoch_0060"
            best_checkpoint = {
                "epoch": 50,
                "checkpoint_dir": str((Path(tmp) / "epoch_0050").resolve()),
                "summary_path": str((Path(tmp) / "epoch_0050" / "tasam_marl_summary.json").resolve()),
                "metrics": {
                    "eval_return": 10.0,
                    "cumulative_return": 100.0,
                    "critic_loss": 0.2,
                    "action_var_mean": 0.10,
                    "selected_fraction": 1.0,
                },
            }
            record, best, streak, stop_now, stop_reason = _checkpoint_record(
                epoch=60,
                checkpoint_dir=checkpoint_dir,
                metrics={
                    "eval_return": 10.05,
                    "cumulative_return": 100.5,
                    "critic_loss": 0.21,
                    "action_var_mean": 0.095,
                    "selected_fraction": 1.0,
                },
                previous_checkpoint_metrics={
                    "eval_return": 10.02,
                    "cumulative_return": 100.2,
                    "critic_loss": 0.20,
                    "action_var_mean": 0.10,
                    "selected_fraction": 1.0,
                },
                best_checkpoint=best_checkpoint,
                plateau_streak=1,
                min_epoch=50,
                improvement_threshold_pct=2.0,
                patience_checkpoints=2,
            )
            self.assertEqual(record["status"], "converged")
            self.assertEqual(streak, 2)
            self.assertTrue(stop_now)
            self.assertIn("plateau_after_epoch_60", stop_reason)
            self.assertEqual(best["epoch"], 60)


class TestArticleSACStateRoundTrip(unittest.TestCase):
    def test_training_state_round_trip_restores_weights_and_optimizers(self):
        trainer = TASAMArticleSACTrainer(
            du_count=1,
            du_state_dim=3,
            global_state_dim=4,
            actor_hidden_dims=(8,),
            critic_hidden_dims=(8,),
            seed=7,
        )
        first_actor_param = next(trainer.actors.parameters())
        first_actor_param.data.fill_(1.23)
        trainer.log_alpha.data.fill_(-2.5)
        trainer.actor_opt.rho = 0.12
        payload = trainer.training_state_dict()

        restored = TASAMArticleSACTrainer(
            du_count=1,
            du_state_dim=3,
            global_state_dim=4,
            actor_hidden_dims=(8,),
            critic_hidden_dims=(8,),
            seed=99,
        )
        restored.load_training_state_dict(payload)

        restored_first_actor_param = next(restored.actors.parameters())
        self.assertAlmostEqual(float(restored_first_actor_param.flatten()[0].item()), 1.23, places=5)
        self.assertAlmostEqual(float(restored.log_alpha.item()), -2.5, places=5)
        self.assertAlmostEqual(float(restored.actor_opt.rho), 0.12, places=5)


class _FakeResumeTrainer:
    train_calls = 0
    export_calls = 0
    loaded_state = None

    def __init__(self, *args, **kwargs):
        pass

    def load_training_state_dict(self, payload):
        type(self).loaded_state = payload

    def training_state_dict(self):
        return {"ok": True}

    def train_epoch(self, *args, **kwargs):
        type(self).train_calls += 1
        return {
            "actor_loss": 0.1,
            "critic_loss": 0.2,
            "eval_return": 12.5,
            "cumulative_return": 125.0,
            "alpha": 0.03,
            "selected_fraction": 0.25,
            "effective_td_var_threshold": 0.2,
            "action_var_mean": 0.1,
        }

    def export_checkpoint(self, output_dir, metadata=None):
        type(self).export_calls += 1
        Path(output_dir).mkdir(parents=True, exist_ok=True)


class TestResumeIgnoreEarlyStop(unittest.TestCase):
    def setUp(self):
        _FakeResumeTrainer.train_calls = 0
        _FakeResumeTrainer.export_calls = 0
        _FakeResumeTrainer.loaded_state = None

    def test_resume_ignore_early_stop_continues_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace_path = root / "trace.jsonl"
            trace_path.write_text('{"fake": true}\n', encoding="utf-8")
            output_dir = root / "run"
            output_dir.mkdir(parents=True, exist_ok=True)
            resume_checkpoint = output_dir / "resume_checkpoint.pt"
            torch.save(
                {
                    "trainer_state": {"epoch": 220},
                    "history": [{"epoch": 220, "eval_return": 9.0}],
                    "checkpoint_records": [
                        {
                            "epoch": 220,
                            "metrics": {
                                "eval_return": 9.0,
                                "critic_loss": 0.3,
                                "action_var_mean": 0.1,
                                "selected_fraction": 0.2,
                            },
                            "plateau_streak": 2,
                        }
                    ],
                    "best_checkpoint": {
                        "epoch": 200,
                        "metrics": {
                            "eval_return": 10.0,
                            "cumulative_return": 100.0,
                            "critic_loss": 0.2,
                        },
                    },
                    "completed_epochs": 220,
                    "target_epochs": 220,
                    "stopped_early": True,
                    "stop_reason": "plateau_after_epoch_220",
                },
                resume_checkpoint,
            )

            fake_record = type(
                "FakeRecord",
                (),
                {
                    "du_states": [[0.0, 0.0, 0.0]],
                    "global_state": [0.0, 0.0, 0.0, 0.0],
                },
            )()

            argv = [
                "train_tasam_marl.py",
                "--trace-jsonl",
                str(trace_path),
                "--output-dir",
                str(output_dir),
                "--trainer-backend",
                "article_sac",
                "--sam-mode",
                "tasam_selective",
                "--epochs",
                "25",
                "--max-epochs",
                "221",
                "--resume",
                "--resume-ignore-early-stop",
            ]
            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(train_module, "load_marl_transition_trace", return_value=[fake_record]):
                    with mock.patch.object(train_module, "TASAMArticleSACTrainer", _FakeResumeTrainer):
                        rc = main()

            self.assertEqual(rc, 0)
            self.assertEqual(_FakeResumeTrainer.train_calls, 1)
            self.assertEqual(_FakeResumeTrainer.loaded_state, {"epoch": 220})
            summary = json.loads((output_dir / "tasam_marl_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["completed_epochs"], 221)
            self.assertTrue(summary["resume_ignore_early_stop"])
            self.assertEqual(summary["resumed_from_stop_reason"], "plateau_after_epoch_220")
            self.assertFalse(summary["stopped_early"])


if __name__ == "__main__":
    unittest.main()
