#!/usr/bin/env python3
"""Run the frozen 3-seed x 5-repetition rApp-only/ASGARD campaign.

This launcher is deliberately gated by the scientific V2X manifest and by a
promoted, replay-compatible checkpoint.  It is safe to plan without
``--execute``; execution is sequential so the two arms of each pair share
the exact schedule and cannot contend for the simulator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .tasam_pairing import canonical_schedule, free_gib, storage_is_rw, write_schedule
except ImportError:
    from tasam_pairing import canonical_schedule, free_gib, storage_is_rw, write_schedule


ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
EVALUATOR = ROOT / "scripts" / "evaluate_asgard_paired_campaign.py"
PROFILE = "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback"
SEEDS = (45, 46, 47)
REPETITIONS = 5


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _local(path: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit(f"{label} fora do workspace: {resolved}") from exc
    return resolved


def _preflight(
    baseline_manifest: Path,
    checkpoint: Path,
    calibration: Path,
    *,
    profile: str,
) -> tuple[dict[str, Any], str]:
    baseline = _json(baseline_manifest)
    if baseline.get("schema") != "greenran.autonomous_vehicle_feasibility.v4":
        raise SystemExit("baseline ASGARD exige manifesto veicular v4 multi-seed")
    if (
        baseline.get("status") != "passed"
        or baseline.get("scientific_decision") != "approved"
        or baseline.get("promotion_eligible") is not True
    ):
        raise SystemExit("baseline V2X ainda não está aprovada cientificamente")
    if baseline.get("profile") != profile:
        raise SystemExit("perfil do manifesto V2X não corresponde à campanha ASGARD")
    provenance = baseline.get("provenance") or {}
    contract = provenance.get("metric_contract") or {}
    matrix = baseline.get("multi_seed_validation") or {}
    if (
        baseline.get("metric_contract") != "per_pdu_cohort_v1"
        or contract.get("collector_mode") != "pdcp_real"
        or contract.get("pdcp_source") != "native_pdcp_pdu_tx_rx"
        or contract.get("proxy_allowed") is not False
        or matrix.get("valid") is not True
        or tuple(matrix.get("required_seeds") or []) != SEEDS
        or tuple(matrix.get("complete_seeds") or []) != SEEDS
        or matrix.get("seed47_reused_from_phase1") is not True
        or matrix.get("provenance_compatible") is not True
    ):
        raise SystemExit("baseline V2X não comprova PDCP real sem proxy")
    meta = _json(checkpoint / "tasam_marl_checkpoint_meta.json")
    if not (checkpoint / "tasam_marl_actors.pt").is_file():
        raise SystemExit("atores do checkpoint ASGARD ausentes")
    if not meta.get("final_metrics"):
        raise SystemExit("checkpoint ASGARD sem final_metrics")
    if meta.get("parent_was_promoted") is not True:
        raise SystemExit("checkpoint ASGARD sem pai promovido")
    if meta.get("replay_imported") is not True:
        raise SystemExit("checkpoint ASGARD sem replay compatível importado")
    calibration_payload = _json(calibration)
    if not calibration_payload.get("schema"):
        raise SystemExit("calibração de energia sem schema")
    digest = _sha256(checkpoint / "tasam_marl_actors.pt")
    if not digest:
        raise SystemExit("não foi possível fixar o hash do checkpoint")
    return baseline, digest


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _arm_command(
    mode: str,
    run_dir: Path,
    *,
    seed: int,
    profile: str,
    wall_time: float,
    sim_time: float,
    decision_target: int,
    schedule: dict[str, Any],
    schedule_file: Path,
    checkpoint: Path,
    calibration: Path,
    baseline_manifest: Path,
    min_free_gib: float,
) -> list[str]:
    return [
        sys.executable, str(ARM), "--mode", mode,
        "--run-dir", str(run_dir), "--seed", str(seed), "--profile", profile,
        "--wall-time", str(wall_time), "--sim-time", str(sim_time),
        "--decision-target", str(decision_target),
        "--pairing-schedule-id", str(schedule["schedule_id"]),
        "--pairing-schedule-file", str(schedule_file),
        "--checkpoint", str(checkpoint), "--energy-calibration", str(calibration),
        "--vehicle-profile-manifest", str(baseline_manifest),
        "--native-fidelity", "--performance-min-rtf", "0.016",
        "--min-free-gib", str(min_free_gib), "--artifact-min-free-gib", "10",
    ]


def _environment(schedule_file: Path, schedule: dict[str, Any], decision_target: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "GREENRAN_LOCAL_ONLY": "1",
        "GREENRAN_CGROUP_ENFORCE": "1",
        "GREENRAN_CGROUP_ALLOW_UNENFORCED": "0",
        "GREENRAN_REQUIRE_REAL_PDCP": "1",
        "GREENRAN_REAL_ONLY": "1",
        "GREENRAN_TASAM_EXPORT_ALLOW_PROXY": "0",
        "GREENRAN_COLLECTION_EVENT_TIME_SOURCE": "wall",
        "GREENRAN_COLLECTION_EVENT_TICK_S": str(schedule.get("tick_s", 0.25)),
        "GREENRAN_DECISION_TARGET": str(decision_target),
        "GREENRAN_PAIRING_SCHEDULE_ID": str(schedule["schedule_id"]),
        "GREENRAN_PAIRING_SCHEDULE_FILE": str(schedule_file),
    })
    return env


def execute(args: argparse.Namespace) -> int:
    campaign = _local(args.campaign_dir, "campaign-dir")
    baseline_manifest = _local(args.baseline_manifest, "baseline-manifest")
    checkpoint = _local(args.checkpoint, "checkpoint")
    calibration = _local(args.calibration, "calibration")
    if campaign.exists() and any(campaign.iterdir()):
        raise SystemExit(f"campanha não está vazia: {campaign}")
    if tuple(args.seeds) != SEEDS or args.repetitions != REPETITIONS:
        raise SystemExit("campanha ASGARD exige seeds 45,46,47 e cinco repetições por seed")
    if args.decision_target != 0:
        raise SystemExit("campanha V2X estrita exige --decision-target 0")
    if args.sim_time < 331.5:
        raise SystemExit("campanha V2X estrita exige warm-up, 30 janelas e drenagem PDCP completos")
    if free_gib(campaign.parent) < args.min_free_gib:
        raise SystemExit("espaço livre insuficiente para a campanha ASGARD")
    ok, options = storage_is_rw(campaign.parent)
    if not ok:
        raise SystemExit(f"armazenamento não está em rw: {options}")
    baseline, checkpoint_sha256 = _preflight(
        baseline_manifest, checkpoint, calibration, profile=args.profile
    )
    campaign.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "schema": "greenran.asgard.paired_campaign.v1",
        "status": "planned",
        "profile": args.profile,
        "seeds": list(SEEDS),
        "repetitions": REPETITIONS,
        "wall_time_s": args.wall_time,
        "sim_time_s": args.sim_time,
        "decision_target": args.decision_target,
        "comparison_arms": {
            "control": {
                "directory": "rapp_only",
                "mode": "rapp_only_actuating",
                "description": "rApp nativa atuando por E2, sem ARMD e sem TA-SAM",
            },
            "treatment": {
                "directory": "combined",
                "mode": "combined",
                "description": "ASGARD: rApp + ARMD + TA-SAM com checkpoint congelado",
            },
        },
        "metric_contract": "per_pdu_cohort_v1",
        "baseline_manifest": str(baseline_manifest),
        "baseline_selected_interval_us": baseline.get("selected_interval_us"),
        "checkpoint": str(checkpoint),
        "checkpoint_actors_sha256": checkpoint_sha256,
        "calibration": str(calibration),
        "created_at": int(time.time()),
        "pairs_completed": [],
    }
    _write(campaign / "campaign_manifest.json", manifest)
    environment = _environment(Path("<per-pair>"), {"schedule_id": "<per-pair>", "tick_s": 0.25}, args.decision_target)

    for seed in SEEDS:
        for repetition in range(1, REPETITIONS + 1):
            root = campaign / f"seed_{seed}" / f"rep_{repetition}"
            root.mkdir(parents=True, exist_ok=False)
            schedule = canonical_schedule(args.profile, seed, args.wall_time, tick_s=0.25)
            schedule["repetition"] = repetition
            schedule_file = root / "pairing_schedule.json"
            write_schedule(schedule_file, schedule)
            pair_state = {"seed": seed, "repetition": repetition, "status": "running", "schedule_id": schedule["schedule_id"]}
            _write(root / "pair_manifest.json", pair_state)
            environment = _environment(schedule_file, schedule, args.decision_target)
            # Preserve the historical directory name, but run the active
            # rApp control through the same E2 path as ASGARD.  Otherwise
            # actuation itself would confound the comparison.
            for mode, directory in (("rapp_only_actuating", "rapp_only"), ("combined", "combined")):
                run_dir = root / directory
                command = _arm_command(
                    mode, run_dir, seed=seed, profile=args.profile,
                    wall_time=args.wall_time, sim_time=args.sim_time,
                    decision_target=args.decision_target, schedule=schedule,
                    schedule_file=schedule_file, checkpoint=checkpoint,
                    calibration=calibration, baseline_manifest=baseline_manifest,
                    min_free_gib=args.min_free_gib,
                )
                log_path = root / f"{directory}.launcher.log"
                with log_path.open("w", encoding="utf-8") as log:
                    result = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False)
                if result.returncode != 0:
                    pair_state.update({"status": "failed", "failed_mode": mode, "exit_code": int(result.returncode)})
                    _write(root / "pair_manifest.json", pair_state)
                    manifest["status"] = "failed"
                    _write(campaign / "campaign_manifest.json", manifest)
                    return int(result.returncode)
                if _sha256(checkpoint / "tasam_marl_actors.pt") != checkpoint_sha256:
                    pair_state.update({"status": "failed", "reason": "checkpoint_mutated"})
                    _write(root / "pair_manifest.json", pair_state)
                    manifest["status"] = "failed"
                    _write(campaign / "campaign_manifest.json", manifest)
                    return 7
            pair_state["status"] = "finished"
            _write(root / "pair_manifest.json", pair_state)
            manifest["pairs_completed"].append({"seed": seed, "repetition": repetition})
            manifest["status"] = "running"
            _write(campaign / "campaign_manifest.json", manifest)

    report = campaign / "campaign_report.json"
    command = [
        sys.executable, str(EVALUATOR), "--campaign-root", str(campaign),
        "--baseline-manifest", str(baseline_manifest), "--checkpoint", str(checkpoint),
        "--profile", args.profile, "--seeds", *[str(seed) for seed in SEEDS],
        "--repetitions", str(REPETITIONS), "--warmup-s", "30", "--duration-s", str(args.sim_time),
        "--output", str(report),
    ]
    code = subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode
    report_payload = _json(report)
    manifest.update({
        "status": "approved" if report_payload.get("scientific_decision") == "approved" else "rejected",
        "scientific_decision": report_payload.get("scientific_decision", "blocked"),
        "report": str(report),
        "evaluator_exit_code": int(code),
        "finished_at": int(time.time()),
    })
    _write(campaign / "campaign_manifest.json", manifest)
    return int(code)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    parser.add_argument("--repetitions", type=int, default=REPETITIONS)
    parser.add_argument("--wall-time", type=float, default=43200.0)
    parser.add_argument("--sim-time", type=float, default=331.5)
    parser.add_argument("--decision-target", type=int, default=0)
    parser.add_argument("--min-free-gib", type=float, default=20.0)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({
            "schema": "greenran.asgard.paired_campaign.plan.v1",
            "campaign_dir": str(args.campaign_dir.resolve()),
            "seeds": list(args.seeds), "repetitions": args.repetitions,
            "profile": args.profile,
            "sim_time_s": args.sim_time,
            "decision_target": args.decision_target,
            "comparison_arms": {
                "control": "rapp_only_actuating",
                "treatment": "combined_asgard",
            },
            "execute": False,
        }, indent=2, ensure_ascii=False))
        return 0
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
