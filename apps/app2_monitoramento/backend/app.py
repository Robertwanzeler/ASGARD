#!/usr/bin/env python3
"""
MVP da App2-Monitoramento.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request


CURRENT_DIR = Path(__file__).resolve().parent
APP_DIR = CURRENT_DIR.parent
PROJECT_ROOT = APP_DIR.parent.parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from greenran_paths import STATE_DIR  # noqa: E402
from greenran_runtime import load_runtime_config  # noqa: E402
from rapp_data_lake import DataLake  # noqa: E402
from services import (  # noqa: E402
    GreenRANContextReader,
    SensorEventStore,
    evaluate_reading,
    evaluate_app2_sla,
    summarize,
)


RUNTIME_CONFIG = load_runtime_config()
APP_HOST = "0.0.0.0"
APP_PORT = 5200

app = Flask(__name__, template_folder=str(APP_DIR / "templates"))

STORE = SensorEventStore(STATE_DIR)
CONTEXT = GreenRANContextReader(STATE_DIR)
DATA_LAKE = DataLake()
APP2_STATE_DIR = STATE_DIR / "app2_monitoramento"


def _safe_read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _load_monitoring_snapshot():
    snapshot = _safe_read_json(APP2_STATE_DIR / "monitoring_snapshot.json", {})
    if not isinstance(snapshot, dict):
        snapshot = {}
    snapshot["app2_sla"] = evaluate_app2_sla(snapshot)
    snapshot["sensor_source"] = {
        "configured": os.environ.get("GREENRAN_APP2_SENSOR_SOURCE", "ns3"),
        "effective": os.environ.get("GREENRAN_APP2_SENSOR_SOURCE_EFFECTIVE", os.environ.get("GREENRAN_APP2_SENSOR_SOURCE", "ns3")),
        "real_ns3_detected": os.environ.get("GREENRAN_APP2_REAL_SENSOR_UES_DETECTED", "0") == "1",
    }
    return snapshot


@app.route("/")
def index():
    manual_readings = STORE.list_readings(limit=10)
    manual_alerts = STORE.list_alerts(limit=10)
    snapshot = _load_monitoring_snapshot()
    sensors = _safe_read_json(APP2_STATE_DIR / "sensors" / "latest.json", [])
    summary = summarize(manual_readings, manual_alerts, CONTEXT.get_context())
    app2_report = DATA_LAKE.get_app2_report(hours=24)
    app2_history = DATA_LAKE.get_recent_app2_snapshots(hours=24, limit=24)
    return render_template(
        "index.html",
        readings=manual_readings,
        alerts=manual_alerts,
        summary=summary,
        snapshot=snapshot,
        sensors=sensors,
        app2_report=app2_report,
        app2_history=app2_history,
        state_dir=str(STATE_DIR),
    )


@app.route("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "app": "app2_monitoramento",
            "state_dir": str(STATE_DIR),
            "readings": len(STORE.list_readings()),
            "alerts": len(STORE.list_alerts()),
            "sensor_source": {
                "configured": os.environ.get("GREENRAN_APP2_SENSOR_SOURCE", "ns3"),
                "effective": os.environ.get("GREENRAN_APP2_SENSOR_SOURCE_EFFECTIVE", os.environ.get("GREENRAN_APP2_SENSOR_SOURCE", "ns3")),
                "real_ns3_detected": os.environ.get("GREENRAN_APP2_REAL_SENSOR_UES_DETECTED", "0") == "1",
            },
        }
    )


@app.route("/api/network-context")
def network_context():
    return jsonify(CONTEXT.get_context())


@app.route("/api/monitoring")
def monitoring_snapshot():
    return jsonify(_load_monitoring_snapshot())


@app.route("/api/sla")
def monitoring_sla():
    snapshot = _load_monitoring_snapshot()
    return jsonify(snapshot.get("app2_sla", {}))


@app.route("/api/sensors/latest")
def latest_sensors():
    sensors = _safe_read_json(APP2_STATE_DIR / "sensors" / "latest.json", [])
    return jsonify({"sensors": sensors})


@app.route("/api/history")
def app2_history():
    hours = request.args.get("hours", default=24, type=int)
    limit = request.args.get("limit", default=120, type=int)
    return jsonify({"history": DATA_LAKE.get_recent_app2_snapshots(hours=max(1, hours), limit=max(1, limit))})


@app.route("/api/report")
def app2_report():
    hours = request.args.get("hours", default=24, type=int)
    return jsonify(DATA_LAKE.get_app2_report(hours=max(1, hours)))


@app.route("/api/readings")
def list_readings():
    limit = request.args.get("limit", type=int)
    return jsonify({"readings": STORE.list_readings(limit=limit)})


@app.route("/api/alerts")
def list_alerts():
    limit = request.args.get("limit", type=int)
    return jsonify({"alerts": STORE.list_alerts(limit=limit)})


@app.route("/api/readings/mock", methods=["POST"])
def create_mock_reading():
    payload = request.get_json(silent=True) or {}
    reading = STORE.add_reading(payload)
    alert = None
    evaluation = evaluate_reading(float(reading.get("value", 0)), reading.get("type", "unknown"))
    if evaluation.get("level") in {"warning", "critical"}:
        alert = STORE.add_alert(
            {
                "level": evaluation["level"],
                "message": evaluation["message"],
                "sensor_id": reading.get("sensor_id"),
                "sensor_type": reading.get("type"),
                "value": reading.get("value"),
            }
        )
    return jsonify({"reading": reading, "alert": alert}), 201


def main():
    import argparse

    parser = argparse.ArgumentParser(description="App2-Monitoramento MVP")
    parser.add_argument("--host", default=APP_HOST, help="Host para bind")
    parser.add_argument("--port", type=int, default=APP_PORT, help="Porta da App2")
    parser.add_argument("--debug", action="store_true", help="Modo debug")
    args = parser.parse_args()

    print("=" * 60)
    print("App2-Monitoramento MVP")
    print("=" * 60)
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")
    print(f"GreenRAN State Dir: {STATE_DIR}")
    print(f"Dashboard GreenRAN: http://localhost:{RUNTIME_CONFIG['dashboard']['port']}")
    print("=" * 60)

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
