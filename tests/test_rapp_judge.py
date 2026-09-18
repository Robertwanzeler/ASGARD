import sys
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapp_judge import RAppJudge, expected_verdict_for_stage
from rapp_data_lake import DataLake


def _proposal(source, verdict, confidence=0.9, **extra):
    value = {
        "source": source,
        "available": True,
        "valid": True,
        "feasible": True,
        "verdict": verdict,
        "action": "FULL_POWER_GUARD" if verdict == "BLOCKED" else "MAINTAIN",
        "confidence": confidence,
    }
    value.update(extra)
    return value


def test_armd_critical_proposal_wins_only_when_its_score_is_better():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "BLOCKED", safety_veto=True, proposal_score=0.99),
        _proposal("ta_sam", "ALLOWED", confidence=0.99, proposal_score=0.60),
    )

    assert result["judge_verdict"] == "BLOCKED"
    assert result["selected_advocate"] == "armd"
    assert result["conflict_type"] == "score_precedence"
    assert result["safety_override"] is False
    assert result["production_deterministic"] is True


def test_tasam_owns_noncritical_state_and_resources():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "CONDITIONAL"),
        _proposal(
            "ta_sam",
            "ALLOWED",
            resource_advice={"enabled": True, "score": 0.8},
        ),
    )

    assert result["judge_verdict"] == "ALLOWED"
    assert result["selected_advocate"] == "ta_sam"
    assert result["conflict_type"] == "score_precedence"
    assert result["tasam_precedence_applied"] is False
    assert result["safety_override"] is False
    assert result["resource_winner"] == "ta_sam"


def test_explicit_score_can_select_tasam_without_rapp_intervention():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "CONDITIONAL", proposal_score=0.42),
        _proposal(
            "ta_sam",
            "ALLOWED",
            proposal_score=0.88,
            resource_allocation={"r_ran": 0.62, "r_ai": 0.38},
        ),
    )

    assert result["selected_advocate"] == "ta_sam"
    assert result["conflict_type"] == "score_precedence"
    assert result["fallback_used"] is False


def test_safety_flag_does_not_override_a_better_tasam_proposal():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {"camera": 1, "vehicle_safety": 1},
        _proposal(
            "armd",
            "BLOCKED",
            confidence=0.85,
            proposal_score=0.40,
            safety_veto=True,
            priority_violation="THROUGHPUT_CRITICAL",
        ),
        _proposal(
            "ta_sam",
            "ALLOWED",
            confidence=0.99,
            proposal_score=0.99,
        ),
    )

    assert result["selected_advocate"] == "ta_sam"
    assert result["conflict_type"] == "score_precedence"
    assert result["safety_override"] is False


def test_armd_cannot_win_a_tie_when_external_policy_prefers_armd():
    judge = RAppJudge(
        {
            "enabled": True,
            "production": True,
            "arbitration": {
                "tie_margin": 0.02,
                "external_last_resort_winner": "armd",
            },
        }
    )
    result = judge.decide(
        {},
        _proposal("armd", "CONDITIONAL", proposal_score=0.60, safety_veto=True),
        _proposal("ta_sam", "ALLOWED", proposal_score=0.60),
    )

    assert result["selected_advocate"] == "ta_sam"
    assert result["conflict_type"] == "tie_tasam_protection"
    assert result["safety_override"] is False


def test_armd_cannot_win_equal_score_when_states_agree():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "CONDITIONAL", proposal_score=0.70),
        _proposal("ta_sam", "CONDITIONAL", proposal_score=0.70),
    )

    assert result["selected_advocate"] == "ta_sam"
    assert result["conflict_type"] == "agreement"


