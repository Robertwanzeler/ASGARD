#!/usr/bin/env python3
"""Plot TA-SAM article-style ablation panels from run histories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINES = ROOT / 'runs' / 'tasam_article_reproduction' / 'baselines'
MODES = ('both_sam', 'no_sam', 'l2', 'actor_sam', 'critic_sam', 'tasam_selective')
LABELS = {
    'both_sam': 'both SAM',
    'no_sam': 'no SAM',
    'l2': 'L2-reg',
    'actor_sam': 'actor SAM',
    'critic_sam': 'critic SAM',
    'tasam_selective': 'TA-SAM',
}
COLORS = {
    'both_sam': '#1f77b4',
    'no_sam': '#ff7f0e',
    'l2': '#f2c14e',
    'actor_sam': '#9467bd',
    'critic_sam': '#7fb13d',
    'tasam_selective': '#2ca02c',
}
SCENARIOS = {
    'equal': '(a) equal $\\rho$ scenario, $\\rho_{actor}=\\rho_{critic}=0.01$',
    'non_equal': '(b) non-equal $\\rho$ scenario, $\\rho_{actor}=0.05$, $\\rho_{critic}=0.01$',
    'dynamic': '(c) dynamic $\\rho$ scenario, uniformly from $0.5$ to $0.01$',
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Plot TA-SAM article ablation panels')
    parser.add_argument('--baselines-root', default=str(DEFAULT_BASELINES), help='Root containing equal/non_equal/dynamic scenario folders')
    parser.add_argument('--output', default=str(DEFAULT_BASELINES / 'tasam_article_ablation.png'), help='Output PNG path')
    parser.add_argument('--metric', choices=('cumulative_return', 'eval_return'), default='cumulative_return', help='History metric to plot')
    return parser


def load_history(summary_path: Path, metric: str) -> tuple[list[int], list[float]]:
    payload = json.loads(summary_path.read_text(encoding='utf-8'))
    history = payload.get('history') or []
    xs = [int(row.get('epoch', idx + 1)) for idx, row in enumerate(history)]
    ys = [float(row.get(metric, 0.0) or 0.0) for row in history]
    return xs, ys


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.baselines_root)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.8), sharey=False)
    for ax, (scenario, subtitle) in zip(axes, SCENARIOS.items()):
        scenario_root = root / scenario
        for mode in MODES:
            summary_path = scenario_root / mode / 'tasam_marl_summary.json'
            if not summary_path.exists():
                continue
            xs, ys = load_history(summary_path, args.metric)
            ax.plot(xs, ys, marker='o', linewidth=1.6, markersize=3.2, label=LABELS.get(mode, mode), color=COLORS.get(mode))
        ax.set_title(subtitle, fontsize=11, y=-0.38)
        ax.set_xlabel('Epoch')
        ax.set_ylabel('Cumulative return' if args.metric == 'cumulative_return' else 'Eval return')
        ax.grid(True, alpha=0.25)
        ax.legend(loc='lower right', fontsize=8)

    fig.suptitle('TA-SAM Article Ablation on GreenRAN Offline Trace', fontsize=15, fontweight='bold')
    fig.tight_layout()
    fig.savefig(output, dpi=200, bbox_inches='tight')
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
