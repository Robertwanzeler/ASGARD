"""Article-aligned TA-SAM MARL trainer with behavior-cloning anchor for GreenRAN."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

import numpy as np
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
    def __init__(
        self,
        input_dim: int,
        hidden_dims: Sequence[int] = (64, 64),
        action_dim: int = 3,
        activation: str = 'relu',
    ) -> None:
        super().__init__()
        act_cls = nn.Tanh if activation == 'tanh' else nn.ReLU
        layers: list[nn.Module] = []
        prev_dim = input_dim
        for hidden_dim in hidden_dims:
            layers.extend([nn.Linear(prev_dim, int(hidden_dim)), act_cls()])
            prev_dim = int(hidden_dim)
        layers.extend([nn.Linear(prev_dim, action_dim), nn.Sigmoid()])
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.net(x)
        total = torch.clamp(out.sum(dim=-1, keepdim=True), min=1e-9)
        return out / total


class GlobalCritic(nn.Module):
    def __init__(
        self,
        input_dim: int,
        hidden_dims: Sequence[int] = (96, 96),
        activation: str = 'relu',
    ) -> None:
        super().__init__()
        act_cls = nn.Tanh if activation == 'tanh' else nn.ReLU
        layers: list[nn.Module] = []
        prev_dim = input_dim
        for hidden_dim in hidden_dims:
            layers.extend([nn.Linear(prev_dim, int(hidden_dim)), act_cls()])
            prev_dim = int(hidden_dim)
        layers.append(nn.Linear(prev_dim, 1))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class SAMOptimizer:
    def __init__(self, params, base_optimizer_cls, rho: float = 0.05, **kwargs) -> None:
        self.params = list(params)
        self.base = base_optimizer_cls(self.params, **kwargs)
        self.rho = rho

    def zero_grad(self) -> None:
        self.base.zero_grad()

    def step(self) -> None:
        self.base.step()

    def sam_first_step(self) -> None:
        self._saved = [p.data.clone() for p in self.params if p.grad is not None]
        grad_norm = math.sqrt(sum(p.grad.norm().item() ** 2 for p in self.params if p.grad is not None)) + 1e-12
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


def linear_rho_schedule(rho_start: float, rho_final: float, progress: float) -> float:
    progress = max(0.0, min(1.0, float(progress)))
    return float(rho_start) + ((float(rho_final) - float(rho_start)) * progress)


def td_scaled_rho(rho_start: float, rho_final: float, td_value: float, td_max: float) -> float:
    if td_max <= 1e-12:
        return float(rho_final)
    scale = max(0.0, min(1.0, float(td_value) / float(td_max)))
    return linear_rho_schedule(rho_final, rho_start, scale)


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
    def __init__(
        self,
        du_count: int,
        du_state_dim: int,
        global_state_dim: int,
        lr: float = 1e-4,
        rho: float = 0.05,
        rho_final: float | None = None,
        sam_mode: str = 'tasam_selective',
        l2_weight: float = 0.0,
        actor_hidden_dims: Sequence[int] = (64, 64),
        critic_hidden_dims: Sequence[int] = (96, 96),
        activation: str = 'relu',
    ) -> None:
        self.actors = nn.ModuleList([
            ActorNetwork(du_state_dim, hidden_dims=actor_hidden_dims, activation=activation)
            for _ in range(du_count)
        ])
        self.critic = GlobalCritic(global_state_dim + (du_count * 3), hidden_dims=critic_hidden_dims, activation=activation)
        self.actor_opt = SAMOptimizer(self.actors.parameters(), torch.optim.Adam, lr=lr, rho=rho)
        self.critic_opt = SAMOptimizer(self.critic.parameters(), torch.optim.Adam, lr=lr, rho=rho)
        self.du_count = du_count
        self.du_state_dim = du_state_dim
        self.global_state_dim = global_state_dim
        self.rho_start = float(rho)
        self.rho_final = float(rho if rho_final is None else rho_final)
        self.sam_mode = str(sam_mode)
        self.l2_weight = float(l2_weight)

    def _sam_enabled_for_actor(self, selected: bool, warmup: bool) -> bool:
        if self.sam_mode in {'none', 'no_sam', 'l2'}:
            return False
        if self.sam_mode in {'actor_sam', 'both_sam'}:
            return True
        if self.sam_mode == 'critic_sam':
            return False
        return bool(selected or warmup)

    def _sam_enabled_for_critic(self) -> bool:
        return self.sam_mode in {'critic_sam', 'both_sam', 'tasam_selective'}

    def _l2_penalty(self, module: nn.Module) -> torch.Tensor:
        if self.l2_weight <= 0.0:
            return torch.tensor(0.0)
        penalty = None
        for param in module.parameters():
            value = param.pow(2).sum()
            penalty = value if penalty is None else penalty + value
        if penalty is None:
            return torch.tensor(0.0)
        return penalty * self.l2_weight

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
            'sam_mode': self.sam_mode,
            'rho_start': self.rho_start,
            'rho_final': self.rho_final,
            'l2_weight': self.l2_weight,
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
        epoch_progress: float = 0.0,
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

        mse = nn.MSELoss()

        first_pass_td_errors: List[float] = []
        first_pass_records: List[tuple] = []
        action_vars: List[float] = []
        for record in records:
            global_x = torch.tensor(record.global_state, dtype=torch.float32)
            du_inputs = [torch.tensor(du_state, dtype=torch.float32) for du_state in record.du_states]
            target_actions = [torch.tensor(target, dtype=torch.float32) for target in record.target_actions]
            reward = torch.tensor(record.reward, dtype=torch.float32)
            with torch.no_grad():
                actor_actions = [actor(du_x) for actor, du_x in zip(self.actors, du_inputs)]
                action_vars.extend(float(torch.var(action).item()) for action in actor_actions)
                action_tensor = torch.cat([a for a in actor_actions], dim=0)
                critic_in = torch.cat([global_x, action_tensor], dim=0)
                value = self.critic(critic_in).squeeze(0)
                td_error = float(abs(value.item() - record.reward))
            first_pass_td_errors.append(td_error)
            n_du = len(du_inputs)
            first_pass_records.append((global_x, du_inputs, target_actions, reward, n_du, td_error))

        effective_threshold = calibrate_td_variance_threshold(
            first_pass_td_errors,
            requested_threshold=td_var_threshold,
            min_selected_fraction=min_selected_fraction,
            warmup=warmup,
        )

        actor_losses = []
        critic_losses = []
        bc_losses = []
        selected_agents = 0
        total_agents = 0
        selected_indices: List[int] = []

        for idx, (global_x, du_inputs, target_actions, reward, n_du, td_error) in enumerate(first_pass_records):
            total_agents += n_du
            actor_actions = [actor(du_x) for actor, du_x in zip(self.actors, du_inputs)]
            action_tensor = torch.cat(actor_actions, dim=0).detach()
            critic_in = torch.cat([global_x, action_tensor], dim=0)
            value = self.critic(critic_in).squeeze(0)
            critic_loss = (value - reward).pow(2) + self._l2_penalty(self.critic)
            self.critic_opt.zero_grad()
            critic_loss.backward()
            if self._sam_enabled_for_critic() and self.critic_opt.rho > 0:
                self.critic_opt.sam_first_step()
                value_adv = self.critic(critic_in).squeeze(0)
                critic_loss_adv = (value_adv - reward).pow(2) + self._l2_penalty(self.critic)
                self.critic_opt.zero_grad()
                critic_loss_adv.backward()
                self.critic_opt.sam_second_step()
            else:
                self.critic_opt.step()
            critic_losses.append(float(critic_loss.item()))

            if td_error >= effective_threshold or warmup:
                selected_indices.append(idx)
                selected_agents += n_du

        if self.sam_mode in {'none', 'no_sam', 'l2', 'actor_sam', 'critic_sam', 'both_sam'}:
            selected_indices = list(range(len(first_pass_records)))
            selected_agents = total_agents

        # Se nada foi selecionado fora do warmup, forcar top 10% por TD error.
        if self.sam_mode == 'tasam_selective' and not selected_indices and not warmup and first_pass_records:
            n_force = max(1, round(len(first_pass_records) * min_selected_fraction))
            sorted_idx = sorted(
                range(len(first_pass_records)),
                key=lambda i: first_pass_td_errors[i],
                reverse=True,
            )[:n_force]
            selected_indices = sorted_idx
            selected_agents = sum(first_pass_records[i][4] for i in sorted_idx)

        for idx in selected_indices:
            global_x, du_inputs, target_actions, reward, n_du, td_error = first_pass_records[idx]
            actor_actions = [actor(du_x) for actor, du_x in zip(self.actors, du_inputs)]
            actor_in = torch.cat([global_x, torch.cat(actor_actions, dim=0)], dim=0)
            actor_value = self.critic(actor_in).squeeze(0)
            bc_loss_val = sum(mse(a, t) for a, t in zip(actor_actions, target_actions)) / max(len(actor_actions), 1)
            actor_loss = (bc_weight * bc_loss_val) + (value_weight * (-actor_value)) + self._l2_penalty(self.actors)

            self.actor_opt.zero_grad()
            actor_loss.backward()
            old_actor_rho = self.actor_opt.rho
            if self.sam_mode == 'tasam_selective':
                epoch_rho = linear_rho_schedule(self.rho_start, self.rho_final, epoch_progress)
                td_max = max(first_pass_td_errors) if first_pass_td_errors else td_error
                self.actor_opt.rho = td_scaled_rho(epoch_rho, self.rho_final, td_error, td_max)
            if self._sam_enabled_for_actor(selected=True, warmup=warmup) and self.actor_opt.rho > 0:
                self.actor_opt.sam_first_step()
                actor_actions_adv = [actor(du_x) for actor, du_x in zip(self.actors, du_inputs)]
                actor_in_adv = torch.cat([global_x, torch.cat(actor_actions_adv, dim=0)], dim=0)
                actor_value_adv = self.critic(actor_in_adv).squeeze(0)
                bc_loss_adv = sum(mse(a, t) for a, t in zip(actor_actions_adv, target_actions)) / max(len(actor_actions_adv), 1)
                actor_loss_adv = (bc_weight * bc_loss_adv) + (value_weight * (-actor_value_adv)) + self._l2_penalty(self.actors)
                self.actor_opt.zero_grad()
                actor_loss_adv.backward()
                self.actor_opt.sam_second_step()
            else:
                self.actor_opt.step()
            self.actor_opt.rho = old_actor_rho

            actor_losses.append(float(actor_loss.item()))
            bc_losses.append(float(bc_loss_val.item()))

        return {
            'actor_loss': sum(actor_losses) / max(len(actor_losses), 1),
            'critic_loss': sum(critic_losses) / max(len(critic_losses), 1),
            'bc_loss': sum(bc_losses) / max(len(bc_losses), 1),
            'action_var_mean': sum(action_vars) / max(len(action_vars), 1),
            'selected_agents': float(selected_agents),
            'selected_fraction': float(selected_agents / max(total_agents, 1)),
            'effective_td_var_threshold': float(effective_threshold),
            'td_var_mean': float(np.mean(first_pass_td_errors)) if first_pass_td_errors else 0.0,
            'td_var_max': float(max(first_pass_td_errors)) if first_pass_td_errors else 0.0,
            'sam_mode': self.sam_mode,
            'rho_start': float(self.rho_start),
            'rho_final': float(self.rho_final),
        }
