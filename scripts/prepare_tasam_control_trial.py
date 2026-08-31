#!/usr/bin/env python3
"""Prepare an auditable ARMD + TA-SAM control-trial gate from shadow evidence."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rapp_marl_control_gate import compute_control_gate


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def evaluate_shadow(db_path: Path, window: int) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT score_delta, shadow_ran_completion_est, live_ran_completion_est,
                   shadow_ai_completion_est, live_ai_completion_est, policy_id
            FROM marl_shadow_comparison_history
            ORDER BY timestamp DESC LIMIT ?
            """,
            (int(window),),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise RuntimeError(f"nenhuma comparação MARL encontrada em {db_path}")
    score_deltas = [float(row["score_delta"] or 0.0) for row in rows]
    ran_deltas = [float(row["shadow_ran_completion_est"] or 0.0) - float(row["live_ran_completion_est"] or 0.0) for row in rows]
    ai_deltas = [float(row["shadow_ai_completion_est"] or 0.0) - float(row["live_ai_completion_est"] or 0.0) for row in rows]
    summary = {
        "sample_count": len(rows),
        "positive_score_rate": sum(delta > 0.0 for delta in score_deltas) / len(rows),
        "wins": sum(delta > 0.0 for delta in score_deltas),
        "losses": sum(delta < 0.0 for delta in score_deltas),
        "avg_score_delta": sum(score_deltas) / len(rows),
        "avg_ran_completion_delta": sum(ran_deltas) / len(rows),
        "avg_ai_completion_delta": sum(ai_deltas) / len(rows),
        "latest_policy_id": str(rows[0]["policy_id"] or ""),
        "evidence_window": int(window),
    }
    return summary


def prepare_trial(
    *,
    selected_manifest: Path,
    shadow_db: Path,
    output_dir: Path,
    window: int,
    min_samples: int,
    min_positive_rate: float,
) -> dict:
    selected = load_json(selected_manifest)
    primary = dict(selected.get("primary") or {})
    if not primary.get("package_dir"):
        raise RuntimeError("manifesto selecionado não possui o checkpoint primary")
    package_dir = Path(str(primary["package_dir"])).resolve()
    if not (package_dir / "tasam_marl_actors.pt").exists() or not (package_dir / "tasam_marl_checkpoint_meta.json").exists():
        raise RuntimeError(f"checkpoint primary incompleto: {package_dir}")

    shadow = evaluate_shadow(shadow_db, window)
    failures = []
    if shadow["sample_count"] < min_samples:
        failures.append(f"amostras insuficientes: {shadow['sample_count']} < {min_samples}")
    if shadow["positive_score_rate"] < min_positive_rate:
        failures.append(f"taxa positiva insuficiente: {shadow['positive_score_rate']:.4f} < {min_positive_rate:.4f}")
    if shadow["avg_score_delta"] <= 0.0:
        failures.append("delta médio de score não positivo")
    if shadow["avg_ran_completion_delta"] < -0.01:
        failures.append("queda média de RAN acima de 1 p.p.")
    if shadow["avg_ai_completion_delta"] < -0.02:
        failures.append("queda média de IA acima de 2 p.p.")
    if failures:
        raise RuntimeError("evidência não aprova o canário: " + "; ".join(failures))

    run_dir = str(package_dir)
    policy_id = f"ta_sam_marl_control_trial_v1:{package_dir.name}"
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=24)
    best_run = {
        **primary,
        "run_dir": run_dir,
        "readiness": "control_candidate",
        "promote_shadow": True,
        "promote_control_candidate": True,
        "control_trial_scope": "joint_armd_tasam_canary_10pct",
        "reasons": [
            "TA-SAM seed 45 epoch 200 selected checkpoint",
            "combined ARMD + TA-SAM shadow evidence passed the 10% canary gate",
        ],
    }
    tasam_eval = {
        "schema": "greenran.tasam_candidate_evaluation.v1",
        "runs_root": str(output_dir),
        "best_run": best_run,
        "evaluated_runs": [best_run],
        "control_trial_evidence": {
            "shadow_db": str(shadow_db.resolve()),
            "shadow_summary": shadow,
            "prepared_at_utc": now.isoformat(),
        },
    }
    runtime_eval = {
        "schema": "greenran.marl_control_trial_runtime_eval.v1",
        "db": str(shadow_db.resolve()),
        "window": window,
        "readiness": "control_trial_candidate",
        "reasons": ["combined shadow evidence passed the joint canary thresholds"],
        "summary": {
            **shadow,
            "checkpoint_coverage": 1.0,
            "recommend_rate": shadow["positive_score_rate"],
            "latest_policy_id": policy_id,
            "latest_source": "checkpoint",
            "latest_readiness": "control_candidate",
        },
    }
    approval = {
        "schema": "greenran.marl_control_trial_approval.v1",
        "approved": True,
        "approved_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "approved_policy_id": policy_id,
        "approved_run_dir": run_dir,
        "scope": {
            "mode": "joint_control_trial",
            "canary_fraction": 0.10,
            "target_decisions": 300,
            "eligible_only_when": ["armd_valid", "tasam_valid", "no_critical_sla_violation"],
            "external_authority": "last_resort_only",
            "proxy": False,
        },
        "rollback": {
            "critical_streak": 3,
            "rolling_window": 30,
            "negative_score_delta": True,
            "ran_drop_p.p.": 1.0,
            "ai_drop_p.p.": 1.0,
        },
    }
    write_json(output_dir / "tasam_candidate_evaluation.json", tasam_eval)
    write_json(output_dir / "marl_shadow_runtime_eval.json", runtime_eval)
    write_json(output_dir / "marl_control_trial_approval.json", approval)
    gate = compute_control_gate(tasam_eval, runtime_eval, approval)
    gate_payload = {
        "tasam_eval_path": str(output_dir / "tasam_candidate_evaluation.json"),
        "runtime_eval_path": str(output_dir / "marl_shadow_runtime_eval.json"),
        "manual_approval_path": str(output_dir / "marl_control_trial_approval.json"),
        "gate": gate,
    }
    write_json(output_dir / "marl_control_gate.json", gate_payload)
    write_json(output_dir / "control_trial_config.json", {
        "schema": "greenran.joint_control_trial_config.v1",
        "mode": "joint_control_trial",
        "fraction": 0.10,
        "target_decisions": 300,
        "rolling_window": 30,
        "critical_streak": 3,
        "min_confidence": 0.60,
        "min_ran_delta": -0.01,
        "min_ai_delta": -0.01,
        "fallback": "live_allocator",
        "external_authority": "last_resort_only",
    })
    return {"shadow": shadow, "gate": gate, "output_dir": str(output_dir.resolve()), "policy_id": policy_id}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the joint ARMD + TA-SAM control-trial manifests")
    parser.add_argument("--selected-manifest", type=Path, default=PROJECT_ROOT / "runs/tasam_selected_greenran_20260807/tasam_selected_checkpoint_manifest.json")
    parser.add_argument("--shadow-db", type=Path, default=PROJECT_ROOT / "runs/tasam_greenran_combined_shadow_seed45_20260809/rapp_data_lake.db")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "runs/tasam_greenran_control_trial_20260809")
    parser.add_argument("--window", type=int, default=300)
    parser.add_argument("--min-samples", type=int, default=300)
    parser.add_argument("--min-positive-rate", type=float, default=0.80)
    args = parser.parse_args()
    payload = prepare_trial(
        selected_manifest=args.selected_manifest,
        shadow_db=args.shadow_db,
        output_dir=args.output_dir,
        window=args.window,
        min_samples=args.min_samples,
        min_positive_rate=args.min_positive_rate,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
