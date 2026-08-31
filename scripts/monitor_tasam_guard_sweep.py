#!/usr/bin/env python3
"""Monitor all variants of a TA-SAM RAN-guard sweep."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_history(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def fmt(row: dict, key: str, digits: int = 4) -> str:
    try:
        return f"{float(row.get(key, 0.0)):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--refresh", type=float, default=10.0)
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    last = ""
    while True:
        manifest = read_json(manifest_path)
        items = manifest.get("configs") or []
        target = int(manifest.get("epochs", 150) or 150)
        lines = []
        all_done = True
        for item in items:
            output = Path(str(item.get("output_dir", "")))
            summary = read_json(output / "tasam_marl_summary.json")
            rows = read_history(output / "epoch_history.jsonl")
            epoch = int(summary.get("completed_epochs", 0) or 0)
            if rows:
                epoch = max(epoch, int(rows[-1].get("epoch", 0) or 0))
            done = epoch >= target
            all_done = all_done and done
            latest = rows[-1] if rows else (summary.get("final_metrics") or {})
            lines.append(
                f"{item.get('name','?'):>7} {epoch:>3}/{target} "
                f"eval={fmt(latest, 'eval_return')} critic={fmt(latest, 'critic_loss')} "
                f"guard={fmt(latest, 'ran_guard_loss')} status={'OK' if done else 'rodando'}"
            )
        output = "\n".join(lines)
        if output != last:
            print(output, flush=True)
            print("-" * 72, flush=True)
            last = output
        if all_done and items:
            print("Sweep concluído.", flush=True)
            return 0
        time.sleep(max(1.0, args.refresh))


if __name__ == "__main__":
    raise SystemExit(main())
