#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp Dashboard (Flask)
========================================

Dashboard web para monitoramento em tempo real do rApp-ResourceOptimizer.

Rotas:
    /           - Dashboard principal
    /metrics    - Métricas em tempo real (JSON)
    /decisions  - Histórico de decisões
    /pattern    - Análise de padrões
    /xapps      - Status dos xApps
    /api/*      - API para dados

Uso:
    python3 rapp_dashboard.py [--port PORT] [--host HOST]
"""

import os
import sys
import json
import time
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from greenran_paths import (
    STATE_DIR,
    TEMPLATES_DIR,
    EXTENDED_METRICS_JSON_PATH,
    XAPP_HEALTH_PATH,
    RAPP_POLICIES_DIR,
    RAPP_LOG_PATH,
    XAPP_INTENTS_DIR,
    ARTICLE00_SCENARIO_CONTROL_PATH,
    as_str,
    load_fixed_scenario_config,
    get_fixed_total_ues,
    get_fixed_active_cameras,
    get_fixed_max_vehicles,
    get_fixed_vehicle_base_imsi,
)
from greenran_runtime import load_runtime_config

from rapp_data_lake import DataLake
from rapp_pattern_engine import PatternRecognition
from rapp_alerts import AlertManager
from rapp_policy_consumer import load_current_policies

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from apps.app3_veicular.backend.services import VehicleStateStore  # noqa: E402

app = Flask(__name__, template_folder=as_str(TEMPLATES_DIR))
RUNTIME_CONFIG = load_runtime_config()

DATA_LAKE = DataLake()
PATTERN_ENGINE = PatternRecognition(DATA_LAKE)
ALERT_MANAGER = AlertManager()
APP3_STORE = VehicleStateStore(STATE_DIR)

METRICS_FILE = as_str(EXTENDED_METRICS_JSON_PATH)
XAPP_HEALTH_FILE = as_str(XAPP_HEALTH_PATH)
POLICY_STATUS_FILE = as_str(RAPP_POLICIES_DIR / "policy_status.json")
APP1_MONITORING_FILE = as_str(STATE_DIR / "app1_vigilancia" / "monitoring_snapshot.json")
APP2_MONITORING_FILE = as_str(STATE_DIR / "app2_monitoramento" / "monitoring_snapshot.json")
APP3_MONITORING_FILE = as_str(STATE_DIR / "app3_veicular" / "monitoring_snapshot.json")
DEVICE_ROLES_FILE = as_str(STATE_DIR / "xapp_metrics" / "device_roles.json")
CONFLICT_LEARNED_REPORT_FILE = as_str(STATE_DIR / "greenran_conflict_report.json")
CONFLICT_LEARNED_ADJ_FILE = as_str(STATE_DIR / "greenran_conflict_adjacency.json")
SCENARIO_CONTROL_FILE = as_str(ARTICLE00_SCENARIO_CONTROL_PATH)

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
VEHICLE_LATENCY_WARNING_MS = 50.0
VEHICLE_LATENCY_CRITICAL_MS = 100.0
VEHICLE_PACKET_LOSS_WARNING_PERCENT = 2.0
VEHICLE_PACKET_LOSS_CRITICAL_PERCENT = 5.0




def get_fixed_scenario_metadata():
    """Resume o baseline canonico do manifesto de cenario fixo."""
    payload = load_fixed_scenario_config()
    scenario_id = str(payload.get('scenario_id', 'unknown') or 'unknown')
    total_ues = int(get_fixed_total_ues())
    active_cameras = int(get_fixed_active_cameras())
    max_vehicles = int(get_fixed_max_vehicles())
    base_imsi = int(get_fixed_vehicle_base_imsi())
    background_ues = max(0, total_ues - active_cameras)
    return {
        'scenario_id': scenario_id,
        'total_ues': total_ues,
        'active_cameras': active_cameras,
        'background_ues': background_ues,
        'max_vehicles': max_vehicles,
        'vehicle_base_imsi': base_imsi,
        'vehicle_imsi_end': base_imsi + max(max_vehicles - 1, 0),
    }

def get_current_metrics():
    """Obtém métricas atuais."""
    if os.path.exists(METRICS_FILE):
        try:
            with open(METRICS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return None


def get_xapp_status():
    """Obtém status dos xApps."""
    if os.path.exists(XAPP_HEALTH_FILE):
        try:
            with open(XAPP_HEALTH_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}


def get_policy_status():
    """Obtém status das políticas."""
    status = {}
    if os.path.exists(POLICY_STATUS_FILE):
        try:
            with open(POLICY_STATUS_FILE, 'r') as f:
                status = json.load(f)
        except Exception:
            status = {}

    current = load_current_policies()
    status["current_energy_policy"] = current.get("energy_policy", {})
    status["current_slice_policy"] = current.get("slice_policy", {})
    status["armd"] = current.get("armd", {})
    return status


def _safe_read_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return default


def get_app1_monitoring():
    """Obtém snapshot de monitoramento da App1-Vigilancia."""
    if os.path.exists(APP1_MONITORING_FILE):
        try:
            with open(APP1_MONITORING_FILE, 'r') as f:
                snapshot = json.load(f)
                if isinstance(snapshot, dict) and 'camera_sla' not in snapshot:
                    snapshot['camera_sla'] = {}
                return snapshot
        except Exception:
            pass
    return {}


def evaluate_app2_sla(snapshot):
    """Avalia a margem operacional da App2 com os mesmos limiares do rApp."""
    sensors = snapshot.get('sensors', {}) if isinstance(snapshot, dict) else {}
    readings = snapshot.get('readings', {}) if isinstance(snapshot, dict) else {}
    network = snapshot.get('network', {}) if isinstance(snapshot, dict) else {}

    total_sensors = int(sensors.get('total', 0) or 0)
    connected_sensors = int(sensors.get('connected', 0) or 0)
    error_sensors = int(sensors.get('error', 0) or 0)
    low_battery_sensors = int(sensors.get('low_battery', 0) or 0)

    connected_ratio = (connected_sensors / total_sensors) if total_sensors > 0 else 0.0
    error_ratio = (error_sensors / total_sensors) if total_sensors > 0 else 0.0
    packet_loss = float(network.get('packet_loss_percent', 0.0) or 0.0)
    delivery_success = float(network.get('delivery_success_percent', 100.0) or 0.0)
    avg_latency_ms = float(network.get('avg_latency_ms', 0.0) or 0.0)
    avg_battery_percent = float(readings.get('avg_battery_percent', 0.0) or 0.0)

    observed = {
        'total_sensors': total_sensors,
        'connected_sensors': connected_sensors,
        'connected_ratio': round(connected_ratio, 4),
        'error_sensors': error_sensors,
        'error_ratio': round(error_ratio, 4),
        'low_battery_sensors': low_battery_sensors,
        'packet_loss_percent': round(packet_loss, 2),
        'delivery_success_percent': round(delivery_success, 2),
        'avg_latency_ms': round(avg_latency_ms, 2),
        'avg_battery_percent': round(avg_battery_percent, 2),
    }

    if total_sensors <= 0:
        return {
            'status': 'INATIVA',
            'runtime_status': 'idle',
            'proposal_status': 'idle',
            'reason': 'Nenhum sensor ativo no snapshot.',
            'observed': observed,
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
        return {
            'status': 'CRÍTICA',
            'runtime_status': 'blocked',
            'proposal_status': 'violation',
            'reason': critical_reasons[0],
            'observed': observed,
        }
    if warning_reasons:
        return {
            'status': 'GUARDA',
            'runtime_status': 'warning',
            'proposal_status': 'warning',
            'reason': warning_reasons[0],
            'observed': observed,
        }
    return {
        'status': 'PROTEGIDA',
        'runtime_status': 'ok',
        'proposal_status': 'ok',
        'reason': 'SLA mMTC atendido para conectividade, entrega, latência e bateria.',
        'observed': observed,
    }


def get_app2_monitoring():
    """Obtém snapshot da App2-Monitoramento, preferindo o Data Lake."""
    snapshot = DATA_LAKE.get_latest_app2_snapshot()
    if snapshot:
        if isinstance(snapshot, dict):
            snapshot['app2_sla'] = evaluate_app2_sla(snapshot)
        return snapshot
    if os.path.exists(APP2_MONITORING_FILE):
        try:
            with open(APP2_MONITORING_FILE, 'r') as f:
                snapshot = json.load(f)
                if isinstance(snapshot, dict):
                    snapshot['app2_sla'] = evaluate_app2_sla(snapshot)
                return snapshot
        except Exception:
            pass
    return {}


def evaluate_vehicle_sla(snapshot):
    """Avalia a margem operacional veicular com os mesmos limiares do App3/rApp."""
    summary = snapshot.get('vehicles', {}) if isinstance(snapshot, dict) else {}
    total_vehicles = int(summary.get('total_vehicles', 0) or 0)
    observed = {
        'total_vehicles': total_vehicles,
        'ego_present': bool(summary.get('ego_present', False)),
        'high_risk_vehicles': int(summary.get('high_risk_vehicles', 0) or 0),
        'medium_risk_vehicles': int(summary.get('medium_risk_vehicles', 0) or 0),
        'degraded_autonomy_vehicles': int(summary.get('degraded_autonomy_vehicles', 0) or 0),
        'max_latency_ms': round(float(summary.get('max_latency_ms', 0.0) or 0.0), 2),
        'max_packet_loss_percent': round(float(summary.get('max_packet_loss_percent', 0.0) or 0.0), 2),
        'max_speed_mps': round(float(summary.get('max_speed_mps', 0.0) or 0.0), 2),
    }

    if total_vehicles <= 0:
        return {
            'status': 'INATIVA',
            'runtime_status': 'idle',
            'proposal_status': 'idle',
            'reason': 'Nenhum veículo ativo no snapshot.',
            'observed': observed,
        }

    high_risk = observed['high_risk_vehicles']
    medium_risk = observed['medium_risk_vehicles']
    degraded = observed['degraded_autonomy_vehicles']
    max_latency = observed['max_latency_ms']
    max_packet_loss = observed['max_packet_loss_percent']

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
            'status': 'CRÍTICA',
            'runtime_status': 'blocked',
            'proposal_status': 'violation',
            'reason': critical_reasons[0],
            'observed': observed,
        }
    if warning_reasons:
        return {
            'status': 'GUARDA',
            'runtime_status': 'warning',
            'proposal_status': 'warning',
            'reason': warning_reasons[0],
            'observed': observed,
        }
    return {
        'status': 'PROTEGIDA',
        'runtime_status': 'ok',
        'proposal_status': 'ok',
        'reason': 'SLA veicular atendido para risco, autonomia e rede.',
        'observed': observed,
    }


def get_app3_monitoring():
    """Obtém snapshot da App3-Veicular recalculando o estado vivo dos veículos."""
    try:
        snapshot = APP3_STORE.refresh_snapshot()
        if isinstance(snapshot, dict):
            snapshot['vehicle_sla'] = evaluate_vehicle_sla(snapshot)
        return snapshot
    except Exception:
        if os.path.exists(APP3_MONITORING_FILE):
            try:
                with open(APP3_MONITORING_FILE, 'r') as f:
                    snapshot = json.load(f)
                    if isinstance(snapshot, dict):
                        snapshot['vehicle_sla'] = evaluate_vehicle_sla(snapshot)
                    return snapshot
            except Exception:
                pass
    return {}


def get_service_sla_status():
    """Consolida os SLAs de App1/câmeras, App3/veículos e App2/sensores."""
    app1_monitoring = get_app1_monitoring()
    app2_monitoring = get_app2_monitoring()
    app3_monitoring = get_app3_monitoring()
    camera_protection = get_camera_protection()

    app1_sla = (app1_monitoring.get('camera_sla', {}) if isinstance(app1_monitoring, dict) else {}) or {}
    app1_observed = app1_sla.get('observed', {}) or {}
    app1_network = (app1_monitoring.get('network', {}) if isinstance(app1_monitoring, dict) else {}) or {}
    app1_runtime_status = app1_sla.get('runtime_status', 'idle')
    if app1_runtime_status == 'blocked':
        app1_status = 'CRÍTICA'
    elif app1_runtime_status == 'warning':
        app1_status = 'GUARDA'
    elif app1_runtime_status == 'ok':
        app1_status = 'PROTEGIDA'
    else:
        app1_status = camera_protection.get('status', 'INATIVA')
    camera_service = {
        'status': app1_status,
        'reason': app1_sla.get('reason') or camera_protection.get('reason', 'Sem dados'),
        'proposal_status': app1_sla.get('proposal_status', 'idle'),
        'runtime_status': app1_runtime_status,
        'active': int(app1_network.get('active_cameras', app1_sla.get('active_cameras', camera_protection.get('active_cameras', 0))) or 0),
        'throughput_mbps': round(float(app1_observed.get('min_throughput_mbps', app1_network.get('min_camera_throughput_mbps', camera_protection.get('min_throughput_mbps', 0.0))) or 0.0), 1),
        'latency_ms': round(float(app1_observed.get('max_latency_ms', app1_network.get('max_camera_latency_ms', float(camera_protection.get('max_latency', 0.0) or 0.0) / 1000.0)) or 0.0), 1),
    }

    app2_sla = (app2_monitoring.get('app2_sla', {}) if isinstance(app2_monitoring, dict) else {}) or {}
    app2_observed = app2_sla.get('observed', {}) or {}
    app2_service = {
        'status': app2_sla.get('status', 'INATIVA'),
        'reason': app2_sla.get('reason', 'Sem dados'),
        'proposal_status': app2_sla.get('proposal_status', 'idle'),
        'runtime_status': app2_sla.get('runtime_status', 'idle'),
        'active': app2_observed.get('total_sensors', 0),
        'connected': app2_observed.get('connected_sensors', 0),
        'latency_ms': round(float(app2_observed.get('avg_latency_ms', 0.0) or 0.0), 1),
        'packet_loss_percent': round(float(app2_observed.get('packet_loss_percent', 0.0) or 0.0), 1),
    }

    app3_sla = (app3_monitoring.get('vehicle_sla', {}) if isinstance(app3_monitoring, dict) else {}) or {}
    app3_observed = app3_sla.get('observed', {}) or {}
    app3_service = {
        'status': app3_sla.get('status', 'INATIVA'),
        'reason': app3_sla.get('reason', 'Sem dados'),
        'proposal_status': app3_sla.get('proposal_status', 'idle'),
        'runtime_status': app3_sla.get('runtime_status', 'idle'),
        'active': app3_observed.get('total_vehicles', 0),
        'high_risk': app3_observed.get('high_risk_vehicles', 0),
        'degraded': app3_observed.get('degraded_autonomy_vehicles', 0),
        'latency_ms': round(float(app3_observed.get('max_latency_ms', 0.0) or 0.0), 1),
        'packet_loss_percent': round(float(app3_observed.get('max_packet_loss_percent', 0.0) or 0.0), 1),
    }

    return {
        'camera': camera_service,
        'app3': app3_service,
        'app2': app2_service,
    }


def get_scenario_actor_counts():
    """Obtém contagens lógicas do cenário atual a partir do metadata de papéis."""
    counts = {
        'cameras': 0,
        'pedestrians': 0,
        'vehicles': 0,
        'stationary_sensors': 0,
        'total_roles': 0,
    }
    payload = _safe_read_json(DEVICE_ROLES_FILE, {})
    roles = payload.get('roles', {}) if isinstance(payload, dict) else {}
    if not isinstance(roles, dict):
        return counts

    for role in roles.values():
        if not isinstance(role, dict):
            continue
        counts['total_roles'] += 1
        device_type = str(role.get('device_type', '') or '').lower()
        mobility = str(role.get('mobility_profile', '') or '').lower()
        if device_type == 'camera':
            counts['cameras'] += 1
        elif mobility == 'pedestrian':
            counts['pedestrians'] += 1
        elif mobility == 'vehicle':
            counts['vehicles'] += 1
        elif mobility == 'stationary':
            counts['stationary_sensors'] += 1
    return counts


def get_learned_conflict_assets():
    """Obtém o relatório e a adjacência aprendida do pipeline de conflitos."""
    report = _safe_read_json(CONFLICT_LEARNED_REPORT_FILE, {})
    adjacency = _safe_read_json(CONFLICT_LEARNED_ADJ_FILE, {})
    return report, adjacency


def get_decision_stats():
    """Obtém estatísticas de decisões."""
    return DATA_LAKE.get_decision_stats(24)


def get_drl_stats():
    """Extrai estatísticas do DRL (SBiLSTM + A3C) do log."""
    import re
    from datetime import datetime
    
    drl_predictions = []
    log_path = as_str(RAPP_LOG_PATH)
    
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f:
                for line in f:
                    if 'rApp DRL' in line:
                        match = re.search(
                            r'\[rApp DRL\] CVaR predicted: ([\d.]+)ms, Decision: (\w+), Power: (\w+)',
                            line
                        )
                        if match:
                            # Extrair timestamp da linha
                            ts_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
                            timestamp = ts_match.group(1) if ts_match else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            
                            drl_predictions.append({
                                'timestamp': timestamp,
                                'cvar': float(match.group(1)),
                                'decision': match.group(2),
                                'power': match.group(3)
                            })
        except Exception as e:
            print(f"[Dashboard] DRL parse error: {e}")
    
    # Calcular estatísticas
    total = len(drl_predictions)
    allowed = sum(1 for p in drl_predictions if p['decision'] == 'ALLOWED')
    blocked = sum(1 for p in drl_predictions if p['decision'] == 'BLOCKED')
    conditional = sum(1 for p in drl_predictions if p['decision'] == 'CONDITIONAL')
    
    #Últimas previsões
    recent = drl_predictions[-30:] if drl_predictions else []

    runtime_status = {
        'loaded': False,
        'active': bool(drl_predictions),
        'paused_by_priority': False,
        'priority_source': '',
        'reason': '',
    }

    try:
        from greenran_paths import DRL_VENV_SITE_PACKAGES
        if os.path.exists(as_str(DRL_VENV_SITE_PACKAGES)):
            runtime_status['loaded'] = True
    except Exception:
        runtime_status['loaded'] = False

    if not drl_predictions:
        service_slas = get_service_sla_status()
        camera = service_slas.get('camera', {}) or {}
        app3 = service_slas.get('app3', {}) or {}
        app2 = service_slas.get('app2', {}) or {}
        latest_reason = ''
        try:
            row = DATA_LAKE.conn.execute(
                "SELECT reason FROM decisions_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            latest_reason = row[0] if row and row[0] else ''
        except Exception:
            latest_reason = ''

        if camera.get('runtime_status') in {'warning', 'blocked'}:
            runtime_status.update({
                'paused_by_priority': True,
                'priority_source': 'camera',
                'reason': latest_reason or camera.get('reason', 'Prioridade de câmera ativa'),
            })
        elif app3.get('runtime_status') in {'warning', 'blocked'}:
            runtime_status.update({
                'paused_by_priority': True,
                'priority_source': 'vehicle',
                'reason': app3.get('reason', 'Prioridade veicular ativa'),
            })
        elif app2.get('runtime_status') in {'warning', 'blocked'}:
            runtime_status.update({
                'paused_by_priority': True,
                'priority_source': 'app2',
                'reason': app2.get('reason', 'Prioridade de sensores ativa'),
            })
        elif runtime_status['loaded']:
            runtime_status['reason'] = 'DRL carregada, mas ainda sem previsões registradas no log.'
        else:
            runtime_status['reason'] = 'DRL não carregada no runtime atual.'
    
    return {
        'predictions': drl_predictions[-100:],  # últimas 100
        'recent': recent,
        'runtime_status': runtime_status,
        'stats': {
            'total': total,
            'allowed': allowed,
            'blocked': blocked,
            'conditional': conditional,
            'allowed_pct': (allowed / total * 100) if total > 0 else 0,
            'blocked_pct': (blocked / total * 100) if total > 0 else 0,
            'conditional_pct': (conditional / total * 100) if total > 0 else 0,
            'avg_cvar': sum(p['cvar'] for p in drl_predictions) / total if total > 0 else 0
        }
    }


def get_period_stats():
    """
    Obtém estatísticas por período de simulação.
    
    Período 1: 0-300s (tráfego leve)
    Período 2: 300-600s (tráfego pesado)
    
    Returns:
        Dict com current_period, period1_stats, period2_stats
    """
    try:
        conn = DATA_LAKE.conn
        cursor = conn.cursor()
        
        # Obter última métrica para determinar período atual
        cursor.execute("SELECT sim_time_s FROM extended_metrics ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        
        if not row:
            return {
                'current_period': 1,
                'current_period_name': 'Inicializando...',
                'current_cvar': 0,
                'period1': {'allowed': 0, 'blocked': 0, 'conditional': 0},
                'period2': {'allowed': 0, 'blocked': 0, 'conditional': 0}
            }
        
        sim_time = row[0] if row[0] else 0
        
        # Determinar período atual
        if sim_time < 300:
            current_period = 1
            current_period_name = "PERÍODO 1 (0-5min) - Tráfego LEVE"
        else:
            current_period = 2
            current_period_name = "PERÍODO 2 (5-10min) - Tráfego PESADO"
        
        # Obter CVaR atual
        cursor.execute("SELECT cvar_per_ue_us FROM extended_metrics ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        current_cvar = (row[0] / 1000) if row and row[0] else 0
        
        # Estatísticas Período 1 (0-300s)
        cursor.execute("""
            SELECT decision, COUNT(*) as cnt
            FROM decisions_history 
            WHERE timestamp IN (
                SELECT timestamp FROM extended_metrics WHERE sim_time_s <= 300
            )
            GROUP BY decision
        """)
        period1 = {'allowed': 0, 'blocked': 0, 'conditional': 0}
        for row in cursor.fetchall():
            if row[0] == 'ALLOWED':
                period1['allowed'] = row[1]
            elif row[0] == 'BLOCKED':
                period1['blocked'] = row[1]
            elif row[0] == 'CONDITIONAL':
                period1['conditional'] = row[1]
        
        # Estatísticas Período 2 (300-600s)
        cursor.execute("""
            SELECT decision, COUNT(*) as cnt
            FROM decisions_history 
            WHERE timestamp IN (
                SELECT timestamp FROM extended_metrics WHERE sim_time_s > 300 AND sim_time_s <= 600
            )
            GROUP BY decision
        """)
        period2 = {'allowed': 0, 'blocked': 0, 'conditional': 0}
        for row in cursor.fetchall():
            if row[0] == 'ALLOWED':
                period2['allowed'] = row[1]
            elif row[0] == 'BLOCKED':
                period2['blocked'] = row[1]
            elif row[0] == 'CONDITIONAL':
                period2['conditional'] = row[1]
        
        return {
            'current_period': current_period,
            'current_period_name': current_period_name,
            'current_cvar': current_cvar,
            'period1': period1,
            'period2': period2
        }
        
    except Exception as e:
        print(f"Erro ao obter estatísticas de período: {e}")
        return {
            'current_period': 1,
            'current_period_name': 'Erro',
            'current_cvar': 0,
            'period1': {'allowed': 0, 'blocked': 0, 'conditional': 0},
            'period2': {'allowed': 0, 'blocked': 0, 'conditional': 0}
        }


def get_camera_protection():
    """
    Obtém status de proteção das câmeras.
    
    As câmeras têm prioridade máxima:
    - throughput < 25Mbps: violação direta
    - throughput 25-30Mbps: faixa de guarda
    - latência >= 80ms: violação direta
    
    Returns:
        Dict com status de proteção
    """
    try:
        metrics = get_current_metrics()
        if not metrics:
            return {
                'status': 'ATIVA',
                'active_cameras': 0,
                'max_latency': 0,
                'min_throughput_mbps': 0,
                'sla_compliant': True,
                'guard_active': False,
                'reason': 'Sem métricas atuais'
            }

        camera_entries = [
            ue for ue in (metrics.get('ue_metrics', {}) or {}).values()
            if ue.get('device_type') == 'camera'
        ]

        if not camera_entries:
            return {
                'status': 'ATIVA',
                'active_cameras': 0,
                'max_latency': 0,
                'min_throughput_mbps': 0,
                'sla_compliant': True,
                'guard_active': False,
                'reason': 'Nenhuma câmera ativa'
            }

        max_latency = max(float(ue.get('latency_us', 0) or 0) for ue in camera_entries)
        min_throughput_kbps = min(
            float(ue.get('rx_throughput_kbps', ue.get('throughput_kbps', 0)) or 0)
            for ue in camera_entries
        )
        min_throughput_mbps = min_throughput_kbps / 1000.0

        latency_block_ms = 80.0
        throughput_min_mbps = 25.0
        throughput_guard_mbps = 30.0

        if min_throughput_mbps < throughput_min_mbps:
            status = 'CRÍTICA'
            sla_compliant = False
            guard_active = False
            reason = f'Throughput {min_throughput_mbps:.1f}Mbps < 25Mbps'
        elif max_latency / 1000.0 >= latency_block_ms:
            status = 'CRÍTICA'
            sla_compliant = False
            guard_active = False
            reason = f'Latência {max_latency / 1000.0:.1f}ms >= 80ms'
        elif min_throughput_mbps < throughput_guard_mbps:
            status = 'GUARDA'
            sla_compliant = True
            guard_active = True
            reason = f'Throughput {min_throughput_mbps:.1f}Mbps em [25-30Mbps]'
        elif max_latency / 1000.0 >= 60.0:
            status = 'GUARDA'
            sla_compliant = True
            guard_active = True
            reason = f'Latência {max_latency / 1000.0:.1f}ms em [60-80ms]'
        else:
            status = 'PROTEGIDA'
            sla_compliant = True
            guard_active = False
            reason = 'SLA com margem'
        
        return {
            'status': status,
            'active_cameras': len(camera_entries),
            'max_latency': max_latency,
            'min_throughput_mbps': min_throughput_mbps,
            'sla_compliant': sla_compliant,
            'guard_active': guard_active,
            'reason': reason
        }

    except Exception as e:
        print(f"Erro ao obter proteção das câmeras: {e}")
        return {
            'status': 'ATIVA',
            'active_cameras': 0,
            'max_latency': 0,
            'min_throughput_mbps': 0,
            'sla_compliant': True,
            'guard_active': False,
            'reason': 'Erro ao ler métricas'
        }


def get_cvar_history(count=50):
    """
    Obtém histórico de CVaR para gráfico.
    
    Args:
        count: Número de amostras a retornar
        
    Returns:
        List de valores de CVaR em ms
    """
    try:
        cursor = DATA_LAKE.conn.cursor()
        cursor.execute("""
            SELECT cvar_per_ue_us 
            FROM extended_metrics 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (count,))
        
        cvar_values = [row[0] / 1000 for row in cursor.fetchall()]
        cvar_values.reverse()
        
        return cvar_values
        
    except Exception as e:
        print(f"Erro ao obter histórico de CVaR: {e}")
        return [0] * count


def get_recent_metrics(minutes=30):
    """Obtém métricas recentes."""
    return DATA_LAKE.get_recent_metrics(minutes)


def get_recent_decisions(minutes=60):
    """
    Obtém decisões recentes com estado.
    
    Returns:
        List de decisões com: timestamp, decision, reason, estado
    """
    try:
        cursor = DATA_LAKE.conn.cursor()
        cursor.execute('''
            SELECT timestamp, datetime, decision, reason,
                   CASE 
                       WHEN decision = 'BLOCKED' THEN 'CRITICAL'
                       WHEN decision = 'ALLOWED' THEN 'NORMAL'
                       WHEN decision = 'CONDITIONAL' THEN 'CONDITIONAL'
                       ELSE 'UNKNOWN'
                   END as estado
            FROM decisions_history
            WHERE timestamp > (SELECT MAX(timestamp) - ? * 60 FROM decisions_history)
            ORDER BY timestamp DESC
            LIMIT 20
        ''', (minutes,))
        
        decisions = []
        for row in cursor.fetchall():
            decisions.append({
                'timestamp': row[0],
                'datetime': row[1],
                'decision': row[2],
                'reason': row[3],
                'estado': row[4]
            })
        
        return decisions
    except Exception as e:
        print(f"Erro ao obter decisões: {e}")
        return []


def get_latest_resource_allocation():
    """Obtém a última alocação RAN/AI aplicada no runtime."""
    try:
        cursor = DATA_LAKE.conn.cursor()
        cursor.execute(
            """
            SELECT
                datetime,
                controller_id,
                target_policy_id,
                d_ran,
                d_ai,
                r_ran,
                r_ai,
                ran_completion_ratio,
                ai_completion_ratio,
                utilization_ratio
            FROM resource_allocation_history
            ORDER BY timestamp DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if not row:
            return {}
        return {
            'datetime': row[0],
            'controller_id': row[1],
            'target_policy_id': row[2],
            'd_ran': round(float(row[3] or 0.0), 4),
            'd_ai': round(float(row[4] or 0.0), 4),
            'r_ran': round(float(row[5] or 0.0), 4),
            'r_ai': round(float(row[6] or 0.0), 4),
            'ran_completion_ratio': round(float(row[7] or 0.0), 4),
            'ai_completion_ratio': round(float(row[8] or 0.0), 4),
            'utilization_ratio': round(float(row[9] or 0.0), 4),
        }
    except Exception as e:
        print(f"Erro ao obter alocação mais recente: {e}")
        return {}


def get_latest_decision_snapshot():
    """Obtém a última decisão operacional do rApp."""
    try:
        cursor = DATA_LAKE.conn.cursor()
        cursor.execute(
            """
            SELECT datetime, decision, energy_state, confidence, reason
            FROM decisions_history
            ORDER BY timestamp DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if not row:
            return {}
        return {
            'datetime': row[0],
            'decision': row[1],
            'energy_state': row[2],
            'confidence': round(float(row[3] or 0.0), 4),
            'reason': row[4] or '',
        }
    except Exception as e:
        print(f"Erro ao obter última decisão: {e}")
        return {}


def get_collection_health_summary():
    """Resume a saúde operacional da coleta lenta do cenário real."""
    summary = {
        'status': 'unknown',
        'label': 'Sem dados',
        'sim_time_latest': 0.0,
        'sim_time_oldest': 0.0,
        'sim_time_advance': 0.0,
        'throughput_latest_kbps': 0.0,
        'throughput_peak_kbps': 0.0,
        'throughput_floor_kbps': 0.0,
        'throughput_low_spikes': 0,
        'note': 'Ainda não há amostras suficientes.',
    }
    try:
        cursor = DATA_LAKE.conn.cursor()
        cursor.execute(
            """
            SELECT datetime, sim_time_s, throughput_kbps
            FROM extended_metrics
            ORDER BY timestamp DESC
            LIMIT 12
            """
        )
        rows = cursor.fetchall()
        if not rows:
            return summary

        sim_times = [float(row[1] or 0.0) for row in rows]
        throughputs = [float(row[2] or 0.0) for row in rows]
        latest_sim = sim_times[0]
        oldest_sim = sim_times[-1]
        sim_advance = latest_sim - oldest_sim
        peak_tp = max(throughputs) if throughputs else 0.0
        floor_tp = min(throughputs) if throughputs else 0.0
        low_spikes = sum(
            1 for value in throughputs
            if value > 0.0 and value < 5000.0
        )
        zeroish_spikes = sum(1 for value in throughputs if value <= 0.0)

        summary.update({
            'sim_time_latest': round(latest_sim, 3),
            'sim_time_oldest': round(oldest_sim, 3),
            'sim_time_advance': round(sim_advance, 3),
            'throughput_latest_kbps': round(throughputs[0], 3) if throughputs else 0.0,
            'throughput_peak_kbps': round(peak_tp, 3),
            'throughput_floor_kbps': round(floor_tp, 3),
            'throughput_low_spikes': int(low_spikes + zeroish_spikes),
        })

        if sim_advance <= 0.0:
            summary.update({
                'status': 'stalled',
                'label': 'Coleta parada',
                'note': 'O sim_time não avançou nas amostras recentes.',
            })
        elif sim_advance < 0.5:
            summary.update({
                'status': 'slow',
                'label': 'Coleta lenta',
                'note': 'O sim_time está avançando muito pouco por janela.',
            })
        else:
            summary.update({
                'status': 'collecting',
                'label': 'Coleta ativa',
                'note': 'O cenário segue coletando no regime real.',
            })

        if peak_tp >= 50000.0 and (low_spikes + zeroish_spikes) > 0:
            summary['note'] += ' Há jitter de throughput no coletor, mas sem evidência de queda total do cenário.'

        return summary
    except Exception as e:
        print(f"Erro ao obter resumo da coleta: {e}")
        summary['note'] = 'Erro ao ler a saúde da coleta.'
        return summary


def build_mobile_ops_snapshot():
    """Monta um resumo curto para operação móvel do cenário."""
    metrics = get_current_metrics() or {}
    global_metrics = metrics.get('global_metrics', {}) if isinstance(metrics, dict) else {}
    scenario_control = _safe_read_json(SCENARIO_CONTROL_FILE, {})
    service_slas = get_service_sla_status()
    latest_decision = get_latest_decision_snapshot()
    latest_alloc = get_latest_resource_allocation()
    collection = get_collection_health_summary()
    xapp_status = get_xapp_status()
    fixed_scenario = get_fixed_scenario_metadata()

    return {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'sim_time_range': metrics.get('sim_time_range', {}),
        'global_metrics': {
            'throughput_kbps': round(float(global_metrics.get('throughput_kbps', 0.0) or 0.0), 3),
            'throughput_source': global_metrics.get('throughput_source', 'unknown'),
            'cvar_ms': round(float(global_metrics.get('cvar_per_ue_us', 0.0) or 0.0) / 1000.0, 3),
            'p95_ms': round(float(global_metrics.get('latency_p95_us', 0.0) or 0.0) / 1000.0, 3),
            'active_ues': int(global_metrics.get('total_active_ues', 0) or 0),
            'active_cameras': int(global_metrics.get('total_active_cameras', 0) or 0),
            'active_sensors': int(global_metrics.get('total_active_sensors', 0) or 0),
            'active_vehicles': int(global_metrics.get('total_active_vehicles', 0) or 0),
        },
        'scenario': {
            'profile': scenario_control.get('collection_event_profile', ''),
            'cycle': int(scenario_control.get('collection_event_cycle', 0) or 0),
            'stage': scenario_control.get('collection_event_stage_name', 'unknown'),
            'generated_at_iso': scenario_control.get('generated_at_iso', ''),
            'fixed_scenario': fixed_scenario,
        },
        'service_slas': service_slas,
        'latest_decision': latest_decision,
        'latest_allocation': latest_alloc,
        'collection': collection,
        'xapps': {
            name: {
                'status': payload.get('status', 'UNKNOWN'),
                'pid': payload.get('pid'),
                'last_cycle': payload.get('last_cycle'),
            }
            for name, payload in (xapp_status or {}).items()
            if name in {'SLICER', 'ENERGY', 'VEHICLE'}
        },
    }


def get_recent_conflicts(minutes=60):
    """Obtém conflitos O-RAN recentes derivados das decisões do rApp."""
    try:
        return DATA_LAKE.get_recent_conflicts(minutes=minutes, limit=50)
    except Exception as e:
        print(f"Erro ao obter conflitos: {e}")
        return []


def get_vehicle_history(minutes=60, limit=60):
    """Obtém histórico agregado dos veículos.

    Prioriza `ue_metrics` quando o ns-3 exporta UEs `vehicle`. Se o runtime
    estiver no caminho CARLA/mock, faz fallback para `app3_snapshots`, que é
    onde o App3 já persiste o estado veicular consolidado.
    """
    cutoff = int(time.time()) - (minutes * 60)
    history = []
    try:
        cursor = DATA_LAKE.conn.execute(
            """
            SELECT
                timestamp,
                MAX(COALESCE(latency_avg_us, latency_us, 0)) / 1000.0 AS max_latency_ms,
                AVG(COALESCE(throughput_kbps, 0)) / 1000.0 AS avg_throughput_mbps,
                COUNT(*) AS active_vehicles
            FROM ue_metrics
            WHERE device_type = 'vehicle' AND timestamp >= ?
            GROUP BY timestamp
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        for row in cursor.fetchall():
            history.append(
                {
                    'timestamp': int(row['timestamp'] or 0),
                    'label': datetime.fromtimestamp(int(row['timestamp'] or 0)).strftime('%H:%M:%S'),
                    'max_latency_ms': round(float(row['max_latency_ms'] or 0.0), 3),
                    'avg_throughput_mbps': round(float(row['avg_throughput_mbps'] or 0.0), 3),
                    'active_vehicles': int(row['active_vehicles'] or 0),
                }
            )
        if history:
            return history

        cursor = DATA_LAKE.conn.execute(
            """
            SELECT
                timestamp,
                total_vehicles,
                max_latency_ms,
                max_packet_loss_percent,
                high_risk_vehicles,
                degraded_autonomy_vehicles
            FROM app3_snapshots
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        for row in cursor.fetchall():
            history.append(
                {
                    'timestamp': int(row['timestamp'] or 0),
                    'label': datetime.fromtimestamp(int(row['timestamp'] or 0)).strftime('%H:%M:%S'),
                    'max_latency_ms': round(float(row['max_latency_ms'] or 0.0), 3),
                    'avg_throughput_mbps': 0.0,
                    'active_vehicles': int(row['total_vehicles'] or 0),
                    'high_risk_vehicles': int(row['high_risk_vehicles'] or 0),
                    'degraded_autonomy_vehicles': int(row['degraded_autonomy_vehicles'] or 0),
                    'max_packet_loss_percent': round(float(row['max_packet_loss_percent'] or 0.0), 3),
                }
            )
    except Exception as e:
        print(f"Erro ao obter histórico veicular: {e}")
    return history


@app.route('/')
def index():
    """Dashboard principal."""
    metrics = get_current_metrics()
    xapp_status = get_xapp_status()
    policy_status = get_policy_status()
    app1_monitoring = get_app1_monitoring()
    app2_monitoring = get_app2_monitoring()
    app3_monitoring = get_app3_monitoring()
    decision_stats = get_decision_stats()
    pattern_summary = PATTERN_ENGINE.get_summary()
    recent_alerts = ALERT_MANAGER.get_recent_alerts(5)
    vehicle_history = get_vehicle_history(60, limit=50)
    scenario_counts = get_scenario_actor_counts()
    fixed_scenario = get_fixed_scenario_metadata()
    
    # Métricas para gráficos
    recent = get_recent_metrics(120)  # 2 horas para incluir dados antigos
    latency_history = [m['latency_us'] / 1000 for m in recent]  # ms
    cameras_history = [m['cameras_active'] for m in recent]
    
    # Obter métricas de coordenação rApp-xApps
    network_health = DATA_LAKE.get_network_health(window_minutes=5)
    
    # Obter trend analysis (CORRETO!)
    from rapp_trend_analysis import TrendAnalysis
    trend_analyzer = TrendAnalysis(DATA_LAKE)
    trend_analysis = trend_analyzer.calculate_latency_slope(window_minutes=5)
    
    # Obter estado dos xApps
    slicer_state = xapp_status.get('SLICER', {}).get('status', 'UNKNOWN')
    energy_saver = 'UNKNOWN'  # Será atualizado com decisões reais
    
    # Obter última decisão
    try:
        conn = DATA_LAKE.conn
        cursor = conn.cursor()
        cursor.execute("SELECT decision, reason FROM decisions_history ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            energy_saver = row[0] if row[0] else 'UNKNOWN'
    except:
        pass
    
    # NOVO: Calcular estatísticas por período
    period_stats = get_period_stats()
    
    # NOVO: Obter proteção das câmeras
    camera_protection = get_camera_protection()
    service_slas = get_service_sla_status()
    
    # NOVO: Obter histórico de CVaR para gráfico
    cvar_history = get_cvar_history(50)
    
    return render_template(
        'dashboard.html',
        metrics=metrics,
        xapp_status=xapp_status,
        policy_status=policy_status,
        decision_stats=decision_stats,
        pattern_summary=pattern_summary,
        recent_alerts=recent_alerts,
        latency_history=latency_history[-20:],
        cameras_history=cameras_history[-20:],
        cvar_history=cvar_history,
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        # Novos dados para coordenação rApp-xApps
        network_health=network_health,
        trend_analysis=trend_analysis,
        slicer_state=slicer_state,
        energy_saver=energy_saver,
        # Dados de períodos
        current_period=period_stats['current_period'],
        current_period_name=period_stats['current_period_name'],
        current_cvar=period_stats['current_cvar'],
        period1_stats=period_stats['period1'],
        period2_stats=period_stats['period2'],
        # Dados de proteção das câmeras
        camera_protection=camera_protection,
        app1_monitoring=app1_monitoring,
        app2_monitoring=app2_monitoring,
        app3_monitoring=app3_monitoring,
        vehicle_history=vehicle_history,
        scenario_counts=scenario_counts,
        service_slas=service_slas,
        fixed_scenario=fixed_scenario,
    )


@app.route('/metrics')
def metrics_page():
    """Página de métricas detalhadas."""
    metrics = get_current_metrics()
    extended = PATTERN_ENGINE.analyze_extended_metrics(30)
    link = PATTERN_ENGINE.analyze_link_quality(30)
    
    return render_template(
        'metrics.html',
        metrics=metrics,
        extended=extended,
        link=link,
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )


@app.route('/decisions')
def decisions_page():
    """Página de histórico de decisões."""
    decision_stats = get_decision_stats()
    recent_decisions = get_recent_decisions(60)
    
    return render_template(
        'decisions.html',
        decision_stats=decision_stats,
        recent_decisions=recent_decisions,
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )


@app.route('/conflicts')
def conflicts_page():
    """Página de gestão de conflitos O-RAN."""
    conflict_stats = DATA_LAKE.get_conflict_stats(24)
    recent_conflicts = get_recent_conflicts(60)
    learned_report, learned_adjacency = get_learned_conflict_assets()
    service_slas = get_service_sla_status()

    return render_template(
        'conflicts.html',
        conflict_stats=conflict_stats,
        recent_conflicts=recent_conflicts,
        learned_report=learned_report,
        learned_adjacency=learned_adjacency,
        service_slas=service_slas,
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )


@app.route('/pattern')
def pattern_page():
    """Página de análise de padrões."""
    summary = PATTERN_ENGINE.get_summary()
    hourly = PATTERN_ENGINE.detect_seasonal_patterns(7)
    daily = PATTERN_ENGINE.detect_day_of_week_pattern(7)
    window = PATTERN_ENGINE.calculate_energy_window()
    
    return render_template(
        'pattern.html',
        summary=summary,
        hourly=hourly,
        daily=daily,
        window=window,
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )


@app.route('/xapps')
def xapps_page():
    """Página de status dos xApps."""
    xapp_status = get_xapp_status()
    policy_status = get_policy_status()
    app1_monitoring = get_app1_monitoring()
    app2_monitoring = get_app2_monitoring()
    app3_monitoring = get_app3_monitoring()
    ack_stats = PATTERN_ENGINE.dl.conn.execute(
        "SELECT COUNT(*) FROM decisions_history WHERE datetime >= datetime('now', '-1 hour')"
    ).fetchone()[0]

    return render_template(
        'xapps.html',
        xapp_status=xapp_status,
        policy_status=policy_status,
        app1_monitoring=app1_monitoring,
        app2_monitoring=app2_monitoring,
        app3_monitoring=app3_monitoring,
        ack_stats=ack_stats,
        grafana_url=f"http://localhost:{RUNTIME_CONFIG['monitoring']['grafana_port']}",
        app1_url="http://localhost:5100",
        app3_url="http://localhost:5300",
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )


@app.route('/ml')
def ml_page():
    """Página de Machine Learning."""
    # Load ML status
    ml_status = {
        'loaded': False,
        'model_type': 'Random Forest',
        'accuracy': 0.0,
        'cv_mean': 0.0,
        'cv_std': 0.0,
        'classes': [],
        'n_features': 0
    }

    # Try to load training report
    report_path = '/home/robert/orange_nuclear/models/training_report.json'
    if os.path.exists(report_path):
        try:
            with open(report_path, 'r') as f:
                report = json.load(f)
                ml_status = {
                    'loaded': True,
                    'model_type': 'Random Forest + XGBoost',
                    'accuracy': report.get('classifier', {}).get('random_forest_accuracy', 0),
                    'cv_mean': report.get('classifier', {}).get('cross_validation_mean', 0),
                    'cv_std': report.get('classifier', {}).get('cross_validation_std', 0),
                    'classes': report.get('classifier', {}).get('classes', []),
                    'n_features': len(report.get('features', []))
                }
        except Exception:
            pass

    # Get last ML prediction from decisions
    last_prediction = {'decision': 'N/A', 'confidence': 0, 'predicted_cvar_ms': 0}
    try:
        cursor = PATTERN_ENGINE.dl.conn.execute(
            """SELECT ml_decision, ml_confidence, ml_predicted_cvar_ms, decision
               FROM decisions_history
               WHERE ml_decision IS NOT NULL AND ml_decision != ''
               ORDER BY timestamp DESC LIMIT 1"""
        )
        row = cursor.fetchone()
        if row:
            last_prediction['decision'] = row[0] or 'N/A'
            last_prediction['confidence'] = row[1] or 0
            last_prediction['predicted_cvar_ms'] = row[2] or 0
    except Exception:
        pass

    # Get prediction history with ML data
    prediction_history = []
    try:
        cursor = PATTERN_ENGINE.dl.conn.execute(
            """SELECT timestamp, ml_decision, decision, ml_confidence,
                      ml_predicted_cvar_ms, ml_influenced
               FROM decisions_history
               WHERE ml_decision IS NOT NULL AND ml_decision != ''
               ORDER BY timestamp DESC LIMIT 20"""
        )
        for row in cursor.fetchall():
            ts = row[0] or 0
            # Convert Unix timestamp to readable format
            try:
                dt_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
            except:
                dt_str = str(ts)
            
            ml_dec = row[1] or 'N/A'
            rule_dec = row[2] or 'UNKNOWN'
            ml_conf = row[3] or 0
            ml_cvar = row[4] or 0
            influenced = row[5] or 0

            # Calculate concordance
            concordance = (ml_dec == rule_dec) if ml_dec not in ('N/A', 'NONE', '') else False

            prediction_history.append({
                'timestamp': dt_str,
                'ml_decision': ml_dec,
                'rule_decision': rule_dec,
                'confidence': ml_conf,
                'predicted_cvar_ms': ml_cvar,
                'ml_influenced': bool(influenced),
                'concordance': concordance
            })
    except Exception as e:
        print(f"Erro ao buscar histórico ML: {e}")

    # Calculate concordance percentage
    concordance_pct = 85.0  # Default
    if prediction_history:
        concordant = sum(1 for p in prediction_history if p.get('concordance', False))
        total = len([p for p in prediction_history if p['ml_decision'] != 'N/A'])
        if total > 0:
            concordance_pct = round(concordant / total * 100, 1)

    # Get feature importance
    feature_importance = {}
    if os.path.exists(report_path):
        try:
            with open(report_path, 'r') as f:
                report = json.load(f)
                fi = report.get('feature_importance', {})
                # Get top 5 features
                sorted_fi = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:5]
                feature_importance = {k: round(v, 4) for k, v in sorted_fi}
        except Exception:
            pass

    if not feature_importance:
        feature_importance = {
            'cvar_ms': 0.25,
            'slope': 0.20,
            'cameras': 0.15,
            'hour': 0.10,
            'ues': 0.08
        }

    # Get retrain info
    retrain_count = 0
    next_retrain = "N/A"
    try:
        # Try to read from orchestrator stats (if available)
        pass
    except Exception:
        pass

    return render_template(
        'ml.html',
        ml_status=ml_status,
        last_prediction=last_prediction,
        prediction_history=prediction_history,
        concordance_pct=concordance_pct,
        feature_importance=feature_importance,
        retrain_count=retrain_count,
        next_retrain=next_retrain,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )


@app.route('/ops')
def ops_mobile_page():
    """Página móvel de operação do cenário real."""
    snapshot = build_mobile_ops_snapshot()
    return render_template(
        'ops_mobile.html',
        snapshot=snapshot,
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )


@app.route('/api/metrics')
def api_metrics():
    """API: Métricas atuais."""
    metrics = get_current_metrics()
    return jsonify(metrics or {})


@app.route('/api/ops')
def api_ops():
    """API: Resumo curto para operação móvel."""
    return jsonify(build_mobile_ops_snapshot())


@app.route('/api/extended')
def api_extended():
    """API: Métricas estendidas."""
    extended = PATTERN_ENGINE.analyze_extended_metrics(30)
    return jsonify(extended)


@app.route('/api/link')
def api_link():
    """API: Qualidade do enlace."""
    link = PATTERN_ENGINE.analyze_link_quality(30)
    return jsonify(link)


@app.route('/api/decisions')
def api_decisions():
    """API: Estatísticas de decisões."""
    stats = get_decision_stats()
    return jsonify(stats)


@app.route('/api/drl')
def api_drl():
    """API: Estatísticas do DRL (SBiLSTM + A3C)."""
    stats = get_drl_stats()
    return jsonify(stats)


@app.route('/drl')
def drl_page():
    """Página: Visualização DRL."""
    stats = get_drl_stats()
    return render_template('drl_dashboard.html', 
                        predictions=stats.get('recent', []),
                        stats=stats.get('stats', {}))


@app.route('/api/history/<int:minutes>')
def api_history(minutes):
    """API: Histórico de métricas."""
    history = get_recent_metrics(minutes)
    return jsonify(history)


@app.route('/api/vehicle-history/<int:minutes>')
def api_vehicle_history(minutes):
    """API: Histórico agregado dos veículos."""
    return jsonify(get_vehicle_history(minutes, limit=120))


@app.route('/api/health')
def api_health():
    """API: Health dos xApps."""
    return jsonify(get_xapp_status())


@app.route('/api/app1')
def api_app1():
    """API: Snapshot da App1-Vigilancia."""
    return jsonify(get_app1_monitoring())


@app.route('/api/app2')
def api_app2():
    """API: Snapshot da App2-Monitoramento."""
    return jsonify(get_app2_monitoring())


@app.route('/api/app3')
def api_app3():
    """API: Snapshot da App3-Veicular."""
    return jsonify(get_app3_monitoring())


@app.route('/api/service-slas')
def api_service_slas():
    """API: Resumo consolidado dos SLAs de App1/câmeras, App3/veículos e App2/sensores."""
    return jsonify(get_service_sla_status())


@app.route('/api/alerts')
def api_alerts():
    """API: Alertas recentes."""
    alerts = ALERT_MANAGER.get_recent_alerts(20)
    return jsonify({'alerts': alerts})


@app.route('/api/pattern/summary')
def api_pattern_summary():
    """API: Resumo de padrões."""
    summary = PATTERN_ENGINE.get_summary()
    return jsonify(summary)


@app.route('/api/efficiency')
def api_efficiency():
    """API: Eficiência da rede."""
    efficiency = PATTERN_ENGINE.calculate_network_efficiency(30)
    return jsonify(efficiency)


@app.route('/api/energy')
def api_energy():
    """API: Estatísticas de energia."""
    stats = DATA_LAKE.get_energy_stats(24)
    return jsonify(stats)


@app.route('/api/energy/current')
def api_energy_current():
    """API: Estado atual de energia."""
    import os
    energy_file = as_str(XAPP_INTENTS_DIR / "energy_command.json")
    if os.path.exists(energy_file):
        try:
            with open(energy_file, 'r') as f:
                cmd = json.load(f)
                power = cmd.get('power_level', 100)
                savings = 100 - power
                action = cmd.get('action', 'UNKNOWN')
                return jsonify({
                    'action': action,
                    'power_percent': power,
                    'savings_percent': savings,
                    'timestamp': cmd.get('timestamp')
                })
        except:
            pass
    return jsonify({'action': 'UNKNOWN', 'power_percent': 100, 'savings_percent': 0})


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='GreenRAN rApp Dashboard')
    parser.add_argument('--host', default=RUNTIME_CONFIG["dashboard"]["host"], help='Host para bind')
    parser.add_argument('--port', type=int, default=int(RUNTIME_CONFIG["dashboard"]["port"]), help='Porta')
    parser.add_argument('--debug', action='store_true', help='Debug mode')
    args = parser.parse_args()
    
    print("=" * 60)
    print("       GreenRAN rApp Dashboard (Flask)")
    print("=" * 60)
    print(f"  Host: {args.host}")
    print(f"  Port: {args.port}")
    print(f"  URL: http://localhost:{args.port}")
    print("=" * 60)
    print()
    print("  Rotas:")
    print("    /           - Dashboard principal")
    print("    /metrics    - Métricas detalhadas")
    print("    /decisions  - Histórico de decisões")
    print("    /pattern    - Análise de padrões")
    print("    /xapps      - Status dos xApps")
    print("    /api/*      - API REST")
    print()
    print("  Pressione Ctrl+C para encerrar")
    print("=" * 60)
    
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
