#!/usr/bin/env python3
"""
Export a MARL trace from the GreenRAN Data Lake for TA-SAM MARL training.

Formato esperado pelo load_marl_trace() em ta_sam_marl.py:
  Cada linha = JSON com:
  - du_states: list[dict] com state_vector, primary_slice, slice_mix
  - global_state: dict com state_vector
  - slice_state: dict com {slice_id: {budget_share, ...}}
  - reward_hint: float (live_score da shadow comparison)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rapp_data_lake import DataLake
from greenran_paths import RAPP_DB_PATH


DEFAULT_DB_PATH = str(RAPP_DB_PATH)
SLICE_ORDER = ("eMBB", "mMTC", "URLLC")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export MARL trace for TA-SAM training")
    p.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path")
    p.add_argument("--output-jsonl", required=True, help="Output JSONL path")
    p.add_argument("--limit", type=int, default=0, help="Optional row limit (newest samples)")
    p.add_argument("--min-reward", type=float, default=-10.0, help="Filtro: reward_hint minimo")
    return p.parse_args()


def _build_slice_state(conn: sqlite3.Connection, timestamp: int) -> dict[str, dict[str, float]]:
    rows = conn.execute(
        "SELECT slice_id, budget_share, qos_pressure, completion_ratio, min_qos_met "
        "FROM marl_slice_state_history WHERE timestamp = ?",
        (timestamp,),
    ).fetchall()
    state: dict[str, dict[str, float]] = {}
    for r in rows:
        state[r[0]] = {
            "budget_share": float(r[1] or 0.0),
            "qos_pressure": float(r[2] or 0.0),
            "completion_ratio": float(r[3] or 0.0),
            "min_qos_met": float(r[4] or 0.0),
        }
    return state


def _build_du_states(conn: sqlite3.Connection, timestamp: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT du_id, primary_slice, slice_mix_json, state_vector_json "
        "FROM marl_du_state_history WHERE timestamp = ? ORDER BY du_id",
        (timestamp,),
    ).fetchall()
    dus: list[dict[str, Any]] = []
    for r in rows:
        sv = json.loads(r[3]) if isinstance(r[3], str) else (r[3] or [])
        sm = json.loads(r[2]) if isinstance(r[2], str) else (r[2] or {})
        dus.append({
            "du_id": r[0],
            "primary_slice": r[1],
            "slice_mix": sm,
            "state_vector": [float(v) for v in sv],
        })
    return dus


def _get_reward(conn: sqlite3.Connection, timestamp: int) -> float | None:
    row = conn.execute(
        "SELECT live_score FROM marl_shadow_comparison_history WHERE timestamp = ? ORDER BY rowid DESC LIMIT 1",
        (timestamp,),
    ).fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return None


def main() -> int:
    args = parse_args()
    dl = DataLake(args.db)
    conn = dl.conn

    # Buscar timestamps com dados completos
    timestamps = conn.execute("""
        SELECT DISTINCT g.timestamp
        FROM marl_global_state_history g
        INNER JOIN marl_du_state_history d ON g.timestamp = d.timestamp
        INNER JOIN marl_slice_state_history s ON g.timestamp = s.timestamp
        ORDER BY g.timestamp DESC
    """).fetchall()
    timestamps = [t[0] for t in timestamps]

    if args.limit > 0:
        timestamps = timestamps[: args.limit]

    total = len(timestamps)
    exported = 0
    out_path = Path(args.output_jsonl)

    with out_path.open("w", encoding="utf-8") as f:
        for ts in timestamps:
            gs_row = conn.execute(
                "SELECT state_vector_json, usable_budget FROM marl_global_state_history WHERE timestamp = ?",
                (ts,),
            ).fetchone()
            if not gs_row:
                continue
            gs_vec = json.loads(gs_row[0]) if isinstance(gs_row[0], str) else (gs_row[0] or [])
            du_states = _build_du_states(conn, ts)
            slice_state = _build_slice_state(conn, ts)
            reward = _get_reward(conn, ts)
            if reward is not None and reward < args.min_reward:
                continue

            record = {
                "du_states": du_states,
                "global_state": {
                    "state_vector": [float(v) for v in gs_vec],
                    "usable_budget": float(gs_row[1] or 1.0),
                },
                "slice_state": slice_state,
            }
            if reward is not None:
                record["reward_hint"] = reward

            f.write(json.dumps(record) + "\n")
            exported += 1

    print(f"[MARL Trace] Exportados {exported}/{total} registros para {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
