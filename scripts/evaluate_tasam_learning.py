#!/usr/bin/env python3
"""Generate the TA-SAM learning meter from one campaign, read-only."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tasam_learning_meter import build_learning_meter  # noqa: E402


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _rows(db: Path) -> list[dict[str, Any]]:
    if not db.is_file():
        return []
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(decisions_history)")}
        if not columns:
            return []
        selected = sorted(columns)
        return [dict(row) for row in conn.execute(f"SELECT {', '.join(selected)} FROM decisions_history ORDER BY id")]


def build_report(campaign_dir: Path, *, target_transitions: int = 180) -> dict[str, Any]:
    campaign_dir = campaign_dir.resolve()
    adaptation = campaign_dir / "adaptation_online"
    state = _json(adaptation / "online_state.json")
    status = _json(adaptation / "online_status.json")
    state = {**state, **{key: value for key, value in status.items() if key not in state}}
    rows = _rows(adaptation / "rapp_data_lake.db")
    meter = build_learning_meter(rows, state, target_transitions=target_transitions)
    meter.update({
        "campaign_dir": str(campaign_dir),
        "adaptation_dir": str(adaptation),
        "decision_count": len(rows),
        "active_checkpoint": state.get("active_checkpoint", ""),
        "initial_checkpoint": state.get("initial_checkpoint", ""),
        "energy_model_version": state.get("energy_model_version", ""),
        "source": "decisions_history_read_only",
    })
    return meter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--target-transitions", type=int, default=180)
    args = parser.parse_args()
    report = build_report(args.campaign_dir, target_transitions=args.target_transitions)
    output = (args.output or args.campaign_dir / "learning_meter.json").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({
        "output": str(output),
        "status": report["status"],
        "learning_meter": report["learning_meter"],
        "updates_completed": report["updates_completed"],
        "economic_transitions": report["economic_transitions"],
        "candidate_promoted": report["candidate_promoted"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

