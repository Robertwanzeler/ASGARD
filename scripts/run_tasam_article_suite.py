#!/usr/bin/env python3
"""Run the article-aligned TA-SAM MARL export and ablation suite."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_COLLECTION_DB = ROOT / 'runs' / 'tasam_article_ns3_collection' / 'rapp_data_lake.db'
DEFAULT_OUTPUT = ROOT / 'runs' / 'sac_bootstrap' / 'tasam_article_suite'
DEFAULT_TRAIN_PYTHON = ROOT / 'drlexp' / '.venv' / 'bin' / 'python'
MODES = ('no_sam', 'l2', 'actor_sam', 'critic_sam', 'both_sam', 'tasam_selective')


def resolve_default_db() -> Path:
    for env_name in ('GREENRAN_TASAM_ACTIVE_DB', 'GREENRAN_DB_PATH'):
        raw = os.environ.get(env_name, '').strip()
        if raw:
            return Path(raw)
    return OFFICIAL_COLLECTION_DB


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Prepare and train TA-SAM MARL article ablations')
    parser.add_argument('--db', default=str(resolve_default_db()), help='Input GreenRAN data-lake SQLite DB')
    parser.add_argument('--output-root', default=str(DEFAULT_OUTPUT), help='Suite output directory')
    parser.add_argument('--trace-jsonl', default=None, help='Reuse/export trace path; defaults inside output-root')
    parser.add_argument('--summary-json', default=None, help='Exporter summary path; defaults inside output-root')
    parser.add_argument('--limit', type=int, default=None, help='Optional transition limit for smoke runs')
    parser.add_argument('--epochs', type=int, default=25, help='Training epochs per mode')
    parser.add_argument('--trainer-backend', choices=('article_sac', 'scaffold'), default='article_sac', help='Training backend')
    parser.add_argument('--lr', type=float, default=1e-4, help='Training learning rate')
    parser.add_argument('--alpha-lr', type=float, default=1e-4, help='Entropy-temperature learning rate')
    parser.add_argument('--sam-rho', type=float, default=0.5, help='Fallback initial SAM rho')
    parser.add_argument('--sam-rho-final', type=float, default=0.01, help='Fallback final TA-SAM rho')
    parser.add_argument('--actor-sam-rho', type=float, default=None, help='Actor initial rho override')
    parser.add_argument('--actor-sam-rho-final', type=float, default=None, help='Actor final rho override')
    parser.add_argument('--critic-sam-rho', type=float, default=None, help='Critic initial rho override')
    parser.add_argument('--critic-sam-rho-final', type=float, default=None, help='Critic final rho override')
    parser.add_argument('--td-var-threshold', type=float, default=0.01, help='Selective TD threshold')
    parser.add_argument('--warmup-epochs', type=int, default=2, help='Warmup epochs')
    parser.add_argument('--bc-weight', type=float, default=0.0, help='Behavior-cloning anchor weight')
    parser.add_argument('--value-weight', type=float, default=0.0, help='Legacy scaffold-only actor value weight')
    parser.add_argument('--l2-weight', type=float, default=1e-4, help='L2 baseline regularization')
    parser.add_argument('--article-hidden', action='store_true', help='Use paper-sized 300,400,400 hidden layers')
    parser.add_argument('--activation', choices=('relu', 'tanh'), default='tanh', help='Hidden-layer activation')
    parser.add_argument('--gamma', type=float, default=0.99, help='Discount factor for article_sac')
    parser.add_argument('--tau', type=float, default=0.01, help='Target critic soft-update factor')
    parser.add_argument('--alpha-init', type=float, default=0.03, help='Initial entropy temperature')
    parser.add_argument('--target-entropy-scale', type=float, default=1.0, help='Target entropy scale')
    parser.add_argument('--batch-size', type=int, default=128, help='Mini-batch size')
    parser.add_argument('--updates-per-epoch', type=int, default=None, help='Gradient updates per epoch')
    parser.add_argument('--actor-update-interval', type=int, default=1, help='Update actors every N critic steps')
    parser.add_argument('--seed', type=int, default=42, help='Deterministic seed')
    parser.add_argument('--train-python', default=None, help='Python executable for TA-SAM training; defaults to drlexp/.venv when present')
    parser.add_argument('--modes', default=','.join(MODES), help='Comma-separated modes to run')
    parser.add_argument('--skip-export', action='store_true', help='Reuse an existing trace-jsonl')
    parser.add_argument('--allow-proxy', action='store_true', help='Keep proxy-latency rows in the exported trace')
    parser.add_argument('--dry-run', action='store_true', help='Print commands without executing them')
    return parser


def run_command(cmd: list[str], dry_run: bool) -> None:
    print(' '.join(cmd), flush=True)
    if dry_run:
        return
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    output_root = Path(args.output_root)
    trace_jsonl = Path(args.trace_jsonl) if args.trace_jsonl else output_root / 'tasam_article_trace.jsonl'
    summary_json = Path(args.summary_json) if args.summary_json else output_root / 'tasam_article_export_summary.json'
    modes = tuple(mode.strip() for mode in args.modes.split(',') if mode.strip())
    invalid = sorted(set(modes) - set(MODES))
    if invalid:
        raise SystemExit(f'Invalid modes: {invalid}. Valid modes: {MODES}')
    if not args.dry_run and not db_path.exists():
        raise SystemExit(
            'Active TA-SAM collection DB not found: '
            f'{db_path}. Pass --db explicitly or set GREENRAN_TASAM_ACTIVE_DB.'
        )

    output_root.mkdir(parents=True, exist_ok=True)
    export_py = sys.executable
    train_py = args.train_python or str(DEFAULT_TRAIN_PYTHON if DEFAULT_TRAIN_PYTHON.exists() else sys.executable)

    if not args.skip_export:
        export_cmd = [
            export_py,
            str(ROOT / 'scripts' / 'export_tasam_article_dataset.py'),
            '--db',
            str(db_path),
            '--output-jsonl',
            str(trace_jsonl),
            '--summary-json',
            str(summary_json),
        ]
        if args.limit is not None:
            export_cmd.extend(['--limit', str(args.limit)])
        if args.allow_proxy:
            export_cmd.append('--allow-proxy')
        run_command(export_cmd, args.dry_run)

    for mode in modes:
        train_cmd = [
            train_py,
            str(ROOT / 'drlexp' / 'training' / 'train_tasam_marl.py'),
            '--trace-jsonl',
            str(trace_jsonl),
            '--output-dir',
            str(output_root / mode),
            '--epochs',
            str(args.epochs),
            '--trainer-backend',
            args.trainer_backend,
            '--lr',
            str(args.lr),
            '--alpha-lr',
            str(args.alpha_lr),
            '--sam-mode',
            mode,
            '--sam-rho',
            str(args.sam_rho),
            '--sam-rho-final',
            str(args.sam_rho_final),
            '--td-var-threshold',
            str(args.td_var_threshold),
            '--warmup-epochs',
            str(args.warmup_epochs),
            '--bc-weight',
            str(args.bc_weight),
            '--value-weight',
            str(args.value_weight),
            '--activation',
            args.activation,
            '--gamma',
            str(args.gamma),
            '--tau',
            str(args.tau),
            '--alpha-init',
            str(args.alpha_init),
            '--target-entropy-scale',
            str(args.target_entropy_scale),
            '--batch-size',
            str(args.batch_size),
            '--actor-update-interval',
            str(args.actor_update_interval),
            '--seed',
            str(args.seed),
        ]
        if args.updates_per_epoch is not None:
            train_cmd.extend(['--updates-per-epoch', str(args.updates_per_epoch)])
        if args.actor_sam_rho is not None:
            train_cmd.extend(['--actor-sam-rho', str(args.actor_sam_rho)])
        if args.actor_sam_rho_final is not None:
            train_cmd.extend(['--actor-sam-rho-final', str(args.actor_sam_rho_final)])
        if args.critic_sam_rho is not None:
            train_cmd.extend(['--critic-sam-rho', str(args.critic_sam_rho)])
        if args.critic_sam_rho_final is not None:
            train_cmd.extend(['--critic-sam-rho-final', str(args.critic_sam_rho_final)])
        if mode == 'l2':
            train_cmd.extend(['--l2-weight', str(args.l2_weight)])
        if args.article_hidden:
            train_cmd.append('--article-hidden')
        run_command(train_cmd, args.dry_run)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
