"""Common adaptive reward contract for new GreenRAN V2X campaigns.

The module is deliberately dependency free and side-effect free.  Historical
reward contracts do not call it; callers opt in with the exact contract name.
Keeping the compositor here makes the Judge, the online environment and the
dataset exporter use the same calculation and the same immutable snapshot.
"""

from __future__ import annotations

from typing import Any, Mapping


REWARD_CONTRACT = "greenran.tasam.v2x.reward_adaptive.v1"
DEFAULT_CONFIG: dict[str, Any] = {
    "schema": REWARD_CONTRACT,
    "update_cadence": "per_rapp_decision",
    "ewma_previous": 0.75,
    "ewma_current": 0.25,
    "healthy_exit_decisions": 3,
    "max_energy_weight": 0.30,
    "v2x_base": 0.45,
    "v2x_risk_slope": 0.35,
    "equity_base": 0.25,
    "equity_risk_slope": 0.20,
    "p95_limit_ms": 20.0,
    "loss_limit_percent": 1.0,
    "healthy_risk_threshold": 0.0,
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result and abs(result) != float("inf") else default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "ok", "complete", "confirmed"}


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, _number(value, low)))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _evidence(observation: Mapping[str, Any]) -> tuple[bool, list[str]]:
    quality = _mapping(_first(
        observation.get("collection_quality"),
        _mapping(observation.get("metrics")).get("collection_quality"),
    ))
    reasons: list[str] = []
    collector = _first(quality.get("collector_mode"), observation.get("collector_mode"))
    if collector != "pdcp_real":
        reasons.append("collector_mode_not_pdcp_real")
    if not _bool(_first(quality.get("pdcp_real"), observation.get("pdcp_real"))):
        reasons.append("pdcp_not_real")
    proxy = _number(_first(
        quality.get("proxy_latency_sample_count"),
        observation.get("proxy_latency_sample_count"),
        _mapping(observation.get("metrics")).get("proxy_latency_sample_count"),
    ), -1.0)
    if proxy != 0.0:
        reasons.append("proxy_samples_present_or_unknown")

    ack_value = _first(
        observation.get("e2_ack_complete"),
        observation.get("ack_e2"),
        observation.get("e2_ack"),
        observation.get("feedback_integrity_valid"),
        quality.get("e2_ack_complete"),
    )
    if ack_value is None or not _bool(ack_value):
        reasons.append("e2_ack_incomplete")
    correlated = _first(
        observation.get("decision_correlation_valid"),
        observation.get("action_correlation_valid"),
        quality.get("decision_correlation_valid"),
    )
    if correlated is None:
        decision_id = _first(observation.get("decision_id"), _mapping(observation.get("decision")).get("decision_id"))
        action_id = _first(observation.get("action_correlation_id"), _mapping(observation.get("decision")).get("action_correlation_id"))
        correlated = bool(decision_id and action_id)
    if not _bool(correlated):
        reasons.append("decision_correlation_missing")
    return not reasons, reasons


def _vehicle_risk(observation: Mapping[str, Any], cfg: Mapping[str, Any]) -> tuple[float, dict[str, float], list[str]]:
    vehicle = _mapping(observation.get("vehicle_metrics"))
    network = _mapping(observation.get("network_health"))
    link = _mapping(_first(observation.get("link_metrics"), observation.get("vehicle_link"), observation.get("link_state")))
    reasons: list[str] = []
    p95 = _first(vehicle.get("max_latency_ms"), vehicle.get("latency_p95_ms"), network.get("p95_ms"))
    if p95 is None and network.get("p95_us") is not None:
        p95 = _number(network.get("p95_us")) / 1000.0
    loss = _first(vehicle.get("max_packet_loss_percent"), vehicle.get("packet_loss_percent"), network.get("packet_loss_percent"))
    p95_limit = max(_number(cfg.get("p95_limit_ms"), 20.0), 1e-9)
    p95_warning = min(10.0, p95_limit)
    loss_limit = max(_number(cfg.get("loss_limit_percent"), 1.0), 1e-9)
    loss_warning = min(0.5, loss_limit)
    if p95 is None:
        reasons.append("p95_missing")
        p95_error = 1.0
    else:
        p95_value = _number(p95)
        p95_error = _clamp((p95_value - p95_warning) / max(p95_limit - p95_warning, 1e-9))
        if p95_value >= p95_limit:
            reasons.append("p95_strict_failure")
    if loss is None:
        reasons.append("loss_missing")
        loss_error = 1.0
    else:
        loss_value = _number(loss)
        loss_error = _clamp((loss_value - loss_warning) / max(loss_limit - loss_warning, 1e-9))
        if loss_value >= loss_limit:
            reasons.append("loss_strict_failure")
    gbr_deficit = _first(
        observation.get("gbr_deficit_persistent"),
        observation.get("scheduler_capacity_shortfall"),
        observation.get("gbr_credit_deficit"),
        vehicle.get("gbr_deficit_persistent"),
        vehicle.get("scheduler_capacity_shortfall"),
        vehicle.get("gbr_credit_deficit"),
    )
    if _bool(gbr_deficit) or _number(gbr_deficit, 0.0) > 0.0:
        reasons.append("persistent_gbr_deficit")
    harq_drops = _number(_first(link.get("harq_max_retx_drops"), vehicle.get("harq_max_retx_drops")), 0.0)
    if harq_drops > 0:
        reasons.append("harq_max_retx_drop")
    harq_nacks = _number(_first(link.get("harq_nack_streak"), vehicle.get("harq_nack_streak")), 0.0)
    harq_error = _clamp(harq_nacks / 2.0)
    if harq_nacks >= 2:
        reasons.append("harq_nack_streak")
    if _bool(_first(link.get("outage"), link.get("radio_outage"), vehicle.get("outage"))):
        reasons.append("radio_outage")
    pending = _bool(_first(link.get("handover_pending"), observation.get("handover_pending")))
    confirmed = _bool(_first(link.get("handover_ack"), link.get("handover_confirmed"), observation.get("handover_confirmed")))
    if pending and not confirmed:
        reasons.append("handover_without_confirmation")
    components = {"p95": p95_error, "loss": loss_error, "harq": harq_error}
    hard = 1.0 if reasons else 0.0
    return max(p95_error, loss_error, harq_error, hard), components, reasons


