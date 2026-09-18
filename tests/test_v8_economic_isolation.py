import tempfile
import threading
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.run_tasam_online_arm import _parent_decision_target_watch
from scripts.run_tasam_online_controlled import _economic_row_is_eligible
from scripts.run_tasam_online_economic_campaign import _arm
from src.rapp_orchestrator import RappResourceOptimizer
from src.rapp_judge import RAppJudge


class TestV8EconomicIsolation(unittest.TestCase):
    def test_native_e2_uses_one_actuator_not_the_legacy_energy_socket(self):
        optimizer = object.__new__(RappResourceOptimizer)
        optimizer.xapp_transport_mode = "socket"
        optimizer._energy_active = True
        optimizer.xapp_manager = Mock()
        optimizer.xapp_manager.is_running.side_effect = lambda name: name == "tasam_actuator"
        optimizer._start_tasam_actuator = Mock(return_value=True)
        optimizer._start_energy_saver = Mock()
        optimizer._stop_energy_saver = Mock()
        with patch.dict("os.environ", {"GREENRAN_TASAM_E2_CONTROL": "1"}, clear=False):
            status = optimizer.decide_xapp_activation({"energy_saver": "ALLOWED"})
        self.assertTrue(status["exclusive_actuator"])
        self.assertEqual(status["control_plane"], "native_e2")
        self.assertFalse(status["legacy_energy_socket_required"])
        optimizer._start_energy_saver.assert_not_called()
        optimizer._stop_energy_saver.assert_not_called()

    def test_legacy_sender_routes_to_native_operational_bundle_in_e2_mode(self):
        optimizer = object.__new__(RappResourceOptimizer)
        optimizer.xapp_transport_mode = "socket"
        optimizer.send_native_operational_bundle = Mock(return_value=True)
        with patch.dict("os.environ", {"GREENRAN_TASAM_E2_CONTROL": "1"}, clear=False):
            result = optimizer.send_energy_command({"energy_saver": "ALLOWED"})
        self.assertTrue(result)
        optimizer.send_native_operational_bundle.assert_called_once()

    def test_shadow_operational_bundle_uses_rapp_candidate_not_tasam_power(self):
        optimizer = object.__new__(RappResourceOptimizer)
        optimizer._canonical_energy_command_power = lambda value: float(value)
        optimizer.send_tasam_control_bundle = Mock(return_value=True)
        decision = {
            "tasam_power_percent": 25,
            "economic_action": {"live_candidate": {"power_percent": 60}},
        }
        result = optimizer.send_native_operational_bundle(decision)
        self.assertTrue(result)
        self.assertEqual(decision["native_operational_action_origin"], "rapp_live")
        optimizer.send_tasam_control_bundle.assert_called_once_with(
            decision, action_origin="rapp_live"
        )
        self.assertEqual(optimizer._rapp_live_power_percent(decision), 60.0)

    def test_hard_veto_uses_native_safety_origin_not_tasam(self):
        optimizer = object.__new__(RappResourceOptimizer)
        optimizer.send_tasam_control_bundle = Mock(return_value=True)
        decision = {"armd_safety_level": "HARD_VETO"}
        optimizer.send_native_operational_bundle(decision)
        optimizer.send_tasam_control_bundle.assert_called_once_with(
            decision, action_origin="armd_rapp_safety"
        )

    def test_only_explicit_hard_veto_is_isolated(self):
        blocked, blocked_reason = RappResourceOptimizer._economic_safety_isolation({
            "energy_saver": "BLOCKED",
            "resource_allocation": {"allocation_state": "BLOCKED"},
        })
        self.assertFalse(blocked)
        self.assertEqual(blocked_reason, "")

        critical, critical_reason = RappResourceOptimizer._economic_safety_isolation({
            "energy_saver": "CONDITIONAL",
            "armd_safety_level": "HARD_VETO",
            "priority_violation": "VEHICLE_CRITICAL",
            "resource_allocation": {
                "allocation_state": "CONDITIONAL",
                "floor_feasible": True,
                "floor_verified": True,
            },
        })
        self.assertTrue(critical)
        self.assertIn("armd_hard_veto", critical_reason)

    def test_noncritical_conditional_remains_available(self):
        isolated, reason = RappResourceOptimizer._economic_safety_isolation({
            "energy_saver": "CONDITIONAL",
            "resource_allocation": {
                "allocation_state": "CONDITIONAL",
                "floor_feasible": True,
                "per_ue_floor_feasible": True,
                "floor_verified": True,
                "failsafe_required": False,
            },
            "armd_analysis": {"critical_violation": False, "safety_veto": False},
        })
        self.assertFalse(isolated)
        self.assertEqual(reason, "")

    def test_armd_levels_distinguish_advice_from_enforcement(self):
        self.assertEqual(
            RappResourceOptimizer._classify_armd_safety_level(
                {"energy_saver": "BLOCKED"},
                {"expected_energy_saver": "CONDITIONAL", "available": True, "proposal_valid": True},
            ),
            "ADVISORY",
        )
        self.assertEqual(
            RappResourceOptimizer._classify_armd_safety_level(
                {},
                {"scenario": "vehicle_critical", "expected_energy_saver": "BLOCKED"},
            ),
            "HARD_VETO",
        )

    def test_explicit_advisory_armd_cannot_win_over_tasam(self):
        judge = RAppJudge({
            "enabled": True,
            "production": True,
            "composition_mode": "cooperative_hierarchy",
        })
        result = judge.decide(
            {},
            {
                "source": "armd", "available": True, "valid": True, "feasible": True,
                "verdict": "CONDITIONAL", "proposal_score": 1.0,
                "armd_safety_level": "ADVISORY",
            },
            {
                "source": "ta_sam", "available": True, "valid": True, "feasible": True,
                "verdict": "ALLOWED", "proposal_score": 0.2,
            },
        )
        self.assertEqual(result["selected_advocate"], "ta_sam")
        self.assertEqual(result["composition_mode"], "competitive")

    def test_isolated_and_neutral_rows_do_not_enter_economic_replay(self):
        base = {
            "economic_action_contract": "applied_action_v2",
            "economic_application_status": "applied",
            "economic_transition_eligible": True,
            "economic_training_eligible": True,
            "decision": {
                "tasam_checkpoint_valid": True,
                "tasam_fallback_used": False,
            },
            "tasam_checkpoint_valid": True,
            "tasam_fallback_used": False,
            "tasam_action_applied": True,
            "judge_feedback": {
                "pdcp_loss_coverage": {"valid": True},
                "topology_valid": True,
                "tasam_action_applied": True,
                "economic_action_alignment_valid": True,
            },
            "economic_action": {
                "command_sent": True,
                "actuation_confirmed": True,
                "actuation_confirmation_source": "ns3_native_observation",
                "native_observation": {"evidence_version": "v2", "transaction_id": 1},
                "tasam_operating_permission": True,
                "armd_safety_level": "CLEAR",
                "live_candidate": {"power_w": 100.0, "total_allocation": 1.0},
                "applied": {"power_w": 90.0, "total_allocation": 0.8},
            },
        }
        self.assertTrue(_economic_row_is_eligible(base))
        isolated = {**base, "economic_safety_isolated": True}
        self.assertFalse(_economic_row_is_eligible(isolated))
        noop = {**base, "economic_execution_mode": "economic_neutral_noop"}
        self.assertFalse(_economic_row_is_eligible(noop))
        rejected = {**base, "economic_training_eligible": False}
        self.assertFalse(_economic_row_is_eligible(rejected))

    def test_online_adaptation_uses_dynamic_maximum_and_promotion_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(
                seed=47, profile="tasam_training_balanced_v3", wall_time=900,
                adaptation_wall_time=6000, sim_time=600, calibration=root / "energy.json",
                decisions=300, adaptation_decisions=1200, adaptation_max_decisions=2400,
                min_free_gib=10, min_new_snapshots=60, min_trainable_transitions=180,
                epochs_per_update=2, controller_poll_seconds=10, shadow_min_decisions=300,
                stage_window_decisions=300, max_rollout_fraction=0.50,
                min_economic_transitions=180, economic_update_min_transitions=64,
            )
            command = _arm("combined_online", root / "adaptation", root / "checkpoint", args)
        self.assertEqual(command[command.index("--decision-target") + 1], "2400")
        self.assertEqual(command[command.index("--min-decision-target") + 1], "1200")
        self.assertIn("--stop-after-promotion", command)
        self.assertEqual(command[command.index("--wall-time") + 1], "6000")

    def test_parent_watch_stops_after_promoted_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "rapp_decisions.jsonl").write_text("{}\n", encoding="utf-8")
            (run_dir / "online_state.json").write_text(
                '{"candidate_promoted": true}\n', encoding="utf-8"
            )
            stop = threading.Event()
            result = {}
            thread = threading.Thread(
                target=_parent_decision_target_watch,
                args=(run_dir, 2400, stop, result),
                kwargs={"min_target": 1, "stop_after_promotion": True},
            )
            thread.start()
            thread.join(timeout=3)
            stop.set()
            self.assertFalse(thread.is_alive())
            self.assertTrue(result.get("requested"))
            request = json.loads((run_dir / "decision_target_stop.json").read_text(encoding="utf-8"))
            self.assertEqual(request["reason"], "candidate_promoted_after_minimum")
            self.assertEqual(request["source"], "arm_parent_promotion_watchdog")


if __name__ == "__main__":
    unittest.main()
