"""Article-style offline SAC-MARL backend for TA-SAM ablations."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn

SLICE_ORDER = ('eMBB', 'mMTC', 'URLLC')
CATEGORY_ORDER = ('ALLOWED', 'CONDITIONAL', 'BLOCKED')
CATEGORY_TO_INDEX = {name: index for index, name in enumerate(CATEGORY_ORDER)}
# Hardware-safe RF levels: all cells remain on, with linear power commands.
POWER_LEVELS = tuple(float(value) for value in range(25, 101, 5))
V10_POWER_LEVELS = (0.0, *POWER_LEVELS)
TEMPORAL_FEATURE_DIM = 10


def category_index(value: object) -> int:
    return CATEGORY_TO_INDEX.get(str(value or '').strip().upper(), -1)


def category_name(index: int) -> str:
    return CATEGORY_ORDER[int(index)] if 0 <= int(index) < len(CATEGORY_ORDER) else 'UNKNOWN'


@dataclass
class MARLTransitionRecord:
    global_state: list[float]
    du_states: list[list[float]]
    behavior_actions: list[list[float]]
    reward: float
    next_global_state: list[float]
    next_du_states: list[list[float]]
    done: bool
    category_target: int = -1
    category_weight: float = 0.0
    category_training_credit: float = 0.0
    temporal_context: list[float] | None = None
    power_target: int = -1
    allocation_target: list[float] | None = None
    allocation_weight: float = 0.0
    allocation_target_metadata: dict[str, object] | None = None
    # The SAC critic must see the action that reached the simulator.  Legacy
    # traces did not persist that distinction and keep their historical
    # derived action through the defaults below.
    behavior_global_action: list[float] | None = None
    economic_weight: float = 1.0


@dataclass
class TransitionTensorDataset:
    global_states: torch.Tensor
    du_states: torch.Tensor
    behavior_actions: torch.Tensor
    rewards: torch.Tensor
    next_global_states: torch.Tensor
    next_du_states: torch.Tensor
    dones: torch.Tensor
    category_targets: torch.Tensor
    category_weights: torch.Tensor
    temporal_contexts: torch.Tensor
    power_targets: torch.Tensor
    allocation_targets: torch.Tensor
    allocation_weights: torch.Tensor
    behavior_global_actions: torch.Tensor
    economic_weights: torch.Tensor

    def __len__(self) -> int:
        return int(self.rewards.shape[0])


class DirichletActor(nn.Module):
    def __init__(
        self,
        input_dim: int,
        hidden_dims: Sequence[int] = (300, 400, 400),
        action_dim: int = 3,
        activation: str = 'tanh',
    ) -> None:
        super().__init__()
        act_cls = nn.Tanh if activation == 'tanh' else nn.ReLU
        layers: list[nn.Module] = []
        prev_dim = input_dim
        for hidden_dim in hidden_dims:
            layers.extend([nn.Linear(prev_dim, int(hidden_dim)), act_cls()])
            prev_dim = int(hidden_dim)
        self.backbone = nn.Sequential(*layers)
        self.concentration_head = nn.Linear(prev_dim, action_dim)

    def concentration(self, states: torch.Tensor) -> torch.Tensor:
        raw = self.concentration_head(self.backbone(states))
        return F.softplus(raw) + 1e-3

    def forward(self, states: torch.Tensor) -> torch.Tensor:
        alpha = self.concentration(states)
        return alpha / torch.clamp(alpha.sum(dim=-1, keepdim=True), min=1e-9)

    def sample(self, states: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        alpha = self.concentration(states)
        dist = torch.distributions.Dirichlet(alpha)
        action = dist.rsample()
        log_prob = dist.log_prob(action).unsqueeze(-1)
        mean_action = alpha / torch.clamp(alpha.sum(dim=-1, keepdim=True), min=1e-9)
        return action, log_prob, mean_action

    def deterministic(self, states: torch.Tensor) -> torch.Tensor:
        return self(states)

    def entropy(self, states: torch.Tensor) -> torch.Tensor:
        alpha = self.concentration(states)
        return torch.distributions.Dirichlet(alpha).entropy().unsqueeze(-1)


class GlobalBudgetActor(nn.Module):
    """Global SAC actor for independent radio, compute and I/O budgets."""

    def __init__(self, input_dim: int, hidden_dims: Sequence[int] = (300, 400, 400),
                 action_dim: int = 3, activation: str = 'tanh') -> None:
        super().__init__()
        act_cls = nn.Tanh if activation == 'tanh' else nn.ReLU
        layers: list[nn.Module] = []
        previous = input_dim
        for hidden in hidden_dims:
            layers.extend([nn.Linear(previous, int(hidden)), act_cls()])
            previous = int(hidden)
        self.backbone = nn.Sequential(*layers)
        self.alpha_head = nn.Linear(previous, action_dim)
        self.beta_head = nn.Linear(previous, action_dim)

    def parameters_ab(self, states: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        features = self.backbone(states)
        return F.softplus(self.alpha_head(features)) + 1.0, F.softplus(self.beta_head(features)) + 1.0

    def sample(self, states: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        alpha, beta = self.parameters_ab(states)
        distribution = torch.distributions.Beta(alpha, beta)
        action = distribution.rsample()
        return action, distribution.log_prob(action).sum(dim=-1, keepdim=True), alpha / (alpha + beta)

    def deterministic(self, states: torch.Tensor) -> torch.Tensor:
        alpha, beta = self.parameters_ab(states)
        return alpha / (alpha + beta)

    def entropy(self, states: torch.Tensor) -> torch.Tensor:
        alpha, beta = self.parameters_ab(states)
        return torch.distributions.Beta(alpha, beta).entropy().sum(dim=-1, keepdim=True)


class OrdinalCategoryHead(nn.Module):
    """Predict the observed operating category from the 13-D global state."""

    def __init__(self, input_dim: int, hidden_dim: int = 64, activation: str = 'tanh', temporal_dim: int = 0) -> None:
        super().__init__()
        act_cls = nn.Tanh if activation == 'tanh' else nn.ReLU
        self.temporal_dim = max(0, int(temporal_dim))
        self.temporal_encoder = (
            nn.Sequential(
                nn.Linear(self.temporal_dim, int(hidden_dim)),
                act_cls(),
                nn.Linear(int(hidden_dim), len(CATEGORY_ORDER)),
            )
            if self.temporal_dim > 0 else None
        )
        self.net = nn.Sequential(
            nn.Linear(int(input_dim), int(hidden_dim)),
            act_cls(),
            nn.Linear(int(hidden_dim), len(CATEGORY_ORDER)),
        )

    def forward(self, states: torch.Tensor, temporal_context: torch.Tensor | None = None) -> torch.Tensor:
        logits = self.net(states)
        # The canonical 13-D contract exposes the current hard-SLA category
        # as its final one-hot triplet.  Preserve that deterministic prior;
        # the learned/temporal residual predicts transitions away from it.
        if states.shape[-1] >= len(CATEGORY_ORDER):
            logits = logits + states[..., -len(CATEGORY_ORDER):]
        if self.temporal_dim > 0:
            if temporal_context is None:
                temporal_context = torch.zeros((states.shape[0], self.temporal_dim), dtype=states.dtype, device=states.device)
            temporal = self.temporal_encoder(temporal_context[..., :self.temporal_dim])
            return logits + temporal
        return logits


class PowerIntentHead(nn.Module):
    """Predict the safe discrete power intent without changing the 13-D API."""

    def __init__(self, input_dim: int, hidden_dim: int = 64, activation: str = 'tanh', temporal_dim: int = 0) -> None:
        super().__init__()
        act_cls = nn.Tanh if activation == 'tanh' else nn.ReLU
        self.temporal_dim = max(0, int(temporal_dim))
        self.temporal_encoder = (
            nn.Sequential(nn.Linear(self.temporal_dim, int(hidden_dim)), act_cls())
            if self.temporal_dim > 0 else None
        )
        head_input_dim = int(input_dim) + (int(hidden_dim) if self.temporal_dim > 0 else 0)
        self.net = nn.Sequential(
            nn.Linear(head_input_dim, int(hidden_dim)),
            act_cls(),
            nn.Linear(int(hidden_dim), len(POWER_LEVELS)),
        )

    def forward(self, states: torch.Tensor, temporal_context: torch.Tensor | None = None) -> torch.Tensor:
        if self.temporal_dim > 0:
            if temporal_context is None:
                temporal_context = torch.zeros((states.shape[0], self.temporal_dim), dtype=states.dtype, device=states.device)
            states = torch.cat([states, self.temporal_encoder(temporal_context[..., :self.temporal_dim])], dim=-1)
        return self.net(states)


class AllocationIntentHead(nn.Module):
    """Predict RAN/AI split and total budget utilization.

    ``output_dim=2`` is retained for historical checkpoints.  New economic
    checkpoints use ``output_dim=3`` and expose ``total_budget_fraction`` as
    an independent action instead of implicitly consuming the whole budget.
    """

    def __init__(self, input_dim: int, hidden_dim: int = 64, activation: str = 'tanh', temporal_dim: int = 0, output_dim: int = 2) -> None:
        super().__init__()
        act_cls = nn.Tanh if activation == 'tanh' else nn.ReLU
        self.temporal_dim = max(0, int(temporal_dim))
        self.output_dim = max(2, int(output_dim))
        self.temporal_encoder = (
            nn.Sequential(nn.Linear(self.temporal_dim, int(hidden_dim)), act_cls())
            if self.temporal_dim > 0 else None
        )
        head_input_dim = int(input_dim) + (int(hidden_dim) if self.temporal_dim > 0 else 0)
        self.net = nn.Sequential(
            nn.Linear(head_input_dim, int(hidden_dim)),
            act_cls(),
            nn.Linear(int(hidden_dim), self.output_dim),
        )

    def forward(self, states: torch.Tensor, temporal_context: torch.Tensor | None = None) -> torch.Tensor:
        if self.temporal_dim > 0:
            if temporal_context is None:
                temporal_context = torch.zeros((states.shape[0], self.temporal_dim), dtype=states.dtype, device=states.device)
            states = torch.cat([states, self.temporal_encoder(temporal_context[..., :self.temporal_dim])], dim=-1)
        raw = self.net(states)
        shares = torch.softmax(raw[..., :2], dim=-1)
        if self.output_dim <= 2:
            return shares
        total = torch.sigmoid(raw[..., 2:3])
        return torch.cat([shares, total], dim=-1)


class CentralizedQNetwork(nn.Module):
    def __init__(
        self,
        state_size: int,
        action_size: int,
        hidden_dims: Sequence[int] = (300, 400, 400),
        activation: str = 'tanh',
    ) -> None:
        super().__init__()
        act_cls = nn.Tanh if activation == 'tanh' else nn.ReLU
        layers: list[nn.Module] = []
        prev_dim = state_size + action_size
        for hidden_dim in hidden_dims:
            layers.extend([nn.Linear(prev_dim, int(hidden_dim)), act_cls()])
            prev_dim = int(hidden_dim)
        layers.append(nn.Linear(prev_dim, 1))
        self.net = nn.Sequential(*layers)

    def forward(self, states: torch.Tensor, actions: torch.Tensor) -> torch.Tensor:
        return self.net(torch.cat([states, actions], dim=-1))


class SAMOptimizer:
    def __init__(self, params, base_optimizer_cls, rho: float = 0.05, **kwargs) -> None:
        self.params = list(params)
        self.base = base_optimizer_cls(self.params, **kwargs)
        self.rho = float(rho)
        self._saved: list[torch.Tensor] = []

    def zero_grad(self) -> None:
        self.base.zero_grad()

    def step(self) -> None:
        self.base.step()

    def sam_first_step(self) -> None:
        self._saved = [p.data.clone() for p in self.params if p.grad is not None]
        grad_norm = math.sqrt(sum(float(p.grad.norm().item() ** 2) for p in self.params if p.grad is not None)) + 1e-12
        with torch.no_grad():
            for p in self.params:
                if p.grad is not None:
                    p.data.add_(self.rho * p.grad / grad_norm)

    def sam_second_step(self) -> None:
        idx = 0
        with torch.no_grad():
            for p in self.params:
                if p.grad is not None:
                    p.data.copy_(self._saved[idx])
                    idx += 1
        self.base.step()

    def state_dict(self) -> dict:
        return {
            'base_optimizer': self.base.state_dict(),
            'rho': float(self.rho),
        }

    def load_state_dict(self, state: dict) -> None:
        self.base.load_state_dict((state or {}).get('base_optimizer') or {})
        self.rho = float((state or {}).get('rho', self.rho) or self.rho)
        self._saved = []


def _normalize(values: list[float]) -> list[float]:
    cleaned = [max(0.0, float(v)) for v in values]
    total = sum(cleaned)
    if total <= 1e-9:
        return [1.0 / max(len(cleaned), 1)] * len(cleaned)
    return [v / total for v in cleaned]


def _power_target(payload: dict) -> int:
    action = payload.get('action') or payload.get('resource_allocation') or {}
    raw = action.get('power_percent', action.get('tasam_power_percent', action.get('energy_power_level')))
    if raw is None:
        raw = (payload.get('reward_components') or {}).get('power_percent')
    try:
        value = float(raw)
    except (TypeError, ValueError):
        aliases = {'POWER_DOWN_ECO': 25.0, 'REDUCE_POWER': 60.0, 'CONDITIONAL_REDUCE': 60.0, 'FULL_POWER': 100.0}
        value = aliases.get(str(raw or '').strip().upper(), -1.0)
    if value < 0.0:
        return -1
    return min(range(len(POWER_LEVELS)), key=lambda index: abs(POWER_LEVELS[index] - value))


def _economic_training_eligible(payload: dict) -> bool:
    """Gate economic heads on a materially applied, non-isolated action."""
    action = payload.get('economic_action') or {}
    if str(
        payload.get('armd_safety_level')
        or action.get('armd_safety_level')
        or ''
    ).upper() == 'HARD_VETO':
        return False
    mode = str(
        payload.get('economic_execution_mode')
        or action.get('economic_execution_mode')
        or ''
    )
    if bool(payload.get('economic_safety_isolated', action.get('economic_safety_isolated', False))):
        return False
    if mode in {'safety_isolated', 'economic_neutral_noop'}:
        return False
    if 'economic_training_eligible' in payload:
        return bool(payload.get('economic_training_eligible'))
    if 'economic_training_eligible' in action:
        return bool(action.get('economic_training_eligible'))
    # Historical applied_action_v2 traces predate the explicit field.  They
    # remain readable; new traces always persist the field explicitly.
    return True


def _safe_power_target(payload: dict) -> int:
    """Derive a supervised power target only from valid real feedback."""
    contract = payload.get('economic_action') or {}
    if str(payload.get('economic_action_contract', '') or '') == 'applied_action_v2':
        if not bool(payload.get('economic_transition_eligible', False)):
            return -1
        if not _economic_training_eligible(payload):
            return -1
        applied = contract.get('applied') if isinstance(contract, dict) else {}
        try:
            value = float((applied or {}).get('power_percent'))
        except (TypeError, ValueError):
            return -1
        if not math.isfinite(value) or not 25.0 <= value <= 100.0:
            return -1
        return min(range(len(POWER_LEVELS)), key=lambda index: abs(POWER_LEVELS[index] - value))
    feedback = payload.get('judge_feedback') or {}
    if not isinstance(feedback, dict):
        return -1
    observed = str(
        feedback.get('tasam_observed_verdict')
        or feedback.get('correct_verdict')
        or payload.get('observed_stage_name')
        or ''
    ).strip().upper()
    if feedback.get('feedback_status', 'observed') != 'observed' or observed not in {
        'ALLOWED', 'CONDITIONAL', 'BLOCKED'
    }:
        return -1
    if observed == 'BLOCKED':
        return min(range(len(POWER_LEVELS)), key=lambda index: abs(POWER_LEVELS[index] - 100.0))
    if observed == 'CONDITIONAL' or bool(feedback.get('stage_boundary_feedback')):
        return min(range(len(POWER_LEVELS)), key=lambda index: abs(POWER_LEVELS[index] - 60.0))
    components = feedback.get('tasam_error_components') or {}
    try:
        ran = float(components.get('ran_completion', -1.0))
        ai = float(components.get('ai_completion', -1.0))
        service_error = float(components.get('service_error', 1.0))
        tail_error = float(components.get('tail_latency_error', 1.0))
        loss_error = float(components.get('packet_loss_error', 1.0))
    except (TypeError, ValueError):
        return -1
    if ran >= 1.0 and ai >= 1.0 and service_error <= 1e-9 and tail_error <= 1e-9 and loss_error <= 1e-9:
        return 0
    return -1


def _allocation_target(payload: dict) -> tuple[list[float], float, dict[str, object]]:
    """Derive an aggregate allocation target from one valid real feedback.

    Targets preserve the observed RAN/IA demand ratio while raising each side
    to its completion target when the shared budget permits it.  If the two
    lower bounds are infeasible, the target is the closest budget projection
    and the reason is persisted for audit instead of being hidden.
    """
    feedback = payload.get('judge_feedback') or {}
    if not isinstance(feedback, dict) or feedback.get('feedback_status', 'observed') != 'observed':
        return [0.5, 0.5], 0.0, {'source': 'missing_or_invalid_real_feedback', 'feasible': False}
    quality = payload.get('collection_quality') or {}
    if not bool(quality.get('pdcp_real', False)) or not bool(quality.get('metric_alignment_valid', False)):
        return [0.5, 0.5], 0.0, {'source': 'non_real_or_misaligned_feedback', 'feasible': False}
    allocation = payload.get('resource_allocation') or payload.get('action') or {}
    if not isinstance(allocation, dict):
        allocation = {}
    components = feedback.get('tasam_error_components') or payload.get('tasam_error_components') or {}
    try:
        usable = float(allocation.get('usable_budget', payload.get('usable_budget', 0.0)) or 0.0)
        ran = float(allocation.get('r_ran', payload.get('r_ran', 0.0)) or 0.0)
        ai = float(allocation.get('r_ai', payload.get('r_ai', 0.0)) or 0.0)
        d_ran = float(allocation.get('d_ran', payload.get('d_ran', 0.0)) or 0.0)
        d_ai = float(allocation.get('d_ai', payload.get('d_ai', 0.0)) or 0.0)
        ran_completion = float(components.get('ran_completion', allocation.get('ran_completion_ratio', 0.0)) or 0.0)
        ai_completion = float(components.get('ai_completion', allocation.get('ai_completion_ratio', 0.0)) or 0.0)
    except (TypeError, ValueError):
        return [0.5, 0.5], 0.0, {'source': 'invalid_allocation_feedback', 'feasible': False}
    if usable <= 1e-9 or d_ran < 0.0 or d_ai < 0.0:
        return [0.5, 0.5], 0.0, {'source': 'invalid_budget_or_demand', 'feasible': False}

    # The economic online campaign opts into a third target: the total
    # fraction of the usable budget.  Historical traces retain the original
    # two-output target and therefore keep their exact behavior.
    if bool(payload.get('allocation_total_head_enabled', False)):
        floor_ran = max(0.0, float(allocation.get('floor_total_ran', 0.0) or 0.0))
        floor_ai = max(0.0, float(allocation.get('floor_total_ai', 0.0) or 0.0))
        floor_total = floor_ran + floor_ai
        if floor_total > usable + 1e-9:
            # An impossible floor is an observation for the safety layer, not
            # an instruction to teach the economic head to exceed its budget.
            return [0.5, 0.5, 1.0], 0.0, {
                'source': 'infeasible_economic_sla_floor',
                'feasible': False,
                'failsafe_required': True,
                'economic_transition_eligible': False,
                'usable_budget': usable,
                'budget_lower_bound_total': floor_total,
                'allocation_head_outputs': ['ran_share', 'ai_share', 'total_budget_fraction'],
            }
        if str(payload.get('economic_action_contract', '') or '') == 'applied_action_v2':
            contract = payload.get('economic_action') or {}
            applied = contract.get('applied') if isinstance(contract, dict) else {}
            if not bool(payload.get('economic_transition_eligible', False)) or not isinstance(applied, dict):
                return [0.5, 0.5, 1.0], 0.0, {
                    'source': 'economic_action_not_eligible',
                    'feasible': False,
                    'economic_transition_eligible': False,
                }
            try:
                applied_ran = float(applied.get('ran_allocation'))
                applied_ai = float(applied.get('ai_allocation'))
                applied_usable = float(applied.get('usable_budget', usable))
            except (TypeError, ValueError):
                return [0.5, 0.5, 1.0], 0.0, {
                    'source': 'missing_applied_economic_allocation',
                    'feasible': False,
                    'economic_transition_eligible': False,
                }
            applied_total = applied_ran + applied_ai
            if (
                not math.isfinite(applied_total)
                or not math.isfinite(applied_usable)
                or applied_usable <= 1e-9
                or applied_ran < 0.0
                or applied_ai < 0.0
                or applied_total > applied_usable + 1e-9
            ):
                return [0.5, 0.5, 1.0], 0.0, {
                    'source': 'invalid_applied_economic_allocation',
                    'feasible': False,
                    'economic_transition_eligible': False,
                }
            total = max(applied_total, 1e-9)
            return [
                max(0.0, min(1.0, applied_ran / total)),
                max(0.0, min(1.0, applied_ai / total)),
                max(0.0, min(1.0, applied_total / applied_usable)),
            ], 1.0, {
                'source': 'verified_applied_economic_action_v2',
                'feasible': True,
                'economic_transition_eligible': True,
                'usable_budget': applied_usable,
                'budget_lower_bound_total': floor_total,
                'target_total_allocation': applied_total,
                'target_total_fraction': max(0.0, min(1.0, applied_total / applied_usable)),
                'allocation_head_outputs': ['ran_share', 'ai_share', 'total_budget_fraction'],
            }
        state = str(allocation.get('allocation_state', 'ALLOWED') or 'ALLOWED').upper()
        margin_scale = {'ALLOWED': 0.05, 'CONDITIONAL': 0.10, 'CRITICAL': 0.75, 'BLOCKED': 1.0}.get(state, 0.10)
        slack = max(0.0, usable - floor_total)
        target_total = min(usable, floor_total + margin_scale * slack)
        demand_total = d_ran + d_ai
        ran_share = d_ran / demand_total if demand_total > 1e-9 else 0.5
        target_ran = max(floor_ran, target_total * ran_share)
        target_ai = max(floor_ai, target_total - target_ran)
        if target_ran + target_ai > usable:
            target_ran = floor_ran
            target_ai = floor_ai
        target_total = min(usable, max(target_ran + target_ai, 1e-9))
        return [target_ran / target_total, target_ai / target_total, target_total / usable], 1.0, {
            'source': 'observed_sla_floor_plus_economic_margin',
            'feasible': floor_total <= usable + 1e-9,
            'usable_budget': usable,
            'floor_total_ran': floor_ran,
            'floor_total_ai': floor_ai,
            'budget_lower_bound_total': floor_total,
            'target_total_allocation': target_total,
            'target_total_fraction': target_total / usable,
            'margin_scale': margin_scale,
            'allocation_head_outputs': ['ran_share', 'ai_share', 'total_budget_fraction'],
        }
    ran_need = max(ran, d_ran)
    ai_need = max(ai, d_ai)
    lower_total = ran_need + ai_need
    feasible = lower_total <= usable + 1e-9
    if not feasible:
        return [0.5, 0.5], 0.0, {
            'source': 'infeasible_hard_sla_floor',
            'feasible': False,
            'failsafe_required': True,
            'usable_budget': usable,
            'budget_lower_bound_total': lower_total,
        }
    else:
        # Use unused budget according to observed demand, without inventing a
        # preference between RAN and IA.
        remainder = max(0.0, usable - lower_total)
        demand_total = d_ran + d_ai
        ran_need += remainder * (d_ran / demand_total if demand_total > 1e-9 else 0.5)
        ai_need += remainder * (d_ai / demand_total if demand_total > 1e-9 else 0.5)
    total = max(ran_need + ai_need, 1e-9)
    return [ran_need / total, ai_need / total], 1.0, {
        'source': 'observed_real_completion_targets',
        'feasible': feasible,
        'usable_budget': usable,
        'target_ran_resource': ran_need,
        'target_ai_resource': ai_need,
        'ran_completion_observed': ran_completion,
        'ai_completion_observed': ai_completion,
        'ran_completion_target': 1.0,
        'ai_completion_target': 1.0,
        'budget_lower_bound_total': lower_total,
    }


def _normalized_allocation_target(
    target: Sequence[float] | None,
    output_dim: int,
) -> list[float]:
    """Return an allocation target with exactly ``output_dim`` components.

    The original allocation head had two outputs (RAN/AI split). A
    three-output trainer can encounter a valid historical transition during
    an upgrade; such records have no total-utilization observation, so their
    explicit neutral legacy value is ``1.0``. New online-economic exports
    always contain an observed third target; this padding is therefore only a
    compatibility guard and never economic evidence.
    """
    width = max(2, int(output_dim))
    defaults = [0.5, 0.5, 1.0]
    values: list[float] = []
    for value in list(target or ())[:width]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = defaults[len(values)] if len(values) < len(defaults) else 0.0
        if not math.isfinite(parsed):
            parsed = defaults[len(values)] if len(values) < len(defaults) else 0.0
        values.append(parsed)
    while len(values) < width:
        values.append(defaults[len(values)] if len(values) < len(defaults) else 0.0)
    if width >= 2:
        values[:2] = _normalize(values[:2])
    if width >= 3:
        values[2] = max(0.0, min(1.0, values[2]))
    return values


def _temporal_context(payload: dict) -> list[float]:
    values = payload.get('temporal_context') or payload.get('temporal_features') or []
    try:
        normalized = [float(value) for value in values]
    except (TypeError, ValueError):
        normalized = []
    if len(normalized) < TEMPORAL_FEATURE_DIM:
        normalized.extend([0.0] * (TEMPORAL_FEATURE_DIM - len(normalized)))
    return normalized[:TEMPORAL_FEATURE_DIM]


def _derive_target_actions(payload: dict) -> list[list[float]]:
    slice_state = payload.get('slice_state') or {}
    slice_budget = {
        slice_id: float((slice_state.get(slice_id) or {}).get('budget_share', 0.0) or 0.0)
        for slice_id in SLICE_ORDER
    }
    fallback_budget = sum(slice_budget.values()) <= 1e-9
    targets: list[list[float]] = []
    for du in payload.get('du_states', []) or []:
        mix = dict((du or {}).get('slice_mix') or {})
        raw = []
        for slice_id in SLICE_ORDER:
            mix_value = float(mix.get(slice_id, 0.0) or 0.0)
            budget_value = 1.0 if fallback_budget else slice_budget.get(slice_id, 0.0)
            raw.append(mix_value * budget_value)
        if sum(raw) <= 1e-9:
            primary = (du or {}).get('primary_slice')
            raw = [1.0 if slice_id == primary else 0.1 for slice_id in SLICE_ORDER]
        targets.append(_normalize(raw))
    return targets


def _applied_economic_action(payload: dict) -> tuple[list[list[float]] | None, list[float] | None]:
    """Return only a fully recorded applied-action v2 contract.

    The global action carries power, AI share and total budget fraction.  DU
    vectors are persisted at application time so replay never reconstructs a
    TA-SAM action from the state after the fact.
    """
    contract_name = str(payload.get('economic_action_contract', '') or '')
    if contract_name not in {'applied_action_v2', 'economic_action_v3_per_du_sleep'}:
        return None, None
    if not bool(payload.get('economic_transition_eligible', False)) or not _economic_training_eligible(payload):
        return None, None
    contract = payload.get('economic_action') or {}
    applied = contract.get('applied') if isinstance(contract, dict) else None
    if not isinstance(applied, dict):
        return None, None
    raw_du_actions = applied.get('du_actions') or []
    if not isinstance(raw_du_actions, list) or len(raw_du_actions) != len(payload.get('du_states') or []):
        return None, None
    try:
        du_actions = [_normalize([float(value) for value in item]) for item in raw_du_actions]
        if any(len(item) != len(SLICE_ORDER) for item in du_actions):
            return None, None
        raw_power = applied.get('power_percent')
        power = float(raw_power) / 100.0 if raw_power is not None else float('nan')
        ran = float(applied.get('ran_allocation'))
        ai = float(applied.get('ai_allocation'))
        usable = float(applied.get('usable_budget'))
    except (TypeError, ValueError):
        return None, None
    total = ran + ai
    if (
        not all(math.isfinite(value) for value in (ran, ai, usable, total))
        or ran < 0.0
        or ai < 0.0
        or usable <= 1e-9
        or total > usable + 1e-9
    ):
        return None, None
    if contract_name == 'economic_action_v3_per_du_sleep':
        raw_by_cell = applied.get('power_percent_by_cell') or {}
        try:
            per_cell = [
                float(raw_by_cell.get(str(cell), raw_by_cell.get(cell))) / 100.0
                for cell in (2, 3, 4)
            ]
        except (TypeError, ValueError):
            return None, None
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in per_cell):
            return None, None
        if sum(value == 0.0 for value in per_cell) > 1:
            return None, None
        return du_actions, per_cell + [ran / max(total, 1e-9), total / usable]
    if not math.isfinite(power) or not 0.25 <= power <= 1.0:
        return None, None
    return du_actions, [power, ai / max(total, 1e-9), total / usable]


def load_marl_transition_trace(path: str | Path) -> list[MARLTransitionRecord]:
    records: list[MARLTransitionRecord] = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        du_states = [list((du or {}).get('state_vector', [])) for du in payload.get('du_states', []) or []]
        next_du_states = [list((du or {}).get('state_vector', [])) for du in payload.get('next_du_states', []) or []]
        global_state = list((payload.get('global_state') or {}).get('state_vector', []))
        next_global_state = list((payload.get('next_global_state') or {}).get('state_vector', []))
        if not du_states or not next_du_states or not global_state or not next_global_state:
            continue
        applied_du_actions, applied_global_action = _applied_economic_action(payload)
        economic_v2 = str(payload.get('economic_action_contract', '') or '') in {
            'applied_action_v2', 'economic_action_v3_per_du_sleep'
        }
        economic_weight = 1.0
        if economic_v2:
            economic_weight = 1.0 if applied_du_actions is not None and applied_global_action is not None else 0.0
            # Category feedback remains useful when a proposal is rejected,
            # but rejected/overridden rows cannot teach the economic critic.
            behavior_actions = applied_du_actions or _derive_target_actions(payload)
        else:
            behavior_actions = _derive_target_actions(payload)
        if len(behavior_actions) != len(du_states) or len(next_du_states) != len(du_states):
            continue
        feedback = payload.get('judge_feedback') or {}
        reward = float(
            payload.get(
                'tasam_online_reward',
                feedback.get('tasam_online_reward', payload.get('reward_hint', 0.0)),
            ) or 0.0
        )
        if economic_v2 and economic_weight <= 0.0:
            reward = 0.0
        observed = payload.get('tasam_observed_verdict') or feedback.get('tasam_observed_verdict') or feedback.get('observed_verdict')
        predicted = payload.get('tasam_predicted_verdict') or feedback.get('tasam_predicted_verdict') or feedback.get('predicted_verdict')
        category_target = category_index(observed)
        predicted_index = category_index(predicted)
        if category_target >= 0:
            # Conditional targets are deliberately over-represented in the
            # ordinal head. Directional mistakes use the same asymmetry as
            # the strong training credit, capped at three per transition.
            category_weight = 1.5 if category_target == category_index("CONDITIONAL") else 1.0
            if predicted_index >= 0 and predicted_index != category_target:
                multiplier = 2.0 if (
                    abs(predicted_index - category_target) >= 2
                    or predicted_index > category_target
                ) else 1.5
                category_weight = min(3.0, category_weight * multiplier)
        else:
            category_weight = 0.0
        training_credit = float(
            payload.get(
                "tasam_training_category_credit",
                feedback.get("tasam_training_category_credit", 0.0),
            )
            or 0.0
        )
        quality = payload.get('collection_quality') or {}
        done = bool(quality.get('sim_reset', False))
        allocation_target, allocation_weight, allocation_target_metadata = _allocation_target(payload)
        records.append(
            MARLTransitionRecord(
                global_state=global_state,
                du_states=du_states,
                behavior_actions=behavior_actions,
                reward=reward,
                next_global_state=next_global_state,
                next_du_states=next_du_states,
                done=done,
                category_target=category_target,
                category_weight=category_weight,
                category_training_credit=training_credit,
                temporal_context=_temporal_context(payload),
                power_target=_safe_power_target(payload),
                allocation_target=allocation_target,
                allocation_weight=allocation_weight,
                allocation_target_metadata=allocation_target_metadata,
                behavior_global_action=applied_global_action,
                economic_weight=economic_weight,
            )
        )
    return records


def linear_rho_schedule(rho_start: float, rho_final: float, progress: float) -> float:
    progress = max(0.0, min(1.0, float(progress)))
    return float(rho_start) + ((float(rho_final) - float(rho_start)) * progress)


def td_scaled_rho(rho_start: float, rho_final: float, td_value: float, td_max: float) -> float:
    if td_max <= 1e-12:
        return float(rho_final)
    scale = max(0.0, min(1.0, float(td_value) / float(td_max)))
    return linear_rho_schedule(rho_final, rho_start, scale)


def calibrate_td_variance_threshold(
    td_variances: list[float],
    requested_threshold: float,
    min_selected_fraction: float = 0.1,
    warmup: bool = False,
) -> float:
    if not td_variances:
        return 0.0
    if warmup:
        return 0.0
    values = sorted(max(0.0, float(v)) for v in td_variances)
    if requested_threshold <= 0.0:
        return 0.0
    n = len(values)
    min_selected_fraction = max(0.0, min(1.0, float(min_selected_fraction)))
    target_selected = max(1, int(round(n * min_selected_fraction)))
    idx = max(0, n - target_selected)
    threshold_from_fraction = values[idx]
    observed_max = values[-1]
    effective = min(float(requested_threshold), float(threshold_from_fraction))
    if requested_threshold > observed_max:
        effective = float(threshold_from_fraction)
    return max(0.0, effective)


def soft_update(target: nn.Module, source: nn.Module, tau: float) -> None:
    with torch.no_grad():
        for target_param, source_param in zip(target.parameters(), source.parameters()):
            target_param.data.mul_(1.0 - tau).add_(tau * source_param.data)


class TASAMArticleSACTrainer:
    def __init__(
        self,
        du_count: int,
        du_state_dim: int,
        global_state_dim: int,
        lr: float = 1e-4,
        alpha_lr: float = 1e-4,
        gamma: float = 0.99,
        tau: float = 0.01,
        alpha_init: float = 0.03,
        target_entropy_scale: float = 1.0,
        actor_rho: float = 0.5,
        actor_rho_final: float | None = None,
        critic_rho: float = 0.5,
        critic_rho_final: float | None = None,
        sam_mode: str = 'tasam_selective',
        l2_weight: float = 0.0,
        actor_hidden_dims: Sequence[int] = (300, 400, 400),
        critic_hidden_dims: Sequence[int] = (300, 400, 400),
        activation: str = 'tanh',
        batch_size: int = 128,
        updates_per_epoch: int | None = None,
        actor_update_interval: int = 1,
        category_loss_weight: float = 0.5,
        category_head_hidden_dim: int = 64,
        category_head_lr: float = 0.001,
        category_head_steps: int = 10,
        temporal_dim: int = 0,
        power_head_hidden_dim: int = 64,
        power_head_lr: float = 0.001,
        power_head_steps: int = 10,
        allocation_head_hidden_dim: int = 64,
        allocation_head_lr: float = 0.001,
        allocation_head_steps: int = 10,
        allocation_head_output_dim: int = 2,
        seed: int = 42,
        global_action_dim: int = 3,
    ) -> None:
        self.device = torch.device('cpu')
        torch.manual_seed(int(seed))
        np.random.seed(int(seed))
        self.du_count = int(du_count)
        self.du_state_dim = int(du_state_dim)
        self.global_state_dim = int(global_state_dim)
        self.action_dim = len(SLICE_ORDER)
        self.global_action_dim = max(3, int(global_action_dim))
        self.joint_action_dim = (self.du_count * self.action_dim) + self.global_action_dim
        self.gamma = float(gamma)
        self.tau = float(tau)
        self.sam_mode = str(sam_mode)
        self.l2_weight = float(l2_weight)
        self.batch_size = max(1, int(batch_size))
        self.updates_per_epoch = updates_per_epoch
        self.actor_update_interval = max(1, int(actor_update_interval))
        self.category_loss_weight = max(0.0, float(category_loss_weight))
        self.category_head_hidden_dim = max(1, int(category_head_hidden_dim))
        self.category_head_lr = float(category_head_lr)
        self.category_head_steps = max(1, int(category_head_steps))
        self.temporal_dim = max(0, int(temporal_dim))
        self.power_head_hidden_dim = max(1, int(power_head_hidden_dim))
        self.power_head_lr = float(power_head_lr)
        self.power_head_steps = max(1, int(power_head_steps))
        self.allocation_head_hidden_dim = max(1, int(allocation_head_hidden_dim))
        self.allocation_head_lr = float(allocation_head_lr)
        self.allocation_head_steps = max(1, int(allocation_head_steps))
        self.allocation_head_output_dim = max(2, int(allocation_head_output_dim))
        self.actor_rho_start = float(actor_rho)
        self.actor_rho_final = float(actor_rho if actor_rho_final is None else actor_rho_final)
        self.critic_rho_start = float(critic_rho)
        self.critic_rho_final = float(critic_rho if critic_rho_final is None else critic_rho_final)
        self.target_entropy = -float(target_entropy_scale) * float(self.joint_action_dim)

        self.actors = nn.ModuleList([
            DirichletActor(du_state_dim, hidden_dims=actor_hidden_dims, action_dim=self.action_dim, activation=activation)
            for _ in range(self.du_count)
        ])
        # Keep the legacy DU/critic initialization stream stable so adding the
        # fourth actor does not silently alter existing seed reproducibility.
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(int(seed) + 7919)
            self.global_actor = GlobalBudgetActor(
                global_state_dim, hidden_dims=actor_hidden_dims,
                action_dim=self.global_action_dim, activation=activation,
            )
        self.critic1 = CentralizedQNetwork(global_state_dim, self.joint_action_dim, hidden_dims=critic_hidden_dims, activation=activation)
        self.critic2 = CentralizedQNetwork(global_state_dim, self.joint_action_dim, hidden_dims=critic_hidden_dims, activation=activation)
        self.target_critic1 = CentralizedQNetwork(global_state_dim, self.joint_action_dim, hidden_dims=critic_hidden_dims, activation=activation)
        self.target_critic2 = CentralizedQNetwork(global_state_dim, self.joint_action_dim, hidden_dims=critic_hidden_dims, activation=activation)
        self.target_critic1.load_state_dict(self.critic1.state_dict())
        self.target_critic2.load_state_dict(self.critic2.state_dict())
        self.category_head = OrdinalCategoryHead(
            global_state_dim,
            hidden_dim=self.category_head_hidden_dim,
            activation=activation,
            temporal_dim=self.temporal_dim,
        )
        self.power_head = PowerIntentHead(
            global_state_dim,
            hidden_dim=self.power_head_hidden_dim,
            activation=activation,
            temporal_dim=self.temporal_dim,
        )
        self.allocation_head = AllocationIntentHead(
            global_state_dim,
            hidden_dim=self.allocation_head_hidden_dim,
            activation=activation,
            temporal_dim=self.temporal_dim,
            output_dim=self.allocation_head_output_dim,
        )

        self.actor_opt = SAMOptimizer(
            list(self.actors.parameters()) + list(self.global_actor.parameters()),
            torch.optim.Adam,
            lr=lr,
            rho=self.actor_rho_start,
        )
        self.category_opt = torch.optim.Adam(
            self.category_head.parameters(), lr=self.category_head_lr
        )
        self.power_opt = torch.optim.Adam(self.power_head.parameters(), lr=self.power_head_lr)
        self.allocation_opt = torch.optim.Adam(self.allocation_head.parameters(), lr=self.allocation_head_lr)
        critic_params = list(self.critic1.parameters()) + list(self.critic2.parameters())
        self.critic_opt = SAMOptimizer(critic_params, torch.optim.Adam, lr=lr, rho=self.critic_rho_start)
        self.log_alpha = torch.tensor(math.log(max(alpha_init, 1e-6)), dtype=torch.float32, requires_grad=True)
        self.alpha_opt = torch.optim.Adam([self.log_alpha], lr=alpha_lr)

    def _l2_penalty(self, *modules: nn.Module) -> torch.Tensor:
        if self.l2_weight <= 0.0:
            return torch.tensor(0.0, dtype=torch.float32)
        penalty = None
        for module in modules:
            for param in module.parameters():
                value = param.pow(2).sum()
                penalty = value if penalty is None else penalty + value
        if penalty is None:
            return torch.tensor(0.0, dtype=torch.float32)
        return penalty * self.l2_weight

    def _critic_sam_enabled(self) -> bool:
        return self.sam_mode in {'critic_sam', 'both_sam', 'tasam_selective'}

    def _actor_sam_enabled(self, selected: bool, warmup: bool) -> bool:
        if self.sam_mode in {'none', 'no_sam', 'l2'}:
            return False
        if self.sam_mode in {'actor_sam', 'both_sam'}:
            return True
        if self.sam_mode == 'critic_sam':
            return False
        return bool(selected or warmup)

    def _prepare_dataset(self, records: list[MARLTransitionRecord]) -> TransitionTensorDataset:
        global_states = torch.tensor([record.global_state for record in records], dtype=torch.float32)
        du_states = torch.tensor([record.du_states for record in records], dtype=torch.float32)
        behavior_actions = torch.tensor([record.behavior_actions for record in records], dtype=torch.float32)
        rewards = torch.tensor([record.reward for record in records], dtype=torch.float32).unsqueeze(-1)
        next_global_states = torch.tensor([record.next_global_state for record in records], dtype=torch.float32)
        next_du_states = torch.tensor([record.next_du_states for record in records], dtype=torch.float32)
        dones = torch.tensor([1.0 if record.done else 0.0 for record in records], dtype=torch.float32).unsqueeze(-1)
        category_targets = torch.tensor([record.category_target for record in records], dtype=torch.int64)
        category_weights = torch.tensor([record.category_weight for record in records], dtype=torch.float32)
        temporal_contexts = torch.tensor(
            [list(record.temporal_context or [0.0] * TEMPORAL_FEATURE_DIM)[:TEMPORAL_FEATURE_DIM] for record in records],
            dtype=torch.float32,
        )
        power_targets = torch.tensor([record.power_target for record in records], dtype=torch.int64)
        allocation_targets = torch.tensor(
            [
                _normalized_allocation_target(
                    record.allocation_target,
                    self.allocation_head_output_dim,
                )
                for record in records
            ],
            dtype=torch.float32,
        )
        allocation_weights = torch.tensor([record.allocation_weight for record in records], dtype=torch.float32)
        behavior_global_actions = torch.tensor(
            [
                list(record.behavior_global_action)
                if record.behavior_global_action is not None
                and len(record.behavior_global_action) == self.global_action_dim
                else (
                    [
                        POWER_LEVELS[record.power_target] / 100.0
                        if 0 <= record.power_target < len(POWER_LEVELS) else 1.0,
                        _normalized_allocation_target(record.allocation_target, self.allocation_head_output_dim)[1],
                        _normalized_allocation_target(record.allocation_target, self.allocation_head_output_dim)[2]
                        if self.allocation_head_output_dim >= 3 else 1.0,
                    ]
                    if self.global_action_dim == 3 else
                    [
                        1.0, 1.0, 1.0,
                        _normalized_allocation_target(record.allocation_target, self.allocation_head_output_dim)[0],
                        _normalized_allocation_target(record.allocation_target, self.allocation_head_output_dim)[2]
                        if self.allocation_head_output_dim >= 3 else 1.0,
                    ]
                )
                for record in records
            ],
            dtype=torch.float32,
        )
        economic_weights = torch.tensor(
            [max(0.0, min(1.0, float(record.economic_weight))) for record in records],
            dtype=torch.float32,
        )
        return TransitionTensorDataset(
            global_states=global_states,
            du_states=du_states,
            behavior_actions=behavior_actions,
            rewards=rewards,
            next_global_states=next_global_states,
            next_du_states=next_du_states,
            dones=dones,
            category_targets=category_targets,
            category_weights=category_weights,
            temporal_contexts=temporal_contexts,
            power_targets=power_targets,
            allocation_targets=allocation_targets,
            allocation_weights=allocation_weights,
            behavior_global_actions=behavior_global_actions,
            economic_weights=economic_weights,
        )

    def _sample_indices(self, dataset_size: int, candidates: torch.Tensor | None = None) -> torch.Tensor:
        if candidates is None:
            batch_size = min(self.batch_size, dataset_size)
            return torch.randint(0, dataset_size, (batch_size,), dtype=torch.int64)
        if candidates.numel() <= 0:
            return torch.empty((0,), dtype=torch.int64)
        batch_size = min(self.batch_size, int(candidates.numel()))
        positions = torch.randint(0, int(candidates.numel()), (batch_size,), dtype=torch.int64)
        return candidates[positions]

    def _batch_from_indices(self, dataset: TransitionTensorDataset, indices: torch.Tensor) -> dict[str, torch.Tensor]:
        return {
            'global_states': dataset.global_states[indices],
            'du_states': dataset.du_states[indices],
            'behavior_actions': torch.cat([
                dataset.behavior_actions[indices].reshape(indices.shape[0], -1),
                dataset.behavior_global_actions[indices],
            ], dim=-1),
            'rewards': dataset.rewards[indices],
            'next_global_states': dataset.next_global_states[indices],
            'next_du_states': dataset.next_du_states[indices],
            'dones': dataset.dones[indices],
            'category_targets': dataset.category_targets[indices],
            'category_weights': dataset.category_weights[indices],
            'temporal_contexts': dataset.temporal_contexts[indices],
            'power_targets': dataset.power_targets[indices],
            'allocation_targets': dataset.allocation_targets[indices],
            'allocation_weights': dataset.allocation_weights[indices],
            'economic_weights': dataset.economic_weights[indices],
        }

    def _category_class_weights(self, dataset: TransitionTensorDataset) -> torch.Tensor:
        mask = (dataset.category_targets >= 0) & (dataset.category_weights > 0.0)
        weights = torch.ones(len(CATEGORY_ORDER), dtype=torch.float32)
        if not bool(mask.any()):
            return weights
        targets = dataset.category_targets[mask]
        counts = torch.bincount(targets, minlength=len(CATEGORY_ORDER)).to(torch.float32)
        total = float(targets.numel())
        raw = torch.where(
            counts > 0.0,
            total / (len(CATEGORY_ORDER) * counts),
            torch.full_like(counts, 3.0),
        )
        conditional_index = category_index("CONDITIONAL")
        raw[conditional_index] = max(float(raw[conditional_index].item()), 1.5)
        return torch.clamp(raw, min=1.0, max=3.0)

    def _category_loss(
        self,
        batch: dict[str, torch.Tensor],
        class_weights: torch.Tensor | None = None,
    ) -> torch.Tensor:
        mask = (batch['category_targets'] >= 0) & (batch['category_weights'] > 0.0)
        if not bool(mask.any()):
            return torch.tensor(0.0, dtype=torch.float32)
        logits = self.category_head(batch['global_states'][mask], batch['temporal_contexts'][mask])
        losses = F.cross_entropy(logits, batch['category_targets'][mask], reduction='none')
        weights = batch['category_weights'][mask]
        if class_weights is not None:
            weights = weights * class_weights[batch['category_targets'][mask]]
        return (losses * weights).sum() / torch.clamp(weights.sum(), min=1e-9)

    def _train_power_head(self, dataset: TransitionTensorDataset) -> dict[str, object]:
        mask = dataset.power_targets >= 0
        if not bool(mask.any()):
            return {'power_head_steps': 0, 'power_head_update_loss': 0.0, 'power_evaluated': 0}
        losses: list[float] = []
        self.power_head.train()
        # The safe 25% state is under-represented in real traces, so retain a
        # mild class balance without inventing observations.
        targets = dataset.power_targets[mask]
        counts = torch.bincount(targets, minlength=len(POWER_LEVELS)).to(torch.float32)
        total = float(targets.numel())
        class_weights = torch.where(counts > 0.0, total / (len(POWER_LEVELS) * counts), torch.ones_like(counts))
        class_weights = torch.clamp(class_weights, min=1.0, max=3.0)
        for _ in range(self.power_head_steps):
            batch = self._batch_from_indices(dataset, self._sample_indices(len(dataset)))
            batch_mask = batch['power_targets'] >= 0
            if not bool(batch_mask.any()):
                continue
            logits = self.power_head(batch['global_states'][batch_mask], batch['temporal_contexts'][batch_mask])
            loss = F.cross_entropy(logits, batch['power_targets'][batch_mask], weight=class_weights)
            self.power_opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.power_head.parameters(), max_norm=5.0)
            self.power_opt.step()
            losses.append(float(loss.item()))
        return {
            'power_head_steps': self.power_head_steps,
            'power_head_update_loss': float(np.mean(losses)) if losses else 0.0,
            'power_evaluated': int(mask.sum().item()),
        }

    def _train_allocation_head(self, dataset: TransitionTensorDataset) -> dict[str, object]:
        if dataset.allocation_targets.shape[-1] != self.allocation_head_output_dim:
            raise RuntimeError(
                "allocation target contract mismatch: "
                f"expected {self.allocation_head_output_dim} values, got "
                f"{dataset.allocation_targets.shape[-1]}"
            )
        mask = dataset.allocation_weights > 0.0
        if not bool(mask.any()):
            return {
                'allocation_head_steps': 0,
                'allocation_head_update_loss': 0.0,
                'allocation_evaluated': 0,
            }
        losses: list[float] = []
        self.allocation_head.train()
        for _ in range(self.allocation_head_steps):
            batch = self._batch_from_indices(dataset, self._sample_indices(len(dataset)))
            batch_mask = batch['allocation_weights'] > 0.0
            if not bool(batch_mask.any()):
                continue
            predicted = self.allocation_head(
                batch['global_states'][batch_mask], batch['temporal_contexts'][batch_mask]
            )
            target = batch['allocation_targets'][batch_mask]
            row_loss = ((predicted - target) ** 2).sum(dim=-1)
            weights = batch['allocation_weights'][batch_mask]
            loss = (row_loss * weights).sum() / torch.clamp(weights.sum(), min=1e-9)
            self.allocation_opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.allocation_head.parameters(), max_norm=5.0)
            self.allocation_opt.step()
            losses.append(float(loss.item()))
        return {
            'allocation_head_steps': self.allocation_head_steps,
            'allocation_head_update_loss': float(np.mean(losses)) if losses else 0.0,
            'allocation_evaluated': int(mask.sum().item()),
        }

    def _allocation_metrics(self, dataset: TransitionTensorDataset) -> dict[str, float]:
        if dataset.allocation_targets.shape[-1] != self.allocation_head_output_dim:
            raise RuntimeError(
                "allocation target contract mismatch: "
                f"expected {self.allocation_head_output_dim} values, got "
                f"{dataset.allocation_targets.shape[-1]}"
            )
        mask = dataset.allocation_weights > 0.0
        if not bool(mask.any()):
            return {
                'allocation_target_ran_mean': 0.0,
                'allocation_target_ai_mean': 0.0,
                'allocation_target_total_mean': 0.0,
                'allocation_prediction_loss': 0.0,
                'economic_transition_evaluated': 0,
                'economic_transition_excluded': 0,
            }
        with torch.no_grad():
            predicted = self.allocation_head(
                dataset.global_states[mask], dataset.temporal_contexts[mask]
            )
            target = dataset.allocation_targets[mask]
            loss = ((predicted - target) ** 2).sum(dim=-1).mean()
        return {
            'allocation_target_ran_mean': float(target[:, 0].mean().item()),
            'allocation_target_ai_mean': float(target[:, 1].mean().item()),
            'allocation_target_total_mean': float(target[:, 2].mean().item()) if target.shape[-1] >= 3 else 1.0,
            'allocation_prediction_loss': float(loss.item()),
        }

    def _train_category_head(self, dataset: TransitionTensorDataset) -> dict[str, object]:
        mask = (dataset.category_targets >= 0) & (dataset.category_weights > 0.0)
        if not bool(mask.any()):
            return {
                'category_head_steps': 0,
                'category_head_update_loss': 0.0,
                'category_class_weights': [1.0] * len(CATEGORY_ORDER),
            }
        class_weights = self._category_class_weights(dataset)
        losses: list[float] = []
        self.category_head.train()
        supervised_indices = torch.nonzero(mask, as_tuple=False).flatten()
        for _ in range(self.category_head_steps):
            # This auxiliary hard-SLA classifier is small; full supervised
            # batches avoid accidentally omitting CONDITIONAL examples.
            batch = self._batch_from_indices(dataset, supervised_indices)
            loss = self._category_loss(batch, class_weights)
            self.category_opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.category_head.parameters(), max_norm=5.0)
            self.category_opt.step()
            losses.append(float(loss.item()))
        return {
            'category_head_steps': self.category_head_steps,
            'category_head_update_loss': float(np.mean(losses)) if losses else 0.0,
            'category_class_weights': [float(value) for value in class_weights.tolist()],
        }

    def _category_metrics(self, dataset: TransitionTensorDataset) -> dict[str, object]:
        mask = (dataset.category_targets >= 0) & (dataset.category_weights > 0.0)
        confusion = {f'{predicted}->{observed}': 0 for predicted in CATEGORY_ORDER for observed in CATEGORY_ORDER}
        if not bool(mask.any()):
            return {
                'category_accuracy': 0.0,
                'category_loss': 0.0,
                'category_evaluated': 0,
                'category_confusion': confusion,
                'category_directional_error_rate': 0.0,
                'conditional_precision': 0.0,
                'conditional_recall': 0.0,
                'conditional_f1': 0.0,
            }
        with torch.no_grad():
            logits = self.category_head(dataset.global_states[mask], dataset.temporal_contexts[mask])
            targets = dataset.category_targets[mask]
            weights = dataset.category_weights[mask]
            class_weights = self._category_class_weights(dataset)
            weighted = weights * class_weights[targets]
            loss = (F.cross_entropy(logits, targets, reduction='none') * weighted).sum() / torch.clamp(weighted.sum(), min=1e-9)
            predicted = logits.argmax(dim=-1)
        correct = int((predicted == targets).sum().item())
        for pred, target in zip(predicted.tolist(), targets.tolist()):
            key = f'{category_name(pred)}->{category_name(target)}'
            if key in confusion:
                confusion[key] += 1
        directional_errors = sum(value for key, value in confusion.items() if key.split('->')[0] != key.split('->')[1])
        conditional_tp = confusion['CONDITIONAL->CONDITIONAL']
        conditional_fp = sum(
            confusion[f'CONDITIONAL->{target}']
            for target in CATEGORY_ORDER if target != 'CONDITIONAL'
        )
        conditional_fn = sum(
            confusion[f'{predicted}->CONDITIONAL']
            for predicted in CATEGORY_ORDER if predicted != 'CONDITIONAL'
        )
        conditional_precision = conditional_tp / max(conditional_tp + conditional_fp, 1)
        conditional_recall = conditional_tp / max(conditional_tp + conditional_fn, 1)
        conditional_f1 = (
            2.0 * conditional_precision * conditional_recall
            / max(conditional_precision + conditional_recall, 1e-12)
        )
        return {
            'category_accuracy': float(correct / max(len(targets), 1)),
            'category_loss': float(loss.item()),
            'category_evaluated': int(len(targets)),
            'category_confusion': confusion,
            'category_directional_error_rate': float(directional_errors / max(len(targets), 1)),
            'conditional_precision': float(conditional_precision),
            'conditional_recall': float(conditional_recall),
            'conditional_f1': float(conditional_f1),
        }

    def _sample_joint_actions(self, du_states: torch.Tensor, global_states: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, list[torch.Tensor]]:
        actions = []
        log_probs = []
        for idx, actor in enumerate(self.actors):
            action, log_prob, _ = actor.sample(du_states[:, idx, :])
            actions.append(action)
            log_probs.append(log_prob)
        global_action, global_log_prob, _ = self.global_actor.sample(global_states)
        actions.append(global_action)
        joint_actions = torch.cat(actions, dim=-1)
        joint_log_prob = torch.stack(log_probs + [global_log_prob], dim=0).sum(dim=0)
        return joint_actions, joint_log_prob, actions

    def _deterministic_joint_actions(self, du_states: torch.Tensor, global_states: torch.Tensor) -> torch.Tensor:
        actions = [actor.deterministic(du_states[:, idx, :]) for idx, actor in enumerate(self.actors)]
        actions.append(self.global_actor.deterministic(global_states))
        return torch.cat(actions, dim=-1)

    def _compute_target_q(self, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        with torch.no_grad():
            next_actions, next_log_prob, _ = self._sample_joint_actions(
                batch['next_du_states'], batch['next_global_states']
            )
            q1_next = self.target_critic1(batch['next_global_states'], next_actions)
            q2_next = self.target_critic2(batch['next_global_states'], next_actions)
            min_q_next = torch.min(q1_next, q2_next)
            alpha = self.log_alpha.exp().detach()
            return batch['rewards'] + ((1.0 - batch['dones']) * self.gamma * (min_q_next - (alpha * next_log_prob)))

    def _preview_td_variances(
        self,
        dataset: TransitionTensorDataset,
        preview_batches: int,
        economic_indices: torch.Tensor | None = None,
    ) -> list[float]:
        values: list[float] = []
        if len(dataset) == 0 or (economic_indices is not None and economic_indices.numel() == 0):
            return values
        for _ in range(max(1, preview_batches)):
            indices = self._sample_indices(len(dataset), economic_indices)
            if indices.numel() == 0:
                continue
            batch = self._batch_from_indices(dataset, indices)
            target_q = self._compute_target_q(batch)
            with torch.no_grad():
                q1 = self.critic1(batch['global_states'], batch['behavior_actions'])
                q2 = self.critic2(batch['global_states'], batch['behavior_actions'])
                td_error = target_q - torch.min(q1, q2)
            values.append(float(td_error.var(unbiased=False).item()))
        return values

    def _evaluate(self, dataset: TransitionTensorDataset) -> dict[str, float]:
        if len(dataset) == 0:
            return {
                'cumulative_return': 0.0,
                'eval_return': 0.0,
                'action_var_mean': 0.0,
                'policy_entropy': 0.0,
            }
        with torch.no_grad():
            joint_det = self._deterministic_joint_actions(dataset.du_states, dataset.global_states)
            q1 = self.critic1(dataset.global_states, joint_det)
            q2 = self.critic2(dataset.global_states, joint_det)
            q = torch.min(q1, q2)
            entropies = [actor.entropy(dataset.du_states[:, idx, :]) for idx, actor in enumerate(self.actors)]
            entropies.append(self.global_actor.entropy(dataset.global_states))
            policy_entropy = torch.stack(entropies, dim=0).sum(dim=0).mean().item()
            action_var = torch.var(joint_det, dim=-1).mean().item()
        return {
            'cumulative_return': float(q.sum().item()),
            'eval_return': float(q.mean().item()),
            'action_var_mean': float(action_var),
            'policy_entropy': float(policy_entropy),
        }

    def export_checkpoint(self, output_dir: str | Path, metadata: dict | None = None) -> None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        torch.save(self.actors.state_dict(), out / 'tasam_marl_actors.pt')
        torch.save(self.global_actor.state_dict(), out / 'tasam_marl_global_actor.pt')
        torch.save(self.critic1.state_dict(), out / 'tasam_marl_critic1.pt')
        torch.save(self.critic2.state_dict(), out / 'tasam_marl_critic2.pt')
        torch.save(self.target_critic1.state_dict(), out / 'tasam_marl_target_critic1.pt')
        torch.save(self.target_critic2.state_dict(), out / 'tasam_marl_target_critic2.pt')
        torch.save(self.category_head.state_dict(), out / 'tasam_marl_category_head.pt')
        torch.save(self.power_head.state_dict(), out / 'tasam_marl_power_head.pt')
        torch.save(self.allocation_head.state_dict(), out / 'tasam_marl_allocation_head.pt')
        payload = {
            'backend': 'article_sac',
            'du_count': self.du_count,
            'du_state_dim': self.du_state_dim,
            'global_state_dim': self.global_state_dim,
            'action_layout': list(SLICE_ORDER),
            'joint_action_dim': self.joint_action_dim,
            'global_action_dim': self.global_action_dim,
            'global_action_layout': (
                ['power_du_2', 'power_du_3', 'power_du_4', 'ran_share', 'total_budget_fraction']
                if self.global_action_dim >= 5 else
                ['radio_power_budget', 'compute_budget', 'io_budget']
            ),
            'global_actor_path': 'tasam_marl_global_actor.pt',
            'sam_mode': self.sam_mode,
            'actor_rho_start': self.actor_rho_start,
            'actor_rho_final': self.actor_rho_final,
            'critic_rho_start': self.critic_rho_start,
            'critic_rho_final': self.critic_rho_final,
            'l2_weight': self.l2_weight,
            'uses_twin_critics': True,
            'uses_entropy_alpha': True,
            'uses_decentralized_actors': True,
            'uses_global_energy_infra_actor': True,
            'uses_centralized_critic': True,
            'category_head_path': 'tasam_marl_category_head.pt',
            'category_head_hidden_dim': self.category_head_hidden_dim,
            'category_head_classes': list(CATEGORY_ORDER),
            'category_loss_weight': self.category_loss_weight,
            'category_head_optimizer': 'Adam',
            'category_head_lr': self.category_head_lr,
            'category_head_steps': self.category_head_steps,
            'temporal_dim': self.temporal_dim,
            'temporal_feature_dim': TEMPORAL_FEATURE_DIM,
            'power_head_path': 'tasam_marl_power_head.pt',
            'power_head_hidden_dim': self.power_head_hidden_dim,
            'power_head_classes': list(POWER_LEVELS),
            'power_head_optimizer': 'Adam',
            'power_head_lr': self.power_head_lr,
            'power_head_steps': self.power_head_steps,
            'allocation_head_path': 'tasam_marl_allocation_head.pt',
            'allocation_head_hidden_dim': self.allocation_head_hidden_dim,
            'allocation_head_outputs': (
                ['ran_share', 'ai_share', 'total_budget_fraction']
                if self.allocation_head_output_dim >= 3 else ['ran_share', 'ai_share']
            ),
            'allocation_head_optimizer': 'Adam',
            'allocation_head_lr': self.allocation_head_lr,
            'allocation_head_steps': self.allocation_head_steps,
            'allocation_head_output_dim': self.allocation_head_output_dim,
            'allocation_target_source': (
                'observed_sla_floor_plus_economic_margin'
                if self.allocation_head_output_dim >= 3
                else 'observed_real_completion_targets'
            ),
            'allocation_target_contract': (
                'economic_three_output_v1'
                if self.allocation_head_output_dim >= 3
                else 'legacy_two_output_v1'
            ),
            'legacy_total_budget_fraction_default': (
                1.0 if self.allocation_head_output_dim >= 3 else None
            ),
            'allocation_completion_targets': {'ran': 1.0, 'ai': 1.0},
            'sla_constraint': 'hard_fail_safe',
        }
        if metadata:
            payload.update(metadata)
        (out / 'tasam_marl_checkpoint_meta.json').write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    def training_state_dict(self) -> dict:
        return {
            'actors_state_dict': self.actors.state_dict(),
            'global_actor_state_dict': self.global_actor.state_dict(),
            'critic1_state_dict': self.critic1.state_dict(),
            'critic2_state_dict': self.critic2.state_dict(),
            'target_critic1_state_dict': self.target_critic1.state_dict(),
            'target_critic2_state_dict': self.target_critic2.state_dict(),
            'category_head_state_dict': self.category_head.state_dict(),
            'power_head_state_dict': self.power_head.state_dict(),
            'allocation_head_state_dict': self.allocation_head.state_dict(),
            'actor_optimizer_state': self.actor_opt.state_dict(),
            'category_optimizer_state': self.category_opt.state_dict(),
            'power_optimizer_state': self.power_opt.state_dict(),
            'allocation_optimizer_state': self.allocation_opt.state_dict(),
            'critic_optimizer_state': self.critic_opt.state_dict(),
            'log_alpha': float(self.log_alpha.detach().item()),
            'alpha_optimizer_state': self.alpha_opt.state_dict(),
            'torch_rng_state': torch.get_rng_state(),
            'numpy_rng_state': np.random.get_state(),
        }

    def load_training_state_dict(self, payload: dict) -> None:
        self.actors.load_state_dict((payload or {}).get('actors_state_dict') or {})
        global_actor_state = (payload or {}).get('global_actor_state_dict')
        if global_actor_state:
            self.global_actor.load_state_dict(global_actor_state)
        self.critic1.load_state_dict((payload or {}).get('critic1_state_dict') or {})
        self.critic2.load_state_dict((payload or {}).get('critic2_state_dict') or {})
        self.target_critic1.load_state_dict((payload or {}).get('target_critic1_state_dict') or {})
        self.target_critic2.load_state_dict((payload or {}).get('target_critic2_state_dict') or {})
        try:
            self.actor_opt.load_state_dict((payload or {}).get('actor_optimizer_state') or {})
        except (ValueError, RuntimeError):
            # Old resume payloads predate the category head and have a
            # different actor optimizer parameter layout.
            pass
        try:
            self.critic_opt.load_state_dict((payload or {}).get('critic_optimizer_state') or {})
        except (ValueError, RuntimeError):
            pass
        category_state = (payload or {}).get('category_head_state_dict')
        if category_state:
            try:
                self.category_head.load_state_dict(category_state)
            except (RuntimeError, TypeError):
                pass
        category_optimizer_state = (payload or {}).get('category_optimizer_state') or {}
        if category_optimizer_state:
            try:
                self.category_opt.load_state_dict(category_optimizer_state)
            except (ValueError, RuntimeError):
                pass
        power_state = (payload or {}).get('power_head_state_dict')
        if power_state:
            try:
                self.power_head.load_state_dict(power_state)
            except (RuntimeError, TypeError):
                pass
        power_optimizer_state = (payload or {}).get('power_optimizer_state') or {}
        if power_optimizer_state:
            try:
                self.power_opt.load_state_dict(power_optimizer_state)
            except (ValueError, RuntimeError):
                pass
        allocation_state = (payload or {}).get('allocation_head_state_dict')
        if allocation_state:
            try:
                self.allocation_head.load_state_dict(allocation_state)
            except (RuntimeError, TypeError):
                pass
        allocation_optimizer_state = (payload or {}).get('allocation_optimizer_state') or {}
        if allocation_optimizer_state:
            try:
                self.allocation_opt.load_state_dict(allocation_optimizer_state)
            except (ValueError, RuntimeError):
                pass
        alpha_value = float((payload or {}).get('log_alpha', float(self.log_alpha.detach().item())) or 0.0)
        with torch.no_grad():
            self.log_alpha.fill_(alpha_value)
        alpha_opt_state = (payload or {}).get('alpha_optimizer_state') or {}
        if alpha_opt_state:
            self.alpha_opt.load_state_dict(alpha_opt_state)
        torch_state = (payload or {}).get('torch_rng_state')
        if torch_state is not None:
            torch.set_rng_state(torch_state)
        numpy_state = (payload or {}).get('numpy_rng_state')
        if numpy_state is not None:
            np.random.set_state(numpy_state)

    def train_epoch(
        self,
        records: list[MARLTransitionRecord],
        td_var_threshold: float = 0.01,
        min_selected_fraction: float = 0.1,
        warmup: bool = False,
        bc_weight: float = 0.0,
        value_weight: float = 0.0,
        epoch_progress: float = 0.0,
    ) -> dict[str, float]:
        if not records:
            return {
                'actor_loss': 0.0,
                'critic_loss': 0.0,
                'bc_loss': 0.0,
                'selected_fraction': 0.0,
                'effective_td_var_threshold': 0.0,
                'td_var_mean': 0.0,
                'td_var_max': 0.0,
                'alpha': float(self.log_alpha.exp().item()),
                'cumulative_return': 0.0,
                'eval_return': 0.0,
                'policy_entropy': 0.0,
                'action_var_mean': 0.0,
                'category_accuracy': 0.0,
                'category_loss': 0.0,
                'category_evaluated': 0,
                'category_confusion': {},
                'category_directional_error_rate': 0.0,
                'allocation_head_steps': 0,
                'allocation_head_update_loss': 0.0,
                'allocation_evaluated': 0,
                'allocation_target_ran_mean': 0.0,
                'allocation_target_ai_mean': 0.0,
                'allocation_target_total_mean': 0.0,
                'allocation_prediction_loss': 0.0,
            }

        dataset = self._prepare_dataset(records)
        category_update = self._train_category_head(dataset)
        power_update = self._train_power_head(dataset)
        allocation_update = self._train_allocation_head(dataset)
        economic_indices = torch.nonzero(dataset.economic_weights > 0.0, as_tuple=False).flatten()
        updates = (
            int(self.updates_per_epoch or max(1, math.ceil(int(economic_indices.numel()) / self.batch_size)))
            if economic_indices.numel() > 0 else 0
        )
        preview_td_vars = self._preview_td_variances(
            dataset, preview_batches=min(8, updates), economic_indices=economic_indices
        )
        effective_threshold = calibrate_td_variance_threshold(
            preview_td_vars,
            requested_threshold=td_var_threshold,
            min_selected_fraction=min_selected_fraction,
            warmup=warmup,
        )
        td_var_cap = max(preview_td_vars) if preview_td_vars else max(float(td_var_threshold), 1e-6)

        actor_losses: list[float] = []
        critic_losses: list[float] = []
        bc_losses: list[float] = []
        alpha_losses: list[float] = []
        td_vars: list[float] = []
        selected_updates = 0
        actor_updates = 0

        critic_rho = linear_rho_schedule(self.critic_rho_start, self.critic_rho_final, epoch_progress)
        self.critic_opt.rho = critic_rho

        for step_idx in range(updates):
            batch = self._batch_from_indices(
                dataset, self._sample_indices(len(dataset), economic_indices)
            )
            target_q = self._compute_target_q(batch)
            q1 = self.critic1(batch['global_states'], batch['behavior_actions'])
            q2 = self.critic2(batch['global_states'], batch['behavior_actions'])
            td_error = target_q.detach() - torch.min(q1.detach(), q2.detach())
            batch_td_var = float(td_error.var(unbiased=False).item())
            td_vars.append(batch_td_var)

            critic_loss = F.mse_loss(q1, target_q) + F.mse_loss(q2, target_q) + self._l2_penalty(self.critic1, self.critic2)
            self.critic_opt.zero_grad()
            critic_loss.backward()
            if self._critic_sam_enabled() and self.critic_opt.rho > 0.0:
                self.critic_opt.sam_first_step()
                q1_adv = self.critic1(batch['global_states'], batch['behavior_actions'])
                q2_adv = self.critic2(batch['global_states'], batch['behavior_actions'])
                critic_loss_adv = F.mse_loss(q1_adv, target_q) + F.mse_loss(q2_adv, target_q) + self._l2_penalty(self.critic1, self.critic2)
                self.critic_opt.zero_grad()
                critic_loss_adv.backward()
                self.critic_opt.sam_second_step()
            else:
                self.critic_opt.step()
            critic_losses.append(float(critic_loss.item()))

            if (step_idx + 1) % self.actor_update_interval != 0:
                soft_update(self.target_critic1, self.critic1, self.tau)
                soft_update(self.target_critic2, self.critic2, self.tau)
                continue

            actor_updates += 1
            joint_actions, log_prob, _ = self._sample_joint_actions(
                batch['du_states'], batch['global_states']
            )
            q1_pi = self.critic1(batch['global_states'], joint_actions)
            q2_pi = self.critic2(batch['global_states'], joint_actions)
            min_q_pi = torch.min(q1_pi, q2_pi)
            alpha = self.log_alpha.exp().detach()
            imitation_loss = F.mse_loss(joint_actions, batch['behavior_actions']) if bc_weight > 0.0 else torch.tensor(0.0)
            actor_loss = (
                ((alpha * log_prob) - min_q_pi).mean()
                + (bc_weight * imitation_loss)
                + self._l2_penalty(self.actors, self.global_actor)
            )

            selected = batch_td_var >= effective_threshold or warmup
            actor_rho = linear_rho_schedule(self.actor_rho_start, self.actor_rho_final, epoch_progress)
            if self.sam_mode == 'tasam_selective':
                actor_rho = td_scaled_rho(actor_rho, self.actor_rho_final, batch_td_var, td_var_cap)
            old_actor_rho = self.actor_opt.rho
            self.actor_opt.rho = actor_rho

            self.actor_opt.zero_grad()
            actor_loss.backward()
            if self._actor_sam_enabled(selected=selected, warmup=warmup) and self.actor_opt.rho > 0.0:
                self.actor_opt.sam_first_step()
                joint_actions_adv, log_prob_adv, _ = self._sample_joint_actions(
                    batch['du_states'], batch['global_states']
                )
                q1_adv = self.critic1(batch['global_states'], joint_actions_adv)
                q2_adv = self.critic2(batch['global_states'], joint_actions_adv)
                min_q_adv = torch.min(q1_adv, q2_adv)
                imitation_loss_adv = F.mse_loss(joint_actions_adv, batch['behavior_actions']) if bc_weight > 0.0 else torch.tensor(0.0)
                actor_loss_adv = (
                    ((alpha * log_prob_adv) - min_q_adv).mean()
                    + (bc_weight * imitation_loss_adv)
                    + self._l2_penalty(self.actors, self.global_actor)
                )
                self.actor_opt.zero_grad()
                actor_loss_adv.backward()
                self.actor_opt.sam_second_step()
                if selected:
                    selected_updates += 1
            else:
                self.actor_opt.step()
                if selected and self._actor_sam_enabled(selected=False, warmup=warmup):
                    selected_updates += 1
            self.actor_opt.rho = old_actor_rho
            actor_losses.append(float(actor_loss.item()))
            bc_losses.append(float(imitation_loss.item()) if torch.is_tensor(imitation_loss) else float(imitation_loss))

            alpha_loss = -(self.log_alpha * (log_prob.detach() + self.target_entropy)).mean()
            self.alpha_opt.zero_grad()
            alpha_loss.backward()
            self.alpha_opt.step()
            alpha_losses.append(float(alpha_loss.item()))

            soft_update(self.target_critic1, self.critic1, self.tau)
            soft_update(self.target_critic2, self.critic2, self.tau)

        eval_metrics = self._evaluate(dataset)
        category_metrics = self._category_metrics(dataset)
        return {
            'actor_loss': float(np.mean(actor_losses)) if actor_losses else 0.0,
            'critic_loss': float(np.mean(critic_losses)) if critic_losses else 0.0,
            'bc_loss': float(np.mean(bc_losses)) if bc_losses else 0.0,
            'alpha_loss': float(np.mean(alpha_losses)) if alpha_losses else 0.0,
            'alpha': float(self.log_alpha.exp().item()),
            'selected_fraction': float(selected_updates / max(actor_updates, 1)),
            'selected_agents': float(selected_updates),
            'effective_td_var_threshold': float(effective_threshold),
            'td_var_mean': float(np.mean(td_vars)) if td_vars else 0.0,
            'td_var_max': float(max(td_vars)) if td_vars else 0.0,
            'sam_mode': self.sam_mode,
            'rho_start': float(self.actor_rho_start),
            'rho_final': float(self.actor_rho_final),
            'critic_rho_start': float(self.critic_rho_start),
            'critic_rho_final': float(self.critic_rho_final),
            'rho_actor': float(linear_rho_schedule(self.actor_rho_start, self.actor_rho_final, epoch_progress)),
            'rho_critic': float(critic_rho),
            'reward_mean': float(dataset.rewards.mean().item()),
            'economic_transition_evaluated': int(economic_indices.numel()),
            'economic_transition_excluded': int(len(dataset) - economic_indices.numel()),
            'category_loss_weight': self.category_loss_weight,
            **category_update,
            **power_update,
            **allocation_update,
            **self._allocation_metrics(dataset),
            **eval_metrics,
            **category_metrics,
        }
