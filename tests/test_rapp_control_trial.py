import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.rapp_control_trial import JointControlTrial


def _decision(score_delta=0.02, ran_delta=0.0, ai_delta=0.0, violation="", priority="mixed"):
    return {
        "timestamp": 1000,
        "priority_violation": violation,
        "armd_analysis": {"available": True, "confidence": 0.95},
        "tasam_advisor": {
            "valid": True,
            "control_gate": {"allow_control_trial": True},
            "resource_advice": {"enabled": True},
        },
        "resource_allocation": {
            "marl_shadow": {
                "comparison": {
                    "score_delta": score_delta,
                    "shadow_ran_completion_est": 0.5 + ran_delta,
                    "live_ran_completion_est": 0.5,
                    "shadow_ai_completion_est": 0.5 + ai_delta,
                    "live_ai_completion_est": 0.5,
                    "priority": priority,
                }
            }
        },
    }


def _shadow_candidate_decision():
    decision = _decision()
    decision["tasam_advisor"].update({
        "valid": False,
        "source": "checkpoint",
        "confidence": 0.74,
        "min_confidence": 0.70,
    })
    return decision


class TestJointControlTrial(unittest.TestCase):
    def test_joint_valid_decision_enters_canary_and_persists_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = JointControlTrial({
                "enabled": True,
                "fraction": 1.0,
                "target_decisions": 300,
                "rolling_window": 30,
                "state_path": str(Path(tmp) / "state.json"),
            })
            result = trial.decide(_decision(), "decision-1")
            self.assertTrue(result["eligible"])
            self.assertTrue(result["canary"])
            self.assertTrue(result["applied"])
            trial.observe(_decision(), result)
            payload = json.loads(trial.state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["applied_decisions"], 1)

    def test_critical_decision_uses_live_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = JointControlTrial({"enabled": True, "fraction": 1.0, "state_path": str(Path(tmp) / "state.json")})
            result = trial.decide(_decision(violation="VEHICLE_CRITICAL"), "decision-1")
            self.assertFalse(result["eligible"])
            self.assertFalse(result["applied"])
            self.assertIn("critical SLA", result["reason"])

    def test_control_trial_accepts_checkpoint_proposal_with_stable_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = JointControlTrial({"enabled": True, "fraction": 1.0, "state_path": str(Path(tmp) / "state.json")})
            result = trial.decide(_shadow_candidate_decision(), "decision-1")
            self.assertTrue(result["eligible"])
            self.assertTrue(result["applied"])

    def test_negative_rolling_score_triggers_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = JointControlTrial({
                "enabled": True,
                "fraction": 1.0,
                "rolling_window": 2,
                "state_path": str(Path(tmp) / "state.json"),
            })
            first = trial.decide(_decision(score_delta=-0.1), "decision-1")
            trial.observe(_decision(score_delta=-0.1), first)
            second = trial.decide(_decision(score_delta=-0.1), "decision-2")
            self.assertTrue(second["rollback"])
            self.assertFalse(second["applied"])
            self.assertEqual(trial.state["status"], "rolled_back")

    def test_rounding_noise_does_not_trigger_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = JointControlTrial({
                "enabled": True,
                "fraction": 1.0,
                "rolling_window": 2,
                "score_delta_epsilon": 1e-4,
                "state_path": str(Path(tmp) / "state.json"),
            })
            first = trial.decide(_decision(score_delta=-1e-6), "decision-1")
            trial.observe(_decision(score_delta=-1e-6), first)
            second = trial.decide(_decision(score_delta=-1e-6), "decision-2")
            self.assertFalse(second["rollback"])
            self.assertTrue(second["applied"])

    def test_target_counts_eligible_decisions_not_total_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            trial = JointControlTrial({
                "enabled": True,
                "fraction": 1.0,
                "target_decisions": 2,
                "state_path": str(Path(tmp) / "state.json"),
            })
            critical = _decision(violation="VEHICLE_CRITICAL")
            first = trial.decide(critical, "critical-1")
            self.assertFalse(first["applied"])
            trial.observe(critical, first)

            second = trial.decide(_decision(), "eligible-1")
            self.assertTrue(second["applied"])
            trial.observe(_decision(), second)

            third = trial.decide(_decision(), "eligible-2")
            self.assertTrue(third["applied"])
            trial.observe(_decision(), third)

            fourth = trial.decide(_decision(), "after-target")
            self.assertFalse(fourth["applied"])
            self.assertEqual(fourth["reason"], "trial target reached")
            self.assertEqual(trial.state["total_decisions"], 4)
            self.assertEqual(trial.state["eligible_decisions"], 2)

    def test_assistant_only_allows_critical_state_inside_armd_envelope(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GREENRAN_TASAM_ADVISOR_MODE": "assistant_only_control"}
        ):
            decision = _decision(violation="VEHICLE_CRITICAL")
            decision["armd_analysis"].update({"loaded": True, "enabled": True})
            trial = JointControlTrial({"enabled": True, "fraction": 1.0, "state_path": str(Path(tmp) / "state.json")})
            result = trial.decide(decision, "decision-critical")
            self.assertTrue(result["eligible"])
            self.assertTrue(result["applied"])
            self.assertEqual(result["mode"], "assistant_only_control")
            self.assertTrue(trial.status()["assistant_only"])

    def test_assistant_only_does_not_rollback_on_expected_critical_protection(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GREENRAN_TASAM_ADVISOR_MODE": "assistant_only_control"}
        ):
            trial = JointControlTrial({
                "enabled": True,
                "fraction": 1.0,
                "critical_streak": 3,
                "state_path": str(Path(tmp) / "state.json"),
            })
            critical = _decision(violation="THROUGHPUT")
            for index in range(3):
                result = trial.decide(critical, f"critical-{index}")
                self.assertTrue(result["applied"])
                trial.observe(critical, result)
            fourth = trial.decide(critical, "critical-3")
            self.assertTrue(fourth["applied"])
            self.assertFalse(fourth["rollback"])
            self.assertFalse(trial.status()["rollback"])

    def test_assistant_only_uses_priority_domain_not_non_priority_ai_delta(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GREENRAN_TASAM_ADVISOR_MODE": "assistant_only_control"}
        ):
            trial = JointControlTrial({
                "enabled": True,
                "fraction": 1.0,
                "rolling_window": 2,
                "state_path": str(Path(tmp) / "state.json"),
            })
            camera_priority = _decision(
                score_delta=0.02,
                ran_delta=0.05,
                ai_delta=-0.20,
                priority="ran_camera",
            )
            first = trial.decide(camera_priority, "camera-1")
            self.assertTrue(first["applied"])
            trial.observe(camera_priority, first)
            second = trial.decide(camera_priority, "camera-2")
            self.assertTrue(second["applied"])
            self.assertFalse(second["rollback"])

    def test_priority_is_inferred_from_live_camera_violation_when_comparison_is_mixed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GREENRAN_TASAM_ADVISOR_MODE": "assistant_only_control"}
        ):
            trial = JointControlTrial({
                "enabled": True,
                "fraction": 1.0,
                "rolling_window": 2,
                "state_path": str(Path(tmp) / "state.json"),
            })
            camera = _decision(score_delta=0.02, ran_delta=0.05, ai_delta=-0.20, priority="mixed")
            camera["priority_violation"] = "THROUGHPUT"
            camera["reason"] = "CÂMERA SLA: Throughput abaixo do mínimo"
            first = trial.decide(camera, "camera-live-1")
            trial.observe(camera, first)
            second = trial.decide(camera, "camera-live-2")
            self.assertTrue(second["applied"])
            self.assertFalse(second["rollback"])
            self.assertEqual(second["candidate_metrics"]["priority"], "ran_camera")

    def test_assistant_only_keeps_camera_priority_when_composite_score_is_negative(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GREENRAN_TASAM_ADVISOR_MODE": "assistant_only_control"}
        ):
            trial = JointControlTrial({
                "enabled": True,
                "fraction": 1.0,
                "rolling_window": 2,
                "state_path": str(Path(tmp) / "state.json"),
            })
            bad = _decision(score_delta=-0.05, ran_delta=0.05, ai_delta=-0.20, priority="ran_camera")
            first = trial.decide(bad, "bad-1")
            trial.observe(bad, first)
            second = trial.decide(bad, "bad-2")
            self.assertFalse(second["rollback"])
            self.assertTrue(second["applied"])

    def test_assistant_only_rolls_back_negative_score_in_mixed_window(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GREENRAN_TASAM_ADVISOR_MODE": "assistant_only_control"}
        ):
            trial = JointControlTrial({
                "enabled": True,
                "fraction": 1.0,
                "rolling_window": 2,
                "state_path": str(Path(tmp) / "state.json"),
            })
            bad = _decision(score_delta=-0.05, ran_delta=0.0, ai_delta=0.0, priority="mixed")
            first = trial.decide(bad, "mixed-1")
            trial.observe(bad, first)
            second = trial.decide(bad, "mixed-2")
            self.assertTrue(second["rollback"])
            self.assertFalse(second["applied"])
            self.assertIn("score delta negative", second["reason"])

    def test_assistant_only_rejects_invalid_proposal_without_live_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GREENRAN_TASAM_ADVISOR_MODE": "assistant_only_control"}
        ):
            decision = _decision()
            decision["tasam_advisor"]["valid"] = False
            decision["tasam_advisor"]["resource_advice"]["enabled"] = False
            trial = JointControlTrial({"enabled": True, "fraction": 1.0, "state_path": str(Path(tmp) / "state.json")})
            result = trial.decide(decision, "decision-invalid")
            self.assertFalse(result["eligible"])
            self.assertFalse(result["applied"])
            self.assertEqual(result["mode"], "assistant_only_invalid")
            self.assertNotIn("live allocator", result["reason"])

    def test_full_control_applies_critical_decision_and_never_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GREENRAN_TASAM_ADVISOR_MODE": "tasam_full_control"}
        ):
            trial = JointControlTrial({
                "enabled": True,
                "rolling_window": 1,
                "state_path": str(Path(tmp) / "state.json"),
            })
            critical = _decision(score_delta=-1.0, violation="VEHICLE_CRITICAL")
            result = trial.decide(critical, "full-critical")
            self.assertTrue(result["eligible"])
            self.assertTrue(result["applied"])
            self.assertEqual(result["mode"], "tasam_full_control")
            self.assertEqual(result["fraction"], 1.0)
            trial.observe(critical, result)
            self.assertFalse(trial.status()["rollback"])


if __name__ == "__main__":
    unittest.main()
