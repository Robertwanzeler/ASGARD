#!/usr/bin/env python3
"""Plan or execute the three-seed online TA-SAM energy campaign.

Execution is opt-in because each seed contains three real-PDCP simulator
arms and the default budget is 600 seconds per arm.  The driver runs arms
sequentially so they cannot interfere with one another, while preserving the
same topology, stage profile, seed and wall-clock budget.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .run_tasam_online_arm import DEFAULT_CHECKPOINT, PROFILE, PROTECTED_MARKERS
except ImportError:  # direct ``python scripts/run_tasam_online_campaign.py``
    from run_tasam_online_arm import DEFAULT_CHECKPOINT, PROFILE, PROTECTED_MARKERS


ROOT = Path(__file__).resolve().parents[1]
ARM_RUNNER = ROOT / "scripts" / "run_tasam_online_arm.py"
EVALUATOR = ROOT / "scripts" / "evaluate_tasam_paired_campaign.py"
PRIMARY_SEED = 47


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _validate_campaign_dir(path: Path) -> None:
    lowered = str(path).lower()
    if "armd" in lowered or any(marker.lower() in lowered for marker in PROTECTED_MARKERS):
        raise SystemExit(f"diretório de campanha protegido ou relacionado a ARMD: {path}")


def build_plan(
    campaign_dir: Path,
    seeds: list[int],
    profile: str,
    wall_time: float,
    checkpoint: Path,
    primary_seed: int = PRIMARY_SEED,
) -> dict[str, Any]:
    return {
        "schema": "greenran.tasam_online_campaign_plan.v1",
        "campaign_dir": str(campaign_dir.resolve()),
        "profile": profile,
        "seeds": seeds,
        "primary_seed": int(primary_seed),
        "primary_seed_selection": "highest_observed_energy_saving_percent",
        "wall_time_s": wall_time,
        "initial_checkpoint": str(checkpoint.resolve()),
        "phase_1": {
            "mode": "train_no_armd",
            "replay": "live_sqlite_only",
            "armd": "off",
            "tasam": "full_control",
            "updates": "continuous_until_end_of_run",
        },
        "phase_2": {
            "arms": ["rapp_only", "combined"],
            "combined": "ARMD assist + TA-SAM online + rApp Judge",
            "pairing": ["seed", "profile", "topology", "traffic", "wall_time", "initial_condition"],
        },
        "protected_paths": list(PROTECTED_MARKERS),
        "energy": {
            "formula": "(energy_rApp_only - energy_ARMD_TA-SAM_rApp) / energy_rApp_only",
            "model": "calibrated_ru_mmwave_power_model",
            "physical_wattmeter": False,
        },
        "created_at": int(time.time()),
    }


def _arm_command(mode: str, run_dir: Path, seed: int, args: argparse.Namespace, checkpoint: Path) -> list[str]:
    return [
        sys.executable,
        str(ARM_RUNNER),
        "--mode", mode,
        "--run-dir", str(run_dir),
        "--seed", str(seed),
        "--profile", args.profile,
        "--wall-time", str(args.wall_time),
        "--checkpoint", str(checkpoint),
        "--min-free-gib", str(args.min_free_gib),
        "--min-new-snapshots", str(args.min_new_snapshots),
        "--min-trainable-transitions", str(args.min_trainable_transitions),
        "--epochs-per-update", str(args.epochs_per_update),
        "--controller-poll-seconds", str(args.controller_poll_seconds),
    ]


def _run(command: list[str], *, dry_run: bool) -> int:
    print("$ " + " ".join(command), flush=True)
    if dry_run:
        return 0
    return int(subprocess.run(command, cwd=ROOT, check=False).returncode)


def execute(args: argparse.Namespace) -> int:
    campaign_dir = args.campaign_dir.resolve()
    _validate_campaign_dir(campaign_dir)
    checkpoint = args.checkpoint.resolve()
    if args.primary_seed not in args.seeds:
        raise SystemExit(f"primary-seed precisa estar na lista de seeds: {args.primary_seed}")
    if not checkpoint.joinpath("tasam_marl_actors.pt").is_file():
        raise SystemExit(f"checkpoint inicial ausente: {checkpoint / 'tasam_marl_actors.pt'}")
    campaign_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        campaign_dir / "campaign_manifest.json",
        build_plan(campaign_dir, args.seeds, args.profile, args.wall_time, checkpoint, args.primary_seed),
    )

    for seed in args.seeds:
        seed_root = campaign_dir / f"seed_{seed}"
        training_dir = seed_root / "train_no_armd"
        baseline_dir = seed_root / "rapp_only"
        combined_dir = seed_root / "combined"
        state = {"seed": seed, "phase": "train_no_armd", "status": "running", "updated_at": int(time.time())}
        _write_json(seed_root / "campaign_state.json", state)
        code = _run(_arm_command("train_no_armd", training_dir, seed, args, checkpoint), dry_run=False)
        if code != 0:
            state.update({"status": "failed", "exit_code": code})
            _write_json(seed_root / "campaign_state.json", state)
            return code
        online_state = _read_json(training_dir / "online_state.json")
        trained_checkpoint = Path(str(online_state.get("active_checkpoint", ""))).resolve()
        if int(online_state.get("updates_completed", 0) or 0) <= 0 or not trained_checkpoint.joinpath("tasam_marl_actors.pt").is_file():
            state.update({"status": "failed", "reason": "treino sem atualização online válida"})
            _write_json(seed_root / "campaign_state.json", state)
            return 5
        state.update({"phase": "rapp_only", "training_checkpoint": str(trained_checkpoint)})
        _write_json(seed_root / "campaign_state.json", state)
        code = _run(_arm_command("rapp_only", baseline_dir, seed, args, trained_checkpoint), dry_run=False)
        if code != 0:
            state.update({"status": "failed", "exit_code": code})
            _write_json(seed_root / "campaign_state.json", state)
            return code
        state.update({"phase": "combined"})
        _write_json(seed_root / "campaign_state.json", state)
        code = _run(_arm_command("combined", combined_dir, seed, args, trained_checkpoint), dry_run=False)
        if code != 0:
            state.update({"status": "failed", "exit_code": code})
            _write_json(seed_root / "campaign_state.json", state)
            return code
        state.update({"status": "finished", "phase": "complete", "finished_at": int(time.time())})
        _write_json(seed_root / "campaign_state.json", state)

    output = args.output.resolve() if args.output else campaign_dir / "paired_comparison.json"
    return _run(
        [
            sys.executable, str(EVALUATOR),
            "--campaign-dir", str(campaign_dir),
            "--seeds", *[str(seed) for seed in args.seeds],
            "--profile", args.profile,
            "--wall-time", str(args.wall_time),
            "--primary-seed", str(args.primary_seed),
            "--output", str(output),
        ],
        dry_run=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, default=ROOT / "runs" / "tasam_online_paired_20260831")
    parser.add_argument("--seeds", type=int, nargs="+", default=[45, 46, 47])
    parser.add_argument("--primary-seed", type=int, default=PRIMARY_SEED)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--wall-time", type=float, default=600.0)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--min-free-gib", type=float, default=40.0)
    parser.add_argument("--min-new-snapshots", type=int, default=100)
    parser.add_argument("--min-trainable-transitions", type=int, default=300)
    parser.add_argument("--epochs-per-update", type=int, default=2)
    parser.add_argument("--controller-poll-seconds", type=float, default=10.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true", help="inicia as nove execuções de simulador")
    args = parser.parse_args()
    _validate_campaign_dir(args.campaign_dir.resolve())
    if args.primary_seed not in args.seeds:
        raise SystemExit(f"primary-seed precisa estar na lista de seeds: {args.primary_seed}")
    plan = build_plan(args.campaign_dir, args.seeds, args.profile, args.wall_time, args.checkpoint, args.primary_seed)
    if not args.execute:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        for seed in args.seeds:
            seed_root = args.campaign_dir.resolve() / f"seed_{seed}"
            print("\nseed", seed)
            for mode in ("train_no_armd", "rapp_only", "combined"):
                checkpoint = args.checkpoint if mode == "train_no_armd" else Path("<checkpoint-produzido-pelo-treino>")
                print("$ " + " ".join(_arm_command(mode, seed_root / mode, seed, args, checkpoint)))
        print("\nUse --execute somente após reparar o HD e liberar espaço no SSD.")
        return 0
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
