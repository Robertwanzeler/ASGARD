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


@dataclass
class MARLTransitionRecord:
    global_state: list[float]
    du_states: list[list[float]]
    behavior_actions: list[list[float]]
    reward: float
    next_global_state: list[float]
    next_du_states: list[list[float]]
    done: bool


@dataclass
class TransitionTensorDataset:
    global_states: torch.Tensor
    du_states: torch.Tensor
    behavior_actions: torch.Tensor
    rewards: torch.Tensor
    next_global_states: torch.Tensor
    next_du_states: torch.Tensor
    dones: torch.Tensor

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
        behavior_actions = _derive_target_actions(payload)
        if len(behavior_actions) != len(du_states) or len(next_du_states) != len(du_states):
            continue
        reward = float(payload.get('reward_hint', 0.0) or 0.0)
        quality = payload.get('collection_quality') or {}
        done = bool(quality.get('sim_reset', False))
        records.append(
            MARLTransitionRecord(
                global_state=global_state,
                du_states=du_states,
                behavior_actions=behavior_actions,
                reward=reward,
                next_global_state=next_global_state,
                next_du_states=next_du_states,
                done=done,
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
        seed: int = 42,
    ) -> None:
        self.device = torch.device('cpu')
        torch.manual_seed(int(seed))
        np.random.seed(int(seed))
        self.du_count = int(du_count)
        self.du_state_dim = int(du_state_dim)
        self.global_state_dim = int(global_state_dim)
        self.action_dim = len(SLICE_ORDER)
        self.joint_action_dim = self.du_count * self.action_dim
        self.gamma = float(gamma)
        self.tau = float(tau)
        self.sam_mode = str(sam_mode)
        self.l2_weight = float(l2_weight)
        self.batch_size = max(1, int(batch_size))
        self.updates_per_epoch = updates_per_epoch
        self.actor_update_interval = max(1, int(actor_update_interval))
        self.actor_rho_start = float(actor_rho)
        self.actor_rho_final = float(actor_rho if actor_rho_final is None else actor_rho_final)
        self.critic_rho_start = float(critic_rho)
        self.critic_rho_final = float(critic_rho if critic_rho_final is None else critic_rho_final)
        self.target_entropy = -float(target_entropy_scale) * float(self.joint_action_dim)

        self.actors = nn.ModuleList([
            DirichletActor(du_state_dim, hidden_dims=actor_hidden_dims, action_dim=self.action_dim, activation=activation)
            for _ in range(self.du_count)
        ])
        self.critic1 = CentralizedQNetwork(global_state_dim, self.joint_action_dim, hidden_dims=critic_hidden_dims, activation=activation)
        self.critic2 = CentralizedQNetwork(global_state_dim, self.joint_action_dim, hidden_dims=critic_hidden_dims, activation=activation)
        self.target_critic1 = CentralizedQNetwork(global_state_dim, self.joint_action_dim, hidden_dims=critic_hidden_dims, activation=activation)
        self.target_critic2 = CentralizedQNetwork(global_state_dim, self.joint_action_dim, hidden_dims=critic_hidden_dims, activation=activation)
        self.target_critic1.load_state_dict(self.critic1.state_dict())
        self.target_critic2.load_state_dict(self.critic2.state_dict())

        self.actor_opt = SAMOptimizer(self.actors.parameters(), torch.optim.Adam, lr=lr, rho=self.actor_rho_start)
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
        return TransitionTensorDataset(
            global_states=global_states,
            du_states=du_states,
            behavior_actions=behavior_actions,
            rewards=rewards,
            next_global_states=next_global_states,
            next_du_states=next_du_states,
            dones=dones,
        )

    def _sample_indices(self, dataset_size: int) -> torch.Tensor:
        batch_size = min(self.batch_size, dataset_size)
        return torch.randint(0, dataset_size, (batch_size,), dtype=torch.int64)

    def _batch_from_indices(self, dataset: TransitionTensorDataset, indices: torch.Tensor) -> dict[str, torch.Tensor]:
        return {
            'global_states': dataset.global_states[indices],
            'du_states': dataset.du_states[indices],
            'behavior_actions': dataset.behavior_actions[indices].reshape(indices.shape[0], -1),
            'rewards': dataset.rewards[indices],
            'next_global_states': dataset.next_global_states[indices],
            'next_du_states': dataset.next_du_states[indices],
            'dones': dataset.dones[indices],
        }

    def _sample_joint_actions(self, du_states: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, list[torch.Tensor]]:
        actions = []
        log_probs = []
        for idx, actor in enumerate(self.actors):
            action, log_prob, _ = actor.sample(du_states[:, idx, :])
            actions.append(action)
            log_probs.append(log_prob)
        joint_actions = torch.cat(actions, dim=-1)
        joint_log_prob = torch.stack(log_probs, dim=0).sum(dim=0)
        return joint_actions, joint_log_prob, actions

    def _deterministic_joint_actions(self, du_states: torch.Tensor) -> torch.Tensor:
        actions = [actor.deterministic(du_states[:, idx, :]) for idx, actor in enumerate(self.actors)]
        return torch.cat(actions, dim=-1)

    def _compute_target_q(self, batch: dict[str, torch.Tensor]) -> torch.Tensor:
        with torch.no_grad():
            next_actions, next_log_prob, _ = self._sample_joint_actions(batch['next_du_states'])
            q1_next = self.target_critic1(batch['next_global_states'], next_actions)
            q2_next = self.target_critic2(batch['next_global_states'], next_actions)
            min_q_next = torch.min(q1_next, q2_next)
            alpha = self.log_alpha.exp().detach()
            return batch['rewards'] + ((1.0 - batch['dones']) * self.gamma * (min_q_next - (alpha * next_log_prob)))

    def _preview_td_variances(
        self,
        dataset: TransitionTensorDataset,
        preview_batches: int,
    ) -> list[float]:
        values: list[float] = []
        if len(dataset) == 0:
            return values
        for _ in range(max(1, preview_batches)):
            batch = self._batch_from_indices(dataset, self._sample_indices(len(dataset)))
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
            joint_det = self._deterministic_joint_actions(dataset.du_states)
            q1 = self.critic1(dataset.global_states, joint_det)
            q2 = self.critic2(dataset.global_states, joint_det)
            q = torch.min(q1, q2)
            entropies = [actor.entropy(dataset.du_states[:, idx, :]) for idx, actor in enumerate(self.actors)]
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
        torch.save(self.critic1.state_dict(), out / 'tasam_marl_critic1.pt')
        torch.save(self.critic2.state_dict(), out / 'tasam_marl_critic2.pt')
        torch.save(self.target_critic1.state_dict(), out / 'tasam_marl_target_critic1.pt')
        torch.save(self.target_critic2.state_dict(), out / 'tasam_marl_target_critic2.pt')
        payload = {
            'backend': 'article_sac',
            'du_count': self.du_count,
            'du_state_dim': self.du_state_dim,
            'global_state_dim': self.global_state_dim,
            'action_layout': list(SLICE_ORDER),
            'joint_action_dim': self.joint_action_dim,
            'sam_mode': self.sam_mode,
            'actor_rho_start': self.actor_rho_start,
            'actor_rho_final': self.actor_rho_final,
            'critic_rho_start': self.critic_rho_start,
            'critic_rho_final': self.critic_rho_final,
            'l2_weight': self.l2_weight,
            'uses_twin_critics': True,
            'uses_entropy_alpha': True,
            'uses_decentralized_actors': True,
            'uses_centralized_critic': True,
        }
        if metadata:
            payload.update(metadata)
        (out / 'tasam_marl_checkpoint_meta.json').write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    def training_state_dict(self) -> dict:
        return {
            'actors_state_dict': self.actors.state_dict(),
            'critic1_state_dict': self.critic1.state_dict(),
            'critic2_state_dict': self.critic2.state_dict(),
            'target_critic1_state_dict': self.target_critic1.state_dict(),
            'target_critic2_state_dict': self.target_critic2.state_dict(),
            'actor_optimizer_state': self.actor_opt.state_dict(),
            'critic_optimizer_state': self.critic_opt.state_dict(),
            'log_alpha': float(self.log_alpha.detach().item()),
            'alpha_optimizer_state': self.alpha_opt.state_dict(),
            'torch_rng_state': torch.get_rng_state(),
            'numpy_rng_state': np.random.get_state(),
        }

    def load_training_state_dict(self, payload: dict) -> None:
        self.actors.load_state_dict((payload or {}).get('actors_state_dict') or {})
        self.critic1.load_state_dict((payload or {}).get('critic1_state_dict') or {})
        self.critic2.load_state_dict((payload or {}).get('critic2_state_dict') or {})
        self.target_critic1.load_state_dict((payload or {}).get('target_critic1_state_dict') or {})
        self.target_critic2.load_state_dict((payload or {}).get('target_critic2_state_dict') or {})
        self.actor_opt.load_state_dict((payload or {}).get('actor_optimizer_state') or {})
        self.critic_opt.load_state_dict((payload or {}).get('critic_optimizer_state') or {})
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
            }

        dataset = self._prepare_dataset(records)
        updates = int(self.updates_per_epoch or max(1, math.ceil(len(dataset) / self.batch_size)))
        preview_td_vars = self._preview_td_variances(dataset, preview_batches=min(8, updates))
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
            batch = self._batch_from_indices(dataset, self._sample_indices(len(dataset)))
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
            joint_actions, log_prob, _ = self._sample_joint_actions(batch['du_states'])
            q1_pi = self.critic1(batch['global_states'], joint_actions)
            q2_pi = self.critic2(batch['global_states'], joint_actions)
            min_q_pi = torch.min(q1_pi, q2_pi)
            alpha = self.log_alpha.exp().detach()
            imitation_loss = F.mse_loss(joint_actions, batch['behavior_actions']) if bc_weight > 0.0 else torch.tensor(0.0)
            actor_loss = ((alpha * log_prob) - min_q_pi).mean() + (bc_weight * imitation_loss) + self._l2_penalty(self.actors)

            selected = batch_td_var >= effective_threshold or warmup
            if self.sam_mode == 'tasam_selective' and not selected and not warmup and selected_updates == 0 and (step_idx + self.actor_update_interval >= updates):
                selected = True
            actor_rho = linear_rho_schedule(self.actor_rho_start, self.actor_rho_final, epoch_progress)
            if self.sam_mode == 'tasam_selective':
                actor_rho = td_scaled_rho(actor_rho, self.actor_rho_final, batch_td_var, td_var_cap)
            old_actor_rho = self.actor_opt.rho
            self.actor_opt.rho = actor_rho

            self.actor_opt.zero_grad()
            actor_loss.backward()
            if self._actor_sam_enabled(selected=selected, warmup=warmup) and self.actor_opt.rho > 0.0:
                self.actor_opt.sam_first_step()
                joint_actions_adv, log_prob_adv, _ = self._sample_joint_actions(batch['du_states'])
                q1_adv = self.critic1(batch['global_states'], joint_actions_adv)
                q2_adv = self.critic2(batch['global_states'], joint_actions_adv)
                min_q_adv = torch.min(q1_adv, q2_adv)
                imitation_loss_adv = F.mse_loss(joint_actions_adv, batch['behavior_actions']) if bc_weight > 0.0 else torch.tensor(0.0)
                actor_loss_adv = ((alpha * log_prob_adv) - min_q_adv).mean() + (bc_weight * imitation_loss_adv) + self._l2_penalty(self.actors)
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
            **eval_metrics,
        }
