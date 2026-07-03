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
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor, HistGradientBoostingRegressor
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import (
    classification_report, confusion_matrix, accuracy_score,
    mean_absolute_error, mean_squared_error, r2_score
)
from sklearn.compose import TransformedTargetRegressor
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
DEFAULT_TRACE_JSONL = ""
KNOWN_SCENARIO_STAGES = [
    "allowed_bootstrap",
    "allowed_stable",
    "allowed_recovery",
    "camera_conditional",
    "camera_blocked",
    "vehicle_conditional",
    "vehicle_blocked",
    "app2_conditional",
    "app2_blocked",
]


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


def build_temporal_evaluation_windows(
    n_samples,
    test_fraction=TEMPORAL_TEST_FRACTION,
    max_windows=3,
    min_train_size=100,
):
    """Build expanding temporal evaluation windows."""
    if n_samples < 10:
        raise ValueError(f"Dataset muito pequeno para avaliação temporal: {n_samples}")

    test_size = max(int(n_samples * test_fraction), 1)
    max_possible_windows = max(1, (n_samples - min_train_size) // max(test_size, 1))
    window_count = min(max_windows, max_possible_windows)

    while window_count > 1 and (n_samples - test_size * window_count) < min_train_size:
        window_count -= 1

    if window_count <= 0:
        window_count = 1

    windows = []
    train_end = n_samples - test_size * window_count
    train_end = max(train_end, min_train_size)

    for index in range(window_count):
        test_end = train_end + test_size
        if index == window_count - 1 or test_end > n_samples:
            test_end = n_samples
        windows.append(
            {
                "index": index + 1,
                "train_end": int(train_end),
                "test_start": int(train_end),
                "test_end": int(test_end),
                "train_size": int(train_end),
                "test_size": int(test_end - train_end),
            }
        )
        train_end = test_end

    return windows


def _normalize_stage_name(value):
    stage = str(value or "").strip()
    return stage if stage in KNOWN_SCENARIO_STAGES else "unknown"


def summarize_temporal_window(df_window, feature_cols, decision_labels, cvar_targets):
    """Summarize and validate a temporal holdout window."""
    test_size = len(df_window)
    rounded_features = np.round(df_window[feature_cols].to_numpy(dtype=float, copy=False), 4)
    feature_rows = [tuple(row.tolist()) for row in rounded_features]
    unique_feature_rows = len(set(feature_rows))
    unique_feature_ratio = (unique_feature_rows / test_size) if test_size else 0.0

    row_to_labels = {}
    for row_key, label in zip(feature_rows, decision_labels):
        row_to_labels.setdefault(row_key, set()).add(str(label))
    conflicting_rows = sum(1 for labels in row_to_labels.values() if len(labels) > 1)
    conflicting_sample_count = sum(
        feature_rows.count(row_key) for row_key, labels in row_to_labels.items() if len(labels) > 1
    )
    conflicting_sample_ratio = (conflicting_sample_count / test_size) if test_size else 0.0

    cvar_targets = np.asarray(cvar_targets, dtype=float)
    cvar_target_variance = float(np.var(cvar_targets)) if len(cvar_targets) else float("nan")
    cvar_target_unique = len({round(float(value), 6) for value in cvar_targets.tolist()}) if len(cvar_targets) else 0

    stage_counts = (
        df_window["scenario_stage"].fillna("unknown").astype(str).value_counts().to_dict()
        if "scenario_stage" in df_window.columns else {}
    )
    decision_counts = pd.Series(decision_labels).value_counts().to_dict() if len(decision_labels) else {}

    flags = {
        "low_feature_diversity": bool(test_size and (unique_feature_rows <= 3 or unique_feature_ratio < 0.05)),
        "conflicting_duplicate_features": bool(conflicting_rows > 0 and conflicting_sample_ratio >= 0.10),
        "low_regression_target_variance": bool(len(cvar_targets) and (cvar_target_variance < 1e-9 or cvar_target_unique <= 1)),
    }

    reasons = []
    if flags["low_feature_diversity"]:
        reasons.append(
            f"holdout feature diversity too low: {unique_feature_rows}/{test_size} unique rows"
        )
    if flags["conflicting_duplicate_features"]:
        reasons.append(
            f"holdout has duplicated feature rows with conflicting labels: {conflicting_sample_count}/{test_size} samples"
        )
    if flags["low_regression_target_variance"]:
        reasons.append(
            f"holdout regression target variance too low: var={cvar_target_variance:.6g}, unique={cvar_target_unique}"
        )

    return {
        "mode": "temporal_holdout_with_guardrails",
        "valid": not reasons,
        "reasons": reasons,
        "test_size": int(test_size),
        "decision_counts": {str(k): int(v) for k, v in decision_counts.items()},
        "stage_counts": {str(k): int(v) for k, v in stage_counts.items()},
        "feature_uniqueness": {
            "unique_rows": int(unique_feature_rows),
            "unique_ratio": float(unique_feature_ratio),
            "conflicting_rows": int(conflicting_rows),
            "conflicting_sample_count": int(conflicting_sample_count),
            "conflicting_sample_ratio": float(conflicting_sample_ratio),
        },
        "regression_target": {
            "variance": cvar_target_variance,
            "unique_values": int(cvar_target_unique),
        },
        "degenerate_flags": flags,
    }


def build_evaluation_summary(df, feature_cols, evaluation_windows):
    """Summarize all temporal evaluation windows."""
    window_summaries = []
    decision_totals = {}
    stage_totals = {}
    reasons = []

    for window in evaluation_windows:
        test_slice = slice(window["test_start"], window["test_end"])
        window_df = df.iloc[test_slice].copy()
        summary = summarize_temporal_window(
            window_df,
            feature_cols,
            window_df["decision"].values,
            window_df["cvar_ms"].values,
        )
        summary.update(
            {
                "index": window["index"],
                "train_size": window["train_size"],
                "test_start": window["test_start"],
                "test_end": window["test_end"],
                "timestamp_start": int(window_df["timestamp"].iloc[0]) if len(window_df) else 0,
                "timestamp_end": int(window_df["timestamp"].iloc[-1]) if len(window_df) else 0,
            }
        )
        if not summary["valid"]:
            reasons.extend(f"window {window['index']}: {reason}" for reason in summary["reasons"])
        for key, value in summary["decision_counts"].items():
            decision_totals[key] = decision_totals.get(key, 0) + int(value)
        for key, value in summary["stage_counts"].items():
            stage_totals[key] = stage_totals.get(key, 0) + int(value)
        window_summaries.append(summary)

    return {
        "mode": "rolling_temporal_windows",
        "valid": not reasons,
        "reasons": reasons,
        "window_count": len(window_summaries),
        "decision_counts": decision_totals,
        "stage_counts": stage_totals,
        "windows": window_summaries,
    }


def build_classifier_sample_weights(train_labels, cvar_train):
    sample_weights = np.ones(len(train_labels), dtype=float)
    healthy_mask = cvar_train <= 10.0
    near_healthy_mask = (cvar_train > 10.0) & (cvar_train <= 20.0)
    sample_weights[healthy_mask & (train_labels == 'ALLOWED')] = 4.0
    sample_weights[near_healthy_mask & (train_labels == 'ALLOWED')] = 2.5
    sample_weights[healthy_mask & (train_labels == 'CONDITIONAL')] = 0.55
    sample_weights[healthy_mask & (train_labels == 'BLOCKED')] = 0.75
    sample_weights[(cvar_train >= 80.0) & (train_labels == 'BLOCKED')] = 1.75
    return sample_weights


def compute_classifier_health_metrics(y_true_encoded, y_pred_encoded, cvar_values, label_encoder):
    healthy_mask = cvar_values <= 20.0
    healthy_accuracy = (
        accuracy_score(y_true_encoded[healthy_mask], y_pred_encoded[healthy_mask])
        if np.any(healthy_mask) else float("nan")
    )
    healthy_allowed_recall = float("nan")
    if np.any(healthy_mask):
        healthy_true = label_encoder.inverse_transform(y_true_encoded[healthy_mask])
        healthy_pred = label_encoder.inverse_transform(y_pred_encoded[healthy_mask])
        allowed_mask = healthy_true == 'ALLOWED'
        if np.any(allowed_mask):
            healthy_allowed_recall = float(np.mean(healthy_pred[allowed_mask] == 'ALLOWED'))
    return healthy_accuracy, healthy_allowed_recall


def evaluate_classifier_candidate(model_builder, X, y_encoded, cvar_values, label_encoder, evaluation_windows):
    y_true_all = []
    y_pred_all = []
    cvar_all = []

    for window in evaluation_windows:
        test_slice = slice(window["test_start"], window["test_end"])
        X_train = X[:window["train_end"]]
        X_test = X[test_slice]
        y_train = y_encoded[:window["train_end"]]
        y_test = y_encoded[test_slice]
        cvar_train = cvar_values[:window["train_end"]]
        cvar_test = cvar_values[test_slice]

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        model = model_builder()
        train_labels = label_encoder.inverse_transform(y_train)
        sample_weights = build_classifier_sample_weights(train_labels, cvar_train)
        model.fit(X_train_scaled, y_train, sample_weight=sample_weights)
        y_pred = model.predict(X_test_scaled)

        y_true_all.append(y_test)
        y_pred_all.append(y_pred)
        cvar_all.append(cvar_test)

    y_true = np.concatenate(y_true_all)
    y_pred = np.concatenate(y_pred_all)
    cvar_eval = np.concatenate(cvar_all)
    label_ids = list(range(len(label_encoder.classes_)))
    healthy_accuracy, healthy_allowed_recall = compute_classifier_health_metrics(
        y_true,
        y_pred,
        cvar_eval,
        label_encoder,
    )

    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "healthy_accuracy": healthy_accuracy,
        "healthy_allowed_recall": healthy_allowed_recall,
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=label_ids).tolist(),
        "classes": label_encoder.classes_.tolist(),
    }


