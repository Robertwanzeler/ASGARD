#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_ROW_CHECKPOINTS = (1000, 2500, 5000)
DEFAULT_SIM_TIME_CHECKPOINTS = (145.0, 300.0, 600.0)
DEFAULT_CONFLICT_STABILITY_ROWS = 250

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from greenran_paths import load_fixed_scenario_config  # type: ignore

CORE_PID_FILES = {
    "ric": "ric.pid",
    "ns3": "ns3.pid",
    "csv_metrics": "csv_metrics.pid",
    "rapp": "rapp.pid",
    "carla_bridge": "carla_bridge.pid",
    "carla_mapper": "carla_ns3_mapper.pid",
    "app2": "app2_monitoramento.pid",
    "app3": "app3_veicular.pid",
    "dashboard": "dashboard.pid",
    "xapp_slicer": "xapp_slicer.pid",
    "xapp_energy": "xapp_energy.pid",
    "xapp_vehicle": "xapp_vehicle.pid",
}

DEFAULT_STATE_DIR = Path(os.environ.get("GREENRAN_CONTROLLED_STATE_DIR", "/tmp/greenran_fixed_awac_controlled"))
DEFAULT_CSV_PATH = REPO_ROOT / "runs" / "sac_bootstrap" / "workload_trace_fixed_baseline_v1_controlled.csv"
DEFAULT_STATUS_PATH = REPO_ROOT / "runs" / "sac_bootstrap" / "fixed_awac_controlled_status.json"
DEFAULT_LAUNCHER_LOG = Path("/tmp/greenran_fixed_awac_controlled_launcher.log")
DEFAULT_MONITOR_LOG = Path("/tmp/greenran_fixed_awac_controlled_monitor.log")
DEFAULT_ROW_TARGET = 5000
DEFAULT_MIN_ROWS = 1000
DEFAULT_RUNTIME_SIM_TIME = 600.0
DEFAULT_POLL_INTERVAL = 20.0
DEFAULT_START_TIMEOUT = 180.0
DEFAULT_APP2_WAIT = 90

RAN_PRESSURE_STAGE_DURATIONS = {
    "drl_article_v1": [
        ("baseline_healthy", 120.0),
        ("app1_warning", 70.0),
        ("app1_guard", 40.0),
        ("baseline_recovery_after_app1", 90.0),
        ("app2_stressed_safe", 90.0),
        ("vehicle_stressed_safe", 90.0),
        ("app2_guard", 45.0),
        ("baseline_recovery_after_app2", 120.0),
        ("vehicle_warning", 45.0),
        ("baseline_recovery_after_vehicle", 120.0),
        ("app2_critical", 30.0),
        ("app1_critical_short", 25.0),
        ("baseline_recovery_after_critical", 150.0),
    ],
    "drl_article_conflict_forced_v1": [
        ("baseline_healthy", 60.0),
        ("camera_overload", 90.0),
        ("mixed_overload", 120.0),
        ("background_overload", 90.0),
        ("recovery_window", 90.0),
        ("camera_overload_repeat", 60.0),
        ("final_recovery", 90.0),
    ],
    "drl_article_conflict_forced_fast_v1": [
        ("baseline_healthy", 10.0),
        ("camera_overload", 20.0),
        ("mixed_overload", 30.0),
        ("background_overload", 20.0),
        ("recovery_window", 15.0),
        ("camera_overload_repeat", 15.0),
        ("final_recovery", 20.0),
    ],
    "drl_article_conflict_smoke_v1": [
        ("baseline_healthy", 1.0),
        ("camera_overload", 8.0),
        ("mixed_overload", 8.0),
        ("background_overload", 6.0),
        ("recovery_window", 4.0),
        ("camera_overload_repeat", 6.0),
        ("final_recovery", 5.0),
    ],
}

