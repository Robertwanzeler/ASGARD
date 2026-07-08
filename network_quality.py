"""Cálculo de qualidade de rede com base em métricas reais do runtime."""

from __future__ import annotations

from typing import Any, Dict, Mapping


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _linear_score(value: float, good: float, bad: float, higher_is_better: bool = True) -> float:
    value = _safe_float(value)
    good = _safe_float(good)
    bad = _safe_float(bad)
    if good == bad:
        return 1.0 if ((higher_is_better and value >= good) or (not higher_is_better and value <= good)) else 0.0
    if higher_is_better:
        if value >= good:
            return 1.0
        if value <= bad:
            return 0.0
        return _clamp01((value - bad) / (good - bad))
    if value <= good:
        return 1.0
    if value >= bad:
        return 0.0
    return _clamp01((bad - value) / (bad - good))


def _pick_camera_entries(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    ue_metrics = metrics.get("ue_metrics", {}) if isinstance(metrics, Mapping) else {}
    if not isinstance(ue_metrics, Mapping):
        return []
    return [
        dict(ue)
        for ue in ue_metrics.values()
        if isinstance(ue, Mapping) and str(ue.get("device_type", "")).lower() == "camera"
    ]


def _pick_vehicle_entries(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    ue_metrics = metrics.get("ue_metrics", {}) if isinstance(metrics, Mapping) else {}
    if not isinstance(ue_metrics, Mapping):
        return []
    return [
        dict(ue)
        for ue in ue_metrics.values()
        if isinstance(ue, Mapping) and str(ue.get("device_type", "")).lower() == "vehicle"
    ]


def _camera_throughput_kbps(entry: Mapping[str, Any]) -> float:
    return max(
        _safe_float(entry.get("rx_throughput_kbps", 0.0)),
        _safe_float(entry.get("throughput_kbps", 0.0)),
        _safe_float(entry.get("total_pdcp_throughput_kbps", 0.0)),
        _safe_float(entry.get("tx_throughput_kbps", 0.0)),
        _safe_float(entry.get("cu_up_throughput_kbps", 0.0)),
    )


def _camera_latency_ms(entry: Mapping[str, Any]) -> float:
    latency_us = max(
        _safe_float(entry.get("latency_us", 0.0)),
        _safe_float(entry.get("latency_avg_us", 0.0)),
        _safe_float(entry.get("latency_max_us", 0.0)),
    )
    return latency_us / 1000.0


def _vehicle_latency_ms(entry: Mapping[str, Any]) -> float:
    latency_us = max(
        _safe_float(entry.get("latency_us", 0.0)),
        _safe_float(entry.get("latency_avg_us", 0.0)),
        _safe_float(entry.get("latency_max_us", 0.0)),
    )
    return latency_us / 1000.0


def _vehicle_packet_loss_percent(entry: Mapping[str, Any]) -> float:
    return _safe_float(entry.get("packet_loss_percent", 0.0))


def evaluate_network_quality(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    """Agrega a saúde da rede em um score de 0 a 100 usando somente dados reais."""

    metrics = metrics if isinstance(metrics, Mapping) else {}
    gm = metrics.get("global_metrics", {}) if isinstance(metrics, Mapping) else {}
    gm = gm if isinstance(gm, Mapping) else {}

    camera_entries = _pick_camera_entries(metrics)
    vehicle_entries = _pick_vehicle_entries(metrics)

    total_active_cameras = int(gm.get("total_active_cameras", len(camera_entries)) or len(camera_entries) or 0)
    total_active_sensors = int(gm.get("total_active_sensors", 0) or 0)
    total_active_vehicles = int(gm.get("total_active_vehicles", len(vehicle_entries)) or len(vehicle_entries) or 0)

    global_packet_loss_percent = _safe_float(gm.get("global_packet_loss_rate", 0.0)) * 100.0
    throughput_mbps = _safe_float(gm.get("throughput_kbps", 0.0)) / 1000.0
    latency_p95_ms = _safe_float(gm.get("latency_p95_us", 0.0)) / 1000.0
    cvar_ms = _safe_float(gm.get("cvar_per_ue_us", 0.0)) / 1000.0
    collector_mode = str(gm.get("collector_mode", "") or "")
    real_latency_sample_count = int(gm.get("real_latency_sample_count", 0) or 0)
    proxy_latency_sample_count = int(gm.get("proxy_latency_sample_count", 0) or 0)

    camera_throughputs_mbps = [_camera_throughput_kbps(entry) / 1000.0 for entry in camera_entries]
    camera_latencies_ms = [_camera_latency_ms(entry) for entry in camera_entries if _camera_latency_ms(entry) > 0]
    vehicle_latencies_ms = [_vehicle_latency_ms(entry) for entry in vehicle_entries if _vehicle_latency_ms(entry) > 0]
    vehicle_packet_losses = [_vehicle_packet_loss_percent(entry) for entry in vehicle_entries]
    camera_latency_coverage = (
        sum(1 for entry in camera_entries if bool(entry.get("has_latency_samples")))
        / len(camera_entries)
        if camera_entries
        else 0.0
    )
    vehicle_latency_coverage = (
        sum(1 for entry in vehicle_entries if bool(entry.get("has_latency_samples")))
        / len(vehicle_entries)
        if vehicle_entries
        else 0.0
    )

    min_camera_throughput_mbps = min(camera_throughputs_mbps) if camera_throughputs_mbps else 0.0
    max_camera_latency_ms = max(camera_latencies_ms) if camera_latencies_ms else 0.0
    max_vehicle_latency_ms = max(vehicle_latencies_ms) if vehicle_latencies_ms else 0.0
    max_vehicle_packet_loss_percent = max(vehicle_packet_losses) if vehicle_packet_losses else 0.0

    camera_throughput_score = _linear_score(min_camera_throughput_mbps, 25.0, 0.0, higher_is_better=True)
    camera_latency_score = _linear_score(max_camera_latency_ms, 100.0, 250.0, higher_is_better=False)
    camera_latency_weight = max(0.50, camera_latency_coverage)
    camera_score = 100.0 * (
        0.70 * camera_throughput_score + 0.30 * (camera_latency_score * camera_latency_weight)
    )
    camera_status = "OK"
    if camera_entries:
        if min_camera_throughput_mbps < 25.0 or max_camera_latency_ms >= 100.0:
            camera_status = "CRITICA"
        elif min_camera_throughput_mbps < 30.0 or max_camera_latency_ms >= 80.0:
            camera_status = "GUARDA"
    else:
        camera_status = "INATIVA"

    sensor_presence_score = 1.0 if total_active_sensors > 0 else 0.0
    sensor_delivery_score = _linear_score(global_packet_loss_percent, 2.0, 10.0, higher_is_better=False)
    sensor_latency_score = _linear_score(latency_p95_ms, 250.0, 1000.0, higher_is_better=False)
    sensor_score = 100.0 * (
        0.40 * sensor_presence_score
        + 0.35 * sensor_delivery_score
        + 0.25 * sensor_latency_score
    )
    sensor_status = "OK"
    if total_active_sensors <= 0:
        sensor_status = "INATIVA"
    elif global_packet_loss_percent >= 10.0 or latency_p95_ms >= 1000.0:
        sensor_status = "CRITICA"
    elif global_packet_loss_percent >= 5.0 or latency_p95_ms >= 500.0:
        sensor_status = "GUARDA"

    vehicle_presence_score = 1.0 if total_active_vehicles > 0 else 0.0
    vehicle_latency_score = _linear_score(max_vehicle_latency_ms, 100.0, 200.0, higher_is_better=False)
    vehicle_loss_score = _linear_score(max_vehicle_packet_loss_percent, 2.0, 5.0, higher_is_better=False)
    vehicle_latency_weight = max(0.50, vehicle_latency_coverage)
    vehicle_score = 100.0 * (
        0.35 * vehicle_presence_score
        + 0.40 * (vehicle_latency_score * vehicle_latency_weight)
        + 0.25 * vehicle_loss_score
    )
    vehicle_status = "OK"
    if total_active_vehicles <= 0:
        vehicle_status = "INATIVA"
    elif max_vehicle_latency_ms >= 100.0 or max_vehicle_packet_loss_percent >= 5.0:
        vehicle_status = "CRITICA"
    elif max_vehicle_latency_ms >= 50.0 or max_vehicle_packet_loss_percent >= 2.0:
        vehicle_status = "GUARDA"

    throughput_score = _linear_score(throughput_mbps, 25.0, 0.0, higher_is_better=True)
    latency_score = _linear_score(latency_p95_ms, 100.0, 250.0, higher_is_better=False)
    cvar_score = _linear_score(cvar_ms, 100.0, 250.0, higher_is_better=False)
    packet_loss_score = _linear_score(global_packet_loss_percent, 1.0, 10.0, higher_is_better=False)
    core_score = 100.0 * (
        0.30 * throughput_score
        + 0.30 * latency_score
        + 0.25 * cvar_score
        + 0.15 * packet_loss_score
    )

    weights = []
    weighted_scores = []
    if camera_entries:
        weights.append(0.40)
        weighted_scores.append(0.40 * camera_score)
    if total_active_sensors > 0:
        weights.append(0.15)
        weighted_scores.append(0.15 * sensor_score)
    if total_active_vehicles > 0:
        weights.append(0.25)
        weighted_scores.append(0.25 * vehicle_score)
    weights.append(0.20)
    weighted_scores.append(0.20 * core_score)

    weight_total = sum(weights) if weights else 1.0
    overall_score = sum(weighted_scores) / weight_total if weight_total > 0 else 0.0
    overall_score = round(max(0.0, min(100.0, overall_score)), 2)

    if overall_score >= 90.0:
        label = "EXCELENTE"
    elif overall_score >= 75.0:
        label = "BOA"
    elif overall_score >= 60.0:
        label = "ATENCAO"
    else:
        label = "CRITICA"

    reasons = []
    if camera_status in {"GUARDA", "CRITICA"}:
        reasons.append(
            f"câmeras: {min_camera_throughput_mbps:.1f} Mbps / {max_camera_latency_ms:.1f} ms"
        )
    if sensor_status in {"GUARDA", "CRITICA"}:
        reasons.append(
            f"sensores: loss {global_packet_loss_percent:.2f}% / p95 {latency_p95_ms:.1f} ms"
        )
    if vehicle_status in {"GUARDA", "CRITICA"}:
        reasons.append(
            f"veículos: lat {max_vehicle_latency_ms:.1f} ms / loss {max_vehicle_packet_loss_percent:.2f}%"
        )
    if throughput_score < 0.7:
        reasons.append(f"throughput agregado {throughput_mbps:.1f} Mbps abaixo da referência")
    if collector_mode != "pdcp_real" or proxy_latency_sample_count > 0:
        reasons.append(
            f"coleta mista: modo={collector_mode or 'unknown'} real={real_latency_sample_count} proxy={proxy_latency_sample_count}"
        )

    return {
        "available": True,
        "real_only": collector_mode == "pdcp_real" and proxy_latency_sample_count == 0,
        "label": label,
        "score": overall_score,
        "camera_score": round(camera_score, 2),
        "sensor_score": round(sensor_score, 2),
        "vehicle_score": round(vehicle_score, 2),
        "core_score": round(core_score, 2),
        "camera_status": camera_status,
        "sensor_status": sensor_status,
        "vehicle_status": vehicle_status,
        "throughput_mbps": round(throughput_mbps, 3),
        "latency_p95_ms": round(latency_p95_ms, 3),
        "cvar_ms": round(cvar_ms, 3),
        "global_packet_loss_percent": round(global_packet_loss_percent, 3),
        "camera_min_throughput_mbps": round(min_camera_throughput_mbps, 3),
        "camera_max_latency_ms": round(max_camera_latency_ms, 3),
        "vehicle_max_latency_ms": round(max_vehicle_latency_ms, 3),
        "vehicle_max_packet_loss_percent": round(max_vehicle_packet_loss_percent, 3),
        "active_cameras": total_active_cameras,
        "active_sensors": total_active_sensors,
        "active_vehicles": total_active_vehicles,
        "reasons": reasons,
    }
