#!/usr/bin/env python3
"""Evaluate one causal rApp-only versus ARMD+TA-SAM pilot pair."""

from __future__ import annotations

import argparse
import html
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from energy_calibration import integrate_energy_events, load_calibration  # noqa: E402


REQUIRED_DUS = {"du_camera_edge", "du_sensor_mixed", "du_vehicle_edge"}
REAL_PDCP = "pdcp_real"


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    except sqlite3.Error:
        return []


def _mean(rows: list[sqlite3.Row], column: str) -> float | None:
    values = [_number(row[column]) for row in rows if column in row.keys() and row[column] is not None]
    return statistics.fmean(values) if values else None


def _count_value(rows: list[sqlite3.Row], column: str, value: Any) -> int:
    return sum(1 for row in rows if column in row.keys() and row[column] == value)


def _group_name(row: sqlite3.Row) -> str:
    raw = str(row["device_type"] or row["domain"] or "unknown").strip().lower() if "device_type" in row.keys() else "unknown"
    if "camera" in raw:
        return "camera"
    if "vehicle" in raw or "car" in raw:
        return "vehicle"
    if "sensor" in raw or "iot" in raw:
        return "sensor"
    return raw or "unknown"


def energy_metric(conn: sqlite3.Connection, calibration_path: Path | None = None) -> dict[str, Any]:
    rows = _rows(conn, "energy_commands")
    if not rows:
        return {"valid": False, "reason": "energy_commands_empty", "energy_j": 0.0}
    try:
        calibration = load_calibration(calibration_path)
        result = integrate_energy_events(rows, calibration)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return {"valid": False, "reason": f"energy_integration_failed:{exc}", "energy_j": 0.0}
    result.update({
        "source": "energy_commands",
        "kind": "calibrated_ru_mmwave_power_model",
        "model_energy_j": result.get("energy_j", 0.0),
        "native_sim_energy_j": None,
        "physical_wattmeter": bool(result.get("physical_wattmeter_available", False)),
        "physical_energy_valid": bool(result.get("absolute_scale_valid", False)),
        "energy_reference_source": result.get("energy_reference_source", "calibrated_model"),
        "proxy_notice": "energia estimada pela simulação; não representa consumo físico medido",
    })
    return result


def infra_metric(run_dir: Path) -> dict[str, Any]:
    payload = _json(run_dir / "infrastructure_metrics.json")
    totals = payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
    keys = ("cpu_usage_usec", "memory_byte_seconds", "memory_peak_bytes", "io_bytes", "artifact_bytes")
    values = {key: _number(totals.get(key), -1.0) for key in keys}
    valid = payload.get("schema") == "greenran.infrastructure.metrics.v1" and payload.get("enforced") is True and payload.get("complete") is True and all(value >= 0 for value in values.values())
    return {"valid": valid, "enforced": payload.get("enforced") is True, "complete": payload.get("complete") is True, "collection_mode": payload.get("collection_mode", "unknown"), **values, "errors": payload.get("errors", [])}


def decision_metric(conn: sqlite3.Connection, mode: str) -> dict[str, Any]:
    rows = _rows(conn, "decisions_history")
    columns = _columns(conn, "decisions_history")
    def count_flag(*names: str) -> int:
        name = next((candidate for candidate in names if candidate in columns), "")
        return sum(1 for row in rows if _number(row[name]) != 0) if name else 0
    source_counts = Counter(str(row["tasam_source"] or "") for row in rows) if "tasam_source" in columns else Counter()
    mode_counts = Counter(str(row["control_trial_mode"] or "") for row in rows) if "control_trial_mode" in columns else Counter()
    application_counts = Counter(
        str(row["economic_application_status"] or "")
        for row in rows
    ) if "economic_application_status" in columns else Counter()
    return {
        "decisions": len(rows),
        "armd_enabled": count_flag("armd_enabled"),
        "tasam_enabled": count_flag("tasam_enabled"),
        "tasam_proposals": count_flag("tasam_proposal_present"),
        "tasam_valid": count_flag("tasam_valid", "tasam_proposal_valid"),
        "tasam_checkpoint_valid": count_flag("tasam_checkpoint_valid"),
        "tasam_fallback_used": count_flag("tasam_fallback_used"),
        "armd_override_applied": count_flag("armd_override_applied", "safety_override"),
        "tasam_evidence_valid": count_flag("tasam_evidence_valid"),
        "tasam_actuation_applied": count_flag("tasam_actuation_applied", "ta_sam_actuation_applied"),
        "proposal_applied_exactly": count_flag("proposal_applied_exactly"),
        "invalid_decisions": count_flag("training_run_invalid"),
        "checkpoint_source_count": source_counts.get("checkpoint", 0),
        "tasam_source_counts": dict(source_counts),
        "control_trial_mode_counts": dict(mode_counts),
        "economic_application_status_counts": dict(application_counts),
        "economic_actions_applied": application_counts.get("applied", 0),
        "economic_action_contract_v2": _count_value(
            rows, "economic_action_contract", "applied_action_v2"
        ) if "economic_action_contract" in columns else 0,
        "mean_ran_completion": _mean(rows, "ran_completion_ratio"),
        "mean_ai_completion": _mean(rows, "ai_completion_ratio"),
        "mean_resource_budget": _mean(rows, "resource_budget"),
        "mean_usable_budget": _mean(rows, "usable_budget"),
        "mean_ran_allocation": _mean(rows, "ran_allocation"),
        "mean_ai_allocation": _mean(rows, "ai_allocation"),
        "mean_requested_power_percent": _mean(rows, "tasam_power_percent"),
        "mean_applied_power_percent": _mean(rows, "tasam_power_applied_percent"),
        "mean_live_power_w": _mean(rows, "live_power_w"),
        "mean_shadow_power_w": _mean(rows, "shadow_power_w"),
        "mean_energy_saving_fraction": _mean(rows, "energy_saving_fraction"),
        "mean_resource_saving_fraction": _mean(rows, "resource_saving_fraction"),
        "mean_utilization_ratio": _mean(rows, "utilization_ratio"),
        "mean_ran_demand": _mean(rows, "ran_demand"),
        "mean_ai_demand": _mean(rows, "ai_demand"),
        "mean_causal_score_delta": _mean(rows, "causal_score_delta"),
        "energy_model_versions": sorted({str(row["energy_model_version"] or "") for row in rows if "energy_model_version" in row.keys()}),
        "mode": mode,
    }


