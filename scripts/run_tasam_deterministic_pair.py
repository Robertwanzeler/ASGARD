#!/usr/bin/env python3
"""Run and validate a deterministic two-arm seed-47 energy pair."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from .tasam_pairing import canonical_schedule, free_gib, storage_is_rw, write_schedule
except ImportError:
    from tasam_pairing import canonical_schedule, free_gib, storage_is_rw, write_schedule


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_tasam_online_arm.py"
EVALUATOR = ROOT / "scripts" / "evaluate_tasam_strict_pair.py"
DEFAULT_CHECKPOINT = ROOT / "runs/tasam_local_checkpoint_seed47_20260906"
LOCAL_RUNTIME_ROOT = ROOT.resolve()
PROFILE = "tasam_training_balanced_v3"


def _validate_local_path(path: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(LOCAL_RUNTIME_ROOT)
    except ValueError as exc:
        raise SystemExit(f"{label} precisa estar no workspace local: {resolved}") from exc
    return resolved


def build_pair_manifest(
    campaign_dir: Path,
    schedule: dict[str, Any],
    *,
    checkpoint: Path,
    decision_target: int,
    max_metric_gap: int = 1,
) -> dict[str, Any]:
    return {
        "schema": "greenran.tasam_deterministic_pair.v1",
        "campaign_dir": str(campaign_dir.resolve()),
        "seed": int(schedule["seed"]),
        "profile": str(schedule["profile"]),
        "wall_time_s": float(schedule["wall_time_s"]),
        "decision_target": int(decision_target),
        "max_metric_gap": int(max_metric_gap),
        "schedule_id": schedule["schedule_id"],
        "schedule_file": str((campaign_dir / "pairing_schedule.json").resolve()),
        "checkpoint": str(checkpoint.resolve()),
        "arms": {
            "rapp_only": {"mode": "rapp_only", "armd": False, "tasam": False},
            "combined": {"mode": "combined", "armd": True, "tasam": True},
        },
        "status": "planned",
        "protected_paths": ["v8_full_control", "v9_directional"],
    }


def arm_command(
    mode: str,
    run_dir: Path,
    *,
    seed: int,
    profile: str,
    wall_time: float,
    decision_target: int,
    schedule_id: str,
    schedule_file: Path,
    checkpoint: Path,
    sim_time: float = 600.0,
) -> list[str]:
    command = [
        sys.executable,
        str(RUNNER),
        "--mode", mode,
        "--run-dir", str(run_dir),
        "--seed", str(seed),
        "--profile", profile,
        "--wall-time", str(wall_time),
        "--sim-time", str(sim_time),
        "--decision-target", str(decision_target),
        "--pairing-schedule-id", schedule_id,
        "--pairing-schedule-file", str(schedule_file),
        "--min-free-gib", "40",
    ]
    if mode == "combined":
        command.extend(["--checkpoint", str(checkpoint)])
    return command


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_pair(args: argparse.Namespace) -> int:
    campaign_dir = args.campaign_dir.resolve()
    args.checkpoint = _validate_local_path(args.checkpoint, "checkpoint")
    campaign_dir = _validate_local_path(campaign_dir, "campaign-dir")
    if "armd" in campaign_dir.name.lower() or any(
        marker in part.lower() for part in campaign_dir.parts for marker in ("v8_full_control", "v9_directional")
    ):
        raise SystemExit(f"diretório de campanha recusado: {campaign_dir}")
    if campaign_dir.exists():
        raise SystemExit(f"campanha já existe; não sobrescrever: {campaign_dir}")
    ok, mount_options = storage_is_rw(campaign_dir.parent)
    if not ok:
        raise SystemExit(f"armazenamento não está em modo rw: {mount_options}")
    if free_gib(campaign_dir.parent) < float(args.min_free_gib):
        raise SystemExit("espaço livre insuficiente para a campanha pareada")
    if not args.checkpoint.is_dir():
        raise SystemExit(f"checkpoint combinado ausente: {args.checkpoint}")

    schedule = canonical_schedule(args.profile, args.seed, args.wall_time, tick_s=args.tick_s)
    campaign_dir.mkdir(parents=True, exist_ok=False)
    schedule_file = campaign_dir / "pairing_schedule.json"
    write_schedule(schedule_file, schedule)
    manifest = build_pair_manifest(
        campaign_dir,
        schedule,
        checkpoint=args.checkpoint,
        decision_target=args.decision_target,
        max_metric_gap=1,
    )
    _write(campaign_dir / "campaign_manifest.json", manifest)

    environment = os.environ.copy()
    environment.update({
        "GREENRAN_LOCAL_ONLY": "1",
        "GREENRAN_CGROUP_ENFORCE": "1",
        "GREENRAN_CGROUP_ALLOW_UNENFORCED": "0",
        "GREENRAN_COLLECTION_EVENT_TIME_SOURCE": "wall",
        "GREENRAN_COLLECTION_EVENT_TICK_S": str(args.tick_s),
        "GREENRAN_DECISION_TARGET": str(args.decision_target),
        "GREENRAN_PAIRING_SCHEDULE_ID": schedule["schedule_id"],
        "GREENRAN_PAIRING_SCHEDULE_FILE": str(schedule_file),
    })
    exit_codes: dict[str, int] = {}
    for mode in ("rapp_only", "combined"):
        run_dir = campaign_dir / mode
        log_path = campaign_dir / f"{mode}.launcher.log"
        command = arm_command(
            mode,
            run_dir,
            seed=args.seed,
            profile=args.profile,
            wall_time=args.wall_time,
            decision_target=args.decision_target,
            schedule_id=schedule["schedule_id"],
            schedule_file=schedule_file,
            checkpoint=args.checkpoint,
            sim_time=args.sim_time,
        )
        manifest["status"] = f"running_{mode}"
        manifest["active_arm"] = mode
        _write(campaign_dir / "campaign_manifest.json", manifest)
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False)
        exit_codes[mode] = int(completed.returncode)
        if completed.returncode != 0:
            manifest["status"] = "failed"
            manifest["failure_arm"] = mode
            manifest["exit_codes"] = exit_codes
            _write(campaign_dir / "campaign_manifest.json", manifest)
            return completed.returncode

    report_path = campaign_dir / "pair_report.json"
    report_command = [sys.executable, str(EVALUATOR),
                      str(campaign_dir / "rapp_only"), str(campaign_dir / "combined"),
                      "--output", str(report_path)]
    with (campaign_dir / "pair_evaluator.log").open("w", encoding="utf-8") as log:
        report_process = subprocess.run(report_command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False)
    manifest["status"] = "finished" if report_process.returncode == 0 else "invalid_pair"
    manifest["exit_codes"] = exit_codes
    manifest["pair_report"] = str(report_path)
    manifest["evaluator_exit_code"] = int(report_process.returncode)
    _write(campaign_dir / "campaign_manifest.json", manifest)
    return int(report_process.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--wall-time", type=float, default=600.0)
    parser.add_argument("--sim-time", type=float, default=600.0)
    parser.add_argument("--decision-target", type=int, default=280)
    parser.add_argument("--tick-s", type=float, default=0.25)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--min-free-gib", type=float, default=40.0)
    args = parser.parse_args()
    if args.decision_target <= 0:
        raise SystemExit("decision-target deve ser positivo para um par determinístico")
    return run_pair(args)


if __name__ == "__main__":
    raise SystemExit(main())
