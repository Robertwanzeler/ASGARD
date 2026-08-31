#!/usr/bin/env python3
"""Versioned TA-SAM online controller for the real GreenRAN runtime.

The active checkpoint is never trained in place.  Live PDCP transitions are
exported into a 70/30 historical/recent replay, a short TA-SAM candidate
update is trained from the active actors, and the candidate is exposed to the
rApp through an atomic manifest.  Rollout is controlled by a separate atomic
fraction file read by the rApp on every decision.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_CHECKPOINT = ROOT / "runs/tasam_training_weak_reinforced_20260812/training_final/seed_0045/tasam_selective"
HISTORICAL_TRACE = ROOT / "runs/greenran_tasam_e2_active_20260827_v7/final_dataset/tasam_article_trace_final.jsonl"
PYTHON = Path("/usr/bin/python3")
STAGES = (("shadow", 0.0), ("canary_10", 0.10), ("canary_25", 0.25), ("canary_50", 0.50), ("full", 1.0))


def full_control_mode() -> bool:
    mode = os.environ.get("GREENRAN_TASAM_ADVISOR_MODE", "").strip().lower()
    return mode in {"tasam_full_control", "tasam-full-control"}


def initial_rollout() -> tuple[str, float]:
    """Resolve an explicitly requested initial rollout for a fresh run."""
    if full_control_mode():
        return "full", 1.0
    raw = os.environ.get("GREENRAN_TASAM_FORCE_FULL_ROLLOUT", "0").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return "full", 1.0
    return "shadow", 0.0


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default
    return payload if isinstance(payload, dict) else ({} if default is None else default)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def count_snapshots(db: Path) -> int:
    if not db.exists():
        return 0
    with sqlite3.connect(str(db)) as conn:
        try:
            return int(conn.execute("select count(*) from marl_global_state_history").fetchone()[0] or 0)
        except sqlite3.OperationalError:
            return 0


def count_decisions(db: Path) -> int:
    if not db.exists():
        return 0
    with sqlite3.connect(str(db)) as conn:
        try:
            return int(conn.execute("select count(*) from decisions_history").fetchone()[0] or 0)
        except sqlite3.OperationalError:
            return 0


def runtime_guard(db: Path, window: int = 30) -> dict[str, Any]:
    """Check the hard online guards using only persisted runtime evidence."""
    result: dict[str, Any] = {
        "window": window,
        "floor_violations": 0,
        "critical_streak": 0,
        "avg_score_delta": None,
        "rollback": False,
        "reason": "no evidence yet",
    }
    if not db.exists():
        return result
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            allocations = conn.execute(
                "select floor_feasible, per_ue_floor_violation_count from resource_allocation_history order by timestamp desc limit ?",
                (window,),
            ).fetchall()
        except sqlite3.OperationalError:
            allocations = []
        try:
            comparisons = conn.execute(
                "select score_delta from marl_shadow_comparison_history order by timestamp desc limit ?",
                (window,),
            ).fetchall()
        except sqlite3.OperationalError:
            comparisons = []

    floor_bad = [row for row in allocations if int(row["floor_feasible"] or 0) == 0 or int(row["per_ue_floor_violation_count"] or 0) > 0]
    result["floor_violations"] = len(floor_bad)
    consecutive_bad = 0
    for row in allocations:
        bad = int(row["floor_feasible"] or 0) == 0 or int(row["per_ue_floor_violation_count"] or 0) > 0
        if bad:
            consecutive_bad += 1
        else:
            break
    result["critical_streak"] = consecutive_bad
    deltas = [float(row["score_delta"] or 0.0) for row in comparisons]
    if deltas:
        result["avg_score_delta"] = sum(deltas) / len(deltas)
    if consecutive_bad >= 3:
        result.update({"rollback": True, "reason": "3 violações consecutivas do piso ARMD"})
    elif len(deltas) >= window and result["avg_score_delta"] < -0.01:
        result.update({"rollback": True, "reason": "degradação média do score na janela de 30 decisões"})
    else:
        result["reason"] = "guardas sem degradação crítica"
    return result


def valid_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        quality = row.get("collection_quality") or {}
        if (
            quality.get("valid_for_training") is True
            and quality.get("collector_mode") == "pdcp_real"
            and bool(quality.get("pdcp_real"))
            and float(quality.get("proxy_latency_sample_count", 0) or 0) == 0
            and bool(quality.get("metric_alignment_valid"))
            and not bool(quality.get("sim_reset"))
            and float(quality.get("current_metric_skew_s", 0) or 0) <= 6.0
            and float(quality.get("next_metric_skew_s", 0) or 0) <= 6.0
            and row.get("next_metrics") is not None
            and row.get("judge_feedback") is not None
        ):
            rows.append(row)
    return rows


def _state_from_stage(stage: Any, fallback: Any = "ALLOWED") -> str:
    name = str(stage or "").strip().lower()
    if "blocked" in name:
        return "BLOCKED"
    if "conditional" in name:
        return "CONDITIONAL"
    if "allowed" in name or "recovery" in name:
        return "ALLOWED"
    value = str(fallback or "ALLOWED").strip().upper()
    return value if value in {"ALLOWED", "CONDITIONAL", "BLOCKED"} else "ALLOWED"


def _append_state_context(vector: Any, operating_state: str, target_dim: int) -> bool:
    values = list(vector or [])
    if len(values) == target_dim:
        return True
    if target_dim == 13 and len(values) == 10:
        vector[:] = values + [
            1.0 if operating_state == "ALLOWED" else 0.0,
            1.0 if operating_state == "CONDITIONAL" else 0.0,
            1.0 if operating_state == "BLOCKED" else 0.0,
        ]
        return True
    return False


def normalize_state_contract(row: dict[str, Any], du_state_dim: int, global_state_dim: int) -> bool:
    """Make legacy 10-D replay rows compatible with the active 13-D actor.

    The old historical trace is intentionally preserved on disk.  Its three
    operating-state indicators are reconstructed from the controlled stage
    only in the derived replay, so a failed or mixed-dimension update cannot
    silently instantiate actors with the wrong input shape.
    """
    current_state = _state_from_stage(
        row.get("scenario_stage"), (row.get("decision") or {}).get("decision")
    )
    next_state = _state_from_stage(
        row.get("next_scenario_stage"), current_state
    )
    global_state = row.get("global_state") or {}
    next_global_state = row.get("next_global_state") or {}
    if not _append_state_context(global_state["state_vector"], current_state, global_state_dim):
        return False
    if not _append_state_context(next_global_state["state_vector"], next_state, global_state_dim):
        return False
    row["global_state"] = global_state
    row["next_global_state"] = next_global_state
    for key, operating_state in (("du_states", current_state), ("next_du_states", next_state)):
        states = row.get(key) or []
        if not states:
            return False
        for du in states:
            if not _append_state_context(du.get("state_vector"), operating_state, du_state_dim):
                return False
    return True


def _checkpoint_dimensions(checkpoint: Path) -> tuple[int, int, int]:
    meta = load_json(checkpoint / "tasam_marl_checkpoint_meta.json")
    return (
        int(meta.get("du_count", 0) or 0),
        int(meta.get("du_state_dim", 0) or 0),
        int(meta.get("global_state_dim", 0) or 0),
    )


def export_recent(args: argparse.Namespace, trace: Path, summary: Path) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "scripts/export_tasam_article_dataset.py"),
        "--db", str(args.db),
        "--output-jsonl", str(trace),
        "--summary-json", str(summary),
        "--max-step-gap-s", "6",
        "--max-sim-reset-gap-s", "1",
        "--limit", str(args.export_limit),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)


def build_replay(
    historical: Path,
    recent: Path,
    output: Path,
    seed: int,
    max_rows: int,
    *,
    expected_du_count: int = 0,
    expected_du_state_dim: int = 0,
    expected_global_state_dim: int = 0,
) -> dict[str, int]:
    old = valid_rows(historical)
    new = valid_rows(recent)
    if expected_du_count and expected_du_state_dim and expected_global_state_dim:
        old = [
            row for row in old
            if len(row.get("du_states") or []) == expected_du_count
            and normalize_state_contract(row, expected_du_state_dim, expected_global_state_dim)
        ]
        new = [
            row for row in new
            if len(row.get("du_states") or []) == expected_du_count
            and normalize_state_contract(row, expected_du_state_dim, expected_global_state_dim)
        ]
    target = min(max_rows, len(old) + len(new))
    old_target = min(len(old), int(round(target * 0.70)))
    new_target = min(len(new), target - old_target)
    if old_target + new_target < target:
        extra_old = min(len(old) - old_target, target - old_target - new_target)
        old_target += max(extra_old, 0)
    rng = random.Random(seed)
    old_choice = rng.sample(old, old_target) if old_target < len(old) else old[:]
    new_choice = new[-new_target:] if new_target else []
    mixed = old_choice + new_choice
    rng.shuffle(mixed)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in mixed:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"historical_valid": len(old), "recent_valid": len(new), "historical_used": len(old_choice), "recent_used": len(new_choice), "total": len(mixed)}


def checkpoint_entry(checkpoint: Path, label: str, readiness: str = "shadow_ready") -> dict[str, Any]:
    required = [checkpoint / name for name in ("tasam_marl_actors.pt", "tasam_marl_checkpoint_meta.json", "tasam_marl_summary.json")]
    if not all(path.is_file() for path in required):
        raise RuntimeError(f"checkpoint incompleto: {checkpoint}")
    summary = load_json(required[2])
    meta = load_json(required[1])
    return {
        "trace_jsonl": str(summary.get("trace_jsonl", "")),
        "epochs": int(summary.get("completed_epochs", summary.get("epochs", 0)) or 0),
        "du_count": int(meta.get("du_count", 0) or 0),
        "final_metrics": summary.get("final_metrics") or {},
        "readiness": readiness,
        "promote_shadow": True,
        "promote_control_candidate": readiness == "control_candidate",
        "run_dir": str(checkpoint.resolve()),
        "summary_path": str((checkpoint / "tasam_marl_summary.json").resolve()),
        "ab_label": label,
    }


def publish_manifests(args: argparse.Namespace, state: dict[str, Any], checkpoint: Path, readiness: str = "shadow_ready") -> None:
    """Publish the checkpoint that is actually allowed to control GreenRAN.

    A pending candidate is intentionally not published here.  Candidate
    evaluation has its own manifest so the active TA-SAM policy can keep
    allocating resources while the next policy is scored offline.
    """
    entry = checkpoint_entry(checkpoint, f"tasam_online_seed45_{state.get('updates_completed', 0):04d}", readiness)
    eval_payload = {
        "schema": "greenran.tasam_online_candidate_evaluation.v1",
        "best_run": entry,
        "evaluated_runs": [entry],
        "online": {"active_checkpoint": str(checkpoint.resolve()), "updated_at": int(time.time())},
    }
    save_json(args.eval_manifest, eval_payload)
    gate_payload = {
        "schema": "greenran.tasam_online_control_gate.v1",
        "tasam_eval_path": str(args.eval_manifest),
        "gate": {
            "status": state.get("stage", "shadow"),
            "allow_shadow": True,
            "allow_control_trial": float(state.get("rollout_fraction", 0.0) or 0.0) > 0.0,
            "manual_approval_required": False,
            "training_readiness": readiness,
            "runtime_readiness": "online_full_control" if full_control_mode() else "online_guarded",
            "policy_id": f"ta_sam_online_seed45:{checkpoint.name}",
            "run_dir": str(checkpoint.resolve()),
            "reasons": (
                ["TA-SAM owns every valid online action; observed error is training feedback"]
                if full_control_mode() else
                ["online candidate is versioned and guarded by ARMD", "rApp Judge remains final authority"]
            ),
        },
    }
    save_json(args.control_gate, gate_payload)


def publish_candidate_shadow_manifest(
    args: argparse.Namespace,
    state: dict[str, Any],
    candidate: Path,
    evaluation: dict[str, Any] | None = None,
) -> None:
    """Publish candidate-only evidence without changing the active policy."""
    entry = checkpoint_entry(
        candidate,
        f"tasam_online_seed45_candidate_{state.get('updates_completed', 0):04d}",
        "shadow_ready",
    )
    save_json(
        args.candidate_eval_manifest,
        {
            "schema": "greenran.tasam_online_candidate_shadow.v1",
            "active_checkpoint": str(Path(state["active_checkpoint"]).resolve()),
            "candidate": entry,
            "runtime_actuation": False,
            "evaluation": evaluation or {},
            "shadow_window": {
                "started_decisions": int(state.get("candidate_shadow_started_decisions", 0) or 0),
                "until_decisions": int(state.get("candidate_shadow_until_decisions", 0) or 0),
                "promotion_window": args.promotion_window,
            },
        },
    )


def evaluate_candidate_shadow(
    args: argparse.Namespace,
    state: dict[str, Any],
    active: Path,
    candidate: Path,
    trace: Path,
) -> tuple[dict[str, Any], Path]:
    """Evaluate active and candidate policies on the same real-PDCP trace."""
    output = candidate.parent / "candidate_shadow_evaluation.json"
    if not trace.exists():
        state["candidate_shadow_evaluation_error"] = f"trace ausente: {trace}"
        return {}, output
    try:
        subprocess.run(
            [
                str(args.train_python),
                str(ROOT / "scripts/evaluate_tasam_same_trace_shadow.py"),
                "--trace", str(trace),
                "--candidate", f"active={active}",
                "--candidate", f"candidate={candidate}",
                "--output", str(output),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                # Candidate replay must use the manifests written by the
                # evaluator script, not the runtime bootstrap checkpoint.
                "GREENRAN_TASAM_CHECKPOINT": "",
                "GREENRAN_TASAM_REQUIRE_CHECKPOINT": "0",
            },
        )
        state.pop("candidate_shadow_evaluation_error", None)
        return load_json(output), output
    except (subprocess.CalledProcessError, OSError) as exc:
        state["candidate_shadow_evaluation_error"] = str(exc)
        return {}, output


def save_rollout_manifest(args: argparse.Namespace, state: dict[str, Any], **extra: Any) -> None:
    """Persist active rollout and candidate-shadow state atomically."""
    rollout = {
        "stage": state.get("stage", "shadow"),
        "fraction": float(state.get("rollout_fraction", 0.0) or 0.0),
        "checkpoint": state.get("active_checkpoint", ""),
    }
    candidate = state.get("candidate_checkpoint")
    if candidate:
        rollout["candidate"] = candidate
        rollout["candidate_shadow"] = True
        rollout["candidate_shadow_started_decisions"] = int(
            state.get("candidate_shadow_started_decisions", 0) or 0
        )
        rollout["candidate_shadow_until_decisions"] = int(
            state.get("candidate_shadow_until_decisions", 0) or 0
        )
    rollout.update(extra)
    save_json(args.rollout_manifest, {"schema": "greenran.tasam_online_rollout.v1", "rollout": rollout})


def migrate_legacy_candidate_shadow_state(
    args: argparse.Namespace, state: dict[str, Any], decision_count: int
) -> bool:
    """Repair the old full->shadow transition without resetting counters."""
    if (
        state.get("stage") != "shadow"
        or not state.get("candidate_checkpoint")
        or not state.get("active_checkpoint")
        or state.get("candidate_checkpoint") == state.get("active_checkpoint")
    ):
        return False
    started = int(
        state.get("candidate_shadow_started_decisions")
        or state.get("stage_started_decisions")
        or decision_count
    )
    state["stage"] = "full"
    state["rollout_fraction"] = 1.0
    state["candidate_shadow_started_decisions"] = started
    state["candidate_shadow_until_decisions"] = started + args.promotion_window
    state["candidate_shadow_migrated"] = True
    state["status"] = "running"
    return True


def advance_rollout(args: argparse.Namespace, state: dict[str, Any], decision_count: int, guard: dict[str, Any]) -> None:
    if full_control_mode():
        # Full-control training never transitions to rollback, shadow or
        # canary.  A completed candidate becomes the active policy directly.
        if state.get("candidate_checkpoint"):
            state["last_good_checkpoint"] = state.get("active_checkpoint")
            state["active_checkpoint"] = state["candidate_checkpoint"]
            state["candidate_checkpoint"] = ""
            state["candidate_promoted"] = True
            state["stage_started_decisions"] = decision_count
            state["candidate_promoted_directly"] = True
            publish_manifests(args, state, Path(state["active_checkpoint"]), "control_candidate")
        state["stage"] = "full"
        state["rollout_fraction"] = 1.0
        state["status"] = "running"
        state["rollback"] = False
        state["rollback_suppressed"] = bool(guard.get("rollback"))
        state["rollback_suppression_reason"] = guard.get("reason", "") if guard.get("rollback") else ""
        save_rollout_manifest(args, state, stage="full", fraction=1.0, reason="tasam_full_control")
        return
    if guard.get("rollback"):
        if state.get("last_good_checkpoint"):
            state["active_checkpoint"] = state["last_good_checkpoint"]
        state["candidate_checkpoint"] = ""
        state["candidate_promoted"] = False
        state["stage"] = "rollback"
        state["rollout_fraction"] = 0.0
        state["rollback_count"] = int(state.get("rollback_count", 0) or 0) + 1
        state["rollback_reason"] = guard.get("reason", "guard failure")
        state["stage_started_decisions"] = decision_count
        publish_manifests(args, state, Path(state["active_checkpoint"]), "shadow_ready")
        save_rollout_manifest(args, state, stage="rollback", fraction=0.0, reason=state["rollback_reason"])
        return
    if state.get("stage") == "rollback":
        return
    if not state.get("candidate_checkpoint") and not state.get("candidate_promoted", False):
        return
    # A candidate created after the previous policy reached full rollout is
    # evaluated in parallel.  The active policy remains at full rollout; only
    # the candidate is shadowed.  This is the key invariant that prevents the
    # online learner from turning off its own acting policy.
    if state.get("stage") == "full" and state.get("candidate_checkpoint") and not state.get("candidate_promoted", False):
        if "candidate_shadow_started_decisions" not in state:
            state["candidate_shadow_started_decisions"] = decision_count
            state["candidate_shadow_until_decisions"] = decision_count + args.promotion_window
            save_rollout_manifest(args, state)
            return
        shadow_started = int(state.get("candidate_shadow_started_decisions", decision_count) or decision_count)
        if decision_count - shadow_started < args.promotion_window:
            return
        if state.get("candidate_shadow_evaluation_error"):
            # Never promote a candidate whose same-trace real-PDCP shadow
            # evaluation failed.  The active TA-SAM policy remains live.
            return
        state["last_good_checkpoint"] = state.get("active_checkpoint")
        state["active_checkpoint"] = state["candidate_checkpoint"]
        state["candidate_checkpoint"] = ""
        state["candidate_promoted"] = True
        state.pop("candidate_shadow_started_decisions", None)
        state.pop("candidate_shadow_until_decisions", None)
        state["stage"] = "full"
        state["rollout_fraction"] = 1.0
        state["stage_started_decisions"] = decision_count
        publish_manifests(args, state, Path(state["active_checkpoint"]), "control_candidate")
        save_rollout_manifest(args, state)
        return
    if decision_count - int(state.get("stage_started_decisions", 0) or 0) < args.promotion_window:
        return
    current = next((idx for idx, (name, _) in enumerate(STAGES) if name == state.get("stage")), 0)
    if current >= len(STAGES) - 1:
        return
    next_name, next_fraction = STAGES[current + 1]
    candidate = state.get("candidate_checkpoint")
    if candidate:
        state["last_good_checkpoint"] = state.get("active_checkpoint")
        state["active_checkpoint"] = candidate
        state["candidate_checkpoint"] = ""
        state["candidate_promoted"] = True
    state["stage"] = next_name
    state["rollout_fraction"] = next_fraction
    state["stage_started_decisions"] = decision_count
    # Publish after the stage transition so the evaluation gate and rollout
    # manifest describe the same effective policy.  In particular, never
    # publish an empty candidate path after promotion to full rollout.
    publish_manifests(
        args,
        state,
        Path(state.get("candidate_checkpoint") or state["active_checkpoint"]),
        "control_candidate" if next_fraction > 0.0 else "shadow_ready",
    )
    save_rollout_manifest(args, state)


def run_update(args: argparse.Namespace, state: dict[str, Any], snapshot_count: int) -> dict[str, Any]:
    update_id = int(state.get("updates_completed", 0) or 0) + 1
    update_dir = args.state_dir / "candidates" / f"candidate_{update_id:04d}"
    update_dir.mkdir(parents=True, exist_ok=True)
    recent_trace = update_dir / "recent_trace.jsonl"
    export_summary = update_dir / "recent_export_summary.json"
    export_recent(args, recent_trace, export_summary)
    active = Path(state["active_checkpoint"])
    du_count, du_state_dim, global_state_dim = _checkpoint_dimensions(active)
    replay = build_replay(
        args.historical_trace,
        recent_trace,
        args.state_dir / "replay_buffer" / f"replay_update_{update_id:04d}.jsonl",
        args.seed + update_id,
        args.replay_rows,
        expected_du_count=du_count,
        expected_du_state_dim=du_state_dim,
        expected_global_state_dim=global_state_dim,
    )
    if replay["total"] < args.min_trainable_transitions:
        return {"status": "waiting_dataset", "update_id": update_id, "snapshot_count": snapshot_count, "replay": replay}
    candidate = update_dir / "tasam_selective"
    train_python = str(args.train_python if args.train_python.exists() else sys.executable)
    cmd = [
        train_python, str(ROOT / "drlexp/training/train_tasam_marl.py"),
        "--trace-jsonl", str(args.state_dir / "replay_buffer" / f"replay_update_{update_id:04d}.jsonl"),
        "--output-dir", str(candidate), "--epochs", str(args.epochs_per_update),
        "--trainer-backend", "article_sac", "--sam-mode", "tasam_selective",
        "--actor-sam-rho", "0.5", "--actor-sam-rho-final", "0.01",
        "--critic-sam-rho", "0.5", "--critic-sam-rho-final", "0.01",
        "--td-var-threshold", "0.01", "--warmup-epochs", "1", "--bc-weight", "0.0",
        "--value-weight", "0.0", "--gamma", "0.99", "--tau", "0.01", "--alpha-init", "0.03",
        "--target-entropy-scale", "1.0", "--batch-size", "128", "--seed", str(args.seed),
        "--article-hidden", "--activation", "tanh", "--init-checkpoint-dir", str(active),
        "--checkpoint-every", str(args.epochs_per_update),
    ]
    try:
        subprocess.run(cmd, cwd=ROOT, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        # Do not let one candidate failure stop the real collection.  Mark
        # this snapshot watermark so the next attempt waits for a fresh
        # online batch instead of retrying every polling interval.
        state["last_update_snapshot_count"] = snapshot_count
        state["last_update_error"] = str(exc)
        return {"status": "training_failed", "update_id": update_id, "snapshot_count": snapshot_count, "replay": replay, "error": str(exc)}
    state["candidate_checkpoint"] = str(candidate.resolve())
    state["candidate_promoted"] = False
    state["updates_completed"] = update_id
    state["last_update_snapshot_count"] = snapshot_count
    state["last_update_at"] = int(time.time())
    state["last_replay"] = replay
    state.pop("last_update_error", None)
    state["history"].append({"update_id": update_id, "candidate": str(candidate.resolve()), "replay": replay, "snapshot_count": snapshot_count})
    if full_control_mode():
        state["last_good_checkpoint"] = str(active.resolve())
        state["active_checkpoint"] = str(candidate.resolve())
        state["candidate_checkpoint"] = ""
        state["candidate_promoted"] = True
        state["stage"] = "full"
        state["rollout_fraction"] = 1.0
        state["candidate_promoted_directly"] = True
        publish_manifests(args, state, candidate, "control_candidate")
        save_rollout_manifest(args, state, stage="full", fraction=1.0, reason="tasam_full_control candidate promoted")
        return {"status": "candidate_promoted", "update_id": update_id, "candidate": str(candidate), "replay": replay}
    if state.get("stage") == "full":
        state["candidate_shadow_started_decisions"] = count_decisions(args.db)
        state["candidate_shadow_until_decisions"] = (
            state["candidate_shadow_started_decisions"] + args.promotion_window
        )
    shadow_evaluation, shadow_evaluation_path = evaluate_candidate_shadow(
        args, state, active, candidate, recent_trace
    )
    state["candidate_shadow_evaluation"] = str(shadow_evaluation_path.resolve())
    publish_manifests(args, state, active, "control_candidate")
    publish_candidate_shadow_manifest(args, state, candidate, shadow_evaluation)
    return {"status": "candidate_ready", "update_id": update_id, "candidate": str(candidate), "replay": replay}


def normalize(args: argparse.Namespace) -> argparse.Namespace:
    args.state_dir = Path(args.state_dir)
    args.db = Path(args.db) if args.db else args.state_dir / "rapp_data_lake.db"
    args.historical_trace = Path(args.historical_trace)
    args.train_python = Path(args.train_python)
    args.rollout_manifest = args.state_dir / "online_rollout.json"
    args.eval_manifest = args.state_dir / "online_eval_manifest.json"
    args.control_gate = args.state_dir / "online_control_gate.json"
    args.candidate_eval_manifest = args.state_dir / "online_candidate_eval_manifest.json"
    args.status_json = args.state_dir / "online_status.json"
    args.state_json = args.state_dir / "online_state.json"
    return args


def resolve_train_python(candidate: Path) -> Path:
    """Use an interpreter that can actually import PyTorch."""
    candidates = [candidate, Path("/usr/bin/python3"), Path(sys.executable)]
    seen: set[str] = set()
    for item in candidates:
        item = Path(item)
        if str(item) in seen or not item.exists():
            continue
        seen.add(str(item))
        probe = subprocess.run(
            [str(item), "-c", "import torch"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            return item
    raise SystemExit("nenhum interpretador disponível consegue importar PyTorch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--db", default=None)
    parser.add_argument("--historical-trace", default=str(HISTORICAL_TRACE))
    parser.add_argument("--checkpoint", default=str(BASE_CHECKPOINT))
    parser.add_argument("--train-python", default=str(PYTHON))
    parser.add_argument("--min-new-snapshots", type=int, default=500)
    parser.add_argument("--min-trainable-transitions", type=int, default=1500)
    parser.add_argument("--replay-rows", type=int, default=1500)
    parser.add_argument("--epochs-per-update", type=int, default=3)
    parser.add_argument("--promotion-window", type=int, default=30)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--export-limit", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=45)
    parser.add_argument(
        "--resume-after-rollback",
        action="store_true",
        help="retoma uma rodada em rollback somente em shadow, sem promover o candidato rejeitado",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--retry-failed-update",
        action="store_true",
        help="reabre uma tentativa de update que falhou, sem reiniciar o runtime de coleta",
    )
    args = normalize(parser.parse_args())
    args.train_python = resolve_train_python(args.train_python)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    args.checkpoint = Path(args.checkpoint).resolve()
    if not args.checkpoint.is_dir():
        raise SystemExit(f"checkpoint não encontrado: {args.checkpoint}")
    state = load_json(args.state_json, default={})
    if not state:
        initial_stage, initial_fraction = initial_rollout()
        state = {
            "schema": "greenran.tasam_online_control_state.v1",
            "training_mode": "online",
            "replay_source": "live_sqlite",
            "min_trainable_transitions": args.min_trainable_transitions,
            "status": "running" if initial_fraction > 0.0 else "shadow",
            "stage": initial_stage,
            "rollout_fraction": initial_fraction,
            "active_checkpoint": str(args.checkpoint),
            "candidate_checkpoint": "",
            "candidate_promoted": False,
            "updates_completed": 0,
            "last_update_snapshot_count": 0,
            "stage_started_decisions": 0,
            "rollback_count": 0,
            "reward_enabled": True,
            "reward_source": "observed_real_metrics",
            "reward_mode": "continuous_observed_error",
            "full_control": full_control_mode(),
            "history": [],
        }
    elif migrate_legacy_candidate_shadow_state(args, state, count_decisions(args.db)):
        # Keep the active checkpoint and counters; the candidate remains in
        # the separate shadow lane until its existing window completes.
        pass
    elif (
        (state.get("candidate_checkpoint") and not state.get("candidate_promoted", False) and state.get("stage") != "shadow")
        or (state.get("stage") == "canary_10" and not state.get("candidate_promoted", False) and state.get("last_good_checkpoint"))
    ):
        # A previous controller could have advanced the fraction before the
        # candidate had its required post-publication observation window.
        # Re-arm that candidate in shadow; the next 30 decisions start here.
        if not state.get("candidate_checkpoint") and state.get("active_checkpoint") != state.get("last_good_checkpoint"):
            state["candidate_checkpoint"] = state.get("active_checkpoint", "")
            state["active_checkpoint"] = state.get("last_good_checkpoint")
        if state.get("stage") == "full":
            state["stage"] = "full"
            state["rollout_fraction"] = 1.0
            state["candidate_shadow_started_decisions"] = count_decisions(args.db)
            state["candidate_shadow_until_decisions"] = state["candidate_shadow_started_decisions"] + args.promotion_window
            state["stage_started_decisions"] = count_decisions(args.db)
            save_rollout_manifest(args, state)
        else:
            state["stage"] = "shadow"
            state["rollout_fraction"] = 0.0
            state["stage_started_decisions"] = count_decisions(args.db)
            save_rollout_manifest(args, state)
    elif state.get("stage") == "rollback" and args.resume_after_rollback:
        # A rollback is sticky by default.  Explicit recovery keeps the last
        # good policy active, discards the rejected candidate from rollout,
        # and reopens only a shadow window so its negative feedback can enter
        # the next 70/30 replay update without touching live allocation.
        state["stage"] = "shadow"
        state["rollout_fraction"] = 0.0
        state["candidate_checkpoint"] = ""
        state["candidate_promoted"] = False
        state["status"] = "running"
        state["recovery_after_rollback"] = True
        state["recovery_started_decisions"] = count_decisions(args.db)
        state["recovery_grace_until_decisions"] = state["recovery_started_decisions"] + args.promotion_window
        save_json(
            args.rollout_manifest,
            {
                "schema": "greenran.tasam_online_rollout.v1",
                "rollout": {
                    "stage": "shadow",
                    "fraction": 0.0,
                    "checkpoint": state.get("active_checkpoint"),
                    "reason": "explicit recovery after rollback; candidate remains rejected",
                },
            },
        )
    # If recovery or a previous controller completed an update but lost the
    # candidate pointer while persisting the rollback state, recover the last
    # versioned candidate from history.  It remains shadow-only until the
    # normal promotion windows pass.
    if state.get("stage") == "shadow" and not state.get("candidate_checkpoint"):
        history = state.get("history") or []
        if history:
            latest_candidate = Path(str(history[-1].get("candidate", "")))
            if (latest_candidate / "tasam_marl_actors.pt").is_file():
                state["candidate_checkpoint"] = str(latest_candidate.resolve())
                state["candidate_promoted"] = False
    state.setdefault("reward_enabled", True)
    state.setdefault("reward_source", "observed_real_metrics")
    state.setdefault("reward_mode", "continuous_observed_error")
    state["full_control"] = full_control_mode()
    if full_control_mode():
        state["stage"] = "full"
        state["rollout_fraction"] = 1.0
        state["status"] = "running"
        state["rollback"] = False
        candidate_path = Path(str(state.get("candidate_checkpoint", "")))
        if candidate_path and (candidate_path / "tasam_marl_actors.pt").is_file():
            state["last_good_checkpoint"] = state.get("active_checkpoint")
            state["active_checkpoint"] = str(candidate_path.resolve())
            state["candidate_checkpoint"] = ""
            state["candidate_promoted"] = True
            state["candidate_promoted_directly"] = True
        else:
            state["candidate_checkpoint"] = ""
    # A controlled restart is a new live session, not a stopped one.  Do not
    # leave the previous shutdown marker in the online status contract.
    state.pop("stopped_at", None)
    if args.retry_failed_update and state.get("last_update_error"):
        # The retry is explicit and one-shot: lower the watermark just enough
        # to re-run the failed batch with the corrected state contract.
        state["last_update_snapshot_count"] = max(
            0, count_snapshots(args.db) - args.min_new_snapshots
        )
        state.pop("last_update_error", None)
    manifest_checkpoint = Path(state["active_checkpoint"])
    publish_manifests(
        args,
        state,
        manifest_checkpoint,
        "control_candidate" if float(state.get("rollout_fraction", 0.0) or 0.0) > 0.0 else "shadow_ready",
    )
    if state.get("candidate_checkpoint") and not full_control_mode():
        candidate_path = Path(state["candidate_checkpoint"])
        if not state.get("candidate_shadow_evaluation"):
            candidate_trace = candidate_path.parent / "recent_trace.jsonl"
            evaluation, evaluation_path = evaluate_candidate_shadow(
                args,
                state,
                Path(state["active_checkpoint"]),
                candidate_path,
                candidate_trace,
            )
            state["candidate_shadow_evaluation"] = str(evaluation_path.resolve())
        else:
            evaluation = load_json(Path(state["candidate_shadow_evaluation"]))
        publish_candidate_shadow_manifest(
            args,
            state,
            candidate_path,
            evaluation,
        )
    save_rollout_manifest(args, state)

    while True:
        snapshots = count_snapshots(args.db)
        decisions = count_decisions(args.db)
        guard = runtime_guard(args.db, 30)
        if full_control_mode():
            guard = {
                **guard,
                "rollback": False,
                "rollback_suppressed": bool(guard.get("rollback")),
                "reason": (
                    f"rollback suprimido em tasam_full_control: {guard.get('reason', '')}"
                    if guard.get("rollback") else "full-control training; guardas apenas auditivas"
                ),
            }
        grace_until = int(state.get("recovery_grace_until_decisions", 0) or 0)
        if state.get("stage") == "shadow" and grace_until and decisions <= grace_until:
            # Do not immediately re-trigger the old rollback window.  The
            # recovery window is shadow-only and exists to let the negative
            # feedback reach the next replay update before normal guards resume.
            guard = {
                **guard,
                "rollback": False,
                "reason": f"janela de recuperação shadow até decisão {grace_until}",
            }
        state["snapshot_count"] = snapshots
        state["decision_count"] = decisions
        state["guard"] = guard
        if guard.get("rollback") and not full_control_mode():
            state["status"] = "rollback"
        elif state.get("stage") != "rollback":
            state["status"] = "running"
        update_result = {"status": "idle"}
        if (
            state.get("stage") != "rollback"
            and snapshots - int(state.get("last_update_snapshot_count", 0) or 0) >= args.min_new_snapshots
            and args.historical_trace.exists()
        ):
            update_result = run_update(args, state, snapshots)
        advance_rollout(args, state, decisions, guard)
        state["last_update_result"] = update_result
        state["updated_at"] = int(time.time())
        save_json(args.state_json, state)
        save_json(args.status_json, {**state, "active_checkpoint": state.get("active_checkpoint"), "candidate_checkpoint": state.get("candidate_checkpoint", ""), "rollout": {"stage": state.get("stage"), "fraction": state.get("rollout_fraction", 0.0)}, "last_update_result": update_result})
        print(json.dumps({"status": state["status"], "snapshots": snapshots, "decisions": decisions, "stage": state.get("stage"), "fraction": state.get("rollout_fraction"), "update": update_result.get("status"), "guard": guard.get("reason")}, ensure_ascii=False), flush=True)
        if args.once:
            return 0
        time.sleep(max(args.poll_seconds, 5.0))


if __name__ == "__main__":
    raise SystemExit(main())
