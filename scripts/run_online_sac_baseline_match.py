#!/usr/bin/env python3
"""Launch an online SAC baseline matched to the current TA-SAM setup."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRAINER = ROOT / "drlexp" / "training" / "train_online_tasam_marl.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run matched online SAC baseline for TA-SAM comparison")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "runs" / "sac_bootstrap" / "online_sac_match_tasam_60ep"),
        help="Output directory for the matched SAC baseline",
    )
    parser.add_argument("--episodes", type=int, default=60, help="Number of episodes to match")
    parser.add_argument("--max-steps", type=int, default=200, help="Maximum steps per episode")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--stage-profile", default="greenran_conflict_cycle", help="Scenario stage profile")
    parser.add_argument("--cpu-threads", type=int, default=4, help="Torch CPU thread budget")
    parser.add_argument("--interop-threads", type=int, default=1, help="Torch interop thread budget")
    parser.add_argument("--eval-interval", type=int, default=50, help="Evaluation interval")
    parser.add_argument("--eval-episodes", type=int, default=5, help="Evaluation episodes")
    parser.add_argument("--checkpoint-interval", type=int, default=25, help="Checkpoint interval")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cmd = [
        sys.executable,
        str(TRAINER),
        "--output-dir",
        str(Path(args.output_dir).resolve()),
        "--episodes",
        str(args.episodes),
        "--max-steps",
        str(args.max_steps),
        "--seed",
        str(args.seed),
        "--stage-profile",
        args.stage_profile,
        "--cpu-threads",
        str(args.cpu_threads),
        "--interop-threads",
        str(args.interop_threads),
        "--eval-interval",
        str(args.eval_interval),
        "--eval-episodes",
        str(args.eval_episodes),
        "--checkpoint-interval",
        str(args.checkpoint_interval),
        "--sam-mode",
        "no_sam",
    ]
    print(" ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
