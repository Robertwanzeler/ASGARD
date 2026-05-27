#!/usr/bin/env python3
"""Backfill dedicated MARL state tables from existing resource allocation snapshots."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from rapp_data_lake import DataLake
from greenran_marl_topology import build_du_state_snapshot_from_resource_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Backfill MARL history tables from resource_allocation_history')
    parser.add_argument('--db', required=True, help='SQLite database path')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    lake = DataLake(str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT timestamp, snapshot_json FROM resource_allocation_history WHERE snapshot_json IS NOT NULL ORDER BY timestamp ASC').fetchall()
    written = 0
    for row in rows:
        try:
            snapshot = json.loads(row['snapshot_json'])
        except (TypeError, json.JSONDecodeError):
            continue
        if 'article_marl_state' not in snapshot:
            snapshot['article_marl_state'] = build_du_state_snapshot_from_resource_snapshot(snapshot)
        lake.record_article_marl_state(snapshot, timestamp=int(row['timestamp']))
        written += 1
    print(json.dumps({'rows': written, 'db': str(db_path)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