STOP_REQUESTED = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controlled fixed-scenario AWAC collection runner for GreenRAN")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="Runtime state directory")
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH), help="Exported workload CSV path")
    parser.add_argument("--status-json", default=str(DEFAULT_STATUS_PATH), help="Machine-readable status output path")
    parser.add_argument("--launcher-log", default=str(DEFAULT_LAUNCHER_LOG), help="Launcher stdout/stderr log path")
    parser.add_argument("--row-target", type=int, default=DEFAULT_ROW_TARGET, help="Collection row target before completion")
    parser.add_argument("--min-rows", type=int, default=DEFAULT_MIN_ROWS, help="Minimum rows considered useful for retraining")
    parser.add_argument("--goal-sim-time", type=float, default=DEFAULT_RUNTIME_SIM_TIME, help="Informational sim_time goal shown in progress")
    parser.add_argument("--runtime-sim-time", type=int, default=int(DEFAULT_RUNTIME_SIM_TIME), help="simTime passed to the GreenRAN launcher")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="Seconds between status refreshes")
    parser.add_argument("--start-timeout", type=float, default=DEFAULT_START_TIMEOUT, help="Seconds to wait for the runtime to become ready")
    parser.add_argument("--app2-wait-seconds", type=int, default=DEFAULT_APP2_WAIT, help="App2 real sensor wait window")
    parser.add_argument("--keep-running", action="store_true", help="Do not stop the runtime when the row target is reached")
    parser.add_argument("--no-start", action="store_true", help="Monitor an existing runtime only; never start a new one")
    parser.add_argument("--force-restart", action="store_true", help="Stop the current runtime before starting a fresh controlled collection")
    parser.add_argument("--reset-state", action="store_true", help="Remove the controlled state dir, CSV, status and checkpoints before starting a fresh collection")
    parser.add_argument("--once", action="store_true", help="Collect and print a single status snapshot")
    parser.add_argument("--collection-profile", default="drl_article_v1", help="Collection event profile used by the launcher")
    parser.add_argument("--checkpoint-dir", default="", help="Directory for automatic checkpoint analysis reports; default derives from status-json name")
    parser.add_argument("--row-checkpoints", default=",".join(str(v) for v in DEFAULT_ROW_CHECKPOINTS), help="Comma-separated row milestones for automatic analysis")
    parser.add_argument("--sim-time-checkpoints", default=",".join(str(v) for v in DEFAULT_SIM_TIME_CHECKPOINTS), help="Comma-separated sim_time milestones for automatic analysis")
    parser.add_argument("--conflict-stability-rows", type=int, default=DEFAULT_CONFLICT_STABILITY_ROWS, help="Rows to keep collecting after first RAN conflict before checkpoint marks it stable")
    return parser.parse_args()


def handle_stop(signum: int, frame: Any) -> None:
    del signum, frame
    global STOP_REQUESTED
    STOP_REQUESTED = True


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def state_path(args: argparse.Namespace, *parts: str) -> Path:
    return Path(args.state_dir).joinpath(*parts)


