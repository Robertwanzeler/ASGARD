#!/usr/bin/env python3
"""Evaluate rApp alone versus rApp + ARMD + TA-SAM as network assistants."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.energy_calibration import integrate_energy_events, load_calibration


SERVICE_WEIGHTS = {"camera": 0.35, "vehicle": 0.35, "sensor": 0.20, "background": 0.10}
COMPOSITE_WEIGHTS = {
    "sla": 0.65,
    "p95": 0.10,
    "cvar": 0.10,
    "throughput": 0.05,
    "packet_loss": 0.05,
    "energy_efficiency_proxy": 0.05,
}
# Keep the paired energy movement visible, but prevent a large calibrated
# energy difference from overwhelming the network-quality components.
ENERGY_RELATIVE_DELTA_CAP = 0.10
RESOURCE_DELTA_KEYS = (
    "resource_saving_pct",
    "ran_allocation_saving_pct",
    "ai_allocation_saving_pct",
    "scheduler_symbols_saving_pct",
    "scheduler_allocations_saving_pct",
    "scheduler_symbols_per_mbit_saving_pct",
    "scheduler_allocations_per_mbit_saving_pct",
    "resource_headroom_delta",
    "delivered_bandwidth_pct",
)


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _latency_quality_score(reference_us: float, observed_us: float) -> float:
    """Return a bounded, monotonic latency score without an early flat ceiling.

    The previous ``reference / observed`` formulation was clamped to 1.0.  In
    the measured GreenRAN range (well below the reference), that erased all
    P95/CVaR differences from the composite index even when raw latency
    improved.  The inverse formulation remains in (0, 1), preserves ordering,
    and keeps the metric sensitive below the reference target.
    """
    reference = max(float(reference_us), 1.0)
    observed = max(float(observed_us), 0.0)
    return reference / (reference + observed)


def _loss_percent(row: sqlite3.Row) -> float:
    if row["packet_loss_percent"] is not None:
        return max(0.0, float(row["packet_loss_percent"]))
    tx = float(row["tx_bytes"] or 0.0)
    rx = float(row["rx_bytes"] or 0.0)
    return 100.0 * max(0.0, 1.0 - (rx / tx if tx > 0 else 0.0))


def _ci95(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0, "mean": 0.0, "low": 0.0, "high": 0.0, "std": 0.0}
    avg = _mean(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    margin = 1.96 * std / math.sqrt(len(values))
    return {"n": len(values), "mean": avg, "low": avg - margin, "high": avg + margin, "std": std}


def _valid_pdcp_metrics(metrics: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return [
        row for row in metrics
        if str(row["collector_mode"] or "") == "pdcp_real"
        and float(row["proxy_latency_sample_count"] or 0.0) == 0.0
        and int(row["pdcp_stale"] or 0) == 0
    ]


def _metric_alignment_tokens(metrics: list[sqlite3.Row]) -> tuple[list[str], str]:
    """Identify the same simulated collector window across paired runs.

    A collector can publish several real snapshots during one rApp decision.
    Pairing by row number therefore compares different scenario stages when
    the two runs have slightly different cadence.  Use simulated time plus
    occurrence within that window when available; old fixture databases fall
    back to deterministic row order.
    """
    counters: dict[str, int] = {}
    tokens: list[str] = []
    has_sim_time = bool(metrics) and "sim_time_s" in metrics[0].keys()
    scheme = "sim_time_occurrence" if has_sim_time else "row_order"
    for index, row in enumerate(metrics):
        if has_sim_time:
            try:
                base = f"sim:{float(row['sim_time_s'] or 0.0):.3f}"
            except (TypeError, ValueError):
                base = "sim:unknown"
        else:
            base = "row"
        occurrence = counters.get(base, 0)
        counters[base] = occurrence + 1
        tokens.append(f"{base}#{occurrence}")
    return tokens, scheme


def _service_scores(ue_rows: list[sqlite3.Row], metric_rows: list[sqlite3.Row]) -> dict[str, float]:
    grouped: dict[str, list[sqlite3.Row]] = {key: [] for key in ("camera", "vehicle", "sensor")}
    for row in ue_rows:
        kind = str(row["device_type"] or "").strip().lower()
        if kind in grouped:
            grouped[kind].append(row)

    camera = [float(row["throughput_kbps"] or 0.0) >= 25_000.0 and float(row["latency_us"] or 0.0) <= 100_000.0 for row in grouped["camera"]]
    vehicle = [float(row["latency_us"] or 0.0) <= 20_000.0 and _loss_percent(row) <= 1.0 for row in grouped["vehicle"]]
    # In real-only PDCP collection, a configured background/sensor UE can be
    # present in the topology without having an observed packet in a given
    # window.  Zero-byte rows are not SLA failures and must not be converted
    # into 100% loss by _loss_percent().  Coverage is audited separately by
    # the run report; this score measures SLA only for observed sensor UEs.
    active_sensor_rows = [
        row for row in grouped["sensor"]
        if float(row["latency_us"] or 0.0) > 0.0
        or float(row["throughput_kbps"] or 0.0) > 0.0
        or float(row["tx_bytes"] or 0.0) > 0.0
        or float(row["rx_bytes"] or 0.0) > 0.0
    ]
    sensor = [
        float(row["latency_us"] or 0.0) <= 500_000.0 and _loss_percent(row) <= 5.0
        for row in active_sensor_rows
    ]
    # The fixed topology stores background UEs in the sensor device class, so
    # background is measured separately from real global PDCP quality.
    background = [float(row["latency_p95_per_ue_us"] or 0.0) <= 2_000_000.0 and float(row["global_packet_loss_rate"] or 0.0) <= 0.10 for row in metric_rows]

    def fraction(values: list[bool]) -> float:
        return sum(values) / len(values) if values else 0.0

    return {
        "camera": fraction(camera),
        "vehicle": fraction(vehicle),
        "sensor": fraction(sensor),
        "background": fraction(background),
    }


def _row_timestamp_ns(row: sqlite3.Row) -> int:
    value = row["timestamp"] if "timestamp" in row.keys() else 0
    return max(0, int(float(value or 0.0) * 1_000_000_000))


def _calibrated_energy(
    energy_rows: list[sqlite3.Row],
    decisions: list[sqlite3.Row],
    metrics: list[sqlite3.Row],
    calibration_path: Path | None = None,
) -> dict[str, Any]:
    if not energy_rows:
        return {
            "valid": False,
            "kind": "calibrated_ru_mmwave_power_model",
            "physical_meter_available": False,
            "reason": "no_energy_state_events",
            "energy_j": 0.0,
            "average_power_w": 0.0,
            "duration_s": 0.0,
            "event_count": 0,
        }
    calibration = load_calibration(calibration_path)
    timestamps = [_row_timestamp_ns(row) for row in decisions + metrics]
    end_timestamp_ns = max(timestamps) if timestamps else None
    result = integrate_energy_events(energy_rows, calibration, end_timestamp_ns=end_timestamp_ns)
    rx_bytes = [
        float(row["total_rx_bytes"] or 0.0)
        for row in metrics
        if "total_rx_bytes" in row.keys() and row["total_rx_bytes"] is not None
    ]
    result["energy_per_mbit"] = (
        result["energy_j"] / (max(rx_bytes) * 8.0 / 1_000_000.0)
        if rx_bytes and max(rx_bytes) > 0.0
        else None
    )
    result["kind"] = "calibrated_ru_mmwave_power_model"
    result["physical_meter_available"] = False
    return result


def _mean_column(rows: list[sqlite3.Row], column: str) -> float:
    values = [float(row[column] or 0.0) for row in rows if column in row.keys()]
    return _mean(values)


def _mean_alias(rows: list[sqlite3.Row], *columns: str) -> float:
    available = set(rows[0].keys()) if rows else set()
    for column in columns:
        if column in available:
            return _mean_column(rows, column)
    return 0.0


def _scheduler_resource_stats(run_dir: Path) -> dict[str, Any]:
    """Summarize real scheduler resource units from the ns-3 trace.

    The GreenRAN trace exposes allocation events and OFDM symbols, but not an
    explicit PRB/RB range.  Keep the unit name explicit so these numbers are
    not presented as physical PRBs when the trace cannot support that claim.
    """
    trace_path = run_dir.resolve() / "ns3_traces" / "EnbSchedAllocTraces.txt"
    empty = {
        "valid": False,
        "trace_available": False,
        "source_file": str(trace_path),
        "unit": "scheduler_symbol_time",
        "row_count": 0,
        "data_allocation_events": 0,
        "data_symbols": 0,
        "dl_allocation_events": 0,
        "ul_allocation_events": 0,
        "retx_allocation_events": 0,
        "physical_prb_count_available": False,
    }
    if not trace_path.is_file():
        return empty

    stats = dict(empty)
    stats["trace_available"] = True
    try:
        with trace_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip() or line.lower().startswith("frame"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 10:
                    continue
                try:
                    rnti = int(parts[3])
                    num_symbols = int(parts[5])
                    tdd_mode = int(parts[7])
                    retx_num = int(parts[8])
                except (TypeError, ValueError):
                    continue
                stats["row_count"] += 1
                if rnti <= 0:
                    continue
                stats["data_allocation_events"] += 1
                stats["data_symbols"] += max(0, num_symbols)
                if tdd_mode == 1:
                    stats["dl_allocation_events"] += 1
                elif tdd_mode == 2:
                    stats["ul_allocation_events"] += 1
                if retx_num > 0:
                    stats["retx_allocation_events"] += 1
    except OSError:
        return empty
    stats["valid"] = stats["row_count"] > 0 and stats["data_allocation_events"] > 0
    return stats


def _resource_metrics(
    run_dir: Path,
    allocation_history: list[sqlite3.Row],
    decisions: list[sqlite3.Row],
    metrics: list[sqlite3.Row],
    throughput_mean_kbps: float,
) -> dict[str, Any]:
    """Return resource accounting separate from the energy model."""
    allocation_rows: list[sqlite3.Row] = list(allocation_history)
    allocation_source = "resource_allocation_history" if allocation_rows else "none"
    if not allocation_rows:
        allocation_rows = decisions
        allocation_source = "decisions_history"

    columns = set(allocation_rows[0].keys()) if allocation_rows else set()
    allocation_field_groups = (
        ("resource_budget",),
        ("usable_budget",),
        ("ran_demand", "d_ran"),
        ("ai_demand", "d_ai"),
        ("ran_allocation", "r_ran"),
        ("ai_allocation", "r_ai"),
        ("ran_completion_ratio",),
        ("ai_completion_ratio",),
        ("utilization_ratio",),
    )
    allocation_fields_available = bool(allocation_rows) and all(
        any(column in columns for column in group)
        for group in allocation_field_groups
    )
    budget = _mean_alias(allocation_rows, "resource_budget")
    usable_budget = _mean_alias(allocation_rows, "usable_budget")
    ran_demand = _mean_alias(allocation_rows, "ran_demand", "d_ran")
    ai_demand = _mean_alias(allocation_rows, "ai_demand", "d_ai")
    ran_allocation = _mean_alias(allocation_rows, "ran_allocation", "r_ran")
    ai_allocation = _mean_alias(allocation_rows, "ai_allocation", "r_ai")
    utilization = _mean_alias(allocation_rows, "utilization_ratio")
    scheduler = _scheduler_resource_stats(run_dir)
    total_rx_bytes = max(
        [
            float(row["total_rx_bytes"] or 0.0)
            for row in metrics
            if "total_rx_bytes" in row.keys() and row["total_rx_bytes"] is not None
        ]
        or [0.0]
    )
    delivered_mbit = total_rx_bytes * 8.0 / 1_000_000.0
    scheduler["delivered_mbit"] = delivered_mbit
    scheduler["symbols_per_mbit"] = (
        scheduler["data_symbols"] / delivered_mbit if delivered_mbit > 0.0 else None
    )
    scheduler["allocations_per_mbit"] = (
        scheduler["data_allocation_events"] / delivered_mbit if delivered_mbit > 0.0 else None
    )
    return {
        "valid": bool(allocation_fields_available and allocation_rows and scheduler["valid"]),
        "allocation_source": allocation_source,
        "allocation_rows": len(allocation_rows),
        "allocation_fields_available": allocation_fields_available,
        "resource_budget": budget,
        "usable_budget": usable_budget,
        "ran_demand": ran_demand,
        "ai_demand": ai_demand,
        "total_demand": ran_demand + ai_demand,
        "ran_allocation": ran_allocation,
        "ai_allocation": ai_allocation,
        "total_allocation": ran_allocation + ai_allocation,
        "ran_completion_ratio": _mean_alias(allocation_rows, "ran_completion_ratio"),
        "ai_completion_ratio": _mean_alias(allocation_rows, "ai_completion_ratio"),
        "utilization_ratio": utilization,
        "resource_headroom": max(0.0, 1.0 - utilization),
        "delivered_bandwidth_mbps": throughput_mean_kbps / 1000.0,
        "scheduler": scheduler,
        "direct_bandwidth_allocation_available": False,
        "resource_unit_note": (
            "allocation do orçamento do rApp e unidades de tempo-símbolo do scheduler; "
            "o trace atual não informa contagem física de PRB/RB"
        ),
    }


def read_network_run(
    run_dir: Path,
    calibration_path: Path | None = None,
    metric_limit: int | None = None,
    metric_tokens: set[str] | None = None,
) -> dict[str, Any]:
    db_path = run_dir.resolve() / "rapp_data_lake.db"
    if not db_path.is_file():
        raise ValueError(f"Data Lake ausente: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    allocation_history: list[sqlite3.Row] = []
    try:
        decisions = conn.execute("SELECT * FROM decisions_history ORDER BY id").fetchall()
        metrics = conn.execute("SELECT * FROM extended_metrics ORDER BY id").fetchall()
        ue_rows = conn.execute("SELECT * FROM ue_metrics ORDER BY id").fetchall()
        try:
            allocation_history = conn.execute(
                "SELECT * FROM resource_allocation_history ORDER BY id"
            ).fetchall()
        except sqlite3.OperationalError:
            allocation_history = []
        try:
            energy_rows = conn.execute("SELECT * FROM energy_commands ORDER BY timestamp, id").fetchall()
        except sqlite3.OperationalError:
            energy_rows = []
    finally:
        conn.close()
    all_real_metrics = _valid_pdcp_metrics(metrics)
    all_metric_tokens, alignment_scheme = _metric_alignment_tokens(all_real_metrics)
    if metric_tokens is None:
        raw_real_metrics = all_real_metrics
    else:
        raw_real_metrics = [
            row for row, token in zip(all_real_metrics, all_metric_tokens)
            if token in metric_tokens
        ]
    proxy_rows = [row for row in metrics if float(row["proxy_latency_sample_count"] or 0.0) > 0.0]
    if not decisions:
        raise ValueError(f"Nenhuma decisão em {db_path}")

    decision_columns = set(decisions[0].keys())
    assistant_modes = [
        str(row["control_trial_mode"] or "") if "control_trial_mode" in decision_columns else ""
        for row in decisions
    ]
    strict_columns_present = {
        "armd_enabled",
        "ta_sam_actuation_applied",
        "control_trial_mode",
        "effective_policy_algorithm",
        "effective_policy_source",
    }.issubset(decision_columns)
    # In the judge architecture TA-SAM is not required to win every decision.
    # The strict condition is that both assistants submitted valid proposals,
    # the rApp selected exactly one of them, and the selected package was the
    # one applied.  Keep the legacy check for old databases that do not have
    # the proposal/judge columns yet.
    judge_columns_present = {
        "armd_proposal_present",
        "armd_proposal_valid",
        "tasam_proposal_present",
        "tasam_proposal_valid",
        "selected_assistant",
        "proposal_applied_exactly",
        "rapp_judge_mode",
    }.issubset(decision_columns)
    if judge_columns_present:
        strict_assistant_control = all(
            int(row["armd_enabled"] or 0) == 1
            and int(row["armd_proposal_present"] or 0) == 1
            and int(row["armd_proposal_valid"] or 0) == 1
            and int(row["tasam_proposal_present"] or 0) == 1
            and int(row["tasam_proposal_valid"] or 0) == 1
            and str(row["control_trial_mode"] or "") in {"assistant_judge", "assistant_only_control"}
            and str(row["rapp_judge_mode"] or "") == "rapp_judge_v1"
            and str(row["selected_assistant"] or "") in {"armd", "ta_sam", "joint"}
            and int(row["proposal_applied_exactly"] or 0) == 1
            and str(row["effective_policy_source"] or "") not in {
                "fallback_after_tasam_error",
                "heuristic_baseline",
                "live_allocator",
            }
            for row in decisions
        )
    else:
        strict_assistant_control = strict_columns_present and all(
            int(row["armd_enabled"] or 0) == 1
            and int(row["ta_sam_actuation_applied"] or 0) == 1
            and str(row["control_trial_mode"] or "") == "assistant_only_control"
            and str(row["effective_policy_algorithm"] or "") == "TA-SAM-MARL"
            and str(row["effective_policy_source"] or "") not in {"fallback_after_tasam_error", "heuristic_baseline"}
            for row in decisions
        )

    # A PDCP snapshot is a collector window, not a one-to-one rApp decision.
    # Evaluate the common prefix of real snapshots and decisions, preserving
    # the raw count for audit. This avoids pairing a synthetic/proxy row or a
    # later post-stop snapshot with an earlier decision.
    aligned_metric_count = min(len(raw_real_metrics), len(decisions))
    if metric_limit is not None and int(metric_limit) > 0:
        aligned_metric_count = min(aligned_metric_count, int(metric_limit))
    real_metrics = raw_real_metrics[:aligned_metric_count]
    service = _service_scores(ue_rows, real_metrics)
    sla_score = sum(SERVICE_WEIGHTS[key] * service[key] for key in SERVICE_WEIGHTS)
    p95_mean_us = _mean([float(row["latency_p95_per_ue_us"] or 0.0) for row in real_metrics])
    cvar_mean_us = _mean([float(row["cvar_per_ue_us"] or 0.0) for row in real_metrics])
    throughput_mean_kbps = _mean([float(row["throughput_kbps"] or 0.0) for row in real_metrics])
    loss_mean = _mean([float(row["global_packet_loss_rate"] or 0.0) for row in real_metrics])
    p95_score = _latency_quality_score(80_000.0, p95_mean_us)
    cvar_score = _latency_quality_score(120_000.0, cvar_mean_us)
    camera_throughput = [float(row["throughput_kbps"] or 0.0) for row in ue_rows if str(row["device_type"] or "") == "camera"]
    throughput_score = _clamp(_mean(camera_throughput) / 25_000.0)
    packet_loss_score = _clamp(1.0 - loss_mean / 0.01)
    allocations = [float(row["utilization_ratio"] or 0.0) for row in decisions]
    ran_completion = [float(row["ran_completion_ratio"] or 0.0) for row in decisions]
    ai_completion = [float(row["ai_completion_ratio"] or 0.0) for row in decisions]
    calibrated_energy = _calibrated_energy(energy_rows, decisions, metrics, calibration_path)
    # Keep the old allocation signal for backwards-compatible diagnostics;
    # paired comparison below uses calibrated joules when available.
    energy_efficiency_raw_values = [
        (0.40 * r + 0.38 * a) / max(u, 1e-6)
        for r, a, u in zip(ran_completion, ai_completion, allocations)
    ]
    energy_efficiency_raw = _mean(energy_efficiency_raw_values)
    energy_score = _mean([_clamp(value) for value in energy_efficiency_raw_values])
    energy_cost_proxy = _mean(allocations)
    resource_metrics = _resource_metrics(
        run_dir,
        allocation_history,
        decisions,
        real_metrics,
        throughput_mean_kbps,
    )
    components = {
        "sla": sla_score,
        "p95": p95_score,
        "cvar": cvar_score,
        "throughput": throughput_score,
        "packet_loss": packet_loss_score,
        "energy_efficiency_proxy": energy_score,
    }
    composite = sum(COMPOSITE_WEIGHTS[key] * components[key] for key in COMPOSITE_WEIGHTS)
    applied = sum(int(row["ta_sam_actuation_applied"] or 0) for row in decisions)
    return {
        "run_dir": str(run_dir.resolve()),
        "decisions": len(decisions),
        # Keep the full collector count visible even when the paired
        # evaluator selects only common simulated windows below.
        "real_pdcp_metric_rows": len(all_real_metrics),
        "aligned_real_pdcp_metric_rows": len(real_metrics),
        "evaluation_window": (
            "common_sim_time_occurrence_windows"
            if metric_tokens is not None and alignment_scheme == "sim_time_occurrence"
            else "common_prefix(real_pdcp_snapshots, decisions)"
        ),
        "proxy_metric_rows": len(proxy_rows),
        # This flag describes the raw collector output. The evaluated
        # statistics may intentionally use a shorter common prefix.
        "metrics_aligned_to_decisions": len(all_real_metrics) == len(decisions),
        "evaluation_metrics_aligned": len(real_metrics) == len(decisions),
        "valid_real_only": bool(real_metrics) and not proxy_rows,
        "service_sla_score": service,
        "service_weights": SERVICE_WEIGHTS,
        "sla_score": sla_score,
        "components": components,
        "composite_index": composite,
        "mean_p95_us": p95_mean_us,
        "mean_cvar_us": cvar_mean_us,
        "mean_throughput_kbps": throughput_mean_kbps,
        "mean_packet_loss": loss_mean,
        "tasam_applied_decisions": applied,
        "tasam_applied_rate": applied / max(len(decisions), 1),
        "strict_assistant_control": strict_assistant_control,
        "assistant_failure_count": sum(1 for mode in assistant_modes if mode in {"assistant_only_invalid", "live_fallback"}),
        "armd_enabled_decisions": sum(int(row["armd_enabled"] or 0) for row in decisions),
        "armd_override_decisions": sum(int(row["armd_override_applied"] or 0) for row in decisions),
        "critical_fallbacks": sum(int(row["ta_sam_actuation_applied"] or 0) == 0 and "critical" in str(row["control_trial_reason"] or "").lower() for row in decisions),
        "effective_algorithms": {
            algorithm: sum(1 for row in decisions if str(row["effective_policy_algorithm"] or "") == algorithm)
            for algorithm in sorted({str(row["effective_policy_algorithm"] or "") for row in decisions})
        },
        "energy_cost_proxy": energy_cost_proxy,
        "energy_efficiency_raw": energy_efficiency_raw,
        "energy_metric": {
            **calibrated_energy,
            "score": energy_score,
            "raw_efficiency": energy_efficiency_raw,
            "allocation_cost_proxy": energy_cost_proxy,
        },
        "resource_metrics": resource_metrics,
        "_alignment_tokens": all_metric_tokens,
        "_alignment_scheme": alignment_scheme,
    }


def pair_result(
    baseline_dir: Path,
    assistant_dir: Path,
    seed: int,
    repetition: int,
    scenario_profile: str = "",
    allow_metric_gap: bool = False,
    calibration_path: Path | None = None,
    target_decisions: int = 65,
) -> dict[str, Any]:
    baseline = read_network_run(baseline_dir, calibration_path)
    assistant = read_network_run(assistant_dir, calibration_path)
    baseline_tokens = list(baseline.pop("_alignment_tokens", []))
    assistant_tokens = set(assistant.pop("_alignment_tokens", []))
    baseline_scheme = baseline.pop("_alignment_scheme", "row_order")
    assistant.pop("_alignment_scheme", None)
    common_tokens = [token for token in baseline_tokens if token in assistant_tokens]
    if common_tokens:
        # Recompute both sides over exactly the same real-PDCP simulated
        # windows and occurrence number. This prevents a post-stop snapshot
        # or a cadence difference from being compared to another stage.
        selected = set(common_tokens)
        baseline = read_network_run(baseline_dir, calibration_path, metric_tokens=selected)
        assistant = read_network_run(assistant_dir, calibration_path, metric_tokens=selected)
        baseline.pop("_alignment_tokens", None)
        assistant.pop("_alignment_tokens", None)
        baseline.pop("_alignment_scheme", None)
        assistant.pop("_alignment_scheme", None)
    deltas = {
        "composite_index": assistant["composite_index"] - baseline["composite_index"],
        "sla_score": assistant["sla_score"] - baseline["sla_score"],
        "p95_pct": 100.0 * (assistant["mean_p95_us"] - baseline["mean_p95_us"]) / max(abs(baseline["mean_p95_us"]), 1e-12),
        "cvar_pct": 100.0 * (assistant["mean_cvar_us"] - baseline["mean_cvar_us"]) / max(abs(baseline["mean_cvar_us"]), 1e-12),
        "throughput_pct": 100.0 * (assistant["mean_throughput_kbps"] - baseline["mean_throughput_kbps"]) / max(abs(baseline["mean_throughput_kbps"]), 1e-12),
        "packet_loss_delta": assistant["mean_packet_loss"] - baseline["mean_packet_loss"],
        "service_sla": {key: assistant["service_sla_score"][key] - baseline["service_sla_score"][key] for key in SERVICE_WEIGHTS},
    }
    component_deltas = {
        key: assistant["components"][key] - baseline["components"][key]
        for key in COMPOSITE_WEIGHTS
    }
    weighted_component_contributions = {
        key: COMPOSITE_WEIGHTS[key] * component_deltas[key]
        for key in COMPOSITE_WEIGHTS
    }
    baseline_energy_metric = baseline["energy_metric"]
    assistant_energy_metric = assistant["energy_metric"]
    baseline_duration = float(baseline_energy_metric.get("duration_s", 0.0) or 0.0)
    assistant_duration = float(assistant_energy_metric.get("duration_s", 0.0) or 0.0)
    duration_ratio = (
        abs(assistant_duration - baseline_duration)
        / max(abs(baseline_duration), 1e-12)
        if baseline_duration > 0.0 else 1.0
    )
    duration_aligned = duration_ratio <= 0.05
    if duration_aligned:
        energy_basis = "calibrated_energy_j"
        baseline_energy_cost = float(baseline_energy_metric["energy_j"])
        assistant_energy_cost = float(assistant_energy_metric["energy_j"])
    else:
        # Same decision count does not imply the same wall-time exposure.
        # Total joules would reward a shorter run even if its instantaneous
        # power were worse, so use calibrated average power in that case.
        energy_basis = "calibrated_average_power_w_duration_mismatch"
        baseline_energy_cost = float(baseline_energy_metric.get("average_power_w", 0.0) or 0.0)
        assistant_energy_cost = float(assistant_energy_metric.get("average_power_w", 0.0) or 0.0)
    relative_energy_saving = (
        (baseline_energy_cost - assistant_energy_cost) / max(abs(baseline_energy_cost), 1e-12)
    )
    balanced_energy_delta = max(
        -ENERGY_RELATIVE_DELTA_CAP,
        min(ENERGY_RELATIVE_DELTA_CAP, relative_energy_saving),
    )
    # Energy is still part of the composite, but its bounded contribution is
    # based on the paired relative cost instead of two independently
    # saturated scores that always made the baseline equal to 1.0.
    component_deltas["energy_efficiency_proxy"] = balanced_energy_delta
    weighted_component_contributions["energy_efficiency_proxy"] = (
        COMPOSITE_WEIGHTS["energy_efficiency_proxy"] * balanced_energy_delta
    )
    balanced_composite_delta = sum(weighted_component_contributions.values())
    deltas["composite_index"] = balanced_composite_delta
    deltas["network_only_composite_index"] = sum(
        weighted_component_contributions[key]
        for key in COMPOSITE_WEIGHTS
        if key != "energy_efficiency_proxy"
    )
    deltas["energy_relative_saving"] = relative_energy_saving
    deltas["energy_balanced_delta"] = balanced_energy_delta
    deltas["energy_comparison_basis"] = energy_basis
    deltas["energy_duration_aligned"] = duration_aligned
    deltas["baseline_duration_s"] = baseline_duration
    deltas["assistant_duration_s"] = assistant_duration
    baseline_resource = baseline["resource_metrics"]
    assistant_resource = assistant["resource_metrics"]

    def saving_pct(baseline_value: float | None, assistant_value: float | None) -> float | None:
        if baseline_value is None or assistant_value is None or abs(float(baseline_value)) <= 1e-12:
            return None
        return 100.0 * (float(baseline_value) - float(assistant_value)) / abs(float(baseline_value))

    baseline_scheduler = baseline_resource.get("scheduler", {})
    assistant_scheduler = assistant_resource.get("scheduler", {})
    resource_delta = {
        # Positive means ASGARD used less of the rApp resource budget.
        "resource_saving_pct": saving_pct(
            baseline_resource.get("utilization_ratio"),
            assistant_resource.get("utilization_ratio"),
        ),
        "ran_allocation_saving_pct": saving_pct(
            baseline_resource.get("ran_allocation"),
            assistant_resource.get("ran_allocation"),
        ),
        "ai_allocation_saving_pct": saving_pct(
            baseline_resource.get("ai_allocation"),
            assistant_resource.get("ai_allocation"),
        ),
        "scheduler_symbols_saving_pct": saving_pct(
            baseline_scheduler.get("data_symbols"),
            assistant_scheduler.get("data_symbols"),
        ),
        "scheduler_allocations_saving_pct": saving_pct(
            baseline_scheduler.get("data_allocation_events"),
            assistant_scheduler.get("data_allocation_events"),
        ),
        "scheduler_symbols_per_mbit_saving_pct": saving_pct(
            baseline_scheduler.get("symbols_per_mbit"),
            assistant_scheduler.get("symbols_per_mbit"),
        ),
        "scheduler_allocations_per_mbit_saving_pct": saving_pct(
            baseline_scheduler.get("allocations_per_mbit"),
            assistant_scheduler.get("allocations_per_mbit"),
        ),
        "resource_headroom_delta": (
            assistant_resource.get("resource_headroom", 0.0)
            - baseline_resource.get("resource_headroom", 0.0)
        ),
        "delivered_bandwidth_pct": (
            100.0 * (
                assistant_resource.get("delivered_bandwidth_mbps", 0.0)
                - baseline_resource.get("delivered_bandwidth_mbps", 0.0)
            ) / max(abs(baseline_resource.get("delivered_bandwidth_mbps", 0.0)), 1e-12)
        ),
        "direct_bandwidth_allocation_available": bool(
            baseline_resource.get("direct_bandwidth_allocation_available")
            and assistant_resource.get("direct_bandwidth_allocation_available")
        ),
        "resource_unit": baseline_scheduler.get("unit", "unknown"),
    }
    resource_valid = bool(
        baseline_resource.get("valid")
        and assistant_resource.get("valid")
        and resource_delta["resource_saving_pct"] is not None
        and resource_delta["scheduler_symbols_per_mbit_saving_pct"] is not None
    )
    resource_delta["valid"] = resource_valid
    deltas["resource"] = resource_delta
    return {
        "seed": int(seed),
        "repetition": int(repetition),
        "scenario_profile": str(scenario_profile or ""),
        "baseline": baseline,
        "assistant": assistant,
        "metrics_aligned": {
            "baseline": baseline["metrics_aligned_to_decisions"],
            "assistant": assistant["metrics_aligned_to_decisions"],
            "same_real_pdcp_rows": baseline["real_pdcp_metric_rows"] == assistant["real_pdcp_metric_rows"],
            "alignment_scheme": baseline_scheme,
            "comparison_aligned_with_one_snapshot_gap": (
                abs(baseline["real_pdcp_metric_rows"] - baseline["decisions"]) <= 1
                and abs(assistant["real_pdcp_metric_rows"] - assistant["decisions"]) <= 1
                and abs(baseline["real_pdcp_metric_rows"] - assistant["real_pdcp_metric_rows"]) <= 1
            ),
            "common_aligned_real_window": (
                baseline["aligned_real_pdcp_metric_rows"]
                == assistant["aligned_real_pdcp_metric_rows"]
                and baseline["aligned_real_pdcp_metric_rows"] >= 50
            ),
            "metric_gap_allowed": bool(allow_metric_gap),
        },
        "deltas": deltas,
        "component_deltas": component_deltas,
        "weighted_component_contributions": weighted_component_contributions,
        "valid": (
            baseline["valid_real_only"]
            and assistant["valid_real_only"]
            and baseline_energy_metric.get("valid", False)
            and assistant_energy_metric.get("valid", False)
            and assistant["strict_assistant_control"]
            and baseline["decisions"] == assistant["decisions"] == int(target_decisions)
            and (
                (baseline["metrics_aligned_to_decisions"] and assistant["metrics_aligned_to_decisions"])
                if not allow_metric_gap
                else (
                    baseline["aligned_real_pdcp_metric_rows"]
                    == assistant["aligned_real_pdcp_metric_rows"]
                    and baseline["aligned_real_pdcp_metric_rows"] >= 50
                )
            )
            and (
                baseline["real_pdcp_metric_rows"] == assistant["real_pdcp_metric_rows"]
                or (
                    allow_metric_gap
                    and baseline["aligned_real_pdcp_metric_rows"] == assistant["aligned_real_pdcp_metric_rows"]
                    and baseline["aligned_real_pdcp_metric_rows"] >= 50
                )
            )
        ),
        "resource_valid": resource_valid,
    }


def aggregate(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [pair for pair in pairs if pair.get("valid")]
    keys = ("composite_index", "sla_score", "p95_pct", "cvar_pct", "throughput_pct", "packet_loss_delta")
    delta_ci = {key: _ci95([float(pair["deltas"][key]) for pair in valid]) for key in keys}
    network_only_ci = _ci95([
        float(pair["deltas"].get("network_only_composite_index", 0.0))
        for pair in valid
    ])
    service_ci = {key: _ci95([float(pair["deltas"]["service_sla"][key]) for pair in valid]) for key in SERVICE_WEIGHTS}
    component_ci = {
        key: _ci95([float(pair.get("component_deltas", {}).get(key, 0.0)) for pair in valid])
        for key in COMPOSITE_WEIGHTS
    }
    contribution_ci = {
        key: _ci95([float(pair.get("weighted_component_contributions", {}).get(key, 0.0)) for pair in valid])
        for key in COMPOSITE_WEIGHTS
    }
    resource_valid = [
        pair for pair in valid
        if pair.get("deltas", {}).get("resource", {}).get("valid", False)
    ]
    resource_ci = {
        key: _ci95([
            float(pair["deltas"]["resource"].get(key, 0.0) or 0.0)
            for pair in resource_valid
            if pair["deltas"]["resource"].get(key) is not None
        ])
        for key in RESOURCE_DELTA_KEYS
    }
    resource_criteria = {
        "all_resource_pairs_valid": len(resource_valid) == len(valid) and len(valid) > 0,
        "scheduler_resource_observed": len(resource_valid) == len(valid) and len(valid) > 0,
        "resource_saving_positive": resource_ci["resource_saving_pct"]["low"] > 0.0,
        "no_material_bandwidth_delivery_loss": resource_ci["delivered_bandwidth_pct"]["mean"] >= -5.0,
    }
    criteria = {
        "all_pairs_valid_real_only": len(valid) == len(pairs) and len(pairs) > 0,
        "assistant_only_enforced": len(valid) == len(pairs) and len(pairs) > 0 and all(
            pair["assistant"].get("strict_assistant_control", False) for pair in valid
        ),
        "global_index_positive": delta_ci["composite_index"]["low"] > 0.0,
        "no_material_service_sla_degradation": all(service_ci[key]["low"] >= -0.01 for key in SERVICE_WEIGHTS),
        "p95_within_5pct": delta_ci["p95_pct"]["mean"] <= 5.0,
        "cvar_within_5pct": delta_ci["cvar_pct"]["mean"] <= 5.0,
        "packet_loss_not_increased": delta_ci["packet_loss_delta"]["mean"] <= 0.0001,
        "tasam_actuation_observed": any(
            pair["assistant"]["tasam_applied_decisions"] == pair["assistant"]["decisions"] > 0
            for pair in valid
        ),
        "energy_calibration_valid": len(valid) == len(pairs) and len(pairs) > 0 and all(
            pair["baseline"].get("energy_metric", {}).get("valid", False)
            and pair["assistant"].get("energy_metric", {}).get("valid", False)
            for pair in valid
        ),
        "network_only_index_positive": network_only_ci["low"] > 0.0,
    }
    criteria_without_energy = {
        key: value for key, value in criteria.items()
        if key not in {"energy_calibration_valid", "global_index_positive"}
    }
    criteria_without_energy["network_only_index_positive"] = network_only_ci["low"] > 0.0
    return {
        "valid_pair_count": len(valid),
        "total_pair_count": len(pairs),
        "delta_ci95": delta_ci,
        "network_only_composite_delta_ci95": network_only_ci,
        "service_sla_delta_ci95": service_ci,
        "component_delta_ci95": component_ci,
        "weighted_component_contribution_ci95": contribution_ci,
        "resource_delta_ci95": resource_ci,
        "resource_criteria": resource_criteria,
        "resource_approved": all(resource_criteria.values()),
        "criteria": criteria,
        "approved": all(criteria.values()),
        "approved_without_energy": all(criteria_without_energy.values()),
        "energy_metric_note": (
            "energia estimada por modelo calibrado RU/mmWave em watts e joules; "
            "não é leitura direta de wattímetro. A comparação relativa por par "
            f"é limitada a ±{ENERGY_RELATIVE_DELTA_CAP:.0%} antes do peso de 5%"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", nargs=4, metavar=("BASELINE", "ASSISTANT", "SEED", "REPETITION"), action="append")
    parser.add_argument("--campaign-root", type=Path)
    parser.add_argument("--profile", default="", help="scenario profile attached to every --pair in this report")
    parser.add_argument(
        "--energy-calibration",
        type=Path,
        help="arquivo JSON de calibração RU/mmWave; por padrão config/energy_calibration.json",
    )
    parser.add_argument(
        "--allow-metric-gap",
        action="store_true",
        help="Use the same common prefix of real PDCP snapshots when collector cadence is not one-to-one with decisions; requires at least 50 aligned rows and no proxy rows.",
    )
    parser.add_argument(
        "--target-decisions",
        type=int,
        default=65,
        help="Quantidade esperada de decisoes em cada lado do par.",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    pairs: list[dict[str, Any]] = []
    if args.campaign_root:
        pair_paths = set(args.campaign_root.rglob("network_pair.json"))
        for path in sorted(pair_paths):
            saved = json.loads(path.read_text(encoding="utf-8"))
            # A per-pair file is itself a small report with one item in
            # ``pairs``; flatten it for campaign aggregation.
            pairs.extend(saved.get("pairs", []) if isinstance(saved, dict) else [])
    for item in args.pair or []:
        pairs.append(
            pair_result(
                Path(item[0]),
                Path(item[1]),
                int(item[2]),
                int(item[3]),
                args.profile,
                args.allow_metric_gap,
                args.energy_calibration,
                args.target_decisions,
            )
        )
    if not pairs:
        raise SystemExit("informe pelo menos --pair ou --campaign-root")
    payload = {
        "schema": "greenran.tasam_network_campaign.v1",
        "service_weights": SERVICE_WEIGHTS,
        "composite_weights": COMPOSITE_WEIGHTS,
        "energy_policy": {
            "weight": COMPOSITE_WEIGHTS["energy_efficiency_proxy"],
            "comparison": "paired_relative_calibrated_ru_mmwave_joules",
            "relative_delta_cap": ENERGY_RELATIVE_DELTA_CAP,
            "hardware_power_meter_available": False,
            "calibration_path": str(args.energy_calibration.resolve()) if args.energy_calibration else None,
        },
        "pairs": pairs,
        "aggregate": aggregate(pairs),
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    csv_output = output.with_name(f"{output.stem}_resources.csv")
    with csv_output.open("w", newline="", encoding="utf-8") as handle:
        fields = ["seed", "repetition", "valid", "resource_valid", *RESOURCE_DELTA_KEYS]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for pair in pairs:
            resource = pair.get("deltas", {}).get("resource", {})
            writer.writerow({
                "seed": pair.get("seed"),
                "repetition": pair.get("repetition"),
                "valid": pair.get("valid", False),
                "resource_valid": resource.get("valid", False),
                **{key: resource.get(key) for key in RESOURCE_DELTA_KEYS},
            })
    print(json.dumps({"output": str(output), "aggregate": payload["aggregate"]}, indent=2, ensure_ascii=False))
    print(json.dumps({"resource_csv": str(csv_output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
