#!/usr/bin/env python3
"""
Primitivas compartilhadas do domínio veicular.

Usado tanto pelo rApp quanto pelo xApp VehicleControl para evitar
divergência entre a classificação offline/online do App3.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from greenran_paths import EXTENDED_METRICS_JSON_PATH, STATE_DIR, as_str


EXTENDED_METRICS_PATH = as_str(EXTENDED_METRICS_JSON_PATH)
APP3_MONITORING_PATH = as_str(STATE_DIR / "app3_veicular" / "monitoring_snapshot.json")


def safe_read_json_file(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_vehicle_metrics(
    extended_metrics_path: str = EXTENDED_METRICS_PATH,
    monitoring_path: str = APP3_MONITORING_PATH,
    stale_after_s: float = 20.0,
) -> dict[str, Any]:
    metrics = {
        "available": False,
        "stale": False,
        "age_seconds": None,
        "total_vehicles": 0,
        "ego_present": False,
        "high_risk_vehicles": 0,
        "medium_risk_vehicles": 0,
        "degraded_autonomy_vehicles": 0,
        "max_latency_ms": 0.0,
        "max_packet_loss_percent": 0.0,
        "max_speed_mps": 0.0,
        "vehicles": [],
    }

    try:
        extended = safe_read_json_file(extended_metrics_path)
        ue_metrics = (extended.get("ue_metrics", {}) or {}) if isinstance(extended, dict) else {}

        vehicle_entries = []
        for imsi, ue_data in ue_metrics.items():
            if not isinstance(ue_data, dict) or ue_data.get("device_type") != "vehicle":
                continue
            vehicle_entries.append(
                {
                    "imsi": imsi,
                    "vehicle_id": ue_data.get("vehicle_id", f"veh-imsi-{imsi}"),
                    "vehicle_role": ue_data.get("vehicle_role", "traffic"),
                    "autonomy_state": ue_data.get("autonomy_state", "unknown"),
                    "risk_state": ue_data.get("risk_state", "unknown"),
                    "latency_ms": float(ue_data.get("latency_avg_us", ue_data.get("latency_us", 0)) or 0) / 1000.0,
                    "packet_loss_percent": float(
                        ue_data.get(
                            "packet_loss_percent",
                            float(extended.get("global_metrics", {}).get("global_packet_loss_rate", 0) or 0) * 100.0,
                        )
                        or 0
                    ),
                    "speed_mps": float(ue_data.get("speed_mps", 0.0) or 0.0),
                    "lane_id": ue_data.get("lane_id"),
                    "waypoint_id": ue_data.get("waypoint_id"),
                }
            )

        if vehicle_entries:
            age_seconds = None
            try:
                age_seconds = max(0.0, time.time() - os.path.getmtime(extended_metrics_path))
            except OSError:
                age_seconds = None

            metrics.update(
                {
                    "available": True,
                    "stale": bool(age_seconds is not None and age_seconds > stale_after_s),
                    "age_seconds": age_seconds,
                    "total_vehicles": len(vehicle_entries),
                    "ego_present": any(v.get("vehicle_role") == "ego" for v in vehicle_entries),
                    "high_risk_vehicles": sum(
                        1 for v in vehicle_entries if str(v.get("risk_state", "")).lower() in {"high", "critical"}
                    ),
                    "medium_risk_vehicles": sum(
                        1 for v in vehicle_entries if str(v.get("risk_state", "")).lower() in {"medium", "warning"}
                    ),
                    "degraded_autonomy_vehicles": sum(
                        1
                        for v in vehicle_entries
                        if str(v.get("autonomy_state", "")).lower() not in {"normal", "unknown"}
                    ),
                    "max_latency_ms": max(float(v.get("latency_ms", 0) or 0) for v in vehicle_entries),
                    "max_packet_loss_percent": max(float(v.get("packet_loss_percent", 0) or 0) for v in vehicle_entries),
                    "max_speed_mps": max(float(v.get("speed_mps", 0) or 0) for v in vehicle_entries),
                    "vehicles": vehicle_entries,
                }
            )
            return metrics

        app3_snapshot = safe_read_json_file(monitoring_path)
        vehicles_summary = app3_snapshot.get("vehicles", {}) if isinstance(app3_snapshot, dict) else {}
        network_summary = app3_snapshot.get("network", {}) if isinstance(app3_snapshot, dict) else {}
        if not isinstance(vehicles_summary, dict):
            return metrics

        total_vehicles = int(vehicles_summary.get("total_vehicles", 0) or 0)
        if total_vehicles <= 0:
            return metrics

        age_seconds = None
        try:
            age_seconds = max(0.0, time.time() - os.path.getmtime(monitoring_path))
        except OSError:
            age_seconds = None

        metrics.update(
            {
                "available": True,
                "stale": bool(age_seconds is not None and age_seconds > stale_after_s),
                "age_seconds": age_seconds,
                "total_vehicles": total_vehicles,
                "ego_present": bool(vehicles_summary.get("ego_present", False)),
                "high_risk_vehicles": int(vehicles_summary.get("high_risk_vehicles", 0) or 0),
                "medium_risk_vehicles": int(vehicles_summary.get("medium_risk_vehicles", 0) or 0),
                "degraded_autonomy_vehicles": int(vehicles_summary.get("degraded_autonomy_vehicles", 0) or 0),
                "max_latency_ms": float(
                    vehicles_summary.get("max_latency_ms", network_summary.get("max_latency_ms", 0.0)) or 0.0
                ),
                "max_packet_loss_percent": float(
                    vehicles_summary.get(
                        "max_packet_loss_percent",
                        network_summary.get("max_packet_loss_percent", 0.0),
                    )
                    or 0.0
                ),
                "max_speed_mps": float(
                    vehicles_summary.get("max_speed_mps", network_summary.get("max_speed_mps", 0.0)) or 0.0
                ),
                "vehicles": [],
            }
        )
        return metrics
    except Exception as e:
        metrics["error"] = str(e)
        return metrics


def evaluate_vehicle_policy(vehicle_metrics: dict[str, Any]) -> dict[str, Any]:
    metrics = vehicle_metrics or {}
    if not metrics.get("available"):
        return {
            "available": False,
            "severity": "none",
            "violation": "",
            "action": "",
            "reason": "",
            "confidence": 0.0,
            "sla_violated": False,
            "guard_active": False,
        }

    high_risk = int(metrics.get("high_risk_vehicles", 0) or 0)
    medium_risk = int(metrics.get("medium_risk_vehicles", 0) or 0)
    degraded_autonomy = int(metrics.get("degraded_autonomy_vehicles", 0) or 0)
    vehicle_latency_ms = float(metrics.get("max_latency_ms", 0) or 0)
    vehicle_packet_loss = float(metrics.get("max_packet_loss_percent", 0) or 0)
    ego_present = bool(metrics.get("ego_present", False))

    critical_reasons = []
    warning_reasons = []

    if metrics.get("stale"):
        age = metrics.get("age_seconds")
        warning_reasons.append(
            f"snapshot de veículo antigo ({age:.0f}s)" if age is not None else "snapshot de veículo antigo"
        )
    if high_risk > 0:
        critical_reasons.append(f"veículos em risco alto={high_risk}")
    if degraded_autonomy > 0:
        critical_reasons.append(f"autonomia degradada em {degraded_autonomy} veículo(s)")
    if ego_present and vehicle_latency_ms >= 100:
        critical_reasons.append(f"latência veicular {vehicle_latency_ms:.0f}ms >= 100ms")
    elif ego_present and vehicle_latency_ms >= 50:
        warning_reasons.append(f"latência veicular {vehicle_latency_ms:.0f}ms >= 50ms")
    if vehicle_packet_loss >= 5:
        critical_reasons.append(f"packet loss veicular {vehicle_packet_loss:.1f}% >= 5%")
    elif vehicle_packet_loss >= 2:
        warning_reasons.append(f"packet loss veicular {vehicle_packet_loss:.1f}% >= 2%")
    if medium_risk > 0 and not critical_reasons:
        warning_reasons.append(f"veículos em risco médio={medium_risk}")

    if critical_reasons:
        return {
            "available": True,
            "severity": "critical",
            "violation": "VEHICLE_CRITICAL",
            "action": "FULL_POWER",
            "reason": critical_reasons[0],
            "confidence": 0.95,
            "sla_violated": True,
            "guard_active": False,
        }

    if warning_reasons:
        return {
            "available": True,
            "severity": "warning",
            "violation": "VEHICLE_WARNING",
            "action": "FULL_POWER_GUARD",
            "reason": warning_reasons[0],
            "confidence": 0.8,
            "sla_violated": False,
            "guard_active": True,
        }

    return {
        "available": True,
        "severity": "normal",
        "violation": "",
        "action": "MONITOR",
        "reason": "cenário veicular saudável",
        "confidence": 0.7,
        "sla_violated": False,
        "guard_active": False,
    }


def build_vehicle_intent(metrics: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    severity = str(policy.get("severity", "none") or "none").lower()
    if severity == "critical":
        state = "CRITICAL"
    elif severity == "warning":
        state = "WARNING"
    elif metrics.get("available"):
        state = "NORMAL"
    else:
        state = "IDLE"

    return {
        "STATE": state,
        "ACTION": policy.get("action", "MONITOR") or "MONITOR",
        "VIOLATION": policy.get("violation", "") or "",
        "REASON": policy.get("reason", "") or "",
        "CONFIDENCE": float(policy.get("confidence", 0.0) or 0.0),
        "TOTAL_VEHICLES": int(metrics.get("total_vehicles", 0) or 0),
        "HIGH_RISK_VEHICLES": int(metrics.get("high_risk_vehicles", 0) or 0),
        "MEDIUM_RISK_VEHICLES": int(metrics.get("medium_risk_vehicles", 0) or 0),
        "DEGRADED_AUTONOMY_VEHICLES": int(metrics.get("degraded_autonomy_vehicles", 0) or 0),
        "MAX_LATENCY_MS": float(metrics.get("max_latency_ms", 0.0) or 0.0),
        "MAX_PACKET_LOSS_PERCENT": float(metrics.get("max_packet_loss_percent", 0.0) or 0.0),
        "MAX_SPEED_MPS": float(metrics.get("max_speed_mps", 0.0) or 0.0),
        "EGO_PRESENT": str(bool(metrics.get("ego_present", False))).lower(),
        "AVAILABLE": str(bool(metrics.get("available", False))).lower(),
    }
