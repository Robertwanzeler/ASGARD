#!/usr/bin/env python3
"""Run offline GreenRAN TA-SAM training across independent random seeds.

The launcher deliberately delegates dataset export, validation, and training
to ``run_tasam_greenran_real.py``.  Each seed gets an isolated output folder
and quality report; no checkpoint is activated and no runtime control path is
changed by this script.
"""

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
DEFAULT_OUTPUT_ROOT = ROOT / "runs" / "tasam_greenran_real" / "multiseed"
DEFAULT_RUNNER = ROOT / "scripts" / "run_tasam_greenran_real.py"


def parse_seeds(raw: str) -> tuple[int, ...]:
    seeds = tuple(int(part.strip()) for part in str(raw).split(",") if part.strip())
    if not seeds:
        raise argparse.ArgumentTypeError("at least one seed is required")
    if len(set(seeds)) != len(seeds):
        raise argparse.ArgumentTypeError("seed values must be unique")
    return seeds


def parse_modes(raw: str) -> tuple[str, ...]:
    modes = tuple(part.strip() for part in str(raw).split(",") if part.strip())
    if not modes:
        raise argparse.ArgumentTypeError("at least one training mode is required")
    return modes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline GreenRAN TA-SAM seeds in parallel")
    parser.add_argument("--trace-jsonl", required=True, help="Validated GreenRAN trainable trace JSONL")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Per-seed output root")
    parser.add_argument("--seeds", type=parse_seeds, default=(42, 43, 44), help="Comma-separated seed list")
    parser.add_argument("--modes", type=parse_modes, default=("no_sam", "tasam_selective"), help="Comma-separated modes")
    parser.add_argument("--epochs", type=int, default=25, help="Training epochs per seed")
    parser.add_argument("--min-transitions", type=int, default=1500, help="Quality-gate minimum")
    parser.add_argument("--expected-topology-id", default="greenran_fixed_marl_v1")
    parser.add_argument("--expected-du-count", type=int, default=3)
    parser.add_argument("--train-python", default=None, help="Trainer Python; auto-detected when omitted")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def build_command(args: argparse.Namespace, seed: int) -> tuple[list[str], Path]:
    output_dir = Path(args.output_root) / f"seed_{int(seed):04d}"
    command = [
        sys.executable,
        str(DEFAULT_RUNNER),
        "--trace-jsonl",
        str(Path(args.trace_jsonl).resolve()),
        "--skip-export",
        "--trace-profile",
        "raw",
        "--output-root",
        str(output_dir.resolve()),
        "--modes",
        ",".join(args.modes),
        "--epochs",
        str(args.epochs),
        "--seed",
        str(seed),
        "--expected-topology-id",
        str(args.expected_topology_id),
        "--expected-du-count",
        str(args.expected_du_count),
        "--min-transitions",
        str(args.min_transitions),
    ]
    if args.train_python:
        command.extend(["--train-python", str(args.train_python)])
    return command, output_dir


def main() -> int:
    args = build_parser().parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    plan: list[dict[str, Any]] = []
    for seed in args.seeds:
        command, output_dir = build_command(args, seed)
        plan.append(
            {
                "seed": seed,
                "output_dir": str(output_dir.resolve()),
                "log_path": str((output_dir / "launcher.log").resolve()),
                "command": command,
            }
        )

    manifest_path = output_root / "multiseed_launch_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "greenran.tasam_multiseed_launch.v1",
                "control_mode": "shadow_only",
                "trace_jsonl": str(Path(args.trace_jsonl).resolve()),
                "seeds": list(args.seeds),
                "modes": list(args.modes),
                "plan": plan,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if args.dry_run:
        print(json.dumps({"manifest": str(manifest_path.resolve()), "plan": plan}, indent=2, ensure_ascii=False))
        return 0

    if not Path(args.trace_jsonl).exists():
        raise SystemExit(f"Trace not found: {args.trace_jsonl}")

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    processes: list[tuple[int, subprocess.Popen[Any], Any]] = []
    exit_code = 0
    try:
        for item in plan:
            output_dir = Path(item["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            handle = (output_dir / "launcher.log").open("a", encoding="utf-8", buffering=1)
            handle.write(json.dumps({"event": "launch", "seed": item["seed"], "timestamp": int(time.time())}) + "\n")
            process = subprocess.Popen(
                item["command"],
                cwd=str(ROOT),
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            processes.append((int(item["seed"]), process, handle))

        while processes:
            for seed, process, handle in list(processes):
                status = process.poll()
                if status is None:
                    continue
                handle.write(json.dumps({"event": "exit", "seed": seed, "returncode": status, "timestamp": int(time.time())}) + "\n")
                handle.close()
                processes.remove((seed, process, handle))
                if status != 0 and exit_code == 0:
                    exit_code = int(status)
                    if args.stop_on_failure:
                        for _, other, _ in processes:
                            try:
                                os.killpg(other.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
            time.sleep(1)
    except KeyboardInterrupt:
        for _, process, handle in processes:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            handle.close()
        return 130
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
