#!/usr/bin/env python3
"""
GreenRAN - Create Grafana Dashboard for App2-Monitoramento
=======================================================
Cria/atualiza dashboard Grafana com consultas InfluxQL válidas.
"""

import sys
import time
import requests
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from greenran_runtime import load_runtime_config


RUNTIME_CONFIG = load_runtime_config()
GRAFANA_HOST = RUNTIME_CONFIG["monitoring"].get("grafana_host", "localhost")
GRAFANA_PORT = int(RUNTIME_CONFIG["monitoring"].get("grafana_port", 3001))
ADMIN_USER = "admin"
ADMIN_PASS = "admin"
API_TIMEOUT_S = 10
GRAFANA_READY_RETRIES = 20
GRAFANA_READY_SLEEP_S = 2
POST_RETRIES = 5
POST_RETRY_SLEEP_S = 2


def wait_for_grafana():
    """Wait until Grafana HTTP API answers before posting the dashboard."""
    health_url = f"http://{GRAFANA_HOST}:{GRAFANA_PORT}/api/health"

    for attempt in range(1, GRAFANA_READY_RETRIES + 1):
        try:
            response = requests.get(
                health_url,
                auth=(ADMIN_USER, ADMIN_PASS),
                timeout=API_TIMEOUT_S,
            )
            if response.status_code == 200:
                return True
        except requests.RequestException:
            pass

        if attempt < GRAFANA_READY_RETRIES:
            time.sleep(GRAFANA_READY_SLEEP_S)

    print("[ERROR] Grafana não respondeu ao health check a tempo")
    return False