def test_proactive_sla_guard_is_advisory_when_no_hard_breach_exists():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {"camera": 1, "vehicle_safety": 1},
        _proposal(
            "armd",
            "CONDITIONAL",
            confidence=0.99,
            proposal_score=0.40,
            safety_veto=False,
            proposal_kind="contextual_sla_guard",
            proactive_sla_guard=True,
            priority_violation="PROACTIVE_SLA_PROTECTION",
        ),
        _proposal(
            "ta_sam",
            "ALLOWED",
            confidence=0.99,
            proposal_score=0.99,
            resource_advice={
                "enabled": True,
                "delta_r_ran_vs_live": -0.03,
                "recommendation": "shift_to_ai",
            },
        ),
    )

    assert result["selected_advocate"] == "ta_sam"
    assert result["conflict_type"] == "score_precedence"
    assert result["safety_override"] is False
    assert result["armd_proposal"]["safety_veto"] is False
    assert result["armd_proposal"]["proactive_sla_guard"] is True
    assert result["resource_winner"] == "ta_sam"


def test_non_guard_tasam_resource_proposal_remains_eligible():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "CONDITIONAL"),
        _proposal(
            "ta_sam",
            "ALLOWED",
            resource_advice={"enabled": True, "delta_r_ran_vs_live": 0.01},
        ),
    )

    assert result["selected_advocate"] == "ta_sam"
    assert result["proactive_sla_guard"] is False
    assert result["resource_winner"] == "ta_sam"


def test_cooperative_hierarchy_composes_armd_floor_with_tasam_optimization():
    judge = RAppJudge({
        "enabled": True,
        "production": True,
        "composition_mode": "cooperative_hierarchy",
    })
    result = judge.decide(
        {},
        _proposal(
            "armd",
            "CONDITIONAL",
            priority_score=0.90,
            resource_allocation={
                "usable_budget": 1.0,
                "floor_total_ran": 0.35,
                "floor_total_ai": 0.15,
                "r_ran": 0.35,
                "r_ai": 0.15,
            },
        ),
        _proposal(
            "ta_sam",
            "ALLOWED",
            resource_score=0.95,
            resource_allocation={
                "usable_budget": 1.0,
                "floor_total_ran": 0.35,
                "floor_total_ai": 0.15,
                "r_ran": 0.70,
                "r_ai": 0.40,
            },
        ),
    )

    allocation = result["selected_proposal"]["resource_allocation"]
    assert result["selected_advocate"] == "joint"
    assert result["composition_mode"] == "cooperative_hierarchy"
    assert result["assistants_cooperated"] is True
    assert result["judge_verdict"] == "ALLOWED"
    assert allocation["allocation_composition"] == "armd_safety_envelope_tasam_optimization"
    assert allocation["r_ran"] >= allocation["floor_total_ran"]
    assert allocation["r_ai"] >= allocation["floor_total_ai"]
    assert allocation["r_ran"] + allocation["r_ai"] <= allocation["usable_budget"] + 1e-9


def test_cooperative_hierarchy_preserves_critical_armd_guard():
    judge = RAppJudge({
        "enabled": True,
        "production": True,
        "composition_mode": "cooperative_hierarchy",
    })
    result = judge.decide(
        {},
        _proposal(
            "armd",
            "BLOCKED",
            safety_veto=True,
            resource_allocation={
                "usable_budget": 1.0,
                "floor_total_ran": 0.35,
                "floor_total_ai": 0.15,
                "r_ran": 0.55,
                "r_ai": 0.25,
            },
        ),
        _proposal(
            "ta_sam",
            "ALLOWED",
            resource_allocation={
                "usable_budget": 1.0,
                "floor_total_ran": 0.35,
                "floor_total_ai": 0.15,
                "r_ran": 0.40,
                "r_ai": 0.20,
            },
        ),
    )

    assert result["selected_advocate"] == "joint"
    assert result["judge_verdict"] == "BLOCKED"
    assert result["armd_envelope_applied"] is True
    assert result["tasam_optimization_applied"] is True
    assert result["immediate_constraint_penalty"] == 0.0


def test_cooperative_feedback_credits_complete_package_and_keeps_component_credits():
    judge = RAppJudge({
        "enabled": True,
        "production": True,
        "composition_mode": "cooperative_hierarchy",
    })
    result = judge.decide(
        {},
        _proposal("armd", "CONDITIONAL"),
        _proposal("ta_sam", "ALLOWED"),
    )
    feedback = judge.evaluate_outcome(result, {"correct_verdict": "ALLOWED"})

    assert feedback["credit_assignment"] == "individual_correctness_cooperative"
    assert feedback["armd_credit"] == -1.0
    assert feedback["tasam_credit"] == 1.0
    assert feedback["joint_credit"] == 0.0
    assert feedback["outcome_reward"] == 0.0


