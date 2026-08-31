#!/usr/bin/env python3
"""Evaluate deterministic TA-SAM actors on the local GreenRAN scenario."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "drlexp" / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from drl.online_greenran_marl_env import OnlineGreenRANMARLEnv
from drl.ta_sam_marl_sac import TASAMArticleSACTrainer


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def evaluate_actor(actor_path: Path, *, episodes: int, max_steps: int, seed: int, stage_profile: str) -> dict[str, Any]:
    env = OnlineGreenRANMARLEnv(max_steps=max_steps, seed=seed, stage_profile=stage_profile)
    metadata_path = actor_path.parent / "tasam_marl_checkpoint_meta.json"
    metadata = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
    trainer = TASAMArticleSACTrainer(
        du_count=env.du_count,
        du_state_dim=env.du_state_dim,
        global_state_dim=env.global_state_dim,
        sam_mode="no_sam",
        actor_hidden_dims=(300, 400, 400),
        critic_hidden_dims=(300, 400, 400),
        activation="tanh",
        ran_guard_hard_enabled=bool(metadata.get("ran_guard_hard_enabled", False)),
        ran_guard_hard_pressure_threshold=safe_float(metadata.get("ran_guard_hard_pressure_threshold", 0.35), 0.35),
        ran_guard_hard_min_embb_share=safe_float(metadata.get("ran_guard_hard_min_embb_share", 0.55), 0.55),
        seed=seed,
    )
    trainer.actors.load_state_dict(torch.load(actor_path, map_location="cpu", weights_only=True))
    trainer.actors.eval()

    returns: list[float] = []
    qos_scores: list[float] = []
    completions = {"eMBB": [], "mMTC": [], "URLLC": []}
    for _ in range(max(1, int(episodes))):
        payload, _ = env.reset()
        episode_return = 0.0
        while True:
            du_states = torch.tensor(
                [list(du.get("state_vector") or []) for du in payload["du_states"]],
                dtype=torch.float32,
            ).unsqueeze(0)
            with torch.no_grad():
                joint_action = trainer._deterministic_joint_actions(du_states)
            action = joint_action.squeeze(0).reshape(env.du_count, 3).tolist()
            next_payload, reward, terminated, truncated, _ = env.step(action)
            episode_return += float(reward)
            completions["eMBB"].append(safe_float((payload["slice_state"].get("eMBB") or {}).get("completion_ratio")))
            completions["mMTC"].append(safe_float((payload["slice_state"].get("mMTC") or {}).get("completion_ratio")))
            completions["URLLC"].append(safe_float((payload["slice_state"].get("URLLC") or {}).get("completion_ratio")))
            qos_scores.append(safe_float(payload.get("global_state", {}).get("qos_score")))
            payload = next_payload
            if terminated or truncated:
                break
        returns.append(episode_return)
    return {
        "actor_path": str(actor_path.resolve()),
        "mean_return": float(np.mean(returns)),
        "std_return": float(np.std(returns)),
        "mean_qos_score": float(np.mean(qos_scores)) if qos_scores else 0.0,
        "mean_completion": {key: float(np.mean(value)) if value else 0.0 for key, value in completions.items()},
        "min_completion": {key: float(np.min(value)) if value else 0.0 for key, value in completions.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-actors", required=True)
    parser.add_argument("--baseline-actors", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--seed", type=int, default=45)
    parser.add_argument("--stage-profile", default="greenran_conflict_cycle")
    args = parser.parse_args()

    candidate = evaluate_actor(Path(args.candidate_actors), episodes=args.episodes, max_steps=args.max_steps, seed=args.seed, stage_profile=args.stage_profile)
    baseline = evaluate_actor(Path(args.baseline_actors), episodes=args.episodes, max_steps=args.max_steps, seed=args.seed, stage_profile=args.stage_profile)
    payload = {
        "evaluation": "deterministic_online_greenran_rollout",
        "scenario": args.stage_profile,
        "episodes": args.episodes,
        "max_steps": args.max_steps,
        "candidate": candidate,
        "baseline_no_sam": baseline,
        "delta_mean_return": candidate["mean_return"] - baseline["mean_return"],
        "delta_return_pct": 100.0 * (candidate["mean_return"] - baseline["mean_return"]) / max(abs(baseline["mean_return"]), 1e-12),
        "delta_qos": candidate["mean_qos_score"] - baseline["mean_qos_score"],
        "delta_completion": {
            key: candidate["mean_completion"][key] - baseline["mean_completion"][key]
            for key in candidate["mean_completion"]
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"salvo em: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
