from __future__ import annotations

from copy import deepcopy

import pytest

from src.greenran_v2x_adaptive_reward import REWARD_CONTRACT, compose_adaptive_reward
from src.rapp_judge import RAppJudge


def _healthy_observation() -> dict:
    return {
        "vehicle_metrics": {
            "available": True,
            "total_vehicles": 5,
            "max_latency_ms": 10.0,
            "max_packet_loss_percent": 0.0,
        },
        "collection_quality": {
            "collector_mode": "pdcp_real",
            "pdcp_real": True,
            "proxy_latency_sample_count": 0,
        },
        "e2_ack_complete": True,
        "decision_correlation_valid": True,
        "energy_evidence": {"native": True, "e2_ack": True, "cost": 0.5},
    }


def test_adaptive_weights_reach_formula_and_energy_cap_after_three_healthy_decisions():
    previous = {}
    result = None
    for _ in range(3):
        result = compose_adaptive_reward(
            previous,
            _healthy_observation(),
            {"reward_contract": REWARD_CONTRACT, "energy_enabled": True},
        )
        previous = {"adaptive_reward_state": result["adaptive_reward_state"]}

    assert result is not None
    assert result["energy_eligible"] is True
    assert result["weights"]["energy"] == pytest.approx(0.30)
    assert result["weights"]["v2x"] == pytest.approx(0.45)
    assert result["weights"]["equity"] == pytest.approx(0.25)
    assert sum(result["weights"].values()) == pytest.approx(1.0)


def test_critical_v2x_failure_cannot_be_compensated_by_energy():
    observation = _healthy_observation()
    observation["vehicle_metrics"]["max_latency_ms"] = 25.0
    result = compose_adaptive_reward(
        {}, observation, {"reward_contract": REWARD_CONTRACT, "energy_enabled": True}
    )

    assert result["risk_v2x"] == 1.0
    assert result["critical"] is True
    assert result["energy_eligible"] is False
    assert result["weights"]["energy"] == 0.0
    assert result["reward"] <= 0.0


def test_missing_ack_proxy_or_telemetry_is_fail_closed():
    observation = _healthy_observation()
    observation["e2_ack_complete"] = False
    observation["collection_quality"]["proxy_latency_sample_count"] = 1
    result = compose_adaptive_reward(
        {}, observation, {"reward_contract": REWARD_CONTRACT, "energy_enabled": True}
    )

    assert result["energy_eligible"] is False
    assert result["critical"] is True
    assert "e2_ack_incomplete" in result["safety_reasons"]
    assert "proxy_samples_present_or_unknown" in result["safety_reasons"]


def test_judge_and_common_compositor_have_identical_snapshot():
    observation = _healthy_observation()
    config = {"reward_contract": REWARD_CONTRACT, "energy_enabled": True}
    direct = compose_adaptive_reward({}, observation, config)
    judged = RAppJudge.compute_observed_error({}, observation, config)

    assert judged["reward_contract"] == REWARD_CONTRACT
    assert judged["tasam_adaptive_reward"]["weights"] == direct["weights"]
    assert judged["tasam_continuous_reward"] == pytest.approx(direct["reward"])
    assert judged["reward_weight_snapshot"] == direct["snapshot"]


def test_reward_snapshot_is_immutable_by_value_between_decisions():
    first = compose_adaptive_reward({}, _healthy_observation(), {"reward_contract": REWARD_CONTRACT})
    state = deepcopy(first["adaptive_reward_state"])
    second = compose_adaptive_reward(
        {"adaptive_reward_state": state},
        _healthy_observation(),
        {"reward_contract": REWARD_CONTRACT},
    )
    assert first["snapshot"] == second["snapshot"]
    assert first["adaptive_reward_state"] is not second["adaptive_reward_state"]
