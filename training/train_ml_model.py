#!/usr/bin/env python3
"""
GreenRAN - ML Training Pipeline
================================
Trains ML models on GreenRAN Data Lake data.

Models:
  1. Random Forest Classifier - Predicts decision (ALLOWED/CONDITIONAL/BLOCKED)
  2. XGBoost Classifier - Predicts decision with better performance
  3. Random Forest Regressor - Predicts CVaR value

Usage:
    python3 train_ml_model.py [--db /tmp/rapp_data_lake.db] [--output ./models]

Output:
    ./models/rf_classifier.joblib  - Random Forest classifier
    ./models/xgb_classifier.joblib - XGBoost classifier
    ./models/rf_regressor.joblib   - CVaR regressor
    ./models/feature_importance.png - Feature importance chart
    ./models/training_report.txt   - Training metrics report
"""

import os
import sys
import time
import sqlite3
import argparse
import json
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

from sklearn.model_selection import TimeSeriesSplit
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import (
    classification_report, confusion_matrix, accuracy_score,
    mean_absolute_error, mean_squared_error, r2_score
)
import joblib

try:
    from xgboost import XGBClassifier
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False
    print("[AVISO] XGBoost não disponível, usando apenas Random Forest")


DEFAULT_DB = "/tmp/rapp_data_lake.db"
DEFAULT_OUTPUT = "./models"
TEMPORAL_TEST_FRACTION = 0.2


def temporal_train_test_split(X, y, test_fraction=TEMPORAL_TEST_FRACTION):
    """Temporal holdout split preserving chronology."""
    n_samples = len(X)
    if n_samples < 10:
        raise ValueError(f"Dataset muito pequeno para split temporal: {n_samples}")

    split_idx = max(int(n_samples * (1 - test_fraction)), 1)
    split_idx = min(split_idx, n_samples - 1)

    return (
        X[:split_idx],
        X[split_idx:],
        y[:split_idx],
        y[split_idx:],
    )


