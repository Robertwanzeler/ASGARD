#!/usr/bin/env python3
"""Monitor and stop a real GreenRAN TA-SAM shadow collection target."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STOP_SCRIPT = ROOT / "scripts" / "stop_tasam_article_ns3_collection.sh"


def count_rows(db: Path) -> dict[str, int]:
    result = {
        "shadow": 0,
        "global": 0,
        "extended": 0,
        "decisions": 0,
    }
    if not db.exists():
        return result
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            cur = conn.cursor()
            for key, table in (
                ("shadow", "marl_shadow_comparison_history"),
                ("global", "marl_global_state_history"),
                ("extended", "extended_metrics"),
                ("decisions", "decisions_history"),
            ):
                try:
                    result[key] = int(cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0] or 0)
                except sqlite3.OperationalError:
                    pass
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return result


def shadow_window(db: Path, window: int) -> dict[str, object]:
    empty = {
        "sample_count": 0,
        "positive_rate": 0.0,
        "avg_delta": 0.0,
        "latest_policy": "",
        "latest_readiness": "",
    }
    if not db.exists():
        return empty
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT policy_id, checkpoint_readiness, score_delta
                FROM marl_shadow_comparison_history
                ORDER BY rowid DESC LIMIT ?
                """,
                (max(1, int(window)),),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return empty
    if not rows:
        return empty
    deltas = [float(row["score_delta"] or 0.0) for row in rows]
    return {
        "sample_count": len(rows),
        "positive_rate": sum(delta > 0.01 for delta in deltas) / len(deltas),
        "avg_delta": sum(deltas) / len(deltas),
        "latest_policy": str(rows[0]["policy_id"] or ""),
        "latest_readiness": str(rows[0]["checkpoint_readiness"] or ""),
    }


def stop_collection(state_dir: Path) -> None:
    subprocess.run(
        ["/bin/bash", str(STOP_SCRIPT)],
        cwd=str(ROOT),
        env={"GREENRAN_STATE_DIR": str(state_dir)},
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Acompanhar uma meta de comparacoes TA-SAM shadow reais")
    parser.add_argument("--db", required=True, help="SQLite Data Lake da coleta")
    parser.add_argument("--state-dir", required=True, help="Estado GreenRAN usado para parar a coleta")
    parser.add_argument("--target-new", type=int, default=1000, help="Meta de novas comparacoes")
    parser.add_argument("--baseline", type=int, default=None, help="Contador inicial fixo; sem isso usa o contador atual")
    parser.add_argument("--poll-seconds", type=float, default=10.0, help="Intervalo de atualizacao")
    parser.add_argument("--window", type=int, default=300, help="Janela para delta medio")
    parser.add_argument("--no-stop", action="store_true", help="Nao parar automaticamente ao atingir a meta")
    args = parser.parse_args()

    db = Path(args.db).resolve()
    state_dir = Path(args.state_dir).resolve()
    initial = count_rows(db)["shadow"] if args.baseline is None else max(0, int(args.baseline))
    target = initial + max(1, int(args.target_new))
    print(f"Inicio: {datetime.now().isoformat(timespec='seconds')}", flush=True)
    print(f"Base anterior: {initial} comparacoes", flush=True)
    print(f"Meta nova: {target} comparacoes totais (+{target - initial})", flush=True)

    while True:
        counts = count_rows(db)
        current = counts["shadow"]
        new_count = max(0, current - initial)
        pct = min(100.0, 100.0 * new_count / max(1, target - initial))
        window = shadow_window(db, args.window)
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"shadow={current} (+{new_count}/{target - initial}, {pct:5.1f}%) "
            f"global={counts['global']} extended={counts['extended']} "
            f"delta{args.window}={float(window['avg_delta']):+.5f} "
            f"positivos={100.0 * float(window['positive_rate']):.1f}% "
            f"policy={window['latest_policy']} readiness={window['latest_readiness']}",
            flush=True,
        )
        if current >= target:
            print("Meta atingida.", flush=True)
            if not args.no_stop:
                print("Parando os servicos da coleta...", flush=True)
                stop_collection(state_dir)
            return 0
        time.sleep(max(1.0, float(args.poll_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
