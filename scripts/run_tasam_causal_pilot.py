#!/usr/bin/env python3
"""Run the fail-closed seed-47 causal TA-SAM pilot."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
PREFLIGHT = ROOT / "scripts" / "preflight_tasam_causal_pilot.py"
EVALUATOR = ROOT / "scripts" / "evaluate_tasam_causal_comparison.py"
DEFAULT_LOCAL_CHECKPOINT = ROOT / "runs/tasam_local_checkpoint_seed47_20260906"
DEFAULT_SHADOW_DB = ROOT / "runs/tasam_local_causal_pilot_seed47_20260906_v4/shadow_300/rapp_data_lake.db"
PROFILE = "tasam_training_balanced_v3"


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run(command: list[str], *, cwd: Path = ROOT) -> int:
    print("$ " + " ".join(str(item) for item in command), flush=True)
    return int(subprocess.run(command, cwd=cwd, check=False).returncode)


def _preflight_command(args: argparse.Namespace, output: Path) -> list[str]:
    return [
        sys.executable, str(PREFLIGHT),
        "--checkpoint", str(args.checkpoint),
        "--shadow-db", str(args.shadow_db),
        "--control-gate", str(args.control_gate),
        "--ns3-bin", str(args.ns3_bin),
        "--output", str(output),
        "--min-free-gib", str(args.min_free_gib),
        "--min-shadow-samples", str(args.min_shadow_samples),
        "--min-positive-rate", str(args.min_positive_rate),
    ]


def _arm_command(mode: str, run_dir: Path, args: argparse.Namespace, *, wall_time: float, sim_time: float, schedule: bool) -> list[str]:
    command = [
        sys.executable, str(ARM), "--mode", mode,
        "--run-dir", str(run_dir), "--seed", str(args.seed),
        "--profile", args.profile, "--wall-time", str(wall_time),
        "--sim-time", str(sim_time), "--checkpoint", str(args.checkpoint),
        "--min-free-gib", str(args.min_free_gib),
        "--control-fraction", str(args.control_fraction),
    ]
    if getattr(args, "calibration", None):
        command.extend(["--energy-calibration", str(args.calibration)])
    if mode == "combined":
        command.extend(["--control-gate", str(args.control_gate)])
    if schedule:
        command.extend(["--pairing-schedule-id", args.schedule_id, "--pairing-schedule-file", str(args.schedule_file)])
    return command


def execute(args: argparse.Namespace) -> int:
    campaign = args.campaign_dir.resolve()
    if campaign.exists() and any(campaign.iterdir()):
        raise SystemExit(f"diretório do piloto não está vazio: {campaign}")
    campaign.mkdir(parents=True, exist_ok=True)
    args.checkpoint = args.checkpoint.resolve()
    args.shadow_db = args.shadow_db.resolve()
    args.control_gate = args.control_gate.resolve()
    args.ns3_bin = args.ns3_bin.resolve()
    for label, path in (
        ("campaign-dir", campaign),
        ("checkpoint", args.checkpoint),
        ("shadow-db", args.shadow_db),
        ("control-gate", args.control_gate),
        ("ns3-bin", args.ns3_bin),
    ):
        try:
            path.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise SystemExit(f"{label} precisa estar no workspace local: {path}") from exc

    os.environ["GREENRAN_LOCAL_ONLY"] = "1"
    os.environ["GREENRAN_CGROUP_ENFORCE"] = "1"
    os.environ["GREENRAN_CGROUP_ALLOW_UNENFORCED"] = "0"

    from tasam_pairing import canonical_schedule, write_schedule
    schedule = canonical_schedule(args.profile, args.seed, args.wall_time)
    args.schedule_id = schedule["schedule_id"]
    args.schedule_file = campaign / "pairing_schedule.json"
    write_schedule(args.schedule_file, schedule)
    _write(campaign / "pilot_manifest.json", {
        "schema": "greenran.tasam.causal_pilot.v1",
        "seed": args.seed, "profile": args.profile, "wall_time_s": args.wall_time,
        "sim_time_s": args.sim_time, "checkpoint": str(args.checkpoint),
        "shadow_db": str(args.shadow_db), "control_gate": str(args.control_gate),
        "schedule_id": args.schedule_id, "created_at": int(time.time()),
    })
    preflight_output = campaign / "preflight.json"
    if _run(_preflight_command(args, preflight_output)) != 0:
        print("Pré-voo reprovado; nenhuma simulação será iniciada.", file=sys.stderr)
        return 2

    canary_dir = campaign / "canary"
    code = _run(_arm_command("combined", canary_dir, args, wall_time=args.canary_wall_time, sim_time=args.canary_wall_time, schedule=False))
    if code != 0:
        print("Canary reprovado; piloto principal não será iniciado.", file=sys.stderr)
        return code
    state = json.loads((canary_dir / "control_trial_state.json").read_text(encoding="utf-8")) if (canary_dir / "control_trial_state.json").is_file() else {}
    if state.get("rollback") is True or int(state.get("applied_decisions", 0) or 0) <= 0:
        print("Canary sem aplicação válida ou com rollback; piloto principal não será iniciado.", file=sys.stderr)
        return 3

    baseline_dir = campaign / "baseline_rapp_only"
    # The arm launcher deliberately rejects paths containing ``armd`` to
    # protect historical artifacts; keep the semantic label in the manifest
    # while using a neutral filesystem name.
    treatment_dir = campaign / "treatment_combined"
    code = _run(_arm_command("rapp_only", baseline_dir, args, wall_time=args.wall_time, sim_time=args.sim_time, schedule=True))
    if code != 0:
        return code
    code = _run(_arm_command("combined", treatment_dir, args, wall_time=args.wall_time, sim_time=args.sim_time, schedule=True))
    if code != 0:
        return code
    output = campaign / "causal_comparison.json"
    evaluation = [sys.executable, str(EVALUATOR), "--baseline", str(baseline_dir), "--treatment", str(treatment_dir), "--seed", str(args.seed), "--wall-time", str(args.wall_time), "--output", str(output)]
    if getattr(args, "calibration", None):
        evaluation.extend(["--energy-calibration", str(args.calibration), "--metric-scope", "simulation"])
    return _run(evaluation)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, default=ROOT / "runs" / "tasam_local_causal_pilot_seed47_20260906_v4" / "main")
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--wall-time", type=float, default=600.0)
    parser.add_argument("--sim-time", type=float, default=600.0)
    parser.add_argument("--canary-wall-time", type=float, default=120.0)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_LOCAL_CHECKPOINT)
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--control-gate", type=Path, required=True)
    parser.add_argument("--ns3-bin", type=Path, default=ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario")
    parser.add_argument("--min-free-gib", type=float, default=10.0)
    parser.add_argument("--min-shadow-samples", type=int, default=300)
    parser.add_argument("--min-positive-rate", type=float, default=0.80)
    parser.add_argument("--control-fraction", type=float, default=0.10)
    parser.add_argument("--calibration", type=Path, default=None)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"schema": "greenran.tasam.causal_pilot_plan.v1", "campaign_dir": str(args.campaign_dir.resolve()), "seed": args.seed, "profile": args.profile, "wall_time_s": args.wall_time, "checkpoint": str(args.checkpoint), "control_gate": str(args.control_gate), "execute": False}, indent=2, ensure_ascii=False))
        return 0
    if not 0.0 < args.control_fraction <= 1.0:
        raise SystemExit("control-fraction deve estar entre 0 e 1")
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