def create_dashboard():
    """Cria dashboard para App2-Monitoramento"""
    if not wait_for_grafana():
        return False
    
    url = f"http://{GRAFANA_HOST}:{GRAFANA_PORT}/api/dashboards/db"
    
    dashboard = {
        "dashboard": {
            "title": "App2 - Monitoramento Ambiental UFPA",
            "tags": ["app2", "greenran", "monitoramento", "sensores"],
            "timezone": "browser",
            "panels": [
                # Panel 1: Sensores Ativos
                {
                    "id": 1,
                    "title": "Sensores Ativos",
                    "type": "stat",
                    "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4},
                    "targets": [{
                        "expr": 'app2_summary{sensors_total=~".*"}',
                        "legendFormat": "Total"
                    }],
                    "fieldConfig": {
                        "defaults": {
                            "unit": "short",
                            "thresholds": [
                                {"value": 0, "color": "red"},
                                {"value": 10, "color": "green"}
                            ]
                        }
                    }
                },
                # Panel 2: Temperatura Média
                {
                    "id": 2,
                    "title": "Temperatura Média",
                    "type": "gauge",
                    "gridPos": {"x": 6, "y": 0, "w": 6, "h": 4},
                    "targets": [{
                        "expr": "avg(app2_summary.avg_temperature)",
                        "legendFormat": "Temperatura"
                    }],
                    "fieldConfig": {
                        "defaults": {
                            "unit": "celsius",
                            "thresholds": [
                                {"value": 0, "color": "blue"},
                                {"value": 25, "color": "green"},
                                {"value": 30, "color": "orange"},
                                {"value": 35, "color": "red"}
                            ],
                            "min": 20,
                            "max": 40
                        }
                    }
                },
                # Panel 3: Umidade Média
                {
                    "id": 3,
                    "title": "Umidade Média",
                    "type": "gauge",
                    "gridPos": {"x": 12, "y": 0, "w": 6, "h": 4},
                    "targets": [{
                        "expr": "avg(app2_summary.avg_humidity)",
                        "legendFormat": "Umidade"
                    }],
                    "fieldConfig": {
                        "defaults": {
                            "unit": "percent",
                            "thresholds": [
                                {"value": 0, "color": "red"},
                                {"value": 50, "color": "orange"},
                                {"value": 70, "color": "green"}
                            ],
                            "min": 0,
                            "max": 100
                        }
                    }
                },
                # Panel 4: Packet Loss
                {
                    "id": 4,
                    "title": "Packet Loss (%)",
                    "type": "graph",
                    "gridPos": {"x": 0, "y": 4, "w": 12, "h": 8},
                    "targets": [
                        {
                            "expr": "avg(app2_summary.packet_loss)",
                            "legendFormat": "Packet Loss",
                            "refId": "A"
                        }
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "unit": "percent",
                            "thresholds": [
                                {"value": 0, "color": "green"},
                                {"value": 5, "color": "orange"},
                                {"value": 10, "color": "red"}
                            ],
                            "custom.lineWidth": 2
                        }
                    },
                    "seriesOverrides": [
                        {"alias": "/.*/", "colors": ["#F2495C"]}
                    ]
                },
                # Panel 5: Temperatura por Sensor
                {
                    "id": 5,
                    "title": "Temperatura por Sensor",
                    "type": "heatmap",
                    "gridPos": {"x": 12, "y": 4, "w": 12, "h": 8},
                    "targets": [{
                        "expr": 'sensor_reading{type="temperature"}',
                        "legendFormat": "Sensor {{sensor_id}}"
                    }],
                    "fieldConfig": {
                        "defaults": {
                            "unit": "celsius",
                            "custom.cellGap": 1,
                            "custom.axisBorderShow": "",
                            "custom.drawMode": "line",
                            "custom.filterable": True
                        }
                    }
                },
                # Panel 6: Alertas
                {
                    "id": 6,
                    "title": "Alertas de Sensores",
                    "type": "table",
                    "gridPos": {"x": 0, "y": 12, "w": 24, "h": 8},
                    "targets": [{
                        "expr": 'count(app2_summary.alerts_count > 0)',
                        "legendFormat": "Alertas Ativos"
                    }]
                },
                # Panel 7: Rede - Tx/Rx Packets
                {
                    "id": 7,
                    "title": "Tráfego de Rede (Tx/Rx)",
                    "type": "graph",
                    "gridPos": {"x": 0, "y": 20, "w": 12, "h": 8},
                    "targets": [
                        {"expr": "sum(app2_summary.tx_packets)", "legendFormat": "TX"},
                        {"expr": "sum(app2_summary.rx_packets)", "legendFormat": "RX"}
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "unit": "short",
                            "custom.lineWidth": 2
                        }
                    }
                },
                # Panel 8: Condutividade do Solo
                {
                    "id": 8,
                    "title": "Condutividade do Solo",
                    "type": "gauge",
                    "gridPos": {"x": 12, "y": 20, "w": 12, "h": 8},
                    "targets": [{
                        "expr": "avg(app2_summary.avg_soil_conductivity)",
                        "legendFormat": "Condutividade"
                    }],
                    "fieldConfig": {
                        "defaults": {
                            "unit": "S",
                            "thresholds": [
                                {"value": 0, "color": "blue"},
                                {"value": 1, "color": "green"},
                                {"value": 2, "color": "orange"}
                            ],
                            "min": 0,
                            "max": 3
                        }
                    }
                }
            ],
            "time": {
                "from": "now-1h",
                "to": "now"
            },
            "refresh": "5s",
            "schemaVersion": 27,
            "version": 1
        },
        "message": "Dashboard App2-Monitoramento criado",
        "overwrite": True
    }
    
    response = requests.post(url, json=dashboard, auth=auth)
    
    if response.status_code in [200, 204]:
        print("✓ Dashboard 'App2 - Monitoramento Ambiental UFPA' criado com sucesso!")
        return True
    else:
        print(f"✗ Erro ao criar dashboard: {response.status_code}")
        print(response.text)
        return False


def influx_target(query: str, ref_id: str = "A", alias: str | None = None) -> dict:
    target = {
        "datasource": "InfluxDB",
        "query": query,
        "rawQuery": True,
        "refId": ref_id,
    }
    if alias:
        target["alias"] = alias
    return target


def thresholds(*steps: tuple[str, float | None]) -> dict:
    return {
        "mode": "absolute",
        "steps": [{"color": color, "value": value} for color, value in steps],
    }


