#!/usr/bin/env python3
"""
Passive runtime stage observer for GreenRAN.

Nao injeta scenario_control nem altera KPIs. Apenas observa o fluxo real do
rApp e marca duas etapas operacionais quando elas ocorrerem naturalmente:

1. uma sequencia sustentada de `ALLOWED`;
2. depois disso, uma sequencia sustentada de `BLOCKED`.

Isso permite acompanhar o comportamento do runtime em duas condicoes sem
forcar o estado da simulacao.
"""

from __future__ import annotations

import argparse
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from greenran_paths import RAPP_DB_PATH, STATE_DIR  # noqa: E402


STATUS_PATH = STATE_DIR / "runtime_stage_status.json"


def _write_status(payload: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(__import__("json").dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATUS_PATH)


def _read_latest_decisions(limit: int) -> list[tuple]:
    if not RAPP_DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(RAPP_DB_PATH))
    try:
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT datetime, decision, reason
            FROM decisions_history
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return rows
    finally:
        conn.close()


def _decision_streak(rows: list[tuple], decision: str) -> int:
    streak = 0
    for _dt, current_decision, _reason in rows:
        if current_decision != decision:
            break
        streak += 1
    return streak


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Observe two natural runtime stages in GreenRAN.")
    parser.add_argument("--required-streak", type=int, default=3, help="required consecutive decisions to mark each stage")
    parser.add_argument("--poll-interval", type=int, default=5, help="seconds between polls")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    running = True

    def stop_handler(signum, frame):  # pragma: no cover - signal path
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    stage = "await_allowed"
    stage1_reached_at = None
    stage2_reached_at = None
    print(
        f"[RuntimeStage] observando runtime livre; etapa1=ALLOWED x{args.required_streak}, "
        f"etapa2=BLOCKED x{args.required_streak}"
    )
    sys.stdout.flush()

    try:
        while running:
            rows = _read_latest_decisions(max(10, args.required_streak))
            latest_dt, latest_decision, latest_reason = rows[0] if rows else ("", "", "")
            allowed_streak = _decision_streak(rows, "ALLOWED")
            blocked_streak = _decision_streak(rows, "BLOCKED")

            if stage == "await_allowed" and allowed_streak >= args.required_streak:
                stage = "await_blocked"
                stage1_reached_at = latest_dt or datetime.now().isoformat(timespec="seconds")
                print(f"[RuntimeStage] etapa 1 atingida naturalmente: ALLOWED x{allowed_streak} em {stage1_reached_at}")
                sys.stdout.flush()
            elif stage == "await_blocked" and blocked_streak >= args.required_streak:
                stage = "completed"
                stage2_reached_at = latest_dt or datetime.now().isoformat(timespec="seconds")
                print(f"[RuntimeStage] etapa 2 atingida naturalmente: BLOCKED x{blocked_streak} em {stage2_reached_at}")
                sys.stdout.flush()

            _write_status(
                {
                    "schema": "greenran.runtime_stage_status.v1",
                    "generated_at": int(time.time()),
                    "generated_at_iso": datetime.now().isoformat(timespec="seconds"),
                    "mode": "passive_observation",
                    "current_stage": stage,
                    "required_streak": args.required_streak,
                    "latest_decision": latest_decision,
                    "latest_reason": latest_reason,
                    "latest_datetime": latest_dt,
                    "allowed_streak": allowed_streak,
                    "blocked_streak": blocked_streak,
                    "stage1_allowed_reached_at": stage1_reached_at,
                    "stage2_blocked_reached_at": stage2_reached_at,
                }
            )
            time.sleep(max(1, args.poll_interval))
    finally:
        if STATUS_PATH.exists():
            STATUS_PATH.unlink()
        print("[RuntimeStage] observador encerrado")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
