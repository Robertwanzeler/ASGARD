#!/usr/bin/env python3
"""Persist validated TA-SAM transitions for explicit reuse in later rounds."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        quality = row.get("collection_quality") or {}
        if quality.get("valid_for_training") is not True:
            continue
        if row.get("judge_feedback_observed") is False or row.get("judge_feedback") is None:
            continue
        rows.append(row)
    return rows


def experience_id(row: dict[str, Any]) -> str:
    return str(
        row.get("tasam_experience_id")
        or f"{row.get('topology_id', '')}|{row.get('timestamp', '')}|{row.get('next_timestamp', '')}"
    )


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path, action="append")
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--round-status", default="partial")
    args = parser.parse_args()

    existing = {experience_id(row): row for row in read_rows(args.bank)}
    source_rows = 0
    added = 0
    for source in args.input:
        rows = read_rows(source)
        source_rows += len(rows)
        for row in rows:
            key = experience_id(row)
            if key in existing:
                continue
            persisted = dict(row)
            persisted.update({
                "tasam_experience_id": key,
                "tasam_experience_source_round": args.round_id,
                "tasam_experience_source_status": args.round_status,
            })
            existing[key] = persisted
            added += 1

    rows = sorted(existing.values(), key=lambda row: (str(row.get("timestamp", "")), experience_id(row)))
    args.bank.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.bank.with_suffix(args.bank.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, args.bank)

    matrix: Counter[str] = Counter()
    strong: list[float] = []
    training: list[float] = []
    for row in rows:
        predicted = str(row.get("tasam_predicted_verdict") or "UNKNOWN")
        observed = str(row.get("tasam_observed_verdict") or "UNKNOWN")
        matrix[f"{predicted}->{observed}"] += 1
        try:
            strong.append(float(row.get("tasam_training_category_credit")))
        except (TypeError, ValueError):
            pass
        try:
            training.append(float(row.get("tasam_training_reward")))
        except (TypeError, ValueError):
            pass
    digest = sha256_file(args.bank)
    manifest = {
        "schema": "greenran.tasam_experience_bank.v1",
        "bank": str(args.bank.resolve()),
        "source_round": args.round_id,
        "source_status": args.round_status,
        "source_rows_read": source_rows,
        "rows_added": added,
        "unique_transitions": len(rows),
        "reward_fields": [
            "tasam_category_credit", "tasam_training_category_credit",
            "tasam_training_category_penalty", "tasam_training_reward",
            "tasam_continuous_reward",
        ],
        "category_confusion": dict(sorted(matrix.items())),
        "mean_training_category_credit": sum(strong) / max(len(strong), 1),
        "mean_training_reward": sum(training) / max(len(training), 1),
        "sha256": digest,
    }
    write_json_atomic(args.bank.with_suffix(".manifest.json"), manifest)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
