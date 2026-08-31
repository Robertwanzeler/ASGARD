#!/usr/bin/env python3
"""
TA-SAM-aligned demand and allocation model for the GreenRAN runtime.

This module materializes the article-aligned variables from [1]:
  - d_ran(t)
  - d_ai(t)
  - r_ran(t)
  - r_ai(t)

[1] Lotfi, F., Rajoli, H. & Afghah, F. "Task-Specific Sharpness-Aware O-RAN
    Resource Management using Multi-Agent Reinforcement Learning".
    IEEE TMLCN, 2025. arXiv:2511.15002.

The runtime keeps the current GreenRAN scenario and uses a deterministic
bootstrap allocator as the live policy while exporting TA-SAM-aligned state for
shadow evaluation.
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from .greenran_marl_topology import build_du_state_snapshot
except ImportError:
    from greenran_marl_topology import build_du_state_snapshot


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


def _normalise_allocation_state(value: Any) -> str:
    state = str(value or "ALLOWED").strip().upper()
    return state if state in {"ALLOWED", "CONDITIONAL", "CRITICAL", "BLOCKED"} else "ALLOWED"


def infer_allocation_state(
    camera_metrics: Dict[str, Any] | None,
    app2_metrics: Dict[str, Any] | None,
    vehicle_metrics: Dict[str, Any] | None,
    network_health: Dict[str, Any] | None,
    config: Dict[str, Any],
) -> str:
    """Infer the resource state when the rApp has not classified it yet."""
    camera = camera_metrics or {}
    app2 = app2_metrics or {}
    vehicle = vehicle_metrics or {}
    health = network_health or {}
    camera_active = _safe_float(camera.get("active_cameras"), 0.0) > 0.0
    camera_hard = camera_active and (
        not bool(camera.get("throughput_ready", True))
        or _safe_float(camera.get("throughput_mbps"), 0.0)
        < _safe_float(config.get("camera_throughput_target_mbps", 25.0), 25.0)
        or _safe_float(camera.get("latency_ms"), 0.0)
        >= _safe_float(config.get("camera_latency_target_ms", 100.0), 100.0)
    )
    camera_warning = camera_active and (
        _safe_float(camera.get("throughput_mbps"), 0.0)
        < _safe_float(config.get("camera_throughput_guard_mbps", 30.0), 30.0)
        or _safe_float(camera.get("latency_ms"), 0.0)
        >= _safe_float(config.get("camera_latency_warning_ms", 80.0), 80.0)
    )
    vehicle_hard = (
        _safe_float(vehicle.get("max_latency_ms"), 0.0)
        >= _safe_float(config.get("vehicle_latency_target_ms", 20.0), 20.0)
        or _safe_float(vehicle.get("max_packet_loss_percent"), 0.0)
        >= _safe_float(config.get("vehicle_loss_target_pct", 1.0), 1.0)
        or _safe_float(vehicle.get("high_risk_vehicles"), 0.0) > 0.0
        or _safe_float(vehicle.get("degraded_autonomy_vehicles"), 0.0) > 0.0
    )
    vehicle_warning = (
        _safe_float(vehicle.get("max_latency_ms"), 0.0)
        >= _safe_float(config.get("vehicle_latency_warning_ms", 10.0), 10.0)
        or _safe_float(vehicle.get("max_packet_loss_percent"), 0.0)
        >= _safe_float(config.get("vehicle_loss_warning_pct", 0.5), 0.5)
    )
    cvar_ms = _safe_float(health.get("cvar_us"), 0.0) / 1000.0
    p95_ms = _safe_float(health.get("p95_us"), 0.0) / 1000.0
    cvar_hard = cvar_ms >= _safe_float(config.get("resource_cvar_critical_ms", 250.0), 250.0)
    p95_hard = p95_ms >= _safe_float(config.get("resource_p95_critical_ms", 120.0), 120.0)
    cvar_warning = cvar_ms >= _safe_float(config.get("cvar_target_ms", 120.0), 120.0)
    p95_warning = p95_ms >= _safe_float(config.get("p95_target_ms", 80.0), 80.0)
    if camera_hard or vehicle_hard or cvar_hard or p95_hard:
        return "BLOCKED"
    if camera_warning or vehicle_warning or cvar_warning or p95_warning:
        return "CONDITIONAL"
    return "ALLOWED"


def _build_per_ue_floor(
    camera_metrics: Dict[str, Any],
    app2_metrics: Dict[str, Any],
    vehicle_metrics: Dict[str, Any],
    d_ran: float,
    d_ai: float,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a non-zero SLA floor ledger for every active UE class."""
    camera_count = max(0, _safe_int(camera_metrics.get("active_cameras"), 0))
    sensor_count = max(0, _safe_int(app2_metrics.get("total_sensors"), 0))
    vehicle_count = max(0, _safe_int(vehicle_metrics.get("total_vehicles"), 0))
    if camera_count == 0:
        camera_count = max(0, _safe_int(config.get("default_camera_ues"), 3))
    if sensor_count == 0 and vehicle_count == 0:
        sensor_count = max(0, _safe_int(config.get("default_sensor_ues"), 12))
    if vehicle_count == 0:
        vehicle_count = max(0, _safe_int(config.get("default_vehicle_ues"), 5))

    fraction = _clamp(config.get("sla_floor_demand_fraction", 0.35), 0.05, 1.0)
    ran_floor = _clamp(
        max(_safe_float(config.get("ran_min_active_demand", 0.15), 0.15), d_ran * fraction),
        0.0,
        1.0,
    ) if camera_count else 0.0
    ai_floor = _clamp(
        max(_safe_float(config.get("ai_min_active_demand", 0.15), 0.15), d_ai * fraction),
        0.0,
        1.0,
    ) if sensor_count or vehicle_count else 0.0

    vehicle_weight = _clamp(config.get("vehicle_floor_priority_weight", 1.5), 1.0, 4.0)
    sensor_weight = 1.0
    weighted_ai_ues = (sensor_count * sensor_weight) + (vehicle_count * vehicle_weight)
    sensor_total = ai_floor * (sensor_count * sensor_weight) / weighted_ai_ues if weighted_ai_ues else 0.0
    vehicle_total = ai_floor * (vehicle_count * vehicle_weight) / weighted_ai_ues if weighted_ai_ues else 0.0
    ledger = []
    for index in range(camera_count):
        ledger.append({
            "ue_id": f"camera-{index + 1}", "service": "camera", "domain": "ran",
            "floor_share": ran_floor / camera_count,
            "sla": {"throughput_mbps": _safe_float(config.get("camera_throughput_target_mbps", 25.0), 25.0), "latency_ms": _safe_float(config.get("camera_latency_target_ms", 100.0), 100.0)},
        })
    for index in range(sensor_count):
        ledger.append({
            "ue_id": f"sensor-{index + 1}", "service": "sensor", "domain": "ai",
            "floor_share": sensor_total / sensor_count,
            "sla": {"latency_ms": 500.0, "packet_loss_percent": 5.0},
        })
    for index in range(vehicle_count):
        ledger.append({
            "ue_id": f"vehicle-{index + 1}", "service": "vehicle", "domain": "ai",
            "floor_share": vehicle_total / vehicle_count,
            "sla": {"latency_ms": _safe_float(config.get("vehicle_latency_target_ms", 20.0), 20.0), "packet_loss_percent": _safe_float(config.get("vehicle_loss_target_pct", 1.0), 1.0)},
        })
    return {
        "per_ue_floor": ledger,
        "floor_by_service": {
            "camera": {"ue_count": camera_count, "total_share": ran_floor, "per_ue_share": ran_floor / camera_count if camera_count else 0.0},
            "sensor": {"ue_count": sensor_count, "total_share": sensor_total, "per_ue_share": sensor_total / sensor_count if sensor_count else 0.0},
            "vehicle": {"ue_count": vehicle_count, "total_share": vehicle_total, "per_ue_share": vehicle_total / vehicle_count if vehicle_count else 0.0},
        },
        "floor_total_ran": ran_floor,
        "floor_total_ai": ai_floor,
        "active_ue_count": len(ledger),
    }


