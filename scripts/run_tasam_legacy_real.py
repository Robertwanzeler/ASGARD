#!/usr/bin/env python3
"""Run TA-SAM article backend on a legacy real-data DB."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'runs' / 'legacy_real' / 'rapp_data_lake.db'
DEFAULT_OUTPUT = ROOT / 'runs' / 'tasam_legacy_real'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run TA-SAM on legacy real-data DB')
    parser.add_argument('--db', default=str(DEFAULT_DB), help='Legacy SQLite DB path')
    parser.add_argument('--output-root', default=str(DEFAULT_OUTPUT), help='Output directory')
    parser.add_argument('--limit', type=int, default=None, help='Optional transition limit')
    parser.add_argument('--epochs', type=int, default=25, help='Training epochs per mode')
    parser.add_argument('--modes', default='no_sam,l2,actor_sam,critic_sam,both_sam,tasam_selective', help='Comma-separated modes')
    parser.add_argument('--allow-proxy', action='store_true', help='Keep proxy-latency rows')
    parser.add_argument('--dry-run', action='store_true', help='Print commands only')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cmd = [
        sys.executable,
        str(ROOT / 'scripts' / 'run_tasam_greenran_real.py'),
        '--db',
        str(args.db),
        '--output-root',
        str(args.output_root),
        '--epochs',
        str(args.epochs),
        '--modes',
        args.modes,
    ]
    if args.limit is not None:
        cmd.extend(['--limit', str(args.limit)])
    if args.allow_proxy:
        cmd.append('--allow-proxy')
    if args.dry_run:
        cmd.append('--dry-run')
    print(' '.join(cmd), flush=True)
    if not args.dry_run:
        subprocess.run(cmd, cwd=ROOT, check=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
