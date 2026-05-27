#!/usr/bin/env python3
"""Shadow-only MARL evaluator for the article-aligned GreenRAN path."""

from __future__ import annotations

import os
from typing import Any, Dict, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    value = _safe_float(value, lower)
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


class MARLShadowRuntimeEvaluator:
    def __init__(self) -> None:
        self.enabled = str(os.environ.get('GREENRAN_MARL_SHADOW_ENABLE', '1')).strip().lower() not in {'0', 'false', 'no'}
        self.policy_id = os.environ.get('GREENRAN_MARL_SHADOW_POLICY_ID', 'ta_sam_marl_shadow_v1')
        self.mode = 'shadow_only'

    def _du_action(self, du_state: Dict[str, Any]) -> List[float]:
        state = list(du_state.get('state_vector', []) or [])
        if len(state) < 10:
            return [0.33, 0.33, 0.34]
        embb_pressure, mmtc_pressure, urllc_pressure = (_safe_float(state[0]), _safe_float(state[1]), _safe_float(state[2]))
        embb_mix, mmtc_mix, urllc_mix = (_safe_float(state[4]), _safe_float(state[5]), _safe_float(state[6]))
        demand_pressure = _safe_float(state[7])
        completion = _safe_float(state[9], 1.0)
        embb_score = max(0.01, embb_mix * (0.5 + embb_pressure + demand_pressure + max(0.0, 1.0 - completion)))
        mmtc_score = max(0.01, mmtc_mix * (0.5 + mmtc_pressure + 0.7 * demand_pressure))
        urllc_score = max(0.01, urllc_mix * (0.5 + urllc_pressure + 1.2 * max(0.0, 1.0 - completion)))
        total = embb_score + mmtc_score + urllc_score
        return [embb_score / total, mmtc_score / total, urllc_score / total]

    def evaluate(self, marl_state: Dict[str, Any] | None, resource_snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.enabled:
            return {'enabled': False, 'policy_id': self.policy_id, 'mode': self.mode}
        marl_state = marl_state or {}
        resource_snapshot = resource_snapshot or {}
        du_states = marl_state.get('du_states', []) or []
        slice_state = marl_state.get('slice_state', {}) or {}
        if not du_states:
            return {'enabled': True, 'policy_id': self.policy_id, 'mode': self.mode, 'available': False}

        du_recommendations = []
        mean_embb = mean_mmtc = mean_urllc = 0.0
        for du in du_states:
            action = self._du_action(du)
            mean_embb += action[0]
            mean_mmtc += action[1]
            mean_urllc += action[2]
            du_recommendations.append({
                'du_id': du.get('du_id', 'unknown'),
                'primary_slice': du.get('primary_slice', 'unknown'),
                'action_vector': [round(v, 4) for v in action],
            })
        count = max(len(du_recommendations), 1)
        mean_embb /= count
        mean_mmtc /= count
        mean_urllc /= count

        usable_budget = _safe_float(resource_snapshot.get('usable_budget', resource_snapshot.get('resource_budget', 1.0)), 1.0)
        current_r_ran = _safe_float(resource_snapshot.get('r_ran', 0.0), 0.0)
        current_r_ai = _safe_float(resource_snapshot.get('r_ai', 0.0), 0.0)
        embb_pressure = _safe_float((slice_state.get('eMBB') or {}).get('qos_pressure', 0.0), 0.0)
        ai_pressure = max(
            _safe_float((slice_state.get('mMTC') or {}).get('qos_pressure', 0.0), 0.0),
            _safe_float((slice_state.get('URLLC') or {}).get('qos_pressure', 0.0), 0.0),
        )
        shadow_ran_share = _clamp(0.4 + (0.35 * mean_embb) + (0.15 * embb_pressure) - (0.10 * ai_pressure), 0.15, 0.9)
        shadow_r_ran = usable_budget * shadow_ran_share
        shadow_r_ai = max(0.0, usable_budget - shadow_r_ran)

        return {
            'enabled': True,
            'available': True,
            'policy_id': self.policy_id,
            'mode': self.mode,
            'topology_id': marl_state.get('topology_id', 'unknown'),
            'du_count': len(du_recommendations),
            'du_recommendations': du_recommendations,
            'shadow_r_ran': round(shadow_r_ran, 4),
            'shadow_r_ai': round(shadow_r_ai, 4),
            'delta_r_ran_vs_live': round(shadow_r_ran - current_r_ran, 4),
            'delta_r_ai_vs_live': round(shadow_r_ai - current_r_ai, 4),
            'mean_action_vector': [round(mean_embb, 4), round(mean_mmtc, 4), round(mean_urllc, 4)],
        }
