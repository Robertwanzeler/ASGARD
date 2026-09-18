#!/usr/bin/env python3
"""Audit an economic campaign without changing its historical evidence."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def audit(campaign: Path) -> dict[str, Any]:
    db = campaign / "adaptation_online" / "rapp_data_lake.db"
    if not db.is_file():
        db = campaign / "rapp_data_lake.db"
    if not db.is_file():
        raise RuntimeError(f"SQLite da campanha não encontrado: {campaign}")
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id, economic_application_status, economic_execution_mode, "
        "armd_safety_level, tasam_operating_permission, economic_training_eligible, "
        "economic_promotion_eligible, economic_action_json, economic_outcome_invalid_reason "
        "FROM decisions_history ORDER BY id"
    ).fetchall()
    reasons: Counter[str] = Counter()
    mismatches: list[dict[str, Any]] = []
    applied = 0
    confirmed = 0
    training = 0
    promotion = 0
    for row in rows:
        action = _json(row["economic_action_json"])
        status = str(row["economic_application_status"] or action.get("application_status") or "")
        if status == "applied":
            applied += 1
        action_confirmed = action.get("actuation_confirmed")
        if action_confirmed is True:
            confirmed += 1
        if row["economic_training_eligible"]:
            training += 1
        if row["economic_promotion_eligible"]:
            promotion += 1
        level = str(row["armd_safety_level"] or action.get("armd_safety_level") or "").upper()
        permission = bool(row["tasam_operating_permission"])
        mode = str(row["economic_execution_mode"] or action.get("economic_execution_mode") or "")
        if status == "applied":
            problems: list[str] = []
            if level not in {"CLEAR", "ADVISORY"}:
                problems.append("armd_level_not_clear_or_advisory")
            if not permission:
                problems.append("tasam_permission_false")
            if mode == "diagnostic":
                problems.append("diagnostic_mode_applied")
            if action_confirmed is False:
                problems.append("actuation_not_confirmed")
            if problems:
                for problem in problems:
                    reasons[problem] += 1
                mismatches.append({"decision_id": row["id"], "reasons": problems})
        invalid = str(row["economic_outcome_invalid_reason"] or "")
        if invalid:
            reasons[invalid] += 1
    connection.close()
    return {
        "schema": "greenran.tasam.economic_evidence_audit.v1",
        "campaign_dir": str(campaign.resolve()),
        "database": str(db.resolve()),
        "historical_data_modified": False,
        "decision_rows": len(rows),
        "applied_rows": applied,
        "actuation_confirmed_rows": confirmed,
        "training_eligible_rows": training,
        "promotion_eligible_rows": promotion,
        "contract_mismatch_count": len(mismatches),
        "contract_mismatches": mismatches[:100],
        "reasons": dict(reasons),
        "energy_interpretation": "estimativa relativa calibrada da simulação ns-3; não é consumo físico",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.campaign.resolve())
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
