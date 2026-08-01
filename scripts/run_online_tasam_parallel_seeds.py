#!/usr/bin/env python3
"""Launch two or more online TA-SAM seeds in parallel with bounded CPU usage."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "runs" / "sac_bootstrap" / "online_tasam_parallel"
DEFAULT_TRAINER = ROOT / "drlexp" / "training" / "train_online_tasam_marl.py"


def _parse_csv_ints(raw: str) -> tuple[int, ...]:
    values = []
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        values.append(int(chunk))
    if not values:
        raise argparse.ArgumentTypeError("at least one seed is required")
    return tuple(values)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded parallel online TA-SAM seeds")
    parser.add_argument("--seeds", type=_parse_csv_ints, default=(42, 43), help="Comma-separated seed list")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Output root for per-seed runs")
    parser.add_argument("--episodes", type=int, default=1500, help="Episodes per seed")
    parser.add_argument("--max-steps", type=int, default=200, help="Maximum steps per episode")
    parser.add_argument("--sam-mode", default="tasam_selective", choices=("tasam_selective", "no_sam", "l2", "actor_sam", "critic_sam", "both_sam"), help="SAM mode")
    parser.add_argument("--activation", default="tanh", choices=("tanh", "relu"), help="Hidden activation")
    parser.add_argument("--stage-profile", default="greenran_conflict_cycle", help="Stage profile")
    parser.add_argument("--cpu-threads", type=int, default=4, help="CPU threads allocated to each training process")
    parser.add_argument("--interop-threads", type=int, default=1, help="Interop threads allocated to each training process")
    parser.add_argument("--checkpoint-interval", type=int, default=10, help="Checkpoint interval for each training process")
    parser.add_argument("--eval-interval", type=int, default=50, help="Evaluation interval for each training process")
    parser.add_argument("--td-var-threshold", type=float, default=0.01, help="Selective TD variance threshold")
    parser.add_argument("--min-selected-fraction", type=float, default=0.10, help="Minimum selected fraction for selective SAM")
    parser.add_argument("--warmup-episodes", type=int, default=2, help="Warmup episodes with forced SAM")
    parser.add_argument("--actor-sam-rho", type=float, default=0.5, help="Initial actor SAM rho")
    parser.add_argument("--actor-sam-rho-final", type=float, default=0.01, help="Final actor SAM rho")
    parser.add_argument("--critic-sam-rho", type=float, default=0.5, help="Initial critic SAM rho")
    parser.add_argument("--critic-sam-rho-final", type=float, default=0.01, help="Final critic SAM rho")
    parser.add_argument("--article-hidden", action="store_true", help="Use article-sized hidden layers (300,400,400)")
    parser.add_argument("--resume", action="store_true", help="Resume each seed from its local checkpoint if present")
    parser.add_argument("--stop-on-failure", action="store_true", help="Stop every other seed if any seed exits with failure")
    parser.add_argument("--python", default=sys.executable, help="Python executable used to spawn the trainer")
    parser.add_argument("--dry-run", action="store_true", help="Print the launch plan without starting processes")
    return parser.parse_args()


def _seed_dir(output_root: Path, seed: int) -> Path:
    return output_root / f"seed_{int(seed):04d}"


def build_command(args: argparse.Namespace, seed: int) -> tuple[list[str], Path]:
    output_dir = _seed_dir(Path(args.output_root), seed)
    cmd = [
        args.python,
        str(DEFAULT_TRAINER),
        "--output-dir",
        str(output_dir),
        "--episodes",
        str(args.episodes),
        "--max-steps",
        str(args.max_steps),
        "--sam-mode",
        str(args.sam_mode),
        "--activation",
        str(args.activation),
        "--stage-profile",
        str(args.stage_profile),
        "--cpu-threads",
        str(args.cpu_threads),
        "--interop-threads",
        str(args.interop_threads),
        "--checkpoint-interval",
        str(args.checkpoint_interval),
        "--eval-interval",
        str(args.eval_interval),
        "--seed",
        str(seed),
    ]
    if args.article_hidden:
        cmd.append("--article-hidden")
    if args.resume:
        cmd.append("--resume")
    cmd.extend(
        [
            "--td-var-threshold",
            str(args.td_var_threshold),
            "--min-selected-fraction",
            str(args.min_selected_fraction),
            "--warmup-episodes",
            str(args.warmup_episodes),
            "--actor-sam-rho",
            str(args.actor_sam_rho),
            "--actor-sam-rho-final",
            str(args.actor_sam_rho_final),
            "--critic-sam-rho",
            str(args.critic_sam_rho),
            "--critic-sam-rho-final",
            str(args.critic_sam_rho_final),
        ]
    )
    return cmd, output_dir


def build_env(cpu_threads: int) -> dict[str, str]:
    env = dict(os.environ)
    thread_value = str(max(1, int(cpu_threads)))
    env["OMP_NUM_THREADS"] = thread_value
    env["MKL_NUM_THREADS"] = thread_value
    env["OPENBLAS_NUM_THREADS"] = thread_value
    env["NUMEXPR_NUM_THREADS"] = thread_value
    env["PYTHONUNBUFFERED"] = "1"
    return env


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    plan: list[dict[str, Any]] = []
    for seed in args.seeds:
        cmd, output_dir = build_command(args, seed)
        plan.append(
            {
                "seed": int(seed),
                "output_dir": str(output_dir.resolve()),
                "log_path": str((output_dir / "launcher.log").resolve()),
                "command": cmd,
            }
        )

    manifest_path = output_root / "parallel_launch_manifest.json"
    manifest_path.write_text(json.dumps({"plan": plan}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.dry_run:
        print(json.dumps({"manifest": str(manifest_path.resolve()), "plan": plan}, indent=2, ensure_ascii=False))
        return 0

    env = build_env(args.cpu_threads)
    processes: list[tuple[int, subprocess.Popen[Any], Any]] = []
    try:
        for item in plan:
            output_dir = Path(item["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            log_handle = (output_dir / "launcher.log").open("a", encoding="utf-8", buffering=1)
            log_handle.write(
                json.dumps(
                    {
                        "event": "launch",
                        "seed": item["seed"],
                        "timestamp": int(time.time()),
                        "command": item["command"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            log_handle.flush()
            proc = subprocess.Popen(
                item["command"],
                cwd=str(ROOT),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )
            processes.append((int(item["seed"]), proc, log_handle))

        exit_code = 0
        while processes:
            for seed, proc, log_handle in list(processes):
                status = proc.poll()
                if status is None:
                    continue
                log_handle.write(
                    json.dumps(
                        {"event": "exit", "seed": seed, "timestamp": int(time.time()), "returncode": int(status)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                log_handle.flush()
                log_handle.close()
                processes.remove((seed, proc, log_handle))
                if status != 0 and exit_code == 0:
                    exit_code = int(status)
                    if args.stop_on_failure:
                        for other_seed, other_proc, _ in processes:
                            try:
                                os.killpg(other_proc.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
            time.sleep(1.0)
        return exit_code
    except KeyboardInterrupt:
        for _, proc, log_handle in processes:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            log_handle.close()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
