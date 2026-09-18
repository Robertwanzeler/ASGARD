#!/usr/bin/env python3
"""
GreenRAN O-RAN - Extended Metrics Collector
==========================================
Reads all available stats from ns-3 simulation and generates comprehensive JSON metrics.

Sources:
    - DlPdcpStats.txt: PDCP layer metrics (ground truth for latency)
    - DlMacStats.txt: MAC layer metrics (MCS, TB size)
    - DlRlcStats.txt: RLC layer metrics
    - DlE2PdcpStats.txt / DlE2RlcStats.txt: E2-exported bearer stats when classic files are absent
    - cu-up-cell-*.txt: CU-UP metrics (throughput)

Usage:
    python3 csv_to_metrics.py [--input-dir DIR] [--output FILE] [--extended-output FILE] [--poll-interval SECONDS]
"""

import os
import sys
import json
import time
import argparse
import math
import traceback
from pathlib import Path
import threading
import signal
from collections import defaultdict
from greenran_paths import (
    NS3_DIR,
    METRICS_JSON_PATH,
    EXTENDED_METRICS_JSON_PATH,
    STATE_DIR,
    ARTICLE00_SCENARIO_CONTROL_PATH,
    CARLA_STATE_DIR,
    CARLA_VEHICLES_PATH,
    CARLA_VEHICLE_MAP_PATH,
    get_fixed_max_vehicles,
    get_fixed_vehicle_base_imsi,
    get_fixed_service_imsis,
    as_str,
)
from greenran_runtime import load_runtime_config

RUNTIME_CONFIG = load_runtime_config()
DEFAULT_INPUT_DIR = as_str(NS3_DIR)
DEFAULT_OUTPUT_FILE = as_str(METRICS_JSON_PATH)
DEFAULT_EXTENDED_OUTPUT_FILE = as_str(EXTENDED_METRICS_JSON_PATH)
DEFAULT_POLL_INTERVAL = float(RUNTIME_CONFIG["collector"]["poll_interval_seconds"])
TRACE_FILE_CANDIDATES = {
    "pdcp": [
        "DlPdcpStats.txt",
        "LteDlPdcpStats.txt",
        "DlE2PdcpStats.txt",
        "DlE2PdcpStatsLte.txt",
    ],
    "rlc": [
        "DlRlcStats.txt",
        "LteDlRlcStats.txt",
        "DlE2RlcStats.txt",
        "DlE2RlcStatsLte.txt",
    ],
}

_FIXED_SERVICE_IMSIS = get_fixed_service_imsis()
CAMERA_IMSI_RANGE = (
    min(_FIXED_SERVICE_IMSIS["camera"]),
    max(_FIXED_SERVICE_IMSIS["camera"]),
)
SENSOR_IMSI_RANGE = (
    min(_FIXED_SERVICE_IMSIS["sensor"]),
    max(_FIXED_SERVICE_IMSIS["sensor"]),
)
VEHICLE_IMSI_RANGE = (
    min(_FIXED_SERVICE_IMSIS["vehicle"]),
    max(_FIXED_SERVICE_IMSIS["vehicle"]),
)
UE_IMSI_RANGE = VEHICLE_IMSI_RANGE
DEFAULT_SENSOR_PROFILES = [
    {"sensor_type": "temperature", "unit": "°C", "connectivity": "5g_redcap", "gateway_id": "GW-5G-01", "domain": "environmental", "nominal_value": 27.0, "nominal_power_mw": 180.0},
    {"sensor_type": "humidity", "unit": "%", "connectivity": "5g_redcap", "gateway_id": "GW-5G-01", "domain": "environmental", "nominal_value": 82.0, "nominal_power_mw": 185.0},
    {"sensor_type": "soil_moisture", "unit": "%", "connectivity": "5g_native", "gateway_id": "GW-5G-02", "domain": "soil", "nominal_value": 58.0, "nominal_power_mw": 520.0},
    {"sensor_type": "soil_temp", "unit": "°C", "connectivity": "5g_native", "gateway_id": "GW-5G-02", "domain": "soil", "nominal_value": 24.0, "nominal_power_mw": 500.0},
    {"sensor_type": "soil_conductivity", "unit": "dS/m", "connectivity": "5g_redcap", "gateway_id": "GW-5G-02", "domain": "soil", "nominal_value": 1.15, "nominal_power_mw": 210.0},
    {"sensor_type": "soil_nitrogen", "unit": "mg/kg", "connectivity": "5g_redcap", "gateway_id": "GW-5G-02", "domain": "soil", "nominal_value": 26.0, "nominal_power_mw": 215.0},
    {"sensor_type": "air_quality", "unit": "AQI", "connectivity": "5g_native", "gateway_id": "GW-5G-03", "domain": "environmental", "nominal_value": 42.0, "nominal_power_mw": 540.0},
    {"sensor_type": "rain_intensity", "unit": "mm/h", "connectivity": "5g_redcap", "gateway_id": "GW-5G-03", "domain": "environmental", "nominal_value": 1.0, "nominal_power_mw": 195.0},
    {"sensor_type": "solar_radiation", "unit": "W/m²", "connectivity": "5g_native", "gateway_id": "GW-5G-03", "domain": "environmental", "nominal_value": 620.0, "nominal_power_mw": 530.0},
]