def stat_options(graph_mode: str = "area") -> dict:
    return {
        "colorMode": "value",
        "graphMode": graph_mode,
        "justifyMode": "auto",
        "orientation": "auto",
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        "textMode": "auto",
    }


def create_dashboard():
    """Cria/atualiza dashboard App2 usando InfluxQL no datasource InfluxDB."""

    dashboard = {
        "title": "App2 - Monitoramento Ambiental UFPA",
        "uid": "8NfKIQhDk",
        "tags": ["app2", "greenran", "monitoramento", "sensores", "ufpa"],
        "timezone": "browser",
        "schemaVersion": 27,
        "version": 2,
        "refresh": "5s",
        "time": {"from": "now-1h", "to": "now"},
        "panels": [
            {
                "id": 1,
                "datasource": "InfluxDB",
                "title": "Sensores Ativos",
                "type": "stat",
                "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4},
                "options": stat_options("area"),
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "thresholds": thresholds(("red", None), ("yellow", 10), ("green", 15)),
                        "unit": "short",
                    },
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT last("sensors_active") FROM "app2_summary" WHERE $timeFilter',
                        alias="Sensores ativos",
                    )
                ],
            },
            {
                "id": 2,
                "datasource": "InfluxDB",
                "title": "Temperatura Média",
                "type": "gauge",
                "gridPos": {"x": 6, "y": 0, "w": 6, "h": 4},
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "thresholds": thresholds(("blue", None), ("green", 25), ("orange", 30), ("red", 35)),
                        "unit": "celsius",
                        "min": 20,
                        "max": 40,
                    },
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT last("avg_temperature") FROM "app2_summary" WHERE $timeFilter',
                        alias="Temperatura",
                    )
                ],
            },
            {
                "id": 3,
                "datasource": "InfluxDB",
                "title": "Umidade Média",
                "type": "gauge",
                "gridPos": {"x": 12, "y": 0, "w": 6, "h": 4},
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "thresholds": thresholds(("red", None), ("orange", 50), ("green", 70)),
                        "unit": "percent",
                        "min": 0,
                        "max": 100,
                    },
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT last("avg_humidity") FROM "app2_summary" WHERE $timeFilter',
                        alias="Umidade",
                    )
                ],
            },
            {
                "id": 4,
                "datasource": "InfluxDB",
                "title": "Packet Loss (%)",
                "type": "timeseries",
                "gridPos": {"x": 0, "y": 4, "w": 12, "h": 8},
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "single"},
                },
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "thresholds": thresholds(("green", None), ("orange", 5), ("red", 10)),
                        "unit": "percent",
                    },
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT mean("packet_loss") FROM "app2_summary" WHERE $timeFilter GROUP BY time($__interval) fill(none)',
                        alias="Packet Loss",
                    )
                ],
            },
            {
                "id": 5,
                "datasource": "InfluxDB",
                "title": "Temperatura por Sensor",
                "type": "timeseries",
                "gridPos": {"x": 12, "y": 4, "w": 12, "h": 8},
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "single"},
                },
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}, "unit": "celsius"},
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT mean("value") FROM "sensor_reading" WHERE "type" = \'temperature\' AND $timeFilter GROUP BY time($__interval), "sensor_id" fill(none)',
                        alias="Sensor $tag_sensor_id",
                    )
                ],
            },
            {
                "id": 6,
                "datasource": "InfluxDB",
                "title": "Alertas de Sensores",
                "type": "stat",
                "gridPos": {"x": 0, "y": 12, "w": 6, "h": 5},
                "options": stat_options("none"),
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "thresholds": thresholds(("green", None), ("yellow", 1), ("red", 3)),
                        "unit": "short",
                    },
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT last("alerts_count") FROM "app2_summary" WHERE $timeFilter',
                        alias="Alertas",
                    )
                ],
            },
            {
                "id": 7,
                "datasource": "InfluxDB",
                "title": "Tráfego de Rede (Tx/Rx)",
                "type": "timeseries",
                "gridPos": {"x": 6, "y": 12, "w": 12, "h": 8},
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "single"},
                },
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}, "unit": "short"},
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT mean("tx_packets") FROM "app2_summary" WHERE $timeFilter GROUP BY time($__interval) fill(none)',
                        ref_id="A",
                        alias="TX",
                    ),
                    influx_target(
                        'SELECT mean("rx_packets") FROM "app2_summary" WHERE $timeFilter GROUP BY time($__interval) fill(none)',
                        ref_id="B",
                        alias="RX",
                    ),
                ],
            },
            {
                "id": 8,
                "datasource": "InfluxDB",
                "title": "Condutividade do Solo",
                "type": "gauge",
                "gridPos": {"x": 18, "y": 12, "w": 6, "h": 8},
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "thresholds": thresholds(("blue", None), ("green", 1), ("orange", 2), ("red", 3)),
                        "unit": "dS/m",
                        "min": 0,
                        "max": 3,
                    },
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT last("avg_soil_conductivity") FROM "app2_summary" WHERE $timeFilter',
                        alias="Condutividade",
                    )
                ],
            },
            {
                "id": 9,
                "datasource": "InfluxDB",
                "title": "Bateria Média dos Sensores",
                "type": "gauge",
                "gridPos": {"x": 0, "y": 20, "w": 6, "h": 6},
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "thresholds": thresholds(("red", None), ("orange", 20), ("green", 50)),
                        "unit": "percent",
                        "min": 0,
                        "max": 100,
                    },
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT last("avg_battery_percent") FROM "app2_summary" WHERE $timeFilter',
                        alias="Bateria média",
                    )
                ],
            },
            {
                "id": 10,
                "datasource": "InfluxDB",
                "title": "Latência Média dos Sensores",
                "type": "timeseries",
                "gridPos": {"x": 6, "y": 20, "w": 9, "h": 6},
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "single"},
                },
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "thresholds": thresholds(("green", None), ("orange", 400), ("red", 800)),
                        "unit": "ms",
                    },
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT mean("avg_sensor_latency_ms") FROM "app2_summary" WHERE $timeFilter GROUP BY time($__interval) fill(none)',
                        alias="Latência média",
                    )
                ],
            },
            {
                "id": 11,
                "datasource": "InfluxDB",
                "title": "Sensores por Conectividade",
                "type": "timeseries",
                "gridPos": {"x": 15, "y": 20, "w": 9, "h": 6},
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "single"},
                },
                "fieldConfig": {
                    "defaults": {"color": {"mode": "palette-classic"}, "unit": "short"},
                    "overrides": [],
                },
                "targets": [
                    influx_target(
                        'SELECT count("value") FROM "sensor_reading" WHERE $timeFilter GROUP BY time($__interval), "connectivity" fill(none)',
                        alias="Modo $tag_connectivity",
                    )
                ],
            },
        ],
    }

    payload = {
        "dashboard": dashboard,
        "folderId": 0,
        "message": "Dashboard App2-Monitoramento atualizado para InfluxQL",
        "overwrite": True,
    }
    url = f"http://{GRAFANA_HOST}:{GRAFANA_PORT}/api/dashboards/db"

    for attempt in range(1, POST_RETRIES + 1):
        try:
            response = requests.post(
                url,
                json=payload,
                auth=(ADMIN_USER, ADMIN_PASS),
                timeout=API_TIMEOUT_S,
            )
            if response.status_code in (200, 202):
                print("[SUCCESS] Dashboard App2 atualizado no Grafana")
                print(f"[INFO] URL: http://{GRAFANA_HOST}:{GRAFANA_PORT}/d/8NfKIQhDk/app2-monitoramento-ambiental-ufpa")
                return True

            print(f"[WARN] Tentativa {attempt}/{POST_RETRIES} falhou com HTTP {response.status_code}")
            if response.text:
                print(response.text)
        except requests.RequestException as exc:
            print(f"[WARN] Tentativa {attempt}/{POST_RETRIES} falhou: {exc}")

        if attempt < POST_RETRIES:
            time.sleep(POST_RETRY_SLEEP_S)

    print("[ERROR] Não foi possível atualizar o dashboard App2 no Grafana")
    return False


if __name__ == "__main__":
    sys.exit(0 if create_dashboard() else 1)
