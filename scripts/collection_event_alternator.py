#!/usr/bin/env python3
"""
Alternate GreenRAN collection events by writing scenario-control stages.

The default mode follows ns-3 simulation time instead of wall-clock time so the
application-side overrides stay aligned with the real offered-load profile.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from greenran_paths import ARTICLE00_SCENARIO_CONTROL_PATH


APP1_HEALTHY = {
    "enabled": True,
    "active_cameras": 3,
    "observed_cameras": 3,
    "critical_cameras": 0,
    "throughput_ready": True,
    "throughput_mbps": 32.0,
    "avg_throughput_mbps": 34.0,
    "latency_ms": 18.0,
    "throughput_source": "collection_event_alternator",
}

APP1_STRESSED_SAFE = {
    **APP1_HEALTHY,
    "throughput_mbps": 29.8,
    "avg_throughput_mbps": 31.0,
    "latency_ms": 34.0,
}

APP1_NEAR_GUARD = {
    **APP1_HEALTHY,
    "throughput_mbps": 30.4,
    "avg_throughput_mbps": 31.2,
    "latency_ms": 54.0,
}

APP1_WARNING = {
    **APP1_HEALTHY,
    "throughput_mbps": 27.4,
    "avg_throughput_mbps": 28.4,
    # Keep the warning inside the rApp's CONDITIONAL band. The ARMD protocol
    # escalates camera latency at >=80 ms, so 82 ms would be BLOCKED rather
    # than the stage label declared by tasam_training_balanced_v1.
    "latency_ms": 72.0,
}

APP1_GUARD = {
    **APP1_HEALTHY,
    "throughput_mbps": 24.2,
    "avg_throughput_mbps": 24.9,
    "latency_ms": 92.0,
    "critical_cameras": 1,
}

APP1_CRITICAL = {
    **APP1_HEALTHY,
    "throughput_mbps": 21.6,
    "avg_throughput_mbps": 22.4,
    "latency_ms": 108.0,
    "critical_cameras": 2,
}

APP2_HEALTHY = {
    "enabled": True,
    "total_sensors": 17,
    "connected_sensors": 17,
    "error_sensors": 0,
    "low_battery_sensors": 0,
    "packet_loss_percent": 3.2,
    "delivery_success_percent": 97.2,
    "avg_latency_ms": 185.0,
    "avg_rssi_dbm": -89.0,
    "avg_battery_percent": 76.0,
    "avg_power_mw": 182.0,
    "network_utilization_percent": 62.0,
    "mode": "healthy",
}

APP2_ALLOWED_STABLE = {
    **APP2_HEALTHY,
    "total_sensors": 25,
    "connected_sensors": 25,
    "error_sensors": 0,
    "low_battery_sensors": 0,
    "packet_loss_percent": 1.2,
    "delivery_success_percent": 99.1,
    "avg_latency_ms": 118.0,
    "avg_rssi_dbm": -84.0,
    "avg_battery_percent": 82.0,
    "avg_power_mw": 168.0,
    "network_utilization_percent": 48.0,
    "mode": "allowed_stable",
}

APP2_WARNING = {
    **APP2_ALLOWED_STABLE,
    "connected_sensors": 24,
    "error_sensors": 1,
    "low_battery_sensors": 1,
    "packet_loss_percent": 5.6,
    "delivery_success_percent": 96.2,
    "avg_latency_ms": 540.0,
    "avg_rssi_dbm": -92.0,
    "avg_battery_percent": 68.0,
    "avg_power_mw": 208.0,
    "network_utilization_percent": 76.0,
    "mode": "warning",
}

APP2_CRITICAL = {
    **APP2_ALLOWED_STABLE,
    "connected_sensors": 20,
    "error_sensors": 5,
    "low_battery_sensors": 3,
    "packet_loss_percent": 8.4,
    "delivery_success_percent": 88.5,
    "avg_latency_ms": 760.0,
    "avg_rssi_dbm": -98.0,
    "avg_battery_percent": 54.0,
    "avg_power_mw": 235.0,
    "network_utilization_percent": 86.0,
    "mode": "critical",
}

VEHICLE_HEALTHY = {
    "enabled": True,
    "total_vehicles": 5,
    "high_risk_vehicles": 0,
    "medium_risk_vehicles": 0,
    "degraded_autonomy_vehicles": 0,
    "ego_latency_ms": 18.0,
    "traffic_latency_ms": 12.0,
    "ego_packet_loss_percent": 0.2,
    "traffic_packet_loss_percent": 0.1,
    "max_speed_mps": 7.0,
    "mode": "healthy",
}

VEHICLE_ALLOWED_STABLE = {
    **VEHICLE_HEALTHY,
    "ego_latency_ms": 12.0,
    "traffic_latency_ms": 8.0,
    "ego_packet_loss_percent": 0.05,
    "traffic_packet_loss_percent": 0.02,
    "max_speed_mps": 5.0,
    "mode": "allowed_stable",
}

VEHICLE_WARNING = {
    **VEHICLE_ALLOWED_STABLE,
    "medium_risk_vehicles": 1,
    "ego_latency_ms": 58.0,
    "traffic_latency_ms": 24.0,
    "ego_packet_loss_percent": 2.6,
    "traffic_packet_loss_percent": 0.8,
    "max_speed_mps": 8.2,
    "mode": "warning",
}

VEHICLE_CRITICAL = {
    **VEHICLE_ALLOWED_STABLE,
    "high_risk_vehicles": 1,
    "degraded_autonomy_vehicles": 1,
    "ego_latency_ms": 122.0,
    "traffic_latency_ms": 38.0,
    "ego_packet_loss_percent": 6.4,
    "traffic_packet_loss_percent": 1.6,
    "max_speed_mps": 9.4,
    "mode": "critical",
}


@dataclass(frozen=True)
class Stage:
    name: str
    duration_s: int
    target_domain: str
    app1: dict[str, Any]
    app2: dict[str, Any]
    vehicle: dict[str, Any]
    note: str


@dataclass(frozen=True)
class StagePosition:
    cycle_index: int
    stage_index: int
    sim_time_s: float
    cycle_elapsed_s: float
    stage_elapsed_s: float
    stage_remaining_s: float
    stage: Stage


SHOULD_STOP = False
DISABLE_APP_OVERRIDES = str(os.environ.get("GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES", "0")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _stage(
    name: str,
    duration_s: int,
    *,
    app1: dict[str, Any] | None = None,
    app2: dict[str, Any] | None = None,
    vehicle: dict[str, Any] | None = None,
    note: str = "",
) -> Stage:
    if name.startswith("camera_"):
        target_domain = "camera"
    elif name.startswith("vehicle_"):
        target_domain = "vehicle"
    elif name.startswith("app2_"):
        target_domain = "app2"
    else:
        target_domain = "global"
    return Stage(
        name=name,
        duration_s=max(1, int(duration_s)),
        target_domain=target_domain,
        app1=(app1 or APP1_HEALTHY).copy(),
        app2=(app2 or APP2_HEALTHY).copy(),
        vehicle=(vehicle or VEHICLE_HEALTHY).copy(),
        note=note,
    )


PROFILES: dict[str, list[Stage]] = {
    "tasam_training_balanced_v1": [
        _stage(
            "allowed_bootstrap",
            4,
            app1=APP1_HEALTHY,
            app2=APP2_ALLOWED_STABLE,
            vehicle=VEHICLE_ALLOWED_STABLE,
            note="Aquecimento inicial com tudo saudável para produzir ALLOWED rapidamente.",
        ),
        _stage(
            "allowed_stable",
            4,
            app1={**APP1_HEALTHY, "throughput_mbps": 34.5, "avg_throughput_mbps": 35.4, "latency_ms": 14.0},
            app2={**APP2_ALLOWED_STABLE, "avg_latency_ms": 92.0, "packet_loss_percent": 0.9, "delivery_success_percent": 99.4, "mode": "allowed_training"},
            vehicle={**VEHICLE_ALLOWED_STABLE, "ego_latency_ms": 10.0, "traffic_latency_ms": 7.0, "mode": "allowed_training"},
            note="Janela ALLOWED limpa para a IA ver economia sustentável.",
        ),
        _stage(
            "camera_conditional",
            4,
            app1=APP1_WARNING,
            app2={**APP2_ALLOWED_STABLE, "avg_latency_ms": 105.0, "mode": "camera_conditional_safe"},
            vehicle=VEHICLE_ALLOWED_STABLE,
            note="Empurra App1 para a faixa 25-30Mbps e gera CONDITIONAL por câmera.",
        ),
        _stage(
            "camera_blocked",
            3,
            app1=APP1_CRITICAL,
            app2={**APP2_ALLOWED_STABLE, "avg_latency_ms": 110.0, "mode": "camera_blocked_safe"},
            vehicle=VEHICLE_ALLOWED_STABLE,
            note="Pulso BLOCKED por câmera para marcar violação forte de SLA.",
        ),
        _stage(
            "vehicle_conditional",
            4,
            app1=APP1_HEALTHY,
            app2=APP2_ALLOWED_STABLE,
            vehicle=VEHICLE_WARNING,
            note="Ativa CONDITIONAL por App3/veículos sem derrubar App1/App2.",
        ),
        _stage(
            "vehicle_blocked",
            3,
            app1=APP1_HEALTHY,
            app2=APP2_ALLOWED_STABLE,
            vehicle=VEHICLE_CRITICAL,
            note="Força BLOCKED por veículo com alto risco e perda veicular.",
        ),
        _stage(
            "app2_conditional",
            4,
            app1=APP1_HEALTHY,
            app2=APP2_WARNING,
            vehicle=VEHICLE_ALLOWED_STABLE,
            note="Coloca App2 em warning para gerar CONDITIONAL mMTC.",
        ),
        _stage(
            "app2_blocked",
            3,
            app1=APP1_HEALTHY,
            app2=APP2_CRITICAL,
            vehicle=VEHICLE_ALLOWED_STABLE,
            note="Janela crítica de App2 para gerar BLOCKED mMTC.",
        ),
        _stage(
            "allowed_recovery",
            4,
            app1={**APP1_HEALTHY, "throughput_mbps": 33.4, "avg_throughput_mbps": 34.0, "latency_ms": 16.0},
            app2={**APP2_ALLOWED_STABLE, "avg_latency_ms": 98.0, "mode": "allowed_recovery"},
            vehicle={**VEHICLE_ALLOWED_STABLE, "ego_latency_ms": 11.0, "traffic_latency_ms": 7.5, "mode": "allowed_recovery"},
            note="Recuperação longa para voltar a ALLOWED e fechar o ciclo balanceado.",
        ),
    ],
    "drl_article_v1": [
        _stage("baseline_healthy", 120, note="Janela saudável para ML/DRL e baseline de coleta."),
        _stage("app1_warning", 70, app1=APP1_WARNING, note="Empurra o App1 para a faixa de cautela sem quebrar totalmente o SLA."),
        _stage("app1_guard", 40, app1=APP1_GUARD, note="Força uma janela curta de guarda do App1 para aumentar a pressão RAN."),
        _stage("baseline_recovery_after_app1", 90, app1=APP1_STRESSED_SAFE, note="Recuperação curta do App1 antes de voltar a pressionar outras camadas."),
        _stage(
            "app2_stressed_safe",
            90,
            app1=APP1_STRESSED_SAFE,
            app2={
                **APP2_HEALTHY,
                "connected_sensors": 17,
                "error_sensors": 1,
                "low_battery_sensors": 0,
                "packet_loss_percent": 4.4,
                "delivery_success_percent": 95.8,
                "avg_latency_ms": 420.0,
                "avg_rssi_dbm": -93.0,
                "avg_battery_percent": 70.0,
                "avg_power_mw": 210.0,
                "network_utilization_percent": 78.0,
                "mode": "stressed_safe",
            },
            note="Pressiona App2 sem cruzar a guarda para manter o DRL ativo.",
        ),
        _stage(
            "vehicle_stressed_safe",
            90,
            app1=APP1_NEAR_GUARD,
            vehicle={
                **VEHICLE_HEALTHY,
                "high_risk_vehicles": 0,
                "medium_risk_vehicles": 0,
                "degraded_autonomy_vehicles": 0,
                "ego_latency_ms": 46.0,
                "traffic_latency_ms": 22.0,
                "ego_packet_loss_percent": 1.8,
                "traffic_packet_loss_percent": 0.6,
                "max_speed_mps": 8.5,
                "mode": "vehicle_stressed_safe",
            },
            note="Pressiona a camada veicular abaixo do limiar de warning.",
        ),
        _stage(
            "app2_guard",
            45,
            app2={
                **APP2_HEALTHY,
                "connected_sensors": 16,
                "error_sensors": 1,
                "low_battery_sensors": 1,
                "packet_loss_percent": 4.8,
                "delivery_success_percent": 95.0,
                "avg_latency_ms": 245.0,
                "avg_rssi_dbm": -95.0,
                "avg_battery_percent": 63.0,
                "avg_power_mw": 220.0,
                "network_utilization_percent": 74.0,
                "mode": "guard",
            },
            note="Dispara guarda curta do App2 e força retorno posterior ao baseline.",
        ),
        _stage("baseline_recovery_after_app2", 120, app1=APP1_STRESSED_SAFE, note="Recuperação após guarda do App2 para o DRL voltar a emitir trace."),
        _stage(
            "vehicle_warning",
            45,
            vehicle={
                **VEHICLE_HEALTHY,
                "medium_risk_vehicles": 1,
                "ego_latency_ms": 58.0,
                "traffic_latency_ms": 28.0,
                "ego_packet_loss_percent": 2.6,
                "traffic_packet_loss_percent": 0.8,
                "max_speed_mps": 8.0,
                "mode": "vehicle_warning",
            },
            note="Ativa warning veicular curto para diversificar os eventos da coleta.",
        ),
        _stage("baseline_recovery_after_vehicle", 120, app1=APP1_STRESSED_SAFE, note="Recuperação após warning veicular."),
        _stage(
            "app2_critical",
            30,
            app1=APP1_WARNING,
            app2={
                **APP2_HEALTHY,
                "connected_sensors": 14,
                "error_sensors": 3,
                "low_battery_sensors": 2,
                "packet_loss_percent": 4.8,
                "delivery_success_percent": 87.5,
                "avg_latency_ms": 360.0,
                "avg_rssi_dbm": -101.0,
                "avg_battery_percent": 58.0,
                "avg_power_mw": 240.0,
                "network_utilization_percent": 68.0,
                "mode": "critical",
            },
            note="Bloco crítico curto para não prender a coleta por muito tempo.",
        ),
        _stage("app1_critical_short", 25, app1=APP1_CRITICAL, note="Pulso crítico curto do App1 para quebrar o regime de RAN sempre satisfeito."),
        _stage("baseline_recovery_after_critical", 150, note="Janela longa de recuperação para o DRL voltar a estabilizar."),
    ],
    "drl_article_conflict_forced_v1": [
        _stage("baseline_healthy", 60, note="Bootstrap curto para estabilizar o runtime antes da pressão forte."),
        _stage("camera_overload", 90, app1={**APP1_CRITICAL, "throughput_mbps": 18.0, "avg_throughput_mbps": 19.2, "latency_ms": 86.0}, note="Sobrecarga forte de câmeras para induzir conflito RAN explícito."),
        _stage("mixed_overload", 120, app1={**APP1_CRITICAL, "throughput_mbps": 16.5, "avg_throughput_mbps": 17.8, "latency_ms": 92.0}, app2={**APP2_HEALTHY, "connected_sensors": 15, "error_sensors": 2, "low_battery_sensors": 1, "packet_loss_percent": 6.2, "delivery_success_percent": 91.0, "avg_latency_ms": 510.0, "avg_rssi_dbm": -97.0, "avg_battery_percent": 64.0, "avg_power_mw": 228.0, "network_utilization_percent": 88.0, "mode": "mixed_overload"}, vehicle={**VEHICLE_HEALTHY, "medium_risk_vehicles": 1, "degraded_autonomy_vehicles": 1, "ego_latency_ms": 62.0, "traffic_latency_ms": 31.0, "ego_packet_loss_percent": 3.4, "traffic_packet_loss_percent": 1.1, "max_speed_mps": 8.8, "mode": "mixed_overload"}, note="Pressão simultânea de câmera, background e apps auxiliares."),
        _stage("background_overload", 90, app1={**APP1_GUARD, "throughput_mbps": 22.5, "avg_throughput_mbps": 23.4, "latency_ms": 73.0}, app2={**APP2_HEALTHY, "connected_sensors": 16, "error_sensors": 1, "low_battery_sensors": 1, "packet_loss_percent": 5.4, "delivery_success_percent": 93.4, "avg_latency_ms": 430.0, "avg_rssi_dbm": -95.0, "avg_battery_percent": 66.0, "avg_power_mw": 220.0, "network_utilization_percent": 82.0, "mode": "background_overload"}, note="Competição forte dos UEs background mantendo App1 pressionado."),
        _stage("recovery_window", 90, app1=APP1_STRESSED_SAFE, note="Janela de recuperação para capturar retorno após conflito."),
        _stage("camera_overload_repeat", 60, app1={**APP1_CRITICAL, "throughput_mbps": 17.4, "avg_throughput_mbps": 18.6, "latency_ms": 95.0}, note="Pulso final para confirmar repetibilidade do conflito RAN."),
        _stage("final_recovery", 90, note="Fechamento com recuperação controlada para dataset balanceado."),
    ],
    "drl_article_conflict_forced_fast_v1": [
        _stage("baseline_healthy", 10, note="Bootstrap curto para estabilizar o runtime antes da pressão forte."),
        _stage("camera_overload", 20, app1={**APP1_CRITICAL, "throughput_mbps": 18.0, "avg_throughput_mbps": 19.2, "latency_ms": 86.0}, note="Sobrecarga forte de câmeras para induzir conflito RAN explícito."),
        _stage("mixed_overload", 30, app1={**APP1_CRITICAL, "throughput_mbps": 16.5, "avg_throughput_mbps": 17.8, "latency_ms": 92.0}, app2={**APP2_HEALTHY, "connected_sensors": 15, "error_sensors": 2, "low_battery_sensors": 1, "packet_loss_percent": 6.2, "delivery_success_percent": 91.0, "avg_latency_ms": 510.0, "avg_rssi_dbm": -97.0, "avg_battery_percent": 64.0, "avg_power_mw": 228.0, "network_utilization_percent": 88.0, "mode": "mixed_overload"}, vehicle={**VEHICLE_HEALTHY, "medium_risk_vehicles": 1, "degraded_autonomy_vehicles": 1, "ego_latency_ms": 62.0, "traffic_latency_ms": 31.0, "ego_packet_loss_percent": 3.4, "traffic_packet_loss_percent": 1.1, "max_speed_mps": 8.8, "mode": "mixed_overload"}, note="Pressão simultânea de câmera, background e apps auxiliares em janela curta."),
        _stage("background_overload", 20, app1={**APP1_GUARD, "throughput_mbps": 22.5, "avg_throughput_mbps": 23.4, "latency_ms": 73.0}, app2={**APP2_HEALTHY, "connected_sensors": 16, "error_sensors": 1, "low_battery_sensors": 1, "packet_loss_percent": 5.4, "delivery_success_percent": 93.4, "avg_latency_ms": 430.0, "avg_rssi_dbm": -95.0, "avg_battery_percent": 66.0, "avg_power_mw": 220.0, "network_utilization_percent": 82.0, "mode": "background_overload"}, note="Competição forte dos UEs background mantendo App1 pressionado em janela curta."),
        _stage("recovery_window", 15, app1=APP1_STRESSED_SAFE, note="Janela curta de recuperação para capturar retorno após conflito."),
        _stage("camera_overload_repeat", 15, app1={**APP1_CRITICAL, "throughput_mbps": 17.4, "avg_throughput_mbps": 18.6, "latency_ms": 95.0}, note="Pulso final curto para confirmar repetibilidade do conflito RAN."),
        _stage("final_recovery", 20, note="Fechamento com recuperação controlada para dataset balanceado."),
    ],
    "drl_article_conflict_smoke_v1": [
        _stage("baseline_healthy", 1, note="Bootstrap mínimo para validar o caminho de conflito quase imediato."),
        _stage("camera_overload", 8, app1={**APP1_CRITICAL, "throughput_mbps": 18.0, "avg_throughput_mbps": 19.2, "latency_ms": 86.0}, note="Sobrecarga de câmera quase imediata para validar conflito RAN sem longa espera de sim_time."),
        _stage("mixed_overload", 8, app1={**APP1_CRITICAL, "throughput_mbps": 16.5, "avg_throughput_mbps": 17.8, "latency_ms": 92.0}, app2={**APP2_HEALTHY, "connected_sensors": 15, "error_sensors": 2, "low_battery_sensors": 1, "packet_loss_percent": 6.2, "delivery_success_percent": 91.0, "avg_latency_ms": 510.0, "avg_rssi_dbm": -97.0, "avg_battery_percent": 64.0, "avg_power_mw": 228.0, "network_utilization_percent": 88.0, "mode": "mixed_overload"}, vehicle={**VEHICLE_HEALTHY, "medium_risk_vehicles": 1, "degraded_autonomy_vehicles": 1, "ego_latency_ms": 62.0, "traffic_latency_ms": 31.0, "ego_packet_loss_percent": 3.4, "traffic_packet_loss_percent": 1.1, "max_speed_mps": 8.8, "mode": "mixed_overload"}, note="Pulso misto curto para consolidar o regime de conflito após o gatilho inicial."),
        _stage("background_overload", 6, app1={**APP1_GUARD, "throughput_mbps": 22.5, "avg_throughput_mbps": 23.4, "latency_ms": 73.0}, app2={**APP2_HEALTHY, "connected_sensors": 16, "error_sensors": 1, "low_battery_sensors": 1, "packet_loss_percent": 5.4, "delivery_success_percent": 93.4, "avg_latency_ms": 430.0, "avg_rssi_dbm": -95.0, "avg_battery_percent": 66.0, "avg_power_mw": 220.0, "network_utilization_percent": 82.0, "mode": "background_overload"}, note="Competição curta dos UEs background para variar o conflito sem alongar a coleta."),
        _stage("recovery_window", 4, app1=APP1_STRESSED_SAFE, note="Recuperação curta para observar retorno após o conflito."),
        _stage("camera_overload_repeat", 6, app1={**APP1_CRITICAL, "throughput_mbps": 17.4, "avg_throughput_mbps": 18.6, "latency_ms": 95.0}, note="Pulso final curto para confirmar repetibilidade do conflito."),
        _stage("final_recovery", 5, note="Fechamento rápido com recuperação controlada."),
    ],
    "drl_balanced_blocked_v1": [
        _stage("blocked_bootstrap", 4, app1=APP1_WARNING, note="Bootstrap curto antes de travar a coleta no regime BLOCKED."),
        _stage("blocked_camera_overload", 10, app1={**APP1_CRITICAL, "throughput_mbps": 17.8, "avg_throughput_mbps": 18.9, "latency_ms": 88.0}, note="Sobrecarga dominante de câmera para bloquear com consistência."),
        _stage("blocked_mixed_overload", 10, app1={**APP1_CRITICAL, "throughput_mbps": 16.4, "avg_throughput_mbps": 17.3, "latency_ms": 92.0}, app2={**APP2_HEALTHY, "connected_sensors": 15, "error_sensors": 2, "low_battery_sensors": 1, "packet_loss_percent": 6.0, "delivery_success_percent": 90.8, "avg_latency_ms": 505.0, "avg_rssi_dbm": -97.0, "avg_battery_percent": 64.0, "avg_power_mw": 228.0, "network_utilization_percent": 87.0, "mode": "blocked_mixed_overload"}, vehicle={**VEHICLE_HEALTHY, "medium_risk_vehicles": 1, "degraded_autonomy_vehicles": 1, "ego_latency_ms": 63.0, "traffic_latency_ms": 31.0, "ego_packet_loss_percent": 3.3, "traffic_packet_loss_percent": 1.1, "max_speed_mps": 8.8, "mode": "blocked_mixed_overload"}, note="Pressão simultânea para manter o classificador em BLOCKED."),
        _stage("blocked_background_overload", 8, app1={**APP1_GUARD, "throughput_mbps": 22.2, "avg_throughput_mbps": 23.1, "latency_ms": 74.0}, app2={**APP2_HEALTHY, "connected_sensors": 16, "error_sensors": 1, "low_battery_sensors": 1, "packet_loss_percent": 5.4, "delivery_success_percent": 93.0, "avg_latency_ms": 435.0, "avg_rssi_dbm": -95.0, "avg_battery_percent": 66.0, "avg_power_mw": 220.0, "network_utilization_percent": 82.0, "mode": "blocked_background_overload"}, note="Fecha a rodada ainda em sobrecarga para reduzir escapes ALLOWED."),
    ],
    "drl_balanced_borderline_v1": [
        _stage("borderline_bootstrap", 5, app1=APP1_STRESSED_SAFE, note="Entrada saudável controlada para preparar a faixa mista."),
        _stage("borderline_app1_near_guard", 8, app1=APP1_NEAR_GUARD, note="Empurra App1 para perto da guarda sem cair em bloqueio duro."),
        _stage("borderline_app2_stressed_safe", 8, app1=APP1_STRESSED_SAFE, app2={**APP2_HEALTHY, "connected_sensors": 17, "error_sensors": 1, "low_battery_sensors": 0, "packet_loss_percent": 4.5, "delivery_success_percent": 95.6, "avg_latency_ms": 425.0, "avg_rssi_dbm": -93.0, "avg_battery_percent": 70.0, "avg_power_mw": 210.0, "network_utilization_percent": 78.0, "mode": "borderline_app2_stressed_safe"}, note="Janela para manter mistura de ALLOWED e CONDITIONAL no background."),
        _stage("borderline_vehicle_warning", 5, app1=APP1_STRESSED_SAFE, vehicle={**VEHICLE_HEALTHY, "medium_risk_vehicles": 1, "ego_latency_ms": 58.0, "traffic_latency_ms": 28.0, "ego_packet_loss_percent": 2.5, "traffic_packet_loss_percent": 0.8, "max_speed_mps": 8.0, "mode": "borderline_vehicle_warning"}, note="Warning veicular leve para estimular CONDITIONAL sem colapsar o cenário."),
        _stage("borderline_recovery", 4, app1=APP1_HEALTHY, note="Recuperação curta para voltar a produzir ALLOWED."),
    ],
    "drl_allowed_only_v1": [
        _stage(
            "allowed_bootstrap",
            8,
            app1=APP1_HEALTHY,
            app2=APP2_ALLOWED_STABLE,
            vehicle=VEHICLE_ALLOWED_STABLE,
            note="Aquecimento curto em zona totalmente saudável antes da janela principal.",
        ),
        _stage(
            "allowed_stable_window",
            24,
            app1={**APP1_HEALTHY, "throughput_mbps": 34.0, "avg_throughput_mbps": 35.0, "latency_ms": 14.0},
            app2=APP2_ALLOWED_STABLE,
            vehicle=VEHICLE_ALLOWED_STABLE,
            note="Mantém App1, App2 e veículo longe das guardas para maximizar decisões ALLOWED estáveis.",
        ),
        _stage(
            "allowed_eco_window",
            18,
            app1={**APP1_HEALTHY, "throughput_mbps": 35.0, "avg_throughput_mbps": 35.8, "latency_ms": 12.0},
            app2={**APP2_ALLOWED_STABLE, "avg_latency_ms": 105.0, "network_utilization_percent": 44.0, "mode": "allowed_eco_window"},
            vehicle={**VEHICLE_ALLOWED_STABLE, "ego_latency_ms": 10.0, "traffic_latency_ms": 7.0, "mode": "allowed_eco_window"},
            note="Janela ainda mais estável para favorecer ALLOWED por CVaR/P95 e reduzir vazamento CONDITIONAL.",
        ),
    ],
}

# The v2 training manifest uses the same nine-stage GreenRAN event schedule
# as the restored v1 alternator. Keep both names explicit so the training,
# collection, and external-validation pipelines cannot silently diverge.
PROFILES["tasam_training_balanced_v2"] = PROFILES["tasam_training_balanced_v1"]

# v3 keeps the exact event payloads from v1/v2, but gives every stage enough
# wall-clock time for the rApp decision and its following real-PDCP metric
# snapshot to be paired into a trainable transition. The former 3-second
# BLOCKED windows routinely produced raw records without next_metrics.
PROFILES["tasam_training_balanced_v3"] = [
    replace(stage, duration_s=12) for stage in PROFILES["tasam_training_balanced_v1"]
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alternate collection events by updating the GreenRAN scenario-control file.")
    parser.add_argument("--profile", default="drl_article_v1", choices=sorted(PROFILES), help="event profile to cycle")
    parser.add_argument("--cycles", type=int, default=0, help="number of full profile cycles; 0 means loop forever")
    parser.add_argument("--state-file", type=Path, default=ARTICLE00_SCENARIO_CONTROL_PATH, help="scenario-control JSON path")
    parser.add_argument("--tick-s", type=float, default=1.0, help="poll interval while waiting for sim_time progression")
    parser.add_argument("--time-source", choices=("sim", "wall"), default="sim", help="drive stage progression from ns-3 sim time or wall clock")
    return parser.parse_args()


def handle_stop(signum: int, _frame: Any) -> None:
    del signum
    global SHOULD_STOP
    SHOULD_STOP = True


def total_cycle_duration(profile: list[Stage]) -> int:
    return sum(stage.duration_s for stage in profile)


def get_extended_metrics_path(state_file: Path) -> Path:
    return state_file.parent / "xapp_metrics" / "extended_metrics.json"


def read_sim_time_s(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0
    sim_time_range = payload.get("sim_time_range", {}) if isinstance(payload, dict) else {}
    if not isinstance(sim_time_range, dict):
        return 0.0
    try:
        return max(0.0, float(sim_time_range.get("end", 0.0) or 0.0))
    except (TypeError, ValueError):
        return 0.0


def get_stage_position(profile: list[Stage], sim_time_s: float) -> StagePosition:
    cycle_duration = max(total_cycle_duration(profile), 1)
    cycle_index = int(max(sim_time_s, 0.0) // cycle_duration) + 1
    cycle_elapsed_s = max(sim_time_s, 0.0) % cycle_duration

    elapsed_before = 0.0
    for stage_index, stage in enumerate(profile, start=1):
        stage_end = elapsed_before + stage.duration_s
        if cycle_elapsed_s < stage_end or stage_index == len(profile):
            stage_elapsed_s = max(cycle_elapsed_s - elapsed_before, 0.0)
            return StagePosition(
                cycle_index=cycle_index,
                stage_index=stage_index,
                sim_time_s=round(max(sim_time_s, 0.0), 3),
                cycle_elapsed_s=round(cycle_elapsed_s, 3),
                stage_elapsed_s=round(stage_elapsed_s, 3),
                stage_remaining_s=round(max(stage.duration_s - stage_elapsed_s, 0.0), 3),
                stage=stage,
            )
        elapsed_before = stage_end

    fallback = profile[-1]
    return StagePosition(
        cycle_index=cycle_index,
        stage_index=len(profile),
        sim_time_s=round(max(sim_time_s, 0.0), 3),
        cycle_elapsed_s=round(cycle_elapsed_s, 3),
        stage_elapsed_s=float(fallback.duration_s),
        stage_remaining_s=0.0,
        stage=fallback,
    )


def write_payload(path: Path, profile_name: str, position: StagePosition) -> None:
    if DISABLE_APP_OVERRIDES:
        app1_payload = {
            "enabled": False,
            "source": "collection_event_alternator",
            "mode": "real_only_collection",
        }
        app2_payload = {
            "enabled": False,
            "source": "collection_event_alternator",
            "mode": "real_only_collection",
        }
        vehicle_payload = {
            "enabled": False,
            "source": "collection_event_alternator",
            "mode": "real_only_collection",
        }
    else:
        app1_payload = position.stage.app1
        app2_payload = position.stage.app2
        vehicle_payload = position.stage.vehicle

    stage_name = position.stage.name.lower()
    if DISABLE_APP_OVERRIDES:
        network_health_override = {
            "enabled": False,
            "source": "collection_event_alternator",
            "mode": "real_only_collection",
        }
    elif "allowed" in stage_name or "bootstrap" in stage_name or "recovery" in stage_name:
        network_health_override = {
            "enabled": True,
            "cvar_us": 32000.0,
            "latest_cvar_us": 32000.0,
            "p95_us": 24000.0,
            "variance_us2": 1.8e8,
            "stability_score": 92.0,
            "slope_ms_per_sec": 0.0,
            "slope_us_per_sec": 0.0,
            "current_latency_ms": 24.0,
            "current_latency_us": 24000.0,
            "time_to_critical_ms": None,
            "time_to_good_ms": None,
            "confidence": 1.0,
            "r_squared": 1.0,
            "n_samples": 999,
            "mode": "allowed",
        }
    elif "conditional" in stage_name or "warning" in stage_name:
        network_health_override = {
            "enabled": True,
            "cvar_us": 95000.0,
            "latest_cvar_us": 95000.0,
            "p95_us": 72000.0,
            "variance_us2": 9.5e8,
            "stability_score": 74.0,
            "slope_ms_per_sec": 0.18,
            "slope_us_per_sec": 180.0,
            "current_latency_ms": 72.0,
            "current_latency_us": 72000.0,
            "time_to_critical_ms": 433.3,
            "time_to_good_ms": None,
            "confidence": 1.0,
            "r_squared": 1.0,
            "n_samples": 999,
            "mode": "conditional",
        }
    else:
        network_health_override = {
            "enabled": True,
            "cvar_us": 118000.0,
            "latest_cvar_us": 118000.0,
            "p95_us": 78000.0,
            "variance_us2": 1.2e9,
            "stability_score": 68.0,
            "slope_ms_per_sec": 0.22,
            "slope_us_per_sec": 220.0,
            "current_latency_ms": 78.0,
            "current_latency_us": 78000.0,
            "time_to_critical_ms": 327.3,
            "time_to_good_ms": None,
            "confidence": 1.0,
            "r_squared": 1.0,
            "n_samples": 999,
            "mode": "blocked_domain_priority",
        }

    payload = {
        "schema": "greenran.scenario_control.v1",
        "generated_at": int(time.time()),
        "generated_at_iso": datetime.now().isoformat(timespec="seconds"),
        "scenario": position.stage.name,
        "collection_event_profile": profile_name,
        "collection_event_cycle": position.cycle_index,
        "collection_event_stage_index": position.stage_index,
        "collection_event_stage_name": position.stage.name,
        "collection_event_target_domain": position.stage.target_domain,
        "duration_s": position.stage.duration_s,
        "note": position.stage.note,
        "sim_time_s": position.sim_time_s,
        "cycle_elapsed_s": position.cycle_elapsed_s,
        "stage_elapsed_s": position.stage_elapsed_s,
        "stage_remaining_s": position.stage_remaining_s,
        "app1_camera_override": app1_payload,
        "app2_sensor_override": app2_payload,
        "vehicle_override": vehicle_payload,
        "network_health_override": network_health_override,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def clear_payload(path: Path) -> None:
    if path.exists():
        path.unlink()


def run_sim_time_driven(args: argparse.Namespace, profile: list[Stage]) -> int:
    cycle_limit = max(0, int(args.cycles))
    metrics_path = get_extended_metrics_path(args.state_file)
    last_key: tuple[int, int] | None = None

    while not SHOULD_STOP:
        sim_time_s = read_sim_time_s(metrics_path)
        position = get_stage_position(profile, sim_time_s)
        if cycle_limit and position.cycle_index > cycle_limit:
            break

        current_key = (position.cycle_index, position.stage_index)
        if current_key != last_key:
            write_payload(args.state_file, args.profile, position)
            print(
                f"[collection-event] mode=sim cycle={position.cycle_index} "
                f"stage={position.stage_index}/{len(profile)} name={position.stage.name} "
                f"sim_time={position.sim_time_s:.3f}s remaining={position.stage_remaining_s:.3f}s "
                f"note={position.stage.note}",
                flush=True,
            )
            last_key = current_key

        time.sleep(max(args.tick_s, 0.1))

    return 0


def run_wall_clock_driven(args: argparse.Namespace, profile: list[Stage]) -> int:
    cycle_limit = max(0, int(args.cycles))
    cycle_index = 0
    while not SHOULD_STOP and (cycle_limit == 0 or cycle_index < cycle_limit):
        cycle_index += 1
        for stage_index, stage in enumerate(profile, start=1):
            if SHOULD_STOP:
                break
            position = StagePosition(
                cycle_index=cycle_index,
                stage_index=stage_index,
                sim_time_s=0.0,
                cycle_elapsed_s=0.0,
                stage_elapsed_s=0.0,
                stage_remaining_s=float(stage.duration_s),
                stage=stage,
            )
            write_payload(args.state_file, args.profile, position)
            print(
                f"[collection-event] mode=wall cycle={cycle_index} stage={stage_index}/{len(profile)} "
                f"name={stage.name} duration={stage.duration_s}s note={stage.note}",
                flush=True,
            )
            deadline = time.monotonic() + stage.duration_s
            while not SHOULD_STOP:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(max(args.tick_s, 0.1), remaining))
    return 0


def main() -> int:
    args = parse_args()
    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    profile = PROFILES[args.profile]
    try:
        if args.time_source == "sim":
            return run_sim_time_driven(args, profile)
        return run_wall_clock_driven(args, profile)
    finally:
        clear_payload(args.state_file)
        print("[collection-event] scenario control cleared", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