def build_regression_sample_weights(y_train):
    train_weights = np.ones_like(y_train, dtype=float)
    train_weights[y_train <= 10.0] = 5.0
    train_weights[(y_train > 10.0) & (y_train <= 20.0)] = 3.5
    train_weights[(y_train > 20.0) & (y_train <= 40.0)] = 2.0
    train_weights[y_train >= 150.0] = 1.2
    return train_weights


def evaluate_regressor_candidate(model_builder, X, y, evaluation_windows):
    y_true_all = []
    y_pred_all = []

    for window in evaluation_windows:
        test_slice = slice(window["test_start"], window["test_end"])
        X_train = X[:window["train_end"]]
        X_test = X[test_slice]
        y_train = y[:window["train_end"]]
        y_test = y[test_slice]

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        model = model_builder()
        train_weights = build_regression_sample_weights(y_train)
        model.fit(X_train_scaled, y_train, sample_weight=train_weights)
        y_pred = model.predict(X_test_scaled)

        y_true_all.append(y_test)
        y_pred_all.append(y_pred)

    y_true = np.concatenate(y_true_all)
    y_pred = np.concatenate(y_pred_all)
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    r2 = r2_score(y_true, y_pred)
    healthy_mask = y_true <= 20.0
    critical_mask = y_true >= 80.0

    return {
        "mae": float(mae),
        "rmse": float(rmse),
        "r2": float(r2),
        "healthy_mae": mean_absolute_error(y_true[healthy_mask], y_pred[healthy_mask]) if np.any(healthy_mask) else float("nan"),
        "healthy_bias": float(np.mean(y_pred[healthy_mask] - y_true[healthy_mask])) if np.any(healthy_mask) else float("nan"),
        "critical_mae": mean_absolute_error(y_true[critical_mask], y_pred[critical_mask]) if np.any(critical_mask) else float("nan"),
    }


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


