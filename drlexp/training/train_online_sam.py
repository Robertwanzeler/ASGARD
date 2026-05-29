#!/usr/bin/env python3
"""
Treino online SAC + SAM alinhado ao artigo Lotfi et al. 2025.

Pipeline:
  1. Cria ambiente OnlineMARLEnv (gerador Markov + recompensa artigo)
  2. Loop episodico: coleta experiencia no ambiente, treina SAC+SAM
  3. SAM integrado ao gradiente do ator com rho dinamico
  4. Exporta checkpoint .pt compativel com rapp_marl_shadow.py
"""

from __future__ import annotations

import argparse
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from drl.online_marl_env import OnlineMARLEnv


@dataclass
class Transition:
    state: np.ndarray
    action: np.ndarray
    reward: float
    next_state: np.ndarray
    done: bool


class ReplayBuffer:
    """Replay buffer com suporte opcional a amostragem priorizada por TD-error."""

    def __init__(self, capacity: int = 100_000, prioritized: bool = False):
        self.capacity = capacity
        self.prioritized = prioritized
        self.buffer: list[Transition] = []
        self.priorities: list[float] = []
        self._idx = 0

    def push(self, state, action, reward, next_state, done, priority: float = 1.0):
        if len(self.buffer) < self.capacity:
            self.buffer.append(Transition(state, action, reward, next_state, done))
            self.priorities.append(priority)
        else:
            self.buffer[self._idx] = Transition(state, action, reward, next_state, done)
            self.priorities[self._idx] = priority
        self._idx = (self._idx + 1) % self.capacity

    def sample(self, batch_size: int):
        n = len(self.buffer)
        if self.prioritized and n > 1:
            probs = np.array(self.priorities[:n]) ** 0.6
            probs = probs / probs.sum()
            indices = np.random.choice(n, size=batch_size, p=probs, replace=False)
        else:
            indices = np.random.randint(0, n, size=batch_size)
        states = np.array([self.buffer[i].state for i in indices])
        actions = np.array([self.buffer[i].action for i in indices])
        rewards = np.array([self.buffer[i].reward for i in indices])
        next_states = np.array([self.buffer[i].next_state for i in indices])
        dones = np.array([self.buffer[i].done for i in indices], dtype=np.float32)
        return (
            torch.tensor(states, dtype=torch.float32),
            torch.tensor(actions, dtype=torch.float32),
            torch.tensor(rewards, dtype=torch.float32).unsqueeze(1),
            torch.tensor(next_states, dtype=torch.float32),
            torch.tensor(dones, dtype=torch.float32).unsqueeze(1),
        ), indices

    def update_priorities(self, indices: list[int], td_errors: list[float]) -> None:
        for idx, td in zip(indices, td_errors):
            if 0 <= idx < len(self.priorities):
                self.priorities[idx] = max(0.01, abs(td))

    def __len__(self):
        return len(self.buffer)


class GaussianActor(nn.Module):
    """Ator Gaussiano 300->400->400 tanh (artigo secao VI-A)."""

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
    """Critico twin 300->400->400 tanh (artigo secao VI-A)."""

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