def parse_int_checkpoints(raw: str) -> list[int]:
    values: list[int] = []
    for token in str(raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        values.append(max(0, safe_int(token, 0)))
    return sorted({value for value in values if value > 0})


def parse_float_checkpoints(raw: str) -> list[float]:
    values: list[float] = []
    for token in str(raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        values.append(max(0.0, safe_float(token, 0.0)))
    return sorted({value for value in values if value > 0.0})


def checkpoint_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "checkpoint_dir", ""):
        return Path(args.checkpoint_dir)
    status_path = Path(args.status_json)
    return status_path.parent / f"{status_path.stem}_checkpoints"


def checkpoint_registry_path(args: argparse.Namespace) -> Path:
    return checkpoint_dir(args) / "checkpoint_registry.json"


def checkpoint_latest_path(args: argparse.Namespace) -> Path:
    return checkpoint_dir(args) / "latest_checkpoint.json"


def load_checkpoint_registry(args: argparse.Namespace) -> dict[str, Any]:
    path = checkpoint_registry_path(args)
    if not path.exists():
        return {"emitted": {}, "first_conflict_rows": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"emitted": {}, "first_conflict_rows": None}
    if not isinstance(payload, dict):
        return {"emitted": {}, "first_conflict_rows": None}
    payload.setdefault("emitted", {})
    payload.setdefault("first_conflict_rows", None)
    return payload


def save_checkpoint_registry(args: argparse.Namespace, registry: dict[str, Any]) -> None:
    path = checkpoint_registry_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def build_checkpoint_verdict(args: argparse.Namespace, status: dict[str, Any], checkpoint_type: str, checkpoint_value: Any) -> dict[str, Any]:
    coverage = status.get("coverage") or {}
    latest_alloc = status.get("latest_alloc") or {}
    ran_pressure = status.get("ran_pressure") or {}
    rows = safe_int(status.get("rows"), 0)
    sim_time_end = safe_float(status.get("sim_time_end"), 0.0)
    ran_completion_unique = safe_int(coverage.get("ran_completion_unique"), 0)
    ran_conflict_observed = ran_completion_unique > 1 or safe_float(latest_alloc.get("ran_completion_ratio"), 1.0) < 0.999999

    if not status.get("runtime_core_alive"):
        readiness = "blocked_runtime"
        recommendation = "runtime principal caiu; coletar logs e estabilizar a instancia antes de seguir"
    elif ran_conflict_observed and rows >= max(args.min_rows, args.conflict_stability_rows):
        readiness = "ready_with_ran_conflict"
        recommendation = "coleta com conflito RAN observado; manter coleta ate aproximar a meta final e preparar retreino"
    elif ran_conflict_observed:
        readiness = "conflict_seen_but_early"
        recommendation = "conflito RAN apareceu; manter coleta por mais algumas centenas de linhas para consolidar o regime"
    elif rows < args.min_rows:
        readiness = "early_collection"
        recommendation = "seguir coletando; ainda falta volume minimo para avaliar prontidao de retreino"
    elif sim_time_end < 145.0:
        readiness = "waiting_pressure_window"
        recommendation = "seguir coletando ate atravessar as primeiras janelas de pressao RAN relevantes"
    else:
        readiness = "volume_ok_without_ran_conflict"
        recommendation = "volume ja util, mas sem conflito RAN real; revisar multiplicadores se isso persistir nos proximos checkpoints"

    return {
        "checkpoint_type": checkpoint_type,
        "checkpoint_value": checkpoint_value,
        "runtime_core_alive": bool(status.get("runtime_core_alive")),
        "rows": rows,
        "sim_time_end": sim_time_end,
        "pressure_stage": ran_pressure.get("stage", "unknown"),
        "pressure_cycle": safe_int(ran_pressure.get("cycle"), 0),
        "ran_completion_unique": ran_completion_unique,
        "ran_conflict_observed": ran_conflict_observed,
        "d_ran_unique": safe_int(coverage.get("d_ran_unique"), 0),
        "utilization_unique": safe_int(coverage.get("utilization_unique"), 0),
        "readiness": readiness,
        "recommendation": recommendation,
    }


def emit_checkpoint_report(args: argparse.Namespace, status: dict[str, Any], checkpoint_key: str, checkpoint_type: str, checkpoint_value: Any, registry: dict[str, Any]) -> dict[str, Any]:
    report_dir = checkpoint_dir(args)
    report_dir.mkdir(parents=True, exist_ok=True)
    verdict = build_checkpoint_verdict(args, status, checkpoint_type, checkpoint_value)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scenario_id": status.get("scenario_id"),
        "collection_profile": status.get("collection_profile"),
        "checkpoint_key": checkpoint_key,
        "status_snapshot": status,
        "verdict": verdict,
    }
    report_path = report_dir / f"{checkpoint_key}.json"
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    checkpoint_latest_path(args).write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    registry.setdefault("emitted", {})[checkpoint_key] = {
        "path": str(report_path),
        "generated_at": payload["generated_at"],
        "readiness": verdict["readiness"],
    }
    save_checkpoint_registry(args, registry)
    print(f"[checkpoint] {checkpoint_key}: {verdict['readiness']} | {verdict['recommendation']}", flush=True)
    return payload