def load_trace_data(trace_jsonl):
    """Load training rows from exported TA-SAM/rApp transition traces."""
    print(f"[1/6] Carregando trace de {trace_jsonl}...")

    trace_path = Path(trace_jsonl)
    if not trace_path.exists():
        print(f"[ERRO] Trace JSONL não encontrado: {trace_jsonl}")
        sys.exit(1)

    rows = []
    with trace_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            metrics = payload.get("metrics") or {}
            decision = payload.get("decision") or {}
            rows.append({
                "timestamp": int(payload.get("timestamp") or 0),
                "scenario_stage": _normalize_stage_name(payload.get("scenario_stage")),
                "sim_time_s": float(metrics.get("sim_time_s", 0.0) or 0.0),
                "global_avg_latency_us": float(
                    metrics.get("global_avg_latency_us", metrics.get("latency_p95_us", 0.0)) or 0.0
                ),
                "global_worst_latency_us": float(
                    metrics.get("global_worst_latency_us", metrics.get("latency_p95_us", 0.0)) or 0.0
                ),
                "global_jitter_us": float(metrics.get("global_jitter_us", 0.0) or 0.0),
                "global_packet_loss_rate": float(metrics.get("global_packet_loss_rate", 0.0) or 0.0),
                "throughput_kbps": float(metrics.get("throughput_kbps", 0.0) or 0.0),
                "total_active_ues": int(metrics.get("total_active_ues", 0) or 0),
                "total_active_cameras": int(metrics.get("total_active_cameras", 0) or 0),
                "total_critical_ues": int(metrics.get("total_critical_ues", 0) or 0),
                "total_tx_bytes": float(metrics.get("total_tx_bytes", 0.0) or 0.0),
                "total_rx_bytes": float(metrics.get("total_rx_bytes", 0.0) or 0.0),
                "cvar_per_ue_us": float(metrics.get("cvar_per_ue_us", 0.0) or 0.0),
                "variance_per_ue_us2": float(metrics.get("variance_per_ue_us2", 0.0) or 0.0),
                "latency_p95_per_ue_us": float(
                    metrics.get("latency_p95_per_ue_us", metrics.get("latency_p95_us", 0.0)) or 0.0
                ),
                "latency_p95_us": float(metrics.get("latency_p95_us", 0.0) or 0.0),
                "decision": str(decision.get("decision") or ""),
                "energy_state": str(decision.get("energy_state") or ""),
                "confidence": float(decision.get("confidence", 0.0) or 0.0),
                "reason": str(decision.get("reason") or ""),
            })

    df = pd.DataFrame(rows)
    print(f"    → {len(df)} registros carregados")
    return df


