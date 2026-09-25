#!/usr/bin/env python3
"""Versioned TA-SAM online controller for the real GreenRAN runtime.

The active checkpoint is never trained in place.  Live PDCP transitions are
exported into a replay, a short TA-SAM candidate update is trained from the
active actors, and the candidate is exposed to the rApp through an atomic
manifest.  The default replay preserves the historical/recent mix used by
older controlled rounds; ``--online-only`` disables that historical input and
uses only transitions from the current SQLite Data Lake.  Rollout is
controlled by a separate atomic fraction file read by the rApp on every
decision.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import math
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
sys.path.insert(0, str(ROOT / "src"))

from tasam_learning_meter import build_learning_meter
from greenran_v2x_adaptive_reward import REWARD_CONTRACT
from greenran_v2x_window90 import validate_online_transition

BASE_CHECKPOINT = ROOT / "runs/tasam_training_weak_reinforced_20260812/training_final/seed_0045/tasam_selective"
HISTORICAL_TRACE = ROOT / "runs/greenran_tasam_e2_active_20260827_v7/final_dataset/tasam_article_trace_final.jsonl"
PYTHON = Path("/usr/bin/python3")
STAGES = (("shadow", 0.0), ("canary_10", 0.10), ("canary_25", 0.25), ("canary_50", 0.50), ("full", 1.0))
ECONOMIC_REPLAY_SCHEMA = "greenran.tasam.economic_replay.v2"
ECONOMIC_ACTION_CONTRACTS = {"applied_action_v2", "economic_action_v3_per_du_sleep"}
# The action heads are ordered against this fixed topology.  Reading an
# arbitrary SQL order would silently attach a DU action to the wrong cell.
CANONICAL_DU_IDS = (
    "du_camera_edge",
    "du_sensor_mixed",
    "du_vehicle_edge",
)


def full_control_mode() -> bool:
    mode = os.environ.get("GREENRAN_TASAM_ADVISOR_MODE", "").strip().lower()
    return mode in {"tasam_full_control", "tasam-full-control"} or os.environ.get(
        "GREENRAN_TASAM_PILOT_FULL_ROLLOUT", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}


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


def publish_learning_meter(args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    """Publish a read-only learning/economic meter for the live watcher."""
    try:
        # Connection.__exit__ commits/rolls back but does not close the
        # sqlite connection. This function runs every polling cycle, so close
        # it explicitly to keep long simulations below the FD limit.
        with closing(sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            # Delayed economic feedback is authoritative only after it has
            # reached the durable transition table. The decisions table is
            # a proposal/audit table and may not contain the updated
            # alignment and realized-effect fields yet.
            transition_columns = {
                str(row[1]) for row in conn.execute(
                    "PRAGMA table_info(tasam_economic_transition_history)"
                )
            }
            rows = []
            if transition_columns:
                selected = [
                    "decision_id", "economic_application_status",
                    "economic_transition_eligible", "economic_training_eligible",
                    "economic_promotion_eligible", "realized_energy_saving_fraction",
                    "realized_allocation_saving_fraction", "tasam_online_reward",
                    "tasam_energy_reward", "tasam_allocation_reward", "tasam_sla_penalty",
                    "calibration_version", "transition_json",
                ]
                selected = [name for name in selected if name in transition_columns]
                for raw in conn.execute(
                    f"SELECT {', '.join(selected)} "
                    "FROM tasam_economic_transition_history ORDER BY decision_id"
                ):
                    row = dict(raw)
                    try:
                        payload = json.loads(str(row.get("transition_json") or "{}"))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        payload = {}
                    decision = payload.get("decision_provenance") or {}
                    feedback = payload.get("judge_feedback") or {}
                    action = payload.get("economic_action") or {}
                    applied = action.get("applied") or {}
                    live = action.get("live_candidate") or {}
                    row.update({
                        "economic_action_json": json.dumps(action, ensure_ascii=False, sort_keys=True),
                        "tasam_checkpoint_valid": decision.get("tasam_checkpoint_valid"),
                        "tasam_fallback_used": decision.get("tasam_fallback_used"),
                        "tasam_evidence_valid": decision.get("tasam_evidence_valid", True),
                        "tasam_action_applied": feedback.get("tasam_action_applied", decision.get("tasam_action_applied")),
                        "ta_sam_actuation_applied": feedback.get("tasam_action_applied", decision.get("tasam_action_applied")),
                        "economic_action_alignment_valid": feedback.get("economic_action_alignment_valid"),
                        "economic_safety_isolated": feedback.get("economic_safety_isolated", action.get("economic_safety_isolated")),
                        "armd_safety_level": feedback.get("armd_safety_level", action.get("armd_safety_level")),
                        "topology_valid": feedback.get("topology_valid", action.get("topology_valid")),
                        "live_power_w": live.get("power_w"),
                        "shadow_power_w": applied.get("power_w"),
                        "live_total_allocation": live.get("total_allocation"),
                        "applied_total_allocation": applied.get("total_allocation"),
                        "calibration_version": (
                            row.get("calibration_version")
                            or feedback.get("energy_model_version")
                            or action.get("energy_model_version")
                            or decision.get("energy_model_version")
                            or ""
                        ),
                    })
                    rows.append(row)
    except sqlite3.Error:
        rows = []
    meter = build_learning_meter(
        rows,
        state,
        target_transitions=int(state.get("min_economic_transitions", 180) or 180),
    )
    meter.update({
        "campaign_dir": str(args.state_dir.parent.resolve()),
        "adaptation_dir": str(args.state_dir.resolve()),
        "decision_count": len(rows),
        "active_checkpoint": state.get("active_checkpoint", ""),
        "updated_at": int(time.time()),
    })
    save_json(args.state_dir / "learning_meter.json", meter)
    return meter


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def experience_id(row: dict[str, Any]) -> str:
    return str(
        row.get("tasam_experience_id")
        or row.get("decision_id")
        or row.get("snapshot_sequence_id")
        or row.get("transition_id")
        or f"{row.get('topology_id', '')}|{row.get('timestamp', '')}|{row.get('next_timestamp', '')}"
    )


def _experience_stats(row: dict[str, Any], confusion: dict[str, int], strong_credits: list[float], training_rewards: list[float]) -> None:
    feedback = row.get("judge_feedback") or {}
    predicted = str(
        row.get("tasam_predicted_verdict")
        or feedback.get("tasam_predicted_verdict")
        or "UNKNOWN"
    ).upper()
    observed = str(
        row.get("tasam_observed_verdict")
        or feedback.get("tasam_observed_verdict")
        or "UNKNOWN"
    ).upper()
    key = f"{predicted}->{observed}"
    confusion[key] = confusion.get(key, 0) + 1
    try:
        strong_credits.append(float(row.get("tasam_training_category_credit")))
    except (TypeError, ValueError):
        pass
    try:
        training_rewards.append(float(row.get("tasam_training_reward")))
    except (TypeError, ValueError):
        pass


def persist_experience_bank(bank: Path, source: Path, round_id: str) -> dict[str, Any]:
    """Append observed transitions without materializing the bank in RAM."""
    existing_ids: set[str] = set()
    unique_rows = 0
    source_rows = 0
    added = 0
    confusion: dict[str, int] = {}
    strong_credits: list[float] = []
    training_rewards: list[float] = []
    if bank.is_file():
        with bank.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                key = experience_id(row)
                if key in existing_ids:
                    continue
                existing_ids.add(key)
                unique_rows += 1
                _experience_stats(row, confusion, strong_credits, training_rewards)

    bank.parent.mkdir(parents=True, exist_ok=True)
    with bank.open("a", encoding="utf-8") as handle:
        for row in iter_valid_rows(source):
            source_rows += 1
            key = experience_id(row)
            if key in existing_ids:
                continue
            persisted = dict(row)
            persisted.update({
                "tasam_experience_id": key,
                "tasam_experience_source_round": round_id,
                "tasam_experience_source_status": "online",
            })
            handle.write(json.dumps(persisted, ensure_ascii=False, sort_keys=True) + "\n")
            existing_ids.add(key)
            unique_rows += 1
            added += 1
            _experience_stats(persisted, confusion, strong_credits, training_rewards)
    manifest = {
        "schema": "greenran.tasam_experience_bank.v1",
        "bank": str(bank.resolve()),
        "source_round": round_id,
        "source_status": "online",
        "source_rows_read": source_rows,
        "unique_transitions": unique_rows,
        "rows_added": added,
        "reward_fields": [
            "tasam_category_credit",
            "tasam_training_category_credit",
            "tasam_training_category_penalty",
            "tasam_training_reward",
            "tasam_continuous_reward",
        ],
        "category_confusion": dict(sorted(confusion.items())),
        "mean_training_category_credit": sum(strong_credits) / max(len(strong_credits), 1),
        "mean_training_reward": sum(training_rewards) / max(len(training_rewards), 1),
        "sha256": sha256_file(bank),
    }
    save_json(bank.with_suffix(".manifest.json"), manifest)
    return manifest


def count_snapshots(db: Path) -> int:
    if not db.exists():
        return 0
    with closing(sqlite3.connect(str(db))) as conn:
        try:
            return int(conn.execute("select count(*) from marl_global_state_history").fetchone()[0] or 0)
        except sqlite3.OperationalError:
            return 0


def count_decisions(db: Path) -> int:
    if not db.exists():
        return 0
    with closing(sqlite3.connect(str(db))) as conn:
        try:
            return int(conn.execute("select count(*) from decisions_history").fetchone()[0] or 0)
        except sqlite3.OperationalError:
            return 0


def runtime_guard(db: Path, window: int = 30) -> dict[str, Any]:
    """Check the hard online guards using only persisted runtime evidence."""
    result: dict[str, Any] = {
        "window": window,
        "floor_violations": 0,
        "infeasible_floor_constraints": 0,
        "critical_streak": 0,
        "avg_score_delta": None,
        "rollback": False,
        "reason": "no evidence yet",
    }
    if not db.exists():
        return result
    with closing(sqlite3.connect(str(db))) as conn:
        conn.row_factory = sqlite3.Row
        try:
            allocations = conn.execute(
                "select floor_feasible, per_ue_floor_violation_count, floor_total_ran, floor_total_ai, usable_budget "
                "from resource_allocation_history order by timestamp desc limit ?",
                (window,),
            ).fetchall()
        except sqlite3.OperationalError:
            allocations = []
        # Shadow comparisons are observational evidence only.  They must not
        # roll back a live policy: historically this query treated a
        # counterfactual shadow score as a realized failure and repeatedly
        # rolled the same run back.  Use only durable, native-confirmed,
        # economically eligible outcomes for a runtime safety guard.
        try:
            transition_columns = {
                str(row[1]) for row in conn.execute(
                    "PRAGMA table_info(tasam_economic_transition_history)"
                )
            }
            required = {
                "economic_application_status", "economic_training_eligible",
                "tasam_sla_penalty", "realized_energy_saving_fraction",
                "realized_allocation_saving_fraction",
            }
            if required.issubset(transition_columns):
                comparisons = conn.execute(
                    """SELECT realized_energy_saving_fraction AS energy_delta,
                              realized_allocation_saving_fraction AS allocation_delta,
                              tasam_sla_penalty AS sla_penalty
                         FROM tasam_economic_transition_history
                        WHERE economic_application_status='applied'
                          AND economic_training_eligible=1
                        ORDER BY decision_id DESC LIMIT ?""",
                    (window,),
                ).fetchall()
            else:
                comparisons = []
        except sqlite3.OperationalError:
            comparisons = []

    infeasible_floor = [
        row for row in allocations
        if float(row["floor_total_ran"] or 0.0) + float(row["floor_total_ai"] or 0.0)
        > float(row["usable_budget"] or 0.0) + 1e-9
    ]
    result["infeasible_floor_constraints"] = len(infeasible_floor)
    # A floor that cannot fit in the currently usable budget is an audited
    # resource shortage, not a TA-SAM policy violation.  The runtime still
    # records it, applies the stock/full-power failsafe, and exposes the
    # negative transition to training; it must not falsely roll back the
    # learner for an infeasible constraint it could not satisfy.
    floor_bad = [
        row for row in allocations
        if float(row["floor_total_ran"] or 0.0) + float(row["floor_total_ai"] or 0.0)
        <= float(row["usable_budget"] or 0.0) + 1e-9
        and (int(row["floor_feasible"] or 0) == 0 or int(row["per_ue_floor_violation_count"] or 0) > 0)
    ]
    result["floor_violations"] = len(floor_bad)
    consecutive_bad = 0
    for row in allocations:
        floor_total = float(row["floor_total_ran"] or 0.0) + float(row["floor_total_ai"] or 0.0)
        usable_budget = float(row["usable_budget"] or 0.0)
        bad = (
            floor_total <= usable_budget + 1e-9
            and (int(row["floor_feasible"] or 0) == 0 or int(row["per_ue_floor_violation_count"] or 0) > 0)
        )
        if bad:
            consecutive_bad += 1
        else:
            break
    result["critical_streak"] = consecutive_bad
    deltas = []
    for row in comparisons:
        try:
            energy = float(row["energy_delta"])
            allocation = float(row["allocation_delta"])
            penalty = float(row["sla_penalty"] or 0.0)
            if all(math.isfinite(value) for value in (energy, allocation, penalty)):
                deltas.append(0.55 * energy + 0.45 * allocation - penalty)
        except (TypeError, ValueError, KeyError):
            continue
    if deltas:
        result["avg_score_delta"] = sum(deltas) / len(deltas)
    if consecutive_bad >= 3:
        result.update({"rollback": True, "reason": "3 violações consecutivas do piso ARMD"})
    elif len(deltas) >= window and result["avg_score_delta"] < -0.01:
        result.update({"rollback": True, "reason": "degradação média realizada na janela econômica"})
    elif infeasible_floor:
        result["reason"] = "pisos SLA inviáveis no orçamento atual; fallback obrigatório registrado"
    else:
        result["reason"] = "guardas sem degradação crítica"
    return result


def iter_valid_rows(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
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
                yield row


def valid_rows(path: Path) -> list[dict[str, Any]]:
    return list(iter_valid_rows(path))


def iter_v2x_valid_rows(path: Path, *, require_judge: bool):
    """Yield V2X rows only after the native live-evidence contract passes."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            quality = row.get("collection_quality") or {}
            try:
                aligned = (
                    float(quality.get("current_metric_skew_s", 0) or 0) <= 6.0
                    and float(quality.get("next_metric_skew_s", 0) or 0) <= 6.0
                )
            except (TypeError, ValueError):
                aligned = False
            if aligned and validate_online_transition(
                row, require_adaptive_reward=True, require_judge=require_judge
            )[0]:
                yield row


V2X_REPLAY_80_20_SCHEMA = "greenran.tasam.v2x.replay_80_20.v1"
V2X_REPLAY_WINDOW90_SCHEMA = "greenran.tasam.v2x.window90.replay_80_20.v1"


def _is_official_evaluation_row(row: dict[str, Any]) -> bool:
    """Keep held-out evaluation transitions out of every online update.

    The legacy economic replay has its own provenance rules.  This check is
    deliberately local to the V2X/article contract so enabling the new
    protocol cannot reinterpret old datasets.
    """
    phase = str(row.get("replay_phase") or row.get("phase") or "").strip().lower()
    partition = str(row.get("replay_partition") or row.get("data_partition") or "").strip().lower()
    return bool(
        row.get("evaluation_frozen")
        or row.get("official_evaluation")
        or row.get("is_evaluation")
        or phase.startswith("evaluation")
        or partition in {"evaluation", "official_evaluation", "test"}
    )


def _v2x_replay_identity(row: dict[str, Any]) -> tuple[str, str, int, str] | None:
    """Return the required phase/episode/seed/timestamp identity or ``None``.

    A decision id alone is not enough: ids can restart for a new seed or a
    simulator episode.  The four-part identity makes both the replay mix and
    later audit deterministic.
    """
    def field(primary: str, fallback: str) -> Any:
        value = row.get(primary)
        return row.get(fallback) if value is None or value == "" else value

    phase = str(field("replay_phase", "phase") or "").strip()
    episode = str(field("replay_episode", "episode") or "").strip()
    timestamp_value = field("replay_timestamp", "timestamp")
    timestamp = "" if timestamp_value is None else str(timestamp_value).strip()
    try:
        seed = int(row.get("replay_seed", row.get("seed")))
    except (TypeError, ValueError):
        return None
    if not phase or not episode or not timestamp:
        return None
    return phase, episode, seed, timestamp


