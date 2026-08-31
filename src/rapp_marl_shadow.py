#!/usr/bin/env python3
"""Checkpoint-backed shadow-only MARL evaluator for the article-aligned GreenRAN path."""

from __future__ import annotations

import json
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any, Dict, List

try:
    from greenran_paths import ARTICLE00_SCENARIO_CONTROL_PATH
except ModuleNotFoundError:
    from src.greenran_paths import ARTICLE00_SCENARIO_CONTROL_PATH


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in {'0', 'false', 'no', 'off', ''}


def _completion_estimate(allocation: float, demand: float) -> float:
    demand = _safe_float(demand, 0.0)
    if demand <= 1e-9:
        return 1.0
    return _clamp(_safe_float(allocation, 0.0) / demand, 0.0, 1.0)


def _shortfall(allocation: float, demand: float) -> float:
    return max(_safe_float(demand, 0.0) - _safe_float(allocation, 0.0), 0.0)


def _surplus(allocation: float, demand: float) -> float:
    return max(_safe_float(allocation, 0.0) - _safe_float(demand, 0.0), 0.0)


def _safe_read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _scenario_priority(resource_snapshot: Dict[str, Any] | None, marl_shadow: Dict[str, Any] | None) -> str:
    marl_shadow = marl_shadow or {}
    stage_name = str(marl_shadow.get('scenario_stage') or '').strip().lower()
    if any(token in stage_name for token in ('camera', 'app1', 'ran_camera')):
        return 'ran_camera'
    if any(token in stage_name for token in ('background', 'ran_balanced')):
        return 'ran_balanced'
    if any(token in stage_name for token in ('vehicle', 'app2', 'ai_guarded')):
        return 'ai_guarded'

    resource_snapshot = resource_snapshot or {}
    ran_components = resource_snapshot.get('ran_components') or {}
    ai_components = resource_snapshot.get('ai_components') or {}
    if stage_name and 'mixed' in stage_name:
        if max(_safe_float(ran_components.get('latency_pressure')), _safe_float(ran_components.get('cvar_pressure')), _safe_float(ran_components.get('p95_pressure'))) >= 0.75:
            return 'ran_camera'
        if max(_safe_float(ai_components.get('vehicle_pressure')), _safe_float(ai_components.get('app2_pressure'))) >= 0.60:
            return 'ai_guarded'
        return 'mixed'
    d_ran = _safe_float(resource_snapshot.get('d_ran', 0.0), 0.0)
    d_ai = _safe_float(resource_snapshot.get('d_ai', 0.0), 0.0)
    if d_ran >= 0.75 and d_ran >= (d_ai + 0.10):
        return 'ran_camera'
    if d_ai >= 0.60 and d_ai > d_ran:
        return 'ai_guarded'
    return 'mixed'


def _load_scenario_control() -> dict:
    return _safe_read_json(ARTICLE00_SCENARIO_CONTROL_PATH)


def _share_of(value: float, total: float, default: float = 0.5) -> float:
    total = _safe_float(total, 0.0)
    if total <= 1e-9:
        return default
    return _clamp(_safe_float(value, 0.0) / total, 0.0, 1.0)


def _desired_ran_share(resource_snapshot: Dict[str, Any] | None, mean_embb: float, mean_mmtc: float, mean_urllc: float, embb_pressure: float, ai_pressure: float, priority: str) -> float:
    resource_snapshot = resource_snapshot or {}
    usable_budget = _safe_float(resource_snapshot.get('usable_budget', resource_snapshot.get('resource_budget', 1.0)), 1.0)
    current_r_ran = _safe_float(resource_snapshot.get('r_ran', 0.0), 0.0)
    d_ran = _safe_float(resource_snapshot.get('d_ran', 0.0), 0.0)
    d_ai = _safe_float(resource_snapshot.get('d_ai', 0.0), 0.0)
    live_share = _share_of(current_r_ran, usable_budget, default=0.5)
    demand_share = _share_of(d_ran, d_ran + d_ai, default=live_share)
    action_share = _clamp((0.78 * mean_embb) + (0.10 * embb_pressure) - (0.03 * mean_mmtc) - (0.05 * mean_urllc), 0.10, 0.98)
    ai_dominance = max(0.0, d_ai - d_ran)
    ran_dominance = max(0.0, d_ran - d_ai)

    if priority == 'ran_camera':
        share = (0.48 * live_share) + (0.30 * demand_share) + (0.22 * action_share)
        share += 0.05 * max(0.0, embb_pressure - 0.30)
        share += 0.03 * max(0.0, ran_dominance)
        lower, upper = 0.45, 0.94
    elif priority == 'ai_guarded':
        share = (0.42 * live_share) + (0.30 * demand_share) + (0.28 * action_share)
        share -= 0.12 * max(0.0, ai_pressure - 0.45)
        share -= 0.08 * ai_dominance
        lower, upper = 0.12, 0.74
    else:
        share = (0.50 * live_share) + (0.32 * demand_share) + (0.18 * action_share)
        share += 0.02 * max(0.0, embb_pressure - 0.30)
        share += 0.015 * max(0.0, ran_dominance)
        share -= 0.08 * max(0.0, ai_pressure - 0.25)
        share -= 0.05 * ai_dominance
        lower, upper = 0.22, 0.86
    return _clamp(share, lower, upper)


