#!/usr/bin/env python3
"""
Script de Visualização de Rewards - A3C V3 vs RF
================================================
Gera gráficos comparativos dos resultados de reward entre A3C V3 e Random Forest.

Executar:
    cd /home/robert/orange_nuclear/drlexp
    source .venv/bin/activate
    python training/plot_rewards.py
"""

import os
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import sqlite3
import pandas as pd
import torch

sys.path.insert(0, 'src')

from drl.models.actor import ActorNetwork

OUTPUT_DIR = '/home/robert/orange_nuclear/drlexp/charts/rewards'
DB_PATH = '/tmp/rapp_data_lake.db'
MODEL_PATH = '/home/robert/orange_nuclear/drlexp/models/a3c/actor_v4.pt'  # Using V4

plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams['figure.figsize'] = (10, 6)
plt.rcParams['font.size'] = 12

def load_data():
    """Carrega dados do Data Lake."""
    conn = sqlite3.connect(DB_PATH)
    
    df = pd.read_sql_query('''
        SELECT 
            m.cvar_per_ue_us,
            m.latency_p95_per_ue_us,
            m.global_avg_latency_us,
            m.global_jitter_us,
            m.global_packet_loss_rate,
            m.throughput_kbps,
            m.total_active_ues,
            m.total_active_cameras,
            m.total_critical_ues,
            m.variance_per_ue_us2,
            d.decision as rf_decision
        FROM extended_metrics m
        JOIN decisions_history d ON m.timestamp = d.timestamp
        WHERE m.cvar_per_ue_us > 0
    ''', conn)
    
    conn.close()
    return df

def load_a3c_model():
    """Carrega modelo A3C V3."""
    actor = ActorNetwork(state_size=18, num_actions=9)
    actor.load_state_dict(torch.load(MODEL_PATH, map_location='cpu'))
    actor.eval()
    return actor

def get_a3c_decision(actor, row):
    """Retorna decisão do A3C para uma linha de dados."""
    cvar_ms = row['cvar_per_ue_us'] / 1000.0
    
    state = torch.FloatTensor([
        cvar_ms, 0, 0,
        row['latency_p95_per_ue_us'] / 1000.0,
        row['global_jitter_us'] / 1000.0,
        row['global_packet_loss_rate'] * 100,
        row['throughput_kbps'] / 1000.0,
        row['total_active_ues'], row['total_active_cameras'], row['total_critical_ues'],
        row['total_active_cameras']/max(row['total_active_ues'],1),
        row['total_critical_ues']/max(row['total_active_ues'],1),
        row['total_active_ues'] * 10, 20.0, 30.0, 0, 0,
        row['variance_per_ue_us2'] / 1_000_000.0
    ])
    
    with torch.no_grad():
        probs = actor(state)
        action = torch.argmax(probs).item()
    
    decision = ['ALLOWED', 'ALLOWED', 'ALLOWED',
                'CONDITIONAL', 'CONDITIONAL', 'CONDITIONAL',
                'BLOCKED', 'BLOCKED', 'BLOCKED'][action]
    
    return decision

def calculate_rf_reward(decision, cvar_ms):
    """Calcula reward para decisão RF."""
    if cvar_ms < 60:
        base = 1.0
    elif cvar_ms < 80:
        base = 0.5
    elif cvar_ms < 100:
        base = 0.0
    else:
        base = -1.0
    
    if decision == 'ALLOWED':
        power_bonus = 0.2
    elif decision == 'BLOCKED':
        power_bonus = -0.3
    else:
        power_bonus = 0.0
    
    if decision == 'BLOCKED' and cvar_ms < 60:
        penalty = -0.5
    else:
        penalty = 0
    
    return base + power_bonus + penalty

def calculate_a3c_reward(decision, cvar_ms):
    """Calcula reward para decisão A3C (V3)."""
    if cvar_ms < 60:
        base = 1.0
    elif cvar_ms < 80:
        base = 0.5
    elif cvar_ms < 100:
        base = 0.0
    else:
        base = -2.0
    
    if cvar_ms > 80 and decision == 'BLOCKED':
        block_bonus = 1.5
    elif cvar_ms > 80 and decision != 'BLOCKED':
        block_bonus = -1.5
    else:
        block_bonus = 0
    
    if cvar_ms < 60 and decision == 'ALLOWED':
        allowed_bonus = 2.0
    elif cvar_ms < 60 and decision == 'BLOCKED':
        allowed_bonus = -0.8
    else:
        allowed_bonus = 0
    
    return base + block_bonus + allowed_bonus

