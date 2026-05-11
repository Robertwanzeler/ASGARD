#!/usr/bin/env python3
"""
GreenRAN - App3-Veicular: Serviços
==================================
Serviços para estado veicular baseado no pipeline CARLA + ns-3.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

VEHICLE_LATENCY_WARNING_MS = 50.0
VEHICLE_LATENCY_CRITICAL_MS = 100.0
VEHICLE_PACKET_LOSS_WARNING_PERCENT = 2.0
VEHICLE_PACKET_LOSS_CRITICAL_PERCENT = 5.0


def _safe_read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def evaluate_vehicle_sla(summary: Dict[str, Any]) -> Dict[str, Any]:
    total_vehicles = int(summary.get("total_vehicles", 0) or 0)
    if total_vehicles <= 0:
        return {
            "ready": False,
            "runtime_status": "idle",
            "proposal_status": "idle",
            "reason": "Nenhum veiculo ativo no snapshot.",
        }

    high_risk = int(summary.get("high_risk_vehicles", 0) or 0)
    medium_risk = int(summary.get("medium_risk_vehicles", 0) or 0)
    degraded = int(summary.get("degraded_autonomy_vehicles", 0) or 0)
    max_latency = float(summary.get("max_latency_ms", 0.0) or 0.0)
    max_packet_loss = float(summary.get("max_packet_loss_percent", 0.0) or 0.0)

    critical_reasons = []
    warning_reasons = []

    if high_risk > 0:
        critical_reasons.append(f"veículos em risco alto={high_risk}")
    if degraded > 0:
        critical_reasons.append(f"autonomia degradada em {degraded} veículo(s)")
    if max_latency >= VEHICLE_LATENCY_CRITICAL_MS:
        critical_reasons.append(f"latência veicular {max_latency:.0f}ms >= 100ms")
    elif max_latency >= VEHICLE_LATENCY_WARNING_MS:
        warning_reasons.append(f"latência veicular {max_latency:.0f}ms >= 50ms")
    if max_packet_loss >= VEHICLE_PACKET_LOSS_CRITICAL_PERCENT:
        critical_reasons.append(f"packet loss veicular {max_packet_loss:.1f}% >= 5%")
    elif max_packet_loss >= VEHICLE_PACKET_LOSS_WARNING_PERCENT:
        warning_reasons.append(f"packet loss veicular {max_packet_loss:.1f}% >= 2%")
    if medium_risk > 0 and not critical_reasons:
        warning_reasons.append(f"veículos em risco médio={medium_risk}")

    if critical_reasons:
        return {
            "ready": True,
            "runtime_status": "blocked",
            "proposal_status": "violation",
            "reason": critical_reasons[0],
        }
    if warning_reasons:
        return {
            "ready": True,
            "runtime_status": "warning",
            "proposal_status": "warning",
            "reason": warning_reasons[0],
        }
    return {
        "ready": True,
        "runtime_status": "ok",
        "proposal_status": "ok",
        "reason": "SLA veicular atendido para risco, autonomia e rede.",
    }


class GreenRANContextReader:
    """Le contexto exportado do GreenRAN."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.metrics_file = state_dir / "xapp_metrics" / "metrics.json"

    def get_context(self) -> Dict[str, Any]:
        return _safe_read_json(self.metrics_file, {})


