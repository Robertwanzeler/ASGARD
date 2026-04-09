#!/usr/bin/env python3
"""
GreenRAN - Gerador de Gráficos
==============================
Gera gráficos de latência, CVaR, decisões do rApp e energia.

Usage:
    python3 generate_charts.py

Output:
    ./charts/ - Diretório com imagens PNG
"""

import os
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import numpy as np

# Configurações
INPUT_DIR = "/home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran"
DB_PATH = "/tmp/rapp_data_lake.db"
OUTPUT_DIR = "./charts"

# Criar diretório de saída
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Configurar matplotlib para tema escuro
plt.style.use('dark_background')
plt.rcParams['figure.facecolor'] = '#16213e'
plt.rcParams['axes.facecolor'] = '#16213e'
plt.rcParams['axes.edgecolor'] = '#333'
plt.rcParams['axes.labelcolor'] = '#eee'
plt.rcParams['xtick.color'] = '#aaa'
plt.rcParams['ytick.color'] = '#aaa'
plt.rcParams['text.color'] = '#eee'
plt.rcParams['font.family'] = 'sans-serif'


def detect_transition(metrics_df):
    """Detecta automaticamente quando o período de congestionamento começa."""
    if metrics_df is None or len(metrics_df) < 10:
        return None

    if 'sim_time_s' not in metrics_df.columns or 'lat_mean_ms' not in metrics_df.columns:
        return None

    # Ponto onde latência média ultrapassa 1.5x a mediana
    median_lat = metrics_df['lat_mean_ms'].median()
    threshold = median_lat * 1.5
    high_latency = metrics_df[metrics_df['lat_mean_ms'] > threshold]

    if len(high_latency) > 0:
        transition = high_latency.iloc[0]['sim_time_s']
        return transition

    # Fallback: 75% dos dados
    return metrics_df['sim_time_s'].quantile(0.75)


def load_latency_data():
    """Lê DlPdcpStats.txt e processa latência por timestamp."""
    filepath = os.path.join(INPUT_DIR, "DlPdcpStats.txt")
    
    if not os.path.exists(filepath):
        print(f"[ERRO] Arquivo não encontrado: {filepath}")
        return None
    
    print("[1/4] Carregando dados de latência...")
    
    # Ler arquivo (ignorar linhas que começam com %)
    df = pd.read_csv(filepath, sep='\t', comment='%')
    
    # Converter timestamp para segundos
    df['time_s'] = df['start']  # Tempo em segundos
    
    # Calcular janela de tempo (janelas de 5 segundos)
    df['window'] = (df['time_s'] // 5) * 5
    
    # Converter delay de segundos para ms
    df['delay_ms'] = df['delay'] * 1000
    
    # Identificar tipo de UE (câmera ou normal)
    # IMSI 1-3 = câmeras, 4+ = UEs normais
    df['is_camera'] = df['IMSI'] <= 3
    
    # Agrupar por janela de tempo
    grouped = df.groupby('window').agg({
        'delay_ms': ['mean', 'max', 'std', 'count'],
        'is_camera': 'sum'  # Número de câmeras
    }).reset_index()
    
    grouped.columns = ['window', 'lat_mean_ms', 'lat_max_ms', 'lat_std_ms', 'packet_count', 'camera_count']
    
    # Calcular P95 por janela (simulado - usar percentil 95 approximated by max near)
    p95_by_window = df.groupby('window')['delay_ms'].quantile(0.95).reset_index()
    p95_by_window.columns = ['window', 'lat_p95_ms']
    
    grouped = grouped.merge(p95_by_window, on='window')
    
    print(f"    → {len(grouped)} janelas de tempo processadas")
    print(f"    → Tempo total: {grouped['window'].max():.0f}s")
    
    return grouped


def load_extended_metrics():
    """Lê métricas estendidas do Data Lake (com CVaR)."""
    if not os.path.exists(DB_PATH):
        print(f"[AVISO] Data Lake não encontrado: {DB_PATH}")
        return None
    
    print("[2/4] Carregando métricas do Data Lake...")
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT 
            timestamp,
            datetime,
            sim_time_s,
            global_avg_latency_us / 1000.0 as lat_mean_ms,
            global_worst_latency_us / 1000.0 as lat_max_ms,
            latency_p95_us / 1000.0 as lat_p95_ms,
            cvar_per_ue_us / 1000.0 as cvar_ms,
            total_active_cameras,
            total_active_ues,
            total_critical_ues,
            throughput_kbps
        FROM extended_metrics
        ORDER BY timestamp
    """, conn)
    conn.close()
    
    if len(df) > 0:
        df['datetime'] = pd.to_datetime(df['datetime'])
        print(f"    → {len(df)} registros carregados")
    else:
        print("    → Nenhum registro encontrado")
    
    return df


def load_rapp_decisions():
    """Lê decisões do rApp do Data Lake."""
    if not os.path.exists(DB_PATH):
        print(f"[AVISO] Data Lake não encontrado: {DB_PATH}")
        return None
    
    print("[3/4] Carregando decisões do rApp...")
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT 
            datetime,
            decision as energy_saver,
            energy_state,
            reason,
            confidence
        FROM decisions_history
        ORDER BY timestamp
    """, conn)
    conn.close()
    
    if len(df) > 0:
        df['datetime'] = pd.to_datetime(df['datetime'])
        print(f"    → {len(df)} decisões carregadas")
    else:
        print("    → Nenhum registro encontrado")
    
    return df