def temporal_cv_accuracy(X_train, y_train, n_splits=5):
    """Time-series cross-validation with per-fold scaling."""
    if len(X_train) < 20:
        return float("nan"), float("nan")

    splitter = TimeSeriesSplit(n_splits=min(n_splits, max(2, len(X_train) // 20)))
    scores = []

    for train_idx, val_idx in splitter.split(X_train):
        scaler = StandardScaler()
        X_fold_train = scaler.fit_transform(X_train[train_idx])
        X_fold_val = scaler.transform(X_train[val_idx])

        model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            min_samples_leaf=2,
            class_weight='balanced',
            random_state=42,
            n_jobs=-1
        )
        model.fit(X_fold_train, y_train[train_idx])
        scores.append(accuracy_score(y_train[val_idx], model.predict(X_fold_val)))

    return float(np.mean(scores)), float(np.std(scores))


def load_data(db_path, hours=None):
    """Load data from SQLite Data Lake.

    Args:
        db_path: Path to SQLite database
        hours: If specified, only load data from last N hours
    """
    print(f"[1/6] Carregando dados de {db_path}...")

    if not os.path.exists(db_path):
        print(f"[ERRO] Database não encontrado: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)

    # Build time filter
    time_filter = ""
    if hours:
        cutoff = int(time.time()) - (hours * 3600)
        time_filter = f"AND m.timestamp >= {cutoff}"
        print(f"    → Filtrando últimas {hours} horas")

    # Load extended metrics joined with decisions
    query = f"""
        SELECT
            m.timestamp,
            m.sim_time_s,
            m.global_avg_latency_us,
            m.global_worst_latency_us,
            m.global_jitter_us,
            m.global_packet_loss_rate,
            m.throughput_kbps,
            m.total_active_ues,
            m.total_active_cameras,
            m.total_critical_ues,
            m.total_tx_bytes,
            m.total_rx_bytes,
            m.cvar_per_ue_us,
            m.variance_per_ue_us2,
            m.latency_p95_per_ue_us,
            m.latency_p95_us,
            d.decision,
            d.energy_state,
            d.confidence,
            d.reason
        FROM extended_metrics m
        JOIN decisions_history d
            ON m.timestamp = d.timestamp
        WHERE m.cvar_per_ue_us > 0
        {time_filter}
        ORDER BY m.timestamp
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    print(f"    → {len(df)} registros carregados")
    return df


def engineer_features(df):
    """Create derived features for ML."""
    print("[2/6] Feature Engineering...")

    # Convert timestamp to datetime features
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
    df['hour'] = df['datetime'].dt.hour
    df['day_of_week'] = df['datetime'].dt.dayofweek

    # Time features remain available for analysis, but are not fed to the model
    # to avoid clock-driven bias.
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    df['is_night'] = ((df['hour'] >= 22) | (df['hour'] < 6)).astype(int)
    df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)

    # Camera ratio
    df['camera_ratio'] = df['total_active_cameras'] / df['total_active_ues'].clip(lower=1)

    # CVaR in ms (more readable)
    df['cvar_ms'] = df['cvar_per_ue_us'] / 1000.0
    df['latency_p95_ms'] = df['latency_p95_per_ue_us'] / 1000.0
    df['avg_latency_ms'] = df['global_avg_latency_us'] / 1000.0

    # Variance in ms²
    df['variance_ms2'] = df['variance_per_ue_us2'] / 1_000_000.0

    # Trend features (difference from previous values)
    df['cvar_diff'] = df['cvar_ms'].diff().fillna(0)
    df['latency_diff'] = df['avg_latency_ms'].diff().fillna(0)

    # Rolling statistics (5-point window)
    df['cvar_rolling_mean'] = df['cvar_ms'].rolling(window=5, min_periods=1).mean()
    df['cvar_rolling_std'] = df['cvar_ms'].rolling(window=5, min_periods=1).std().fillna(0)

    # Throughput in Mbps
    df['throughput_mbps'] = df['throughput_kbps'] / 1000.0
    
    # Packet loss rate (já existe no banco como decimal, converter para %)
    if 'global_packet_loss_rate' in df.columns:
        df['packet_loss_rate'] = df['global_packet_loss_rate'] * 100
    else:
        df['packet_loss_rate'] = 0.0
    
    # Jitter in ms (já está em us no banco)
    if 'global_jitter_us' in df.columns:
        df['jitter_ms'] = df['global_jitter_us'] / 1000.0
    else:
        df['jitter_ms'] = 0.0
    
    # TX/RX ratio (proporção de tráfego uplink vs downlink)
    if 'total_tx_bytes' in df.columns and 'total_rx_bytes' in df.columns:
        df['tx_rx_ratio'] = df['total_tx_bytes'] / df['total_rx_bytes'].clip(lower=1)
    else:
        df['tx_rx_ratio'] = 0.0
    
    # Features de TREND (3) - NOVAS para ML PREDITIVA
    # cvar_trend: diferença entre CVaR atual e 5 ciclos atrás
    df['cvar_trend'] = df['cvar_ms'].diff(5).fillna(0)
    
    # throughput_trend: diferença de throughput (positivo = aumentando)
    if 'throughput_mbps' in df.columns:
        df['throughput_trend'] = df['throughput_mbps'].diff().fillna(0)
    else:
        df['throughput_trend'] = 0
    
    # packet_loss_trend: diferença de packet loss (positivo = piorando)
    df['packet_loss_trend'] = df['packet_loss_rate'].diff().fillna(0)

    # ============================================================
    # LAG FEATURES - NOVAS para ML PREDITIVA (predizer próximo ciclo)
    # ============================================================
    # Lag features - últimos 5 ciclos (t-1, t-2, ..., t-5)
    df['cvar_lag_1'] = df['cvar_ms'].shift(1).fillna(df['cvar_ms'])
    df['cvar_lag_2'] = df['cvar_ms'].shift(2).fillna(df['cvar_lag_1'])
    df['cvar_lag_3'] = df['cvar_ms'].shift(3).fillna(df['cvar_lag_2'])
    df['cvar_lag_4'] = df['cvar_ms'].shift(4).fillna(df['cvar_lag_3'])
    df['cvar_lag_5'] = df['cvar_ms'].shift(5).fillna(df['cvar_lag_4'])

    df['throughput_lag_1'] = df['throughput_mbps'].shift(1).fillna(df['throughput_mbps'])
    df['throughput_lag_2'] = df['throughput_mbps'].shift(2).fillna(df['throughput_lag_1'])
    df['throughput_lag_3'] = df['throughput_mbps'].shift(3).fillna(df['throughput_lag_2'])

    df['packet_loss_lag_1'] = df['packet_loss_rate'].shift(1).fillna(df['packet_loss_rate'])
    df['packet_loss_lag_2'] = df['packet_loss_rate'].shift(2).fillna(df['packet_loss_lag_1'])
    df['packet_loss_lag_3'] = df['packet_loss_rate'].shift(3).fillna(df['packet_loss_lag_2'])

    df['latency_lag_1'] = df['avg_latency_ms'].shift(1).fillna(df['avg_latency_ms'])
    df['latency_lag_2'] = df['avg_latency_ms'].shift(2).fillna(df['latency_lag_1'])
    
    # Rolling features (média dos últimos N ciclos)
    df['cvar_rolling_3'] = df['cvar_ms'].rolling(3, min_periods=1).mean()
    df['cvar_rolling_10'] = df['cvar_ms'].rolling(10, min_periods=1).mean()
    df['cvar_rolling_std_3'] = df['cvar_ms'].rolling(3, min_periods=1).std().fillna(0)
    
    # Aceleração (derivada segunda - indica se tendência está acelerando)
    df['cvar_acceleration'] = df['cvar_diff'].diff().fillna(0)
    
    # Velocidade de mudança (diferença da diferença)
    df['latency_acceleration'] = df['latency_diff'].diff().fillna(0)
    
    # NOVAS FEATURES PREDITIVAS (do rapp_ml_predictor.py)
    # jitter_trend: tendência do jitter (piorando ou melhorando)
    df['jitter_trend'] = df['jitter_ms'].diff().fillna(0)
    
    # cvar_momentum: aceleração da aceleração (momento de mudança)
    df['cvar_momentum'] = df['cvar_acceleration'].diff().fillna(0)
    
    # critical_ue_ratio: proporção de UEs críticos (indica carga crítica)
    df['critical_ue_ratio'] = df['total_critical_ues'] / df['total_active_ues'].clip(lower=1)

    # CVaR zones
    df['cvar_zone'] = pd.cut(
        df['cvar_ms'],
        bins=[0, 60, 80, float('inf')],
        labels=[0, 1, 2]  # 0=green, 1=yellow, 2=red
    ).astype(int)

    feature_cols = [
        # Features originais (16) - REMOVIDO sim_time_s (não é causal!)
        'cvar_ms',
        'cvar_diff',
        'cvar_rolling_mean',
        'cvar_rolling_std',
        'latency_p95_ms',
        'avg_latency_ms',
        'variance_ms2',
        'total_active_cameras',
        'camera_ratio',
        'total_critical_ues',
        'cvar_zone',
        # Features de rede (5)
        'throughput_mbps',
        'packet_loss_rate',
        'jitter_ms',
        'tx_rx_ratio',
        # Features de TREND (3) - CAUSAIS
        'cvar_trend',
        'throughput_trend',
        'packet_loss_trend',
        # LAG FEATURES - NOVAS para ML PREDITIVA (15 features)
        'cvar_lag_1', 'cvar_lag_2', 'cvar_lag_3', 'cvar_lag_4', 'cvar_lag_5',
        'throughput_lag_1', 'throughput_lag_2', 'throughput_lag_3',
        'packet_loss_lag_1', 'packet_loss_lag_2', 'packet_loss_lag_3',
        'latency_lag_1', 'latency_lag_2',
        # Rolling e Acceleration (8) - CAUSAIS
        'cvar_rolling_3', 'cvar_rolling_10', 'cvar_rolling_std_3',
        'cvar_acceleration', 'latency_acceleration',
        # NOVAS FEATURES PREDITIVAS (3) - CAUSAIS
        'jitter_trend', 'cvar_momentum', 'critical_ue_ratio',
    ]

    print(f"    → {len(feature_cols)} features criadas")
    return df, feature_cols


def train_classifier(df, feature_cols, output_dir):
    """Train decision classifier - VERSÃO PREDITIVA (target = próximo ciclo)."""
    print("[3/6] Treinando classificador de decisões PREDITIVO...")
    print("       → Target: decision_next (predizer próximo ciclo)")

    # Prepare data - USAR decision_next (target preditivo)
    X = df[feature_cols].values
    y = df['decision_next'].values  # <-- PREDITIVO: próximo ciclo

    # Encode labels
    le = LabelEncoder()
    y_encoded = le.fit_transform(y)

    # Temporal split
    X_train, X_test, y_train, y_test = temporal_train_test_split(X, y_encoded)

    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Random Forest
    rf = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_split=5,
        min_samples_leaf=2,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1
    )
    rf.fit(X_train_scaled, y_train)

    # Evaluate
    y_pred_rf = rf.predict(X_test_scaled)
    rf_accuracy = accuracy_score(y_test, y_pred_rf)

    print(f"    Random Forest Accuracy: {rf_accuracy:.4f}")
    print(f"    Classification Report:")
    print(classification_report(y_test, y_pred_rf, target_names=le.classes_))

    # Time-series cross-validation
    cv_mean, cv_std = temporal_cv_accuracy(X_train, y_train, n_splits=5)
    print(f"    Cross-validation temporal: {cv_mean:.4f} ± {cv_std:.4f}")

    # Save
    joblib.dump(rf, os.path.join(output_dir, 'rf_classifier.joblib'))
    joblib.dump(scaler, os.path.join(output_dir, 'rf_scaler.joblib'))
    joblib.dump(le, os.path.join(output_dir, 'label_encoder.joblib'))

    results = {
        'rf_accuracy': rf_accuracy,
        'cv_mean': cv_mean,
        'cv_std': cv_std,
        'feature_importance': dict(zip(feature_cols, rf.feature_importances_.tolist())),
        'confusion_matrix': confusion_matrix(y_test, y_pred_rf).tolist(),
        'classes': le.classes_.tolist(),
        'split_mode': 'temporal_holdout'
    }

    # XGBoost if available
    if HAS_XGBOOST:
        print("    Treinando XGBoost...")
        xgb = XGBClassifier(
            n_estimators=100,
            max_depth=6,
            learning_rate=0.1,
            random_state=42,
            n_jobs=-1,
            eval_metric='mlogloss'
        )
        xgb.fit(X_train_scaled, y_train)
        y_pred_xgb = xgb.predict(X_test_scaled)
        xgb_accuracy = accuracy_score(y_test, y_pred_xgb)
        print(f"    XGBoost Accuracy: {xgb_accuracy:.4f}")

        joblib.dump(xgb, os.path.join(output_dir, 'xgb_classifier.joblib'))
        results['xgb_accuracy'] = xgb_accuracy

    return results, rf, scaler, le


def train_regressor(df, feature_cols, output_dir):
    """Train CVaR regressor - VERSÃO PREDITIVA (target = próximo ciclo)."""
    print("[4/6] Treinando regressor de CVaR PREDITIVO...")
    print("       → Target: cvar_next (predizer CVaR do próximo ciclo)")

    # Prepare data - USAR cvar_next (target preditivo)
    X = df[feature_cols].values
    y = df['cvar_next'].values  # <-- PREDITIVO: próximo ciclo

    # Temporal split
    X_train, X_test, y_train, y_test = temporal_train_test_split(X, y)

    # Scale
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Random Forest Regressor
    rf_reg = RandomForestRegressor(
        n_estimators=100,
        max_depth=10,
        min_samples_split=5,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1
    )
    rf_reg.fit(X_train_scaled, y_train)

    # Evaluate
    y_pred = rf_reg.predict(X_test_scaled)
    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)

    print(f"    MAE: {mae:.2f} ms")
    print(f"    RMSE: {rmse:.2f} ms")
    print(f"    R²: {r2:.4f}")

    # Save
    joblib.dump(rf_reg, os.path.join(output_dir, 'rf_regressor.joblib'))
    joblib.dump(scaler, os.path.join(output_dir, 'reg_scaler.joblib'))

    return {
        'mae': mae,
        'rmse': rmse,
        'r2': r2
    }, rf_reg


def save_report(clf_results, reg_results, feature_cols, output_dir):
    """Save training report."""
    print("[5/6] Salvando relatório...")

    report = {
        'timestamp': datetime.now().isoformat(),
        'dataset_size': clf_results.get('dataset_size', 0),
        'features': feature_cols,
        'classifier': {
            'random_forest_accuracy': clf_results['rf_accuracy'],
            'cross_validation_mean': clf_results['cv_mean'],
            'cross_validation_std': clf_results['cv_std'],
            'confusion_matrix': clf_results['confusion_matrix'],
            'classes': clf_results['classes'],
        },
        'regressor': {
            'mae_ms': reg_results['mae'],
            'rmse_ms': reg_results['rmse'],
            'r2': reg_results['r2'],
        },
        'feature_importance': clf_results['feature_importance']
    }

    if 'xgb_accuracy' in clf_results:
        report['classifier']['xgboost_accuracy'] = clf_results['xgb_accuracy']

    report_path = os.path.join(output_dir, 'training_report.json')
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)

    # Text report
    txt_path = os.path.join(output_dir, 'training_report.txt')
    with open(txt_path, 'w') as f:
        f.write("=" * 60 + "\n")
        f.write("  GreenRAN ML Training Report\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        f.write("CLASSIFIER (Decisão rApp)\n")
        f.write("-" * 40 + "\n")
        f.write(f"  Random Forest Accuracy: {clf_results['rf_accuracy']:.4f}\n")
        if 'xgb_accuracy' in clf_results:
            f.write(f"  XGBoost Accuracy: {clf_results['xgb_accuracy']:.4f}\n")
        f.write(f"  Cross-validation: {clf_results['cv_mean']:.4f} ± {clf_results['cv_std']:.4f}\n")
        f.write(f"  Classes: {clf_results['classes']}\n\n")

        f.write("REGRESSOR (CVaR prediction)\n")
        f.write("-" * 40 + "\n")
        f.write(f"  MAE: {reg_results['mae']:.2f} ms\n")
        f.write(f"  RMSE: {reg_results['rmse']:.2f} ms\n")
        f.write(f"  R²: {reg_results['r2']:.4f}\n\n")

        f.write("TOP 10 FEATURES (by importance)\n")
        f.write("-" * 40 + "\n")
        sorted_features = sorted(
            clf_results['feature_importance'].items(),
            key=lambda x: x[1],
            reverse=True
        )
        for feat, imp in sorted_features[:10]:
            f.write(f"  {feat}: {imp:.4f}\n")

    print(f"    → Relatório salvo em {txt_path}")


def plot_feature_importance(clf_results, output_dir):
    """Plot feature importance chart."""
    print("[6/6] Gerando gráfico de feature importance...")

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        plt.style.use('dark_background')
        plt.rcParams['figure.facecolor'] = '#16213e'
        plt.rcParams['axes.facecolor'] = '#16213e'

        importance = clf_results['feature_importance']
        sorted_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)

        features = [f[0] for f in sorted_features]
        values = [f[1] for f in sorted_features]

        fig, ax = plt.subplots(figsize=(12, 8))
        colors = ['#00ff88' if v > 0.05 else '#00aaff' if v > 0.02 else '#ffaa00' for v in values]
        bars = ax.barh(range(len(features)), values, color=colors)
        ax.set_yticks(range(len(features)))
        ax.set_yticklabels(features)
        ax.set_xlabel('Importância', fontsize=12)
        ax.set_title('Feature Importance - Random Forest Classifier', fontsize=14, fontweight='bold')
        ax.invert_yaxis()
        ax.grid(True, alpha=0.3, axis='x')

        plt.tight_layout()
        plt.savefig(os.path.join(output_dir, 'feature_importance.png'),
                    dpi=150, facecolor='#16213e', bbox_inches='tight')
        plt.close()
        print("    → Gráfico salvo")
    except ImportError:
        print("    [AVISO] matplotlib não disponível, pulando gráfico")


def main():
    parser = argparse.ArgumentParser(description='GreenRAN ML Training Pipeline')
    parser.add_argument('--db', default=DEFAULT_DB, help='Path to SQLite database')
    parser.add_argument('--output', default=DEFAULT_OUTPUT, help='Output directory for models')
    parser.add_argument('--retrain', action='store_true', help='Retrain with recent data only (last 24h)')
    parser.add_argument('--hours', type=int, default=24, help='Hours of data to use for retraining')
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    print("=" * 60)
    print("  GreenRAN ML Training Pipeline")
    if args.retrain:
        print(f"  Mode: RETRAIN (last {args.hours}h)")
    print("=" * 60)
    print()

    # Load data
    df = load_data(args.db, hours=args.hours if args.retrain else None)

    if len(df) < 50:
        print(f"[ERRO] Dataset muito pequeno ({len(df)} registros). Mínimo: 50")
        sys.exit(1)

    # Show class distribution
    print(f"\n    Distribuição de classes:")
    for cls, count in df['decision'].value_counts().items():
        print(f"      {cls}: {count} ({count/len(df)*100:.1f}%)")

    # Feature engineering (cria cvar_ms e outras features)
    df, feature_cols = engineer_features(df)
    
    # ============================================================
    # TARGET PREDITIVO - Criar target deslocado (t+1)
    # PARA ML preditiva, queremos predizer o próximo ciclo
    # DEVE ser feito DEPOIS de engineer_features (que cria cvar_ms)
    # ============================================================
    # decision_next = decisão do ciclo seguinte
    df['decision_next'] = df['decision'].shift(-1)
    df['cvar_next'] = df['cvar_ms'].shift(-1)  # CVaR do próximo ciclo (para regressor)
    
    # Remover últimas linhas sem target (shift(-1) cria NaN no final)
    df = df.dropna(subset=['decision_next'])
    
    print(f"\n    → Target preditivo: decision_next (predizer próximo ciclo)")
    print(f"    → Registros após shift: {len(df)}")

    # Train classifier
    clf_results, rf_clf, clf_scaler, le = train_classifier(df, feature_cols, args.output)
    clf_results['dataset_size'] = len(df)

    # Train regressor
    reg_results, rf_reg = train_regressor(df, feature_cols, args.output)

    # Save report with timestamp if retraining
    if args.retrain:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_path = os.path.join(args.output, f'training_report_{timestamp}.json')
    else:
        report_path = os.path.join(args.output, 'training_report.json')

    save_report(clf_results, reg_results, feature_cols, args.output)

    # Plot
    plot_feature_importance(clf_results, args.output)

    print()
    print("=" * 60)
    print("  TREINAMENTO CONCLUÍDO")
    print("=" * 60)
    print(f"  Modelos salvos em: {os.path.abspath(args.output)}/")
    print()
    print("  Arquivos gerados:")
    for f in sorted(os.listdir(args.output)):
        size = os.path.getsize(os.path.join(args.output, f))
        print(f"    {f} ({size/1024:.1f} KB)")
    print()


if __name__ == "__main__":
    main()