def maybe_emit_checkpoints(args: argparse.Namespace, status: dict[str, Any], registry: dict[str, Any]) -> list[dict[str, Any]]:
    emitted_reports: list[dict[str, Any]] = []
    emitted = registry.setdefault("emitted", {})
    if "startup_snapshot" not in emitted:
        emitted_reports.append(emit_checkpoint_report(args, status, "startup_snapshot", "startup", status.get("updated_at"), registry))
    rows = safe_int(status.get("rows"), 0)
    sim_time_end = safe_float(status.get("sim_time_end"), 0.0)
    coverage = status.get("coverage") or {}
    ran_completion_unique = safe_int(coverage.get("ran_completion_unique"), 0)

    for milestone in parse_int_checkpoints(args.row_checkpoints):
        key = f"rows_{milestone}"
        if rows >= milestone and key not in emitted:
            emitted_reports.append(emit_checkpoint_report(args, status, key, "rows", milestone, registry))

    for milestone in parse_float_checkpoints(args.sim_time_checkpoints):
        key = f"sim_time_{str(milestone).replace('.', '_')}"
        if sim_time_end >= milestone and key not in emitted:
            emitted_reports.append(emit_checkpoint_report(args, status, key, "sim_time", milestone, registry))

    if ran_completion_unique > 1 and registry.get("first_conflict_rows") is None:
        registry["first_conflict_rows"] = rows
        save_checkpoint_registry(args, registry)

    if ran_completion_unique > 1 and "ran_conflict_first_seen" not in emitted:
        emitted_reports.append(emit_checkpoint_report(args, status, "ran_conflict_first_seen", "ran_conflict", "first_seen", registry))

    first_conflict_rows = safe_int(registry.get("first_conflict_rows"), 0)
    stable_target = first_conflict_rows + max(args.conflict_stability_rows, 0)
    if first_conflict_rows > 0 and rows >= stable_target and "ran_conflict_stable" not in emitted:
        emitted_reports.append(emit_checkpoint_report(args, status, "ran_conflict_stable", "ran_conflict", stable_target, registry))

    if status.get("completed") and "collection_completed" not in emitted:
        emitted_reports.append(emit_checkpoint_report(args, status, "collection_completed", "collection", status.get("row_target"), registry))

    return emitted_reports


