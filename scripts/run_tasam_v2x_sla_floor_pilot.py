#!/usr/bin/env python3
"""Launch the isolated SLA-governed ASGARD energy pilot.

The historical safe-power ledger is deliberately not an input.  A new
``sla_floor_state.json`` is created in the campaign directory and is updated
only from post-action native SLA evidence by the orchestrator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
EXPORTER = ROOT / "scripts" / "export_tasam_article_dataset.py"
DEFAULT_BINARY = ROOT / (
    "ns-O-RAN-flexric/mmwave-LENA-oran/build-v9-optimized/scratch/"
    "ns3.42-Energy_saving_with_cell_utilization_scenario-optimized"
)
DEFAULT_PROFILE = "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max"
DEFAULT_MODE = "asgard_v2x_sla_floor_online"
DEFAULT_REWARD = "greenran.tasam.v2x.reward_adaptive.v1"
DEFAULT_CALIBRATION = ROOT / "config/energy_calibration_sim_v3_sleep.json"
CONTRACT = "greenran.tasam.v2x.sla_floor.v1"
ECONOMIC = "economic_action_v3_per_du_sleep"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(bytes.fromhex(sha256(item)))
    return digest.hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_history(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"replay histórico ausente: {path}")
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) < 90:
        raise SystemExit(f"replay histórico tem {len(rows)} linhas; são necessárias 90")
    return {"path": str(path.resolve()), "sha256": sha256(path), "rows": len(rows)}


def build_command(args: argparse.Namespace, arm_dir: Path, recent: Path) -> list[str]:
    command = [
        sys.executable, str(ARM),
        "--mode", DEFAULT_MODE,
        "--run-dir", str(arm_dir),
        "--seed", "43",
        "--profile", DEFAULT_PROFILE,
        "--wall-time", str(int(args.wall_time)),
        "--sim-time", "120",
        "--decision-target", "0",
        "--native-fidelity",
        "--performance-min-rtf", "0.016",
        "--binary", str(args.binary.resolve()),
        "--checkpoint", str(args.checkpoint.resolve()),
        "--execution-slot", args.execution_slot,
        "--disable-app-overrides",
        "--energy-enabled",
        "--energy-calibration", str(args.calibration.resolve()),
        "--experience-bank", str(args.history.resolve()),
        "--recent-experience-bank", str(recent),
        "--replay-rows", "90",
        "--min-new-snapshots", "18",
        "--min-trainable-transitions", "90",
        "--epochs-per-update", "1",
        "--controller-poll-seconds", "5",
        "--max-rollout-fraction", "1.0",
        "--min-economic-transitions", "0",
        "--economic-update-min-transitions", "0",
        "--min-free-gib", "20",
        "--artifact-min-free-gib", "20",
        "--artifact-budget-gib", "4",
    ]
    return command


def run(args: argparse.Namespace) -> int:
    if args.seed != 43 or args.sim_time != 120 or args.decision_target != 0:
        raise SystemExit("o piloto SLA exige seed 43, sim-time 120 e decision-target=0")
    if args.profile != DEFAULT_PROFILE or args.mode != DEFAULT_MODE:
        raise SystemExit("perfil e modo são fixos no piloto SLA")
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        raise SystemExit(f"binário ausente/não executável: {args.binary}")
    if not args.checkpoint.is_dir():
        raise SystemExit(f"checkpoint ausente: {args.checkpoint}")
    if not args.calibration.is_file():
        raise SystemExit(f"calibração ausente: {args.calibration}")
    history = validate_history(args.history)
    root = args.output_root.resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"output-root não está vazio: {root}")
    arm_dir = root / "online" / "arm"
    recent = arm_dir / "recent_replay.jsonl"
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "greenran.tasam.v2x.sla_floor_pilot.manifest.v1",
        "campaign_kind": "sla_floor_engineering_pilot",
        "status": "running",
        "promotion_eligible": False,
        "scientific_decision": "not_promotable",
        "mode": DEFAULT_MODE,
        "profile": DEFAULT_PROFILE,
        "seed": 43,
        "sim_time_s": 120,
        "decision_target": 0,
        "collector_mode": "pdcp_real",
        "proxy_allowed": False,
        "scenario_control_override": False,
        "native_evidence_version": "v6",
        "sla_floor_contract": CONTRACT,
        "sla_floor_config": str(ROOT / "config/greenran_v2x_sla_floor.json"),
        "sla_floor_config_sha256": sha256(ROOT / "config/greenran_v2x_sla_floor.json"),
        "economic_action_contract": ECONOMIC,
        "reward_contract": DEFAULT_REWARD,
        "historical_safe_power_floor_authority": False,
        "history": history,
        "binary": str(args.binary.resolve()),
        "binary_sha256": sha256(args.binary),
        "checkpoint": str(args.checkpoint.resolve()),
        "checkpoint_sha256": tree_sha256(args.checkpoint),
        "execution_slot": args.execution_slot,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(root / "campaign_manifest.json", manifest)
    command = build_command(args, arm_dir, recent)
    log_path = root / "online" / "arm.log"
    arm_dir.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    state = read_json(arm_dir / "sla_floor_state.json", {})
    online = read_json(arm_dir / "online_state.json", {})
    raw = arm_dir / "raw_export.jsonl"
    export_error = ""
    try:
        subprocess.run([
            sys.executable, str(EXPORTER),
            "--db", str(arm_dir / "rapp_data_lake.db"),
            "--output-jsonl", str(raw),
            "--summary-json", str(arm_dir / "raw_export_summary.json"),
            "--e2-audit-jsonl", str(arm_dir / "xapp_intents" / "tasam_control_audit.jsonl"),
            "--reward-contract", DEFAULT_REWARD,
            "--energy-enabled",
            "--energy-calibration", str(args.calibration.resolve()),
            "--allocation-total-head-enabled",
        ], cwd=ROOT, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        export_error = str(exc)
    status = "metric_invalid" if export_error else (
        "pilot_complete" if process.returncode == 0 else "pilot_training_incomplete"
    )
    report = {
        "schema": "greenran.tasam.v2x.sla_floor_pilot.report.v1",
        "campaign_kind": "sla_floor_engineering_pilot",
        "status": status,
        "promotion_eligible": False,
        "scientific_decision": "not_promotable",
        "arm_exit_code": process.returncode,
        "online_state": online,
        "sla_floor_state": state,
        "replay_history": history,
        "raw_export": str(raw) if raw.is_file() else "",
        "export_error": export_error,
        "strict_requirements": {
            "all_imsis": list(range(1, 21)),
            "vehicle_tx_pdus_min": 500,
            "pdcp_loss_exclusive_percent": 1,
            "vehicle_p95_exclusive_us": 20000,
            "camera_throughput_min_kbps": 25000,
            "sensor_delivery_min_percent": 95,
        },
    }
    manifest.update({"status": status, "arm_exit_code": process.returncode, "online_state": online})
    write_json(root / "campaign_manifest.json", manifest)
    write_json(root / "campaign_report.json", report)
    return 0 if status == "pilot_complete" else 2


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--mode", default=DEFAULT_MODE)
    parser.add_argument("--seed", type=int, default=43)
    parser.add_argument("--sim-time", type=float, default=120)
    parser.add_argument("--decision-target", type=int, default=0)
    parser.add_argument("--wall-time", type=float, default=9000)
    parser.add_argument("--execution-slot", choices=("slot-a", "slot-b"), default="slot-b")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