def enforce_resource_floor(snapshot: Dict[str, Any], r_ran: float, r_ai: float) -> Dict[str, Any]:
    """Clamp an assistant proposal so it cannot violate the UE floor."""
    result = dict(snapshot or {})
    floor_ran = _safe_float(result.get("floor_total_ran"), 0.0)
    floor_ai = _safe_float(result.get("floor_total_ai"), 0.0)
    budget = max(0.0, _safe_float(result.get("usable_budget", result.get("resource_budget", 1.0)), 1.0))
    ran = max(float(r_ran), floor_ran)
    ai = max(float(r_ai), floor_ai)
    feasible = bool(result.get("floor_feasible", floor_ran + floor_ai <= budget + 1e-9))
    if ran + ai > budget and ran + ai > 0.0:
        if floor_ran + floor_ai <= budget + 1e-9:
            extra = max(0.0, budget - floor_ran - floor_ai)
            extra_ran = max(0.0, ran - floor_ran)
            extra_ai = max(0.0, ai - floor_ai)
            extra_total = extra_ran + extra_ai
            ran = floor_ran + extra * extra_ran / extra_total if extra_total else floor_ran
            ai = floor_ai + extra * extra_ai / extra_total if extra_total else floor_ai
        else:
            scale = budget / (ran + ai)
            ran *= scale
            ai *= scale
            feasible = False
    d_ran = _safe_float(result.get("d_ran"), 0.0)
    d_ai = _safe_float(result.get("d_ai"), 0.0)
    result.update({
        "r_ran": ran,
        "r_ai": ai,
        "ran_completion_ratio": 1.0 if d_ran <= 1e-9 else _clamp(ran / d_ran),
        "ai_completion_ratio": 1.0 if d_ai <= 1e-9 else _clamp(ai / d_ai),
        "utilization_ratio": _clamp((ran + ai) / max(_safe_float(result.get("resource_budget"), 1.0), 1e-9)),
        "floor_feasible": feasible,
        "floor_enforced": True,
    })
    return result


