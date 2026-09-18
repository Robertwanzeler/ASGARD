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
from drl.ta_sam_marl_sac import MARLTransitionRecord, TASAMArticleSACTrainer, _allocation_target, load_marl_transition_trace  # noqa: E402


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
        first_global_param = next(trainer.global_actor.parameters())
        first_global_param.data.fill_(0.77)
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
        self.assertAlmostEqual(
            float(next(restored.global_actor.parameters()).flatten()[0].item()), 0.77, places=5
        )
        self.assertAlmostEqual(float(restored.log_alpha.item()), -2.5, places=5)
        self.assertAlmostEqual(float(restored.actor_opt.rho), 0.12, places=5)

    def test_category_head_trains_from_observed_feedback_and_exports(self):
        record = MARLTransitionRecord(
            global_state=[0.0, 0.0, 0.0, 0.0],
            du_states=[[[0.0, 0.0, 0.0]][0]],
            behavior_actions=[[[0.33, 0.33, 0.34]][0]],
            reward=0.5,
            next_global_state=[0.0, 0.0, 0.0, 0.0],
            next_du_states=[[[0.0, 0.0, 0.0]][0]],
            done=False,
            category_target=0,
            category_weight=1.0,
        )
        trainer = TASAMArticleSACTrainer(
            du_count=1,
            du_state_dim=3,
            global_state_dim=4,
            actor_hidden_dims=(8,),
            critic_hidden_dims=(8,),
            category_head_hidden_dim=8,
            batch_size=1,
            seed=3,
        )
        metrics = trainer.train_epoch([record], warmup=True)
        self.assertEqual(metrics["category_evaluated"], 1)
        self.assertIn("category_accuracy", metrics)
        with tempfile.TemporaryDirectory() as tmp:
            trainer.export_checkpoint(tmp)
            self.assertTrue((Path(tmp) / "tasam_marl_category_head.pt").is_file())
            self.assertTrue((Path(tmp) / "tasam_marl_global_actor.pt").is_file())
            meta = json.loads((Path(tmp) / "tasam_marl_checkpoint_meta.json").read_text())
            self.assertEqual(meta["category_head_classes"], ["ALLOWED", "CONDITIONAL", "BLOCKED"])
            self.assertTrue(meta["uses_global_energy_infra_actor"])
            self.assertEqual(meta["joint_action_dim"], 6)

    def test_trace_loader_uses_observed_category_as_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "global_state": {"state_vector": [0.0] * 4},
                "next_global_state": {"state_vector": [0.0] * 4},
                "du_states": [{"state_vector": [0.0] * 3, "slice_mix": {"eMBB": 1.0}}],
                "next_du_states": [{"state_vector": [0.0] * 3}],
                "judge_feedback": {
                    "tasam_predicted_verdict": "CONDITIONAL",
                    "tasam_observed_verdict": "ALLOWED",
                },
                "tasam_predicted_verdict": "CONDITIONAL",
                "tasam_observed_verdict": "ALLOWED",
                "reward_hint": -1.0,
            }
            path = Path(tmp) / "trace.jsonl"
            path.write_text(json.dumps(payload) + "\n")
            records = load_marl_transition_trace(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].category_target, 0)
            self.assertEqual(records[0].category_weight, 2.0)

    def test_category_head_learns_separable_13d_category_tail(self):
        records = []
        for category in range(3):
            tail = [1.0 if index == category else 0.0 for index in range(3)]
            state = [0.0] * 10 + tail
            for _ in range(30):
                records.append(MARLTransitionRecord(
                    global_state=state,
                    du_states=[[0.0] * 13],
                    behavior_actions=[[1.0 / 3.0] * 3],
                    reward=-1.0 if category else 0.5,
                    next_global_state=state,
                    next_du_states=[[0.0] * 13],
                    done=False,
                    category_target=category,
                    category_weight=1.0,
                ))
        trainer = TASAMArticleSACTrainer(
            du_count=1,
            du_state_dim=13,
            global_state_dim=13,
            actor_hidden_dims=(8,),
            critic_hidden_dims=(8,),
            category_head_hidden_dim=8,
            category_head_steps=30,
            batch_size=32,
            updates_per_epoch=1,
            seed=47,
        )
        metrics = trainer.train_epoch(records, warmup=True)
        self.assertGreater(metrics["conditional_recall"], 0.0)
        self.assertGreater(metrics["conditional_f1"], 0.0)

    def test_conditional_target_gets_priority_weight_and_strong_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "global_state": {"state_vector": [0.0] * 4},
                "next_global_state": {"state_vector": [0.0] * 4},
                "du_states": [{"state_vector": [0.0] * 3, "slice_mix": {"eMBB": 1.0}}],
                "next_du_states": [{"state_vector": [0.0] * 3}],
                "tasam_predicted_verdict": "ALLOWED",
                "tasam_observed_verdict": "CONDITIONAL",
                "tasam_training_category_credit": -1.0,
                "reward_hint": -1.0,
            }
            path = Path(tmp) / "trace.jsonl"
            path.write_text(json.dumps(payload) + "\n")
            records = load_marl_transition_trace(path)
            self.assertEqual(records[0].category_target, 1)
            self.assertEqual(records[0].category_weight, 2.25)
            self.assertEqual(records[0].category_training_credit, -1.0)

    def test_allocation_target_raises_both_domains_when_budget_is_feasible(self):
        payload = {
            "collection_quality": {"pdcp_real": True, "metric_alignment_valid": True},
            "action": {
                "usable_budget": 1.0,
                "r_ran": 0.15,
                "r_ai": 0.15,
                "d_ran": 0.40,
                "d_ai": 0.40,
                "ran_completion_ratio": 0.375,
                "ai_completion_ratio": 0.375,
            },
            "judge_feedback": {"feedback_status": "observed"},
        }
        target, weight, metadata = _allocation_target(payload)
        self.assertEqual(weight, 1.0)
        self.assertTrue(metadata["feasible"])
        self.assertGreaterEqual(target[0], 0.475 - 1e-6)
        self.assertGreaterEqual(target[1], 0.30 - 1e-6)
        self.assertAlmostEqual(sum(target), 1.0, places=6)

    def test_economic_allocation_target_contains_total_budget_fraction(self):
        payload = {
            "allocation_total_head_enabled": True,
            "collection_quality": {"pdcp_real": True, "metric_alignment_valid": True},
            "action": {
                "usable_budget": 1.0,
                "r_ran": 0.3,
                "r_ai": 0.3,
                "d_ran": 0.4,
                "d_ai": 0.4,
                "floor_total_ran": 0.25,
                "floor_total_ai": 0.25,
                "allocation_state": "ALLOWED",
            },
            "judge_feedback": {"feedback_status": "observed"},
        }
        target, weight, metadata = _allocation_target(payload)
        self.assertEqual(weight, 1.0)
        self.assertEqual(len(target), 3)
        self.assertAlmostEqual(target[0] + target[1], 1.0, places=6)
        self.assertAlmostEqual(target[2], 0.525, places=6)
        self.assertEqual(metadata["source"], "observed_sla_floor_plus_economic_margin")

    def test_infeasible_economic_floor_has_zero_weight_and_never_exceeds_one(self):
        payload = {
            "economic_action_contract": "applied_action_v2",
            "allocation_total_head_enabled": True,
            "economic_transition_eligible": True,
            "collection_quality": {"pdcp_real": True, "metric_alignment_valid": True},
            "action": {
                "usable_budget": 0.80,
                "floor_total_ran": 0.55,
                "floor_total_ai": 0.35,
                "r_ran": 0.55,
                "r_ai": 0.35,
            },
            "judge_feedback": {"feedback_status": "observed"},
        }
        target, weight, metadata = _allocation_target(payload)
        self.assertEqual(weight, 0.0)
        self.assertFalse(metadata["economic_transition_eligible"])
        self.assertTrue(all(0.0 <= value <= 1.0 for value in target))

    def test_economic_v2_replay_uses_the_applied_action_not_state_target(self):
        payload = {
            "economic_action_contract": "applied_action_v2",
            "economic_transition_eligible": True,
            "global_state": {"state_vector": [0.0] * 4},
            "next_global_state": {"state_vector": [0.0] * 4},
            "du_states": [
                {"state_vector": [0.0] * 3, "slice_mix": {"eMBB": 1.0}}
                for _ in range(3)
            ],
            "next_du_states": [{"state_vector": [0.0] * 3} for _ in range(3)],
            "collection_quality": {"pdcp_real": True, "metric_alignment_valid": True},
            "action": {"usable_budget": 1.0, "r_ran": 0.9, "r_ai": 0.1},
            "economic_action": {
                "contract": "applied_action_v2",
                "applied": {
                    "power_percent": 25.0,
                    "ran_allocation": 0.30,
                    "ai_allocation": 0.20,
                    "usable_budget": 1.0,
                    "du_actions": [[0.2, 0.3, 0.5]] * 3,
                },
            },
            "judge_feedback": {
                "feedback_status": "observed",
                "economic_transition_eligible": True,
                "tasam_online_reward": 0.42,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.jsonl"
            trace.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            record = load_marl_transition_trace(trace)[0]
        self.assertEqual(record.behavior_actions, [[0.2, 0.3, 0.5]] * 3)
        self.assertEqual(record.behavior_global_action, [0.25, 0.4, 0.5])
        self.assertEqual(record.economic_weight, 1.0)
        self.assertAlmostEqual(record.reward, 0.42)

    def test_three_output_trainer_normalizes_legacy_allocation_targets(self):
        def record(target):
            return MARLTransitionRecord(
                global_state=[0.0] * 4,
                du_states=[[0.0] * 3],
                behavior_actions=[[1.0 / 3.0] * 3],
                reward=0.5,
                next_global_state=[0.0] * 4,
                next_du_states=[[0.0] * 3],
                done=False,
                allocation_target=target,
                allocation_weight=1.0,
            )

        economic = TASAMArticleSACTrainer(
            du_count=1, du_state_dim=3, global_state_dim=4,
            actor_hidden_dims=(8,), critic_hidden_dims=(8,),
            allocation_head_hidden_dim=8, allocation_head_steps=1,
            allocation_head_output_dim=3, batch_size=2, seed=47,
        )
        dataset = economic._prepare_dataset([
            record([0.4, 0.6]), record([0.6, 0.4, 0.7]),
        ])
        self.assertEqual(tuple(dataset.allocation_targets.shape), (2, 3))
        self.assertAlmostEqual(float(dataset.allocation_targets[0, 2]), 1.0, places=6)
        self.assertAlmostEqual(float(dataset.allocation_targets[1, 2]), 0.7, places=6)
        self.assertEqual(economic._train_allocation_head(dataset)["allocation_evaluated"], 2)

        legacy = TASAMArticleSACTrainer(
            du_count=1, du_state_dim=3, global_state_dim=4,
            actor_hidden_dims=(8,), critic_hidden_dims=(8,),
            allocation_head_hidden_dim=8, allocation_head_steps=1,
            allocation_head_output_dim=2, batch_size=1, seed=47,
        )
        legacy_dataset = legacy._prepare_dataset([record([0.4, 0.6, 0.7])])
        self.assertEqual(tuple(legacy_dataset.allocation_targets.shape), (1, 2))
        self.assertAlmostEqual(float(legacy_dataset.allocation_targets[0, 0]), 0.4, places=6)
        self.assertAlmostEqual(float(legacy_dataset.allocation_targets[0, 1]), 0.6, places=6)

    def test_allocation_head_is_exported_and_trained_from_real_feedback(self):
        payload = {
            "global_state": {"state_vector": [0.0] * 4},
            "next_global_state": {"state_vector": [0.0] * 4},
            "du_states": [{"state_vector": [0.0] * 3, "slice_mix": {"eMBB": 1.0}}],
            "next_du_states": [{"state_vector": [0.0] * 3}],
            "collection_quality": {"pdcp_real": True, "metric_alignment_valid": True},
            "action": {"usable_budget": 1.0, "r_ran": 0.2, "r_ai": 0.2, "d_ran": 0.4, "d_ai": 0.4},
            "judge_feedback": {"feedback_status": "observed", "tasam_observed_verdict": "ALLOWED"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.jsonl"
            trace.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            records = load_marl_transition_trace(trace)
            trainer = TASAMArticleSACTrainer(
                du_count=1, du_state_dim=3, global_state_dim=4,
                actor_hidden_dims=(8,), critic_hidden_dims=(8,),
                allocation_head_hidden_dim=8, allocation_head_steps=2,
                batch_size=1, seed=47,
            )
            metrics = trainer.train_epoch(records, warmup=True)
            self.assertEqual(metrics["allocation_evaluated"], 1)
            self.assertIn("allocation_prediction_loss", metrics)
            trainer.export_checkpoint(Path(tmp) / "checkpoint")
            self.assertTrue((Path(tmp) / "checkpoint" / "tasam_marl_allocation_head.pt").is_file())
            metadata = json.loads((Path(tmp) / "checkpoint" / "tasam_marl_checkpoint_meta.json").read_text())
            self.assertEqual(metadata["allocation_head_outputs"], ["ran_share", "ai_share"])


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
