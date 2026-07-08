#!/usr/bin/env python3
"""Activate a packaged TA-SAM checkpoint for GreenRAN shadow runtime."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deploy_shadow_checkpoint import (
    _blocking_errors,
    build_manifest_entry,
    validate_checkpoint,
    validate_readiness,
)
from evaluate_marl_control_gate import evaluate_control_gate, write_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SELECTED_MANIFEST = (
    PROJECT_ROOT / "runs" / "sac_bootstrap" / "tasam_selected_greenran" / "tasam_selected_checkpoint_manifest.json"
)
DEFAULT_ACTIVE_EVAL = PROJECT_ROOT / "runs" / "sac_bootstrap" / "tasam_candidate_evaluation_latest.json"
DEFAULT_GATE_OUTPUT = PROJECT_ROOT / "runs" / "sac_bootstrap" / "marl_control_gate_latest.json"
DEFAULT_RUNTIME_EVAL = PROJECT_ROOT / "runs" / "sac_bootstrap" / "marl_shadow_runtime_eval_latest.json"
DEFAULT_MANUAL_APPROVAL = PROJECT_ROOT / "runs" / "sac_bootstrap" / "marl_control_trial_approval.json"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "runs" / "sac_bootstrap" / "tasam_candidate_evaluation_backups"
DEFAULT_ACTIVATION_RECEIPT = PROJECT_ROOT / "runs" / "sac_bootstrap" / "tasam_selected_greenran" / "activation_receipt_latest.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Activate a packaged TA-SAM checkpoint for GreenRAN shadow runtime")
    parser.add_argument("--selected-manifest", default=str(DEFAULT_SELECTED_MANIFEST), help="Selected checkpoint manifest JSON")
    parser.add_argument("--role", choices=["primary", "secondary"], default="primary", help="Selected checkpoint role to activate")
    parser.add_argument(
        "--readiness",
        choices=["not_ready", "shadow_ready", "control_candidate"],
        default="shadow_ready",
        help="Readiness label to write in the active evaluation manifest",
    )
    parser.add_argument(
        "--active-eval",
        default=str(DEFAULT_ACTIVE_EVAL),
        help="Active TA-SAM evaluation manifest consumed by the runtime",
    )
    parser.add_argument("--gate-output", default=str(DEFAULT_GATE_OUTPUT), help="Output path for the control gate manifest")
    parser.add_argument("--runtime-eval", default=str(DEFAULT_RUNTIME_EVAL), help="Runtime shadow evaluation manifest path")
    parser.add_argument("--manual-approval", default=str(DEFAULT_MANUAL_APPROVAL), help="Manual approval manifest path")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR), help="Where to store backups of the previous active manifest")
    parser.add_argument("--receipt", default=str(DEFAULT_ACTIVATION_RECEIPT), help="Activation receipt JSON path")
    parser.add_argument("--skip-shadow-verify", action="store_true", default=False, help="Skip direct rapp_marl_shadow checkpoint-load verification")
    parser.add_argument(
        "--force-noncritical",
        action="store_true",
        default=False,
        help="Allow activation even when legacy readiness thresholds report non-critical validation errors",
    )
    return parser


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _verify_shadow_loader() -> dict[str, Any]:
    from rapp_marl_shadow import MARLShadowRuntimeEvaluator

    evaluator = MARLShadowRuntimeEvaluator({"mode": "shadow", "stability_window": 2})
    marl_state = {
        "topology_id": "greenran_fixed_marl_v1",
        "du_states": [
            {"du_id": "du1", "primary_slice": "eMBB", "state_vector": [0.8, 0.2, 0.1, 0.3, 0.8, 0.1, 0.1, 0.7, 0.6, 0.8]},
            {"du_id": "du2", "primary_slice": "mMTC", "state_vector": [0.4, 0.6, 0.2, 0.3, 0.2, 0.7, 0.1, 0.5, 0.4, 0.7]},
            {"du_id": "du3", "primary_slice": "URLLC", "state_vector": [0.3, 0.2, 0.9, 0.4, 0.1, 0.2, 0.7, 0.6, 0.5, 0.6]},
        ],
        "slice_state": {
            "eMBB": {"qos_pressure": 0.8},
            "mMTC": {"qos_pressure": 0.6},
            "URLLC": {"qos_pressure": 0.9},
        },
    }
    resource_snapshot = {
        "usable_budget": 0.95,
        "resource_budget": 1.0,
        "d_ran": 0.9,
        "d_ai": 0.4,
        "r_ran": 0.7,
        "r_ai": 0.25,
    }
    result = evaluator.evaluate(marl_state, resource_snapshot=resource_snapshot)
    return {
        "enabled": bool(result.get("enabled", False)),
        "available": bool(result.get("available", False)),
        "policy_id": str(result.get("policy_id", "") or ""),
        "source": str(result.get("source", "") or ""),
        "checkpoint_run_dir": str(result.get("checkpoint_run_dir", "") or ""),
        "checkpoint_readiness": str(result.get("checkpoint_readiness", "") or ""),
        "checkpoint_error": str(result.get("checkpoint_error", "") or ""),
        "du_count": int(result.get("du_count", 0) or 0),
        "score_delta": float(((result.get("comparison") or {}).get("score_delta", 0.0) or 0.0)),
        "recommend_shadow": bool(((result.get("comparison") or {}).get("recommend_shadow", False))),
        "gate_status": str((((result.get("control_gate") or {}).get("status", "")) or "")),
    }


def _selected_entry(selected_manifest_path: Path, role: str) -> dict[str, Any]:
    payload = _load_json(selected_manifest_path)
    entry = payload.get(role) or {}
    if not entry:
        raise ValueError(f"role '{role}' missing from {selected_manifest_path}")
    package_dir = Path(str(entry.get("package_dir", "") or "")).resolve()
    if not package_dir.exists():
        raise ValueError(f"package_dir not found for role '{role}': {package_dir}")
    entry = dict(entry)
    entry["package_dir"] = str(package_dir)
    return entry


def _backup_previous_manifest(active_eval_path: Path, backup_dir: Path) -> Path | None:
    if not active_eval_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"tasam_candidate_evaluation_{timestamp}.json"
    backup_path.write_text(active_eval_path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def activate_selected_checkpoint(
    *,
    selected_manifest_path: Path,
    role: str,
    readiness: str,
    active_eval_path: Path,
    gate_output_path: Path,
    runtime_eval_path: Path,
    manual_approval_path: Path,
    backup_dir: Path,
    receipt_path: Path,
    force_noncritical: bool,
    verify_shadow_load: bool,
) -> dict[str, Any]:
    selected = _selected_entry(selected_manifest_path, role)
    checkpoint_dir = Path(selected["package_dir"]).resolve()

    errors = validate_checkpoint(checkpoint_dir)
    errors.extend(validate_readiness(checkpoint_dir, readiness))
    if errors and _blocking_errors(errors):
        raise RuntimeError("blocking validation errors: " + "; ".join(errors))
    if errors and not force_noncritical:
        raise RuntimeError("validation errors require --force-noncritical: " + "; ".join(errors))

    previous_manifest = _load_json(active_eval_path) if active_eval_path.exists() else {"runs_root": str(active_eval_path.parent)}
    previous_best = previous_manifest.get("best_run") or {}
    backup_path = _backup_previous_manifest(active_eval_path, backup_dir)

    entry = build_manifest_entry(checkpoint_dir, readiness)
    evaluated = [row for row in list(previous_manifest.get("evaluated_runs") or []) if row.get("run_dir") != entry.get("run_dir")]
    evaluated.append(entry)
    previous_manifest["evaluated_runs"] = evaluated
    previous_manifest["best_run"] = entry
    previous_manifest["selected_checkpoint_activation"] = {
        "activated_at_utc": _utc_now(),
        "selected_manifest_path": str(selected_manifest_path.resolve()),
        "selected_role": role,
        "selected_usage_profile": selected.get("usage_profile", ""),
        "selected_package_dir": str(checkpoint_dir),
        "validation_errors": errors,
        "previous_best_run_dir": previous_best.get("run_dir", ""),
        "backup_path": str(backup_path.resolve()) if backup_path else "",
    }
    _write_json(active_eval_path, previous_manifest)

    gate_payload = evaluate_control_gate(
        tasam_eval_path=active_eval_path,
        runtime_eval_path=runtime_eval_path,
        manual_approval_path=manual_approval_path,
    )
    write_payload(gate_payload, gate_output_path)
    shadow_verification = _verify_shadow_loader() if verify_shadow_load else {}

    receipt = {
        "activated_at_utc": _utc_now(),
        "selected_manifest_path": str(selected_manifest_path.resolve()),
        "selected_role": role,
        "readiness": readiness,
        "force_noncritical": bool(force_noncritical),
        "checkpoint_dir": str(checkpoint_dir),
        "summary_path": entry.get("summary_path", ""),
        "validation_errors": errors,
        "backup_path": str(backup_path.resolve()) if backup_path else "",
        "active_eval_path": str(active_eval_path.resolve()),
        "gate_output_path": str(gate_output_path.resolve()),
        "gate_status": ((gate_payload or {}).get("gate") or {}).get("status", ""),
        "gate_policy_id": ((gate_payload or {}).get("gate") or {}).get("policy_id", ""),
        "shadow_loader_verification": shadow_verification,
    }
    _write_json(receipt_path, receipt)
    return receipt


def main() -> int:
    args = build_parser().parse_args()
    receipt = activate_selected_checkpoint(
        selected_manifest_path=Path(args.selected_manifest),
        role=args.role,
        readiness=args.readiness,
        active_eval_path=Path(args.active_eval),
        gate_output_path=Path(args.gate_output),
        runtime_eval_path=Path(args.runtime_eval),
        manual_approval_path=Path(args.manual_approval),
        backup_dir=Path(args.backup_dir),
        receipt_path=Path(args.receipt),
        force_noncritical=bool(args.force_noncritical),
        verify_shadow_load=not bool(args.skip_shadow_verify),
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