def load_energy_commands():
    """Lê comandos de energia do Data Lake."""
    if not os.path.exists(DB_PATH):
        return None
    
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT 
            datetime,
            command,
            power_percent,
            reason
        FROM energy_commands
        ORDER BY timestamp
    """, conn)
    conn.close()
    
    if len(df) > 0:
        df['datetime'] = pd.to_datetime(df['datetime'])
        df['savings_percent'] = 100 - df['power_percent']
    
    return df


def plot_latencia_vs_tempo(metrics_df, period_transition=None):
    """Gera gráfico de latência vs tempo."""
    print("    → Gerando: latencia_vs_tempo.png")
    
    fig, ax = plt.subplots(figsize=(14, 6))
    
    if metrics_df is None or len(metrics_df) == 0:
        ax.text(0.5, 0.5, 'Sem dados disponíveis', 
                ha='center', va='center', fontsize=16, color='#ff4444')
        plt.savefig(os.path.join(OUTPUT_DIR, 'latencia_vs_tempo.png'), 
                    dpi=150, facecolor='#16213e', bbox_inches='tight')
        plt.close()
        return
    
    # Plot latência média
    ax.plot(metrics_df['sim_time_s'], metrics_df['lat_mean_ms'], 
            label='Latência Média', color='#00ff88', linewidth=2, alpha=0.8)
    
    # Plot P95
    ax.plot(metrics_df['sim_time_s'], metrics_df['lat_p95_ms'], 
            label='Latência P95', color='#ffaa00', linewidth=2, alpha=0.8)
    
    # Plot pior caso
    ax.plot(metrics_df['sim_time_s'], metrics_df['lat_max_ms'], 
            label='Latência Máxima', color='#ff4444', linewidth=1, alpha=0.5, linestyle='--')
    
    # Linha do SLA
    ax.axhline(y=100, color='#ff4444', linestyle=':', linewidth=2, label='SLA (100ms)')
    
    # Linha de transição de período
    if period_transition is not None and metrics_df['sim_time_s'].max() > period_transition:
        ax.axvline(x=period_transition, color='#00aaff', linestyle='--',
                   linewidth=2, label=f'Transição ({period_transition:.0f}s)')
        ax.axvspan(period_transition, metrics_df['sim_time_s'].max(),
                   alpha=0.2, color='#ff4444', label='Período 2 (Congestionamento)')
    
    ax.set_xlabel('Tempo de Simulação (segundos)', fontsize=12)
    ax.set_ylabel('Latência (ms)', fontsize=12)
    ax.set_title('Latência vs Tempo de Simulação\nGreenRAN O-RAN', fontsize=14, fontweight='bold')
    ax.legend(loc='upper right', fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, metrics_df['sim_time_s'].max() + 10)
    
    # Formatar eixo y
    ax.set_ylim(0, max(150, metrics_df['lat_max_ms'].max() * 1.1))
    
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'latencia_vs_tempo.png'), 
                dpi=150, facecolor='#16213e', bbox_inches='tight')
    plt.close()


def plot_cvar_por_periodo(metrics_df, period_transition=None):
    """Gera gráfico de CVaR por período."""
    print("    → Gerando: cvar_por_periodo.png")
    
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    
    if metrics_df is None or len(metrics_df) == 0 or 'cvar_ms' not in metrics_df.columns:
        axes[0].text(0.5, 0.5, 'Sem dados disponíveis', 
                     ha='center', va='center', fontsize=14, color='#ff4444')
        plt.savefig(os.path.join(OUTPUT_DIR, 'cvar_por_periodo.png'), 
                    dpi=150, facecolor='#16213e', bbox_inches='tight')
        plt.close()
        return
    
    # Separar períodos
    if period_transition is not None:
        periodo1 = metrics_df[metrics_df['sim_time_s'] < period_transition]['cvar_ms']
        periodo2 = metrics_df[metrics_df['sim_time_s'] >= period_transition]['cvar_ms']
    else:
        periodo1 = metrics_df['cvar_ms']
        periodo2 = pd.Series([], dtype=float)

    # Gráfico 1: Boxplot
    ax1 = axes[0]
    if len(periodo1.dropna()) > 0 and len(periodo2.dropna()) > 0:
        box_data = [periodo1.dropna(), periodo2.dropna()]
        bp = ax1.boxplot(box_data, labels=[f'Período 1\n(0-{period_transition:.0f}s)\nTráfego Leve', f'Período 2\n({period_transition:.0f}s+)\nCongestionamento'],
                        patch_artist=True, widths=0.6)

        colors = ['#00ff88', '#ff4444']
        for patch, color in zip(bp['boxes'], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.6)
    else:
        box_data = [periodo1.dropna()]
        bp = ax1.boxplot(box_data, labels=['Todos os Dados'],
                        patch_artist=True, widths=0.6)
        for patch in bp['boxes']:
            patch.set_facecolor('#00aaff')
            patch.set_alpha(0.6)
    
    ax1.axhline(y=80, color='#ffaa00', linestyle='--', linewidth=2, label='Limiar BLOCKED (80ms)')
    ax1.axhline(y=60, color='#00aaff', linestyle=':', linewidth=2, label='Limiar ALLOWED (60ms)')
    
    ax1.set_ylabel('CVaR (ms)', fontsize=12)
    ax1.set_title('Distribuição do CVaR por Período', fontsize=14, fontweight='bold')
    ax1.legend(loc='upper right', fontsize=10)
    ax1.grid(True, alpha=0.3, axis='y')
    
    # Gráfico 2: Linha do tempo com CVaR
    ax2 = axes[1]
    ax2.plot(metrics_df['sim_time_s'], metrics_df['cvar_ms'], 
             color='#00aaff', linewidth=2, label='CVaR')
    ax2.fill_between(metrics_df['sim_time_s'], 0, metrics_df['cvar_ms'], 
                     alpha=0.3, color='#00aaff')
    
    ax2.axhline(y=80, color='#ff4444', linestyle='--', linewidth=2, label='Limiar BLOCKED (80ms)')
    ax2.axhline(y=60, color='#ffaa00', linestyle=':', linewidth=2, label='Limiar ALLOWED (60ms)')
    if period_transition is not None:
        ax2.axvline(x=period_transition, color='#00ff88', linestyle='--', linewidth=2, label=f'Transição ({period_transition:.0f}s)')
    
    ax2.set_xlabel('Tempo de Simulação (segundos)', fontsize=12)
    ax2.set_ylabel('CVaR (ms)', fontsize=12)
    ax2.set_title('CVaR ao Longo do Tempo', fontsize=14, fontweight='bold')
    ax2.legend(loc='upper left', fontsize=10)
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'cvar_por_periodo.png'), 
                dpi=150, facecolor='#16213e', bbox_inches='tight')
    plt.close()


def plot_decisoes_rapp(decisions_df, period_transition=None):
    """Gera gráfico de decisões do rApp."""
    print("    → Gerando: decisoes_rapp.png")
    
    fig, axes = plt.subplots(2, 1, figsize=(14, 10))
    
    if decisions_df is None or len(decisions_df) == 0:
        axes[0].text(0.5, 0.5, 'Sem dados disponíveis', 
                      ha='center', va='center', fontsize=14, color='#ff4444')
        plt.savefig(os.path.join(OUTPUT_DIR, 'decisoes_rapp.png'), 
                    dpi=150, facecolor='#16213e', bbox_inches='tight')
        plt.close()
        return
    
    # Criar índice se não tiver sim_time_s
    if 'sim_time_s' not in decisions_df.columns:
        decisions_df = decisions_df.reset_index(drop=True)
        decisions_df['sim_time_s'] = decisions_df.index * 5  # ~5 segundos por ciclo
    
    # Gráfico 1: Timeline das decisões
    ax1 = axes[0]
    
    # Mapear decisões para cores
    color_map = {
        'BLOCKED': '#ff4444',
        'ALLOWED': '#00ff88',
        'CONDITIONAL': '#ffaa00',
        'UNKNOWN': '#888888'
    }
    
    # Criar coluna numérica para plot
    decision_numeric = decisions_df['energy_saver'].map({
        'BLOCKED': 2,
        'ALLOWED': 0,
        'CONDITIONAL': 1,
        'UNKNOWN': -1
    }).fillna(-1)
    
    ax1.scatter(decisions_df['sim_time_s'], decision_numeric, 
                c=decisions_df['energy_saver'].map(color_map), 
                s=30, alpha=0.7)
    
    ax1.set_yticks([0, 1, 2])
    ax1.set_yticklabels(['ALLOWED', 'CONDITIONAL', 'BLOCKED'])
    ax1.set_xlabel('Tempo de Simulação (segundos)', fontsize=12)
    ax1.set_ylabel('Decisão do rApp', fontsize=12)
    ax1.set_title('Decisões do rApp ao Longo do Tempo', fontsize=14, fontweight='bold')
    ax1.grid(True, alpha=0.3)
    
    # Linha de transição se existir
    if period_transition is not None and decisions_df['sim_time_s'].max() > period_transition:
        ax1.axvline(x=period_transition, color='#00aaff', linestyle='--', linewidth=2, label=f'Transição ({period_transition:.0f}s)')
        ax1.axvspan(period_transition, decisions_df['sim_time_s'].max(),
                    alpha=0.1, color='#ff4444', label='Período 2')
        ax1.legend(loc='upper right')
    
    # Gráfico 2: Pizza de distribuição
    ax2 = axes[1]
    
    decision_counts = decisions_df['energy_saver'].value_counts()
    colors_pie = [color_map.get(d, '#888888') for d in decision_counts.index]
    
    wedges, texts, autotexts = ax2.pie(decision_counts.values, 
                                        labels=decision_counts.index,
                                        colors=colors_pie,
                                        autopct='%1.1f%%',
                                        startangle=90,
                                        explode=[0.02] * len(decision_counts))
    
    for autotext in autotexts:
        autotext.set_color('#000')
        autotext.set_fontweight('bold')
    
    ax2.set_title('Distribuição das Decisões do rApp', fontsize=14, fontweight='bold')
    
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'decisoes_rapp.png'), 
                dpi=150, facecolor='#16213e', bbox_inches='tight')
    plt.close()


def plot_energia_tempo(energy_df, decisions_df):
    """Gera gráfico de energia ao longo do tempo."""
    print("    → Gerando: energia_tempo.png")
    
    fig, axes = plt.subplots(2, 1, figsize=(14, 10))
    
    # Tentar obter dados de energia
    data_df = None
    
    if energy_df is not None and len(energy_df) > 0:
        data_df = energy_df.copy()
    elif decisions_df is not None and len(decisions_df) > 0:
        data_df = decisions_df.copy()
        data_df['power_percent'] = data_df['energy_saver'].map({
            'BLOCKED': 100,
            'ALLOWED': 25,
            'CONDITIONAL': 70,
            'UNKNOWN': 100
        })
    
    if data_df is None or len(data_df) == 0:
        axes[0].text(0.5, 0.5, 'Sem dados disponíveis', 
                      ha='center', va='center', fontsize=14, color='#ff4444')
        plt.savefig(os.path.join(OUTPUT_DIR, 'energia_tempo.png'), 
                    dpi=150, facecolor='#16213e', bbox_inches='tight')
        plt.close()
        return
    
    # Criar índice se não tiver sim_time_s
    if 'sim_time_s' not in data_df.columns:
        data_df = data_df.reset_index(drop=True)
        data_df['sim_time_s'] = data_df.index * 5  # ~5 segundos por ciclo
    
    # Gráfico 1: Potência ao longo do tempo
    ax1 = axes[0]
    
    ax1.plot(data_df['sim_time_s'], data_df['power_percent'], 
             color='#ffaa00', linewidth=2, marker='o', markersize=3)
    ax1.fill_between(data_df['sim_time_s'], 0, data_df['power_percent'], 
                     alpha=0.3, color='#ffaa00')
    
    ax1.set_xlabel('Tempo de Simulação (segundos)', fontsize=12)
    ax1.set_ylabel('Potência (%)', fontsize=12)
    ax1.set_title('Potência da Rede ao Longo do Tempo', fontsize=14, fontweight='bold')
    ax1.set_ylim(0, 110)
    ax1.grid(True, alpha=0.3)
    ax1.axhline(y=100, color='#ff4444', linestyle=':', linewidth=1, label='FULL_POWER')
    ax1.axhline(y=70, color='#ffaa00', linestyle=':', linewidth=1, label='CONDITIONAL')
    ax1.axhline(y=25, color='#00ff88', linestyle=':', linewidth=1, label='POWER_DOWN')
    ax1.legend(loc='upper right')
    
    # Gráfico 2: Economia ao longo do tempo
    ax2 = axes[1]
    
    savings = 100 - data_df['power_percent']
    
    ax2.plot(data_df['sim_time_s'], savings, 
             color='#00ff88', linewidth=2, marker='o', markersize=3)
    ax2.fill_between(data_df['sim_time_s'], 0, savings, 
                     alpha=0.3, color='#00ff88')
    
    ax2.set_xlabel('Tempo de Simulação (segundos)', fontsize=12)
    ax2.set_ylabel('Economia (%)', fontsize=12)
    ax2.set_title('Economia de Energia ao Longo do Tempo', fontsize=14, fontweight='bold')
    ax2.set_ylim(0, 110)
    ax2.grid(True, alpha=0.3)
    
    # Calcular média
    avg_savings = savings.mean()
    ax2.axhline(y=avg_savings, color='#ffaa00', linestyle='--', 
                linewidth=2, label=f'Média: {avg_savings:.1f}%')
    ax2.legend(loc='upper right')
    
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'energia_tempo.png'), 
                dpi=150, facecolor='#16213e', bbox_inches='tight')
    plt.close()


def plot_ues_cameras(metrics_df, period_transition=None):
    """Gera gráfico de UEs e câmeras ao longo do tempo."""
    print("    → Gerando: ues_cameras.png")
    
    fig, ax = plt.subplots(figsize=(14, 6))
    
    if metrics_df is None or len(metrics_df) == 0:
        ax.text(0.5, 0.5, 'Sem dados disponíveis', 
                ha='center', va='center', fontsize=14, color='#ff4444')
        plt.savefig(os.path.join(OUTPUT_DIR, 'ues_cameras.png'), 
                    dpi=150, facecolor='#16213e', bbox_inches='tight')
        plt.close()
        return
    
    ax.plot(metrics_df['sim_time_s'], metrics_df['total_active_ues'], 
            label='UEs Ativas', color='#00aaff', linewidth=2)
    ax.plot(metrics_df['sim_time_s'], metrics_df['total_active_cameras'], 
            label='Câmeras Ativas', color='#ff4444', linewidth=2, marker='o', markersize=3)
    
    if period_transition is not None and metrics_df['sim_time_s'].max() > period_transition:
        ax.axvline(x=period_transition, color='#00ff88', linestyle='--', linewidth=2, label=f'Transição ({period_transition:.0f}s)')
        ax.axvspan(period_transition, metrics_df['sim_time_s'].max(),
                   alpha=0.1, color='#ff4444', label='Período 2')
    
    ax.set_xlabel('Tempo de Simulação (segundos)', fontsize=12)
    ax.set_ylabel('Quantidade', fontsize=12)
    ax.set_title('UEs e Câmeras Ativas ao Longo do Tempo', fontsize=14, fontweight='bold')
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'ues_cameras.png'), 
                dpi=150, facecolor='#16213e', bbox_inches='tight')
    plt.close()


def export_csv(metrics_df, decisions_df, energy_df):
    """Exporta dados para CSV."""
    print("[4/4] Exportando CSVs...")
    
    if metrics_df is not None and len(metrics_df) > 0:
        metrics_df.to_csv(os.path.join(OUTPUT_DIR, 'metrics_extended.csv'), index=False)
        print("    → metrics_extended.csv")
    
    if decisions_df is not None and len(decisions_df) > 0:
        decisions_df.to_csv(os.path.join(OUTPUT_DIR, 'decisions_rapp.csv'), index=False)
        print("    → decisions_rapp.csv")
    
    if energy_df is not None and len(energy_df) > 0:
        energy_df.to_csv(os.path.join(OUTPUT_DIR, 'energy_commands.csv'), index=False)
        print("    → energy_commands.csv")


def main():
    """Função principal."""
    print("=" * 60)
    print("  GreenRAN - Gerador de Gráficos")
    print("=" * 60)
    print()
    
    # Carregar dados
    metrics_df = load_extended_metrics()
    decisions_df = load_rapp_decisions()
    energy_df = load_energy_commands()

    # Detectar transição de período
    period_transition = detect_transition(metrics_df)
    if period_transition is not None:
        print(f"    → Transição detectada em: {period_transition:.0f}s")
    else:
        print("    → Sem transição detectada")

    # Gerar gráficos
    print()
    print("[GRÁFICOS]")

    if metrics_df is not None and len(metrics_df) > 0:
        plot_latencia_vs_tempo(metrics_df, period_transition)
        plot_cvar_por_periodo(metrics_df, period_transition)
        plot_ues_cameras(metrics_df, period_transition)

    if decisions_df is not None and len(decisions_df) > 0:
        plot_decisoes_rapp(decisions_df, period_transition)
    
    if energy_df is not None or decisions_df is not None:
        plot_energia_tempo(energy_df, decisions_df)
    
    # Exportar CSVs
    print()
    export_csv(metrics_df, decisions_df, energy_df)
    
    print()
    print("=" * 60)
    print(f"  GRÁFICOS GERADOS EM: {os.path.abspath(OUTPUT_DIR)}/")
    print("=" * 60)
    print()
    print("Arquivos gerados:")
    for f in sorted(os.listdir(OUTPUT_DIR)):
        print(f"  - {f}")
    print()


if __name__ == "__main__":
    main()
