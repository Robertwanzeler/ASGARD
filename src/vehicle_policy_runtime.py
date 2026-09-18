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

from greenran_paths import (
    EXTENDED_METRICS_JSON_PATH,
    STATE_DIR,
    as_str,
    get_fixed_service_imsis,
)


EXTENDED_METRICS_PATH = as_str(EXTENDED_METRICS_JSON_PATH)
APP3_MONITORING_PATH = as_str(STATE_DIR / "app3_veicular" / "monitoring_snapshot.json")
SCENARIO_CONTROL_PATH = as_str(STATE_DIR / "article00_scenario_control.json")


def safe_read_json_file(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_vehicle_override(path: str = SCENARIO_CONTROL_PATH) -> dict[str, Any]:
    payload = safe_read_json_file(path)
    override = payload.get("vehicle_override", {}) if isinstance(payload, dict) else {}
    if not isinstance(override, dict) or not override.get("enabled", False):
        return {}
    return override


def _build_vehicle_metrics_from_override(override: dict[str, Any]) -> dict[str, Any]:
    total_vehicles = max(0, int(override.get("total_vehicles", 0) or 0))
    if total_vehicles <= 0:
        return {}

    high_risk = max(0, min(total_vehicles, int(override.get("high_risk_vehicles", 0) or 0)))
    medium_risk = max(0, min(total_vehicles, int(override.get("medium_risk_vehicles", 0) or 0)))
    degraded = max(0, min(total_vehicles, int(override.get("degraded_autonomy_vehicles", 0) or 0)))
    ego_latency_ms = float(override.get("ego_latency_ms", 0.0) or 0.0)
    traffic_latency_ms = float(override.get("traffic_latency_ms", ego_latency_ms) or ego_latency_ms)
    ego_loss = float(override.get("ego_packet_loss_percent", 0.0) or 0.0)
    traffic_loss = float(override.get("traffic_packet_loss_percent", ego_loss) or ego_loss)
    max_speed_mps = float(override.get("max_speed_mps", 0.0) or 0.0)

    vehicles = []
    for index in range(total_vehicles):
        is_ego = index == 0
        is_high = index < high_risk
        is_medium = not is_high and (index - high_risk) < medium_risk
        autonomy_degraded = index < degraded
        vehicles.append(
            {
                "imsi": str(16 + index),
                "vehicle_id": f"scenario-veh-{index + 1:03d}",
                "vehicle_role": "ego" if is_ego else "traffic",
                "autonomy_state": "degraded" if autonomy_degraded else "normal",
                "risk_state": "high" if is_high else ("medium" if is_medium else "low"),
                "latency_ms": ego_latency_ms if is_ego else traffic_latency_ms,
                "packet_loss_percent": ego_loss if is_ego else traffic_loss,
                "speed_mps": max_speed_mps,
                "lane_id": f"L{(index % 3) + 1}",
                "waypoint_id": f"scenario-wp-{index + 1:03d}",
            }
        )

    return {
        "available": True,
        "stale": False,
        "age_seconds": 0.0,
        "total_vehicles": total_vehicles,
        "ego_present": total_vehicles > 0,
        "high_risk_vehicles": high_risk,
        "medium_risk_vehicles": medium_risk,
        "degraded_autonomy_vehicles": degraded,
        "max_latency_ms": max(float(v.get("latency_ms", 0.0) or 0.0) for v in vehicles),
        "max_packet_loss_percent": max(float(v.get("packet_loss_percent", 0.0) or 0.0) for v in vehicles),
        "max_speed_mps": max_speed_mps,
        "vehicles": vehicles,
        "scenario_mode": str(override.get("mode", "scenario_control_override") or "scenario_control_override"),
    }


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
        strict_real = os.environ.get("GREENRAN_REQUIRE_REAL_PDCP", "0").strip().lower() in {
            "1", "true", "yes", "on"
        }
        override = _load_vehicle_override()
        if override and not strict_real:
            override_metrics = _build_vehicle_metrics_from_override(override)
            if override_metrics:
                return override_metrics

        extended = safe_read_json_file(extended_metrics_path)
        ue_metrics = (extended.get("ue_metrics", {}) or {}) if isinstance(extended, dict) else {}

        vehicle_entries = []
        invalid_real_vehicle = False
        for imsi, ue_data in ue_metrics.items():
            if not isinstance(ue_data, dict) or ue_data.get("device_type") != "vehicle":
                continue
            loss_value = ue_data.get("packet_loss_percent")
            if strict_real and (
                ue_data.get("pdcp_provenance") != "pdcp_real"
                or loss_value is None
                or bool(ue_data.get("latency_is_proxy"))
            ):
                invalid_real_vehicle = True
                continue
            vehicle_entries.append(
                {
                    "imsi": imsi,
                    "vehicle_id": ue_data.get("vehicle_id", f"veh-imsi-{imsi}"),
                    "vehicle_role": ue_data.get("vehicle_role", "traffic"),
                    "autonomy_state": ue_data.get("autonomy_state", "unknown"),
                    "risk_state": ue_data.get("risk_state", "unknown"),
                    "latency_ms": float(ue_data.get("latency_avg_us", ue_data.get("latency_us", 0)) or 0) / 1000.0,
                    "packet_loss_percent": float(loss_value or 0.0),
                    "tx_pdus": int(ue_data.get("tx_pdus", 0) or 0),
                    "rx_pdus": int(ue_data.get("rx_pdus", 0) or 0),
                    "sample_window_s": float(ue_data.get("sample_window_s", 0.0) or 0.0),
                    "speed_mps": float(ue_data.get("speed_mps", 0.0) or 0.0),
                    "lane_id": ue_data.get("lane_id"),
                    "waypoint_id": ue_data.get("waypoint_id"),
                }
            )

        expected_vehicle_imsis = {str(value) for value in get_fixed_service_imsis()["vehicle"]}
        observed_vehicle_imsis = {str(item.get("imsi")) for item in vehicle_entries}
        if strict_real and (invalid_real_vehicle or observed_vehicle_imsis != expected_vehicle_imsis):
            metrics["reason"] = "real_pdcp_vehicle_coverage_incomplete"
            return metrics

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
                    "min_tx_pdus": min((int(v.get("tx_pdus", 0) or 0) for v in vehicle_entries), default=0),
                    "min_rx_pdus": min((int(v.get("rx_pdus", 0) or 0) for v in vehicle_entries), default=0),
                    "min_sample_window_s": min((float(v.get("sample_window_s", 0.0) or 0.0) for v in vehicle_entries), default=0.0),
                    "sample_window_s": max((float(v.get("sample_window_s", 0.0) or 0.0) for v in vehicle_entries), default=0.0),
                    "vehicles": vehicle_entries,
                }
            )
            return metrics

        app3_snapshot = safe_read_json_file(monitoring_path)
        if strict_real and (
            app3_snapshot.get("schema") != "greenran.app3.pdcp_real_snapshot.v1"
            or app3_snapshot.get("pdcp_provenance") != "pdcp_real"
            or not app3_snapshot.get("valid")
        ):
            metrics["reason"] = "real_pdcp_app3_snapshot_missing"
            return metrics
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
                "min_tx_pdus": int(vehicles_summary.get("min_tx_pdus", 0) or 0),
                "min_rx_pdus": int(vehicles_summary.get("min_rx_pdus", 0) or 0),
                "min_sample_window_s": float(vehicles_summary.get("min_sample_window_s", 0.0) or 0.0),
                "sample_window_s": float(vehicles_summary.get("sample_window_s", 0.0) or 0.0),
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

    # A short real-PDCP window is evidence that traffic exists, but not yet
    # enough evidence to classify a hard vehicle SLA violation.  Keep the
    # loss calculation intact and quarantine this observation from economic
    # replay.  Once the window is mature, the existing <1% SLA is unchanged.
    vehicle_rows = metrics.get("vehicles") if isinstance(metrics.get("vehicles"), list) else []
    evidence_fields_present = (
        "min_tx_pdus" in metrics
        or any(isinstance(row, dict) and "tx_pdus" in row for row in vehicle_rows)
    )
    min_tx_pdus = int(metrics.get("min_tx_pdus", 0) or 0)
    min_sample_window_s = float(metrics.get("min_sample_window_s", 0.0) or 0.0)
    if vehicle_rows:
        min_tx_pdus = min(
            (int(row.get("tx_pdus", 0) or 0) for row in vehicle_rows),
            default=min_tx_pdus,
        )
        min_sample_window_s = min(
            (float(row.get("sample_window_s", 0.0) or 0.0) for row in vehicle_rows),
            default=min_sample_window_s,
        )
    if evidence_fields_present and (min_tx_pdus < 100 or min_sample_window_s < 1.0):
        return {
            "available": True,
            "severity": "unknown",
            "violation": "VEHICLE_WARMUP",
            "action": "MONITOR",
            "reason": (
                "janela veicular insuficiente para decisão SLA "
                f"(min_tx_pdus={min_tx_pdus}, min_window_s={min_sample_window_s:.3f})"
            ),
            "confidence": 0.0,
            "sla_violated": False,
            "guard_active": True,
            "warmup": True,
            "economic_replay_eligible": False,
        }

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
    # GreenRAN QoS: autonomous vehicles have a 20 ms target, 10 ms warning,
    # and at most 1% packet loss (0.5% warning).
    if ego_present and vehicle_latency_ms >= 20:
        critical_reasons.append(f"latência veicular {vehicle_latency_ms:.0f}ms >= 20ms")
    elif ego_present and vehicle_latency_ms >= 10:
        warning_reasons.append(f"latência veicular {vehicle_latency_ms:.0f}ms >= 10ms")
    if vehicle_packet_loss >= 1:
        critical_reasons.append(f"packet loss veicular {vehicle_packet_loss:.1f}% >= 1%")
    elif vehicle_packet_loss >= 0.5:
        warning_reasons.append(f"packet loss veicular {vehicle_packet_loss:.1f}% >= 0.5%")
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