def test_tasam_allocation_state_is_used_when_verdict_is_nested():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "BLOCKED", proposal_score=0.80),
        {
            "source": "ta_sam",
            "available": True,
            "valid": True,
            "feasible": True,
            "action": "MONITOR",
            "resource_advice": {"enabled": True, "allocation_state": "CONDITIONAL"},
            "proposal_score": 0.70,
        },
    )

    assert result["tasam_proposal"]["verdict"] == "CONDITIONAL"
    feedback = judge.evaluate_outcome(result, {"correct_verdict": "CONDITIONAL"})
    assert feedback["tasam_credit"] == 1.0
    assert feedback["proposal_errors"]["ta_sam"]["valid"] is True


def test_valid_assistants_cannot_be_overridden_by_rapp_fallback():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "ALLOWED", confidence=0.90),
        _proposal("ta_sam", "CONDITIONAL", confidence=0.72),
        {
            "source": "rapp_policy",
            "available": True,
            "valid": True,
            "feasible": True,
            "verdict": "BLOCKED",
            "action": "FULL_POWER_GUARD",
            "confidence": 0.80,
        },
    )

    assert result["judge_verdict"] == "ALLOWED"
    assert result["selected_advocate"] == "armd"
    assert result["conflict_type"] == "score_precedence"
    assert result["fallback_used"] is False


def test_fallback_is_used_only_when_both_assistants_are_invalid():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "UNKNOWN", available=False, valid=False),
        _proposal("ta_sam", "UNKNOWN", available=False, valid=False),
        {
            "source": "rapp_policy",
            "available": True,
            "valid": True,
            "feasible": True,
            "verdict": "CONDITIONAL",
            "action": "FULL_POWER_GUARD",
            "confidence": 0.80,
        },
    )

    assert result["selected_advocate"] == "rapp_policy"
    assert result["fallback_used"] is True
    assert result["tasam_precedence_applied"] is False


def test_outcome_feedback_rewards_both_assistants_when_both_are_correct():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "BLOCKED", safety_veto=True),
        _proposal("ta_sam", "BLOCKED"),
    )
    feedback = judge.evaluate_outcome(
        result,
        {"correct_verdict": "BLOCKED", "critical_violation": True},
    )

    assert feedback["outcome_observed"] is True
    assert feedback["severity_penalty"] == 0.0
    assert feedback["outcome_reward"] == 1.0
    assert feedback["armd_credit"] == 1.0
    assert feedback["tasam_credit"] == 1.0
    assert feedback["credit_assignment"] == "joint_correct"


def test_outcome_feedback_penalizes_both_when_they_agree_on_wrong_state():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "CONDITIONAL"),
        _proposal("ta_sam", "CONDITIONAL"),
    )
    feedback = judge.evaluate_outcome(result, {"correct_verdict": "BLOCKED"})

    assert feedback["severity_penalty"] == 0.5
    assert feedback["outcome_reward"] == 0.5
    assert feedback["armd_credit"] == -0.5
    assert feedback["tasam_credit"] == -0.5
    assert feedback["tasam_category_credit"] == -0.5
    assert feedback["tasam_category_penalty"] == 0.5
    assert feedback["tasam_category_error"] is True
    assert feedback["credit_assignment"] == "joint_error"
    assert feedback["tasam_training_category_credit"] == -1.0
    assert feedback["tasam_training_category_penalty"] == 1.0


def test_correct_assistant_gets_full_credit_and_wrong_adjacent_state_is_penalized():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "CONDITIONAL"),
        _proposal("ta_sam", "ALLOWED"),
    )
    feedback = judge.evaluate_outcome(result, {"correct_verdict": "CONDITIONAL"})

    assert feedback["armd_credit"] == 1.0
    assert feedback["tasam_credit"] == -0.5
    assert feedback["credit_assignment"] == "individual_correctness"
    assert feedback["proposal_errors"]["ta_sam"]["classification"] == "state_error"


