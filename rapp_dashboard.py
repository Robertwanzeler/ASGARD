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
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        # Novos dados para coordenação rApp-xApps
        network_health=network_health,
        trend_analysis=trend_analysis,
        slicer_state=slicer_state,
        energy_saver=energy_saver
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
            """SELECT datetime, ml_decision, decision, ml_confidence,
                      ml_predicted_cvar_ms, ml_influenced
               FROM decisions_history
               ORDER BY timestamp DESC LIMIT 20"""
        )
        for row in cursor.fetchall():
            ml_dec = row[1] or 'N/A'
            rule_dec = row[2]
            ml_conf = row[3] or 0
            ml_cvar = row[4] or 0
            influenced = row[5] or 0

            # Calculate concordance
            concordance = (ml_dec == rule_dec) if ml_dec != 'N/A' else False

            prediction_history.append({
                'timestamp': row[0],
                'ml_decision': ml_dec,
                'rule_decision': rule_dec,
                'confidence': ml_conf,
                'predicted_cvar_ms': ml_cvar,
                'ml_influenced': influenced,
                'concordance': concordance
            })
    except Exception:
        pass

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

    return render_template(
        'ml.html',
        ml_status=ml_status,
        last_prediction=last_prediction,
        prediction_history=prediction_history,
        concordance_pct=concordance_pct,
        feature_importance=feature_importance,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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


@app.route('/api/energy')
def api_energy():
    """API: Estatísticas de energia."""
    stats = DATA_LAKE.get_energy_stats(24)
    return jsonify(stats)


@app.route('/api/energy/current')
def api_energy_current():
    """API: Estado atual de energia."""
    import os
    energy_file = "/tmp/xapp_intents/energy_command.json"
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
