#!/usr/bin/env python3
"""
GreenRAN DRL Dashboard - Manual Import
====================================
Creates dashboard DRL for manual import into Grafana.
Exports to JSON file that can be imported via UI.
"""

import json

DASHBOARD = {
    "dashboard": {
        "id": 1,
        "uid": "drl-greenran",
        "title": "DRL - SBiLSTM + A3C",
        "tags": ["greenran", "drl"],
        "timezone": "browser",
        "schemaVersion": 16,
        "version": 1,
        "refresh": "5s",
        "panels": [
            {
                "id": 1,
                "title": "DRL CVaR Prediction",
                "type": "timeseries",
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                "targets": [{
                    "refId": "A",
                    "query": "SELECT time, cvar FROM drl_prediction ORDER BY time DESC",
                    "datasourceId": 1
                }]
            },
            {
                "id": 2,
                "title": "Latest Decision",
                "type": "stat", 
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
                "targets": [{
                    "refId": "A", 
                    "query": "SELECT last(decision) as decision, last(power) as power FROM drl_prediction",
                    "datasourceId": 1
                }]
            },
            {
                "id": 3,
                "title": "Decision Count",
                "type": "bargauge",
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8},
                "targets": [{
                    "refId": "A",
                    "query": "SELECT count(decision) as count FROM drl_prediction GROUP BY decision",
                    "datasourceId": 1
                }]
            },
            {
                "id": 4,
                "title": "Power Distribution", 
                "type": "piechart",
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 8},
                "targets": [{
                    "refId": "A",
                    "query": "SELECT count(power) as power_count FROM drl_prediction GROUP BY power",
                    "datasourceId": 1
                }]
            }
        ],
        "templating": {"list": []},
        "time": {"from": "now-1h", "to": "now"},
        "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m"]}
    }
}

# Save to file
output_file = "/home/robert/orange_nuclear/push/drl_dashboard_import.json"
with open(output_file, 'w') as f:
    json.dump(DASHBOARD, f, indent=2)

print(f"[SUCCESS] Dashboard JSON saved to: {output_file}")
print(f"\nTo import into Grafana:")
print(f"1. Go to http://localhost:3001")
print(f"2. Dashboards → Import")
print(f"3. Upload {output_file}")
print(f"4. Set datasource to 'influx'")
print(f"5. Click Import")