import os
import unittest
from collections import deque

from src.greenran_marl_topology import _split_ai_demands
from src.rapp_marl_shadow import MARLShadowRuntimeEvaluator, _scenario_priority


class TasamPolicyGuardTests(unittest.TestCase):
    def test_live_camera_pressure_overrides_mixed_stage(self):
        snapshot = {
            "ran_components": {
                "throughput_pressure": 0.35,
                "latency_pressure": 0.90,
                "cvar_pressure": 0.80,
            },
            "ai_components": {"vehicle_pressure": 0.40},
        }
        self.assertEqual(
            _scenario_priority(snapshot, {"scenario_stage": "mixed_overload"}),
            "ran_camera",
        )

    def test_contention_stages_activate_ai_guard(self):
        self.assertEqual(_scenario_priority({}, {"scenario_stage": "app2_blocked"}), "ai_guarded")
        self.assertEqual(_scenario_priority({}, {"scenario_stage": "vehicle_conditional"}), "ai_guarded")

    def test_soft_tail_does_not_override_non_camera_contention_stage(self):
        snapshot = {
            "ran_components": {"cvar_pressure": 0.98, "p95_pressure": 0.95},
            "ai_components": {"vehicle_pressure": 0.75},
        }
        self.assertEqual(
            _scenario_priority(snapshot, {"scenario_stage": "vehicle_blocked"}),
            "ai_guarded",
        )

    def test_ai_guard_preserves_and_recovers_ai_reserve(self):
        evaluator = MARLShadowRuntimeEvaluator.__new__(MARLShadowRuntimeEvaluator)
        evaluator.global_ran_guard_enabled = True
        resource = {
            "usable_budget": 1.0,
            "resource_budget": 1.0,
            "r_ran": 0.60,
            "r_ai": 0.40,
            "d_ran": 0.40,
            "d_ai": 0.60,
        }
        guarded, info = evaluator._apply_global_ran_guard(0.80, resource, "ai_guarded", 0.0)
        self.assertLessEqual(guarded, 0.52)
        self.assertTrue(info["applied"])
        self.assertGreaterEqual(info["ai_floor_share"], 0.48)

    def test_vehicle_urgency_receives_bounded_ai_share_bonus(self):
        base_sensor, base_vehicle = _split_ai_demands(
            1.0, {"app2_pressure": 0.45, "vehicle_pressure": 0.45}
        )
        urgent_sensor, urgent_vehicle = _split_ai_demands(
            1.0, {"app2_pressure": 0.45, "vehicle_pressure": 0.80}
        )
        self.assertGreater(urgent_vehicle, base_vehicle)
        self.assertLess(urgent_sensor, base_sensor)
        self.assertAlmostEqual(urgent_sensor + urgent_vehicle, 1.0)

    def test_control_mode_does_not_require_positive_shadow_delta(self):
        old_mode = os.environ.get("GREENRAN_TASAM_ADVISOR_MODE")
        try:
            os.environ["GREENRAN_TASAM_ADVISOR_MODE"] = "assistant_only_control"
            evaluator = MARLShadowRuntimeEvaluator.__new__(MARLShadowRuntimeEvaluator)
            evaluator.enabled = True
            evaluator.mode = "assistant_only_control"
            evaluator.advisory_mode = "assistant_only_control"
            evaluator.policy_id = "test-policy"
            evaluator.checkpoint_source = "checkpoint"
            evaluator.checkpoint_readiness = "shadow_ready"
            evaluator.checkpoint_run_dir = "test"
            evaluator.checkpoint_meta = {}
            evaluator._checkpoint_error = ""
            evaluator._load_control_gate = lambda: {}
            evaluator.min_confidence = 0.60
            evaluator.stability_window = 2
            evaluator._recommendation_history = deque(maxlen=2)
            evaluator.enable_resource_advice = True
            evaluator.enable_energy_advice = True
            evaluator.global_ran_guard_enabled = False
            evaluator.global_ran_guard_noncritical_enabled = False
            evaluator._du_action = lambda _idx, _du: ([1.0, 0.0, 0.0], "checkpoint")

            state = {
                "du_states": [{"du_id": "du0", "state_vector": [0.0] * 10}],
                "slice_state": {
                    "eMBB": {"qos_pressure": 0.0},
                    "mMTC": {"qos_pressure": 0.0},
                    "URLLC": {"qos_pressure": 0.0},
                },
            }
            resource = {
                "usable_budget": 1.0,
                "resource_budget": 1.0,
                "r_ran": 0.5,
                "r_ai": 0.5,
                "d_ran": 0.5,
                "d_ai": 0.5,
            }
            evaluator.evaluate(state, resource)
            result = evaluator.evaluate(state, resource)
            self.assertTrue(result["valid"])
            self.assertTrue(result["advisor"]["evidence_flags"]["stable_recommendation"])
            self.assertIn("positive_score_delta", result["advisor"]["evidence_flags"])
        finally:
            if old_mode is None:
                os.environ.pop("GREENRAN_TASAM_ADVISOR_MODE", None)
            else:
                os.environ["GREENRAN_TASAM_ADVISOR_MODE"] = old_mode

    def test_explicit_replay_stage_overrides_stale_live_stage(self):
        evaluator = MARLShadowRuntimeEvaluator.__new__(MARLShadowRuntimeEvaluator)
        evaluator.enabled = False
        evaluator.mode = "shadow_only"
        evaluator.advisory_mode = "shadow"
        evaluator.policy_id = "test-policy"

        # The disabled path is intentionally not enough to exercise stage
        # resolution, so use the helper contract directly in a minimal object
        # with a live stage that would otherwise be different.
        evaluator.enabled = True
        evaluator.checkpoint_source = "heuristic"
        evaluator.checkpoint_readiness = "unknown"
        evaluator.checkpoint_run_dir = ""
        evaluator._checkpoint_error = ""
        evaluator._load_control_gate = lambda: {}
        evaluator._smooth_p95_tail_pressure = lambda resource: (resource, {})
        evaluator._du_action = lambda _idx, _du: ([1.0, 0.0, 0.0], "heuristic")
        evaluator._apply_global_ran_guard = lambda share, resource, priority, embb: (share, {})
        evaluator._limit_ran_share_step = lambda share, resource, priority: (share, {})
        evaluator._resource_advice = lambda *args: {}
        evaluator._energy_advice = lambda *args: {}
        evaluator._recommendation_signature = lambda *args: "sig"
        evaluator._stability_state = lambda signature: {"stable": True}
        evaluator._advisor_confidence = lambda *args: 0.8
        evaluator.global_ran_guard_enabled = False
        evaluator.global_ran_guard_noncritical_enabled = False
        evaluator.enable_resource_advice = True
        evaluator.enable_energy_advice = True
        evaluator._recommendation_history = deque(maxlen=2)
        evaluator.min_confidence = 0.6
        evaluator.stability_window = 2
        evaluator.camera_guard_enabled = False
        evaluator.camera_guard_risk_threshold = 0.35
        evaluator.p95_tail_ema_alpha = 1.0
        evaluator.p95_tail_activation_threshold = 0.0
        evaluator.p95_tail_hysteresis = 0.0
        evaluator.p95_tail_share_gain = 0.1
        evaluator.max_ran_share_step = 0.25

        state = {
            "scenario_stage": "camera_blocked",
            "du_states": [{"du_id": "du0", "state_vector": [0.0] * 10}],
            "slice_state": {
                "eMBB": {"qos_pressure": 0.0},
                "mMTC": {"qos_pressure": 0.0},
                "URLLC": {"qos_pressure": 0.0},
            },
        }
        resource = {
            "usable_budget": 1.0, "resource_budget": 1.0,
            "r_ran": 0.5, "r_ai": 0.5, "d_ran": 0.5, "d_ai": 0.5,
        }
        result = evaluator.evaluate(state, resource)
        self.assertEqual(result["scenario_stage"], "camera_blocked")


if __name__ == "__main__":
    unittest.main()
