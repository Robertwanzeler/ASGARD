"""Article-faithful online MARL environment for the current GreenRAN scenario."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

try:
    from greenran_marl_topology import load_logical_du_topology
    from rapp_sac_resource_model import compute_shared_resource_snapshot
except ImportError:  # pragma: no cover - fallback when imported as package
    from src.greenran_marl_topology import load_logical_du_topology
    from src.rapp_sac_resource_model import compute_shared_resource_snapshot


SLICE_ORDER = ("eMBB", "mMTC", "URLLC")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    value = _safe_float(value, lower)
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def _power_percent(resource_action: dict[str, Any]) -> float:
    """Return the applied/proposed power level using the campaign contract."""
    raw = resource_action.get("power_percent", resource_action.get("tasam_power_percent"))
    if raw is None:
        raw = resource_action.get("energy_power_level", 100.0)
    if isinstance(raw, str):
        aliases = {
            "ECO": 25.0,
            "POWER_DOWN_ECO": 25.0,
            "REDUCE": 60.0,
            "REDUCE_POWER": 60.0,
            "CONDITIONAL_REDUCE": 60.0,
            "FULL_POWER": 100.0,
        }
        raw = aliases.get(raw.strip().upper(), raw)
    return max(25.0, min(100.0, _safe_float(raw, 100.0)))


def _normalize(raw: Sequence[float]) -> list[float]:
    cleaned = [max(0.0, _safe_float(value, 0.0)) for value in raw]
    total = sum(cleaned)
    if total <= 1e-12:
        return [1.0 / max(len(cleaned), 1)] * len(cleaned)
    return [value / total for value in cleaned]


def build_balanced_vehicle_energy_reward(
    slice_state: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
    resource_action: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    """Reward GreenRAN priorities while discouraging unnecessary full allocation.

    URLLC is the vehicle slice in the fixed GreenRAN topology.  The reward
    keeps cameras and sensors in the objective, gives vehicles the largest
    service weight, and applies a moderate resource-use penalty only above
    80% of the usable budget.  Energy remains a proxy: no hardware power
    meter is available in the training trace.
    """
    embb = slice_state["eMBB"]
    mmtc = slice_state["mMTC"]
    urllc = slice_state["URLLC"]
    embb_qos = 0.55 * _clamp(embb.get("completion_ratio")) + 0.45 * (1.0 - _clamp(embb.get("qos_pressure")))
    mmtc_qos = 0.55 * _clamp(mmtc.get("completion_ratio")) + 0.45 * (1.0 - _clamp(mmtc.get("qos_pressure")))

    # Vehicle SLA and global tail latency are different signals.  The old
    # fallback used global CVaR as vehicle latency, which saturated the
    # vehicle score for every overloaded stage and hid the allocation
    # gradient.  The environment now exports the real vehicle metric; CVaR
    # is handled separately as a network-wide tail-risk term below.
    vehicle_latency_ms = _safe_float(
        metrics.get("vehicle_latency_ms", metrics.get("cvar_per_ue_us", 0.0) / 1000.0),
        0.0,
    )
    vehicle_loss_pct = _safe_float(
        metrics.get("vehicle_packet_loss_percent", metrics.get("global_packet_loss_rate", 0.0) * 100.0),
        0.0,
    )
    vehicle_latency_score = (
        1.0
        if vehicle_latency_ms <= 10.0
        else 0.0
        if vehicle_latency_ms >= 20.0
        else 1.0 - ((vehicle_latency_ms - 10.0) / 10.0)
    )
    vehicle_loss_score = 1.0 - _clamp(vehicle_loss_pct / 1.0)
    vehicle_qos = (
        0.60 * _clamp(urllc.get("completion_ratio"))
        + 0.20 * vehicle_latency_score
        + 0.20 * vehicle_loss_score
    )
    # Vehicles receive the largest service weight; cameras remain second and
    # sensors remain protected rather than being ignored under contention.
    qos_score = (0.25 * embb_qos) + (0.20 * mmtc_qos) + (0.55 * vehicle_qos)

    usable = max(_safe_float(resource_action.get("usable_budget", 1.0), 1.0), 1e-9)
    allocations = resource_action.get("slice_allocation") or {}
    alloc_sum = sum(_safe_float(allocations.get(sid, 0.0), 0.0) for sid in SLICE_ORDER)
    demand_sum = sum(_safe_float(slice_state[sid].get("demand"), 0.0) for sid in SLICE_ORDER)
    utilization_ratio = alloc_sum / usable
    over_alloc_penalty = max(0.0, (alloc_sum - usable) / usable)
    shortage_penalty = max(0.0, (demand_sum - alloc_sum) / max(demand_sum, 1e-9))
    min_qos_penalty = sum(1.0 - _clamp(slice_state[sid].get("min_qos_met")) for sid in SLICE_ORDER) / len(SLICE_ORDER)
    # Full-budget actions are not automatically wrong, but allocation above
    # 80% is penalized progressively so the policy learns to release unused
    # capacity when service targets are already met.
    resource_use_penalty = _clamp((utilization_ratio - 0.80) / 0.20)
    vehicle_sla_penalty = 1.0 - vehicle_qos
    loss_penalty = min(0.20, _safe_float(metrics.get("global_packet_loss_rate", 0.0), 0.0) * 20.0)
    cvar_ms = max(0.0, _safe_float(metrics.get("cvar_per_ue_us", 0.0), 0.0) / 1000.0)
    cvar_reference_ms = max(0.001, _safe_float(metrics.get("cvar_reference_ms", cvar_ms), cvar_ms))
    cvar_target_ms = max(0.001, _safe_float(metrics.get("cvar_target_ms", 120.0), 120.0))
    relative_tail_excess = max(0.0, (cvar_ms / cvar_reference_ms) - 1.0)
    target_tail_excess = max(0.0, (cvar_ms - cvar_target_ms) / cvar_target_ms)
    # Relative excess detects regressions even when absolute CVaR is below a
    # legacy SLA threshold.  The absolute term prevents the policy from
    # accepting a persistently high tail merely because its reference is high.
    # CVaR is a hard safety objective for this campaign: relative tail
    # regressions dominate small energy/resource gains.
    tail_risk_penalty = min(2.0, (0.80 * relative_tail_excess) + (0.20 * target_tail_excess))
    # Completion targets are explicit campaign objectives. RAN is represented
    # by cameras/eMBB; AI is the demand-weighted mMTC+URLLC aggregate.
    ran_completion = _clamp(embb.get("completion_ratio"), 0.0, 1.0)
    ai_demand = _safe_float(slice_state["mMTC"].get("demand"), 0.0) + _safe_float(slice_state["URLLC"].get("demand"), 0.0)
    ai_completion = (
        (
            _safe_float(slice_state["mMTC"].get("completion_ratio"), 0.0) * _safe_float(slice_state["mMTC"].get("demand"), 0.0)
            + _safe_float(slice_state["URLLC"].get("completion_ratio"), 0.0) * _safe_float(slice_state["URLLC"].get("demand"), 0.0)
        ) / ai_demand
        if ai_demand > 1e-9
        else 1.0
    )
    ran_target = 0.95
    ai_target = 0.75
    ran_shortfall = max(0.0, ran_target - ran_completion) / ran_target
    ai_shortfall = max(0.0, ai_target - ai_completion) / ai_target
    completion_shortfall_penalty = min(1.0, (0.60 * ran_shortfall) + (0.40 * ai_shortfall))
    underallocation_penalty = min(1.0, max(shortage_penalty, completion_shortfall_penalty))
    excess_allocation_penalty = min(1.0, max(over_alloc_penalty, resource_use_penalty))
    blocked = str(
        resource_action.get("allocation_state", resource_action.get("state_category", ""))
        or metrics.get("allocation_state", "")
    ).upper() == "BLOCKED"
    sla_safe = (
        not blocked
        and ran_completion >= ran_target
        and ai_completion >= ai_target
        and vehicle_sla_penalty <= 0.20
        and cvar_ms <= cvar_target_ms
        and _safe_float(metrics.get("global_packet_loss_rate", 0.0), 0.0) <= 0.01
    )
    power_percent = _power_percent(resource_action)
    # Energy reduction is penalized only when service targets are met. In a
    # critical state the safety/service objective has priority over energy.
    power_cost_penalty = ((power_percent - 25.0) / 75.0) if sla_safe else 0.0
    # The campaign objective is a bounded error budget. Completion and
    # underallocation dominate the energy incentive, so a lower-power action
    # cannot look good while the services are starved.
    normalized_loss = _clamp(loss_penalty / 0.20)
    continuous_error = (
        (0.25 * _clamp(1.0 - qos_score))
        + (0.10 * _clamp(tail_risk_penalty))
        + (0.05 * normalized_loss)
        + (0.35 * completion_shortfall_penalty)
        + (0.15 * underallocation_penalty)
        + (0.05 * excess_allocation_penalty)
        + (0.05 * power_cost_penalty)
    )
    reward = 1.0 - (2.0 * _clamp(continuous_error))
    reward = max(-1.0, min(1.0, float(reward)))
    return float(reward), {
        "qos_score": float(qos_score),
        "embb_qos": float(embb_qos),
        "mmtc_qos": float(mmtc_qos),
        "vehicle_qos": float(vehicle_qos),
        "vehicle_latency_score": float(vehicle_latency_score),
        "vehicle_loss_score": float(vehicle_loss_score),
        "vehicle_sla_penalty": float(vehicle_sla_penalty),
        "over_alloc_penalty": float(over_alloc_penalty),
        "excess_allocation_penalty": float(excess_allocation_penalty),
        "shortage_penalty": float(shortage_penalty),
        "min_qos_penalty": float(min_qos_penalty),
        "resource_use_penalty": float(resource_use_penalty),
        "cvar_ms": float(cvar_ms),
        "cvar_reference_ms": float(cvar_reference_ms),
        "relative_tail_excess": float(relative_tail_excess),
        "tail_risk_penalty": float(tail_risk_penalty),
        "loss_penalty": float(loss_penalty),
        "ran_completion": float(ran_completion),
        "ai_completion": float(ai_completion),
        "ran_completion_target": float(ran_target),
        "ai_completion_target": float(ai_target),
        "completion_shortfall_penalty": float(completion_shortfall_penalty),
        "underallocation_penalty": float(underallocation_penalty),
        "power_percent": float(power_percent),
        "power_cost_penalty": float(power_cost_penalty),
        "energy_action": str(resource_action.get("energy_action", resource_action.get("tasam_energy_action", "")) or ""),
        "power_penalty_gated_by_service": float(1.0 if sla_safe else 0.0),
    }


def _split_ai_demands(ai_total: float, ai_components: dict[str, Any] | None) -> tuple[float, float]:
    components = ai_components or {}
    app2_pressure = _clamp(_safe_float(components.get("app2_pressure", 0.5), 0.5))
    vehicle_pressure = _clamp(_safe_float(components.get("vehicle_pressure", 0.5), 0.5))
    total = app2_pressure + vehicle_pressure
    if total <= 1e-12:
        return ai_total * 0.5, ai_total * 0.5
    return ai_total * (app2_pressure / total), ai_total * (vehicle_pressure / total)


@dataclass(frozen=True)
class StagePreset:
    name: str
    duration_steps: int
    camera: dict[str, Any]
    app2: dict[str, Any]
    vehicle: dict[str, Any]
    network: dict[str, Any]


HEALTHY_CAMERA = {
    "enabled": True,
    "active_cameras": 3,
    "observed_cameras": 3,
    "critical_cameras": 0,
    "throughput_ready": True,
    "throughput_mbps": 32.0,
    "avg_throughput_mbps": 34.0,
    "latency_ms": 18.0,
}
HEALTHY_APP2 = {
    "enabled": True,
    "total_sensors": 17,
    "connected_sensors": 17,
    "error_sensors": 0,
    "low_battery_sensors": 0,
    "packet_loss_percent": 3.2,
    "delivery_success_percent": 97.2,
    "avg_latency_ms": 185.0,
    "avg_rssi_dbm": -89.0,
    "avg_battery_percent": 76.0,
    "avg_power_mw": 182.0,
    "network_utilization_percent": 62.0,
}
HEALTHY_VEHICLE = {
    "enabled": True,
    "total_vehicles": 5,
    "high_risk_vehicles": 0,
    "medium_risk_vehicles": 0,
    "degraded_autonomy_vehicles": 0,
    "max_latency_ms": 18.0,
    "max_packet_loss_percent": 0.2,
    "ego_latency_ms": 18.0,
    "traffic_latency_ms": 12.0,
}
HEALTHY_NETWORK = {
    "cvar_us": 32000.0,
    "latest_cvar_us": 32000.0,
    "p95_us": 24000.0,
    "stability_score": 95.0,
    "variance_us2": 2.8e8,
}

STAGE_PROFILES = {
    "greenran_conflict_cycle": (
        StagePreset(
            name="baseline_healthy",
            duration_steps=10,
            camera=dict(HEALTHY_CAMERA),
            app2=dict(HEALTHY_APP2),
            vehicle=dict(HEALTHY_VEHICLE),
            network=dict(HEALTHY_NETWORK),
        ),
        StagePreset(
            name="camera_overload",
            duration_steps=20,
            camera={**HEALTHY_CAMERA, "critical_cameras": 2, "throughput_mbps": 18.0, "avg_throughput_mbps": 19.2, "latency_ms": 86.0},
            app2=dict(HEALTHY_APP2),
            vehicle=dict(HEALTHY_VEHICLE),
            network={"cvar_us": 95000.0, "latest_cvar_us": 95000.0, "p95_us": 72000.0, "stability_score": 68.0, "variance_us2": 1.1e9},
        ),
        StagePreset(
            name="mixed_overload",
            duration_steps=30,
            camera={**HEALTHY_CAMERA, "critical_cameras": 2, "throughput_mbps": 16.5, "avg_throughput_mbps": 17.8, "latency_ms": 92.0},
            app2={
                **HEALTHY_APP2,
                "connected_sensors": 15,
                "error_sensors": 2,
                "low_battery_sensors": 1,
                "packet_loss_percent": 6.2,
                "delivery_success_percent": 91.0,
                "avg_latency_ms": 510.0,
                "avg_rssi_dbm": -97.0,
                "avg_battery_percent": 64.0,
                "avg_power_mw": 228.0,
                "network_utilization_percent": 88.0,
            },
            vehicle={
                **HEALTHY_VEHICLE,
                "medium_risk_vehicles": 1,
                "degraded_autonomy_vehicles": 1,
                "max_latency_ms": 62.0,
                "max_packet_loss_percent": 3.4,
                "ego_latency_ms": 62.0,
                "traffic_latency_ms": 31.0,
            },
            network={"cvar_us": 118000.0, "latest_cvar_us": 118000.0, "p95_us": 78000.0, "stability_score": 54.0, "variance_us2": 1.8e9},
        ),
        StagePreset(
            name="background_overload",
            duration_steps=20,
            camera={**HEALTHY_CAMERA, "critical_cameras": 1, "throughput_mbps": 22.5, "avg_throughput_mbps": 23.4, "latency_ms": 73.0},
            app2={
                **HEALTHY_APP2,
                "connected_sensors": 16,
                "error_sensors": 1,
                "low_battery_sensors": 1,
                "packet_loss_percent": 5.4,
                "delivery_success_percent": 93.4,
                "avg_latency_ms": 430.0,
                "avg_rssi_dbm": -95.0,
                "avg_battery_percent": 66.0,
                "avg_power_mw": 220.0,
                "network_utilization_percent": 82.0,
            },
            vehicle=dict(HEALTHY_VEHICLE),
            network={"cvar_us": 76000.0, "latest_cvar_us": 76000.0, "p95_us": 58000.0, "stability_score": 73.0, "variance_us2": 9.0e8},
        ),
        StagePreset(
            name="recovery_window",
            duration_steps=15,
            camera={**HEALTHY_CAMERA, "throughput_mbps": 29.8, "avg_throughput_mbps": 31.0, "latency_ms": 34.0},
            app2=dict(HEALTHY_APP2),
            vehicle=dict(HEALTHY_VEHICLE),
            network={"cvar_us": 42000.0, "latest_cvar_us": 42000.0, "p95_us": 30000.0, "stability_score": 90.0, "variance_us2": 3.5e8},
        ),
        StagePreset(
            name="camera_overload_repeat",
            duration_steps=15,
            camera={**HEALTHY_CAMERA, "critical_cameras": 2, "throughput_mbps": 17.4, "avg_throughput_mbps": 18.6, "latency_ms": 95.0},
            app2=dict(HEALTHY_APP2),
            vehicle=dict(HEALTHY_VEHICLE),
            network={"cvar_us": 102000.0, "latest_cvar_us": 102000.0, "p95_us": 76000.0, "stability_score": 61.0, "variance_us2": 1.25e9},
        ),
    ),
}


DEFAULT_SHARED_RESOURCE_CONFIG = {
    "r_max": 1.0,
    "ran_min_share": 0.35,
    "ai_min_share": 0.15,
    "ran_priority_bias": 1.35,
    "min_utilization_ratio": 0.55,
    "allocation_smoothing": 0.0,
    "target_camera_capacity": 4.0,
    "target_sensor_capacity": 17.0,
    "target_vehicle_capacity": 5.0,
    "camera_throughput_target_mbps": 25.0,
    "camera_throughput_guard_mbps": 30.0,
    "camera_headroom_target_mbps": 35.0,
    "camera_latency_warning_ms": 80.0,
    "camera_latency_target_ms": 100.0,
    "cvar_target_ms": 120.0,
    "cvar_guard_relative_regression_pct": 5.0,
    "cvar_guard_required_windows": 2,
    "p95_target_ms": 80.0,
    "app2_latency_target_ms": 1000.0,
    "vehicle_latency_target_ms": 20.0,
    "vehicle_loss_target_pct": 1.0,
    "ran_min_active_demand": 0.15,
    "ai_min_active_demand": 0.15,
}


class OnlineGreenRANMARLEnv:
    """Current-scenario online MARL environment with article-style interfaces."""

    def __init__(self, *, max_steps: int = 200, seed: int = 42, stage_profile: str = "greenran_conflict_cycle") -> None:
        if stage_profile not in STAGE_PROFILES:
            raise ValueError(f"Unknown stage_profile={stage_profile!r}. Valid: {sorted(STAGE_PROFILES)}")
        self.max_steps = max(1, int(max_steps))
        self.rng = np.random.default_rng(int(seed))
        self.stage_profile_name = str(stage_profile)
        self.stage_profile = STAGE_PROFILES[self.stage_profile_name]
        self.topology = load_logical_du_topology()
        self.du_count = int(self.topology.get("logical_du_count", len(self.topology.get("logical_dus", []))) or 0)
        self.du_state_dim = 10
        self.global_state_dim = 10
        self.action_dim = len(SLICE_ORDER)
        self._step_count = 0
        self._stage_index = 0
        self._stage_elapsed = 0
        self._last_allocation = {"r_ran": 0.5, "r_ai": 0.5}
        self._current_payload: dict[str, Any] = {}
        self._cvar_reference_us = 0.0

    def reset(self) -> tuple[dict[str, Any], dict[str, Any]]:
        self._step_count = 0
        self._stage_index = 0
        self._stage_elapsed = 0
        self._last_allocation = {"r_ran": 0.5, "r_ai": 0.5}
        self._cvar_reference_us = 0.0
        self._current_payload = self._build_payload(self.stage_profile[self._stage_index], bootstrap=True)
        return self._current_payload, {"scenario_stage": self._current_payload["scenario_stage"]}

    def step(self, joint_action: np.ndarray | Sequence[Sequence[float]]) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        if not self._current_payload:
            raise RuntimeError("Environment must be reset before step()")
        action = np.asarray(joint_action, dtype=np.float32)
        if action.shape != (self.du_count, self.action_dim):
            raise ValueError(f"Expected action shape {(self.du_count, self.action_dim)}, got {tuple(action.shape)}")

        payload = self._current_payload
        weighted_action = self._aggregate_action(payload["du_states"], action)
        next_allocation = self._action_to_allocation(payload["base_resource_snapshot"], weighted_action)
        record = self._build_transition_record(payload, action, weighted_action, next_allocation)
        reward = float(record["reward"])

        self._step_count += 1
        self._last_allocation = {"r_ran": next_allocation["r_ran"], "r_ai": next_allocation["r_ai"]}
        terminated = self._step_count >= self.max_steps
        next_stage = self._advance_stage()
        self._current_payload = self._build_payload(next_stage, bootstrap=False)
        info = {
            "scenario_stage": payload["scenario_stage"],
            "reward_components": dict(record["reward_components"]),
            "weighted_action": [round(value, 6) for value in weighted_action],
            "resource_allocation": {
                "usable_budget": next_allocation["usable_budget"],
                "r_ran": next_allocation["r_ran"],
                "r_ai": next_allocation["r_ai"],
                "slice_allocation": dict(next_allocation["slice_allocation"]),
            },
        }
        return self._current_payload, reward, terminated, False, info

    def _advance_stage(self) -> StagePreset:
        self._stage_elapsed += 1
        current = self.stage_profile[self._stage_index]
        if self._stage_elapsed >= max(1, current.duration_steps):
            self._stage_index = (self._stage_index + 1) % len(self.stage_profile)
            self._stage_elapsed = 0
        return self.stage_profile[self._stage_index]

    def _aggregate_action(self, du_states: list[dict[str, Any]], action: np.ndarray) -> list[float]:
        weights = [max(_safe_float((du or {}).get("demand_share", 0.0), 0.0), 1e-6) for du in du_states]
        total_weight = sum(weights)
        if total_weight <= 1e-12:
            weights = [1.0] * len(du_states)
            total_weight = float(len(du_states))
        mean = np.zeros(self.action_dim, dtype=np.float64)
        for idx, vector in enumerate(action):
            normalized = np.asarray(_normalize(vector.tolist()), dtype=np.float64)
            mean += normalized * (weights[idx] / total_weight)
        return _normalize(mean.tolist())

    def _action_to_allocation(self, base_snapshot: dict[str, Any], weighted_action: Sequence[float]) -> dict[str, Any]:
        usable_budget = _safe_float(base_snapshot.get("usable_budget", 1.0), 1.0)
        embb_share, mmtc_share, urllc_share = _normalize(weighted_action)
        slice_allocation = {
            "eMBB": usable_budget * embb_share,
            "mMTC": usable_budget * mmtc_share,
            "URLLC": usable_budget * urllc_share,
        }
        return {
            "usable_budget": usable_budget,
            "d_ran": _safe_float(base_snapshot.get("d_ran", 0.0), 0.0),
            "d_ai": _safe_float(base_snapshot.get("d_ai", 0.0), 0.0),
            "ai_components": dict(base_snapshot.get("ai_components") or {}),
            "r_ran": slice_allocation["eMBB"],
            "r_ai": slice_allocation["mMTC"] + slice_allocation["URLLC"],
            "slice_allocation": slice_allocation,
        }

    def _build_payload(self, stage: StagePreset, *, bootstrap: bool) -> dict[str, Any]:
        camera = dict(stage.camera)
        app2 = dict(stage.app2)
        vehicle = dict(stage.vehicle)
        network = dict(stage.network)
        base_snapshot = compute_shared_resource_snapshot(
            camera_metrics=camera,
            app2_metrics=app2,
            vehicle_metrics=vehicle,
            network_health=network,
            shared_resource_config=DEFAULT_SHARED_RESOURCE_CONFIG,
            previous_allocation=(None if bootstrap else self._last_allocation),
        )
        heuristic_slice = self._build_slice_state(
            camera_metrics=camera,
            app2_metrics=app2,
            vehicle_metrics=vehicle,
            network_health=network,
            base_snapshot=base_snapshot,
            slice_allocation={
                "eMBB": _safe_float(base_snapshot.get("r_ran", 0.0), 0.0),
                **self._split_ai_allocation(base_snapshot),
            },
        )
        du_states = self._build_du_states(heuristic_slice, _safe_float(base_snapshot.get("usable_budget", 1.0), 1.0))
        global_state = self._build_global_state(heuristic_slice, base_snapshot)
        return {
            "scenario_stage": stage.name,
            "camera_metrics": camera,
            "app2_metrics": app2,
            "vehicle_metrics": vehicle,
            "network_health": network,
            "base_resource_snapshot": base_snapshot,
            "slice_state": heuristic_slice,
            "du_states": du_states,
            "global_state": global_state,
        }

    def _split_ai_allocation(self, snapshot: dict[str, Any]) -> dict[str, float]:
        mmtc_alloc, urllc_alloc = _split_ai_demands(_safe_float(snapshot.get("r_ai", 0.0), 0.0), snapshot.get("ai_components"))
        return {"mMTC": mmtc_alloc, "URLLC": urllc_alloc}

    def _build_slice_state(
        self,
        *,
        camera_metrics: dict[str, Any],
        app2_metrics: dict[str, Any],
        vehicle_metrics: dict[str, Any],
        network_health: dict[str, Any],
        base_snapshot: dict[str, Any],
        slice_allocation: dict[str, float],
    ) -> dict[str, dict[str, Any]]:
        d_ran = _safe_float(base_snapshot.get("d_ran", 0.0), 0.0)
        d_ai = _safe_float(base_snapshot.get("d_ai", 0.0), 0.0)
        usable_budget = _safe_float(base_snapshot.get("usable_budget", 1.0), 1.0)
        mmtc_demand, urllc_demand = _split_ai_demands(d_ai, base_snapshot.get("ai_components"))

        embb_throughput = _safe_float(camera_metrics.get("throughput_mbps", 0.0), 0.0)
        embb_latency = _safe_float(camera_metrics.get("latency_ms", 0.0), 0.0)
        embb_pressure = max(
            _clamp((25.0 - embb_throughput) / 25.0),
            _clamp((embb_latency - 40.0) / 60.0),
            _clamp(_safe_float(network_health.get("p95_us", 0.0), 0.0) / 1000.0 / 120.0),
        )

        connected_ratio = _clamp(_safe_float(app2_metrics.get("connected_sensors", 0), 0.0) / max(_safe_float(app2_metrics.get("total_sensors", 1), 1.0), 1.0))
        delivery_success = _clamp(_safe_float(app2_metrics.get("delivery_success_percent", 100.0), 100.0) / 100.0)
        mmtc_latency_ms = _safe_float(app2_metrics.get("avg_latency_ms", 0.0), 0.0)
        mmtc_pressure = max(
            _clamp((0.95 - connected_ratio) / 0.35),
            _clamp((0.95 - delivery_success) / 0.25),
            _clamp(mmtc_latency_ms / 1000.0),
        )

        vehicle_latency_ms = _safe_float(vehicle_metrics.get("max_latency_ms", 0.0), 0.0)
        vehicle_loss_pct = _safe_float(vehicle_metrics.get("max_packet_loss_percent", 0.0), 0.0)
        high_risk = _safe_float(vehicle_metrics.get("high_risk_vehicles", 0.0), 0.0)
        total_vehicles = max(_safe_float(vehicle_metrics.get("total_vehicles", 1.0), 1.0), 1.0)
        urllc_pressure = max(
            _clamp(vehicle_latency_ms / 120.0),
            _clamp(vehicle_loss_pct / 10.0),
            _clamp(high_risk / total_vehicles),
        )

        embb_alloc = _safe_float(slice_allocation.get("eMBB", 0.0), 0.0)
        mmtc_alloc = _safe_float(slice_allocation.get("mMTC", 0.0), 0.0)
        urllc_alloc = _safe_float(slice_allocation.get("URLLC", 0.0), 0.0)

        return {
            "eMBB": {
                "slice_id": "eMBB",
                "ue_count": _safe_int(camera_metrics.get("active_cameras", 0), 0) + 9,
                "demand": d_ran,
                "allocation": embb_alloc,
                "qos_pressure": embb_pressure,
                "completion_ratio": 1.0 if d_ran <= 1e-9 else _clamp(embb_alloc / d_ran),
                "min_qos_met": 1.0 if embb_pressure < 0.35 else 0.0,
                "budget_share": _clamp(embb_alloc / max(usable_budget, 1e-9)),
            },
            "mMTC": {
                "slice_id": "mMTC",
                "ue_count": _safe_int(app2_metrics.get("total_sensors", 0), 0),
                "demand": mmtc_demand,
                "allocation": mmtc_alloc,
                "qos_pressure": mmtc_pressure,
                "completion_ratio": 1.0 if mmtc_demand <= 1e-9 else _clamp(mmtc_alloc / mmtc_demand),
                "min_qos_met": 1.0 if mmtc_pressure < 0.35 else 0.0,
                "budget_share": _clamp(mmtc_alloc / max(usable_budget, 1e-9)),
            },
            "URLLC": {
                "slice_id": "URLLC",
                "ue_count": _safe_int(vehicle_metrics.get("total_vehicles", 0), 0),
                "demand": urllc_demand,
                "allocation": urllc_alloc,
                "qos_pressure": urllc_pressure,
                "completion_ratio": 1.0 if urllc_demand <= 1e-9 else _clamp(urllc_alloc / urllc_demand),
                "min_qos_met": 1.0 if urllc_pressure < 0.35 else 0.0,
                "budget_share": _clamp(urllc_alloc / max(usable_budget, 1e-9)),
            },
        }

    def _build_du_states(self, slice_state: dict[str, dict[str, Any]], usable_budget: float) -> list[dict[str, Any]]:
        total_users = 17 + 5 + 12
        du_states: list[dict[str, Any]] = []
        for du in self.topology.get("logical_dus", []):
            mix = dict((du or {}).get("slice_mix") or {})
            mix_values = _normalize([mix.get("eMBB", 0.0), mix.get("mMTC", 0.0), mix.get("URLLC", 0.0)])
            embb_weight, mmtc_weight, urllc_weight = mix_values
            ue_count = int(
                len((du or {}).get("camera_imsis", []) or [])
                + len((du or {}).get("background_imsis", []) or [])
                + max(0, int(((du or {}).get("vehicle_imsi_range") or [0, -1])[1]) - int(((du or {}).get("vehicle_imsi_range") or [0, -1])[0]) + 1)
            )
            demand_share = (
                embb_weight * _safe_float(slice_state["eMBB"]["demand"])
                + mmtc_weight * _safe_float(slice_state["mMTC"]["demand"])
                + urllc_weight * _safe_float(slice_state["URLLC"]["demand"])
            )
            allocation_share = (
                embb_weight * _safe_float(slice_state["eMBB"]["allocation"])
                + mmtc_weight * _safe_float(slice_state["mMTC"]["allocation"])
                + urllc_weight * _safe_float(slice_state["URLLC"]["allocation"])
            )
            state_vector = [
                _safe_float(slice_state["eMBB"]["qos_pressure"]),
                _safe_float(slice_state["mMTC"]["qos_pressure"]),
                _safe_float(slice_state["URLLC"]["qos_pressure"]),
                _clamp(ue_count / max(total_users, 1)),
                embb_weight,
                mmtc_weight,
                urllc_weight,
                _clamp(demand_share / max(usable_budget, 1e-9)) if usable_budget > 1e-9 else 0.0,
                _clamp(allocation_share / max(usable_budget, 1e-9)) if usable_budget > 1e-9 else 0.0,
                _clamp(allocation_share / max(demand_share, 1e-9)) if demand_share > 1e-9 else 1.0,
            ]
            du_states.append(
                {
                    "du_id": str((du or {}).get("du_id", "unknown") or "unknown"),
                    "role": str((du or {}).get("role", "unknown") or "unknown"),
                    "primary_slice": str((du or {}).get("primary_slice", "eMBB") or "eMBB"),
                    "ue_count": ue_count,
                    "slice_mix": {"eMBB": embb_weight, "mMTC": mmtc_weight, "URLLC": urllc_weight},
                    "demand_share": demand_share,
                    "allocation_share": allocation_share,
                    "state_vector": state_vector,
                }
            )
        return du_states

    def _build_global_state(self, slice_state: dict[str, dict[str, Any]], base_snapshot: dict[str, Any]) -> dict[str, Any]:
        usable_budget = _safe_float(base_snapshot.get("usable_budget", 1.0), 1.0)
        total_demand = sum(_safe_float(slice_state[sid]["demand"]) for sid in SLICE_ORDER)
        total_alloc = sum(_safe_float(slice_state[sid]["allocation"]) for sid in SLICE_ORDER)
        return {
            "topology_id": self.topology.get("topology_id", "greenran_fixed_marl_v1"),
            "logical_du_count": len(self.topology.get("logical_dus", [])),
            "total_demand": total_demand,
            "usable_budget": usable_budget,
            "state_vector": [
                _safe_float(slice_state["eMBB"]["demand"]),
                _safe_float(slice_state["mMTC"]["demand"]),
                _safe_float(slice_state["URLLC"]["demand"]),
                _safe_float(slice_state["eMBB"]["allocation"]),
                _safe_float(slice_state["mMTC"]["allocation"]),
                _safe_float(slice_state["URLLC"]["allocation"]),
                _safe_float(slice_state["eMBB"]["completion_ratio"]),
                _safe_float(slice_state["mMTC"]["completion_ratio"]),
                _safe_float(slice_state["URLLC"]["completion_ratio"]),
                _clamp(total_alloc / max(usable_budget, 1e-9)) if usable_budget > 1e-9 else 0.0,
            ],
        }

    def _build_metrics(
        self,
        *,
        camera_metrics: dict[str, Any],
        app2_metrics: dict[str, Any],
        vehicle_metrics: dict[str, Any],
        network_health: dict[str, Any],
        slice_state: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        embb_completion = _safe_float(slice_state["eMBB"]["completion_ratio"], 0.0)
        mmtc_completion = _safe_float(slice_state["mMTC"]["completion_ratio"], 0.0)
        urllc_completion = _safe_float(slice_state["URLLC"]["completion_ratio"], 0.0)
        latency_scale = 1.0 + max(0.0, 1.0 - urllc_completion)
        vehicle_loss = _safe_float(vehicle_metrics.get("max_packet_loss_percent", 0.0), 0.0) / 100.0
        sensor_loss = _safe_float(app2_metrics.get("packet_loss_percent", 0.0), 0.0) / 100.0
        cvar_us = _safe_float(network_health.get("cvar_us", 0.0), 0.0)
        # ``baseline_cvar_us`` is a historical reporting baseline (often the
        # old 484.7 ms pre-fix run), not a live reward reference.  Only an
        # explicitly supplied live reference may override the local EWMA.
        explicit_reference_us = _safe_float(network_health.get("reference_cvar_us", 0.0), 0.0)
        cvar_reference_us = explicit_reference_us or self._cvar_reference_us or cvar_us
        return {
            "throughput_kbps": _safe_float(camera_metrics.get("throughput_mbps", 0.0), 0.0) * 1000.0 * embb_completion,
            "cvar_per_ue_us": cvar_us * latency_scale,
            "cvar_observed_us": cvar_us,
            "cvar_reference_ms": cvar_reference_us / 1000.0,
            "cvar_target_ms": _safe_float(DEFAULT_SHARED_RESOURCE_CONFIG.get("cvar_target_ms"), 120.0),
            "vehicle_latency_ms": _safe_float(vehicle_metrics.get("max_latency_ms", 0.0), 0.0),
            "vehicle_packet_loss_percent": _safe_float(vehicle_metrics.get("max_packet_loss_percent", 0.0), 0.0),
            "global_packet_loss_rate": min(0.25, (0.55 * vehicle_loss * (1.0 - urllc_completion)) + (0.45 * sensor_loss * (1.0 - mmtc_completion))),
            "total_active_ues": sum(_safe_int(slice_state[sid]["ue_count"], 0) for sid in SLICE_ORDER),
            "total_active_cameras": _safe_int(camera_metrics.get("active_cameras", 0), 0),
        }

    def _build_reward(self, slice_state: dict[str, dict[str, Any]], metrics: dict[str, Any], resource_action: dict[str, Any]) -> tuple[float, dict[str, float]]:
        return build_balanced_vehicle_energy_reward(slice_state, metrics, resource_action)

    def _build_transition_record(
        self,
        payload: dict[str, Any],
        joint_action: np.ndarray,
        weighted_action: Sequence[float],
        next_allocation: dict[str, Any],
    ) -> dict[str, Any]:
        slice_state = self._build_slice_state(
            camera_metrics=payload["camera_metrics"],
            app2_metrics=payload["app2_metrics"],
            vehicle_metrics=payload["vehicle_metrics"],
            network_health=payload["network_health"],
            base_snapshot=payload["base_resource_snapshot"],
            slice_allocation=next_allocation["slice_allocation"],
        )
        du_states = self._build_du_states(slice_state, next_allocation["usable_budget"])
        global_state = self._build_global_state(slice_state, next_allocation)
        metrics = self._build_metrics(
            camera_metrics=payload["camera_metrics"],
            app2_metrics=payload["app2_metrics"],
            vehicle_metrics=payload["vehicle_metrics"],
            network_health=payload["network_health"],
            slice_state=slice_state,
        )
        reward, reward_components = self._build_reward(slice_state, metrics, next_allocation)
        # Keep a slowly moving pre-action reference.  This makes the reward
        # sensitive to regressions between stages while avoiding a noisy
        # one-sample baseline.
        current_cvar_us = _safe_float(metrics.get("cvar_observed_us", 0.0), 0.0)
        if current_cvar_us > 0.0 and not self._cvar_reference_us:
            self._cvar_reference_us = current_cvar_us
        elif current_cvar_us > 0.0:
            self._cvar_reference_us = (0.90 * self._cvar_reference_us) + (0.10 * current_cvar_us)
        return {
            "scenario_stage": payload["scenario_stage"],
            "global_state": global_state,
            "du_states": du_states,
            "slice_state": slice_state,
            "reward": reward,
            "reward_components": reward_components,
            "metrics": metrics,
            "joint_action": joint_action.tolist(),
            "weighted_action": list(weighted_action),
            "resource_allocation": next_allocation,
        }
