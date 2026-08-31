#!/usr/bin/env python3
"""Merge and structurally validate GreenRAN TA-SAM article traces."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


SCHEMA = "greenran.tasam_article_transition.v1"


def main() -> int:
    p = argparse.ArgumentParser(description="Merge validated TA-SAM transition traces")
    p.add_argument("--inputs", nargs="+", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--summary", required=True)
    args = p.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    invalid = 0
    schemas = Counter()
    dUs = Counter()
    variants = Counter()
    stages = Counter()
    seen = set()

    merged_records = []
    for raw_path in args.inputs:
        path = Path(raw_path)
        if not path.exists():
            raise SystemExit(f"trace ausente: {path}")
        # Repetitions intentionally reuse the same simulation timestamps.
        # The filename is therefore part of the identity; using only the
        # common report directory collapses distinct seeds/repetitions.
        stem = path.stem
        variant = stem.split("_judge", 1)[0] if "_judge" in stem else path.parent.parent.name
        for line_no, line in enumerate(path.open(encoding="utf-8"), 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"JSON inválido em {path}:{line_no}: {exc}")
            if record.get("schema") != SCHEMA:
                raise SystemExit(f"schema inesperado em {path}:{line_no}")
            du_states = record.get("du_states") or []
            if len(du_states) != 3:
                raise SystemExit(f"3 DUs esperados em {path}:{line_no}")
            if record.get("reward_hint") is None and record.get("reward_components") is None:
                raise SystemExit(f"recompensa ausente em {path}:{line_no}")
            key = (variant, record.get("timestamp"), record.get("next_timestamp"))
            if key in seen:
                continue
            seen.add(key)
            record["campaign_variant"] = variant
            merged_records.append(record)

    # Campaigns can cover overlapping wall-clock intervals.  Keep every
    # unique transition, but order the merged trace chronologically so the
    # trainer's next-state sequence and dataset quality gate remain valid.
    merged_records.sort(key=lambda record: (int(record.get("timestamp") or 0), int(record.get("next_timestamp") or 0)))
    with output.open("w", encoding="utf-8") as out:
        for record in merged_records:
            du_states = record.get("du_states") or []
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            rows += 1
            schemas[record["schema"]] += 1
            dUs[len(du_states)] += 1
            variants[record["campaign_variant"]] += 1
            stages[str(record.get("scenario_stage") or "unknown")] += 1

    summary = {
        "schema": "greenran.tasam_merged_dataset_summary.v1",
        "inputs": [str(Path(x).resolve()) for x in args.inputs],
        "output": str(output.resolve()),
        "rows": rows,
        "invalid_rows": invalid,
        "schemas": dict(schemas),
        "du_counts": dict(dUs),
        "variant_counts": dict(variants),
        "stage_counts": dict(stages),
        "training_ready": rows > 0 and invalid == 0 and dUs == Counter({3: rows}),
    }
    Path(args.summary).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
