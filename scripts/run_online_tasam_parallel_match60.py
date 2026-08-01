#!/usr/bin/env python3
"""Launch matched 60-episode TA-SAM runs in parallel for direct SAC comparison."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "run_online_tasam_parallel_seeds.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run matched 60-episode TA-SAM seeds in parallel")
    parser.add_argument(
        "--output-root",
        default=str(ROOT / "runs" / "sac_bootstrap" / "online_tasam_parallel_match60_v1"),
        help="Output root for matched TA-SAM parallel seeds",
    )
    parser.add_argument("--seeds", default="42,43", help="Comma-separated seed list")
    parser.add_argument("--episodes", type=int, default=60, help="Episodes per seed")
    parser.add_argument("--max-steps", type=int, default=200, help="Maximum steps per episode")
    parser.add_argument("--cpu-threads", type=int, default=4, help="Torch CPU thread budget per seed")
    parser.add_argument("--interop-threads", type=int, default=1, help="Torch interop thread budget per seed")
    parser.add_argument("--checkpoint-interval", type=int, default=10, help="Checkpoint interval")
    parser.add_argument("--eval-interval", type=int, default=20, help="Evaluation interval")
    parser.add_argument("--stage-profile", default="greenran_conflict_cycle", help="Scenario stage profile")
    parser.add_argument("--activation", default="tanh", choices=("tanh", "relu"), help="Hidden activation")
    parser.add_argument(
        "--sam-mode",
        default="tasam_selective",
        choices=("tasam_selective", "actor_sam", "critic_sam", "both_sam", "no_sam", "l2"),
        help="SAM mode for the matched 60-episode run",
    )
    parser.add_argument("--td-var-threshold", type=float, default=0.01, help="Selective TD variance threshold")
    parser.add_argument("--min-selected-fraction", type=float, default=0.10, help="Minimum selected fraction for selective SAM")
    parser.add_argument("--warmup-episodes", type=int, default=2, help="Warmup episodes with forced SAM")
    parser.add_argument("--actor-sam-rho", type=float, default=0.5, help="Initial actor SAM rho")
    parser.add_argument("--actor-sam-rho-final", type=float, default=0.01, help="Final actor SAM rho")
    parser.add_argument("--critic-sam-rho", type=float, default=0.5, help="Initial critic SAM rho")
    parser.add_argument("--critic-sam-rho-final", type=float, default=0.01, help="Final critic SAM rho")
    parser.add_argument("--article-hidden", action="store_true", help="Use article-sized hidden layers (300,400,400)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cmd = [
        sys.executable,
        str(LAUNCHER),
        "--output-root",
        str(Path(args.output_root).resolve()),
        "--seeds",
        args.seeds,
        "--episodes",
        str(args.episodes),
        "--max-steps",
        str(args.max_steps),
        "--sam-mode",
        str(args.sam_mode),
        "--td-var-threshold",
        str(args.td_var_threshold),
        "--min-selected-fraction",
        str(args.min_selected_fraction),
        "--warmup-episodes",
        str(args.warmup_episodes),
        "--activation",
        args.activation,
        "--stage-profile",
        args.stage_profile,
        "--cpu-threads",
        str(args.cpu_threads),
        "--interop-threads",
        str(args.interop_threads),
        "--actor-sam-rho",
        str(args.actor_sam_rho),
        "--actor-sam-rho-final",
        str(args.actor_sam_rho_final),
        "--critic-sam-rho",
        str(args.critic_sam_rho),
        "--critic-sam-rho-final",
        str(args.critic_sam_rho_final),
        "--checkpoint-interval",
        str(args.checkpoint_interval),
        "--eval-interval",
        str(args.eval_interval),
    ]
    if args.article_hidden:
        cmd.append("--article-hidden")
    print(" ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
