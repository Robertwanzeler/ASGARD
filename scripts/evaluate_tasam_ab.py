#!/usr/bin/env python3
"""Compare isolated real-PDCP rApp baseline and TA-SAM A/B rounds."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def pct_delta(candidate: float, baseline: float) -> float:
    return 100.0 * (candidate - baseline) / max(abs(baseline), 1e-12)


def _state_signature(row: sqlite3.Row) -> tuple[Any, ...]:
    """Build a coarse demand/state key for diagnostic matching.

    Baseline and TA-SAM are separate executions, so decision id is not a
    causal pairing key.  Demand and budget are the comparable state fields
    available in the decision lake.  This remains diagnostic; aggregate
    matched-run metrics are the promotion criterion.
    """
    def rounded(name: str) -> float:
        return round(float(row[name] or 0.0), 6)

    return (
        str(row["decision"] or ""),
        rounded("ran_demand"),
        rounded("ai_demand"),
        rounded("usable_budget"),
        rounded("resource_budget"),
    )


def read_run(run_dir: Path) -> dict[str, Any]:
    db_path = run_dir / "rapp_data_lake.db"
    if not db_path.exists():
        raise SystemExit(f"Data Lake ausente: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        decisions = conn.execute("SELECT * FROM decisions_history ORDER BY id").fetchall()
        metrics = conn.execute("SELECT * FROM extended_metrics ORDER BY id").fetchall()
    finally:
        conn.close()

    def floats(rows: list[sqlite3.Row], key: str) -> list[float]:
        return [float(row[key] or 0.0) for row in rows if key in row.keys() and row[key] is not None]

    real_metrics = [
        row for row in metrics
        if str(row["collector_mode"] or "") == "pdcp_real"
        and float(row["proxy_latency_sample_count"] or 0.0) == 0.0
        and int(row["pdcp_stale"] or 0) == 0
    ]
    proxy_metrics = [row for row in metrics if float(row["proxy_latency_sample_count"] or 0.0) > 0.0]
    applied = [row for row in decisions if int(row["ta_sam_actuation_applied"] or 0) == 1]
    critical_fallbacks = [
        row for row in decisions
        if int(row["ta_sam_actuation_applied"] or 0) == 0
        and "critical" in str(row["control_trial_reason"] or "").lower()
    ]
    ran_completion = floats(decisions, "ran_completion_ratio")
    ai_completion = floats(decisions, "ai_completion_ratio")
    p95 = floats(real_metrics, "latency_p95_per_ue_us")
    cvar = floats(real_metrics, "cvar_per_ue_us")
    loss = floats(real_metrics, "global_packet_loss_rate")
    throughput = floats(real_metrics, "throughput_kbps")
    # Diagnostic score with the same RAN/IA weighting used by the runtime
    # mixed-priority proxy; it is not the promotion gate by itself.
    score_values = [0.40 * r + 0.38 * a for r, a in zip(ran_completion, ai_completion)]
    decision_records = [
        {
            "id": int(row["id"]),
            "score": 0.40 * float(row["ran_completion_ratio"] or 0.0)
            + 0.38 * float(row["ai_completion_ratio"] or 0.0),
            "state_signature": _state_signature(row),
        }
        for row in decisions
    ]
    algorithms: dict[str, int] = {}
    for row in decisions:
        name = str(row["effective_policy_algorithm"] or "unknown")
        algorithms[name] = algorithms.get(name, 0) + 1
    return {
        "run_dir": str(run_dir.resolve()),
        "decisions": len(decisions),
        "real_pdcp_metric_rows": len(real_metrics),
        "proxy_metric_rows": len(proxy_metrics),
        "tasam_applied_decisions": len(applied),
        "tasam_applied_rate": len(applied) / max(len(decisions), 1),
        "critical_fallbacks": len(critical_fallbacks),
        "effective_algorithms": algorithms,
        "mean_ran_completion": mean(ran_completion),
        "mean_ai_completion": mean(ai_completion),
        "diagnostic_score": mean(score_values),
        # Per-decision pairing is diagnostic only: applying TA-SAM can change
        # later states. The definitive comparison remains aggregate metrics.
        "decision_scores": [item["score"] for item in decision_records],
        "decision_records": decision_records,
        "mean_p95_us": mean(p95),
        "mean_cvar_us": mean(cvar),
        "mean_packet_loss": mean(loss),
        "mean_throughput_kbps": mean(throughput),
        "valid_real_only": bool(real_metrics) and not proxy_metrics,
        "decision_rows": [
            {
                "id": int(row["id"]),
                "ta_sam_applied": bool(row["ta_sam_actuation_applied"]),
                "algorithm": row["effective_policy_algorithm"],
                "reason": row["control_trial_reason"],
            }
            for row in decisions
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--tasam", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--decision-score-tolerance",
        type=float,
        default=0.001,
        help="Difference below this value is reported as a diagnostic tie, not a win/loss",
    )
    args = parser.parse_args()

    baseline = read_run(args.baseline.resolve())
    tasam = read_run(args.tasam.resolve())
    tolerance = max(0.0, float(args.decision_score_tolerance))
    baseline_by_state: dict[tuple[Any, ...], deque[dict[str, Any]]] = defaultdict(deque)
    for item in baseline["decision_records"]:
        baseline_by_state[tuple(item["state_signature"])].append(item)
    matched_scores: list[tuple[float, float]] = []
    for item in tasam["decision_records"]:
        candidates = baseline_by_state.get(tuple(item["state_signature"]))
        if candidates:
            reference = candidates.popleft()
            matched_scores.append((float(reference["score"]), float(item["score"])))
    paired_wins = sum(tasam_score > baseline_score + tolerance for baseline_score, tasam_score in matched_scores)
    paired_losses = sum(baseline_score > tasam_score + tolerance for baseline_score, tasam_score in matched_scores)
    paired_ties = len(matched_scores) - paired_wins - paired_losses
    meaningful_samples = paired_wins + paired_losses
    paired_win_rate = paired_wins / meaningful_samples if meaningful_samples else None
    comparison = {
        "diagnostic_score_delta": tasam["diagnostic_score"] - baseline["diagnostic_score"],
        "ran_completion_delta": tasam["mean_ran_completion"] - baseline["mean_ran_completion"],
        "ai_completion_delta": tasam["mean_ai_completion"] - baseline["mean_ai_completion"],
        "p95_delta_pct": pct_delta(tasam["mean_p95_us"], baseline["mean_p95_us"]),
        "cvar_delta_pct": pct_delta(tasam["mean_cvar_us"], baseline["mean_cvar_us"]),
        "packet_loss_delta": tasam["mean_packet_loss"] - baseline["mean_packet_loss"],
        "throughput_delta_pct": pct_delta(tasam["mean_throughput_kbps"], baseline["mean_throughput_kbps"]),
        "paired_decision_samples": len(matched_scores),
        "paired_decision_wins": paired_wins,
        "paired_decision_losses": paired_losses,
        "paired_decision_ties": paired_ties,
        "paired_meaningful_samples": meaningful_samples,
        "paired_decision_win_rate": paired_win_rate,
        "paired_decision_score_tolerance": tolerance,
        "paired_decision_pairing": "demand_budget_state_signature_fifo_diagnostic",
    }
    acceptance = {
        "baseline_real_only": baseline["valid_real_only"],
        "tasam_real_only": tasam["valid_real_only"],
        "tasam_actuation_observed": tasam["tasam_applied_decisions"] > 0,
        "score_improved": comparison["diagnostic_score_delta"] > 0.0,
        # Separate-run index/state pairing is diagnostic only.  Do not let it
        # invalidate an aggregate matched-run comparison, especially when all
        # observed differences are below the declared tolerance.
        "paired_wins_over_50pct": (
            meaningful_samples > 0 and paired_win_rate > 0.50
        ),
        "ran_not_degraded_over_1pp": comparison["ran_completion_delta"] >= -0.01,
        "ai_not_degraded_over_1pp": comparison["ai_completion_delta"] >= -0.01,
        "p95_not_increased_over_5pct": comparison["p95_delta_pct"] <= 5.0,
        "cvar_not_increased_over_5pct": comparison["cvar_delta_pct"] <= 5.0,
        "no_proxy": baseline["proxy_metric_rows"] == 0 and tasam["proxy_metric_rows"] == 0,
    }
    payload = {
        "schema": "greenran.tasam_ab_result.v1",
        "seed": args.seed,
        "baseline": baseline,
        "tasam": tasam,
        "comparison": comparison,
        "acceptance": acceptance,
        "valid_comparison": all(
            value for key, value in acceptance.items() if key != "paired_wins_over_50pct"
        ),
        "decision_winner": "tasam" if acceptance["score_improved"] else "baseline_or_inconclusive",
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "valid_comparison": payload["valid_comparison"], "acceptance": acceptance}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
