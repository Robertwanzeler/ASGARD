#!/usr/bin/env python3
"""
GreenRAN App1 Dashboard Creator for Grafana
===========================================

Cria/atualiza um dashboard provisionado por arquivo para o measurement
`app1_summary`, evitando dependência da API autenticada do Grafana.
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
    / "app1_greenran.json"
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
    "gnetId": None,
    "graphTooltip": 0,
    "links": [],
    "panels": [
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "green", "value": None},
                            {"color": "yellow", "value": 0.45},
                            {"color": "red", "value": 0.78},
                        ],
                    },
                    "unit": "percentunit",
                },
                "overrides": [],
            },
            "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
            "id": 1,
            "options": {
                "legend": {"displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "single"},
            },
            "targets": [
                {
                    "alias": "Visual Risk Score",
                    "datasource": "InfluxDB",
                    "query": 'SELECT mean("visual_risk_score") FROM "app1_summary" WHERE $timeFilter GROUP BY time($__interval) fill(none)',
                    "rawQuery": True,
                    "refId": "A",
                }
            ],
            "title": "App1 Visual Risk Score",
            "type": "timeseries",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "unit": "short",
                },
                "overrides": [],
            },
            "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
            "id": 2,
            "options": {
                "legend": {"displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "single"},
            },
            "targets": [
                {
                    "alias": "Motion Intensity",
                    "datasource": "InfluxDB",
                    "query": 'SELECT mean("motion_intensity") FROM "app1_summary" WHERE $timeFilter GROUP BY time($__interval) fill(none)',
                    "rawQuery": True,
                    "refId": "A",
                }
            ],
            "title": "App1 Motion Intensity",
            "type": "timeseries",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "green", "value": None},
                            {"color": "yellow", "value": 1},
                            {"color": "red", "value": 3},
                        ],
                    },
                },
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 8, "x": 0, "y": 8},
            "id": 3,
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "justifyMode": "auto",
                "orientation": "auto",
                "reduceOptions": {
                    "calcs": ["lastNotNull"],
                    "fields": "",
                    "values": False,
                },
                "textMode": "auto",
            },
            "targets": [
                {
                    "datasource": "InfluxDB",
                    "query": 'SELECT last("videos_uploaded") FROM "app1_summary"',
                    "rawQuery": True,
                    "refId": "A",
                }
            ],
            "title": "Uploads de Vídeo",
            "type": "stat",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "thresholds"}},
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 8, "x": 8, "y": 8},
            "id": 4,
            "options": {
                "colorMode": "value",
                "graphMode": "none",
                "justifyMode": "auto",
                "orientation": "auto",
                "reduceOptions": {
                    "calcs": ["lastNotNull"],
                    "fields": "",
                    "values": False,
                },
                "textMode": "auto",
            },
            "targets": [
                {
                    "datasource": "InfluxDB",
                    "query": 'SELECT last("analyses_critical") FROM "app1_summary"',
                    "rawQuery": True,
                    "refId": "A",
                }
            ],
            "title": "Análises Críticas",
            "type": "stat",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {"color": {"mode": "palette-classic"}},
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 8, "x": 16, "y": 8},
            "id": 5,
            "options": {
                "legend": {"displayMode": "table", "placement": "bottom"},
                "pieType": "pie",
                "reduceOptions": {
                    "calcs": ["lastNotNull"],
                    "fields": "",
                    "values": False,
                },
                "tooltip": {"mode": "single"},
            },
            "targets": [
                {
                    "datasource": "InfluxDB",
                    "query": 'SELECT last("events_total") AS "total", last("events_open") AS "open", last("events_validated") AS "validated" FROM "app1_summary"',
                    "rawQuery": True,
                    "refId": "A",
                }
            ],
            "title": "Eventos App1",
            "type": "piechart",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "green", "value": None},
                            {"color": "yellow", "value": 60},
                            {"color": "red", "value": 80},
                        ],
                    },
                    "unit": "ms",
                },
                "overrides": [],
            },
            "gridPos": {"h": 8, "w": 24, "x": 0, "y": 14},
            "id": 6,
            "options": {
                "legend": {"displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "single"},
            },
            "targets": [
                {
                    "alias": "Avg Latency Seen by App1",
                    "datasource": "InfluxDB",
                    "query": 'SELECT mean("avg_latency_ms") FROM "app1_summary" WHERE $timeFilter GROUP BY time($__interval) fill(none)',
                    "rawQuery": True,
                    "refId": "A",
                }
            ],
            "title": "Latência de Rede Vista pela App1",
            "type": "timeseries",
        },
        {
            "datasource": "InfluxDB",
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "red", "value": None},
                            {"color": "yellow", "value": 1},
                            {"color": "green", "value": 3},
                        ],
                    },
                    "unit": "short",
                },
                "overrides": [],
            },
            "gridPos": {"h": 6, "w": 8, "x": 0, "y": 22},
            "id": 7,
            "options": {
                "colorMode": "value",
                "graphMode": "area",
                "justifyMode": "auto",
                "orientation": "auto",
                "reduceOptions": {
                    "calcs": ["lastNotNull"],
                    "fields": "",
                    "values": False,
                },
                "textMode": "auto",
            },
            "targets": [
                {
                    "datasource": "InfluxDB",
                    "query": 'SELECT last("cameras_bound") FROM "app1_summary"',
                    "rawQuery": True,
                    "refId": "A",
                }
            ],
            "title": "Câmeras Vinculadas",
            "type": "stat",
        },
    ],
    "refresh": "5s",
    "schemaVersion": 27,
    "style": "dark",
    "tags": ["greenran", "app1", "ufpa"],
    "templating": {"list": []},
    "time": {"from": "now-1h", "to": "now"},
    "timepicker": {
        "refresh_intervals": ["5s", "10s", "30s", "1m", "5m", "15m"],
    },
    "timezone": "browser",
    "title": "App1 - Vigilancia UFPA",
    "uid": "app1-greenran",
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
