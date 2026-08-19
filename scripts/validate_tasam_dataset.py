#!/usr/bin/env python3
"""Validate a TA-SAM transition trace before offline training.

The validator is deliberately independent from the trainer.  It prevents a
paper/6-DU trace from being silently used by the 3-DU GreenRAN policy and
rejects traces whose real latency signal has collapsed to one constant value.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_TOPOLOGY_ID = "greenran_fixed_marl_v1"
SLICE_ORDER = ("eMBB", "mMTC", "URLLC")


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a TA-SAM transition trace")
    parser.add_argument("--trace-jsonl", required=True, help="Transition JSONL to validate")
    parser.add_argument("--output-json", default=None, help="Optional quality report path")
    parser.add_argument("--expected-topology-id", default=DEFAULT_TOPOLOGY_ID)
    parser.add_argument("--expected-du-count", type=int, default=3)
    parser.add_argument("--expected-du-state-dim", type=int, default=10)
    parser.add_argument("--expected-action-dim", type=int, default=3)
    parser.add_argument("--min-transitions", type=int, default=1500)
    parser.add_argument("--min-distinct-latency-values", type=int, default=2)
    parser.add_argument("--min-class-ratio", type=float, default=0.05)
    parser.add_argument("--allow-invalid", action="store_true", help="Do not reject invalid collection_quality rows")
    return parser


def validate_trace(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    counts = Counter()
    latency_values: set[float] = set()
    cvar_values: set[float] = set()
    topology_ids: Counter[str] = Counter()
    du_counts: Counter[int] = Counter()
    state_dims: Counter[int] = Counter()
    action_dims: Counter[int] = Counter()
    previous_timestamp: int | None = None
    rows = 0
    invalid_quality = 0
    missing_next = 0

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            rows += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: invalid JSON ({exc.msg})")
                continue

            topology_id = str(record.get("topology_id") or "")
            topology_ids[topology_id] += 1
            if topology_id != args.expected_topology_id:
                errors.append(f"line {line_number}: topology_id={topology_id!r}, expected {args.expected_topology_id!r}")

            timestamp = _int(record.get("timestamp"))
            if timestamp is None:
                errors.append(f"line {line_number}: missing timestamp")
            elif previous_timestamp is not None and timestamp <= previous_timestamp:
                errors.append(f"line {line_number}: timestamps are not strictly increasing")
            previous_timestamp = timestamp if timestamp is not None else previous_timestamp

            global_state = record.get("global_state") or {}
            if str(global_state.get("topology_id") or topology_id) != args.expected_topology_id:
                errors.append(f"line {line_number}: global_state topology mismatch")
            du_states = record.get("du_states") or []
            du_count = len(du_states)
            du_counts[du_count] += 1
            if du_count != args.expected_du_count:
                errors.append(f"line {line_number}: du_count={du_count}, expected {args.expected_du_count}")
            for du in du_states:
                state_vector = du.get("state_vector") or {}
                state_dim = len(state_vector)
                state_dims[state_dim] += 1
                if state_dim != args.expected_du_state_dim:
                    errors.append(f"line {line_number}: du_state_dim={state_dim}, expected {args.expected_du_state_dim}")
                slice_mix = du.get("slice_mix") or {}
                observed_action_dim = len([slice_id for slice_id in SLICE_ORDER if slice_id in slice_mix])
                action_dims[observed_action_dim] += 1
                if observed_action_dim != args.expected_action_dim:
                    errors.append(
                        f"line {line_number}: derived action_dim={observed_action_dim}, "
                        f"expected {args.expected_action_dim}"
                    )

            action = record.get("action") or {}
            if not action:
                errors.append(f"line {line_number}: missing action")

            next_global = record.get("next_global_state") or {}
            next_du_states = record.get("next_du_states") or []
            if not next_global or len(next_du_states) != args.expected_du_count:
                missing_next += 1
                errors.append(f"line {line_number}: missing or incompatible next state")

            reward = _float(record.get("reward_hint"))
            if reward is None:
                errors.append(f"line {line_number}: invalid reward_hint")

            quality = record.get("collection_quality") or {}
            is_valid = bool(quality.get("valid_for_training"))
            if not is_valid:
                invalid_quality += 1
                if not args.allow_invalid:
                    errors.append(f"line {line_number}: collection_quality.valid_for_training is false")
            collector_mode = str(quality.get("collector_mode") or "").lower()
            if collector_mode not in {"", "pdcp_real"} and not bool(quality.get("pdcp_real")):
                errors.append(f"line {line_number}: collector_mode is not pdcp_real")
            if bool(quality.get("has_proxy")) or _float(quality.get("proxy_latency_sample_count")) not in (None, 0.0):
                errors.append(f"line {line_number}: proxy latency present")
            if bool(quality.get("sim_reset")):
                errors.append(f"line {line_number}: simulation reset marked in transition")

            decision = str((record.get("decision") or {}).get("decision") or "unknown")
            counts[decision] += 1
            metrics = record.get("metrics") or {}
            latency = _float(metrics.get("latency_p95_us"))
            cvar = _float(metrics.get("cvar_per_ue_us"))
            if latency is not None:
                latency_values.add(round(latency, 6))
            if cvar is not None:
                cvar_values.add(round(cvar, 6))

    if rows < args.min_transitions:
        errors.append(f"only {rows} transitions; minimum is {args.min_transitions}")
    if len(latency_values) < args.min_distinct_latency_values:
        errors.append(
            f"latency_p95_us has only {len(latency_values)} distinct values; "
            f"minimum is {args.min_distinct_latency_values}"
        )
        warnings.append("latency signal may be stale, saturated, or incorrectly sourced")
    if len(cvar_values) < args.min_distinct_latency_values:
        errors.append(
            f"cvar_per_ue_us has only {len(cvar_values)} distinct values; "
            f"minimum is {args.min_distinct_latency_values}"
        )
    if rows:
        for decision in ("ALLOWED", "CONDITIONAL", "BLOCKED"):
            ratio = counts[decision] / rows
            if ratio < args.min_class_ratio:
                warnings.append(f"decision class {decision} ratio is {ratio:.3f} < {args.min_class_ratio:.3f}")

    if len(errors) > 100:
        errors = errors[:100] + [f"additional validation errors omitted: {len(errors) - 100}"]

    report = {
        "schema": "greenran.tasam_dataset_quality.v1",
        "trace_jsonl": str(path.resolve()),
        "expected": {
            "topology_id": args.expected_topology_id,
            "du_count": args.expected_du_count,
            "du_state_dim": args.expected_du_state_dim,
            "action_dim": args.expected_action_dim,
            "min_transitions": args.min_transitions,
        },
        "rows": rows,
        "invalid_quality_rows": invalid_quality,
        "missing_next_state_rows": missing_next,
        "topology_ids": dict(topology_ids),
        "du_counts": {str(k): v for k, v in du_counts.items()},
        "du_state_dims": {str(k): v for k, v in state_dims.items()},
        "derived_action_dims": {str(k): v for k, v in action_dims.items()},
        "decision_counts": dict(counts),
        "distinct_latency_p95_us": len(latency_values),
        "distinct_cvar_per_ue_us": len(cvar_values),
        "quality_gate": "passed" if not errors else "blocked",
        "training_ready": not errors,
        "errors": errors,
        "warnings": warnings,
    }
    return report


def main() -> int:
    args = build_parser().parse_args()
    path = Path(args.trace_jsonl)
    if not path.exists():
        raise SystemExit(f"trace not found: {path}")
    report = validate_trace(path, args)
    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["training_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
