#!/usr/bin/env python3

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


class App2ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="greenran_app2_test_")
        os.environ["GREENRAN_STATE_DIR"] = self.temp_dir

        project_root = Path(__file__).resolve().parents[3]
        backend_dir = project_root / "apps" / "app2_monitoramento" / "backend"
        src_dir = project_root / "src"

        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))

        for module_name in [
            "greenran_paths",
            "greenran_runtime",
            "services",
            "app",
        ]:
            if module_name in sys.modules:
                del sys.modules[module_name]

        self.app_module = importlib.import_module("app")
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("GREENRAN_STATE_DIR", None)
        os.environ.pop("GREENRAN_API_TOKEN", None)

    def _write_monitoring_snapshot(self, payload: dict) -> None:
        app_dir = Path(self.temp_dir) / "app2_monitoramento"
        app_dir.mkdir(parents=True, exist_ok=True)
        with open(app_dir / "monitoring_snapshot.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def test_health_endpoint(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["app"], "app2_monitoramento")

    def test_create_mock_reading(self):
        response = self.client.post(
            "/api/readings/mock",
            json={
                "sensor_id": "SOIL-01",
                "sensor_type": "soil_moisture",
                "value": 10.0,
                "unit": "%",
            },
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["reading"]["sensor_id"], "SOIL-01")
        self.assertIsNotNone(payload["alert"])
        self.assertEqual(payload["alert"]["level"], "warning")

    def test_mutation_requires_token_for_non_loopback_client(self):
        response = self.client.post(
            "/api/readings/mock",
            json={"sensor_id": "REMOTE-01", "type": "soil_moisture", "value": 10.0},
            environ_base={"REMOTE_ADDR": "192.0.2.10"},
        )
        self.assertEqual(response.status_code, 503)

    def test_mutation_accepts_bearer_token_from_environment(self):
        os.environ["GREENRAN_API_TOKEN"] = "test-token"
        response = self.client.post(
            "/api/readings/mock",
            json={"sensor_id": "REMOTE-02", "type": "soil_moisture", "value": 10.0},
            headers={"Authorization": "Bearer test-token"},
            environ_base={"REMOTE_ADDR": "192.0.2.10"},
        )
        self.assertEqual(response.status_code, 201)

    def test_report_endpoint_returns_default_structure(self):
        response = self.client.get("/api/report")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("window_hours", payload)
        self.assertIn("samples", payload)

    def test_history_endpoint_returns_list(self):
        response = self.client.get("/api/history?hours=24&limit=5")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("history", payload)
        self.assertIsInstance(payload["history"], list)

    def test_monitoring_snapshot_exposes_explicit_sla_margin(self):
        self._write_monitoring_snapshot(
            {
                "timestamp": "2026-04-28T12:00:00",
                "sensors": {
                    "total": 10,
                    "active": 10,
                    "connected": 10,
                    "error": 1,
                    "low_battery": 1,
                    "gateways": ["GW-01", "GW-02"],
                },
                "readings": {
                    "avg_battery_percent": 23.5,
                    "avg_power_mw": 118.0,
                },
                "network": {
                    "packet_loss_percent": 5.8,
                    "avg_latency_ms": 540.0,
                    "delivery_success_percent": 94.2,
                    "tx_packets": 300,
                    "rx_packets": 282,
                },
                "alerts": [],
            }
        )

        response = self.client.get("/api/monitoring")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("app2_sla", payload)
        self.assertEqual(payload["app2_sla"]["proposal_status"], "warning")
        self.assertEqual(payload["app2_sla"]["runtime_status"], "warning")
        self.assertIn("packet loss", payload["app2_sla"]["reason"])

        sla_response = self.client.get("/api/sla")
        self.assertEqual(sla_response.status_code, 200)
        sla_payload = sla_response.get_json()
        self.assertEqual(sla_payload["observed"]["connected_sensors"], 10)
        self.assertEqual(sla_payload["runtime_status"], "warning")


if __name__ == "__main__":
    unittest.main()
