#!/usr/bin/env python3
"""Build an offline TA-SAM dataset from repeated short ns-3 collection rounds."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = ROOT / "config" / "tasam_article_ns3_collection.json"
DEFAULT_OUTPUT = ROOT / "runs" / "tasam_article_offline_collection"
DEFAULT_NS3_BIN = (
    ROOT
    / "ns-O-RAN-flexric"
    / "mmwave-LENA-oran"
    / "build"
    / "scratch"
    / "ns3.42-Energy_saving_with_cell_utilization_scenario-default"
)
DEFAULT_TRACE_NAME = "tasam_article_trace.jsonl"
DEFAULT_SUMMARY_NAME = "tasam_article_export_summary.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run short ns-3 TA-SAM rounds and merge the exported dataset")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT), help="Output directory for round artifacts")
    parser.add_argument("--rounds", type=int, default=3, help="Number of short collection rounds")
    parser.add_argument("--sim-time", type=int, default=120, help="Simulation time per round")
    parser.add_argument("--settle-seconds", type=float, default=8.0, help="Wait after ns-3 exits before stopping collector/rApp")
    parser.add_argument("--post-ns3-grace-seconds", type=float, default=5.0, help="Keep collector/rApp alive briefly after ns-3 exits so final traces can be ingested")
    parser.add_argument("--stall-timeout-seconds", type=float, default=120.0, help="Abort a round if sim/traces stop making progress for this many wall-clock seconds")
    parser.add_argument("--max-round-wall-seconds", type=float, default=240.0, help="Abort a round if the wall-clock runtime exceeds this bound even if the process stays alive")
    parser.add_argument("--collector-poll-interval", type=float, default=0.5, help="Collector polling interval")
    parser.add_argument("--orchestrator-interval", type=float, default=1.0, help="rApp interval during offline collection")
    parser.add_argument("--ue-count", type=int, default=50, help="Total UE count per round")
    parser.add_argument("--camera-ue-count", type=int, default=19, help="Camera UE count per round")
    parser.add_argument("--vehicle-ue-count", type=int, default=12, help="Vehicle UE count per round")
    parser.add_argument("--mmwave-enb-nodes", type=int, default=6, help="mmWave DU/gNB node count")
    parser.add_argument("--logical-du-count", type=int, default=0, help="Logical DU count override; 0 keeps mmWave node count")
    parser.add_argument("--ue-speed-min", type=float, default=0.0, help="Minimum UE speed")
    parser.add_argument("--ue-speed-max", type=float, default=3.0, help="Maximum UE speed")
    parser.add_argument("--ran-pressure-profile", default="drl_article_v1", help="Scenario traffic pressure profile")
    parser.add_argument("--collection-event-profile", default="", help="Optional scenario-control profile for application-side staged collection")
    parser.add_argument("--collection-event-time-source", choices=("sim", "wall"), default="sim", help="Drive collection-event stages from sim or wall clock")
    parser.add_argument("--armd-mode", default="off", help="ARMD runtime mode for offline collection (default: off)")
    parser.add_argument("--pdcp-stale-seconds", type=float, default=3600.0, help="PDCP stale threshold for offline collection; keep high to avoid proxy fallback during short rounds")
    parser.add_argument("--export-limit", type=int, default=0, help="Optional transition cap per round")
    parser.add_argument("--allow-proxy", action="store_true", help="Legacy compatibility option for exploratory runs")
    parser.add_argument("--ns3-bin", default=str(DEFAULT_NS3_BIN), help="ns-3 scenario binary")
    parser.add_argument("--base-config", default=str(BASE_CONFIG), help="Base scenario JSON config")
    parser.add_argument("--dry-run", action="store_true", help="Prepare commands without executing them")
    return parser


def run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None, dry_run: bool = False) -> None:
    print(" ".join(cmd), flush=True)
    if dry_run:
        return
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def start_process(
    cmd: list[str],
    *,
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> subprocess.Popen[str] | None:
    print(" ".join(cmd), flush=True)
    if dry_run:
        return None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def terminate_process(proc: subprocess.Popen[str] | None, timeout: float = 10.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.2)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    proc.wait(timeout=5)


def cleanup_round_xapps(env: dict[str, str], dry_run: bool = False) -> None:
    cmd = [
        sys.executable,
        "-c",
        (
            "from rapp_xapp_manager import XAppManager; "
            "manager = XAppManager(); "
            "manager.stop_all(); "
            "manager.cleanup_zombies()"
        ),
    ]
    cleanup_env = dict(env)
    cleanup_env["GREENRAN_CLEAN_SCOPE"] = "instance"
    run(cmd, cwd=ROOT, env=cleanup_env, dry_run=dry_run)


def resolve_ns3_bin(raw: str) -> Path:
    path = Path(raw)
    if not path.exists():
        raise SystemExit(f"ns-3 binary not found: {path}")
    if not os.access(path, os.X_OK):
        raise SystemExit(f"ns-3 binary is not executable: {path}")
    return path


def load_base_config(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Failed to load base config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Base config is not a JSON object: {path}")
    return payload


def _expand_range(start: int, end: int) -> list[int]:
    if end < start:
        return []
    return list(range(start, end + 1))


def build_round_config(base: dict[str, Any], args: argparse.Namespace, round_index: int) -> dict[str, Any]:
    ue_count = int(args.ue_count)
    camera_count = int(args.camera_ue_count)
    vehicle_count = int(args.vehicle_ue_count)
    if ue_count <= 0:
        raise SystemExit("ue-count must be positive")
    if camera_count < 0 or vehicle_count < 0 or camera_count + vehicle_count > ue_count:
        raise SystemExit("camera-ue-count + vehicle-ue-count must fit inside ue-count")

    background_start = camera_count + 1
    background_end = ue_count - vehicle_count
    vehicle_start = background_end + 1
    logical_du_count = int(args.logical_du_count or args.mmwave_enb_nodes)

    payload = json.loads(json.dumps(base))
    payload["scenario_id"] = f"tasam_article_offline_round_{round_index:04d}"

    ns3_cfg = payload.setdefault("ns3", {})
    ns3_cfg["total_ues"] = ue_count
    ns3_cfg["camera_imsis"] = _expand_range(1, camera_count)
    ns3_cfg["background_imsi_range"] = [background_start, background_end] if background_end >= background_start else []
    ns3_cfg["mmwave_enb_nodes"] = int(args.mmwave_enb_nodes)
    ns3_cfg["ue_speed_mps"] = {"min": float(args.ue_speed_min), "max": float(args.ue_speed_max)}

    device_roles = payload.setdefault("device_roles", {})
    device_roles["camera_imsi_range"] = [1, camera_count] if camera_count > 0 else []
    device_roles["sensor_imsi_range"] = [background_start, background_end] if background_end >= background_start else []
    device_roles["vehicle_imsi_range"] = [vehicle_start, ue_count] if vehicle_count > 0 else []

    apps = payload.setdefault("apps", {})
    app1 = apps.setdefault("app1", {})
    app1["active_cameras"] = camera_count
    app3 = apps.setdefault("app3", {})
    app3["max_vehicles"] = vehicle_count
    app3["vehicle_imsi_range"] = [vehicle_start, ue_count] if vehicle_count > 0 else []
    app3["base_imsi"] = vehicle_start if vehicle_count > 0 else ue_count + 1

    marl = payload.setdefault("marl", {})
    marl["logical_du_count"] = logical_du_count
    if isinstance(marl.get("logical_dus"), list) and logical_du_count > 0:
        marl["logical_dus"] = marl["logical_dus"][:logical_du_count]

    return payload


def write_round_config(config: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def prepare_round_dirs(round_dir: Path) -> dict[str, Path]:
    state_dir = round_dir / "state"
    trace_dir = state_dir / "ns3_traces"
    export_dir = round_dir / "export"
    xapp_metrics = state_dir / "xapp_metrics"
    for path in (round_dir, state_dir, trace_dir, export_dir, xapp_metrics):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "round_dir": round_dir,
        "state_dir": state_dir,
        "trace_dir": trace_dir,
        "export_dir": export_dir,
        "config_path": round_dir / "scenario.json",
        "collector_log": round_dir / "csv_metrics.log",
        "rapp_log": round_dir / "rapp.log",
        "ns3_log": round_dir / "ns3.log",
        "alternator_log": round_dir / "collection_event_alternator.log",
        "db_path": state_dir / "rapp_data_lake.db",
        "trace_jsonl": export_dir / DEFAULT_TRACE_NAME,
        "summary_json": export_dir / DEFAULT_SUMMARY_NAME,
    }


def build_runtime_env(paths: dict[str, Path], config_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    pythonpath_parts = [str(ROOT), str(ROOT / "src")]
    if env.get("PYTHONPATH"):
        pythonpath_parts.append(env["PYTHONPATH"])
    env.update(
        {
            "GREENRAN_PROJECT_DIR": str(ROOT),
            "GREENRAN_STATE_DIR": str(paths["state_dir"]),
            "GREENRAN_DB_PATH": str(paths["db_path"]),
            "GREENRAN_FIXED_SCENARIO_CONFIG": str(config_path),
            "PYTHONPATH": ":".join(pythonpath_parts),
        }
    )
    return env


def apply_runtime_overrides(env: dict[str, str], args: argparse.Namespace) -> dict[str, str]:
    mode = str(getattr(args, "armd_mode", "") or "").strip()
    if mode:
        env["GREENRAN_ARMD_MODE"] = mode
    env["GREENRAN_PDCP_STALE_SECONDS"] = str(float(getattr(args, "pdcp_stale_seconds", 3600.0) or 3600.0))
    return env


def build_ns3_command(ns3_bin: Path, args: argparse.Namespace) -> list[str]:
    return [
        str(ns3_bin),
        "--e2TermIp=127.0.0.1",
        f"--simTime={int(args.sim_time)}",
        f"--ranPressureProfile={args.ran_pressure_profile}",
        "--enableTraces=1",
        f"--ueCount={int(args.ue_count)}",
        f"--cameraUeCount={int(args.camera_ue_count)}",
        f"--vehicleUeCount={int(args.vehicle_ue_count)}",
        f"--mmWaveEnbNodes={int(args.mmwave_enb_nodes)}",
        f"--ueSpeedMin={float(args.ue_speed_min)}",
        f"--ueSpeedMax={float(args.ue_speed_max)}",
        "--enableTracesAfterAttach=0",
        "--useMcUeDevices=true",
        "--enableE2FileLogging=true",
        "--e2lteEnabled=false",
        "--e2nrEnabled=true",
        "--e2du=false",
        "--e2cuUp=true",
        "--e2cuCp=false",
    ]


def _newer_than(path: Path, started_at: float) -> bool:
    try:
        return path.stat().st_mtime >= started_at - 2.0
    except OSError:
        return False


def hydrate_missing_traces(trace_dir: Path, started_at: float) -> None:
    candidates = [
        trace_dir,
        ROOT / "ns-O-RAN-flexric" / "mmwave-LENA-oran",
        ROOT / "runs" / "tasam_article_ns3_collection" / "ns3_traces",
    ]
    expected = (
        "DlPdcpStats.txt",
        "DlRlcStats.txt",
        "UlPdcpStats.txt",
        "UlRlcStats.txt",
        "DlE2PdcpStats.txt",
        "DlE2RlcStats.txt",
        "UlE2PdcpStats.txt",
        "UlE2RlcStats.txt",
    )
    for name in expected:
        target = trace_dir / name
        if target.exists():
            continue
        for candidate_dir in candidates:
            source = candidate_dir / name
            if source.exists() and _newer_than(source, started_at):
                shutil.copy2(source, target)
                break


def _read_last_trace_window_end(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return 0.0
    for line in reversed(lines):
        text = line.strip()
        if not text or text.startswith("%"):
            continue
        parts = text.split()
        if len(parts) < 2:
            continue
        try:
            return float(parts[1])
        except (TypeError, ValueError):
            continue
    return 0.0


def read_round_progress_signature(paths: dict[str, Path]) -> tuple[float, float, int, int, int]:
    metrics_path = paths["state_dir"] / "xapp_metrics" / "extended_metrics.json"
    sim_time_s = 0.0
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        sim_time_s = float(
            payload.get("sim_time_s")
            or ((payload.get("sim_time_range") or {}).get("end"))
            or 0.0
        )
    except Exception:
        sim_time_s = 0.0

    trace_dir = paths["trace_dir"]
    pdcp_window_end_s = _read_last_trace_window_end(trace_dir / "DlPdcpStats.txt")
    pdcp_bytes = int((trace_dir / "DlPdcpStats.txt").stat().st_size) if (trace_dir / "DlPdcpStats.txt").exists() else 0
    rlc_bytes = int((trace_dir / "DlRlcStats.txt").stat().st_size) if (trace_dir / "DlRlcStats.txt").exists() else 0
    e2_bytes = int((trace_dir / "DlE2PdcpStats.txt").stat().st_size) if (trace_dir / "DlE2PdcpStats.txt").exists() else 0
    return (sim_time_s, pdcp_window_end_s, pdcp_bytes, rlc_bytes, e2_bytes)


def progress_signature_advanced(
    current: tuple[float, float, int, int, int],
    previous: tuple[float, float, int, int, int],
) -> bool:
    current_sim, current_pdcp_end, current_pdcp_bytes, current_rlc_bytes, current_e2_bytes = current
    previous_sim, previous_pdcp_end, previous_pdcp_bytes, previous_rlc_bytes, previous_e2_bytes = previous
    if current_sim > previous_sim:
        return True
    if current_pdcp_end > previous_pdcp_end:
        return True
    return (
        (previous_pdcp_bytes == 0 and current_pdcp_bytes > 0)
        or (previous_rlc_bytes == 0 and current_rlc_bytes > 0)
        or (previous_e2_bytes == 0 and current_e2_bytes > 0)
    )


def sqlite_counts(db_path: Path) -> dict[str, int]:
    counts = {"extended_metrics": 0, "decisions_history": 0}
    if not db_path.exists():
        return counts
    conn = sqlite3.connect(str(db_path))
    try:
        for table in counts:
            try:
                counts[table] = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
            except sqlite3.DatabaseError:
                counts[table] = 0
    finally:
        conn.close()
    return counts


def wait_for_round_settle(db_path: Path, settle_seconds: float, dry_run: bool) -> dict[str, int]:
    if dry_run:
        return {"extended_metrics": 0, "decisions_history": 0}
    deadline = time.time() + settle_seconds
    latest = sqlite_counts(db_path)
    while time.time() < deadline:
        time.sleep(0.5)
        current = sqlite_counts(db_path)
        if current != latest:
            latest = current
            deadline = time.time() + settle_seconds
    return latest


def export_round(paths: dict[str, Path], args: argparse.Namespace, dry_run: bool) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "export_tasam_article_dataset.py"),
        "--db",
        str(paths["db_path"]),
        "--output-jsonl",
        str(paths["trace_jsonl"]),
        "--summary-json",
        str(paths["summary_json"]),
    ]
    if int(args.export_limit) > 0:
        cmd.extend(["--limit", str(int(args.export_limit))])
    if args.allow_proxy:
        cmd.append("--allow-proxy")
    run(cmd, cwd=ROOT, dry_run=dry_run)
    if dry_run or not paths["summary_json"].exists():
        return {}
    return json.loads(paths["summary_json"].read_text(encoding="utf-8"))


def build_collection_event_command(paths: dict[str, Path], args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts" / "collection_event_alternator.py"),
        "--profile",
        str(args.collection_event_profile),
        "--cycles",
        "0",
        "--state-file",
        str(paths["state_dir"] / "article00_scenario_control.json"),
        "--time-source",
        str(args.collection_event_time_source),
    ]


def merge_round_traces(output_root: Path, rounds: list[dict[str, Any]]) -> dict[str, Any]:
    merged_trace = output_root / DEFAULT_TRACE_NAME
    merged_summary = output_root / "offline_collection_summary.json"
    total_lines = 0
    successful_rounds = 0
    decision_counts_total: dict[str, int] = {}
    stage_counts_total: dict[str, int] = {}
    collection_profiles: dict[str, int] = {}
    with merged_trace.open("w", encoding="utf-8") as out_handle:
        for round_payload in rounds:
            trace_path = Path(round_payload["trace_jsonl"])
            if not trace_path.exists():
                continue
            text = trace_path.read_text(encoding="utf-8")
            if not text.strip():
                continue
            out_handle.write(text if text.endswith("\n") else text + "\n")
            total_lines += sum(1 for line in text.splitlines() if line.strip())
            successful_rounds += 1
            for key, value in (round_payload.get("decision_counts") or {}).items():
                decision_counts_total[str(key)] = decision_counts_total.get(str(key), 0) + int(value or 0)
            for key, value in (round_payload.get("stage_counts") or {}).items():
                stage_counts_total[str(key)] = stage_counts_total.get(str(key), 0) + int(value or 0)
            profile = str(round_payload.get("collection_event_profile") or "").strip()
            if profile:
                profile_transitions = int(round_payload.get("written_transitions", 0) or 0)
                if profile_transitions <= 0:
                    profile_transitions = sum(1 for line in text.splitlines() if line.strip())
                collection_profiles[profile] = collection_profiles.get(profile, 0) + profile_transitions

    payload = {
        "schema": "greenran.tasam_article_offline_collection.v1",
        "output_root": str(output_root),
        "merged_trace_jsonl": str(merged_trace),
        "round_count": len(rounds),
        "successful_rounds": successful_rounds,
        "written_transitions_total": total_lines,
        "decision_counts_total": decision_counts_total,
        "stage_counts_total": stage_counts_total,
        "collection_event_profiles": collection_profiles,
        "rounds": rounds,
    }
    merged_summary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def collect_round(round_index: int, args: argparse.Namespace, base_config: dict[str, Any], ns3_bin: Path) -> dict[str, Any]:
    round_dir = Path(args.output_root) / f"round_{round_index:04d}"
    paths = prepare_round_dirs(round_dir)
    round_config = build_round_config(base_config, args, round_index)
    write_round_config(round_config, paths["config_path"])

    gen_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "generate_article_ns3_device_roles.py"),
        "--config",
        str(paths["config_path"]),
        "--state-dir",
        str(paths["state_dir"]),
    ]
    run(gen_cmd, cwd=ROOT, dry_run=args.dry_run)

    env = apply_runtime_overrides(build_runtime_env(paths, paths["config_path"]), args)
    collector_cmd = [
        sys.executable,
        str(ROOT / "src" / "csv_to_metrics.py"),
        "--input-dir",
        str(paths["trace_dir"]),
        "--output",
        str(paths["state_dir"] / "xapp_metrics" / "metrics.json"),
        "--extended-output",
        str(paths["state_dir"] / "xapp_metrics" / "extended_metrics.json"),
        "--poll-interval",
        str(float(args.collector_poll_interval)),
    ]
    rapp_cmd = [
        sys.executable,
        str(ROOT / "src" / "rapp_orchestrator.py"),
        "--synthetic",
        "0",
        "--interval",
        str(float(args.orchestrator_interval)),
    ]
    ns3_cmd = build_ns3_command(ns3_bin, args)

    started_at = time.time()
    collector_proc = start_process(collector_cmd, cwd=ROOT, log_path=paths["collector_log"], env=env, dry_run=args.dry_run)
    rapp_proc = start_process(rapp_cmd, cwd=ROOT, log_path=paths["rapp_log"], env=env, dry_run=args.dry_run)
    alternator_proc = None
    if str(getattr(args, "collection_event_profile", "") or "").strip():
        alternator_proc = start_process(
            build_collection_event_command(paths, args),
            cwd=ROOT,
            log_path=paths["alternator_log"],
            env=env,
            dry_run=args.dry_run,
        )
    ns3_proc = start_process(ns3_cmd, cwd=paths["trace_dir"], log_path=paths["ns3_log"], env=env, dry_run=args.dry_run)
    ns3_exit = 0
    try:
        if ns3_proc is not None:
            stall_timeout = float(getattr(args, "stall_timeout_seconds", 0.0) or 0.0)
            max_round_wall = float(getattr(args, "max_round_wall_seconds", 0.0) or 0.0)
            progress_signature = read_round_progress_signature(paths)
            last_progress_at = time.time()
            while True:
                polled = ns3_proc.poll()
                if polled is not None:
                    ns3_exit = polled
                    break
                time.sleep(1.0)
                if max_round_wall > 0 and (time.time() - started_at) >= max_round_wall:
                    print(
                        f"[offline] {round_dir.name}: round exceeded wall timeout "
                        f"elapsed={time.time() - started_at:.1f}s limit={max_round_wall:.1f}s; terminating ns-3",
                        flush=True,
                    )
                    terminate_process(ns3_proc, timeout=5.0)
                    ns3_exit = ns3_proc.poll()
                    if ns3_exit is None:
                        ns3_exit = 1
                    break
                current_signature = read_round_progress_signature(paths)
                if progress_signature_advanced(current_signature, progress_signature):
                    progress_signature = current_signature
                    last_progress_at = time.time()
                    continue
                if stall_timeout > 0 and (time.time() - last_progress_at) >= stall_timeout:
                    print(
                        f"[offline] {round_dir.name}: detected stalled round "
                        f"sim_time={current_signature[0]:.1f}s pdcp_end={current_signature[1]:.1f}s "
                        f"pdcp_bytes={current_signature[2]} rlc_bytes={current_signature[3]} "
                        f"e2_bytes={current_signature[4]} "
                        f"stalled_for={time.time() - last_progress_at:.1f}s; terminating ns-3",
                        flush=True,
                    )
                    terminate_process(ns3_proc, timeout=5.0)
                    ns3_exit = ns3_proc.poll()
                    if ns3_exit is None:
                        ns3_exit = 1
                    break
    finally:
        hydrate_missing_traces(paths["trace_dir"], started_at)
        print(f"[offline] {round_dir.name}: stopping alternator", flush=True)
        terminate_process(alternator_proc, timeout=3.0)
        if not args.dry_run and float(getattr(args, "post_ns3_grace_seconds", 0.0) or 0.0) > 0:
            grace = float(args.post_ns3_grace_seconds)
            print(f"[offline] {round_dir.name}: post-ns3 grace {grace:.1f}s for final trace ingestion", flush=True)
            time.sleep(grace)
        print(f"[offline] {round_dir.name}: stopping collector", flush=True)
        terminate_process(collector_proc, timeout=3.0)
        print(f"[offline] {round_dir.name}: stopping rapp", flush=True)
        terminate_process(rapp_proc, timeout=3.0)
        print(f"[offline] {round_dir.name}: cleaning round xapps", flush=True)
        cleanup_round_xapps(env, args.dry_run)
        print(f"[offline] {round_dir.name}: waiting for db settle", flush=True)
        settle_counts = wait_for_round_settle(paths["db_path"], float(args.settle_seconds), args.dry_run)

    print(f"[offline] {round_dir.name}: exporting dataset", flush=True)
    export_summary = export_round(paths, args, args.dry_run)
    collection_event_profile = str(getattr(args, "collection_event_profile", "") or "")
    if export_summary and collection_event_profile:
        export_summary["collection_event_profile"] = collection_event_profile
        paths["summary_json"].write_text(
            json.dumps(export_summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return {
        "round_id": f"round_{round_index:04d}",
        "round_dir": str(round_dir),
        "state_dir": str(paths["state_dir"]),
        "trace_jsonl": str(paths["trace_jsonl"]),
        "summary_json": str(paths["summary_json"]),
        "ns3_exit_code": ns3_exit,
        "db_counts": settle_counts,
        "written_transitions": int(export_summary.get("written_transitions", 0) or 0),
        "candidate_snapshots": int(export_summary.get("candidate_snapshots", 0) or 0),
        "decision_counts": export_summary.get("decision_counts") or {},
        "stage_counts": export_summary.get("stage_counts") or {},
        "collection_event_profile": collection_event_profile,
    }


def main() -> int:
    args = build_parser().parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    ns3_bin = resolve_ns3_bin(args.ns3_bin)
    base_config = load_base_config(Path(args.base_config))

    rounds: list[dict[str, Any]] = []
    for round_index in range(1, int(args.rounds) + 1):
        rounds.append(collect_round(round_index, args, base_config, ns3_bin))

    payload = merge_round_traces(output_root, rounds)
    print(json.dumps(payload, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