class VehicleStateStore:
    """Le e materializa o estado veicular derivado do pipeline combinado."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.app_state_dir = self.state_dir / "app3_veicular"
        self.snapshot_path = self.app_state_dir / "monitoring_snapshot.json"
        self.vehicles_path = self.app_state_dir / "vehicles" / "latest.json"
        self.events_path = self.app_state_dir / "events" / "latest.json"
        self.extended_metrics_path = self.state_dir / "xapp_metrics" / "extended_metrics.json"
        self.app_state_dir.mkdir(parents=True, exist_ok=True)
        self.vehicles_path.parent.mkdir(parents=True, exist_ok=True)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_json(self, path: Path, payload: Any) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp_path.replace(path)

    def _read_extended_metrics(self) -> Dict[str, Any]:
        data = _safe_read_json(self.extended_metrics_path, {})
        return data if isinstance(data, dict) else {}

    def list_vehicles(self) -> List[Dict[str, Any]]:
        data = self._read_extended_metrics()
        ue_metrics = data.get("ue_metrics", {}) if isinstance(data, dict) else {}
        vehicles = []
        for imsi, metrics in ue_metrics.items():
            if metrics.get("device_type") != "vehicle":
                continue
            latency_ms = float(metrics.get("latency_us", 0.0) or 0.0) / 1000.0
            throughput_mbps = float(
                metrics.get("rx_throughput_kbps", metrics.get("throughput_kbps", 0.0)) or 0.0
            ) / 1000.0
            record = {
                "imsi": str(imsi),
                "vehicle_id": metrics.get("vehicle_id", f"veh-{imsi}"),
                "vehicle_role": metrics.get("vehicle_role", "traffic"),
                "is_ego": metrics.get("vehicle_role") == "ego",
                "latency_ms": round(latency_ms, 3),
                "packet_loss_percent": round(float(metrics.get("packet_loss_percent", 0.0) or 0.0), 3),
                "throughput_mbps": round(throughput_mbps, 3),
                "speed_mps": round(float(metrics.get("speed_mps", 0.0) or 0.0), 3),
                "heading_deg": round(float(metrics.get("heading_deg", 0.0) or 0.0), 3),
                "lane_id": metrics.get("lane_id"),
                "waypoint_id": metrics.get("waypoint_id"),
                "autonomy_state": metrics.get("autonomy_state", "normal"),
                "risk_state": metrics.get("risk_state", "low"),
                "position": metrics.get("position", {}),
                "cell_id": metrics.get("cell_id"),
                "gateway_id": metrics.get("gateway_id"),
                "connectivity": metrics.get("connectivity"),
                "domain": metrics.get("domain"),
                "mobility_profile": metrics.get("mobility_profile"),
                "timestamp_iso": data.get("timestamp_iso"),
            }
            vehicles.append(record)
        vehicles.sort(key=lambda item: (not item.get("is_ego", False), item.get("vehicle_id", "")))
        return vehicles

    def get_ego_vehicle(self) -> Optional[Dict[str, Any]]:
        for vehicle in self.list_vehicles():
            if vehicle.get("is_ego"):
                return vehicle
        return None

    def summarize(self, vehicles: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not vehicles:
            return {
                "total_vehicles": 0,
                "ego_present": False,
                "high_risk_vehicles": 0,
                "medium_risk_vehicles": 0,
                "degraded_autonomy_vehicles": 0,
                "max_latency_ms": 0.0,
                "max_packet_loss_percent": 0.0,
                "max_speed_mps": 0.0,
            }
        high_risk = sum(1 for item in vehicles if item.get("risk_state") == "high")
        medium_risk = sum(1 for item in vehicles if item.get("risk_state") == "medium")
        degraded = sum(1 for item in vehicles if item.get("autonomy_state") != "normal")
        return {
            "total_vehicles": len(vehicles),
            "ego_present": any(item.get("is_ego") for item in vehicles),
            "high_risk_vehicles": high_risk,
            "medium_risk_vehicles": medium_risk,
            "degraded_autonomy_vehicles": degraded,
            "max_latency_ms": round(max(item.get("latency_ms", 0.0) for item in vehicles), 3),
            "max_packet_loss_percent": round(max(item.get("packet_loss_percent", 0.0) for item in vehicles), 3),
            "max_speed_mps": round(max(item.get("speed_mps", 0.0) for item in vehicles), 3),
        }

    def build_events(self, vehicles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = time.time()
        events: List[Dict[str, Any]] = []
        for vehicle in vehicles:
            if vehicle.get("risk_state") == "high":
                events.append(
                    {
                        "event_type": "vehicle_critical",
                        "severity": "critical",
                        "vehicle_id": vehicle.get("vehicle_id"),
                        "reason": "risk_state=high",
                        "timestamp": now,
                        "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                    }
                )
            elif vehicle.get("risk_state") == "medium":
                events.append(
                    {
                        "event_type": "vehicle_warning",
                        "severity": "warning",
                        "vehicle_id": vehicle.get("vehicle_id"),
                        "reason": "risk_state=medium",
                        "timestamp": now,
                        "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                    }
                )
            if float(vehicle.get("latency_ms", 0.0) or 0.0) >= VEHICLE_LATENCY_CRITICAL_MS:
                events.append(
                    {
                        "event_type": "vehicle_critical",
                        "severity": "critical",
                        "vehicle_id": vehicle.get("vehicle_id"),
                        "reason": f"latência {vehicle['latency_ms']:.0f}ms >= 100ms",
                        "timestamp": now,
                        "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                    }
                )
            elif float(vehicle.get("latency_ms", 0.0) or 0.0) >= VEHICLE_LATENCY_WARNING_MS:
                events.append(
                    {
                        "event_type": "vehicle_warning",
                        "severity": "warning",
                        "vehicle_id": vehicle.get("vehicle_id"),
                        "reason": f"latência {vehicle['latency_ms']:.0f}ms >= 50ms",
                        "timestamp": now,
                        "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                    }
                )
        return events

    def refresh_snapshot(self) -> Dict[str, Any]:
        vehicles = self.list_vehicles()
        summary = self.summarize(vehicles)
        events = self.build_events(vehicles)
        snapshot = {
            "simulation": {
                "source": "carla_ns3_combined",
                "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            },
            "vehicles": summary,
            "network": {
                "max_latency_ms": summary.get("max_latency_ms", 0.0),
                "max_packet_loss_percent": summary.get("max_packet_loss_percent", 0.0),
                "max_speed_mps": summary.get("max_speed_mps", 0.0),
            },
            "sla": evaluate_vehicle_sla(summary),
            "events_count": len(events),
        }
        self._write_json(self.snapshot_path, snapshot)
        self._write_json(self.vehicles_path, vehicles)
        self._write_json(self.events_path, events)
        return snapshot

    def get_snapshot(self) -> Dict[str, Any]:
        snapshot = _safe_read_json(self.snapshot_path, {})
        if snapshot:
            return snapshot
        return self.refresh_snapshot()

    def list_events(self) -> List[Dict[str, Any]]:
        data = _safe_read_json(self.events_path, [])
        return data if isinstance(data, list) else []
