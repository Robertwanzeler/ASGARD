#!/usr/bin/env python3
"""Split a GreenRAN TA-SAM trace into chronological scenario cycles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Input trainable trace JSONL')
    parser.add_argument('--output-dir', required=True, help='Directory for train/validation/test traces')
    parser.add_argument('--train-cycles', type=int, default=2)
    parser.add_argument('--validation-cycles', type=int, default=1)
    args = parser.parse_args()

    source = Path(args.input)
    output_dir = Path(args.output_dir)
    rows = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not rows:
        raise SystemExit('trace vazio')
    rows.sort(key=lambda row: (int(row.get('timestamp', 0) or 0), int((row.get('decision') or {}).get('id', 0) or 0)))

    # A new complete scenario pass starts at the next allowed_stable block.
    starts = [
        index for index, row in enumerate(rows)
        if row.get('scenario_stage') == 'allowed_stable'
        and (index == 0 or rows[index - 1].get('scenario_stage') != 'allowed_stable')
    ]
    if not starts or starts[0] != 0:
        raise SystemExit(f'nao foi possivel identificar ciclos: starts={starts}')
    boundaries = starts + [len(rows)]
    cycles = [rows[boundaries[index]:boundaries[index + 1]] for index in range(len(starts))]
    train_count = int(args.train_cycles)
    validation_count = int(args.validation_cycles)
    if train_count < 1 or validation_count < 1 or train_count + validation_count > len(cycles):
        raise SystemExit(f'ciclos insuficientes: total={len(cycles)}')

    train_rows = [row for cycle in cycles[:train_count] for row in cycle]
    validation_rows = [row for cycle in cycles[train_count:train_count + validation_count] for row in cycle]
    test_rows = [row for cycle in cycles[train_count + validation_count:] for row in cycle]

    output_dir.mkdir(parents=True, exist_ok=True)

    def write_jsonl(name: str, values: list[dict]) -> str:
        path = output_dir / name
        path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in values), encoding='utf-8')
        return str(path.resolve())

    train_path = write_jsonl('train.jsonl', train_rows)
    validation_path = write_jsonl('validation.jsonl', validation_rows)
    test_path = write_jsonl('test.jsonl', test_rows)
    manifest = {
        'schema': 'greenran.tasam_cycle_split.v1',
        'input': str(source.resolve()),
        'cycle_boundaries': boundaries,
        'cycle_sizes': [len(cycle) for cycle in cycles],
        'train_cycles': list(range(train_count)),
        'validation_cycles': list(range(train_count, train_count + validation_count)),
        'test_cycles': list(range(train_count + validation_count, len(cycles))),
        'train_rows': len(train_rows),
        'validation_rows': len(validation_rows),
        'test_rows': len(test_rows),
        'stage_counts': {
            'train': _stage_counts(train_rows),
            'validation': _stage_counts(validation_rows),
            'test': _stage_counts(test_rows),
        },
        'paths': {'train': train_path, 'validation': validation_path, 'test': test_path},
    }
    (output_dir / 'split_manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


def _stage_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        stage = str(row.get('scenario_stage') or 'unknown')
        counts[stage] = counts.get(stage, 0) + 1
    return counts


if __name__ == '__main__':
    raise SystemExit(main())