def normalize_training_frame(df):
    """Ensure expected columns exist even for reduced trace-based datasets."""
    defaults = {
        "timestamp": 0,
        "scenario_stage": "unknown",
        "sim_time_s": 0.0,
        "global_avg_latency_us": 0.0,
        "global_worst_latency_us": 0.0,
        "global_jitter_us": 0.0,
        "global_packet_loss_rate": 0.0,
        "throughput_kbps": 0.0,
        "total_active_ues": 0,
        "total_active_cameras": 0,
        "total_critical_ues": 0,
        "total_tx_bytes": 0.0,
        "total_rx_bytes": 0.0,
        "cvar_per_ue_us": 0.0,
        "variance_per_ue_us2": 0.0,
        "latency_p95_per_ue_us": 0.0,
        "latency_p95_us": 0.0,
        "decision": "",
        "energy_state": "",
        "confidence": 0.0,
        "reason": "",
    }
    for column, default in defaults.items():
        if column not in df.columns:
            df[column] = default
    return df


def engineer_features(df):
    """Create derived features for ML."""
    print("[2/6] Feature Engineering...")
    df = normalize_training_frame(df.copy())

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
    # LAG FEATURES - alinhadas ao runtime causal do rApp
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

    # Contexto do cenário: ajuda a separar estágios onde as métricas observáveis
    # ficam quase iguais, mas a política desejada muda por aplicação.
    df['scenario_stage'] = df['scenario_stage'].map(_normalize_stage_name)
    for stage_name in KNOWN_SCENARIO_STAGES:
        df[f'stage_{stage_name}'] = (df['scenario_stage'] == stage_name).astype(int)
    df['stage_unknown'] = (df['scenario_stage'] == 'unknown').astype(int)

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
        # Contexto de estágio (10)
        'stage_allowed_bootstrap',
        'stage_allowed_stable',
        'stage_allowed_recovery',
        'stage_camera_conditional',
        'stage_camera_blocked',
        'stage_vehicle_conditional',
        'stage_vehicle_blocked',
        'stage_app2_conditional',
        'stage_app2_blocked',
        'stage_unknown',
    ]

    print(f"    → {len(feature_cols)} features criadas")
    return df, feature_cols