def _score_proxy(ran_completion: float, ai_completion: float, total_shortfall: float, total_surplus: float, budget_gap: float, priority: str = 'mixed') -> float:
    if priority == 'ran_camera':
        ran_weight, ai_weight = 0.55, 0.20
        shortfall_weight, surplus_weight, budget_weight = 0.17, 0.04, 0.04
    elif priority == 'ai_guarded':
        ran_weight, ai_weight = 0.30, 0.45
        shortfall_weight, surplus_weight, budget_weight = 0.15, 0.05, 0.05
    else:
        ran_weight, ai_weight = 0.40, 0.38
        shortfall_weight, surplus_weight, budget_weight = 0.14, 0.04, 0.04
    return (
        (ran_weight * _clamp(ran_completion))
        + (ai_weight * _clamp(ai_completion))
        - (shortfall_weight * max(total_shortfall, 0.0))
        - (surplus_weight * max(total_surplus, 0.0))
        - (budget_weight * max(budget_gap, 0.0))
    )


def build_shadow_comparison(resource_snapshot: Dict[str, Any] | None, marl_shadow: Dict[str, Any] | None) -> Dict[str, Any]:
    """Build a proxy comparison between live allocation and shadow allocation.

    This does not actuate the MARL policy. It estimates how the current shadow
    allocation would compare against the live allocator under the same observed
    demand snapshot.
    """
    resource_snapshot = resource_snapshot or {}
    marl_shadow = marl_shadow or {}
    if not marl_shadow or not marl_shadow.get('available'):
        return {}

    usable_budget = _safe_float(resource_snapshot.get('usable_budget', resource_snapshot.get('resource_budget', 1.0)), 1.0)
    d_ran = _safe_float(resource_snapshot.get('d_ran', 0.0), 0.0)
    d_ai = _safe_float(resource_snapshot.get('d_ai', 0.0), 0.0)

    live_r_ran = _safe_float(resource_snapshot.get('r_ran', 0.0), 0.0)
    live_r_ai = _safe_float(resource_snapshot.get('r_ai', 0.0), 0.0)
    shadow_r_ran = _safe_float(marl_shadow.get('shadow_r_ran', live_r_ran), live_r_ran)
    shadow_r_ai = _safe_float(marl_shadow.get('shadow_r_ai', live_r_ai), live_r_ai)

    live_ran_completion = _completion_estimate(live_r_ran, d_ran)
    live_ai_completion = _completion_estimate(live_r_ai, d_ai)
    shadow_ran_completion = _completion_estimate(shadow_r_ran, d_ran)
    shadow_ai_completion = _completion_estimate(shadow_r_ai, d_ai)

    live_shortfall = _shortfall(live_r_ran, d_ran) + _shortfall(live_r_ai, d_ai)
    shadow_shortfall = _shortfall(shadow_r_ran, d_ran) + _shortfall(shadow_r_ai, d_ai)
    live_surplus = _surplus(live_r_ran, d_ran) + _surplus(live_r_ai, d_ai)
    shadow_surplus = _surplus(shadow_r_ran, d_ran) + _surplus(shadow_r_ai, d_ai)
    live_budget_gap = abs((live_r_ran + live_r_ai) - usable_budget)
    shadow_budget_gap = abs((shadow_r_ran + shadow_r_ai) - usable_budget)

    priority = _scenario_priority(resource_snapshot, marl_shadow)
    live_score = _score_proxy(live_ran_completion, live_ai_completion, live_shortfall, live_surplus, live_budget_gap, priority=priority)
    shadow_score = _score_proxy(shadow_ran_completion, shadow_ai_completion, shadow_shortfall, shadow_surplus, shadow_budget_gap, priority=priority)
    score_delta = shadow_score - live_score

    recommend_shadow = bool(
        marl_shadow.get('source') in {'checkpoint', 'mixed'}
        and marl_shadow.get('checkpoint_readiness') in {'shadow_ready', 'control_candidate'}
        and score_delta > 0.01
    )

    return {
        'live_score': round(live_score, 6),
        'shadow_score': round(shadow_score, 6),
        'score_delta': round(score_delta, 6),
        'live_ran_completion_est': round(live_ran_completion, 4),
        'shadow_ran_completion_est': round(shadow_ran_completion, 4),
        'live_ai_completion_est': round(live_ai_completion, 4),
        'shadow_ai_completion_est': round(shadow_ai_completion, 4),
        'live_total_shortfall': round(live_shortfall, 4),
        'shadow_total_shortfall': round(shadow_shortfall, 4),
        'live_total_surplus': round(live_surplus, 4),
        'shadow_total_surplus': round(shadow_surplus, 4),
        'live_budget_gap': round(live_budget_gap, 4),
        'shadow_budget_gap': round(shadow_budget_gap, 4),
        'priority': priority,
        'recommend_shadow': recommend_shadow,
    }


