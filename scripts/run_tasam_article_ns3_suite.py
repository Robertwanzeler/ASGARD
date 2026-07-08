#!/usr/bin/env python3
"""Run the official TA-SAM article reproduction against the ns-3 collection DB."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_COLLECTION_DB = ROOT / 'runs' / 'tasam_article_ns3_collection' / 'rapp_data_lake.db'
DEFAULT_OUTPUT = ROOT / 'runs' / 'tasam_article_ns3_suite'


def resolve_default_db() -> Path:
    for env_name in ('GREENRAN_TASAM_ACTIVE_DB', 'GREENRAN_DB_PATH'):
        raw = os.environ.get(env_name, '').strip()
        if raw:
            return Path(raw)
    return OFFICIAL_COLLECTION_DB


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run TA-SAM article suite on isolated ns-3 collection')
    parser.add_argument('--db', default=str(resolve_default_db()), help='Input SQLite DB')
    parser.add_argument('--output-root', default=str(DEFAULT_OUTPUT), help='Suite output directory')
    parser.add_argument('--trace-jsonl', default=None, help='Optional trace output path')
    parser.add_argument('--summary-json', default=None, help='Optional export summary path')
    parser.add_argument('--limit', type=int, default=None, help='Optional transition limit')
    parser.add_argument('--epochs', type=int, default=25, help='Training epochs per mode')
    parser.add_argument('--dry-run', action='store_true', help='Print commands only')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cmd = [
        sys.executable,
        str(ROOT / 'scripts' / 'run_tasam_article_reproduction.py'),
        '--db',
        str(args.db),
        '--output-root',
        str(args.output_root),
        '--epochs',
        str(args.epochs),
        '--modes',
        'no_sam,l2,actor_sam,critic_sam,both_sam,tasam_selective',
    ]
    if args.trace_jsonl:
        cmd.extend(['--trace-jsonl', str(args.trace_jsonl)])
    if args.summary_json:
        cmd.extend(['--summary-json', str(args.summary_json)])
    if args.limit is not None:
        cmd.extend(['--limit', str(args.limit)])
    if args.dry_run:
        cmd.append('--dry-run')
    print(' '.join(cmd), flush=True)
    if not args.dry_run:
        subprocess.run(cmd, cwd=ROOT, check=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
