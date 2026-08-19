#!/usr/bin/env python3
"""Generate explicit IMSI role metadata for the isolated TA-SAM article ns-3 track."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_SENSOR_PROFILES = [
    {"sensor_type": "temperature", "unit": "°C", "connectivity": "5g_redcap", "gateway_id": "GW-ART-01", "domain": "environmental", "nominal_value": 27.0, "nominal_power_mw": 180.0},
    {"sensor_type": "humidity", "unit": "%", "connectivity": "5g_redcap", "gateway_id": "GW-ART-01", "domain": "environmental", "nominal_value": 82.0, "nominal_power_mw": 185.0},
    {"sensor_type": "soil_moisture", "unit": "%", "connectivity": "5g_native", "gateway_id": "GW-ART-02", "domain": "soil", "nominal_value": 58.0, "nominal_power_mw": 520.0},
    {"sensor_type": "air_quality", "unit": "AQI", "connectivity": "5g_native", "gateway_id": "GW-ART-03", "domain": "environmental", "nominal_value": 42.0, "nominal_power_mw": 540.0},
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate device_roles.json for TA-SAM article ns-3 collection')
    parser.add_argument('--config', required=True, help='Scenario JSON config')
    parser.add_argument('--state-dir', required=True, help='Runtime state dir')
    parser.add_argument('--ue-count', type=int, default=None, help='Override total UE count for runtime-scaled collection')
    parser.add_argument('--camera-ue-count', type=int, default=None, help='Override camera UE count for runtime-scaled collection')
    parser.add_argument('--vehicle-ue-count', type=int, default=None, help='Override vehicle UE count for runtime-scaled collection')
    return parser


def expand_range(bounds):
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        return []
    start = int(bounds[0])
    end = int(bounds[1])
    if end < start:
        return []
    return list(range(start, end + 1))


def build_runtime_ranges(ue_count, camera_ue_count, vehicle_ue_count):
    total = max(0, int(ue_count or 0))
    cameras = max(0, min(total, int(camera_ue_count or 0)))
    vehicles = max(0, min(total - cameras, int(vehicle_ue_count or 0)))
    background = max(0, total - cameras - vehicles)

    camera_imsis = list(range(1, cameras + 1))
    sensor_start = cameras + 1
    sensor_end = cameras + background
    sensor_imsis = list(range(sensor_start, sensor_end + 1)) if background > 0 else []
    vehicle_start = total - vehicles + 1
    vehicle_imsis = list(range(vehicle_start, total + 1)) if vehicles > 0 else []
    return camera_imsis, sensor_imsis, vehicle_imsis


def main() -> int:
    args = build_parser().parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    state_dir = Path(args.state_dir)
    output_path = state_dir / 'xapp_metrics' / 'device_roles.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)

    roles = {}
    ns3_cfg = cfg.get('ns3', {}) if isinstance(cfg, dict) else {}
    app3_cfg = (cfg.get('apps', {}) or {}).get('app3', {}) if isinstance(cfg, dict) else {}
    device_roles = cfg.get('device_roles', {}) if isinstance(cfg, dict) else {}

    if args.ue_count is not None:
        camera_imsis, sensor_imsis, vehicle_imsis = build_runtime_ranges(
            args.ue_count,
            args.camera_ue_count,
            args.vehicle_ue_count,
        )
    else:
        camera_imsis = ns3_cfg.get('camera_imsis') or expand_range(device_roles.get('camera_imsi_range'))
        sensor_imsis = expand_range(device_roles.get('sensor_imsi_range') or ns3_cfg.get('background_imsi_range'))
        vehicle_imsis = expand_range(device_roles.get('vehicle_imsi_range') or app3_cfg.get('vehicle_imsi_range'))

    for idx, imsi in enumerate(camera_imsis):
        roles[str(int(imsi))] = {
            'device_type': 'camera',
            'label': f'article_camera_{idx + 1:03d}',
            'domain': 'embb',
            'mobility_profile': 'fixed',
        }

    for idx, imsi in enumerate(sensor_imsis):
        profile = DEFAULT_SENSOR_PROFILES[idx % len(DEFAULT_SENSOR_PROFILES)]
        roles[str(int(imsi))] = {
            'device_type': 'sensor',
            'label': f'article_sensor_{idx + 1:03d}',
            'sensor_type': profile['sensor_type'],
            'unit': profile['unit'],
            'connectivity': profile['connectivity'],
            'gateway_id': profile['gateway_id'],
            'domain': profile['domain'],
            'nominal_value': profile['nominal_value'],
            'nominal_power_mw': profile['nominal_power_mw'],
            'nominal_battery_percent': 100.0,
            'nominal_rssi_dbm': -88.0,
            'tx_interval_s': 5 if profile['connectivity'] == '5g_native' else 10,
            'mobility_profile': 'pedestrian' if idx % 4 == 0 else 'stationary',
        }

    for idx, imsi in enumerate(vehicle_imsis):
        roles[str(int(imsi))] = {
            'device_type': 'vehicle',
            'label': f'article_vehicle_{idx + 1:03d}',
            'vehicle_id': f'article-veh-{idx + 1:03d}',
            'vehicle_role': 'ego' if idx == 0 else 'traffic',
            'connectivity': '5g_native',
            'gateway_id': 'GW-VEH-ARTICLE',
            'domain': 'vehicular',
            'mobility_profile': 'vehicle',
            'autonomy_state': 'normal',
            'risk_state': 'low',
        }

    payload = {
        'generated_by': 'generate_article_ns3_device_roles.py',
        'scenario': cfg.get('scenario_id', 'tasam_article_ns3_collection_v1'),
        'roles': roles,
        'counts': {
            'camera': len(camera_imsis),
            'sensor': len(sensor_imsis),
            'vehicle': len(vehicle_imsis),
        },
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output_path), 'counts': payload['counts']}, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