def test_critical_underreaction_receives_strong_penalty():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "BLOCKED", safety_veto=True),
        _proposal("ta_sam", "ALLOWED"),
    )
    feedback = judge.evaluate_outcome(
        result,
        {"correct_verdict": "BLOCKED", "critical_violation": True, "resource_reward": 1.0},
    )

    assert feedback["armd_credit"] == 1.0
    assert feedback["tasam_credit"] == -1.0
    assert feedback["proposal_errors"]["ta_sam"]["classification"] == "critical_error"
    assert feedback["proposal_errors"]["ta_sam"]["penalty"] == 1.0


def test_tasam_blocked_when_conditional_is_always_penalized():
    judge = RAppJudge({"enabled": True, "production": True})
    result = {
        "selected_proposal": _proposal("ta_sam", "BLOCKED"),
        "armd_proposal": _proposal("armd", "CONDITIONAL"),
        "tasam_proposal": _proposal("ta_sam", "BLOCKED"),
    }
    feedback = judge.evaluate_outcome(
        result,
        {"correct_verdict": "CONDITIONAL", "resource_reward": 1.0},
    )
    assert feedback["tasam_category_credit"] == -1.0
    assert feedback["tasam_category_penalty"] == 1.0
    assert feedback["tasam_credit"] == -1.0
    assert feedback["tasam_resource_credit"] == 1.0
    assert feedback["tasam_training_category_credit"] == -2.0
    assert feedback["tasam_training_category_penalty"] == 2.0


def test_directional_one_level_penalty_applies_to_all_category_pairs():
    judge = RAppJudge({"enabled": True, "production": True})

    def tasam_credit(predicted, observed):
        result = {
            "selected_proposal": _proposal("ta_sam", predicted),
            "armd_proposal": _proposal("armd", observed),
            "tasam_proposal": _proposal("ta_sam", predicted),
        }
        feedback = judge.evaluate_outcome(result, {"correct_verdict": observed})
        return feedback["tasam_category_credit"], feedback["tasam_category_penalty"]

    assert tasam_credit("BLOCKED", "CONDITIONAL") == (-1.0, 1.0)
    assert tasam_credit("CONDITIONAL", "BLOCKED") == (-0.5, 0.5)
    assert tasam_credit("CONDITIONAL", "ALLOWED") == (-1.0, 1.0)
    assert tasam_credit("ALLOWED", "CONDITIONAL") == (-0.5, 0.5)


def test_two_level_errors_remain_maximally_penalized_in_both_directions():
    judge = RAppJudge({"enabled": True, "production": True})
    for predicted, observed in (("ALLOWED", "BLOCKED"), ("BLOCKED", "ALLOWED")):
        proposal = _proposal("ta_sam", predicted)
        feedback = judge.evaluate_outcome(
            {
                "selected_proposal": proposal,
                "armd_proposal": _proposal("armd", observed),
                "tasam_proposal": proposal,
            },
            {"correct_verdict": observed},
        )
        assert feedback["tasam_category_credit"] == -1.0
        assert feedback["tasam_category_penalty"] == 1.0


def test_invalid_tasam_proposal_gets_maximum_categorical_penalty():
    judge = RAppJudge({"enabled": True, "production": True})
    invalid = _proposal("ta_sam", "UNKNOWN", valid=False, available=False)
    result = {
        "selected_proposal": invalid,
        "armd_proposal": _proposal("armd", "CONDITIONAL"),
        "tasam_proposal": invalid,
    }
    feedback = judge.evaluate_outcome(result, {"correct_verdict": "CONDITIONAL"})
    assert feedback["tasam_category_credit"] == -1.0
    assert feedback["tasam_category_penalty"] == 1.0
    assert feedback["tasam_category_error"] is True
    assert feedback["tasam_training_category_credit"] == -2.0
    assert feedback["tasam_training_category_penalty"] == 2.0


