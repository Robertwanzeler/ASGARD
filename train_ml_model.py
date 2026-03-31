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
import sqlite3
import argparse
import json
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

from sklearn.model_selection import train_test_split, cross_val_score
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


def load_data(db_path):
    """Load data from SQLite Data Lake."""
    print(f"[1/6] Carregando dados de {db_path}...")

    if not os.path.exists(db_path):
        print(f"[ERRO] Database não encontrado: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)

    # Load extended metrics joined with decisions
    query = """
        SELECT
            m.timestamp,
            m.sim_time_s,
            m.global_avg_latency_us,
            m.global_worst_latency_us,
            m.global_jitter_us,
            m.throughput_kbps,
            m.total_active_ues,
            m.total_active_cameras,
            m.total_critical_ues,
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

    # Cyclical encoding for hour
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)

    # Time-based features
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

    # CVaR zones
    df['cvar_zone'] = pd.cut(
        df['cvar_ms'],
        bins=[0, 60, 80, float('inf')],
        labels=[0, 1, 2]  # 0=green, 1=yellow, 2=red
    ).astype(int)

    feature_cols = [
        'cvar_ms',
        'cvar_diff',
        'cvar_rolling_mean',
        'cvar_rolling_std',
        'latency_p95_ms',
        'avg_latency_ms',
        'variance_ms2',
        'total_active_cameras',
        'total_active_ues',
        'camera_ratio',
        'total_critical_ues',
        'hour_sin',
        'hour_cos',
        'is_night',
        'is_weekend',
        'cvar_zone',
        'sim_time_s',
    ]

    print(f"    → {len(feature_cols)} features criadas")
    return df, feature_cols


def train_classifier(df, feature_cols, output_dir):
    """Train decision classifier."""
    print("[3/6] Treinando classificador de decisões...")

    # Prepare data
    X = df[feature_cols].values
    y = df['decision'].values

    # Encode labels
    le = LabelEncoder()
    y_encoded = le.fit_transform(y)

    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_encoded, test_size=0.2, random_state=42, stratify=y_encoded
    )

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

    # Cross-validation
    cv_scores = cross_val_score(rf, X_train_scaled, y_train, cv=5, scoring='accuracy')
    print(f"    Cross-validation: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    # Save
    joblib.dump(rf, os.path.join(output_dir, 'rf_classifier.joblib'))
    joblib.dump(scaler, os.path.join(output_dir, 'rf_scaler.joblib'))
    joblib.dump(le, os.path.join(output_dir, 'label_encoder.joblib'))

    results = {
        'rf_accuracy': rf_accuracy,
        'cv_mean': cv_scores.mean(),
        'cv_std': cv_scores.std(),
        'feature_importance': dict(zip(feature_cols, rf.feature_importances_.tolist())),
        'confusion_matrix': confusion_matrix(y_test, y_pred_rf).tolist(),
        'classes': le.classes_.tolist()
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
    """Train CVaR regressor."""
    print("[4/6] Treinando regressor de CVaR...")

    # Prepare data
    X = df[feature_cols].values
    y = df['cvar_ms'].values

    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

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
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    print("=" * 60)
    print("  GreenRAN ML Training Pipeline")
    print("=" * 60)
    print()

    # Load data
    df = load_data(args.db)

    if len(df) < 50:
        print(f"[ERRO] Dataset muito pequeno ({len(df)} registros). Mínimo: 50")
        sys.exit(1)

    # Feature engineering
    df, feature_cols = engineer_features(df)

    # Train classifier
    clf_results, rf_clf, clf_scaler, le = train_classifier(df, feature_cols, args.output)
    clf_results['dataset_size'] = len(df)

    # Train regressor
    reg_results, rf_reg = train_regressor(df, feature_cols, args.output)

    # Save report
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
