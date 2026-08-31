#!/usr/bin/env python3
"""Build deterministic mixed/holdout JSONL files for TA-SAM training."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def read_records(paths: list[Path]) -> tuple[list[dict], Counter[str]]:
    records: list[dict] = []
    stages: Counter[str] = Counter()
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict) or not str(item.get("schema", "")).startswith("greenran."):
                    raise ValueError(f"registro GreenRAN inválido: {path}:{line_no}")
                stages[str(item.get("scenario_stage", "unknown"))] += 1
                records.append(item)
    if not records:
        raise ValueError("nenhum registro encontrado")
    return records, stages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, type=Path, help="Fonte real JSONL; pode repetir")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()
    for path in args.source:
        if not path.is_file():
            raise SystemExit(f"fonte ausente: {path}")
    records, stages = read_records(args.source)
    # A merged offline trace must have a monotonic timeline for the dataset
    # quality gate.  Rebase only bookkeeping timestamps; transition states,
    # actions, rewards and metrics remain byte-for-byte equivalent otherwise.
    base_timestamp = int(records[0].get("timestamp") or 0)
    for index, record in enumerate(records):
        old_timestamp = int(record.get("timestamp") or base_timestamp)
        old_next = int(record.get("next_timestamp") or old_timestamp + 1)
        duration = max(1, old_next - old_timestamp)
        timestamp = base_timestamp + index * 5
        record["timestamp"] = timestamp
        record["next_timestamp"] = timestamp + duration
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(
            {
                "schema": "greenran.tasam_generalization_dataset.v1",
                "sources": [str(path.resolve()) for path in args.source],
                "records": len(records),
                "scenario_stage_counts": dict(sorted(stages.items())),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output.resolve()), "records": len(records), "stages": dict(stages)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
