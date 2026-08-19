#!/usr/bin/env python3
"""
GreenRAN App3 Dashboard Creator for Grafana
===========================================

Cria/atualiza um dashboard provisionado por arquivo para o measurement
`app3_summary`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DASHBOARD_FILE = (
    BASE_DIR
    / "ns-O-RAN-flexric"
    / "mmwave-LENA-oran"
    / "GUI"
    / "grafana"
    / "dashboards"
    / "app3_greenran.json"
)


DASHBOARD_JSON = {
    "annotations": {
        "list": [
            {
                "builtIn": 1,
                "datasource": "-- Grafana --",
                "enable": True,
                "hide": True,
                "iconColor": "rgba(0, 211, 255, 1)",
                "name": "Annotations & Alerts",
                "type": "dashboard",
            }
        ]
    },
    "editable": True,
    "graphTooltip": 0,
    "links": [],
    "panels": [
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}, "unit": "short"},
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 6, "x": 0, "y": 0},
            "id": 1,
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
            },
            "targets": [{"datasource": "InfluxDB", "query": 'SELECT last("total_vehicles") FROM "app3_summary"', "rawQuery": True, "refId": "A"}],
            "title": "Veículos Ativos",
            "type": "stat",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}, "unit": "short"},
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 6, "x": 6, "y": 0},
            "id": 2,
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
            },
            "targets": [{"datasource": "InfluxDB", "query": 'SELECT last("high_risk_vehicles") FROM "app3_summary"', "rawQuery": True, "refId": "A"}],
            "title": "Veículos Alto Risco",
            "type": "stat",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}, "unit": "short"},
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 6, "x": 12, "y": 0},
            "id": 3,
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
            },
            "targets": [{"datasource": "InfluxDB", "query": 'SELECT last("medium_risk_vehicles") FROM "app3_summary"', "rawQuery": True, "refId": "A"}],
            "title": "Veículos Médio Risco",
            "type": "stat",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}, "unit": "short"},
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 6, "x": 18, "y": 0},
            "id": 4,
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
            },
            "targets": [{"datasource": "InfluxDB", "query": 'SELECT last("degraded_autonomy_vehicles") FROM "app3_summary"', "rawQuery": True, "refId": "A"}],
            "title": "Autonomia Degradada",
            "type": "stat",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}, "unit": "ms"},
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 6, "x": 0, "y": 6},
            "id": 5,
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
            },
            "targets": [{"alias": "Latência Máxima", "datasource": "InfluxDB", "query": 'SELECT last("max_latency_ms") FROM "app3_summary" WHERE $timeFilter', "rawQuery": True, "refId": "A"}],
            "title": "Latência Veicular Máxima",
            "type": "stat",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}, "unit": "percent"},
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 6, "x": 6, "y": 6},
            "id": 6,
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
            },
            "targets": [{"alias": "Perda Máxima", "datasource": "InfluxDB", "query": 'SELECT last("max_packet_loss_percent") FROM "app3_summary" WHERE $timeFilter', "rawQuery": True, "refId": "A"}],
            "title": "Perda de Pacotes Veicular",
            "type": "stat",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}, "unit": "velocitymps"},
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 6, "x": 12, "y": 6},
            "id": 7,
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
            },
            "targets": [{"alias": "Velocidade Máxima", "datasource": "InfluxDB", "query": 'SELECT last("max_speed_mps") FROM "app3_summary" WHERE $timeFilter', "rawQuery": True, "refId": "A"}],
            "title": "Velocidade Máxima",
            "type": "stat",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}, "unit": "short"},
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 6, "x": 18, "y": 6},
            "id": 8,
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto",
            },
            "targets": [{"alias": "Eventos", "datasource": "InfluxDB", "query": 'SELECT last("events_count") FROM "app3_summary" WHERE $timeFilter', "rawQuery": True, "refId": "A"}],
            "title": "Eventos Veiculares",
            "type": "stat",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}, "unit": "ms"},
                "overrides": [],
            },
            "gridPos": {"h": 8, "w": 12, "x": 0, "y": 12},
            "id": 9,
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "single"}},
            "targets": [{"alias": "Latência Máxima", "datasource": "InfluxDB", "query": 'SELECT mean("max_latency_ms") FROM "app3_summary" WHERE $timeFilter GROUP BY time($__interval) fill(none)', "rawQuery": True, "refId": "A"}],
            "title": "Histórico de Latência Veicular",
            "type": "timeseries",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}, "unit": "percent"},
                "overrides": [],
            },
            "gridPos": {"h": 8, "w": 12, "x": 12, "y": 12},
            "id": 10,
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "single"}},
            "targets": [{"alias": "Perda Máxima", "datasource": "InfluxDB", "query": 'SELECT mean("max_packet_loss_percent") FROM "app3_summary" WHERE $timeFilter GROUP BY time($__interval) fill(none)', "rawQuery": True, "refId": "A"}],
            "title": "Histórico de Perda de Pacotes",
            "type": "timeseries",
        },
    ],
    "refresh": "5s",
    "schemaVersion": 27,
    "style": "dark",
    "tags": ["greenran", "app3", "veicular"],
    "templating": {"list": []},
    "time": {"from": "now-1h", "to": "now"},
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m", "15m"]},
    "timezone": "browser",
    "title": "App3 - Veicular GreenRAN v2",
    "uid": "app3-greenran-v2",
    "version": 1,
}


def create_dashboard() -> bool:
    try:
        DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
            json.dump(DASHBOARD_JSON, f, indent=2)
            f.write("\n")
        print(f"[SUCCESS] Dashboard provisionado em {DASHBOARD_FILE}")
        print("[INFO] O Grafana deve detectar a mudança em poucos segundos.")
        return True
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return False


if __name__ == "__main__":
    ok = create_dashboard()
    sys.exit(0 if ok else 1)
