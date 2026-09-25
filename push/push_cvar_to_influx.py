#!/usr/bin/env python3
"""
GreenRAN O-RAN - Push CVaR to InfluxDB
======================================

Script para exportar CVaR do SQLite para InfluxDB em tempo real.

Uso:
    python3 push_cvar_to_influx.py

Funcionamento:
    1. Lê CVaR do SQLite (/tmp/rapp_data_lake.db)
    2. Envia para InfluxDB via HTTP
    3. Repete a cada 5 segundos
"""

import os
import sys
import time
import sqlite3
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from greenran_paths import RAPP_DB_PATH, RAPP_LOG_PATH as CORE_RAPP_LOG_PATH, as_str
from greenran_runtime import load_runtime_config

RUNTIME_CONFIG = load_runtime_config()

DEFAULT_INFLUX_HOST = RUNTIME_CONFIG["monitoring"]["influxdb_host"]
DEFAULT_INFLUX_PORT = int(RUNTIME_CONFIG["monitoring"]["influxdb_port"])
DEFAULT_INFLUX_DB = RUNTIME_CONFIG["monitoring"]["influxdb_db"]
DEFAULT_INFLUX_USER = os.getenv("GREENRAN_INFLUXDB_USER", "")
DEFAULT_INFLUX_PASSWORD = os.getenv("GREENRAN_INFLUXDB_PASSWORD", "")
DEFAULT_INTERVAL = int(RUNTIME_CONFIG["monitoring"]["push_interval_seconds"])
DEFAULT_SQLITE_PATH = as_str(RAPP_DB_PATH)
RAPP_LOG_PATH = as_str(CORE_RAPP_LOG_PATH)


def get_latest_drl():
    """Extrai dados DRL (SBiLSTM + A3C) do log."""
    import re
    
    if not os.path.exists(RAPP_LOG_PATH):
        return None
    
    try:
        with open(RAPP_LOG_PATH, 'r') as f:
            lines = f.readlines()
        
        # Procurar últimas linhas com DRL
        drl_data = []
        for line in reversed(lines[-100:]):
            if 'rApp DRL' in line and 'CVaR predicted' in line:
                match = re.search(
                    r'CVaR predicted: ([\d.]+)ms, Decision: (\w+), Power: (\w+)',
                    line
                )
                if match:
                    # Extrair timestamp
                    ts_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                    timestamp = ts_match.group(1) if ts_match else ""
                    
                    drl_data.append({
                        'cvar': float(match.group(1)),
                        'decision': match.group(2),
                        'power': match.group(3),
                        'timestamp': timestamp
                    })
                    
                    if len(drl_data) >= 10:
                        break
        
        return drl_data[-1] if drl_data else None
    except Exception as e:
        print(f"[DRL Parse] Erro: {e}")
        return None


def get_latest_cvar(sqlite_path):
    """Obtém o último CVaR do banco SQLite."""
    if not os.path.exists(sqlite_path):
        return None
    
    try:
        conn = sqlite3.connect(sqlite_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT cvar_per_ue_us, timestamp, datetime 
            FROM extended_metrics 
            WHERE cvar_per_ue_us > 0
            ORDER BY timestamp DESC 
            LIMIT 1
        """)
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {"cvar": result[0] / 1000.0, "timestamp": result[1], "datetime": result[2]}
        return None
    except Exception as e:
        print(f"[SQLite] Erro: {e}")
        return None


def push_to_influx(cvar_data, host, port, db, user, password):
    """Envia CVaR para InfluxDB via HTTP."""
    if not cvar_data:
        return False
    
    url = f"http://{host}:{port}/write?db={db}"
    
    cvar = cvar_data["cvar"]
    datetime_str = cvar_data.get("datetime", "")
    ts = int(time.time() * 1e9)
    
    line = f"cvar,source=data_lake value={cvar:.2f} {ts}"
    
    try:
        response = requests.post(
            url,
            data=line,
            headers={"Content-Type": "application/octet-stream"},
            auth=(user, password),
            timeout=5
        )
        
        if response.status_code == 204:
            return True
        else:
            print(f"[InfluxDB] Erro: status={response.status_code}")
            return False
    except Exception as e:
        print(f"[InfluxDB] Erro: {e}")
        return False


def push_drl_to_influx(drl_data, host, port, db, user, password):
    """Envia dados DRL para InfluxDB via HTTP."""
    if not drl_data:
        return False
    
    url = f"http://{host}:{port}/write?db={db}"
    ts = int(time.time() * 1e9)
    
    cvar = drl_data["cvar"]
    decision = drl_data["decision"]
    power = drl_data["power"]
    
    # Enviar como string com escape
    line = f"drl_prediction,model=sbilstm_a3c cvar={cvar:.2f},decision=\"{decision}\",power=\"{power}\" {ts}"
    
    try:
        response = requests.post(
            url,
            data=line,
            headers={"Content-Type": "application/octet-stream"},
            auth=(user, password),
            timeout=5
        )
        
        if response.status_code == 204:
            return True
        else:
            print(f"[DRL InfluxDB] Erro: status={response.status_code}")
            return False
    except Exception as e:
        print(f"[DRL InfluxDB] Erro: {e}")
        return False


def main():
    print("=" * 60)
    print("  GreenRAN - Push CVaR to InfluxDB")
    print("=" * 60)
    print(f"  SQLite: {DEFAULT_SQLITE_PATH}")
    print(f"  InfluxDB: {DEFAULT_INFLUX_HOST}:{DEFAULT_INFLUX_PORT}/{DEFAULT_INFLUX_DB}")
    print(f"  Interval: {DEFAULT_INTERVAL}s")
    print("=" * 60)
    
    total_pushed = 0
    total_drl_pushed = 0
    
    try:
        while True:
            # Enviar CVaR do Data Lake
            cvar_data = get_latest_cvar(DEFAULT_SQLITE_PATH)
            
            if cvar_data and cvar_data["cvar"] is not None:
                current_cvar = cvar_data["cvar"]
                success = push_to_influx(
                    cvar_data,
                    DEFAULT_INFLUX_HOST,
                    DEFAULT_INFLUX_PORT,
                    DEFAULT_INFLUX_DB,
                    DEFAULT_INFLUX_USER,
                    DEFAULT_INFLUX_PASSWORD
                )

                if success:
                    total_pushed += 1
                    print(f"  [CVaR] Enviado: {current_cvar:.2f}ms (total: {total_pushed})")
                else:
                    print(f"  [CVaR] Falha ao enviar CVaR={current_cvar:.2f}")
            
            # Enviar DRL (SBiLSTM + A3C)
            drl_data = get_latest_drl()
            if drl_data:
                success_drl = push_drl_to_influx(
                    drl_data,
                    DEFAULT_INFLUX_HOST,
                    DEFAULT_INFLUX_PORT,
                    DEFAULT_INFLUX_DB,
                    DEFAULT_INFLUX_USER,
                    DEFAULT_INFLUX_PASSWORD
                )
                if success_drl:
                    total_drl_pushed += 1
                    print(f"  [DRL] Enviado: cvar={drl_data['cvar']:.2f}ms, dec={drl_data['decision']}, pow={drl_data['power']}")
            
            time.sleep(DEFAULT_INTERVAL)
            
    except KeyboardInterrupt:
        print(f"\n\n  Parado pelo usuário.")
        print(f"  Total de CVaRs enviados: {total_pushed}")
        sys.exit(0)
    except Exception as e:
        print(f"\n  ERRO: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
