#!/usr/bin/env python3
"""
GreenRAN - App2-Monitoramento: Simulador de Sensores
=============================================
Simula sensores IoT para monitoramento ambiental e solo.
"""

import json
import random
import time
from datetime import datetime
from pathlib import Path
import argparse

# Caminho fixo para evitar problemas de import
STATE_DIR = Path("/tmp")
APP2_STATE_DIR = STATE_DIR / "app2_monitoramento"
SCENARIO_CONTROL_FILE = STATE_DIR / "article00_scenario_control.json"

SENSOR_PROFILES = [
    ("temperature", "°C", 20, 35, "5g_redcap", "GW-5G-01", "environmental"),
    ("humidity", "%", 60, 95, "5g_redcap", "GW-5G-01", "environmental"),
    ("soil_moisture", "%", 20, 80, "lora_fwa", "GW-LORA-01", "soil"),
    ("soil_temp", "°C", 22, 32, "lora_fwa", "GW-LORA-01", "soil"),
    ("soil_conductivity", "dS/m", 0.1, 2.5, "lora_fwa", "GW-LORA-01", "soil"),
    ("soil_nitrogen", "mg/kg", 8, 48, "lora_fwa", "GW-LORA-01", "soil"),
    ("air_quality", "AQI", 20, 160, "5g_native", "GW-5G-02", "environmental"),
    ("rain_intensity", "mm/h", 0, 18, "ntn_gateway", "GW-NTN-01", "environmental"),
    ("solar_radiation", "W/m²", 0, 1000, "5g_redcap", "GW-5G-02", "environmental"),
]

CONNECTIVITY_PROFILES = {
    "5g_native": {"latency": (25, 80), "rssi": (-88, -65), "loss": (0.1, 2.5), "power": (450, 900)},
    "5g_redcap": {"latency": (40, 150), "rssi": (-95, -70), "loss": (0.2, 4.0), "power": (120, 280)},
    "lora_fwa": {"latency": (180, 650), "rssi": (-118, -88), "loss": (1.0, 9.0), "power": (35, 95)},
    "ntn_gateway": {"latency": (500, 1400), "rssi": (-125, -95), "loss": (2.0, 12.0), "power": (80, 180)},
}

def ensure_dirs():
    APP2_STATE_DIR.mkdir(parents=True, exist_ok=True)
    (APP2_STATE_DIR / "sensors").mkdir(exist_ok=True)


def _safe_read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _load_sensor_override():
    control = _safe_read_json(SCENARIO_CONTROL_FILE, {})
    override = control.get("app2_sensor_override", {}) or {}
    if not override.get("enabled", False):
        return {}
    return override

