#!/usr/bin/env python3
"""Promote a validated TA-SAM checkpoint to the active shadow manifests.

The promotion is deliberately shadow-only: it never grants control-trial
authority and never changes ARMD settings. Existing manifests are backed up
before being replaced.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy_shadow_checkpoint import (  # noqa: E402
    _blocking_errors,
    build_manifest_entry,
    validate_checkpoint,
    validate_readiness,
)


DEFAULT_TARGETS = [
    PROJECT_ROOT / "runs" / "sac_bootstrap" / "tasam_candidate_evaluation_latest.json",
    PROJECT_ROOT / "runs" / "tasam_greenran_control_trial_20260809" / "tasam_candidate_evaluation.json",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote TA-SAM checkpoint as shadow primary")
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--readiness", choices=["not_ready", "shadow_ready"], default="shadow_ready")
    parser.add_argument("--target", action="append", dest="targets", help="Active manifest target; repeatable")
    parser.add_argument("--force-noncritical", action="store_true", help="Allow non-critical readiness warnings")
    return parser.parse_args()


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def backup(path: Path, stamp: str) -> str:
    if not path.exists():
        return ""
    backup_path = path.parent / "tasam_candidate_evaluation_backups" / f"{path.stem}_before_seed46_{stamp}{path.suffix}"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return str(backup_path.resolve())


def promote(checkpoint_dir: Path, targets: list[Path], readiness: str, force_noncritical: bool) -> dict:
    errors = validate_checkpoint(checkpoint_dir)
    errors.extend(validate_readiness(checkpoint_dir, readiness))
    if errors and _blocking_errors(errors):
        raise RuntimeError("blocking validation errors: " + "; ".join(errors))
    if errors and not force_noncritical:
        raise RuntimeError("non-critical validation errors require --force-noncritical: " + "; ".join(errors))

    entry = build_manifest_entry(checkpoint_dir, readiness)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = []
    for target in targets:
        target = target.resolve()
        payload = load_json(target)
        if not payload:
            payload = {"runs_root": str(target.parent)}
        previous_best = dict(payload.get("best_run") or {})
        evaluated = [row for row in list(payload.get("evaluated_runs") or []) if row.get("run_dir") != entry["run_dir"]]
        evaluated.append(entry)
        payload["evaluated_runs"] = evaluated
        payload["best_run"] = entry
        payload["selected_checkpoint_promotion"] = {
            "promoted_at_utc": datetime.now(timezone.utc).isoformat(),
            "checkpoint_dir": str(checkpoint_dir),
            "readiness": readiness,
            "promote_shadow": True,
            "promote_control_candidate": False,
            "previous_best_run_dir": previous_best.get("run_dir", ""),
            "validation_warnings": errors,
        }
        backup_path = backup(target, stamp)
        write_json_atomic(target, payload)
        results.append({
            "manifest": str(target),
            "backup": backup_path,
            "previous_best_run_dir": previous_best.get("run_dir", ""),
            "new_best_run_dir": entry["run_dir"],
        })

    return {
        "checkpoint_dir": str(checkpoint_dir),
        "readiness": readiness,
        "promote_shadow": True,
        "promote_control_candidate": False,
        "validation_warnings": errors,
        "targets": results,
    }


def main() -> int:
    args = parse_args()
    checkpoint_dir = Path(args.checkpoint_dir).resolve()
    targets = [Path(value) for value in args.targets] if args.targets else DEFAULT_TARGETS
    try:
        result = promote(checkpoint_dir, targets, args.readiness, args.force_noncritical)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
