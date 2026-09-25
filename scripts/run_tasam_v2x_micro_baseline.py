#!/usr/bin/env python3
"""Run and validate the non-promotable 120 s V2X rApp micro-baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = ROOT / "runs"
ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
DEFAULT_BINARY = ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario"
DEFAULT_CHECKPOINT = ROOT / "runs/sac_bootstrap/online_sac_match_tasam_60ep"
PROFILE = "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback"
MODE = "rapp_only_actuating"
SEED = 47
SIM_TIME_S = 120.0
WALL_TIME_S = 9000.0
PERFORMANCE_MIN_RTF = 0.016
EXPECTED_STAGES = (
    "allowed_bootstrap",
    "allowed_stable",
    "camera_conditional",
    "camera_blocked",
    "vehicle_conditional",
    "vehicle_blocked",
    "app2_conditional",
    "app2_blocked",
    "allowed_recovery",
)
VEHICLE_IMSIS = (16, 17, 18, 19, 20)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_fixed_args(args: argparse.Namespace) -> None:
    if args.profile != PROFILE:
        raise SystemExit(f"micro-baseline exige o perfil {PROFILE}")
    if args.mode != MODE:
        raise SystemExit(f"micro-baseline exige o modo {MODE}")
    if int(args.seed) != SEED:
        raise SystemExit("micro-baseline exige seed 47")
    if float(args.sim_time) != SIM_TIME_S:
        raise SystemExit("micro-baseline exige exatamente 120 s simulados")
    if int(args.decision_target) != 0:
        raise SystemExit("micro-baseline exige decision-target=0")
    if float(args.performance_min_rtf) != PERFORMANCE_MIN_RTF:
        raise SystemExit("micro-baseline exige performance-min-rtf=0.016")
    if args.energy_enabled:
        raise SystemExit("micro-baseline não permite a fase energética")


def _decision_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "rapp_decisions.jsonl"
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def stage_evidence(run_dir: Path) -> dict[str, Any]:
    records = _decision_records(run_dir)
    first_seen: list[str] = []
    per_stage: dict[str, dict[str, Any]] = {}
    for record in records:
        stage = str(record.get("collection_event_stage_name") or "")
        if stage not in EXPECTED_STAGES:
            continue
        item = per_stage.setdefault(stage, {"decisions": 0, "pdcp_correlated": 0})
        item["decisions"] += 1
        snapshot = str(record.get("snapshot_sequence_id") or "")
        if snapshot.startswith("pdcp:"):
            item["pdcp_correlated"] += 1
        if stage not in first_seen:
            first_seen.append(stage)
    observed = first_seen[: len(EXPECTED_STAGES)]
    return {
        "expected": list(EXPECTED_STAGES),
        "observed_first_cycle": observed,
        "complete_order": tuple(observed) == EXPECTED_STAGES,
        "per_stage": per_stage,
        "decision_count": len(records),
        "correlated_decisions": sum(
            int(item.get("pdcp_correlated", 0)) for item in per_stage.values()
        ),
    }


def _pdcp_trace(run_dir: Path) -> Path | None:
    candidates = (
        run_dir / "ns3_traces" / "DlPdcpStats.txt",
        run_dir / "ns3_traces" / "DlE2PdcpStats.txt",
        run_dir / "ns3_energy" / "DlPdcpStats.txt",
    )
    return next((path for path in candidates if path.is_file() and path.stat().st_size > 0), None)


def pdcp_evidence(run_dir: Path) -> dict[str, Any]:
    trace = _pdcp_trace(run_dir)
    totals = {imsi: {"tx_pdus": 0, "rx_pdus": 0, "rows": 0} for imsi in VEHICLE_IMSIS}
    if trace is not None:
        with trace.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                fields = raw.split()
                if len(fields) < 9 or raw.lstrip().startswith("%"):
                    continue
                try:
                    imsi = int(fields[3])
                    tx = int(fields[6])
                    rx = int(fields[8])
                except (ValueError, IndexError):
                    continue
                if imsi in totals:
                    totals[imsi]["tx_pdus"] += max(0, tx)
                    totals[imsi]["rx_pdus"] += max(0, rx)
                    totals[imsi]["rows"] += 1
    complete = trace is not None and all(item["tx_pdus"] > 0 and item["rx_pdus"] > 0 for item in totals.values())
    return {
        "collector_mode": "pdcp_real" if complete else "unknown",
        "pdcp_source": str(trace) if trace else "",
        "proxy_allowed": False,
        "proxy_count": 0,
        "complete_vehicle_coverage": complete,
        "by_imsi": {str(imsi): value for imsi, value in totals.items()},
        "p95_ms": None,
        "p95_note": "micro-baseline não promove SLA; P95 individual exige trace PDU dedicado",
    }


def control_evidence(run_dir: Path) -> dict[str, Any]:
    actuator = run_dir / "xapp_tasam_actuator.log"
    observations = run_dir / "ns3_energy" / "TasamControlObservations.csv"
    actuator_text = actuator.read_text(encoding="utf-8", errors="replace") if actuator.is_file() else ""
    observation_text = observations.read_text(encoding="utf-8", errors="replace") if observations.is_file() else ""
    sent_success = bool(re.search(r"control tx end success=1", actuator_text))
    readback = "power_readback" in observation_text or "state_snapshot" in observation_text
    return {
        "e2_control_sent_success": sent_success,
        "e2_readback_observed": readback,
        "valid": sent_success and readback,
        "actuator_log": str(actuator) if actuator.is_file() else "",
        "observation_trace": str(observations) if observations.is_file() else "",
    }


def trace_integrity(run_dir: Path) -> dict[str, Any]:
    candidates = {
        "scheduler": (run_dir / "ns3_energy" / "VehicleSchedulerTrace.csv", run_dir / "ns3_traces" / "VehicleSchedulerTrace.csv"),
        "link": (run_dir / "ns3_energy" / "VehicleLinkTrace.csv", run_dir / "ns3_traces" / "VehicleLinkTrace.csv"),
        "association": (run_dir / "ns3_energy" / "TasamAssociationTrace.csv", run_dir / "ns3_traces" / "TasamAssociationTrace.csv"),
    }
    selected = {}
    for name, paths in candidates.items():
        path = next((item for item in paths if item.is_file() and item.stat().st_size > 0), None)
        selected[name] = str(path) if path else ""
    return {"valid": all(selected.values()), "files": selected}


def simulation_evidence(run_dir: Path) -> dict[str, Any]:
    arm = _read_json(run_dir / "arm" / "arm_manifest.json")
    if not arm:
        arm = _read_json(run_dir / "arm_manifest.json")
    performance = arm.get("simulation_performance") if isinstance(arm.get("simulation_performance"), dict) else {}
    observed = float(performance.get("sim_time_observed_s", 0.0) or 0.0)
    return {
        "sim_time_requested_s": SIM_TIME_S,
        "sim_time_observed_s": observed,
        "wall_time_observed_s": float(performance.get("wall_time_observed_s", 0.0) or 0.0),
        "rtf": performance.get("rtf"),
        "performance_min_rtf": PERFORMANCE_MIN_RTF,
        "valid": observed >= SIM_TIME_S and (performance.get("valid") is not False),
        "arm_status": arm.get("status", ""),
    }


def build_report(campaign_dir: Path, arm_returncode: int) -> dict[str, Any]:
    arm_dir = campaign_dir / "arm"
    stages = stage_evidence(arm_dir)
    pdcp = pdcp_evidence(arm_dir)
    control = control_evidence(arm_dir)
    traces = trace_integrity(arm_dir)
    simulation = simulation_evidence(campaign_dir)
    if simulation.get("rtf") is not None and float(simulation["rtf"]) < PERFORMANCE_MIN_RTF:
        status = "performance_infeasible"
    elif not simulation["valid"] or not stages["complete_order"] or stages["correlated_decisions"] == 0 or not pdcp["complete_vehicle_coverage"] or not control["valid"] or not traces["valid"]:
        status = "micro_incomplete"
    else:
        status = "micro_complete"
    return {
        "schema": "greenran.v2x.micro_baseline_report.v1",
        "campaign_kind": "micro_v2x_operational_baseline",
        "status": status,
        "scientific_decision": "not_promotable",
        "promotion_eligible": False,
        "baseline_eligible": False,
        "arm_returncode": int(arm_returncode),
        "profile": PROFILE,
        "mode": MODE,
        "seed": SEED,
        "stage_evidence": stages,
        "pdcp_evidence": pdcp,
        "control_evidence": control,
        "trace_integrity": traces,
        "simulation_evidence": simulation,
        "sla_scoring": "observational_only",
        "scenario_control_override_excluded_from_sla": True,
        "recorded_at": int(time.time()),
    }


def run_campaign(args: argparse.Namespace) -> int:
    validate_fixed_args(args)
    campaign_dir = args.output_root.resolve()
    try:
        campaign_dir.relative_to(RUNS_ROOT.resolve())
    except ValueError as exc:
        raise SystemExit(f"output-root precisa estar em {RUNS_ROOT}") from exc
    if campaign_dir.exists() and any(campaign_dir.iterdir()):
        raise SystemExit(f"output-root não está vazio: {campaign_dir}")
    if not ARM.is_file() or not args.binary.is_file() or not args.checkpoint.is_dir():
        raise SystemExit("arm, binário ou checkpoint de referência ausente")
    campaign_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "greenran.v2x.micro_baseline_manifest.v1",
        "campaign_kind": "micro_v2x_operational_baseline",
        "status": "running",
        "scientific_decision": "not_promotable",
        "promotion_eligible": False,
        "profile": PROFILE,
        "mode": MODE,
        "seed": SEED,
        "sim_time_s": SIM_TIME_S,
        "wall_time_s": WALL_TIME_S,
        "decision_target": 0,
        "performance_min_rtf": PERFORMANCE_MIN_RTF,
        "checkpoint_role": "unused_for_rapp_only",
        "binary": str(args.binary),
        "binary_sha256": _sha256(args.binary),
        "profile_config": str(ROOT / "config" / f"{PROFILE}.json"),
        "profile_config_sha256": _sha256(ROOT / "config" / f"{PROFILE}.json"),
        "launcher_sha256": _sha256(Path(__file__).resolve()),
        "created_at": int(time.time()),
    }
    _write_json(campaign_dir / "campaign_manifest.json", manifest)
    arm_dir = campaign_dir / "arm"
    command = [
        sys.executable, str(ARM),
        "--mode", MODE,
        "--run-dir", str(arm_dir),
        "--seed", str(SEED),
        "--profile", PROFILE,
        "--checkpoint", str(args.checkpoint),
        "--binary", str(args.binary),
        "--wall-time", str(int(WALL_TIME_S)),
        "--sim-time", str(int(SIM_TIME_S)),
        "--decision-target", "0",
        "--min-free-gib", "20",
        "--artifact-budget-gib", "4",
        "--artifact-min-free-gib", "10",
        "--native-fidelity",
        "--performance-min-rtf", str(PERFORMANCE_MIN_RTF),
    ]
    _write_json(campaign_dir / "command.json", {"command": command, "checkpoint_role": "unused_for_rapp_only"})
    completed = subprocess.run(command, cwd=ROOT, check=False)
    report = build_report(campaign_dir, completed.returncode)
    manifest.update({
        "status": report["status"],
        "scientific_decision": "not_promotable",
        "promotion_eligible": False,
        "finished_at": int(time.time()),
        "arm_returncode": int(completed.returncode),
    })
    _write_json(campaign_dir / "campaign_manifest.json", manifest)
    _write_json(campaign_dir / "campaign_report.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "micro_complete" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--mode", default=MODE)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--sim-time", type=float, default=SIM_TIME_S)
    parser.add_argument("--decision-target", type=int, default=0)
    parser.add_argument("--performance-min-rtf", type=float, default=PERFORMANCE_MIN_RTF)
    parser.add_argument("--energy-enabled", action="store_true")
    args = parser.parse_args()
    args.binary = args.binary.resolve()
    args.checkpoint = args.checkpoint.resolve()
    return run_campaign(args)


if __name__ == "__main__":
    raise SystemExit(main())
