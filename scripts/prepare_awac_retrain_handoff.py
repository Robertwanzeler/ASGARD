#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS = REPO_ROOT / "runs" / "sac_bootstrap" / "fixed_awac_pressure_smoke_clean_v5_status.json"
DEFAULT_CHECKPOINT = REPO_ROOT / "runs" / "sac_bootstrap" / "fixed_awac_pressure_smoke_clean_v5_status_checkpoints" / "latest_checkpoint.json"
DEFAULT_MANIFEST = REPO_ROOT / "runs" / "sac_bootstrap" / "awac_retrain_handoff_latest.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "runs" / "sac_bootstrap"
DEFAULT_PYTHON = REPO_ROOT / "drlexp" / ".venv" / "bin" / "python"
DEFAULT_TRAIN_SCRIPT = REPO_ROOT / "drlexp" / "training" / "train_sac.py"
DEFAULT_STATE_DIR = Path("/tmp/greenran_fixed_awac_pressure_smoke_clean_v5")
DEFAULT_CSV = REPO_ROOT / "runs" / "sac_bootstrap" / "workload_trace_fixed_baseline_v1_pressure_smoke_clean_v5.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare AWAC retraining handoff from a live GreenRAN collection")
    parser.add_argument("--status-json", default=str(DEFAULT_STATUS), help="Path to collection status.json")
    parser.add_argument("--latest-checkpoint", default=str(DEFAULT_CHECKPOINT), help="Path to latest checkpoint JSON")
    parser.add_argument("--manifest-json", default=str(DEFAULT_MANIFEST), help="Path to write the retraining handoff manifest")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Root directory for offline AWAC output dirs")
    parser.add_argument("--python-bin", default=str(DEFAULT_PYTHON), help="Python binary used for AWAC training")
    parser.add_argument("--train-script", default=str(DEFAULT_TRAIN_SCRIPT), help="Path to train_sac.py")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="Runtime state directory used by the probe refresh")
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV), help="Workload CSV path used by the probe refresh")
    parser.add_argument("--collection-profile", default="drl_article_conflict_smoke_v1", help="Collection profile passed to the probe refresh")
    parser.add_argument("--goal-sim-time", type=float, default=120.0, help="Goal sim_time passed to the probe refresh")
    parser.add_argument("--runtime-sim-time", type=int, default=120, help="Runtime sim_time passed to the probe refresh")
    parser.add_argument("--row-target", type=int, default=5000, help="Row target passed to the probe refresh")
    parser.add_argument("--min-rows", type=int, default=300, help="Minimum rows passed to the probe refresh")
    parser.add_argument("--app2-wait-seconds", type=int, default=15, help="App2 wait passed to the probe refresh")
    parser.add_argument("--probe-refresh", action=argparse.BooleanOptionalAction, default=True, help="Refresh the collection status/checkpoint with a live --no-start --once probe before evaluating stop-readiness")
    parser.add_argument("--stop-rows", type=int, default=1500, help="Minimum collected rows before the run is considered ready to stop")
    parser.add_argument("--min-d-ran-unique", type=int, default=4, help="Minimum d_ran diversity required")
    parser.add_argument("--min-ran-completion-unique", type=int, default=10, help="Minimum ran_completion diversity required")
    parser.add_argument("--require-readiness", default="ready_with_ran_conflict", help="Checkpoint readiness value required for stop readiness")
    parser.add_argument("--poll-seconds", type=float, default=20.0, help="Polling interval when --watch is enabled")
    parser.add_argument("--watch", action="store_true", help="Keep polling until the handoff becomes ready")
    parser.add_argument("--train", action="store_true", help="Start offline AWAC training automatically when ready")
    parser.add_argument("--once", action="store_true", help="Evaluate once, print result and exit")
    return parser.parse_args()


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def refresh_probe(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_fixed_awac_controlled_collection.py"),
        "--state-dir",
        str(args.state_dir),
        "--csv-path",
        str(args.csv_path),
        "--status-json",
        str(args.status_json),
        "--launcher-log",
        "/tmp/greenran_prepare_handoff_launcher.log",
        "--checkpoint-dir",
        str(Path(args.latest_checkpoint).parent),
        "--collection-profile",
        str(args.collection_profile),
        "--row-target",
        str(args.row_target),
        "--min-rows",
        str(args.min_rows),
        "--goal-sim-time",
        str(args.goal_sim_time),
        "--runtime-sim-time",
        str(args.runtime_sim_time),
        "--app2-wait-seconds",
        str(args.app2_wait_seconds),
        "--no-start",
        "--once",
    ]
    subprocess.run(cmd, cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def build_train_command(args: argparse.Namespace, status: dict) -> tuple[list[str], Path]:
    csv_path = Path(status.get("csv_path") or "")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    scenario_id = str(status.get("scenario_id") or "collection")
    out_dir = Path(args.output_root) / f"offline_awac_{stamp}_{scenario_id}_handoff"
    cmd = [
        str(Path(args.python_bin)),
        str(Path(args.train_script)),
        "--workload-csv",
        str(csv_path),
        "--output-dir",
        str(out_dir),
        "--offline-mode",
        "awac",
    ]
    return cmd, out_dir


def evaluate(args: argparse.Namespace) -> dict:
    if args.probe_refresh:
        refresh_probe(args)

    status = load_json(Path(args.status_json))
    checkpoint = load_json(Path(args.latest_checkpoint))
    verdict = checkpoint.get("verdict") or {}
    coverage = status.get("coverage") or {}

    rows = safe_int(status.get("rows"), 0)
    d_ran_unique = safe_int(coverage.get("d_ran_unique"), 0)
    ran_completion_unique = safe_int(coverage.get("ran_completion_unique"), 0)
    runtime_core_alive = bool(status.get("runtime_core_alive"))
    readiness = str(verdict.get("readiness") or "unknown")

    conditions = {
        "runtime_core_alive": runtime_core_alive,
        "required_readiness": readiness == str(args.require_readiness),
        "rows_gte_stop_rows": rows >= args.stop_rows,
        "d_ran_unique_gte_min": d_ran_unique >= args.min_d_ran_unique,
        "ran_completion_unique_gte_min": ran_completion_unique >= args.min_ran_completion_unique,
    }
    stop_ready = all(conditions.values())
    train_cmd, output_dir = build_train_command(args, status)
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scenario_id": status.get("scenario_id"),
        "collection_profile": status.get("collection_profile"),
        "status_json": str(Path(args.status_json)),
        "latest_checkpoint_json": str(Path(args.latest_checkpoint)),
        "probe_refresh": bool(args.probe_refresh),
        "status": {
            "rows": rows,
            "row_target": safe_int(status.get("row_target"), 0),
            "sim_time_end": safe_float(status.get("sim_time_end"), 0.0),
            "pressure_stage": ((status.get("ran_pressure") or {}).get("stage") or "unknown"),
            "latest_alloc": status.get("latest_alloc") or {},
            "coverage": coverage,
            "runtime_core_alive": runtime_core_alive,
        },
        "checkpoint": {
            "key": checkpoint.get("checkpoint_key", "missing"),
            "readiness": readiness,
            "ran_conflict_observed": bool(verdict.get("ran_conflict_observed", False)),
            "recommendation": verdict.get("recommendation", "n/a"),
        },
        "stop_criteria": {
            "stop_rows": args.stop_rows,
            "min_d_ran_unique": args.min_d_ran_unique,
            "min_ran_completion_unique": args.min_ran_completion_unique,
            "require_readiness": args.require_readiness,
        },
        "conditions": conditions,
        "stop_ready": stop_ready,
        "workload_csv": status.get("csv_path"),
        "suggested_output_dir": str(output_dir),
        "suggested_train_command": train_cmd,
        "suggested_train_command_shell": " ".join(shlex.quote(part) for part in train_cmd),
    }
    return manifest


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def maybe_train(args: argparse.Namespace, manifest: dict) -> int:
    if not args.train:
        return 0
    if not manifest.get("stop_ready"):
        return 2
    cmd = manifest.get("suggested_train_command") or []
    if not cmd:
        return 3
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)
    return 0