def soft_update(target: nn.Module, source: nn.Module, tau: float) -> None:
    with torch.no_grad():
        for tp, sp in zip(target.parameters(), source.parameters()):
            tp.data.mul_(1.0 - tau).add_(tau * sp.data)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Treino online SAC+SAM alinhado ao artigo")
    p.add_argument("--output-dir", default="runs/sac_bootstrap/online_sam", help="Diretorio de saida")
    p.add_argument("--episodes", type=int, default=20_000, help="Numero de episodios de treino")
    p.add_argument("--max-steps", type=int, default=200, help="Passos por episodio")
    p.add_argument("--batch-size", type=int, default=256, help="Tamanho do batch")
    p.add_argument("--replay-capacity", type=int, default=100_000, help="Capacidade do replay buffer")
    p.add_argument("--warmup-steps", type=int, default=1000, help="Passos de coleta antes de comecar a treinar")
    p.add_argument("--train-freq", type=int, default=1, help="Treinar a cada N passos")
    p.add_argument("--gradient-steps", type=int, default=1, help="Passos de gradiente por treino")
    p.add_argument("--actor-lr", type=float, default=1e-4, help="Learning rate do ator (artigo)")
    p.add_argument("--critic-lr", type=float, default=1e-4, help="Learning rate do critico (artigo)")
    p.add_argument("--alpha-lr", type=float, default=1e-4, help="Learning rate da temperatura (artigo)")
    p.add_argument("--gamma", type=float, default=0.99, help="Fator de desconto")
    p.add_argument("--tau", type=float, default=0.005, help="Soft-update do target critic")
    p.add_argument("--sam-rho-init", type=float, default=0.05, help="Rho inicial do SAM (artigo)")
    p.add_argument("--sam-rho-final", type=float, default=0.005, help="Rho final do SAM (apos decaimento)")
    p.add_argument("--alpha-init", type=float, default=0.2, help="Temperatura de entropia inicial")
    p.add_argument("--target-entropy-scale", type=float, default=0.5, help="Escala para target entropy")
    p.add_argument("--grad-clip", type=float, default=1.0, help="Norma maxima do gradiente")
    p.add_argument("--eval-interval", type=int, default=500, help="Episodios entre avaliacoes")
    p.add_argument("--eval-episodes", type=int, default=5, help="Episodios de avaliacao")
    p.add_argument("--seed", type=int, default=42, help="Semente aleatoria")
    p.add_argument("--device", default="auto", help="Dispositivo (auto, cpu, cuda)")
    p.add_argument("--prioritized-replay", action="store_true", default=False, help="Usa amostragem priorizada por TD-error (artigo)")
    p.add_argument("--save-replay", action="store_true", default=False, help="Salvar replay apos treino")
    return p.parse_args()


def evaluate(actor: GaussianActor, env: OnlineMARLEnv, episodes: int, device: torch.device) -> dict:
    actor.eval()
    rewards = []
    ran_comps = []
    ai_comps = []
    qos_terms = []
    res_penalties = []
    min_qos_penalties = []
    for _ in range(episodes):
        obs, _ = env.reset()
        ep_rew = 0.0
        while True:
            with torch.no_grad():
                s = torch.tensor(obs, dtype=torch.float32, device=device).unsqueeze(0)
                action = actor.deterministic(s).squeeze(0).cpu().numpy()
            next_obs, reward, term, trunc, info = env.step(action)
            ep_rew += reward
            obs = next_obs
            qos_terms.append(info.get("qos_term", 0.0))
            res_penalties.append(info.get("res_penalty", 0.0))
            min_qos_penalties.append(info.get("min_qos_penalty", 0.0))
            if term or trunc:
                break
        rewards.append(ep_rew)
        ran_comps.append(info["ran_completion"])
        ai_comps.append(info["ai_completion"])
    actor.train()
    return {
        "mean_return": float(np.mean(rewards)),
        "std_return": float(np.std(rewards)),
        "mean_ran_completion": float(np.mean(ran_comps)),
        "mean_ai_completion": float(np.mean(ai_comps)),
        "avg_qos_term": float(np.mean(qos_terms)) if qos_terms else 0.0,
        "avg_res_penalty": float(np.mean(res_penalties)) if res_penalties else 0.0,
        "avg_min_qos_penalty": float(np.mean(min_qos_penalties)) if min_qos_penalties else 0.0,
    }