class MARLShadowRuntimeEvaluator:
    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        config = config or {}
        self.enabled = _to_bool(
            os.environ.get('GREENRAN_TASAM_ADVISOR_ENABLED', os.environ.get('GREENRAN_MARL_SHADOW_ENABLE', config.get('enabled', True))),
            default=True,
        )
        self.policy_id = os.environ.get('GREENRAN_MARL_SHADOW_POLICY_ID', 'ta_sam_marl_shadow_v1')
        self.mode = 'shadow_only'
        self.advisory_mode = str(
            os.environ.get('GREENRAN_TASAM_ADVISOR_MODE', config.get('mode', 'shadow'))
        ).strip().lower() or 'shadow'
        # A control run must never silently replace a missing/incompatible
        # learned policy with the compatibility heuristic.  The explicit
        # checkpoint is also useful during bootstrap, before the controller
        # has published the first state-dir manifest.
        self.require_checkpoint = _to_bool(
            os.environ.get(
                'GREENRAN_TASAM_REQUIRE_CHECKPOINT',
                config.get('require_checkpoint', False),
            ),
            default=False,
        )
        self.checkpoint_override = str(
            os.environ.get(
                'GREENRAN_TASAM_CHECKPOINT',
                config.get('checkpoint', ''),
            ) or ''
        ).strip()
        self.checkpoint_readiness_override = str(
            os.environ.get(
                'GREENRAN_TASAM_CHECKPOINT_READINESS',
                config.get('checkpoint_readiness', 'control_candidate'),
            ) or 'control_candidate'
        ).strip().lower()
        self.min_confidence = _clamp(
            os.environ.get('GREENRAN_TASAM_MIN_CONFIDENCE', config.get('min_confidence', 0.70)),
            0.0,
            1.0,
        )
        self.stability_window = max(
            1,
            _safe_int(os.environ.get('GREENRAN_TASAM_STABILITY_WINDOW', config.get('stability_window', 2)), 2),
        )
        self.enable_resource_advice = _to_bool(
            os.environ.get('GREENRAN_TASAM_RESOURCE_ADVICE', config.get('enable_resource_advice', True)),
            default=True,
        )
        self.enable_energy_advice = _to_bool(
            os.environ.get('GREENRAN_TASAM_ENERGY_ADVICE', config.get('enable_energy_advice', True)),
            default=True,
        )
        self.project_root = Path(__file__).resolve().parent.parent
        self.eval_manifest_path = Path(
            os.environ.get(
                'GREENRAN_TASAM_EVAL_MANIFEST',
                self.project_root / 'runs' / 'tasam_greenran_real' / 'tasam_candidate_evaluation_latest.json',
            )
        )
        self.control_gate_manifest_path = Path(
            os.environ.get(
                'GREENRAN_MARL_CONTROL_GATE_MANIFEST',
                self.project_root / 'runs' / 'tasam_greenran_real' / 'marl_control_gate_latest.json',
            )
        )
        self.checkpoint_source = 'heuristic'
        self.checkpoint_run_dir = ''
        self.checkpoint_readiness = 'unknown'
        self.checkpoint_meta: Dict[str, Any] = {}
        self._checkpoint_manifest_signature = self._path_signature(self.eval_manifest_path)
        self._active_policy_id_base = self.policy_id
        self._torch = None
        self._actors = None
        self._checkpoint_error = ''
        self._recommendation_history: deque[str] = deque(maxlen=self.stability_window)
        if self.enabled:
            self._try_load_checkpoint(bootstrap=True)

    @property
    def checkpoint_loaded(self) -> bool:
        """Whether all active DU actors came from a loaded checkpoint."""
        return self._actors is not None and self._torch is not None

    @property
    def checkpoint_error(self) -> str:
        return self._checkpoint_error

    def _load_json(self, path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _load_control_gate(self) -> dict:
        payload = self._load_json(self.control_gate_manifest_path)
        gate = (payload or {}).get('gate') or {}
        return gate if isinstance(gate, dict) else {}

    @staticmethod
    def _path_signature(path: Path) -> tuple[int, int] | None:
        """Return a cheap signature for atomic manifest hot-reload checks."""
        try:
            stat = path.stat()
        except OSError:
            return None
        return (int(stat.st_mtime_ns), int(stat.st_size))

    def _reload_checkpoint_if_changed(self) -> None:
        """Reload a newly promoted candidate without interrupting the rApp.

        Promotion manifests are written atomically by the online controller.
        A malformed or incompatible candidate is rejected and the last active
        checkpoint remains in service.
        """
        manifest_path = getattr(self, 'eval_manifest_path', None)
        if not manifest_path:
            return
        signature = self._path_signature(manifest_path)
        if signature == getattr(self, '_checkpoint_manifest_signature', None):
            return

        previous = {
            'checkpoint_source': getattr(self, 'checkpoint_source', 'heuristic'),
            'checkpoint_run_dir': getattr(self, 'checkpoint_run_dir', ''),
            'checkpoint_readiness': getattr(self, 'checkpoint_readiness', 'unknown'),
            'checkpoint_meta': getattr(self, 'checkpoint_meta', {}),
            'torch': getattr(self, '_torch', None),
            'actors': getattr(self, '_actors', None),
            'policy_id': getattr(self, 'policy_id', 'ta_sam_marl_shadow_v1'),
            'checkpoint_error': getattr(self, '_checkpoint_error', ''),
        }
        self._try_load_checkpoint(bootstrap=False)
        if self._actors is None:
            self.checkpoint_source = previous['checkpoint_source']
            self.checkpoint_run_dir = previous['checkpoint_run_dir']
            self.checkpoint_readiness = previous['checkpoint_readiness']
            self.checkpoint_meta = previous['checkpoint_meta']
            self._torch = previous['torch']
            self._actors = previous['actors']
            self.policy_id = previous['policy_id']
            self._checkpoint_error = previous['checkpoint_error']
            self._checkpoint_error = f'candidate rejected; active checkpoint kept: {self._checkpoint_error}'
            self._checkpoint_manifest_signature = signature
            return
        self._checkpoint_manifest_signature = signature


    def _ensure_local_torch_importable(self) -> None:
        venv_site = self.project_root / 'drlexp' / '.venv' / 'lib'
        if not venv_site.exists():
            return
        py_tag = f"python{sys.version_info.major}.{sys.version_info.minor}"
        for child in sorted(venv_site.glob(f'{py_tag}/site-packages')):
            candidate = child / 'torch'
            if candidate.exists():
                site_path = str(child)
                if site_path not in sys.path:
                    sys.path.insert(0, site_path)
                return

    def _try_load_checkpoint(self, *, bootstrap: bool = False) -> None:
        manifest = self._load_json(self.eval_manifest_path)
        best = (manifest or {}).get('best_run') or {}
        override_dir = Path(self.checkpoint_override).expanduser() if self.checkpoint_override else None

        # On the first load, an explicit checkpoint is authoritative.  This
        # makes startup deterministic even when a stale manifest from another
        # campaign is present.  Later hot-reloads use the atomically published
        # manifest so online candidates can replace the bootstrap policy.
        use_override = bool(bootstrap and override_dir)
        if use_override:
            self.checkpoint_readiness = self.checkpoint_readiness_override
            self.checkpoint_run_dir = str(override_dir)
        else:
            if not best:
                if override_dir:
                    self.checkpoint_readiness = self.checkpoint_readiness_override
                    self.checkpoint_run_dir = str(override_dir)
                else:
                    self._checkpoint_error = 'best_run missing from evaluation manifest'
                    return
            else:
                self.checkpoint_readiness = str(best.get('readiness', 'unknown') or 'unknown')
                self.checkpoint_run_dir = str(best.get('run_dir', '') or '')
                if not bool(best.get('promote_shadow', False)):
                    if override_dir:
                        self.checkpoint_readiness = self.checkpoint_readiness_override
                        self.checkpoint_run_dir = str(override_dir)
                    else:
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

        hidden_dims = self.checkpoint_meta.get('actor_hidden_dims') or [64, 64]
        if not isinstance(hidden_dims, list):
            hidden_dims = [64, 64]
        hidden_dims = [max(1, _safe_int(dim, 64)) for dim in hidden_dims if _safe_int(dim, 0) > 0] or [64, 64]
        activation_name = str(self.checkpoint_meta.get('activation', 'relu') or 'relu').strip().lower()

        def _activation_factory():
            if activation_name == 'tanh':
                return nn.Tanh()
            if activation_name == 'gelu':
                return nn.GELU()
            if activation_name == 'elu':
                return nn.ELU()
            if activation_name == 'leaky_relu':
                return nn.LeakyReLU()
            return nn.ReLU()

        action_layout = self.checkpoint_meta.get('action_layout') or ['eMBB', 'mMTC', 'URLLC']
        action_dim = max(1, len(action_layout) if isinstance(action_layout, list) else 3)

        class _LegacyActorNetwork(nn.Module):
            def __init__(self, input_dim: int, hidden_dims: List[int], action_dim: int = 3) -> None:
                super().__init__()
                layers: List[nn.Module] = []
                prev_dim = input_dim
                for hidden_dim in hidden_dims:
                    layers.append(nn.Linear(prev_dim, hidden_dim))
                    layers.append(_activation_factory())
                    prev_dim = hidden_dim
                layers.append(nn.Linear(prev_dim, action_dim))
                layers.append(nn.Sigmoid())
                self.net = nn.Sequential(*layers)

            def forward(self, x):
                out = self.net(x)
                total = torch.clamp(out.sum(dim=-1, keepdim=True), min=1e-9)
                return out / total

        state_dict = torch.load(ckpt_path, map_location='cpu')

        class _ArticleDirichletActor(nn.Module):
            def __init__(self, input_dim: int, hidden_dims: List[int], action_dim: int = 3) -> None:
                super().__init__()
                layers: List[nn.Module] = []
                prev_dim = input_dim
                for hidden_dim in hidden_dims:
                    layers.append(nn.Linear(prev_dim, hidden_dim))
                    layers.append(_activation_factory())
                    prev_dim = hidden_dim
                self.backbone = nn.Sequential(*layers)
                self.concentration_head = nn.Linear(prev_dim, action_dim)

            def forward(self, x):
                raw = self.concentration_head(self.backbone(x))
                alpha = torch.nn.functional.softplus(raw) + 1e-3
                total = torch.clamp(alpha.sum(dim=-1, keepdim=True), min=1e-9)
                return alpha / total

        state_keys = list(state_dict.keys()) if isinstance(state_dict, dict) else []
        article_style_state = any('.concentration_head.' in key or '.backbone.' in key for key in state_keys)
        actor_cls = _ArticleDirichletActor if article_style_state else _LegacyActorNetwork
        actors = nn.ModuleList([actor_cls(du_state_dim, hidden_dims=hidden_dims, action_dim=action_dim) for _ in range(du_count)])
        try:
            actors.load_state_dict(state_dict)
        except RuntimeError as exc:
            self._checkpoint_error = f'checkpoint load failed: {exc}'
            return
        actors.eval()
        self._torch = torch
        self._actors = actors
        self.policy_id = f"{self._active_policy_id_base}:{run_dir.name}"
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

    def _resource_advice(self, priority: str, resource_snapshot: Dict[str, Any], comparison: Dict[str, Any], shadow_r_ran: float, shadow_r_ai: float) -> Dict[str, Any]:
        current_r_ran = _safe_float(resource_snapshot.get('r_ran', 0.0), 0.0)
        current_r_ai = _safe_float(resource_snapshot.get('r_ai', 0.0), 0.0)
        delta_r_ran = shadow_r_ran - current_r_ran
        delta_r_ai = shadow_r_ai - current_r_ai

        if not self.enable_resource_advice:
            return {'enabled': False}

        if delta_r_ran > 0.02:
            recommendation = 'shift_to_ran'
        elif delta_r_ran < -0.02:
            recommendation = 'shift_to_ai'
        else:
            recommendation = 'hold'

        return {
            'enabled': True,
            'priority': priority,
            'recommendation': recommendation,
            'suggested_r_ran': round(shadow_r_ran, 4),
            'suggested_r_ai': round(shadow_r_ai, 4),
            'delta_r_ran_vs_live': round(delta_r_ran, 4),
            'delta_r_ai_vs_live': round(delta_r_ai, 4),
            'score_delta': round(_safe_float(comparison.get('score_delta', 0.0), 0.0), 6),
        }

    def _energy_advice(self, priority: str, comparison: Dict[str, Any], shadow_r_ran: float, live_r_ran: float) -> Dict[str, Any]:
        if not self.enable_energy_advice:
            return {'enabled': False}

        score_delta = _safe_float(comparison.get('score_delta', 0.0), 0.0)
        live_shortfall = _safe_float(comparison.get('live_total_shortfall', 0.0), 0.0)
        shadow_shortfall = _safe_float(comparison.get('shadow_total_shortfall', 0.0), 0.0)
        live_ran_completion = _safe_float(comparison.get('live_ran_completion_est', 0.0), 0.0)
        shadow_ran_completion = _safe_float(comparison.get('shadow_ran_completion_est', 0.0), 0.0)
        delta_r_ran = shadow_r_ran - live_r_ran

        decision = 'CONDITIONAL'
        action = 'MONITOR'
        reason = 'shadow comparison inconclusive'

        if score_delta > 0.02 and shadow_shortfall <= live_shortfall:
            if priority in {'ran_camera', 'ai_guarded'} and abs(delta_r_ran) >= 0.03:
                decision = 'CONDITIONAL'
                action = 'FULL_POWER_GUARD'
                reason = f'shadow keeps protected allocation for priority={priority}'
            elif shadow_ran_completion >= live_ran_completion:
                decision = 'ALLOWED'
                action = 'REDUCE_POWER'
                reason = 'shadow preserves service completion with better score proxy'
        elif score_delta < -0.01:
            decision = 'BLOCKED'
            action = 'FULL_POWER'
            reason = 'shadow candidate degrades score proxy'

        return {
            'enabled': True,
            'decision': decision,
            'action': action,
            'reason': reason,
            'score_delta': round(score_delta, 6),
        }

    def _apply_global_ran_guard(self, ran_share: float, resource_snapshot: Dict[str, Any] | None, priority: str, embb_pressure: float = 0.0) -> tuple[float, Dict[str, Any]]:
        """Keep at least 48% of the shared budget available to AI workloads."""
        enabled = bool(getattr(self, 'global_ran_guard_enabled', False))
        ai_floor = _clamp(os.environ.get('GREENRAN_TASAM_AI_FLOOR_SHARE', 0.48), 0.0, 1.0)
        max_ran = _clamp(os.environ.get('GREENRAN_TASAM_RAN_GUARD_MAX_SHARE', 1.0 - ai_floor), 0.0, 1.0)
        info = {
            'enabled': enabled,
            'applied': False,
            'priority': priority,
            'ai_floor_share': ai_floor,
            'max_ran_share': max_ran,
            'embb_pressure': _safe_float(embb_pressure),
        }
        if enabled and priority == 'ai_guarded':
            guarded = min(_clamp(ran_share), max_ran)
            info['applied'] = guarded < _clamp(ran_share)
            info['reason'] = 'ai_floor_48_percent'
            return guarded, info
        return _clamp(ran_share), info

    def _limit_ran_share_step(self, ran_share: float, resource_snapshot: Dict[str, Any] | None, priority: str) -> tuple[float, Dict[str, Any]]:
        """Bound allocation changes to avoid a one-cycle floor/rollback spike."""
        resource_snapshot = resource_snapshot or {}
        current = _share_of(
            _safe_float(resource_snapshot.get('r_ran', 0.0)),
            _safe_float(resource_snapshot.get('usable_budget', resource_snapshot.get('resource_budget', 1.0)), 1.0),
            default=0.5,
        )
        maximum_step = _clamp(getattr(self, 'max_ran_share_step', 0.25), 0.0, 1.0)
        bounded = max(current - maximum_step, min(current + maximum_step, _clamp(ran_share)))
        return bounded, {'applied': abs(bounded - _clamp(ran_share)) > 1e-9, 'max_step': maximum_step, 'priority': priority}

    def _advisor_confidence(self, source: str, comparison: Dict[str, Any], stable_recommendation: bool) -> float:
        source_score = {
            'checkpoint': 0.92,
            'mixed': 0.78,
            'heuristic': 0.52,
        }.get(source, 0.45)
        readiness_score = {
            'control_candidate': 0.96,
            'shadow_ready': 0.88,
            'not_ready': 0.40,
            'unknown': 0.45,
        }.get(self.checkpoint_readiness, 0.45)
        delta_score = _clamp(max(_safe_float(comparison.get('score_delta', 0.0), 0.0), 0.0) / 0.08, 0.0, 1.0)
        stability_score = 1.0 if stable_recommendation else 0.45
        return _clamp(
            (0.35 * source_score)
            + (0.25 * readiness_score)
            + (0.25 * delta_score)
            + (0.15 * stability_score),
            0.0,
            1.0,
        )

    def _recommendation_signature(self, priority: str, resource_advice: Dict[str, Any], energy_advice: Dict[str, Any]) -> str:
        return ':'.join(
            [
                priority,
                str(resource_advice.get('recommendation', 'hold')),
                str(round(_safe_float(resource_advice.get('suggested_r_ran', 0.0), 0.0), 3)),
                str(energy_advice.get('decision', 'CONDITIONAL')),
                str(energy_advice.get('action', 'MONITOR')),
            ]
        )

    def _stability_state(self, signature: str) -> Dict[str, Any]:
        self._recommendation_history.append(signature)
        stable = len(self._recommendation_history) >= self.stability_window and len(set(self._recommendation_history)) == 1
        return {
            'window': self.stability_window,
            'samples': len(self._recommendation_history),
            'stable': stable,
        }

    def apply_armd_policy_envelope(
        self,
        marl_shadow: Dict[str, Any] | None,
        armd_proposal: Dict[str, Any] | None,
        resource_snapshot: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Constrain the TA-SAM proposal with the current ARMD policy.

        ARMD remains the safety/policy authority. TA-SAM can optimize the
        remaining shared budget and learn from the resulting reward, but it
        cannot relax ARMD's per-service floors or a critical veto. Keeping
        this step explicit also makes the hierarchy auditable in SQLite.
        """
        result = dict(marl_shadow or {})
        advisor = dict(result.get('advisor') or {})
        proposal = armd_proposal if isinstance(armd_proposal, dict) else {}
        allocation = proposal.get('resource_allocation') or {}
        snapshot = resource_snapshot or {}
        envelope = {
            'applied': False,
            'authority': 'ARMD-GreenRAN',
            'policy_id': proposal.get('proposal_id', ''),
            'verdict': str(proposal.get('verdict', '') or ''),
            'safety_veto': bool(proposal.get('safety_veto', False)),
            'reason': 'ARMD proposal unavailable',
        }
        if not proposal or not proposal.get('available') or not proposal.get('valid'):
            result['armd_policy_envelope'] = envelope
            advisor['armd_policy_envelope'] = envelope
            result['advisor'] = advisor
            return result

        budget = _safe_float(
            allocation.get('usable_budget', snapshot.get('usable_budget', snapshot.get('resource_budget', 1.0))),
            1.0,
        )
        floor_ran = max(0.0, _safe_float(allocation.get('floor_total_ran', 0.0), 0.0))
        floor_ai = max(0.0, _safe_float(allocation.get('floor_total_ai', 0.0), 0.0))
        requested_ran = _safe_float(result.get('shadow_r_ran', snapshot.get('r_ran', 0.0)), 0.0)
        requested_ai = _safe_float(result.get('shadow_r_ai', snapshot.get('r_ai', 0.0)), 0.0)
        hard_veto = bool(
            proposal.get('safety_veto')
            or proposal.get('critical_violation')
            or str(proposal.get('verdict', '')).upper() == 'BLOCKED'
        )

        if hard_veto:
            bounded_ran = _safe_float(allocation.get('r_ran', floor_ran), floor_ran)
            bounded_ai = _safe_float(allocation.get('r_ai', floor_ai), floor_ai)
            envelope['reason'] = 'ARMD critical policy/veto fixes the protected allocation'
        else:
            bounded_ran = max(requested_ran, floor_ran)
            bounded_ai = max(requested_ai, floor_ai)
            envelope['reason'] = 'TA-SAM optimized inside ARMD floors'

        if budget > 0.0 and bounded_ran + bounded_ai > budget:
            # Preserve the ARMD floors first, then trim only the surplus
            # introduced by TA-SAM. A malformed envelope is normalized rather
            # than silently allowing the shared budget to be exceeded.
            floor_total = floor_ran + floor_ai
            if floor_total >= budget:
                scale = budget / floor_total if floor_total > 0.0 else 0.0
                bounded_ran, bounded_ai = floor_ran * scale, floor_ai * scale
                envelope['reason'] += '; ARMD floors normalized to budget'
            else:
                surplus = (bounded_ran + bounded_ai) - budget
                ran_extra = max(0.0, bounded_ran - floor_ran)
                ai_extra = max(0.0, bounded_ai - floor_ai)
                extra_total = ran_extra + ai_extra
                if extra_total > 0.0:
                    bounded_ran -= surplus * ran_extra / extra_total
                    bounded_ai -= surplus * ai_extra / extra_total

        bounded_ran = max(0.0, bounded_ran)
        bounded_ai = max(0.0, bounded_ai)
        result['shadow_r_ran'] = round(bounded_ran, 4)
        result['shadow_r_ai'] = round(bounded_ai, 4)
        result['delta_r_ran_vs_live'] = round(bounded_ran - _safe_float(snapshot.get('r_ran', 0.0), 0.0), 4)
        result['delta_r_ai_vs_live'] = round(bounded_ai - _safe_float(snapshot.get('r_ai', 0.0), 0.0), 4)
        comparison = build_shadow_comparison(
            snapshot,
            dict(result, shadow_r_ran=bounded_ran, shadow_r_ai=bounded_ai),
        )
        result['comparison'] = comparison
        resource_advice = dict(advisor.get('resource_advice') or {})
        resource_advice.update({
            'suggested_r_ran': round(bounded_ran, 4),
            'suggested_r_ai': round(bounded_ai, 4),
            'delta_r_ran_vs_live': result['delta_r_ran_vs_live'],
            'delta_r_ai_vs_live': result['delta_r_ai_vs_live'],
            'armd_envelope_applied': True,
        })
        advisor['resource_advice'] = resource_advice
        if hard_veto:
            advisor['energy_advice'] = {
                'enabled': True,
                'decision': 'BLOCKED',
                'action': 'FULL_POWER',
                'reason': 'ARMD critical policy overrides TA-SAM reduction',
                'score_delta': round(_safe_float(comparison.get('score_delta', 0.0), 0.0), 6),
            }
        envelope.update({
            'applied': True,
            'floor_total_ran': round(floor_ran, 4),
            'floor_total_ai': round(floor_ai, 4),
            'bounded_r_ran': round(bounded_ran, 4),
            'bounded_r_ai': round(bounded_ai, 4),
            'reason': envelope['reason'],
        })
        result['armd_policy_envelope'] = envelope
        advisor['armd_policy_envelope'] = envelope
        advisor['reason'] = envelope['reason']
        result['advisor'] = advisor
        return result

    def evaluate(self, marl_state: Dict[str, Any] | None, resource_snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if self.enabled:
            self._reload_checkpoint_if_changed()
        if not self.enabled:
            return {'enabled': False, 'policy_id': self.policy_id, 'mode': self.mode, 'advisory_mode': self.advisory_mode}
        marl_state = marl_state or {}
        resource_snapshot = resource_snapshot or {}
        scenario_control = _load_scenario_control()
        du_states = marl_state.get('du_states', []) or []
        slice_state = marl_state.get('slice_state', {}) or {}
        if not du_states:
            return {
                'enabled': True,
                'policy_id': self.policy_id,
                'mode': self.mode,
                'advisory_mode': self.advisory_mode,
                'available': False,
                'source': self.checkpoint_source,
                'checkpoint_readiness': self.checkpoint_readiness,
                'checkpoint_error': self._checkpoint_error,
                'control_gate': self._load_control_gate(),
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
        if getattr(self, 'require_checkpoint', False) and used_checkpoint != len(du_recommendations):
            # Preserve the audit trail, but make the control path unavailable;
            # a control run must not actuate a partial heuristic policy.
            return {
                'enabled': True,
                'available': False,
                'valid': False,
                'would_influence': False,
                'policy_id': self.policy_id,
                'mode': self.mode,
                'advisory_mode': self.advisory_mode,
                'source': 'unavailable',
                'checkpoint_readiness': self.checkpoint_readiness,
                'checkpoint_run_dir': self.checkpoint_run_dir,
                'checkpoint_error': (
                    self._checkpoint_error
                    or 'required checkpoint could not produce actions for every DU'
                ),
                'required_checkpoint': True,
                'du_count': len(du_recommendations),
                'du_recommendations': du_recommendations,
                'control_gate': self._load_control_gate(),
            }
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
        replay_stage = str(marl_state.get('scenario_stage') or marl_state.get('collection_event_stage_name') or '').strip()
        live_stage = str((scenario_control or {}).get('collection_event_stage_name') or (scenario_control or {}).get('scenario') or '').strip()
        scenario_stage = replay_stage or live_stage
        priority = _scenario_priority(resource_snapshot, {'scenario_stage': scenario_stage})
        if hasattr(self, '_smooth_p95_tail_pressure'):
            resource_snapshot, tail_info = self._smooth_p95_tail_pressure(resource_snapshot)
        else:
            tail_info = {}
        shadow_ran_share = _desired_ran_share(
            resource_snapshot,
            mean_embb=mean_embb,
            mean_mmtc=mean_mmtc,
            mean_urllc=mean_urllc,
            embb_pressure=embb_pressure,
            ai_pressure=ai_pressure,
            priority=priority,
        )
        shadow_ran_share, global_guard = self._apply_global_ran_guard(shadow_ran_share, resource_snapshot, priority, embb_pressure)
        shadow_ran_share, step_guard = self._limit_ran_share_step(shadow_ran_share, resource_snapshot, priority)
        shadow_r_ran = usable_budget * shadow_ran_share
        shadow_r_ai = max(0.0, usable_budget - shadow_r_ran)
        final_source = 'checkpoint' if used_checkpoint == len(du_recommendations) and du_recommendations else 'heuristic'
        if used_checkpoint and used_checkpoint < len(du_recommendations):
            final_source = 'mixed'

        result = {
            'enabled': True,
            'available': True,
            'policy_id': self.policy_id,
            'mode': self.mode,
            'advisory_mode': self.advisory_mode,
            'source': final_source,
            'checkpoint_readiness': self.checkpoint_readiness,
            'checkpoint_run_dir': self.checkpoint_run_dir,
            'checkpoint_error': self._checkpoint_error,
            'control_gate': self._load_control_gate(),
            'scenario_stage': scenario_stage,
            'topology_id': marl_state.get('topology_id', 'unknown'),
            'du_count': len(du_recommendations),
            'du_recommendations': du_recommendations,
            'shadow_r_ran': round(shadow_r_ran, 4),
            'shadow_r_ai': round(shadow_r_ai, 4),
            'delta_r_ran_vs_live': round(shadow_r_ran - current_r_ran, 4),
            'delta_r_ai_vs_live': round(shadow_r_ai - current_r_ai, 4),
            'mean_action_vector': [round(mean_embb, 4), round(mean_mmtc, 4), round(mean_urllc, 4)],
            'priority': priority,
            'guard': {'global_ran': global_guard, 'step': step_guard, 'p95_tail': tail_info},
        }
        result['comparison'] = build_shadow_comparison(resource_snapshot, result)
        resource_advice = self._resource_advice(priority, resource_snapshot, result['comparison'], shadow_r_ran, shadow_r_ai)
        energy_advice = self._energy_advice(priority, result['comparison'], shadow_r_ran, current_r_ran)
        stability = self._stability_state(self._recommendation_signature(priority, resource_advice, energy_advice))
        confidence = self._advisor_confidence(final_source, result['comparison'], stability['stable'])
        evidence_flags = {
            'checkpoint_ready': self.checkpoint_readiness in {'shadow_ready', 'control_candidate'},
            'checkpoint_backed': final_source in {'checkpoint', 'mixed'},
            'positive_score_delta': (
                _safe_float(result['comparison'].get('score_delta', 0.0), 0.0) > 0.0
                or self.advisory_mode in {'assistant_only_control', 'control', 'integration'}
            ),
            'stable_recommendation': stability['stable'],
            'resource_budget_respected': abs((shadow_r_ran + shadow_r_ai) - usable_budget) <= 1e-6,
        }
        gate_passed = all(
            (
                evidence_flags['checkpoint_ready'],
                evidence_flags['checkpoint_backed'],
                evidence_flags['positive_score_delta'],
                evidence_flags['stable_recommendation'],
                confidence >= self.min_confidence,
            )
        )
        advisor = {
            'enabled': True,
            'mode': self.advisory_mode,
            'policy_id': self.policy_id,
            'source': final_source,
            'confidence': round(confidence, 4),
            'min_confidence': round(self.min_confidence, 4),
            'stability_window': self.stability_window,
            'valid': gate_passed,
            'gate_passed': gate_passed,
            'would_influence': gate_passed and self.advisory_mode not in {'shadow', 'shadow_only'},
            'resource_advice': resource_advice,
            'energy_advice': energy_advice,
            'du_contributions': [
                {
                    'du_id': item.get('du_id', 'unknown'),
                    'primary_slice': item.get('primary_slice', 'unknown'),
                    'source': item.get('source', 'unknown'),
                    'dominant_slice': ['eMBB', 'mMTC', 'URLLC'][max(range(3), key=lambda idx: item.get('action_vector', [0.0, 0.0, 0.0])[idx])],
                    'dominant_share': max(item.get('action_vector', [0.0, 0.0, 0.0])),
                    'action_vector': item.get('action_vector', []),
                }
                for item in du_recommendations
            ],
            'stability': stability,
            'evidence_flags': evidence_flags,
            'arbitration_score': round(
                (0.65 * confidence) + (0.35 * _clamp(max(_safe_float(result['comparison'].get('score_delta', 0.0), 0.0), 0.0) / 0.08, 0.0, 1.0)),
                4,
            ),
            'reason': energy_advice.get('reason', ''),
        }
        result['confidence'] = advisor['confidence']
        result['valid'] = advisor['valid']
        result['would_influence'] = advisor['would_influence']
        result['advisor'] = advisor
        return result