def print_summary(manifest: dict) -> None:
    status = manifest.get("status") or {}
    checkpoint = manifest.get("checkpoint") or {}
    print("AWAC Retrain Handoff")
    print("=" * 20)
    print(f"scenario_id      : {manifest.get('scenario_id', 'unknown')}")
    print(f"profile          : {manifest.get('collection_profile', 'unknown')}")
    print(f"probe_refresh    : {manifest.get('probe_refresh', False)}")
    print(f"rows             : {status.get('rows', 0)}")
    print(f"sim_time_end     : {status.get('sim_time_end', 0.0)}")
    print(f"pressure_stage   : {status.get('pressure_stage', 'unknown')}")
    print(f"readiness        : {checkpoint.get('readiness', 'unknown')}")
    print(f"stop_ready       : {manifest.get('stop_ready', False)}")
    print(f"conditions       : {manifest.get('conditions', {})}")
    print(f"workload_csv     : {manifest.get('workload_csv', '')}")
    print(f"output_dir       : {manifest.get('suggested_output_dir', '')}")
    print(f"train_command    : {manifest.get('suggested_train_command_shell', '')}")


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest_json)

    if args.watch and args.once:
        raise SystemExit("use either --watch or --once, not both")

    while True:
        manifest = evaluate(args)
        write_manifest(manifest_path, manifest)
        print_summary(manifest)
        if args.once or not args.watch:
            return maybe_train(args, manifest)
        if manifest.get("stop_ready"):
            return maybe_train(args, manifest)
        time.sleep(max(args.poll_seconds, 1.0))


if __name__ == "__main__":
    raise SystemExit(main())