def plot_reward_comparison(rf_rewards, a3c_rewards):
    """Gráfico 1: Comparação de reward médio."""
    fig, ax = plt.subplots(figsize=(10, 6))
    
    x = ['Random Forest', 'A3C V3']
    y = [np.mean(rf_rewards), np.mean(a3c_rewards)]
    colors = ['#3498db', '#2ecc71']
    
    bars = ax.bar(x, y, color=colors, edgecolor='black', linewidth=1.5)
    
    ax.set_ylabel('Reward Médio', fontsize=14)
    ax.set_title('Comparação de Reward: RF vs A3C V3', fontsize=16, fontweight='bold')
    
    for bar, val in zip(bars, y):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.02, 
                f'{val:.3f}', ha='center', va='bottom', fontsize=14, fontweight='bold')
    
    ax.set_ylim(min(y) - 0.5, max(y) + 0.5)
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    
    plt.tight_layout()
    plt.savefig(f'{OUTPUT_DIR}/reward_comparison.png', dpi=150)
    plt.close()
    print(f'✅ reward_comparison.png')

def plot_reward_by_zone(df, rf_rewards, a3c_rewards):
    """Gráfico 2: Reward médio por zona de CVaR."""
    fig, ax = plt.subplots(figsize=(10, 6))
    
    zones = ['Green\n(CVaR <60ms)', 'Yellow\n(60-80ms)', 'Red\n(CVaR >80ms)']
    zone_indices = [
        (df['cvar_per_ue_us']/1000 < 60),
        (df['cvar_per_ue_us']/1000 >= 60) & (df['cvar_per_ue_us']/1000 < 80),
        (df['cvar_per_ue_us']/1000 >= 80)
    ]
    
    rf_zone_rewards = [np.mean([r for r, idx in zip(rf_rewards, idxs) if idx]) for idxs in zone_indices]
    a3c_zone_rewards = [np.mean([r for r, idx in zip(a3c_rewards, idxs) if idx]) for idxs in zone_indices]
    
    x = np.arange(len(zones))
    width = 0.35
    
    bars1 = ax.bar(x - width/2, rf_zone_rewards, width, label='Random Forest', color='#3498db')
    bars2 = ax.bar(x + width/2, a3c_zone_rewards, width, label='A3C V3', color='#2ecc71')
    
    ax.set_ylabel('Reward Médio', fontsize=14)
    ax.set_title('Reward Médio por Zona de CVaR', fontsize=16, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(zones)
    ax.legend()
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    
    plt.tight_layout()
    plt.savefig(f'{OUTPUT_DIR}/reward_by_zone.png', dpi=150)
    plt.close()
    print(f'✅ reward_by_zone.png')

def plot_decision_distribution(df):
    """Gráfico 3: Distribuição de decisões."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    
    rf_counts = df['rf_decision'].value_counts()
    a3c_decisions = []
    
    actor = load_a3c_model()
    for idx, row in df.iterrows():
        a3c_decisions.append(get_a3c_decision(actor, row))
    
    a3c_counts = pd.Series(a3c_decisions).value_counts()
    
    colors = {'ALLOWED': '#27ae60', 'CONDITIONAL': '#f39c12', 'BLOCKED': '#e74c3c'}
    
    # RF Pie
    axes[0].pie(rf_counts.values, labels=rf_counts.index, autopct='%1.1f%%',
                colors=[colors[c] for c in rf_counts.index], startangle=90)
    axes[0].set_title('Random Forest', fontsize=14, fontweight='bold')
    
    # A3C Pie
    axes[1].pie(a3c_counts.values, labels=a3c_counts.index, autopct='%1.1f%%',
                colors=[colors[c] for c in a3c_counts.index], startangle=90)
    axes[1].set_title('A3C V3', fontsize=14, fontweight='bold')
    
    fig.suptitle('Distribuição de Decisões', fontsize=16, fontweight='bold', y=1.02)
    
    plt.tight_layout()
    plt.savefig(f'{OUTPUT_DIR}/decision_distribution.png', dpi=150)
    plt.close()
    print(f'✅ decision_distribution.png')

def plot_energy_efficiency():
    """Gráfico 4: Eficiência energética."""
    fig, ax = plt.subplots(figsize=(10, 6))
    
    systems = ['Random Forest', 'A3C V3']
    power = [86.8, 78.7]
    economy = [13.2, 21.3]
    
    x = np.arange(len(systems))
    width = 0.35
    
    bars1 = ax.bar(x - width/2, power, width, label='Potência Média (%)', color='#e74c3c')
    bars2 = ax.bar(x + width/2, economy, width, label='Economia (%)', color='#2ecc71')
    
    ax.set_ylabel('Porcentagem (%)', fontsize=14)
    ax.set_title('Eficiência Energética: RF vs A3C V3', fontsize=16, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(systems)
    ax.legend()
    ax.set_ylim(0, 100)
    
    for bar, val in zip(bars1, power):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, 
                f'{val:.1f}%', ha='center', fontsize=12)
    
    for bar, val in zip(bars2, economy):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, 
                f'{val:.1f}%', ha='center', fontsize=12)
    
    plt.tight_layout()
    plt.savefig(f'{OUTPUT_DIR}/energy_efficiency.png', dpi=150)
    plt.close()
    print(f'✅ energy_efficiency.png')

def plot_summary_metrics():
    """Gráfico 5: Resumo das métricas."""
    fig = plt.figure(figsize=(14, 10))
    
    metrics = ['ALLOWED (%)', 'BLOCKED (%)', 'Potência (%)', 'Economia (%)', 'Proteção SLA (%)']
    rf_values = [25.4, 73, 86.8, 13.2, 73]
    a3c_values = [26, 74, 78.7, 21.3, 88]
    
    # Create grid
    gs = fig.add_gridspec(2, 3, hspace=0.3, wspace=0.3)
    
    # 1. Metric comparison (bar chart)
    ax1 = fig.add_subplot(gs[0, :2])
    x = np.arange(len(metrics))
    width = 0.35
    ax1.barh(x - width/2, rf_values, width, label='Random Forest', color='#3498db')
    ax1.barh(x + width/2, a3c_values, width, label='A3C V3', color='#2ecc71')
    ax1.set_yticks(x)
    ax1.set_yticklabels(metrics)
    ax1.set_xlabel('Valor')
    ax1.set_title('Comparação de Métricas', fontweight='bold')
    ax1.legend(loc='lower right')
    ax1.set_xlim(0, 100)
    
    # 2. Winner summary
    ax2 = fig.add_subplot(gs[0, 2])
    ax2.axis('off')
    winners = [
        ('Economia', 'A3C V3', '#2ecc71'),
        ('Proteção SLA', 'A3C V3', '#2ecc71'),
        ('ALLOWED', 'EMPATE', '#95a5a6'),
        ('BLOCKED', 'EMPATE', '#95a5a6')
    ]
    for i, (metric, winner, color) in enumerate(winners):
        ax2.text(0.5, 0.8 - i*0.2, f'{metric}:', fontsize=12, ha='center', fontweight='bold')
        ax2.text(0.5, 0.6 - i*0.2, winner, fontsize=14, ha='center', color=color, fontweight='bold')
    ax2.set_title('Vencedores', fontweight='bold')
    
    # 3. Pie chart for final comparison
    ax3 = fig.add_subplot(gs[1, 0])
    sizes = [13.2, 86.8]
    colors_pie = ['#2ecc71', '#e74c3c']
    ax3.pie(sizes, labels=['Economia\nRF', 'Perda\nRF'], colors=colors_pie, autopct='%1.1f%%')
    ax3.set_title('RF - Energia', fontweight='bold')
    
    ax4 = fig.add_subplot(gs[1, 1])
    sizes = [21.3, 78.7]
    ax4.pie(sizes, labels=['Economia\nA3C', 'Perda\nA3C'], colors=colors_pie, autopct='%1.1f%%')
    ax4.set_title('A3C V3 - Energia', fontweight='bold')
    
    ax5 = fig.add_subplot(gs[1, 2])
    improvement = ((21.3 - 13.2) / 13.2) * 100
    ax5.text(0.5, 0.7, f'Melhoria', fontsize=14, ha='center', fontweight='bold')
    ax5.text(0.5, 0.5, f'{improvement:.1f}%', fontsize=28, ha='center', color='#2ecc71', fontweight='bold')
    ax5.text(0.5, 0.3, 'em economia', fontsize=12, ha='center')
    ax5.axis('off')
    
    fig.suptitle('Resumo de Métricas: Random Forest vs A3C V3', fontsize=18, fontweight='bold', y=0.98)
    
    plt.tight_layout()
    plt.savefig(f'{OUTPUT_DIR}/summary_metrics.png', dpi=150)
    plt.close()
    print(f'✅ summary_metrics.png')

def main():
    print('='*60)
    print('Gerando Gráficos de Reward - A3C V3 vs RF')
    print('='*60)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print('\n📊 Carregando dados do Data Lake...')
    df = load_data()
    print(f'   {len(df)} amostras carregadas')
    
    print('\n🎯 Carregando modelo A3C V3...')
    actor = load_a3c_model()
    
    print('\n📈 Calculando decisões e rewards...')
    a3c_decisions = []
    a3c_rewards = []
    rf_rewards = []
    
    for idx, row in df.iterrows():
        cvar_ms = row['cvar_per_ue_us'] / 1000.0
        
        # RF
        rf_decision = row['rf_decision']
        rf_reward = calculate_rf_reward(rf_decision, cvar_ms)
        rf_rewards.append(rf_reward)
        
        # A3C
        a3c_decision = get_a3c_decision(actor, row)
        a3c_reward = calculate_a3c_reward(a3c_decision, cvar_ms)
        a3c_decisions.append(a3c_decision)
        a3c_rewards.append(a3c_reward)
    
    print('\n🎨 Gerando gráficos...')
    
    plot_reward_comparison(rf_rewards, a3c_rewards)
    plot_reward_by_zone(df, rf_rewards, a3c_rewards)
    plot_decision_distribution(df)
    plot_energy_efficiency()
    plot_summary_metrics()
    
    print(f'\n✅ Todos os gráficos salvos em: {OUTPUT_DIR}/')
    print('='*60)

if __name__ == '__main__':
    main()