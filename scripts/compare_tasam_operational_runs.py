#!/usr/bin/env python3
"""Compare matched aggregate metrics from baseline and assistant-only runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def pct_change(new: float, old: float) -> float:
    return 100.0 * (new - old) / max(abs(old), 1e-12)


def reduction_pct(new: float, old: float) -> float:
    return 100.0 * (old - new) / max(abs(old), 1e-12)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--assistant", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    assistant = json.loads(args.assistant.read_text(encoding="utf-8"))
    b = baseline["network_metrics"]
    a = assistant["network_metrics"]
    b_energy = b.get("energy_metric") or {}
    a_energy = a.get("energy_metric") or {}
    energy_valid = bool(b_energy.get("valid", False) and a_energy.get("valid", False))
    baseline_energy_j = float(b_energy.get("energy_j", 0.0) or 0.0)
    assistant_energy_j = float(a_energy.get("energy_j", 0.0) or 0.0)
    baseline_power_w = float(b_energy.get("average_power_w", 0.0) or 0.0)
    assistant_power_w = float(a_energy.get("average_power_w", 0.0) or 0.0)
    report = {
        "schema": "greenran.tasam_operational_comparison.v2",
        "baseline_report": str(args.baseline.resolve()),
        "assistant_report": str(args.assistant.resolve()),
        "valid_inputs": bool(
            baseline["acceptance"]["valid"]
            and assistant["acceptance"]["valid"]
            and energy_valid
        ),
        "energy_valid": energy_valid,
        "decision_counts": {"baseline": b["decisions"], "assistant": a["decisions"]},
        "assistant_minus_baseline": {
            "composite_index_absolute": a["composite_index"] - b["composite_index"],
            "composite_index_percent": pct_change(a["composite_index"], b["composite_index"]),
            "sla_score_absolute": a["sla_score"] - b["sla_score"],
            "sla_score_percentage_points": 100.0 * (a["sla_score"] - b["sla_score"]),
            "sla_score_percent": pct_change(a["sla_score"], b["sla_score"]),
            "p95_reduction_percent": reduction_pct(a["mean_p95_us"], b["mean_p95_us"]),
            "cvar_reduction_percent": reduction_pct(a["mean_cvar_us"], b["mean_cvar_us"]),
            "throughput_percent": pct_change(a["mean_throughput_kbps"], b["mean_throughput_kbps"]),
            "packet_loss_reduction_percent": reduction_pct(a["mean_packet_loss"], b["mean_packet_loss"]),
            "energy_j_delta": assistant_energy_j - baseline_energy_j,
            "energy_reduction_percent": reduction_pct(assistant_energy_j, baseline_energy_j),
            "baseline_energy_j": baseline_energy_j,
            "assistant_energy_j": assistant_energy_j,
            "baseline_average_power_w": baseline_power_w,
            "assistant_average_power_w": assistant_power_w,
            "energy_per_mbit_reduction_percent": reduction_pct(
                float(a_energy.get("energy_per_mbit", 0.0) or 0.0),
                float(b_energy.get("energy_per_mbit", 0.0) or 0.0),
            ) if b_energy.get("energy_per_mbit") and a_energy.get("energy_per_mbit") else None,
            "service_sla_percentage_points": {
                key: 100.0 * (a["service_sla_score"].get(key, 0.0) - b["service_sla_score"].get(key, 0.0))
                for key in sorted(set(b["service_sla_score"]) | set(a["service_sla_score"]))
            },
        },
        "caveat": "aggregate matched-run comparison; not a per-decision paired confidence interval",
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["valid_inputs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
