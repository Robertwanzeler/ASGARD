#!/usr/bin/env python3
"""
xApp VehicleControl.

Atua como o terceiro xApp do GreenRAN, produzindo uma intenção operacional
dedicada ao domínio veicular (App3) a partir do snapshot combinado
ns-3/CARLA/App3.
"""

from __future__ import annotations

import argparse
import signal
import time

from greenran_paths import VEHICLE_INTENT_PATH, ensure_runtime_dirs, as_str
from vehicle_policy_runtime import build_vehicle_intent, evaluate_vehicle_policy, get_vehicle_metrics


class VehicleControlXApp:
    def __init__(self, interval: float = 2.0):
        self.interval = max(1.0, float(interval))
        self.running = True
        ensure_runtime_dirs()
        self.intent_path = as_str(VEHICLE_INTENT_PATH)

    def _signal_handler(self, signum, frame):
        self.running = False

    def _write_intent(self, intent: dict) -> None:
        with open(self.intent_path, "w", encoding="utf-8") as f:
            f.write("xApp=vehicle_control\n")
            f.write(f"TIMESTAMP={int(time.time())}\n")
            for key, value in intent.items():
                if isinstance(value, float):
                    f.write(f"{key}={value:.4f}\n")
                else:
                    f.write(f"{key}={value}\n")

    def run(self) -> int:
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        print("[VehicleControl] starting")
        print("[VehicleControl] ready")

        while self.running:
            metrics = get_vehicle_metrics()
            policy = evaluate_vehicle_policy(metrics)
            intent = build_vehicle_intent(metrics, policy)
            self._write_intent(intent)
            time.sleep(self.interval)

        print("[VehicleControl] stopping")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GreenRAN xApp VehicleControl")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    args = parser.parse_args()
    return VehicleControlXApp(interval=args.interval).run()


if __name__ == "__main__":
    raise SystemExit(main())