def economic_outcome_metric(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read only realized v2 outcomes persisted by the delayed Judge feedback."""
    rows = _rows(conn, "judge_outcome_history")
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["feedback_json"] or "{}"))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        feedback = payload.get("feedback") if isinstance(payload, dict) else {}
        if not isinstance(feedback, dict) or feedback.get("economic_action_contract") != "applied_action_v2":
            continue
        outcomes.append(feedback)
    def coherent(row: dict[str, Any]) -> bool:
        action = row.get("economic_action") or {}
        if row.get("economic_transition_eligible") is not True:
            return False
        if str(row.get("economic_application_status") or action.get("application_status") or "") != "applied":
            return False
        level = str(row.get("armd_safety_level") or action.get("armd_safety_level") or "").upper()
        if level and level not in {"CLEAR", "ADVISORY"}:
            return False
        if "tasam_operating_permission" in row and not bool(row.get("tasam_operating_permission")):
            return False
        if "tasam_operating_permission" in action and not bool(action.get("tasam_operating_permission")):
            return False
        if str(row.get("economic_execution_mode") or action.get("economic_execution_mode") or "") == "diagnostic":
            return False
        if "actuation_confirmed" in row and not bool(row.get("actuation_confirmed")):
            return False
        if "actuation_confirmed" in action and not bool(action.get("actuation_confirmed")):
            return False
        return True

    eligible = [row for row in outcomes if coherent(row)]
    alignment = [
        row for row in eligible
        if row.get("economic_action_alignment_valid") is True
    ]
    return {
        "outcomes": len(outcomes),
        "eligible": len(eligible),
        "eligible_rate": len(eligible) / max(len(outcomes), 1),
        "power_alignment_rate": len(alignment) / max(len(eligible), 1),
        "mean_realized_energy_saving_fraction": statistics.fmean(
            [_number(row.get("realized_energy_saving_fraction")) for row in eligible]
        ) if eligible else None,
        "mean_realized_allocation_saving_fraction": statistics.fmean(
            [_number(row.get("realized_allocation_saving_fraction")) for row in eligible]
        ) if eligible else None,
        "invalid_reasons": dict(Counter(
            str(row.get("economic_invalid_reason") or "") for row in outcomes
            if row.get("economic_transition_eligible") is not True
        )),
    }


def decision_series(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return a compact, JSON-safe series for the human-readable report."""
    rows = _rows(conn, "decisions_history")
    series: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        def value(*names: str) -> Any:
            for name in names:
                if name in row.keys() and row[name] is not None:
                    return row[name]
            return None

        series.append({
            "index": index + 1,
            "decision_id": value("decision_id", "id"),
            "sim_time_s": value("sim_time_s", "simulation_time_s", "timestamp_s"),
            "live_power_w": value("live_power_w"),
            # Shadow is counterfactual evidence only.  A treatment value is
            # valid here only when the applied command was persisted.
            "treatment_power_w": value("applied_power_w"),
            "live_total_allocation": value("live_total_allocation"),
            "treatment_total_allocation": value("applied_total_allocation", "total_allocation"),
            "ran_allocation": value("ran_allocation", "applied_ran_allocation"),
            "ai_allocation": value("ai_allocation", "applied_ai_allocation"),
        })
    return series


def network_metric(conn: sqlite3.Connection) -> dict[str, Any]:
    ue_rows = _rows(conn, "ue_metrics")
    extended = _rows(conn, "extended_metrics")
    real = [row for row in ue_rows if "pdcp_provenance" in row.keys() and str(row["pdcp_provenance"] or "") == REAL_PDCP and _number(row["latency_is_proxy"] if "latency_is_proxy" in row.keys() else 1, 1) == 0]
    groups: dict[str, dict[str, Any]] = {}
    for name in sorted({_group_name(row) for row in real}):
        subset = [row for row in real if _group_name(row) == name]
        groups[name] = {
            "rows": len(subset),
            "throughput_kbps": _mean(subset, "throughput_kbps"),
            "latency_p95_us": _mean(subset, "latency_p95_us"),
            "packet_loss_percent": _mean(subset, "packet_loss_percent"),
        }
    required_groups = {"camera", "sensor", "vehicle"}
    loss_coverage = {
        name: bool(
            groups.get(name, {}).get("rows", 0)
            and groups.get(name, {}).get("packet_loss_percent") is not None
            and groups.get(name, {}).get("latency_p95_us") is not None
            and groups.get(name, {}).get("throughput_kbps") is not None
        )
        for name in sorted(required_groups)
    }
    return {
        "ue_rows": len(ue_rows),
        "real_pdcp_rows": len(real),
        "proxy_rows": len(ue_rows) - len(real),
        "collector_modes": dict(Counter(str(row["collector_mode"] or "") for row in extended if "collector_mode" in row.keys())),
        "mean_throughput_kbps": _mean(real, "throughput_kbps"),
        "mean_latency_p95_us": _mean(real, "latency_p95_us"),
        "mean_packet_loss_percent": _mean(real, "packet_loss_percent"),
        "groups": groups,
        "required_group_loss_coverage": loss_coverage,
        "required_group_loss_valid": all(loss_coverage.values()),
        "extended_rows": len(extended),
        "mean_global_latency_us": _mean(extended, "global_avg_latency_us"),
        "mean_global_p95_us": _mean(extended, "latency_p95_us"),
    }


def _arm(run_dir: Path, mode: str, *, calibration_path: Path | None = None, metric_scope: str = "simulation") -> dict[str, Any]:
    manifest = _json(run_dir / "arm_manifest.json")
    db_path = run_dir / "rapp_data_lake.db"
    report: dict[str, Any] = {"run_dir": str(run_dir.resolve()), "manifest": manifest, "database_present": db_path.is_file()}
    if not db_path.is_file():
        report.update({"valid": False, "reason": "database_missing"})
        return report
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        du_rows = _rows(conn, "marl_du_state_history")
        du_ids = sorted({str(row["du_id"]) for row in du_rows if "du_id" in row.keys()})
        decisions = decision_metric(conn, mode)
        network = network_metric(conn)
        energy = energy_metric(conn, calibration_path)
        judges = len(_rows(conn, "judge_outcome_history"))
        economic_outcomes = economic_outcome_metric(conn)
        series = decision_series(conn)
    finally:
        conn.close()
    rollback_state = _json(run_dir / "control_trial_state.json")
    common = {
        "manifest_finished": manifest.get("status") == "finished",
        "database_present": True,
        "decisions_present": decisions["decisions"] > 0,
        "three_dus": REQUIRED_DUS.issubset(set(du_ids)),
        "real_pdcp_only": network["real_pdcp_rows"] > 0 and network["proxy_rows"] == 0 and network["real_pdcp_rows"] == network["ue_rows"],
        "collector_real": bool(network["collector_modes"]) and set(network["collector_modes"]) == {REAL_PDCP},
        "pdcp_loss_groups_complete": network["required_group_loss_valid"],
        "no_invalid_decisions": decisions["invalid_decisions"] == 0,
    }
    if metric_scope == "hardware":
        common["infrastructure_enforced"] = infra_metric(run_dir)["valid"]
    if mode == "rapp_only":
        contract = manifest.get("contract") or {}
        arm_checks = {
            "mode": manifest.get("mode") == "rapp_only",
            "armd_disabled": decisions["armd_enabled"] == 0,
            "tasam_disabled": decisions["tasam_enabled"] == 0,
            "contract_isolated": contract.get("armd_mode") == "off" and contract.get("actuation_enabled") is False,
        }
    else:
        arm_checks = {
            "mode": manifest.get("mode") == "combined",
            "armd_on": decisions["armd_enabled"] == decisions["decisions"] > 0,
            "checkpoint_on_all_decisions": decisions["checkpoint_source_count"] == decisions["decisions"] > 0,
            "tasam_valid_on_all_decisions": decisions["tasam_valid"] == decisions["decisions"] > 0,
            "tasam_checkpoint_valid_on_all_decisions": decisions["tasam_checkpoint_valid"] == decisions["decisions"] > 0,
            "no_tasam_fallback": decisions["tasam_fallback_used"] == 0,
            "tasam_evidence_valid_on_all_decisions": decisions["tasam_evidence_valid"] == decisions["decisions"] > 0,
            "causal_energy_model_present": len(decisions["energy_model_versions"]) == 1 and bool(decisions["energy_model_versions"][0]),
            "judge_outcomes_present": judges == decisions["decisions"] and judges > 0,
            "ta_sam_actuation_observed": decisions["tasam_actuation_applied"] > 0,
            "economic_action_contract_v2": decisions["economic_action_contract_v2"] == decisions["decisions"] > 0,
            "economic_actions_applied": decisions["economic_actions_applied"] > 0,
            "realized_economic_outcomes": economic_outcomes["eligible"] > 0,
            "projected_applied_power_alignment": economic_outcomes["power_alignment_rate"] >= 0.95,
            "no_control_rollback": rollback_state.get("rollback") is not True,
            "no_invalid_assistant_mode": not any(key in {"assistant_only_invalid", "rollback"} for key in decisions["control_trial_mode_counts"]),
        }
    report.update({
        "du_ids": du_ids,
        "decisions": decisions,
        "network": network,
        "energy": energy,
        "judge_outcomes": judges,
        "economic_outcomes": economic_outcomes,
        "decision_series": series,
        "rollback": rollback_state,
        "criteria": {**common, **arm_checks},
        "valid": all(common.values()) and all(arm_checks.values()),
    })
    if metric_scope == "hardware":
        report["infrastructure"] = infra_metric(run_dir)
    return report


def _delta(treatment: float | None, baseline: float | None) -> float | None:
    if treatment is None or baseline is None:
        return None
    return treatment - baseline


def _sla_guard(baseline: dict[str, Any], treatment: dict[str, Any]) -> dict[str, Any]:
    b = baseline["network"]
    t = treatment["network"]
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    for group in ("camera", "sensor", "vehicle"):
        bg = b["groups"].get(group, {})
        tg = t["groups"].get(group, {})
        p95_b, p95_t = bg.get("latency_p95_us"), tg.get("latency_p95_us")
        thr_b, thr_t = bg.get("throughput_kbps"), tg.get("throughput_kbps")
        loss_b, loss_t = bg.get("packet_loss_percent"), tg.get("packet_loss_percent")
        checks[f"{group}_latency_within_1pct"] = p95_b is not None and p95_t is not None and p95_t <= p95_b * 1.01
        checks[f"{group}_throughput_within_1pct"] = thr_b is not None and thr_t is not None and thr_t >= thr_b * 0.99
        checks[f"{group}_loss_within_1pp"] = loss_b is not None and loss_t is not None and loss_t <= loss_b + 1.0
        details[group] = {"latency_delta_us": _delta(p95_t, p95_b), "throughput_delta_kbps": _delta(thr_t, thr_b), "loss_delta_pp": _delta(loss_t, loss_b)}
    return {"valid": bool(checks) and all(checks.values()), "checks": checks, "details": details}


def build_report(
    baseline_dir: Path,
    treatment_dir: Path,
    *,
    seed: int = 47,
    expected_sim_time: float = 600.0,
    expected_wall_time: float = 900.0,
    wall_time: float | None = None,
    calibration_path: Path | None = None,
    metric_scope: str = "simulation",
) -> dict[str, Any]:
    if metric_scope not in {"simulation", "hardware"}:
        raise ValueError(f"metric_scope inválido: {metric_scope}")
    # Compatibility for archived callers. New campaigns always pass the two
    # separate durations above; a legacy single value still means both.
    if wall_time is not None:
        expected_sim_time = float(wall_time)
        expected_wall_time = float(wall_time)
    baseline = _arm(baseline_dir.resolve(), "rapp_only", calibration_path=calibration_path, metric_scope=metric_scope)
    treatment = _arm(treatment_dir.resolve(), "combined", calibration_path=calibration_path, metric_scope=metric_scope)
    bm, tm = baseline.get("manifest", {}), treatment.get("manifest", {})
    pairing = {
        "same_seed": bm.get("seed") == tm.get("seed") == seed,
        "same_profile": bool(bm.get("profile")) and bm.get("profile") == tm.get("profile"),
        "same_sim_time_config": (
            _number(bm.get("sim_time_s"), -1) == _number(tm.get("sim_time_s"), -1) == expected_sim_time
        ),
        "same_wall_time_config": (
            _number(bm.get("wall_time_s"), -1) == _number(tm.get("wall_time_s"), -1) == expected_wall_time
        ),
        "different_run_dirs": baseline_dir.resolve() != treatment_dir.resolve(),
    }
    be, te = baseline.get("energy", {}), treatment.get("energy", {})
    energy_j_b, energy_j_t = _number(be.get("energy_j"), 0.0), _number(te.get("energy_j"), 0.0)
    duration_b, duration_t = _number(be.get("duration_s"), 0.0), _number(te.get("duration_s"), 0.0)
    average_power_b, average_power_t = _number(be.get("average_power_w"), 0.0), _number(te.get("average_power_w"), 0.0)
    duration_comparable = duration_b > 0.0 and duration_t > 0.0 and abs(duration_b - duration_t) <= 1.0
    total_saving = (energy_j_b - energy_j_t) / energy_j_b if energy_j_b > 0 else None
    power_saving = (average_power_b - average_power_t) / average_power_b if average_power_b > 0 else None
    # Total joules only answer the causal question when integrations span the
    # same duration.  A target-bounded arm can stop at a different wall time,
    # in which case average modeled power is the primary normalized metric.
    saving = total_saving if duration_comparable else power_saving
    br, tr = baseline.get("decisions", {}), treatment.get("decisions", {})
    baseline_total_allocation = (
        _number(br.get("mean_ran_allocation"), 0.0)
        + _number(br.get("mean_ai_allocation"), 0.0)
    )
    treatment_total_allocation = (
        _number(tr.get("mean_ran_allocation"), 0.0)
        + _number(tr.get("mean_ai_allocation"), 0.0)
    )
    allocation_saving = (
        (baseline_total_allocation - treatment_total_allocation) / baseline_total_allocation
        if baseline_total_allocation > 0.0 else None
    )
    baseline_shortfall = max(
        0.0,
        _number(br.get("mean_ran_demand"), 0.0) + _number(br.get("mean_ai_demand"), 0.0) - baseline_total_allocation,
    )
    treatment_shortfall = max(
        0.0,
        _number(tr.get("mean_ran_demand"), 0.0) + _number(tr.get("mean_ai_demand"), 0.0) - treatment_total_allocation,
    )
    resources = {
        "ran_completion_delta": _delta(tr.get("mean_ran_completion"), br.get("mean_ran_completion")),
        "ai_completion_delta": _delta(tr.get("mean_ai_completion"), br.get("mean_ai_completion")),
        "ran_allocation_delta": _delta(tr.get("mean_ran_allocation"), br.get("mean_ran_allocation")),
        "ai_allocation_delta": _delta(tr.get("mean_ai_allocation"), br.get("mean_ai_allocation")),
        "budget_utilization_delta": _delta(tr.get("mean_utilization_ratio"), br.get("mean_utilization_ratio")),
        "baseline_total_allocation": baseline_total_allocation,
        "treatment_total_allocation": treatment_total_allocation,
        "allocation_saving_fraction": allocation_saving,
        "shadow_allocation_saving_fraction_treatment": tr.get("mean_resource_saving_fraction"),
        "shortfall_delta": treatment_shortfall - baseline_shortfall,
        "surplus_delta": (
            (treatment_total_allocation - _number(tr.get("mean_ran_demand"), 0.0) - _number(tr.get("mean_ai_demand"), 0.0))
            - (baseline_total_allocation - _number(br.get("mean_ran_demand"), 0.0) - _number(br.get("mean_ai_demand"), 0.0))
        ),
    }
    if metric_scope == "hardware":
        resources.update({
            "cpu_usage_usec_delta": _delta(treatment["infrastructure"].get("cpu_usage_usec"), baseline["infrastructure"].get("cpu_usage_usec")),
            "memory_byte_seconds_delta": _delta(treatment["infrastructure"].get("memory_byte_seconds"), baseline["infrastructure"].get("memory_byte_seconds")),
            "memory_peak_bytes_delta": _delta(treatment["infrastructure"].get("memory_peak_bytes"), baseline["infrastructure"].get("memory_peak_bytes")),
            "io_bytes_delta": _delta(treatment["infrastructure"].get("io_bytes"), baseline["infrastructure"].get("io_bytes")),
            "artifact_bytes_delta": _delta(treatment["infrastructure"].get("artifact_bytes"), baseline["infrastructure"].get("artifact_bytes")),
        })
    sla = _sla_guard(baseline, treatment)
    validation = {
        "pairing_valid": all(pairing.values()),
        "baseline_valid": baseline.get("valid", False),
        "treatment_valid": treatment.get("valid", False),
        "energy_valid": be.get("valid", False) and te.get("valid", False),
        "energy_model_same": bool(be.get("calibration_version")) and be.get("calibration_version") == te.get("calibration_version"),
        "sla_guard_valid": sla["valid"],
        "actual_applied_power_non_regression": average_power_t <= average_power_b,
    }
    measurement_valid = all(validation.values())
    metrics = {
        "energy_rapp_only_j": energy_j_b,
        "energy_tasam_armd_rapp_j": energy_j_t,
        "energy_saving_fraction": saving,
        "energy_saving_percent": saving * 100.0 if saving is not None else None,
        "energy_per_decision_baseline_j": energy_j_b / max(1, br.get("decisions", 0)),
        "energy_per_decision_treatment_j": energy_j_t / max(1, tr.get("decisions", 0)),
        "energy_comparison_basis": "total_energy_j" if duration_comparable else "average_power_w",
        "integration_duration_s_baseline": duration_b,
        "integration_duration_s_treatment": duration_t,
        "total_energy_saving_fraction": total_saving,
        "average_power_saving_fraction": power_saving,
        "average_power_w_baseline": average_power_b,
        "average_power_w_treatment": average_power_t,
        "model_energy_j_baseline": _number(be.get("model_energy_j"), 0.0),
        "model_energy_j_treatment": _number(te.get("model_energy_j"), 0.0),
        "native_sim_energy_j_baseline": be.get("native_sim_energy_j"),
        "native_sim_energy_j_treatment": te.get("native_sim_energy_j"),
        "physical_energy_valid": bool(be.get("physical_energy_valid")) and bool(te.get("physical_energy_valid")),
        "energy_reference_sources": sorted(set((be.get("energy_reference_source", ""), te.get("energy_reference_source", ""))) - {""}),
        "causal_score_delta_mean_treatment": tr.get("mean_causal_score_delta"),
        "energy_saving_fraction_mean_treatment": tr.get("mean_energy_saving_fraction"),
        "resource_saving_fraction_mean_treatment": tr.get("mean_resource_saving_fraction"),
        "energy_model_versions": sorted(set((be.get("calibration_version", ""), te.get("calibration_version", ""))) - {""}),
        "resources": resources,
    }
    if metric_scope == "hardware":
        metrics["infrastructure"] = {
            "baseline": baseline.get("infrastructure"),
            "treatment": treatment.get("infrastructure"),
        }
    objective_met = (
        measurement_valid
        and saving is not None and saving > 0
        and allocation_saving is not None and allocation_saving >= 0
    )
    tasam_applied = int(tr.get("tasam_actuation_applied", 0) or 0)
    fallback_count = int(tr.get("tasam_fallback_used", 0) or 0)
    if not measurement_valid or tasam_applied <= 0:
        classification = "no_evidence"
    elif not sla["valid"]:
        classification = "sla_regression"
    elif saving is None or allocation_saving is None:
        classification = "inconclusive"
    elif saving > 0 and allocation_saving >= 0:
        classification = "improvement"
    elif saving > 0:
        classification = "tradeoff"
    else:
        classification = "inconclusive"

    def group_sla(report: dict[str, Any], group: str) -> float | None:
        value = (report.get("network", {}).get("groups", {}).get(group, {}) or {}).get("packet_loss_percent")
        return max(0.0, min(1.0, 1.0 - _number(value) / 100.0)) if value is not None else None

    comparison = {
        "baseline_energy_j": energy_j_b,
        "treatment_energy_j": energy_j_t,
        "energy_delta_j": energy_j_t - energy_j_b,
        "energy_saving_fraction": saving,
        "baseline_average_power_w": average_power_b,
        "treatment_average_power_w": average_power_t,
        "baseline_ran_allocation": _number(br.get("mean_ran_allocation")),
        "treatment_ran_allocation": _number(tr.get("mean_ran_allocation")),
        "baseline_ai_allocation": _number(br.get("mean_ai_allocation")),
        "treatment_ai_allocation": _number(tr.get("mean_ai_allocation")),
        "baseline_total_allocation": baseline_total_allocation,
        "treatment_total_allocation": treatment_total_allocation,
        "allocation_delta": treatment_total_allocation - baseline_total_allocation,
        "allocation_saving_fraction": allocation_saving,
        "baseline_budget_utilization": _number(br.get("mean_utilization_ratio")),
        "treatment_budget_utilization": _number(tr.get("mean_utilization_ratio")),
        "baseline_shortfall": baseline_shortfall,
        "treatment_shortfall": treatment_shortfall,
        "baseline_surplus": baseline_total_allocation - _number(br.get("mean_ran_demand")) - _number(br.get("mean_ai_demand")),
        "treatment_surplus": treatment_total_allocation - _number(tr.get("mean_ran_demand")) - _number(tr.get("mean_ai_demand")),
        "baseline_camera_sla": group_sla(baseline, "camera"),
        "treatment_camera_sla": group_sla(treatment, "camera"),
        "baseline_sensor_sla": group_sla(baseline, "sensor"),
        "treatment_sensor_sla": group_sla(treatment, "sensor"),
        "baseline_vehicle_sla": group_sla(baseline, "vehicle"),
        "treatment_vehicle_sla": group_sla(treatment, "vehicle"),
        "baseline_latency": baseline.get("network", {}).get("mean_latency_p95_us"),
        "treatment_latency": treatment.get("network", {}).get("mean_latency_p95_us"),
        "baseline_throughput": baseline.get("network", {}).get("mean_throughput_kbps"),
        "treatment_throughput": treatment.get("network", {}).get("mean_throughput_kbps"),
        "tasam_decisions_applied": tasam_applied,
        "tasam_decisions_rejected": int(tr.get("economic_application_status_counts", {}).get("rejected", 0) or 0),
        "armd_overrides": int(tr.get("armd_override_applied", 0) or 0),
        "rollbacks": int((treatment.get("rollback") or {}).get("rollback_count", 0) or 0),
        "fallbacks": fallback_count,
        "causal_score_delta": tr.get("mean_causal_score_delta"),
        "classification": classification,
        "energy_interpretation": "estimativa relativa da simulacao ns-3; nao representa consumo fisico medido",
    }
    return {
        "schema": "greenran.tasam.causal_comparison.v2",
        "metric_scope": metric_scope,
        "seed": seed,
        "expected_sim_time_s": expected_sim_time,
        "expected_wall_time_s": expected_wall_time,
        "arms": {"baseline": "rapp_only", "treatment": "combined"},
        "pairing": pairing,
        "baseline": baseline,
        "treatment": treatment,
        "metrics": metrics,
        "sla": sla,
        "validation": validation,
        "measurement_valid": measurement_valid,
        "objective_met": objective_met,
        "comparison": comparison,
        "classification": classification,
        "energy_interpretation": "energia estimada pelo modelo nativo/calibrado da simulacao; nao representa consumo fisico medido",
    }


def _html_number(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "sim" if value else "não"
    try:
        return f"{float(value):.4f}{suffix}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _html_percent(value: Any) -> str:
    if value is None:
        return "n/a"
    return _html_number(_number(value) * 100.0, "%")


def _bar(label: str, baseline: Any, treatment: Any, *, suffix: str = "") -> str:
    values = [_number(baseline), _number(treatment)]
    scale = max(max(values), 1e-9)
    rows = []
    for name, value, color in (("rApp-only", baseline, "#64748b"), ("ARMD + TA-SAM + rApp", treatment, "#16a34a")):
        width = max(0.0, min(100.0, _number(value) / scale * 100.0))
        rows.append(
            f'<div class="bar-row"><span>{html.escape(name)}</span>'
            f'<div class="bar-track"><div class="bar" style="width:{width:.2f}%;background:{color}"></div></div>'
            f'<strong>{_html_number(value, suffix)}</strong></div>'
        )
    return f'<section><h3>{html.escape(label)}</h3>{"".join(rows)}</section>'


def _svg_series(
    baseline: list[dict[str, Any]],
    treatment: list[dict[str, Any]],
    baseline_key: str,
    treatment_key: str,
    title: str,
) -> str:
    width, height, pad = 760, 220, 28
    values = [
        _number(row.get(key), float("nan"))
        for rows, key in ((baseline, baseline_key), (treatment, treatment_key))
        for row in rows
    ]
    values = [value for value in values if math.isfinite(value)]
    if not values:
        return f"<section><h3>{html.escape(title)}</h3><p>Sem série temporal disponível.</p></section>"
    low, high = min(values), max(values)
    if abs(high - low) < 1e-12:
        high = low + 1.0

    def points(rows: list[dict[str, Any]], key: str) -> str:
        usable = [row for row in rows if row.get(key) is not None]
        if not usable:
            return ""
        count = max(len(usable) - 1, 1)
        coords = []
        for index, row in enumerate(usable):
            x = pad + (width - 2 * pad) * index / count
            y = height - pad - (height - 2 * pad) * (_number(row.get(key)) - low) / (high - low)
            coords.append(f"{x:.1f},{y:.1f}")
        return " ".join(coords)

    return (
        f'<section><h3>{html.escape(title)}</h3>'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">'
        f'<line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#cbd5e1"/>'
        f'<polyline points="{points(baseline, baseline_key)}" fill="none" stroke="#64748b" stroke-width="2"/>'
        f'<polyline points="{points(treatment, treatment_key)}" fill="none" stroke="#16a34a" stroke-width="2"/>'
        f'</svg><p class="legend"><span class="gray">■</span> rApp-only &nbsp; <span class="green">■</span> ARMD + TA-SAM + rApp</p></section>'
    )


def render_html_report(report: dict[str, Any], output: Path) -> None:
    """Render a dependency-free report suitable for opening locally."""
    comparison = report.get("comparison", {})
    baseline_series = report.get("baseline", {}).get("decision_series", [])
    treatment_series = report.get("treatment", {}).get("decision_series", [])
    classification = html.escape(str(report.get("classification", "inconclusive")))
    valid = "válida" if report.get("measurement_valid") else "não válida"
    cards = [
        ("Classificação", classification),
        ("Medição", valid),
        ("Economia de energia", _html_percent(comparison.get("energy_saving_fraction"))),
        ("Economia de alocação", _html_percent(comparison.get("allocation_saving_fraction"))),
        ("TA-SAM aplicado", str(comparison.get("tasam_decisions_applied", 0))),
    ]
    card_html = "".join(f'<div class="card"><small>{html.escape(label)}</small><b>{value}</b></div>' for label, value in cards)
    table_rows = []
    for label, key in (
        ("Potência média (W)", "average_power_w"),
        ("Alocação RAN", "ran_allocation"),
        ("Alocação AI", "ai_allocation"),
        ("Alocação total", "total_allocation"),
        ("Utilização do orçamento", "budget_utilization"),
    ):
        if key == "average_power_w":
            b, t = comparison.get("baseline_average_power_w"), comparison.get("treatment_average_power_w")
        elif key == "ran_allocation":
            b, t = comparison.get("baseline_ran_allocation"), comparison.get("treatment_ran_allocation")
        elif key == "ai_allocation":
            b, t = comparison.get("baseline_ai_allocation"), comparison.get("treatment_ai_allocation")
        elif key == "total_allocation":
            b, t = comparison.get("baseline_total_allocation"), comparison.get("treatment_total_allocation")
        else:
            b, t = comparison.get("baseline_budget_utilization"), comparison.get("treatment_budget_utilization")
        table_rows.append(f"<tr><td>{html.escape(label)}</td><td>{_html_number(b)}</td><td>{_html_number(t)}</td></tr>")
    html_text = f'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>Comparação econômica TA-SAM</title>
<style>body{{font:15px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172033}}h1{{margin-bottom:.25rem}}.notice{{background:#fff7ed;border-left:4px solid #f97316;padding:.8rem}}.cards{{display:flex;gap:.8rem;flex-wrap:wrap;margin:1rem 0}}.card{{border:1px solid #dbe2ea;border-radius:8px;padding:.8rem 1rem;min-width:150px}}.card small{{display:block;color:#64748b}}.card b{{display:block;font-size:1.25rem;margin-top:.25rem}}section{{margin:1.5rem 0}}.bar-row{{display:grid;grid-template-columns:180px 1fr 100px;gap:.6rem;align-items:center;margin:.5rem 0}}.bar-track{{height:16px;background:#e2e8f0;border-radius:99px;overflow:hidden}}.bar{{height:100%}}svg{{width:100%;height:220px;border:1px solid #e2e8f0;background:#f8fafc}}.gray{{color:#64748b}}.green{{color:#16a34a}}table{{border-collapse:collapse;width:100%}}th,td{{padding:.55rem;border-bottom:1px solid #e2e8f0;text-align:right}}th:first-child,td:first-child{{text-align:left}}.legend{{color:#475569}}</style></head>
<body><h1>Comparação rApp-only × ARMD + TA-SAM + rApp</h1>
<p class="notice">Energia: estimativa relativa calibrada da simulação ns-3; não representa consumo físico medido. CPU, memória e I/O não participam desta conclusão.</p>
<div class="cards">{card_html}</div>
{_bar("Potência média", comparison.get("baseline_average_power_w"), comparison.get("treatment_average_power_w"), suffix=" W")}
{_bar("Alocação total", comparison.get("baseline_total_allocation"), comparison.get("treatment_total_allocation"))}
{_svg_series(baseline_series, treatment_series, "live_power_w", "treatment_power_w", "Potência ao longo das decisões")}
{_svg_series(baseline_series, treatment_series, "live_total_allocation", "treatment_total_allocation", "Alocação ao longo das decisões")}
<section><h2>Resumo por braço</h2><table><thead><tr><th>Métrica</th><th>rApp-only</th><th>ARMD + TA-SAM + rApp</th></tr></thead><tbody>{"".join(table_rows)}</tbody></table></section>
<section><h2>SLA</h2><pre>{html.escape(json.dumps(report.get("sla", dict()), indent=2, ensure_ascii=False))}</pre></section>
</body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(html_text, encoding="utf-8")
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--treatment", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--expected-sim-time", type=float, default=600.0)
    parser.add_argument("--expected-wall-time", type=float, default=900.0)
    parser.add_argument("--wall-time", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--html-output", type=Path, default=None)
    parser.add_argument("--energy-calibration", type=Path)
    parser.add_argument("--metric-scope", choices=("simulation", "hardware"), default="simulation")
    args = parser.parse_args()
    if args.wall_time is not None:
        args.expected_sim_time = args.wall_time
        args.expected_wall_time = args.wall_time
    report = build_report(
        args.baseline, args.treatment, seed=args.seed,
        expected_sim_time=args.expected_sim_time,
        expected_wall_time=args.expected_wall_time,
        calibration_path=args.energy_calibration, metric_scope=args.metric_scope,
    )
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    html_output = (args.html_output or args.output.with_suffix(".html")).resolve()
    render_html_report(report, html_output)
    print(json.dumps({"output": str(args.output.resolve()), "html_output": str(html_output), "measurement_valid": report["measurement_valid"], "objective_met": report["objective_met"]}, ensure_ascii=False))
    return 0 if report["measurement_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
