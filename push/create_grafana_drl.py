#!/usr/bin/env python3
"""
GreenRAN DRL Dashboard Creator for Grafana
===========================================
Creates DRL dashboard with panels for:
- DRL CVaR Prediction (SBiLSTM)
- DRL Decisions (A3C)
- DRL Power

Usage:
    python3 create_grafana_drl.py
"""

import requests
import json
import sys

GRAFANA_URL = "http://localhost:3001"
GRAFANA_USER = "admin"
GRAFANA_PASSWORD = "admin"
INFLUX_DB = "influx"

# Dashboard JSON
DASHBOARD_JSON = {
    "dashboard": {
        "id": None,
        "uid": "drl-greenran",
        "title": "DRL - SBiLSTM + A3C",
        "tags": ["greenran", "drl"],
        "timezone": "browser",
        "schemaVersion": 16,
        "version": 0,
        "refresh": "5s",
        "panels": [
            # Panel 1: DRL CVaR Prediction
            {
                "id": 1,
                "title": "DRL CVaR Prediction (SBiLSTM)",
                "type": "timeseries",
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                "targets": [{
                    "refId": "A",
                    "query": f"SELECT time, cvar FROM drl_prediction ORDER BY time DESC"
                }],
                "fieldConfig": {
                    "defaults": {
                        "unit": "ms",
                        "custom": {
                            "lineWidth": 2,
                            "fillOpacity": 10,
                            "gradientMode": "opacity"
                        }
                    }
                }
            },
            # Panel 2: DRL Decisions
            {
                "id": 2,
                "title": "DRL Decisions (A3C)",
                "type": "bargauge",
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
                "targets": [{
                    "refId": "A",
                    "query": f"SELECT last(decision) FROM drl_prediction GROUP BY decision"
                }],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "custom": {
                            "lineWidth": 2
                        }
                    }
                }
            },
            # Panel 3: DRL Power
            {
                "id": 3,
                "title": "DRL Power",
                "type": "stat",
                "gridPos": {"h": 6, "w": 12, "x": 0, "y": 8},
                "targets": [{
                    "refId": "A",
                    "query": f"SELECT last(power) FROM drl_prediction"
                }]
            },
            # Panel 4: DRL Stats Summary
            {
                "id": 4,
                "title": "DRL Statistics",
                "type": "table",
                "gridPos": {"h": 6, "w": 12, "x": 12, "y": 8},
                "targets": [{
                    "refId": "A",
                    "query": f"SELECT count(cvar) as total, avg(cvar) as avg_cvar, min(cvar) as min_cvar, max(cvar) as max_cvar FROM drl_prediction"
                }]
            },
            # Panel 5: Decision Distribution
            {
                "id": 5,
                "title": "Decision Distribution",
                "type": "piechart",
                "gridPos": {"h": 8, "w": 8, "x": 0, "y": 14},
                "targets": [{
                    "refId": "A",
                    "query": f"SELECT count(decision) FROM drl_prediction GROUP BY decision"
                }]
            },
            # Panel 6: Power Distribution
            {
                "id": 6,
                "title": "Power Distribution",
                "type": "piechart",
                "gridPos": {"h": 8, "w": 8, "x": 8, "y": 14},
                "targets": [{
                    "refId": "A",
                    "query": f"SELECT count(power) FROM drl_prediction GROUP BY power"
                }]
            },
            # Panel 7: CVaR History
            {
                "id": 7,
                "title": "DRL CVaR History (24h)",
                "type": "heatmap",
                "gridPos": {"h": 8, "w": 8, "x": 16, "y": 14},
                "targets": [{
                    "refId": "A",
                    "query": f"SELECT mean(cvar) FROM drl_prediction GROUP BY time(1h)"
                }]
            }
        ],
        "templating": {
            "list": []
        },
        "time": {
            "from": "now-1h",
            "to": "now"
        },
        "timepicker": {
            "refresh_intervals": ["5s", "10s", "30s", "1m", "5m", "15m"]
        }
    },
    "message": "DRL Dashboard created",
    "overwrite": True
}


def create_dashboard():
    """Create DRL dashboard in Grafana."""
    
    url = f"{GRAFANA_URL}/api/dashboards/db"
    
    # Check if dashboard exists
    check_url = f"{GRAFANA_URL}/api/dashboards/uid/drl-greenran"
    try:
        response = requests.get(check_url, auth=(GRAFANA_USER, GRAFANA_PASSWORD))
        if response.status_code == 200:
            print("[Grafana] Dashboard DRL already exists - will overwrite")
    except:
        pass
    
    # Create/Update dashboard
    try:
        response = requests.post(
            url,
            json=DASHBOARD_JSON,
            auth=(GRAFANA_USER, GRAFANA_PASSWORD),
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code in [200, 201]:
            print("[SUCCESS] DRL Dashboard created!")
            print(f"[URL] {GRAFANA_URL}/d/drl-greenran/drl-sbilstm-a3c")
            return True
        else:
            print(f"[ERROR] Status: {response.status_code}")
            print(f"[ERROR] {response.text}")
            return False
            
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("  GreenRAN - DRL Dashboard Creator")
    print("=" * 60)
    print(f"  Grafana: {GRAFANA_URL}")
    print("=" * 60)
    
    success = create_dashboard()
    
    if success:
        print("\n" + "=" * 60)
        print("  ✅ DRL Dashboard created successfully!")
        print("=" * 60)
        print(f"\n  Access: {GRAFANA_URL}/d/drl-greenran/drl-sbilstm-a3c")
    else:
        print("\n[ERROR] Failed to create dashboard")
        sys.exit(1)