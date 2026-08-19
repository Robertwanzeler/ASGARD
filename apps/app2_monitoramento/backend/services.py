#!/usr/bin/env python3
"""
GreenRAN - App2-Monitoramento: Serviços
=====================================
Serviços para monitoramento de sensores IoT.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

APP2_CONNECTED_CRITICAL_RATIO = 0.85
APP2_CONNECTED_WARNING_RATIO = 0.90
APP2_CONNECTED_GUARD_RATIO = 0.95
APP2_PACKET_LOSS_CRITICAL_PERCENT = 10.0
APP2_PACKET_LOSS_WARNING_PERCENT = 5.0
APP2_DELIVERY_CRITICAL_PERCENT = 90.0
APP2_DELIVERY_WARNING_PERCENT = 95.0
APP2_LATENCY_CRITICAL_MS = 1000.0
APP2_LATENCY_WARNING_MS = 500.0
APP2_BATTERY_CRITICAL_PERCENT = 15.0
APP2_BATTERY_WARNING_PERCENT = 25.0
APP2_ERROR_CRITICAL_RATIO = 0.20
APP2_ERROR_WARNING_COUNT = 2


def _safe_read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


class SensorEventStore:
    """Persistência simples de leituras de sensores."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.app_state_dir = self.state_dir / "app2_monitoramento"
        self.readings_file = self.app_state_dir / "readings.json"
        self.alerts_file = self.app_state_dir / "alerts.json"
        self.app_state_dir.mkdir(parents=True, exist_ok=True)
        
        if not self.readings_file.exists():
            self._write_json(self.readings_file, [])
        if not self.alerts_file.exists():
            self._write_json(self.alerts_file, [])

    def _write_json(self, path: Path, data: Any) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _read_json(self, path: Path) -> Any:
        return _safe_read_json(path, [])

    def list_readings(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        readings = sorted(
            self._read_json(self.readings_file),
            key=lambda item: item.get("timestamp", 0),
            reverse=True
        )
        return readings[:limit] if limit is not None else readings

    def list_alerts(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        alerts = sorted(
            self._read_json(self.alerts_file),
            key=lambda item: item.get("timestamp", 0),
            reverse=True
        )
        return alerts[:limit] if limit is not None else alerts

    def add_reading(self, reading: Dict[str, Any]) -> Dict[str, Any]:
        readings = self.list_readings(limit=100)
        sensor_type = reading.get("sensor_type") or reading.get("type", "unknown")
        record = {
            "id": reading.get("sensor_id", 0),
            "sensor_id": reading.get("sensor_id", 0),
            "type": sensor_type,
            "sensor_type": sensor_type,
            "value": reading.get("value", 0),
            "unit": reading.get("unit", ""),
            "location": reading.get("location", "UFPA-Campus-Belem"),
            "source": reading.get("source", "api"),
            "timestamp": reading.get("timestamp", time.time()),
            "status": reading.get("status", "ok")
        }
        readings.insert(0, record)
        self._write_json(self.readings_file, readings[:1000])
        return record

    def add_alert(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        alerts = self.list_alerts(limit=100)
        record = {
            "level": alert.get("level", "info"),
            "message": alert.get("message", ""),
            "sensor_id": alert.get("sensor_id"),
            "sensor_type": alert.get("sensor_type"),
            "value": alert.get("value"),
            "timestamp": alert.get("timestamp", time.time())
        }
        alerts.insert(0, record)
        self._write_json(self.alerts_file, alerts[:500])
        return record


class GreenRANContextReader:
    """Lê contexto do GreenRAN."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.metrics_file = state_dir / "xapp_metrics" / "metrics.json"

    def get_context(self) -> Dict[str, Any]:
        return _safe_read_json(self.metrics_file, {})


def evaluate_app2_sla(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    sensors = snapshot.get("sensors", {}) if isinstance(snapshot, dict) else {}
    readings = snapshot.get("readings", {}) if isinstance(snapshot, dict) else {}
    network = snapshot.get("network", {}) if isinstance(snapshot, dict) else {}

    total_sensors = int(sensors.get("total", 0) or 0)
    connected_sensors = int(sensors.get("connected", 0) or 0)
    error_sensors = int(sensors.get("error", 0) or 0)
    low_battery_sensors = int(sensors.get("low_battery", 0) or 0)

    connected_ratio = (connected_sensors / total_sensors) if total_sensors > 0 else 0.0
    error_ratio = (error_sensors / total_sensors) if total_sensors > 0 else 0.0
    packet_loss = float(network.get("packet_loss_percent", 0.0) or 0.0)
    delivery_success = float(network.get("delivery_success_percent", 100.0) or 0.0)
    avg_latency_ms = float(network.get("avg_latency_ms", 0.0) or 0.0)
    avg_battery_percent = float(readings.get("avg_battery_percent", 0.0) or 0.0)

    targets = {
        "connected_warning_ratio": APP2_CONNECTED_WARNING_RATIO,
        "connected_critical_ratio": APP2_CONNECTED_CRITICAL_RATIO,
        "packet_loss_warning_percent": APP2_PACKET_LOSS_WARNING_PERCENT,
        "packet_loss_critical_percent": APP2_PACKET_LOSS_CRITICAL_PERCENT,
        "delivery_warning_percent": APP2_DELIVERY_WARNING_PERCENT,
        "delivery_critical_percent": APP2_DELIVERY_CRITICAL_PERCENT,
        "latency_warning_ms": APP2_LATENCY_WARNING_MS,
        "latency_critical_ms": APP2_LATENCY_CRITICAL_MS,
        "battery_warning_percent": APP2_BATTERY_WARNING_PERCENT,
        "battery_critical_percent": APP2_BATTERY_CRITICAL_PERCENT,
        "error_warning_count": APP2_ERROR_WARNING_COUNT,
        "error_critical_ratio": APP2_ERROR_CRITICAL_RATIO,
    }
    observed = {
        "total_sensors": total_sensors,
        "connected_sensors": connected_sensors,
        "connected_ratio": round(connected_ratio, 4),
        "error_sensors": error_sensors,
        "error_ratio": round(error_ratio, 4),
        "low_battery_sensors": low_battery_sensors,
        "packet_loss_percent": round(packet_loss, 2),
        "delivery_success_percent": round(delivery_success, 2),
        "avg_latency_ms": round(avg_latency_ms, 2),
        "avg_battery_percent": round(avg_battery_percent, 2),
    }

    if total_sensors <= 0:
        return {
            "ready": False,
            "targets": targets,
            "observed": observed,
            "proposal_status": "idle",
            "runtime_status": "idle",
            "reason": "Nenhum sensor ativo no snapshot.",
        }

    critical_reasons = []
    warning_reasons = []

    if connected_ratio < APP2_CONNECTED_CRITICAL_RATIO:
        critical_reasons.append(f"conectividade {connected_ratio:.0%} < 85%")
    elif connected_ratio < APP2_CONNECTED_WARNING_RATIO:
        warning_reasons.append(f"conectividade {connected_ratio:.0%} < 90%")
    elif connected_ratio < APP2_CONNECTED_GUARD_RATIO:
        warning_reasons.append(f"conectividade {connected_ratio:.0%} < 95%")

    if packet_loss >= APP2_PACKET_LOSS_CRITICAL_PERCENT:
        critical_reasons.append(f"packet loss {packet_loss:.1f}% >= 10%")
    elif packet_loss >= APP2_PACKET_LOSS_WARNING_PERCENT:
        warning_reasons.append(f"packet loss {packet_loss:.1f}% >= 5%")

    if delivery_success < APP2_DELIVERY_CRITICAL_PERCENT:
        critical_reasons.append(f"entrega {delivery_success:.1f}% < 90%")
    elif delivery_success < APP2_DELIVERY_WARNING_PERCENT:
        warning_reasons.append(f"entrega {delivery_success:.1f}% < 95%")

    if avg_latency_ms >= APP2_LATENCY_CRITICAL_MS:
        critical_reasons.append(f"latência média {avg_latency_ms:.0f}ms >= 1000ms")
    elif avg_latency_ms >= APP2_LATENCY_WARNING_MS:
        warning_reasons.append(f"latência média {avg_latency_ms:.0f}ms >= 500ms")

    if avg_battery_percent < APP2_BATTERY_CRITICAL_PERCENT:
        critical_reasons.append(f"bateria média {avg_battery_percent:.1f}% < 15%")
    elif avg_battery_percent < APP2_BATTERY_WARNING_PERCENT or low_battery_sensors > 0:
        warning_reasons.append(f"bateria média {avg_battery_percent:.1f}% / baixa={low_battery_sensors}")

    if error_ratio >= APP2_ERROR_CRITICAL_RATIO:
        critical_reasons.append(f"sensores em erro {error_ratio:.0%} >= 20%")
    elif error_sensors >= APP2_ERROR_WARNING_COUNT:
        warning_reasons.append(f"sensores em erro={error_sensors}")

    if critical_reasons:
        runtime_status = "blocked"
        proposal_status = "violation"
        reason = critical_reasons[0]
    elif warning_reasons:
        runtime_status = "warning"
        proposal_status = "warning"
        reason = warning_reasons[0]
    else:
        runtime_status = "ok"
        proposal_status = "ok"
        reason = "SLA mMTC atendido para conectividade, entrega, latência e bateria."

    return {
        "ready": True,
        "targets": targets,
        "observed": observed,
        "proposal_status": proposal_status,
        "runtime_status": runtime_status,
        "reason": reason,
    }


def summarize(readings: List[Dict], alerts: List[Dict], context: Dict) -> Dict:
    """Resume leituras e alertas."""
    network_context = context.get("network", {}) if isinstance(context, dict) else {}
    global_metrics = context.get("global_metrics", {}) if isinstance(context, dict) else {}
    policies = context.get("policies", {}) if isinstance(context, dict) else {}

    network = {
        "avg_latency_ms": round(float(
            network_context.get("avg_latency_ms", global_metrics.get("global_avg_latency_us", 0) / 1000.0)
        ), 2),
        "packet_loss_rate": network_context.get(
            "packet_loss_rate",
            global_metrics.get("global_packet_loss_rate", 0),
        ),
        "throughput_mbps": round(float(
            network_context.get("throughput_mbps", global_metrics.get("throughput_kbps", 0) / 1000.0)
        ), 2),
        "active_ues": int(network_context.get("active_ues", global_metrics.get("total_active_ues", 0) or 0)),
    }
    summary = {
        "total_readings": len(readings),
        "active_sensors": len(set(r.get("sensor_id", r.get("id")) for r in readings)),
        "avg_temperature": 0,
        "avg_humidity": 0,
        "alerts_count": len(alerts),
        "total_alerts": len(alerts),
        "critical_alerts": sum(1 for alert in alerts if alert.get("level") == "critical"),
        "network_context": bool(context),
        "network": network,
        "policies": {
            "slice_state": policies.get("slice_state", "UNKNOWN"),
            "energy_status": policies.get("energy_status", "UNKNOWN"),
        },
    }
    if not readings:
        return summary

    temperature = [r.get("value", 0) for r in readings if r.get("type") == "temperature"]
    humidity = [r.get("value", 0) for r in readings if r.get("type") == "humidity"]
    summary["avg_temperature"] = sum(temperature) / len(temperature) if temperature else 0
    summary["avg_humidity"] = sum(humidity) / len(humidity) if humidity else 0
    return summary


def evaluate_reading(value: float, sensor_type: str) -> Dict[str, Any]:
    """Avalia uma leitura de sensor."""
    thresholds = {
        "temperature": {"min": 20, "max": 35, "warning": 32, "critical": 35},
        "humidity": {"min": 60, "max": 95, "warning": 90, "critical": 95},
        "soil_moisture": {"min": 20, "max": 80, "warning": 75, "critical": 80},
        "soil_temp": {"min": 22, "max": 32, "warning": 30, "critical": 32},
        "soil_conductivity": {"min": 0.1, "max": 2.5, "warning": 2.0, "critical": 2.5},
        "air_quality": {"min": 50, "max": 100, "warning": 70, "critical": 50},
    }

    th = thresholds.get(sensor_type, {})
    if not th:
        return {"status": "ok", "level": "info", "message": ""}

    if sensor_type == "air_quality" and value <= th.get("critical", 0):
        return {"status": "critical", "level": "critical", "message": f"Qualidade do ar crítica: {value}"}
    if value >= th.get("critical", 100):
        return {"status": "critical", "level": "critical", "message": f"Valor crítico: {value}"}
    elif value >= th.get("warning", 100):
        return {"status": "warning", "level": "warning", "message": f"Valor elevado: {value}"}
    elif value < th.get("min", 0):
        return {"status": "low", "level": "warning", "message": f"Valor baixo: {value}"}
    else:
        return {"status": "ok", "level": "info", "message": ""}