def get_ran_pressure_progress(profile: str, sim_time_end: float) -> dict[str, Any]:
    normalized_profile = str(profile or "none")
    if normalized_profile == "none":
        return {
            "profile": normalized_profile,
            "stage": "none",
            "cycle": 0,
            "stage_index": -1,
            "stage_elapsed": 0.0,
            "stage_remaining": 0.0,
            "cycle_elapsed": 0.0,
            "cycle_duration": 0.0,
        }

    stages = RAN_PRESSURE_STAGE_DURATIONS.get(normalized_profile)
    if not stages:
        return {
            "profile": normalized_profile,
            "stage": "unknown",
            "cycle": 0,
            "stage_index": -1,
            "stage_elapsed": 0.0,
            "stage_remaining": 0.0,
            "cycle_elapsed": 0.0,
            "cycle_duration": 0.0,
        }

    cycle_duration = sum(duration for _name, duration in stages)
    if cycle_duration <= 0.0:
        return {
            "profile": normalized_profile,
            "stage": "invalid",
            "cycle": 0,
            "stage_index": -1,
            "stage_elapsed": 0.0,
            "stage_remaining": 0.0,
            "cycle_elapsed": 0.0,
            "cycle_duration": 0.0,
        }

    safe_sim_time = max(safe_float(sim_time_end), 0.0)
    cycle_index = int(safe_sim_time // cycle_duration)
    cycle_elapsed = safe_sim_time - (cycle_index * cycle_duration)
    elapsed_before_stage = 0.0
    for stage_index, (stage_name, duration) in enumerate(stages):
        stage_end = elapsed_before_stage + duration
        if cycle_elapsed < stage_end or stage_index == len(stages) - 1:
            stage_elapsed = max(cycle_elapsed - elapsed_before_stage, 0.0)
            return {
                "profile": normalized_profile,
                "stage": stage_name,
                "cycle": cycle_index,
                "stage_index": stage_index,
                "stage_elapsed": round(stage_elapsed, 3),
                "stage_remaining": round(max(duration - stage_elapsed, 0.0), 3),
                "cycle_elapsed": round(cycle_elapsed, 3),
                "cycle_duration": round(cycle_duration, 3),
            }
        elapsed_before_stage = stage_end

    return {
        "profile": normalized_profile,
        "stage": "unknown",
        "cycle": cycle_index,
        "stage_index": -1,
        "stage_elapsed": 0.0,
        "stage_remaining": 0.0,
        "cycle_elapsed": round(cycle_elapsed, 3),
        "cycle_duration": round(cycle_duration, 3),
    }


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def process_alive(pid: int | None) -> bool:
    return pid is not None and Path(f"/proc/{pid}").exists()


def pid_snapshot(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for name, filename in CORE_PID_FILES.items():
        pid = read_pid(state_path(args, filename))
        snapshot[name] = {
            "pid": pid,
            "alive": process_alive(pid),
        }
    return snapshot


def core_runtime_alive(snapshot: dict[str, dict[str, Any]]) -> bool:
    required = ("ns3", "csv_metrics", "rapp")
    return all(snapshot.get(name, {}).get("alive") for name in required)


def run_command(cmd: list[str], *, env: dict[str, str] | None = None, stdout: Any = None, stderr: Any = None, check: bool = False, start_new_session: bool = False) -> subprocess.CompletedProcess[str] | subprocess.Popen[str]:
    if start_new_session:
        return subprocess.Popen(cmd, cwd=REPO_ROOT, env=env, stdout=stdout, stderr=stderr, text=True, start_new_session=True)
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, stdout=stdout, stderr=stderr, text=True, check=check)


def stop_runtime() -> None:
    run_command(["bash", "scripts/stop_all.sh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reset_collection_state(args: argparse.Namespace) -> None:
    state_dir = Path(args.state_dir)
    if state_dir.exists():
        shutil.rmtree(state_dir, ignore_errors=True)

    csv_path = Path(args.csv_path)
    if csv_path.exists():
        csv_path.unlink()

    status_path = Path(args.status_json)
    if status_path.exists():
        status_path.unlink()

    checkpoint_path = checkpoint_dir(args)
    if checkpoint_path.exists():
        shutil.rmtree(checkpoint_path, ignore_errors=True)

    launcher_log = Path(args.launcher_log)
    if launcher_log.exists():
        launcher_log.unlink()


def start_runtime(args: argparse.Namespace) -> int:
    env = os.environ.copy()
    env.update({
        "GREENRAN_STATE_DIR": str(Path(args.state_dir)),
        "GREENRAN_ENABLE_MONITORING_STACK": "0",
        "GREENRAN_RL_POLICY": "awac",
        "GREENRAN_APP2_REAL_SENSOR_WAIT_SECONDS": str(args.app2_wait_seconds),
        "GREENRAN_SIM_TIME": str(args.runtime_sim_time),
        "GREENRAN_COLLECTION_EVENT_PROFILE": str(args.collection_profile),
        "GREENRAN_RAN_PRESSURE_PROFILE": str(args.collection_profile),
        "GREENRAN_DRL_ARTICLE_OUT_DIR": str(REPO_ROOT / "runs" / "eedrl_greenran_final" / "article_metrics_fixed_baseline_controlled"),
    })
    launcher_log = Path(args.launcher_log)
    launcher_log.parent.mkdir(parents=True, exist_ok=True)
    with launcher_log.open("a", encoding="utf-8") as handle:
        handle.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] starting controlled collection runtime\n"
        )
        handle.flush()
        proc = run_command(["bash", "scripts/run_drl_article_real_collection.sh"], env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    assert isinstance(proc, subprocess.Popen)
    return proc.pid


def wait_for_runtime(args: argparse.Namespace) -> None:
    deadline = time.time() + args.start_timeout
    while time.time() < deadline:
        snapshot = pid_snapshot(args)
        if core_runtime_alive(snapshot):
            db_path = state_path(args, "rapp_data_lake.db")
            if db_path.exists():
                try:
                    conn = sqlite3.connect(str(db_path))
                    cursor = conn.cursor()
                    cursor.execute("select count(*) from resource_allocation_history")
                    alloc_rows = safe_int(cursor.fetchone()[0], 0)
                    conn.close()
                except sqlite3.Error:
                    alloc_rows = 0
                if alloc_rows > 0:
                    return
        time.sleep(2)
    raise TimeoutError(f"runtime did not become ready within {args.start_timeout}s")


def export_csv(args: argparse.Namespace) -> None:
    csv_path = Path(args.csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        sys.executable,
        str(REPO_ROOT / "scripts" / "export_sac_workload_trace.py"),
        "--db",
        str(state_path(args, "rapp_data_lake.db")),
        "--output-csv",
        str(csv_path),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    try:
        with csv_path.open("r", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def summarize_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    if not rows:
        return {
            "rows": 0,
            "controllers": {},
            "d_ran_unique": 0,
            "d_ai_unique": 0,
            "ran_completion_unique": 0,
            "ai_completion_unique": 0,
            "utilization_unique": 0,
        }
    def unique_count(key: str) -> int:
        return len({round(safe_float(row.get(key), 0.0), 6) for row in rows})
    controllers = Counter((row.get("controller_id") or "unknown") for row in rows)
    return {
        "rows": len(rows),
        "controllers": dict(controllers),
        "d_ran_unique": unique_count("d_ran"),
        "d_ai_unique": unique_count("d_ai"),
        "ran_completion_unique": unique_count("ran_completion_ratio"),
        "ai_completion_unique": unique_count("ai_completion_ratio"),
        "utilization_unique": unique_count("utilization_ratio"),
    }


def query_db(args: argparse.Namespace) -> dict[str, Any]:
    db_path = state_path(args, "rapp_data_lake.db")
    result: dict[str, Any] = {
        "db_exists": db_path.exists(),
        "resource_allocation_rows": 0,
        "latest_alloc": None,
        "latest_ext": None,
    }
    if not db_path.exists():
        return result
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        result["resource_allocation_rows"] = safe_int(cursor.execute("select count(*) from resource_allocation_history").fetchone()[0], 0)
        result["latest_alloc"] = cursor.execute(
            """
            select datetime, controller_id, d_ran, d_ai, r_ran, r_ai, ran_completion_ratio, ai_completion_ratio, utilization_ratio
            from resource_allocation_history
            order by timestamp desc
            limit 1
            """
        ).fetchone()
        result["latest_ext"] = cursor.execute(
            """
            select datetime, sim_time_s, throughput_kbps, cvar_per_ue_us
            from extended_metrics
            order by timestamp desc
            limit 1
            """
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        pass
    return result


def read_extended_metrics(args: argparse.Namespace) -> dict[str, Any]:
    path = state_path(args, "xapp_metrics", "extended_metrics.json")
    if not path.exists():
        return {}
    for _attempt in range(3):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            time.sleep(0.1)
        except OSError:
            break
    return {}


def sanitize_sim_time_end(raw_sim_time_end: float, latest_ext: Any, goal_sim_time: float) -> float:
    sim_time_end = max(safe_float(raw_sim_time_end), 0.0)
    throughput_kbps = 0.0
    if latest_ext:
        throughput_kbps = safe_float(latest_ext[2], 0.0)
    if sim_time_end > max(goal_sim_time * 1.5, 1000.0) and throughput_kbps <= 0.0:
        return 0.0
    if sim_time_end > max(goal_sim_time * 10.0, 10000.0):
        return 0.0
    return sim_time_end


def current_status(args: argparse.Namespace, started_at: float, launcher_pid: int | None) -> dict[str, Any]:
    export_csv(args)
    csv_path = Path(args.csv_path)
    rows = load_rows(csv_path)
    row_summary = summarize_rows(rows)
    db = query_db(args)
    ext = read_extended_metrics(args)
    snapshot = pid_snapshot(args)
    sim_time_range = ext.get("sim_time_range", {}) if isinstance(ext, dict) else {}
    latest_alloc = db.get("latest_alloc")
    latest_ext = db.get("latest_ext")
    sim_time_end_candidate = sim_time_range.get("end")
    if sim_time_end_candidate in (None, "", 0, 0.0) and latest_ext:
        sim_time_end_candidate = latest_ext[1]
    sim_time_end = sanitize_sim_time_end(sim_time_end_candidate, latest_ext, safe_float(args.goal_sim_time, 0.0))
    row_count = safe_int(row_summary.get("rows"), 0)
    row_target_remaining = max(args.row_target - row_count, 0)
    min_row_remaining = max(args.min_rows - row_count, 0)
    sim_time_remaining = max(safe_float(args.goal_sim_time, 0.0) - sim_time_end, 0.0)
    ran_pressure = get_ran_pressure_progress(args.collection_profile, sim_time_end)
    status = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "scenario_id": str((load_fixed_scenario_config() or {}).get("scenario_id", "unknown") or "unknown"),
        "collection_profile": str(args.collection_profile),
        "state_dir": str(Path(args.state_dir)),
        "csv_path": str(csv_path),
        "launcher_pid": launcher_pid,
        "launcher_alive": process_alive(launcher_pid),
        "runtime_core_alive": core_runtime_alive(snapshot),
        "pid_snapshot": snapshot,
        "rows": row_count,
        "row_target": int(args.row_target),
        "row_target_remaining": row_target_remaining,
        "min_rows": int(args.min_rows),
        "min_rows_remaining": min_row_remaining,
        "goal_sim_time": float(args.goal_sim_time),
        "sim_time_end": sim_time_end,
        "sim_time_remaining": sim_time_remaining,
        "ran_pressure": ran_pressure,
        "checkpoint_dir": str(checkpoint_dir(args)),
        "resource_allocation_rows": safe_int(db.get("resource_allocation_rows"), 0),
        "controllers": row_summary.get("controllers", {}),
        "coverage": {
            "d_ran_unique": row_summary.get("d_ran_unique", 0),
            "d_ai_unique": row_summary.get("d_ai_unique", 0),
            "ran_completion_unique": row_summary.get("ran_completion_unique", 0),
            "ai_completion_unique": row_summary.get("ai_completion_unique", 0),
            "utilization_unique": row_summary.get("utilization_unique", 0),
        },
        "latest_alloc": {
            "datetime": latest_alloc[0],
            "controller_id": latest_alloc[1],
            "d_ran": safe_float(latest_alloc[2]),
            "d_ai": safe_float(latest_alloc[3]),
            "r_ran": safe_float(latest_alloc[4]),
            "r_ai": safe_float(latest_alloc[5]),
            "ran_completion_ratio": safe_float(latest_alloc[6]),
            "ai_completion_ratio": safe_float(latest_alloc[7]),
            "utilization_ratio": safe_float(latest_alloc[8]),
        } if latest_alloc else None,
        "latest_ext": {
            "datetime": latest_ext[0],
            "sim_time_s": safe_float(latest_ext[1]),
            "throughput_kbps": safe_float(latest_ext[2]),
            "cvar_per_ue_us": safe_float(latest_ext[3]),
        } if latest_ext else None,
        "sim_time_range": sim_time_range,
        "wall_seconds": round(max(time.time() - started_at, 0.0), 1),
        "completed": row_target_remaining == 0,
    }
    return status


def write_status_json(args: argparse.Namespace, status: dict[str, Any]) -> None:
    path = Path(args.status_json)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def render_status(status: dict[str, Any]) -> str:
    latest_alloc = status.get("latest_alloc") or {}
    latest_ext = status.get("latest_ext") or {}
    controllers = status.get("controllers") or {}
    coverage = status.get("coverage") or {}
    ran_pressure = status.get("ran_pressure") or {}
    lines = [
        "GreenRAN Fixed AWAC Controlled Collection",
        "========================================",
        f"Atualizado em: {status.get('updated_at')}",
        f"Scenario ID : {status.get('scenario_id')}",
        f"State Dir   : {status.get('state_dir')}",
        f"CSV         : {status.get('csv_path')}",
        f"Checkpoints : {status.get('checkpoint_dir')}",
        f"Wall Time   : {status.get('wall_seconds')} s",
        "",
        "runtime",
        f"  core_alive        : {status.get('runtime_core_alive')}",
        f"  launcher_alive    : {status.get('launcher_alive')} (pid={status.get('launcher_pid')})",
        f"  core_pids         : " + ", ".join(f"{name}={'up' if meta.get('alive') else 'down'}" for name, meta in (status.get('pid_snapshot') or {}).items() if name in ('ric', 'ns3', 'csv_metrics', 'rapp', 'app2', 'app3')),
        "",
        "progress",
        f"  rows              : {status.get('rows')} / {status.get('row_target')} (faltam {status.get('row_target_remaining')})",
        f"  min_rows          : {status.get('rows')} / {status.get('min_rows')} (faltam {status.get('min_rows_remaining')})",
        f"  sim_time          : {status.get('sim_time_end'):.3f} / {status.get('goal_sim_time'):.3f} (faltam {status.get('sim_time_remaining'):.3f})",
        f"  ran_pressure      : profile={ran_pressure.get('profile', 'none')} cycle={ran_pressure.get('cycle', 0)} stage={ran_pressure.get('stage', 'none')} elapsed={safe_float(ran_pressure.get('stage_elapsed', 0.0)):.3f}s remaining={safe_float(ran_pressure.get('stage_remaining', 0.0)):.3f}s",
        f"  alloc_rows_db     : {status.get('resource_allocation_rows')}",
        "",
        "latest allocation",
        f"  datetime          : {latest_alloc.get('datetime', '-')}",
        f"  controller        : {latest_alloc.get('controller_id', '-')}",
        f"  d_ran / d_ai      : {latest_alloc.get('d_ran', 0.0):.4f} / {latest_alloc.get('d_ai', 0.0):.4f}",
        f"  r_ran / r_ai      : {latest_alloc.get('r_ran', 0.0):.4f} / {latest_alloc.get('r_ai', 0.0):.4f}",
        f"  completion        : ran={latest_alloc.get('ran_completion_ratio', 0.0):.4f} ai={latest_alloc.get('ai_completion_ratio', 0.0):.4f}",
        f"  utilization       : {latest_alloc.get('utilization_ratio', 0.0):.4f}",
        "",
        "latest extended metrics",
        f"  datetime          : {latest_ext.get('datetime', '-')}",
        f"  sim_time_s        : {latest_ext.get('sim_time_s', 0.0):.4f}",
        f"  throughput_kbps   : {latest_ext.get('throughput_kbps', 0.0):.2f}",
        f"  cvar_per_ue_us    : {latest_ext.get('cvar_per_ue_us', 0.0):.2f}",
        "",
        "coverage",
        f"  controllers       : {controllers}",
        f"  d_ran_unique      : {coverage.get('d_ran_unique', 0)}",
        f"  d_ai_unique       : {coverage.get('d_ai_unique', 0)}",
        f"  ran_completion    : {coverage.get('ran_completion_unique', 0)}",
        f"  ai_completion     : {coverage.get('ai_completion_unique', 0)}",
        f"  utilization       : {coverage.get('utilization_unique', 0)}",
    ]
    if status.get("completed"):
        lines.extend(["", "status", "  meta de linhas atingida"]) 
    elif not status.get("runtime_core_alive"):
        lines.extend(["", "status", "  runtime principal nao esta vivo; colete logs e verifique o launcher"]) 
    else:
        lines.extend(["", "status", "  coleta controlada em andamento"]) 
    return "\n".join(lines)


def print_status(status: dict[str, Any]) -> None:
    if sys.stdout.isatty():
        clear_cmd = "cls" if os.name == "nt" else "clear"
        os.system(clear_cmd)
    print(render_status(status), flush=True)


def main() -> int:
    args = parse_args()
    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    Path(args.state_dir).mkdir(parents=True, exist_ok=True)
    Path(args.csv_path).parent.mkdir(parents=True, exist_ok=True)

    launcher_pid: int | None = None
    snapshot = pid_snapshot(args)
    if args.force_restart:
        stop_runtime()
        snapshot = pid_snapshot(args)
    if args.reset_state:
        reset_collection_state(args)
        snapshot = pid_snapshot(args)

    if not core_runtime_alive(snapshot):
        if args.no_start:
            raise SystemExit("runtime nao esta ativo e --no-start foi usado")
        launcher_pid = start_runtime(args)
        wait_for_runtime(args)
    else:
        launcher_pid = None

    started_at = time.time()
    registry = load_checkpoint_registry(args)
    while True:
        status = current_status(args, started_at, launcher_pid)
        checkpoint_reports = maybe_emit_checkpoints(args, status, registry)
        if checkpoint_reports:
            registry = load_checkpoint_registry(args)
        write_status_json(args, status)
        print_status(status)

        if args.once:
            return 0
        if status.get("completed"):
            if not args.keep_running:
                stop_runtime()
            return 0
        if not status.get("runtime_core_alive"):
            return 1
        if STOP_REQUESTED:
            return 0
        time.sleep(max(args.poll_interval, 1.0))


if __name__ == "__main__":
    raise SystemExit(main())
