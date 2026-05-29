#!/usr/bin/env python3
"""
Offline SAC bootstrap trainer for the CAORA-style GreenRAN migration.

Based on the SAC resource allocation formulation from [1], adapted for
offline bootstrap training with behavior cloning (BC) warm-start and
SAC/AWAC refinement.

[1] Lotfi, F., Rajoli, H. & Afghah, F. "Task-Specific Sharpness-Aware O-RAN
    Resource Management using Multi-Agent Reinforcement Learning".
    IEEE TMLCN, 2025. arXiv:2511.15002.

Pipeline:
1. Load the real CAORA-style workload trace exported from the current scenario.
2. Rebuild sequential transitions (state, action, reward, next_state, done).
3. Pretrain the actor with behavior cloning on the heuristic controller trace.
4. Refine the policy with an offline SAC loop (twin critics + entropy tuning).
5. Evaluate the deterministic actor on the workload environment rollout.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn
from torch.nn.utils import clip_grad_norm_
from torch.utils.data import DataLoader, TensorDataset

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from drl.caora_sac_environment import CAORASACEnv, load_workload_trace


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline SAC bootstrap trainer for GreenRAN")
    parser.add_argument("--workload-csv", required=True, help="CSV exported from export_sac_workload_trace.py")
    parser.add_argument("--output-dir", default="runs/sac_bootstrap/sac_offline", help="Training artifact directory")
    parser.add_argument("--delta-step", type=float, default=0.1, help="Action scaling used by the runtime model")
    parser.add_argument("--offline-mode", choices=["sac", "awac"], default="awac", help="Offline refinement mode for the actor")
    parser.add_argument("--bc-epochs", type=int, default=80, help="Behavior cloning warm-start epochs")
    parser.add_argument("--sac-epochs", type=int, default=140, help="Offline SAC refinement epochs")
    parser.add_argument("--batch-size", type=int, default=32, help="Mini-batch size")
    parser.add_argument("--actor-lr", type=float, default=1e-4, help="Actor optimizer learning rate (artigo: 1e-4)")
    parser.add_argument("--critic-lr", type=float, default=1e-4, help="Critic optimizer learning rate (artigo: 1e-4)")
    parser.add_argument("--alpha-lr", type=float, default=1e-4, help="Entropy temperature learning rate (artigo: 1e-4)")
    parser.add_argument("--gamma", type=float, default=0.99, help="Discount factor")
    parser.add_argument("--tau", type=float, default=0.01, help="Target critic soft-update rate")
    parser.add_argument("--bc-weight", type=float, default=0.15, help="Behavior-cloning regularization during SAC")
    parser.add_argument("--bc-anchor-weight", type=float, default=1.5, help="Anchor the actor to the BC policy outputs during offline refinement")
    parser.add_argument("--critic-warmup-epochs", type=int, default=12, help="Epochs that only update critics before actor refinement starts")
    parser.add_argument("--actor-update-interval", type=int, default=4, help="Update the actor every N critic steps")
    parser.add_argument("--awac-lambda", type=float, default=0.25, help="Advantage temperature for AWAC-style weighted regression")
    parser.add_argument("--awac-max-weight", type=float, default=12.0, help="Maximum importance weight for AWAC-style actor updates")
    parser.add_argument("--grad-clip", type=float, default=1.0, help="Gradient clipping norm")
    parser.add_argument("--alpha-init", type=float, default=0.03, help="Initial entropy temperature")
    parser.add_argument("--alpha-min", type=float, default=1e-4, help="Minimum entropy temperature")
    parser.add_argument("--alpha-max", type=float, default=0.5, help="Maximum entropy temperature")
    parser.add_argument("--target-entropy-scale", type=float, default=0.25, help="Scale applied to action_size for target entropy")
    parser.add_argument("--normalize-rewards", action="store_true", default=True, help="Normalize rewards using train-split statistics")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    return parser.parse_args()


@dataclass
class TransitionDataset:
    train_states: torch.Tensor
    train_actions: torch.Tensor
    train_rewards: torch.Tensor
    train_next_states: torch.Tensor
    train_dones: torch.Tensor
    val_states: torch.Tensor
    val_actions: torch.Tensor
    val_rewards: torch.Tensor
    val_next_states: torch.Tensor
    val_dones: torch.Tensor
    all_states: torch.Tensor
    all_actions: torch.Tensor
    all_rewards: torch.Tensor
    all_next_states: torch.Tensor
    all_dones: torch.Tensor


class GaussianActor(nn.Module):
    def __init__(self, state_size: int = 5, action_size: int = 2) -> None:
        super().__init__()
        self.backbone = nn.Sequential(
            nn.Linear(state_size, 300),
            nn.Tanh(),
            nn.Linear(300, 400),
            nn.Tanh(),
            nn.Linear(400, 400),
            nn.Tanh(),
        )
        self.mean_head = nn.Linear(400, action_size)
        self.log_std_head = nn.Linear(400, action_size)

    def forward(self, states: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        hidden = self.backbone(states)
        mean = self.mean_head(hidden)
        log_std = torch.clamp(self.log_std_head(hidden), min=-5.0, max=1.5)
        return mean, log_std

    def sample(self, states: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        mean, log_std = self(states)
        std = log_std.exp()
        normal = torch.distributions.Normal(mean, std)
        z = normal.rsample()
        action = torch.tanh(z)
        log_prob = normal.log_prob(z) - torch.log(1 - action.pow(2) + 1e-6)
        log_prob = log_prob.sum(dim=-1, keepdim=True)
        mean_action = torch.tanh(mean)
        return action, log_prob, mean_action

    def deterministic(self, states: torch.Tensor) -> torch.Tensor:
        mean, _ = self(states)
        return torch.tanh(mean)


class QNetwork(nn.Module):
    def __init__(self, state_size: int = 5, action_size: int = 2) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_size + action_size, 300),
            nn.Tanh(),
            nn.Linear(300, 400),
            nn.Tanh(),
            nn.Linear(400, 400),
            nn.Tanh(),
            nn.Linear(400, 1),
        )

    def forward(self, states: torch.Tensor, actions: torch.Tensor) -> torch.Tensor:
        return self.net(torch.cat([states, actions], dim=-1))


def seed_everything(seed: int) -> None:
    np.random.seed(seed)
    torch.manual_seed(seed)


def _reward_from_row(row: dict[str, str]) -> float:
    ran_comp = float(row["ran_completion_ratio"])
    ai_comp = float(row["ai_completion_ratio"])
    usable = float(row.get("usable_budget", 1.0) or 1.0)
    r_ran = float(row["r_ran"])
    r_ai = float(row["r_ai"])
    alpha_ran = 4.0
    alpha_ai = 2.5
    beta_res = 2.0
    gamma_minqos = 5.0
    qos_min_ran = 0.7
    qos_min_ai = 0.5
    sig_ran = 1.0 / (1.0 + np.exp(-alpha_ran * ran_comp))
    sig_ai = 1.0 / (1.0 + np.exp(-alpha_ai * ai_comp))
    qos_term = (sig_ran + sig_ai) / 2.0
    usado = r_ran + r_ai
    excesso = max(0.0, usado - usable)
    res_penalty = -beta_res * excesso
    abaixo = max(0.0, qos_min_ran - ran_comp) + max(0.0, qos_min_ai - ai_comp)
    min_qos_penalty = -gamma_minqos * abaixo
    return qos_term + res_penalty + min_qos_penalty


def _state_action_from_row(row: dict[str, str], delta_step: float) -> tuple[list[float], list[float]]:
    d_ran = float(row["d_ran"])
    d_ai = float(row["d_ai"])
    usable_budget = float(row.get("usable_budget", 1.0) or 1.0)
    r_ran = float(row["r_ran"])
    r_ai = float(row["r_ai"])
    delta_r_ran = float(row["delta_r_ran"])
    delta_r_ai = float(row["delta_r_ai"])
    prev_r_ran = r_ran - delta_r_ran
    prev_r_ai = r_ai - delta_r_ai

    state = [d_ran, d_ai, prev_r_ran, prev_r_ai, usable_budget]
    action = [
        float(np.clip(delta_r_ran / max(delta_step, 1e-6), -1.0, 1.0)),
        float(np.clip(delta_r_ai / max(delta_step, 1e-6), -1.0, 1.0)),
    ]
    return state, action


def load_transition_dataset(csv_path: str | Path, delta_step: float) -> TransitionDataset:
    with Path(csv_path).open("r", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) < 2:
        raise ValueError("At least 2 rows are required to build sequential SAC transitions")

    states = []
    actions = []
    rewards = []
    next_states = []
    dones = []

    parsed = [_state_action_from_row(row, delta_step) for row in rows]
    row_rewards = [_reward_from_row(row) for row in rows]
    for idx, (state, action) in enumerate(parsed):
        done = idx == len(parsed) - 1
        next_state = parsed[idx + 1][0] if not done else state
        states.append(state)
        actions.append(action)
        rewards.append(row_rewards[idx])
        next_states.append(next_state)
        dones.append(1.0 if done else 0.0)

    states_t = torch.tensor(states, dtype=torch.float32)
    actions_t = torch.tensor(actions, dtype=torch.float32)
    rewards_t = torch.tensor(rewards, dtype=torch.float32).unsqueeze(1)
    next_states_t = torch.tensor(next_states, dtype=torch.float32)
    dones_t = torch.tensor(dones, dtype=torch.float32).unsqueeze(1)

    split_idx = max(1, int(len(states_t) * 0.8))
    if split_idx >= len(states_t):
        split_idx = max(1, len(states_t) - 1)

    return TransitionDataset(
        train_states=states_t[:split_idx],
        train_actions=actions_t[:split_idx],
        train_rewards=rewards_t[:split_idx],
        train_next_states=next_states_t[:split_idx],
        train_dones=dones_t[:split_idx],
        val_states=states_t[split_idx:],
        val_actions=actions_t[split_idx:],
        val_rewards=rewards_t[split_idx:],
        val_next_states=next_states_t[split_idx:],
        val_dones=dones_t[split_idx:],
        all_states=states_t,
        all_actions=actions_t,
        all_rewards=rewards_t,
        all_next_states=next_states_t,
        all_dones=dones_t,
    )


def soft_update(target: nn.Module, source: nn.Module, tau: float) -> None:
    with torch.no_grad():
        for target_param, source_param in zip(target.parameters(), source.parameters()):
            target_param.data.mul_(1.0 - tau).add_(tau * source_param.data)


def evaluate_actor(actor: GaussianActor, env: CAORASACEnv) -> dict[str, float]:
    obs, _ = env.reset()
    total_reward = 0.0
    utilization_samples = []
    ran_completion_samples = []
    ai_completion_samples = []

    while True:
        with torch.no_grad():
            action = actor.deterministic(torch.tensor(obs, dtype=torch.float32).unsqueeze(0)).squeeze(0).cpu().numpy()
        next_obs, reward, terminated, truncated, info = env.step(action)
        total_reward += reward
        utilization_samples.append(info["utilization"])
        ran_completion_samples.append(info["ran_completion"])
        ai_completion_samples.append(info["ai_completion"])
        obs = next_obs
        if terminated or truncated:
            break

    return {
        "rollout_reward": float(total_reward),
        "avg_utilization": float(np.mean(utilization_samples)) if utilization_samples else 0.0,
        "avg_ran_completion": float(np.mean(ran_completion_samples)) if ran_completion_samples else 0.0,
        "avg_ai_completion": float(np.mean(ai_completion_samples)) if ai_completion_samples else 0.0,
    }


def run_behavior_cloning(
    actor: GaussianActor,
    dataset: TransitionDataset,
    epochs: int,
    batch_size: int,
    learning_rate: float,
) -> dict[str, list[float] | float]:
    train_ds = TensorDataset(dataset.train_states, dataset.train_actions)
    train_loader = DataLoader(train_ds, batch_size=min(batch_size, len(train_ds)), shuffle=True)
    optimizer = torch.optim.Adam(actor.parameters(), lr=learning_rate)
    train_curve: list[float] = []
    val_curve: list[float] = []
    best_val = float("inf")
    best_state = None

    for _ in range(epochs):
        actor.train()
        batch_losses = []
        for batch_states, batch_actions in train_loader:
            pred = actor.deterministic(batch_states)
            loss = F.mse_loss(pred, batch_actions)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            batch_losses.append(float(loss.item()))
        train_loss = float(np.mean(batch_losses)) if batch_losses else 0.0
        train_curve.append(train_loss)

        actor.eval()
        with torch.no_grad():
            val_pred = actor.deterministic(dataset.val_states)
            val_loss = float(F.mse_loss(val_pred, dataset.val_actions).item()) if len(dataset.val_states) else train_loss
        val_curve.append(val_loss)

        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.detach().cpu().clone() for k, v in actor.state_dict().items()}

    if best_state is not None:
        actor.load_state_dict(best_state)

    actor.eval()
    with torch.no_grad():
        val_pred = actor.deterministic(dataset.val_states)
        val_action_mae = float(torch.mean(torch.abs(val_pred - dataset.val_actions)).item()) if len(dataset.val_states) else 0.0
        all_pred = actor.deterministic(dataset.all_states)
        action_mae = float(torch.mean(torch.abs(all_pred - dataset.all_actions)).item())

    return {
        "train_curve": train_curve,
        "val_curve": val_curve,
        "best_val_loss": best_val,
        "val_action_mae": val_action_mae,
        "action_mae": action_mae,
        "best_state": {k: v.detach().cpu().clone() for k, v in actor.state_dict().items()},
    }


def run_offline_sac(
    actor: GaussianActor,
    dataset: TransitionDataset,
    args: argparse.Namespace,
    reference_actor_state: dict[str, torch.Tensor],
) -> dict[str, list[float] | float | nn.Module]:
    state_size = dataset.all_states.shape[1]
    action_size = dataset.all_actions.shape[1]

    reference_actor = GaussianActor(state_size=state_size, action_size=action_size)
    reference_actor.load_state_dict(reference_actor_state)
    reference_actor.eval()
    for param in reference_actor.parameters():
        param.requires_grad_(False)

    critic1 = QNetwork(state_size=state_size, action_size=action_size)
    critic2 = QNetwork(state_size=state_size, action_size=action_size)
    target_critic1 = QNetwork(state_size=state_size, action_size=action_size)
    target_critic2 = QNetwork(state_size=state_size, action_size=action_size)
    target_critic1.load_state_dict(critic1.state_dict())
    target_critic2.load_state_dict(critic2.state_dict())

    critic1_opt = torch.optim.Adam(critic1.parameters(), lr=args.critic_lr)
    critic2_opt = torch.optim.Adam(critic2.parameters(), lr=args.critic_lr)
    actor_opt = torch.optim.Adam(actor.parameters(), lr=args.actor_lr)

    alpha_init = float(np.clip(args.alpha_init, args.alpha_min, args.alpha_max))
    log_alpha = torch.tensor(math.log(alpha_init), dtype=torch.float32, requires_grad=True)
    alpha_opt = torch.optim.Adam([log_alpha], lr=args.alpha_lr)
    target_entropy = -(float(action_size) * float(args.target_entropy_scale))
    min_log_alpha = math.log(max(args.alpha_min, 1e-8))
    max_log_alpha = math.log(max(args.alpha_max, args.alpha_min + 1e-8))

    train_rewards = dataset.train_rewards
    if args.normalize_rewards and len(train_rewards):
        reward_mean = train_rewards.mean()
        reward_std = train_rewards.std(unbiased=False)
        reward_scale = torch.clamp(reward_std, min=1e-4)
        normalized_train_rewards = (train_rewards - reward_mean) / reward_scale
    else:
        reward_mean = torch.zeros(1, dtype=torch.float32)
        reward_scale = torch.ones(1, dtype=torch.float32)
        normalized_train_rewards = train_rewards

    train_ds = TensorDataset(
        dataset.train_states,
        dataset.train_actions,
        normalized_train_rewards,
        dataset.train_next_states,
        dataset.train_dones,
    )
    train_loader = DataLoader(train_ds, batch_size=min(args.batch_size, len(train_ds)), shuffle=True)

    actor_loss_curve: list[float] = []
    critic_loss_curve: list[float] = []
    alpha_curve: list[float] = []
    val_action_mae_curve: list[float] = []
    best_actor_state = {k: v.detach().cpu().clone() for k, v in actor.state_dict().items()}
    best_val_action_mae = float("inf")
    actor_updates = 0
    global_step = 0

    for epoch_idx in range(args.sac_epochs):
        epoch_actor_losses = []
        epoch_critic_losses = []

        for states, actions, rewards, next_states, dones in train_loader:
            global_step += 1
            with torch.no_grad():
                if epoch_idx < args.critic_warmup_epochs:
                    next_actions, next_log_prob, _ = reference_actor.sample(next_states)
                else:
                    current_next_actions, current_next_log_prob, _ = actor.sample(next_states)
                    ref_next_actions, ref_next_log_prob, _ = reference_actor.sample(next_states)
                    next_actions = 0.75 * ref_next_actions + 0.25 * current_next_actions
                    next_actions = torch.clamp(next_actions, min=-1.0, max=1.0)
                    next_log_prob = 0.75 * ref_next_log_prob + 0.25 * current_next_log_prob
                target_q1 = target_critic1(next_states, next_actions)
                target_q2 = target_critic2(next_states, next_actions)
                target_q = torch.min(target_q1, target_q2)
                if args.offline_mode == "sac":
                    target_q = target_q - log_alpha.exp() * next_log_prob
                target_value = rewards + (1.0 - dones) * args.gamma * target_q

            q1 = critic1(states, actions)
            q2 = critic2(states, actions)
            critic1_loss = F.mse_loss(q1, target_value)
            critic2_loss = F.mse_loss(q2, target_value)
            critic_loss = critic1_loss + critic2_loss

            critic1_opt.zero_grad()
            critic2_opt.zero_grad()
            critic_loss.backward()
            clip_grad_norm_(critic1.parameters(), args.grad_clip)
            clip_grad_norm_(critic2.parameters(), args.grad_clip)
            critic1_opt.step()
            critic2_opt.step()

            should_update_actor = (
                epoch_idx >= args.critic_warmup_epochs
                and (global_step % max(args.actor_update_interval, 1) == 0)
            )
            if should_update_actor:
                sampled_actions, log_prob, det_actions = actor.sample(states)
                with torch.no_grad():
                    ref_actions = reference_actor.deterministic(states)
                bc_loss = F.mse_loss(det_actions, actions)
                anchor_loss = F.mse_loss(det_actions, ref_actions)

                if args.offline_mode == "awac":
                    q1_data = critic1(states, actions)
                    q2_data = critic2(states, actions)
                    q_data = torch.min(q1_data, q2_data)
                    q1_ref = critic1(states, ref_actions)
                    q2_ref = critic2(states, ref_actions)
                    v_ref = torch.min(q1_ref, q2_ref)
                    advantage = q_data - v_ref
                    weights = torch.exp(advantage / max(args.awac_lambda, 1e-6))
                    weights = torch.clamp(weights, min=0.0, max=args.awac_max_weight).detach()
                    per_sample_bc = torch.mean((det_actions - actions).pow(2), dim=-1, keepdim=True)
                    weighted_bc_loss = (weights * per_sample_bc).mean()
                    actor_loss = (
                        weighted_bc_loss
                        + (args.bc_weight * bc_loss)
                        + (args.bc_anchor_weight * anchor_loss)
                    )
                else:
                    q1_pi = critic1(states, sampled_actions)
                    q2_pi = critic2(states, sampled_actions)
                    min_q_pi = torch.min(q1_pi, q2_pi)
                    actor_loss = (
                        (log_alpha.exp() * log_prob - min_q_pi).mean()
                        + (args.bc_weight * bc_loss)
                        + (args.bc_anchor_weight * anchor_loss)
                    )

                actor_opt.zero_grad()
                actor_loss.backward()
                clip_grad_norm_(actor.parameters(), args.grad_clip)
                actor_opt.step()

                if args.offline_mode == "sac":
                    alpha_loss = -(log_alpha * (log_prob + target_entropy).detach()).mean()
                    alpha_opt.zero_grad()
                    alpha_loss.backward()
                    alpha_opt.step()
                    with torch.no_grad():
                        log_alpha.clamp_(min=min_log_alpha, max=max_log_alpha)
                epoch_actor_losses.append(float(actor_loss.item()))
                actor_updates += 1

            soft_update(target_critic1, critic1, args.tau)
            soft_update(target_critic2, critic2, args.tau)

            epoch_critic_losses.append(float(critic_loss.item()))

        actor_loss_curve.append(float(np.mean(epoch_actor_losses)) if epoch_actor_losses else 0.0)
        critic_loss_curve.append(float(np.mean(epoch_critic_losses)) if epoch_critic_losses else 0.0)
        alpha_curve.append(float(log_alpha.exp().item()))

        actor.eval()
        with torch.no_grad():
            val_pred = actor.deterministic(dataset.val_states)
            val_action_mae = float(torch.mean(torch.abs(val_pred - dataset.val_actions)).item()) if len(dataset.val_states) else 0.0
        val_action_mae_curve.append(val_action_mae)
        if val_action_mae < best_val_action_mae:
            best_val_action_mae = val_action_mae
            best_actor_state = {k: v.detach().cpu().clone() for k, v in actor.state_dict().items()}
        actor.train()

    actor.load_state_dict(best_actor_state)
    actor.eval()
    critic1.eval()
    critic2.eval()
    with torch.no_grad():
        val_pred = actor.deterministic(dataset.val_states)
        all_pred = actor.deterministic(dataset.all_states)
        val_action_mae = float(torch.mean(torch.abs(val_pred - dataset.val_actions)).item()) if len(dataset.val_states) else 0.0
        action_mae = float(torch.mean(torch.abs(all_pred - dataset.all_actions)).item())

    return {
        "actor": actor,
        "critic1": critic1,
        "critic2": critic2,
        "final_actor_loss": actor_loss_curve[-1] if actor_loss_curve else 0.0,
        "final_critic_loss": critic_loss_curve[-1] if critic_loss_curve else 0.0,
        "final_alpha": alpha_curve[-1] if alpha_curve else float(log_alpha.exp().item()),
        "best_val_action_mae": best_val_action_mae,
        "val_action_mae": val_action_mae,
        "action_mae": action_mae,
        "actor_updates": actor_updates,
        "actor_loss_curve": actor_loss_curve,
        "critic_loss_curve": critic_loss_curve,
        "alpha_curve": alpha_curve,
        "val_action_mae_curve": val_action_mae_curve,
        "reward_mean": float(reward_mean.item()) if torch.is_tensor(reward_mean) else float(reward_mean),
        "reward_scale": float(reward_scale.item()) if torch.is_tensor(reward_scale) else float(reward_scale),
    }


def save_checkpoint(path: Path, model: nn.Module, **metadata: object) -> None:
    payload = {
        "state_dict": model.state_dict(),
        **metadata,
    }
    torch.save(payload, path)


def main() -> int:
    args = parse_args()
    seed_everything(args.seed)

    trace = load_workload_trace(args.workload_csv)
    dataset = load_transition_dataset(args.workload_csv, delta_step=args.delta_step)
    env = CAORASACEnv(workload_trace=trace, delta_step=args.delta_step)

    actor = GaussianActor(state_size=dataset.all_states.shape[1], action_size=dataset.all_actions.shape[1])
    bc_metrics = run_behavior_cloning(
        actor=actor,
        dataset=dataset,
        epochs=args.bc_epochs,
        batch_size=args.batch_size,
        learning_rate=args.actor_lr,
    )
    bc_actor_state = bc_metrics["best_state"]
    bc_actor = GaussianActor(state_size=dataset.all_states.shape[1], action_size=dataset.all_actions.shape[1])
    bc_actor.load_state_dict(bc_actor_state)
    bc_rollout_metrics = evaluate_actor(bc_actor, env)

    sac_metrics = run_offline_sac(
        actor=actor,
        dataset=dataset,
        args=args,
        reference_actor_state=bc_actor_state,
    )
    chosen_actor = sac_metrics["actor"]
    chosen_policy_source = args.offline_mode
    if bc_metrics["val_action_mae"] <= sac_metrics["best_val_action_mae"]:
        chosen_actor = bc_actor
        chosen_policy_source = "behavior_cloning"
    rollout_metrics = evaluate_actor(chosen_actor, env)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    actor_path = out_dir / "sac_actor_offline.pt"
    critic1_path = out_dir / "sac_critic1_offline.pt"
    critic2_path = out_dir / "sac_critic2_offline.pt"
    bc_actor_path = out_dir / "sac_actor_bc.pt"
    save_checkpoint(
        bc_actor_path,
        bc_actor,
        state_size=dataset.all_states.shape[1],
        action_size=dataset.all_actions.shape[1],
        delta_step=args.delta_step,
        policy_source="behavior_cloning",
    )
    save_checkpoint(
        actor_path,
        chosen_actor,
        state_size=dataset.all_states.shape[1],
        action_size=dataset.all_actions.shape[1],
        delta_step=args.delta_step,
        policy_source=chosen_policy_source,
    )
    save_checkpoint(
        critic1_path,
        sac_metrics["critic1"],
        state_size=dataset.all_states.shape[1],
        action_size=dataset.all_actions.shape[1],
    )
    save_checkpoint(
        critic2_path,
        sac_metrics["critic2"],
        state_size=dataset.all_states.shape[1],
        action_size=dataset.all_actions.shape[1],
    )

    summary = {
        "workload_csv": os.path.abspath(args.workload_csv),
        "trace_points": len(trace),
        "train_points": int(len(dataset.train_states)),
        "val_points": int(len(dataset.val_states)),
        "delta_step": args.delta_step,
        "offline_mode": args.offline_mode,
        "bc_epochs": args.bc_epochs,
        "sac_epochs": args.sac_epochs,
        "batch_size": args.batch_size,
        "actor_lr": args.actor_lr,
        "critic_lr": args.critic_lr,
        "alpha_lr": args.alpha_lr,
        "gamma": args.gamma,
        "tau": args.tau,
        "bc_weight": args.bc_weight,
        "bc_anchor_weight": args.bc_anchor_weight,
        "critic_warmup_epochs": args.critic_warmup_epochs,
        "actor_update_interval": args.actor_update_interval,
        "awac_lambda": args.awac_lambda,
        "awac_max_weight": args.awac_max_weight,
        "grad_clip": args.grad_clip,
        "alpha_init": args.alpha_init,
        "alpha_min": args.alpha_min,
        "alpha_max": args.alpha_max,
        "target_entropy_scale": args.target_entropy_scale,
        "normalize_rewards": bool(args.normalize_rewards),
        "bc_best_val_loss": bc_metrics["best_val_loss"],
        "bc_final_train_loss": bc_metrics["train_curve"][-1] if bc_metrics["train_curve"] else 0.0,
        "bc_val_action_mae": bc_metrics["val_action_mae"],
        "bc_action_mae": bc_metrics["action_mae"],
        "bc_rollout": bc_rollout_metrics,
        "offline_sac_final_actor_loss": sac_metrics["final_actor_loss"],
        "offline_sac_final_critic_loss": sac_metrics["final_critic_loss"],
        "offline_sac_final_alpha": sac_metrics["final_alpha"],
        "offline_sac_best_val_action_mae": sac_metrics["best_val_action_mae"],
        "offline_sac_actor_updates": sac_metrics["actor_updates"],
        "offline_sac_reward_mean": sac_metrics["reward_mean"],
        "offline_sac_reward_scale": sac_metrics["reward_scale"],
        "val_action_mae": sac_metrics["val_action_mae"],
        "action_mae": sac_metrics["action_mae"],
        "chosen_policy_source": chosen_policy_source,
        "rollout": rollout_metrics,
        "bc_train_loss_curve": bc_metrics["train_curve"],
        "bc_val_loss_curve": bc_metrics["val_curve"],
        "sac_actor_loss_curve": sac_metrics["actor_loss_curve"],
        "sac_critic_loss_curve": sac_metrics["critic_loss_curve"],
        "sac_alpha_curve": sac_metrics["alpha_curve"],
        "sac_val_action_mae_curve": sac_metrics["val_action_mae_curve"],
        "bc_actor_checkpoint": str(bc_actor_path.resolve()),
        "actor_checkpoint": str(actor_path.resolve()),
        "critic1_checkpoint": str(critic1_path.resolve()),
        "critic2_checkpoint": str(critic2_path.resolve()),
    }

    with (out_dir / "sac_offline_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=True)
        handle.write("\n")

    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
