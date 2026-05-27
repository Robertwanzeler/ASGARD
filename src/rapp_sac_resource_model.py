#!/usr/bin/env python3
"""
CAORA-style demand and allocation model for the GreenRAN runtime.

This module materializes the article-aligned variables from [1]:
  - d_ran(t)
  - d_ai(t)
  - r_ran(t)
  - r_ai(t)

[1] Lotfi, F., Rajoli, H. & Afghah, F. "Task-Specific Sharpness-Aware O-RAN
    Resource Management using Multi-Agent Reinforcement Learning".
    IEEE TMLCN, 2025. arXiv:2511.15002.

Until a trained SAC agent is available online, the runtime uses a deterministic
bootstrap allocator that preserves RAN priority while exporting real workload
traces for future SAC training.
"""

from __future__ import annotations

from typing import Any, Dict


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = lower
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


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


def estimate_ran_demand(
    camera_metrics: Dict[str, Any] | None,
    network_health: Dict[str, Any] | None,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    camera_metrics = camera_metrics or {}
    network_health = network_health or {}

    target_cameras = max(1.0, _safe_float(config.get("target_camera_capacity", 4.0), 4.0))
    active_cameras = max(0.0, _safe_float(camera_metrics.get("active_cameras", 0), 0.0))
    throughput_mbps = _safe_float(camera_metrics.get("throughput_mbps", 0.0), 0.0)
    latency_ms = _safe_float(camera_metrics.get("latency_ms", 0.0), 0.0)
    observed_cameras = max(0.0, _safe_float(camera_metrics.get("observed_cameras", active_cameras), active_cameras))
    critical_cameras = max(0.0, _safe_float(camera_metrics.get("critical_cameras", 0.0), 0.0))
    throughput_ready = bool(camera_metrics.get("throughput_ready", False))
    cvar_ms = _safe_float(network_health.get("cvar_us", 0.0), 0.0) / 1000.0
    p95_ms = _safe_float(network_health.get("p95_us", 0.0), 0.0) / 1000.0

    camera_activity = _clamp(active_cameras / target_cameras)
    camera_observability = _clamp(observed_cameras / max(active_cameras, 1.0)) if active_cameras > 0 else 0.0
    critical_ratio = _clamp(critical_cameras / max(active_cameras, 1.0)) if active_cameras > 0 else 0.0
    throughput_target = max(1.0, _safe_float(config.get("camera_throughput_target_mbps", 25.0), 25.0))
    throughput_pressure = _clamp((throughput_target - throughput_mbps) / throughput_target)
    headroom_target = max(1.0, _safe_float(config.get("camera_headroom_target_mbps", 35.0), 35.0))
    throughput_headroom_pressure = _clamp((headroom_target - throughput_mbps) / headroom_target)
    latency_pressure = _clamp((latency_ms - 20.0) / 60.0)
    latency_guard_pressure = _clamp((latency_ms - 40.0) / 40.0)
    severe_throughput_pressure = _clamp(((throughput_target * 1.15) - throughput_mbps) / max(throughput_target * 1.15, 1.0))
    cvar_pressure = _clamp(cvar_ms / max(1.0, _safe_float(config.get("cvar_target_ms", 120.0), 120.0)))
    p95_pressure = _clamp(p95_ms / max(1.0, _safe_float(config.get("p95_target_ms", 80.0), 80.0)))
    warmup_pressure = 1.0 if active_cameras > 0 and not throughput_ready else 0.0
    throughput_warning = 1.0 if active_cameras > 0 and throughput_ready and throughput_mbps < 30.0 else 0.0
    throughput_violation = 1.0 if active_cameras > 0 and throughput_ready and throughput_mbps < throughput_target else 0.0
    latency_warning = 1.0 if active_cameras > 0 and latency_ms >= 60.0 else 0.0
    latency_violation = 1.0 if active_cameras > 0 and latency_ms >= 80.0 else 0.0

    demand = _clamp(
        (0.18 * camera_activity)
        + (0.16 * throughput_pressure)
        + (0.10 * throughput_headroom_pressure)
        + (0.12 * latency_pressure)
        + (0.10 * cvar_pressure)
        + (0.06 * p95_pressure)
        + (0.06 * warmup_pressure)
        + (0.08 * critical_ratio)
        + (0.06 * severe_throughput_pressure)
        + (0.04 * latency_guard_pressure)
        + (0.02 * throughput_warning)
        + (0.02 * latency_warning)
    )
    if active_cameras > 0:
        ran_floor = _clamp(
            _safe_float(config.get("ran_min_active_demand", 0.15), 0.15)
            + (0.10 * camera_activity)
            + (0.05 * (1.0 - camera_observability)),
            0.0,
            1.0,
        )
        if throughput_warning or latency_warning:
            ran_floor = max(
                ran_floor,
                _clamp(
                    0.42
                    + (0.16 * max(throughput_pressure, severe_throughput_pressure))
                    + (0.12 * max(latency_pressure, latency_guard_pressure))
                    + (0.08 * critical_ratio),
                    0.0,
                    1.0,
                ),
            )
        if throughput_violation or latency_violation or critical_ratio > 0.0:
            ran_floor = max(
                ran_floor,
                _clamp(
                    0.68
                    + (0.14 * max(throughput_pressure, severe_throughput_pressure))
                    + (0.10 * max(latency_pressure, latency_guard_pressure))
                    + (0.10 * critical_ratio),
                    0.0,
                    1.0,
                ),
            )
        demand = max(demand, ran_floor)

    return {
        "demand": demand,
        "components": {
            "camera_activity": camera_activity,
            "camera_observability": camera_observability,
            "critical_ratio": critical_ratio,
            "throughput_pressure": throughput_pressure,
            "throughput_headroom_pressure": throughput_headroom_pressure,
            "latency_pressure": latency_pressure,
            "latency_guard_pressure": latency_guard_pressure,
            "severe_throughput_pressure": severe_throughput_pressure,
            "cvar_pressure": cvar_pressure,
            "p95_pressure": p95_pressure,
            "warmup_pressure": warmup_pressure,
            "throughput_warning": throughput_warning,
            "throughput_violation": throughput_violation,
            "latency_warning": latency_warning,
            "latency_violation": latency_violation,
        },
    }


def estimate_ai_demand(
    app2_metrics: Dict[str, Any] | None,
    vehicle_metrics: Dict[str, Any] | None,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    app2_metrics = app2_metrics or {}
    vehicle_metrics = vehicle_metrics or {}

    total_sensors = max(0.0, _safe_float(app2_metrics.get("total_sensors", 0), 0.0))
    connected_ratio = _clamp(_safe_float(app2_metrics.get("connected_ratio", 1.0), 1.0))
    delivery_success = _clamp(_safe_float(app2_metrics.get("delivery_success_percent", 100.0), 100.0) / 100.0)
    app2_latency_ms = _safe_float(app2_metrics.get("avg_latency_ms", 0.0), 0.0)
    app2_stale = 1.0 if app2_metrics.get("stale") else 0.0

    target_sensors = max(1.0, _safe_float(config.get("target_sensor_capacity", 12.0), 12.0))
    sensor_activity = _clamp(total_sensors / target_sensors)
    sensor_connectivity_pressure = _clamp((0.95 - connected_ratio) / 0.35)
    delivery_pressure = _clamp((0.95 - delivery_success) / 0.25)
    sensor_latency_pressure = _clamp(app2_latency_ms / max(1.0, _safe_float(config.get("app2_latency_target_ms", 1000.0), 1000.0)))
    app2_pressure = _clamp(
        (0.30 * sensor_activity)
        + (0.25 * sensor_connectivity_pressure)
        + (0.20 * delivery_pressure)
        + (0.15 * sensor_latency_pressure)
        + (0.10 * app2_stale)
    )

    total_vehicles = max(0.0, _safe_float(vehicle_metrics.get("total_vehicles", 0), 0.0))
    high_risk = max(0.0, _safe_float(vehicle_metrics.get("high_risk_vehicles", 0), 0.0))
    medium_risk = max(0.0, _safe_float(vehicle_metrics.get("medium_risk_vehicles", 0), 0.0))
    degraded = max(0.0, _safe_float(vehicle_metrics.get("degraded_autonomy_vehicles", 0), 0.0))
    vehicle_latency_ms = _safe_float(vehicle_metrics.get("max_latency_ms", 0.0), 0.0)
    vehicle_loss_pct = _safe_float(vehicle_metrics.get("max_packet_loss_percent", 0.0), 0.0)

    target_vehicles = max(1.0, _safe_float(config.get("target_vehicle_capacity", 5.0), 5.0))
    vehicle_activity = _clamp(total_vehicles / target_vehicles)
    vehicle_risk_pressure = _clamp(((2.0 * high_risk) + medium_risk + (1.5 * degraded)) / (2.0 * target_vehicles))
    vehicle_latency_pressure = _clamp(vehicle_latency_ms / max(1.0, _safe_float(config.get("vehicle_latency_target_ms", 120.0), 120.0)))
    vehicle_loss_pressure = _clamp(vehicle_loss_pct / max(1.0, _safe_float(config.get("vehicle_loss_target_pct", 10.0), 10.0)))
    vehicle_pressure = _clamp(
        (0.25 * vehicle_activity)
        + (0.35 * vehicle_risk_pressure)
        + (0.20 * vehicle_latency_pressure)
        + (0.20 * vehicle_loss_pressure)
    )

    demand = _clamp((0.55 * app2_pressure) + (0.45 * vehicle_pressure))
    if total_sensors > 0 or total_vehicles > 0:
        demand = max(demand, _clamp(_safe_float(config.get("ai_min_active_demand", 0.15), 0.15)))

    return {
        "demand": demand,
        "components": {
            "app2_pressure": app2_pressure,
            "vehicle_pressure": vehicle_pressure,
            "sensor_activity": sensor_activity,
            "vehicle_activity": vehicle_activity,
        },
    }


def compute_shared_resource_snapshot(
    camera_metrics: Dict[str, Any] | None,
    app2_metrics: Dict[str, Any] | None,
    vehicle_metrics: Dict[str, Any] | None,
    network_health: Dict[str, Any] | None,
    shared_resource_config: Dict[str, Any],
    previous_allocation: Dict[str, float] | None = None,
) -> Dict[str, Any]:
    cfg = shared_resource_config or {}
    ran = estimate_ran_demand(camera_metrics, network_health, cfg)
    ai = estimate_ai_demand(app2_metrics, vehicle_metrics, cfg)
    d_ran = ran["demand"]
    d_ai = ai["demand"]
    r_max = max(0.1, _safe_float(cfg.get("r_max", 1.0), 1.0))
    ran_min_share = _clamp(cfg.get("ran_min_share", 0.35), 0.0, 1.0)
    ai_min_share = _clamp(cfg.get("ai_min_share", 0.15), 0.0, 1.0)
    ran_priority_bias = max(1.0, _safe_float(cfg.get("ran_priority_bias", 1.35), 1.35))
    min_utilization = _clamp(cfg.get("min_utilization_ratio", 0.55), 0.0, 1.0)
    total_pressure = _clamp((d_ran + d_ai) / 1.35, 0.0, 1.0)
    usable_budget = r_max * _clamp(min_utilization + total_pressure * (1.0 - min_utilization))

    base_ran = usable_budget * ran_min_share if d_ran > 0 else 0.0
    base_ai = usable_budget * ai_min_share if d_ai > 0 else 0.0
    remaining = max(0.0, usable_budget - base_ran - base_ai)

    weighted_ran = d_ran * ran_priority_bias
    weighted_ai = d_ai
    weight_sum = weighted_ran + weighted_ai
    if weight_sum > 0:
        extra_ran = remaining * (weighted_ran / weight_sum)
        extra_ai = remaining * (weighted_ai / weight_sum)
    else:
        extra_ran = remaining * 0.5
        extra_ai = remaining * 0.5

    r_ran = base_ran + extra_ran
    r_ai = base_ai + extra_ai

    # Smooth allocation to avoid large jumps in successive cycles.
    prev = previous_allocation or {}
    prev_ran = _safe_float(prev.get("r_ran", r_ran), r_ran)
    prev_ai = _safe_float(prev.get("r_ai", r_ai), r_ai)
    smoothing = _clamp(cfg.get("allocation_smoothing", 0.35), 0.0, 1.0)
    r_ran = ((1.0 - smoothing) * r_ran) + (smoothing * prev_ran)
    r_ai = ((1.0 - smoothing) * r_ai) + (smoothing * prev_ai)
    total_alloc = r_ran + r_ai
    if total_alloc > usable_budget and total_alloc > 0:
        scale = usable_budget / total_alloc
        r_ran *= scale
        r_ai *= scale

    ran_completion = 1.0 if d_ran <= 1e-9 else _clamp(r_ran / d_ran)
    ai_completion = 1.0 if d_ai <= 1e-9 else _clamp(r_ai / d_ai)
    utilization = _clamp((r_ran + r_ai) / r_max)

    return {
        "controller_id": "caora_bootstrap_heuristic",
        "target_policy_id": "caora_sac_resource_allocation",
        "decision_domain": "resource_allocation",
        "action_semantics": "resource_share_delta",
        "resource_budget": r_max,
        "usable_budget": usable_budget,
        "d_ran": d_ran,
        "d_ai": d_ai,
        "r_ran": r_ran,
        "r_ai": r_ai,
        "delta_r_ran": r_ran - prev_ran,
        "delta_r_ai": r_ai - prev_ai,
        "ran_completion_ratio": ran_completion,
        "ai_completion_ratio": ai_completion,
        "utilization_ratio": utilization,
        "ran_components": ran["components"],
        "ai_components": ai["components"],
    }
