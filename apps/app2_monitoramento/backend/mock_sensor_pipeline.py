#!/usr/bin/env python3
"""
Gerador simples de leituras simuladas para a App2-Monitoramento.
"""

from __future__ import annotations

import argparse
import json
import random
from urllib import request


DEFAULT_URL = "http://127.0.0.1:5200/api/readings/mock"

DEFAULT_SENSOR_VALUES = {
    "soil_moisture": (15.0, 90.0, "%"),
    "temperature": (16.0, 42.0, "C"),
    "rainfall": (0.0, 90.0, "mm"),
    "air_quality": (20.0, 220.0, "AQI"),
}


def send_reading(url: str, sensor_id: str, sensor_type: str, location: str, value: float, unit: str) -> None:
    payload = {
        "sensor_id": sensor_id,
        "sensor_type": sensor_type,
        "type": sensor_type,
        "value": value,
        "unit": unit,
        "location": location,
        "source": "mock_sensor_pipeline",
    }

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with request.urlopen(req, timeout=5) as resp:
        print(resp.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock pipeline da App2-Monitoramento")
    parser.add_argument("--url", default=DEFAULT_URL, help="Endpoint da App2")
    parser.add_argument("--sensor-id", default="SENSOR-01", help="Identificador do sensor")
    parser.add_argument("--sensor-type", default="soil_moisture", choices=sorted(DEFAULT_SENSOR_VALUES.keys()))
    parser.add_argument("--location", default="Area Verde UFPA", help="Local da leitura")
    parser.add_argument("--value", type=float, default=None, help="Valor da leitura")
    args = parser.parse_args()

    min_val, max_val, unit = DEFAULT_SENSOR_VALUES[args.sensor_type]
    value = args.value if args.value is not None else round(random.uniform(min_val, max_val), 2)
    send_reading(args.url, args.sensor_id, args.sensor_type, args.location, value, unit)


if __name__ == "__main__":
    main()
