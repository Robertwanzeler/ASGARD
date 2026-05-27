#!/usr/bin/env python3
"""
Generate a paper-style figure package for EE-DRL-GreenRAN.

The output mirrors the organization style used in the ARMD/GraphSAGE track:
- a stable package directory under runs/
- architecture figures generated here
- quantitative figures curated from the DRL track
- summary JSON/Markdown manifest
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
except ModuleNotFoundError as exc:
    raise SystemExit(
        "matplotlib is required for generate_eedrl_greenran_paper_figures.py; "
        "run it with ./drlexp/.venv/bin/python or an equivalent environment."
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_PACKAGE = PROJECT_ROOT / "runs" / "eedrl_greenran_final"
DEFAULT_OUTPUT = DEFAULT_PACKAGE / "paper_figures"
METRICS_PATH = PROJECT_ROOT / "drlexp" / "models" / "evaluation_metrics.json"
RUNTIME_DB = Path("/tmp/rapp_data_lake.db")
A3C_MODEL = PROJECT_ROOT / "drlexp" / "models" / "a3c" / "actor_v7.pt"
SBiLSTM_HISTORY_CANDIDATES = [
    PROJECT_ROOT / "drlexp" / "models" / "sbilstm" / "sbilstm_training_history.json",
    PROJECT_ROOT / "models" / "sbilstm" / "sbilstm_training_history.json",
]
A3C_HISTORY_CANDIDATES = [
    PROJECT_ROOT / "drlexp" / "models" / "a3c" / "a3c_training_history.json",
]
A3C_SUMMARY_CANDIDATES = [
    PROJECT_ROOT / "drlexp" / "models" / "a3c" / "a3c_training_summary.json",
]
DRL_COMPARE_MD = PROJECT_ROOT / "drlexp" / "docs" / "COMPARACAO_RF_DRL.md"
ARTICLE_PAGE_12 = PROJECT_ROOT / "runs" / "eedrl_greenran_final" / "article_pages" / "article-12.png"
REAL_DATA_ONLY = True

ARMD_TEXT_STYLE = {
    "font.family": "serif",
    "font.size": 7,
    "axes.titlesize": 8,
    "axes.labelsize": 7,
    "legend.fontsize": 5.2,
    "xtick.labelsize": 6,
    "ytick.labelsize": 6,
}

DRL_STYLES = {
    "baseline": {"color": "#1f77ff", "edgecolor": "black", "linewidth": 0.7},
    "greenran": {"color": "#8b4513", "edgecolor": "black", "linewidth": 0.7},
    "train": {"color": "#274690", "linewidth": 1.15, "linestyle": "-"},
    "validation": {"color": "#d1495b", "linewidth": 1.15, "linestyle": "--"},
    "best": {"color": "#2b7a3d", "linewidth": 1.15, "linestyle": "-."},
    "episode_mean": {"color": "#7d1fb2", "linewidth": 1.15, "linestyle": "-", "alpha": 0.55},
    "rolling_mean": {"color": "#2b7a3d", "linewidth": 1.15, "linestyle": "-"},
    "test": {"color": "#e41a1c", "linewidth": 1.15, "linestyle": "-."},
    "validation_hist": {"color": "#ff8c1a", "linewidth": 1.1, "linestyle": "--"},
}

DRL_LAYOUTS = {
    "quantitative_default": {
        "figsize": (5.8, 4.6),
        "subplots_adjust": {"bottom": 0.28, "left": 0.12, "right": 0.985, "top": 0.88},
        "legend": {
            "loc": "upper center",
            "bbox_to_anchor": (0.5, -0.17),
            "frameon": True,
            "framealpha": 0.95,
            "fancybox": False,
            "borderpad": 0.18,
            "labelspacing": 0.16,
            "columnspacing": 0.7,
            "handlelength": 1.45,
            "handletextpad": 0.4,
            "ncol": 2,
        },
    },
    "quantitative_rotated_labels": {
        "figsize": (5.8, 4.6),
        "subplots_adjust": {"bottom": 0.34, "left": 0.12, "right": 0.985, "top": 0.88},
        "legend": None,
    },
    "supplementary_vertical": {
        "figsize": (5.8, 7.4),
        "subplots_adjust": {"bottom": 0.10, "hspace": 0.55, "left": 0.12, "right": 0.985, "top": 0.94},
    },
    "supplementary_compare": {
        "figsize": (11.6, 5.1),
        "subplots_adjust": {"wspace": 0.04, "left": 0.02, "right": 0.98, "top": 0.90, "bottom": 0.03},
    },
    "architecture": {
        "figsize": (11.5, 6.0),
    },
}

ARCH_BOX_LINEWIDTH = 1.4
ARCH_ARROW_LW = 1.4
ARCH_ARROW_MUTATION_SCALE = 12
ARCH_BOX_FONTSIZE = 8.5
ARCH_ARROW_FONTSIZE = 7
ARCH_TITLE_FONTSIZE = 10

CURATED_QUANTITATIVE = [
    ("fig05_sbilstm_mae_evolution.png", PROJECT_ROOT / "drlexp" / "charts" / "mae_evolucao.png"),
    ("fig06_a3c_reward_function.png", PROJECT_ROOT / "drlexp" / "charts" / "reward_function.png"),
    ("fig07_energy_efficiency_rf_vs_a3c.png", PROJECT_ROOT / "drlexp" / "charts" / "energia_comparacao.png"),
    ("fig08_decision_distribution.png", PROJECT_ROOT / "drlexp" / "charts" / "rewards" / "decision_distribution.png"),
    ("fig09_reward_by_zone.png", PROJECT_ROOT / "drlexp" / "charts" / "rewards" / "reward_by_zone.png"),
    ("fig10_summary_metrics.png", PROJECT_ROOT / "drlexp" / "charts" / "rewards" / "summary_metrics.png"),
]

BASE_CATALOG = [
    {
        "paper_figure": "Fig. 1",
        "greenran_figure": "fig01_eedrl_greenran_system_model.png",
        "section": "architecture",
        "title": "EE-DRL-GreenRAN System Model",
        "role": "Equivalente GreenRAN do modelo de slicing/rede do artigo.",
        "source_type": "generated",
        "readiness": "ready",
        "reason": "Figura arquitetural gerada especificamente para o runtime GreenRAN.",
    },
    {
        "paper_figure": "Fig. 2",
        "greenran_figure": "fig02_eedrl_greenran_timescales.png",
        "section": "architecture",
        "title": "Large vs Small Time-Scale Control",
        "role": "Mostra a separacao entre SBiLSTM e A3C no runtime GreenRAN.",
        "source_type": "generated",
        "readiness": "ready",
        "reason": "Figura arquitetural gerada especificamente para o runtime GreenRAN.",
    },
    {
        "paper_figure": "Fig. 3",
        "greenran_figure": "fig03_eedrl_greenran_components.png",
        "section": "architecture",
        "title": "EE-DRL-GreenRAN Components",
        "role": "Diagrama dos blocos rApp, xApps, ARMD e Data Lake.",
        "source_type": "generated",
        "readiness": "ready",
        "reason": "Figura arquitetural gerada especificamente para o runtime GreenRAN.",
    },
    {
        "paper_figure": "Fig. 4",
        "greenran_figure": "fig04_eedrl_greenran_state_action.png",
        "section": "architecture",
        "title": "State/Action Mapping",
        "role": "Mapeia features de estado e espaco de acao no runtime.",
        "source_type": "generated",
        "readiness": "ready",
        "reason": "Figura arquitetural gerada especificamente para o runtime GreenRAN.",
    },
    {
        "paper_figure": "Fig. 5",
        "greenran_figure": "fig05_sbilstm_mae_evolution.png",
        "section": "quantitative",
        "title": "SBiLSTM MAE Evolution",
        "role": "Figura quantitativa correspondente a evolucao do preditor temporal.",
        "source_type": "curated_chart",
        "source_file": "mae_evolucao.png",
        "readiness": "pending",
        "reason": "O chart atual usa historico simulado em plot_results.py; falta serie oficial de treino.",
    },
    {
        "paper_figure": "Fig. 6",
        "greenran_figure": "fig06_a3c_reward_function.png",
        "section": "quantitative",
        "title": "A3C Reward Function",
        "role": "Curva de reward da trilha A3C no GreenRAN.",
        "source_type": "curated_chart",
        "source_file": "reward_function.png",
        "readiness": "pending",
        "reason": "O chart atual usa episodios simulados em plot_results.py; falta log oficial por episodio.",
    },
    {
        "paper_figure": "Fig. 7",
        "greenran_figure": "fig07_energy_efficiency_rf_vs_a3c.png",
        "section": "quantitative",
        "title": "Cumulative Energy Economy Over Time",
        "role": "Compara a economia energetica acumulada do RF observado contra o replay A3C no mesmo cenario real.",
        "source_type": "generated",
        "readiness": "pending",
        "reason": "Requer derivacao direta a partir de energy_commands e do replay A3C sobre o Data Lake real.",
    },
    {
        "paper_figure": "Fig. 8",
        "greenran_figure": "fig08_decision_distribution.png",
        "section": "quantitative",
        "title": "Cumulative Acceptance Ratio",
        "role": "Evolucao da taxa acumulada de aceitacao de decisoes nao bloqueadas no cenario real.",
        "source_type": "generated",
        "readiness": "pending",
        "reason": "Requer derivacao direta das decisoes reais do Data Lake e do replay A3C.",
    },
    {
        "paper_figure": "Fig. 9",
        "greenran_figure": "fig09_reward_by_zone.png",
        "section": "quantitative",
        "title": "Cumulative Reward Over Time",
        "role": "Reward cumulativo medio no historico real do cenario, comparando RF observado e replay A3C.",
        "source_type": "generated",
        "readiness": "pending",
        "reason": "Requer derivacao direta da funcao de reward aplicada aos estados reais do Data Lake.",
    },
    {
        "paper_figure": "Fig. 10",
        "greenran_figure": "fig10_summary_metrics.png",
        "section": "quantitative",
        "title": "Real Scenario Network Metrics",
        "role": "Serie temporal real de utilizacao de rede e entrega do App2 para o cenario GreenRAN.",
        "source_type": "generated",
        "readiness": "pending",
        "reason": "Requer snapshots reais do App2 persistidos no Data Lake.",
    },
]


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def box(ax, xy, w, h, text, fc="#eef4ff", ec="#274690", fontsize=10, weight="bold"):
    patch = FancyBboxPatch(
        xy,
        w,
        h,
        boxstyle="round,pad=0.02,rounding_size=0.02",
        linewidth=ARCH_BOX_LINEWIDTH,
        edgecolor=ec,
        facecolor=fc,
    )
    ax.add_patch(patch)
    ax.text(xy[0] + w / 2, xy[1] + h / 2, text, ha="center", va="center", fontsize=fontsize, fontweight=weight)
    return patch


def arrow(ax, start, end, color="#333333", text=None, text_offset=(0, 0), lw=1.6):
    arr = FancyArrowPatch(start, end, arrowstyle="->", mutation_scale=ARCH_ARROW_MUTATION_SCALE, linewidth=lw, color=color)
    ax.add_patch(arr)
    if text:
        mx = (start[0] + end[0]) / 2 + text_offset[0]
        my = (start[1] + end[1]) / 2 + text_offset[1]
        ax.text(mx, my, text, fontsize=ARCH_ARROW_FONTSIZE, color=color, ha="center", va="center")


def save_fig(fig, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=260, facecolor="white")
    plt.close(fig)


def apply_armd_axis_style(ax, title: str, xlabel: str = "", ylabel: str = ""):
    plt.rcParams.update(ARMD_TEXT_STYLE)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="both", width=0.8, length=4)
    ax.grid(True, linestyle="--", linewidth=0.45, alpha=0.28)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)


def create_figure(layout_key: str, nrows: int = 1, ncols: int = 1):
    layout = DRL_LAYOUTS[layout_key]
    plt.rcParams.update(ARMD_TEXT_STYLE)
    return plt.subplots(nrows, ncols, figsize=layout["figsize"])


def finalize_figure(fig, path: Path, layout_key: str):
    adjust = DRL_LAYOUTS[layout_key].get("subplots_adjust")
    if adjust:
        fig.subplots_adjust(**adjust)
    save_fig(fig, path)


def apply_standard_legend(ax, layout_key: str = "quantitative_default"):
    legend_cfg = DRL_LAYOUTS[layout_key].get("legend")
    if legend_cfg:
        ax.legend(**legend_cfg)


def setup_architecture_axes(title: str):
    fig, ax = create_figure("architecture")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_title(title, fontsize=ARCH_TITLE_FONTSIZE, fontweight="bold")
    return fig, ax


def load_runtime_reward_frame():
    try:
        import pandas as pd
        import torch
    except ModuleNotFoundError:
        return None

    if not RUNTIME_DB.exists() or not A3C_MODEL.exists():
        return None

    db = sqlite3.connect(str(RUNTIME_DB))
    df = pd.read_sql_query(
        """
        SELECT 
            m.timestamp,
            m.datetime,
            m.cvar_per_ue_us,
            m.latency_p95_per_ue_us,
            m.global_jitter_us,
            m.global_packet_loss_rate,
            m.throughput_kbps,
            m.total_active_ues,
            m.total_active_cameras,
            m.total_critical_ues,
            m.variance_per_ue_us2,
            d.decision as rf_decision,
            e.power_percent as rf_power_percent,
            a.network_utilization_percent as app2_network_utilization_percent,
            a.delivery_success_percent as app2_delivery_success_percent
        FROM extended_metrics m
        JOIN decisions_history d ON m.timestamp = d.timestamp
        LEFT JOIN energy_commands e ON m.timestamp = e.timestamp
        LEFT JOIN app2_snapshots a ON m.timestamp = a.timestamp
        WHERE m.cvar_per_ue_us > 0
        ORDER BY m.timestamp
        """,
        db,
    )
    db.close()
    if df.empty:
        return None

    import sys

    sys.path.insert(0, str(PROJECT_ROOT / "drlexp" / "src"))
    from drl.models.actor import ActorNetwork

    actor = ActorNetwork(state_size=18, num_actions=9)
    actor.load_state_dict(torch.load(A3C_MODEL, map_location="cpu"))
    actor.eval()

    def get_a3c_action(row):
        cvar_ms = row["cvar_per_ue_us"] / 1000.0
        state = torch.FloatTensor(
            [
                cvar_ms,
                0,
                0,
                row["latency_p95_per_ue_us"] / 1000.0,
                row["global_jitter_us"] / 1000.0,
                row["global_packet_loss_rate"] * 100,
                row["throughput_kbps"] / 1000.0,
                row["total_active_ues"],
                row["total_active_cameras"],
                row["total_critical_ues"],
                row["total_active_cameras"] / max(row["total_active_ues"], 1),
                row["total_critical_ues"] / max(row["total_active_ues"], 1),
                row["total_active_ues"] * 10,
                20.0,
                30.0,
                0,
                0,
                row["variance_per_ue_us2"] / 1_000_000.0,
            ]
        )
        with torch.no_grad():
            probs = actor(state)
            action = torch.argmax(probs).item()
        decisions = ["ALLOWED", "ALLOWED", "ALLOWED", "CONDITIONAL", "CONDITIONAL", "CONDITIONAL", "BLOCKED", "BLOCKED", "BLOCKED"]
        powers = ["REDUCE", "MAINTAIN", "INCREASE"]
        probs_list = probs.squeeze().tolist()
        return {
            "decision": decisions[action],
            "power_mode": powers[action % 3],
            "confidence": float(max(probs_list) if probs_list else 0.0),
        }

    def rf_reward(decision, cvar_ms):
        if cvar_ms < 60:
            base = 1.0
        elif cvar_ms < 80:
            base = 0.5
        elif cvar_ms < 100:
            base = 0.0
        else:
            base = -1.0
        power_bonus = 0.2 if decision == "ALLOWED" else (-0.3 if decision == "BLOCKED" else 0.0)
        penalty = -0.5 if decision == "BLOCKED" and cvar_ms < 60 else 0.0
        return base + power_bonus + penalty

    def a3c_reward(decision, cvar_ms):
        if cvar_ms < 60:
            base = 1.0
        elif cvar_ms < 80:
            base = 0.5
        elif cvar_ms < 100:
            base = 0.0
        else:
            base = -2.0
        block_bonus = 1.5 if cvar_ms > 80 and decision == "BLOCKED" else (-1.5 if cvar_ms > 80 and decision != "BLOCKED" else 0.0)
        allowed_bonus = 2.0 if cvar_ms < 60 and decision == "ALLOWED" else (-0.8 if cvar_ms < 60 and decision == "BLOCKED" else 0.0)
        return base + block_bonus + allowed_bonus

    df["cvar_ms"] = df["cvar_per_ue_us"] / 1000.0
    df["latency_p95_ms"] = df["latency_p95_per_ue_us"] / 1000.0
    df["jitter_ms"] = df["global_jitter_us"] / 1000.0
    df["packet_loss_pct"] = df["global_packet_loss_rate"] * 100.0
    df["throughput_mbps"] = df["throughput_kbps"] / 1000.0
    df["critical_ue_ratio"] = df["total_critical_ues"] / df["total_active_ues"].clip(lower=1)
    df["cvar_trend"] = df["cvar_ms"].diff().fillna(0.0)
    action_meta = df.apply(get_a3c_action, axis=1, result_type="expand")
    df["a3c_decision"] = action_meta["decision"]
    df["a3c_power_mode"] = action_meta["power_mode"]
    df["a3c_confidence"] = action_meta["confidence"]
    df["rf_reward"] = df.apply(lambda row: rf_reward(row["rf_decision"], row["cvar_ms"]), axis=1)
    df["a3c_reward"] = df.apply(lambda row: a3c_reward(row["a3c_decision"], row["cvar_ms"]), axis=1)
    df["sample_idx"] = range(1, len(df) + 1)
    return df


def cumulative_mean(values):
    running = []
    total = 0.0
    for idx, value in enumerate(values, start=1):
        total += float(value)
        running.append(total / idx)
    return running


def runtime_line_style(key: str, linestyle: str = "-"):
    return {
        "color": DRL_STYLES[key]["color"],
        "linewidth": 1.15,
        "linestyle": linestyle,
    }


def build_fig05_from_history(out: Path) -> bool:
    history_path = first_existing(SBiLSTM_HISTORY_CANDIDATES)
    if history_path is None:
        return False
    history = load_json(history_path)
    if not isinstance(history, list) or not history:
        return False

    epochs = [int(item.get("epoch", 0)) for item in history]
    train_loss = [float(item.get("train_loss", 0.0)) for item in history]
    val_loss = [float(item.get("val_loss", 0.0)) for item in history]
    best_loss = [float(item.get("best_val_loss", 0.0)) for item in history]

    fig, ax = create_figure("quantitative_default")
    ax.plot(epochs, train_loss, label="Train loss", **DRL_STYLES["train"])
    ax.plot(epochs, val_loss, label="Validation loss", **DRL_STYLES["validation"])
    ax.plot(epochs, best_loss, label="Best validation loss", **DRL_STYLES["best"])
    apply_armd_axis_style(ax, "SBiLSTM Training Evolution", xlabel="Epoch", ylabel="Loss")
    apply_standard_legend(ax)
    finalize_figure(fig, out, "quantitative_default")
    return True


def build_fig06_from_history(out: Path) -> bool:
    history_path = first_existing(A3C_HISTORY_CANDIDATES)
    if history_path is None:
        return False
    history = load_json(history_path)
    if not isinstance(history, list) or not history:
        return False

    grouped: dict[int, list[float]] = {}
    for item in history:
        episode = int(item.get("episode", 0))
        grouped.setdefault(episode, []).append(float(item.get("reward", 0.0)))
    episodes = sorted(grouped)
    rewards = [sum(grouped[e]) / len(grouped[e]) for e in episodes]
    rolling = []
    window = 5
    for idx in range(len(rewards)):
        start = max(0, idx - window + 1)
        rolling.append(sum(rewards[start:idx + 1]) / len(rewards[start:idx + 1]))

    fig, ax = create_figure("quantitative_default")
    ax.plot(episodes, rewards, label="Episode mean reward", **DRL_STYLES["episode_mean"])
    ax.plot(episodes, rolling, label="Rolling mean (5)", **DRL_STYLES["rolling_mean"])
    apply_armd_axis_style(ax, "A3C Reward Evolution", xlabel="Episode", ylabel="Reward")
    apply_standard_legend(ax)
    finalize_figure(fig, out, "quantitative_default")
    return True


def build_fig07_from_runtime(out: Path) -> bool:
    df = load_runtime_reward_frame()
    if df is None or df.empty:
        return False
    try:
        from src.rapp_drl_predictor import DRLPredictor
    except ModuleNotFoundError:
        return False

    fig, ax = create_figure("quantitative_default")
    helper = DRLPredictor()
    replay_power = []
    for _, row in df.iterrows():
        state = {
            "cvar_ms": float(row["cvar_ms"]),
            "cvar_trend": float(row["cvar_trend"]),
            "latency_p95_ms": float(row["latency_p95_ms"]),
            "packet_loss_pct": float(row["packet_loss_pct"]),
            "critical_ues": float(row["total_critical_ues"]),
            "critical_ue_ratio": float(row["critical_ue_ratio"]),
            "variance_ms2": float(row["variance_per_ue_us2"]) / 1_000_000.0,
        }
        risk_score, _ = helper._compute_risk_score(state, float(row["cvar_ms"]))
        _, power_percent, _ = helper._select_policy_action(
            str(row["a3c_decision"]),
            str(row["a3c_power_mode"]),
            float(row["cvar_ms"]),
            float(row["cvar_ms"]),
            float(row["cvar_trend"]),
            float(row["a3c_confidence"]),
            risk_score,
        )
        replay_power.append(float(power_percent))

    rf_power = df["rf_power_percent"].fillna(100.0).astype(float).tolist()
    rf_economy = [100.0 - value for value in rf_power]
    a3c_economy = [100.0 - value for value in replay_power]

    ax.plot(df["sample_idx"], cumulative_mean(rf_economy), label="RF observed", **runtime_line_style("baseline"))
    ax.plot(df["sample_idx"], cumulative_mean(a3c_economy), label="EE-DRL replay", **runtime_line_style("greenran"))
    apply_armd_axis_style(ax, "Cumulative Energy Economy", xlabel="Time Step", ylabel="Running mean economy (%)")
    apply_standard_legend(ax)
    finalize_figure(fig, out, "quantitative_default")
    return True


def build_fig08_from_runtime(out: Path) -> bool:
    df = load_runtime_reward_frame()
    if df is None:
        return False
    rf_acceptance = ((df["rf_decision"] != "BLOCKED").astype(float) * 100.0).tolist()
    a3c_acceptance = ((df["a3c_decision"] != "BLOCKED").astype(float) * 100.0).tolist()

    fig, ax = create_figure("quantitative_default")
    ax.plot(df["sample_idx"], cumulative_mean(rf_acceptance), label="RF observed", **runtime_line_style("baseline"))
    ax.plot(df["sample_idx"], cumulative_mean(a3c_acceptance), label="EE-DRL replay", **runtime_line_style("greenran"))
    apply_armd_axis_style(ax, "Cumulative Acceptance Ratio", xlabel="Time Step", ylabel="Running mean accepted (%)")
    apply_standard_legend(ax)
    finalize_figure(fig, out, "quantitative_default")
    return True


def build_fig09_from_runtime(out: Path) -> bool:
    df = load_runtime_reward_frame()
    if df is None:
        return False

    fig, ax = create_figure("quantitative_default")
    ax.plot(df["sample_idx"], cumulative_mean(df["rf_reward"].tolist()), label="RF observed", **runtime_line_style("baseline"))
    ax.plot(df["sample_idx"], cumulative_mean(df["a3c_reward"].tolist()), label="EE-DRL replay", **runtime_line_style("greenran"))
    apply_armd_axis_style(ax, "Cumulative Reward on Real Scenario", xlabel="Time Step", ylabel="Running mean reward")
    apply_standard_legend(ax)
    finalize_figure(fig, out, "quantitative_default")
    return True


def build_fig10_from_runtime(out: Path) -> bool:
    df = load_runtime_reward_frame()
    if df is None or df.empty:
        return False
    app2 = df.dropna(subset=["app2_network_utilization_percent", "app2_delivery_success_percent"]).copy()
    if app2.empty:
        return False

    app2 = app2.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")

    fig, ax1 = create_figure("quantitative_default")
    ax2 = ax1.twinx()
    ax1.plot(
        app2["sample_idx"],
        app2["app2_network_utilization_percent"],
        label="App2 utilization",
        color=DRL_STYLES["baseline"]["color"],
        linewidth=1.15,
    )
    ax2.plot(
        app2["sample_idx"],
        app2["app2_delivery_success_percent"],
        label="App2 delivery success",
        color=DRL_STYLES["greenran"]["color"],
        linewidth=1.15,
        linestyle="--",
    )
    apply_armd_axis_style(ax1, "Real Scenario Utilization and Delivery", xlabel="Time Step", ylabel="Utilization (%)")
    ax2.set_ylabel("Delivery success (%)")
    ax2.tick_params(axis="y", width=0.8, length=4)
    for spine in ax2.spines.values():
        spine.set_linewidth(0.8)

    lines = ax1.get_lines() + ax2.get_lines()
    labels = [line.get_label() for line in lines]
    legend_cfg = dict(DRL_LAYOUTS["quantitative_default"]["legend"])
    ax1.legend(lines, labels, **legend_cfg)
    finalize_figure(fig, out, "quantitative_default")
    return True


def load_sbilstm_bundle():
    try:
        import importlib.util
        import torch
        from sklearn.model_selection import train_test_split
    except ModuleNotFoundError:
        return None

    history_path = first_existing(SBiLSTM_HISTORY_CANDIDATES)
    model_path = PROJECT_ROOT / "drlexp" / "models" / "sbilstm" / "sbilstm_final.pt"
    if history_path is None or not model_path.exists():
        return None

    module_path = PROJECT_ROOT / "drlexp" / "training" / "train_sbilstm.py"
    spec = importlib.util.spec_from_file_location("greenran_train_sbilstm", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    checkpoint = torch.load(model_path, map_location="cpu")
    config = dict(module.DEFAULT_CONFIG)
    config.update(checkpoint.get("config", {}))

    df = module.load_data(config["db_path"], config["prediction_window"])
    feature_cols = [
        "cvar_ms", "cvar_trend", "cvar_acceleration",
        "latency_p95_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps",
        "total_active_ues", "total_active_cameras", "total_critical_ues",
        "camera_ratio", "critical_ue_ratio", "allocated_rbs",
        "current_power", "power_budget", "hour_sin", "hour_cos", "variance_ms2"
    ]
    df["allocated_rbs"] = df["total_active_ues"] * 10
    df["current_power"] = 20.0
    df["power_budget"] = 30.0
    sequences, targets = module.create_sequences(df, feature_cols, "cvar_ms", config["prediction_window"])

    X_train_val, X_test, y_train_val, y_test = train_test_split(
        sequences, targets, test_size=config["test_size"], random_state=42
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_train_val, y_train_val, test_size=config["val_size"], random_state=42
    )

    model = module.create_sbilstm_model(
        {
            "input_size": config["input_size"],
            "hidden_size_1": config["hidden_size_1"],
            "hidden_size_2": config["hidden_size_2"],
            "num_layers": config["num_layers"],
            "dropout": config["dropout"],
            "output_size": config["output_size"],
        }
    )
    model.load_state_dict(checkpoint["model"])
    model.eval()

    def predict(array):
        preds = []
        batch_size = 128
        with torch.no_grad():
            for start in range(0, len(array), batch_size):
                batch = torch.FloatTensor(array[start:start + batch_size])
                output = model.predict(batch).squeeze().cpu().numpy()
                if getattr(output, "ndim", 0) == 0:
                    preds.append(float(output))
                else:
                    preds.extend(output.tolist())
        return preds

    history = load_json(history_path)
    train_pred = predict(X_train)
    val_pred = predict(X_val)
    test_pred = predict(X_test)

    return {
        "history": history,
        "train_true": y_train,
        "val_true": y_val,
        "test_true": y_test,
        "train_pred": train_pred,
        "val_pred": val_pred,
        "test_pred": test_pred,
    }


def build_supp_sbilstm_learning_curves(out: Path) -> bool:
    bundle = load_sbilstm_bundle()
    if bundle is None:
        return False
    history = bundle["history"]
    if not history:
        return False

    epochs = [int(item["epoch"]) for item in history]
    train_mse = [float(item["train_loss"]) for item in history]
    val_mse = [float(item["val_loss"]) for item in history]
    test_true = bundle["test_true"]
    test_pred = bundle["test_pred"]
    test_mse = sum((float(t) - float(p)) ** 2 for t, p in zip(test_true, test_pred)) / max(1, len(test_true))

    fig, axes = create_figure("supplementary_vertical", nrows=2, ncols=1)

    axes[0].plot(epochs, train_mse, color=DRL_STYLES["baseline"]["color"], linewidth=1.15, label="Train")
    axes[0].plot(epochs, val_mse, color="#2ca02c", linewidth=1.1, linestyle="--", label="Validation")
    axes[0].plot(epochs, [test_mse] * len(epochs), **DRL_STYLES["test"], label="Test")
    apply_armd_axis_style(axes[0], "SBiLSTM MSE by Split", xlabel="Epoch", ylabel="Mean Squared Error (MSE)")
    axes[0].legend(loc="upper right", frameon=True, framealpha=0.95, fancybox=False)
    axes[0].text(0.5, -0.26, "(a)", transform=axes[0].transAxes, ha="center", va="center", fontsize=8)

    zoom_epochs = epochs[max(0, len(epochs) - 25):]
    zoom_train = train_mse[max(0, len(train_mse) - 25):]
    zoom_val = val_mse[max(0, len(val_mse) - 25):]
    axes[1].plot(zoom_epochs, zoom_train, color=DRL_STYLES["baseline"]["color"], linewidth=1.15, label="Train")
    axes[1].plot(zoom_epochs, zoom_val, color="#2ca02c", linewidth=1.1, linestyle="--", label="Validation")
    axes[1].plot(zoom_epochs, [test_mse] * len(zoom_epochs), **DRL_STYLES["test"], label="Test")
    apply_armd_axis_style(axes[1], "SBiLSTM MSE by Split (Late Epochs)", xlabel="Epoch", ylabel="Mean Squared Error (MSE)")
    axes[1].legend(loc="upper right", frameon=True, framealpha=0.95, fancybox=False)
    axes[1].text(0.5, -0.26, "(b)", transform=axes[1].transAxes, ha="center", va="center", fontsize=8)

    finalize_figure(fig, out, "supplementary_vertical")
    return True


def build_supp_sbilstm_residual_histograms(out: Path) -> bool:
    bundle = load_sbilstm_bundle()
    if bundle is None:
        return False

    import numpy as np

    train_res = np.array(bundle["train_true"]) - np.array(bundle["train_pred"])
    val_res = np.array(bundle["val_true"]) - np.array(bundle["val_pred"])
    test_res = np.array(bundle["test_true"]) - np.array(bundle["test_pred"])

    fig, axes = create_figure("supplementary_vertical", nrows=2, ncols=1)
    bins_full = np.linspace(min(train_res.min(), val_res.min(), test_res.min()), max(train_res.max(), val_res.max(), test_res.max()), 24)
    axes[0].hist(train_res, bins=bins_full, histtype="step", linewidth=1.15, color=DRL_STYLES["baseline"]["color"], label="Train")
    axes[0].hist(val_res, bins=bins_full, histtype="step", linewidth=1.1, linestyle="--", color=DRL_STYLES["validation_hist"]["color"], label="Validation")
    axes[0].hist(test_res, bins=bins_full, histtype="step", linewidth=1.1, linestyle="-.", color="#2ca02c", label="Test")
    apply_armd_axis_style(axes[0], "Residual Histogram by Split", xlabel="Errors = Targets - Outputs", ylabel="Instances")
    axes[0].legend(loc="upper right", frameon=True, framealpha=0.95, fancybox=False)
    axes[0].text(0.5, -0.26, "(c)", transform=axes[0].transAxes, ha="center", va="center", fontsize=8)

    q = max(np.quantile(np.abs(np.concatenate([train_res, val_res, test_res])), 0.98), 0.01)
    bins_zoom = np.linspace(-q, q, 24)
    axes[1].hist(train_res, bins=bins_zoom, histtype="step", linewidth=1.15, color=DRL_STYLES["baseline"]["color"], label="Train")
    axes[1].hist(val_res, bins=bins_zoom, histtype="step", linewidth=1.1, linestyle="--", color=DRL_STYLES["validation_hist"]["color"], label="Validation")
    axes[1].hist(test_res, bins=bins_zoom, histtype="step", linewidth=1.1, linestyle="-.", color="#2ca02c", label="Test")
    apply_armd_axis_style(axes[1], "Residual Histogram by Split (Zoom)", xlabel="Errors = Targets - Outputs", ylabel="Instances")
    axes[1].legend(loc="upper right", frameon=True, framealpha=0.95, fancybox=False)
    axes[1].text(0.5, -0.26, "(d)", transform=axes[1].transAxes, ha="center", va="center", fontsize=8)

    finalize_figure(fig, out, "supplementary_vertical")
    return True


def build_article_vs_greenran_comparison(article_crop, greenran_path: Path, title: str, left_label: str, right_label: str, out: Path) -> bool:
    if not ARTICLE_PAGE_12.exists() or not greenran_path.exists():
        return False
    article_img = mpimg.imread(str(ARTICLE_PAGE_12))
    ours_img = mpimg.imread(str(greenran_path))
    x1, y1, x2, y2 = article_crop
    article_slice = article_img[y1:y2, x1:x2]

    fig, axes = create_figure("supplementary_compare", nrows=1, ncols=2)
    for ax, img, label in zip(axes, [article_slice, ours_img], [left_label, right_label]):
        ax.imshow(img)
        ax.axis("off")
        ax.set_title(label, fontsize=8)
    fig.suptitle(title, fontsize=10, fontweight="bold", y=0.98)
    finalize_figure(fig, out, "supplementary_compare")
    return True


def fig01_system_model(out: Path):
    fig, ax = setup_architecture_axes("Fig. 1. EE-DRL-GreenRAN System Model")

    box(ax, (0.03, 0.63), 0.18, 0.22, "App1\nVigilancia", fc="#f5fbff", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.03, 0.36), 0.18, 0.22, "App2\nmMTC", fc="#f5fbff", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.03, 0.09), 0.18, 0.22, "App3\nVeicular", fc="#f5fbff", fontsize=ARCH_BOX_FONTSIZE)

    box(ax, (0.28, 0.70), 0.17, 0.14, "xApp\nSLICER", fc="#fff4d8", ec="#a86b00", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.28, 0.43), 0.17, 0.14, "xApp\nENERGY", fc="#fff4d8", ec="#a86b00", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.28, 0.16), 0.17, 0.14, "xApp\nVEHICLE", fc="#fff4d8", ec="#a86b00", fontsize=ARCH_BOX_FONTSIZE)

    box(ax, (0.53, 0.28), 0.18, 0.42, "rApp\nEE-DRL-GreenRAN\n\nSBiLSTM + A3C\n+ ARMD support", fc="#eaf7ec", ec="#2b7a3d", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.78, 0.58), 0.16, 0.18, "Near-RT RIC\nA1 / Policy", fc="#fff0f0", ec="#a33a3a", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.78, 0.28), 0.16, 0.18, "ns-3 + CARLA\nRAN / Mobility", fc="#fff0f0", ec="#a33a3a", fontsize=ARCH_BOX_FONTSIZE)

    arrow(ax, (0.21, 0.74), (0.28, 0.77), text="KPI / SLA", lw=ARCH_ARROW_LW)
    arrow(ax, (0.21, 0.47), (0.28, 0.50), text="sensor state", lw=ARCH_ARROW_LW)
    arrow(ax, (0.21, 0.20), (0.28, 0.23), text="vehicle state", lw=ARCH_ARROW_LW)
    arrow(ax, (0.45, 0.77), (0.53, 0.60), text="intent", lw=ARCH_ARROW_LW)
    arrow(ax, (0.45, 0.50), (0.53, 0.49), text="intent", lw=ARCH_ARROW_LW)
    arrow(ax, (0.45, 0.23), (0.53, 0.38), text="intent", lw=ARCH_ARROW_LW)
    arrow(ax, (0.71, 0.57), (0.78, 0.67), text="A1 policy", lw=ARCH_ARROW_LW)
    arrow(ax, (0.71, 0.40), (0.78, 0.37), text="control / metrics", lw=ARCH_ARROW_LW)
    arrow(ax, (0.78, 0.54), (0.71, 0.54), color="#7a1f1f", lw=ARCH_ARROW_LW)
    arrow(ax, (0.78, 0.36), (0.71, 0.36), color="#7a1f1f", lw=ARCH_ARROW_LW)
    save_fig(fig, out)


def fig02_timescales(out: Path):
    fig, ax = setup_architecture_axes("Fig. 2. Large vs Small Time-Scale Control in EE-DRL-GreenRAN")

    box(ax, (0.05, 0.60), 0.22, 0.20, "Large Time-Scale\nSBiLSTM\ntraffic / CVaR forecast", fc="#eef4ff", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.39, 0.60), 0.22, 0.20, "Prediction Window\nresource expectation\nper service domain", fc="#eef4ff", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.73, 0.60), 0.22, 0.20, "Policy envelope\nfor next window", fc="#eef4ff", fontsize=ARCH_BOX_FONTSIZE)

    box(ax, (0.05, 0.18), 0.22, 0.20, "Small Time-Scale\nA3C\nonline decision", fc="#eaf7ec", ec="#2b7a3d", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.39, 0.18), 0.22, 0.20, "xApps + rApp\nALLOWED / CONDITIONAL / BLOCKED", fc="#eaf7ec", ec="#2b7a3d", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.73, 0.18), 0.22, 0.20, "A1 / Near-RT RIC\npower + slice action", fc="#eaf7ec", ec="#2b7a3d", fontsize=ARCH_BOX_FONTSIZE)

    arrow(ax, (0.27, 0.70), (0.39, 0.70), text="PW", lw=ARCH_ARROW_LW)
    arrow(ax, (0.61, 0.70), (0.73, 0.70), text="forecast", lw=ARCH_ARROW_LW)
    arrow(ax, (0.27, 0.28), (0.39, 0.28), text="every step", lw=ARCH_ARROW_LW)
    arrow(ax, (0.61, 0.28), (0.73, 0.28), text="runtime actuation", lw=ARCH_ARROW_LW)
    arrow(ax, (0.50, 0.60), (0.50, 0.38), text="guidance", text_offset=(0.05, 0.0), lw=ARCH_ARROW_LW)
    save_fig(fig, out)


def fig03_components(out: Path):
    fig, ax = setup_architecture_axes("Fig. 3. EE-DRL-GreenRAN Components")

    box(ax, (0.05, 0.62), 0.18, 0.16, "Data Lake\nSQLite\nmetrics + decisions", fc="#f6f7fb", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.28, 0.62), 0.18, 0.16, "SBiLSTM\nforecast block", fc="#eef4ff", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.51, 0.62), 0.18, 0.16, "A3C\npolicy block", fc="#eaf7ec", ec="#2b7a3d", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.74, 0.62), 0.18, 0.16, "EE-PA / policy\npower semantics", fc="#fff4d8", ec="#a86b00", fontsize=ARCH_BOX_FONTSIZE)

    box(ax, (0.17, 0.22), 0.22, 0.18, "ARMD-GreenRAN\nvalidated scenarios", fc="#f4ecff", ec="#6a32a8", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.42, 0.22), 0.22, 0.18, "rApp Arbiter\nhard SLA guards", fc="#fff0f0", ec="#a33a3a", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.67, 0.22), 0.22, 0.18, "3 xApps\nSLICER / ENERGY / VEHICLE", fc="#f5fbff", fontsize=ARCH_BOX_FONTSIZE)

    arrow(ax, (0.23, 0.70), (0.28, 0.70), lw=ARCH_ARROW_LW)
    arrow(ax, (0.46, 0.70), (0.51, 0.70), lw=ARCH_ARROW_LW)
    arrow(ax, (0.69, 0.70), (0.74, 0.70), lw=ARCH_ARROW_LW)
    arrow(ax, (0.39, 0.31), (0.42, 0.31), lw=ARCH_ARROW_LW)
    arrow(ax, (0.64, 0.31), (0.67, 0.31), lw=ARCH_ARROW_LW)
    arrow(ax, (0.60, 0.62), (0.53, 0.40), text="policy prior", text_offset=(0.05, 0.02), lw=ARCH_ARROW_LW)
    arrow(ax, (0.28, 0.62), (0.28, 0.40), text="forecast", text_offset=(-0.05, 0.01), lw=ARCH_ARROW_LW)
    arrow(ax, (0.17, 0.40), (0.29, 0.40), text="scenario prior", text_offset=(0.0, 0.03), lw=ARCH_ARROW_LW)
    save_fig(fig, out)


def fig04_state_action(out: Path, metrics: dict):
    sb = metrics.get("sbilstm", {})
    a3c = metrics.get("a3c", {})
    fig, ax = setup_architecture_axes("Fig. 4. EE-DRL-GreenRAN State/Action Mapping")

    box(ax, (0.03, 0.22), 0.26, 0.56, "State (18 features)\n\nCVaR / P95 / jitter\nthroughput / loss\nactive UEs / cameras\nRBs / power budget\nhour encoding", fc="#eef4ff", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.38, 0.55), 0.22, 0.23, "SBiLSTM\nforecast\n\nR²={:.4f}\nMAE={:.2f} ms".format(sb.get("r2", 0.0), sb.get("mae_ms", 0.0)), fc="#eef4ff", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.38, 0.22), 0.22, 0.23, "A3C actor-critic\nonline action\n\nallowed={} %\nconditional={} %\nblocked={} %".format(
        a3c.get("allowed_percent", 0),
        a3c.get("conditional_percent", 0),
        a3c.get("blocked_percent", 0),
    ), fc="#eaf7ec", ec="#2b7a3d", fontsize=ARCH_BOX_FONTSIZE)
    box(ax, (0.70, 0.22), 0.24, 0.56, "Hybrid action space\n\nALLOWED / CONDITIONAL / BLOCKED\nx\nREDUCE / MAINTAIN / INCREASE\n\nRuntime output:\nA1 policy + xApp activation", fc="#fff4d8", ec="#a86b00", fontsize=ARCH_BOX_FONTSIZE)

    arrow(ax, (0.29, 0.63), (0.38, 0.66), text="large scale", lw=ARCH_ARROW_LW)
    arrow(ax, (0.29, 0.36), (0.38, 0.33), text="small scale", lw=ARCH_ARROW_LW)
    arrow(ax, (0.60, 0.66), (0.70, 0.63), text="guidance", lw=ARCH_ARROW_LW)
    arrow(ax, (0.60, 0.33), (0.70, 0.36), text="decision", lw=ARCH_ARROW_LW)
    save_fig(fig, out)


def copy_quantitative(target_dir: Path, eval_metrics: dict) -> list[dict]:
    target_dir.mkdir(parents=True, exist_ok=True)
    copied = []

    generated_builders = {
        "fig05_sbilstm_mae_evolution.png": build_fig05_from_history,
        "fig06_a3c_reward_function.png": build_fig06_from_history,
        "fig07_energy_efficiency_rf_vs_a3c.png": build_fig07_from_runtime,
        "fig08_decision_distribution.png": build_fig08_from_runtime,
        "fig09_reward_by_zone.png": build_fig09_from_runtime,
        "fig10_summary_metrics.png": build_fig10_from_runtime,
    }

    for name, source in CURATED_QUANTITATIVE:
        dest = target_dir / name
        builder = generated_builders.get(name)
        if builder and builder(dest):
            copied.append(
                {"name": name, "source": "official_history_or_summary", "destination": str(dest), "kind": "quantitative"}
            )
            continue
        if REAL_DATA_ONLY:
            continue
        if not source.exists():
            continue
        shutil.copy2(source, dest)
        copied.append({"name": name, "source": str(source), "destination": str(dest), "kind": "quantitative"})
    return copied


def resolve_catalog(quantitative_figures: list[dict]) -> list[dict]:
    available = {item["name"]: item for item in quantitative_figures}
    resolved = []
    for item in BASE_CATALOG:
        item = dict(item)
        if item["section"] == "architecture":
            resolved.append(item)
            continue
        figure = available.get(item["greenran_figure"])
        source = str(figure.get("source", "")) if figure else ""
        if source == "official_history_or_summary":
            item["source_type"] = "generated"
        if item["greenran_figure"] == "fig05_sbilstm_mae_evolution.png":
            if source == "official_history_or_summary":
                item["readiness"] = "ready"
                item["reason"] = "Gerada a partir de sbilstm_training_history.json oficial."
        elif item["greenran_figure"] == "fig06_a3c_reward_function.png":
            if source == "official_history_or_summary":
                item["readiness"] = "ready"
                item["reason"] = "Gerada a partir de a3c_training_history.json oficial."
        elif item["greenran_figure"] == "fig07_energy_efficiency_rf_vs_a3c.png":
            if source == "official_history_or_summary":
                item["readiness"] = "ready"
                item["reason"] = "Gerada a partir de energy_commands reais e replay A3C sobre o mesmo cenario."
        elif item["greenran_figure"] == "fig08_decision_distribution.png":
            if source == "official_history_or_summary":
                item["readiness"] = "ready"
                item["reason"] = "Gerada a partir de decisions_history reais e replay A3C no Data Lake."
        elif item["greenran_figure"] == "fig09_reward_by_zone.png":
            if source == "official_history_or_summary":
                item["readiness"] = "ready"
                item["reason"] = "Gerada a partir da funcao de reward aplicada aos estados reais do Data Lake."
        elif item["greenran_figure"] == "fig10_summary_metrics.png":
            if source == "official_history_or_summary":
                item["readiness"] = "ready"
                item["reason"] = "Gerada a partir de snapshots reais do App2 persistidos no Data Lake."
        resolved.append(item)
    return resolved


def write_summary(summary: dict, json_path: Path, md_path: Path):
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    lines = [
        "# EE-DRL-GreenRAN Paper Figures",
        "",
        "- Track: `EE-DRL-GreenRAN`",
        "- Reference: `Energy-Efficient Deep Reinforcement Learning Assisted Resource Allocation for 5G-RAN Slicing`",
        f"- Architecture figures: `{len(summary['architecture_figures'])}`",
        f"- Quantitative figures: `{len(summary['quantitative_figures'])}`",
        "",
        "## Architecture",
        "",
    ]
    for item in summary["architecture_figures"]:
        lines.append(f"- `{item['name']}`")
    lines.extend(["", "## Quantitative", ""])
    catalog_by_name = {item["greenran_figure"]: item for item in summary.get("catalog", [])}
    for item in summary["quantitative_figures"]:
        source = item["source"]
        source_label = Path(source).name if source not in {"official_history_or_summary"} else "official_history_or_summary"
        status = catalog_by_name.get(item["name"], {}).get("readiness", "unknown")
        lines.append(f"- `{item['name']}` ← `{source_label}` (`{status}`)")
    if summary.get("supplementary_figures"):
        lines.extend(["", "## Supplementary Comparison", ""])
        for item in summary["supplementary_figures"]:
            lines.append(f"- `{item['name']}` ← `{Path(item['destination']).name}`")
    lines.extend(["", "## Catalog", "", "| Paper | GreenRAN | Tipo | Status | Papel no texto |", "|------|----------|------|--------|----------------|"])
    for item in summary.get("catalog", []):
        lines.append(
            f"| `{item['paper_figure']}` | `{item['greenran_figure']}` | `{item['source_type']}` | `{item['readiness']}` | {item['role']} |"
        )
    lines.extend(["", "## Pending Reasons", ""])
    for item in summary.get("catalog", []):
        if item.get("readiness") != "ready":
            lines.append(f"- `{item['greenran_figure']}`: {item['reason']}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate paper-style figures for EE-DRL-GreenRAN.")
    parser.add_argument("--package-root", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    package_root = args.package_root.resolve()
    output_dir = args.output_dir.resolve()
    architecture_dir = output_dir / "architecture"
    quantitative_dir = output_dir / "quantitative"
    comparison_dir = output_dir / "comparison"
    architecture_dir.mkdir(parents=True, exist_ok=True)
    quantitative_dir.mkdir(parents=True, exist_ok=True)
    comparison_dir.mkdir(parents=True, exist_ok=True)

    metrics = load_json(METRICS_PATH)

    fig01_system_model(architecture_dir / "fig01_eedrl_greenran_system_model.png")
    fig02_timescales(architecture_dir / "fig02_eedrl_greenran_timescales.png")
    fig03_components(architecture_dir / "fig03_eedrl_greenran_components.png")
    fig04_state_action(architecture_dir / "fig04_eedrl_greenran_state_action.png", metrics)

    architecture_figures = [
        {"name": "fig01_eedrl_greenran_system_model.png", "kind": "architecture"},
        {"name": "fig02_eedrl_greenran_timescales.png", "kind": "architecture"},
        {"name": "fig03_eedrl_greenran_components.png", "kind": "architecture"},
        {"name": "fig04_eedrl_greenran_state_action.png", "kind": "architecture"},
    ]
    quantitative_figures = copy_quantitative(quantitative_dir, metrics)
    catalog = resolve_catalog(quantitative_figures)
    supplementary_figures = []
    supp_a = comparison_dir / "suppA_sbilstm_learning_curves.png"
    if build_supp_sbilstm_learning_curves(supp_a):
        supplementary_figures.append({"name": "suppA_sbilstm_learning_curves.png", "destination": str(supp_a)})
    supp_b = comparison_dir / "suppB_sbilstm_residual_histograms.png"
    if build_supp_sbilstm_residual_histograms(supp_b):
        supplementary_figures.append({"name": "suppB_sbilstm_residual_histograms.png", "destination": str(supp_b)})
    comp_c = comparison_dir / "suppC_article_vs_greenran_learning.png"
    if build_article_vs_greenran_comparison(
        article_crop=(660, 110, 1145, 640),
        greenran_path=supp_a,
        title="Article Fig. 5(a,b) vs EE-DRL-GreenRAN",
        left_label="Article reference",
        right_label="EE-DRL-GreenRAN",
        out=comp_c,
    ):
        supplementary_figures.append({"name": "suppC_article_vs_greenran_learning.png", "destination": str(comp_c)})
    comp_d = comparison_dir / "suppD_article_vs_greenran_histograms.png"
    if build_article_vs_greenran_comparison(
        article_crop=(640, 640, 1160, 1265),
        greenran_path=supp_b,
        title="Article Fig. 5(c,d) vs EE-DRL-GreenRAN",
        left_label="Article reference",
        right_label="EE-DRL-GreenRAN",
        out=comp_d,
    ):
        supplementary_figures.append({"name": "suppD_article_vs_greenran_histograms.png", "destination": str(comp_d)})

    summary = {
        "schema": "greenran.eedrl_greenran_paper_figures.v1",
        "display_name": "EE-DRL-GreenRAN",
        "package_root": str(package_root),
        "output_dir": str(output_dir),
        "architecture_figures": architecture_figures,
        "quantitative_figures": quantitative_figures,
        "supplementary_figures": supplementary_figures,
        "catalog": catalog,
        "notes": [
            "Architecture figures are generated specifically for the GreenRAN adaptation of the EE-DRL-RA paper.",
            "Quantitative figures are generated only from real GreenRAN scenario artifacts and official training histories.",
            "Legacy PNG charts under drlexp/charts are ignored in real-data-only mode.",
            "This package intentionally mirrors the organization style used in the ARMD-GreenRAN track.",
        ],
    }

    write_summary(
        summary,
        output_dir / "paper_figures_summary.json",
        output_dir / "paper_figures_summary.md",
    )

    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "architecture_dir": str(architecture_dir),
                "quantitative_dir": str(quantitative_dir),
                "architecture_count": len(architecture_figures),
                "quantitative_count": len(quantitative_figures),
                "summary_json": str(output_dir / "paper_figures_summary.json"),
                "summary_md": str(output_dir / "paper_figures_summary.md"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