def train_classifier(df, feature_cols, output_dir, evaluation_windows, evaluation_summary):
    """Train decision classifier on the current observed state."""
    print("[3/6] Treinando classificador de decisões...")
    print("       → Target: decision (estado atual)")

    X = df[feature_cols].values
    y = df['decision'].values
    cvar_values = df['cvar_ms'].values

    # Encode labels
    le = LabelEncoder()
    y_encoded = le.fit_transform(y)

    cv_mean, cv_std = temporal_cv_accuracy(X, y_encoded, n_splits=5)
    print(f"    Cross-validation temporal: {cv_mean:.4f} ± {cv_std:.4f}")

    rf_builder = lambda: RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_split=5,
        min_samples_leaf=2,
        class_weight=None,
        random_state=42,
        n_jobs=-1
    )
    rf_eval = evaluate_classifier_candidate(rf_builder, X, y_encoded, cvar_values, le, evaluation_windows)
    rf_accuracy = rf_eval["accuracy"]
    healthy_accuracy = rf_eval["healthy_accuracy"]
    healthy_allowed_recall = rf_eval["healthy_allowed_recall"]

    print(f"    Random Forest Accuracy: {rf_accuracy:.4f}")
    print(f"    Healthy Accuracy (<=20ms): {healthy_accuracy:.4f}")
    print(f"    Healthy ALLOWED recall: {healthy_allowed_recall:.4f}")

    results = {
        'rf_accuracy': rf_accuracy,
        'healthy_accuracy': healthy_accuracy,
        'healthy_allowed_recall': healthy_allowed_recall,
        'cv_mean': cv_mean,
        'cv_std': cv_std,
        'feature_importance': {},
        'confusion_matrix': rf_eval['confusion_matrix'],
        'classes': rf_eval['classes'],
        'split_mode': 'rolling_temporal_windows',
        'evaluation': evaluation_summary,
    }

    best_name = 'random_forest'
    best_score = (
        healthy_allowed_recall if not np.isnan(healthy_allowed_recall) else -1.0,
        healthy_accuracy if not np.isnan(healthy_accuracy) else -1.0,
        rf_accuracy,
    )

    xgb_path = os.path.join(output_dir, 'xgb_classifier.joblib')

    # XGBoost if available
    if HAS_XGBOOST:
        print("    Treinando XGBoost...")
        xgb_builder = lambda: XGBClassifier(
            n_estimators=100,
            max_depth=6,
            learning_rate=0.1,
            random_state=42,
            n_jobs=-1,
            eval_metric='mlogloss'
        )
        xgb_eval = evaluate_classifier_candidate(xgb_builder, X, y_encoded, cvar_values, le, evaluation_windows)
        xgb_accuracy = xgb_eval["accuracy"]
        xgb_healthy_accuracy = xgb_eval["healthy_accuracy"]
        xgb_healthy_allowed_recall = xgb_eval["healthy_allowed_recall"]
        print(f"    XGBoost Accuracy: {xgb_accuracy:.4f}")
        print(f"    XGBoost Healthy Accuracy (<=20ms): {xgb_healthy_accuracy:.4f}")
        print(f"    XGBoost Healthy ALLOWED recall: {xgb_healthy_allowed_recall:.4f}")

        results['xgb_accuracy'] = xgb_accuracy
        results['xgb_healthy_accuracy'] = xgb_healthy_accuracy
        results['xgb_healthy_allowed_recall'] = xgb_healthy_allowed_recall

        xgb_score = (
            xgb_healthy_allowed_recall if not np.isnan(xgb_healthy_allowed_recall) else -1.0,
            xgb_healthy_accuracy if not np.isnan(xgb_healthy_accuracy) else -1.0,
            xgb_accuracy,
        )
        if xgb_score > best_score:
            best_name = 'xgboost'
            best_score = xgb_score
    elif os.path.exists(xgb_path):
        os.remove(xgb_path)
        print("    XGBoost indisponível: removendo xgb_classifier.joblib antigo")

    # Fit runtime models on the full dataset after evaluation.
    scaler = StandardScaler()
    X_scaled_full = scaler.fit_transform(X)
    full_train_labels = le.inverse_transform(y_encoded)
    full_sample_weights = build_classifier_sample_weights(full_train_labels, cvar_values)
    rf = rf_builder()
    rf.fit(X_scaled_full, y_encoded, sample_weight=full_sample_weights)
    results['feature_importance'] = dict(zip(feature_cols, rf.feature_importances_.tolist()))

    if HAS_XGBOOST:
        xgb = xgb_builder()
        xgb.fit(X_scaled_full, y_encoded, sample_weight=full_sample_weights)
        joblib.dump(xgb, xgb_path)
        if best_name == 'xgboost':
            best_model = xgb
            best_scaler = scaler
    else:
        best_model = rf
        best_scaler = scaler

    # Save chosen classifier for runtime
    joblib.dump(best_model, os.path.join(output_dir, 'best_classifier.joblib'))
    joblib.dump(rf, os.path.join(output_dir, 'rf_classifier.joblib'))
    joblib.dump(best_scaler, os.path.join(output_dir, 'rf_scaler.joblib'))
    joblib.dump(le, os.path.join(output_dir, 'label_encoder.joblib'))
    results['selected_classifier'] = best_name
    print(f"    Classificador selecionado para runtime: {best_name}")

    return results, rf, scaler, le


