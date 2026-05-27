#!/usr/bin/env python3
"""
Carrega configuracao versionavel de runtime do GreenRAN Core.

Prioridade:
1. Valores do arquivo em config/core/runtime.json
2. Overrides por variaveis de ambiente GREENRAN_*
3. Defaults internos de fallback
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict

from greenran_paths import CONFIG_DIR


RUNTIME_CONFIG_PATH = CONFIG_DIR / "core" / "runtime.json"

DEFAULT_RUNTIME_CONFIG: Dict[str, Any] = {
    "dashboard": {
        "host": "0.0.0.0",
        "port": 5000,
        "debug": False,
    },
    "orchestrator": {
        "interval_seconds": 5,
        "synthetic_days": 0,
    },
    "monitoring": {
        "grafana_port": 3001,
        "influxdb_host": "localhost",
        "influxdb_port": 8086,
        "influxdb_db": "influx",
        "gui_port": 8000,
        "push_interval_seconds": 5,
    },
    "collector": {
        "poll_interval_seconds": 1.0,
    },
    "simulation": {
        "default_sim_time_seconds": 600,
    },
    "shared_resources": {
        "r_max": 1.0,
        "ran_min_share": 0.35,
        "ai_min_share": 0.15,
        "ran_priority_bias": 1.35,
        "allocation_smoothing": 0.35,
        "min_utilization_ratio": 0.55,
        "target_camera_capacity": 4.0,
        "target_sensor_capacity": 12.0,
        "target_vehicle_capacity": 5.0,
        "camera_throughput_target_mbps": 25.0,
        "camera_headroom_target_mbps": 35.0,
        "cvar_target_ms": 120.0,
        "p95_target_ms": 80.0,
        "app2_latency_target_ms": 1000.0,
        "vehicle_latency_target_ms": 120.0,
        "vehicle_loss_target_pct": 10.0,
        "ran_min_active_demand": 0.15,
        "ai_min_active_demand": 0.15
    },
}


def _deep_update(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    env_map = {
        "GREENRAN_DASHBOARD_HOST": ("dashboard", "host", str),
        "GREENRAN_DASHBOARD_PORT": ("dashboard", "port", int),
        "GREENRAN_DASHBOARD_DEBUG": ("dashboard", "debug", _parse_bool),
        "GREENRAN_ORCHESTRATOR_INTERVAL": ("orchestrator", "interval_seconds", float),
        "GREENRAN_ORCHESTRATOR_SYNTHETIC_DAYS": ("orchestrator", "synthetic_days", int),
        "GREENRAN_PUSH_INTERVAL": ("monitoring", "push_interval_seconds", int),
        "GREENRAN_INFLUXDB_HOST": ("monitoring", "influxdb_host", str),
        "GREENRAN_INFLUXDB_PORT": ("monitoring", "influxdb_port", int),
        "GREENRAN_INFLUXDB_DB": ("monitoring", "influxdb_db", str),
        "GREENRAN_COLLECTOR_POLL_INTERVAL": ("collector", "poll_interval_seconds", float),
        "GREENRAN_SIM_TIME": ("simulation", "default_sim_time_seconds", int),
        "GREENRAN_RESOURCE_R_MAX": ("shared_resources", "r_max", float),
        "GREENRAN_RESOURCE_RAN_MIN_SHARE": ("shared_resources", "ran_min_share", float),
        "GREENRAN_RESOURCE_AI_MIN_SHARE": ("shared_resources", "ai_min_share", float),
        "GREENRAN_RESOURCE_RAN_PRIORITY_BIAS": ("shared_resources", "ran_priority_bias", float),
    }

    for env_name, (section, key, caster) in env_map.items():
        value = os.environ.get(env_name)
        if value is None:
            continue
        config[section][key] = caster(value)

    return config


def load_runtime_config() -> Dict[str, Any]:
    config = deepcopy(DEFAULT_RUNTIME_CONFIG)

    if RUNTIME_CONFIG_PATH.exists():
        try:
            with open(RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as f:
                file_config = json.load(f)
            _deep_update(config, file_config)
        except Exception as exc:
            print(f"[RuntimeConfig] AVISO: falha ao carregar {RUNTIME_CONFIG_PATH}: {exc}")

    return _apply_env_overrides(config)
