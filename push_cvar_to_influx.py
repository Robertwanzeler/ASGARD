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

DEFAULT_INFLUX_HOST = "172.18.0.2"
DEFAULT_INFLUX_PORT = 8086
DEFAULT_INFLUX_DB = "influx"
DEFAULT_INFLUX_USER = "admin"
DEFAULT_INFLUX_PASSWORD = "admin"
DEFAULT_INTERVAL = 5
DEFAULT_SQLITE_PATH = "/tmp/rapp_data_lake.db"


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


def main():
    print("=" * 60)
    print("  GreenRAN - Push CVaR to InfluxDB")
    print("=" * 60)
    print(f"  SQLite: {DEFAULT_SQLITE_PATH}")
    print(f"  InfluxDB: {DEFAULT_INFLUX_HOST}:{DEFAULT_INFLUX_PORT}/{DEFAULT_INFLUX_DB}")
    print(f"  Interval: {DEFAULT_INTERVAL}s")
    print("=" * 60)
    
    last_cvar = None
    total_pushed = 0
    
    try:
        while True:
            cvar_data = get_latest_cvar(DEFAULT_SQLITE_PATH)
            
            if cvar_data and cvar_data["cvar"] is not None:
                current_cvar = cvar_data["cvar"]
                
                if current_cvar != last_cvar:
                    success = push_to_influx(
                        cvar_data,
                        DEFAULT_INFLUX_HOST,
                        DEFAULT_INFLUX_PORT,
                        DEFAULT_INFLUX_DB,
                        DEFAULT_INFLUX_USER,
                        DEFAULT_INFLUX_PASSWORD
                    )
                    
                    if success:
                        last_cvar = current_cvar
                        total_pushed += 1
                        print(f"  [CVaR] Enviado: {current_cvar:.2f}ms (total: {total_pushed})")
                    else:
                        print(f"  [CVaR] Falha ao enviar CVaR={current_cvar:.2f}")
                else:
                    pass
            
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