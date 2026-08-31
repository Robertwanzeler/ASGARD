#!/usr/bin/env python3
"""Exporta a coleta TA-SAM shadow do SQLite para o InfluxDB/Grafana.

Este processo e somente leitura no Data Lake. Ele nao altera, reinicia ou
controla a coleta GreenRAN; apenas publica novas linhas em measurements
separadas para visualizacao no Grafana.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import requests


TABLES = {
    "shadow": "marl_shadow_comparison_history",
    "global": "marl_global_state_history",
    "extended": "extended_metrics",
    "decisions": "decisions_history",
}


def esc(value: Any) -> str:
    """Escape de tag Influx line protocol."""
    return str(value or "").replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def row_time(row: sqlite3.Row) -> int:
    # O timestamp do Data Lake e epoch em segundos. O id diferencia linhas
    # que tenham sido gravadas no mesmo segundo.
    return max(1, int(number(row["timestamp"]))) * 1_000_000_000 + int(row["id"] or 0)


def tags(**values: Any) -> str:
    return ",".join(f"{esc(k)}={esc(v)}" for k, v in values.items() if v not in (None, ""))


def fields(values: dict[str, Any]) -> str:
    return ",".join(f"{key}={number(value):.8f}" for key, value in values.items())


def parse_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["snapshot_json"] or "{}")
        if isinstance(payload, dict):
            return payload
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return {}


def shadow_point(row: sqlite3.Row) -> str:
    snapshot = parse_snapshot(row)
    marl_shadow = snapshot.get("marl_shadow", {}) if isinstance(snapshot.get("marl_shadow"), dict) else {}
    comparison = snapshot.get("comparison", {}) if isinstance(snapshot.get("comparison"), dict) else {}
    stage = marl_shadow.get("scenario_stage", snapshot.get("scenario_stage", ""))
    return (
        f"tasam_shadow_comparison,{tags(policy_id=row['policy_id'], readiness=row['checkpoint_readiness'], stage=stage)} "
        + fields({
            "live_score": row["live_score"],
            "shadow_score": row["shadow_score"],
            "score_delta": row["score_delta"],
            "live_ran_completion": row["live_ran_completion_est"],
            "shadow_ran_completion": row["shadow_ran_completion_est"],
            "live_ai_completion": row["live_ai_completion_est"],
            "shadow_ai_completion": row["shadow_ai_completion_est"],
            "live_shortfall": row["live_total_shortfall"],
            "shadow_shortfall": row["shadow_total_shortfall"],
            "live_budget_gap": row["live_budget_gap"],
            "shadow_budget_gap": row["shadow_budget_gap"],
            "delta_r_ran": row["delta_r_ran"],
            "delta_r_ai": row["delta_r_ai"],
            "available": row["available"],
            "recommend_shadow": row["recommend_shadow"],
            "comparison_score_delta": comparison.get("score_delta", row["score_delta"]),
        })
        + f" {row_time(row)}"
    )


def global_point(row: sqlite3.Row) -> str:
    vector = []
    try:
        vector = json.loads(row["state_vector_json"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    values = {
        "total_demand": row["total_demand"],
        "usable_budget": row["usable_budget"],
        "du_count": row["logical_du_count"],
    }
    if isinstance(vector, list):
        for index, value in enumerate(vector[:10]):
            values[f"state_{index}"] = value
    return f"tasam_global_state,{tags(topology=row['topology_id'])} {fields(values)} {row_time(row)}"


def extended_point(row: sqlite3.Row) -> str:
    return (
        f"tasam_extended_metrics,{tags(collector_mode=row['collector_mode'], throughput_source=row['throughput_source'])} "
        + fields({
            "worst_latency_ms": number(row["global_worst_latency_us"]) / 1000.0,
            "avg_latency_ms": number(row["global_avg_latency_us"]) / 1000.0,
            "p95_latency_ms": number(row["latency_p95_us"]) / 1000.0,
            "cvar_ms": number(row["cvar_per_ue_us"]) / 1000.0,
            "packet_loss_rate": row["global_packet_loss_rate"],
            "throughput_kbps": row["throughput_kbps"],
            "active_ues": row["total_active_ues"],
            "active_cameras": row["total_active_cameras"],
            "real_latency_samples": row["real_latency_sample_count"],
            "proxy_latency_samples": row["proxy_latency_sample_count"],
            "pdcp_stale": row["pdcp_stale"],
        })
        + f" {row_time(row)}"
    )


def decision_point(row: sqlite3.Row) -> str:
    return (
        f"tasam_decision,{tags(decision=row['decision'], tasam_mode=row['tasam_mode'], winner=row['advisor_arbitration_winner'], armd_mode=row['armd_mode'])} "
        + fields({
            "confidence": row["tasam_confidence"],
            "valid": row["tasam_valid"],
            "would_influence": row["tasam_would_influence"],
            "arbitration_score": row["advisor_arbitration_score"],
            "ran_completion": row["ran_completion_ratio"],
            "ai_completion": row["ai_completion_ratio"],
            "network_improvement_pct": row["network_improvement_pct"],
            "cvar_improvement_pct": row["cvar_improvement_pct"],
        })
        + f" {row_time(row)}"
    )


def summary_point(db: Path, window: int = 300) -> str | None:
    """Publica um resumo da janela recente para cartões do Grafana."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT score_delta, live_score, shadow_score
            FROM marl_shadow_comparison_history
            ORDER BY id DESC LIMIT ?
            """,
            (max(1, int(window)),),
        ).fetchall()
        total = int(conn.execute("SELECT count(*) FROM marl_shadow_comparison_history").fetchone()[0] or 0)
    finally:
        conn.close()
    if not rows:
        return None
    deltas = [number(row["score_delta"]) for row in rows]
    latest = rows[0]
    return (
        f"tasam_shadow_summary,window={int(window)} "
        + fields({
            "total_comparisons": total,
            "window_comparisons": len(rows),
            "positive_rate": sum(delta > 0.01 for delta in deltas) / len(deltas),
            "avg_delta": sum(deltas) / len(deltas),
            "latest_delta": latest["score_delta"],
            "latest_live_score": latest["live_score"],
            "latest_shadow_score": latest["shadow_score"],
        })
        + f" {time.time_ns()}"
    )


def fetch_rows(db: Path, table: str, last_id: int) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            f"SELECT * FROM {table} WHERE id > ? ORDER BY id ASC LIMIT 500", (last_id,)
        ).fetchall()
    finally:
        conn.close()


def publish(lines: list[str], url: str) -> None:
    if not lines:
        return
    response = requests.post(
        url,
        data="\n".join(lines).encode("utf-8"),
        headers={"Content-Type": "application/octet-stream"},
        timeout=10,
    )
    response.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description="Publica TA-SAM shadow no Grafana sem alterar a coleta")
    parser.add_argument("--db", required=True, help="SQLite Data Lake da coleta")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8086)
    parser.add_argument("--database", default="influx")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    db = Path(args.db).resolve()
    url = f"http://{args.host}:{args.port}/write?db={args.database}&precision=ns"
    last_ids = {key: 0 for key in TABLES}
    print(f"[TASAM_INFLUX] somente leitura: {db}", flush=True)
    print(f"[TASAM_INFLUX] destino: {url}", flush=True)

    while True:
        published = 0
        try:
            for key, table in TABLES.items():
                rows = fetch_rows(db, table, last_ids[key])
                lines = []
                for row in rows:
                    if key == "shadow":
                        lines.append(shadow_point(row))
                    elif key == "global":
                        lines.append(global_point(row))
                    elif key == "extended":
                        lines.append(extended_point(row))
                    else:
                        lines.append(decision_point(row))
                    last_ids[key] = max(last_ids[key], int(row["id"]))
                if lines:
                    publish(lines, url)
                    published += len(lines)
            summary = summary_point(db)
            if summary:
                publish([summary], url)
            if published:
                print(f"[TASAM_INFLUX] publicadas={published} ids={last_ids}", flush=True)
        except (OSError, sqlite3.Error, requests.RequestException, KeyError, ValueError) as exc:
            print(f"[TASAM_INFLUX] aguardando: {exc}", flush=True)
        if args.once:
            return 0
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
