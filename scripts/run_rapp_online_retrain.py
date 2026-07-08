#!/usr/bin/env python3
"""Run incremental rApp ML retraining from the trainable online trace."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = ROOT / "runs" / "tasam_article_ns3_collection"
DEFAULT_MODELS_DIR = ROOT / "models"
DEFAULT_EXPORT_DIRNAME = "tasam_article_export"
DEFAULT_TARGET_TRAINABLE = 1000
DEFAULT_MIN_NEW_ROWS = 100
DEFAULT_FEATURE_PROFILE = "no_stage_with_slice_state"
DEFAULT_REQUIRED_DECISION_TRANSITIONS = (
    "ALLOWED->CONDITIONAL",
    "CONDITIONAL->BLOCKED",
    "BLOCKED->CONDITIONAL",
    "BLOCKED->ALLOWED",
)
DEFAULT_REQUIRED_SCENARIO_FAMILIES = ("allowed", "camera", "vehicle", "app2")
QUALITY_RUNTIME_FEATURES = (
    "cvar_ms",
    "latency_p95_ms",
    "avg_latency_ms",
    "variance_ms2",
    "total_active_cameras",
    "camera_ratio",
    "total_critical_ues",
    "critical_ue_ratio",
    "cvar_zone",
    "throughput_mbps",
    "packet_loss_rate",
    "jitter_ms",
    "tx_rx_ratio",
)
QUALITY_SLICE_STATE_FEATURES = (
    "global_total_demand",
    "global_usable_budget",
    "slice_embb_qos_pressure",
    "slice_embb_completion_ratio",
    "slice_embb_min_qos_met",
    "slice_embb_budget_share",
    "slice_mmtc_qos_pressure",
    "slice_mmtc_completion_ratio",
    "slice_mmtc_min_qos_met",
    "slice_mmtc_budget_share",
    "slice_urllc_qos_pressure",
    "slice_urllc_completion_ratio",
    "slice_urllc_min_qos_met",
    "slice_urllc_budget_share",
)
PROMOTED_MODEL_FILES = (
    "best_classifier.joblib",
    "rf_classifier.joblib",
    "rf_scaler.joblib",
    "label_encoder.joblib",
    "rf_regressor.joblib",
    "reg_scaler.joblib",
    "training_report.json",
    "training_report.txt",
    "feature_importance.png",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Incremental ML retraining for the rApp from online trainable traces")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="Runtime state directory")
    parser.add_argument("--models-dir", default=str(DEFAULT_MODELS_DIR), help="Directory where runtime ML models are stored")
    parser.add_argument("--trainable-trace-jsonl", default=None, help="Explicit trainable trace JSONL")
    parser.add_argument("--trainable-summary-json", default=None, help="Explicit trainable summary JSON")
    parser.add_argument("--manifest-out", default=None, help="Explicit retrain manifest output path")
    parser.add_argument("--collection-quality-json", default=None, help="Explicit collection-quality report output path")
    parser.add_argument("--baseline-report-json", default=None, help="Optional explicit baseline training_report.json used for promotion comparison")
    parser.add_argument("--python", default=sys.executable, help="Python executable used for training")
    parser.add_argument(
        "--feature-profile",
        default=DEFAULT_FEATURE_PROFILE,
        help="Feature profile used for official online retraining/promotion",
    )
    parser.add_argument("--min-trainable", type=int, default=50, help="Minimum trainable rows required to retrain")
    parser.add_argument("--target-trainable", type=int, default=DEFAULT_TARGET_TRAINABLE, help="Campaign target for 100%% real trainable rows")
    parser.add_argument("--min-new-rows", type=int, default=DEFAULT_MIN_NEW_ROWS, help="Minimum number of new real trainable rows required since the last retrain attempt")
    parser.add_argument("--min-class-count", type=int, default=5, help="Minimum rows per class required to retrain")
    parser.add_argument("--required-classes", nargs="*", default=["ALLOWED", "CONDITIONAL", "BLOCKED"], help="Classes that must exist in the trainable subset")
    parser.add_argument("--min-rf-accuracy", type=float, default=0.95, help="Minimum Random Forest accuracy required for promotion")
    parser.add_argument("--min-r2", type=float, default=0.80, help="Minimum regressor R2 required for promotion")
    parser.add_argument("--min-healthy-allowed-recall", type=float, default=0.70, help="Minimum healthy ALLOWED recall required when available")
    parser.add_argument("--max-class-dominance-ratio", type=float, default=0.55, help="Maximum allowed ratio for the dominant decision class in the clean trainable set")
    parser.add_argument("--min-decision-transitions", type=int, default=30, help="Minimum decision-state transitions required in the clean trainable trace")
    parser.add_argument("--min-decision-transition-ratio", type=float, default=0.05, help="Minimum ratio of decision transitions across consecutive trainable rows")
    parser.add_argument("--min-distinct-decision-transitions", type=int, default=4, help="Minimum number of distinct decision transition types required")
    parser.add_argument("--required-decision-transitions", nargs="*", default=list(DEFAULT_REQUIRED_DECISION_TRANSITIONS), help="Decision transitions that must appear in the clean trainable trace")
    parser.add_argument("--required-scenario-families", nargs="*", default=list(DEFAULT_REQUIRED_SCENARIO_FAMILIES), help="Scenario families that must be represented in the clean trainable trace")
    parser.add_argument("--max-constant-feature-ratio", type=float, default=0.25, help="Maximum share of key runtime features that may stay constant across the clean trainable trace")
    parser.add_argument("--min-valid-training-ratio", type=float, default=1.0, help="Minimum ratio of rows explicitly marked valid_for_training in the clean trace")
    parser.add_argument("--min-accuracy-gain", type=float, default=0.01, help="Minimum absolute Random Forest accuracy gain vs the compatible baseline before promotion")
    parser.add_argument("--max-class-recall-drop", type=float, default=0.03, help="Maximum allowed recall drop for CONDITIONAL/BLOCKED vs the compatible baseline")
    parser.add_argument("--dry-run", action="store_true", help="Print training command without executing")
    return parser


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_training_command(python_bin: str, trace_path: Path, models_dir: Path, feature_profile: str) -> list[str]:
    return [
        python_bin,
        str(ROOT / "training" / "train_ml_model.py"),
        "--trace-jsonl",
        str(trace_path),
        "--feature-profile",
        str(feature_profile),
        "--output",
        str(models_dir),
    ]


def safe_float(value: Any, default: float = float("nan")) -> float:
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
        return float(values[0])
    q = max(0.0, min(1.0, q))
    ordered = sorted(float(value) for value in values)
    pos = q * (len(ordered) - 1)
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return ordered[lower]
    weight = pos - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def metric_stats(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values]
    if not clean:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "std": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "unique_count": 0,
            "unique_ratio": 0.0,
        }
    mean = sum(clean) / len(clean)
    variance = sum((value - mean) ** 2 for value in clean) / len(clean)
    unique_count = len({round(value, 6) for value in clean})
    return {
        "count": len(clean),
        "min": min(clean),
        "max": max(clean),
        "mean": mean,
        "std": math.sqrt(variance),
        "p50": percentile(clean, 0.50),
        "p95": percentile(clean, 0.95),
        "unique_count": unique_count,
        "unique_ratio": unique_count / len(clean),
    }


def scenario_family(stage: str) -> str:
    normalized = str(stage or "unknown").strip().lower()
    if normalized.startswith("allowed_"):
        return "allowed"
    if normalized.startswith("camera_"):
        return "camera"
    if normalized.startswith("vehicle_"):
        return "vehicle"
    if normalized.startswith("app2_"):
        return "app2"
    return "other"


def extract_runtime_features(metrics: dict[str, Any]) -> dict[str, float]:
    total_active_ues = max(1.0, safe_float(metrics.get("total_active_ues"), 0.0))
    total_active_cameras = safe_float(metrics.get("total_active_cameras"), 0.0)
    total_critical_ues = safe_float(metrics.get("total_critical_ues"), 0.0)
    total_tx_bytes = safe_float(metrics.get("total_tx_bytes"), 0.0)
    total_rx_bytes = max(1.0, safe_float(metrics.get("total_rx_bytes"), 0.0))
    cvar_ms = safe_float(metrics.get("cvar_per_ue_us"), 0.0) / 1000.0
    return {
        "cvar_ms": cvar_ms,
        "latency_p95_ms": safe_float(metrics.get("latency_p95_per_ue_us", metrics.get("latency_p95_us")), 0.0) / 1000.0,
        "avg_latency_ms": safe_float(metrics.get("global_avg_latency_us"), 0.0) / 1000.0,
        "variance_ms2": safe_float(metrics.get("variance_per_ue_us2"), 0.0) / 1_000_000.0,
        "total_active_cameras": total_active_cameras,
        "camera_ratio": total_active_cameras / total_active_ues,
        "total_critical_ues": total_critical_ues,
        "critical_ue_ratio": total_critical_ues / total_active_ues,
        "cvar_zone": 0.0 if cvar_ms <= 60.0 else 1.0 if cvar_ms <= 80.0 else 2.0,
        "throughput_mbps": safe_float(metrics.get("throughput_kbps"), 0.0) / 1000.0,
        "packet_loss_rate": safe_float(metrics.get("global_packet_loss_rate"), 0.0) * 100.0,
        "jitter_ms": safe_float(metrics.get("global_jitter_us"), 0.0) / 1000.0,
        "tx_rx_ratio": total_tx_bytes / total_rx_bytes,
    }


def extract_slice_state_features(row: dict[str, Any]) -> dict[str, float]:
    global_state = dict(row.get("global_state") or {})
    slice_state = dict(row.get("slice_state") or {})

    def slice_metric(slice_id: str, field: str) -> float:
        return safe_float(((slice_state.get(slice_id) or {}).get(field)))

    return {
        "global_total_demand": safe_float(global_state.get("total_demand")),
        "global_usable_budget": safe_float(global_state.get("usable_budget")),
        "slice_embb_qos_pressure": slice_metric("eMBB", "qos_pressure"),
        "slice_embb_completion_ratio": slice_metric("eMBB", "completion_ratio"),
        "slice_embb_min_qos_met": slice_metric("eMBB", "min_qos_met"),
        "slice_embb_budget_share": slice_metric("eMBB", "budget_share"),
        "slice_mmtc_qos_pressure": slice_metric("mMTC", "qos_pressure"),
        "slice_mmtc_completion_ratio": slice_metric("mMTC", "completion_ratio"),
        "slice_mmtc_min_qos_met": slice_metric("mMTC", "min_qos_met"),
        "slice_mmtc_budget_share": slice_metric("mMTC", "budget_share"),
        "slice_urllc_qos_pressure": slice_metric("URLLC", "qos_pressure"),
        "slice_urllc_completion_ratio": slice_metric("URLLC", "completion_ratio"),
        "slice_urllc_min_qos_met": slice_metric("URLLC", "min_qos_met"),
        "slice_urllc_budget_share": slice_metric("URLLC", "budget_share"),
    }


def quality_feature_names(feature_profile: str) -> tuple[str, ...]:
    profile = str(feature_profile or "").strip().lower()
    if profile == "no_stage_with_slice_state":
        return QUALITY_RUNTIME_FEATURES + QUALITY_SLICE_STATE_FEATURES
    return QUALITY_RUNTIME_FEATURES


def analyze_collection_quality(
    trace_path: Path,
    summary: dict[str, Any],
    *,
    feature_profile: str,
    generated_at: datetime,
) -> dict[str, Any]:
    selected_feature_names = quality_feature_names(feature_profile)
    report: dict[str, Any] = {
        "schema": "greenran.rapp_online_collection_quality.v1",
        "generated_at": generated_at.isoformat(),
        "feature_profile": str(feature_profile),
        "source_trace_jsonl": str(trace_path),
        "trace_exists": trace_path.exists(),
        "rows": 0,
        "collection_purity": {
            "collector_modes": {},
            "pdcp_real_rows": 0,
            "non_pdcp_real_rows": 0,
            "proxy_rows": 0,
            "valid_for_training_rows": 0,
            "valid_for_training_ratio": 0.0,
        },
        "class_balance": {
            "decision_counts": {},
            "dominant_class": "",
            "dominant_ratio": float("nan"),
        },
        "scenario_families": {
            "counts": {},
            "stages": dict(summary.get("stage_counts_after") or {}),
        },
        "decision_transitions": {
            "counts": {},
            "total": 0,
            "distinct": 0,
            "ratio": 0.0,
        },
        "stage_transitions": {
            "counts": {},
            "total": 0,
            "distinct": 0,
            "ratio": 0.0,
        },
        "feature_distributions": {},
        "feature_constancy": {
            "feature_count": len(selected_feature_names),
            "constant_features": [],
            "constant_feature_ratio": 0.0,
            "low_diversity_features": [],
        },
        "summary_snapshot": {
            "profile": summary.get("profile"),
            "rules": dict(summary.get("rules") or {}),
            "decision_counts_after": dict(summary.get("decision_counts_after") or {}),
            "stage_purity_after": dict(summary.get("stage_purity_after") or {}),
        },
    }
    if not trace_path.exists():
        report["collection_purity"]["error"] = "trace_jsonl_missing"
        return report

    decision_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    decision_transitions: Counter[str] = Counter()
    stage_transitions: Counter[str] = Counter()
    collector_modes: Counter[str] = Counter()
    feature_values: dict[str, list[float]] = {feature: [] for feature in selected_feature_names}
    pdcp_real_rows = 0
    non_pdcp_real_rows = 0
    proxy_rows = 0
    valid_rows = 0
    total_rows = 0
    previous_decision = ""
    previous_stage = ""

    with trace_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            total_rows += 1
            row = json.loads(line)
            decision = str((row.get("decision") or {}).get("decision") or "unknown")
            stage = str(row.get("scenario_stage") or "unknown")
            metrics = dict(row.get("metrics") or {})
            quality = dict(row.get("collection_quality") or {})
            decision_counts[decision] += 1
            family_counts[scenario_family(stage)] += 1
            if previous_decision and previous_decision != decision:
                decision_transitions[f"{previous_decision}->{decision}"] += 1
            if previous_stage and previous_stage != stage:
                stage_transitions[f"{previous_stage}->{stage}"] += 1
            previous_decision = decision
            previous_stage = stage

            collector_mode = str(quality.get("collector_mode") or "unknown")
            collector_modes[collector_mode] += 1
            if bool(quality.get("pdcp_real", False)):
                pdcp_real_rows += 1
            else:
                non_pdcp_real_rows += 1
            if safe_float(quality.get("proxy_latency_sample_count"), 0.0) > 0.0:
                proxy_rows += 1
            if bool(quality.get("valid_for_training", False)):
                valid_rows += 1

            features = extract_runtime_features(metrics)
            if str(feature_profile or "").strip().lower() == "no_stage_with_slice_state":
                features.update(extract_slice_state_features(row))
            for feature_name, feature_value in features.items():
                if feature_name in feature_values:
                    feature_values[feature_name].append(feature_value)

    dominant_class = ""
    dominant_ratio = float("nan")
    if decision_counts and total_rows > 0:
        dominant_class, dominant_count = decision_counts.most_common(1)[0]
        dominant_ratio = dominant_count / total_rows

    distributions = {feature: metric_stats(values) for feature, values in feature_values.items()}
    constant_features = sorted(
        feature for feature, stats in distributions.items() if int(stats.get("unique_count", 0) or 0) <= 1
    )
    low_diversity_features = sorted(
        feature
        for feature, stats in distributions.items()
        if int(stats.get("unique_count", 0) or 0) <= 3 or safe_float(stats.get("unique_ratio"), 0.0) < 0.01
    )

    report["rows"] = total_rows
    report["collection_purity"] = {
        "collector_modes": dict(collector_modes),
        "pdcp_real_rows": pdcp_real_rows,
        "non_pdcp_real_rows": non_pdcp_real_rows,
        "proxy_rows": proxy_rows,
        "valid_for_training_rows": valid_rows,
        "valid_for_training_ratio": 0.0 if total_rows == 0 else valid_rows / total_rows,
    }
    report["class_balance"] = {
        "decision_counts": dict(decision_counts),
        "dominant_class": dominant_class,
        "dominant_ratio": dominant_ratio,
    }
    report["scenario_families"] = {
        "counts": dict(family_counts),
        "stages": dict(summary.get("stage_counts_after") or {}),
    }
    report["decision_transitions"] = {
        "counts": dict(decision_transitions),
        "total": sum(decision_transitions.values()),
        "distinct": len(decision_transitions),
        "ratio": 0.0 if total_rows <= 1 else sum(decision_transitions.values()) / (total_rows - 1),
    }
    report["stage_transitions"] = {
        "counts": dict(stage_transitions),
        "total": sum(stage_transitions.values()),
        "distinct": len(stage_transitions),
        "ratio": 0.0 if total_rows <= 1 else sum(stage_transitions.values()) / (total_rows - 1),
    }
    report["feature_distributions"] = distributions
    report["feature_constancy"] = {
        "feature_count": len(selected_feature_names),
        "constant_features": constant_features,
        "constant_feature_ratio": 0.0 if not selected_feature_names else len(constant_features) / len(selected_feature_names),
        "low_diversity_features": low_diversity_features,
    }
    return report


def evaluate_collection_quality(
    report: dict[str, Any],
    *,
    max_class_dominance_ratio: float,
    min_decision_transitions: int,
    min_decision_transition_ratio: float,
    min_distinct_decision_transitions: int,
    required_decision_transitions: list[str],
    required_scenario_families: list[str],
    max_constant_feature_ratio: float,
    min_valid_training_ratio: float,
) -> tuple[str, list[str], dict[str, Any]]:
    reasons: list[str] = []
    purity = dict(report.get("collection_purity") or {})
    class_balance = dict(report.get("class_balance") or {})
    decision_transitions = dict(report.get("decision_transitions") or {})
    family_counts = dict((report.get("scenario_families") or {}).get("counts") or {})
    feature_constancy = dict(report.get("feature_constancy") or {})

    rows = int(report.get("rows", 0) or 0)
    dominant_ratio = safe_float(class_balance.get("dominant_ratio"))
    transition_total = int(decision_transitions.get("total", 0) or 0)
    transition_ratio = safe_float(decision_transitions.get("ratio"), 0.0)
    transition_counts = dict(decision_transitions.get("counts") or {})
    transition_distinct = int(decision_transitions.get("distinct", len(transition_counts)) or 0)
    constant_feature_ratio = safe_float(feature_constancy.get("constant_feature_ratio"), 0.0)
    valid_training_ratio = safe_float(purity.get("valid_for_training_ratio"), 0.0)
    missing_transitions = [transition for transition in required_decision_transitions if int(transition_counts.get(transition, 0) or 0) <= 0]
    missing_families = [family for family in required_scenario_families if int(family_counts.get(family, 0) or 0) <= 0]

    checks = {
        "rows": rows,
        "dominant_class": class_balance.get("dominant_class"),
        "dominant_ratio": dominant_ratio,
        "decision_transition_total": transition_total,
        "decision_transition_ratio": transition_ratio,
        "distinct_decision_transitions": transition_distinct,
        "missing_decision_transitions": missing_transitions,
        "missing_scenario_families": missing_families,
        "constant_feature_ratio": constant_feature_ratio,
        "constant_features": list(feature_constancy.get("constant_features") or []),
        "valid_for_training_ratio": valid_training_ratio,
        "proxy_rows": int(purity.get("proxy_rows", 0) or 0),
        "non_pdcp_real_rows": int(purity.get("non_pdcp_real_rows", 0) or 0),
    }

    if not bool(report.get("trace_exists")):
        reasons.append("trainable trace JSONL is missing")
    if rows <= 0:
        reasons.append("clean trainable trace is empty")
    if int(purity.get("proxy_rows", 0) or 0) > 0:
        reasons.append(f"proxy rows still present in clean trace: {int(purity.get('proxy_rows', 0) or 0)}")
    if int(purity.get("non_pdcp_real_rows", 0) or 0) > 0:
        reasons.append(f"non-pdcp-real rows still present in clean trace: {int(purity.get('non_pdcp_real_rows', 0) or 0)}")
    if not math.isnan(dominant_ratio) and dominant_ratio > max_class_dominance_ratio:
        reasons.append(f"dominant decision class above gate: {dominant_ratio} > {max_class_dominance_ratio}")
    if transition_total < min_decision_transitions:
        reasons.append(f"decision transitions below gate: {transition_total} < {min_decision_transitions}")
    if transition_ratio < min_decision_transition_ratio:
        reasons.append(f"decision transition ratio below gate: {transition_ratio} < {min_decision_transition_ratio}")
    if transition_distinct < min_distinct_decision_transitions:
        reasons.append(
            f"distinct decision transition types below gate: {transition_distinct} < {min_distinct_decision_transitions}"
        )
    if missing_transitions:
        reasons.append(f"required decision transitions missing: {', '.join(missing_transitions)}")
    if missing_families:
        reasons.append(f"required scenario families missing: {', '.join(missing_families)}")
    if constant_feature_ratio > max_constant_feature_ratio:
        reasons.append(f"constant feature ratio above gate: {constant_feature_ratio} > {max_constant_feature_ratio}")
    if valid_training_ratio < min_valid_training_ratio:
        reasons.append(f"valid_for_training ratio below gate: {valid_training_ratio} < {min_valid_training_ratio}")

    status = "ready" if not reasons else "blocked_quality"
    return status, reasons, checks


def evaluate_readiness(summary: dict[str, Any], min_trainable: int, min_class_count: int, required_classes: list[str]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    rows = int(summary.get("rows_after", 0) or 0)
    decision_counts = dict(summary.get("decision_counts_after") or {})

    if rows < min_trainable:
        reasons.append(f"trainable rows below minimum: {rows} < {min_trainable}")
    for cls in required_classes:
        if int(decision_counts.get(cls, 0) or 0) < min_class_count:
            reasons.append(
                f"class {cls} below minimum: {int(decision_counts.get(cls, 0) or 0)} < {min_class_count}"
            )
    status = "ready" if not reasons else "blocked"
    return status, reasons


def evaluate_training_report(
    report: dict[str, Any],
    *,
    min_rf_accuracy: float,
    min_r2: float,
    min_healthy_allowed_recall: float,
    expected_feature_profile: str,
    baseline_comparison: dict[str, Any] | None = None,
    min_accuracy_gain: float = 0.0,
    max_class_recall_drop: float = 1.0,
) -> tuple[str, list[str], dict[str, Any]]:
    reasons: list[str] = []
    evaluation = dict(report.get("evaluation") or {}) if isinstance(report, dict) else {}
    classifier = dict(report.get("classifier") or {}) if isinstance(report, dict) else {}
    regressor = dict(report.get("regressor") or {}) if isinstance(report, dict) else {}
    report_feature_profile = str(report.get("feature_profile") or "").strip()
    report_features = list(report.get("features") or []) if isinstance(report, dict) else []

    rf_accuracy = safe_float(classifier.get("random_forest_accuracy"))
    healthy_allowed_recall = safe_float(classifier.get("healthy_allowed_recall"))
    r2_value = safe_float(regressor.get("r2"))
    evaluation_valid = bool(evaluation.get("valid", True))
    baseline_comparison = dict(baseline_comparison or {})
    baseline_available = bool(baseline_comparison.get("available", False))
    accuracy_gain = safe_float(((baseline_comparison.get("delta") or {}).get("rf_accuracy")), float("nan"))
    healthy_allowed_delta = safe_float(
        ((baseline_comparison.get("delta") or {}).get("healthy_allowed_recall")),
        float("nan"),
    )
    recall_drop_by_class = dict(baseline_comparison.get("recall_drop_by_class") or {})
    accuracy_tie_epsilon = 1e-9

    checks = {
        "rf_accuracy": rf_accuracy,
        "healthy_allowed_recall": healthy_allowed_recall,
        "regressor_r2": r2_value,
        "evaluation_valid": evaluation_valid,
        "feature_profile": report_feature_profile,
        "baseline_comparison_available": baseline_available,
        "baseline_accuracy_gain": accuracy_gain,
        "baseline_status": baseline_comparison.get("status"),
    }

    if report_feature_profile != expected_feature_profile:
        reasons.append(
            f"feature_profile mismatch: {report_feature_profile or 'missing'} != {expected_feature_profile}"
        )
    elif expected_feature_profile in {"no_stage", "no_stage_with_slice_state"} and any(
        str(feature).startswith("stage_") for feature in report_features
    ):
        reasons.append(f"feature_profile {expected_feature_profile} contains stage_* features")

    if reasons:
        return "invalid_evaluation", reasons, checks

    if not evaluation_valid:
        reasons.extend(str(reason) for reason in (evaluation.get("reasons") or []))
        return "invalid_evaluation", reasons or ["training evaluation window is invalid"], checks

    if math.isnan(rf_accuracy) or rf_accuracy < min_rf_accuracy:
        reasons.append(f"rf_accuracy below gate: {rf_accuracy} < {min_rf_accuracy}")
    if math.isnan(r2_value) or r2_value < min_r2:
        reasons.append(f"regressor_r2 below gate: {r2_value} < {min_r2}")
    if not math.isnan(healthy_allowed_recall) and healthy_allowed_recall < min_healthy_allowed_recall:
        reasons.append(
            f"healthy_allowed_recall below gate: {healthy_allowed_recall} < {min_healthy_allowed_recall}"
        )
    if baseline_available:
        safe_accuracy_tie = (
            not math.isnan(accuracy_gain)
            and abs(accuracy_gain) <= accuracy_tie_epsilon
            and (math.isnan(healthy_allowed_delta) or healthy_allowed_delta >= -accuracy_tie_epsilon)
        )
        if math.isnan(accuracy_gain) or (
            accuracy_gain < min_accuracy_gain and not safe_accuracy_tie
        ):
            reasons.append(f"rf_accuracy gain below baseline gate: {accuracy_gain} < {min_accuracy_gain}")
        for cls in ("CONDITIONAL", "BLOCKED"):
            recall_drop = safe_float(recall_drop_by_class.get(cls), float("nan"))
            if not math.isnan(recall_drop) and recall_drop > max_class_recall_drop:
                reasons.append(
                    f"{cls} recall regressed beyond gate: {recall_drop} > {max_class_recall_drop}"
                )

    status = "promote" if not reasons else "reject_metrics"
    return status, reasons, checks


def extract_class_metric(report: dict[str, Any], cls: str, metric: str) -> float:
    classifier = dict(report.get("classifier") or {})
    class_report = dict(classifier.get("classification_report") or {})
    metrics = dict(class_report.get(cls) or {})
    return safe_float(metrics.get(metric))


def summarize_training_metrics(report: dict[str, Any]) -> dict[str, Any]:
    classifier = dict(report.get("classifier") or {})
    regressor = dict(report.get("regressor") or {})
    class_report = dict(classifier.get("classification_report") or {})
    classes = list(classifier.get("classes") or [])
    per_class = {
        cls: {
            "precision": safe_float((class_report.get(cls) or {}).get("precision")),
            "recall": safe_float((class_report.get(cls) or {}).get("recall")),
            "f1_score": safe_float((class_report.get(cls) or {}).get("f1-score")),
            "support": safe_float((class_report.get(cls) or {}).get("support")),
        }
        for cls in classes
    }
    return {
        "feature_profile": str(report.get("feature_profile") or ""),
        "rf_accuracy": safe_float(classifier.get("random_forest_accuracy")),
        "healthy_allowed_recall": safe_float(classifier.get("healthy_allowed_recall")),
        "healthy_accuracy": safe_float(classifier.get("healthy_accuracy")),
        "cv_mean": safe_float(classifier.get("cross_validation_mean")),
        "cv_std": safe_float(classifier.get("cross_validation_std")),
        "regressor_r2": safe_float(regressor.get("r2")),
        "macro_f1": safe_float((class_report.get("macro avg") or {}).get("f1-score")),
        "weighted_f1": safe_float((class_report.get("weighted avg") or {}).get("f1-score")),
        "per_class": per_class,
    }


def load_baseline_report(path: Path, expected_feature_profile: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = load_json(path)
    status = {
        "available": False,
        "status": "missing",
        "path": str(path),
        "reasons": [],
    }
    if not payload:
        status["reasons"] = ["baseline training_report.json missing or unreadable"]
        return {}, status
    feature_profile = str(payload.get("feature_profile") or "").strip()
    if feature_profile != expected_feature_profile:
        status["status"] = "incompatible"
        status["reasons"] = [
            f"baseline feature_profile mismatch: {feature_profile or 'missing'} != {expected_feature_profile}"
        ]
        return payload, status
    status["available"] = True
    status["status"] = "ready"
    return payload, status


def build_baseline_comparison(
    current_report: dict[str, Any],
    baseline_report: dict[str, Any],
    baseline_status: dict[str, Any],
) -> dict[str, Any]:
    comparison: dict[str, Any] = dict(baseline_status)
    comparison.setdefault("reasons", [])
    comparison["current"] = summarize_training_metrics(current_report)
    if not baseline_status.get("available", False):
        comparison["baseline"] = summarize_training_metrics(baseline_report) if baseline_report else {}
        comparison["delta"] = {}
        comparison["recall_drop_by_class"] = {}
        return comparison

    baseline_metrics = summarize_training_metrics(baseline_report)
    current_metrics = comparison["current"]
    delta = {
        "rf_accuracy": safe_float(current_metrics.get("rf_accuracy")) - safe_float(baseline_metrics.get("rf_accuracy")),
        "healthy_allowed_recall": safe_float(current_metrics.get("healthy_allowed_recall")) - safe_float(baseline_metrics.get("healthy_allowed_recall")),
        "regressor_r2": safe_float(current_metrics.get("regressor_r2")) - safe_float(baseline_metrics.get("regressor_r2")),
        "macro_f1": safe_float(current_metrics.get("macro_f1")) - safe_float(baseline_metrics.get("macro_f1")),
        "weighted_f1": safe_float(current_metrics.get("weighted_f1")) - safe_float(baseline_metrics.get("weighted_f1")),
    }
    recall_drop_by_class = {}
    per_class_delta = {}
    for cls in sorted(set(current_metrics.get("per_class", {})) | set(baseline_metrics.get("per_class", {}))):
        current_cls = dict((current_metrics.get("per_class") or {}).get(cls) or {})
        baseline_cls = dict((baseline_metrics.get("per_class") or {}).get(cls) or {})
        current_recall = safe_float(current_cls.get("recall"))
        baseline_recall = safe_float(baseline_cls.get("recall"))
        current_precision = safe_float(current_cls.get("precision"))
        baseline_precision = safe_float(baseline_cls.get("precision"))
        current_f1 = safe_float(current_cls.get("f1_score"))
        baseline_f1 = safe_float(baseline_cls.get("f1_score"))
        recall_drop_by_class[cls] = baseline_recall - current_recall
        per_class_delta[cls] = {
            "recall": current_recall - baseline_recall,
            "precision": current_precision - baseline_precision,
            "f1_score": current_f1 - baseline_f1,
        }
    comparison["baseline"] = baseline_metrics
    comparison["delta"] = delta
    comparison["recall_drop_by_class"] = recall_drop_by_class
    comparison["per_class_delta"] = per_class_delta
    comparison["status"] = "ready"
    return comparison


def promote_models(staging_dir: Path, models_dir: Path) -> list[str]:
    models_dir.mkdir(parents=True, exist_ok=True)
    promoted: list[str] = []
    for filename in PROMOTED_MODEL_FILES:
        source = staging_dir / filename
        if not source.exists():
            continue
        target = models_dir / filename
        shutil.copy2(source, target)
        promoted.append(filename)
    return promoted


def previous_retrain_context(manifest_path: Path) -> dict[str, Any]:
    payload = load_json(manifest_path)
    previous_summary = dict(payload.get("summary") or {}) if isinstance(payload, dict) else {}
    previous_rows = int(previous_summary.get("rows_after", 0) or 0)
    previous_status = str(payload.get("status") or "")
    default_trigger_rows = previous_rows if previous_status in {
        "promoted",
        "rejected_metrics",
        "invalid_evaluation",
        "trained",
    } else 0
    return {
        "payload": payload,
        "last_trigger_rows": int(payload.get("last_trigger_rows", default_trigger_rows) or 0),
    }


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def main() -> int:
    args = build_parser().parse_args()
    state_dir = Path(args.state_dir)
    export_dir = state_dir / DEFAULT_EXPORT_DIRNAME
    trace_path = Path(args.trainable_trace_jsonl) if args.trainable_trace_jsonl else export_dir / "rapp_online_trainable_trace.jsonl"
    summary_path = Path(args.trainable_summary_json) if args.trainable_summary_json else export_dir / "rapp_online_trainable_summary.json"
    manifest_path = Path(args.manifest_out) if args.manifest_out else export_dir / "rapp_online_retrain_latest.json"
    collection_quality_path = Path(args.collection_quality_json) if args.collection_quality_json else export_dir / "rapp_online_collection_quality.json"
    models_dir = Path(args.models_dir)
    baseline_report_path = Path(args.baseline_report_json) if args.baseline_report_json else models_dir / "training_report.json"
    generated_at = datetime.now(timezone.utc)
    staging_dir = export_dir / "retrain_staging" / generated_at.strftime("%Y%m%dT%H%M%SZ")

    summary = load_json(summary_path)
    collection_quality_report = analyze_collection_quality(
        trace_path,
        summary,
        feature_profile=args.feature_profile,
        generated_at=generated_at,
    )
    write_json(collection_quality_path, collection_quality_report)
    quality_status, quality_reasons, quality_checks = evaluate_collection_quality(
        collection_quality_report,
        max_class_dominance_ratio=float(args.max_class_dominance_ratio),
        min_decision_transitions=int(args.min_decision_transitions),
        min_decision_transition_ratio=float(args.min_decision_transition_ratio),
        min_distinct_decision_transitions=int(args.min_distinct_decision_transitions),
        required_decision_transitions=list(args.required_decision_transitions),
        required_scenario_families=list(args.required_scenario_families),
        max_constant_feature_ratio=float(args.max_constant_feature_ratio),
        min_valid_training_ratio=float(args.min_valid_training_ratio),
    )
    previous = previous_retrain_context(manifest_path)
    current_rows = int(summary.get("rows_after", 0) or 0)
    last_trigger_rows = int(previous.get("last_trigger_rows", 0) or 0)
    new_rows_since_trigger = max(0, current_rows - last_trigger_rows)
    next_retrain_rows = last_trigger_rows + int(args.min_new_rows)
    status, reasons = evaluate_readiness(summary, args.min_trainable, args.min_class_count, list(args.required_classes))
    if status == "ready" and last_trigger_rows > 0 and new_rows_since_trigger < int(args.min_new_rows):
        status = "blocked_new_rows"
        reasons = [
            f"new real trainable rows below cadence: {new_rows_since_trigger} < {int(args.min_new_rows)}"
        ]
    if status == "ready" and quality_status != "ready":
        status = quality_status
        reasons = quality_reasons
    cmd = build_training_command(args.python, trace_path, staging_dir, args.feature_profile)

    payload: dict[str, Any] = {
        "schema": "greenran.rapp_online_retrain.v1",
        "generated_at": generated_at.isoformat(),
        "state_dir": str(state_dir),
        "trainable_trace_jsonl": str(trace_path),
        "trainable_summary_json": str(summary_path),
        "models_dir": str(models_dir),
        "staging_dir": str(staging_dir),
        "feature_profile": str(args.feature_profile),
        "collection_quality_json": str(collection_quality_path),
        "baseline_report_json": str(baseline_report_path),
        "command": cmd,
        "summary": summary,
        "collection_quality": collection_quality_report,
        "collection_quality_gate": {
            "status": quality_status,
            "reasons": quality_reasons,
            "checks": quality_checks,
        },
        "status": status,
        "reasons": reasons,
        "dry_run": bool(args.dry_run),
        "target_trainable": int(args.target_trainable),
        "last_trigger_rows": last_trigger_rows,
        "new_rows_since_trigger": new_rows_since_trigger,
        "next_retrain_rows": next_retrain_rows,
        "promotion_gate": {
            "feature_profile": str(args.feature_profile),
            "min_rf_accuracy": float(args.min_rf_accuracy),
            "min_r2": float(args.min_r2),
            "min_healthy_allowed_recall": float(args.min_healthy_allowed_recall),
            "min_accuracy_gain": float(args.min_accuracy_gain),
            "max_class_recall_drop": float(args.max_class_recall_drop),
        },
        "retrain_cadence": {
            "min_new_rows": int(args.min_new_rows),
        },
    }

    if status != "ready":
        write_manifest(manifest_path, payload)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(" ".join(cmd), flush=True)
    if args.dry_run:
        payload["status"] = "dry_run"
        write_manifest(manifest_path, payload)
        return 0

    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    payload["returncode"] = int(result.returncode)
    payload["stdout_tail"] = result.stdout.splitlines()[-20:]
    payload["stderr_tail"] = result.stderr.splitlines()[-20:]
    payload["status"] = "trained" if result.returncode == 0 else "failed"

    report_path = staging_dir / "training_report.json"
    payload["training_report"] = load_json(report_path)
    payload["promoted_files"] = []
    payload["metric_gate_checks"] = {}
    baseline_report, baseline_status = load_baseline_report(baseline_report_path, args.feature_profile)
    payload["baseline_comparison"] = build_baseline_comparison(
        payload["training_report"],
        baseline_report,
        baseline_status,
    )
    if result.returncode == 0:
        payload["last_trigger_rows"] = current_rows
        payload["new_rows_since_trigger"] = new_rows_since_trigger
        payload["next_retrain_rows"] = current_rows + int(args.min_new_rows)
        promote_status, promote_reasons, checks = evaluate_training_report(
            payload["training_report"],
            min_rf_accuracy=args.min_rf_accuracy,
            min_r2=args.min_r2,
            min_healthy_allowed_recall=args.min_healthy_allowed_recall,
            expected_feature_profile=args.feature_profile,
            baseline_comparison=payload["baseline_comparison"],
            min_accuracy_gain=args.min_accuracy_gain,
            max_class_recall_drop=args.max_class_recall_drop,
        )
        payload["metric_gate_checks"] = checks
        payload["metric_gate_status"] = promote_status
        if promote_status == "promote":
            payload["promoted_files"] = promote_models(staging_dir, models_dir)
            payload["status"] = "promoted"
        elif promote_status == "invalid_evaluation":
            payload["status"] = "invalid_evaluation"
            payload["reasons"] = list(payload.get("reasons", [])) + promote_reasons
        else:
            payload["status"] = "rejected_metrics"
            payload["reasons"] = list(payload.get("reasons", [])) + promote_reasons
    write_manifest(manifest_path, payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.returncode == 0 else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
