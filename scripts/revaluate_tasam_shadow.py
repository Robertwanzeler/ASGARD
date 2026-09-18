#!/usr/bin/env python3
"""Offline simulation-only revaluation of a preserved TA-SAM shadow database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from energy_calibration import integrate_energy_events, load_calibration, state_power_w  # noqa: E402


def _num(value, default=0.0):
    try:
        parsed = float(value)
        return parsed if parsed == parsed and abs(parsed) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _mean(rows, key):
    values = [_num(row[key]) for row in rows if key in row.keys() and row[key] is not None]
    return statistics.fmean(values) if values else None


def _group_name(row):
    raw = str(row["device_type"] or row["domain"] or "unknown").strip().lower() if "device_type" in row.keys() else "unknown"
    if "camera" in raw:
        return "camera"
    if "vehicle" in raw or "car" in raw:
        return "vehicle"
    if "sensor" in raw or "iot" in raw:
        return "sensor"
    return raw or "unknown"


def _simulation_sla(ue_rows):
    real = [
        row for row in ue_rows
        if "pdcp_provenance" in row.keys()
        and str(row["pdcp_provenance"] or "") == "pdcp_real"
        and _num(row["latency_is_proxy"] if "latency_is_proxy" in row.keys() else 1, 1) == 0
    ]
    groups = {}
    for name in sorted({_group_name(row) for row in real}):
        subset = [row for row in real if _group_name(row) == name]
        groups[name] = {
            "rows": len(subset),
            "throughput_kbps": _mean(subset, "throughput_kbps"),
            "latency_p95_us": _mean(subset, "latency_p95_us"),
            "packet_loss_percent": _mean(subset, "packet_loss_percent"),
        }
    return {"pdcp_real_rows": len(real), "proxy_rows": len(ue_rows) - len(real), "groups": groups}


def _simulation_allocation(decisions, comparisons):
    live_shortfall, live_surplus = [], []
    for row in decisions:
        ran_demand = _num(row["ran_demand"] if "ran_demand" in row.keys() else 0.0)
        ai_demand = _num(row["ai_demand"] if "ai_demand" in row.keys() else 0.0)
        ran_allocation = _num(row["ran_allocation"] if "ran_allocation" in row.keys() else 0.0)
        ai_allocation = _num(row["ai_allocation"] if "ai_allocation" in row.keys() else 0.0)
        live_shortfall.append(max(ran_demand - ran_allocation, 0.0) + max(ai_demand - ai_allocation, 0.0))
        live_surplus.append(max(ran_allocation - ran_demand, 0.0) + max(ai_allocation - ai_demand, 0.0))

    samples = []
    for row in comparisons:
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            snapshot = {}
        marl = snapshot.get("marl_shadow") if isinstance(snapshot.get("marl_shadow"), dict) else {}
        comparison = snapshot.get("comparison") if isinstance(snapshot.get("comparison"), dict) else {}
        shadow_ran = _num(marl.get("shadow_r_ran"), 0.0)
        shadow_ai = _num(marl.get("shadow_r_ai"), 0.0)
        delta_ran = _num(row["delta_r_ran"] if "delta_r_ran" in row.keys() else marl.get("delta_r_ran_vs_live"))
        delta_ai = _num(row["delta_r_ai"] if "delta_r_ai" in row.keys() else marl.get("delta_r_ai_vs_live"))
        samples.append({
            "live_ran": shadow_ran - delta_ran,
            "shadow_ran": shadow_ran,
            "live_ai": shadow_ai - delta_ai,
            "shadow_ai": shadow_ai,
            "live_ran_completion": _num(row["live_ran_completion_est"] if "live_ran_completion_est" in row.keys() else comparison.get("live_ran_completion_est")),
            "shadow_ran_completion": _num(row["shadow_ran_completion_est"] if "shadow_ran_completion_est" in row.keys() else comparison.get("shadow_ran_completion_est")),
            "live_ai_completion": _num(row["live_ai_completion_est"] if "live_ai_completion_est" in row.keys() else comparison.get("live_ai_completion_est")),
            "shadow_ai_completion": _num(row["shadow_ai_completion_est"] if "shadow_ai_completion_est" in row.keys() else comparison.get("shadow_ai_completion_est")),
            "live_shortfall": _num(row["live_total_shortfall"] if "live_total_shortfall" in row.keys() else comparison.get("live_total_shortfall")),
            "shadow_shortfall": _num(row["shadow_total_shortfall"] if "shadow_total_shortfall" in row.keys() else comparison.get("shadow_total_shortfall")),
            "live_surplus": _num(comparison.get("live_total_surplus")),
            "shadow_surplus": _num(comparison.get("shadow_total_surplus")),
            "live_budget_gap": _num(row["live_budget_gap"] if "live_budget_gap" in row.keys() else comparison.get("live_budget_gap")),
            "shadow_budget_gap": _num(row["shadow_budget_gap"] if "shadow_budget_gap" in row.keys() else comparison.get("shadow_budget_gap")),
            "energy_saving": _num(row["energy_saving_fraction"] if "energy_saving_fraction" in row.keys() else comparison.get("energy_saving_fraction")),
            "resource_saving": _num(row["resource_saving_fraction"] if "resource_saving_fraction" in row.keys() else comparison.get("resource_saving_fraction")),
        })

    def mean_sample(key):
        return _mean(samples, key)

    live_ran, shadow_ran = mean_sample("live_ran"), mean_sample("shadow_ran")
    live_ai, shadow_ai = mean_sample("live_ai"), mean_sample("shadow_ai")
    live_total = (live_ran or 0.0) + (live_ai or 0.0)
    shadow_total = (shadow_ran or 0.0) + (shadow_ai or 0.0)
    allocation_saving = (live_total - shadow_total) / live_total if live_total > 0 else None
    usable_budget = _mean(decisions, "usable_budget")
    return {
        "live_ran_allocation": live_ran,
        "shadow_ran_allocation": shadow_ran,
        "live_ai_allocation": live_ai,
        "shadow_ai_allocation": shadow_ai,
        "allocation_delta": {
            "ran": shadow_ran - live_ran if live_ran is not None and shadow_ran is not None else None,
            "ai": shadow_ai - live_ai if live_ai is not None and shadow_ai is not None else None,
            "total": shadow_total - live_total,
        },
        "resource_saving_fraction": allocation_saving,
        "budget_utilization": {
            "mean_resource_budget": _mean(decisions, "resource_budget"),
            "mean_usable_budget": usable_budget,
            "live": _mean(decisions, "utilization_ratio"),
            "shadow": shadow_total / usable_budget if usable_budget not in (None, 0.0) else None,
        },
        "shortfall": {"live": statistics.fmean(live_shortfall) if live_shortfall else None, "shadow": mean_sample("shadow_shortfall")},
        "surplus": {"live": statistics.fmean(live_surplus) if live_surplus else None, "shadow": mean_sample("shadow_surplus")},
        "ran_completion": {"live": mean_sample("live_ran_completion"), "shadow": mean_sample("shadow_ran_completion")},
        "ai_completion": {"live": mean_sample("live_ai_completion"), "shadow": mean_sample("shadow_ai_completion")},
        "mean_energy_saving_fraction": mean_sample("energy_saving"),
        "mean_resource_saving_fraction": mean_sample("resource_saving"),
    }


def revaluate(db: Path, output: Path, calibration_path: Path, infrastructure: Path | None = None) -> dict:
    calibration = load_calibration(calibration_path)
    conn = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        energy_rows = conn.execute("SELECT * FROM energy_commands ORDER BY timestamp_ns, id").fetchall()
        comparisons = conn.execute("SELECT * FROM marl_shadow_comparison_history ORDER BY id").fetchall()
        decisions = conn.execute("SELECT * FROM decisions_history ORDER BY id").fetchall()
        ue_rows = conn.execute("SELECT * FROM ue_metrics ORDER BY id").fetchall()
    finally:
        conn.close()

    energy = integrate_energy_events(energy_rows, calibration)
    recalculated = []
    for row in comparisons:
        live_pct = _num(row["live_power_percent"] if "live_power_percent" in row.keys() else 100.0, 100.0)
        shadow_pct = _num(row["shadow_power_percent"] if "shadow_power_percent" in row.keys() else live_pct, live_pct)
        live_w = state_power_w(calibration, 1, 1, live_pct)
        shadow_w = state_power_w(calibration, 1, 1, shadow_pct)
        saving = (live_w - shadow_w) / live_w if live_w > 0 else 0.0
        base = _num(row["base_sla_resource_score_delta"] if "base_sla_resource_score_delta" in row.keys() else row["score_delta"])
        recalculated.append({
            "causal_score_delta": base + 0.20 * max(-1.0, min(1.0, saving)),
            "live_power_w": live_w,
            "shadow_power_w": shadow_w,
            "live_power_percent": live_pct,
            "shadow_power_percent": shadow_pct,
            "energy_saving_fraction": saving,
        })

    old_deltas = [_num(row["causal_score_delta"] if "causal_score_delta" in row.keys() else row["score_delta"]) for row in comparisons]
    old_positive = sum(value > 0.0 for value in old_deltas)
    positive = sum(item["causal_score_delta"] > 0.0 for item in recalculated)
    allocation = _simulation_allocation(decisions, comparisons)
    sla = _simulation_sla(ue_rows)
    checkpoint_valid = sum(_num(row["tasam_checkpoint_valid"] if "tasam_checkpoint_valid" in row.keys() else 0) == 1 for row in comparisons)
    fallback_used = sum(_num(row["tasam_fallback_used"] if "tasam_fallback_used" in row.keys() else 1) != 0 for row in comparisons)
    energy_valid = sum(_num(row["energy_valid"] if "energy_valid" in row.keys() else 0) == 1 for row in comparisons)
    positive_rate = positive / len(recalculated) if recalculated else 0.0
    gate = {
        "minimum_decisions": 300,
        "decisions_ok": len(recalculated) >= 300,
        "checkpoint_valid_all": checkpoint_valid == len(recalculated) and len(recalculated) > 0,
        "fallback_zero": fallback_used == 0,
        "energy_valid_all": energy_valid == len(recalculated) and len(recalculated) > 0,
        "proxy_samples": sla["proxy_rows"],
        "positive_rate_minimum": 0.80,
        "positive_rate": positive_rate,
        "positive_rate_ok": positive_rate >= 0.80,
        "eligible_for_control": False,
        "block_reason": "positive_rate_below_80_or_offline_calibration_not_promotable",
    }
    payload = {
        "schema": "greenran.tasam.shadow.simulation_revaluation.v1",
        "metric_scope": "simulation",
        "source_db": str(db.resolve()),
        "calibration_path": str(calibration_path.resolve()),
        "calibration_version": calibration.get("calibration_version"),
        "calibration_corpus_id": calibration.get("calibration_corpus_id", ""),
        "old_result_preserved": True,
        "control_executed": False,
        "decisions": len(recalculated),
        "old_positive_rate": old_positive / len(old_deltas) if old_deltas else 0.0,
        "old_gate_result": "rejected" if old_deltas and old_positive / len(old_deltas) < 0.80 else "unknown",
        "positive_rate": positive_rate,
        "mean_causal_score_delta": statistics.fmean(item["causal_score_delta"] for item in recalculated) if recalculated else 0.0,
        "simulation_metrics": {
            "energy": energy,
            "energy_per_decision_j": energy.get("energy_j", 0.0) / max(1, len(recalculated)),
            "power": {
                "live_power_w": _mean(recalculated, "live_power_w"),
                "shadow_power_w": _mean(recalculated, "shadow_power_w"),
                "live_power_percent": _mean(recalculated, "live_power_percent"),
                "shadow_power_percent": _mean(recalculated, "shadow_power_percent"),
                "energy_saving_fraction": _mean(recalculated, "energy_saving_fraction"),
            },
            "allocation": allocation,
            "sla_observed": sla,
        },
        "gate": gate,
        "gate_eligible": False,
        "interpretation": "energia relativa da simulacao; nao representa consumo fisico medido",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--infrastructure", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    payload = revaluate(args.db, args.output, args.calibration, args.infrastructure)
    print(json.dumps({"output": str(args.output), "decisions": payload["decisions"], "positive_rate": payload["positive_rate"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
