#!/usr/bin/env python3
"""Logical DU topology and state export aligned to the TA-SAM MARL article."""

from __future__ import annotations

import os
from typing import Any, Dict, List

try:
    from .greenran_paths import (
        get_fixed_background_imsi_range,
        get_fixed_camera_imsis,
        get_fixed_max_vehicles,
        get_fixed_marl_topology,
        get_fixed_total_ues,
        get_fixed_vehicle_base_imsi,
    )
except ImportError:
    from greenran_paths import (
        get_fixed_background_imsi_range,
        get_fixed_camera_imsis,
        get_fixed_max_vehicles,
        get_fixed_marl_topology,
        get_fixed_total_ues,
        get_fixed_vehicle_base_imsi,
    )


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


def _explicit_state_features(
    resource_snapshot: Dict[str, Any] | None,
    operating_state: Any = None,
) -> List[float]:
    """Optionally expose the authoritative 3-way operating state to MARL.

    The historical article-compatible vector remains 10-dimensional by
    default.  New state-aware rounds opt in through an environment flag and
    append a one-hot ALLOWED/CONDITIONAL/BLOCKED context, so old checkpoints
    keep their original input shape and remain loadable.
    """
    if os.environ.get('GREENRAN_TASAM_EXPLICIT_STATE_FEATURE', '0').strip() != '1':
        return []
    snapshot = resource_snapshot or {}
    # This feature describes the state observed from the current network
    # metrics.  It must not inherit the previous allocation/action state,
    # otherwise a prior BLOCKED decision can contaminate a healthy snapshot.
    state = str(
        operating_state
        if operating_state is not None
        else snapshot.get('state_category') or snapshot.get('network_operating_state')
        or snapshot.get('allocation_state')
        or ''
    ).strip().upper()
    if state == 'CRITICAL':
        state = 'BLOCKED'
    if state not in {'ALLOWED', 'CONDITIONAL', 'BLOCKED'}:
        state = 'ALLOWED'
    return [
        1.0 if state == 'ALLOWED' else 0.0,
        1.0 if state == 'CONDITIONAL' else 0.0,
        1.0 if state == 'BLOCKED' else 0.0,
    ]


def load_logical_du_topology() -> dict:
    topology = get_fixed_marl_topology() or {}
    dus = topology.get('logical_dus') if isinstance(topology, dict) else []
    if not isinstance(dus, list):
        dus = []
    if dus:
        return {
            'topology_id': str(topology.get('topology_id', 'greenran_fixed_marl_v1') or 'greenran_fixed_marl_v1'),
            'logical_du_count': _safe_int(topology.get('logical_du_count', len(dus)), len(dus)),
            'logical_dus': dus,
            'slice_profiles': topology.get('slice_profiles', {}),
        }

    base = get_fixed_vehicle_base_imsi()
    max_vehicles = get_fixed_max_vehicles()
    return {
        'topology_id': 'greenran_fixed_marl_v1',
        'logical_du_count': 3,
        'logical_dus': [
            {'du_id': 'du_camera_edge', 'role': 'camera_edge', 'primary_slice': 'eMBB', 'camera_imsis': list(get_fixed_camera_imsis()), 'background_imsis': [4, 5, 6], 'slice_mix': {'eMBB': 0.85, 'mMTC': 0.10, 'URLLC': 0.05}},
            {'du_id': 'du_sensor_mixed', 'role': 'sensor_mixed', 'primary_slice': 'mMTC', 'background_imsis': [7, 8, 9], 'slice_mix': {'eMBB': 0.35, 'mMTC': 0.55, 'URLLC': 0.10}},
            {'du_id': 'du_vehicle_edge', 'role': 'vehicle_edge', 'primary_slice': 'URLLC', 'background_imsis': [10, 11, 12], 'vehicle_imsi_range': [base, base + max_vehicles - 1], 'slice_mix': {'eMBB': 0.20, 'mMTC': 0.15, 'URLLC': 0.65}},
        ],
        'slice_profiles': {
            'eMBB': {'app': 'app1', 'domain': 'camera', 'qos_target': 'throughput'},
            'mMTC': {'app': 'app2', 'domain': 'sensor', 'qos_target': 'delivery'},
            'URLLC': {'app': 'app3', 'domain': 'vehicle', 'qos_target': 'latency'},
        },
    }


def _split_ai_demands(ai_total: float, ai_components: Dict[str, Any] | None) -> tuple[float, float]:
    components = ai_components or {}
    app2_pressure = _clamp(_safe_float(components.get('app2_pressure', 0.5), 0.5))
    vehicle_pressure = _clamp(_safe_float(components.get('vehicle_pressure', 0.5), 0.5))
    total = app2_pressure + vehicle_pressure
    if total <= 1e-9:
        return ai_total * 0.5, ai_total * 0.5
    return ai_total * (app2_pressure / total), ai_total * (vehicle_pressure / total)


