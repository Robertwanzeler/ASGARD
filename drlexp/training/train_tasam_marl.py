#!/usr/bin/env python3
"""Train an initial TA-SAM-style MARL scaffold on exported GreenRAN DU traces."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TRAINING_DIR = Path(__file__).resolve().parent
DRLEXP_SRC = TRAINING_DIR.parent / 'src'
sys.path.insert(0, str(DRLEXP_SRC))

from drl.ta_sam_marl import TASAMMultiAgentTrainer, load_marl_trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Train initial TA-SAM MARL scaffold for GreenRAN')
    parser.add_argument('--trace-jsonl', required=True, help='Input MARL trace JSONL path')
    parser.add_argument('--output-dir', required=True, help='Output directory')
    parser.add_argument('--epochs', type=int, default=25, help='Training epochs')
    parser.add_argument('--lr', type=float, default=3e-4, help='Learning rate')
    parser.add_argument('--sam-rho', type=float, default=0.05, help='Initial SAM rho')
    parser.add_argument('--td-var-threshold', type=float, default=0.01, help='Selective SAM threshold over TD proxy variance')
    parser.add_argument('--min-selected-fraction', type=float, default=0.10, help='Minimum fraction of agent updates selected each epoch')
    parser.add_argument('--warmup-epochs', type=int, default=2, help='Force actor updates during early epochs before selective SAM takes over')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    records = load_marl_trace(args.trace_jsonl)
    if not records:
        raise SystemExit('No MARL records found in trace file')

    du_count = len(records[0].du_states)
    du_state_dim = len(records[0].du_states[0])
    global_state_dim = len(records[0].global_state)
    trainer = TASAMMultiAgentTrainer(
        du_count=du_count,
        du_state_dim=du_state_dim,
        global_state_dim=global_state_dim,
        lr=args.lr,
        rho=args.sam_rho,
    )

    history = []
    for epoch in range(1, args.epochs + 1):
        metrics = trainer.train_epoch(
            records,
            td_var_threshold=args.td_var_threshold,
            min_selected_fraction=args.min_selected_fraction,
            warmup=epoch <= args.warmup_epochs,
        )
        metrics['epoch'] = epoch
        metrics['warmup'] = epoch <= args.warmup_epochs
        history.append(metrics)

    summary = {
        'trace_jsonl': str(Path(args.trace_jsonl).resolve()),
        'epochs': args.epochs,
        'du_count': du_count,
        'du_state_dim': du_state_dim,
        'global_state_dim': global_state_dim,
        'sam_rho': args.sam_rho,
        'requested_td_var_threshold': args.td_var_threshold,
        'min_selected_fraction': args.min_selected_fraction,
        'warmup_epochs': args.warmup_epochs,
        'final_metrics': history[-1],
        'history': history,
    }
    trainer.export_checkpoint(output_dir, metadata=summary)
    (output_dir / 'tasam_marl_summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary['final_metrics'], indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