def main() -> int:
    args = parse_args()
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(device)

    env = OnlineMARLEnv(max_steps=args.max_steps, seed=args.seed)
    eval_env = OnlineMARLEnv(max_steps=args.max_steps, seed=args.seed + 999)

    state_size = env.observation_space.shape[0]
    action_size = env.action_space.shape[0]

    actor = GaussianActor(state_size, action_size).to(device)
    critic1 = QNetwork(state_size, action_size).to(device)
    critic2 = QNetwork(state_size, action_size).to(device)
    target_critic1 = QNetwork(state_size, action_size).to(device)
    target_critic2 = QNetwork(state_size, action_size).to(device)
    target_critic1.load_state_dict(critic1.state_dict())
    target_critic2.load_state_dict(critic2.state_dict())

    actor_opt = torch.optim.Adam(actor.parameters(), lr=args.actor_lr)
    critic1_opt = torch.optim.Adam(critic1.parameters(), lr=args.critic_lr)
    critic2_opt = torch.optim.Adam(critic2.parameters(), lr=args.critic_lr)

    log_alpha = torch.tensor(math.log(args.alpha_init), dtype=torch.float32, requires_grad=True, device=device)
    alpha_opt = torch.optim.Adam([log_alpha], lr=args.alpha_lr)
    target_entropy = -(float(action_size) * float(args.target_entropy_scale))

    replay = ReplayBuffer(capacity=args.replay_capacity, prioritized=args.prioritized_replay)

    episode_returns = []
    episode_losses = []
    eval_results = []
    global_step = 0

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for ep in range(1, args.episodes + 1):
        obs, _ = env.reset()
        ep_return = 0.0
        ep_actor_losses = []
        ep_critic_losses = []

        while True:
            # Amostra acao com exploracao
            with torch.no_grad():
                s = torch.tensor(obs, dtype=torch.float32, device=device).unsqueeze(0)
                if len(replay) < args.warmup_steps:
                    action = env.action_space.sample()
                else:
                    action, _, _ = actor.sample(s)
                    action = action.squeeze(0).cpu().numpy()

            next_obs, reward, term, trunc, info = env.step(action)
            replay.push(obs, action, reward, next_obs, term or trunc)
            ep_return += reward
            obs = next_obs
            global_step += 1

            # Treino SAC + SAM
            if len(replay) >= args.warmup_steps and global_step % args.train_freq == 0:
                for _ in range(args.gradient_steps):
                    (states, actions, rewards, next_states, dones), batch_indices = replay.sample(args.batch_size)
                    states = states.to(device)
                    actions = actions.to(device)
                    rewards = rewards.to(device)
                    next_states = next_states.to(device)
                    dones = dones.to(device)

                    # --- Critic update ---
                    with torch.no_grad():
                        next_actions, next_log_prob, _ = actor.sample(next_states)
                        tq1 = target_critic1(next_states, next_actions)
                        tq2 = target_critic2(next_states, next_actions)
                        tq = torch.min(tq1, tq2) - log_alpha.exp() * next_log_prob
                        target_q = rewards + (1.0 - dones) * args.gamma * tq

                    q1 = critic1(states, actions)
                    q2 = critic2(states, actions)
                    critic_loss = F.mse_loss(q1, target_q) + F.mse_loss(q2, target_q)

                    critic1_opt.zero_grad()
                    critic2_opt.zero_grad()
                    critic_loss.backward()
                    clip_grad_norm_(critic1.parameters(), args.grad_clip)
                    clip_grad_norm_(critic2.parameters(), args.grad_clip)
                    critic1_opt.step()
                    critic2_opt.step()
                    ep_critic_losses.append(float(critic_loss.item()))

                    # --- Atualiza prioridades do replay buffer ---
                    if args.prioritized_replay:
                        with torch.no_grad():
                            td_errors = []
                            for s, a, r in zip(states, actions, rewards):
                                q1_v = critic1(s.unsqueeze(0), a.unsqueeze(0))
                                q2_v = critic2(s.unsqueeze(0), a.unsqueeze(0))
                                q_v = torch.min(q1_v, q2_v)
                                td_errors.append(float(abs(q_v.item() - r.item())))
                            replay.update_priorities(list(batch_indices), td_errors)

                    # --- Actor update with SAM ---
                    sampled_actions, log_prob, _ = actor.sample(states)
                    q1_pi = critic1(states, sampled_actions)
                    q2_pi = critic2(states, sampled_actions)
                    min_q_pi = torch.min(q1_pi, q2_pi)
                    actor_loss = (log_alpha.exp() * log_prob - min_q_pi).mean()

                    actor_opt.zero_grad()
                    actor_loss.backward()

                    rho = args.sam_rho_init
                    if args.sam_rho_init > 0:
                        progress = float(ep) / float(args.episodes)
                        rho = max(args.sam_rho_final, args.sam_rho_init * (1.0 - progress))
                        grad_norm = 0.0
                        for p in actor.parameters():
                            if p.grad is not None:
                                grad_norm += p.grad.norm().item() ** 2
                        grad_norm = math.sqrt(grad_norm) + 1e-12

                        saved = [p.data.clone() for p in actor.parameters() if p.grad is not None]
                        with torch.no_grad():
                            for p in actor.parameters():
                                if p.grad is not None:
                                    p.data.add_(rho * p.grad / grad_norm)

                        sampled_actions_adv, log_prob_adv, _ = actor.sample(states)
                        q1_adv = critic1(states, sampled_actions_adv)
                        q2_adv = critic2(states, sampled_actions_adv)
                        min_q_adv = torch.min(q1_adv, q2_adv)
                        actor_loss_adv = (log_alpha.exp() * log_prob_adv - min_q_adv).mean()

                        actor_opt.zero_grad()
                        actor_loss_adv.backward()

                        idx = 0
                        with torch.no_grad():
                            for p in actor.parameters():
                                if p.grad is not None:
                                    p.data.copy_(saved[idx])
                                    idx += 1

                    clip_grad_norm_(actor.parameters(), args.grad_clip)
                    actor_opt.step()
                    ep_actor_losses.append(float(actor_loss.item()))

                    # --- Temperature update ---
                    alpha_loss = -(log_alpha * (log_prob + target_entropy).detach()).mean()
                    alpha_opt.zero_grad()
                    alpha_loss.backward()
                    alpha_opt.step()

                    # --- Soft-update target critics ---
                    soft_update(target_critic1, critic1, args.tau)
                    soft_update(target_critic2, critic2, args.tau)

            if term or trunc:
                break

        episode_returns.append(ep_return)
        if ep_actor_losses:
            episode_losses.append({
                "actor": float(np.mean(ep_actor_losses)),
                "critic": float(np.mean(ep_critic_losses)) if ep_critic_losses else 0.0,
            })

        # Avaliacao periodica
        if ep % args.eval_interval == 0:
            eval_metrics = evaluate(actor, eval_env, args.eval_episodes, device)
            eval_results.append({"episode": ep, **eval_metrics})
            rho_val = max(args.sam_rho_final, args.sam_rho_init * (1.0 - ep / args.episodes))
            print(
                f"ep={ep:>6d}  return={ep_return:>7.2f}  "
                f"eval_ret={eval_metrics['mean_return']:>7.2f}  "
                f"qos={eval_metrics['avg_qos_term']:+.3f}  "
                f"pres={eval_metrics['avg_res_penalty']:+.3f}  "
                f"pmin={eval_metrics['avg_min_qos_penalty']:+.3f}  "
                f"actor_loss={episode_losses[-1]['actor']:>6.3f}  "
                f"rho={rho_val:.4f}  "
                f"alpha={float(log_alpha.exp().item()):.3f}"
            )

    # --- Final evaluation ---
    final_eval = evaluate(actor, eval_env, args.eval_episodes, device)

    # --- Save checkpoint ---
    actor_path = out_dir / "online_sam_actor.pt"
    critic1_path = out_dir / "online_sam_critic1.pt"
    critic2_path = out_dir / "online_sam_critic2.pt"
    torch.save({"state_dict": actor.state_dict(), "state_size": state_size, "action_size": action_size}, actor_path)
    torch.save({"state_dict": critic1.state_dict()}, critic1_path)
    torch.save({"state_dict": critic2.state_dict()}, critic2_path)

    mean_ep_actor_loss = float(np.mean([l["actor"] for l in episode_losses[-100:]])) if episode_losses else 0.0
    mean_ep_critic_loss = float(np.mean([l["critic"] for l in episode_losses[-100:]])) if episode_losses else 0.0
    summary = {
        "output_dir": str(out_dir.resolve()),
        "args": vars(args),
        "total_episodes": args.episodes,
        "total_steps": global_step,
        "final_eval": final_eval,
        "eval_history": eval_results,
        "actor_checkpoint": str(actor_path.resolve()),
        "critic1_checkpoint": str(critic1_path.resolve()),
        "critic2_checkpoint": str(critic2_path.resolve()),
        "mean_return_last_100": float(np.mean(episode_returns[-100:])) if len(episode_returns) >= 100 else float(np.mean(episode_returns)),
        "mean_actor_loss_last_100": mean_ep_actor_loss,
        "mean_critic_loss_last_100": mean_ep_critic_loss,
    }
    (out_dir / "online_sam_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
