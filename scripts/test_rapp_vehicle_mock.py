#!/usr/bin/env python3
"""Valida a politica veicular do rApp sem subir o runtime completo."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rapp_orchestrator import evaluate_vehicle_policy  # noqa: E402


def _make_metrics(**overrides):
    base = {
        "available": True,
        "stale": False,
        "age_seconds": 0.5,
        "total_vehicles": 5,
        "ego_present": True,
        "high_risk_vehicles": 0,
        "medium_risk_vehicles": 0,
        "degraded_autonomy_vehicles": 0,
        "max_latency_ms": 12.0,
        "max_packet_loss_percent": 0.1,
        "max_speed_mps": 9.5,
        "vehicles": [
            {
                "imsi": "16",
                "vehicle_id": "veh-01",
                "vehicle_role": "ego",
                "risk_state": "low",
                "autonomy_state": "normal",
                "latency_ms": 12.0,
                "packet_loss_percent": 0.1,
            }
        ],
    }
    base.update(overrides)
    return base


def main():
    cases = {
        "healthy": _make_metrics(),
        "warning": _make_metrics(
            medium_risk_vehicles=1,
            max_latency_ms=58.0,
            max_packet_loss_percent=2.5,
            vehicles=[
                {
                    "imsi": "16",
                    "vehicle_id": "veh-01",
                    "vehicle_role": "ego",
                    "risk_state": "medium",
                    "autonomy_state": "normal",
                    "latency_ms": 58.0,
                    "packet_loss_percent": 2.5,
                }
            ],
        ),
        "critical": _make_metrics(
            high_risk_vehicles=1,
            degraded_autonomy_vehicles=1,
            max_latency_ms=125.0,
            max_packet_loss_percent=5.8,
            vehicles=[
                {
                    "imsi": "16",
                    "vehicle_id": "veh-01",
                    "vehicle_role": "ego",
                    "risk_state": "high",
                    "autonomy_state": "degraded",
                    "latency_ms": 125.0,
                    "packet_loss_percent": 5.8,
                }
            ],
        ),
    }

    results = {name: evaluate_vehicle_policy(metrics) for name, metrics in cases.items()}
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
