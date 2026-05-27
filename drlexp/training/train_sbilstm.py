#!/usr/bin/env python3
"""
SBiLSTM Training Script
=======================
Training script for Stacked Bidirectional LSTM (SBiLSTM).
Based on IEEE paper for large time-scale resource prediction.

This script:
1. Loads data from GreenRAN Data Lake
2. Prepares sequences for prediction window
3. Trains SBiLSTM to predict next PW resources
4. Validates and compares with RF Regressor

Author: GreenRAN Team - UFPA
"""

import os
import sys
import argparse
import json
import yaml
import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import matplotlib.pyplot as plt
import seaborn as sns

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from drl.models.sbilstm import SBiLSTM, create_sbilstm_model


def write_json(path: str, payload: dict | list) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=True)
        f.write('\n')


# Default configuration
DEFAULT_CONFIG = {
    'input_size': 18,
    'hidden_size_1': 128,
    'hidden_size_2': 64,
    'num_layers': 2,
    'dropout': 0.2,
    'output_size': 1,
    'learning_rate': 0.0005,
    'batch_size': 32,
    'epochs': 600,
    'prediction_window': 100,
    'db_path': '/tmp/rapp_data_lake.db',
    'model_dir': './models/sbilstm',
    'test_size': 0.2,
    'val_size': 0.1
}


class GreenRANDataset(Dataset):
    """Dataset for GreenRAN resource prediction."""
    
    def __init__(self, sequences, targets):
        """
        Initialize dataset.
        
        Args:
            sequences: Input sequences (samples, seq_len, features)
            targets: Target values (samples,)
        """
        self.sequences = torch.FloatTensor(sequences)
        self.targets = torch.FloatTensor(targets)
        
    def __len__(self):
        return len(self.sequences)
    
    def __getitem__(self, idx):
        return self.sequences[idx], self.targets[idx]