def simulate_sensor_data(num_sensors=17):
    sensors = []
    base_profiles = SENSOR_PROFILES * (num_sensors // len(SENSOR_PROFILES) + 1)

    for i in range(num_sensors):
        sensor_type, unit, min_value, max_value, connectivity, gateway_id, domain = base_profiles[i]
        profile = CONNECTIVITY_PROFILES[connectivity]
        packet_loss = random.uniform(*profile["loss"])
        battery_percent = max(3, min(100, random.gauss(78, 14) - (i % 5) * 3))
        connected = random.random() > min(0.35, packet_loss / 100.0 + (0.08 if battery_percent < 15 else 0))
        value = random.uniform(min_value, max_value)

        sensors.append({
            'sensor_id': i + 1,
            'type': sensor_type,
            'value': round(value, 2),
            'unit': unit,
            'timestamp': datetime.now().isoformat(),
            'lat': -1.24 + random.uniform(-0.01, 0.01),
            'lon': -48.50 + random.uniform(-0.01, 0.01),
            'status': 'ok' if connected else 'error',
            'domain': domain,
            'connectivity': connectivity,
            'gateway_id': gateway_id,
            'battery_percent': round(battery_percent, 1),
            'latency_ms': round(random.uniform(*profile["latency"]), 1),
            'packet_loss_percent': round(packet_loss, 2),
            'rssi_dbm': round(random.uniform(*profile["rssi"]), 1),
            'power_mw': round(random.uniform(*profile["power"]), 1),
            'tx_interval_s': 5 if connectivity in ['5g_native', '5g_redcap'] else 30,
            'packets_tx': random.randint(8, 30),
        })
    
    return sensors


def _clamp(value, lower, upper):
    return max(lower, min(upper, value))


def apply_sensor_override(sensors, override):
    if not override:
        return sensors

    total = len(sensors)
    connected_target = int(override.get("connected_sensors", total) or total)
    connected_target = _clamp(connected_target, 0, total)
    error_target = total - connected_target
    low_battery_target = int(override.get("low_battery_sensors", 0) or 0)
    low_battery_target = _clamp(low_battery_target, 0, total)

    packet_loss_target = float(override.get("packet_loss_percent", 0.0) or 0.0)
    avg_latency_target = float(override.get("avg_latency_ms", 120.0) or 120.0)
    avg_rssi_target = float(override.get("avg_rssi_dbm", -92.0) or -92.0)
    avg_battery_target = float(override.get("avg_battery_percent", 75.0) or 75.0)
    avg_power_target = float(override.get("avg_power_mw", 180.0) or 180.0)
    utilization_target = float(override.get("network_utilization_percent", 60.0) or 60.0)

    for idx, sensor in enumerate(sensors):
        connected = idx < connected_target
        sensor["status"] = "ok" if connected else "error"
        sensor["packet_loss_percent"] = round(_clamp(packet_loss_target + random.uniform(-0.35, 0.35), 0.0, 100.0), 2)
        sensor["latency_ms"] = round(max(1.0, avg_latency_target + random.uniform(-12.0, 12.0)), 1)
        sensor["rssi_dbm"] = round(avg_rssi_target + random.uniform(-4.0, 4.0), 1)
        sensor["battery_percent"] = round(
            max(3.0, (12.0 if idx < low_battery_target else avg_battery_target) + random.uniform(-3.0, 3.0)),
            1,
        )
        sensor["power_mw"] = round(max(10.0, avg_power_target + random.uniform(-18.0, 18.0)), 1)
        base_packets = max(6, int(utilization_target / 5) + random.randint(-2, 2))
        if not connected:
            base_packets = max(2, base_packets - error_target)
        sensor["packets_tx"] = base_packets

    return sensors

def generate_monitoring_snapshot(sensors):
    active = sum(1 for s in sensors if s['status'] == 'ok')
    error = sum(1 for s in sensors if s['status'] == 'error')
    
    temp_readings = [s['value'] for s in sensors if s['type'] == 'temperature']
    humidity_readings = [s['value'] for s in sensors if s['type'] in ['humidity', 'soil_moisture']]
    soil_conductivity_readings = [s['value'] for s in sensors if s['type'] == 'soil_conductivity']
    packet_losses = [s['packet_loss_percent'] for s in sensors]
    latencies = [s['latency_ms'] for s in sensors]
    rssi_values = [s['rssi_dbm'] for s in sensors]
    batteries = [s['battery_percent'] for s in sensors]
    power_values = [s['power_mw'] for s in sensors]
    packets_tx = sum(s['packets_tx'] for s in sensors)
    packets_lost = sum(int(round(s['packets_tx'] * s['packet_loss_percent'] / 100.0)) for s in sensors)
    packets_rx = max(0, packets_tx - packets_lost)
    
    avg_temp = sum(temp_readings) / len(temp_readings) if temp_readings else 0
    avg_humidity = sum(humidity_readings) / len(humidity_readings) if humidity_readings else 0
    avg_soil_conductivity = (
        sum(soil_conductivity_readings) / len(soil_conductivity_readings)
        if soil_conductivity_readings
        else 0
    )
    
    packet_loss = sum(packet_losses) / len(packet_losses) if packet_losses else 0
    low_battery = sum(1 for s in sensors if s['battery_percent'] < 20)
    connected = sum(1 for s in sensors if s['status'] == 'ok')
    
    snapshot = {
        'timestamp': datetime.now().isoformat(),
        'sensors': {
            'total': len(sensors),
            'active': active,
            'connected': connected,
            'error': error,
            'low_battery': low_battery,
            'connectivity_modes': sorted(set(s['connectivity'] for s in sensors)),
            'gateways': sorted(set(s['gateway_id'] for s in sensors)),
        },
        'readings': {
            'avg_temperature_c': round(avg_temp, 2),
            'avg_humidity_percent': round(avg_humidity, 2),
            'avg_soil_conductivity': round(avg_soil_conductivity, 2),
            'avg_battery_percent': round(sum(batteries) / len(batteries), 2) if batteries else 0,
            'avg_power_mw': round(sum(power_values) / len(power_values), 2) if power_values else 0,
        },
        'network': {
            'packet_loss_percent': round(packet_loss, 2),
            'tx_packets': packets_tx,
            'rx_packets': packets_rx,
            'lost_packets': packets_lost,
            'avg_latency_ms': round(sum(latencies) / len(latencies), 2) if latencies else 0,
            'avg_rssi_dbm': round(sum(rssi_values) / len(rssi_values), 2) if rssi_values else 0,
            'network_utilization_percent': round(min(100, packets_tx / max(1, len(sensors) * 30) * 100), 2),
            'delivery_success_percent': round((packets_rx / packets_tx) * 100, 2) if packets_tx else 100,
        },
        'alerts': []
    }
    
    if packet_loss > 10:
        snapshot['alerts'].append({
            'level': 'critical',
            'message': f'Packet loss alto: {packet_loss:.1f}% (SLA: 5%)'
        })
    elif packet_loss > 5:
        snapshot['alerts'].append({
            'level': 'warning',
            'message': f'Packet loss elevado: {packet_loss:.1f}%'
        })
    
    if avg_temp > 32:
        snapshot['alerts'].append({
            'level': 'warning',
            'message': f'Temperatura alta: {avg_temp:.1f}°C'
        })

    if low_battery > 0:
        snapshot['alerts'].append({
            'level': 'warning',
            'message': f'{low_battery} sensor(es) com bateria abaixo de 20%'
        })

    if connected < len(sensors) * 0.9:
        snapshot['alerts'].append({
            'level': 'critical',
            'message': f'Conectividade degradada: {connected}/{len(sensors)} sensores conectados'
        })
    
    return snapshot

def main():
    parser = argparse.ArgumentParser(description='Simulador de sensores App2')
    parser.add_argument('--num-sensors', type=int, default=17)
    parser.add_argument('--interval', type=float, default=5.0)
    args = parser.parse_args()
    
    ensure_dirs()
    
    snapshot_file = APP2_STATE_DIR / "monitoring_snapshot.json"
    sensors_file = APP2_STATE_DIR / "sensors" / "latest.json"
    
    print(f"=== App2 Sensores: {args.num_sensors} | intervalo: {args.interval}s ===")
    
    counter = 0
    while True:
        counter += 1
        sensors = simulate_sensor_data(args.num_sensors)
        sensors = apply_sensor_override(sensors, _load_sensor_override())
        snapshot = generate_monitoring_snapshot(sensors)
        
        with open(snapshot_file, 'w') as f:
            json.dump(snapshot, f, indent=2)
        
        with open(sensors_file, 'w') as f:
            json.dump(sensors, f, indent=2)
        
        s = snapshot['sensors']
        r = snapshot['readings']
        n = snapshot['network']
        print(f"[{counter:04d}] Ativos: {s['active']}/{s['total']} | Temp: {r['avg_temperature_c']:.1f}°C | "
              f"Hum: {r['avg_humidity_percent']:.1f}% | PktLoss: {n['packet_loss_percent']:.1f}%")
        
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
