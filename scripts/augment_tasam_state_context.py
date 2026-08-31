#!/usr/bin/env python3
"""Add explicit ALLOWED/CONDITIONAL/BLOCKED context to a TA-SAM trace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def state_from_stage(stage: str, fallback: str = "ALLOWED") -> str:
    name = str(stage or "").strip().lower()
    if "blocked" in name:
        return "BLOCKED"
    if "conditional" in name:
        return "CONDITIONAL"
    if "allowed" in name or "recovery" in name:
        return "ALLOWED"
    value = str(fallback or "ALLOWED").upper()
    return value if value in {"ALLOWED", "CONDITIONAL", "BLOCKED"} else "ALLOWED"


def append_context(vector: list[float], state: str) -> list[float]:
    return list(vector or []) + [
        1.0 if state == "ALLOWED" else 0.0,
        1.0 if state == "CONDITIONAL" else 0.0,
        1.0 if state == "BLOCKED" else 0.0,
    ]


def transform(row: dict, next_state: str) -> dict:
    current_state = state_from_stage(row.get("scenario_stage"), (row.get("decision") or {}).get("decision"))
    global_state = row.get("global_state") or {}
    global_state["state_vector"] = append_context(global_state.get("state_vector") or [], current_state)
    global_state["operating_state"] = current_state
    row["global_state"] = global_state
    for du in row.get("du_states") or []:
        du["state_vector"] = append_context(du.get("state_vector") or [], current_state)
        du["operating_state"] = current_state
    next_global = row.get("next_global_state") or {}
    next_global["state_vector"] = append_context(next_global.get("state_vector") or [], next_state)
    next_global["operating_state"] = next_state
    row["next_global_state"] = next_global
    for du in row.get("next_du_states") or []:
        du["state_vector"] = append_context(du.get("state_vector") or [], next_state)
        du["operating_state"] = next_state
    row["state_context_schema"] = "one_hot_allowed_conditional_blocked_v1"
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.input)
    target = Path(args.output)
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for index, row in enumerate(rows):
            next_stage = rows[index + 1].get("scenario_stage") if index + 1 < len(rows) else row.get("scenario_stage")
            next_state = state_from_stage(next_stage, (row.get("next_global_state") or {}).get("operating_state"))
            handle.write(json.dumps(transform(row, next_state), ensure_ascii=False) + "\n")
    print(json.dumps({"input_rows": len(rows), "output": str(target), "state_dim": 13, "schema": "one_hot_allowed_conditional_blocked_v1"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