def load_data(db_path: str, prediction_window: int = 100) -> pd.DataFrame:
    """
    Load and preprocess data from GreenRAN Data Lake.
    
    Args:
        db_path: Path to SQLite database
        prediction_window: Size of prediction window
        
    Returns:
        DataFrame with processed features
    """
    print(f"[SBiLSTM] Loading data from {db_path}...")
    
    conn = sqlite3.connect(db_path)
    
    query = """
        SELECT 
            m.timestamp,
            m.sim_time_s,
            m.cvar_per_ue_us,
            m.latency_p95_per_ue_us,
            m.global_avg_latency_us,
            m.global_jitter_us,
            m.global_packet_loss_rate,
            m.throughput_kbps,
            m.total_active_ues,
            m.total_active_cameras,
            m.total_critical_ues,
            m.total_tx_bytes,
            m.total_rx_bytes,
            m.variance_per_ue_us2,
            d.decision
        FROM extended_metrics m
        JOIN decisions_history d ON m.timestamp = d.timestamp
        WHERE m.cvar_per_ue_us > 0
        ORDER BY m.timestamp
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    print(f"[SBiLSTM] Loaded {len(df)} records")
    
    # Feature engineering
    print("[SBiLSTM] Engineering features...")
    
    # Basic conversions
    df['cvar_ms'] = df['cvar_per_ue_us'] / 1000.0
    df['latency_p95_ms'] = df['latency_p95_per_ue_us'] / 1000.0
    df['avg_latency_ms'] = df['global_avg_latency_us'] / 1000.0
    df['jitter_ms'] = df['global_jitter_us'] / 1000.0
    df['packet_loss_pct'] = df['global_packet_loss_rate'] * 100
    df['throughput_mbps'] = df['throughput_kbps'] / 1000.0
    df['variance_ms2'] = df['variance_per_ue_us2'] / 1_000_000.0
    
    # Derived features
    df['camera_ratio'] = df['total_active_cameras'] / df['total_active_ues'].clip(lower=1)
    df['critical_ue_ratio'] = df['total_critical_ues'] / df['total_active_ues'].clip(lower=1)
    df['tx_rx_ratio'] = df['total_tx_bytes'] / df['total_rx_bytes'].clip(lower=1)
    
    # Time features
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
    df['hour'] = df['datetime'].dt.hour
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    
    # Trend features
    df['cvar_diff'] = df['cvar_ms'].diff().fillna(0)
    df['latency_diff'] = df['avg_latency_ms'].diff().fillna(0)
    df['cvar_trend'] = df['cvar_ms'].diff(5).fillna(0)
    
    # Rolling features
    df['cvar_rolling_mean'] = df['cvar_ms'].rolling(5, min_periods=1).mean()
    df['cvar_rolling_std'] = df['cvar_ms'].rolling(5, min_periods=1).std().fillna(0)
    df['cvar_rolling_3'] = df['cvar_ms'].rolling(3, min_periods=1).mean()
    df['cvar_rolling_10'] = df['cvar_ms'].rolling(10, min_periods=1).mean()
    df['cvar_rolling_std_3'] = df['cvar_ms'].rolling(3, min_periods=1).std().fillna(0)
    
    # Acceleration
    df['cvar_acceleration'] = df['cvar_diff'].diff().fillna(0)
    df['latency_acceleration'] = df['latency_diff'].diff().fillna(0)
    
    # Other features
    df['jitter_trend'] = df['jitter_ms'].diff().fillna(0)
    df['cvar_momentum'] = df['cvar_acceleration'].diff().fillna(0)
    
    # Energy history
    df['energy_history'] = (df['decision'] == 'BLOCKED').astype(float)
    df['energy_history'] = df['energy_history'].rolling(5, min_periods=1).mean()
    
    # CVaR zone
    df['cvar_zone'] = pd.cut(df['cvar_ms'], bins=[0, 60, 80, float('inf')], labels=[0, 1, 2]).astype(int)
    
    print(f"[SBiLSTM] Features engineered: {len(df.columns)} columns")
    
    return df


def create_sequences(df: pd.DataFrame, feature_cols: list, target_col: str, 
                    prediction_window: int = 100) -> tuple:
    """
    Create sequences for SBiLSTM training.
    
    Args:
        df: DataFrame with features
        feature_cols: List of feature column names
        target_col: Target column name
        prediction_window: Size of sequence window
        
    Returns:
        Tuple of (sequences, targets)
    """
    print(f"[SBiLSTM] Creating sequences with PW={prediction_window}...")
    
    sequences = []
    targets = []
    
    for i in range(len(df) - prediction_window):
        # Sequence: past PW time steps
        seq = df[feature_cols].iloc[i:i + prediction_window].values
        # Target: CVaR at next time step
        target = df[target_col].iloc[i + prediction_window]
        
        sequences.append(seq)
        targets.append(target)
    
    sequences = np.array(sequences)
    targets = np.array(targets)
    
    print(f"[SBiLSTM] Created {len(sequences)} sequences")
    print(f"[SBiLSTM] Sequence shape: {sequences.shape}")
    print(f"[SBiLSTM] Target shape: {targets.shape}")
    
    return sequences, targets


def train_sbilstm(config: dict = None):
    """
    Train SBiLSTM model.
    
    Args:
        config: Configuration dictionary
    """
    config = config or DEFAULT_CONFIG
    
    print("=" * 60)
    print("SBiLSTM Training - GreenRAN Resource Prediction")
    print("=" * 60)
    print(f"Prediction Window: {config['prediction_window']}")
    print(f"Hidden Layers: {config['hidden_size_1']} -> {config['hidden_size_2']}")
    print(f"Epochs: {config['epochs']}, Batch: {config['batch_size']}")
    print()
    
    # Create model directory
    os.makedirs(config['model_dir'], exist_ok=True)
    
    # Load data
    df = load_data(config['db_path'], config['prediction_window'])
    
    # Define features (18 features matching state space)
    feature_cols = [
        'cvar_ms', 'cvar_trend', 'cvar_acceleration',
        'latency_p95_ms', 'jitter_ms', 'packet_loss_pct', 'throughput_mbps',
        'total_active_ues', 'total_active_cameras', 'total_critical_ues',
        'camera_ratio', 'critical_ue_ratio', 'allocated_rbs',
        'current_power', 'power_budget', 'hour_sin', 'hour_cos', 'variance_ms2'
    ]
    
    # Add missing features
    df['allocated_rbs'] = df['total_active_ues'] * 10
    df['current_power'] = 20.0  # Placeholder
    df['power_budget'] = 30.0  # Placeholder
    
    target_col = 'cvar_ms'
    
    # Create sequences
    sequences, targets = create_sequences(
        df, feature_cols, target_col, config['prediction_window']
    )
    
    # Train/val/test split
    X_train_val, X_test, y_train_val, y_test = train_test_split(
        sequences, targets, test_size=config['test_size'], random_state=42
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_train_val, y_train_val, test_size=config['val_size'], random_state=42
    )
    
    print(f"\nData split:")
    print(f"  Train: {len(X_train)} sequences")
    print(f"  Val:   {len(X_val)} sequences")
    print(f"  Test:  {len(X_test)} sequences")
    
    # Create datasets
    train_dataset = GreenRANDataset(X_train, y_train)
    val_dataset = GreenRANDataset(X_val, y_val)
    test_dataset = GreenRANDataset(X_test, y_test)
    
    train_loader = DataLoader(train_dataset, batch_size=config['batch_size'], shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=config['batch_size'])
    test_loader = DataLoader(test_dataset, batch_size=config['batch_size'])
    
    # Create model
    model = create_sbilstm_model({
        'input_size': config['input_size'],
        'hidden_size_1': config['hidden_size_1'],
        'hidden_size_2': config['hidden_size_2'],
        'num_layers': config['num_layers'],
        'dropout': config['dropout'],
        'output_size': config['output_size']
    })
    
    print(f"\nModel architecture:")
    print(model)
    
    # Loss and optimizer
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=config['learning_rate'])
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='min', patience=5, factor=0.5
    )
    
    # Training loop
    print("\n" + "=" * 60)
    print("Training...")
    print("=" * 60)
    
    best_val_loss = float('inf')
    patience = 20
    patience_counter = 0
    training_history = []
    
    for epoch in range(config['epochs']):
        # Training
        model.train()
        train_loss = 0.0
        
        for batch_x, batch_y in train_loader:
            optimizer.zero_grad()
            
            output = model.predict(batch_x)
            loss = criterion(output.squeeze(), batch_y)
            
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            
            train_loss += loss.item()
        
        train_loss /= len(train_loader)
        
        # Validation
        model.eval()
        val_loss = 0.0
        
        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                output = model.predict(batch_x)
                loss = criterion(output.squeeze(), batch_y)
                val_loss += loss.item()
        
        val_loss /= len(val_loader)
        
        # Update scheduler
        scheduler.step(val_loss)
        
        # Early stopping check
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            
            # Save best model
            torch.save(model.state_dict(), os.path.join(config['model_dir'], 'best_model.pt'))
        else:
            patience_counter += 1
            
        # Logging
        current_lr = float(optimizer.param_groups[0]['lr'])
        training_history.append({
            'epoch': epoch + 1,
            'train_loss': float(train_loss),
            'val_loss': float(val_loss),
            'best_val_loss': float(best_val_loss),
            'learning_rate': current_lr,
            'patience_counter': int(patience_counter),
        })
        if epoch % 5 == 0:
            print(f"Epoch {epoch+1}/{config['epochs']}: "
                  f"Train Loss={train_loss:.6f}, Val Loss={val_loss:.6f}")
            
        if patience_counter >= patience:
            print(f"\nEarly stopping at epoch {epoch+1}")
            break
    
    # Load best model
    model.load_state_dict(torch.load(os.path.join(config['model_dir'], 'best_model.pt')))
    
    # Evaluation on test set
    print("\n" + "=" * 60)
    print("Test Set Evaluation")
    print("=" * 60)
    
    model.eval()
    predictions = []
    actuals = []
    
    with torch.no_grad():
        for batch_x, batch_y in test_loader:
            output = model.predict(batch_x)
            predictions.extend(output.squeeze().numpy())
            actuals.extend(batch_y.numpy())
    
    predictions = np.array(predictions)
    actuals = np.array(actuals)
    
    # Metrics
    mse = mean_squared_error(actuals, predictions)
    mae = mean_absolute_error(actuals, predictions)
    r2 = r2_score(actuals, predictions)
    
    print(f"MSE:  {mse:.6f}")
    print(f"MAE:  {mae:.4f} ms")
    print(f"R²:   {r2:.4f}")
    
    # Compare with RF baseline
    print("\n" + "=" * 60)
    print("Comparison with Random Forest (baseline)")
    print("=" * 60)
    print(f"RF MAE: 0.58 ms")
    print(f"SBiLSTM MAE: {mae:.4f} ms")
    
    if mae < 0.58:
        print("✅ SBiLSTM OUTPERFORMS RF!")
    else:
        print("ℹ️ RF still competitive - DRL training may need more data/epochs")
    
    # Save model
    torch.save({
        'model': model.state_dict(),
        'config': config,
        'metrics': {'mse': mse, 'mae': mae, 'r2': r2}
    }, os.path.join(config['model_dir'], 'sbilstm_final.pt'))

    history_json = os.path.join(config['model_dir'], 'sbilstm_training_history.json')
    history_csv = os.path.join(config['model_dir'], 'sbilstm_training_history.csv')
    summary_json = os.path.join(config['model_dir'], 'sbilstm_training_summary.json')

    write_json(history_json, training_history)
    pd.DataFrame(training_history).to_csv(history_csv, index=False)
    write_json(summary_json, {
        'model': 'SBiLSTM',
        'epochs_requested': int(config['epochs']),
        'epochs_completed': len(training_history),
        'best_val_loss': float(best_val_loss),
        'test_metrics': {'mse': float(mse), 'mae': float(mae), 'r2': float(r2)},
        'history_json': history_json,
        'history_csv': history_csv,
    })

    print(f"\n✅ Model saved to {config['model_dir']}")
    print(f"✅ History saved to {history_json} and {history_csv}")
    
    return model, {'mse': mse, 'mae': mae, 'r2': r2}


def evaluate_sbilstm(model_path: str, db_path: str = None):
    """
    Evaluate trained SBiLSTM model.
    
    Args:
        model_path: Path to saved model
        db_path: Path to database
    """
    config = DEFAULT_CONFIG.copy()
    if db_path:
        config['db_path'] = db_path
        
    print("=" * 60)
    print("SBiLSTM Model Evaluation")
    print("=" * 60)
    
    # Load model
    checkpoint = torch.load(model_path)
    model = create_sbilstm_model(checkpoint['config'])
    model.load_state_dict(checkpoint['model'])
    
    print(f"Model metrics: {checkpoint['metrics']}")
    
    # Note: Full evaluation would require loading test data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='SBiLSTM Training for GreenRAN')
    parser.add_argument('--mode', type=str, default='train', choices=['train', 'eval'],
                        help='Mode: train or evaluate')
    parser.add_argument('--model', type=str, default=None,
                        help='Path to model for evaluation')
    parser.add_argument('--config', type=str, default=None,
                        help='Path to config file')
    parser.add_argument('--epochs', type=int, default=50,
                        help='Number of epochs')
    parser.add_argument('--db', type=str, default='/tmp/rapp_data_lake.db',
                        help='Path to data lake')
    parser.add_argument('--pw', type=int, default=100,
                        help='Prediction window size')
    
    args = parser.parse_args()
    
    # Load config
    config = DEFAULT_CONFIG.copy()
    if args.config:
        with open(args.config, 'r') as f:
            config.update(yaml.safe_load(f))
    
    # Override with command line args
    config['db_path'] = args.db
    config['epochs'] = args.epochs
    config['prediction_window'] = args.pw
    
    if args.mode == 'train':
        train_sbilstm(config)
    else:
        if args.model is None:
            print("Error: --model required for evaluation")
        else:
            evaluate_sbilstm(args.model, args.db)