class ExtendedMetricsCollector:
    def __init__(self, input_dir, output_file, extended_output_file, poll_interval):
        self.input_dir = Path(input_dir)
        self.output_file = output_file
        self.extended_output_file = extended_output_file
        self.poll_interval = poll_interval
        self.running = True
        self.lock = threading.Lock()

        # Persistência de UEs (Memória de 5 segundos)
        self.active_ues_cache = {} # imsi -> last_seen_timestamp
        self.activity_window = 5.0 # 5 segundos

        # Throughput delta tracking
        self.last_total_tx_bytes = 0
        self.last_total_rx_bytes = 0
        self.last_sim_time = 0.0
        # v9_fidelity keeps PDCP real but avoids rereading the complete trace
        # on every wall-clock poll.  The cache is reset on inode change or
        # truncation and only complete newline-terminated records are parsed.
        self._pdcp_cache = []
        self._pdcp_offset = 0
        self._pdcp_inode = 0
        
        # Packet loss tracking (simulated based on network conditions)
        self.packet_loss_baseline = 0.001  # 0.1% baseline
        self.last_packet_loss = 0.0
        self.packet_loss_history = []
        self.device_role_map_path = STATE_DIR / "xapp_metrics" / "device_roles.json"
        self.device_role_map = {}
        self.device_role_map_mtime = None
        self.app2_state_dir = STATE_DIR / "app2_monitoramento"
        self.app2_sensors_dir = self.app2_state_dir / "sensors"
        self.app2_sensors_file = self.app2_sensors_dir / "latest.json"
        self.app2_snapshot_file = self.app2_state_dir / "monitoring_snapshot.json"
        self.app3_monitoring_file = STATE_DIR / "app3_veicular" / "monitoring_snapshot.json"
        self.scenario_control_path = ARTICLE00_SCENARIO_CONTROL_PATH
        self.carla_state_dir = CARLA_STATE_DIR
        self.carla_vehicles_path = CARLA_VEHICLES_PATH
        self.carla_vehicle_map_path = CARLA_VEHICLE_MAP_PATH
        self.carla_vehicle_map = {}
        self.carla_vehicle_state = {}
        self.carla_vehicle_map_mtime = None
        self.carla_vehicle_state_mtime = None
        self._warned_missing_pdcp = False
        self.trace_stale_threshold_s = float(os.environ.get("GREENRAN_PDCP_STALE_SECONDS", "30"))
        fixed_scenario_config = os.environ.get("GREENRAN_FIXED_SCENARIO_CONFIG", "")
        state_dir_hint = os.environ.get("GREENRAN_STATE_DIR", "")
        article_collection_runtime = "tasam_article_ns3_collection" in fixed_scenario_config or "tasam_article_ns3_collection" in state_dir_hint
        self.require_real_pdcp = article_collection_runtime or os.environ.get("GREENRAN_REQUIRE_REAL_PDCP", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        os.makedirs(os.path.dirname(extended_output_file), exist_ok=True)
        os.makedirs(self.app2_sensors_dir, exist_ok=True)
        os.makedirs(self.carla_state_dir, exist_ok=True)
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _calculate_packet_loss(self, worst_latency_us, critical_count, camera_count):
        """
        Calculate simulated packet loss based on network conditions.
        
        Factors:
        - Higher latency/jitter increases packet loss
        - Critical UEs indicate problems
        - Camera count affects congestion
        """
        import random
        
        # Base packet loss: 0.1% to 0.5% in normal conditions
        base_rate = self.packet_loss_baseline
        
        # Increase based on network conditions
        multiplier = 1.0
        
        # High latency increases packet loss
        if worst_latency_us > 80000:  # > 80ms
            multiplier += 2.0  # Strong indicator of congestion
        elif worst_latency_us > 50000:  # > 50ms
            multiplier += 1.0
        elif worst_latency_us > 30000:  # > 30ms
            multiplier += 0.5
        
        # Critical UEs increase packet loss significantly
        if critical_count > 0:
            multiplier += float(critical_count) * 0.5
        
        # Many cameras can cause congestion
        if camera_count >= 5:
            multiplier += 0.5
        
        # Calculate final rate (0.1% to 5% max)
        packet_loss = min(base_rate * multiplier, 0.05)
        
        # Add some randomness (±20%)
        packet_loss *= random.uniform(0.8, 1.2)
        
        # Keep history for smoothing
        self.packet_loss_history.append(packet_loss)
        if len(self.packet_loss_history) > 10:
            self.packet_loss_history.pop(0)
        
        # Return moving average
        if self.packet_loss_history:
            return sum(self.packet_loss_history) / len(self.packet_loss_history)
        return packet_loss
    
    def _signal_handler(self, signum, frame):
        self.running = False

    def _trace_status(self, path, metrics=None, stale_threshold_s=None):
        threshold = self.trace_stale_threshold_s if stale_threshold_s is None else float(stale_threshold_s)
        status = {
            'path': str(path),
            'exists': False,
            'size_bytes': 0,
            'mtime': 0.0,
            'age_s': None,
            'stale_threshold_s': threshold,
            'stale': False,
            'latest_sim_time_s': None,
        }
        try:
            path = Path(path)
            if not path.exists():
                return status
            stat = path.stat()
            age_s = max(0.0, time.time() - stat.st_mtime)
            status.update({
                'exists': True,
                'size_bytes': int(stat.st_size),
                'mtime': float(stat.st_mtime),
                'age_s': age_s,
                'stale': age_s > threshold,
            })
            if metrics:
                latest = max(
                    (float(m.get('time_end', m.get('time', 0.0)) or 0.0) for m in metrics),
                    default=None,
                )
                status['latest_sim_time_s'] = latest
        except Exception as e:
            status['error'] = str(e)
        return status

    def _attach_trace_status(self, metrics, trace_status, collector_mode=None):
        gm = metrics.setdefault('global_metrics', {})
        gm['trace_file_status'] = trace_status
        pdcp_status = trace_status.get('pdcp', {}) if isinstance(trace_status, dict) else {}
        rlc_status = trace_status.get('rlc', {}) if isinstance(trace_status, dict) else {}
        mac_status = trace_status.get('mac', {}) if isinstance(trace_status, dict) else {}
        gm['pdcp_stale'] = bool(pdcp_status.get('stale'))
        gm['rlc_stale'] = bool(rlc_status.get('stale'))
        gm['mac_stale'] = bool(mac_status.get('stale'))
        gm['pdcp_trace_age_s'] = pdcp_status.get('age_s')
        gm['rlc_trace_age_s'] = rlc_status.get('age_s')
        gm['mac_trace_age_s'] = mac_status.get('age_s')
        gm['pdcp_latest_sim_time_s'] = pdcp_status.get('latest_sim_time_s')
        if collector_mode:
            gm['collector_mode'] = collector_mode
        return metrics

    def _resolve_trace_file(self, trace_kind):
        candidates = TRACE_FILE_CANDIDATES.get(trace_kind, [])
        if not candidates:
            raise KeyError(f"unknown trace kind: {trace_kind}")

        fallback = self.input_dir / candidates[0]
        for filename in candidates:
            candidate = self.input_dir / filename
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        return fallback

    def _refresh_device_role_map(self):
        """Reload explicit IMSI role metadata when the scenario updates it."""
        try:
            if not self.device_role_map_path.exists():
                self.device_role_map = {}
                self.device_role_map_mtime = None
                return

            mtime = self.device_role_map_path.stat().st_mtime
            if self.device_role_map_mtime is not None and mtime == self.device_role_map_mtime:
                return

            with open(self.device_role_map_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            roles = payload.get("roles", payload) if isinstance(payload, dict) else {}
            self.device_role_map = roles if isinstance(roles, dict) else {}
            self.device_role_map_mtime = mtime
        except Exception as e:
            print(f"[CSV_METRICS] Error reloading device role metadata: {e}")
            self.device_role_map = {}
            self.device_role_map_mtime = None

    def _refresh_carla_vehicle_map(self):
        try:
            if not self.carla_vehicle_map_path.exists():
                self.carla_vehicle_map = {}
                self.carla_vehicle_map_mtime = None
                return

            mtime = self.carla_vehicle_map_path.stat().st_mtime
            if self.carla_vehicle_map_mtime is not None and mtime == self.carla_vehicle_map_mtime:
                return

            with open(self.carla_vehicle_map_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            roles = payload.get("roles", {}) if isinstance(payload, dict) else {}
            self.carla_vehicle_map = roles if isinstance(roles, dict) else {}
            self.carla_vehicle_map_mtime = mtime
        except Exception as e:
            print(f"[CSV_METRICS] Error reloading CARLA vehicle map: {e}")
            self.carla_vehicle_map = {}
            self.carla_vehicle_map_mtime = None

    def _refresh_carla_vehicle_state(self):
        try:
            if not self.carla_vehicles_path.exists():
                self.carla_vehicle_state = {}
                self.carla_vehicle_state_mtime = None
                return

            mtime = self.carla_vehicles_path.stat().st_mtime
            if self.carla_vehicle_state_mtime is not None and mtime == self.carla_vehicle_state_mtime:
                return

            with open(self.carla_vehicles_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            vehicles = payload.get("vehicles", []) if isinstance(payload, dict) else []
            self.carla_vehicle_state = {
                str(vehicle.get("vehicle_id")): vehicle
                for vehicle in vehicles
                if isinstance(vehicle, dict) and vehicle.get("vehicle_id")
            }
            self.carla_vehicle_state_mtime = mtime
        except Exception as e:
            print(f"[CSV_METRICS] Error reloading CARLA vehicle state: {e}")
            self.carla_vehicle_state = {}
            self.carla_vehicle_state_mtime = None

    def get_carla_vehicle_binding(self, imsi):
        self._refresh_carla_vehicle_map()
        meta = self.carla_vehicle_map.get(str(imsi), {})
        return meta if isinstance(meta, dict) and meta else {}

    def get_carla_vehicle_state(self, vehicle_id):
        self._refresh_carla_vehicle_state()
        if not vehicle_id:
            return {}
        state = self.carla_vehicle_state.get(str(vehicle_id), {})
        return state if isinstance(state, dict) else {}

    def _estimate_proxy_latency_us(self, imsi, device_type, throughput_kbps, mac_entry, rlc_entry, vehicle_state=None):
        """Estimate explicit proxy latency when ns-3 did not emit PDCP/CU-UP latency."""
        try:
            imsi_num = int(imsi)
        except (TypeError, ValueError):
            imsi_num = 0

        mcs_values = mac_entry.get('mcs', []) if mac_entry else []
        tb_values = mac_entry.get('tb_sizes', []) if mac_entry else []
        avg_mcs = sum(mcs_values) / len(mcs_values) if mcs_values else 10.0
        avg_tb = sum(tb_values) / len(tb_values) if tb_values else 0.0
        rlc_penalty_ms = 20.0 if rlc_entry else 0.0
        jitter_ms = float((imsi_num * 7) % 23)

        if device_type == 'vehicle':
            if vehicle_state and vehicle_state.get('latency_ms') is not None:
                return max(1.0, float(vehicle_state.get('latency_ms') or 0.0) * 1000.0), 'vehicle_state_proxy'
            risk_penalty = 45.0 if (imsi_num % 5) == 0 else 15.0 if (imsi_num % 3) == 0 else 0.0
            mcs_penalty = max(0.0, 18.0 - avg_mcs) * 2.0
            throughput_penalty = max(0.0, 1200.0 - float(throughput_kbps or 0.0)) / 40.0
            latency_ms = 35.0 + risk_penalty + mcs_penalty + throughput_penalty + jitter_ms
            return max(1.0, latency_ms * 1000.0), 'app3_vehicle_proxy'

        if device_type == 'sensor':
            mcs_penalty = max(0.0, 15.0 - avg_mcs) * 6.0
            throughput_penalty = max(0.0, 64.0 - float(throughput_kbps or 0.0)) * 1.6
            tb_penalty = 35.0 if avg_tb <= 0 else max(0.0, 180.0 - avg_tb) / 4.0
            latency_ms = 95.0 + mcs_penalty + throughput_penalty + tb_penalty + rlc_penalty_ms + jitter_ms
            return max(1.0, latency_ms * 1000.0), 'app2_sensor_proxy'

        if device_type == 'camera':
            mcs_penalty = max(0.0, 20.0 - avg_mcs) * 2.5
            throughput_penalty = max(0.0, 25000.0 - float(throughput_kbps or 0.0)) / 650.0
            latency_ms = 28.0 + mcs_penalty + throughput_penalty + rlc_penalty_ms + (jitter_ms / 2.0)
            return max(1.0, latency_ms * 1000.0), 'embb_mac_proxy'

        mcs_penalty = max(0.0, 16.0 - avg_mcs) * 3.0
        throughput_penalty = max(0.0, 1000.0 - float(throughput_kbps or 0.0)) / 80.0
        latency_ms = 55.0 + mcs_penalty + throughput_penalty + rlc_penalty_ms + jitter_ms
        return max(1.0, latency_ms * 1000.0), 'background_mac_proxy'

    def _build_virtual_vehicle_state(self, imsi, index, fallback_end):
        phase = int(float(fallback_end or 0.0) // 30.0) % 4
        risk_state = 'high' if (index + phase) % 5 == 0 else 'medium' if (index + phase) % 3 == 0 else 'low'
        autonomy_state = 'degraded' if risk_state == 'high' else 'assisted' if risk_state == 'medium' else 'normal'
        base_latency_ms = 42.0 + (index * 9.0) + (phase * 6.0)
        if risk_state == 'high':
            base_latency_ms += 42.0
        elif risk_state == 'medium':
            base_latency_ms += 20.0
        packet_loss = 1.0 + (index * 0.7) + (phase * 0.8)
        if risk_state == 'high':
            packet_loss += 5.0
        elif risk_state == 'medium':
            packet_loss += 2.0
        return {
            'vehicle_id': f'proxy-veh-{index + 1:02d}',
            'vehicle_role': 'ego' if index == 0 else 'traffic',
            'connectivity': '5g_native',
            'gateway_id': 'GW-VEH-PROXY',
            'domain': 'vehicular',
            'mobility_profile': 'vehicle',
            'autonomy_state': autonomy_state,
            'risk_state': risk_state,
            'packet_loss_percent': round(packet_loss, 3),
            'speed_mps': round(max(0.5, 14.0 - (index * 0.8) - (phase * 0.4)), 3),
            'heading_deg': float((index * 32 + phase * 17) % 360),
            'lane_id': f'L{(index % 3) + 1}',
            'waypoint_id': f'proxy-wp-{phase}-{index}',
            'position': {'x': float(index * 14), 'y': float(phase * 3), 'z': 0.0},
            'latency_ms': round(base_latency_ms, 3),
            'source': 'collector_virtual_vehicle_proxy',
        }

    def _load_app2_sensor_override(self):
        if self.require_real_pdcp:
            return {}
        try:
            if not self.scenario_control_path.exists():
                return {}
            with open(self.scenario_control_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            override = payload.get("app2_sensor_override", {}) if isinstance(payload, dict) else {}
            if not isinstance(override, dict) or not override.get("enabled", False):
                return {}
            return override
        except Exception as e:
            print(f"[CSV_METRICS] Error loading App2 scenario control override: {e}")
            return {}

    def _load_app1_camera_override(self):
        if self.require_real_pdcp:
            return {}
        try:
            if not self.scenario_control_path.exists():
                return {}
            with open(self.scenario_control_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            override = payload.get("app1_camera_override", {}) if isinstance(payload, dict) else {}
            if not isinstance(override, dict) or not override.get("enabled", False):
                return {}
            return override
        except Exception as e:
            print(f"[CSV_METRICS] Error loading App1 scenario control override: {e}")
            return {}

    def _apply_app2_sensor_override(self, sensor_entries, snapshot, override):
        if not override or not sensor_entries or not snapshot:
            return sensor_entries, snapshot

        total = len(sensor_entries)
        connected_target = int(override.get("connected_sensors", total) or total)
        connected_target = max(0, min(total, connected_target))
        error_target = total - connected_target
        low_battery_target = int(override.get("low_battery_sensors", 0) or 0)
        low_battery_target = max(0, min(total, low_battery_target))

        packet_loss_target = max(0.0, min(100.0, float(override.get("packet_loss_percent", 0.0) or 0.0)))
        avg_latency_target = max(0.0, float(override.get("avg_latency_ms", 120.0) or 120.0))
        avg_rssi_target = float(override.get("avg_rssi_dbm", -92.0) or -92.0)
        avg_battery_target = max(0.0, min(100.0, float(override.get("avg_battery_percent", 75.0) or 75.0)))
        avg_power_target = max(0.0, float(override.get("avg_power_mw", 180.0) or 180.0))
        utilization_target = max(0.0, min(100.0, float(override.get("network_utilization_percent", 60.0) or 60.0)))
        delivery_target = max(0.0, min(100.0, float(override.get("delivery_success_percent", 100.0) or 100.0)))

        total_packets_tx = 0
        for idx, sensor in enumerate(sensor_entries):
            connected = idx < connected_target
            packets_tx = max(2, int(utilization_target / 5.0))
            if not connected:
                packets_tx = max(2, packets_tx - max(1, error_target))

            sensor["status"] = "ok" if connected else "error"
            sensor["packet_loss_percent"] = round(packet_loss_target, 2)
            sensor["latency_ms"] = round(avg_latency_target, 2)
            sensor["rssi_dbm"] = round(avg_rssi_target, 1)
            sensor["battery_percent"] = round(12.0 if idx < low_battery_target else avg_battery_target, 1)
            sensor["power_mw"] = round(avg_power_target, 1)
            sensor["packets_tx"] = packets_tx
            sensor["source"] = "ns3_real_sensor_ue_override"
            total_packets_tx += packets_tx

        packets_rx = int(round(total_packets_tx * (delivery_target / 100.0)))
        packets_lost = max(total_packets_tx - packets_rx, 0)

        snapshot["sensors"]["active"] = connected_target
        snapshot["sensors"]["connected"] = connected_target
        snapshot["sensors"]["error"] = error_target
        snapshot["sensors"]["low_battery"] = low_battery_target
        snapshot["readings"]["avg_battery_percent"] = round(avg_battery_target, 2)
        snapshot["readings"]["avg_power_mw"] = round(avg_power_target, 2)
        snapshot["network"]["packet_loss_percent"] = round(packet_loss_target, 2)
        snapshot["network"]["tx_packets"] = total_packets_tx
        snapshot["network"]["rx_packets"] = packets_rx
        snapshot["network"]["lost_packets"] = packets_lost
        snapshot["network"]["avg_latency_ms"] = round(avg_latency_target, 2)
        snapshot["network"]["avg_rssi_dbm"] = round(avg_rssi_target, 2)
        snapshot["network"]["network_utilization_percent"] = round(utilization_target, 2)
        snapshot["network"]["delivery_success_percent"] = round(delivery_target, 2)
        snapshot["simulation"] = {
            "mode": str(override.get("mode", "scenario_control_override") or "scenario_control_override"),
            "simulated_hour": None,
        }
        return sensor_entries, snapshot

    def _load_vehicle_override(self):
        if self.require_real_pdcp:
            return {}
        try:
            if not self.scenario_control_path.exists():
                return {}
            with open(self.scenario_control_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            override = payload.get("vehicle_override", {}) if isinstance(payload, dict) else {}
            if not isinstance(override, dict) or not override.get("enabled", False):
                return {}
            return override
        except Exception as e:
            print(f"[CSV_METRICS] Error loading vehicle scenario control override: {e}")
            return {}

    def _apply_vehicle_override(self, vehicle_payload, override, vehicle_index):
        if not override:
            return dict(vehicle_payload or {})

        payload = dict(vehicle_payload or {})
        role = str(payload.get("vehicle_role", payload.get("role", "ego" if vehicle_index == 0 else "traffic")) or "traffic")
        is_ego = role == "ego"
        high_target = max(0, int(override.get("high_risk_vehicles", 0) or 0))
        medium_target = max(0, int(override.get("medium_risk_vehicles", 0) or 0))
        degraded_target = max(0, int(override.get("degraded_autonomy_vehicles", 0) or 0))
        ego_latency_ms = float(override.get("ego_latency_ms", 18.0) or 18.0)
        traffic_latency_ms = float(override.get("traffic_latency_ms", 14.0) or 14.0)
        ego_packet_loss = float(override.get("ego_packet_loss_percent", 0.2) or 0.2)
        traffic_packet_loss = float(override.get("traffic_packet_loss_percent", 0.1) or 0.1)
        max_speed_mps = float(override.get("max_speed_mps", 7.0) or 7.0)

        if vehicle_index < high_target:
            risk_state = "high"
        elif vehicle_index < (high_target + medium_target):
            risk_state = "medium"
        else:
            risk_state = "low"

        payload.update({
            "vehicle_role": role,
            "autonomy_state": "degraded" if vehicle_index < degraded_target else "normal",
            "risk_state": risk_state,
            "latency_ms": round(ego_latency_ms if is_ego else traffic_latency_ms, 3),
            "packet_loss_percent": round(ego_packet_loss if is_ego else traffic_packet_loss, 3),
            "speed_mps": round(max(0.5, max_speed_mps - (vehicle_index * 0.35)), 3),
            "scenario_mode": str(override.get("mode", "vehicle_controlled") or "vehicle_controlled"),
        })
        payload.setdefault("connectivity", "5g_native")
        payload.setdefault("gateway_id", "GW-VEH-PROXY")
        payload.setdefault("domain", "vehicular")
        payload.setdefault("mobility_profile", "vehicle")
        return payload

    def get_device_meta(self, imsi):
        """Return explicit metadata for an IMSI when available."""
        carla_meta = self.get_carla_vehicle_binding(imsi)
        if carla_meta.get("device_type") == "vehicle":
            return carla_meta

        self._refresh_device_role_map()
        meta = self.device_role_map.get(str(imsi), {})
        if isinstance(meta, dict) and meta:
            return meta

        try:
            imsi_num = int(imsi)
        except (TypeError, ValueError):
            return {}

        if CAMERA_IMSI_RANGE[0] <= imsi_num <= CAMERA_IMSI_RANGE[1]:
            return {
                "device_type": "camera",
                "label": f"camera_{imsi_num}",
            }

        if VEHICLE_IMSI_RANGE[0] <= imsi_num <= VEHICLE_IMSI_RANGE[1]:
            return {
                "device_type": "vehicle",
                "label": f"vehicle_{imsi_num - VEHICLE_IMSI_RANGE[0] + 1}",
                "vehicle_id": f"veh-imsi-{imsi_num}",
                "vehicle_role": "ego" if imsi_num == VEHICLE_IMSI_RANGE[0] else "traffic",
                "connectivity": "5g_native",
                "gateway_id": "GW-VEH-01",
                "domain": "vehicular",
                "mobility_profile": "vehicle",
            }

        if SENSOR_IMSI_RANGE[0] <= imsi_num <= SENSOR_IMSI_RANGE[1]:
            sensor_index = imsi_num - SENSOR_IMSI_RANGE[0]
            profile = DEFAULT_SENSOR_PROFILES[sensor_index % len(DEFAULT_SENSOR_PROFILES)]
            mobility_profile = "stationary"
            if 4 <= imsi_num <= 10:
                mobility_profile = "pedestrian"
            elif 16 <= imsi_num <= 20:
                mobility_profile = "vehicle"
            return {
                "device_type": "sensor",
                "label": f"sensor_{sensor_index + 1}",
                "sensor_type": profile["sensor_type"],
                "unit": profile["unit"],
                "connectivity": profile["connectivity"],
                "gateway_id": profile["gateway_id"],
                "domain": profile["domain"],
                "nominal_value": profile["nominal_value"],
                "nominal_power_mw": profile["nominal_power_mw"],
                "nominal_battery_percent": 100.0,
                "nominal_rssi_dbm": -88.0,
                "tx_interval_s": 5 if profile["connectivity"] == "5g_native" else 10,
                "mobility_profile": mobility_profile,
            }

        return {}
    
    def get_device_type(self, imsi):
        """Determine device type from IMSI"""
        explicit_meta = self.get_device_meta(imsi)
        explicit_type = explicit_meta.get("device_type")
        if explicit_type:
            return str(explicit_type)

        try:
            imsi_num = int(imsi)
            if CAMERA_IMSI_RANGE[0] <= imsi_num <= CAMERA_IMSI_RANGE[1]:
                return "camera"
            elif VEHICLE_IMSI_RANGE[0] <= imsi_num <= VEHICLE_IMSI_RANGE[1]:
                return "vehicle"
            elif SENSOR_IMSI_RANGE[0] <= imsi_num <= SENSOR_IMSI_RANGE[1]:
                return "sensor"
            elif UE_IMSI_RANGE[0] <= imsi_num <= UE_IMSI_RANGE[1]:
                return "background"
        except ValueError:
            pass
        return "background"

    def export_app2_metrics(self, extended_metrics):
        """Export App2-compatible sensor views from real ns-3 sensor UEs."""
        sensor_entries = []
        ue_metrics = extended_metrics.get("ue_metrics", {}) or {}
        global_metrics = extended_metrics.get("global_metrics", {}) or {}
        sensor_values = []
        sensor_latencies = []
        sensor_rssi = []
        observed_rx_packets = 0
        global_loss_percent = float(global_metrics.get("global_packet_loss_rate", 0.0) or 0.0) * 100.0
        global_loss_percent = max(0.0, min(global_loss_percent, 100.0))

        for imsi, ue_data in ue_metrics.items():
            if ue_data.get("device_type") != "sensor":
                continue

            meta = self.get_device_meta(imsi)
            tx_pdus = int(ue_data.get("tx_pdus", 0) or 0)
            rx_pdus = int(ue_data.get("rx_pdus", 0) or 0)
            observed_rx_packets += rx_pdus
            real_throughput_kbps = max(
                float(ue_data.get("throughput_kbps", 0) or 0),
                float(ue_data.get("tx_throughput_kbps", 0) or 0),
                float(ue_data.get("rx_throughput_kbps", 0) or 0),
                float(ue_data.get("total_pdcp_throughput_kbps", 0) or 0),
                float(ue_data.get("cu_up_throughput_kbps", 0) or 0),
            )
            connected = bool(
                ue_data.get("has_latency_samples")
                or rx_pdus > 0
                or real_throughput_kbps > 0
            )
            packet_loss_percent = global_loss_percent
            latency_ms = float(ue_data.get("latency_avg_us", ue_data.get("latency_us", 0)) or 0) / 1000.0
            nominal_value = float(meta.get("nominal_value", 0.0) or 0.0)
            value = nominal_value if nominal_value else round(real_throughput_kbps, 2)
            unit = str(meta.get("unit", "kbps") or "kbps")
            rssi_dbm = float(meta.get("nominal_rssi_dbm", -88.0) or -88.0)
            battery_percent = float(meta.get("nominal_battery_percent", 100.0) or 100.0)
            power_mw = float(meta.get("nominal_power_mw", 220.0) or 220.0)

            sensor_entries.append({
                "sensor_id": int(imsi),
                "type": str(meta.get("sensor_type", "telemetry") or "telemetry"),
                "value": round(value, 2),
                "unit": unit,
                "timestamp": extended_metrics.get("timestamp_iso"),
                "status": "ok" if connected else "error",
                "domain": str(meta.get("domain", "network") or "network"),
                "connectivity": str(meta.get("connectivity", "5g_native") or "5g_native"),
                "gateway_id": str(meta.get("gateway_id", "GW-5G-NS3") or "GW-5G-NS3"),
                "battery_percent": round(battery_percent, 1),
                "latency_ms": round(latency_ms, 2),
                "packet_loss_percent": round(packet_loss_percent, 2),
                "rssi_dbm": round(rssi_dbm, 1),
                "power_mw": round(power_mw, 1),
                "tx_interval_s": int(meta.get("tx_interval_s", 5) or 5),
                "packets_tx": rx_pdus,
                "mobility_profile": str(meta.get("mobility_profile", "stationary") or "stationary"),
                "source": "ns3_real_sensor_ue",
            })

            sensor_values.append(value)
            sensor_latencies.append(latency_ms)
            sensor_rssi.append(rssi_dbm)

        if not sensor_entries:
            return

        connected_count = sum(1 for s in sensor_entries if s["status"] == "ok")
        error_count = len(sensor_entries) - connected_count
        total_throughput_kbps = sum(
            max(
                float(ue_data.get("throughput_kbps", 0) or 0),
                float(ue_data.get("tx_throughput_kbps", 0) or 0),
                float(ue_data.get("rx_throughput_kbps", 0) or 0),
                float(ue_data.get("total_pdcp_throughput_kbps", 0) or 0),
                float(ue_data.get("cu_up_throughput_kbps", 0) or 0),
            )
            for ue_data in ue_metrics.values()
            if ue_data.get("device_type") == "sensor"
        )
        network_utilization = min(100.0, total_throughput_kbps / max(len(sensor_entries) * 64.0, 1.0))
        estimated_tx_packets = int(round(observed_rx_packets / max(1e-9, (1.0 - global_loss_percent / 100.0)))) if observed_rx_packets > 0 and global_loss_percent < 100.0 else observed_rx_packets
        packets_lost = max(estimated_tx_packets - observed_rx_packets, 0)
        delivery_success = 100.0 - global_loss_percent

        snapshot = {
            "timestamp": extended_metrics.get("timestamp_iso"),
            "sensors": {
                "total": len(sensor_entries),
                "active": connected_count,
                "connected": connected_count,
                "error": error_count,
                "low_battery": 0,
                "connectivity_modes": sorted({s["connectivity"] for s in sensor_entries}),
                "gateways": sorted({s["gateway_id"] for s in sensor_entries}),
            },
            "readings": {
                "avg_temperature_c": round(
                    sum(s["value"] for s in sensor_entries if s["type"] == "temperature")
                    / max(1, sum(1 for s in sensor_entries if s["type"] == "temperature")),
                    2,
                ) if any(s["type"] == "temperature" for s in sensor_entries) else 0,
                "avg_humidity_percent": round(
                    sum(s["value"] for s in sensor_entries if s["type"] in {"humidity", "soil_moisture"})
                    / max(1, sum(1 for s in sensor_entries if s["type"] in {"humidity", "soil_moisture"})),
                    2,
                ) if any(s["type"] in {"humidity", "soil_moisture"} for s in sensor_entries) else 0,
                "avg_soil_conductivity": round(
                    sum(s["value"] for s in sensor_entries if s["type"] == "soil_conductivity")
                    / max(1, sum(1 for s in sensor_entries if s["type"] == "soil_conductivity")),
                    2,
                ) if any(s["type"] == "soil_conductivity" for s in sensor_entries) else 0,
                "avg_battery_percent": round(sum(s["battery_percent"] for s in sensor_entries) / len(sensor_entries), 2),
                "avg_power_mw": round(sum(s["power_mw"] for s in sensor_entries) / len(sensor_entries), 2),
            },
            "network": {
                "packet_loss_percent": round(global_loss_percent, 2),
                "tx_packets": estimated_tx_packets,
                "rx_packets": observed_rx_packets,
                "lost_packets": packets_lost,
                "avg_latency_ms": round(sum(sensor_latencies) / len(sensor_latencies), 2) if sensor_latencies else 0,
                "avg_rssi_dbm": round(sum(sensor_rssi) / len(sensor_rssi), 2) if sensor_rssi else -88.0,
                "network_utilization_percent": round(network_utilization, 2),
                "delivery_success_percent": round(delivery_success, 2),
            },
            "alerts": [],
            "simulation": {
                "mode": "ns3_real_sensor_ues",
                "simulated_hour": None,
            },
        }

        app2_override = self._load_app2_sensor_override()
        if app2_override:
            sensor_entries, snapshot = self._apply_app2_sensor_override(
                sensor_entries,
                snapshot,
                app2_override,
            )

        self.write_metrics(sensor_entries, self.app2_sensors_file)
        self.write_metrics(snapshot, self.app2_snapshot_file)

    def export_device_roles_snapshot(self, extended_metrics):
        """Persist observed roles without dropping configured, quiet UEs."""
        self._refresh_device_role_map()
        roles = {
            str(imsi): dict(meta)
            for imsi, meta in (self.device_role_map or {}).items()
            if isinstance(meta, dict)
        }
        for imsi, ue_data in (extended_metrics.get("ue_metrics", {}) or {}).items():
            meta = dict(roles.get(str(imsi)) or self.get_device_meta(imsi) or {})
            meta.setdefault("device_type", ue_data.get("device_type", "background"))
            roles[str(imsi)] = meta

        if not roles:
            return

        payload = {
            "generated_by": "csv_to_metrics_fallback",
            "scenario": "runtime_fallback",
            "roles": roles,
        }
        self.write_metrics(payload, str(self.device_role_map_path))

    def export_app3_metrics(self, extended_metrics):
        """Publish an App3 view derived only from real ns-3 PDCP rows."""
        expected = {str(imsi) for imsi in _FIXED_SERVICE_IMSIS["vehicle"]}
        rows = []
        for imsi, data in (extended_metrics.get("ue_metrics", {}) or {}).items():
            if not isinstance(data, dict) or data.get("device_type") != "vehicle":
                continue
            rows.append((str(imsi), data))

        observed = {imsi for imsi, _ in rows}
        real_rows = [
            data for _, data in rows
            if data.get("pdcp_provenance") == "pdcp_real"
            and data.get("packet_loss_percent") is not None
            and not bool(data.get("latency_is_proxy"))
        ]
        valid = observed == expected and len(real_rows) == len(expected)
        reason = "ok" if valid else "real_pdcp_vehicle_coverage_incomplete"
        snapshot = {
            "schema": "greenran.app3.pdcp_real_snapshot.v1",
            "source": "ns3_pdcp_real",
            "pdcp_provenance": "pdcp_real" if valid else "invalid",
            "valid": valid,
            "validation_reason": reason,
            "vehicle_imsis": sorted(observed, key=lambda value: int(value) if value.isdigit() else value),
            "simulation": {
                "mode": "ns3_real_vehicle_ues",
                "total_ues": int((extended_metrics.get("global_metrics") or {}).get("total_active_ues", 0) or 0),
            },
            "vehicles": {
                "total_vehicles": len(real_rows) if valid else 0,
                "min_tx_pdus": min((int(data.get("tx_pdus", 0) or 0) for data in real_rows), default=0),
                "min_rx_pdus": min((int(data.get("rx_pdus", 0) or 0) for data in real_rows), default=0),
                "min_sample_window_s": min((float(data.get("sample_window_s", 0.0) or 0.0) for data in real_rows), default=0.0),
                "sample_window_s": max((float(data.get("sample_window_s", 0.0) or 0.0) for data in real_rows), default=0.0),
                "ego_present": any(
                    str(data.get("vehicle_role", "")).lower() == "ego" for data in real_rows
                ),
                "high_risk_vehicles": sum(
                    1 for data in real_rows
                    if str(data.get("risk_state", "")).lower() in {"high", "critical"}
                ),
                "medium_risk_vehicles": sum(
                    1 for data in real_rows
                    if str(data.get("risk_state", "")).lower() in {"medium", "warning"}
                ),
                "degraded_autonomy_vehicles": sum(
                    1 for data in real_rows
                    if str(data.get("autonomy_state", "")).lower() not in {"", "normal", "unknown"}
                ),
                "max_latency_ms": max(
                    (float(data.get("latency_avg_us", data.get("latency_us", 0.0)) or 0.0) / 1000.0
                     for data in real_rows),
                    default=0.0,
                ),
                "max_packet_loss_percent": max(
                    (float(data.get("packet_loss_percent", 0.0) or 0.0) for data in real_rows),
                    default=0.0,
                ),
            },
            "network": {
                "max_latency_ms": max(
                    (float(data.get("latency_avg_us", data.get("latency_us", 0.0)) or 0.0) / 1000.0
                     for data in real_rows),
                    default=0.0,
                ),
                "max_packet_loss_percent": max(
                    (float(data.get("packet_loss_percent", 0.0) or 0.0) for data in real_rows),
                    default=0.0,
                ),
            },
        }
        self.app3_monitoring_file.parent.mkdir(parents=True, exist_ok=True)
        self.write_metrics(snapshot, str(self.app3_monitoring_file))
        return valid
    
    def process_pdcp_stats(self, filepath):
        """Process DlPdcpStats.txt - Ground truth for latency
        
        Format (tab-separated):
        start  end  CellId  IMSI  RNTI  LCID  nTxPDUs  TxBytes  nRxPDUs  RxBytes  delay  stdDev  min  max  PduSize  stdDev  min  max
        """
        if not filepath.exists():
            return []
        
        fidelity = os.environ.get("GREENRAN_NATIVE_TRACE_PROFILE") == "v9_fidelity"
        if not fidelity:
            metrics = []
            try:
                with open(filepath, 'r') as f:
                    lines = f
                    for line in lines:
                        if line.startswith('%') or line.startswith('start') or not line.strip():
                            continue
                        parts = line.strip().split('\t')
                        if len(parts) < 15:
                            continue
                        try:
                            time_start = float(parts[0]); time_end = float(parts[1])
                            cell_id = int(parts[2]); imsi = parts[3]; rnti = int(parts[4])
                            lcid = int(parts[5]); n_tx_pdus = int(parts[6]); tx_bytes = int(parts[7])
                            n_rx_pdus = int(parts[8]); rx_bytes = int(parts[9])
                            delay_s = float(parts[10]); delay_stddev = float(parts[11])
                            delay_min = float(parts[12]); delay_max = float(parts[13]); pdu_size = int(parts[14])
                            metrics.append({
                                'time_start': time_start, 'time_end': time_end,
                                'cell_id': cell_id, 'imsi': imsi, 'rnti': rnti, 'lcid': lcid,
                                'n_tx_pdus': n_tx_pdus, 'tx_bytes': tx_bytes,
                                'n_rx_pdus': n_rx_pdus, 'rx_bytes': rx_bytes,
                                'delay_s': delay_s, 'delay_us': delay_s * 1_000_000,
                                'delay_stddev_s': delay_stddev, 'delay_stddev_us': delay_stddev * 1_000_000,
                                'delay_min_s': delay_min, 'delay_min_us': delay_min * 1_000_000,
                                'delay_max_s': delay_max, 'delay_max_us': delay_max * 1_000_000,
                                'pdu_size': pdu_size, 'device_type': self.get_device_type(imsi),
                            })
                        except (ValueError, IndexError):
                            continue
            except Exception as e:
                print(f"[CSV_METRICS] Error reading PDCP stats: {e}")
            return metrics

        metrics = self._pdcp_cache
        try:
            stat = filepath.stat()
            inode = int(getattr(stat, "st_ino", 0) or 0)
            if inode != self._pdcp_inode or stat.st_size < self._pdcp_offset:
                metrics = []
                self._pdcp_cache = metrics
                self._pdcp_offset = 0
                self._pdcp_inode = inode
            with filepath.open("rb") as handle:
                if self._pdcp_offset == 0:
                    header = handle.readline()
                    self._pdcp_offset = handle.tell()
                handle.seek(self._pdcp_offset)
                body = handle.read()
            last_newline = body.rfind(b"\n")
            if last_newline < 0:
                return list(metrics)
            complete = body[:last_newline + 1]
            self._pdcp_offset += len(complete)
            for raw in complete.decode("utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith('%') or line.startswith('start'):
                    continue
                parts = line.split('\t')
                if len(parts) < 15:
                    continue
                try:
                    time_start = float(parts[0]); time_end = float(parts[1])
                    cell_id = int(parts[2]); imsi = parts[3]; rnti = int(parts[4])
                    lcid = int(parts[5]); n_tx_pdus = int(parts[6]); tx_bytes = int(parts[7])
                    n_rx_pdus = int(parts[8]); rx_bytes = int(parts[9])
                    delay_s = float(parts[10]); delay_stddev = float(parts[11])
                    delay_min = float(parts[12]); delay_max = float(parts[13]); pdu_size = int(parts[14])
                    metrics.append({
                        'time_start': time_start, 'time_end': time_end,
                        'cell_id': cell_id, 'imsi': imsi, 'rnti': rnti, 'lcid': lcid,
                        'n_tx_pdus': n_tx_pdus, 'tx_bytes': tx_bytes,
                        'n_rx_pdus': n_rx_pdus, 'rx_bytes': rx_bytes,
                        'delay_s': delay_s, 'delay_us': delay_s * 1_000_000,
                        'delay_stddev_s': delay_stddev, 'delay_stddev_us': delay_stddev * 1_000_000,
                        'delay_min_s': delay_min, 'delay_min_us': delay_min * 1_000_000,
                        'delay_max_s': delay_max, 'delay_max_us': delay_max * 1_000_000,
                        'pdu_size': pdu_size, 'device_type': self.get_device_type(imsi),
                    })
                except (ValueError, IndexError):
                    continue
            del metrics[:-20000]
            self._pdcp_cache = metrics
            return list(metrics)
        except Exception as e:
            print(f"[CSV_METRICS] Error reading PDCP stats: {e}")
            return list(metrics)

    def process_mac_stats(self, filepath):
        """Process DlMacStats.txt - MAC layer metrics
        
        Format (tab-separated):
        time  cellId  IMSI  frame  sframe  RNTI  mcsTb1  sizeTb1  mcsTb2  sizeTb2  ccId
        """
        if not filepath.exists():
            return []
        
        metrics = []
        
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    if line.startswith('%') or line.startswith('time') or not line.strip():
                        continue
                    
                    parts = line.strip().split('\t')
                    if len(parts) < 11:
                        continue
                    
                    try:
                        time_val = float(parts[0])
                        cell_id = int(parts[1])
                        imsi = parts[2]
                        frame = int(parts[3])
                        sframe = int(parts[4])
                        rnti = int(parts[5])
                        mcs_tb1 = int(parts[6]) if parts[6] else 0
                        size_tb1 = int(parts[7]) if parts[7] else 0
                        mcs_tb2 = int(parts[8]) if parts[8] else 0
                        size_tb2 = int(parts[9]) if parts[9] else 0
                        cc_id = int(parts[10]) if len(parts) > 10 and parts[10] else 0
                        
                        metrics.append({
                            'time': time_val,
                            'cell_id': cell_id,
                            'imsi': imsi,
                            'frame': frame,
                            'sframe': sframe,
                            'rnti': rnti,
                            'mcs_tb1': mcs_tb1,
                            'size_tb1': size_tb1,
                            'mcs_tb2': mcs_tb2,
                            'size_tb2': size_tb2,
                            'cc_id': cc_id,
                            'device_type': self.get_device_type(imsi)
                        })
                    except (ValueError, IndexError):
                        continue
                        
        except Exception as e:
            print(f"[CSV_METRICS] Error reading MAC stats: {e}")
        
        return metrics

    def process_native_aggregate(self):
        """Read the compact v9 native control trace, not raw scheduler dumps."""
        path = self.input_dir.parent / "ns3_energy" / "TasamControlObservations.csv"
        if not path.is_file():
            return {}
        latest = {}
        try:
            import csv
            with path.open(newline="", encoding="utf-8", errors="replace") as handle:
                for row in csv.DictReader(handle):
                    try:
                        cell = int(row.get("CellId", 0) or 0)
                        sim_time = float(row.get("Time", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if cell <= 0:
                        continue
                    previous = latest.get(cell)
                    if previous is None or sim_time >= previous["sim_time_s"]:
                        latest[cell] = {
                            "sim_time_s": sim_time,
                            "cell_id": cell,
                            "transaction_id": int(row.get("SchedulerTransactionId", 0) or 0),
                            "power_transaction_id": int(row.get("PowerTransactionId", 0) or 0),
                            "tx_power_percent": float(row.get("TxPowerPercent", 0.0) or 0.0),
                            "policy_active": str(row.get("PolicyActive", "0")).lower() in {"1", "true", "yes"},
                            "active_dl_symbols": int(row.get("ActiveDlSymbols", 0) or 0),
                            "active_dl_symbol_capacity": int(row.get("ActiveDlSymbolCapacity", 0) or 0),
                            "evidence_version": str(row.get("EvidenceVersion") or ""),
                        }
        except (OSError, ValueError, csv.Error):
            return {}
        return latest

    def process_mmwave_sched_stats(self, filepath):
        """Process EnbSchedAllocTraces.txt - mmWave scheduler allocations."""
        if not filepath.exists():
            return {}

        recent_rows = []

        try:
            with open(filepath, 'r') as f:
                for line in f:
                    if line.startswith('frame') or not line.strip():
                        continue

                    parts = line.strip().split('\t')
                    if len(parts) < 10:
                        continue

                    try:
                        recent_rows.append({
                            'frame': int(parts[0]),
                            'subframe': int(parts[1]),
                            'slot': int(parts[2]),
                            'rnti': int(parts[3]),
                            'first_sym': int(parts[4]),
                            'num_sym': int(parts[5]),
                            'type': int(parts[6]),
                            'tdd_mode': int(parts[7]),
                            'retx_num': int(parts[8]),
                            'cc_id': int(parts[9]),
                        })
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            print(f"[CSV_METRICS] Error reading mmWave scheduler stats: {e}")
            return {}

        if not recent_rows:
            return {}

        window_rows = recent_rows[-5000:]
        rnti_stats = defaultdict(lambda: {
            'allocations': 0,
            'symbols': 0,
            'dl_allocations': 0,
            'ul_allocations': 0,
            'control_allocations': 0,
            'retx_allocations': 0,
            'cc_ids': set(),
        })
        for row in window_rows:
            stats = rnti_stats[row['rnti']]
            stats['allocations'] += 1
            stats['symbols'] += row['num_sym']
            stats['cc_ids'].add(row['cc_id'])
            if row['retx_num'] > 0:
                stats['retx_allocations'] += 1
            if row['rnti'] == 0 or row['type'] == 2:
                stats['control_allocations'] += 1
            elif row['tdd_mode'] == 1:
                stats['dl_allocations'] += 1
            elif row['tdd_mode'] == 2:
                stats['ul_allocations'] += 1

        per_rnti = {}
        for rnti, stats in rnti_stats.items():
            per_rnti[str(rnti)] = {
                'allocations': stats['allocations'],
                'symbols': stats['symbols'],
                'dl_allocations': stats['dl_allocations'],
                'ul_allocations': stats['ul_allocations'],
                'control_allocations': stats['control_allocations'],
                'retx_allocations': stats['retx_allocations'],
                'cc_ids': sorted(stats['cc_ids']),
            }

        data_rntis = [rnti for rnti in rnti_stats if rnti > 0]

        return {
            'row_count': len(recent_rows),
            'window_row_count': len(window_rows),
            'observed_rntis': len(rnti_stats),
            'observed_data_rntis': len(data_rntis),
            'observed_control_rntis': 1 if 0 in rnti_stats else 0,
            'per_rnti': per_rnti,
            'rnti_counts': {rnti: stats['allocations'] for rnti, stats in rnti_stats.items()},
            'source_file': str(filepath),
        }
    
    def process_rlc_stats(self, filepath):
        """Process DlRlcStats.txt - RLC layer metrics."""
        if not filepath.exists():
            return []

        metrics = []

        try:
            with open(filepath, 'r') as f:
                for line in f:
                    if line.startswith('%') or line.startswith('start') or not line.strip():
                        continue

                    parts = line.strip().split('	')
                    if len(parts) < 15:
                        continue

                    try:
                        time_start = float(parts[0])
                        time_end = float(parts[1])
                        cell_id = int(parts[2])
                        imsi = parts[3]
                        rnti = int(parts[4])
                        lcid = int(parts[5])
                        n_tx_pdus = int(parts[6])
                        tx_bytes = int(parts[7])
                        n_rx_pdus = int(parts[8])
                        rx_bytes = int(parts[9])
                        delay_s = float(parts[10])
                        delay_stddev = float(parts[11])
                        delay_min = float(parts[12])
                        delay_max = float(parts[13])
                        pdu_size = float(parts[14])

                        metrics.append({
                            'time_start': time_start,
                            'time_end': time_end,
                            'cell_id': cell_id,
                            'imsi': imsi,
                            'rnti': rnti,
                            'lcid': lcid,
                            'n_tx_pdus': n_tx_pdus,
                            'tx_bytes': tx_bytes,
                            'n_rx_pdus': n_rx_pdus,
                            'rx_bytes': rx_bytes,
                            'delay_s': delay_s,
                            'delay_us': delay_s * 1_000_000,
                            'delay_stddev_s': delay_stddev,
                            'delay_stddev_us': delay_stddev * 1_000_000,
                            'delay_min_s': delay_min,
                            'delay_min_us': delay_min * 1_000_000,
                            'delay_max_s': delay_max,
                            'delay_max_us': delay_max * 1_000_000,
                            'pdu_size': pdu_size,
                            'device_type': self.get_device_type(imsi)
                        })
                    except (ValueError, IndexError):
                        continue

        except Exception as e:
            print(f"[CSV_METRICS] Error reading RLC stats: {e}")

        return metrics

    def process_cu_up_stats(self, filepaths):
        """Process cu-up-cell-*.txt and extract the latest per-UE throughput snapshot.

        The CU-UP file is not perfectly aligned with its header in all scenarios, so this
        parser intentionally supports two observed layouts:
            - legacy layout with PDCP throughput/latency in columns 7/8
            - current qos-only layout where only columns 9/10 are populated

        In the qos-only layout we do not treat the CU-UP file as a reliable
        throughput source, because those columns no longer represent the same
        semantics expected by the old parser.
        """
        latest_rows = []
        source_files = []
        file_snapshots = []
        format_variants = set()
        has_throughput_signal = False

        for filepath in sorted(filepaths):
            if not filepath.exists():
                continue

            source_files.append(str(filepath))
            file_latest_ts = None
            file_latest_rows = []
            file_mtime = filepath.stat().st_mtime

            try:
                with open(filepath, "r") as f:
                    header = None
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line:
                            continue
                        if line.startswith("timestamp"):
                            header = [part.strip() for part in line.split(",")]
                            continue

                        parts = [part.strip() for part in line.split(",")]
                        if len(parts) < 11:
                            continue

                        try:
                            timestamp = int(parts[0])
                            imsi = str(int(parts[1]))
                        except ValueError:
                            continue

                        populated_slots = [idx for idx, value in enumerate(parts) if value]
                        legacy_throughput_present = len(parts) > 8 and bool(parts[7] or parts[8])
                        qos_only_variant = populated_slots == [0, 1, 9, 10]

                        if legacy_throughput_present:
                            format_variants.add("legacy_pdcp_throughput")
                        elif qos_only_variant:
                            format_variants.add("qos_only_no_throughput")
                        else:
                            format_variants.add("unknown_sparse_layout")

                        cell_average_latency_ms = float(parts[2] or 0.0) if len(parts) > 2 and parts[2] else 0.0
                        pdcp_throughput_mbps = 0.0
                        pdcp_latency_ms = 0.0

                        if legacy_throughput_present:
                            try:
                                pdcp_throughput_mbps = float(parts[7] or 0.0)
                                pdcp_latency_ms = float(parts[8] or 0.0)
                                has_throughput_signal = has_throughput_signal or pdcp_throughput_mbps > 0.0
                            except ValueError:
                                pdcp_throughput_mbps = 0.0
                                pdcp_latency_ms = 0.0

                        row = {
                            "timestamp": timestamp,
                            "imsi": imsi,
                            "cell_average_latency_ms": cell_average_latency_ms,
                            "pdcp_throughput_mbps": pdcp_throughput_mbps,
                            "pdcp_throughput_kbps": pdcp_throughput_mbps * 1000.0,
                            "pdcp_latency_ms": pdcp_latency_ms,
                            "source_file": str(filepath),
                        }

                        if file_latest_ts is None or timestamp > file_latest_ts:
                            file_latest_ts = timestamp
                            file_latest_rows = [row]
                        elif timestamp == file_latest_ts:
                            file_latest_rows.append(row)
            except Exception as e:
                print(f"[CSV_METRICS] Error reading CU-UP stats from {filepath}: {e}")

            if file_latest_rows:
                file_snapshots.append({
                    "path": str(filepath),
                    "mtime": file_mtime,
                    "latest_timestamp": file_latest_ts,
                    "rows": file_latest_rows,
                })

        if file_snapshots:
            freshest_mtime = max(snapshot["mtime"] for snapshot in file_snapshots)
            freshness_window_s = max(10.0, self.poll_interval * 10.0)
            active_snapshots = [
                snapshot for snapshot in file_snapshots
                if freshest_mtime - snapshot["mtime"] <= freshness_window_s
            ]
            for snapshot in active_snapshots:
                latest_rows.extend(snapshot["rows"])
            source_files = [snapshot["path"] for snapshot in active_snapshots]

        per_ue = {}
        total_throughput_kbps = 0.0
        latest_timestamp = 0

        for row in latest_rows:
            imsi = row["imsi"]
            latest_timestamp = max(latest_timestamp, row["timestamp"])
            per_ue[imsi] = {
                "throughput_kbps": row["pdcp_throughput_kbps"],
                "pdcp_latency_ms": row["pdcp_latency_ms"],
                "cell_average_latency_ms": row["cell_average_latency_ms"],
                "source_file": row["source_file"],
            }
            total_throughput_kbps += row["pdcp_throughput_kbps"]

        return {
            "source_files": source_files,
            "latest_timestamp": latest_timestamp,
            "total_throughput_kbps": total_throughput_kbps,
            "per_ue": per_ue,
            "row_count": len(latest_rows),
            "format_variants": sorted(format_variants),
            "has_throughput_signal": has_throughput_signal,
        }
    
    def percentile(self, data, p):
        """Retorna percentil p (0-100)"""
        if not data:
            return 0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        return sorted_data[min(idx, len(sorted_data)-1)]
    
    def percentile_5(self, data):
        return self.percentile(data, 5)
    
    def percentile_95(self, data):
        return self.percentile(data, 95)
    
    def min_nonzero(self, data):
        """Retorna menor valor > 0, ou 0 se todos forem zero"""
        nonzero = [x for x in data if x > 0]
        return min(nonzero) if nonzero else 0
    
    def median(self, data):
        """Retorna mediana dos dados"""
        if not data:
            return 0
        sorted_data = sorted(data)
        n = len(sorted_data)
        if n % 2 == 0:
            return (sorted_data[n//2 - 1] + sorted_data[n//2]) / 2
        else:
            return sorted_data[n//2]

    def calculate_cvar(self, data, tail_fraction=0.05):
        """Tail-mean of the worst tail_fraction values."""
        if not data:
            return 0

        sorted_desc = sorted(data, reverse=True)
        tail_count = max(1, math.ceil(len(sorted_desc) * tail_fraction))
        tail_values = sorted_desc[:tail_count]
        return sum(tail_values) / len(tail_values)

    def aggregate_metrics_without_pdcp(self, mac_metrics, rlc_metrics=None, cu_up_metrics=None, mmwave_sched_metrics=None):
        """Build a degraded-but-usable metric snapshot when PDCP stats are absent."""

        fallback_time_samples = []
        if mac_metrics:
            fallback_time_samples.extend(
                float(item.get('time', 0.0) or 0.0)
                for item in mac_metrics[-5000:]
                if float(item.get('time', 0.0) or 0.0) >= 0.0
            )
        if rlc_metrics:
            fallback_time_samples.extend(
                float(item.get('time_end', item.get('time_start', 0.0)) or 0.0)
                for item in rlc_metrics[-5000:]
                if float(item.get('time_end', item.get('time_start', 0.0)) or 0.0) >= 0.0
            )

        if fallback_time_samples:
            fallback_start = min(fallback_time_samples)
            fallback_end = max(fallback_time_samples)
            fallback_window_s = max(fallback_end - fallback_start, max(self.poll_interval, 1.0))
        else:
            fallback_end = max(float(self.last_sim_time or 0.0), 0.0)
            fallback_start = fallback_end
            fallback_window_s = max(self.poll_interval, 1.0)

        result = {
            'timestamp': int(time.time() * 1000),
            'timestamp_iso': time.strftime('%Y-%m-%d %H:%M:%S'),
            'sim_time_range': {
                'start': fallback_start,
                'end': fallback_end,
                'window_s': fallback_window_s,
            },
            'ue_metrics': {},
            'cell_metrics': {},
            'global_metrics': {
                'global_worst_latency_us': 0.0,
                'global_worst_camera_latency_us': 0.0,
                'global_avg_latency_us': 0.0,
                'global_min_latency_us': 0.0,
                'global_max_latency_us': 0.0,
                'global_jitter_us': 0.0,
                'global_packet_loss_rate': 0.0,
                'total_active_ues': 0,
                'total_active_cameras': 0,
                'total_active_sensors': 0,
                'total_active_vehicles': 0,
                'total_critical_ues': 0,
                'total_tx_bytes': 0,
                'total_rx_bytes': 0,
                'total_tx_pdus': 0,
                'total_rx_pdus': 0,
                'throughput_kbps': 0.0,
                'throughput_source': 'cu_up_fallback',
                'pdcp_delta_throughput_kbps': 0.0,
                'cu_up_total_throughput_kbps': float((cu_up_metrics or {}).get('total_throughput_kbps', 0) or 0),
                'latency_p5_us': 0.0,
                'latency_p95_us': 0.0,
                'latency_min_nonzero_us': 0.0,
                'latency_median_us': 0.0,
                'latency_p95_per_ue_us': 0.0,
                'variance_per_ue_us2': 0.0,
                'cvar_per_ue_us': 0.0,
                'cvar_tail_count': 0,
                'ue_count': 0,
                'ues_with_latency_samples': 0,
                'ues_without_latency_samples': 0,
                'lte_mac_observed_ues': 0,
                'mmwave_sched_observed_rntis': 0,
                'mmwave_sched_observed_data_rntis': 0,
                'mmwave_sched_observed_control_rntis': 0,
                'mmwave_sched_mapped_ues': 0,
                'mmwave_sched_window_allocs': 0,
                'mac_trace_mode': 'unknown',
                'collector_mode': 'no_pdcp_proxy_latency',
                'latency_sample_source_counts': {},
                'real_latency_sample_count': 0,
                'proxy_latency_sample_count': 0,
                'proxy_expanded_sensors': False,
                'proxy_virtual_vehicles': False,
            },
            'active_cameras': 0,
            'critical_cameras': 0,
            'critical_ues': 0,
            'source_files': {
                'pdcp': str(self.input_dir / "DlPdcpStats.txt"),
                'mac': str(self.input_dir / "DlMacStats.txt"),
                'rlc': str(self.input_dir / "DlRlcStats.txt"),
                'cu_up': (cu_up_metrics or {}).get('source_files', []),
            },
        }

        latencies = []
        rlc_latest_by_imsi = {}
        mac_by_imsi = defaultdict(lambda: {
            'mcs': [],
            'tb_sizes': [],
            'last_cell_id': 0,
            'time_min': float('inf'),
            'time_max': 0.0,
            'tb_bytes_total': 0,
        })
        mac_window_start = float('inf')
        mac_window_end = 0.0

        if rlc_metrics:
            result['global_metrics']['rlc_record_count'] = len(rlc_metrics)
            for item in rlc_metrics[-5000:]:
                rlc_latest_by_imsi[item['imsi']] = item

        if mac_metrics:
            all_mcs = []
            all_tb_sizes = []
            for item in mac_metrics[-5000:]:
                data = mac_by_imsi[item['imsi']]
                tb_total = max(0, int(item['size_tb1']) + int(item['size_tb2']))
                time_val = float(item.get('time', 0.0) or 0.0)
                data['mcs'].append(item['mcs_tb1'])
                data['tb_sizes'].append(tb_total)
                data['last_cell_id'] = item.get('cell_id', 0)
                data['tb_bytes_total'] += tb_total
                data['time_min'] = min(data['time_min'], time_val)
                data['time_max'] = max(data['time_max'], time_val)
                mac_window_start = min(mac_window_start, time_val)
                mac_window_end = max(mac_window_end, time_val)
                all_mcs.append(item['mcs_tb1'])
                all_tb_sizes.append(tb_total)

            result['global_metrics']['lte_mac_observed_ues'] = len(mac_by_imsi)
            if all_mcs:
                result['global_metrics']['global_mcs_avg'] = sum(all_mcs) / len(all_mcs)
                result['global_metrics']['global_mcs_min'] = min(all_mcs)
                result['global_metrics']['global_mcs_max'] = max(all_mcs)
                result['global_metrics']['global_tb_size_avg'] = sum(all_tb_sizes) / len(all_tb_sizes)

        if mmwave_sched_metrics:
            result['global_metrics']['mmwave_sched_observed_rntis'] = int(mmwave_sched_metrics.get('observed_rntis', 0) or 0)
            result['global_metrics']['mmwave_sched_observed_data_rntis'] = int(mmwave_sched_metrics.get('observed_data_rntis', 0) or 0)
            result['global_metrics']['mmwave_sched_observed_control_rntis'] = int(mmwave_sched_metrics.get('observed_control_rntis', 0) or 0)
            result['global_metrics']['mmwave_sched_window_allocs'] = int(mmwave_sched_metrics.get('window_row_count', 0) or 0)
            result['source_files']['mmwave_sched'] = mmwave_sched_metrics.get('source_file', '')

        imsis = set()
        imsis.update(mac_by_imsi.keys())
        imsis.update(rlc_latest_by_imsi.keys())
        imsis.update((cu_up_metrics or {}).get('per_ue', {}).keys())

        self._refresh_carla_vehicle_map()
        carla_vehicle_imsis = {
            str(imsi) for imsi, meta in self.carla_vehicle_map.items()
            if isinstance(meta, dict) and meta.get('device_type') == 'vehicle'
        }
        imsis.update(carla_vehicle_imsis)

        observed_sensor_imsis = {str(imsi) for imsi in imsis if self.get_device_type(imsi) == 'sensor'}
        expanded_sensor_imsis = set()
        target_sensor_count = len(DEFAULT_SENSOR_PROFILES)
        if not self.require_real_pdcp and len(observed_sensor_imsis) < target_sensor_count:
            for sensor_imsi in range(SENSOR_IMSI_RANGE[0], SENSOR_IMSI_RANGE[0] + target_sensor_count):
                imsi_text = str(sensor_imsi)
                if self.get_device_type(imsi_text) == 'sensor' and imsi_text not in imsis:
                    expanded_sensor_imsis.add(imsi_text)
                    imsis.add(imsi_text)
                if len(observed_sensor_imsis) + len(expanded_sensor_imsis) >= target_sensor_count:
                    break

        virtual_vehicle_imsis = set()
        if not self.require_real_pdcp and not carla_vehicle_imsis:
            vehicle_base = get_fixed_vehicle_base_imsi()
            vehicle_count_target = max(1, min(get_fixed_max_vehicles(), 5))
            for offset in range(vehicle_count_target):
                imsi_text = str(vehicle_base + offset)
                virtual_vehicle_imsis.add(imsi_text)
                imsis.add(imsi_text)

        result['global_metrics']['proxy_expanded_sensors'] = bool(expanded_sensor_imsis)
        result['global_metrics']['proxy_virtual_vehicles'] = bool(virtual_vehicle_imsis)

        SLA_THRESHOLD_US = 100000
        critical_count = 0
        camera_count = 0
        camera_critical_count = 0
        sensor_count = 0
        vehicle_count = 0
        app1_override = self._load_app1_camera_override()
        app2_override = self._load_app2_sensor_override()
        vehicle_override = self._load_vehicle_override()
        vehicle_override_index = 0
        latency_source_counts = defaultdict(int)
        real_latency_sample_count = 0
        proxy_latency_sample_count = 0

        for imsi in sorted(imsis, key=lambda value: int(value) if str(value).isdigit() else str(value)):
            cu_up_entry = (cu_up_metrics or {}).get('per_ue', {}).get(imsi, {})
            rlc_entry = rlc_latest_by_imsi.get(imsi, {})
            mac_entry = mac_by_imsi.get(imsi, {})
            device_type = 'vehicle' if imsi in virtual_vehicle_imsis else self.get_device_type(imsi)
            cell_id = (
                rlc_entry.get('cell_id')
                or mac_entry.get('last_cell_id')
                or 0
            )

            throughput_kbps = float(cu_up_entry.get('throughput_kbps', 0.0) or 0.0)
            throughput_source = 'cu_up'
            mac_throughput_kbps = 0.0
            if mac_entry and mac_entry.get('tb_bytes_total', 0) > 0:
                mac_window_s = max(
                    float(mac_entry.get('time_max', 0.0) or 0.0) - float(mac_entry.get('time_min', 0.0) or 0.0),
                    max(self.poll_interval, 1.0),
                )
                mac_throughput_kbps = (float(mac_entry.get('tb_bytes_total', 0) or 0) * 8.0) / (mac_window_s * 1000.0)
                if throughput_kbps <= 0.0:
                    throughput_kbps = mac_throughput_kbps
                    throughput_source = 'mac_tb_window'

            if throughput_kbps <= 0.0 and device_type == 'sensor':
                throughput_kbps = 12.0 + ((int(imsi) if str(imsi).isdigit() else 0) % 9) * 5.0
                throughput_source = 'app2_proxy_profile'
            elif throughput_kbps <= 0.0 and device_type == 'vehicle':
                throughput_kbps = 900.0 + ((int(imsi) if str(imsi).isdigit() else 0) % 5) * 180.0
                throughput_source = 'app3_proxy_profile'

            vehicle_state = {}
            vehicle_meta = self.get_carla_vehicle_binding(imsi)
            if device_type == 'vehicle':
                if vehicle_meta:
                    vehicle_state = self.get_carla_vehicle_state(vehicle_meta.get('vehicle_id'))
                elif imsi in virtual_vehicle_imsis:
                    vehicle_index = int(imsi) - get_fixed_vehicle_base_imsi()
                    vehicle_state = self._build_virtual_vehicle_state(imsi, max(0, vehicle_index), fallback_end)
                if vehicle_override:
                    base_vehicle_payload = dict(vehicle_state or {})
                    if vehicle_meta:
                        base_vehicle_payload.setdefault('vehicle_id', vehicle_meta.get('vehicle_id'))
                        base_vehicle_payload.setdefault('vehicle_role', vehicle_meta.get('vehicle_role', 'traffic'))
                        base_vehicle_payload.setdefault('connectivity', vehicle_meta.get('connectivity', '5g_native'))
                        base_vehicle_payload.setdefault('gateway_id', vehicle_meta.get('gateway_id', 'GW-VEH-01'))
                        base_vehicle_payload.setdefault('domain', vehicle_meta.get('domain', 'vehicular'))
                        base_vehicle_payload.setdefault('mobility_profile', vehicle_meta.get('mobility_profile', 'vehicle'))
                    vehicle_state = self._apply_vehicle_override(base_vehicle_payload, vehicle_override, vehicle_override_index)

            latency_us = max(0.0, float(cu_up_entry.get('pdcp_latency_ms', 0.0) or 0.0) * 1000.0)
            latency_source = 'cu_up_real' if latency_us > 0 else ''
            latency_is_proxy = False
            if latency_us <= 0.0:
                latency_us = max(0.0, float(rlc_entry.get('delay_us', 0.0) or 0.0))
                if latency_us > 0.0:
                    latency_source = 'rlc_real'
                else:
                    latency_us, latency_source = self._estimate_proxy_latency_us(
                        imsi, device_type, throughput_kbps, mac_entry, rlc_entry, vehicle_state=vehicle_state
                    )
                    latency_is_proxy = True
            if latency_is_proxy and device_type == 'camera' and app1_override and app1_override.get('latency_ms') is not None:
                latency_us = max(1.0, float(app1_override.get('latency_ms') or 0.0) * 1000.0)
                latency_source = 'app1_camera_override'
                latency_is_proxy = True
            elif latency_is_proxy and device_type == 'sensor' and app2_override and app2_override.get('avg_latency_ms') is not None:
                latency_us = max(1.0, float(app2_override.get('avg_latency_ms') or 0.0) * 1000.0)
                latency_source = 'app2_sensor_override'
                latency_is_proxy = True
            if latency_is_proxy and device_type == 'vehicle' and vehicle_override and vehicle_state.get('latency_ms') is not None:
                latency_us = max(1.0, float(vehicle_state.get('latency_ms') or 0.0) * 1000.0)
                latency_source = 'vehicle_state_proxy'
                latency_is_proxy = True
            has_latency_samples = latency_us > 0
            is_critical = has_latency_samples and latency_us >= SLA_THRESHOLD_US

            if has_latency_samples:
                latencies.append(latency_us)
                latency_source_counts[latency_source] += 1
                if latency_is_proxy:
                    proxy_latency_sample_count += 1
                else:
                    real_latency_sample_count += 1
            if is_critical:
                critical_count += 1

            if device_type == 'camera':
                camera_count += 1
                if is_critical:
                    camera_critical_count += 1
            elif device_type == 'sensor':
                sensor_count += 1
            elif device_type == 'vehicle':
                vehicle_count += 1
                vehicle_override_index += 1

            result['ue_metrics'][imsi] = {
                'device_type': device_type,
                'cell_id': cell_id,
                'latency_us': latency_us,
                'latency_avg_us': latency_us,
                'latency_min_us': latency_us if has_latency_samples else 0,
                'latency_max_us': latency_us if has_latency_samples else 0,
                'jitter_us': 0,
                'pdu_size_avg': 0,
                'tx_bytes': int(mac_entry.get('tb_bytes_total', 0) or 0),
                'rx_bytes': int(mac_entry.get('tb_bytes_total', 0) or 0),
                'tx_pdus': len(mac_entry.get('tb_sizes', [])),
                'rx_pdus': len(mac_entry.get('tb_sizes', [])),
                'throughput_kbps': throughput_kbps,
                'tx_throughput_kbps': mac_throughput_kbps,
                'rx_throughput_kbps': throughput_kbps,
                'total_pdcp_throughput_kbps': throughput_kbps,
                'packet_count': (1 if has_latency_samples else 0) + len(mac_entry.get('tb_sizes', [])),
                'has_latency_samples': has_latency_samples,
                'latency_source': latency_source,
                'latency_is_proxy': latency_is_proxy,
                'is_critical': is_critical,
                'throughput_source': throughput_source,
                'cu_up_throughput_kbps': float(cu_up_entry.get('throughput_kbps', 0.0) or 0.0),
                'cu_up_pdcp_latency_ms': float(cu_up_entry.get('pdcp_latency_ms', 0.0) or 0.0),
                'rlc_records': 1 if rlc_entry else 0,
                'proxy_expanded': imsi in expanded_sensor_imsis or imsi in virtual_vehicle_imsis,
            }

            if device_type == 'vehicle':
                vehicle_payload = dict(vehicle_state or {})
                if vehicle_meta:
                    vehicle_payload.setdefault('vehicle_id', vehicle_meta.get('vehicle_id'))
                    vehicle_payload.setdefault('vehicle_role', vehicle_meta.get('vehicle_role', 'traffic'))
                    vehicle_payload.setdefault('connectivity', vehicle_meta.get('connectivity', '5g_native'))
                    vehicle_payload.setdefault('gateway_id', vehicle_meta.get('gateway_id', 'GW-VEH-01'))
                    vehicle_payload.setdefault('domain', vehicle_meta.get('domain', 'vehicular'))
                    vehicle_payload.setdefault('mobility_profile', vehicle_meta.get('mobility_profile', 'vehicle'))
                existing_packet_loss = result['ue_metrics'][imsi].get('packet_loss_percent')
                existing_pdcp_provenance = result['ue_metrics'][imsi].get('pdcp_provenance')
                preserve_real_loss = (
                    existing_pdcp_provenance == 'pdcp_real'
                    and existing_packet_loss is not None
                )
                result['ue_metrics'][imsi].update({
                    'vehicle_id': vehicle_payload.get('vehicle_id', f'veh-imsi-{imsi}'),
                    'vehicle_role': vehicle_payload.get('vehicle_role', vehicle_payload.get('role', 'traffic')),
                    'connectivity': vehicle_payload.get('connectivity', '5g_native'),
                    'gateway_id': vehicle_payload.get('gateway_id', 'GW-VEH-PROXY'),
                    'domain': vehicle_payload.get('domain', 'vehicular'),
                    'mobility_profile': vehicle_payload.get('mobility_profile', 'vehicle'),
                    'autonomy_state': vehicle_payload.get('autonomy_state', 'normal'),
                    'risk_state': vehicle_payload.get('risk_state', 'low'),
                    'packet_loss_percent': (
                        existing_packet_loss
                        if preserve_real_loss
                        else (
                            float(vehicle_payload.get('packet_loss_percent'))
                            if vehicle_payload.get('packet_loss_percent') is not None
                            else None
                        )
                    ),
                    'speed_mps': float(vehicle_payload.get('speed_mps', 0.0) or 0.0),
                    'heading_deg': float(vehicle_payload.get('heading_deg', 0.0) or 0.0),
                    'lane_id': vehicle_payload.get('lane_id'),
                    'waypoint_id': vehicle_payload.get('waypoint_id'),
                    'position': vehicle_payload.get('position', {'x': 0.0, 'y': 0.0, 'z': 0.0}),
                })

            if mac_entry:
                if mac_entry['mcs']:
                    result['ue_metrics'][imsi]['mcs_avg'] = sum(mac_entry['mcs']) / len(mac_entry['mcs'])
                if mac_entry['tb_sizes']:
                    result['ue_metrics'][imsi]['tb_size_avg'] = sum(mac_entry['tb_sizes']) / len(mac_entry['tb_sizes'])

        worst_latency = max(latencies) if latencies else 0.0
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        result['global_metrics']['global_worst_latency_us'] = worst_latency
        result['global_metrics']['global_worst_camera_latency_us'] = max(
            (data.get('latency_us', 0.0) for data in result['ue_metrics'].values() if data.get('device_type') == 'camera'),
            default=0.0,
        )
        result['global_metrics']['global_avg_latency_us'] = avg_latency
        result['global_metrics']['global_min_latency_us'] = min(latencies) if latencies else 0.0
        result['global_metrics']['global_max_latency_us'] = worst_latency
        result['global_metrics']['global_packet_loss_rate'] = self._calculate_packet_loss(worst_latency, critical_count, camera_count)
        mac_total_tb_bytes = sum(int(data.get('tb_bytes_total', 0) or 0) for data in mac_by_imsi.values())
        mac_window_s = max(mac_window_end - mac_window_start, max(self.poll_interval, 1.0)) if mac_window_end > 0.0 and mac_window_start != float('inf') else max(self.poll_interval, 1.0)
        mac_total_throughput_kbps = (mac_total_tb_bytes * 8.0) / (mac_window_s * 1000.0) if mac_total_tb_bytes > 0 else 0.0
        cu_up_total_throughput_kbps = float((cu_up_metrics or {}).get('total_throughput_kbps', 0) or 0)
        result['global_metrics']['throughput_kbps'] = cu_up_total_throughput_kbps if cu_up_total_throughput_kbps > 0 else mac_total_throughput_kbps
        result['global_metrics']['throughput_source'] = 'cu_up_fallback' if cu_up_total_throughput_kbps > 0 else 'mac_tb_window_fallback'
        result['global_metrics']['pdcp_delta_throughput_kbps'] = 0.0
        result['global_metrics']['cu_up_total_throughput_kbps'] = cu_up_total_throughput_kbps
        result['global_metrics']['mac_total_throughput_kbps'] = mac_total_throughput_kbps
        result['global_metrics']['total_tx_bytes'] = mac_total_tb_bytes
        result['global_metrics']['total_rx_bytes'] = mac_total_tb_bytes
        result['global_metrics']['total_tx_pdus'] = sum(len(data.get('tb_sizes', [])) for data in mac_by_imsi.values())
        result['global_metrics']['total_rx_pdus'] = result['global_metrics']['total_tx_pdus']
        result['global_metrics']['total_active_ues'] = len(result['ue_metrics'])
        result['global_metrics']['total_active_cameras'] = camera_count
        result['global_metrics']['total_active_sensors'] = sensor_count
        result['global_metrics']['total_active_vehicles'] = vehicle_count
        result['global_metrics']['total_critical_ues'] = critical_count
        result['global_metrics']['latency_p5_us'] = self.percentile_5(latencies) if latencies else 0.0
        result['global_metrics']['latency_p95_us'] = self.percentile_95(latencies) if latencies else 0.0
        result['global_metrics']['latency_min_nonzero_us'] = self.min_nonzero(latencies)
        result['global_metrics']['latency_median_us'] = self.median(latencies) if latencies else 0.0
        result['global_metrics']['latency_p95_per_ue_us'] = result['global_metrics']['latency_p95_us']
        result['global_metrics']['cvar_per_ue_us'] = self.calculate_cvar(latencies, tail_fraction=0.05) if latencies else 0.0
        result['global_metrics']['cvar_tail_count'] = max(1, math.ceil(len(latencies) * 0.05)) if latencies else 0
        result['global_metrics']['ue_count'] = len(latencies)
        result['global_metrics']['ues_with_latency_samples'] = len(latencies)
        result['global_metrics']['ues_without_latency_samples'] = max(0, len(result['ue_metrics']) - len(latencies))
        result['global_metrics']['latency_sample_source_counts'] = dict(latency_source_counts)
        result['global_metrics']['real_latency_sample_count'] = real_latency_sample_count
        result['global_metrics']['proxy_latency_sample_count'] = proxy_latency_sample_count
        result['global_metrics']['collector_mode'] = 'no_pdcp_proxy_latency'
        result['global_metrics']['pdcp_provenance'] = 'pdcp_real' if proxy_latency_sample_count == 0 and real_latency_sample_count > 0 else 'proxy'
        result['global_metrics']['pdcp_real'] = proxy_latency_sample_count == 0 and real_latency_sample_count > 0
        result['global_metrics']['scenario_override_active'] = bool(app1_override or app2_override or vehicle_override)
        result['global_metrics']['effective_source'] = 'pdcp_real' if proxy_latency_sample_count == 0 and real_latency_sample_count > 0 else 'proxy'

        result['active_cameras'] = camera_count
        result['critical_cameras'] = camera_critical_count
        result['critical_ues'] = critical_count

        return result
    
    def aggregate_metrics(self, pdcp_metrics, mac_metrics, rlc_metrics=None, cu_up_metrics=None, mmwave_sched_metrics=None, pdcp_file=None, rlc_file=None):
        """Aggregate all metrics into comprehensive JSON"""
        
        result = {
            'timestamp': int(time.time() * 1000),
            'timestamp_iso': time.strftime('%Y-%m-%d %H:%M:%S'),
            'sim_time_range': {},
            'ue_metrics': {},
            'cell_metrics': {},
            'global_metrics': {
                'global_worst_latency_us': 0.0,
                'global_avg_latency_us': 0.0,
                'global_min_latency_us': float('inf'),
                'global_max_latency_us': 0.0,
                'global_jitter_us': 0.0,
                'global_packet_loss_rate': 0.0,
                'total_active_ues': 0,
                'total_active_cameras': 0,
                'total_active_vehicles': 0,
                'total_critical_ues': 0,
                'total_tx_bytes': 0,
                'total_rx_bytes': 0,
                'total_tx_pdus': 0,
                'total_rx_pdus': 0,
                'throughput_kbps': 0.0
            },
            'active_cameras': 0,
            'critical_cameras': 0
        }
        
        if not pdcp_metrics:
            return self.aggregate_metrics_without_pdcp(
                mac_metrics,
                rlc_metrics=rlc_metrics,
                cu_up_metrics=cu_up_metrics,
                mmwave_sched_metrics=mmwave_sched_metrics,
            )
        
        MAX_LATENCY_THRESHOLD_US = 500000  # 500ms - filter outliers
        
        pdcp_metrics = [m for m in pdcp_metrics if m.get('delay_us', 0) <= MAX_LATENCY_THRESHOLD_US]
        
        if not pdcp_metrics:
            return self.aggregate_metrics_without_pdcp(
                mac_metrics,
                rlc_metrics=rlc_metrics,
                cu_up_metrics=cu_up_metrics,
                mmwave_sched_metrics=mmwave_sched_metrics,
            )
        
        pdcp_metrics.sort(key=lambda m: (m.get('time_end', 0), m.get('time_start', 0)))

        current_time = pdcp_metrics[-1].get('time_end', 0)
        recent_window = 30.0
        recent_threshold = max(0.0, current_time - recent_window)
        recent_metrics = [
            m for m in pdcp_metrics
            if m.get('time_end', m.get('time_start', 0)) >= recent_threshold
        ]
        
        if not recent_metrics:
            return result
        
        observed_window_start = recent_metrics[0].get('time_start', 0)
        observed_window_end = recent_metrics[-1].get('time_end', recent_metrics[-1].get('time_start', 0))
        observed_window_s = max(observed_window_end - observed_window_start, 1e-6)

        result['sim_time_range'] = {
            'start': observed_window_start,
            'end': observed_window_end,
            'window_s': observed_window_s
        }
        
        result['pdcp_rnti_to_imsi'] = {}
        for m in pdcp_metrics:
            rnti = m.get('rnti', 0)
            imsi = m.get('imsi', '')
            if rnti > 0 and imsi:
                result['pdcp_rnti_to_imsi'][rnti] = imsi
        
        ue_data = defaultdict(lambda: {
            'latencies': [],
            'tx_bytes': 0,
            'rx_bytes': 0,
            'tx_pdus': 0,
            'rx_pdus': 0,
            'jitters': [],
            'pdu_sizes': [],
            'lat_min': float('inf'),
            'lat_max': 0.0,
            'device_type': 'unknown',
            'cell_id': 0,
            'time_start_min': float('inf'),
            'time_end_max': 0.0,
        })
        
        all_latencies = []
        all_jitters = []
        
        # Atualizar cache de atividade dos UEs
        current_sim_time = recent_metrics[-1]['time_end'] if recent_metrics else self.last_processed_time
        for m in recent_metrics:
            self.active_ues_cache[m['imsi']] = m['time_end']
            
        # Remover UEs inativos (mais de 5 segundos sem pacotes)
        active_imsis = [imsi for imsi, last_seen in self.active_ues_cache.items() 
                       if (current_sim_time - last_seen) < self.activity_window]
        self.active_ues_cache = {imsi: self.active_ues_cache[imsi] for imsi in active_imsis}

        for m in recent_metrics:
            imsi = m['imsi']
            ue_data[imsi]['tx_bytes'] += m['tx_bytes']
            ue_data[imsi]['rx_bytes'] += m['rx_bytes']
            ue_data[imsi]['tx_pdus'] += m['n_tx_pdus']
            ue_data[imsi]['rx_pdus'] += m['n_rx_pdus']
            ue_data[imsi]['device_type'] = m['device_type']
            ue_data[imsi]['cell_id'] = m['cell_id']
            ue_data[imsi]['time_start_min'] = min(ue_data[imsi]['time_start_min'], m['time_start'])
            ue_data[imsi]['time_end_max'] = max(ue_data[imsi]['time_end_max'], m['time_end'])

            has_valid_latency = m['n_rx_pdus'] > 0 and m['delay_us'] > 0
            if has_valid_latency:
                ue_data[imsi]['latencies'].append(m['delay_us'])
                ue_data[imsi]['jitters'].append(m['delay_stddev_us'])
                ue_data[imsi]['pdu_sizes'].append(m['pdu_size'])
                ue_data[imsi]['lat_min'] = min(ue_data[imsi]['lat_min'], m['delay_min_us'])
                ue_data[imsi]['lat_max'] = max(ue_data[imsi]['lat_max'], m['delay_max_us'])

        # Garantir que UEs no cache mas sem tráfego recente também sejam processados
        for imsi in active_imsis:
            if imsi not in ue_data:
                ue_data[imsi]['device_type'] = self.get_device_type(imsi)
                ue_data[imsi]['latencies'] = [] # Ativo mas sem dados novos
        
        camera_count = 0
        sensor_count = 0
        vehicle_count = 0
        camera_critical_count = 0
        critical_count = 0
        total_tx_bytes = 0
        total_rx_bytes = 0
        worst_latency = 0
        worst_camera_latency = 0
        latency_source_counts = defaultdict(int)
        real_latency_sample_count = 0
        proxy_latency_sample_count = 0
        app1_override = self._load_app1_camera_override()
        app2_override = self._load_app2_sensor_override()
        vehicle_override = self._load_vehicle_override()
        vehicle_override_index = 0
        
        SLA_THRESHOLD_US = 100000
        
        for imsi, data in ue_data.items():
            has_latency_samples = bool(data['latencies'])
            avg_latency = sum(data['latencies']) / len(data['latencies']) if has_latency_samples else 0
            max_latency = max(data['latencies']) if has_latency_samples else 0
            avg_jitter = sum(data['jitters']) / len(data['jitters']) if data['jitters'] else 0
            avg_pdu_size = sum(data['pdu_sizes']) / len(data['pdu_sizes']) if data['pdu_sizes'] else 0
            
            active_window_s = observed_window_s
            if data['time_start_min'] != float('inf') and data['time_end_max'] > 0:
                active_window_s = max(data['time_end_max'] - data['time_start_min'], 1e-6)

            tx_throughput_kbps = (data['tx_bytes'] * 8) / (active_window_s * 1000) if active_window_s > 0 else 0
            rx_throughput_kbps = (data['rx_bytes'] * 8) / (active_window_s * 1000) if active_window_s > 0 else 0
            total_pdcp_throughput_kbps = ((data['tx_bytes'] + data['rx_bytes']) * 8) / (active_window_s * 1000) if active_window_s > 0 else 0
            throughput_kbps = rx_throughput_kbps
            tx_pdus = int(data.get('tx_pdus', 0) or 0)
            rx_pdus = int(data.get('rx_pdus', 0) or 0)
            pdcp_real_observed = tx_pdus > 0 or rx_pdus > 0
            packet_loss_percent = (
                max(0.0, min(100.0, ((tx_pdus - rx_pdus) / tx_pdus) * 100.0))
                if tx_pdus > 0 else None
            )

            result['ue_metrics'][imsi] = {
                'device_type': data['device_type'],
                'cell_id': data.get('cell_id', 0),
                'latency_us': max_latency,
                'latency_avg_us': avg_latency,
                'latency_min_us': data['lat_min'] if data['lat_min'] != float('inf') else 0,
                'latency_max_us': data['lat_max'] if has_latency_samples else 0,
                'latency_p95_us': self.percentile_95(data['latencies']) if has_latency_samples else 0,
                'jitter_us': avg_jitter,
                'pdu_size_avg': avg_pdu_size,
                'tx_bytes': data['tx_bytes'],
                'rx_bytes': data['rx_bytes'],
                'tx_pdus': tx_pdus,
                'rx_pdus': rx_pdus,
                'sample_window_s': active_window_s,
                'throughput_kbps': throughput_kbps,
                'tx_throughput_kbps': tx_throughput_kbps,
                'rx_throughput_kbps': rx_throughput_kbps,
                'total_pdcp_throughput_kbps': total_pdcp_throughput_kbps,
                'packet_count': len(data['latencies']),
                'has_latency_samples': has_latency_samples,
                'latency_source': 'pdcp_real' if has_latency_samples else '',
                'latency_is_proxy': False,
                'pdcp_provenance': 'pdcp_real' if pdcp_real_observed else 'unavailable',
                'packet_loss_percent': packet_loss_percent,
                'pdcp_loss_source': 'pdcp_real_tx_rx' if packet_loss_percent is not None else 'unavailable',
                'scenario_override_active': False,
                'effective_source': 'pdcp_real' if has_latency_samples else 'unavailable',
                'is_critical': has_latency_samples and max_latency >= SLA_THRESHOLD_US,
                'throughput_source': 'pdcp_rx_window'
            }

            if (
                not result['ue_metrics'][imsi]['has_latency_samples']
                and data['device_type'] == 'camera'
                and app1_override
                and app1_override.get('latency_ms') is not None
            ):
                override_latency_us = max(1.0, float(app1_override.get('latency_ms') or 0.0) * 1000.0)
                result['ue_metrics'][imsi].update({
                    'latency_us': override_latency_us,
                    'latency_avg_us': override_latency_us,
                    'latency_min_us': override_latency_us,
                    'latency_max_us': override_latency_us,
                    'jitter_us': 0.0,
                    'packet_count': max(int(result['ue_metrics'][imsi].get('packet_count', 0) or 0), 1),
                    'has_latency_samples': True,
                    'latency_source': 'app1_camera_override',
                    'latency_is_proxy': True,
                    'pdcp_provenance': 'proxy',
                    'scenario_override_active': True,
                    'effective_source': 'app1_camera_override',
                    'is_critical': override_latency_us >= SLA_THRESHOLD_US,
                })
            elif (
                not result['ue_metrics'][imsi]['has_latency_samples']
                and data['device_type'] == 'sensor'
                and app2_override
                and app2_override.get('avg_latency_ms') is not None
            ):
                override_latency_us = max(1.0, float(app2_override.get('avg_latency_ms') or 0.0) * 1000.0)
                result['ue_metrics'][imsi].update({
                    'latency_us': override_latency_us,
                    'latency_avg_us': override_latency_us,
                    'latency_min_us': override_latency_us,
                    'latency_max_us': override_latency_us,
                    'jitter_us': 0.0,
                    'packet_count': max(int(result['ue_metrics'][imsi].get('packet_count', 0) or 0), 1),
                    'has_latency_samples': True,
                    'latency_source': 'app2_sensor_override',
                    'latency_is_proxy': True,
                    'pdcp_provenance': 'proxy',
                    'scenario_override_active': True,
                    'effective_source': 'app2_sensor_override',
                    'is_critical': override_latency_us >= SLA_THRESHOLD_US,
                })

            if result['ue_metrics'][imsi]['device_type'] == 'vehicle':
                vehicle_meta = self.get_carla_vehicle_binding(imsi)
                vehicle_id = vehicle_meta.get('vehicle_id') if vehicle_meta else None
                vehicle_state = self.get_carla_vehicle_state(vehicle_id) if vehicle_meta else {}
                vehicle_payload = dict(vehicle_state or {})
                if vehicle_meta:
                    vehicle_payload.setdefault('vehicle_id', vehicle_id)
                    vehicle_payload.setdefault('vehicle_role', vehicle_meta.get('vehicle_role', 'traffic'))
                    vehicle_payload.setdefault('connectivity', vehicle_meta.get('connectivity', '5g_native'))
                    vehicle_payload.setdefault('gateway_id', vehicle_meta.get('gateway_id', 'GW-VEH-01'))
                    vehicle_payload.setdefault('domain', vehicle_meta.get('domain', 'vehicular'))
                    vehicle_payload.setdefault('mobility_profile', vehicle_meta.get('mobility_profile', 'vehicle'))
                elif vehicle_override:
                    vehicle_payload.setdefault('vehicle_id', f'veh-imsi-{imsi}')
                    vehicle_payload.setdefault('vehicle_role', 'ego' if vehicle_override_index == 0 else 'traffic')
                if vehicle_override:
                    vehicle_payload = self._apply_vehicle_override(vehicle_payload, vehicle_override, vehicle_override_index)

                position = vehicle_payload.get('position')
                if not isinstance(position, dict):
                    position = {
                        'x': float(vehicle_payload.get('x', 0.0) or 0.0),
                        'y': float(vehicle_payload.get('y', 0.0) or 0.0),
                        'z': float(vehicle_payload.get('z', 0.0) or 0.0),
                    }
                existing_packet_loss = result['ue_metrics'][imsi].get('packet_loss_percent')
                existing_pdcp_provenance = result['ue_metrics'][imsi].get('pdcp_provenance')
                preserve_real_loss = (
                    existing_pdcp_provenance == 'pdcp_real'
                    and existing_packet_loss is not None
                )
                result['ue_metrics'][imsi].update({
                    'vehicle_id': vehicle_payload.get('vehicle_id', vehicle_id or f'veh-imsi-{imsi}'),
                    'vehicle_role': vehicle_payload.get('vehicle_role', vehicle_payload.get('role', 'traffic')),
                    'connectivity': vehicle_payload.get('connectivity', '5g_native'),
                    'gateway_id': vehicle_payload.get('gateway_id', 'GW-VEH-01'),
                    'domain': vehicle_payload.get('domain', 'vehicular'),
                    'mobility_profile': vehicle_payload.get('mobility_profile', 'vehicle'),
                    'autonomy_state': vehicle_payload.get('autonomy_state', vehicle_meta.get('autonomy_state', 'normal') if vehicle_meta else 'normal'),
                    'risk_state': vehicle_payload.get('risk_state', vehicle_meta.get('risk_state', 'low') if vehicle_meta else 'low'),
                    'packet_loss_percent': (
                        existing_packet_loss
                        if preserve_real_loss
                        else (
                            float(vehicle_payload.get('packet_loss_percent'))
                            if vehicle_payload.get('packet_loss_percent') is not None
                            else None
                        )
                    ),
                    'speed_mps': float(vehicle_payload.get('speed_mps', 0.0) or 0.0),
                    'heading_deg': float(vehicle_payload.get('heading_deg', 0.0) or 0.0),
                    'lane_id': vehicle_payload.get('lane_id'),
                    'waypoint_id': vehicle_payload.get('waypoint_id'),
                    'position': position,
                })
                override_latency_ms = vehicle_payload.get('latency_ms')
                if vehicle_override and override_latency_ms is not None and not result['ue_metrics'][imsi].get('has_latency_samples'):
                    override_latency_us = max(1.0, float(override_latency_ms or 0.0) * 1000.0)
                    result['ue_metrics'][imsi].update({
                        'latency_us': override_latency_us,
                        'latency_avg_us': override_latency_us,
                        'latency_min_us': override_latency_us,
                        'latency_max_us': override_latency_us,
                        'jitter_us': 0.0,
                        'packet_count': max(int(result['ue_metrics'][imsi].get('packet_count', 0) or 0), 1),
                        'has_latency_samples': True,
                        'latency_source': 'vehicle_state_proxy',
                        'latency_is_proxy': True,
                        'pdcp_provenance': 'proxy',
                        'scenario_override_active': True,
                        'effective_source': 'vehicle_state_proxy',
                        'is_critical': override_latency_us >= SLA_THRESHOLD_US,
                    })
                vehicle_override_index += 1

            effective_has_latency_samples = bool(result['ue_metrics'][imsi].get('has_latency_samples'))
            effective_latency_us = float(result['ue_metrics'][imsi].get('latency_us', 0.0) or 0.0)
            effective_is_critical = bool(result['ue_metrics'][imsi].get('is_critical'))
            ue_latency_source = result['ue_metrics'][imsi].get('latency_source', '')
            if effective_has_latency_samples and ue_latency_source:
                latency_source_counts[ue_latency_source] += 1
                if result['ue_metrics'][imsi].get('latency_is_proxy'):
                    proxy_latency_sample_count += 1
                else:
                    real_latency_sample_count += 1
                all_latencies.append(effective_latency_us)
                all_jitters.append(float(result['ue_metrics'][imsi].get('jitter_us', 0.0) or 0.0))

            total_tx_bytes += data['tx_bytes']
            total_rx_bytes += data['rx_bytes']
            
            if data['device_type'] == 'camera':
                camera_count += 1
                if effective_has_latency_samples and effective_is_critical:
                    camera_critical_count += 1
                if effective_has_latency_samples and effective_latency_us > worst_camera_latency:
                    worst_camera_latency = effective_latency_us
            elif data['device_type'] == 'sensor':
                sensor_count += 1
            elif data['device_type'] == 'vehicle':
                vehicle_count += 1
            
            if effective_has_latency_samples and effective_is_critical:
                critical_count += 1
            
            if effective_has_latency_samples and effective_latency_us > worst_latency:
                worst_latency = effective_latency_us

        self._refresh_carla_vehicle_map()
        carla_vehicle_imsis = {
            str(imsi) for imsi, meta in self.carla_vehicle_map.items()
            if isinstance(meta, dict) and meta.get('device_type') == 'vehicle'
        }
        proxy_carla_vehicle_imsis = set()

        for imsi in sorted(carla_vehicle_imsis - set(result['ue_metrics'].keys()), key=lambda value: int(value) if str(value).isdigit() else str(value)):
            vehicle_meta = self.get_carla_vehicle_binding(imsi)
            vehicle_id = vehicle_meta.get('vehicle_id')
            vehicle_state = self.get_carla_vehicle_state(vehicle_id)
            latency_us, latency_source = self._estimate_proxy_latency_us(
                imsi,
                'vehicle',
                0.0,
                {},
                {},
                vehicle_state=vehicle_state,
            )
            has_latency_samples = latency_us > 0
            is_critical = has_latency_samples and latency_us >= SLA_THRESHOLD_US
            position = vehicle_state.get('position')
            if not isinstance(position, dict):
                position = {
                    'x': float(vehicle_state.get('x', 0.0) or 0.0),
                    'y': float(vehicle_state.get('y', 0.0) or 0.0),
                    'z': float(vehicle_state.get('z', 0.0) or 0.0),
                }

            result['ue_metrics'][imsi] = {
                'device_type': 'vehicle',
                'cell_id': 0,
                'latency_us': latency_us,
                'latency_avg_us': latency_us,
                'latency_min_us': latency_us if has_latency_samples else 0,
                'latency_max_us': latency_us if has_latency_samples else 0,
                'jitter_us': 0,
                'pdu_size_avg': 0,
                'tx_bytes': 0,
                'rx_bytes': 0,
                'tx_pdus': 0,
                'rx_pdus': 0,
                'throughput_kbps': 0.0,
                'tx_throughput_kbps': 0.0,
                'rx_throughput_kbps': 0.0,
                'total_pdcp_throughput_kbps': 0.0,
                'packet_count': 1 if has_latency_samples else 0,
                'has_latency_samples': has_latency_samples,
                'latency_source': latency_source,
                'latency_is_proxy': True,
                'is_critical': is_critical,
                'throughput_source': 'carla_context_only',
                'proxy_expanded': True,
                'vehicle_id': vehicle_id or f'veh-imsi-{imsi}',
                'vehicle_role': vehicle_meta.get('vehicle_role', vehicle_state.get('vehicle_role', vehicle_state.get('role', 'traffic'))),
                'connectivity': vehicle_meta.get('connectivity', '5g_native'),
                'gateway_id': vehicle_meta.get('gateway_id', 'GW-VEH-01'),
                'domain': vehicle_meta.get('domain', 'vehicular'),
                'mobility_profile': vehicle_meta.get('mobility_profile', 'vehicle'),
                'autonomy_state': vehicle_state.get('autonomy_state', vehicle_meta.get('autonomy_state', 'normal')),
                'risk_state': vehicle_state.get('risk_state', vehicle_meta.get('risk_state', 'low')),
                'packet_loss_percent': float(vehicle_state.get('packet_loss_percent', 0.0) or 0.0),
                'speed_mps': float(vehicle_state.get('speed_mps', 0.0) or 0.0),
                'heading_deg': float(vehicle_state.get('heading_deg', 0.0) or 0.0),
                'lane_id': vehicle_state.get('lane_id'),
                'waypoint_id': vehicle_state.get('waypoint_id'),
                'position': position,
            }

            proxy_carla_vehicle_imsis.add(imsi)
            vehicle_count += 1
            if is_critical:
                critical_count += 1
            if has_latency_samples:
                all_latencies.append(latency_us)
                all_jitters.append(0.0)
                latency_source_counts[latency_source] += 1
                proxy_latency_sample_count += 1
                worst_latency = max(worst_latency, latency_us)
        
        global_avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0
        global_avg_jitter = sum(all_jitters) / len(all_jitters) if all_jitters else 0
        
        global_min_latency = min(all_latencies) if all_latencies else 0
        global_max_latency = max(all_latencies) if all_latencies else 0
        
        # Métricas robustas - todas as amostras válidas já excluem zeros sem Rx
        latency_p5 = self.percentile_5(all_latencies) if all_latencies else 0
        latency_p95 = self.percentile_95(all_latencies)
        latency_min_nonzero = self.min_nonzero(all_latencies)
        latency_median = self.median(all_latencies) if all_latencies else 0
        
        # Métricas POR UE para capturar UEs críticos
        OUTLIER_THRESHOLD_US = 500000  # 500ms - filtrar outliers extremos
        ue_avg_latencies = []
        ue_worst_latencies = []
        for imsi, data in ue_data.items():
            if data['latencies']:
                # Filtrar outliers antes de calcular métricas
                valid_latencies = [l for l in data['latencies'] if l < OUTLIER_THRESHOLD_US]
                if not valid_latencies:
                    valid_latencies = data['latencies']  # Fallback se todos forem outliers
                # Usar a MÉDIA de cada UE
                ue_avg = sum(valid_latencies) / len(valid_latencies)
                ue_avg_latencies.append(ue_avg)
                # Usar a PIOR latência de cada UE (para CVaR correto)
                ue_worst = max(valid_latencies)
                ue_worst_latencies.append(ue_worst)

        for imsi in proxy_carla_vehicle_imsis:
            latency_us = float(result['ue_metrics'][imsi].get('latency_us', 0.0) or 0.0)
            if latency_us > 0.0:
                ue_avg_latencies.append(latency_us)
                ue_worst_latencies.append(latency_us)
        
        # CVaR e Variância agora usam latência POR UE
        if ue_avg_latencies:
            latency_p95_per_ue = self.percentile_95(ue_avg_latencies)
            
            ue_mean = sum(ue_avg_latencies) / len(ue_avg_latencies)
            variance_per_ue = sum((x - ue_mean) ** 2 for x in ue_avg_latencies) / len(ue_avg_latencies)
            
            if ue_worst_latencies:
                cvar_per_ue = self.calculate_cvar(ue_worst_latencies, tail_fraction=0.05)
                cvar_tail_count = max(1, math.ceil(len(ue_worst_latencies) * 0.05))
            else:
                cvar_per_ue = 0
                cvar_tail_count = 0
        else:
            latency_p95_per_ue = latency_p95
            variance_per_ue = 0
            cvar_per_ue = 0
            cvar_tail_count = 0

        # Calculate throughput using PDCP delta (fallback / cross-check)
        current_sim_time = recent_metrics[-1].get('time_end', 0) if recent_metrics else 0
        sim_time_delta = current_sim_time - self.last_sim_time

        if self.last_sim_time > 0 and sim_time_delta > 0:
            delta_tx = total_tx_bytes - self.last_total_tx_bytes
            delta_rx = total_rx_bytes - self.last_total_rx_bytes
            delta_throughput = (delta_tx + delta_rx) * 8 / (sim_time_delta * 1000)
            pdcp_delta_throughput_kbps = max(0, delta_throughput)
        else:
            total_throughput = total_tx_bytes + total_rx_bytes
            pdcp_delta_throughput_kbps = (total_throughput * 8) / (observed_window_s * 1000) if observed_window_s > 0 else 0

        self.last_total_tx_bytes = total_tx_bytes
        self.last_total_rx_bytes = total_rx_bytes
        self.last_sim_time = current_sim_time

        pdcp_window_throughput_kbps = sum(
            float(ue.get('throughput_kbps', 0) or 0)
            for ue in result['ue_metrics'].values()
        )
        cu_up_total_throughput_kbps = float((cu_up_metrics or {}).get('total_throughput_kbps', 0) or 0)
        throughput_kbps = pdcp_delta_throughput_kbps
        throughput_source = 'pdcp_delta'
        pdcp_cu_up_ratio = (
            pdcp_delta_throughput_kbps / cu_up_total_throughput_kbps
            if cu_up_total_throughput_kbps > 0
            else 1.0
        )
        pdcp_window_ratio = (
            pdcp_delta_throughput_kbps / pdcp_window_throughput_kbps
            if pdcp_window_throughput_kbps > 0
            else 1.0
        )

        # The PDCP delta is sensitive to asynchronous file flushes. When the
        # delta collapses but the per-UE PDCP window throughput is still high,
        # prefer the window view to avoid publishing false near-zero spikes.
        pdcp_delta_stale_vs_window = (
            pdcp_window_throughput_kbps >= 10_000.0
            and (
                pdcp_delta_throughput_kbps <= 1_000.0
                or pdcp_window_ratio < 0.2
            )
        )
        if pdcp_delta_stale_vs_window:
            throughput_kbps = pdcp_window_throughput_kbps
            throughput_source = 'pdcp_rx_window_fallback'

        # When PDCP/RLC snapshots lag behind the fresher CU-UP files, the PDCP
        # delta can briefly collapse to near-zero even though the real cell
        # throughput is still healthy. Prefer CU-UP only if the PDCP window is
        # also unavailable and CU-UP is the only healthy signal left.
        pdcp_stale_vs_cu_up = (
            not pdcp_delta_stale_vs_window
            and
            cu_up_total_throughput_kbps >= 10_000.0
            and (
                pdcp_delta_throughput_kbps <= 1_000.0
                or pdcp_cu_up_ratio < 0.2
            )
        )
        if pdcp_stale_vs_cu_up:
            throughput_kbps = cu_up_total_throughput_kbps
            throughput_source = 'cu_up_stale_pdcp_fallback'

        result['global_metrics'] = {
            'global_worst_latency_us': worst_latency,
            'global_worst_camera_latency_us': worst_camera_latency,
            'global_avg_latency_us': global_avg_latency,
            'global_min_latency_us': global_min_latency,
            'global_max_latency_us': global_max_latency,
            'global_jitter_us': global_avg_jitter,
            'global_packet_loss_rate': self._calculate_packet_loss(worst_latency, critical_count, camera_count),
            'total_active_ues': len(result['ue_metrics']),
            'total_active_cameras': camera_count,
            'total_active_sensors': sensor_count,
            'total_active_vehicles': vehicle_count,
            'total_critical_ues': critical_count,
            'total_tx_bytes': total_tx_bytes,
            'total_rx_bytes': total_rx_bytes,
            'total_tx_pdus': sum(d.get('tx_pdus', 0) for d in result['ue_metrics'].values()),
            'total_rx_pdus': sum(d.get('rx_pdus', 0) for d in result['ue_metrics'].values()),
            'throughput_kbps': throughput_kbps,
            'throughput_source': throughput_source,
            'pdcp_delta_throughput_kbps': pdcp_delta_throughput_kbps,
            'pdcp_window_throughput_kbps': pdcp_window_throughput_kbps,
            'cu_up_total_throughput_kbps': cu_up_total_throughput_kbps,
            'pdcp_cu_up_ratio': pdcp_cu_up_ratio,
            'pdcp_window_ratio': pdcp_window_ratio,
            'pdcp_delta_stale_vs_window': pdcp_delta_stale_vs_window,
            'pdcp_stale_vs_cu_up': pdcp_stale_vs_cu_up,
            'latency_p5_us': latency_p5,
            'latency_p95_us': latency_p95,
            'latency_min_nonzero_us': latency_min_nonzero,
            'latency_median_us': latency_median,
            'latency_p95_per_ue_us': latency_p95_per_ue,
            'variance_per_ue_us2': variance_per_ue,
            'cvar_per_ue_us': cvar_per_ue,
            'cvar_tail_count': cvar_tail_count,
            'ue_count': len(ue_avg_latencies),
            'ues_with_latency_samples': len(ue_avg_latencies),
            'ues_without_latency_samples': max(0, len(result['ue_metrics']) - len(ue_avg_latencies)),
            'collector_mode': 'pdcp_real',
            'pdcp_provenance': 'pdcp_real' if proxy_latency_sample_count == 0 else 'mixed_real_proxy',
            'pdcp_real': proxy_latency_sample_count == 0 and real_latency_sample_count > 0,
            'scenario_override_active': bool(app1_override or app2_override or vehicle_override),
            'effective_source': 'pdcp_real' if proxy_latency_sample_count == 0 and real_latency_sample_count > 0 else 'proxy',
            'latency_sample_source_counts': dict(latency_source_counts),
            'real_latency_sample_count': real_latency_sample_count,
            'proxy_latency_sample_count': proxy_latency_sample_count,
            'proxy_expanded_sensors': False,
            'proxy_virtual_vehicles': bool(proxy_carla_vehicle_imsis),
            'lte_mac_observed_ues': 0,
            'mmwave_sched_observed_rntis': 0,
            'mmwave_sched_observed_data_rntis': 0,
            'mmwave_sched_observed_control_rntis': 0,
            'mmwave_sched_mapped_ues': 0,
            'mmwave_sched_window_allocs': 0,
            'mac_trace_mode': 'unknown',
        }
        
        result['active_cameras'] = camera_count
        result['critical_cameras'] = camera_critical_count
        result['critical_ues'] = critical_count
        
        if mac_metrics:
            mac_by_imsi = defaultdict(lambda: {'mcs': [], 'tb_sizes': []})
            all_mcs = []
            all_tb_sizes = []
            
            for m in mac_metrics[-1000:]:
                imsi = m['imsi']
                mac_by_imsi[imsi]['mcs'].append(m['mcs_tb1'])
                mac_by_imsi[imsi]['tb_sizes'].append(m['size_tb1'] + m['size_tb2'])
                all_mcs.append(m['mcs_tb1'])
                all_tb_sizes.append(m['size_tb1'] + m['size_tb2'])
            
            # Métricas globais de MAC
            if all_mcs:
                result['global_metrics']['global_mcs_avg'] = sum(all_mcs) / len(all_mcs) if all_mcs else 0
                result['global_metrics']['global_mcs_min'] = min(all_mcs) if all_mcs else 0
                result['global_metrics']['global_mcs_max'] = max(all_mcs) if all_mcs else 0
                result['global_metrics']['global_tb_size_avg'] = sum(all_tb_sizes) / len(all_tb_sizes) if all_tb_sizes else 0
            result['global_metrics']['lte_mac_observed_ues'] = len(mac_by_imsi)
            
            for imsi, data in mac_by_imsi.items():
                if imsi in result['ue_metrics']:
                    result['ue_metrics'][imsi]['mcs_avg'] = sum(data['mcs']) / len(data['mcs']) if data['mcs'] else 0
                    result['ue_metrics'][imsi]['tb_size_avg'] = sum(data['tb_sizes']) / len(data['tb_sizes']) if data['tb_sizes'] else 0

        if mmwave_sched_metrics:
            mmwave_sched_observed_rntis = int(mmwave_sched_metrics.get('observed_rntis', 0) or 0)
            mmwave_sched_data_rntis = int(mmwave_sched_metrics.get('observed_data_rntis', 0) or 0)
            result['global_metrics']['mmwave_sched_observed_rntis'] = mmwave_sched_observed_rntis
            result['global_metrics']['mmwave_sched_observed_data_rntis'] = mmwave_sched_data_rntis
            result['global_metrics']['mmwave_sched_observed_control_rntis'] = int(mmwave_sched_metrics.get('observed_control_rntis', 0) or 0)
            result['global_metrics']['mmwave_sched_window_allocs'] = int(mmwave_sched_metrics.get('window_row_count', 0) or 0)

            lte_mac_observed = int(result['global_metrics'].get('lte_mac_observed_ues', 0) or 0)
            if lte_mac_observed <= 1 and mmwave_sched_data_rntis > 1:
                result['global_metrics']['mac_trace_mode'] = 'lte_anchor_only_mmwave_multi'
            elif lte_mac_observed > 1:
                result['global_metrics']['mac_trace_mode'] = 'lte_per_ue'
            elif mmwave_sched_data_rntis > 0:
                result['global_metrics']['mac_trace_mode'] = 'mmwave_scheduler_only'

        if rlc_metrics:
            rlc_by_imsi = defaultdict(int)
            rlc_by_cell = defaultdict(lambda: {'imsis': set(), 'rnti_to_imsi': {}})
            for item in rlc_metrics[-5000:]:
                rlc_by_imsi[item['imsi']] += 1
                if item.get('rnti', 0) > 0:
                    cell_id = item.get('cell_id', 0)
                    rlc_by_cell[cell_id]['imsis'].add(item['imsi'])
                    rlc_by_cell[cell_id]['rnti_to_imsi'][item['rnti']] = item['imsi']

            scheduler_cell_id = 0
            rnti_to_imsi = {}
            pdcp_rnti_to_imsi = result.get('pdcp_rnti_to_imsi', {})
            if pdcp_rnti_to_imsi:
                rnti_to_imsi = dict(pdcp_rnti_to_imsi)
                scheduler_cell_id = 1
            if rlc_by_cell:
                best_cell_id, best_cell_data = max(
                    rlc_by_cell.items(),
                    key=lambda item: len(item[1]['imsis'])
                )
                for rnti_val, imsi_val in best_cell_data['rnti_to_imsi'].items():
                    if rnti_val not in rnti_to_imsi:
                        rnti_to_imsi[rnti_val] = imsi_val

            result['global_metrics']['rlc_record_count'] = len(rlc_metrics)
            result['global_metrics']['rlc_observed_ues'] = len(rlc_by_imsi)
            result['global_metrics']['mmwave_sched_mapping_cell_id'] = scheduler_cell_id

            for imsi, count in rlc_by_imsi.items():
                if imsi in result['ue_metrics']:
                    result['ue_metrics'][imsi]['rlc_records'] = count

            if mmwave_sched_metrics:
                mmwave_by_imsi = {}
                unmapped_rntis = []
                for rnti_text, sched in mmwave_sched_metrics.get('per_rnti', {}).items():
                    try:
                        rnti = int(rnti_text)
                    except ValueError:
                        continue
                    if rnti <= 0:
                        continue

                    imsi = rnti_to_imsi.get(rnti)
                    if not imsi:
                        unmapped_rntis.append(rnti)
                        continue

                    entry = {
                        'rnti': rnti,
                        'allocations': sched.get('allocations', 0),
                        'symbols': sched.get('symbols', 0),
                        'dl_allocations': sched.get('dl_allocations', 0),
                        'ul_allocations': sched.get('ul_allocations', 0),
                        'retx_allocations': sched.get('retx_allocations', 0),
                        'cc_ids': sched.get('cc_ids', []),
                    }
                    mmwave_by_imsi[imsi] = entry
                    if imsi in result['ue_metrics']:
                        result['ue_metrics'][imsi]['mmwave_sched_rnti'] = rnti
                        result['ue_metrics'][imsi]['mmwave_sched_allocations'] = entry['allocations']
                        result['ue_metrics'][imsi]['mmwave_sched_symbols'] = entry['symbols']
                        result['ue_metrics'][imsi]['mmwave_sched_dl_allocations'] = entry['dl_allocations']
                        result['ue_metrics'][imsi]['mmwave_sched_ul_allocations'] = entry['ul_allocations']
                        result['ue_metrics'][imsi]['mmwave_sched_retx_allocations'] = entry['retx_allocations']

                result['mmwave_scheduler_metrics'] = {
                    'by_imsi': mmwave_by_imsi,
                    'unmapped_rntis': sorted(unmapped_rntis),
                    'active_imsis_without_mmwave_sched': sorted(
                        [imsi for imsi in result['ue_metrics'] if imsi not in mmwave_by_imsi],
                        key=lambda value: int(value) if str(value).isdigit() else value
                    ),
                    'mapping_cell_id': scheduler_cell_id,
                    'control_rnti': mmwave_sched_metrics.get('per_rnti', {}).get('0', {}),
                    'window_row_count': mmwave_sched_metrics.get('window_row_count', 0),
                    'source_file': mmwave_sched_metrics.get('source_file', ''),
                }
                result['global_metrics']['mmwave_sched_mapped_ues'] = len(mmwave_by_imsi)

        if cu_up_metrics:
            result['global_metrics']['cu_up_latest_timestamp'] = cu_up_metrics.get('latest_timestamp', 0)
            result['global_metrics']['cu_up_row_count'] = cu_up_metrics.get('row_count', 0)

        if pdcp_file is None:
            pdcp_file = self.input_dir / "DlPdcpStats.txt"
        if rlc_file is None:
            rlc_file = self.input_dir / "DlRlcStats.txt"
        result.setdefault('source_files', {})
        result['source_files'].update({
            'pdcp': str(pdcp_file),
            'mac': str(self.input_dir / "DlMacStats.txt"),
            'rlc': str(rlc_file),
        })
        if cu_up_metrics:
            result['source_files']['cu_up'] = cu_up_metrics.get('source_files', [])

            for imsi, data in cu_up_metrics.get('per_ue', {}).items():
                if imsi in result['ue_metrics']:
                    result['ue_metrics'][imsi]['cu_up_throughput_kbps'] = data.get('throughput_kbps', 0)
                    result['ue_metrics'][imsi]['cu_up_pdcp_latency_ms'] = data.get('pdcp_latency_ms', 0)
                else:
                    result['ue_metrics'][imsi] = {
                        'device_type': self.get_device_type(imsi),
                        'cell_id': 0,
                        'latency_us': 0,
                        'latency_avg_us': 0,
                        'latency_min_us': 0,
                        'latency_max_us': 0,
                        'jitter_us': 0,
                        'pdu_size_avg': 0,
                        'tx_bytes': 0,
                        'rx_bytes': 0,
                        'tx_pdus': 0,
                        'rx_pdus': 0,
                        'throughput_kbps': data.get('throughput_kbps', 0),
                        'tx_throughput_kbps': 0,
                        'rx_throughput_kbps': data.get('throughput_kbps', 0),
                        'total_pdcp_throughput_kbps': data.get('throughput_kbps', 0),
                        'packet_count': 0,
                        'has_latency_samples': False,
                        'is_critical': False,
                        'cu_up_throughput_kbps': data.get('throughput_kbps', 0),
                        'cu_up_pdcp_latency_ms': data.get('pdcp_latency_ms', 0),
                        'throughput_source': 'cu_up',
                    }

        if mmwave_sched_metrics:
            result.setdefault('source_files', {})
            result['source_files']['mmwave_sched'] = mmwave_sched_metrics.get('source_file', '')
        
        return result
    
    def write_metrics(self, metrics, filepath):
        """Write metrics to JSON file"""
        try:
            tmp_path = f"{filepath}.tmp"
            with open(tmp_path, 'w') as f:
                json.dump(metrics, f, indent=2)
            os.replace(tmp_path, filepath)
        except Exception as e:
            print(f"[CSV_METRICS] Error writing metrics to {filepath}: {e}")

    def clear_runtime_outputs(self):
        """Remove stale runtime JSON outputs when strict real-only data is unavailable."""
        for path in (self.output_file, self.extended_output_file):
            try:
                if path and os.path.exists(path):
                    os.unlink(path)
            except Exception as e:
                print(f"[CSV_METRICS] Error clearing stale runtime output {path}: {e}")
    
    def write_standard_metrics(self, extended_metrics):
        """Write standard metrics for xApps consumption"""
        result = {
            'timestamp': extended_metrics.get('timestamp', int(time.time() * 1000)),
            'cells': {
                'aggregated': {
                    'ues': {},
                    'cell_average_latency_us': extended_metrics.get('global_metrics', {}).get('global_avg_latency_us', 0),
                    'worst_latency_us': extended_metrics.get('global_metrics', {}).get('global_worst_latency_us', 0),
                    'latency_p95_us': extended_metrics.get('global_metrics', {}).get('latency_p95_us', 0),
                    'active_ues': extended_metrics.get('global_metrics', {}).get('total_active_ues', 0),
                    'time_window_s': extended_metrics.get('sim_time_range', {}).get('window_s', 30)
                }
            },
            'global_worst_latency_us': extended_metrics.get('global_metrics', {}).get('global_worst_latency_us', 0),
            'latency_p95_us': extended_metrics.get('global_metrics', {}).get('latency_p95_us', 0),
            'active_cameras': extended_metrics.get('active_cameras', 0),
            'critical_cameras': extended_metrics.get('critical_cameras', 0),
            'active_sensors': extended_metrics.get('global_metrics', {}).get('total_active_sensors', 0),
            'active_vehicles': extended_metrics.get('global_metrics', {}).get('total_active_vehicles', 0),
        }
        
        for imsi, ue_data in extended_metrics.get('ue_metrics', {}).items():
            result['cells']['aggregated']['ues'][imsi] = {
                'latency_us': ue_data.get('latency_us', 0),
                'throughput_kbps': ue_data.get('throughput_kbps', 0),
                'packets': ue_data.get('packet_count', 0),
                'type': ue_data.get('device_type', 'unknown')
            }
        
        return result

    def _snapshot_has_proxy_latency(self, extended_metrics):
        gm = (extended_metrics or {}).get('global_metrics', {}) or {}
        try:
            if int(gm.get('proxy_latency_sample_count', 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass

        for ue_data in ((extended_metrics or {}).get('ue_metrics', {}) or {}).values():
            if bool((ue_data or {}).get('latency_is_proxy')):
                return True
        return False

    def _retain_real_only_snapshot(self, extended_metrics):
        """Remove unobserved/proxy UEs from a strict real-PDCP snapshot.

        A strict run must never publish an estimated latency as if it were a
        radio measurement.  An IMSI with real PDCP TX/RX counters but no RX
        latency sample is still retained as a real observation: its receive
        throughput remains zero and its latency remains unsampled.  This is
        important for camera SLA failures, where TX without RX is the actual
        network outcome, not a proxy estimate.
        """
        if not isinstance(extended_metrics, dict):
            return extended_metrics
        ue_metrics = extended_metrics.get('ue_metrics') or {}
        original_proxy_count = int(
            (extended_metrics.get('global_metrics') or {}).get('proxy_latency_sample_count', 0) or 0
        )
        real_ues = {}
        for imsi, data in ue_metrics.items():
            if not isinstance(data, dict) or bool(data.get('latency_is_proxy')):
                continue
            has_latency = bool(data.get('has_latency_samples'))
            has_pdcp_observation = (
                int(data.get('tx_pdus', 0) or 0) > 0
                or int(data.get('rx_pdus', 0) or 0) > 0
                or float(data.get('tx_bytes', 0) or 0.0) > 0.0
                or float(data.get('rx_bytes', 0) or 0.0) > 0.0
            )
            if has_latency or has_pdcp_observation:
                if not has_latency:
                    data['observation_source'] = 'pdcp_real_no_rx_latency'
                real_ues[imsi] = data
        excluded = max(0, len(ue_metrics) - len(real_ues))
        if excluded == 0:
            return extended_metrics

        extended_metrics['ue_metrics'] = real_ues
        gm = extended_metrics.setdefault('global_metrics', {})
        latencies = [float(data.get('latency_us', 0) or 0) for data in real_ues.values() if float(data.get('latency_us', 0) or 0) > 0]
        jitters = [float(data.get('jitter_us', 0) or 0) for data in real_ues.values() if float(data.get('latency_us', 0) or 0) > 0]
        cameras = [data for data in real_ues.values() if data.get('device_type') == 'camera']
        sensors = [data for data in real_ues.values() if data.get('device_type') == 'sensor']
        vehicles = [data for data in real_ues.values() if data.get('device_type') == 'vehicle']
        critical = sum(1 for data in real_ues.values() if bool(data.get('is_critical')))
        real_source_counts = defaultdict(int)
        no_latency_sample_count = 0
        for data in real_ues.values():
            source = str(data.get('latency_source') or '')
            if source:
                real_source_counts[source] += 1
            if not bool(data.get('has_latency_samples')):
                no_latency_sample_count += 1
        gm.update({
            'total_active_ues': len(real_ues),
            'total_active_cameras': len(cameras),
            'total_active_sensors': len(sensors),
            'total_active_vehicles': len(vehicles),
            'total_critical_ues': critical,
            'total_tx_bytes': sum(int(data.get('tx_bytes', 0) or 0) for data in real_ues.values()),
            'total_rx_bytes': sum(int(data.get('rx_bytes', 0) or 0) for data in real_ues.values()),
            'total_tx_pdus': sum(int(data.get('tx_pdus', 0) or 0) for data in real_ues.values()),
            'total_rx_pdus': sum(int(data.get('rx_pdus', 0) or 0) for data in real_ues.values()),
            'throughput_kbps': sum(float(data.get('throughput_kbps', 0) or 0) for data in real_ues.values()),
            'global_avg_latency_us': sum(latencies) / len(latencies) if latencies else 0,
            'global_min_latency_us': min(latencies) if latencies else 0,
            'global_max_latency_us': max(latencies) if latencies else 0,
            'global_worst_latency_us': max(latencies) if latencies else 0,
            'global_worst_camera_latency_us': max((float(data.get('latency_us', 0) or 0) for data in cameras), default=0),
            'global_jitter_us': sum(jitters) / len(jitters) if jitters else 0,
            'latency_p5_us': self.percentile_5(latencies) if latencies else 0,
            'latency_p95_us': self.percentile_95(latencies) if latencies else 0,
            'latency_min_nonzero_us': self.min_nonzero(latencies),
            'latency_median_us': self.median(latencies) if latencies else 0,
            'ues_with_latency_samples': len(latencies),
            'ues_without_latency_samples': no_latency_sample_count,
            'collector_mode': 'pdcp_real',
            'latency_sample_source_counts': dict(real_source_counts),
            'real_latency_sample_count': len(latencies),
            # If the snapshot contained only proxy/mock rows, keep the
            # explicit signal so strict mode skips it.  When at least one
            # genuine PDCP observation remains, excluded proxy rows are not
            # published and the resulting snapshot is proxy-free.
            'proxy_latency_sample_count': original_proxy_count if not real_ues else 0,
            'proxy_expanded_sensors': False,
            'proxy_virtual_vehicles': False,
            'excluded_proxy_ue_count': excluded,
            'real_pdcp_observations_without_latency': no_latency_sample_count,
        })
        extended_metrics['active_cameras'] = len(cameras)
        extended_metrics['critical_cameras'] = sum(1 for data in cameras if bool(data.get('is_critical')))
        extended_metrics['critical_ues'] = critical
        return extended_metrics
    
    def run(self, once=False):
        """Main loop"""
        pdcp_file = self._resolve_trace_file("pdcp")
        mac_file = self.input_dir / "DlMacStats.txt"
        rlc_file = self._resolve_trace_file("rlc")
        mmwave_sched_file = self.input_dir / "EnbSchedAllocTraces.txt"
        
        print(f"[CSV_METRICS] Starting Extended Metrics Collector")
        print(f"[CSV_METRICS] PDCP: {pdcp_file}")
        print(f"[CSV_METRICS] MAC:  {mac_file}")
        print(f"[CSV_METRICS] RLC:  {rlc_file}")
        print(f"[CSV_METRICS] mmWave Sched: {mmwave_sched_file}")
        print(f"[CSV_METRICS] Output: {self.output_file}")
        print(f"[CSV_METRICS] Extended: {self.extended_output_file}")
        
        iteration = 0
        while self.running:
            iteration += 1

            try:
                current_pdcp_file = self._resolve_trace_file("pdcp")
                current_rlc_file = self._resolve_trace_file("rlc")
                if current_pdcp_file != pdcp_file:
                    print(f"[CSV_METRICS] PDCP source switched: {pdcp_file} -> {current_pdcp_file}")
                    pdcp_file = current_pdcp_file
                    self._warned_missing_pdcp = False
                if current_rlc_file != rlc_file:
                    print(f"[CSV_METRICS] RLC source switched: {rlc_file} -> {current_rlc_file}")
                    rlc_file = current_rlc_file

                cu_up_files = sorted(self.input_dir.glob("cu-up-cell-*.txt"))
                if iteration == 1 or iteration % 60 == 0:
                    print(
                        f"[CSV_METRICS] CU-UP sources: "
                        f"{', '.join(str(p) for p in cu_up_files) if cu_up_files else 'none'}"
                    )

                pdcp_metrics = self.process_pdcp_stats(pdcp_file)
                fidelity = os.environ.get("GREENRAN_NATIVE_TRACE_PROFILE") == "v9_fidelity"
                mac_metrics = [] if fidelity else self.process_mac_stats(mac_file)
                rlc_metrics = [] if fidelity else self.process_rlc_stats(rlc_file)
                mmwave_sched_metrics = (
                    {"native_aggregate": self.process_native_aggregate()}
                    if fidelity else self.process_mmwave_sched_stats(mmwave_sched_file)
                )
                cu_up_metrics = self.process_cu_up_stats(cu_up_files)
                trace_status = {
                    'pdcp': self._trace_status(pdcp_file, pdcp_metrics),
                    'rlc': self._trace_status(rlc_file, rlc_metrics),
                    'mac': self._trace_status(mac_file, mac_metrics),
                }
                has_fallback_data = bool(mac_metrics or rlc_metrics or (cu_up_metrics and cu_up_metrics.get('per_ue')))
                pdcp_is_stale = bool(pdcp_metrics and trace_status['pdcp'].get('stale') and has_fallback_data)

                if self.require_real_pdcp and (not pdcp_metrics or pdcp_is_stale):
                    if iteration == 1 or iteration % 5 == 0:
                        reason = "stale" if pdcp_is_stale else "missing"
                        print(
                            f"[CSV_METRICS] Strict real-only mode: skipping snapshot because PDCP is {reason}."
                        )
                    self.clear_runtime_outputs()
                    if once:
                        break
                    time.sleep(self.poll_interval)
                    continue
                
                if pdcp_metrics or has_fallback_data:
                    if not pdcp_metrics and not self._warned_missing_pdcp:
                        print("[CSV_METRICS] Nenhum trace PDCP real disponivel; usando fallback com CU-UP/MAC/RLC.")
                        self._warned_missing_pdcp = True
                    if pdcp_is_stale:
                        if iteration % 5 == 0:
                            print(
                                f"[CSV_METRICS] PDCP stale: age={trace_status['pdcp'].get('age_s', 0):.1f}s; "
                                "using MAC/RLC/CU-UP fallback for this snapshot."
                            )
                        extended = self.aggregate_metrics_without_pdcp(
                            mac_metrics,
                            rlc_metrics=rlc_metrics,
                            cu_up_metrics=cu_up_metrics,
                            mmwave_sched_metrics=mmwave_sched_metrics,
                        )
                        self._attach_trace_status(extended, trace_status, collector_mode='pdcp_stale')
                    else:
                        extended = self.aggregate_metrics(
                            pdcp_metrics,
                            mac_metrics,
                            rlc_metrics=rlc_metrics,
                            cu_up_metrics=cu_up_metrics,
                            mmwave_sched_metrics=mmwave_sched_metrics,
                            pdcp_file=pdcp_file,
                            rlc_file=rlc_file,
                        )
                        self._attach_trace_status(extended, trace_status)

                    if fidelity:
                        native_aggregate = mmwave_sched_metrics.get("native_aggregate", {})
                        extended["native_control_observations"] = native_aggregate
                        gm = extended.setdefault("global_metrics", {})
                        gm["native_evidence_version"] = str(
                            os.environ.get("GREENRAN_NATIVE_EVIDENCE_VERSION", "v4")
                        ).strip() or "v4"
                        gm["native_observed_cells"] = sorted(native_aggregate)
                        gm["native_allocation_observed"] = bool(
                            native_aggregate and all(
                                int(item.get("active_dl_symbol_capacity", 0) or 0) > 0
                                for item in native_aggregate.values()
                            )
                        )

                    if self.require_real_pdcp and self._snapshot_has_proxy_latency(extended):
                        extended = self._retain_real_only_snapshot(extended)
                        if iteration == 1 or iteration % 5 == 0:
                            excluded = extended.get('global_metrics', {}).get('excluded_proxy_ue_count', 0)
                            print(
                                f"[CSV_METRICS] Strict real-only mode: excluding {excluded} UEs "
                                "without a real PDCP latency sample; no proxy is published."
                            )

                    if self.require_real_pdcp and self._snapshot_has_proxy_latency(extended):
                        if iteration == 1 or iteration % 5 == 0:
                            proxy_count = (
                                extended.get('global_metrics', {}).get('proxy_latency_sample_count', 0)
                            )
                            print(
                                "[CSV_METRICS] Strict real-only mode: skipping snapshot because "
                                f"proxy latency is still present (proxy_latency_sample_count={proxy_count})."
                            )
                        self.clear_runtime_outputs()
                        if once:
                            break
                        time.sleep(self.poll_interval)
                        continue
                    
                    self.write_metrics(extended, self.extended_output_file)
                    self.export_device_roles_snapshot(extended)
                    self.export_app2_metrics(extended)
                    self.export_app3_metrics(extended)
                    
                    standard = self.write_standard_metrics(extended)
                    self.write_metrics(standard, self.output_file)
                    
                    gm = extended.get('global_metrics', {})
                    if iteration % 5 == 0:
                        print(f"[CSV_METRICS] iter={iteration} "
                              f"lat={gm.get('global_worst_latency_us', 0)/1000:.1f}ms "
                              f"avg={gm.get('global_avg_latency_us', 0)/1000:.1f}ms "
                              f"cams={extended.get('active_cameras', 0)} "
                              f"sensors={gm.get('total_active_sensors', 0)} "
                              f"critical={extended.get('critical_cameras', 0)} "
                              f"ues={gm.get('total_active_ues', 0)} "
                              f"tp={gm.get('throughput_kbps', 0):.0f}kbps")
                else:
                    if iteration % 20 == 0:
                        print(f"[CSV_METRICS] Waiting for data... (iter {iteration})")
            except Exception as e:
                print(f"[CSV_METRICS] Loop failure on iter {iteration}: {e}")
                traceback.print_exc()
            
            if once:
                break
            time.sleep(self.poll_interval)
        
        print("[CSV_METRICS] Shutdown")


def main():
    parser = argparse.ArgumentParser(description='GreenRAN Extended Metrics Collector')
    parser.add_argument('--input-dir', '-i', default=DEFAULT_INPUT_DIR)
    parser.add_argument('--output', '-o', default=DEFAULT_OUTPUT_FILE)
    parser.add_argument('--extended-output', '-e', default=DEFAULT_EXTENDED_OUTPUT_FILE)
    parser.add_argument('--poll-interval', '-p', type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument('--once', action='store_true', help='Process one final snapshot and exit.')
    args = parser.parse_args()
    
    collector = ExtendedMetricsCollector(
        args.input_dir,
        args.output,
        args.extended_output,
        args.poll_interval
    )
    collector.run(once=args.once)


if __name__ == '__main__':
    main()
