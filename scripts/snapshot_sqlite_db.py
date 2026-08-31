#!/usr/bin/env python3
"""Create a consistent SQLite snapshot from the live TA-SAM collection DB."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def build_parser() -> argparse.ArgumentParser:
    default_db = Path(os.environ.get("GREENRAN_DB_PATH", "/tmp/rapp_data_lake.db"))
    default_dir = Path(
        os.environ.get("GREENRAN_DB_SNAPSHOT_DIR", str(default_db.parent / "db_snapshots"))
    )
    parser = argparse.ArgumentParser(description="Snapshot a live SQLite DB using sqlite backup")
    parser.add_argument("--db", default=str(default_db), help="Source SQLite DB")
    parser.add_argument("--snapshot-dir", default=str(default_dir), help="Snapshot directory")
    parser.add_argument("--prefix", default=default_db.stem, help="Snapshot filename prefix")
    parser.add_argument(
        "--retain",
        type=int,
        default=_env_int("GREENRAN_DB_SNAPSHOT_RETENTION", 2),
        help="How many snapshots to keep",
    )
    return parser


def snapshot_name(prefix: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{prefix}_{now.strftime('%Y%m%dT%H%M%SZ')}.db"


def backup_sqlite_database(source: Path, destination: Path) -> None:
    source_uri = f"file:{source.resolve()}?mode=ro"
    src = sqlite3.connect(source_uri, uri=True)
    dst = sqlite3.connect(str(destination))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def prune_snapshots(snapshot_dir: Path, prefix: str, retain: int) -> list[Path]:
    if retain <= 0:
        return []
    snapshots = sorted(snapshot_dir.glob(f"{prefix}_*.db"))
    stale = snapshots[:-retain]
    for path in stale:
        path.unlink(missing_ok=True)
    return stale


def write_manifest(
    snapshot_dir: Path,
    source_db: Path,
    latest_snapshot: Path,
    removed: list[Path],
    retained: int,
) -> Path:
    payload: dict[str, Any] = {
        "schema": "greenran.sqlite_snapshot_manifest.v1",
        "source_db": str(source_db),
        "latest_snapshot": str(latest_snapshot),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "retained_snapshots": retained,
        "removed_snapshots": [str(path) for path in removed],
    }
    manifest = snapshot_dir / "latest_snapshot.json"
    manifest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def create_snapshot(db_path: Path, snapshot_dir: Path, prefix: str, retain: int) -> dict[str, Any]:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {db_path}")
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    target = snapshot_dir / snapshot_name(prefix)
    temp_target = target.with_suffix(".tmp")
    backup_sqlite_database(db_path, temp_target)
    temp_target.replace(target)

    removed = prune_snapshots(snapshot_dir, prefix, retain)
    manifest = write_manifest(snapshot_dir, db_path, target, removed, max(retain, 0))

    return {
        "schema": "greenran.sqlite_snapshot_result.v1",
        "source_db": str(db_path),
        "snapshot_path": str(target),
        "manifest_path": str(manifest),
        "removed_snapshots": [str(path) for path in removed],
    }


def main() -> int:
    args = build_parser().parse_args()
    result = create_snapshot(
        db_path=Path(args.db),
        snapshot_dir=Path(args.snapshot_dir),
        prefix=args.prefix,
        retain=args.retain,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