def _v2x_replay_rows(
    path: Path, *, require_adaptive_reward: bool = False,
    require_judge: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Load only native, identified, non-evaluation transition rows."""
    accepted: list[dict[str, Any]] = []
    rejected = {
        "invalid_native_transition": 0,
        "official_evaluation_excluded": 0,
        "missing_phase_episode_seed_or_timestamp": 0,
        "duplicate_transition_identity": 0,
        "adaptive_reward_contract_missing": 0,
        "adaptive_reward_invalid": 0,
        "evaluation_seed_excluded": 0,
        "scenario_control_override_excluded": 0,
    }
    seen: set[tuple[str, str, int, str]] = set()
    source_rows = (
        iter_v2x_valid_rows(path, require_judge=True)
        if require_judge else iter_valid_rows(path)
    )
    for row in source_rows:
        try:
            seed = int(row.get("replay_seed", row.get("seed")))
        except (TypeError, ValueError):
            seed = -1
        if seed not in {43, 44}:
            rejected["evaluation_seed_excluded"] += 1
            continue
        if row.get("scenario_control_override") is not False:
            rejected["scenario_control_override_excluded"] += 1
            continue
        if require_adaptive_reward:
            if row.get("reward_contract") != REWARD_CONTRACT:
                rejected["adaptive_reward_contract_missing"] += 1
                continue
            if row.get("collection_quality", {}).get("valid_for_training") is not True:
                rejected["adaptive_reward_invalid"] += 1
                continue
            snapshot = row.get("adaptive_reward")
            if not isinstance(snapshot, dict) or snapshot.get("reward_contract") != REWARD_CONTRACT:
                rejected["adaptive_reward_invalid"] += 1
                continue
        if _is_official_evaluation_row(row):
            rejected["official_evaluation_excluded"] += 1
            continue
        identity = _v2x_replay_identity(row)
        if identity is None:
            rejected["missing_phase_episode_seed_or_timestamp"] += 1
            continue
        if identity in seen:
            rejected["duplicate_transition_identity"] += 1
            continue
        seen.add(identity)
        accepted.append(row)
    return accepted, rejected


def build_v2x_replay_80_20(
    historical: Path,
    recent: Path,
    output: Path,
    seed: int,
    max_rows: int = 600,
    schema: str = V2X_REPLAY_80_20_SCHEMA,
    replay_policy: str = "historical_80_recent_20",
    run_seed: int | None = None,
    strict_recent_evidence: bool = False,
) -> dict[str, Any]:
    """Build the article V2X replay with exact, non-replaceable 80/20 quotas.

    The function is intentionally fail-closed.  It never borrows one bucket
    to fill the other and never duplicates a transition, even while a
    controller is short on data.  A caller must wait for the missing phase to
    produce evidence instead of training a differently distributed policy.
    """
    target = int(max_rows)
    if target <= 0:
        raise ValueError("replay 80/20 exige max_rows positivo")
    # This is a V2X-specific contract.  It always requires an immutable
    # adaptive-reward snapshot, including in dry-run and unit-test paths.
    require_adaptive_reward = True
    historical_rows, historical_rejected = _v2x_replay_rows(
        historical, require_adaptive_reward=require_adaptive_reward, require_judge=False
    )
    recent_rows, recent_rejected = _v2x_replay_rows(
        recent, require_adaptive_reward=require_adaptive_reward,
        require_judge=strict_recent_evidence,
    )
    historical_quota = int(target * 0.80)
    recent_quota = target - historical_quota

    # A source must not be able to appear in both buckets.  This can happen
    # if a caller appends the current export to the bank before sampling it.
    historical_ids = {_v2x_replay_identity(row) for row in historical_rows}
    recent_unique: list[dict[str, Any]] = []
    overlap = 0
    for row in recent_rows:
        if _v2x_replay_identity(row) in historical_ids:
            overlap += 1
            continue
        recent_unique.append(row)
    recent_rows = recent_unique
    quota_ready = len(historical_rows) >= historical_quota and len(recent_rows) >= recent_quota
    report = {
        "schema": schema,
        "replay_policy": replay_policy,
        "seed": int(seed),
        "sampling_rng_seed": int(seed),
        "run_seed": int(run_seed) if run_seed is not None else None,
        "target_rows": target,
        "historical_required": historical_quota,
        "recent_required": recent_quota,
        "historical_valid": len(historical_rows),
        "recent_valid": len(recent_rows),
        "unique_available": len(historical_rows) + len(recent_rows),
        "historical_used": 0,
        "recent_used": 0,
        "total": 0,
        "no_duplicates": True,
        "evaluation_excluded": True,
        "source_overlap_excluded": overlap,
        "historical_rejected": historical_rejected,
        "recent_rejected": recent_rejected,
        "quota_ready": quota_ready,
        "quota_unmet_groups": [
            *([] if len(historical_rows) >= historical_quota else ["historical"]),
            *([] if len(recent_rows) >= recent_quota else ["recent"]),
        ],
        "selected_transition_ids": {"historical": [], "recent": []},
    }
    if not quota_ready:
        report["status"] = "waiting_replay_quota"
        return report

    rng = random.Random(seed)
    historical_choice = (
        rng.sample(historical_rows, historical_quota)
        if historical_quota < len(historical_rows) else list(historical_rows)
    )
    # Recency is meaningful inside the current bucket.  Retain the newest
    # required transitions rather than randomly replacing them with old rows.
    recent_choice = recent_rows[-recent_quota:] if recent_quota else []
    mixed = [
        {**row, "replay_bucket": "historical", "replay_contract": schema}
        for row in historical_choice
    ] + [
        {**row, "replay_bucket": "recent", "replay_contract": schema}
        for row in recent_choice
    ]
    rng.shuffle(mixed)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in mixed:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    report.update({
        "status": "ready",
        "historical_used": historical_quota,
        "recent_used": recent_quota,
        "total": len(mixed),
        "phase_coverage": sorted({str(row.get("replay_phase")) for row in mixed}),
        "output": str(output),
        "selected_transition_ids": {
            "historical": ["|".join(map(str, _v2x_replay_identity(row))) for row in historical_choice],
            "recent": ["|".join(map(str, _v2x_replay_identity(row))) for row in recent_choice],
        },
    })
    return report


def build_v2x_replay_window90(
    historical: Path,
    recent: Path,
    output: Path,
    seed: int,
    run_seed: int | None = None,
    strict_recent_evidence: bool = False,
) -> dict[str, Any]:
    """Build the pilot's immutable 72 historical / 18 recent replay."""
    return build_v2x_replay_80_20(
        historical,
        recent,
        output,
        seed,
        max_rows=90,
        schema=V2X_REPLAY_WINDOW90_SCHEMA,
        replay_policy="window90_historical_80_recent_20",
        run_seed=run_seed,
        strict_recent_evidence=strict_recent_evidence,
    )


def _annotate_v2x_recent_trace(trace: Path, args: argparse.Namespace, update_id: int) -> None:
    """Add campaign-local training identity to a derived export only."""
    if not trace.is_file():
        return
    phase = f"{args.state_dir.name}:update_{update_id:04d}"
    rows: list[dict[str, Any]] = []
    with trace.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            row["replay_phase"] = str(row.get("replay_phase") or phase)
            row["replay_episode"] = str(row.get("replay_episode") or update_id)
            row["replay_seed"] = int(row.get("replay_seed", row.get("seed", args.seed)) or args.seed)
            timestamp = row.get("replay_timestamp")
            if timestamp is None or timestamp == "":
                timestamp = row.get("timestamp")
            row["replay_timestamp"] = "" if timestamp is None else str(timestamp)
            row["replay_partition"] = "training"
            # Keep the exporter value authoritative.  In particular, never
            # turn missing evidence into a valid false value while a Judge or
            # native readback is still pending.
            if "scenario_control_override" not in row:
                row["scenario_control_override"] = None
            # Persisted banks use ``experience_id`` for de-duplication.  Give
            # V2X rows the same phase-scoped identity used by the 80/20
            # sampler so simulator decision counters may safely restart.
            row["tasam_experience_id"] = (
                f"v2x|{row['replay_phase']}|{row['replay_episode']}|"
                f"{row['replay_seed']}|{row['replay_timestamp']}"
            )
            rows.append(row)
    with trace.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _annotate_v2x_candidate(candidate: Path, active: Path, args: argparse.Namespace, replay: dict[str, Any]) -> None:
    """Attach immutable article provenance to a successfully trained candidate."""
    for name in ("tasam_marl_checkpoint_meta.json", "tasam_marl_summary.json"):
        path = candidate / name
        payload = load_json(path)
        if not payload:
            continue
        payload.update({
            "article_method": "sac_l2" if args.sam_mode == "l2" else "ta_sam_selective",
            "sam_mode": args.sam_mode,
            "l2_weight": float(args.l2_weight),
            "replay_contract": (
                V2X_REPLAY_WINDOW90_SCHEMA
                if getattr(args, "replay_policy", "") == "v2x_window90"
                else V2X_REPLAY_80_20_SCHEMA
            ),
            "reward_contract": REWARD_CONTRACT,
            "historical_replay_source": str(args.experience_bank),
            "historical_replay_sha256": sha256_file(args.experience_bank),
            "recent_replay_source": str(args.recent_experience_bank),
            "replay_rows": replay.get("total"),
            "replay_quotas": {
                "historical": replay.get("historical_used"),
                "recent": replay.get("recent_used"),
            },
            "parent_checkpoint": str(active),
            "evaluation_eligible": False,
            "promotion_eligible": False,
        })
        save_json(path, payload)


def _economic_row_is_eligible(row: dict[str, Any], *, current_native_only: bool = False) -> bool:
    """Return true only for applied, fully evidenced economic transitions."""
    if row.get("economic_action_contract") not in ECONOMIC_ACTION_CONTRACTS:
        return False
    if str(row.get("economic_application_status") or "") != "applied":
        return False
    if not bool(row.get("economic_transition_eligible")):
        return False
    decision = row.get("decision") or {}
    feedback = row.get("judge_feedback") or {}
    action = row.get("economic_action") or {}
    execution_mode = str(
        row.get("economic_execution_mode")
        or action.get("economic_execution_mode")
        or ""
    )
    if bool(row.get("economic_safety_isolated", action.get("economic_safety_isolated", False))):
        return False
    armd_level = str(
        row.get("armd_safety_level", action.get("armd_safety_level", "")) or ""
    ).upper()
    if armd_level == "HARD_VETO":
        return False
    if armd_level and armd_level not in {"CLEAR", "ADVISORY"}:
        return False
    if execution_mode in {"safety_isolated", "economic_neutral_noop"}:
        return False
    # New applied_action_v2 rows must prove that the actuator state was
    # observed in the following correlated window.  Legacy rows without the
    # field remain readable for historical reports, while an explicit false
    # value is always rejected.
    if "actuation_confirmed" in action and not bool(action.get("actuation_confirmed")):
        return False
    native_observation = action.get("native_observation") or {}
    required_version = os.environ.get("GREENRAN_NATIVE_EVIDENCE_VERSION", "").strip()
    allowed_versions = ({required_version} if required_version else {"v2", "v4", "v5"})
    if native_observation.get("evidence_version") not in allowed_versions:
        return False
    if current_native_only:
        if native_observation.get("evidence_version") != "v5":
            return False
        try:
            if int(native_observation.get("transaction_id") or 0) <= 0:
                return False
            if int(native_observation.get("native_control_sequence") or 0) <= 0:
                return False
        except (TypeError, ValueError):
            return False
        if not str(native_observation.get("action_correlation_id") or "").strip():
            return False
        observations = native_observation.get("observations")
        if not isinstance(observations, list) or len(observations) != 3:
            return False
        if {
            int(item.get("cell_id")) for item in observations
            if isinstance(item, dict) and item.get("cell_id") is not None
        } != {2, 3, 4}:
            return False
        for item in observations:
            if not isinstance(item, dict):
                return False
            for field in (
                "power_readback_sim_time_s", "policy_active_sim_time_s",
                "tx_power_percent", "native_allocation_fraction",
            ):
                try:
                    if not math.isfinite(float(item.get(field))):
                        return False
                except (TypeError, ValueError):
                    return False
    observed_cells = native_observation.get("cell_ids")
    if observed_cells is None:
        raw_cells = native_observation.get("cells")
        if isinstance(raw_cells, list):
            observed_cells = [
                item.get("cell_id")
                for item in raw_cells
                if isinstance(item, dict) and item.get("cell_id") is not None
            ]
        else:
            observed_cells = raw_cells
    if current_native_only:
        if set(observed_cells or []) != {2, 3, 4}:
            return False
        expected_campaign = os.environ.get("GREENRAN_CAMPAIGN_ID", "").strip()
        expected_generation = os.environ.get("GREENRAN_NATIVE_SOURCE_GENERATION", "").strip()
        if expected_campaign and str(native_observation.get("campaign_id") or "") != expected_campaign:
            return False
        if expected_generation and str(native_observation.get("source_generation") or "") != expected_generation:
            return False
    if not bool(action.get("actuation_confirmed")):
        return False
    if "command_sent" in action and not bool(action.get("command_sent")):
        return False
    if "tasam_operating_permission" in action and not bool(action.get("tasam_operating_permission")):
        return False
    if execution_mode == "diagnostic":
        return False
    # The new contract explicitly marks whether the applied action is useful
    # for economic replay.  Keep legacy v2 rows readable when the field is
    # absent, but never let an explicit false value through.
    if "economic_training_eligible" in row and not bool(row.get("economic_training_eligible")):
        return False
    if "economic_training_eligible" in action and not bool(action.get("economic_training_eligible")):
        return False
    coverage = feedback.get("pdcp_loss_coverage") or action.get("pdcp_loss_coverage") or {}
    if not isinstance(coverage, dict) or not bool(coverage.get("valid")):
        return False
    if not bool(feedback.get("topology_valid", action.get("topology_valid", False))):
        return False
    if not bool(feedback.get("tasam_action_applied", row.get("tasam_action_applied", False))):
        return False
    if bool(decision.get("tasam_fallback_used", False)):
        return False
    if not bool(decision.get("tasam_checkpoint_valid", False)):
        return False
    if not bool(feedback.get("economic_action_alignment_valid", False)):
        return False
    live = action.get("live_candidate") or {}
    applied = action.get("applied") or {}
    try:
        live_power = float(live.get("power_w"))
        applied_power = float(applied.get("power_w"))
        live_total = float(live.get("total_allocation"))
        applied_total = float(applied.get("total_allocation"))
    except (TypeError, ValueError):
        return False
    return live_power > 0.0 and applied_power > 0.0 and live_total > 0.0 and applied_total >= 0.0


def economic_replay_evidence(path: Path) -> dict[str, Any]:
    """Count only economic transitions whose effects were actually applied."""
    total = eligible = promotion_eligible = rejected = applied = aligned = 0
    energy_savings: list[float] = []
    allocation_savings: list[float] = []
    calibration_versions: set[str] = set()
    for row in iter_valid_rows(path):
        if row.get("economic_action_contract") not in ECONOMIC_ACTION_CONTRACTS:
            continue
        total += 1
        if str(row.get("economic_application_status") or "") == "applied":
            applied += 1
            if bool((row.get("judge_feedback") or {}).get("economic_action_alignment_valid", False)):
                aligned += 1
        if _economic_row_is_eligible(row):
            eligible += 1
            version = str(row.get("calibration_version") or "").strip()
            if version:
                calibration_versions.add(version)
            if bool(row.get("economic_promotion_eligible", (row.get("economic_action") or {}).get("economic_promotion_eligible", False))):
                promotion_eligible += 1
            action = row.get("economic_action") or {}
            feedback = row.get("judge_feedback") or {}
            for key in ("realized_energy_saving_fraction", "realized_allocation_saving_fraction"):
                value = row.get(key, feedback.get(key, action.get(key)))
                try:
                    (energy_savings if key.startswith("realized_energy") else allocation_savings).append(float(value))
                except (TypeError, ValueError):
                    pass
        else:
            rejected += 1
    mean_energy = sum(energy_savings) / len(energy_savings) if energy_savings else 0.0
    mean_allocation = sum(allocation_savings) / len(allocation_savings) if allocation_savings else 0.0
    return {
        "contract_rows": total,
        "eligible_transitions": eligible,
        "economic_training_eligible_transitions": eligible,
        "economic_promotion_eligible_transitions": promotion_eligible,
        "promotion_beneficial_rate": promotion_eligible / max(eligible, 1),
        "mean_realized_energy_saving_fraction": mean_energy,
        "mean_realized_allocation_saving_fraction": mean_allocation,
        "ineligible_transitions": rejected,
        "applied_actions": applied,
        "projected_applied_aligned_actions": aligned,
        "projected_applied_alignment_rate": aligned / max(applied, 1),
        "calibration_versions": sorted(calibration_versions),
        "calibration_version": next(iter(calibration_versions), "") if len(calibration_versions) == 1 else "",
        "calibration_version_valid": len(calibration_versions) == 1,
    }


def filter_economic_replay(source: Path, output: Path) -> dict[str, int]:
    """Write a replay containing no categorical-only or rejected actions."""
    total = eligible = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in iter_valid_rows(source):
            total += 1
            if not _economic_row_is_eligible(
                row,
                current_native_only=bool(os.environ.get("GREENRAN_NATIVE_EVIDENCE_VERSION", "").strip()),
            ):
                continue
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            eligible += 1
    return {"source_rows": total, "eligible_rows": eligible}


def build_sqlite_economic_replay(
    db_path: Path,
    output: Path,
    seed: int,
    max_rows: int,
) -> dict[str, Any]:
    """Materialize a compact, exact-state trainer input from SQLite.

    Economic transitions intentionally do not duplicate the large MARL state
    snapshots.  They carry exact source/observed timestamps instead, and this
    function rehydrates those states from the canonical history tables.  A
    missing or malformed state is rejected; nearest-timestamp matching would
    train the policy on an action from a different network state.
    """
    if not db_path.is_file():
        return {
            "status": "missing_database", "source_rows": 0,
            "eligible_rows": 0, "rehydrated_rows": 0,
            "rejected_rows": 0, "rejected_reasons": {"missing_database": 1},
        }

    def reject(reason: str) -> None:
        rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1

    def numeric_vector(raw: Any, *, dimension: int, label: str) -> tuple[list[float] | None, str]:
        try:
            decoded = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, f"{label}_invalid_json"
        if not isinstance(decoded, list) or len(decoded) != dimension:
            return None, f"{label}_dimension"
        try:
            values = [float(value) for value in decoded]
        except (TypeError, ValueError):
            return None, f"{label}_non_numeric"
        if not all(math.isfinite(value) for value in values):
            return None, f"{label}_non_finite"
        return values, ""

    def state_at(
        conn: sqlite3.Connection,
        timestamp: int,
        *,
        prefix: str,
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None, str]:
        global_row = conn.execute(
            "SELECT state_vector_json, usable_budget FROM marl_global_state_history WHERE timestamp = ?",
            (timestamp,),
        ).fetchone()
        if global_row is None:
            return None, None, f"{prefix}_global_state_missing"
        global_vector, error = numeric_vector(
            global_row["state_vector_json"], dimension=13, label=f"{prefix}_global_state"
        )
        if global_vector is None:
            return None, None, error
        du_rows = conn.execute(
            """
            SELECT du_id, role, primary_slice, slice_mix_json, state_vector_json
              FROM marl_du_state_history
             WHERE timestamp = ?
            """,
            (timestamp,),
        ).fetchall()
        if len(du_rows) != len(CANONICAL_DU_IDS):
            return None, None, f"{prefix}_du_count"
        by_du = {str(row["du_id"]): row for row in du_rows}
        if len(by_du) != len(du_rows) or set(by_du) != set(CANONICAL_DU_IDS):
            return None, None, f"{prefix}_du_topology"
        du_states: list[dict[str, Any]] = []
        for du_id in CANONICAL_DU_IDS:
            row = by_du[du_id]
            vector, error = numeric_vector(
                row["state_vector_json"], dimension=13, label=f"{prefix}_{du_id}"
            )
            if vector is None:
                return None, None, error
            try:
                slice_mix = json.loads(row["slice_mix_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                return None, None, f"{prefix}_{du_id}_slice_mix"
            if not isinstance(slice_mix, dict):
                return None, None, f"{prefix}_{du_id}_slice_mix"
            du_states.append({
                "du_id": du_id,
                "role": str(row["role"] or ""),
                "primary_slice": str(row["primary_slice"] or ""),
                "slice_mix": slice_mix,
                "state_vector": vector,
            })
        try:
            usable_budget = float(global_row["usable_budget"] or 0.0)
        except (TypeError, ValueError):
            usable_budget = 0.0
        return du_states, {"state_vector": global_vector, "usable_budget": usable_budget}, ""

    def selected(mapping: Any, fields: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(mapping, dict):
            return {}
        result: dict[str, Any] = {}
        for field in fields:
            if field not in mapping or mapping[field] is None:
                continue
            try:
                result[field] = json.loads(json.dumps(mapping[field], ensure_ascii=False))
            except (TypeError, ValueError):
                continue
        return result

    def transition_payload(
        raw: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Read both historical v1 and compact v2 durable records."""
        if raw.get("schema") == "greenran.tasam.economic_transition.v2":
            decision = raw.get("decision_provenance") or {}
            next_decision = raw.get("next_decision_provenance") or {}
            feedback = raw.get("judge_feedback") or {}
            action = raw.get("economic_action") or {}
            allocation = raw.get("resource_allocation") or {}
        else:
            decision = raw.get("decision") or {}
            next_decision = raw.get("next_decision") or {}
            feedback = raw.get("judge_feedback") or {}
            action = feedback.get("economic_action") or decision.get("economic_action") or {}
            allocation = decision.get("resource_allocation") or decision.get("action") or {}
        return tuple(
            value if isinstance(value, dict) else {}
            for value in (decision, next_decision, feedback, action, allocation)
        )  # type: ignore[return-value]

    reservoir: list[dict[str, Any]] = []
    source_rows = 0
    rehydrated_rows = 0
    rejected_reasons: dict[str, int] = {}
    try:
        with closing(sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT decision_id, decision_timestamp, observed_timestamp,
                       source_metric_snapshot_id, observed_metric_snapshot_id,
                       economic_action_contract, economic_application_status,
                       economic_transition_eligible, economic_training_eligible,
                       economic_promotion_eligible, realized_energy_saving_fraction,
                       realized_allocation_saving_fraction, tasam_online_reward,
                       tasam_energy_reward, tasam_allocation_reward, tasam_sla_penalty,
                       calibration_version, transition_json, transition_sha256
                  FROM tasam_economic_transition_history
                 WHERE economic_training_eligible = 1
                 ORDER BY decision_id ASC
                """
            )
            rng = random.Random(int(seed))
            for row in rows:
                source_rows += 1
                try:
                    payload = json.loads(str(row["transition_json"] or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    reject("transition_json_invalid")
                    continue
                decision, next_decision, feedback, raw_action, raw_allocation = transition_payload(payload)
                if not decision or not feedback:
                    reject("transition_payload_incomplete")
                    continue
                try:
                    source_timestamp = int(row["decision_timestamp"])
                    observed_timestamp = int(row["observed_timestamp"])
                except (TypeError, ValueError):
                    reject("transition_timestamp_invalid")
                    continue
                du_states, global_state, reason = state_at(
                    conn, source_timestamp, prefix="source"
                )
                if reason:
                    reject(reason)
                    continue
                next_du_states, next_global_state, reason = state_at(
                    conn, observed_timestamp, prefix="observed"
                )
                if reason:
                    reject(reason)
                    continue
                action = selected(raw_action, (
                    "contract", "application_status", "correlation_id",
                    "economic_transition_eligible", "economic_training_eligible",
                    "economic_promotion_eligible", "economic_execution_mode",
                    "economic_safety_isolated", "economic_safety_isolation_reason",
                    "economic_isolation_source", "outcome_invalid_reason",
                    "rejection_reason", "safety_override", "armd_safety_level",
                    "armd_role", "armd_advisory_only", "armd_hard_veto",
                    "tasam_operating_permission", "topology_valid",
                    "pdcp_loss_coverage", "pdcp_metric_snapshot_id",
                    "realized_energy_saving_fraction", "realized_allocation_saving_fraction",
                    "proposed", "projected", "applied", "live_candidate",
                    "command_sent", "actuation_confirmed", "actuation_confirmation_source",
                    "observed_power_percent", "observed_power_w", "observed_ru_count",
                    "observed_mmwave_count", "confirmation_decision_id",
                    "native_control_sequence", "native_observation",
                ))
                if not action:
                    reject("economic_action_invalid")
                    continue
                resource_allocation = selected(raw_allocation, (
                    "usable_budget", "resource_budget", "r_ran", "r_ai", "d_ran", "d_ai",
                    "floor_total_ran", "floor_total_ai", "floor_feasible", "floor_verified",
                    "allocation_state", "ran_completion_ratio", "ai_completion_ratio",
                ))
                feedback = selected(feedback, (
                    "feedback_status", "outcome_observed", "observed_metric_id",
                    "pdcp_metric_snapshot_id", "pdcp_loss_coverage", "topology_valid",
                    "tasam_action_applied", "economic_action_alignment_valid",
                    "economic_action_contract", "economic_application_status",
                    "economic_transition_eligible", "economic_training_eligible",
                    "economic_promotion_eligible", "economic_execution_mode",
                    "economic_safety_isolated", "economic_safety_isolation_reason",
                    "economic_outcome_invalid_reason", "armd_safety_level", "armd_role",
                    "realized_energy_saving_fraction", "realized_allocation_saving_fraction",
                    "tasam_online_reward", "tasam_energy_reward",
                    "tasam_allocation_reward", "tasam_sla_penalty",
                    "tasam_observed_verdict", "tasam_predicted_verdict",
                    "tasam_error_components", "energy_model_version",
                ))
                item = {
                    "schema": ECONOMIC_REPLAY_SCHEMA,
                    "decision_id": int(row["decision_id"]),
                    "timestamp": source_timestamp,
                    "next_timestamp": observed_timestamp,
                    "du_states": du_states,
                    "global_state": global_state,
                    "next_du_states": next_du_states,
                    "next_global_state": next_global_state,
                    "decision": {
                        "decision_id": int(row["decision_id"]),
                        "tasam_checkpoint_valid": bool(decision.get("tasam_checkpoint_valid", False)),
                        "tasam_fallback_used": bool(decision.get("tasam_fallback_used", False)),
                    },
                    "next_metrics": next_decision.get("metrics") or next_decision.get("next_metrics") or {},
                    "scenario_stage": str(
                        decision.get("decision_stage_name")
                        or raw_allocation.get("scenario_stage")
                        or "unknown"
                    ),
                    "judge_feedback": feedback,
                    "economic_action": action,
                    "resource_allocation": resource_allocation,
                    "action": resource_allocation,
                    "allocation_total_head_enabled": True,
                    "economic_action_contract": str(row["economic_action_contract"] or action.get("contract") or ""),
                    "economic_application_status": str(row["economic_application_status"] or action.get("application_status") or ""),
                    "economic_transition_eligible": bool(row["economic_transition_eligible"]),
                    "economic_training_eligible": bool(row["economic_training_eligible"]),
                    "economic_promotion_eligible": bool(row["economic_promotion_eligible"]),
                    "economic_execution_mode": feedback.get("economic_execution_mode", action.get("economic_execution_mode")),
                    "economic_safety_isolated": bool(feedback.get("economic_safety_isolated", action.get("economic_safety_isolated", False))),
                    "armd_safety_level": feedback.get("armd_safety_level", action.get("armd_safety_level", "")),
                    "realized_energy_saving_fraction": row["realized_energy_saving_fraction"],
                    "realized_allocation_saving_fraction": row["realized_allocation_saving_fraction"],
                    "tasam_online_reward": row["tasam_online_reward"],
                    "tasam_energy_reward": row["tasam_energy_reward"],
                    "tasam_allocation_reward": row["tasam_allocation_reward"],
                    "tasam_sla_penalty": row["tasam_sla_penalty"],
                    "tasam_action_applied": bool(feedback.get("tasam_action_applied", False)),
                    "topology_valid": bool(feedback.get("topology_valid", False)),
                    "calibration_version": str(row["calibration_version"] or ""),
                    "source_metric_snapshot_id": row["source_metric_snapshot_id"],
                    "observed_metric_snapshot_id": row["observed_metric_snapshot_id"],
                    "economic_transition_sha256": str(row["transition_sha256"] or ""),
                    "collection_quality": {
                        "valid_for_training": True,
                        "collector_mode": "pdcp_real",
                        "pdcp_real": True,
                        "proxy_latency_sample_count": 0,
                        "metric_alignment_valid": True,
                        "sim_reset": False,
                        "current_metric_skew_s": 0.0,
                        "next_metric_skew_s": 0.0,
                    },
                    "pdcp_loss_coverage": feedback.get("pdcp_loss_coverage") or action.get("pdcp_loss_coverage") or {},
                    "economic_transition_sqlite_decision_id": int(row["decision_id"]),
                }
                action = item.get("economic_action") or {}
                # Keep the durable-row contract identical to the historical
                # JSONL contract.  The decision itself contains provenance
                # from the proposal, while delayed economic eligibility and
                # realized effects live in judge_feedback/action.
                for key in (
                    "economic_action_contract", "economic_application_status",
                    "economic_transition_eligible", "economic_training_eligible",
                    "economic_promotion_eligible", "economic_execution_mode",
                    "economic_safety_isolated", "armd_safety_level",
                    "realized_energy_saving_fraction", "realized_allocation_saving_fraction",
                    "tasam_online_reward", "tasam_energy_reward",
                    "tasam_allocation_reward", "tasam_sla_penalty",
                    "topology_valid", "tasam_action_applied",
                ):
                    if key not in item or item.get(key) is None:
                        item[key] = feedback.get(key, action.get(key, item.get(key)))
                if not _economic_row_is_eligible(
                    item,
                    current_native_only=bool(os.environ.get("GREENRAN_NATIVE_EVIDENCE_VERSION", "").strip()),
                ):
                    reject("economic_contract_ineligible")
                    continue
                rehydrated_rows += 1
                if len(reservoir) < max_rows:
                    reservoir.append(item)
                else:
                    index = rng.randrange(rehydrated_rows)
                    if index < max_rows:
                        reservoir[index] = item
    except sqlite3.Error as exc:
        return {
            "status": "sqlite_error", "source_rows": source_rows,
            "eligible_rows": 0, "rehydrated_rows": rehydrated_rows,
            "rejected_rows": sum(rejected_reasons.values()),
            "rejected_reasons": dict(sorted(rejected_reasons.items())),
            "error": str(exc),
        }

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for item in sorted(reservoir, key=lambda value: int(value.get("decision_id", 0) or 0)):
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "status": "ok",
        "source_rows": source_rows,
        "eligible_rows": len(reservoir),
        "rehydrated_rows": rehydrated_rows,
        "rejected_rows": sum(rejected_reasons.values()),
        "rejected_reasons": dict(sorted(rejected_reasons.items())),
        "temporary_bytes": output.stat().st_size if output.exists() else 0,
        "replay_schema": ECONOMIC_REPLAY_SCHEMA,
        "sqlite_source": str(db_path.resolve()),
        "seed": int(seed),
    }


def _replay_contract_valid(
    row: dict[str, Any],
    expected_du_count: int,
    expected_du_state_dim: int,
    expected_global_state_dim: int,
) -> bool:
    if not (expected_du_count and expected_du_state_dim and expected_global_state_dim):
        return True
    return (
        len(row.get("du_states") or []) == expected_du_count
        and normalize_state_contract(row, expected_du_state_dim, expected_global_state_dim)
    )


def build_persistent_bank_replay(
    bank: Path,
    output: Path,
    seed: int,
    max_rows: int,
    *,
    expected_du_count: int = 0,
    expected_du_state_dim: int = 0,
    expected_global_state_dim: int = 0,
    category_error_repeat: int = 0,
) -> dict[str, Any]:
    """Sample a large experience bank in streaming mode.

    Bank rows can contain full state/trace context and therefore be much
    larger than their JSON line count suggests.  Reservoir sampling keeps the
    replay capped without loading the entire bank into the controller.
    """
    target = max(max_rows, 0)
    if not bank.is_file() or target <= 0:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("", encoding="utf-8")
        return {
            "historical_valid": 0, "recent_valid": 0, "unique_available": 0,
            "historical_used": 0, "recent_used": 0, "total": 0,
            "prioritization": "persistent_bank_streaming_v1",
        }

    if category_error_repeat > 0:
        # The clean campaign bank is online-only. Materializing its unique
        # rows here makes the four quota contract identical to build_replay;
        # the normal non-focused path remains streaming for legacy banks.
        rows = [
            row for row in iter_valid_rows(bank)
            if _replay_contract_valid(row, expected_du_count, expected_du_state_dim, expected_global_state_dim)
        ]
        mixed, quota_stats = _focused_replay(rows, target, random.Random(seed))
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            for row in mixed:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        category_error_available = sum(1 for row in rows if has_category_error(row))
        category_error_unique = sum(1 for row in mixed if has_category_error(row) and not row.get("replay_sampling_copy"))
        category_error_repeated = sum(1 for row in mixed if has_category_error(row) and row.get("replay_sampling_copy"))
        return {
            "historical_valid": len(rows), "recent_valid": 0,
            "unique_available": len(rows), "historical_used": len(mixed),
            "recent_used": 0, "total": len(mixed),
            "prioritization": "energy_focused_40_30_20_10_streaming_v1",
            "category_error_available": category_error_available,
            "category_error_selected_unique": category_error_unique,
            "category_error_in_base": category_error_unique,
            "category_error_repeated": category_error_repeated,
            "category_error_in_replay": category_error_unique + category_error_repeated,
            "category_error_replay_fraction": float(category_error_unique + category_error_repeated) / max(len(mixed), 1),
            "category_confusion_counts": _category_confusion_counts(mixed),
            "replay_quotas": quota_stats,
            "quota_ready": bool(quota_stats.get("quota_ready", True)),
            "quota_unmet_groups": list(quota_stats.get("quota_unmet_groups", [])),
            "conditional_replay_fraction": float(quota_stats.get("conditional_transition_selected_unique", 0) + quota_stats.get("conditional_transition_repeated", 0)) / max(len(mixed), 1),
        }

    rng = random.Random(seed)
    seen_ids: set[str] = set()
    all_reservoir: list[dict[str, Any]] = []
    groups: dict[str, dict[int, list[dict[str, Any]]]] = {
        "conditional": {}, "other_error": {}, "correct": {},
    }
    available_by_group = {"conditional": 0, "other_error": 0, "correct": 0}
    category_error_available = 0
    total_available = 0

    def reservoir_add(bucket: list[dict[str, Any]], row: dict[str, Any], capacity: int, count: int) -> None:
        if len(bucket) < capacity:
            bucket.append(row)
            return
        index = rng.randrange(count)
        if index < capacity:
            bucket[index] = row

    for row in iter_valid_rows(bank):
        if not _replay_contract_valid(row, expected_du_count, expected_du_state_dim, expected_global_state_dim):
            continue
        key = experience_id(row)
        if key in seen_ids:
            continue
        seen_ids.add(key)
        total_available += 1
        reservoir_add(all_reservoir, row, target, total_available)
        error = has_category_error(row)
        observed = observed_category(row)
        if error:
            category_error_available += 1
        group = "conditional" if observed == "CONDITIONAL" else ("other_error" if error else "correct")
        available_by_group[group] += 1
        priority = category_error_priority(row) if group != "correct" else 0
        bucket = groups[group].setdefault(priority, [])
        reservoir_add(bucket, row, target, available_by_group[group])

    quota_conditional = int(math.ceil(target * 0.60))
    quota_other_error = int(math.ceil(target * 0.30))
    quota_correct = max(0, target - quota_conditional - quota_other_error)

    def flatten(group: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for priority in sorted(groups[group], reverse=True):
            rows.extend(groups[group][priority])
        return rows

    conditional_rows = flatten("conditional")
    other_error_rows = flatten("other_error")
    correct_rows = flatten("correct")
    selected_conditional, conditional_unique, conditional_repeated = _select_replay_quota(
        conditional_rows, quota_conditional, rng, prioritize_errors=True
    )
    selected_other_error, other_error_unique, other_error_repeated = _select_replay_quota(
        other_error_rows, quota_other_error, rng, prioritize_errors=True
    )
    selected_correct, correct_unique, correct_repeated = _select_replay_quota(
        correct_rows, quota_correct, rng
    )
    mixed = selected_conditional + selected_other_error + selected_correct
    missing_groups = [
        group for group, quota, rows in (
            ("conditional", quota_conditional, conditional_rows),
            ("other_error", quota_other_error, other_error_rows),
            ("correct", quota_correct, correct_rows),
        )
        if quota > 0 and not rows
    ]
    category_error_repeated = sum(
        1 for row in mixed if has_category_error(row) and row.get("replay_sampling_copy")
    )
    quota_stats = {
        "conditional_available": available_by_group["conditional"],
        "conditional_selected_unique": conditional_unique,
        "conditional_repeated": conditional_repeated,
        "other_error_available": available_by_group["other_error"],
        "other_error_selected_unique": other_error_unique,
        "other_error_repeated": other_error_repeated,
        "correct_available": available_by_group["correct"],
        "correct_selected_unique": correct_unique,
        "correct_repeated": correct_repeated,
        "conditional_quota": quota_conditional,
        "other_error_quota": quota_other_error,
        "correct_quota": quota_correct,
        "quota_ready": not missing_groups,
        "quota_unmet_groups": missing_groups,
    }
    rng.shuffle(mixed)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in mixed:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    category_error_selected_unique = sum(
        1 for row in mixed if has_category_error(row) and not row.get("replay_sampling_copy")
    )
    return {
        "historical_valid": total_available,
        "recent_valid": 0,
        "unique_available": total_available,
        "historical_used": len(mixed),
        "recent_used": 0,
        "total": len(mixed),
        "prioritization": "categorical_conditional_priority_60_30_10_streaming_v1",
        "category_error_available": category_error_available,
        "category_error_selected_unique": category_error_selected_unique,
        "category_error_in_base": category_error_selected_unique,
        "category_error_repeated": category_error_repeated,
        "category_error_in_replay": category_error_selected_unique + category_error_repeated,
        "category_error_replay_fraction": float(category_error_selected_unique + category_error_repeated) / max(len(mixed), 1),
        "category_confusion_counts": _category_confusion_counts(mixed),
        "replay_quotas": quota_stats,
        "quota_ready": bool(quota_stats.get("quota_ready", True)),
        "quota_unmet_groups": list(quota_stats.get("quota_unmet_groups", [])),
        "conditional_replay_fraction": float(quota_stats["conditional_selected_unique"] + quota_stats["conditional_repeated"]) / max(len(mixed), 1),
        "other_error_replay_fraction": float(quota_stats["other_error_selected_unique"] + quota_stats["other_error_repeated"]) / max(len(mixed), 1),
        "correct_replay_fraction": float(quota_stats["correct_selected_unique"] + quota_stats["correct_repeated"]) / max(len(mixed), 1),
    }


def has_category_error(row: dict[str, Any]) -> bool:
    """Return the explicit categorical-error signal for a valid transition."""
    feedback = row.get("judge_feedback") or {}
    if not isinstance(feedback, dict):
        feedback = {}
    if "tasam_category_error" in feedback:
        return bool(feedback.get("tasam_category_error"))
    return bool(row.get("tasam_category_error", False))


def observed_category(row: dict[str, Any]) -> str:
    feedback = row.get("judge_feedback") or {}
    return str(
        row.get("tasam_observed_verdict")
        or feedback.get("tasam_observed_verdict")
        or feedback.get("observed_verdict")
        or "UNKNOWN"
    ).strip().upper()


def category_error_priority(row: dict[str, Any]) -> int:
    """Rank categorical errors, making over-severe predictions highest priority."""
    feedback = row.get("judge_feedback") or {}
    predicted = str(row.get("tasam_predicted_verdict") or feedback.get("tasam_predicted_verdict") or "").upper()
    observed = observed_category(row)
    order = {"ALLOWED": 1, "CONDITIONAL": 2, "BLOCKED": 3}
    if not has_category_error(row):
        return 0
    if predicted in order and observed in order:
        distance = abs(order[predicted] - order[observed])
        if distance >= 2 or order[predicted] > order[observed]:
            return 3
    return 2


def _select_replay_quota(
    rows: list[dict[str, Any]],
    quota: int,
    rng: random.Random,
    *,
    prioritize_errors: bool = False,
) -> tuple[list[dict[str, Any]], int, int]:
    """Select exactly one replay quota, repeating only inside its group.

    The focused replay is a training contract, not a best-effort class mix.
    Once the unique online rows are exhausted, copies are made from the same
    group; rows from another group are never used to fill this quota.
    """
    if quota <= 0:
        return [], 0, 0
    ordered = list(rows)
    rng.shuffle(ordered)
    if prioritize_errors:
        ordered.sort(key=category_error_priority, reverse=True)
    if not ordered:
        return [], 0, 0
    selected = ordered[:quota]
    unique_count = len(selected)
    repeated_count = 0
    repeat_index = 0
    while len(selected) < quota:
        source = ordered[repeat_index % len(ordered)]
        repeated = dict(source)
        repeated["replay_sampling_copy"] = int(repeat_index // len(ordered)) + 1
        selected.append(repeated)
        repeated_count += 1
        repeat_index += 1
    return selected, unique_count, repeated_count


def _focused_replay(rows: list[dict[str, Any]], target: int, rng: random.Random) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the energy-focused online replay with four disjoint quotas."""
    quotas = {
        "conditional_transition": int(round(target * 0.40)),
        "underallocation": int(round(target * 0.30)),
        "high_power_safe": int(round(target * 0.20)),
    }
    quotas["correct_recent"] = max(0, target - sum(quotas.values()))
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name in (*quotas,)}
    seen: set[str] = set()
    for row in rows:
        key = experience_id(row)
        if key in seen:
            continue
        seen.add(key)
        feedback = row.get("judge_feedback") or {}
        components = feedback.get("tasam_error_components") or row.get("tasam_error_components") or {}
        boundary = bool(row.get("stage_boundary_feedback") or feedback.get("stage_boundary_feedback"))
        observed = observed_category(row)
        if observed == "CONDITIONAL" or boundary or has_category_error(row):
            groups["conditional_transition"].append(row)
        try:
            ran = float(components.get("ran_completion", row.get("ran_completion_ratio", 1.0)) or 0.0)
            ai = float(components.get("ai_completion", row.get("ai_completion_ratio", 1.0)) or 0.0)
            shortfall = float(components.get("completion_shortfall_penalty", 0.0) or 0.0)
            underallocation = float(components.get("underallocation_penalty", 0.0) or 0.0)
        except (TypeError, ValueError):
            ran, ai, shortfall, underallocation = 0.0, 0.0, 1.0, 1.0
        if shortfall > 0.0 or underallocation > 0.0 or ran < 0.95 or ai < 0.75:
            groups["underallocation"].append(row)
        try:
            power = float(row.get("tasam_power_percent", components.get("power_percent", 100.0)) or 100.0)
        except (TypeError, ValueError):
            power = 100.0
        service_safe = (
            observed == "ALLOWED"
            and not has_category_error(row)
            and ran >= 0.95
            and ai >= 0.75
            and float(components.get("service_error", 0.0) or 0.0) <= 1e-9
            and float(components.get("tail_latency_error", 0.0) or 0.0) <= 1e-9
        )
        if power >= 100.0 and service_safe:
            groups["high_power_safe"].append(row)
        if not has_category_error(row) and observed in {"ALLOWED", "CONDITIONAL", "BLOCKED"}:
            groups["correct_recent"].append(row)

    priorities = {
        "conditional_transition": lambda row: category_error_priority(row),
        "underallocation": lambda row: float((row.get("judge_feedback") or {}).get("tasam_error_components", {}).get("underallocation_penalty", 0.0) or 0.0),
        "high_power_safe": lambda row: float(row.get("tasam_power_percent", 100.0) or 100.0),
        "correct_recent": lambda row: 0,
    }
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    stats: dict[str, Any] = {"quota_ready": True, "quota_unmet_groups": {}}
    for name, quota in quotas.items():
        candidates = [row for row in groups[name] if experience_id(row) not in used]
        rng.shuffle(candidates)
        candidates.sort(key=priorities[name], reverse=True)
        if quota > 0 and not candidates:
            stats["quota_ready"] = False
            stats["quota_unmet_groups"][name] = quota
        picked, unique_count, repeated_count = _select_replay_quota(candidates, quota, rng, prioritize_errors=name == "conditional_transition")
        selected.extend(picked)
        used.update(experience_id(row) for row in candidates[:unique_count])
        stats[f"{name}_available"] = len(candidates)
        stats[f"{name}_quota"] = quota
        stats[f"{name}_selected_unique"] = unique_count
        stats[f"{name}_repeated"] = repeated_count
    rng.shuffle(selected)
    stats["quota_unmet_groups"] = sorted(stats["quota_unmet_groups"])
    return selected, stats


def _category_confusion_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        feedback = row.get("judge_feedback") or {}
        predicted = str(row.get("tasam_predicted_verdict") or feedback.get("tasam_predicted_verdict") or "UNKNOWN").upper()
        observed = str(row.get("tasam_observed_verdict") or feedback.get("tasam_observed_verdict") or "UNKNOWN").upper()
        key = f"{predicted}->{observed}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


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


def _category_one_hot(operating_state: str) -> list[float]:
    return [
        1.0 if operating_state == "ALLOWED" else 0.0,
        1.0 if operating_state == "CONDITIONAL" else 0.0,
        1.0 if operating_state == "BLOCKED" else 0.0,
    ]


def _append_state_context(vector: Any, operating_state: str, target_dim: int) -> tuple[bool, bool]:
    """Repair the category tail and report whether the source was coherent.

    Historical traces occasionally contain a 13-D vector whose last three
    indicators describe a different stage than the transition metadata.  The
    derived replay must use the authoritative stage, while the persistent
    source remains untouched.  The second return value records whether a
    repair was necessary.
    """
    if not isinstance(vector, list):
        return False, False
    values = list(vector)
    expected = _category_one_hot(operating_state)
    if target_dim == 13 and len(values) == 13:
        consistent = values[-3:] == expected
        vector[-3:] = expected
        return True, consistent
    if target_dim == 13 and len(values) == 10:
        vector[:] = values + expected
        return True, False
    if len(values) == target_dim:
        return True, True
    return False, False


def _stage_value(row: dict[str, Any], current: bool) -> tuple[str, str]:
    fields = (
        ("decision_stage_name", "scenario_stage", "collection_event_stage_name")
        if current
        else ("observed_stage_name", "next_scenario_stage", "next_collection_event_stage_name")
    )
    for field in fields:
        value = row.get(field)
        if value:
            return _state_from_stage(value), field
    return _state_from_stage(None, (row.get("decision") or {}).get("decision")), "fallback"


def normalize_state_contract(row: dict[str, Any], du_state_dim: int, global_state_dim: int) -> bool:
    """Make legacy 10-D replay rows compatible with the active 13-D actor.

    The old historical trace is intentionally preserved on disk.  Its three
    operating-state indicators are reconstructed from the controlled stage
    only in the derived replay, so a failed or mixed-dimension update cannot
    silently instantiate actors with the wrong input shape.
    """
    current_state, current_source = _stage_value(row, current=True)
    next_state, next_source = _stage_value(row, current=False)
    if next_source == "fallback":
        next_state = current_state
    global_state = row.get("global_state") or {}
    next_global_state = row.get("next_global_state") or {}
    consistency = True
    repaired = False
    ok, was_consistent = _append_state_context(global_state.get("state_vector"), current_state, global_state_dim)
    if not ok:
        return False
    consistency = consistency and was_consistent
    repaired = repaired or not was_consistent
    ok, was_consistent = _append_state_context(next_global_state.get("state_vector"), next_state, global_state_dim)
    if not ok:
        return False
    consistency = consistency and was_consistent
    repaired = repaired or not was_consistent
    row["global_state"] = global_state
    row["next_global_state"] = next_global_state
    for key, operating_state in (("du_states", current_state), ("next_du_states", next_state)):
        states = row.get(key) or []
        if not states:
            return False
        for du in states:
            ok, was_consistent = _append_state_context(du.get("state_vector"), operating_state, du_state_dim)
            if not ok:
                return False
            consistency = consistency and was_consistent
            repaired = repaired or not was_consistent
    row["state_category_source"] = {"current": current_source, "next": next_source}
    row["state_category_consistent"] = bool(consistency)
    row["state_category_repaired"] = bool(repaired)
    row["stage_boundary_feedback"] = bool(
        current_source != "fallback"
        and next_source != "fallback"
        and current_state != next_state
    )
    return True


def _checkpoint_dimensions(checkpoint: Path) -> tuple[int, int, int]:
    meta = load_json(checkpoint / "tasam_marl_checkpoint_meta.json")
    return (
        int(meta.get("du_count", 0) or 0),
        int(meta.get("du_state_dim", 0) or 0),
        int(meta.get("global_state_dim", 0) or 0),
    )


def _checkpoint_temporal_dim(checkpoint: Path) -> int:
    """Use the active checkpoint's temporal contract for candidate training."""
    meta = load_json(checkpoint / "tasam_marl_checkpoint_meta.json")
    try:
        return max(0, int(meta.get("temporal_dim", 0) or 0))
    except (TypeError, ValueError):
        return 0


def export_recent(args: argparse.Namespace, trace: Path, summary: Path) -> None:
    v2x_replay = getattr(args, "replay_policy", "legacy") in {"v2x_80_20", "v2x_window90"}
    cmd = [
        sys.executable,
        str(ROOT / "scripts/export_tasam_article_dataset.py"),
        "--db", str(args.db),
        "--output-jsonl", str(trace),
        "--summary-json", str(summary),
        "--e2-audit-jsonl", str(args.state_dir / "xapp_intents" / "tasam_control_audit.jsonl"),
        "--max-step-gap-s", "60" if v2x_replay else "6",
        "--max-sim-reset-gap-s", "1",
        "--limit", str(args.export_limit),
        # Keep pending rows visible to the live finalizer.  The replay
        # validator below remains fail-closed and admits only rows whose
        # subsequent native metric and Judge feedback have arrived.
        "--include-invalid",
    ]
    # Forward the active checkpoint contract explicitly.  Otherwise, missing
    # optional diagnostics in a persisted decision can silently downgrade an
    # economic three-output target to the legacy two-output form.
    if int(getattr(args, "allocation_head_output_dim", 2)) >= 3:
        cmd.append("--allocation-total-head-enabled")
    reward_contract = str(getattr(args, "reward_contract", "legacy") or "legacy")
    if reward_contract != "legacy":
        cmd.extend(["--reward-contract", reward_contract])
    if str(os.environ.get("GREENRAN_TASAM_REWARD_ENERGY_ENABLED", "0")).strip().lower() in {
        "1", "true", "yes", "on",
    }:
        cmd.append("--energy-enabled")
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
    category_error_repeat: int = 0,
) -> dict[str, Any]:
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
    total_available = len(old) + len(new)
    if (
        category_error_repeat > 0
        and total_available > 0
        and any(observed_category(row) != "UNKNOWN" for row in old + new)
    ):
        # Focused energy training intentionally fills the configured replay
        # capacity and repeats only inside each disjoint online quota.
        target = max(max_rows, 0)
        mixed, quota_stats = _focused_replay(old + new, target, random.Random(seed))
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            for row in mixed:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        category_error_available = sum(1 for row in old + new if has_category_error(row))
        category_error_selected_unique = sum(
            1 for row in mixed if has_category_error(row) and not row.get("replay_sampling_copy")
        )
        category_error_repeated = sum(
            1 for row in mixed if has_category_error(row) and row.get("replay_sampling_copy")
        )
        return {
            "historical_valid": len(old),
            "recent_valid": len(new),
            "unique_available": total_available,
            "historical_used": len(old),
            "recent_used": len(new),
            "total": len(mixed),
            "prioritization": "energy_focused_40_30_20_10_v1",
            "category_error_available": category_error_available,
            "category_error_selected_unique": category_error_selected_unique,
            "category_error_in_base": category_error_selected_unique,
            "category_error_repeated": category_error_repeated,
            "category_error_in_replay": category_error_selected_unique + category_error_repeated,
            "category_error_replay_fraction": float(category_error_selected_unique + category_error_repeated) / max(len(mixed), 1),
            "category_confusion_counts": _category_confusion_counts(mixed),
            "replay_quotas": quota_stats,
            "quota_ready": bool(quota_stats.get("quota_ready", True)),
            "quota_unmet_groups": list(quota_stats.get("quota_unmet_groups", [])),
            "conditional_replay_fraction": float(quota_stats.get("conditional_transition_selected_unique", 0) + quota_stats.get("conditional_transition_repeated", 0)) / max(len(mixed), 1),
        }
    target = min(max_rows, total_available)
    if (
        category_error_repeat > 0
        and total_available < max_rows
        and any(has_category_error(row) for row in old + new)
    ):
        # A short replay may be padded only because an opted-in quota has no
        # enough unique examples. Normal runs remain capped by available rows.
        target = max_rows
    rng = random.Random(seed)
    old_target = min(len(old), int(round(target * 0.70)))
    new_target = min(len(new), target - old_target)
    if old_target + new_target < target:
        extra_old = min(len(old) - old_target, target - old_target - new_target)
        old_target += max(extra_old, 0)
    old_choice = rng.sample(old, old_target) if old_target < len(old) else old[:]
    new_choice = new[-new_target:] if new_target else []
    mixed = old_choice + new_choice
    category_error_available = sum(1 for row in old + new if has_category_error(row))
    category_error_selected_unique = 0
    category_error_repeated = 0
    prioritization = "uniform_valid_rows"
    quota_stats = {
        "conditional_available": 0,
        "conditional_selected_unique": 0,
        "conditional_repeated": 0,
        "other_error_available": 0,
        "other_error_selected_unique": 0,
        "other_error_repeated": 0,
        "correct_available": 0,
        "correct_selected_unique": 0,
        "correct_repeated": 0,
        "quota_ready": True,
        "quota_unmet_groups": [],
    }
    if category_error_repeat > 0 and total_available > 0:
        # The strong-training replay is explicitly 60/30/10: all observed
        # CONDITIONAL states (including ALLOWED -> CONDITIONAL errors), other
        # categorical errors, then recent correct non-CONDITIONAL rows.
        prioritization = "categorical_conditional_priority_60_30_10_v1"
        all_rows = old + new
        conditional_rows = [row for row in all_rows if observed_category(row) == "CONDITIONAL"]
        other_error_rows = [
            row for row in all_rows
            if has_category_error(row) and observed_category(row) != "CONDITIONAL"
        ]
        correct_rows = [
            row for row in list(reversed(new)) + old
            if not has_category_error(row) and observed_category(row) != "CONDITIONAL"
        ]
        quota_conditional = int(math.ceil(target * 0.60))
        quota_other_error = int(math.ceil(target * 0.30))
        quota_correct = max(0, target - quota_conditional - quota_other_error)
        quota_stats.update({
            "conditional_available": len(conditional_rows),
            "other_error_available": len(other_error_rows),
            "correct_available": len(correct_rows),
            "conditional_quota": quota_conditional,
            "other_error_quota": quota_other_error,
            "correct_quota": quota_correct,
        })

        def ordered(rows: list[dict[str, Any]], prioritize_errors: bool = False) -> list[dict[str, Any]]:
            result = list(rows)
            rng.shuffle(result)
            if prioritize_errors:
                result.sort(key=category_error_priority, reverse=True)
            return result

        selected_conditional, conditional_unique, conditional_repeated = _select_replay_quota(
            conditional_rows, quota_conditional, rng, prioritize_errors=True
        )
        selected_other_error, other_error_unique, other_error_repeated = _select_replay_quota(
            other_error_rows, quota_other_error, rng, prioritize_errors=True
        )
        selected_correct, correct_unique, correct_repeated = _select_replay_quota(
            correct_rows, quota_correct, rng
        )
        mixed = selected_conditional + selected_other_error + selected_correct
        quota_stats["conditional_selected_unique"] = conditional_unique
        quota_stats["conditional_repeated"] = conditional_repeated
        quota_stats["other_error_selected_unique"] = other_error_unique
        quota_stats["other_error_repeated"] = other_error_repeated
        quota_stats["correct_selected_unique"] = correct_unique
        quota_stats["correct_repeated"] = correct_repeated
        missing_groups = [
            group for group, quota, rows in (
                ("conditional", quota_conditional, conditional_rows),
                ("other_error", quota_other_error, other_error_rows),
                ("correct", quota_correct, correct_rows),
            )
            if quota > 0 and not rows
        ]
        quota_stats["quota_ready"] = not missing_groups
        quota_stats["quota_unmet_groups"] = missing_groups
        if missing_groups and any(observed_category(row) == "UNKNOWN" for row in all_rows):
            # Legacy fixtures and old databases may not carry the observed
            # category yet. Keep their historical best-effort behavior for
            # compatibility, but expose that the strict quota was unavailable.
            mixed = list(all_rows)
            error_rows = [row for row in mixed if has_category_error(row)]
            repeat_index = 0
            while len(mixed) < target and error_rows:
                source = error_rows[repeat_index % len(error_rows)]
                repeated = dict(source)
                repeated["replay_sampling_copy"] = int(repeat_index // len(error_rows)) + 1
                mixed.append(repeated)
                repeat_index += 1
            category_error_repeated = sum(
                1 for row in mixed if has_category_error(row) and row.get("replay_sampling_copy")
            )
            quota_stats["legacy_unknown_category_fallback"] = True
        category_error_selected_unique = sum(
            1 for row in mixed
            if has_category_error(row) and not row.get("replay_sampling_copy")
        )
        category_error_repeated = sum(
            1 for row in mixed if has_category_error(row) and row.get("replay_sampling_copy")
        )
        rng.shuffle(mixed)
    else:
        rng.shuffle(mixed)
        category_error_selected_unique = sum(1 for row in mixed if has_category_error(row))
        if category_error_repeat > 0 and len(mixed) < max_rows:
            prioritization = "categorical_error_oversampling_v1"
            error_rows = [row for row in mixed if has_category_error(row)]
            rng.shuffle(error_rows)
            repeat_budget = min(max_rows - len(mixed), len(error_rows) * int(category_error_repeat))
            for index in range(repeat_budget):
                repeated = dict(error_rows[index % len(error_rows)])
                repeated["replay_sampling_copy"] = int(index // len(error_rows)) + 1
                mixed.append(repeated)
                category_error_repeated += 1
            rng.shuffle(mixed)
    category_error_in_base = sum(1 for row in mixed if has_category_error(row) and not row.get("replay_sampling_copy"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in mixed:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {
        "historical_valid": len(old),
        "recent_valid": len(new),
        "unique_available": total_available,
        "historical_used": len(old_choice),
        "recent_used": len(new_choice),
        "total": len(mixed),
        "prioritization": prioritization,
        "category_error_available": category_error_available,
        "category_error_selected_unique": category_error_selected_unique,
        "category_error_in_base": category_error_in_base,
        "category_error_repeated": category_error_repeated,
        "category_error_in_replay": category_error_in_base + category_error_repeated,
        "category_error_replay_fraction": float((category_error_in_base + category_error_repeated) / max(len(mixed), 1)),
        "category_confusion_counts": _category_confusion_counts(mixed),
        "replay_quotas": quota_stats,
        "quota_ready": bool(quota_stats.get("quota_ready", True)),
        "quota_unmet_groups": list(quota_stats.get("quota_unmet_groups", [])),
        "conditional_replay_fraction": float(quota_stats.get("conditional_selected_unique", 0) + quota_stats.get("conditional_repeated", 0)) / max(len(mixed), 1),
        "other_error_replay_fraction": float(quota_stats.get("other_error_selected_unique", 0) + quota_stats.get("other_error_repeated", 0)) / max(len(mixed), 1),
        "correct_replay_fraction": float(quota_stats.get("correct_selected_unique", 0) + quota_stats.get("correct_repeated", 0)) / max(len(mixed), 1),
    }


def checkpoint_entry(checkpoint: Path, label: str, readiness: str = "shadow_ready") -> dict[str, Any]:
    required = [checkpoint / name for name in ("tasam_marl_actors.pt", "tasam_marl_checkpoint_meta.json", "tasam_marl_summary.json")]
    if not all(path.is_file() for path in required):
        raise RuntimeError(f"checkpoint incompleto: {checkpoint}")
    summary = load_json(required[2])
    meta = load_json(required[1])
    category_head = checkpoint / "tasam_marl_category_head.pt"
    curriculum = meta.get("category_head_pretraining")
    if not isinstance(curriculum, dict):
        curriculum = summary.get("category_curriculum")
    if not isinstance(curriculum, dict):
        curriculum = {}
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
        "category_head": str(category_head.resolve()) if category_head.is_file() else "",
        "category_head_available": category_head.is_file(),
        "category_head_accuracy": (summary.get("final_metrics") or {}).get("category_accuracy", 0.0),
        "category_head_loss": (summary.get("final_metrics") or {}).get("category_loss", 0.0),
        "category_curriculum": curriculum,
    }


def checkpoint_is_complete(checkpoint: Path) -> bool:
    """Return whether a candidate is safe to publish to the live manifests.

    Training writes a checkpoint directory incrementally.  The controller may
    observe the directory between the last tensor write and the metadata
    write, so callers must treat an incomplete path as a pending candidate,
    never as an exception that can terminate the collection.
    """
    return all(
        (checkpoint / name).is_file()
        for name in ("tasam_marl_actors.pt", "tasam_marl_checkpoint_meta.json", "tasam_marl_summary.json")
    )


class CandidateRetentionIntegrityError(RuntimeError):
    """Raised when pruning would risk deleting a protected checkpoint."""


def _candidate_root(path: Path, candidates_dir: Path) -> Path:
    """Normalize a checkpoint leaf to its ``candidate_XXXX`` directory.

    Checkpoint state historically stores the leaf ``candidate_XXXX/tasam_selective``
    while retention operates on the candidate root.  Comparing those two paths
    directly was the v5 data-loss bug.
    """
    candidate_parent = candidates_dir.resolve()
    current = Path(path).resolve()
    for ancestor in (current, *current.parents):
        if ancestor.parent == candidate_parent and ancestor.name.startswith("candidate_"):
            return ancestor
    return current


def _checkpoint_leaf(root: Path) -> Path:
    selective = root / "tasam_selective"
    return selective if selective.is_dir() else root


def _candidate_history_event(candidates_dir: Path, event: str, **payload: Any) -> None:
    """Append a compact, auditable candidate lifecycle event."""
    if not candidates_dir.is_dir():
        return
    history_path = candidates_dir / "candidate_history.jsonl"
    record = {
        "schema": "greenran.tasam.candidate_history.v1",
        "event": event,
        "timestamp": int(time.time()),
        **payload,
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def checkpoint_quality(checkpoint: Path) -> tuple[tuple[float, float, float, float, float], bool]:
    """Return the lexicographic category-first promotion quality."""
    try:
        # A curriculum-only bootstrap has a perfect synthetic category score,
        # but no online policy/reward history.  It is an initialization
        # baseline, not a promoted candidate, so comparing live candidates
        # against it would freeze the freshly reset actors forever.
        metadata = load_json(checkpoint / "tasam_marl_checkpoint_meta.json")
        if metadata.get("initialization_source") == "clean_curriculum_only":
            return (0.0, 0.0, 0.0, 0.0, 0.0), False
        metrics = load_json(checkpoint / "tasam_marl_summary.json").get("final_metrics") or {}
        values = (
            float(metrics.get("conditional_f1")),
            float(metrics.get("conditional_recall")),
            float(metrics.get("category_accuracy")),
            float(metrics.get("training_reward_mean", metrics.get("reward_mean"))),
            -float(metrics.get("category_loss")),
        )
    except (OSError, TypeError, ValueError, KeyError):
        return (0.0, 0.0, 0.0, 0.0, 0.0), False
    return values, True


def checkpoint_eval_return(checkpoint: Path) -> tuple[float, bool]:
    """Read the V2X learner return used only after category quality ties."""
    try:
        metrics = load_json(checkpoint / "tasam_marl_summary.json").get("final_metrics") or {}
        value = float(metrics["eval_return"])
    except (KeyError, OSError, TypeError, ValueError):
        return 0.0, False
    return (value, math.isfinite(value))


def _promotion_operational_gate(candidate: Path, active: Path) -> tuple[bool, dict[str, Any]]:
    """Apply optional service/energy gates without breaking old checkpoints."""
    candidate_summary = load_json(candidate / "tasam_marl_summary.json")
    active_summary = load_json(active / "tasam_marl_summary.json")
    candidate_metrics = candidate_summary.get("final_metrics") or {}
    active_metrics = active_summary.get("final_metrics") or {}
    keys = (
        "sla_score", "cvar_ms", "ran_completion_ratio", "ai_completion_ratio", "energy_per_mbit"
    )
    if not any(key in candidate_metrics and key in active_metrics for key in keys):
        return True, {"available": False, "reason": "legacy_checkpoint_without_operational_metrics"}
    checks: dict[str, bool] = {}
    if "sla_score" in candidate_metrics and "sla_score" in active_metrics:
        checks["sla_non_degraded"] = float(candidate_metrics["sla_score"]) >= float(active_metrics["sla_score"])
    if "cvar_ms" in candidate_metrics and "cvar_ms" in active_metrics:
        checks["cvar_non_degraded"] = float(candidate_metrics["cvar_ms"]) <= float(active_metrics["cvar_ms"])
    for key in ("ran_completion_ratio", "ai_completion_ratio"):
        if key in candidate_metrics and key in active_metrics:
            checks[f"{key}_within_1pp"] = float(candidate_metrics[key]) >= float(active_metrics[key]) - 0.01
    if "energy_per_mbit" in candidate_metrics and "energy_per_mbit" in active_metrics:
        checks["energy_per_mbit_improved"] = float(candidate_metrics["energy_per_mbit"]) < float(active_metrics["energy_per_mbit"])
    return all(checks.values()), {"available": True, "checks": checks}


def _candidate_shadow_gate(
    state: dict[str, Any], candidate: Path, active: Path
) -> tuple[bool, dict[str, Any]]:
    """Require a structurally valid, non-inferior same-trace evaluation."""
    if state.get("economic_action_contract") not in {"applied_action_v2", "economic_action_v3_per_du_sleep"}:
        return True, {"enabled": False}
    evaluation_path = Path(str(state.get("candidate_shadow_evaluation") or ""))
    if not evaluation_path.is_file():
        return False, {
            "enabled": True,
            "valid": False,
            "reason": "candidate_shadow_evaluation_missing",
        }
    evaluation = load_json(evaluation_path)
    candidates = evaluation.get("candidates") or {}
    candidate_entry = candidates.get("candidate") or {}
    active_entry = candidates.get("active") or {}
    comparison = candidate_entry.get("candidate_vs_active") or {}
    candidate_summary = candidate_entry.get("summary") or {}
    required_samples = max(1, int(state.get("promotion_window", 30) or 30))
    try:
        samples = int(comparison.get("samples", 0) or 0)
        avg_causal = float(comparison["avg_causal_score_delta"])
        avg_ran = float(comparison["avg_ran_completion_delta"])
        avg_ai = float(comparison["avg_ai_completion_delta"])
    except (KeyError, TypeError, ValueError):
        return False, {
            "enabled": True,
            "valid": False,
            "reason": "candidate_shadow_evaluation_incomplete",
        }
    candidate_dir = str(candidate.resolve())
    active_dir = str(active.resolve()) if active else ""
    checks = {
        "candidate_checkpoint_loaded": bool(candidate_entry.get("checkpoint_loaded")),
        "active_checkpoint_loaded": bool(active_entry.get("checkpoint_loaded")),
        "candidate_checkpoint_matches": str(candidate_entry.get("checkpoint_dir", "")) == candidate_dir,
        "active_checkpoint_matches": str(active_entry.get("checkpoint_dir", "")) == active_dir,
        "minimum_shadow_samples": samples >= required_samples,
        "candidate_not_worse_than_active": avg_causal >= -0.001,
        "ran_completion_within_1pp": avg_ran >= -0.01,
        "ai_completion_within_2pp": avg_ai >= -0.02,
    }
    return all(checks.values()), {
        "enabled": True,
        "valid": True,
        "required_samples": required_samples,
        "samples": samples,
        "avg_causal_score_delta": avg_causal,
        "avg_ran_completion_delta": avg_ran,
        "avg_ai_completion_delta": avg_ai,
        "candidate_summary_avg_causal_score_delta_vs_rapp": candidate_summary.get(
            "avg_causal_score_delta_vs_rapp"
        ),
        "checks": checks,
    }


def _economic_candidate_gate(state: dict[str, Any], candidate: Path) -> tuple[bool, dict[str, Any]]:
    """Require applied v2 economic evidence before a candidate can control."""
    contract_name = state.get("economic_action_contract")
    if contract_name not in {"applied_action_v2", "economic_action_v3_per_du_sleep"}:
        return True, {"enabled": False}
    metadata = load_json(candidate / "tasam_marl_checkpoint_meta.json")
    replay = metadata.get("economic_replay") or {}
    required = int(state.get("min_economic_transitions", 0) or 0)
    # Evidência absoluta (alinhamento/benefício/energia) só é exigível quando
    # o operador pediu evidência (required > 0).  Com required == 0 o replay
    # do candidato pode não ter nenhuma transição aplicada (ex.: banco
    # histórico derivado do baseline max-power) e as taxas valem 0.0 por
    # construção — exigir alinhamento ≥95% sobre dados vazios bloqueava
    # 100% das promoções do piloto (r19) sem proteção adicional: a qualidade
    # continua garantida pelo gate de não-inferioridade e pelos vetos de
    # segurança de runtime.
    evidence_required = required > 0
    available = int(replay.get("eligible_transitions", 0) or 0)
    promotion_eligible = int(replay.get("economic_promotion_eligible_transitions", 0) or 0)
    applied = int(replay.get("applied_actions", 0) or 0)
    alignment_rate = float(replay.get("projected_applied_alignment_rate", 0.0) or 0.0)
    promotion_rate = float(replay.get("promotion_beneficial_rate", 0.0) or 0.0)
    mean_energy = float(replay.get("mean_realized_energy_saving_fraction", 0.0) or 0.0)
    mean_allocation = float(replay.get("mean_realized_allocation_saving_fraction", 0.0) or 0.0)
    checks = {
        "contract_matches": metadata.get("economic_action_contract") == contract_name,
        "v10_joint_action_dim": (
            metadata.get("joint_action_dim") == 14
            if contract_name == "economic_action_v3_per_du_sleep" else True
        ),
        "bounded_total_budget_target": metadata.get("total_budget_fraction_bounds") == [0.0, 1.0],
        "economic_training_transitions": available >= required,
        "economic_promotion_transitions": promotion_eligible >= required,
        "applied_actions": applied >= required,
        "projected_applied_alignment_at_least_95pct": (
            alignment_rate >= 0.95 if evidence_required else True
        ),
        "promotion_beneficial_rate_at_least_80pct": (
            promotion_rate >= 0.80 if evidence_required else True
        ),
        "mean_realized_energy_saving_positive": (
            mean_energy > 0.0 if evidence_required else True
        ),
        "mean_realized_allocation_saving_nonnegative": (
            mean_allocation >= -0.001 if evidence_required else True
        ),
        "calibration_version_present_and_unique": (
            bool(replay.get("calibration_version_valid")) and bool(replay.get("calibration_version"))
            if evidence_required else True
        ),
    }
    return all(checks.values()), {
        "enabled": True,
        "evidence_required": evidence_required,
        "required_applied_economic_transitions": required,
        "available_applied_economic_transitions": available,
        "available_economic_promotion_transitions": promotion_eligible,
        "available_applied_actions": applied,
        "projected_applied_alignment_rate": alignment_rate,
        "promotion_beneficial_rate": promotion_rate,
        "mean_realized_energy_saving_fraction": mean_energy,
        "mean_realized_allocation_saving_fraction": mean_allocation,
        "checks": checks,
    }


def promote_candidate_if_better(state: dict[str, Any], candidate: Path, *, reason: str) -> bool:
    """Promote only a candidate that is not inferior to the active policy."""
    active = Path(str(state.get("active_checkpoint") or ""))
    if not checkpoint_is_complete(candidate):
        state["candidate_shadow_evaluation_error"] = (
            f"checkpoint_incomplete:{candidate}"
        )
        state["candidate_evaluation_blocked"] = True
        state["candidate_evaluation_blocked_reason"] = "checkpoint_incomplete"
        state["candidate_promoted"] = False
        return False
    candidate_quality, candidate_available = checkpoint_quality(candidate)
    active_quality, active_available = checkpoint_quality(active)
    candidate_return, candidate_return_available = checkpoint_eval_return(candidate)
    active_return, active_return_available = checkpoint_eval_return(active)
    operational_ok, operational_gate = _promotion_operational_gate(candidate, active)
    candidate_shadow_ok, candidate_shadow_gate = _candidate_shadow_gate(state, candidate, active)
    economic_ok, economic_gate = _economic_candidate_gate(state, candidate)
    # Empty/legacy summaries retain the historical behavior for compatibility.
    if not candidate_available or not active_available:
        better = True
    elif candidate_quality > active_quality:
        better = True
    elif candidate_quality == active_quality:
        # Category/SLA quality remains primary.  A tied candidate may replace
        # the active policy only when its frozen evaluation return is higher.
        better = (
            candidate_return_available
            and active_return_available
            and candidate_return > active_return + 1e-9
        )
    else:
        better = False
    better = bool(better and operational_ok and candidate_shadow_ok and economic_ok)
    state["promotion_comparison"] = {
        "reason": reason,
        "candidate": str(candidate.resolve()),
        "active": str(active.resolve()) if active else "",
        "candidate_quality": list(candidate_quality),
        "active_quality": list(active_quality),
        "candidate_quality_available": candidate_available,
        "active_quality_available": active_available,
        "candidate_eval_return": candidate_return if candidate_return_available else None,
        "active_eval_return": active_return if active_return_available else None,
        "eval_return_tiebreak_used": candidate_quality == active_quality,
        "promoted": better,
        "operational_gate": operational_gate,
        "candidate_shadow_gate": candidate_shadow_gate,
        "economic_gate": economic_gate,
    }
    if not better:
        state["candidate_rejected"] = True
        state["candidate_rejection_reason"] = (
            "candidate_shadow_non_inferior_gate"
            if not candidate_shadow_ok
            else (
                "economic_action_contract_or_evidence_invalid"
                if not economic_ok else "inferior_category_or_eval_return_quality"
            )
        )
        state["candidate_promoted"] = False
        state["last_candidate_rejection"] = {
            "candidate": str(candidate.resolve()),
            "reason": state["candidate_rejection_reason"],
            "comparison": state.get("promotion_comparison", {}),
            "timestamp": int(time.time()),
        }
        state["last_rejected_checkpoint"] = str(candidate.resolve())
        # ``candidate_checkpoint`` denotes a pending candidate only.  Keep the
        # rejected artifact through ``last_rejected_checkpoint`` for audit, so
        # the controller cannot repeatedly re-evaluate the same rejection or
        # confuse it with the active policy on restart.
        state["candidate_checkpoint"] = ""
        state.pop("candidate_shadow_checkpoint", None)
        state.pop("candidate_shadow_started_decisions", None)
        state.pop("candidate_shadow_until_decisions", None)
        candidates_dir = candidate.resolve().parent.parent
        _candidate_history_event(
            candidates_dir,
            "rejected",
            candidate=str(candidate.resolve()),
            reason=state["candidate_rejection_reason"],
            active_checkpoint=str(state.get("active_checkpoint") or ""),
            active_checkpoint_promoted=bool(state.get("active_checkpoint_promoted", False)),
        )
        return False
    state["last_good_checkpoint"] = state.get("active_checkpoint")
    state["previous_promoted_checkpoint"] = state.get("last_promoted_checkpoint", "")
    state["active_checkpoint"] = str(candidate.resolve())
    state["candidate_checkpoint"] = ""
    state["candidate_promoted"] = True
    state["active_checkpoint_promoted"] = True
    state["last_promoted_checkpoint"] = str(candidate.resolve())
    state["last_promoted_update_id"] = int(state.get("updates_completed", 0) or 0)
    state["promotion_count"] = int(state.get("promotion_count", 0) or 0) + 1
    state.pop("last_candidate_rejection", None)
    state.pop("candidate_rejected", None)
    state.pop("candidate_rejection_reason", None)
    candidates_dir = candidate.resolve().parent.parent
    _candidate_history_event(
        candidates_dir,
        "promoted",
        candidate=str(candidate.resolve()),
        active_checkpoint=str(candidate.resolve()),
        update_id=state["last_promoted_update_id"],
        promotion_count=state["promotion_count"],
    )
    return True


def prune_candidate_artifacts(args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    """Retain only checkpoints that can still affect the live policy.

    Candidate summaries and hashes remain auditable in JSONL; intermediate
    tensor directories are disposable outputs of this campaign only.
    """
    candidates_dir = args.state_dir / "candidates"
    if not candidates_dir.is_dir():
        return {"removed": 0, "removed_bytes": 0, "retained": []}
    preserve: set[Path] = set()
    protected_keys = (
        "active_checkpoint", "last_good_checkpoint", "candidate_checkpoint",
        "last_promoted_checkpoint", "last_rejected_checkpoint",
    )
    for key in protected_keys:
        value = state.get(key)
        if value:
            preserve.add(_candidate_root(Path(str(value)), candidates_dir))
    # A rejection is kept for audit, but must never replace the active policy.
    rejection = state.get("last_candidate_rejection") or {}
    if isinstance(rejection, dict) and rejection.get("candidate"):
        preserve.add(_candidate_root(Path(str(rejection["candidate"])), candidates_dir))
    candidate_dirs = sorted(
        (path for path in candidates_dir.glob("candidate_*") if path.is_dir()),
        key=lambda path: path.name,
    )
    # The arm's disk-budget monitor can observe the directory between the
    # learner finishing its tensors and the controller atomically publishing
    # online_state.json.  Preserve the newest generated candidate during that
    # hand-off even if the monitor still has the previous state snapshot.
    # Otherwise a valid pending candidate can be deleted as an "old rejected"
    # artifact and the controller later sees a dangling path.
    if candidate_dirs:
        preserve.add(candidate_dirs[-1].resolve())
    # Fail closed before deleting anything.  A configured protected path that
    # disappeared indicates an integrity problem, not a disposable artifact.
    missing_protected = []
    for protected in sorted(preserve):
        if protected.parent != candidates_dir.resolve():
            continue
        leaf = _checkpoint_leaf(protected)
        if not checkpoint_is_complete(leaf):
            missing_protected.append(str(leaf))
    if missing_protected:
        raise CandidateRetentionIntegrityError(
            "checkpoint protegido ausente ou incompleto: " + ", ".join(missing_protected)
        )
    rejected_kept = 0
    removed: list[dict[str, Any]] = []
    history_path = candidates_dir / "candidate_history.jsonl"
    for path in candidate_dirs:
        if path.resolve() in preserve:
            continue
        # Retain one rejected candidate for a direct audit; older rejected
        # candidates are represented by their manifest and SHA-256 below.
        if rejected_kept < 1:
            rejected_kept += 1
            continue
        meta = path / "tasam_selective" / "tasam_marl_checkpoint_meta.json"
        digest = ""
        if meta.is_file():
            digest = hashlib.sha256(meta.read_bytes()).hexdigest()
        size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        removed.append({"path": str(path.resolve()), "bytes": size, "metadata_sha256": digest})
        shutil.rmtree(path)
    if removed:
        with history_path.open("a", encoding="utf-8") as handle:
            for item in removed:
                handle.write(json.dumps({
                    "schema": "greenran.tasam.candidate_history.v1",
                    "event": "pruned",
                    "timestamp": int(time.time()),
                    "active_checkpoint": state.get("active_checkpoint", ""),
                    "last_promoted_checkpoint": state.get("last_promoted_checkpoint", ""),
                    **item,
                }, ensure_ascii=False) + "\n")
    # Verify every protected root after pruning as well.  This catches an
    # unexpected external cleanup or a future retention change immediately.
    for protected in sorted(preserve):
        if protected.parent == candidates_dir.resolve() and not checkpoint_is_complete(_checkpoint_leaf(protected)):
            raise CandidateRetentionIntegrityError(
                f"checkpoint protegido perdeu integridade após poda: {protected}"
            )
    return {
        "removed": len(removed),
        "removed_bytes": sum(int(item["bytes"]) for item in removed),
        "retained": [str(path.resolve()) for path in candidate_dirs if path.exists()],
    }


def _annotate_economic_candidate(
    candidate: Path,
    active: Path,
    replay: dict[str, Any],
    contract_name: str = "applied_action_v2",
) -> dict[str, Any]:
    """Version the derived candidate without mutating its parent checkpoint."""
    metadata_path = candidate / "tasam_marl_checkpoint_meta.json"
    metadata = load_json(metadata_path)
    parent_actor = active / "tasam_marl_actors.pt"
    metadata.update({
        "economic_action_contract": contract_name,
        "allocation_target_contract": (
            "economic_per_du_sleep_v3"
            if contract_name == "economic_action_v3_per_du_sleep"
            else "economic_applied_action_v2"
        ),
        "total_budget_fraction_bounds": [0.0, 1.0],
        "economic_safety_isolation": "blocked_and_critical_v1",
        "economic_thresholds": {
            "training_energy_saving_min": 0.005,
            "training_allocation_saving_min": -0.001,
        },
        "parent_checkpoint_sha256": sha256_file(parent_actor) if parent_actor.is_file() else "",
        "economic_replay": replay,
    })
    save_json(metadata_path, metadata)
    return metadata


def _stage_window(args: argparse.Namespace, state: dict[str, Any]) -> int:
    return int(
        getattr(args, "shadow_min_decisions", 30)
        if state.get("stage") == "shadow"
        else getattr(args, "stage_window_decisions", args.promotion_window)
    )


def _max_stage_index(state: dict[str, Any]) -> int:
    maximum = float(state.get("max_rollout_fraction", 1.0) or 1.0)
    allowed = [index for index, (_, fraction) in enumerate(STAGES) if fraction <= maximum + 1e-9]
    return max(allowed) if allowed else 0


def publish_manifests(args: argparse.Namespace, state: dict[str, Any], checkpoint: Path, readiness: str = "shadow_ready") -> bool:
    """Publish the checkpoint that is actually allowed to control GreenRAN.

    A pending candidate is intentionally not published here.  Candidate
    evaluation has its own manifest so the active TA-SAM policy can keep
    allocating resources while the next policy is scored offline.
    """
    if not checkpoint_is_complete(checkpoint):
        state["candidate_shadow_evaluation_error"] = f"checkpoint_incomplete:{checkpoint}"
        state["candidate_evaluation_blocked"] = True
        state["candidate_evaluation_blocked_reason"] = "checkpoint_incomplete"
        return False
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
    return True


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
    database = args.state_dir / "rapp_data_lake.db"
    if not database.exists():
        state["candidate_shadow_evaluation_error"] = f"SQLite ausente: {database}"
        return {}, output
    try:
        completed = subprocess.run(
            [
                str(args.train_python),
                str(ROOT / "scripts/evaluate_tasam_same_trace_shadow.py"),
                "--sqlite-db", str(database),
                "--candidate", f"active={active}",
                "--candidate", f"candidate={candidate}",
                "--output", str(output),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
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
    except subprocess.CalledProcessError as exc:
        stderr = str(exc.stderr or "")[-4000:]
        state["candidate_shadow_evaluation_error"] = json.dumps({
            "reason": "candidate_evaluation_blocked",
            "returncode": exc.returncode,
            "stderr": stderr,
        }, ensure_ascii=False)
        state["candidate_evaluation_blocked"] = True
        state["candidate_evaluation_blocked_reason"] = "candidate_evaluation_blocked"
        return {}, output
    except subprocess.TimeoutExpired as exc:
        stderr = str(exc.stderr or "")[-4000:]
        state["candidate_shadow_evaluation_error"] = json.dumps({
            "reason": "candidate_evaluation_blocked",
            "timeout_seconds": 120,
            "stderr": stderr,
        }, ensure_ascii=False)
        state["candidate_evaluation_blocked"] = True
        state["candidate_evaluation_blocked_reason"] = "candidate_evaluation_blocked"
        return {}, output
    except OSError as exc:
        state["candidate_shadow_evaluation_error"] = json.dumps({
            "reason": "candidate_evaluation_blocked",
            "error": str(exc),
        }, ensure_ascii=False)
        state["candidate_evaluation_blocked"] = True
        return {}, output


def save_rollout_manifest(args: argparse.Namespace, state: dict[str, Any], **extra: Any) -> None:
    """Persist active rollout and candidate-shadow state atomically."""
    rollout = {
        "stage": state.get("stage", "shadow"),
        "fraction": float(state.get("rollout_fraction", 0.0) or 0.0),
        "checkpoint": state.get("active_checkpoint", ""),
        "max_rollout_fraction": float(state.get("max_rollout_fraction", 1.0) or 1.0),
        "economic_action_contract": state.get("economic_action_contract", ""),
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
            candidate = Path(state["candidate_checkpoint"])
            if promote_candidate_if_better(state, candidate, reason="full_control_update"):
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
    if guard.get("rollback") and not (
        state.get("stage") == "rollback"
        and state.get("rollback_reason") == guard.get("reason", "guard failure")
    ):
        if state.get("last_good_checkpoint"):
            state["active_checkpoint"] = state["last_good_checkpoint"]
            previous = state.get("previous_promoted_checkpoint")
            if previous:
                state["last_promoted_checkpoint"] = previous
            state["active_checkpoint_promoted"] = bool(
                state.get("last_promoted_checkpoint")
                and Path(str(state["last_promoted_checkpoint"])).exists()
            )
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
    # Economic v2 starts with a versioned bootstrap checkpoint.  It must be
    # allowed to collect applied canary evidence before a freshly trained
    # candidate can satisfy the promotion gate, so lack of a candidate does
    # not freeze shadow forever.
    if (
        not state.get("candidate_checkpoint")
        and not state.get("candidate_promoted", False)
        and state.get("economic_action_contract") not in ECONOMIC_ACTION_CONTRACTS
    ):
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
        candidate = Path(state["candidate_checkpoint"])
        if not promote_candidate_if_better(state, candidate, reason="full_shadow_window"):
            return
        state.pop("candidate_shadow_started_decisions", None)
        state.pop("candidate_shadow_until_decisions", None)
        state["stage"] = "full"
        state["rollout_fraction"] = 1.0
        state["stage_started_decisions"] = decision_count
        publish_manifests(args, state, Path(state["active_checkpoint"]), "control_candidate")
        save_rollout_manifest(args, state)
        return
    # At the configured rollout ceiling there is no further stage transition,
    # but a pending candidate still needs its post-update shadow window. The
    # previous controller stopped at canary_50 without ever running the
    # promotion gate.
    maximum_stage = _max_stage_index(state)
    current = next((idx for idx, (name, _) in enumerate(STAGES) if name == state.get("stage")), 0)
    if (
        current >= maximum_stage
        and state.get("candidate_checkpoint")
        and not state.get("candidate_promoted", False)
        and state.get("stage") != "full"
    ):
        candidate = str(Path(state["candidate_checkpoint"]).resolve())
        if state.get("candidate_shadow_checkpoint") != candidate:
            state["candidate_shadow_checkpoint"] = candidate
            state["candidate_shadow_started_decisions"] = decision_count
            state["candidate_shadow_until_decisions"] = decision_count + args.promotion_window
            save_rollout_manifest(args, state)
            return
        shadow_started = int(state.get("candidate_shadow_started_decisions", decision_count) or decision_count)
        if decision_count - shadow_started < args.promotion_window:
            return
        if state.get("candidate_shadow_evaluation_error"):
            return
        if promote_candidate_if_better(state, Path(candidate), reason="max_rollout_shadow_window"):
            state.pop("candidate_shadow_checkpoint", None)
            state.pop("candidate_shadow_started_decisions", None)
            state.pop("candidate_shadow_until_decisions", None)
            state["stage"] = state.get("stage", "canary_50")
            state["rollout_fraction"] = float(state.get("max_rollout_fraction", 0.50) or 0.50)
            state["stage_started_decisions"] = decision_count
            publish_manifests(args, state, Path(state["active_checkpoint"]), "control_candidate")
            save_rollout_manifest(args, state, reason="candidate_promoted_at_rollout_ceiling")
        return
    if decision_count - int(state.get("stage_started_decisions", 0) or 0) < _stage_window(args, state):
        return
    current = next((idx for idx, (name, _) in enumerate(STAGES) if name == state.get("stage")), 0)
    maximum_stage = _max_stage_index(state)
    if current >= maximum_stage:
        return
    next_name, next_fraction = STAGES[current + 1]
    candidate = state.get("candidate_checkpoint")
    if candidate:
        if not promote_candidate_if_better(state, Path(candidate), reason="stage_transition"):
            # Keep the active checkpoint and collect the next approved
            # rollout tranche.  A rejected candidate cannot block evidence
            # collection for the v2 bootstrap policy, and it is never
            # counted as a promoted economic action.
            if state.get("economic_action_contract") not in ECONOMIC_ACTION_CONTRACTS:
                return
    state["stage"] = next_name
    state["rollout_fraction"] = next_fraction
    state["stage_started_decisions"] = decision_count
    # Publish after the stage transition so the evaluation gate and rollout
    # manifest describe the same effective policy.  In particular, never
    # publish an empty candidate path after promotion to full rollout.
    # The pending candidate is evaluated separately and must never be
    # published merely because a rollout window elapsed.  In particular, a
    # candidate can still be incomplete while the learner is finalizing its
    # metadata.  Keep the last complete active checkpoint authoritative and
    # leave the pending candidate visible only in the audit state.
    publish_manifests(
        args,
        state,
        Path(state["active_checkpoint"]),
        "control_candidate" if next_fraction > 0.0 else "shadow_ready",
    )
    save_rollout_manifest(args, state)


def run_update(args: argparse.Namespace, state: dict[str, Any], snapshot_count: int) -> dict[str, Any]:
    update_id = int(state.get("updates_completed", 0) or 0) + 1
    update_dir = args.state_dir / "candidates" / f"candidate_{update_id:04d}"
    update_dir.mkdir(parents=True, exist_ok=True)
    recent_trace = update_dir / "recent_trace.jsonl"
    export_summary = update_dir / "recent_export_summary.json"
    economic_contract = state.get("economic_action_contract") in {
        "applied_action_v2", "economic_action_v3_per_du_sleep"
    }
    sqlite_economic_replay = bool(
        economic_contract and getattr(args, "sqlite_economic_replay", False)
    )
    v2x_replay_80_20 = getattr(args, "replay_policy", "legacy") == "v2x_80_20"
    v2x_replay_window90 = getattr(args, "replay_policy", "legacy") == "v2x_window90"
    v2x_replay = v2x_replay_80_20 or v2x_replay_window90
    replay_schema = V2X_REPLAY_WINDOW90_SCHEMA if v2x_replay_window90 else V2X_REPLAY_80_20_SCHEMA
    # The energy V3 pilot deliberately combines the economic action head with
    # the window90 72/18 replay.  It must use the JSONL V2X evidence contract;
    # the SQLite economic history is a different, legacy replay source and is
    # rejected only when explicitly requested alongside the V2X policy.
    if v2x_replay and sqlite_economic_replay:
        raise ValueError("replay V2X window90 não pode usar SQLite econômico")
    if v2x_replay and not args.experience_bank:
        raise ValueError("replay V2X 80/20 exige --experience-bank histórico")
    if v2x_replay and not args.recent_experience_bank:
        raise ValueError("replay V2X 80/20 exige --recent-experience-bank privado")
    # The durable economic bank already contains every training transition.
    # Exporting a second article trace here used to duplicate multi-gigabyte
    # decision snapshots before the learner even started.
    if not sqlite_economic_replay:
        export_recent(args, recent_trace, export_summary)
    if v2x_replay:
        _annotate_v2x_recent_trace(recent_trace, args, update_id)
    bank_manifest = None
    if args.experience_bank and not sqlite_economic_replay and not v2x_replay:
        bank_manifest = persist_experience_bank(
            args.experience_bank, recent_trace, args.state_dir.name
        )
    active = Path(state["active_checkpoint"])
    du_count, du_state_dim, global_state_dim = _checkpoint_dimensions(active)
    temporal_dim = _checkpoint_temporal_dim(active)
    # The candidate continues the active policy, so head dimensions and the
    # power grid must come from the active checkpoint meta — not from CLI
    # defaults.  A 3-output allocation head trained with the default 2 made
    # every online update crash with a state_dict size mismatch.
    try:
        active_meta = json.loads(
            (active / "tasam_marl_checkpoint_meta.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        active_meta = {}
    meta_allocation_dim = len(active_meta.get("allocation_head_outputs") or [])
    allocation_dim = (
        meta_allocation_dim if meta_allocation_dim in (2, 3)
        else int(getattr(args, "allocation_head_output_dim", 2))
    )
    meta_global_dim = int(active_meta.get("global_action_dim") or 0)
    if meta_global_dim in (3, 5):
        update_global_dim = meta_global_dim
    elif state.get("economic_action_contract") == "economic_action_v3_per_du_sleep":
        update_global_dim = 5
    else:
        update_global_dim = int(getattr(args, "global_action_dim", 3))
    update_env = dict(os.environ)
    active_power_classes = [float(v) for v in (active_meta.get("power_head_classes") or [])]
    if active_power_classes and 0.0 in active_power_classes:
        update_env["GREENRAN_TASAM_POWER_LEVELS"] = "v10"
    else:
        update_env.pop("GREENRAN_TASAM_POWER_LEVELS", None)
    # Economic v12 uses the SQLite transition table as its durable bank.  It
    # is deliberately independent of the most recent 600-row JSONL tail.
    replay_recent = Path("/dev/null") if args.experience_bank else recent_trace
    replay_path = args.state_dir / "replay_buffer" / f"replay_update_{update_id:04d}.jsonl"
    if v2x_replay:
        # Sample the bank before appending the current export.  Otherwise the
        # current phase can be silently counted as both historical and recent.
        bank_manifest = persist_experience_bank(
            args.recent_experience_bank, recent_trace, args.state_dir.name
        )
        replay = (
            build_v2x_replay_window90(
                args.experience_bank, args.recent_experience_bank,
                replay_path, args.seed + update_id, run_seed=args.seed,
                strict_recent_evidence=True,
            )
            if v2x_replay_window90 else
            build_v2x_replay_80_20(
                args.experience_bank, args.recent_experience_bank,
                replay_path, args.seed + update_id, args.replay_rows,
                run_seed=args.seed, strict_recent_evidence=True,
            )
        )
    elif economic_contract and getattr(args, "sqlite_economic_replay", False):
        replay = build_sqlite_economic_replay(
            args.state_dir / "rapp_data_lake.db",
            replay_path,
            args.seed + update_id,
            args.replay_rows,
        )
    elif args.experience_bank:
        replay = build_persistent_bank_replay(
            args.experience_bank,
            replay_path,
            args.seed + update_id,
            args.replay_rows,
            expected_du_count=du_count,
            expected_du_state_dim=du_state_dim,
            expected_global_state_dim=global_state_dim,
            category_error_repeat=(
                args.category_error_repeat
                if args.prioritize_category_errors else 0
            ),
        )
    else:
        replay = build_replay(
            args.historical_trace,
            replay_recent,
            replay_path,
            args.seed + update_id,
            args.replay_rows,
            expected_du_count=du_count,
            expected_du_state_dim=du_state_dim,
            expected_global_state_dim=global_state_dim,
            category_error_repeat=(
                args.category_error_repeat
                if args.prioritize_category_errors else 0
            ),
        )
    if v2x_replay and not replay.get("quota_ready", False):
        state["last_update_snapshot_count"] = snapshot_count
        state["last_replay"] = replay
        save_json(update_dir / "replay_manifest.json", {
            "schema": replay_schema,
            "update_id": update_id,
            "replay": replay,
            "training_started": False,
        })
        return {
            "status": "waiting_replay_80_20_quota",
            "update_id": update_id,
            "snapshot_count": snapshot_count,
            "replay": replay,
            "experience_bank": bank_manifest,
        }
    unmet_groups = set(replay.get("quota_unmet_groups", []))
    if args.prioritize_category_errors and "conditional_transition" in unmet_groups:
        # Do not train without the central state-transition signal. Optional
        # strata (for example, high-power healthy decisions) may be absent in
        # a short pilot; keep the gap explicit in the manifest and train on
        # the available online strata instead of stalling the controller.
        state["last_update_snapshot_count"] = snapshot_count
        if sqlite_economic_replay:
            replay_path.unlink(missing_ok=True)
        return {
            "status": "waiting_replay_quota",
            "update_id": update_id,
            "snapshot_count": snapshot_count,
            "replay": replay,
            "experience_bank": bank_manifest,
        }
    if v2x_replay:
        save_json(update_dir / "replay_manifest.json", {
            "schema": replay_schema,
            "update_id": update_id,
            "replay": replay,
            "training_started": True,
            "evaluation_excluded": True,
        })
    training_trace = replay_path
    economic_filter = None
    economic_v2x_replay = bool(economic_contract and v2x_replay)
    if economic_contract and not economic_v2x_replay:
        economic_trace = replay_path if sqlite_economic_replay else (
            args.state_dir / "replay_buffer" / f"economic_replay_update_{update_id:04d}.jsonl"
        )
        economic_filter = (
            {
                "source_rows": replay.get("source_rows", 0),
                "eligible_rows": replay.get("eligible_rows", 0),
                "rehydrated_rows": replay.get("rehydrated_rows", 0),
                "rejected_rows": replay.get("rejected_rows", 0),
                "rejected_reasons": replay.get("rejected_reasons", {}),
                "temporary_bytes": replay.get("temporary_bytes", 0),
                "source": "sqlite_exact_state",
            }
            if getattr(args, "sqlite_economic_replay", False)
            else filter_economic_replay(replay_path, economic_trace)
        )
        replay["economic_filter"] = economic_filter
        save_json(update_dir / "replay_manifest.json", {
            "schema": "greenran.tasam.economic_replay_manifest.v2",
            "update_id": update_id,
            "source": "sqlite_exact_state" if sqlite_economic_replay else "jsonl",
            "replay": replay,
            "filter": economic_filter,
        })
        if economic_filter["eligible_rows"] < args.economic_update_min_transitions:
            state["last_update_snapshot_count"] = snapshot_count
            state["last_economic_replay"] = {
                **(economic_replay_evidence(economic_trace) if economic_trace.exists() else {}),
                "filter": economic_filter,
            }
            for temporary in (recent_trace, replay_path if sqlite_economic_replay else economic_trace):
                temporary.unlink(missing_ok=True)
            return {
                "status": "waiting_economic_dataset",
                "update_id": update_id,
                "snapshot_count": snapshot_count,
                "replay": replay,
                "economic_replay": state["last_economic_replay"],
                "experience_bank": bank_manifest,
            }
    # Repeated quota fills are useful for weighting, but must never satisfy
    # the minimum-data gate by themselves. Require that many unique online
    # transitions before starting a learner update.
    minimum_trainable = (
        args.economic_update_min_transitions
        if economic_contract and not economic_v2x_replay else args.min_trainable_transitions
    )
    available_trainable = (
        economic_filter["eligible_rows"]
        if economic_contract and not economic_v2x_replay and economic_filter is not None
        else replay.get("unique_available", 0)
    )
    if available_trainable < minimum_trainable:
        for temporary in (recent_trace, replay_path if sqlite_economic_replay else None):
            if temporary:
                Path(temporary).unlink(missing_ok=True)
        return {
            "status": "waiting_dataset",
            "update_id": update_id,
            "snapshot_count": snapshot_count,
            "replay": replay,
            "experience_bank": bank_manifest,
        }
    candidate = update_dir / str(args.sam_mode)
    train_python = str(args.train_python if args.train_python.exists() else sys.executable)
    cmd = [
        train_python, str(ROOT / "drlexp/training/train_tasam_marl.py"),
        "--trace-jsonl", str(
            economic_trace if economic_contract and not economic_v2x_replay else replay_path
        ),
        "--output-dir", str(candidate), "--epochs", str(args.epochs_per_update),
        "--trainer-backend", "article_sac", "--sam-mode", str(args.sam_mode),
        "--lr", str(args.learning_rate), "--l2-weight", str(args.l2_weight),
        "--actor-sam-rho", "0.5", "--actor-sam-rho-final", "0.01",
        "--critic-sam-rho", "0.5", "--critic-sam-rho-final", "0.01",
        "--td-var-threshold", "0.01", "--warmup-epochs", "1", "--bc-weight", "0.0",
        "--value-weight", "0.0", "--gamma", "0.99", "--tau", "0.01", "--alpha-init", "0.03",
        "--target-entropy-scale", "1.0", "--batch-size", "128", "--seed", str(args.seed),
        "--category-loss-weight", str(args.category_loss_weight),
        "--category-head-hidden-dim", str(args.category_head_hidden_dim),
        "--allocation-head-output-dim", str(allocation_dim),
        "--global-action-dim", str(update_global_dim),
        "--temporal-dim", str(temporal_dim),
        "--power-head-hidden-dim", "64",
        "--power-head-lr", "0.001",
        "--power-head-steps", "10",
        "--allocation-head-hidden-dim", "64",
        "--allocation-head-lr", "0.001",
        "--allocation-head-steps", "10",
        "--article-hidden", "--activation", "tanh", "--init-checkpoint-dir", str(active),
        "--checkpoint-every", str(args.epochs_per_update),
    ]
    try:
        subprocess.run(cmd, cwd=ROOT, check=True, env=update_env)
    except (subprocess.CalledProcessError, OSError) as exc:
        # Do not let one candidate failure stop the real collection.  Mark
        # this snapshot watermark so the next attempt waits for a fresh
        # online batch instead of retrying every polling interval.
        state["last_update_snapshot_count"] = snapshot_count
        state["last_update_error"] = str(exc)
        state["last_economic_replay"] = {
            "filter": economic_filter,
            "training_status": "failed",
            "training_error": str(exc),
        }
        for temporary in (
            recent_trace,
            replay_path if sqlite_economic_replay else economic_trace if economic_contract and not economic_v2x_replay else None,
        ):
            if temporary:
                Path(temporary).unlink(missing_ok=True)
        return {
            "status": "training_failed",
            "update_id": update_id,
            "snapshot_count": snapshot_count,
            "replay": replay,
            "experience_bank": bank_manifest,
            "error": str(exc),
        }
    if v2x_replay:
        _annotate_v2x_candidate(candidate, active, args, replay)
    economic_replay = economic_replay_evidence(
        economic_trace if economic_contract and not economic_v2x_replay else replay_path
    )
    if economic_contract and economic_filter is not None:
        economic_replay["filter"] = economic_filter
        economic_replay["replay_schema"] = replay.get("replay_schema", "")
        economic_replay["rehydrated_rows"] = replay.get("rehydrated_rows", 0)
        economic_replay["rejected_rows"] = replay.get("rejected_rows", 0)
        economic_replay["rejected_reasons"] = replay.get("rejected_reasons", {})
    if economic_contract:
        _annotate_economic_candidate(
            candidate,
            active,
            economic_replay,
            state.get("economic_action_contract") or "applied_action_v2",
        )
    state["candidate_checkpoint"] = str(candidate.resolve())
    state["candidate_promoted"] = False
    state["updates_completed"] = update_id
    state["last_update_snapshot_count"] = snapshot_count
    state["last_update_at"] = int(time.time())
    state["last_replay"] = replay
    state["last_economic_replay"] = economic_replay
    state.pop("last_update_error", None)
    state["history"].append({
        "update_id": update_id,
        "candidate": str(candidate.resolve()),
        "replay": replay,
        "economic_replay": economic_replay,
        "snapshot_count": snapshot_count,
    })
    if full_control_mode():
        # O gate de não-inferioridade (promote_candidate_if_better ->
        # _candidate_shadow_gate) consome o artefato apontado por
        # state["candidate_shadow_evaluation"].  Este ramo pulava
        # evaluate_candidate_shadow, o artefato nunca existia e toda
        # promoção falhava com candidate_shadow_evaluation_missing
        # (piloto r19: promotion_count=0 em 239 decisões).  Avalie o
        # candidato antes de promover; o rigor do gate permanece intacto.
        shadow_evaluation, shadow_evaluation_path = evaluate_candidate_shadow(
            args, state, active, candidate,
            replay_path if sqlite_economic_replay else recent_trace,
        )
        state["candidate_shadow_evaluation"] = str(shadow_evaluation_path.resolve())
        publish_candidate_shadow_manifest(args, state, candidate, shadow_evaluation)
        if not promote_candidate_if_better(state, candidate, reason="full_control_update_result"):
            save_rollout_manifest(args, state, stage="full", fraction=1.0, reason="candidate_not_promoted")
            return {
                "status": "candidate_rejected",
                "update_id": update_id,
                "candidate": str(candidate),
                "replay": replay,
                "experience_bank": bank_manifest,
            }
        state["stage"] = "full"
        state["rollout_fraction"] = 1.0
        state["candidate_promoted_directly"] = True
        publish_manifests(args, state, candidate, "control_candidate")
        save_rollout_manifest(args, state, stage="full", fraction=1.0, reason="tasam_full_control candidate promoted")
        return {
            "status": "candidate_promoted",
            "update_id": update_id,
            "candidate": str(candidate),
            "replay": replay,
            "experience_bank": bank_manifest,
        }
    if state.get("stage") == "full":
        state["candidate_shadow_started_decisions"] = count_decisions(args.db)
        state["candidate_shadow_until_decisions"] = (
            state["candidate_shadow_started_decisions"] + args.promotion_window
        )
    shadow_evaluation, shadow_evaluation_path = evaluate_candidate_shadow(
        args, state, active, candidate,
        replay_path if sqlite_economic_replay else recent_trace,
    )
    if sqlite_economic_replay:
        # The durable copy remains SQLite.  The compact replay must survive
        # long enough for same-trace candidate evaluation, then be removed.
        replay_path.unlink(missing_ok=True)
    state["candidate_shadow_evaluation"] = str(shadow_evaluation_path.resolve())
    publish_manifests(args, state, active, "control_candidate")
    publish_candidate_shadow_manifest(args, state, candidate, shadow_evaluation)
    state["candidate_retention"] = prune_candidate_artifacts(args, state)
    save_json(args.state_json, state)
    return {
        "status": "candidate_ready",
        "update_id": update_id,
        "candidate": str(candidate),
        "replay": replay,
        "experience_bank": bank_manifest,
    }


def normalize(args: argparse.Namespace) -> argparse.Namespace:
    args.state_dir = Path(args.state_dir)
    args.db = Path(args.db) if args.db else args.state_dir / "rapp_data_lake.db"
    args.historical_trace = Path(args.historical_trace)
    args.experience_bank = Path(args.experience_bank) if args.experience_bank else None
    args.recent_experience_bank = Path(args.recent_experience_bank) if args.recent_experience_bank else None
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
    parser.add_argument(
        "--experience-bank",
        default=None,
        help="banco persistente de experiências anteriores para bootstrap explícito",
    )
    parser.add_argument(
        "--recent-experience-bank",
        default=None,
        help="banco mutável exclusivo do arm para as 20%% transições recentes",
    )
    parser.add_argument(
        "--online-only",
        action="store_true",
        help="usa somente transições reais exportadas desta execução; não carrega replay histórico",
    )
    parser.add_argument("--checkpoint", default=str(BASE_CHECKPOINT))
    parser.add_argument("--train-python", default=str(PYTHON))
    parser.add_argument("--min-new-snapshots", type=int, default=500)
    parser.add_argument("--min-trainable-transitions", type=int, default=180)
    parser.add_argument("--replay-rows", type=int, default=600)
    parser.add_argument(
        "--replay-policy",
        choices=("legacy", "v2x_80_20", "v2x_window90"),
        default="legacy",
        help="contrato de amostragem; v2x_80_20 usa somente 80%% histórico e 20%% recente",
    )
    parser.add_argument(
        "--reward-contract", default="legacy",
        help="contrato de recompensa exportado antes da amostragem do replay",
    )
    parser.add_argument(
        "--sam-mode",
        choices=("tasam_selective", "l2"),
        default="tasam_selective",
        help="TA-SAM seletivo ou baseline SAC com regularização L2",
    )
    parser.add_argument("--l2-weight", type=float, default=0.0)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument(
        "--prioritize-category-errors",
        action="store_true",
        help="mantém todas as transições válidas e repete os erros categóricos no replay",
    )
    parser.add_argument(
        "--category-error-repeat",
        type=int,
        default=1,
        help="cópias adicionais por erro categórico quando a priorização está ativa",
    )
    parser.add_argument("--category-loss-weight", type=float, default=0.5)
    parser.add_argument("--category-head-hidden-dim", type=int, default=64)
    parser.add_argument("--allocation-head-output-dim", type=int, choices=(2, 3), default=2)
    parser.add_argument("--global-action-dim", type=int, choices=(3, 5), default=3)
    parser.add_argument("--epochs-per-update", type=int, default=3)
    parser.add_argument("--promotion-window", type=int, default=30)
    parser.add_argument(
        "--shadow-min-decisions", type=int, default=30,
        help="minimum real-PDCP decisions before leaving shadow",
    )
    parser.add_argument(
        "--stage-window-decisions", type=int, default=30,
        help="real-PDCP decisions per guarded rollout stage",
    )
    parser.add_argument(
        "--max-rollout-fraction", type=float, default=1.0,
        help="hard rollout cap; economic v2 uses 0.50 and never reaches full-control",
    )
    parser.add_argument(
        "--min-economic-transitions", type=int, default=0,
        help="minimum applied v2 economic transitions required for promotion",
    )
    parser.add_argument(
        "--economic-update-min-transitions", type=int, default=64,
        help="minimum fully evidenced applied v2 transitions required to train an economic candidate",
    )
    parser.add_argument(
        "--sqlite-economic-replay", action="store_true",
        help="use the durable tasam_economic_transition_history table as replay source",
    )
    parser.add_argument(
        "--update-milestones", default="",
        help="comma-separated snapshot milestones at which exactly one online update may run",
    )
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
    try:
        args.update_milestones = tuple(
            sorted({int(item.strip()) for item in str(args.update_milestones).split(",") if item.strip()})
        )
    except ValueError as exc:
        raise SystemExit("--update-milestones aceita apenas inteiros separados por vírgula") from exc
    if any(value <= 0 for value in args.update_milestones):
        raise SystemExit("--update-milestones deve conter valores positivos")
    if args.replay_policy in {"v2x_80_20", "v2x_window90"}:
        if args.online_only:
            raise SystemExit("replay V2X 80/20 não aceita --online-only")
        if args.experience_bank is None:
            raise SystemExit("replay V2X 80/20 exige --experience-bank")
        if args.recent_experience_bank is None:
            raise SystemExit("replay V2X 80/20 exige --recent-experience-bank")
        if args.prioritize_category_errors:
            raise SystemExit("replay V2X 80/20 não aceita repetição/priorização de categorias")
        expected_rows = 90 if args.replay_policy == "v2x_window90" else 600
        if args.replay_rows != expected_rows:
            raise SystemExit(
                "contrato V2X 80/20 exige exatamente "
                f"{expected_rows} transições ({int(expected_rows * 0.8)}/{int(expected_rows * 0.2)})"
            )
    if args.sam_mode == "l2" and args.l2_weight <= 0.0:
        raise SystemExit("baseline SAC-L2 exige --l2-weight positivo")
    args.max_rollout_fraction = min(max(float(args.max_rollout_fraction), 0.0), 1.0)
    if args.max_rollout_fraction not in {0.0, 0.10, 0.25, 0.50, 1.0}:
        raise SystemExit("--max-rollout-fraction deve ser 0, 0.10, 0.25, 0.50 ou 1.0")
    if args.shadow_min_decisions <= 0 or args.stage_window_decisions <= 0:
        raise SystemExit("as janelas de decisão devem ser positivas")
    args.train_python = resolve_train_python(args.train_python)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    args.checkpoint = Path(args.checkpoint).resolve()
    if not args.checkpoint.is_dir():
        raise SystemExit(f"checkpoint não encontrado: {args.checkpoint}")
    state = load_json(args.state_json, default={})
    if not state:
        initial_stage, initial_fraction = initial_rollout()
        state = {
            "schema": "greenran.tasam_online_control_state.v2",
            "training_mode": "online",
            "replay_source": "live_sqlite",
            "historical_replay_enabled": not args.online_only,
            "experience_bank_enabled": bool(args.experience_bank),
            "experience_bank": str(args.experience_bank) if args.experience_bank else "",
            "recent_experience_bank": str(args.recent_experience_bank) if args.recent_experience_bank else "",
            "replay_contract": (
                V2X_REPLAY_WINDOW90_SCHEMA
                if args.replay_policy == "v2x_window90"
                else V2X_REPLAY_80_20_SCHEMA
                if args.replay_policy == "v2x_80_20"
                else "legacy"
            ),
            "replay_policy": args.replay_policy,
            "sam_mode": args.sam_mode,
            "l2_weight": float(args.l2_weight),
            "learning_rate": float(args.learning_rate),
            "min_trainable_transitions": args.min_trainable_transitions,
            "status": "running" if initial_fraction > 0.0 else "shadow",
            "stage": initial_stage,
            "rollout_fraction": initial_fraction,
            "active_checkpoint": str(args.checkpoint),
            "candidate_checkpoint": "",
            "candidate_promoted": False,
            "active_checkpoint_promoted": False,
            "last_promoted_checkpoint": "",
            "previous_promoted_checkpoint": "",
            "last_promoted_update_id": 0,
            "promotion_count": 0,
            "last_candidate_rejection": {},
            "last_rejected_checkpoint": "",
            "updates_completed": 0,
            "update_milestones": list(args.update_milestones),
            "last_update_snapshot_count": 0,
            "stage_started_decisions": 0,
            "rollback_count": 0,
            "reward_enabled": True,
            "reward_source": "observed_real_metrics",
            "reward_mode": "continuous_observed_error",
            "reward_contract": str(args.reward_contract or "legacy"),
            "reward_weight_snapshot": {
                "max_energy_weight": 0.30,
                "ewma_previous": 0.75,
                "ewma_current": 0.25,
                "healthy_exit_decisions": 3,
                "v2x_base": 0.45,
                "v2x_risk_slope": 0.35,
                "equity_base": 0.25,
                "equity_risk_slope": 0.20,
            } if str(args.reward_contract or "") == REWARD_CONTRACT else {},
            "replay_strategy": (
                "energy_focused_40_30_20_10_v1"
                if args.prioritize_category_errors else "uniform_valid_rows"
            ),
            "category_error_repeat": max(0, int(args.category_error_repeat)),
            "category_loss_weight": float(args.category_loss_weight),
            "category_head_hidden_dim": int(args.category_head_hidden_dim),
            "category_head_training": True,
            "category_curriculum": checkpoint_entry(args.checkpoint, "initial").get("category_curriculum") or {},
            "full_control": full_control_mode(),
            "economic_action_contract": os.environ.get(
                "GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT", ""
            ).strip(),
            "max_rollout_fraction": args.max_rollout_fraction,
            "shadow_min_decisions": args.shadow_min_decisions,
            "stage_window_decisions": args.stage_window_decisions,
            "min_economic_transitions": args.min_economic_transitions,
            "economic_update_min_transitions": args.economic_update_min_transitions,
            "ml_enabled": os.environ.get("GREENRAN_ML_ENABLED", "0") in {"1", "true", "True"},
            "retrain_enabled": os.environ.get("GREENRAN_ML_RETRAIN_ENABLED", "false") in {"1", "true", "True"},
            "history": [],
        }
    # Persist the active reward contract even when resuming an older state
    # file.  The snapshot is audit metadata; it never rewrites past rows.
    state["reward_contract"] = str(args.reward_contract or state.get("reward_contract") or "legacy")
    if state["reward_contract"] == REWARD_CONTRACT:
        state["reward_weight_snapshot"] = {
            "max_energy_weight": 0.30,
            "ewma_previous": 0.75,
            "ewma_current": 0.25,
            "healthy_exit_decisions": 3,
            "v2x_base": 0.45,
            "v2x_risk_slope": 0.35,
            "equity_base": 0.25,
            "equity_risk_slope": 0.20,
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
        # the next focused replay update without touching live allocation.
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
    state.setdefault("active_checkpoint_promoted", False)
    state.setdefault("last_promoted_checkpoint", "")
    state.setdefault("previous_promoted_checkpoint", "")
    state.setdefault("last_promoted_update_id", 0)
    state.setdefault("promotion_count", 0)
    state.setdefault("last_candidate_rejection", {})
    state.setdefault("last_rejected_checkpoint", "")
    state.setdefault("reward_source", "observed_real_metrics")
    state.setdefault("reward_mode", "continuous_observed_error")
    state.setdefault("category_loss_weight", float(args.category_loss_weight))
    state.setdefault("category_head_hidden_dim", int(args.category_head_hidden_dim))
    state.setdefault("category_head_training", True)
    state.setdefault("economic_action_contract", os.environ.get(
        "GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT", ""
    ).strip())
    state["max_rollout_fraction"] = args.max_rollout_fraction
    state["shadow_min_decisions"] = args.shadow_min_decisions
    state["stage_window_decisions"] = args.stage_window_decisions
    state["promotion_window"] = args.promotion_window
    state["min_economic_transitions"] = args.min_economic_transitions
    state["economic_update_min_transitions"] = args.economic_update_min_transitions
    state["update_milestones"] = list(args.update_milestones)
    state["ml_enabled"] = os.environ.get("GREENRAN_ML_ENABLED", "0") in {"1", "true", "True"}
    state["retrain_enabled"] = os.environ.get("GREENRAN_ML_RETRAIN_ENABLED", "false") in {"1", "true", "True"}
    state["category_curriculum"] = checkpoint_entry(
        Path(str(state.get("active_checkpoint") or args.checkpoint)), "active"
    ).get("category_curriculum") or {}
    if args.prioritize_category_errors:
        state["replay_strategy"] = "energy_focused_40_30_20_10_v1"
    state["historical_replay_enabled"] = not args.online_only
    state["experience_bank_enabled"] = bool(args.experience_bank)
    state["experience_bank"] = str(args.experience_bank) if args.experience_bank else ""
    state["replay_source"] = (
        "live_sqlite_with_persistent_experience_bank"
        if args.experience_bank
        else "live_sqlite_only" if args.online_only else "live_sqlite_with_historical_context"
    )
    state["full_control"] = full_control_mode()
    if (
        state.get("economic_action_contract") in ECONOMIC_ACTION_CONTRACTS
        and float(state.get("rollout_fraction", 0.0) or 0.0) > args.max_rollout_fraction
    ):
        state["stage"] = "canary_50"
        state["rollout_fraction"] = args.max_rollout_fraction
        state["stage_started_decisions"] = count_decisions(args.db)
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
            state["active_checkpoint_promoted"] = True
            state["last_promoted_checkpoint"] = str(candidate_path.resolve())
            state["last_promoted_update_id"] = int(state.get("updates_completed", 0) or 0)
            state["promotion_count"] = int(state.get("promotion_count", 0) or 0) + 1
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
    # A watermark de snapshots só é válida dentro do mesmo Data Lake. Estados
    # herdados de checkpoints/campanhas anteriores podem carregar uma watermark
    # de um DB antigo; contra um DB novo (ou recriado), o portão de update
    # nunca dispararia (causa raiz de updates_completed=0 nas campanhas v6-v11).
    db_snapshots_now = count_snapshots(args.db)
    if db_snapshots_now < int(state.get("last_update_snapshot_count", 0) or 0):
        state["last_update_snapshot_count"] = 0
    manifest_checkpoint = Path(state["active_checkpoint"])
    publish_manifests(
        args,
        state,
        manifest_checkpoint,
        "control_candidate" if float(state.get("rollout_fraction", 0.0) or 0.0) > 0.0 else "shadow_ready",
    )
    # Sem o filtro full-control: no modo full-control o artefato de shadow
    # também precisa existir para o gate de não-inferioridade; em retomadas,
    # materialize-o aqui quando ausente (mesma recuperação do modo protegido).
    if state.get("candidate_checkpoint"):
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
    publish_learning_meter(args, state)

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
        pending_candidate = bool(
            state.get("candidate_checkpoint")
            and not state.get("candidate_promoted", False)
            and (
                state.get("candidate_shadow_started_decisions") is not None
                or state.get("candidate_shadow_evaluation_error")
                or state.get("candidate_shadow_evaluation")
            )
        )
        milestone_due = True
        if args.update_milestones:
            completed = int(state.get("updates_completed", 0) or 0)
            milestone_due = any(
                snapshots >= milestone and completed < index
                for index, milestone in enumerate(args.update_milestones, start=1)
            )
        if (
            state.get("stage") != "rollback"
            and not pending_candidate
            and milestone_due
            and snapshots - int(state.get("last_update_snapshot_count", 0) or 0) >= args.min_new_snapshots
            and (
                args.online_only
                or args.historical_trace.exists()
                or (args.experience_bank and args.experience_bank.exists())
            )
        ):
            update_result = run_update(args, state, snapshots)
        advance_rollout(args, state, decisions, guard)
        state["last_update_result"] = update_result
        state["updated_at"] = int(time.time())
        save_json(args.state_json, state)
        save_json(args.status_json, {**state, "active_checkpoint": state.get("active_checkpoint"), "candidate_checkpoint": state.get("candidate_checkpoint", ""), "rollout": {"stage": state.get("stage"), "fraction": state.get("rollout_fraction", 0.0)}, "last_update_result": update_result})
        publish_learning_meter(args, state)
        print(json.dumps({"status": state["status"], "snapshots": snapshots, "decisions": decisions, "stage": state.get("stage"), "fraction": state.get("rollout_fraction"), "update": update_result.get("status"), "guard": guard.get("reason")}, ensure_ascii=False), flush=True)
        if args.once:
            return 0
        time.sleep(max(args.poll_seconds, 5.0))


if __name__ == "__main__":
    raise SystemExit(main())
