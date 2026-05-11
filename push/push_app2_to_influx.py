#!/usr/bin/env python3
"""
GreenRAN - Push App2 Monitoring to InfluxDB
============================================

Lê o snapshot de monitoramento da App2-Sensores e envia métricas
para InfluxDB, permitindo visualização no Grafana.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from greenran_paths import STATE_DIR, as_str
from greenran_runtime import load_runtime_config


RUNTIME_CONFIG = load_runtime_config()
DEFAULT_INFLUX_HOST = RUNTIME_CONFIG["monitoring"]["influxdb_host"]
DEFAULT_INFLUX_PORT = int(RUNTIME_CONFIG["monitoring"]["influxdb_port"])
DEFAULT_INFLUX_DB = RUNTIME_CONFIG["monitoring"]["influxdb_db"]
DEFAULT_INTERVAL = int(RUNTIME_CONFIG["monitoring"]["push_interval_seconds"])
APP2_MONITORING_FILE = STATE_DIR / "app2_monitoramento" / "monitoring_snapshot.json"


def tag_value(value):
    return str(value).replace(" ", "_").replace(",", "_").replace("=", "_")


def load_snapshot():
    if not APP2_MONITORING_FILE.exists():
        return {}
    try:
        with open(APP2_MONITORING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def snapshot_age_seconds():
    if not APP2_MONITORING_FILE.exists():
        return None
    return time.time() - APP2_MONITORING_FILE.stat().st_mtime


def build_lines(snapshot):
    if not snapshot:
        return []

    ts = int(time.time() * 1e9)
    lines = []

    # Sensors summary
    sensors = snapshot.get("sensors", {})
    readings = snapshot.get("readings", {})
    network = snapshot.get("network", {})
    alerts = snapshot.get("alerts", [])

    # App2 summary measurement - CORRIGIDO
    line = f"app2_summary "
    line += f"sensors_total={sensors.get('total', 0)}i,"
    line += f"sensors_active={sensors.get('active', 0)}i,"
    line += f"sensors_connected={sensors.get('connected', sensors.get('active', 0))}i,"
    line += f"sensors_error={sensors.get('error', 0)}i,"
    line += f"sensors_low_battery={sensors.get('low_battery', 0)}i,"
    line += f"avg_temperature={readings.get('avg_temperature_c', 0)},"
    line += f"avg_humidity={readings.get('avg_humidity_percent', 0)},"
    line += f"avg_soil_conductivity={readings.get('avg_soil_conductivity', 0)},"
    line += f"avg_battery_percent={readings.get('avg_battery_percent', 0)},"
    line += f"avg_power_mw={readings.get('avg_power_mw', 0)},"
    line += f"packet_loss={network.get('packet_loss_percent', 0)},"
    line += f"avg_sensor_latency_ms={network.get('avg_latency_ms', 0)},"
    line += f"avg_rssi_dbm={network.get('avg_rssi_dbm', 0)},"
    line += f"network_utilization_percent={network.get('network_utilization_percent', 0)},"
    line += f"delivery_success_percent={network.get('delivery_success_percent', 100)},"
    line += f"tx_packets={network.get('tx_packets', 0)}i,"
    line += f"rx_packets={network.get('rx_packets', 0)}i,"
    line += f"lost_packets={network.get('lost_packets', 0)}i,"
    line += f"alerts_count={len(alerts)}i"
    line += f" {ts}"
    lines.append(line)

    # Sensor readings individual
    sensors_file = STATE_DIR / "app2_monitoramento" / "sensors" / "latest.json"
    if sensors_file.exists():
        with open(sensors_file, 'r') as f:
            individual_sensors = json.load(f)
        
        for sensor in individual_sensors:
            sensor_id = sensor.get('sensor_id', 0)
            sensor_type = sensor.get('type', 'unknown')
            value = sensor.get('value', 0)
            status = sensor.get('status', 'ok')
            connectivity = tag_value(sensor.get('connectivity', 'unknown'))
            gateway_id = tag_value(sensor.get('gateway_id', 'unknown'))
            domain = tag_value(sensor.get('domain', 'unknown'))
            
            # Line Protocol CORRIGIDO
            line = (
                f"sensor_reading,sensor_id={sensor_id},type={tag_value(sensor_type)},"
                f"status={tag_value(status)},connectivity={connectivity},gateway_id={gateway_id},domain={domain} "
                f"value={value},"
                f"battery_percent={sensor.get('battery_percent', 0)},"
                f"latency_ms={sensor.get('latency_ms', 0)},"
                f"packet_loss_percent={sensor.get('packet_loss_percent', 0)},"
                f"rssi_dbm={sensor.get('rssi_dbm', 0)},"
                f"power_mw={sensor.get('power_mw', 0)} {ts}"
            )
            lines.append(line)

    return lines


def send_to_influx(lines, host, port, db):
    url = f"http://{host}:{port}/write?db={db}"
    print(f"Enviando para {url}...")
    print(f"Linhas: {len(lines)}")
    try:
        response = requests.post(url, data="\n".join(lines), timeout=5)
        print(f"Status: {response.status_code}")
        if response.status_code != 204 and response.text:
            print(f"Resposta InfluxDB: {response.text[:500]}")
        return response.status_code == 204
    except Exception as e:
        print(f"Erro ao enviar para InfluxDB: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Push App2 data to InfluxDB")
    parser.add_argument("--host", default=DEFAULT_INFLUX_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_INFLUX_PORT)
    parser.add_argument("--db", default=DEFAULT_INFLUX_DB)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    args = parser.parse_args()

    print(f"=== Push App2 to InfluxDB ===")
    print(f"Host: {args.host}:{args.port}/{args.db}")
    print(f"Interval: {args.interval}s")
    print()

    counter = 0
    while True:
        counter += 1

        snapshot = load_snapshot()
        age = snapshot_age_seconds()
        max_age = max(args.interval * 3, 30)
        if age is not None and age > max_age:
            print(f"[{counter:04d}] Snapshot App2 antigo ({age:.0f}s). Verifique simulate_sensors.py")
            time.sleep(args.interval)
            continue

        lines = build_lines(snapshot)

        if lines:
            success = send_to_influx(lines, args.host, args.port, args.db)
            if success:
                sensors = snapshot.get("sensors", {})
                reading = snapshot.get("readings", {})
                network = snapshot.get("network", {})
                print(f"[{counter:04d}] OK | Sensores: {sensors.get('active', 0)}/{sensors.get('total', 0)} | "
                      f"Temp: {reading.get('avg_temperature_c', 0):.1f}°C | "
                      f"PktLoss: {network.get('packet_loss_percent', 0):.1f}%")
            else:
                print(f"[{counter:04d}] ERRO ao enviar")
        else:
            print(f"[{counter:04d}] Sem dados")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
