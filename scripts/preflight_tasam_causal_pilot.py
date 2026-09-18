#!/usr/bin/env python3
"""Fail-closed preflight for the causal GreenRAN TA-SAM pilot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

try:
    from run_tasam_online_arm import EXPECTED_NS3_BINARY, _validate_checkpoint, _validate_control_gate
except ModuleNotFoundError:  # package import from the repository test runner
    from scripts.run_tasam_online_arm import EXPECTED_NS3_BINARY, _validate_checkpoint, _validate_control_gate
try:
    from greenran_infra_budget import resolve_cgroup_root
except ModuleNotFoundError:  # package import from the repository test runner
    from src.greenran_infra_budget import resolve_cgroup_root


ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("simulator", "ric_xapps", "rapp_armd", "tasam", "collectors")


def _local_path_check(path: Path, label: str) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return {"path": str(resolved), "label": label, "valid": False, "reason": "outside local workspace"}
    return {"path": str(resolved), "label": label, "valid": True}


def _shadow_gate(db: Path, *, window: int, min_samples: int, min_positive_rate: float) -> dict[str, Any]:
    if not db.is_file():
        return {"valid": False, "reason": "shadow database missing", "path": str(db)}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(marl_shadow_comparison_history)")}
        required = {
            "causal_score_delta", "tasam_checkpoint_valid", "tasam_fallback_used",
            "tasam_evidence_valid", "energy_valid", "resource_valid", "energy_model_version",
        }
        if not required.issubset(columns):
            conn.close()
            return {"valid": False, "reason": "shadow evidence uses legacy schema", "missing_columns": sorted(required - columns), "path": str(db)}
        rows = conn.execute(
            "SELECT * FROM marl_shadow_comparison_history ORDER BY timestamp DESC LIMIT ?",
            (int(window),),
        ).fetchall()
    except sqlite3.Error as exc:
        conn.close()
        return {"valid": False, "reason": f"shadow query failed: {exc}", "path": str(db)}
    conn.close()
    if not rows:
        return {"valid": False, "reason": "shadow evidence empty", "path": str(db)}
    scores = [float(row["causal_score_delta"] or 0.0) for row in rows]
    ran = [float(row["shadow_ran_completion_est"] or 0.0) - float(row["live_ran_completion_est"] or 0.0) for row in rows]
    ai = [float(row["shadow_ai_completion_est"] or 0.0) - float(row["live_ai_completion_est"] or 0.0) for row in rows]
    summary = {
        "sample_count": len(rows),
        "positive_score_rate": sum(value > 0.0 for value in scores) / len(rows),
        "avg_score_delta": sum(scores) / len(scores),
        "positive_score_rate": sum(value >= 0.0 for value in scores) / len(scores),
        "checkpoint_valid_count": sum(int(row["tasam_checkpoint_valid"] or 0) == 1 for row in rows),
        "fallback_count": sum(int(row["tasam_fallback_used"] or 0) != 0 for row in rows),
        "evidence_valid_count": sum(int(row["tasam_evidence_valid"] or 0) == 1 for row in rows),
        "energy_valid_count": sum(int(row["energy_valid"] or 0) == 1 for row in rows),
        "resource_valid_count": sum(int(row["resource_valid"] or 0) == 1 for row in rows),
        "energy_model_versions": sorted({str(row["energy_model_version"] or "") for row in rows}),
        "avg_ran_completion_delta": sum(ran) / len(ran),
        "avg_ai_completion_delta": sum(ai) / len(ai),
        "policy_ids": sorted({str(row["policy_id"] or "") for row in rows}),
        "path": str(db.resolve()),
    }
    failures = []
    if summary["sample_count"] < min_samples:
        failures.append(f"samples {summary['sample_count']} < {min_samples}")
    if summary["positive_score_rate"] < min_positive_rate:
        failures.append("positive score rate below threshold")
    if summary["checkpoint_valid_count"] != len(rows):
        failures.append("checkpoint TA-SAM inválido")
    if summary["fallback_count"] != 0:
        failures.append("fallback TA-SAM detectado")
    if summary["evidence_valid_count"] != len(rows) or summary["energy_valid_count"] != len(rows) or summary["resource_valid_count"] != len(rows):
        failures.append("evidência energética/recursos incompleta")
    if len(summary["energy_model_versions"]) != 1 or not summary["energy_model_versions"][0]:
        failures.append("versão de calibração energética inconsistente")
    if summary["avg_score_delta"] <= 0.0:
        failures.append("average score delta is not positive")
    if summary["avg_ran_completion_delta"] < -0.01:
        failures.append("RAN completion delta below -1pp")
    if summary["avg_ai_completion_delta"] < -0.02:
        failures.append("AI completion delta below -2pp")
    summary["valid"] = not failures
    summary["failures"] = failures
    return summary


def _cgroup_probe(root: Path) -> dict[str, Any]:
    required = []
    writable = []
    missing = []
    for group in GROUPS:
        path = root / group
        if not path.is_dir():
            missing.append(str(path))
            continue
        for filename in ("cpu.max", "memory.high", "io.weight", "cpu.stat", "memory.current", "io.stat"):
            candidate = path / filename
            required.append(str(candidate))
            if os.access(candidate, os.W_OK if filename in {"cpu.max", "memory.high", "io.weight"} else os.R_OK):
                writable.append(str(candidate))
    limit_files = len(GROUPS) * 3
    writable_limits = sum(1 for group in GROUPS for filename in ("cpu.max", "memory.high", "io.weight") if os.access(root / group / filename, os.W_OK))
    return {
        "root": str(root),
        "groups_present": len(missing) == 0,
        "missing": missing,
        "writable_limit_files": writable_limits,
        "required_limit_files": limit_files,
        "readable_or_writable_files": len(writable),
        "enforced_capable": len(missing) == 0 and writable_limits == limit_files,
    }


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    run_parent = args.output.resolve().parent
    free_gib = shutil.disk_usage(run_parent).free / (1024 ** 3)
    binary = args.ns3_bin.resolve()
    checks: dict[str, Any] = {
        "disk": {"path": str(run_parent), "free_gib": free_gib, "minimum_free_gib": args.min_free_gib, "valid": free_gib >= args.min_free_gib},
        "binary": {"path": str(binary), "expected_name": EXPECTED_NS3_BINARY, "valid": binary.name == EXPECTED_NS3_BINARY and binary.is_file() and os.access(binary, os.X_OK)},
        "scenario": {"ue_count": 20, "du_count": 3, "e2_du": True, "e2_nr": False, "real_pdcp": True, "proxy": False, "valid": True},
        "cgroup": _cgroup_probe(args.cgroup_root),
    }
    local_paths = [
        _local_path_check(args.checkpoint, "checkpoint"),
        _local_path_check(args.shadow_db, "shadow-db"),
        _local_path_check(args.control_gate, "control-gate"),
        _local_path_check(args.output, "output"),
        _local_path_check(args.ns3_bin, "ns3-bin"),
    ]
    checks["local_only"] = {"paths": local_paths, "valid": all(item["valid"] for item in local_paths)}
    try:
        _validate_checkpoint(args.checkpoint.resolve())
        checks["checkpoint"] = {"path": str(args.checkpoint.resolve()), "valid": True}
    except SystemExit as exc:
        checks["checkpoint"] = {"path": str(args.checkpoint.resolve()), "valid": False, "reason": str(exc)}
    if args.shadow_db:
        checks["shadow"] = _shadow_gate(args.shadow_db.resolve(), window=args.shadow_window, min_samples=args.min_shadow_samples, min_positive_rate=args.min_positive_rate)
    else:
        checks["shadow"] = {"valid": False, "reason": "shadow evidence path not supplied"}
    if args.control_gate:
        try:
            _validate_control_gate(args.control_gate.resolve(), args.checkpoint.resolve())
            payload = json.loads(args.control_gate.resolve().read_text(encoding="utf-8"))
            gate = payload.get("gate") if isinstance(payload, dict) else None
            checks["control_gate"] = {"path": str(args.control_gate.resolve()), "valid": True, "gate": gate or {}}
        except (OSError, json.JSONDecodeError, SystemExit) as exc:
            checks["control_gate"] = {"path": str(args.control_gate.resolve()), "valid": False, "reason": str(exc)}
    else:
        checks["control_gate"] = {"valid": False, "reason": "control gate path not supplied"}
    checks["valid"] = all([
        checks["disk"]["valid"], checks["binary"]["valid"], checks["scenario"]["valid"],
        checks["cgroup"]["enforced_capable"], checks["checkpoint"]["valid"],
        checks["shadow"]["valid"], checks["control_gate"]["valid"], checks["local_only"]["valid"],
    ])
    return {"schema": "greenran.tasam.causal_preflight.v1", "valid": checks["valid"], "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--shadow-db", type=Path, required=True)
    parser.add_argument("--control-gate", type=Path, required=True)
    parser.add_argument("--ns3-bin", type=Path, default=ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario")
    parser.add_argument("--cgroup-root", type=Path, default=resolve_cgroup_root())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-free-gib", type=float, default=10.0)
    parser.add_argument("--shadow-window", type=int, default=300)
    parser.add_argument("--min-shadow-samples", type=int, default=300)
    parser.add_argument("--min-positive-rate", type=float, default=0.80)
    args = parser.parse_args()
    report = preflight(args)
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "valid": report["valid"]}, ensure_ascii=False))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