def train_regressor(df, feature_cols, output_dir, evaluation_windows):
    """Train CVaR regressor on the current observed state."""
    print("[4/6] Treinando regressor de CVaR...")
    print("       → Target: cvar_ms (estado atual)")

    X = df[feature_cols].values
    y = df['cvar_ms'].values

    reg_builder = lambda: TransformedTargetRegressor(
        regressor=HistGradientBoostingRegressor(
            loss='squared_error',
            learning_rate=0.05,
            max_iter=300,
            max_depth=8,
            min_samples_leaf=20,
            l2_regularization=0.1,
            random_state=42
        ),
        func=np.log1p,
        inverse_func=np.expm1
    )
    eval_metrics = evaluate_regressor_candidate(reg_builder, X, y, evaluation_windows)
    mae = eval_metrics["mae"]
    rmse = eval_metrics["rmse"]
    r2 = eval_metrics["r2"]
    healthy_mae = eval_metrics["healthy_mae"]
    healthy_bias = eval_metrics["healthy_bias"]
    critical_mae = eval_metrics["critical_mae"]

    print(f"    MAE: {mae:.2f} ms")
    print(f"    RMSE: {rmse:.2f} ms")
    print(f"    R²: {r2:.4f}")
    print(f"    Healthy MAE (<=20ms): {healthy_mae:.2f} ms")
    print(f"    Healthy bias (pred-real): {healthy_bias:.2f} ms")
    print(f"    Critical MAE (>=80ms): {critical_mae:.2f} ms")

    # Fit runtime regressor on the full dataset after evaluation.
    scaler = StandardScaler()
    X_scaled_full = scaler.fit_transform(X)
    train_weights = build_regression_sample_weights(y)
    rf_reg = reg_builder()
    rf_reg.fit(X_scaled_full, y, sample_weight=train_weights)

    # Save
    joblib.dump(rf_reg, os.path.join(output_dir, 'rf_regressor.joblib'))
    joblib.dump(scaler, os.path.join(output_dir, 'reg_scaler.joblib'))

    return {
        'mae': mae,
        'rmse': rmse,
        'r2': r2,
        'healthy_mae': healthy_mae,
        'healthy_bias': healthy_bias,
        'critical_mae': critical_mae,
        'model_name': 'HistGradientBoostingRegressor+log1p'
    }, rf_reg


