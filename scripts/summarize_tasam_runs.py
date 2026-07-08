#!/usr/bin/env python3
"""Aggregate TA-SAM MARL run summaries across baseline modes."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

MODES = ('no_sam', 'l2', 'actor_sam', 'critic_sam', 'both_sam', 'tasam_selective')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Summarize TA-SAM baseline outputs')
    parser.add_argument('--run-root', required=True, help='Directory containing mode subdirectories')
    parser.add_argument('--output-json', default=None, help='Optional JSON output')
    parser.add_argument('--output-csv', default=None, help='Optional CSV output')
    return parser


def load_summary(path: Path) -> dict[str, Any] | None:
    summary_path = path / 'tasam_marl_summary.json'
    if not summary_path.exists():
        return None
    return json.loads(summary_path.read_text(encoding='utf-8'))


def row_for(mode: str, summary: dict[str, Any]) -> dict[str, Any]:
    final = summary.get('final_metrics') or {}
    return {
        'mode': mode,
        'trainer_backend': summary.get('trainer_backend'),
        'article_faithful_algorithm': summary.get('article_faithful_algorithm'),
        'epochs': summary.get('epochs'),
        'du_count': summary.get('du_count'),
        'sam_rho': summary.get('sam_rho'),
        'sam_rho_final': summary.get('sam_rho_final'),
        'actor_sam_rho': summary.get('actor_sam_rho'),
        'actor_sam_rho_final': summary.get('actor_sam_rho_final'),
        'critic_sam_rho': summary.get('critic_sam_rho'),
        'critic_sam_rho_final': summary.get('critic_sam_rho_final'),
        'activation': summary.get('activation'),
        'actor_hidden_dims': summary.get('actor_hidden_dims'),
        'critic_hidden_dims': summary.get('critic_hidden_dims'),
        'actor_loss': final.get('actor_loss'),
        'critic_loss': final.get('critic_loss'),
        'bc_loss': final.get('bc_loss'),
        'alpha': final.get('alpha'),
        'eval_return': final.get('eval_return'),
        'cumulative_return': final.get('cumulative_return'),
        'selected_fraction': final.get('selected_fraction'),
        'td_var_mean': final.get('td_var_mean'),
        'td_var_max': final.get('td_var_max'),
        'action_var_mean': final.get('action_var_mean'),
        'policy_entropy': final.get('policy_entropy'),
    }


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.run_root)
    rows = []
    for mode in MODES:
        summary = load_summary(root / mode)
        if summary:
            rows.append(row_for(mode, summary))
    payload = {
        'schema': 'greenran.tasam_run_comparison.v2',
        'run_root': str(root),
        'mode_count': len(rows),
        'rows': rows,
    }
    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if args.output_csv:
        out = Path(args.output_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open('w', newline='', encoding='utf-8') as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ['mode'])
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
