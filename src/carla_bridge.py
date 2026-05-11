#!/usr/bin/env python3
"""
Bridge minimo CARLA -> GreenRAN.

Exporta snapshots periodicos dos veiculos em JSON para integracao
com o pipeline do csv_to_metrics.py e o rApp.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from pathlib import Path

from greenran_paths import ARTICLE00_SCENARIO_CONTROL_PATH, CARLA_VEHICLES_PATH, ensure_runtime_dirs


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    tmp_path.replace(path)


def _safe_read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _load_vehicle_override() -> dict:
    control = _safe_read_json(ARTICLE00_SCENARIO_CONTROL_PATH, {})
    override = control.get("vehicle_override", {}) or {}
    if not override.get("enabled", False):
        return {}
    return override


def _apply_vehicle_override(vehicles: list[dict], override: dict) -> list[dict]:
    if not override:
        return vehicles

    total_vehicles = int(override.get("total_vehicles", len(vehicles)) or len(vehicles))
    total_vehicles = max(1, min(total_vehicles, len(vehicles)))
    selected = [dict(vehicle) for vehicle in vehicles[:total_vehicles]]

    high_risk_remaining = max(0, int(override.get("high_risk_vehicles", 0) or 0))
    medium_risk_remaining = max(0, int(override.get("medium_risk_vehicles", 0) or 0))
    degraded_remaining = max(0, int(override.get("degraded_autonomy_vehicles", 0) or 0))
    ego_latency_ms = float(override.get("ego_latency_ms", 18.0) or 18.0)
    traffic_latency_ms = float(override.get("traffic_latency_ms", 14.0) or 14.0)
    ego_packet_loss = float(override.get("ego_packet_loss_percent", 0.2) or 0.2)
    traffic_packet_loss = float(override.get("traffic_packet_loss_percent", 0.1) or 0.1)
    max_speed_mps = float(override.get("max_speed_mps", 7.0) or 7.0)
    scenario_mode = override.get("mode", "vehicle_controlled")

    for index, vehicle in enumerate(selected):
        is_ego = vehicle.get("role") == "ego"
        if high_risk_remaining > 0:
            vehicle["risk_state"] = "high"
            high_risk_remaining -= 1
        elif medium_risk_remaining > 0:
            vehicle["risk_state"] = "medium"
            medium_risk_remaining -= 1
        else:
            vehicle["risk_state"] = "low"

        if degraded_remaining > 0:
            vehicle["autonomy_state"] = "degraded"
            degraded_remaining -= 1
        else:
            vehicle["autonomy_state"] = "normal"

        vehicle["latency_ms"] = round(ego_latency_ms if is_ego else traffic_latency_ms, 3)
        vehicle["packet_loss_percent"] = round(ego_packet_loss if is_ego else traffic_packet_loss, 3)
        vehicle["speed_mps"] = round(max(0.5, max_speed_mps - (index * 0.35)), 3)
        vehicle["scenario_mode"] = scenario_mode

    return selected


def _mock_vehicle_payload() -> dict:
    now = time.time()
    vehicles = [
        {
            "vehicle_id": "veh-01",
            "role": "ego",
            "x": -120.0,
            "y": -40.0,
            "z": 0.0,
            "speed_mps": 6.5,
            "heading_deg": 18.0,
            "lane_id": "lane-west-01",
            "waypoint_id": "wp-west-101",
            "autonomy_state": "normal",
            "risk_state": "low",
            "latency_ms": 18.0,
            "packet_loss_percent": 0.2,
        },
        {
            "vehicle_id": "veh-02",
            "role": "traffic",
            "x": -30.0,
            "y": 95.0,
            "z": 0.0,
            "speed_mps": 4.8,
            "heading_deg": 270.0,
            "lane_id": "lane-north-02",
            "waypoint_id": "wp-north-202",
            "autonomy_state": "normal",
            "risk_state": "low",
            "latency_ms": 14.0,
            "packet_loss_percent": 0.1,
        },
        {
            "vehicle_id": "veh-03",
            "role": "traffic",
            "x": 80.0,
            "y": -110.0,
            "z": 0.0,
            "speed_mps": 5.4,
            "heading_deg": 45.0,
            "lane_id": "lane-east-03",
            "waypoint_id": "wp-east-303",
            "autonomy_state": "normal",
            "risk_state": "low",
            "latency_ms": 13.0,
            "packet_loss_percent": 0.1,
        },
        {
            "vehicle_id": "veh-04",
            "role": "traffic",
            "x": 145.0,
            "y": 120.0,
            "z": 0.0,
            "speed_mps": 4.2,
            "heading_deg": 160.0,
            "lane_id": "lane-east-04",
            "waypoint_id": "wp-east-404",
            "autonomy_state": "normal",
            "risk_state": "low",
            "latency_ms": 13.0,
            "packet_loss_percent": 0.1,
        },
        {
            "vehicle_id": "veh-05",
            "role": "traffic",
            "x": 12.0,
            "y": -165.0,
            "z": 0.0,
            "speed_mps": 6.8,
            "heading_deg": 8.0,
            "lane_id": "lane-south-05",
            "waypoint_id": "wp-south-505",
            "autonomy_state": "normal",
            "risk_state": "low",
            "latency_ms": 15.0,
            "packet_loss_percent": 0.1,
        },
    ]
    vehicles = _apply_vehicle_override(vehicles, _load_vehicle_override())
    return {
        "generated_by": "carla_bridge_mock",
        "mode": "mock",
        "timestamp": now,
        "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "vehicles": vehicles,
    }


class CarlaBridge:
    def __init__(self, host: str, port: int, output: Path, poll_interval: float, mode: str):
        self.host = host
        self.port = port
        self.output = output
        self.poll_interval = poll_interval
        self.mode = mode
        self.running = True

    def _signal_handler(self, signum, frame):
        self.running = False

    def _fetch_live_payload(self) -> dict:
        try:
            import carla  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"CARLA Python API unavailable: {exc}") from exc

        client = carla.Client(self.host, self.port)
        client.set_timeout(2.0)
        world = client.get_world()
        actors = world.get_actors().filter("vehicle.*")
        world_map = world.get_map()
        vehicles = []

        for actor in actors:
            transform = actor.get_transform()
            location = transform.location
            rotation = transform.rotation
            velocity = actor.get_velocity()
            speed_mps = math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2)
            waypoint = None
            lane_id = None
            waypoint_id = None
            try:
                waypoint = world_map.get_waypoint(location, project_to_road=True, lane_type=carla.LaneType.Driving)
            except Exception:
                waypoint = None
            if waypoint is not None:
                lane_id = f"road-{waypoint.road_id}:section-{waypoint.section_id}:lane-{waypoint.lane_id}"
                waypoint_id = f"road-{waypoint.road_id}:section-{waypoint.section_id}:lane-{waypoint.lane_id}:s-{round(waypoint.s, 2)}"

            role_name = actor.attributes.get("role_name", "")
            vehicles.append(
                {
                    "vehicle_id": f"veh-{actor.id}",
                    "role": "ego" if role_name in {"hero", "ego", "ego_vehicle"} else "traffic",
                    "x": round(location.x, 3),
                    "y": round(location.y, 3),
                    "z": round(location.z, 3),
                    "speed_mps": round(speed_mps, 3),
                    "heading_deg": round(rotation.yaw, 3),
                    "lane_id": lane_id,
                    "waypoint_id": waypoint_id,
                    "autonomy_state": "normal",
                    "risk_state": "low",
                    "latency_ms": 0.0,
                    "packet_loss_percent": 0.0,
                }
            )

        vehicles = _apply_vehicle_override(vehicles, _load_vehicle_override())
        now = time.time()
        return {
            "generated_by": "carla_bridge_live",
            "mode": "carla",
            "timestamp": now,
            "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "vehicles": vehicles,
        }

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        ensure_runtime_dirs()
        print(f"[CARLA_BRIDGE] mode={self.mode} output={self.output}")

        while self.running:
            try:
                if self.mode == "mock":
                    payload = _mock_vehicle_payload()
                else:
                    payload = self._fetch_live_payload()
                _write_json_atomic(self.output, payload)
            except Exception as exc:
                print(f"[CARLA_BRIDGE] warning: {exc}")
            time.sleep(self.poll_interval)

        print("[CARLA_BRIDGE] shutdown")


def main() -> None:
    parser = argparse.ArgumentParser(description="CARLA vehicle state bridge for GreenRAN")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--output", default=str(CARLA_VEHICLES_PATH))
    parser.add_argument("--poll-interval", type=float, default=0.2)
    parser.add_argument("--mode", choices=["carla", "mock"], default="carla")
    args = parser.parse_args()

    bridge = CarlaBridge(
        host=args.host,
        port=args.port,
        output=Path(args.output),
        poll_interval=args.poll_interval,
        mode=args.mode,
    )
    bridge.run()


if __name__ == "__main__":
    main()