def save_report(clf_results, reg_results, feature_cols, output_dir):
    """Save training report."""
    print("[5/6] Salvando relatório...")

    report = {
        'timestamp': datetime.now().isoformat(),
        'dataset_size': clf_results.get('dataset_size', 0),
        'features': feature_cols,
        'evaluation': clf_results.get('evaluation', {}),
        'classifier': {
            'selected_classifier': clf_results.get('selected_classifier', 'random_forest'),
            'random_forest_accuracy': clf_results['rf_accuracy'],
            'cross_validation_mean': clf_results['cv_mean'],
            'cross_validation_std': clf_results['cv_std'],
            'confusion_matrix': clf_results['confusion_matrix'],
            'classes': clf_results['classes'],
        },
        'regressor': {
            'model': reg_results.get('model_name', 'unknown'),
            'mae_ms': reg_results['mae'],
            'rmse_ms': reg_results['rmse'],
            'r2': reg_results['r2'],
            'healthy_mae_ms': reg_results.get('healthy_mae'),
            'healthy_bias_ms': reg_results.get('healthy_bias'),
            'critical_mae_ms': reg_results.get('critical_mae'),
        },
        'feature_importance': clf_results['feature_importance']
    }

    if 'xgb_accuracy' in clf_results:
        report['classifier']['xgboost_accuracy'] = clf_results['xgb_accuracy']
        report['classifier']['xgb_healthy_accuracy'] = clf_results.get('xgb_healthy_accuracy')
        report['classifier']['xgb_healthy_allowed_recall'] = clf_results.get('xgb_healthy_allowed_recall')
    report['classifier']['healthy_accuracy'] = clf_results.get('healthy_accuracy')
    report['classifier']['healthy_allowed_recall'] = clf_results.get('healthy_allowed_recall')

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

        evaluation = clf_results.get('evaluation', {})
        f.write("EVALUATION WINDOW\n")
        f.write("-" * 40 + "\n")
        f.write(f"  Mode: {evaluation.get('mode', 'unknown')}\n")
        f.write(f"  Valid: {evaluation.get('valid', True)}\n")
        f.write(f"  Window count: {evaluation.get('window_count', 0)}\n")
        if evaluation.get('reasons'):
            for reason in evaluation.get('reasons', []):
                f.write(f"  Reason: {reason}\n")
        if evaluation.get('decision_counts'):
            f.write(f"  Test decisions: {evaluation.get('decision_counts')}\n")
        if evaluation.get('stage_counts'):
            f.write(f"  Test stages: {evaluation.get('stage_counts')}\n")
        f.write("\n")

        f.write("CLASSIFIER (Decisão rApp)\n")
        f.write("-" * 40 + "\n")
        f.write(f"  Selecionado para runtime: {clf_results.get('selected_classifier', 'random_forest')}\n")
        f.write(f"  Random Forest Accuracy: {clf_results['rf_accuracy']:.4f}\n")
        f.write(f"  Healthy Accuracy (<=20ms): {clf_results.get('healthy_accuracy', float('nan')):.4f}\n")
        f.write(f"  Healthy ALLOWED recall: {clf_results.get('healthy_allowed_recall', float('nan')):.4f}\n")
        if 'xgb_accuracy' in clf_results:
            f.write(f"  XGBoost Accuracy: {clf_results['xgb_accuracy']:.4f}\n")
            f.write(f"  XGBoost Healthy Accuracy (<=20ms): {clf_results.get('xgb_healthy_accuracy', float('nan')):.4f}\n")
            f.write(f"  XGBoost Healthy ALLOWED recall: {clf_results.get('xgb_healthy_allowed_recall', float('nan')):.4f}\n")
        f.write(f"  Cross-validation: {clf_results['cv_mean']:.4f} ± {clf_results['cv_std']:.4f}\n")
        f.write(f"  Classes: {clf_results['classes']}\n\n")

        f.write("REGRESSOR (CVaR prediction)\n")
        f.write("-" * 40 + "\n")
        f.write(f"  Modelo: {reg_results.get('model_name', 'unknown')}\n")
        f.write(f"  MAE: {reg_results['mae']:.2f} ms\n")
        f.write(f"  RMSE: {reg_results['rmse']:.2f} ms\n")
        f.write(f"  R²: {reg_results['r2']:.4f}\n\n")
        f.write(f"  Healthy MAE (<=20ms): {reg_results.get('healthy_mae', float('nan')):.2f} ms\n")
        f.write(f"  Healthy bias (pred-real): {reg_results.get('healthy_bias', float('nan')):.2f} ms\n")
        f.write(f"  Critical MAE (>=80ms): {reg_results.get('critical_mae', float('nan')):.2f} ms\n\n")

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
    parser.add_argument('--trace-jsonl', default=DEFAULT_TRACE_JSONL, help='Optional exported trace JSONL as training source')
    parser.add_argument('--output', default=DEFAULT_OUTPUT, help='Output directory for models')
    parser.add_argument('--retrain', action='store_true', help='Retrain with recent data only (last 24h)')
    parser.add_argument('--hours', type=int, default=24, help='Hours of data to use for retraining')
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    print("=" * 60)
    print("  GreenRAN ML Training Pipeline")
    if args.trace_jsonl:
        print("  Source: TRACE_JSONL")
    if args.retrain:
        print(f"  Mode: RETRAIN (last {args.hours}h)")
    print("=" * 60)
    print()

    # Load data
    if args.trace_jsonl:
        df = load_trace_data(args.trace_jsonl)
    else:
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

    print(f"\n    → Target de treino: estado atual da rede")
    print(f"    → Registros utilizáveis: {len(df)}")
    evaluation_windows = build_temporal_evaluation_windows(len(df))
    evaluation_summary = build_evaluation_summary(df, feature_cols, evaluation_windows)

    # Train classifier
    clf_results, rf_clf, clf_scaler, le = train_classifier(
        df,
        feature_cols,
        args.output,
        evaluation_windows,
        evaluation_summary,
    )
    clf_results['dataset_size'] = len(df)

    # Train regressor
    reg_results, rf_reg = train_regressor(df, feature_cols, args.output, evaluation_windows)

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
