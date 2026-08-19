#!/usr/bin/env python3
"""
Helpers para acionar e resumir o pipeline oficial de retreino online do rApp.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from greenran_paths import MODELS_DIR, PROJECT_ROOT, STATE_DIR


EXPORT_DIRNAME = "tasam_article_export"
MANIFEST_FILENAME = "rapp_online_retrain_latest.json"


def build_online_retrain_command(
    *,
    python_bin: str,
    state_dir: Path = STATE_DIR,
    models_dir: Path = MODELS_DIR,
) -> list[str]:
    script_path = PROJECT_ROOT / "scripts" / "run_rapp_online_retrain.py"
    return [
        python_bin,
        str(script_path),
        "--state-dir",
        str(state_dir),
        "--models-dir",
        str(models_dir),
    ]


def online_retrain_manifest_path(state_dir: Path = STATE_DIR) -> Path:
    return Path(state_dir) / EXPORT_DIRNAME / MANIFEST_FILENAME


def load_online_retrain_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def summarize_online_retrain_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    training_report = dict(payload.get("training_report") or {})
    classifier = dict(training_report.get("classifier") or {})
    regressor = dict(training_report.get("regressor") or {})
    collection_quality_gate = dict(payload.get("collection_quality_gate") or {})
    return {
        "status": str(payload.get("status") or "missing"),
        "reasons": list(payload.get("reasons") or []),
        "feature_profile": str(training_report.get("feature_profile") or payload.get("feature_profile") or ""),
        "rf_accuracy": classifier.get("random_forest_accuracy"),
        "healthy_allowed_recall": classifier.get("healthy_allowed_recall"),
        "classes": list(classifier.get("classes") or []),
        "regressor_r2": regressor.get("r2"),
        "promoted_files": list(payload.get("promoted_files") or []),
        "collection_quality_status": str(collection_quality_gate.get("status") or ""),
        "collection_quality_reasons": list(collection_quality_gate.get("reasons") or []),
        "next_retrain_rows": payload.get("next_retrain_rows"),
        "new_rows_since_trigger": payload.get("new_rows_since_trigger"),
    }
