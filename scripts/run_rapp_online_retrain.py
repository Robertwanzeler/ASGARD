#!/usr/bin/env python3
"""Run incremental rApp ML retraining from the trainable online trace."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = ROOT / "runs" / "tasam_article_ns3_collection"
DEFAULT_MODELS_DIR = ROOT / "models"
DEFAULT_EXPORT_DIRNAME = "tasam_article_export"
DEFAULT_TARGET_TRAINABLE = 1000
DEFAULT_MIN_NEW_ROWS = 100
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
    parser.add_argument("--python", default=sys.executable, help="Python executable used for training")
    parser.add_argument("--min-trainable", type=int, default=50, help="Minimum trainable rows required to retrain")
    parser.add_argument("--target-trainable", type=int, default=DEFAULT_TARGET_TRAINABLE, help="Campaign target for 100%% real trainable rows")
    parser.add_argument("--min-new-rows", type=int, default=DEFAULT_MIN_NEW_ROWS, help="Minimum number of new real trainable rows required since the last retrain attempt")
    parser.add_argument("--min-class-count", type=int, default=5, help="Minimum rows per class required to retrain")
    parser.add_argument("--required-classes", nargs="*", default=["ALLOWED", "CONDITIONAL", "BLOCKED"], help="Classes that must exist in the trainable subset")
    parser.add_argument("--min-rf-accuracy", type=float, default=0.95, help="Minimum Random Forest accuracy required for promotion")
    parser.add_argument("--min-r2", type=float, default=0.80, help="Minimum regressor R2 required for promotion")
    parser.add_argument("--min-healthy-allowed-recall", type=float, default=0.70, help="Minimum healthy ALLOWED recall required when available")
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


def build_training_command(python_bin: str, trace_path: Path, models_dir: Path) -> list[str]:
    return [
        python_bin,
        str(ROOT / "training" / "train_ml_model.py"),
        "--trace-jsonl",
        str(trace_path),
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
) -> tuple[str, list[str], dict[str, Any]]:
    reasons: list[str] = []
    evaluation = dict(report.get("evaluation") or {}) if isinstance(report, dict) else {}
    classifier = dict(report.get("classifier") or {}) if isinstance(report, dict) else {}
    regressor = dict(report.get("regressor") or {}) if isinstance(report, dict) else {}

    rf_accuracy = safe_float(classifier.get("random_forest_accuracy"))
    healthy_allowed_recall = safe_float(classifier.get("healthy_allowed_recall"))
    r2_value = safe_float(regressor.get("r2"))
    evaluation_valid = bool(evaluation.get("valid", True))

    checks = {
        "rf_accuracy": rf_accuracy,
        "healthy_allowed_recall": healthy_allowed_recall,
        "regressor_r2": r2_value,
        "evaluation_valid": evaluation_valid,
    }

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

    status = "promote" if not reasons else "reject_metrics"
    return status, reasons, checks


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    state_dir = Path(args.state_dir)
    export_dir = state_dir / DEFAULT_EXPORT_DIRNAME
    trace_path = Path(args.trainable_trace_jsonl) if args.trainable_trace_jsonl else export_dir / "rapp_online_trainable_trace.jsonl"
    summary_path = Path(args.trainable_summary_json) if args.trainable_summary_json else export_dir / "rapp_online_trainable_summary.json"
    manifest_path = Path(args.manifest_out) if args.manifest_out else export_dir / "rapp_online_retrain_latest.json"
    models_dir = Path(args.models_dir)
    generated_at = datetime.now(timezone.utc)
    staging_dir = export_dir / "retrain_staging" / generated_at.strftime("%Y%m%dT%H%M%SZ")

    summary = load_json(summary_path)
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
    cmd = build_training_command(args.python, trace_path, staging_dir)

    payload: dict[str, Any] = {
        "schema": "greenran.rapp_online_retrain.v1",
        "generated_at": generated_at.isoformat(),
        "state_dir": str(state_dir),
        "trainable_trace_jsonl": str(trace_path),
        "trainable_summary_json": str(summary_path),
        "models_dir": str(models_dir),
        "staging_dir": str(staging_dir),
        "command": cmd,
        "summary": summary,
        "status": status,
        "reasons": reasons,
        "dry_run": bool(args.dry_run),
        "target_trainable": int(args.target_trainable),
        "last_trigger_rows": last_trigger_rows,
        "new_rows_since_trigger": new_rows_since_trigger,
        "next_retrain_rows": next_retrain_rows,
        "promotion_gate": {
            "min_rf_accuracy": float(args.min_rf_accuracy),
            "min_r2": float(args.min_r2),
            "min_healthy_allowed_recall": float(args.min_healthy_allowed_recall),
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
    if result.returncode == 0:
        payload["last_trigger_rows"] = current_rows
        payload["new_rows_since_trigger"] = new_rows_since_trigger
        payload["next_retrain_rows"] = current_rows + int(args.min_new_rows)
        promote_status, promote_reasons, checks = evaluate_training_report(
            payload["training_report"],
            min_rf_accuracy=args.min_rf_accuracy,
            min_r2=args.min_r2,
            min_healthy_allowed_recall=args.min_healthy_allowed_recall,
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
