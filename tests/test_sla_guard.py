import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapp_orchestrator import RappResourceOptimizer


def _optimizer():
    optimizer = RappResourceOptimizer.__new__(RappResourceOptimizer)
    optimizer.cycle = 1
    return optimizer


def _base_decision():
    return {
        "camera_metrics": {
            "active_cameras": 4,
            "throughput_mbps": 24.0,
            "throughput_ready": True,
            "latency_ms": 60.0,
        },
        "vehicle_metrics": {
            "available": True,
            "total_vehicles": 5,
            "max_latency_ms": 18.0,
            "max_packet_loss_percent": 0.1,
        },
    }


def _armd_noop():
    return {
        "available": True,
        "proposal_present": True,
        "proposal_valid": True,
        "scenario": "greenran_global_noop",
        "confidence": 0.75,
    }


def _tasam_reduction(delta=-0.03):
    return {
        "valid": True,
        "feasible": True,
        "resource_advice": {"enabled": True, "delta_r_ran_vs_live": delta},
    }


def test_camera_and_vehicle_activity_creates_contextual_guard():
    advice = _optimizer()._build_proactive_sla_guard_advice(
        _base_decision(), _armd_noop(), _tasam_reduction()
    )

    assert advice["proposal_kind"] == "contextual_sla_guard"
    assert advice["scenario"] == "proactive_sla_guard"
    assert advice["domain"] == "camera"
    assert advice["safety_veto"] is True
    assert advice["priority_violation"] == "PROACTIVE_SLA_PROTECTION"
    assert advice["proactive_sla_guard"] is True

    proposal = _optimizer()._build_armd_proposal(
        {"timestamp": 1}, advice, {"usable_budget": 1.0, "resource_budget": 1.0}
    )
    assert proposal["resource_allocation"]["proposal_priority"] == "camera"
    assert proposal["resource_allocation"]["allocation_state"] == "BLOCKED"
    assert proposal["resource_allocation"]["reinforcement_ran"] > 0.0
    assert proposal["safety_veto"] is True


def test_vehicle_only_activity_is_protected_when_camera_is_absent():
    decision = _base_decision()
    decision["camera_metrics"]["active_cameras"] = 0
    decision["vehicle_metrics"]["max_latency_ms"] = 21.0
    advice = _optimizer()._build_proactive_sla_guard_advice(
        decision, _armd_noop(), _tasam_reduction(-0.02)
    )

    assert advice["proposal_kind"] == "contextual_sla_guard"
    assert advice["domain"] == "vehicle"
    assert advice["safety_veto"] is True


def test_no_active_priority_service_does_not_create_guard():
    decision = _base_decision()
    decision["camera_metrics"]["active_cameras"] = 0
    decision["vehicle_metrics"]["total_vehicles"] = 0
    advice = _optimizer()._build_proactive_sla_guard_advice(
        decision, _armd_noop(), _tasam_reduction()
    )

    assert advice["scenario"] == "greenran_floor_minimum"
    assert advice["proposal_kind"] == "resource_floor"
    assert advice["source"] == "runtime_floor"
    assert advice["safety_veto"] is False
    assert advice["proactive_sla_guard"] is False


def test_warning_margin_keeps_energy_guard_without_resource_veto():
    decision = _base_decision()
    decision["camera_metrics"]["throughput_mbps"] = 28.0
    advice = _optimizer()._build_proactive_sla_guard_advice(
        decision, _armd_noop(), _tasam_reduction()
    )
    assert advice["scenario"] == "greenran_floor_minimum"
    assert advice["proposal_kind"] == "resource_floor"

    proposal = _optimizer()._build_armd_proposal(
        {"timestamp": 1, "priority_violation": "VEHICLE_WARNING"},
        {**_armd_noop(), "domain": "vehicle", "expected_energy_saver": "CONDITIONAL", "expected_action": "FULL_POWER_GUARD"},
        {"usable_budget": 1.0, "resource_budget": 1.0},
    )
    assert proposal["verdict"] == "CONDITIONAL"
    assert proposal["action"] == "FULL_POWER_GUARD"
    assert proposal["safety_veto"] is False
    assert proposal["priority_score"] == 0.25


def test_relative_cvar_regression_requires_two_windows_before_guard():
    optimizer = _optimizer()
    decision = _base_decision()
    decision["camera_metrics"].update({"throughput_mbps": 32.0, "latency_ms": 18.0})
    decision["network_health"] = {"cvar_us": 5500.0}
    optimizer._prev_cvar_us = 2000.0
    first = optimizer._build_proactive_sla_guard_advice(decision, _armd_noop(), _tasam_reduction())
    assert first["scenario"] == "greenran_floor_minimum"
    assert first["proposal_kind"] == "resource_floor"

    second = optimizer._build_proactive_sla_guard_advice(decision, _armd_noop(), _tasam_reduction())
    assert second["proposal_kind"] == "contextual_sla_guard"
    assert second["domain"] == "camera"
    assert any("CVaR subiu" in reason for reason in [second["reason"]])


def test_critical_cvar_creates_network_guard_without_priority_service():
    optimizer = _optimizer()
    decision = _base_decision()
    decision["camera_metrics"]["active_cameras"] = 0
    decision["vehicle_metrics"]["total_vehicles"] = 0
    decision["network_health"] = {"cvar_us": 130000.0}
    advice = optimizer._build_proactive_sla_guard_advice(decision, _armd_noop(), _tasam_reduction())
    assert advice["proposal_kind"] == "contextual_sla_guard"
    assert advice["domain"] == "network"
    assert advice["safety_veto"] is True


def test_cvar_guard_applies_even_without_ran_reduction():
    optimizer = _optimizer()
    decision = _base_decision()
    decision["camera_metrics"].update({"throughput_mbps": 32.0, "latency_ms": 18.0})
    decision["network_health"] = {"cvar_us": 2_200.0}
    optimizer._prev_cvar_us = 2_000.0
    tasam = _tasam_reduction(delta=0.0)
    first = optimizer._build_proactive_sla_guard_advice(decision, _armd_noop(), tasam)
    assert first["scenario"] == "greenran_floor_minimum"
    assert first["proposal_kind"] == "resource_floor"
    second = optimizer._build_proactive_sla_guard_advice(decision, _armd_noop(), tasam)
    assert second["proposal_kind"] == "contextual_sla_guard"
    assert second["safety_veto"] is True
    assert second["guard_thresholds"]["cvar_relative_regression_pct"] == 5.0
