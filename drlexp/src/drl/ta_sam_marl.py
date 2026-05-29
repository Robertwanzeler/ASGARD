"""Article-aligned TA-SAM MARL trainer with behavior-cloning anchor for GreenRAN."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

import torch
from torch import nn

SLICE_ORDER = ('eMBB', 'mMTC', 'URLLC')


@dataclass
class MARLRecord:
    global_state: List[float]
    du_states: List[List[float]]
    reward: float
    target_actions: List[List[float]]


def _normalize(values: List[float]) -> List[float]:
    cleaned = [max(0.0, float(v)) for v in values]
    total = sum(cleaned)
    if total <= 1e-9:
        return [1.0 / max(len(cleaned), 1)] * len(cleaned)
    return [v / total for v in cleaned]


def _derive_target_actions(payload: dict) -> List[List[float]]:
    slice_state = payload.get('slice_state') or {}
    slice_budget = {
        slice_id: float((slice_state.get(slice_id) or {}).get('budget_share', 0.0) or 0.0)
        for slice_id in SLICE_ORDER
    }
    fallback_budget = sum(slice_budget.values()) <= 1e-9
    targets: List[List[float]] = []
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


def load_marl_trace(path: str | Path) -> List[MARLRecord]:
    records: List[MARLRecord] = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        du_states = [list((du or {}).get('state_vector', [])) for du in payload.get('du_states', [])]
        if not du_states:
            continue
        global_state = list((payload.get('global_state') or {}).get('state_vector', []))
        reward = float(payload.get('reward_hint', 0.0) or 0.0)
        target_actions = _derive_target_actions(payload)
        if len(target_actions) != len(du_states):
            continue
        records.append(MARLRecord(global_state=global_state, du_states=du_states, reward=reward, target_actions=target_actions))
    return records


class ActorNetwork(nn.Module):
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

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.net(x)
        total = torch.clamp(out.sum(dim=-1, keepdim=True), min=1e-9)
        return out / total


class GlobalCritic(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int = 96) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class SAMOptimizer:
    def __init__(self, params, base_optimizer_cls, rho: float = 0.05, **kwargs) -> None:
        self.base = base_optimizer_cls(params, **kwargs)
        self.rho = rho

    def zero_grad(self) -> None:
        self.base.zero_grad()

    def step(self) -> None:
        self.base.step()


def calibrate_td_variance_threshold(
    td_variances: List[float],
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


class TASAMMultiAgentTrainer:
    def __init__(self, du_count: int, du_state_dim: int, global_state_dim: int, lr: float = 3e-4, rho: float = 0.05) -> None:
        self.actors = nn.ModuleList([ActorNetwork(du_state_dim) for _ in range(du_count)])
        self.critic = GlobalCritic(global_state_dim + (du_count * 3))
        self.actor_opt = SAMOptimizer(self.actors.parameters(), torch.optim.Adam, lr=lr, rho=rho)
        self.critic_opt = SAMOptimizer(self.critic.parameters(), torch.optim.Adam, lr=lr, rho=rho)
        self.du_count = du_count
        self.du_state_dim = du_state_dim
        self.global_state_dim = global_state_dim

    def export_checkpoint(self, output_dir: str | Path, metadata: dict | None = None) -> None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        torch.save(self.actors.state_dict(), out / 'tasam_marl_actors.pt')
        torch.save(self.critic.state_dict(), out / 'tasam_marl_critic.pt')
        payload = {
            'du_count': self.du_count,
            'du_state_dim': self.du_state_dim,
            'global_state_dim': self.global_state_dim,
            'action_layout': list(SLICE_ORDER),
            'actor_output_normalized': True,
        }
        if metadata:
            payload.update(metadata)
        (out / 'tasam_marl_checkpoint_meta.json').write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    def train_epoch(
        self,
        records: List[MARLRecord],
        td_var_threshold: float = 0.01,
        min_selected_fraction: float = 0.1,
        warmup: bool = False,
        bc_weight: float = 1.0,
        value_weight: float = 0.10,
    ) -> dict[str, float]:
        if not records:
            return {
                'actor_loss': 0.0,
                'critic_loss': 0.0,
                'bc_loss': 0.0,
                'action_var_mean': 0.0,
                'selected_agents': 0.0,
                'selected_fraction': 0.0,
                'effective_td_var_threshold': 0.0,
                'td_var_mean': 0.0,
                'td_var_max': 0.0,
            }

        calibration_vars: List[float] = []
        for record in records:
            for actor, du_state in zip(self.actors, record.du_states):
                du_x = torch.tensor(du_state, dtype=torch.float32)
                with torch.no_grad():
                    action = actor(du_x)
                calibration_vars.append(float(torch.var(action).item()))

        effective_threshold = calibrate_td_variance_threshold(
            calibration_vars,
            requested_threshold=td_var_threshold,
            min_selected_fraction=min_selected_fraction,
            warmup=warmup,
        )

        actor_losses = []
        critic_losses = []
        bc_losses = []
        action_vars = []
        selected_agents = 0
        total_agents = 0
        mse = nn.MSELoss()

        for record in records:
            global_x = torch.tensor(record.global_state, dtype=torch.float32)
            du_inputs = [torch.tensor(du_state, dtype=torch.float32) for du_state in record.du_states]
            target_actions = [torch.tensor(target, dtype=torch.float32) for target in record.target_actions]
            total_agents += len(du_inputs)

            actor_actions = []
            td_errors = []
            for actor, du_x in zip(self.actors, du_inputs):
                action = actor(du_x)
                actor_actions.append(action)
                action_vars.append(float(torch.var(action).item()))
                td_errors.append(float(torch.var(action).item()))

            action_tensor_detached = torch.cat([action.detach() for action in actor_actions], dim=0)
            critic_in = torch.cat([global_x, action_tensor_detached], dim=0)
            value = self.critic(critic_in).squeeze(0)
            reward = torch.tensor(record.reward, dtype=torch.float32)
            critic_loss = (value - reward).pow(2)
            self.critic_opt.zero_grad()
            critic_loss.backward()
            self.critic_opt.step()
            critic_losses.append(float(critic_loss.item()))

            td_variance = sum(td_errors) / max(len(td_errors), 1)
            if td_variance >= effective_threshold:
                actor_actions = [actor(du_x) for actor, du_x in zip(self.actors, du_inputs)]
                actor_in = torch.cat([global_x, torch.cat(actor_actions, dim=0)], dim=0)
                actor_value = self.critic(actor_in).squeeze(0)
                bc_loss = sum(mse(action, target) for action, target in zip(actor_actions, target_actions)) / max(len(actor_actions), 1)
                actor_loss = (bc_weight * bc_loss) + (value_weight * (-actor_value))
                self.actor_opt.zero_grad()
                actor_loss.backward()
                self.actor_opt.step()
                actor_losses.append(float(actor_loss.item()))
                bc_losses.append(float(bc_loss.item()))
                selected_agents += len(record.du_states)

        return {
            'actor_loss': sum(actor_losses) / max(len(actor_losses), 1),
            'critic_loss': sum(critic_losses) / max(len(critic_losses), 1),
            'bc_loss': sum(bc_losses) / max(len(bc_losses), 1),
            'action_var_mean': sum(action_vars) / max(len(action_vars), 1),
            'selected_agents': float(selected_agents),
            'selected_fraction': float(selected_agents / max(total_agents, 1)),
            'effective_td_var_threshold': float(effective_threshold),
            'td_var_mean': float(sum(calibration_vars) / max(len(calibration_vars), 1)),
            'td_var_max': float(max(calibration_vars) if calibration_vars else 0.0),
        }
