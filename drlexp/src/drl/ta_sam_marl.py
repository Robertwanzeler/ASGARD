"""Initial TA-SAM MARL trainer scaffold for GreenRAN article alignment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

import torch
from torch import nn


@dataclass
class MARLRecord:
    global_state: List[float]
    du_states: List[List[float]]
    reward: float


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
        records.append(MARLRecord(global_state=global_state, du_states=du_states, reward=reward))
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
        return self.net(x)


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


class TASAMMultiAgentTrainer:
    def __init__(self, du_count: int, du_state_dim: int, global_state_dim: int, lr: float = 3e-4, rho: float = 0.05) -> None:
        self.actors = nn.ModuleList([ActorNetwork(du_state_dim) for _ in range(du_count)])
        self.critic = GlobalCritic(global_state_dim + (du_count * 3))
        self.actor_opt = SAMOptimizer(self.actors.parameters(), torch.optim.Adam, lr=lr, rho=rho)
        self.critic_opt = SAMOptimizer(self.critic.parameters(), torch.optim.Adam, lr=lr, rho=rho)

    def train_epoch(self, records: List[MARLRecord], td_var_threshold: float = 0.01) -> dict[str, float]:
        if not records:
            return {'actor_loss': 0.0, 'critic_loss': 0.0, 'selected_agents': 0.0}

        actor_losses = []
        critic_losses = []
        selected_agents = 0
        for record in records:
            global_x = torch.tensor(record.global_state, dtype=torch.float32)
            du_inputs = [torch.tensor(du_state, dtype=torch.float32) for du_state in record.du_states]

            actor_actions = []
            td_errors = []
            for actor, du_x in zip(self.actors, du_inputs):
                action = actor(du_x)
                actor_actions.append(action)
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
            if td_variance >= td_var_threshold:
                actor_actions = [actor(du_x) for actor, du_x in zip(self.actors, du_inputs)]
                actor_in = torch.cat([global_x, torch.cat(actor_actions, dim=0)], dim=0)
                actor_value = self.critic(actor_in).squeeze(0)
                actor_loss = -actor_value
                self.actor_opt.zero_grad()
                actor_loss.backward()
                self.actor_opt.step()
                actor_losses.append(float(actor_loss.item()))
                selected_agents += len(record.du_states)

        return {
            'actor_loss': sum(actor_losses) / max(len(actor_losses), 1),
            'critic_loss': sum(critic_losses) / max(len(critic_losses), 1),
            'selected_agents': float(selected_agents),
        }
