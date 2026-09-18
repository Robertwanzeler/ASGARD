#!/usr/bin/env python3
"""Read-only monitor for a paired rApp-only GreenRAN run."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any


STAGES = (
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


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def pid_alive(path: Path) -> bool:
    try:
        os.kill(int(path.read_text(encoding="utf-8").strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def fmt(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/d"


def snapshot(run_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "decisions": 0,
        "metrics": 0,
        "stages": {stage: 0 for stage in STAGES},
        "flags": [],
        "real_metrics": 0,
        "proxy_metrics": 0,
        "latest": {},
        "energy_j": 0.0,
        "duration_s": 0.0,
        "average_power_w": 0.0,
        "db_mib": 0.0,
    }
    db = run_dir / "rapp_data_lake.db"
    if not db.is_file():
        return result
    result["db_mib"] = db.stat().st_size / (1024 * 1024)
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            result["decisions"] = int(conn.execute("select count(*) from decisions_history").fetchone()[0] or 0)
            result["metrics"] = int(conn.execute("select count(*) from extended_metrics").fetchone()[0] or 0)
            rows = conn.execute(
                "select collection_event_stage_name, count(*) from decisions_history group by collection_event_stage_name"
            ).fetchall()
            for stage, count in rows:
                if stage in result["stages"]:
                    result["stages"][stage] = int(count or 0)
            cols = {row[1] for row in conn.execute("pragma table_info(extended_metrics)").fetchall()}
            if "collector_mode" in cols:
                real, proxy = conn.execute(
                    "select coalesce(sum(case when collector_mode='pdcp_real' then 1 else 0 end),0), "
                    "coalesce(sum(case when coalesce(proxy_latency_sample_count,0)>0 then 1 else 0 end),0) "
                    "from extended_metrics"
                ).fetchone()
                result["real_metrics"] = int(real or 0)
                result["proxy_metrics"] = int(proxy or 0)
            fields = [
                "timestamp", "sim_time_s", "collector_mode", "throughput_kbps",
                "global_packet_loss_rate", "latency_p95_us", "cvar_per_ue_us",
            ]
            available = [field for field in fields if field in cols]
            if available:
                row = conn.execute(
                    f"select {', '.join(available)} from extended_metrics order by timestamp desc limit 1"
                ).fetchone()
                result["latest"] = dict(zip(available, row or ()))
            try:
                t0, t1 = conn.execute("select min(timestamp), max(timestamp) from decisions_history").fetchone()
            except sqlite3.OperationalError:
                t0, t1 = None, None
            if t0 is not None and t1 is not None:
                result["duration_s"] = max(0.0, float(t1 - t0))
            try:
                commands = conn.execute(
                    "select timestamp, power_w from energy_commands order by timestamp, id"
                ).fetchall()
                energy = 0.0
                if commands and t0 is not None and t1 is not None:
                    for index, (timestamp, power_w) in enumerate(commands):
                        if timestamp >= t1:
                            continue
                        next_timestamp = commands[index + 1][0] if index + 1 < len(commands) else t1
                        seconds = max(0.0, min(float(next_timestamp), float(t1)) - max(float(timestamp), float(t0)))
                        energy += float(power_w or 0.0) * seconds
                result["energy_j"] = energy
                if result["duration_s"]:
                    result["average_power_w"] = energy / result["duration_s"]
            except sqlite3.OperationalError:
                pass
            try:
                result["flags"] = conn.execute(
                    "select tasam_enabled, armd_enabled, count(*) "
                    "from decisions_history group by tasam_enabled, armd_enabled"
                ).fetchall()
            except sqlite3.OperationalError:
                result["flags"] = []
    except sqlite3.Error as exc:
        result["db_error"] = str(exc)
    return result


def render(args: argparse.Namespace) -> None:
    run_dir = args.run_dir.resolve()
    manifest = read_json(run_dir / "arm_manifest.json")
    wall = read_json(run_dir / "wall_clock_status.json")
    online = read_json(run_dir / "online_status.json")
    data = snapshot(run_dir)
    disk = shutil.disk_usage(run_dir.parent if run_dir.parent.exists() else run_dir)
    alive = {
        name: pid_alive(run_dir / name)
        for name in ("collection_launcher.pid", "wall_clock_supervisor.pid", "rapp.pid", "ns3_supervisor.pid")
        if (run_dir / name).exists()
    }
    if not args.no_color:
        print("\033[2J\033[H", end="")
    print("=== GreenRAN | rApp-only pareado | seed 47 ===")
    print(f"diretório: {run_dir}")
    print(f"estado: {online.get('status') or wall.get('phase') or manifest.get('status', 'starting')} | PIDs: {alive or 'aguardando'}")
    print(f"perfil: {manifest.get('profile', 'tasam_training_balanced_v3')} | duração alvo: {fmt(manifest.get('wall_time_s', 3600), 0)} s | decorrido: {fmt(data['duration_s'], 1)} s")
    print(f"contrato: ARMD=off | TA-SAM=off | rApp nativa | flags DB={data['flags'] or 'n/d'}")
    print(f"decisões={data['decisions']:,} | métricas={data['metrics']:,} | PDCP real={data['real_metrics']:,} | proxy={data['proxy_metrics']:,}")
    latest = data["latest"]
    print(
        "último: "
        f"sim_time={fmt(latest.get('sim_time_s'), 1)} s | "
        f"throughput={fmt(float(latest.get('throughput_kbps', 0) or 0) / 1000, 2)} Mbps | "
        f"P95={fmt(float(latest.get('latency_p95_us', 0) or 0) / 1000, 2)} ms | "
        f"CVaR={fmt(float(latest.get('cvar_per_ue_us', 0) or 0) / 1000, 2)} ms | "
        f"perda={fmt(float(latest.get('global_packet_loss_rate', 0) or 0) * 100, 4)}%"
    )
    print(f"energia estimada={data['energy_j'] / 1000:.2f} kJ | potência média={fmt(data['average_power_w'], 2)} W | DB={fmt(data['db_mib'], 2)} MiB")
    print("estágios: " + " | ".join(f"{stage}={data['stages'][stage]}" for stage in STAGES))
    print(f"disco livre={disk.free / (1024**3):.2f} GiB")
    if data.get("db_error"):
        print(f"ERRO DB: {data['db_error']}")
    print("Ctrl+C encerra somente este painel.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-color", action="store_true", help="não limpar a tela")
    args = parser.parse_args()
    while True:
        render(args)
        if args.once:
            return 0
        time.sleep(max(2.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
