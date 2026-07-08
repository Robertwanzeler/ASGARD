#!/usr/bin/env python3
"""Article-faithful online TA-SAM MARL training on the current GreenRAN scenario."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "drlexp" / "src"))
sys.path.insert(0, str(ROOT / "src"))

from drl.online_greenran_marl_env import OnlineGreenRANMARLEnv
from drl.ta_sam_marl_sac import MARLTransitionRecord, TASAMArticleSACTrainer


_SHOULD_EXIT = False


def _signal_handler(signum, frame) -> None:  # pragma: no cover - signal driven
    global _SHOULD_EXIT
    _SHOULD_EXIT = True


@dataclass
class ReplayEntry:
    record: MARLTransitionRecord


class ReplayBuffer:
    def __init__(self, capacity: int = 100_000) -> None:
        self.capacity = max(1, int(capacity))
        self.buffer: list[ReplayEntry] = []
        self._idx = 0

    def push(self, record: MARLTransitionRecord) -> None:
        entry = ReplayEntry(record=record)
        if len(self.buffer) < self.capacity:
            self.buffer.append(entry)
        else:
            self.buffer[self._idx] = entry
        self._idx = (self._idx + 1) % self.capacity

    def records(self) -> list[MARLTransitionRecord]:
        return [entry.record for entry in self.buffer]

    def state_dict(self) -> dict[str, Any]:
        return {
            "capacity": self.capacity,
            "buffer": [
                {
                    "global_state": entry.record.global_state,
                    "du_states": entry.record.du_states,
                    "behavior_actions": entry.record.behavior_actions,
                    "reward": entry.record.reward,
                    "next_global_state": entry.record.next_global_state,
                    "next_du_states": entry.record.next_du_states,
                    "done": entry.record.done,
                }
                for entry in self.buffer
            ],
            "idx": self._idx,
        }

    def load_state_dict(self, payload: dict[str, Any]) -> None:
        self.capacity = max(1, int((payload or {}).get("capacity", self.capacity) or self.capacity))
        self._idx = int((payload or {}).get("idx", 0) or 0)
        self.buffer = []
        for item in (payload or {}).get("buffer") or []:
            self.buffer.append(
                ReplayEntry(
                    record=MARLTransitionRecord(
                        global_state=list(item.get("global_state") or []),
                        du_states=[list(values or []) for values in item.get("du_states") or []],
                        behavior_actions=[list(values or []) for values in item.get("behavior_actions") or []],
                        reward=float(item.get("reward", 0.0) or 0.0),
                        next_global_state=list(item.get("next_global_state") or []),
                        next_du_states=[list(values or []) for values in item.get("next_du_states") or []],
                        done=bool(item.get("done", False)),
                    )
                )
            )

    def __len__(self) -> int:
        return len(self.buffer)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train article-faithful online TA-SAM MARL on GreenRAN")
    parser.add_argument("--output-dir", default=str(ROOT / "runs" / "sac_bootstrap" / "online_tasam_marl"), help="Output directory")
    parser.add_argument("--episodes", type=int, default=1500, help="Number of training episodes")
    parser.add_argument("--max-steps", type=int, default=200, help="Maximum steps per episode")
    parser.add_argument("--train-freq", type=int, default=1, help="Run one training update every N environment steps")
    parser.add_argument("--warmup-steps", type=int, default=256, help="Collect this many transitions before training")
    parser.add_argument("--replay-capacity", type=int, default=100_000, help="Replay buffer capacity")
    parser.add_argument("--lr", type=float, default=1e-4, help="Learning rate")
    parser.add_argument("--alpha-lr", type=float, default=1e-4, help="Entropy alpha learning rate")
    parser.add_argument("--gamma", type=float, default=0.99, help="Discount factor")
    parser.add_argument("--tau", type=float, default=0.01, help="Soft target update factor")
    parser.add_argument("--alpha-init", type=float, default=0.03, help="Initial entropy alpha")
    parser.add_argument("--target-entropy-scale", type=float, default=1.0, help="Joint-action target entropy scale")
    parser.add_argument("--sam-mode", choices=("tasam_selective", "no_sam", "l2", "actor_sam", "critic_sam", "both_sam"), default="tasam_selective", help="TA-SAM ablation mode")
    parser.add_argument("--actor-sam-rho", type=float, default=0.5, help="Initial actor SAM rho")
    parser.add_argument("--actor-sam-rho-final", type=float, default=0.01, help="Final actor SAM rho")
    parser.add_argument("--critic-sam-rho", type=float, default=0.5, help="Initial critic SAM rho")
    parser.add_argument("--critic-sam-rho-final", type=float, default=0.01, help="Final critic SAM rho")
    parser.add_argument("--td-var-threshold", type=float, default=0.01, help="TD variance threshold for selective SAM")
    parser.add_argument("--min-selected-fraction", type=float, default=0.10, help="Minimum actor updates selected under TA-SAM")
    parser.add_argument("--warmup-episodes", type=int, default=2, help="Force actor SAM on during early episodes")
    parser.add_argument("--l2-weight", type=float, default=0.0, help="L2 regularization weight for baseline mode")
    parser.add_argument("--batch-size", type=int, default=128, help="Minibatch size")
    parser.add_argument("--updates-per-train", type=int, default=1, help="Gradient updates performed each training event")
    parser.add_argument("--actor-update-interval", type=int, default=1, help="Update actors every N critic steps")
    parser.add_argument("--eval-interval", type=int, default=50, help="Evaluate every N episodes")
    parser.add_argument("--eval-episodes", type=int, default=5, help="Evaluation episodes")
    parser.add_argument("--checkpoint-interval", type=int, default=25, help="Checkpoint every N episodes")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--stage-profile", default="greenran_conflict_cycle", help="Scenario stage profile for the online environment")
    parser.add_argument("--article-hidden", action="store_true", help="Use 300,400,400 hidden dims")
    parser.add_argument("--activation", choices=("tanh", "relu"), default="tanh", help="Hidden activation")
    parser.add_argument("--cpu-threads", type=int, default=4, help="Torch CPU thread budget for this training process")
    parser.add_argument("--interop-threads", type=int, default=1, help="Torch CPU interop thread budget for this training process")
    parser.add_argument("--resume", action="store_true", help="Resume from output-dir/online_tasam_marl_resume.pt")
    return parser.parse_args()


def _configure_cpu_runtime(cpu_threads: int, interop_threads: int) -> None:
    cpu_threads = max(1, int(cpu_threads))
    interop_threads = max(1, int(interop_threads))
    os.environ.setdefault("OMP_NUM_THREADS", str(cpu_threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(cpu_threads))
    os.environ.setdefault("OPENBLAS_NUM_THREADS", str(cpu_threads))
    os.environ.setdefault("NUMEXPR_NUM_THREADS", str(cpu_threads))
    torch.set_num_threads(cpu_threads)
    try:
        torch.set_num_interop_threads(interop_threads)
    except RuntimeError:
        pass


def _save_resume_checkpoint(
    path: Path,
    *,
    trainer: TASAMArticleSACTrainer,
    replay: ReplayBuffer,
    episode: int,
    global_step: int,
    episode_returns: list[float],
    train_history: list[dict[str, Any]],
    eval_history: list[dict[str, Any]],
) -> None:
    payload = {
        "episode": int(episode),
        "global_step": int(global_step),
        "trainer_state": trainer.training_state_dict(),
        "replay": replay.state_dict(),
        "episode_returns": list(episode_returns),
        "train_history": list(train_history),
        "eval_history": list(eval_history),
        "numpy_rng_state": np.random.get_state(),
        "torch_rng_state": torch.get_rng_state(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, path)


def _load_resume_checkpoint(
    path: Path,
    *,
    trainer: TASAMArticleSACTrainer,
    replay: ReplayBuffer,
) -> tuple[int, int, list[float], list[dict[str, Any]], list[dict[str, Any]]]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    trainer.load_training_state_dict((payload or {}).get("trainer_state") or {})
    replay.load_state_dict((payload or {}).get("replay") or {})
    numpy_state = (payload or {}).get("numpy_rng_state")
    if numpy_state is not None:
        np.random.set_state(numpy_state)
    torch_state = (payload or {}).get("torch_rng_state")
    if torch_state is not None:
        torch.set_rng_state(torch_state)
    return (
        int((payload or {}).get("episode", 0) or 0),
        int((payload or {}).get("global_step", 0) or 0),
        list((payload or {}).get("episode_returns") or []),
        list((payload or {}).get("train_history") or []),
        list((payload or {}).get("eval_history") or []),
    )


def _sample_action(trainer: TASAMArticleSACTrainer, du_states: list[list[float]]) -> list[list[float]]:
    tensor = torch.tensor(du_states, dtype=torch.float32).unsqueeze(0)
    with torch.no_grad():
        joint_actions, _, _ = trainer._sample_joint_actions(tensor)
    flat = joint_actions.squeeze(0).cpu().numpy()
    return flat.reshape(trainer.du_count, trainer.action_dim).tolist()


def _deterministic_action(trainer: TASAMArticleSACTrainer, du_states: list[list[float]]) -> list[list[float]]:
    tensor = torch.tensor(du_states, dtype=torch.float32).unsqueeze(0)
    with torch.no_grad():
        joint_actions = trainer._deterministic_joint_actions(tensor)
    flat = joint_actions.squeeze(0).cpu().numpy()
    return flat.reshape(trainer.du_count, trainer.action_dim).tolist()


def evaluate(
    trainer: TASAMArticleSACTrainer,
    *,
    episodes: int,
    max_steps: int,
    seed: int,
    stage_profile: str,
) -> dict[str, float]:
    env = OnlineGreenRANMARLEnv(max_steps=max_steps, seed=seed, stage_profile=stage_profile)
    rewards: list[float] = []
    embb_completion: list[float] = []
    mmtc_completion: list[float] = []
    urllc_completion: list[float] = []
    for _ in range(max(1, int(episodes))):
        payload, _ = env.reset()
        ep_reward = 0.0
        while True:
            action = _deterministic_action(trainer, [list(du.get("state_vector") or []) for du in payload["du_states"]])
            next_payload, reward, terminated, truncated, _ = env.step(action)
            ep_reward += float(reward)
            embb_completion.append(_safe_float((payload["slice_state"].get("eMBB") or {}).get("completion_ratio", 0.0), 0.0))
            mmtc_completion.append(_safe_float((payload["slice_state"].get("mMTC") or {}).get("completion_ratio", 0.0), 0.0))
            urllc_completion.append(_safe_float((payload["slice_state"].get("URLLC") or {}).get("completion_ratio", 0.0), 0.0))
            payload = next_payload
            if terminated or truncated:
                break
        rewards.append(ep_reward)
    return {
        "mean_return": float(np.mean(rewards)) if rewards else 0.0,
        "std_return": float(np.std(rewards)) if rewards else 0.0,
        "mean_embb_completion": float(np.mean(embb_completion)) if embb_completion else 0.0,
        "mean_mmtc_completion": float(np.mean(mmtc_completion)) if mmtc_completion else 0.0,
        "mean_urllc_completion": float(np.mean(urllc_completion)) if urllc_completion else 0.0,
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main() -> int:
    args = parse_args()
    _configure_cpu_runtime(args.cpu_threads, args.interop_threads)
    np.random.seed(int(args.seed))
    torch.manual_seed(int(args.seed))

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    resume_path = output_dir / "online_tasam_marl_resume.pt"
    summary_path = output_dir / "online_tasam_marl_summary.json"
    history_path = output_dir / "online_tasam_marl_history.jsonl"

    env = OnlineGreenRANMARLEnv(max_steps=args.max_steps, seed=args.seed, stage_profile=args.stage_profile)
    actor_hidden = (300, 400, 400) if args.article_hidden else (64, 64)
    critic_hidden = (300, 400, 400) if args.article_hidden else (96, 96)
    trainer = TASAMArticleSACTrainer(
        du_count=env.du_count,
        du_state_dim=env.du_state_dim,
        global_state_dim=env.global_state_dim,
        lr=args.lr,
        alpha_lr=args.alpha_lr,
        gamma=args.gamma,
        tau=args.tau,
        alpha_init=args.alpha_init,
        target_entropy_scale=args.target_entropy_scale,
        actor_rho=args.actor_sam_rho,
        actor_rho_final=args.actor_sam_rho_final,
        critic_rho=args.critic_sam_rho,
        critic_rho_final=args.critic_sam_rho_final,
        sam_mode=args.sam_mode,
        l2_weight=args.l2_weight,
        actor_hidden_dims=actor_hidden,
        critic_hidden_dims=critic_hidden,
        activation=args.activation,
        batch_size=args.batch_size,
        updates_per_epoch=args.updates_per_train,
        actor_update_interval=args.actor_update_interval,
        seed=args.seed,
    )
    replay = ReplayBuffer(capacity=args.replay_capacity)

    start_episode = 1
    global_step = 0
    episode_returns: list[float] = []
    train_history: list[dict[str, Any]] = []
    eval_history: list[dict[str, Any]] = []
    history_path.unlink(missing_ok=True)
    if args.resume and resume_path.exists():
        start_ep_minus_one, global_step, episode_returns, train_history, eval_history = _load_resume_checkpoint(
            resume_path,
            trainer=trainer,
            replay=replay,
        )
        start_episode = start_ep_minus_one + 1
        if train_history:
            history_path.write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in train_history),
                encoding="utf-8",
            )

    for episode in range(start_episode, args.episodes + 1):
        payload, _ = env.reset()
        episode_reward = 0.0
        last_train_metrics: dict[str, Any] | None = None
        while True:
            du_states = [list(du.get("state_vector") or []) for du in payload["du_states"]]
            action = _sample_action(trainer, du_states)
            next_payload, reward, terminated, truncated, _ = env.step(action)
            record = MARLTransitionRecord(
                global_state=list(payload["global_state"]["state_vector"]),
                du_states=du_states,
                behavior_actions=[list(values) for values in action],
                reward=float(reward),
                next_global_state=list(next_payload["global_state"]["state_vector"]),
                next_du_states=[list(du.get("state_vector") or []) for du in next_payload["du_states"]],
                done=bool(terminated or truncated),
            )
            replay.push(record)
            payload = next_payload
            episode_reward += float(reward)
            global_step += 1

            if len(replay) >= args.warmup_steps and global_step % args.train_freq == 0:
                warmup = episode <= args.warmup_episodes
                progress = float(episode - 1) / max(args.episodes - 1, 1)
                last_train_metrics = trainer.train_epoch(
                    replay.records(),
                    td_var_threshold=args.td_var_threshold,
                    min_selected_fraction=args.min_selected_fraction,
                    warmup=warmup,
                    bc_weight=0.0,
                    value_weight=0.0,
                    epoch_progress=progress,
                )

            if terminated or truncated or _SHOULD_EXIT:
                break

        episode_returns.append(float(episode_reward))
        entry = {
            "episode": episode,
            "global_step": global_step,
            "episode_return": float(episode_reward),
            "replay_size": len(replay),
        }
        if last_train_metrics:
            entry.update(last_train_metrics)
        train_history.append(entry)
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

        if episode % args.eval_interval == 0 or episode == args.episodes:
            eval_metrics = evaluate(
                trainer,
                episodes=args.eval_episodes,
                max_steps=args.max_steps,
                seed=args.seed + episode + 1000,
                stage_profile=args.stage_profile,
            )
            eval_entry = {"episode": episode, "global_step": global_step, **eval_metrics}
            eval_history.append(eval_entry)

        if episode % args.checkpoint_interval == 0 or _SHOULD_EXIT:
            _save_resume_checkpoint(
                resume_path,
                trainer=trainer,
                replay=replay,
                episode=episode,
                global_step=global_step,
                episode_returns=episode_returns,
                train_history=train_history,
                eval_history=eval_history,
            )
            summary = {
                "output_dir": str(output_dir.resolve()),
                "status": "interrupted" if _SHOULD_EXIT else "checkpointed",
                "episode": episode,
                "global_step": global_step,
            }
            summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if _SHOULD_EXIT:
            break

    final_eval = eval_history[-1] if eval_history else evaluate(
        trainer,
        episodes=args.eval_episodes,
        max_steps=args.max_steps,
        seed=args.seed + 9000,
        stage_profile=args.stage_profile,
    )
    final_summary = {
        "output_dir": str(output_dir.resolve()),
        "args": vars(args),
        "article_faithful_algorithm": True,
        "experiment_track": "online_greenran_article_marl",
        "du_count": env.du_count,
        "du_state_dim": env.du_state_dim,
        "global_state_dim": env.global_state_dim,
        "total_episodes_requested": int(args.episodes),
        "completed_episodes": len(episode_returns),
        "total_steps": int(global_step),
        "mean_return_last_100": float(np.mean(episode_returns[-100:])) if episode_returns else 0.0,
        "final_eval": final_eval,
        "eval_history": eval_history,
        "history_jsonl": str(history_path.resolve()),
        "resume_checkpoint": str(resume_path.resolve()),
    }
    trainer.export_checkpoint(output_dir, metadata=final_summary)
    summary_path.write_text(json.dumps(final_summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(final_summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