def apply_per_ue_allocation(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Materialize the aggregate assistant allocation into per-UE shares.

    The resource model keeps RAN and AI as aggregate pools, while the SLA
    floor ledger identifies the individual UEs that must be protected.  This
    function turns that ledger into an auditable allocation map.  Floors are
    assigned first; any remaining pool is distributed by deterministic
    service priority so the total per-domain allocation remains equal to
    ``r_ran``/``r_ai``.

    This is intentionally not used by the rApp-only baseline.  The baseline
    remains a fixed state band and has no per-UE intelligent allocation.
    """
    result = dict(snapshot or {})
    ledger = result.get("per_ue_floor") or []
    if not isinstance(ledger, list):
        ledger = []

    domain_totals = {
        "ran": max(0.0, _safe_float(result.get("r_ran"), 0.0)),
        "ai": max(0.0, _safe_float(result.get("r_ai"), 0.0)),
    }
    floor_totals = {
        "ran": max(0.0, _safe_float(result.get("floor_total_ran"), 0.0)),
        "ai": max(0.0, _safe_float(result.get("floor_total_ai"), 0.0)),
    }
    service_priority = {"camera": 1.0, "sensor": 1.0, "vehicle": 1.5}
    allocations = []
    domain_entries = {"ran": [], "ai": []}

    for raw in ledger:
        if not isinstance(raw, dict):
            continue
        domain = str(raw.get("domain", "") or "").strip().lower()
        if domain not in domain_entries:
            continue
        entry = dict(raw)
        entry["floor_share"] = max(0.0, _safe_float(entry.get("floor_share"), 0.0))
        entry["allocated_share"] = entry["floor_share"]
        entry["reinforcement_share"] = 0.0
        entry["floor_met"] = True
        domain_entries[domain].append(entry)

    for domain, entries in domain_entries.items():
        total = domain_totals[domain]
        floor_total = sum(_safe_float(item.get("floor_share"), 0.0) for item in entries)
        if not entries:
            continue

        # Infeasible floors are scaled consistently, and are explicitly
        # reported instead of being presented as a successful guarantee.
        if total + 1e-9 < floor_total:
            scale = total / floor_total if floor_total > 1e-12 else 0.0
            for item in entries:
                item["allocated_share"] = item["floor_share"] * scale
                item["floor_met"] = False
            continue

        extra = max(0.0, total - floor_total)
        weights = []
        for item in entries:
            service = str(item.get("service", "") or "").strip().lower()
            floor_share = _safe_float(item.get("floor_share"), 0.0)
            weights.append(max(1e-12, floor_share) * service_priority.get(service, 1.0))
        weight_total = sum(weights)
        for item, weight in zip(entries, weights):
            reinforcement = extra * weight / weight_total if weight_total else 0.0
            item["reinforcement_share"] = reinforcement
            item["allocated_share"] = item["floor_share"] + reinforcement

    for domain in ("ran", "ai"):
        allocations.extend(domain_entries[domain])

    # Round only for the emitted contract; correct the last UE in each domain
    # so the serialized values still sum exactly to the aggregate pool.
    for domain in ("ran", "ai"):
        entries = domain_entries[domain]
        if not entries:
            continue
        rounded_total = sum(round(_safe_float(item["allocated_share"]), 12) for item in entries)
        correction = domain_totals[domain] - rounded_total
        entries[-1]["allocated_share"] = max(0.0, round(entries[-1]["allocated_share"] + correction, 12))
        for item in entries:
            item["floor_share"] = round(_safe_float(item["floor_share"]), 12)
            item["reinforcement_share"] = round(_safe_float(item["reinforcement_share"]), 12)
            item["allocated_share"] = round(_safe_float(item["allocated_share"]), 12)

    violations = sum(1 for item in allocations if not bool(item.get("floor_met", False)))
    result.update({
        "per_ue_allocation_version": "per_ue_floor_v1",
        "per_ue_allocation": allocations,
        "per_ue_floor_applied": True,
        "per_ue_floor_violation_count": violations,
        "per_ue_floor_feasible": violations == 0 and bool(result.get("floor_feasible", True)),
        "per_ue_application_status": "computed",
    })
    return result


def enforce_resource_state(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the configured state reinforcement after an assistant proposal.

    A proposal may contain only a split (``r_ran``/``r_ai``).  This barrier
    makes the runtime policy explicit: ALLOWED and CONDITIONAL are floor-only,
    while CRITICAL/BLOCKED must reserve a bounded amount above the floor.  The
    assistant can still use more than the required amount when it fits the
    budget, but it cannot under-provision a critical state.
    """
    result = dict(snapshot or {})
    state = _normalise_allocation_state(result.get("allocation_state", "ALLOWED"))
    floor_ran = _safe_float(result.get("floor_total_ran"), 0.0)
    floor_ai = _safe_float(result.get("floor_total_ai"), 0.0)
    budget = max(0.0, _safe_float(result.get("usable_budget", result.get("resource_budget", 1.0)), 1.0))
    floor_feasible = floor_ran + floor_ai <= budget + 1e-9
    scales = {
        "ALLOWED": 0.0,
        "CONDITIONAL": _clamp(result.get("conditional_reinforcement_scale", 0.0), 0.0, 1.0),
        "CRITICAL": _clamp(result.get("critical_reinforcement_scale", 0.75), 0.0, 1.0),
        "BLOCKED": _clamp(result.get("blocked_reinforcement_scale", 1.0), 0.0, 1.0),
    }
    stored_scale_state = _normalise_allocation_state(result.get("reinforcement_scale_state", state))
    stored_scale = result.get("reinforcement_scale") if stored_scale_state == state else scales[state]
    scale = _clamp(stored_scale, 0.0, 1.0)
    slack = max(0.0, budget - floor_ran - floor_ai)
    required_extra = slack * scale
    d_ran = max(0.0, _safe_float(result.get("d_ran"), 0.0))
    d_ai = max(0.0, _safe_float(result.get("d_ai"), 0.0))
    ran_weight = d_ran * max(1.0, _safe_float(result.get("ran_priority_bias"), 1.35))
    ai_weight = d_ai
    priority_domain = str(result.get("priority_domain", "global") or "global")
    if priority_domain == "camera":
        ran_weight *= 2.0
    elif priority_domain in {"vehicle", "sensor"}:
        ai_weight *= 2.0
    total_weight = ran_weight + ai_weight
    if total_weight <= 0.0:
        required_ran_extra = required_extra * 0.5
    else:
        required_ran_extra = required_extra * ran_weight / total_weight
    required_ai_extra = required_extra - required_ran_extra
    if state in {"ALLOWED", "CONDITIONAL"}:
        # Non-critical assistant decisions are deliberately floor-only.  Do
        # not preserve a larger TA-SAM/RL vector here: that would turn the
        # per-UE minimum into a lower bound while silently consuming the
        # energy headroom that this policy is meant to save.
        candidate_ran = floor_ran
        candidate_ai = floor_ai
    else:
        candidate_ran = max(
            _safe_float(result.get("r_ran"), 0.0),
            floor_ran + required_ran_extra,
        )
        candidate_ai = max(
            _safe_float(result.get("r_ai"), 0.0),
            floor_ai + required_ai_extra,
        )
    result["allocation_state"] = state
    result["reinforcement_scale"] = scale
    result["reinforcement_scale_state"] = state
    result["floor_feasible"] = bool(result.get("floor_feasible", floor_feasible)) and floor_feasible
    result = enforce_resource_floor(result, candidate_ran, candidate_ai)
    result["reinforcement_ran"] = max(0.0, result["r_ran"] - floor_ran)
    result["reinforcement_ai"] = max(0.0, result["r_ai"] - floor_ai)
    return result


def apply_baseline_resource_band(
    snapshot: Dict[str, Any],
    allocation_state: str | None,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply the deliberately simple rApp-only resource band.

    The baseline is not the per-UE floor.  It is a fixed, state-dependent
    policy used as the non-intelligent reference against which ARMD/TA-SAM are
    compared.  Ratios are relative to the usable budget so the band remains
    reproducible when demand changes the available budget.
    """
    result = dict(snapshot or {})
    state = _normalise_allocation_state(allocation_state or result.get("allocation_state"))
    if state == "CRITICAL":
        state = "BLOCKED"
    bands = config.get("baseline_resource_bands", {}) or {}
    default_bands = {
        "ALLOWED": {"ran_ratio": 0.6029416173, "ai_ratio": 0.3970583827},
        "CONDITIONAL": {"ran_ratio": 0.6432199344, "ai_ratio": 0.3567800656},
        "BLOCKED": {"ran_ratio": 0.7246045526, "ai_ratio": 0.2753954474},
    }
    band = dict(default_bands.get(state, default_bands["CONDITIONAL"]))
    band.update(bands.get(state, {}) or {})
    ran_ratio = _clamp(band.get("ran_ratio", 0.5), 0.0, 1.0)
    ai_ratio = _clamp(band.get("ai_ratio", 1.0 - ran_ratio), 0.0, 1.0)
    ratio_total = ran_ratio + ai_ratio
    if ratio_total <= 0.0:
        ran_ratio, ai_ratio = 0.5, 0.5
    else:
        ran_ratio, ai_ratio = ran_ratio / ratio_total, ai_ratio / ratio_total
    usable_budget = max(
        0.0,
        _safe_float(result.get("usable_budget", result.get("resource_budget", 1.0)), 1.0),
    )
    ran = usable_budget * ran_ratio
    ai = usable_budget * ai_ratio
    d_ran = _safe_float(result.get("d_ran"), 0.0)
    d_ai = _safe_float(result.get("d_ai"), 0.0)
    result.update({
        "allocation_state": state,
        "allocation_mode": "baseline_fixed_band_v1",
        "baseline_band_state": state,
        "baseline_band_ran_ratio": ran_ratio,
        "baseline_band_ai_ratio": ai_ratio,
        "baseline_band_applied": True,
        "r_ran": ran,
        "r_ai": ai,
        "reinforcement_ran": 0.0,
        "reinforcement_ai": 0.0,
        "floor_enforced": False,
        "per_ue_allocation": [],
        "per_ue_floor_applied": False,
        "per_ue_floor_violation_count": 0,
        "per_ue_floor_feasible": True,
        "per_ue_application_status": "baseline_not_applicable",
        "ran_completion_ratio": 1.0 if d_ran <= 1e-9 else _clamp(ran / d_ran),
        "ai_completion_ratio": 1.0 if d_ai <= 1e-9 else _clamp(ai / d_ai),
        "utilization_ratio": _clamp((ran + ai) / max(_safe_float(result.get("resource_budget"), 1.0), 1e-9)),
        "source": "rapp_baseline_fixed_band",
        "controller_id": "rapp_live_allocator_baseline",
    })
    return result


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
    throughput_guard = max(throughput_target, _safe_float(config.get("camera_throughput_guard_mbps", 30.0), 30.0))
    latency_warning_ms = max(1.0, _safe_float(config.get("camera_latency_warning_ms", 80.0), 80.0))
    latency_target_ms = max(latency_warning_ms, _safe_float(config.get("camera_latency_target_ms", 100.0), 100.0))
    throughput_pressure = _clamp((throughput_target - throughput_mbps) / throughput_target)
    headroom_target = max(1.0, _safe_float(config.get("camera_headroom_target_mbps", 35.0), 35.0))
    throughput_headroom_pressure = _clamp((headroom_target - throughput_mbps) / headroom_target)
    latency_pressure = _clamp((latency_ms - 20.0) / max(latency_target_ms - 20.0, 1.0))
    latency_guard_pressure = _clamp((latency_ms - 40.0) / max(latency_warning_ms - 40.0, 1.0))
    severe_throughput_pressure = _clamp(((throughput_target * 1.15) - throughput_mbps) / max(throughput_target * 1.15, 1.0))
    cvar_pressure = _clamp(cvar_ms / max(1.0, _safe_float(config.get("cvar_target_ms", 120.0), 120.0)))
    p95_pressure = _clamp(p95_ms / max(1.0, _safe_float(config.get("p95_target_ms", 80.0), 80.0)))
    warmup_pressure = 1.0 if active_cameras > 0 and not throughput_ready else 0.0
    throughput_warning = 1.0 if active_cameras > 0 and throughput_ready and throughput_mbps < throughput_guard else 0.0
    throughput_violation = 1.0 if active_cameras > 0 and throughput_ready and throughput_mbps < throughput_target else 0.0
    latency_warning = 1.0 if active_cameras > 0 and latency_ms >= latency_warning_ms else 0.0
    latency_violation = 1.0 if active_cameras > 0 and latency_ms >= latency_target_ms else 0.0

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
    allocation_state: str | None = None,
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
    state = _normalise_allocation_state(
        allocation_state
        or infer_allocation_state(camera_metrics, app2_metrics, vehicle_metrics, network_health, cfg)
    )
    floor = _build_per_ue_floor(camera_metrics or {}, app2_metrics or {}, vehicle_metrics or {}, d_ran, d_ai, cfg)
    floor_ran = floor["floor_total_ran"]
    floor_ai = floor["floor_total_ai"]
    floor_total = floor_ran + floor_ai
    total_pressure = _clamp((d_ran + d_ai) / 1.35, 0.0, 1.0)
    usable_budget = r_max * _clamp(min_utilization + total_pressure * (1.0 - min_utilization))

    floor_feasible = floor_total <= usable_budget + 1e-9
    reinforcement_scale = {
        "ALLOWED": 0.0,
        "CONDITIONAL": _clamp(cfg.get("conditional_reinforcement_scale", 0.0), 0.0, 1.0),
        "CRITICAL": _clamp(cfg.get("critical_reinforcement_scale", 0.75), 0.0, 1.0),
        "BLOCKED": _clamp(cfg.get("blocked_reinforcement_scale", 1.0), 0.0, 1.0),
    }[state]
    slack = max(0.0, usable_budget - floor_total)
    priority_domain = "global"
    camera_components = ran.get("components", {})
    vehicle_components = ai.get("components", {})
    if camera_components.get("throughput_violation") or camera_components.get("latency_violation"):
        priority_domain = "camera"
    elif vehicle_components.get("vehicle_pressure", 0.0) >= vehicle_components.get("app2_pressure", 0.0):
        priority_domain = "vehicle"
    elif vehicle_components.get("app2_pressure", 0.0) > 0.0:
        priority_domain = "sensor"

    base_ran = floor_ran if d_ran > 0 else 0.0
    base_ai = floor_ai if d_ai > 0 else 0.0
    remaining = slack * reinforcement_scale

    weighted_ran = d_ran * ran_priority_bias
    weighted_ai = d_ai
    if priority_domain == "camera":
        weighted_ran *= 2.0
    elif priority_domain in {"vehicle", "sensor"}:
        weighted_ai *= 2.0
    weight_sum = weighted_ran + weighted_ai
    if weight_sum > 0:
        extra_ran = remaining * (weighted_ran / weight_sum)
        extra_ai = remaining * (weighted_ai / weight_sum)
    else:
        extra_ran = remaining * 0.5
        extra_ai = remaining * 0.5

    r_ran = base_ran + extra_ran
    r_ai = base_ai + extra_ai

    # Critical states reinforce the violated domain immediately; healthy
    # states return to the floor in two stable cycles.
    prev = previous_allocation or {}
    prev_state = _normalise_allocation_state(prev.get("allocation_state", "ALLOWED"))
    healthy_streak = int(prev.get("healthy_streak", 0) or 0) + 1 if state == "ALLOWED" else 0
    if state == "ALLOWED":
        previous_excess_ran = max(0.0, _safe_float(prev.get("r_ran"), floor_ran) - _safe_float(prev.get("floor_total_ran"), floor_ran))
        previous_excess_ai = max(0.0, _safe_float(prev.get("r_ai"), floor_ai) - _safe_float(prev.get("floor_total_ai"), floor_ai))
        if prev_state != "ALLOWED" and healthy_streak == 1:
            r_ran = floor_ran + 0.5 * previous_excess_ran
            r_ai = floor_ai + 0.5 * previous_excess_ai
        else:
            r_ran, r_ai = floor_ran, floor_ai

    # Smooth allocation to avoid large jumps in successive cycles.
    prev_ran = _safe_float(prev.get("r_ran", r_ran), r_ran)
    prev_ai = _safe_float(prev.get("r_ai", r_ai), r_ai)
    smoothing = _clamp(cfg.get("allocation_smoothing", 0.35), 0.0, 1.0)
    if state in {"CRITICAL", "BLOCKED"}:
        smoothing = 0.0
    # ALLOWED is deliberately unsmoothed: after the first healthy cycle the
    # policy must actually return to the SLA floor on the second one.  The
    # non-allowed states still use smoothing to avoid oscillation, except for
    # CRITICAL/BLOCKED where the reinforcement is immediate.
    if state != "ALLOWED":
        r_ran = ((1.0 - smoothing) * r_ran) + (smoothing * prev_ran)
        r_ai = ((1.0 - smoothing) * r_ai) + (smoothing * prev_ai)
    total_alloc = r_ran + r_ai
    if total_alloc > usable_budget and total_alloc > 0:
        scale = usable_budget / total_alloc
        r_ran *= scale
        r_ai *= scale

    r_ran = max(r_ran, floor_ran)
    r_ai = max(r_ai, floor_ai)
    if r_ran + r_ai > usable_budget and floor_feasible:
        excess = (r_ran + r_ai) - usable_budget
        reducible_ran = max(0.0, r_ran - floor_ran)
        reducible_ai = max(0.0, r_ai - floor_ai)
        reducible = reducible_ran + reducible_ai
        if reducible > 0:
            r_ran -= excess * reducible_ran / reducible
            r_ai -= excess * reducible_ai / reducible

    ran_completion = 1.0 if d_ran <= 1e-9 else _clamp(r_ran / d_ran)
    ai_completion = 1.0 if d_ai <= 1e-9 else _clamp(r_ai / d_ai)
    utilization = _clamp((r_ran + r_ai) / r_max)

    snapshot = {
        "controller_id": "tasam_greenran_bootstrap_heuristic",
        "target_policy_id": "ta_sam_shadow_runtime",
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
        "allocation_state": state,
        "priority_domain": priority_domain,
        "healthy_streak": healthy_streak,
        "reinforcement_scale": reinforcement_scale,
        "reinforcement_scale_state": state,
        "conditional_reinforcement_scale": _clamp(cfg.get("conditional_reinforcement_scale", 0.0), 0.0, 1.0),
        "critical_reinforcement_scale": _clamp(cfg.get("critical_reinforcement_scale", 0.75), 0.0, 1.0),
        "blocked_reinforcement_scale": _clamp(cfg.get("blocked_reinforcement_scale", 1.0), 0.0, 1.0),
        "ran_priority_bias": ran_priority_bias,
        "reinforcement_ran": max(0.0, r_ran - floor_ran),
        "reinforcement_ai": max(0.0, r_ai - floor_ai),
        "floor_feasible": floor_feasible,
        "floor_enforced": True,
        "floor_policy": "sla_per_ue_v1",
        **floor,
        "ran_components": ran["components"],
        "ai_components": ai["components"],
    }
    snapshot = enforce_resource_state(snapshot)
    snapshot["article_marl_state"] = build_du_state_snapshot(
        camera_metrics=camera_metrics,
        app2_metrics=app2_metrics,
        vehicle_metrics=vehicle_metrics,
        network_health=network_health,
        resource_snapshot=snapshot,
    )
    return snapshot