def _equity_risk(observation: Mapping[str, Any]) -> tuple[float, dict[str, float], list[str]]:
    vehicle = _mapping(observation.get("vehicle_metrics"))
    camera = _mapping(observation.get("camera_metrics"))
    sensors = _mapping(_first(observation.get("app2_metrics"), observation.get("sensor_metrics")))
    reasons: list[str] = []
    floor = _first(
        observation.get("floor_violations"), observation.get("per_ue_floor_violation_count"),
        vehicle.get("floor_violations"), vehicle.get("floor_violation_count"),
    )
    starvation = _first(observation.get("starvation_ue_count"), observation.get("starvation"), vehicle.get("starvation_ue_count"))
    floor_error = 1.0 if (floor is not None and _number(floor) > 0) or _first(observation.get("floor_feasible"), vehicle.get("floor_feasible")) is False else 0.0
    starvation_error = 1.0 if (starvation is not None and (_number(starvation) > 0 or _bool(starvation))) else 0.0
    if floor_error:
        reasons.append("ue_floor_violation")
    if starvation_error:
        reasons.append("ue_starvation")
    camera_error = max(
        _clamp((95.0 - _number(camera.get("completion_percent"), 100.0)) / 95.0),
        _clamp(_number(camera.get("packet_loss_percent"), 0.0) / 1.0),
    ) if camera else 0.0
    sensor_error = max(
        _clamp((90.0 - _number(sensors.get("delivery_success_percent"), 100.0)) / 90.0),
        _clamp(_number(sensors.get("packet_loss_percent"), 0.0) / 1.0),
    ) if sensors else 0.0
    components = {"floor": floor_error, "starvation": starvation_error, "camera": camera_error, "sensors": sensor_error}
    return max(floor_error, starvation_error, camera_error, sensor_error), components, reasons


