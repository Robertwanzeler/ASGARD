#!/usr/bin/env python3
"""Monitor a running TA-SAM offline training directory."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def number(row: dict, key: str, digits: int = 4) -> str:
    value = row.get(key)
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def print_status(rows: list[dict], summary: dict, target: int) -> None:
    completed = int(summary.get("completed_epochs", 0) or 0)
    if rows:
        completed = max(completed, int(rows[-1].get("epoch", 0) or 0))
    percent = min(100.0, 100.0 * completed / max(target, 1))
    print(f"TA-SAM treino: {completed}/{target} épocas ({percent:.1f}%)")
    if not rows:
        print("  aguardando epoch_history.jsonl...")
        return
    latest = rows[-1]
    print(
        "  última: "
        f"epoch={latest.get('epoch', '-')} "
        f"eval={number(latest, 'eval_return')} "
        f"critic={number(latest, 'critic_loss')} "
        f"actor={number(latest, 'actor_loss')} "
        f"alpha={number(latest, 'alpha')} "
        f"selected={number(latest, 'selected_fraction', 2)} "
        f"ran_guard={number(latest, 'ran_guard_loss')}"
    )
    print("  últimas épocas:")
    for row in rows[-5:]:
        print(
            f"    {int(row.get('epoch', 0)):>3}: "
            f"eval={number(row, 'eval_return')} "
            f"critic={number(row, 'critic_loss')} "
            f"guard={number(row, 'ran_guard_loss')} "
            f"selected={number(row, 'selected_fraction', 2)}"
        )
    if summary.get("stopped_early"):
        print(f"  parado cedo: {summary.get('stop_reason', 'motivo não informado')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, help="Diretório do treino")
    parser.add_argument("--target-epochs", type=int, default=150)
    parser.add_argument("--refresh", type=float, default=5.0)
    args = parser.parse_args()

    output_dir = Path(args.dir).expanduser().resolve()
    history_path = output_dir / "epoch_history.jsonl"
    summary_path = output_dir / "tasam_marl_summary.json"
    last_completed = -1
    while True:
        rows = load_rows(history_path)
        summary = load_json(summary_path)
        completed = int(summary.get("completed_epochs", 0) or 0)
        if rows:
            completed = max(completed, int(rows[-1].get("epoch", 0) or 0))
        if completed != last_completed or summary.get("stopped_early") or completed >= args.target_epochs:
            print_status(rows, summary, args.target_epochs)
            print("-" * 72, flush=True)
            last_completed = completed
        if completed >= args.target_epochs or summary.get("stopped_early"):
            print("Monitor encerrado: treino finalizado ou interrompido.", flush=True)
            return 0
        time.sleep(max(0.5, args.refresh))


if __name__ == "__main__":
    raise SystemExit(main())
