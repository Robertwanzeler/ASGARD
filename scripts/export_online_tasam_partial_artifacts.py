#!/usr/bin/env python3
"""Export partial summary and figures from an online TA-SAM history JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = ROOT / "runs" / "sac_bootstrap" / "online_tasam_marl"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _moving_average(values: list[float], window: int) -> list[float]:
    out: list[float] = []
    for idx in range(len(values)):
        start = max(0, idx - window + 1)
        out.append(mean(values[start : idx + 1]))
    return out


def _load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def build_summary(rows: list[dict], history_path: Path, figure_path: Path) -> dict:
    returns = [_safe_float(row.get("episode_return")) for row in rows]
    episodes = [int(row.get("episode", 0) or 0) for row in rows]
    critic_rows = [row for row in rows if "critic_loss" in row]
    critic_losses = [_safe_float(row.get("critic_loss")) for row in critic_rows]
    td_var_values = [_safe_float(row.get("td_var_mean")) for row in critic_rows]
    alpha_values = [_safe_float(row.get("alpha")) for row in critic_rows]
    rho_values = [_safe_float(row.get("rho_actor")) for row in critic_rows]
    eval_values = [_safe_float(row.get("eval_return")) for row in critic_rows]
    selected_fraction_values = [_safe_float(row.get("selected_fraction")) for row in critic_rows]

    return {
        "status": "partial_history_only",
        "history_jsonl": str(history_path.resolve()),
        "figure_png": str(figure_path.resolve()),
        "resume_checkpoint_available": False,
        "summary_checkpoint_available": False,
        "completed_episodes": len(rows),
        "last_episode": episodes[-1] if episodes else 0,
        "last_global_step": int(rows[-1].get("global_step", 0) or 0) if rows else 0,
        "mean_return_all": mean(returns) if returns else 0.0,
        "mean_return_last_5": mean(returns[-5:]) if len(returns) >= 5 else (mean(returns) if returns else 0.0),
        "mean_return_last_10": mean(returns[-10:]) if len(returns) >= 10 else (mean(returns) if returns else 0.0),
        "mean_return_last_20": mean(returns[-20:]) if len(returns) >= 20 else (mean(returns) if returns else 0.0),
        "best_episode_return": max(returns) if returns else 0.0,
        "worst_episode_return": min(returns) if returns else 0.0,
        "last_episode_return": returns[-1] if returns else 0.0,
        "last_eval_return_proxy": eval_values[-1] if eval_values else 0.0,
        "last_critic_loss": critic_losses[-1] if critic_losses else 0.0,
        "last_td_var_mean": td_var_values[-1] if td_var_values else 0.0,
        "last_alpha": alpha_values[-1] if alpha_values else 0.0,
        "last_rho_actor": rho_values[-1] if rho_values else 0.0,
        "mean_selected_fraction": mean(selected_fraction_values) if selected_fraction_values else 0.0,
        "train_rows_with_updates": len(critic_rows),
        "notes": [
            "Este artefato parcial aproveita o historico real dos 60 episodios.",
            "Nao existe checkpoint de retomada para continuar do episodio final registrado.",
            "Os dados sao validos para figuras e analise de comportamento do treino online.",
        ],
    }


def render_figure(rows: list[dict], output_path: Path) -> None:
    episodes = [int(row.get("episode", 0) or 0) for row in rows]
    returns = [_safe_float(row.get("episode_return")) for row in rows]
    return_ma = _moving_average(returns, 5)

    critic_rows = [row for row in rows if "critic_loss" in row]
    critic_episodes = [int(row.get("episode", 0) or 0) for row in critic_rows]
    critic_losses = [_safe_float(row.get("critic_loss")) for row in critic_rows]
    td_var_values = [_safe_float(row.get("td_var_mean")) for row in critic_rows]
    alpha_values = [_safe_float(row.get("alpha")) for row in critic_rows]
    rho_values = [_safe_float(row.get("rho_actor")) for row in critic_rows]

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)

    ax = axes[0, 0]
    ax.plot(episodes, returns, color="#1f77b4", linewidth=1.6, label="episode return")
    ax.plot(episodes, return_ma, color="#ff7f0e", linewidth=2.0, label="media movel (5)")
    ax.set_title("Online TA-SAM Return")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Return")
    ax.legend(frameon=True)

    ax = axes[0, 1]
    ax.plot(critic_episodes, critic_losses, color="#d62728", linewidth=1.7, label="critic loss")
    ax.plot(critic_episodes, td_var_values, color="#9467bd", linewidth=1.4, label="td_var_mean")
    ax.set_title("Critic Stability")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Value")
    ax.legend(frameon=True)

    ax = axes[1, 0]
    ax.plot(critic_episodes, alpha_values, color="#2ca02c", linewidth=1.8, label="alpha")
    ax.plot(critic_episodes, rho_values, color="#8c564b", linewidth=1.8, label="rho_actor")
    ax.set_title("Exploration / SAM")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Value")
    ax.legend(frameon=True)

    ax = axes[1, 1]
    ax.plot(critic_episodes, [_safe_float(row.get("eval_return")) for row in critic_rows], color="#17becf", linewidth=1.8, label="eval_return proxy")
    ax.plot(critic_episodes, [_safe_float(row.get("selected_fraction")) for row in critic_rows], color="#7f7f7f", linewidth=1.5, label="selected_fraction")
    ax.set_title("Online Progress Proxy")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Value")
    ax.legend(frameon=True)

    for ax in axes.flatten():
        ax.grid(True, linestyle="--", linewidth=0.45, alpha=0.30)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export partial artifacts for online TA-SAM runs")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="Directory containing online_tasam_marl_history.jsonl")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    history_path = run_dir / "online_tasam_marl_history.jsonl"
    if not history_path.exists():
        raise SystemExit(f"History file not found: {history_path}")

    rows = _load_rows(history_path)
    if not rows:
        raise SystemExit(f"History file is empty: {history_path}")

    figure_path = run_dir / "online_tasam_marl_partial_training_curves.png"
    summary_path = run_dir / "online_tasam_marl_partial_summary.json"

    render_figure(rows, figure_path)
    summary = build_summary(rows, history_path, figure_path)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
