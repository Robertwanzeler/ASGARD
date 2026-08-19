#!/usr/bin/env python3
"""
GreenRAN - Push App1 Monitoring to InfluxDB
===========================================

Lê o snapshot de monitoramento da App1-Vigilancia e envia métricas
agregadas para InfluxDB, permitindo visualização no Grafana.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from greenran_paths import STATE_DIR, as_str  # noqa: E402
from greenran_runtime import load_runtime_config  # noqa: E402


RUNTIME_CONFIG = load_runtime_config()
DEFAULT_INFLUX_HOST = RUNTIME_CONFIG["monitoring"]["influxdb_host"]
DEFAULT_INFLUX_PORT = int(RUNTIME_CONFIG["monitoring"]["influxdb_port"])
DEFAULT_INFLUX_DB = RUNTIME_CONFIG["monitoring"]["influxdb_db"]
DEFAULT_INTERVAL = int(RUNTIME_CONFIG["monitoring"]["push_interval_seconds"])
APP1_MONITORING_FILE = STATE_DIR / "app1_vigilancia" / "monitoring_snapshot.json"


def load_snapshot() -> dict:
    if not APP1_MONITORING_FILE.exists():
        return {}
    try:
        with open(APP1_MONITORING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def build_lines(snapshot: dict) -> list[str]:
    if not snapshot:
        return []

    ts = int(time.time() * 1e9)
    events = snapshot.get("events", {})
    analyses = snapshot.get("analyses", {})
    videos = snapshot.get("videos", {})
    cameras = snapshot.get("cameras", {})
    network = snapshot.get("network", {})
    policies = snapshot.get("policies", {})
    latest_camera = str(analyses.get("latest_camera_id", "unknown")).replace(" ", "_")
    energy_action = str(policies.get("energy_action", "UNKNOWN")).replace(" ", "_")

    return [
        (
            f"app1_summary,app=app1_vigilancia,camera={latest_camera},energy_action={energy_action} "
            f"events_total={int(events.get('total', 0))},"
            f"events_open={int(events.get('open', 0))},"
            f"events_validated={int(events.get('validated', 0))},"
            f"analyses_total={int(analyses.get('total', 0))},"
            f"analyses_critical={int(analyses.get('critical', 0))},"
            f"visual_risk_score={float(analyses.get('latest_visual_risk_score', 0.0) or 0.0):.3f},"
            f"motion_intensity={float(analyses.get('latest_motion_intensity', 0.0) or 0.0):.3f},"
            f"videos_uploaded={int(videos.get('uploaded', 0))},"
            f"videos_valid={int(videos.get('valid_videos', 0))},"
            f"videos_with_artifacts={int(videos.get('with_artifacts', 0))},"
            f"stored_megabytes={float(videos.get('stored_megabytes', 0.0) or 0.0):.3f},"
            f"cameras_total={int(cameras.get('total', 0))},"
            f"cameras_enabled={int(cameras.get('enabled', 0))},"
            f"cameras_bound={int(cameras.get('bound_sources', 0))},"
            f"cameras_evidence_ready={int(cameras.get('evidence_ready', 0))},"
            f"cameras_critical_active={int(cameras.get('critical_active', 0))},"
            f"avg_latency_ms={float(network.get('avg_latency_ms', 0.0) or 0.0):.3f},"
            f"active_cameras={int(network.get('active_cameras', 0) or 0)} "
            f"{ts}"
        )
    ]


def push_lines(lines: list[str], host: str, port: int, db: str) -> bool:
    if not lines:
        return True
    url = f"http://{host}:{port}/write?db={db}"
    try:
        response = requests.post(
            url,
            data="\n".join(lines),
            headers={"Content-Type": "application/octet-stream"},
            timeout=5,
        )
        return response.status_code == 204
    except Exception as exc:
        print(f"[App1Influx] Erro ao enviar: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Push App1-Vigilancia para InfluxDB")
    parser.add_argument("--host", default=DEFAULT_INFLUX_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_INFLUX_PORT)
    parser.add_argument("--db", default=DEFAULT_INFLUX_DB)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    args = parser.parse_args()

    print(f"[App1Influx] Monitorando {as_str(APP1_MONITORING_FILE)}")
    last_updated_at = None

    while True:
        snapshot = load_snapshot()
        updated_at = snapshot.get("updated_at") if snapshot else None
        if snapshot and updated_at != last_updated_at:
            lines = build_lines(snapshot)
            if push_lines(lines, args.host, args.port, args.db):
                print(f"[App1Influx] Snapshot enviado ({updated_at})")
                last_updated_at = updated_at
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
