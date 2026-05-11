#!/usr/bin/env python3
"""
Mapeia veiculos do CARLA para IMSIs/UEs do runtime GreenRAN.
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from pathlib import Path

from greenran_paths import CARLA_VEHICLES_PATH, CARLA_VEHICLE_MAP_PATH, ensure_runtime_dirs


def _read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    tmp_path.replace(path)


class CarlaNs3Mapper:
    def __init__(self, input_path: Path, output_path: Path, base_imsi: int, max_vehicles: int, poll_interval: float):
        self.input_path = input_path
        self.output_path = output_path
        self.base_imsi = base_imsi
        self.max_vehicles = max_vehicles
        self.poll_interval = poll_interval
        self.running = True
        self.mapping = {}

    def _signal_handler(self, signum, frame):
        self.running = False

    def _load_previous_mapping(self):
        payload = _read_json(self.output_path, {})
        mappings = payload.get("mappings", []) if isinstance(payload, dict) else []
        for entry in mappings:
            vehicle_id = entry.get("vehicle_id")
            imsi = entry.get("imsi")
            if vehicle_id and imsi:
                self.mapping[str(vehicle_id)] = int(imsi)

    def _build_payload(self, vehicles: list[dict]) -> dict:
        self._load_previous_mapping()
        current_ids = [str(v.get("vehicle_id")) for v in vehicles if v.get("vehicle_id")]

        used_imsis = set(self.mapping.values())
        for vehicle_id in current_ids:
            if vehicle_id in self.mapping:
                continue
            for candidate in range(self.base_imsi, self.base_imsi + self.max_vehicles):
                if candidate not in used_imsis:
                    self.mapping[vehicle_id] = candidate
                    used_imsis.add(candidate)
                    break

        self.mapping = {vehicle_id: imsi for vehicle_id, imsi in self.mapping.items() if vehicle_id in current_ids}

        roles = {}
        mappings = []
        for vehicle in vehicles:
            vehicle_id = str(vehicle.get("vehicle_id"))
            if vehicle_id not in self.mapping:
                continue
            imsi = self.mapping[vehicle_id]
            role = {
                "device_type": "vehicle",
                "label": vehicle_id,
                "vehicle_id": vehicle_id,
                "vehicle_role": vehicle.get("role", "traffic"),
                "mobility_profile": "vehicle",
                "connectivity": "5g_native",
                "gateway_id": "GW-VEH-01",
                "domain": "vehicular",
                "autonomy_state": vehicle.get("autonomy_state", "normal"),
                "risk_state": vehicle.get("risk_state", "low"),
            }
            roles[str(imsi)] = role
            mappings.append(
                {
                    "vehicle_id": vehicle_id,
                    "imsi": imsi,
                    "role": vehicle.get("role", "traffic"),
                }
            )

        return {
            "generated_by": "carla_ns3_mapper",
            "source": str(self.input_path),
            "base_imsi": self.base_imsi,
            "max_vehicles": self.max_vehicles,
            "mappings": mappings,
            "roles": roles,
            "timestamp": time.time(),
        }

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        ensure_runtime_dirs()
        print(f"[CARLA_NS3_MAPPER] input={self.input_path} output={self.output_path}")

        while self.running:
            vehicles_payload = _read_json(self.input_path, {})
            vehicles = vehicles_payload.get("vehicles", []) if isinstance(vehicles_payload, dict) else []
            payload = self._build_payload(vehicles)
            _write_json_atomic(self.output_path, payload)
            time.sleep(self.poll_interval)

        print("[CARLA_NS3_MAPPER] shutdown")


def main() -> None:
    parser = argparse.ArgumentParser(description="Map CARLA vehicles to GreenRAN IMSIs")
    parser.add_argument("--input", default=str(CARLA_VEHICLES_PATH))
    parser.add_argument("--output", default=str(CARLA_VEHICLE_MAP_PATH))
    parser.add_argument("--base-imsi", type=int, default=16)
    parser.add_argument("--max-vehicles", type=int, default=5)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    args = parser.parse_args()

    mapper = CarlaNs3Mapper(
        input_path=Path(args.input),
        output_path=Path(args.output),
        base_imsi=args.base_imsi,
        max_vehicles=args.max_vehicles,
        poll_interval=args.poll_interval,
    )
    mapper.run()


if __name__ == "__main__":
    main()
