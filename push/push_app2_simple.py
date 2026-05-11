#!/usr/bin/env python3
"""
Simple push App2 to InfluxDB - Direct execution (CORRIGIDO)
"""

import json
import time
import requests
from pathlib import Path

STATE_DIR = Path("/tmp")
APP2_MONITORING_FILE = STATE_DIR / "app2_monitoramento" / "monitoring_snapshot.json"

def main():
    print("=== Push App2 to InfluxDB (Direct) ===")
    
    if not APP2_MONITORING_FILE.exists():
        print("Arquivo nao encontrado!")
        return
    
    with open(APP2_MONITORING_FILE) as f:
        snapshot = json.load(f)
    
    if not snapshot:
        print("Sem dados!")
        return
    
    # Build line - CORRIGIDO com valores numéricos
    ts = int(time.time() * 1e9)
    
    sensors = snapshot.get("sensors", {})
    readings = snapshot.get("readings", {})
    network = snapshot.get("network", {})
    
    # Line Protocol correto
    line = f"app2_summary "
    line += f"sensors_total={sensors.get('total', 0)}i,"
    line += f"sensors_active={sensors.get('active', 0)}i,"
    line += f"sensors_error={sensors.get('error', 0)}i,"
    line += f"avg_temperature={readings.get('avg_temperature_c', 0)},"
    line += f"avg_humidity={readings.get('avg_humidity_percent', 0)},"
    line += f"avg_soil_conductivity={readings.get('avg_soil_conductivity', 0)},"
    line += f"packet_loss={network.get('packet_loss_percent', 0)},"
    line += f"tx_packets={network.get('tx_packets', 0)}i,"
    line += f"rx_packets={network.get('rx_packets', 0)}i,"
    line += f"alerts_count={len(snapshot.get('alerts', []))}i"
    line += f" {ts}"
    
    print(f"Line: {line}")
    
    # Send to InfluxDB
    url = "http://localhost:8086/write?db=influx"
    try:
        response = requests.post(url, data=line, timeout=5)
        print(f"Status: {response.status_code}")
        if response.status_code == 204:
            print("OK - Dados enviados!")
        else:
            print(f"Erro: {response.text}")
    except Exception as e:
        print(f"Erro: {e}")

if __name__ == "__main__":
    main()