def test_feedback_waits_for_delayed_observation():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide({}, _proposal("armd", "ALLOWED"), {})
    feedback = judge.evaluate_outcome(result, {})

    assert feedback["outcome_observed"] is False
    assert feedback["feedback_status"] == "awaiting_observation"


def test_normal_observation_penalizes_overconservative_armd():
    judge = RAppJudge({"enabled": True, "production": True})
    result = judge.decide(
        {},
        _proposal("armd", "BLOCKED", safety_veto=False),
        _proposal("ta_sam", "ALLOWED"),
    )
    feedback = judge.evaluate_outcome(
        result,
        {
            "correct_verdict": "ALLOWED",
            "observed": True,
        },
    )

    assert feedback["armd_credit"] == -1.0
    assert feedback["tasam_credit"] == 1.0
    assert feedback["credit_assignment"] == "individual_correctness"


def test_observed_classifier_distinguishes_normal_warning_and_critical():
    config = {
        "shared_resources": {
            "camera_throughput_target_mbps": 25.0,
            "camera_throughput_guard_mbps": 30.0,
            "camera_latency_warning_ms": 80.0,
            "camera_latency_target_ms": 100.0,
        }
    }
    normal = RAppJudge.derive_observed_outcome(
        {"camera_metrics": {"active_cameras": 1, "throughput_ready": True, "throughput_mbps": 35, "latency_ms": 20}},
        config,
    )
    warning = RAppJudge.derive_observed_outcome(
        {"camera_metrics": {"active_cameras": 1, "throughput_ready": True, "throughput_mbps": 27, "latency_ms": 20}},
        config,
    )
    critical = RAppJudge.derive_observed_outcome(
        {"camera_metrics": {"active_cameras": 1, "throughput_ready": True, "throughput_mbps": 20, "latency_ms": 20}},
        config,
    )

    assert normal["correct_verdict"] == "ALLOWED"
    assert warning["correct_verdict"] == "CONDITIONAL"
    assert critical["correct_verdict"] == "BLOCKED"


def test_delayed_feedback_is_persisted_in_datalake():
    with tempfile.TemporaryDirectory() as tmp:
        lake = DataLake(f"{tmp}/rapp.db")
        lake.record_judge_outcome(
            {
                "timestamp": 101,
                "selected_assistant": "armd",
                "rapp_judge_result": {"selected_advocate": "armd"},
            },
            {
                "correct_verdict": "ALLOWED",
                "outcome_observed": True,
                "outcome_reward": 0.5,
                "severity_penalty": 0.5,
                "armd_credit": -0.5,
                "tasam_credit": 1.0,
                "tasam_category_credit": 1.0,
                "tasam_category_penalty": 0.0,
                "tasam_category_error": False,
                "tasam_predicted_verdict": "ALLOWED",
                "tasam_observed_verdict": "ALLOWED",
                "credit_assignment": "individual_correctness",
            },
            {"correct_verdict": "ALLOWED", "reason": "rede normal"},
            observed_timestamp=102,
        )
        row = sqlite3.connect(f"{tmp}/rapp.db").execute(
            "SELECT correct_verdict, armd_credit, tasam_credit, "
            "tasam_category_credit, tasam_category_penalty, tasam_category_error, "
            "tasam_predicted_verdict, tasam_observed_verdict, reason "
            "FROM judge_outcome_history WHERE decision_timestamp = 101"
        ).fetchone()
        assert row == ("ALLOWED", -0.5, 1.0, 1.0, 0.0, 0, "ALLOWED", "ALLOWED", "rede normal")


