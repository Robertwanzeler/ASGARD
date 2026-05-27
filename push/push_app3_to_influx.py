#!/usr/bin/env python3
"""
GreenRAN - Push App3 Monitoring to InfluxDB
===========================================

Lê o snapshot de monitoramento da App3-Veicular e envia métricas
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
APP3_MONITORING_FILE = STATE_DIR / "app3_veicular" / "monitoring_snapshot.json"


def tag_value(value: object) -> str:
    return str(value).replace(" ", "_").replace(",", "_").replace("=", "_")


def load_snapshot() -> dict:
    if not APP3_MONITORING_FILE.exists():
        return {}
    try:
        with open(APP3_MONITORING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def build_lines(snapshot: dict) -> list[str]:
    if not snapshot:
        return []

    ts = int(time.time() * 1e9)
    vehicles = snapshot.get("vehicles", {}) or {}
    network = snapshot.get("network", {}) or {}
    sla = snapshot.get("vehicle_sla", snapshot.get("sla", {})) or {}
    simulation = snapshot.get("simulation", {}) or {}

    source = tag_value(simulation.get("source", "unknown"))
    runtime_status = tag_value(sla.get("runtime_status", "unknown"))
    proposal_status = tag_value(sla.get("proposal_status", "unknown"))

    line = (
        f"app3_summary,app=app3_veicular,source={source},runtime_status={runtime_status},proposal_status={proposal_status} "
        f"total_vehicles={int(vehicles.get('total_vehicles', 0))}i,"
        f"ego_present={1 if vehicles.get('ego_present', False) else 0}i,"
        f"high_risk_vehicles={int(vehicles.get('high_risk_vehicles', 0))}i,"
        f"medium_risk_vehicles={int(vehicles.get('medium_risk_vehicles', 0))}i,"
        f"degraded_autonomy_vehicles={int(vehicles.get('degraded_autonomy_vehicles', 0))}i,"
        f"max_latency_ms={float(network.get('max_latency_ms', vehicles.get('max_latency_ms', 0.0)) or 0.0):.3f},"
        f"max_packet_loss_percent={float(network.get('max_packet_loss_percent', vehicles.get('max_packet_loss_percent', 0.0)) or 0.0):.3f},"
        f"max_speed_mps={float(network.get('max_speed_mps', vehicles.get('max_speed_mps', 0.0)) or 0.0):.3f},"
        f"events_count={int(snapshot.get('events_count', 0))}i "
        f"{ts}"
    )
    return [line]


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
        print(f"[App3Influx] Erro ao enviar: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Push App3-Veicular para InfluxDB")
    parser.add_argument("--host", default=DEFAULT_INFLUX_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_INFLUX_PORT)
    parser.add_argument("--db", default=DEFAULT_INFLUX_DB)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    args = parser.parse_args()

    print(f"[App3Influx] Monitorando {as_str(APP3_MONITORING_FILE)}")
    last_timestamp = None

    while True:
        snapshot = load_snapshot()
        snapshot_ts = ((snapshot.get("simulation", {}) or {}).get("timestamp_iso")) if snapshot else None
        if snapshot and snapshot_ts != last_timestamp:
            lines = build_lines(snapshot)
            if push_lines(lines, args.host, args.port, args.db):
                vehicles = snapshot.get("vehicles", {}) or {}
                print(
                    "[App3Influx] Snapshot enviado "
                    f"(vehicles={vehicles.get('total_vehicles', 0)}, "
                    f"high_risk={vehicles.get('high_risk_vehicles', 0)}, "
                    f"latency={vehicles.get('max_latency_ms', 0.0)}ms)"
                )
                last_timestamp = snapshot_ts
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