def build_slice_state(
    camera_metrics: Dict[str, Any] | None,
    app2_metrics: Dict[str, Any] | None,
    vehicle_metrics: Dict[str, Any] | None,
    network_health: Dict[str, Any] | None,
    resource_snapshot: Dict[str, Any] | None,
) -> Dict[str, Dict[str, Any]]:
    camera_metrics = camera_metrics or {}
    app2_metrics = app2_metrics or {}
    vehicle_metrics = vehicle_metrics or {}
    network_health = network_health or {}
    resource_snapshot = resource_snapshot or {}

    d_ran = _safe_float(resource_snapshot.get('d_ran', 0.0), 0.0)
    d_ai = _safe_float(resource_snapshot.get('d_ai', 0.0), 0.0)
    r_ran = _safe_float(resource_snapshot.get('r_ran', 0.0), 0.0)
    r_ai = _safe_float(resource_snapshot.get('r_ai', 0.0), 0.0)
    usable_budget = _safe_float(resource_snapshot.get('usable_budget', resource_snapshot.get('resource_budget', 1.0)), 1.0)
    mmtc_demand, urllc_demand = _split_ai_demands(d_ai, resource_snapshot.get('ai_components'))
    mmtc_alloc, urllc_alloc = _split_ai_demands(r_ai, resource_snapshot.get('ai_components'))

    total_ues = get_fixed_total_ues()
    active_cameras = max(1, _safe_int(camera_metrics.get('active_cameras', len(get_fixed_camera_imsis())), len(get_fixed_camera_imsis())))
    background_start, background_end = get_fixed_background_imsi_range()
    background_ues = max(0, background_end - background_start + 1)
    sensor_count = max(_safe_int(app2_metrics.get('active_sensors', app2_metrics.get('total_sensors', 0)), 0), 1)
    vehicle_count = max(_safe_int(vehicle_metrics.get('total_vehicles', 0), 0), 1)

    embb_throughput = _safe_float(camera_metrics.get('throughput_mbps', 0.0), 0.0)
    embb_latency = _safe_float(camera_metrics.get('latency_ms', 0.0), 0.0)
    embb_pressure = max(
        _clamp((25.0 - embb_throughput) / 25.0),
        _clamp((embb_latency - 40.0) / 60.0),
        _clamp(_safe_float(network_health.get('p95_us', 0.0), 0.0) / 1000.0 / 120.0),
    )

    connected_ratio = _clamp(_safe_float(app2_metrics.get('connected_ratio', 1.0), 1.0))
    delivery_success = _clamp(_safe_float(app2_metrics.get('delivery_success_percent', 100.0), 100.0) / 100.0)
    mmtc_latency_ms = _safe_float(app2_metrics.get('avg_latency_ms', 0.0), 0.0)
    mmtc_pressure = max(
        _clamp((0.95 - connected_ratio) / 0.35),
        _clamp((0.95 - delivery_success) / 0.25),
        _clamp(mmtc_latency_ms / 1000.0),
    )

    vehicle_latency_ms = _safe_float(vehicle_metrics.get('max_latency_ms', 0.0), 0.0)
    vehicle_loss_pct = _safe_float(vehicle_metrics.get('max_packet_loss_percent', 0.0), 0.0)
    high_risk = _safe_float(vehicle_metrics.get('high_risk_vehicles', 0.0), 0.0)
    urllc_pressure = max(
        _clamp(vehicle_latency_ms / 120.0),
        _clamp(vehicle_loss_pct / 10.0),
        _clamp(high_risk / max(vehicle_count, 1)),
    )

    return {
        'eMBB': {
            'slice_id': 'eMBB',
            'ue_count': min(total_ues, active_cameras + background_ues),
            'demand': d_ran,
            'allocation': r_ran,
            'qos_pressure': embb_pressure,
            'completion_ratio': _clamp(_safe_float(resource_snapshot.get('ran_completion_ratio', 0.0), 0.0)),
            'min_qos_met': 1.0 if embb_pressure < 0.35 else 0.0,
            'budget_share': _clamp(r_ran / max(usable_budget, 1e-9)),
        },
        'mMTC': {
            'slice_id': 'mMTC',
            'ue_count': sensor_count,
            'demand': mmtc_demand,
            'allocation': mmtc_alloc,
            'qos_pressure': mmtc_pressure,
            'completion_ratio': 1.0 if mmtc_demand <= 1e-9 else _clamp(mmtc_alloc / max(mmtc_demand, 1e-9)),
            'min_qos_met': 1.0 if mmtc_pressure < 0.35 else 0.0,
            'budget_share': _clamp(mmtc_alloc / max(usable_budget, 1e-9)),
        },
        'URLLC': {
            'slice_id': 'URLLC',
            'ue_count': vehicle_count,
            'demand': urllc_demand,
            'allocation': urllc_alloc,
            'qos_pressure': urllc_pressure,
            'completion_ratio': 1.0 if urllc_demand <= 1e-9 else _clamp(urllc_alloc / max(urllc_demand, 1e-9)),
            'min_qos_met': 1.0 if urllc_pressure < 0.35 else 0.0,
            'budget_share': _clamp(urllc_alloc / max(usable_budget, 1e-9)),
        },
    }


