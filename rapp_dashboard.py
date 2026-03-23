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

sys.path.insert(0, '/home/robert/orange_nuclear')

from rapp_data_lake import DataLake
from rapp_pattern_engine import PatternRecognition
from rapp_alerts import AlertManager

app = Flask(__name__)

DATA_LAKE = DataLake()
PATTERN_ENGINE = PatternRecognition(DATA_LAKE)
ALERT_MANAGER = AlertManager()

METRICS_FILE = "/tmp/xapp_metrics/extended_metrics.json"
XAPP_HEALTH_FILE = "/tmp/xapp_health.json"
POLICY_STATUS_FILE = "/tmp/rapp_policies/policy_status.json"


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
    if os.path.exists(POLICY_STATUS_FILE):
        try:
            with open(POLICY_STATUS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}


def get_decision_stats():
    """Obtém estatísticas de decisões."""
    return DATA_LAKE.get_decision_stats(24)


def get_recent_metrics(minutes=30):
    """Obtém métricas recentes."""
    return DATA_LAKE.get_recent_metrics(minutes)


@app.route('/')
def index():
    """Dashboard principal."""
    metrics = get_current_metrics()
    xapp_status = get_xapp_status()
    policy_status = get_policy_status()
    decision_stats = get_decision_stats()
    pattern_summary = PATTERN_ENGINE.get_summary()
    recent_alerts = ALERT_MANAGER.get_recent_alerts(5)
    
    # Métricas para gráficos
    recent = get_recent_metrics(60)
    latency_history = [m['latency_us'] / 1000 for m in recent]  # ms
    cameras_history = [m['cameras_active'] for m in recent]
    
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
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
    recent = get_recent_metrics(60)
    
    return render_template(
        'decisions.html',
        decision_stats=decision_stats,
        recent=recent[-20:],
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
    ack_stats = PATTERN_ENGINE.dl.conn.execute(
        "SELECT COUNT(*) FROM decisions_history WHERE datetime >= datetime('now', '-1 hour')"
    ).fetchone()[0]
    
    return render_template(
        'xapps.html',
        xapp_status=xapp_status,
        policy_status=policy_status,
        ack_stats=ack_stats,
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )


@app.route('/api/metrics')
def api_metrics():
    """API: Métricas atuais."""
    metrics = get_current_metrics()
    return jsonify(metrics or {})


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


@app.route('/api/history/<int:minutes>')
def api_history(minutes):
    """API: Histórico de métricas."""
    history = get_recent_metrics(minutes)
    return jsonify(history)


@app.route('/api/health')
def api_health():
    """API: Health dos xApps."""
    return jsonify(get_xapp_status())


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


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='GreenRAN rApp Dashboard')
    parser.add_argument('--host', default='0.0.0.0', help='Host para bind')
    parser.add_argument('--port', type=int, default=5000, help='Porta')
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
