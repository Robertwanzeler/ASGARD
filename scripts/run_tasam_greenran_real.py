#!/usr/bin/env python3
"""Run the TA-SAM article method and baselines on the real GreenRAN collection."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_COLLECTION_DB = ROOT / 'runs' / 'tasam_article_ns3_collection' / 'rapp_data_lake.db'
DEFAULT_OUTPUT = ROOT / 'runs' / 'tasam_greenran_real'
DEFAULT_TRAIN_PYTHON = ROOT / 'drlexp' / '.venv' / 'bin' / 'python'
MODES = ('no_sam', 'l2', 'actor_sam', 'critic_sam', 'both_sam', 'tasam_selective')


def resolve_default_db() -> Path:
    for env_name in ('GREENRAN_TASAM_ACTIVE_DB', 'GREENRAN_DB_PATH'):
        raw = os.environ.get(env_name, '').strip()
        if raw:
            return Path(raw)
    return OFFICIAL_COLLECTION_DB


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run TA-SAM on real GreenRAN/rApp data')
    parser.add_argument('--db', default=str(resolve_default_db()), help='GreenRAN data-lake SQLite DB')
    parser.add_argument('--output-root', default=str(DEFAULT_OUTPUT), help='Output directory')
    parser.add_argument('--trace-jsonl', default=None, help='Reuse an existing raw or filtered trace JSONL')
    parser.add_argument('--skip-export', action='store_true', help='Skip DB export and reuse the trace path already on disk')
    parser.add_argument('--trace-profile', choices=('raw', 'article_faithful', 'article_stress', 'postfix_clean'), default='raw', help='Dataset profile to train from')
    parser.add_argument('--limit', type=int, default=None, help='Optional transition limit')
    parser.add_argument('--epochs', type=int, default=25, help='Training epochs; maps to paper Nt')
    parser.add_argument('--modes', default=','.join(MODES), help='Comma-separated modes')
    parser.add_argument('--allow-proxy', action='store_true', help='Keep proxy-latency rows')
    parser.add_argument('--max-p95-ms', type=float, default=None, help='Optional upper bound for filtered latency_p95_ms')
    parser.add_argument('--max-cvar-ms', type=float, default=None, help='Optional upper bound for filtered cvar_ms')
    parser.add_argument('--train-python', default=None, help='Training Python executable')
    parser.add_argument('--dry-run', action='store_true', help='Print commands only')
    return parser


def run(cmd: list[str], dry_run: bool) -> None:
    print(' '.join(cmd), flush=True)
    if not dry_run:
        subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    output_root = Path(args.output_root)
    raw_trace = Path(args.trace_jsonl) if args.trace_jsonl else output_root / 'tasam_greenran_real_trace.jsonl'
    export_summary = output_root / 'tasam_greenran_real_export_summary.json'
    train_trace = raw_trace
    modes = tuple(mode.strip() for mode in args.modes.split(',') if mode.strip())
    invalid = sorted(set(modes) - set(MODES))
    if invalid:
        raise SystemExit(f'Invalid modes: {invalid}. Valid modes: {MODES}')
    if not args.dry_run and not db_path.exists():
        raise SystemExit(
            'Active GreenRAN collection DB not found: '
            f'{db_path}. Pass --db explicitly or set GREENRAN_TASAM_ACTIVE_DB.'
        )
    train_py = args.train_python or str(DEFAULT_TRAIN_PYTHON if DEFAULT_TRAIN_PYTHON.exists() else sys.executable)
    output_root.mkdir(parents=True, exist_ok=True)

    if not args.skip_export and args.trace_jsonl is None:
        export_cmd = [
            sys.executable,
            str(ROOT / 'scripts' / 'export_tasam_article_dataset.py'),
            '--db',
            str(db_path),
            '--output-jsonl',
            str(raw_trace),
            '--summary-json',
            str(export_summary),
        ]
        if args.limit is not None:
            export_cmd.extend(['--limit', str(args.limit)])
        if args.allow_proxy:
            export_cmd.append('--allow-proxy')
        run(export_cmd, args.dry_run)
    elif not raw_trace.exists() and not args.dry_run:
        raise SystemExit(f'Trace not found: {raw_trace}')

    if args.trace_profile != 'raw':
        filtered_trace = output_root / f'tasam_greenran_real_trace_{args.trace_profile}.jsonl'
        filtered_summary = output_root / f'tasam_greenran_real_filter_{args.trace_profile}_summary.json'
        filter_cmd = [
            sys.executable,
            str(ROOT / 'scripts' / 'filter_tasam_trace.py'),
            '--input-jsonl',
            str(raw_trace),
            '--output-jsonl',
            str(filtered_trace),
            '--summary-json',
            str(filtered_summary),
            '--profile',
            args.trace_profile,
        ]
        if args.max_p95_ms is not None:
            filter_cmd.extend(['--max-p95-ms', str(args.max_p95_ms)])
        if args.max_cvar_ms is not None:
            filter_cmd.extend(['--max-cvar-ms', str(args.max_cvar_ms)])
        run(filter_cmd, args.dry_run)
        train_trace = filtered_trace

    for mode in modes:
        cmd = [
            train_py,
            str(ROOT / 'drlexp' / 'training' / 'train_tasam_marl.py'),
            '--trace-jsonl',
            str(train_trace),
            '--output-dir',
            str(output_root / mode),
            '--epochs',
            str(args.epochs),
            '--trainer-backend',
            'article_sac',
            '--lr',
            '0.0001',
            '--alpha-lr',
            '0.0001',
            '--sam-mode',
            mode,
            '--actor-sam-rho',
            '0.5',
            '--actor-sam-rho-final',
            '0.01',
            '--critic-sam-rho',
            '0.5',
            '--critic-sam-rho-final',
            '0.01',
            '--td-var-threshold',
            '0.01',
            '--warmup-epochs',
            '2',
            '--bc-weight',
            '0.0',
            '--value-weight',
            '0.0',
            '--gamma',
            '0.99',
            '--tau',
            '0.01',
            '--alpha-init',
            '0.03',
            '--target-entropy-scale',
            '1.0',
            '--batch-size',
            '128',
            '--seed',
            '42',
            '--article-hidden',
            '--activation',
            'tanh',
        ]
        if mode == 'l2':
            cmd.extend(['--l2-weight', '0.0001'])
        run(cmd, args.dry_run)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
