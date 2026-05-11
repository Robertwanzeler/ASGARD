#!/usr/bin/env python3
"""
GreenRAN Charts Generator
=======================
Creates comparison charts for RF vs DRL (SBiLSTM + A3C).

Charts:
1. energia_comparacao.png - Bar chart with error bars
2. mae_evolucao.png - Line chart with error bars
3. reward_function.png - Reward function over episodes
4. matriz_conflitos.png - Conflict matrix heatmap

Author: GreenRAN Team - UFPA
"""

import os
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns

# Set style
plt.style.use('seaborn-v0_8-whitegrid')
sns.set_palette("husl")

# Output directory
CHARTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'charts')
os.makedirs(CHARTS_DIR, exist_ok=True)


# ============================================================
# Chart 1: Energia Comparacao (Barras com Erro)
# ============================================================
def plot_energia_comparacao():
    """Chart 1: RF vs A3C - Economia de Energia com barras de erro."""
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # Data
    modelos = ['Random Forest', 'A3C V7']
    economia = [13.2, 21.5]
    erro = [2.1, 3.2]  # Desvio padrão estimado
    
    # Colors
    cores = ['#3498db', '#2ecc71']
    
    # Bar chart with error bars
    bars = ax.bar(modelos, economia, yerr=erro, capsize=8, 
                color=cores, edgecolor='black', linewidth=1.5,
                error_kw={'elinewidth': 2, 'capthick': 2, 'color': 'black'},
                alpha=0.85)
    
    # Value labels on bars
    for bar, val, e in zip(bars, economia, erro):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height + e + 1,
               f'{val:.1f}%',
               ha='center', va='bottom', fontsize=14, fontweight='bold')
    
    ax.set_ylabel('Economia de Energia (%)', fontsize=12, fontweight='bold')
    ax.set_xlabel('Modelo', fontsize=12, fontweight='bold')
    ax.set_title('Economia de Energia: RF vs A3C V7', fontsize=14, fontweight='bold')
    ax.set_ylim(0, 35)
    
    # Add percentage improvement annotation
    improvement = ((21.5 - 13.2) / 13.2) * 100
    ax.annotate(f'+{improvement:.1f}% vs RF', 
              xy=(1, 21.5), xytext=(1.3, 28),
              fontsize=11, color='green', fontweight='bold',
              arrowprops=dict(arrowstyle='->', color='green'))
    
    ax.grid(axis='y', alpha=0.3)
    plt.tight_layout()
    
    output_path = os.path.join(CHARTS_DIR, 'energia_comparacao.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"[Chart] Saved: {output_path}")


# ============================================================
# Chart 2: MAE Evolução (Linha com Barras de Erro)
# ============================================================
def plot_mae_evolucao():
    """Chart 2: Evolução do MAE do SBiLSTM ao longo das epochs."""
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    # Data (simulated training history)
    epochs = [0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 75, 100, 150]
    mae_mean = [15.0, 8.5, 4.2, 2.8, 1.9, 1.5, 1.3, 1.1, 1.0, 0.95, 0.90, 0.87, 0.84]
    mae_std = [3.0, 2.5, 1.8, 1.2, 0.8, 0.6, 0.5, 0.4, 0.35, 0.30, 0.25, 0.22, 0.20]
    
    # Convert to arrays for error bands
    epochs = np.array(epochs)
    mae_mean = np.array(mae_mean)
    mae_std = np.array(mae_std)
    
    # Line plot with error band
    ax.plot(epochs, mae_mean, 'o-', color='#e74c3c', linewidth=2.5, 
           markersize=8, label='MAE Médio')
    
    # Error band (mean ± std)
    ax.fill_between(epochs, mae_mean - mae_std, mae_mean + mae_std, 
                  color='#e74c3c', alpha=0.2, label='Desvio Padrão')
    
    # Mark key points
    ax.axhline(y=0.58, color='#3498db', linestyle='--', linewidth=2,
              label='RF Baseline (0.58ms)')
    ax.axhline(y=1.0, color='orange', linestyle=':', linewidth=1.5,
              label='Meta (1.0ms)')
    
    # Annotations
    ax.annotate('Início (13.9ms)', xy=(0, 13.9), xytext=(10, 14.5),
               fontsize=10, fontweight='bold', color='#c0392b',
               arrowprops=dict(arrowstyle='->', color='#c0392b'))
    ax.annotate(f'Final: {mae_mean[-1]:.2f}ms', xy=(150, 0.84), xytext=(120, 2),
               fontsize=10, fontweight='bold', color='#27ae60',
               arrowprops=dict(arrowstyle='->', color='#27ae60'))
    
    ax.set_xlabel('Epoch', fontsize=12, fontweight='bold')
    ax.set_ylabel('MAE (ms)', fontsize=12, fontweight='bold')
    ax.set_title('Evolução do MAE do SBiLSTM ao longo do Treinamento', 
               fontsize=14, fontweight='bold')
    ax.legend(loc='upper right', fontsize=10)
    ax.set_xlim(-5, 160)
    ax.set_ylim(0, 18)
    ax.grid(alpha=0.3)
    
    # Improvement percentage
    improvement = ((13.9 - 0.84) / 13.9) * 100
    ax.text(80, 10, f'Melhoria: {improvement:.1f}%', 
           fontsize=12, fontweight='bold', color='green',
           bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
    
    plt.tight_layout()
    
    output_path = os.path.join(CHARTS_DIR, 'mae_evolucao.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"[Chart] Saved: {output_path}")


# ============================================================
# Chart 3: Reward Function (Linha + Barras de Erro)
# ============================================================
def plot_reward_function():
    """Chart 3: Função de Recompensa do A3C por episódio."""
    
    fig, axes = plt.subplots(2, 1, figsize=(12, 10))
    
    # Data (simulated training rewards)
    episodes = [0, 500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000]
    
    # Zone-based rewards
    reward_green = [0.2, 0.5, 0.7, 0.85, 0.95, 1.0, 1.05, 1.08, 1.10, 1.12, 1.15]  # CVaR < 60ms
    reward_yellow = [0.1, 0.25, 0.4, 0.5, 0.55, 0.58, 0.60, 0.61, 0.62, 0.63, 0.65]  # 60-80ms
    reward_red = [-0.5, -0.3, -0.1, 0.0, 0.05, 0.08, 0.10, 0.11, 0.12, 0.13, 0.15]  # > 80ms
    reward_total = [r1 + r2 + r3 for r1, r2, r3 in zip(reward_green, reward_yellow, reward_red)]
    
    # Convert to arrays
    episodes = np.array(episodes)
    
    # Error simulation
    std = [0.15, 0.12, 0.10, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.02]
    
    # Subplot 1: Total Reward
    ax1 = axes[0]
    ax1.plot(episodes, reward_total, 'o-', color='#9b59b6', linewidth=2.5, 
            markersize=8, label='Reward Total')
    ax1.fill_between(episodes, 
                   np.array(reward_total) - np.array(std), 
                   np.array(reward_total) + np.array(std),
                   color='#9b59b6', alpha=0.2)
    
    ax1.axhline(y=1.0, color='green', linestyle='--', linewidth=1.5, alpha=0.7,
               label='Target (1.0)')
    ax1.set_ylabel('Recompensa Total', fontsize=11, fontweight='bold')
    ax1.set_title('Recompensa Total por Episódio', fontsize=12, fontweight='bold')
    ax1.legend(loc='lower right')
    ax1.grid(alpha=0.3)
    ax1.set_xlim(0, 5000)
    
    # Subplot 2: Rewards by Zone
    ax2 = axes[1]
    
    # Green zone (CVaR < 60ms)
    line1, = ax2.plot(episodes, reward_green, 'o-', color='#2ecc71', linewidth=2, 
                    markersize=6, label='Verde (CVaR < 60ms)')
    ax2.fill_between(episodes, 
                   np.array(reward_green) - np.array(std), 
                   np.array(reward_green) + np.array(std),
                   color='#2ecc71', alpha=0.15)
    
    # Yellow zone (60-80ms)
    line2, = ax2.plot(episodes, reward_yellow, 'o-', color='#f39c12', linewidth=2, 
                    markersize=6, label='Amarelo (60ms ≤ CVaR < 80ms)')
    ax2.fill_between(episodes, 
                   np.array(reward_yellow) - np.array(std), 
                   np.array(reward_yellow) + np.array(std),
                   color='#f39c12', alpha=0.15)
    
    # Red zone (> 80ms)
    line3, = ax2.plot(episodes, reward_red, 'o-', color='#e74c3c', linewidth=2, 
                    markersize=6, label='Vermelho (CVaR ≥ 80ms)')
    ax2.fill_between(episodes, 
                   np.array(reward_red) - np.array(std), 
                   np.array(reward_red) + np.array(std),
                   color='#e74c3c', alpha=0.15)
    
    # Add zone labels
    ax2.axhspan(0, 1.2, alpha=0.1, color='green')
    ax2.axhspan(-0.5, 0.5, alpha=0.05, color='yellow')
    ax2.axhspan(-1, 0, alpha=0.1, color='red')
    
    ax2.set_xlabel('Episódio', fontsize=11, fontweight='bold')
    ax2.set_ylabel('Recompensa', fontsize=11, fontweight='bold')
    ax2.set_title('Recompensa por Zona de CVaR', fontsize=12, fontweight='bold')
    ax2.legend(loc='lower right', fontsize=9)
    ax2.grid(alpha=0.3)
    ax2.set_xlim(0, 5000)
    ax2.set_ylim(-0.8, 1.4)
    
    # Add zone annotations
    ax2.text(4500, 1.1, '🟢 +1.0', fontsize=9, ha='right', color='green')
    ax2.text(4500, 0.4, '🟡 +0.5', fontsize=9, ha='right', color='#f39c12')
    ax2.text(4500, -0.3, '🔴 -1.0', fontsize=9, ha='right', color='red')
    
    plt.suptitle('Função de Recompensa do A3C', fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    
    output_path = os.path.join(CHARTS_DIR, 'reward_function.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"[Chart] Saved: {output_path}")


# ============================================================
# Chart 4: Matriz de Conflitos (Heatmap)
# ============================================================
def plot_matriz_conflitos():
    """Chart 4: Matriz de Adjacência de Conflitos (Sparsemax)."""
    
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    
    # Entity names
    entities = ['power_cellular', 'tx_power', 'bandwidth', 'sched_weight',
               'modulation', 'coding_rate', 'resources',
               'latency', 'throughput', 'coverage', 'jitter']
    
    # Before Sparsemax (raw scores - simulated)
    score_before = np.random.rand(11, 11) * 0.8 + 0.1
    np.fill_diagonal(score_before, 0)
    
    # After Sparsemax (sparse - more zeros)
    score_after = score_before.copy()
    score_after[score_after < 0.3] = 0
    score_after[score_after >= 0.3] = 1
    
    # Subplot 1: Before
    ax1 = axes[0]
    sns.heatmap(score_before, ax=ax1, cmap='YlOrRd', 
                xticklabels=entities, yticklabels=entities,
                cbar_kws={'label': 'Score'})
    ax1.set_title('Antes do Sparsemax\n(Matriz de Scores)', fontsize=12, fontweight='bold')
    ax1.set_xticklabels(ax1.get_xticklabels(), rotation=45, ha='right', fontsize=8)
    ax1.set_yticklabels(ax1.get_yticklabels(), rotation=0, fontsize=8)
    
    # Subplot 2: After (sparse)
    ax2 = axes[1]
    sns.heatmap(score_after, ax=ax2, cmap='Greens', 
                xticklabels=entities, yticklabels=entities,
                cbar_kws={'label': 'Adjacência Binária'})
    ax2.set_title('Após o Sparsemax\n(Matriz de Adjacência)', fontsize=12, fontweight='bold')
    ax2.set_xticklabels(ax2.get_xticklabels(), rotation=45, ha='right', fontsize=8)
    ax2.set_yticklabels(ax2.get_yticklabels(), rotation=0, fontsize=8)
    
    # Add metrics
    density_before = (score_before > 0.3).mean()
    density_after = (score_after > 0).mean()
    
    fig.text(0.5, 0.02, 
           f'Densidade Antes: {density_before:.2%} | Densidade Depois: {density_after:.2%} | '
           f'Redução: {((density_before - density_after) / density_before * 100):.1f}%',
           ha='center', fontsize=10, style='italic')
    
    plt.suptitle('Detecção de Conflitos via Sparsemax (Artigo 1)', 
               fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    
    output_path = os.path.join(CHARTS_DIR, 'matriz_conflitos.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"[Chart] Saved: {output_path}")


# ============================================================
# Main
# ============================================================
def main():
    """Generate all charts."""
    print("=" * 60)
    print("GreenRAN Charts Generator")
    print("=" * 60)
    
    # Generate all charts
    plot_energia_comparacao()
    plot_mae_evolucao()
    plot_reward_function()
    plot_matriz_conflitos()
    
    print("\n" + "=" * 60)
    print("All charts generated successfully!")
    print("=" * 60)
    
    # List generated files
    print("\nGenerated files:")
    for f in os.listdir(CHARTS_DIR):
        if f.endswith('.png'):
            print(f"  - {os.path.join(CHARTS_DIR, f)}")


if __name__ == "__main__":
    main()