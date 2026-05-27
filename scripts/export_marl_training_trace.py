#!/usr/bin/env python3
"""Export per-DU/per-slice MARL snapshots from dedicated MARL history tables."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from greenran_paths import RAPP_DB_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Export MARL DU/slice trace from MARL history tables')
    parser.add_argument('--db', default=str(RAPP_DB_PATH), help='SQLite database path')
    parser.add_argument('--output', required=True, help='Output JSONL path')
    return parser


def table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    row = cursor.execute("select name from sqlite_master where type='table' and name=?", (name,)).fetchone()
    return row is not None


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.db)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if not table_exists(cursor, 'marl_global_state_history'):
        raise SystemExit('marl_global_state_history table not found')

    global_rows = cursor.execute(
        'SELECT timestamp, datetime, topology_id, logical_du_count, total_demand, usable_budget, state_vector_json, snapshot_json FROM marl_global_state_history ORDER BY timestamp ASC'
    ).fetchall()

    written = 0
    with out_path.open('w', encoding='utf-8') as fh:
        for row in global_rows:
            slice_rows = cursor.execute(
                'SELECT slice_id, ue_count, demand, allocation, qos_pressure, completion_ratio, min_qos_met, budget_share, snapshot_json FROM marl_slice_state_history WHERE timestamp=? ORDER BY slice_id ASC',
                (row['timestamp'],),
            ).fetchall()
            du_rows = cursor.execute(
                'SELECT du_id, role, primary_slice, ue_count, demand_share, allocation_share, slice_mix_json, state_vector_json, snapshot_json FROM marl_du_state_history WHERE timestamp=? ORDER BY du_id ASC',
                (row['timestamp'],),
            ).fetchall()
            if not du_rows:
                continue

            record = {
                'timestamp': row['timestamp'],
                'datetime': row['datetime'],
                'topology_id': row['topology_id'],
                'global_state': {
                    'topology_id': row['topology_id'],
                    'logical_du_count': row['logical_du_count'],
                    'total_demand': row['total_demand'],
                    'usable_budget': row['usable_budget'],
                    'state_vector': json.loads(row['state_vector_json'] or '[]'),
                },
                'slice_state': {},
                'du_states': [],
            }
            reward_hint = 0.0
            for s in slice_rows:
                payload = json.loads(s['snapshot_json'] or '{}') if s['snapshot_json'] else {
                    'slice_id': s['slice_id'],
                    'ue_count': s['ue_count'],
                    'demand': s['demand'],
                    'allocation': s['allocation'],
                    'qos_pressure': s['qos_pressure'],
                    'completion_ratio': s['completion_ratio'],
                    'min_qos_met': s['min_qos_met'],
                    'budget_share': s['budget_share'],
                }
                record['slice_state'][s['slice_id']] = payload
                reward_hint += float(payload.get('completion_ratio', 0.0) or 0.0)
            reward_hint /= max(len(slice_rows), 1)

            for d in du_rows:
                payload = json.loads(d['snapshot_json'] or '{}') if d['snapshot_json'] else {
                    'du_id': d['du_id'],
                    'role': d['role'],
                    'primary_slice': d['primary_slice'],
                    'ue_count': d['ue_count'],
                    'demand_share': d['demand_share'],
                    'allocation_share': d['allocation_share'],
                    'slice_mix': json.loads(d['slice_mix_json'] or '{}'),
                    'state_vector': json.loads(d['state_vector_json'] or '[]'),
                }
                record['du_states'].append(payload)

            record['reward_hint'] = reward_hint
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')
            written += 1

    print(json.dumps({'rows': written, 'output': str(out_path)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
