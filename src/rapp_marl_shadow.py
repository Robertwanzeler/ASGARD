#!/usr/bin/env python3
"""Checkpoint-backed shadow-only MARL evaluator for the article-aligned GreenRAN path."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
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
        self.project_root = Path(__file__).resolve().parent.parent
        self.eval_manifest_path = Path(
            os.environ.get(
                'GREENRAN_TASAM_EVAL_MANIFEST',
                self.project_root / 'runs' / 'sac_bootstrap' / 'tasam_candidate_evaluation_latest.json',
            )
        )
        self.checkpoint_source = 'heuristic'
        self.checkpoint_run_dir = ''
        self.checkpoint_readiness = 'unknown'
        self.checkpoint_meta: Dict[str, Any] = {}
        self._torch = None
        self._actors = None
        self._checkpoint_error = ''
        if self.enabled:
            self._try_load_checkpoint()

    def _load_json(self, path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _ensure_local_torch_importable(self) -> None:
        venv_site = self.project_root / 'drlexp' / '.venv' / 'lib'
        if not venv_site.exists():
            return
        for child in sorted(venv_site.glob('python*/site-packages')):
            candidate = child / 'torch'
            if candidate.exists():
                site_path = str(child)
                if site_path not in sys.path:
                    sys.path.insert(0, site_path)
                return

    def _try_load_checkpoint(self) -> None:
        manifest = self._load_json(self.eval_manifest_path)
        best = (manifest or {}).get('best_run') or {}
        if not best:
            self._checkpoint_error = 'best_run missing from evaluation manifest'
            return
        self.checkpoint_readiness = str(best.get('readiness', 'unknown') or 'unknown')
        self.checkpoint_run_dir = str(best.get('run_dir', '') or '')
        if not bool(best.get('promote_shadow', False)):
            self._checkpoint_error = 'best_run not approved for shadow promotion'
            return
        if not self.checkpoint_run_dir:
            self._checkpoint_error = 'run_dir missing from evaluation manifest'
            return
        run_dir = Path(self.checkpoint_run_dir)
        meta_path = run_dir / 'tasam_marl_checkpoint_meta.json'
        ckpt_path = run_dir / 'tasam_marl_actors.pt'
        if not meta_path.exists() or not ckpt_path.exists():
            self._checkpoint_error = 'checkpoint files missing'
            return
        self.checkpoint_meta = self._load_json(meta_path)
        try:
            self._ensure_local_torch_importable()
            import torch
            from torch import nn
        except Exception as exc:
            self._checkpoint_error = f'torch unavailable: {exc}'
            return

        du_count = int(self.checkpoint_meta.get('du_count', 0) or 0)
        du_state_dim = int(self.checkpoint_meta.get('du_state_dim', 0) or 0)
        if du_count <= 0 or du_state_dim <= 0:
            self._checkpoint_error = 'invalid checkpoint metadata'
            return

        class _ActorNetwork(nn.Module):
            def __init__(self, input_dim: int, hidden_dim: int = 64, action_dim: int = 3) -> None:
                super().__init__()
                self.net = nn.Sequential(
                    nn.Linear(input_dim, hidden_dim),
                    nn.ReLU(),
                    nn.Linear(hidden_dim, hidden_dim),
                    nn.ReLU(),
                    nn.Linear(hidden_dim, action_dim),
                    nn.Sigmoid(),
                )

            def forward(self, x):
                return self.net(x)

        actors = nn.ModuleList([_ActorNetwork(du_state_dim) for _ in range(du_count)])
        state_dict = torch.load(ckpt_path, map_location='cpu')
        actors.load_state_dict(state_dict)
        actors.eval()
        self._torch = torch
        self._actors = actors
        self.policy_id = f"{self.policy_id}:{run_dir.name}"
        self.checkpoint_source = 'checkpoint'
        self._checkpoint_error = ''

    def _heuristic_du_action(self, du_state: Dict[str, Any]) -> List[float]:
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

    def _checkpoint_du_action(self, du_index: int, du_state: Dict[str, Any]) -> List[float] | None:
        if self._actors is None or self._torch is None:
            return None
        state = list(du_state.get('state_vector', []) or [])
        if du_index >= len(self._actors):
            return None
        expected_dim = int(self.checkpoint_meta.get('du_state_dim', len(state)) or len(state))
        if len(state) != expected_dim:
            return None
        with self._torch.no_grad():
            tensor = self._torch.tensor(state, dtype=self._torch.float32)
            out = self._actors[du_index](tensor).tolist()
        if len(out) != 3:
            return None
        total = sum(float(v) for v in out)
        if total <= 1e-9:
            return None
        return [float(v) / total for v in out]

    def _du_action(self, du_index: int, du_state: Dict[str, Any]) -> tuple[List[float], str]:
        checkpoint_action = self._checkpoint_du_action(du_index, du_state)
        if checkpoint_action is not None:
            return checkpoint_action, 'checkpoint'
        return self._heuristic_du_action(du_state), 'heuristic'

    def evaluate(self, marl_state: Dict[str, Any] | None, resource_snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.enabled:
            return {'enabled': False, 'policy_id': self.policy_id, 'mode': self.mode}
        marl_state = marl_state or {}
        resource_snapshot = resource_snapshot or {}
        du_states = marl_state.get('du_states', []) or []
        slice_state = marl_state.get('slice_state', {}) or {}
        if not du_states:
            return {
                'enabled': True,
                'policy_id': self.policy_id,
                'mode': self.mode,
                'available': False,
                'source': self.checkpoint_source,
                'checkpoint_readiness': self.checkpoint_readiness,
                'checkpoint_error': self._checkpoint_error,
            }

        du_recommendations = []
        mean_embb = mean_mmtc = mean_urllc = 0.0
        used_checkpoint = 0
        for idx, du in enumerate(du_states):
            action, source = self._du_action(idx, du)
            if source == 'checkpoint':
                used_checkpoint += 1
            mean_embb += action[0]
            mean_mmtc += action[1]
            mean_urllc += action[2]
            du_recommendations.append({
                'du_id': du.get('du_id', 'unknown'),
                'primary_slice': du.get('primary_slice', 'unknown'),
                'source': source,
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
        final_source = 'checkpoint' if used_checkpoint == len(du_recommendations) and du_recommendations else 'heuristic'
        if used_checkpoint and used_checkpoint < len(du_recommendations):
            final_source = 'mixed'

        return {
            'enabled': True,
            'available': True,
            'policy_id': self.policy_id,
            'mode': self.mode,
            'source': final_source,
            'checkpoint_readiness': self.checkpoint_readiness,
            'checkpoint_run_dir': self.checkpoint_run_dir,
            'checkpoint_error': self._checkpoint_error,
            'topology_id': marl_state.get('topology_id', 'unknown'),
            'du_count': len(du_recommendations),
            'du_recommendations': du_recommendations,
            'shadow_r_ran': round(shadow_r_ran, 4),
            'shadow_r_ai': round(shadow_r_ai, 4),
            'delta_r_ran_vs_live': round(shadow_r_ran - current_r_ran, 4),
            'delta_r_ai_vs_live': round(shadow_r_ai - current_r_ai, 4),
            'mean_action_vector': [round(mean_embb, 4), round(mean_mmtc, 4), round(mean_urllc, 4)],
        }
