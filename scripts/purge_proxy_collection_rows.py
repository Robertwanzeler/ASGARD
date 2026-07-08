#!/usr/bin/env python3
"""Purge proxy-contaminated collection rows from the TA-SAM live DB."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


TIMESTAMP_TABLES = [
    "extended_metrics",
    "ue_metrics",
    "decisions_history",
    "app2_snapshots",
    "app2_sensor_readings",
    "conflict_events",
    "resource_allocation_history",
    "marl_global_state_history",
    "marl_slice_state_history",
    "marl_du_state_history",
    "marl_shadow_comparison_history",
    "energy_commands",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Purge proxy-latency rows from the live TA-SAM collection DB")
    parser.add_argument("--db", required=True, help="Path to rapp_data_lake.db")
    parser.add_argument("--backup", default=None, help="Optional explicit backup path")
    parser.add_argument(
        "--include-suspected-stale-proxy",
        action="store_true",
        help="Also purge broken legacy rows that the exporter classifies as suspected_stale_proxy",
    )
    parser.add_argument(
        "--include-non-pdcp-real",
        action="store_true",
        help="Also purge any remaining rows whose collector_mode is not pdcp_real",
    )
    parser.add_argument("--apply", action="store_true", help="Apply deletes; otherwise only report")
    return parser.parse_args()


def table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    row = cur.execute(
        "select 1 from sqlite_master where type='table' and name=? limit 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def proxy_timestamps(cur: sqlite3.Cursor) -> list[int]:
    rows = cur.execute(
        """
        select timestamp
        from extended_metrics
        where proxy_latency_sample_count > 0
        order by timestamp
        """
    ).fetchall()
    return [int(row[0]) for row in rows]


def suspected_stale_proxy_timestamps(cur: sqlite3.Cursor) -> list[int]:
    rows = cur.execute(
        """
        select timestamp
        from extended_metrics
        where collector_mode = ''
          and proxy_latency_sample_count = 0
          and real_latency_sample_count = 0
          and throughput_kbps <= 0
          and total_tx_bytes <= 0
          and total_rx_bytes <= 0
          and sim_time_s <= 1.0
          and cvar_per_ue_us > 0
          and variance_per_ue_us2 <= 0
        order by timestamp
        """
    ).fetchall()
    return [int(row[0]) for row in rows]


def non_pdcp_real_timestamps(cur: sqlite3.Cursor) -> list[int]:
    rows = cur.execute(
        """
        select timestamp
        from extended_metrics
        where collector_mode != 'pdcp_real'
        order by timestamp
        """
    ).fetchall()
    return [int(row[0]) for row in rows]


def count_matches(cur: sqlite3.Cursor, table_name: str, timestamps: list[int]) -> int:
    if not timestamps or not table_exists(cur, table_name):
        return 0
    placeholders = ",".join("?" for _ in timestamps)
    row = cur.execute(
        f"select count(*) from {table_name} where timestamp in ({placeholders})",
        timestamps,
    ).fetchone()
    return int(row[0] or 0)


def delete_matches(cur: sqlite3.Cursor, table_name: str, timestamps: list[int]) -> int:
    if not timestamps or not table_exists(cur, table_name):
        return 0
    placeholders = ",".join("?" for _ in timestamps)
    cur.execute(f"delete from {table_name} where timestamp in ({placeholders})", timestamps)
    return int(cur.rowcount or 0)


def backup_path_for(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return db_path.with_name(f"{db_path.stem}_pre_proxy_purge_{stamp}{db_path.suffix}")


def backup_sqlite_db(source_path: Path, backup_path: Path) -> None:
    source = sqlite3.connect(str(source_path))
    dest = sqlite3.connect(str(backup_path))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).resolve()
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    explicit_proxy = proxy_timestamps(cur)
    suspected_proxy = suspected_stale_proxy_timestamps(cur) if args.include_suspected_stale_proxy else []
    non_pdcp_real = non_pdcp_real_timestamps(cur) if args.include_non_pdcp_real else []
    timestamps = sorted(set(explicit_proxy) | set(suspected_proxy) | set(non_pdcp_real))
    summary: dict[str, object] = {
        "db_path": str(db_path),
        "apply": bool(args.apply),
        "explicit_proxy_timestamp_count": len(explicit_proxy),
        "suspected_stale_proxy_timestamp_count": len(suspected_proxy),
        "non_pdcp_real_timestamp_count": len(non_pdcp_real),
        "proxy_timestamp_count": len(timestamps),
        "tables": {},
    }

    if timestamps:
        summary["first_proxy_timestamp"] = timestamps[0]
        summary["last_proxy_timestamp"] = timestamps[-1]

    for table_name in TIMESTAMP_TABLES:
        summary["tables"][table_name] = {
            "matched_rows": count_matches(cur, table_name, timestamps),
        }

    if not args.apply:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        conn.close()
        return 0

    backup_path = Path(args.backup).resolve() if args.backup else backup_path_for(db_path)
    backup_sqlite_db(db_path, backup_path)

    deleted_by_table: dict[str, int] = {}
    try:
        cur.execute("begin immediate")
        for table_name in TIMESTAMP_TABLES:
            deleted_by_table[table_name] = delete_matches(cur, table_name, timestamps)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise

    remaining_proxy = int(
        cur.execute(
            "select count(*) from extended_metrics where proxy_latency_sample_count > 0"
        ).fetchone()[0]
        or 0
    )
    total_extended = int(cur.execute("select count(*) from extended_metrics").fetchone()[0] or 0)
    clean_extended = int(
        cur.execute("select count(*) from extended_metrics where proxy_latency_sample_count = 0").fetchone()[0]
        or 0
    )

    summary["backup_path"] = str(backup_path)
    summary["deleted_rows"] = deleted_by_table
    summary["remaining_proxy_rows"] = remaining_proxy
    summary["remaining_extended_metrics_rows"] = total_extended
    summary["remaining_clean_extended_metrics_rows"] = clean_extended
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
