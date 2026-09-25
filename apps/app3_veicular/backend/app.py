#!/usr/bin/env python3
"""
App3-Veicular.

- API de estado veicular
- resumo operacional do ego vehicle
- integracao com o contexto combinado CARLA + ns-3
"""

from __future__ import annotations

import sys
import os
import threading
import time
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
from services import GreenRANContextReader, VehicleStateStore  # noqa: E402


RUNTIME_CONFIG = load_runtime_config()
APP_HOST = os.environ.get("GREENRAN_APP3_HOST", "127.0.0.1")
APP_PORT = 5300

app = Flask(__name__, template_folder=str(APP_DIR / "templates"))

STORE = VehicleStateStore(STATE_DIR)
CONTEXT = GreenRANContextReader(STATE_DIR)
_REFRESH_STOP = threading.Event()


def _background_snapshot_refresh(interval_seconds: float = 1.0) -> None:
    while not _REFRESH_STOP.is_set():
        try:
            STORE.refresh_snapshot()
        except Exception:
            pass
        _REFRESH_STOP.wait(interval_seconds)


@app.route("/")
def index():
    snapshot = STORE.refresh_snapshot()
    vehicles = STORE.list_vehicles()
    ego = STORE.get_ego_vehicle()
    events = STORE.list_events()
    return render_template(
        "index.html",
        snapshot=snapshot,
        vehicles=vehicles,
        ego=ego,
        events=events[:20],
        state_dir=str(STATE_DIR),
    )


@app.route("/api/health")
def health():
    snapshot = STORE.refresh_snapshot()
    summary = snapshot.get("vehicles", {}) if isinstance(snapshot, dict) else {}
    return jsonify(
        {
            "status": "ok",
            "app": "app3_veicular",
            "state_dir": str(STATE_DIR),
            "vehicles": int(summary.get("total_vehicles", 0) or 0),
            "ego_present": bool(summary.get("ego_present", False)),
        }
    )


@app.route("/api/network-context")
def network_context():
    return jsonify(CONTEXT.get_context())


@app.route("/api/monitoring")
def monitoring_snapshot():
    return jsonify(STORE.refresh_snapshot())


@app.route("/api/vehicles")
def list_vehicles():
    STORE.refresh_snapshot()
    return jsonify({"vehicles": STORE.list_vehicles()})


@app.route("/api/vehicles/ego")
def ego_vehicle():
    STORE.refresh_snapshot()
    ego = STORE.get_ego_vehicle()
    if ego is None:
        return jsonify({"vehicle": None, "message": "ego vehicle indisponível"}), 404
    return jsonify({"vehicle": ego})


@app.route("/api/vehicles/summary")
def vehicles_summary():
    snapshot = STORE.refresh_snapshot()
    summary = dict(snapshot.get("vehicles", {}))
    summary["vehicle_sla"] = snapshot.get("sla", {})
    summary["network"] = snapshot.get("network", {})
    return jsonify(summary)


@app.route("/api/vehicles/events")
def vehicle_events():
    limit = request.args.get("limit", default=50, type=int)
    events = STORE.build_events(STORE.list_vehicles())
    return jsonify({"events": events[: max(1, limit)]})


def main():
    import argparse

    parser = argparse.ArgumentParser(description="App3-Veicular MVP")
    parser.add_argument("--host", default=APP_HOST, help="Host para bind")
    parser.add_argument("--port", type=int, default=APP_PORT, help="Porta do App3")
    parser.add_argument("--debug", action="store_true", help="Modo debug")
    args = parser.parse_args()

    print("=" * 60)
    print("App3-Veicular MVP")
    print("=" * 60)
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")
    print(f"GreenRAN State Dir: {STATE_DIR}")
    print(f"Dashboard GreenRAN: http://localhost:{RUNTIME_CONFIG['dashboard']['port']}")
    print("=" * 60)

    refresher = threading.Thread(
        target=_background_snapshot_refresh,
        kwargs={"interval_seconds": 1.0},
        daemon=True,
        name="app3-snapshot-refresh",
    )
    refresher.start()

    try:
        app.run(host=args.host, port=args.port, debug=args.debug)
    finally:
        _REFRESH_STOP.set()


if __name__ == "__main__":
    main()