def build_du_state_snapshot(
    camera_metrics: Dict[str, Any] | None,
    app2_metrics: Dict[str, Any] | None,
    vehicle_metrics: Dict[str, Any] | None,
    network_health: Dict[str, Any] | None,
    resource_snapshot: Dict[str, Any] | None,
    operating_state: Any = None,
) -> Dict[str, Any]:
    topology = load_logical_du_topology()
    slice_state = build_slice_state(camera_metrics, app2_metrics, vehicle_metrics, network_health, resource_snapshot)
    usable_budget = _safe_float((resource_snapshot or {}).get('usable_budget', (resource_snapshot or {}).get('resource_budget', 1.0)), 1.0)
    total_demand = sum(_safe_float(v.get('demand', 0.0), 0.0) for v in slice_state.values())

    du_states: List[Dict[str, Any]] = []
    for du in topology.get('logical_dus', []):
        mix = du.get('slice_mix', {}) if isinstance(du, dict) else {}
        embb_weight = _clamp(_safe_float(mix.get('eMBB', 0.0), 0.0))
        mmtc_weight = _clamp(_safe_float(mix.get('mMTC', 0.0), 0.0))
        urllc_weight = _clamp(_safe_float(mix.get('URLLC', 0.0), 0.0))
        weight_sum = embb_weight + mmtc_weight + urllc_weight
        if weight_sum <= 1e-9:
            embb_weight, mmtc_weight, urllc_weight = 1.0, 0.0, 0.0
            weight_sum = 1.0
        embb_weight /= weight_sum
        mmtc_weight /= weight_sum
        urllc_weight /= weight_sum

        bg_imsis = du.get('background_imsis', []) if isinstance(du, dict) else []
        camera_imsis = du.get('camera_imsis', []) if isinstance(du, dict) else []
        vehicle_range = du.get('vehicle_imsi_range', []) if isinstance(du, dict) else []
        vehicle_count = 0
        if isinstance(vehicle_range, list) and len(vehicle_range) == 2:
            vehicle_count = max(0, int(vehicle_range[1]) - int(vehicle_range[0]) + 1)

        ue_count = len(bg_imsis) + len(camera_imsis) + vehicle_count
        demand_share = (
            embb_weight * _safe_float(slice_state['eMBB']['demand']) +
            mmtc_weight * _safe_float(slice_state['mMTC']['demand']) +
            urllc_weight * _safe_float(slice_state['URLLC']['demand'])
        )
        allocation_share = (
            embb_weight * _safe_float(slice_state['eMBB']['allocation']) +
            mmtc_weight * _safe_float(slice_state['mMTC']['allocation']) +
            urllc_weight * _safe_float(slice_state['URLLC']['allocation'])
        )
        state_vector = [
            _safe_float(slice_state['eMBB']['qos_pressure']),
            _safe_float(slice_state['mMTC']['qos_pressure']),
            _safe_float(slice_state['URLLC']['qos_pressure']),
            _clamp(ue_count / max(get_fixed_total_ues() + get_fixed_max_vehicles(), 1)),
            embb_weight,
            mmtc_weight,
            urllc_weight,
            _clamp(demand_share / max(usable_budget, 1e-9)),
            _clamp(allocation_share / max(usable_budget, 1e-9)),
            _clamp(allocation_share / max(demand_share, 1e-9)) if demand_share > 1e-9 else 1.0,
        ]
        state_vector.extend(_explicit_state_features(resource_snapshot, operating_state))
        du_states.append({
            'du_id': str(du.get('du_id', 'unknown') or 'unknown'),
            'role': str(du.get('role', 'unknown') or 'unknown'),
            'primary_slice': str(du.get('primary_slice', 'eMBB') or 'eMBB'),
            'ue_count': ue_count,
            'slice_mix': {'eMBB': embb_weight, 'mMTC': mmtc_weight, 'URLLC': urllc_weight},
            'demand_share': demand_share,
            'allocation_share': allocation_share,
            'state_vector': state_vector,
        })

    global_state = {
        'topology_id': topology.get('topology_id', 'greenran_fixed_marl_v1'),
        'logical_du_count': len(du_states),
        'total_demand': total_demand,
        'usable_budget': usable_budget,
        'state_vector': [
            _safe_float(slice_state['eMBB']['demand']),
            _safe_float(slice_state['mMTC']['demand']),
            _safe_float(slice_state['URLLC']['demand']),
            _safe_float(slice_state['eMBB']['allocation']),
            _safe_float(slice_state['mMTC']['allocation']),
            _safe_float(slice_state['URLLC']['allocation']),
            _safe_float((resource_snapshot or {}).get('ran_completion_ratio', 0.0)),
            _safe_float((resource_snapshot or {}).get('ai_completion_ratio', 0.0)),
            _safe_float((resource_snapshot or {}).get('utilization_ratio', 0.0)),
            _clamp(total_demand / max(usable_budget, 1e-9)) if usable_budget > 1e-9 else 0.0,
        ],
    }
    global_state['state_vector'].extend(_explicit_state_features(resource_snapshot, operating_state))
    global_state['state_category'] = str(
        operating_state
        if operating_state is not None
        else (resource_snapshot or {}).get('state_category')
        or (resource_snapshot or {}).get('network_operating_state')
        or (resource_snapshot or {}).get('allocation_state')
        or 'ALLOWED'
    ).upper()

    return {
        'topology_id': topology.get('topology_id', 'greenran_fixed_marl_v1'),
        'slice_state': slice_state,
        'du_states': du_states,
        'global_state': global_state,
    }


