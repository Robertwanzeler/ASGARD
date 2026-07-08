#!/usr/bin/env python3
"""Filter exported TA-SAM traces into cleaner training subsets."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


POSTFIX_CLEAN_SINCE_TS = 1782930635
POSTFIX_CLEAN_ALLOWED_STAGES = (
    "allowed_bootstrap",
    "allowed_stable",
    "camera_conditional",
    "camera_blocked",
    "vehicle_conditional",
    "vehicle_blocked",
    "app2_conditional",
    "app2_blocked",
    "allowed_recovery",
)
POSTFIX_CLEAN_EXPECTED_DECISION = {
    "allowed_bootstrap": "ALLOWED",
    "allowed_stable": "ALLOWED",
    "camera_conditional": "CONDITIONAL",
    "camera_blocked": "BLOCKED",
    "vehicle_conditional": "CONDITIONAL",
    "vehicle_blocked": "BLOCKED",
    "app2_conditional": "CONDITIONAL",
    "app2_blocked": "BLOCKED",
    "allowed_recovery": "ALLOWED",
}

PROFILES = ("raw", "article_faithful", "article_stress", "postfix_clean", "rapp_online_trainable")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Filter TA-SAM transition traces")
    parser.add_argument("--input-jsonl", required=True, help="Input transition JSONL")
    parser.add_argument("--output-jsonl", required=True, help="Filtered output JSONL")
    parser.add_argument("--summary-json", default=None, help="Optional filter summary JSON")
    parser.add_argument("--profile", choices=PROFILES, default="article_faithful", help="Filter profile")
    parser.add_argument("--max-p95-ms", type=float, default=None, help="Optional upper bound for latency_p95_us")
    parser.add_argument("--max-cvar-ms", type=float, default=None, help="Optional upper bound for cvar_per_ue_us")
    return parser


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    q = max(0.0, min(1.0, q))
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return ordered[lower]
    weight = pos - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def metric_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


def profile_rules(profile: str) -> dict[str, Any]:
    if profile == "raw":
        return {
            "allowed_stages": None,
            "require_valid": False,
            "require_pdcp_real": False,
            "require_proxy_free": False,
            "since_ts": 0,
            "require_metrics": False,
            "require_next_metrics": False,
            "expected_stage_decisions": {},
            "strict_stage_reasons": False,
        }
    if profile == "article_stress":
        return {
            "allowed_stages": {"conflict_context"},
            "require_valid": True,
            "require_pdcp_real": True,
            "require_proxy_free": True,
            "since_ts": 0,
            "require_metrics": False,
            "require_next_metrics": False,
            "expected_stage_decisions": {},
            "strict_stage_reasons": False,
        }
    if profile == "postfix_clean":
        return {
            "allowed_stages": set(POSTFIX_CLEAN_ALLOWED_STAGES),
            "require_valid": True,
            "require_pdcp_real": True,
            "require_proxy_free": True,
            "since_ts": POSTFIX_CLEAN_SINCE_TS,
            "require_metrics": True,
            "require_next_metrics": True,
            "expected_stage_decisions": dict(POSTFIX_CLEAN_EXPECTED_DECISION),
            "strict_stage_reasons": True,
        }
    if profile == "rapp_online_trainable":
        return {
            "allowed_stages": set(POSTFIX_CLEAN_ALLOWED_STAGES),
            "require_valid": True,
            "require_pdcp_real": True,
            "require_proxy_free": True,
            "since_ts": POSTFIX_CLEAN_SINCE_TS,
            "require_metrics": True,
            "require_next_metrics": True,
            "expected_stage_decisions": dict(POSTFIX_CLEAN_EXPECTED_DECISION),
            "strict_stage_reasons": True,
        }
    return {
        "allowed_stages": {"baseline_healthy"},
        "require_valid": True,
        "require_pdcp_real": True,
        "require_proxy_free": True,
        "since_ts": 0,
        "require_metrics": False,
        "require_next_metrics": False,
        "expected_stage_decisions": {},
        "strict_stage_reasons": False,
    }


def should_keep(record: dict[str, Any], rules: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str]:
    quality = record.get("collection_quality") or {}
    metrics = record.get("metrics") or {}
    next_metrics = record.get("next_metrics") or {}
    stage = str(record.get("scenario_stage") or "unknown")
    timestamp = int(record.get("timestamp") or 0)
    decision = str((record.get("decision") or {}).get("decision") or "unknown")

    if timestamp < int(rules.get("since_ts", 0) or 0):
        return False, "pre_fix"
    if bool(rules.get("require_metrics")) and not metrics:
        return False, "missing_metrics"
    if bool(rules.get("require_next_metrics")) and not next_metrics:
        return False, "missing_next_metrics"

    if rules["allowed_stages"] is not None and stage not in rules["allowed_stages"]:
        if bool(rules.get("strict_stage_reasons")):
            return False, "unexpected_stage"
        return False, f"stage:{stage}"
    expected_decision = (rules.get("expected_stage_decisions") or {}).get(stage)
    if expected_decision and decision != expected_decision:
        return False, "unexpected_decision_for_stage"
    if rules["require_proxy_free"] and safe_float(quality.get("proxy_latency_sample_count"), 0.0) > 0.0:
        return False, "proxy_latency"
    if rules["require_pdcp_real"]:
        collector_mode = str(quality.get("collector_mode") or "").lower()
        pdcp_real = bool(quality.get("pdcp_real", False))
        if collector_mode not in {"", "pdcp_real"} and not pdcp_real:
            return False, f"collector_mode:{quality.get('collector_mode') or 'unknown'}"
        if collector_mode == "" and not pdcp_real:
            return False, "pdcp_real:false"
    if rules["require_valid"] and not bool(quality.get("valid_for_training", False)):
        return False, "invalid_transition"

    p95_ms = safe_float(metrics.get("latency_p95_us"), 0.0) / 1000.0
    cvar_ms = safe_float(metrics.get("cvar_per_ue_us"), 0.0) / 1000.0
    if args.max_p95_ms is not None and p95_ms > args.max_p95_ms:
        return False, "p95_cap"
    if args.max_cvar_ms is not None and cvar_ms > args.max_cvar_ms:
        return False, "cvar_cap"
    return True, "kept"


def main() -> int:
    args = build_parser().parse_args()
    input_path = Path(args.input_jsonl)
    output_path = Path(args.output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rules = profile_rules(args.profile)
    stage_before: Counter[str] = Counter()
    stage_after: Counter[str] = Counter()
    decision_before: Counter[str] = Counter()
    decision_after: Counter[str] = Counter()
    drop_reasons: Counter[str] = Counter()
    stage_decisions_after: dict[str, Counter[str]] = {}
    p95_kept: list[float] = []
    cvar_kept: list[float] = []
    written = 0
    total = 0

    with input_path.open("r", encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            total += 1
            record = json.loads(line)
            stage = str(record.get("scenario_stage") or "unknown")
            decision = str((record.get("decision") or {}).get("decision") or "unknown")
            stage_before[stage] += 1
            decision_before[decision] += 1
            keep, reason = should_keep(record, rules, args)
            if not keep:
                drop_reasons[reason] += 1
                continue
            stage_after[stage] += 1
            decision_after[decision] += 1
            if stage not in stage_decisions_after:
                stage_decisions_after[stage] = Counter()
            stage_decisions_after[stage][decision] += 1
            metrics = record.get("metrics") or {}
            p95_kept.append(safe_float(metrics.get("latency_p95_us"), 0.0) / 1000.0)
            cvar_kept.append(safe_float(metrics.get("cvar_per_ue_us"), 0.0) / 1000.0)
            dst.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    stage_purity_after: dict[str, Any] = {}
    expected_stage_decisions = rules.get("expected_stage_decisions") or {}
    for stage, counter in sorted(stage_decisions_after.items()):
        total_stage = sum(counter.values())
        top_decision, top_count = counter.most_common(1)[0]
        expected_decision = expected_stage_decisions.get(stage) or top_decision
        expected_count = counter.get(expected_decision, 0)
        stage_purity_after[stage] = {
            "total": total_stage,
            "expected_decision": expected_decision,
            "expected_count": expected_count,
            "purity": 0.0 if total_stage == 0 else expected_count / total_stage,
            "decision_counts": dict(counter),
        }

    summary = {
        "schema": "greenran.tasam_trace_filter_summary.v1",
        "input_jsonl": str(input_path),
        "output_jsonl": str(output_path),
        "profile": args.profile,
        "rules": {
            "allowed_stages": sorted(rules["allowed_stages"]) if rules["allowed_stages"] is not None else "all",
            "require_valid": rules["require_valid"],
            "require_pdcp_real": rules["require_pdcp_real"],
            "require_proxy_free": rules["require_proxy_free"],
            "since_ts": int(rules.get("since_ts", 0) or 0),
            "require_metrics": bool(rules.get("require_metrics", False)),
            "require_next_metrics": bool(rules.get("require_next_metrics", False)),
            "expected_stage_decisions": dict(expected_stage_decisions),
            "max_p95_ms": args.max_p95_ms,
            "max_cvar_ms": args.max_cvar_ms,
        },
        "rows_before": total,
        "rows_after": written,
        "drop_ratio": 0.0 if total == 0 else (total - written) / total,
        "drop_reasons": dict(drop_reasons),
        "stage_counts_before": dict(stage_before),
        "stage_counts_after": dict(stage_after),
        "decision_counts_before": dict(decision_before),
        "decision_counts_after": dict(decision_after),
        "stage_purity_after": stage_purity_after,
        "kept_metrics_ms": {
            "latency_p95": metric_stats(p95_kept),
            "cvar": metric_stats(cvar_kept),
        },
    }
    if args.summary_json:
        summary_path = Path(args.summary_json)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