def test_datalake_persists_exact_decision_id_and_stage_alignment():
    with tempfile.TemporaryDirectory() as tmp:
        lake = DataLake(f"{tmp}/rapp.db")
        decision = {
            "timestamp": 101,
            "collection_event_stage_name": "camera_conditional",
            "selected_assistant": "ta_sam",
            "rapp_judge_result": {"selected_advocate": "ta_sam"},
        }
        decision_id = lake.record_decision(decision)
        assert decision_id is not None
        decision["decision_id"] = decision_id
        lake.record_judge_outcome(
            decision,
            {
                "correct_verdict": "BLOCKED",
                "outcome_observed": True,
                "tasam_category_credit": -0.5,
                "tasam_category_penalty": 0.5,
                "tasam_category_error": True,
                "tasam_predicted_verdict": "CONDITIONAL",
                "tasam_observed_verdict": "BLOCKED",
                "decision_stage_name": "camera_conditional",
                "observed_stage_name": "camera_blocked",
                "stage_boundary_feedback": True,
                "nominal_expected_verdict": "CONDITIONAL",
            },
            {"correct_verdict": "BLOCKED", "reason": "transição real"},
            observed_timestamp=102,
        )
        row = sqlite3.connect(f"{tmp}/rapp.db").execute(
            "SELECT decision_id, decision_stage_name, observed_stage_name, "
            "stage_boundary_feedback, nominal_expected_verdict "
            "FROM judge_outcome_history WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        assert row == (decision_id, "camera_conditional", "camera_blocked", 1, "CONDITIONAL")


def test_stage_nominal_labels_are_explicit_and_observed_classification_is_real():
    assert expected_verdict_for_stage("allowed_stable") == "ALLOWED"
    assert expected_verdict_for_stage("vehicle_conditional") == "CONDITIONAL"
    healthy = {
        "camera_metrics": {"active_cameras": 3, "throughput_ready": True, "throughput_mbps": 32, "latency_ms": 18},
        "vehicle_metrics": {
            "available": True, "total_vehicles": 5, "ego_present": True,
            "max_latency_ms": 8, "max_packet_loss_percent": 0.2,
        },
        "app2_metrics": {"delivery_success_percent": 99, "packet_loss_percent": 1.2, "avg_latency_ms": 118},
        "network_health": {"cvar_us": 50000},
    }
    conditional_vehicle = {
        **healthy,
        "vehicle_metrics": {
            "available": True, "total_vehicles": 5, "ego_present": True,
            "medium_risk_vehicles": 1, "max_latency_ms": 14, "max_packet_loss_percent": 0.6,
        },
    }
    assert RAppJudge.derive_observed_outcome(healthy)["correct_verdict"] == "ALLOWED"
    assert RAppJudge.derive_observed_outcome(conditional_vehicle)["correct_verdict"] == "CONDITIONAL"


def test_continuous_observed_reward_is_monotonic_and_records_components():
    healthy = {
        "camera_metrics": {"active_cameras": 1, "throughput_mbps": 35, "latency_ms": 20},
        "vehicle_metrics": {"available": True, "total_vehicles": 1, "max_latency_ms": 10, "max_packet_loss_percent": 0.1},
        "app2_metrics": {"delivery_success_percent": 100, "packet_loss_percent": 0.1, "avg_latency_ms": 100},
        "network_health": {"cvar_us": 50000, "p95_us": 60000, "global_packet_loss_rate": 0.001},
        "resource_allocation": {"r_ran": 0.5, "r_ai": 0.4, "ran_demand": 0.5, "ai_demand": 0.4, "usable_budget": 1.0},
    }
    degraded = {
        **healthy,
        "camera_metrics": {"active_cameras": 1, "throughput_mbps": 10, "latency_ms": 300},
        "vehicle_metrics": {"available": True, "total_vehicles": 1, "max_latency_ms": 100, "max_packet_loss_percent": 20, "high_risk_vehicles": 1},
        "network_health": {"cvar_us": 300000, "p95_us": 250000, "global_packet_loss_rate": 0.2},
        "resource_allocation": {"r_ran": 0.1, "r_ai": 0.1, "ran_demand": 0.8, "ai_demand": 0.8, "usable_budget": 1.0},
    }
    first = RAppJudge.compute_observed_error({}, healthy, {})
    second = RAppJudge.compute_observed_error({}, degraded, {})
    assert second["tasam_observed_error"] > first["tasam_observed_error"]
    assert second["tasam_continuous_reward"] < first["tasam_continuous_reward"]
    assert second["tasam_error_components"]["resource_error"] > 0
    assert second["tasam_reward_source"] == "observed_real_metrics"