def build_du_state_snapshot_from_resource_snapshot(resource_snapshot: Dict[str, Any] | None) -> Dict[str, Any]:
    resource_snapshot = resource_snapshot or {}
    d_ran = _clamp(_safe_float(resource_snapshot.get('d_ran', 0.0), 0.0))
    d_ai = _clamp(_safe_float(resource_snapshot.get('d_ai', 0.0), 0.0))
    ran_completion = _clamp(_safe_float(resource_snapshot.get('ran_completion_ratio', 0.0), 0.0))
    ai_completion = _clamp(_safe_float(resource_snapshot.get('ai_completion_ratio', 0.0), 0.0))
    ai_components = resource_snapshot.get('ai_components') if isinstance(resource_snapshot, dict) else {}
    app2_demand, urllc_demand = _split_ai_demands(d_ai, ai_components)
    app2_alloc, urllc_alloc = _split_ai_demands(_safe_float(resource_snapshot.get('r_ai', 0.0), 0.0), ai_components)

    camera_metrics = {
        'active_cameras': len(get_fixed_camera_imsis()),
        'throughput_mbps': max(0.0, 25.0 * ran_completion),
        'latency_ms': 30.0 + (70.0 * max(0.0, 1.0 - ran_completion)),
    }
    app2_metrics = {
        'total_sensors': 12,
        'active_sensors': 12,
        'connected_ratio': 1.0 - (0.3 * max(0.0, 1.0 - ai_completion)),
        'delivery_success_percent': 100.0 * (1.0 - (0.2 * max(0.0, 1.0 - ai_completion))),
        'avg_latency_ms': 150.0 + (850.0 * max(0.0, 1.0 - (app2_alloc / max(app2_demand, 1e-9)) if app2_demand > 1e-9 else 0.0)),
    }
    vehicle_metrics = {
        'total_vehicles': get_fixed_max_vehicles(),
        'high_risk_vehicles': int(round(max(0.0, 1.0 - (urllc_alloc / max(urllc_demand, 1e-9)) if urllc_demand > 1e-9 else 0.0) * get_fixed_max_vehicles())),
        'max_latency_ms': 40.0 + (100.0 * max(0.0, 1.0 - (urllc_alloc / max(urllc_demand, 1e-9)) if urllc_demand > 1e-9 else 0.0)),
        'max_packet_loss_percent': 2.0 + (8.0 * max(0.0, 1.0 - (urllc_alloc / max(urllc_demand, 1e-9)) if urllc_demand > 1e-9 else 0.0)),
    }
    network_health = {
        'p95_us': (40.0 + (80.0 * max(0.0, 1.0 - ran_completion))) * 1000.0,
        'cvar_us': (80.0 + (140.0 * max(0.0, 1.0 - ran_completion))) * 1000.0,
    }
    return build_du_state_snapshot(
        camera_metrics=camera_metrics,
        app2_metrics=app2_metrics,
        vehicle_metrics=vehicle_metrics,
        network_health=network_health,
        resource_snapshot=resource_snapshot,
    )