def compose_adaptive_reward(
    previous_decision: Mapping[str, Any] | None,
    observation: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
    *,
    energy_enabled: bool | None = None,
    energy_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose one immutable adaptive-reward snapshot for a V2X decision."""
    previous = _mapping(previous_decision)
    current = _mapping(observation)
    supplied = _mapping(config)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_mapping(supplied.get("reward")))
    cfg.update({key: value for key, value in supplied.items() if key in cfg})
    evidence_ok, evidence_reasons = _evidence(current)
    v2x_raw, v2x_components, v2x_reasons = _vehicle_risk(current, cfg)
    equity_raw, equity_components, equity_reasons = _equity_risk(current)
    state = _mapping(_first(current.get("adaptive_reward_state"), previous.get("adaptive_reward_state")))
    prev_v2x = _number(state.get("risk_v2x_ewma"), v2x_raw)
    prev_equity = _number(state.get("risk_equity_ewma"), equity_raw)
    alpha_previous = _number(cfg.get("ewma_previous"), 0.75)
    alpha_current = _number(cfg.get("ewma_current"), 0.25)
    v2x_ewma = alpha_previous * prev_v2x + alpha_current * v2x_raw
    equity_ewma = alpha_previous * prev_equity + alpha_current * equity_raw
    hard_reasons = evidence_reasons + v2x_reasons + equity_reasons
    raw_critical = bool(hard_reasons) or v2x_raw >= 1.0 or equity_raw >= 1.0
    previous_critical = _bool(state.get("critical"))
    healthy = not raw_critical and v2x_raw <= _number(cfg.get("healthy_risk_threshold"), 0.0) and equity_raw <= _number(cfg.get("healthy_risk_threshold"), 0.0)
    healthy_streak = int(_number(state.get("healthy_streak"), 0)) + 1 if healthy else 0
    exit_count = max(1, int(_number(cfg.get("healthy_exit_decisions"), 3)))
    critical = raw_critical or (previous_critical and healthy_streak < exit_count)
    risk_v2x = 1.0 if critical and (raw_critical or previous_critical) else max(v2x_raw, v2x_ewma)
    risk_equity = 1.0 if critical and (raw_critical or previous_critical) else max(equity_raw, equity_ewma)
    energy = _mapping(_first(energy_evidence, current.get("energy_evidence")))
    native_energy = _bool(_first(energy.get("native"), energy.get("native_e2"), energy.get("e2_native")))
    energy_ack = _bool(_first(energy.get("e2_ack"), energy.get("ack_complete")))
    energy_allowed = energy_enabled if energy_enabled is not None else _bool(supplied.get("energy_enabled"))
    eligible = bool(energy_allowed and native_energy and energy_ack and evidence_ok and not critical and healthy_streak >= exit_count and not v2x_reasons and not equity_reasons)
    energy_weight = _number(cfg.get("max_energy_weight"), 0.30) * (1.0 if eligible else 0.0) * (1.0 - risk_v2x) * (1.0 - risk_equity)
    energy_weight = min(_number(cfg.get("max_energy_weight"), 0.30), max(0.0, energy_weight))
    a = _number(cfg.get("v2x_base"), 0.45) + _number(cfg.get("v2x_risk_slope"), 0.35) * risk_v2x
    b = _number(cfg.get("equity_base"), 0.25) + _number(cfg.get("equity_risk_slope"), 0.20) * risk_equity
    denominator = max(a + b, 1e-9)
    v2x_weight = (1.0 - energy_weight) * a / denominator
    equity_weight = (1.0 - energy_weight) * b / denominator
    energy_cost = _clamp(_first(energy.get("cost"), energy.get("cost_fraction"), current.get("energy_cost"), current.get("power_percent", 0.0) / 100.0))
    error_v2x = max(v2x_raw, v2x_ewma)
    error_equity = max(equity_raw, equity_ewma)
    reward = _clamp(1.0 - (v2x_weight * error_v2x + equity_weight * error_equity + energy_weight * energy_cost), -1.0, 1.0)
    if hard_reasons:
        reward = min(reward, 0.0)
    return {
        "reward_contract": REWARD_CONTRACT,
        "reward": round(reward, 9),
        "continuous_reward": round(reward, 9),
        "observed_error": round(1.0 - reward, 9),
        "risk_v2x_raw": round(v2x_raw, 9),
        "risk_v2x": round(risk_v2x, 9),
        "risk_equity_raw": round(equity_raw, 9),
        "risk_equity": round(risk_equity, 9),
        "healthy": healthy,
        "healthy_streak": healthy_streak,
        "critical": critical,
        "energy_eligible": eligible,
        "energy_cost": round(energy_cost, 9),
        "weights": {"v2x": round(v2x_weight, 9), "equity": round(equity_weight, 9), "energy": round(energy_weight, 9)},
        "errors": {"v2x": round(error_v2x, 9), "equity": round(error_equity, 9), "energy": round(energy_cost, 9)},
        "raw_components": {"v2x": v2x_components, "equity": equity_components},
        "safety_reasons": hard_reasons,
        "evidence_valid": evidence_ok,
        "adaptive_reward_state": {
            "risk_v2x_ewma": round(v2x_ewma, 9),
            "risk_equity_ewma": round(equity_ewma, 9),
            "healthy_streak": healthy_streak,
            "critical": critical,
        },
        "snapshot": {
            "contract": REWARD_CONTRACT,
            "max_energy_weight": _number(cfg.get("max_energy_weight"), 0.30),
            "ewma_previous": alpha_previous,
            "ewma_current": alpha_current,
            "healthy_exit_decisions": exit_count,
            "v2x_base": _number(cfg.get("v2x_base"), 0.45),
            "v2x_risk_slope": _number(cfg.get("v2x_risk_slope"), 0.35),
            "equity_base": _number(cfg.get("equity_base"), 0.25),
            "equity_risk_slope": _number(cfg.get("equity_risk_slope"), 0.20),
        },
    }


def is_adaptive_reward_config(config: Mapping[str, Any] | None) -> bool:
    value = _mapping(config)
    return value.get("reward_contract") == REWARD_CONTRACT or value.get("schema") == REWARD_CONTRACT or _mapping(value.get("reward")).get("schema") == REWARD_CONTRACT
