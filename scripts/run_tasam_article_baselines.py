#!/usr/bin/env python3
"""Run TA-SAM article baselines from the real ns-3 article collection DB."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_COLLECTION_DB = ROOT / 'runs' / 'tasam_article_ns3_collection' / 'rapp_data_lake.db'
DEFAULT_OUTPUT = ROOT / 'runs' / 'tasam_article_reproduction' / 'baselines'
DEFAULT_TRAIN_PYTHON = ROOT / 'drlexp' / '.venv' / 'bin' / 'python'
MODES = ('no_sam', 'l2', 'actor_sam', 'critic_sam', 'both_sam', 'tasam_selective')
RHO_SCENARIOS = ('equal', 'non_equal', 'dynamic')


def resolve_default_db() -> Path:
    for env_name in ('GREENRAN_TASAM_ACTIVE_DB', 'GREENRAN_DB_PATH'):
        raw = os.environ.get(env_name, '').strip()
        if raw:
            return Path(raw)
    return OFFICIAL_COLLECTION_DB


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run TA-SAM paper baselines')
    parser.add_argument('--db', default=str(resolve_default_db()), help='ns-3 article collection SQLite DB')
    parser.add_argument('--output-root', default=str(DEFAULT_OUTPUT), help='Output directory')
    parser.add_argument('--trace-jsonl', default=None, help='Trace output path; defaults inside output-root')
    parser.add_argument('--summary-json', default=None, help='Exporter summary path; defaults inside output-root')
    parser.add_argument('--limit', type=int, default=None, help='Optional transition limit')
    parser.add_argument('--epochs', type=int, default=25, help='Training epochs; maps to paper Nt')
    parser.add_argument('--seed', type=int, default=42, help='Deterministic seed')
    parser.add_argument('--modes', default=','.join(MODES), help='Comma-separated baseline modes')
    parser.add_argument('--rho-scenario', choices=RHO_SCENARIOS, default='dynamic', help='Paper rho scenario to reproduce')
    parser.add_argument('--allow-proxy', action='store_true', help='Keep proxy-latency rows in the exported trace')
    parser.add_argument('--train-python', default=None, help='Training Python executable')
    parser.add_argument('--dry-run', action='store_true', help='Print commands only')
    return parser


def run(cmd: list[str], dry_run: bool) -> None:
    print(' '.join(cmd), flush=True)
    if not dry_run:
        subprocess.run(cmd, cwd=ROOT, check=True)


def rho_args(scenario: str) -> list[str]:
    if scenario == 'equal':
        return ['--actor-sam-rho', '0.01', '--actor-sam-rho-final', '0.01', '--critic-sam-rho', '0.01', '--critic-sam-rho-final', '0.01']
    if scenario == 'non_equal':
        return ['--actor-sam-rho', '0.05', '--actor-sam-rho-final', '0.05', '--critic-sam-rho', '0.01', '--critic-sam-rho-final', '0.01']
    return ['--actor-sam-rho', '0.5', '--actor-sam-rho-final', '0.01', '--critic-sam-rho', '0.5', '--critic-sam-rho-final', '0.01']


def main() -> int:
    args = build_parser().parse_args()
    output_root = Path(args.output_root) / args.rho_scenario
    db_path = Path(args.db)
    trace = Path(args.trace_jsonl) if args.trace_jsonl else output_root / 'tasam_article_trace.jsonl'
    summary_json = Path(args.summary_json) if args.summary_json else output_root / 'tasam_article_export_summary.json'
    modes = tuple(mode.strip() for mode in args.modes.split(',') if mode.strip())
    invalid = sorted(set(modes) - set(MODES))
    if invalid:
        raise SystemExit(f'Invalid modes: {invalid}. Valid modes: {MODES}')
    if not args.dry_run and not db_path.exists():
        raise SystemExit(
            'ns-3 article collection DB not found: '
            f'{db_path}. Run scripts/run_tasam_article_ns3_collection.sh first.'
        )
    train_py = args.train_python or str(DEFAULT_TRAIN_PYTHON if DEFAULT_TRAIN_PYTHON.exists() else sys.executable)
    export_cmd = [
        sys.executable,
        str(ROOT / 'scripts' / 'export_tasam_article_dataset.py'),
        '--db',
        str(db_path),
        '--output-jsonl',
        str(trace),
        '--summary-json',
        str(summary_json),
    ]
    if args.limit is not None:
        export_cmd.extend(['--limit', str(args.limit)])
    if args.allow_proxy:
        export_cmd.append('--allow-proxy')
    run(export_cmd, args.dry_run)
    for mode in modes:
        cmd = [
            train_py,
            str(ROOT / 'drlexp' / 'training' / 'train_tasam_marl.py'),
            '--trace-jsonl',
            str(trace),
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
            str(args.seed),
            '--article-hidden',
            '--activation',
            'tanh',
        ]
        cmd.extend(rho_args(args.rho_scenario))
        if mode == 'l2':
            cmd.extend(['--l2-weight', '0.0001'])
        run(cmd, args.dry_run)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
