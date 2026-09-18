#!/usr/bin/env python3
"""Run the online-economic TA-SAM adaptation and paired frozen evaluation.

The adaptation arm learns while ARMD and the rApp Judge are active.  Its
resulting checkpoint is then frozen for the shadow gate and the final paired
baseline/treatment comparison.  Older campaign launchers are intentionally
left unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from greenran_infra_budget import InfraBudgetError, assert_cgroup_delegation  # noqa: E402
from greenran_paths import get_fixed_service_imsis  # noqa: E402
from tasam_learning_meter import build_learning_meter  # noqa: E402

ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
GATE = ROOT / "scripts" / "prepare_tasam_control_trial.py"
EVALUATOR = ROOT / "scripts" / "evaluate_tasam_causal_comparison.py"
DEFAULT_CHECKPOINT = ROOT / "runs" / "tasam_economic_checkpoint_v10_seed47_20260917"
DEFAULT_CALIBRATION = ROOT / "config" / "energy_calibration_sim_v3_sleep.json"
DEFAULT_CAMPAIGN = ROOT / "runs" / "tasam_asgard_adaptation_seed47_20260917_v10"
ECONOMIC_ACTION_V2 = "applied_action_v2"
ECONOMIC_ACTION_V3 = "economic_action_v3_per_du_sleep"


def _checkpoint_is_complete(checkpoint: Path) -> bool:
    return all(
        (checkpoint / name).is_file()
        for name in (
            "tasam_marl_actors.pt",
            "tasam_marl_checkpoint_meta.json",
            "tasam_marl_summary.json",
        )
    )


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run(command: list[str], *, dry_run: bool = False, env: dict[str, str] | None = None) -> int:
    print("$ " + " ".join(command), flush=True)
    if dry_run:
        return 0
    return int(subprocess.run(command, cwd=ROOT, env=env, check=False).returncode)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_vehicle_profile_manifest(
    path: Path, *, expected_profile: str | None = None
) -> dict[str, Any]:
    path = path.resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit(f"manifesto veicular fora do workspace local: {path}") from exc
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"manifesto veicular inválido: {path}: {exc}") from exc
    if (
        payload.get("schema") != "greenran.autonomous_vehicle_feasibility.v1"
        or payload.get("status") != "passed"
        or int(payload.get("selected_interval_us") or 0) not in {4000, 6000, 8000, 12000, 16000}
    ):
        raise SystemExit("manifesto veicular não aprovado ou sem intervalo selecionado")
    if expected_profile is not None and payload.get("profile") != expected_profile:
        raise SystemExit(
            "manifesto veicular pertence a outro perfil: "
            f"esperado={expected_profile} obtido={payload.get('profile')}"
        )
    return payload


def _derive_applied_action_v2_checkpoint(
    source: Path, destination: Path, calibration_data: dict[str, Any] | None = None
) -> Path:
    """Copy the historical checkpoint once and version the economic contract.

    The new metadata is deliberately written only into the campaign-local
    copy.  The source checkpoint stays a reproducible parent and can still be
    used to re-run earlier campaigns.
    """
    source_meta_path = source / "tasam_marl_checkpoint_meta.json"
    try:
        metadata = json.loads(source_meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"checkpoint pai inválido: {source_meta_path}: {exc}") from exc
    if (
        int(metadata.get("du_count", 0) or 0) != 3
        or int(metadata.get("du_state_dim", 0) or 0) != 13
        or int(metadata.get("global_state_dim", 0) or 0) != 13
        or int(metadata.get("joint_action_dim", 0) or 0) != 12
        or int(metadata.get("allocation_head_output_dim", 0) or 0) != 3
        or "total_budget_fraction" not in list(metadata.get("allocation_head_outputs") or [])
    ):
        raise RuntimeError("checkpoint pai não possui a cabeça econômica 3-D compatível")
    if destination.exists():
        raise RuntimeError(f"checkpoint v2 já existe: {destination}")
    shutil.copytree(source, destination)
    parent_actor = source / "tasam_marl_actors.pt"
    metadata.update({
        "economic_action_contract": "applied_action_v2",
        "allocation_target_contract": "economic_applied_action_v2",
        "total_budget_fraction_bounds": [0.0, 1.0],
        "economic_safety_isolation": "blocked_and_critical_v1",
        "economic_thresholds": {
            "training_energy_saving_min": 0.005,
            "training_allocation_saving_min": -0.001,
        },
        "parent_checkpoint": str(source.resolve()),
        "parent_checkpoint_sha256": _sha256(parent_actor) if parent_actor.is_file() else "",
        "model_version": "tasam_economic_online_v4",
        "calibration_version": str(
            (calibration_data or {}).get("calibration_version") or ""
        ),
        "calibration_sha256": str(
            (calibration_data or {}).get("calibration_sha256") or ""
        ),
    })
    _write(destination / "tasam_marl_checkpoint_meta.json", metadata)
    return destination


def _prepare_bootstrap_checkpoint(
    source: Path, destination: Path, calibration_data: dict[str, Any] | None = None
) -> tuple[Path, str]:
    """Create the campaign-local bootstrap without downgrading v10.

    v2 parents retain the historical metadata upgrade path.  A v10 parent is
    copied byte-for-byte into the new campaign and only receives lineage
    metadata in the copy; its 14-dimensional actor and per-DU sleep contract
    must reach the online arm unchanged.
    """
    meta_path = source / "tasam_marl_checkpoint_meta.json"
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"checkpoint pai inválido: {meta_path}: {exc}") from exc
    contract = str(metadata.get("economic_action_contract") or "")
    if contract == ECONOMIC_ACTION_V2:
        return (
            _derive_applied_action_v2_checkpoint(source, destination, calibration_data),
            contract,
        )
    if contract != ECONOMIC_ACTION_V3:
        raise RuntimeError(
            "checkpoint econômico incompatível: "
            f"{contract or 'contrato ausente'}; esperado {ECONOMIC_ACTION_V2} ou {ECONOMIC_ACTION_V3}"
        )
    required = {
        "du_count": 3,
        "du_state_dim": 13,
        "global_state_dim": 13,
        "global_action_dim": 5,
        "joint_action_dim": 14,
    }
    for key, expected in required.items():
        if int(metadata.get(key, 0) or 0) != expected:
            raise RuntimeError(f"checkpoint v10 incompatível: {key} != {expected}")
    if not _checkpoint_is_complete(source):
        raise RuntimeError(f"checkpoint v10 incompleto: {source}")
    if destination.exists():
        raise RuntimeError(f"checkpoint econômico local já existe: {destination}")
    shutil.copytree(source, destination)
    parent_actor = source / "tasam_marl_actors.pt"
    local_metadata = dict(metadata)
    local_metadata.update({
        "economic_action_contract": ECONOMIC_ACTION_V3,
        "parent_checkpoint": str(source.resolve()),
        "parent_checkpoint_sha256": _sha256(parent_actor) if parent_actor.is_file() else "",
        "warm_start": True,
        "parent_was_promoted": False,
        "replay_imported": False,
        "calibration_version": str((calibration_data or {}).get("calibration_version") or ""),
        "calibration_sha256": str((calibration_data or {}).get("calibration_sha256") or ""),
        "economic_replay": {
            "eligible_transitions": 0,
            "source": "new_campaign_only",
            "prior_evidence_imported": False,
        },
    })
    _write(destination / "tasam_marl_checkpoint_meta.json", local_metadata)
    return destination, contract


def _arm(mode: str, run_dir: Path, checkpoint: Path, args: argparse.Namespace, *, gate: Path | None = None) -> list[str]:
    is_online_adaptation = mode == "combined_online"
    arm_wall_time = getattr(args, "adaptation_wall_time", args.wall_time) if is_online_adaptation else args.wall_time
    arm_decision_target = (
        getattr(args, "adaptation_max_decisions", args.decisions)
        if is_online_adaptation else args.decisions
    )
    command = [
        sys.executable, str(ARM), "--mode", mode,
        "--run-dir", str(run_dir), "--seed", str(args.seed),
        "--profile", args.profile, "--wall-time", str(arm_wall_time),
        "--sim-time", str(args.sim_time), "--checkpoint", str(checkpoint),
        "--energy-calibration", str(args.calibration),
        "--decision-target", str(arm_decision_target), "--min-free-gib", str(args.min_free_gib),
        "--min-new-snapshots", str(args.min_new_snapshots),
        "--min-trainable-transitions", str(args.min_trainable_transitions),
        "--epochs-per-update", str(args.epochs_per_update),
        "--controller-poll-seconds", str(args.controller_poll_seconds),
        "--shadow-min-decisions", str(getattr(args, "shadow_min_decisions", 300)),
        "--stage-window-decisions", str(getattr(args, "stage_window_decisions", 300)),
        "--max-rollout-fraction", str(getattr(args, "max_rollout_fraction", 0.50)),
        "--min-economic-transitions", str(getattr(args, "min_economic_transitions", 180)),
        "--economic-update-min-transitions", str(getattr(args, "economic_update_min_transitions", 64)),
    ]
    if getattr(args, "native_fidelity", False):
        command.extend(["--native-fidelity", "--performance-min-rtf", str(getattr(args, "performance_min_rtf", 0.016))])
    if gate is not None:
        command.extend(["--control-gate", str(gate)])
    vehicle_manifest = getattr(args, "vehicle_profile_manifest", None)
    if vehicle_manifest:
        command.extend(["--vehicle-profile-manifest", str(vehicle_manifest)])
    if is_online_adaptation:
        command.extend([
            "--min-decision-target", str(getattr(args, "adaptation_decisions", 1200)),
            "--stop-after-promotion",
            "--artifact-budget-gib", "2",
            "--artifact-min-free-gib", str(args.min_free_gib),
        ])
    return command


def _active_checkpoint(adaptation_dir: Path, min_applied_actions: int) -> Path:
    state_path = adaptation_dir / "online_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"estado online ausente ou inválido: {state_path}") from exc
    promoted = bool(state.get("active_checkpoint_promoted", False))
    if "active_checkpoint_promoted" not in state:
        # Read old campaign state without allowing a transient rejected
        # candidate to masquerade as the active policy.
        promoted = bool(state.get("last_promoted_checkpoint") or state.get("candidate_promoted", False))
    checkpoint_value = state.get("last_promoted_checkpoint") or state.get("active_checkpoint", "")
    checkpoint = Path(str(checkpoint_value)).resolve()
    updates = int(state.get("updates_completed", 0) or 0)
    if not promoted or updates < 1 or not _checkpoint_is_complete(checkpoint):
        raise RuntimeError(f"treino online não produziu checkpoint promovido: updates={updates}, checkpoint={checkpoint}")
    metadata = json.loads((checkpoint / "tasam_marl_checkpoint_meta.json").read_text(encoding="utf-8"))
    contract = metadata.get("economic_action_contract")
    if contract == ECONOMIC_ACTION_V3:
        if int(metadata.get("global_action_dim", 0) or 0) != 5 or int(metadata.get("joint_action_dim", 0) or 0) != 14:
            raise RuntimeError("checkpoint ativo v10 tem dimensões econômicas incompatíveis")
    elif contract != ECONOMIC_ACTION_V2:
        raise RuntimeError("checkpoint ativo não tem contrato econômico v2/v3 compatível")
    if not (metadata.get("economic_replay") or {}).get("eligible_transitions"):
        raise RuntimeError("checkpoint ativo é apenas o bootstrap; nenhum candidato econômico foi promovido")
    db = adaptation_dir / "rapp_data_lake.db"
    try:
        with sqlite3.connect(str(db)) as conn:
            applied = int(conn.execute(
                "select count(*) from tasam_economic_transition_history "
                "where economic_training_eligible=1 "
                "and economic_application_status='applied'"
            ).fetchone()[0] or 0)
    except sqlite3.Error as exc:
        raise RuntimeError(f"não foi possível validar ações econômicas aplicadas: {exc}") from exc
    if applied < min_applied_actions:
        raise RuntimeError(
            "treino online não alcançou ações TA-SAM economicamente aplicadas: "
            f"{applied} < {min_applied_actions}"
        )
    return checkpoint


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return round(sum(values) / len(values), 6) if values else None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _service_completion_by_snapshot(conn: sqlite3.Connection) -> dict[int, dict[str, float]]:
    """Return real-PDCP completion by canonical service and metric snapshot.

    The production decisions table intentionally keeps delayed feedback in its
    JSON contract.  This lookup makes the observation report complete without
    inventing SLA values when a snapshot is absent or incomplete.
    """
    try:
        rows = conn.execute(
            """
            SELECT em.id, um.imsi, um.tx_pdus, um.rx_pdus, um.pdcp_provenance,
                   um.packet_loss_percent
              FROM extended_metrics em
              JOIN ue_metrics um ON um.timestamp = em.timestamp
             WHERE em.id IS NOT NULL
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    services = get_fixed_service_imsis()
    expected = {
        name: {int(imsi) for imsi in imsis}
        for name, imsis in services.items()
        if name in {"camera", "sensor", "vehicle"}
    }
    grouped: dict[int, dict[int, list[tuple[Any, ...]]]] = {}
    for snapshot_id, imsi, tx_pdus, rx_pdus, provenance, packet_loss in rows:
        try:
            snapshot = int(snapshot_id)
            ue = int(imsi)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(snapshot, {}).setdefault(ue, []).append(
            (tx_pdus, rx_pdus, provenance, packet_loss)
        )
    result: dict[int, dict[str, float]] = {}
    for snapshot_id, by_ue in grouped.items():
        service_values: dict[str, float] = {}
        for service, service_imsis in expected.items():
            if set(by_ue) != set().union(*expected.values()):
                # Exact topology is validated by the collector.  Do not
                # expose a service completion from a partial/extra snapshot.
                continue
            rows_for_service = [
                row
                for imsi in service_imsis
                for row in by_ue.get(imsi, [])
            ]
            if len(rows_for_service) != len(service_imsis):
                continue
            if any(str(row[2] or "") != "pdcp_real" for row in rows_for_service):
                continue
            try:
                tx_total = sum(float(row[0]) for row in rows_for_service)
                rx_total = sum(float(row[1]) for row in rows_for_service)
            except (TypeError, ValueError):
                continue
            if tx_total <= 0 or rx_total < 0:
                continue
            service_values[f"{service}_sla"] = round(
                max(0.0, min(1.0, rx_total / tx_total)), 6
            )
        if len(service_values) == 3:
            result[snapshot_id] = service_values
    return result


def _observation_summary(
    campaign: Path,
    adaptation: Path,
    checkpoint: Path,
    calibration: Path,
    *,
    min_applied_actions: int,
) -> dict[str, Any]:
    """Summarize only the online adaptation; this is not causal evidence."""
    db = adaptation / "rapp_data_lake.db"
    rows: list[dict[str, Any]] = []
    if db.is_file():
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            columns = {row[1] for row in conn.execute("pragma table_info(decisions_history)")}
            feedback_by_decision: dict[int, dict[str, Any]] = {}
            try:
                feedback_rows = conn.execute(
                    "SELECT decision_id, feedback_json FROM judge_outcome_history"
                ).fetchall()
            except sqlite3.Error:
                feedback_rows = []
            for feedback_row in feedback_rows:
                try:
                    decision_id = int(feedback_row[0])
                except (TypeError, ValueError):
                    continue
                feedback_by_decision[decision_id] = _json_object(feedback_row[1])
            service_completion = _service_completion_by_snapshot(conn)
            selected = [
                key for key in (
                    "id", "timestamp", "metric_snapshot_id", "economic_action_json",
                    "economic_application_status", "economic_rejection_reason",
                        "economic_transition_eligible", "tasam_checkpoint_valid",
                        "tasam_fallback_used", "tasam_evidence_valid", "live_power_w",
                        "shadow_power_w", "energy_saving_fraction", "resource_saving_fraction",
                        "economic_execution_mode", "economic_safety_isolated",
                        "economic_safety_isolation_reason", "economic_training_eligible",
                        "economic_promotion_eligible", "realized_energy_saving_fraction",
                        "realized_allocation_saving_fraction",
                        "actuation_confirmed", "actuation_confirmation_source", "observed_power_percent",
                        "observed_power_w", "observed_ru_count", "observed_mmwave_count",
                        "confirmation_decision_id",
                        "armd_safety_level", "armd_role", "armd_advisory_only",
                        "armd_hard_veto", "tasam_operating_permission",
                        "tasam_envelope_min_power", "tasam_envelope_max_power",
                        "economic_isolation_source",
                    "tasam_online_reward", "tasam_energy_reward", "tasam_allocation_reward",
                        "tasam_sla_penalty", "applied_power_percent", "applied_total_allocation",
                    "economic_action_alignment_valid",
                    "applied_ran_allocation", "applied_ai_allocation", "total_budget_fraction",
                    "ran_completion_ratio", "ai_completion_ratio", "camera_sla",
                    "sensor_sla", "vehicle_sla", "armd_override_applied",
                    "safety_override", "tasam_actuation_applied", "ta_sam_actuation_applied",
                ) if key in columns
            ]
            if selected:
                rows = [dict(row) for row in conn.execute(
                    f"select {', '.join(selected)} from decisions_history order by id"
                )]
                for row in rows:
                    feedback_doc = feedback_by_decision.get(int(row.get("id") or 0), {})
                    feedback = _json_object(feedback_doc.get("feedback"))
                    action = _json_object(row.get("economic_action_json"))
                    action = _json_object(feedback_doc.get("economic_action")) or action
                    row_status = str(
                        row.get("economic_application_status")
                        or feedback.get("economic_application_status")
                        or action.get("application_status")
                        or ""
                    )
                    for key in (
                        "economic_transition_eligible", "tasam_checkpoint_valid",
                        "tasam_fallback_used", "tasam_evidence_valid",
                        "live_power_w", "shadow_power_w", "energy_saving_fraction",
                        "resource_saving_fraction", "economic_execution_mode",
                        "economic_safety_isolated", "economic_safety_isolation_reason",
                        "economic_action_alignment_valid",
                        "economic_training_eligible", "economic_promotion_eligible",
                        "realized_energy_saving_fraction", "realized_allocation_saving_fraction",
                        "actuation_confirmed", "actuation_confirmation_source", "observed_power_percent",
                        "observed_power_w", "observed_ru_count", "observed_mmwave_count",
                        "confirmation_decision_id",
                        "armd_safety_level", "armd_role", "armd_advisory_only",
                        "armd_hard_veto", "tasam_operating_permission",
                        "tasam_envelope_min_power", "tasam_envelope_max_power",
                        "economic_isolation_source",
                    ):
                        if row.get(key) is None and feedback.get(key) is not None:
                            row[key] = feedback.get(key)
                    for key in (
                        "tasam_online_reward", "tasam_energy_reward",
                        "tasam_allocation_reward", "tasam_sla_penalty",
                    ):
                        if feedback.get(key) is not None:
                            row[key] = feedback.get(key)
                    if row_status == "applied":
                        applied_action = _json_object(action.get("applied"))
                        for output_key, action_key in (
                            ("applied_power_percent", "power_percent"),
                            ("applied_total_allocation", "total_allocation"),
                            ("applied_ran_allocation", "ran_allocation"),
                            ("applied_ai_allocation", "ai_allocation"),
                        ):
                            if row.get(output_key) is None:
                                row[output_key] = feedback.get(output_key, applied_action.get(action_key))
                        if row.get("total_budget_fraction") is None:
                            try:
                                total = float(applied_action.get("total_allocation"))
                                budget = float(applied_action.get("usable_budget"))
                                if budget > 0:
                                    row["total_budget_fraction"] = max(0.0, min(1.0, total / budget))
                            except (TypeError, ValueError):
                                pass
                    snapshot_id = row.get("pdcp_metric_snapshot_id") or row.get("metric_snapshot_id")
                    try:
                        service_values = service_completion.get(int(snapshot_id), {})
                    except (TypeError, ValueError):
                        service_values = {}
                    for key in ("camera_sla", "sensor_sla", "vehicle_sla"):
                        if row.get(key) is None and key in service_values:
                            row[key] = service_values[key]
    def count_where(predicate) -> int:
        return sum(1 for row in rows if predicate(row))

    applied = count_where(lambda row: row.get("economic_application_status") == "applied")
    rejected = count_where(lambda row: row.get("economic_application_status") == "rejected")
    reverted = count_where(lambda row: row.get("economic_application_status") in {"reverted", "rollback"})
    fallback = count_where(lambda row: bool(row.get("tasam_fallback_used")))
    checkpoint_invalid = count_where(lambda row: row.get("tasam_checkpoint_valid") not in (1, True))
    eligible = count_where(lambda row: bool(row.get("economic_transition_eligible")))
    training_eligible = count_where(lambda row: bool(row.get("economic_training_eligible")))
    promotion_eligible = count_where(lambda row: bool(row.get("economic_promotion_eligible")))
    safety_isolated = count_where(lambda row: bool(row.get("economic_safety_isolated")))
    overrides = count_where(lambda row: bool(row.get("armd_override_applied") or row.get("safety_override")))
    armd_clear = count_where(lambda row: str(row.get("armd_safety_level") or "").upper() == "CLEAR")
    armd_advisory = count_where(lambda row: str(row.get("armd_safety_level") or "").upper() == "ADVISORY")
    armd_hard_veto = count_where(lambda row: str(row.get("armd_safety_level") or "").upper() == "HARD_VETO")
    tasam_applied = count_where(lambda row: bool(row.get("tasam_actuation_applied") or row.get("ta_sam_actuation_applied")))
    online_status = {}
    status_path = adaptation / "online_status.json"
    if status_path.is_file():
        try:
            online_status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            online_status = {}
    calibration_data = json.loads(calibration.read_text(encoding="utf-8"))
    calibration_data["calibration_sha256"] = _sha256(calibration)
    checkpoint_meta = json.loads((checkpoint / "tasam_marl_checkpoint_meta.json").read_text(encoding="utf-8"))
    active_checkpoint_promoted = bool(
        online_status.get("active_checkpoint_promoted", False)
    )
    if "active_checkpoint_promoted" not in online_status:
        active_checkpoint_promoted = bool(
            online_status.get("last_promoted_checkpoint")
            or online_status.get("candidate_promoted", False)
        )
    last_promoted_checkpoint = str(
        online_status.get("last_promoted_checkpoint")
        or online_status.get("active_checkpoint", checkpoint)
    )
    summary = {
        "schema": "greenran.tasam.online_observation.v1",
        "metric_scope": "simulation",
        "causal_comparison": False,
        "energy_interpretation": "estimativa relativa da simulação ns-3; não é consumo físico",
        "campaign_dir": str(campaign),
        "adaptation_dir": str(adaptation),
        "decision_count": len(rows),
        "applied_decisions": applied,
        "rejected_decisions": rejected,
        "reverted_decisions": reverted,
        "economic_transition_eligible": eligible,
        "economic_training_eligible": training_eligible,
        "economic_promotion_eligible": promotion_eligible,
        "economic_safety_isolated": safety_isolated,
        "tasam_checkpoint_invalid": checkpoint_invalid,
        "tasam_fallback_used": fallback,
        "armd_or_safety_overrides": overrides,
        "armd_safety_levels": {
            "CLEAR": armd_clear,
            "ADVISORY": armd_advisory,
            "HARD_VETO": armd_hard_veto,
        },
        "tasam_actuation_applied": tasam_applied,
        "updates_completed": int(online_status.get("updates_completed", 0) or 0),
        "candidate_promoted": bool(online_status.get("candidate_promoted", False)),
        "active_checkpoint_promoted": active_checkpoint_promoted,
        "last_promoted_checkpoint": last_promoted_checkpoint,
        "last_promoted_update_id": int(online_status.get("last_promoted_update_id", 0) or 0),
        "promotion_count": int(online_status.get("promotion_count", 0) or 0),
        "last_candidate_rejection": online_status.get("last_candidate_rejection") or {},
        "initial_checkpoint": str(checkpoint_meta.get("parent_checkpoint", "")),
        "active_checkpoint": str(checkpoint),
        "energy_model_version": calibration_data.get("calibration_version"),
        "calibration_status": calibration_data.get("status"),
        "average_live_power_w": _mean(rows, "live_power_w"),
        "average_shadow_power_w": _mean(rows, "shadow_power_w"),
        "average_energy_saving_fraction": _mean(rows, "energy_saving_fraction"),
        "average_allocation_saving_fraction": _mean(rows, "resource_saving_fraction"),
        "average_total_budget_fraction": _mean(rows, "total_budget_fraction"),
        "average_applied_power_percent": _mean(rows, "applied_power_percent"),
        "average_applied_total_allocation": _mean(rows, "applied_total_allocation"),
        "average_applied_ran_allocation": _mean(rows, "applied_ran_allocation"),
        "average_applied_ai_allocation": _mean(rows, "applied_ai_allocation"),
        "average_online_reward": _mean(rows, "tasam_online_reward"),
        "average_energy_reward": _mean(rows, "tasam_energy_reward"),
        "average_allocation_reward": _mean(rows, "tasam_allocation_reward"),
        "average_realized_energy_saving_fraction": _mean(rows, "realized_energy_saving_fraction"),
        "average_realized_allocation_saving_fraction": _mean(rows, "realized_allocation_saving_fraction"),
        "average_sla_penalty": _mean(rows, "tasam_sla_penalty"),
        "camera_sla": _mean(rows, "camera_sla"),
        "sensor_sla": _mean(rows, "sensor_sla"),
        "vehicle_sla": _mean(rows, "vehicle_sla"),
        "acceptance": {
            "updates_completed": int(online_status.get("updates_completed", 0) or 0) >= 1,
            "active_checkpoint_promoted": active_checkpoint_promoted,
            "min_applied_actions": applied >= min_applied_actions,
            "checkpoint_valid": checkpoint_invalid == 0,
            "fallback_zero": fallback == 0,
            "critical_rollback_zero": reverted == 0,
        },
    }
    meter_state = dict(online_status)
    try:
        state_payload = json.loads((adaptation / "online_state.json").read_text(encoding="utf-8"))
        if isinstance(state_payload, dict):
            meter_state = {**state_payload, **meter_state}
    except (OSError, json.JSONDecodeError):
        pass
    # The durable economic table is authoritative for alignment and realized
    # effects.  Keep the decision rows useful for dashboards, but hydrate the
    # compact Judge v2 fields before computing the meter.
    summary["learning_meter"] = build_learning_meter(rows, meter_state, target_transitions=min_applied_actions)
    summary["valid"] = all(summary["acceptance"].values())
    return summary


def run(args: argparse.Namespace) -> int:
    # Do this before mkdir/write so a missing one-time bootstrap cannot leave
    # a campaign that looks started but never received real limits.
    try:
        assert_cgroup_delegation()
    except InfraBudgetError as exc:
        raise SystemExit(str(exc)) from exc
    campaign = args.campaign_dir.resolve()
    free_gib = shutil.disk_usage(campaign.parent).free / (1024 ** 3)
    if free_gib < float(args.startup_min_free_gib):
        raise SystemExit(
            "espaço livre insuficiente para a campanha econômica online: "
            f"{free_gib:.2f} GiB < {args.startup_min_free_gib:.2f} GiB; "
            "a campanha não foi criada"
        )
    if campaign.exists() and any(campaign.iterdir()):
        raise SystemExit(f"campanha já contém artefatos; escolha outro diretório: {campaign}")
    checkpoint = args.checkpoint.resolve()
    calibration = args.calibration.resolve()
    try:
        calibration_data = json.loads(calibration.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"calibração energética inválida: {calibration}: {exc}") from exc
    if not isinstance(calibration_data, dict):
        raise SystemExit(f"calibração energética inválida: objeto JSON esperado: {calibration}")
    calibration_data["calibration_sha256"] = _sha256(calibration)
    try:
        checkpoint_metadata = json.loads(
            (checkpoint / "tasam_marl_checkpoint_meta.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"metadata do checkpoint inválido: {checkpoint}: {exc}") from exc
    if checkpoint_metadata.get("economic_action_contract") == ECONOMIC_ACTION_V3:
        if calibration_data.get("schema") != "greenran.energy_calibration.v3":
            raise SystemExit(
                "checkpoint v10 exige calibração greenran.energy_calibration.v3 "
                "(sim_v3_sleep); a calibração provisória não pode ser usada"
            )
    vehicle_profile = None
    if args.require_vehicle_feasibility and args.vehicle_profile_manifest is None:
        raise SystemExit(
            "campanha econômica exige baseline veicular aprovado para o mesmo perfil; "
            "execute vehicle_feasibility antes do treino"
        )
    if args.vehicle_profile_manifest is not None:
        vehicle_profile = _validate_vehicle_profile_manifest(
            args.vehicle_profile_manifest, expected_profile=args.profile
        )
    # Validate every external dependency before creating the campaign.  A
    # failed baseline feasibility check must not leave an empty-looking run
    # that could later be mistaken for an interrupted adaptation.
    campaign.mkdir(parents=True, exist_ok=True)
    # A persistent dispatcher may briefly run an older normalizer after a
    # source update.  Checkpoint lineage is still unambiguous: a campaign
    # seeded from candidate_XXXX is a warm-start and must never inherit its
    # parent's economic evidence.  Infer the provenance at the launcher
    # boundary so the campaign manifest remains truthful even in that window.
    inferred_warm_start = bool(
        getattr(args, "warm_start", False)
        or "/candidates/candidate_" in str(checkpoint)
    )
    warm_start_parent = (
        Path(getattr(args, "warm_start_parent", "")).resolve()
        if getattr(args, "warm_start_parent", None)
        else checkpoint
    )
    runtime_env = dict(os.environ)
    runtime_env["GREENRAN_ENERGY_CALIBRATION_PATH"] = str(calibration)
    if args.benchmark_manifest is not None:
        benchmark_manifest = args.benchmark_manifest.resolve()
        if not benchmark_manifest.is_file():
            raise SystemExit(f"manifesto de benchmark ausente: {benchmark_manifest}")
        runtime_env["GREENRAN_TASAM_BENCHMARK_MANIFEST"] = str(benchmark_manifest)
    manifest: dict[str, Any] = {
        "schema": "greenran.tasam.online_economic_campaign.v2",
        "system_name": "ASGARD",
        "metric_scope": "simulation",
        "seed": args.seed,
        "profile": args.profile,
        "adaptation_decisions": args.adaptation_decisions,
        "evaluation_decisions": args.decisions,
        "observe_only": bool(args.observe_only),
        "initial_checkpoint": str(checkpoint),
        "calibration": str(calibration),
        "startup_min_free_gib": float(args.startup_min_free_gib),
        "runtime_min_free_gib": float(args.min_free_gib),
        "vehicle_profile_manifest": str(args.vehicle_profile_manifest.resolve()) if args.vehicle_profile_manifest else "",
        "vehicle_packet_interval_us": int(vehicle_profile["selected_interval_us"]) if vehicle_profile else None,
        "phases": {},
        "created_at": int(time.time()),
        "warm_start": inferred_warm_start,
        "warm_start_parent": str(warm_start_parent),
        "parent_was_promoted": bool(getattr(args, "parent_was_promoted", False)),
        "warm_start_evidence_reset": inferred_warm_start,
        "excluded_prior_economic_transitions": inferred_warm_start,
    }
    _write(campaign / "campaign_manifest.json", manifest)

    source_meta = json.loads((checkpoint / "tasam_marl_checkpoint_meta.json").read_text(encoding="utf-8"))
    source_contract = str(source_meta.get("economic_action_contract") or "")
    bootstrap_name = (
        "initial_checkpoint_economic_action_v3_per_du_sleep"
        if source_contract == ECONOMIC_ACTION_V3
        else "initial_checkpoint_applied_action_v2"
    )
    bootstrap, bootstrap_contract = _prepare_bootstrap_checkpoint(
        checkpoint, campaign / bootstrap_name, calibration_data
    )
    manifest["economic_action_contract"] = bootstrap_contract
    manifest["bootstrap_checkpoint"] = str(bootstrap)
    _write(campaign / "campaign_manifest.json", manifest)

    adaptation = campaign / "adaptation_online"
    code = _run(_arm("combined_online", adaptation, bootstrap, argparse.Namespace(**{**vars(args), "decisions": args.adaptation_decisions})), dry_run=args.dry_run, env=runtime_env)
    manifest["phases"]["adaptation_online"] = {"run_dir": str(adaptation), "exit_code": code}
    if code != 0 and not args.dry_run:
        # Preserve the diagnostic evidence even when the arm exits blocked.
        # The SQLite transition history is still useful for explaining why
        # promotion did not happen; replacing it with an empty report hid
        # the actual replay outcome.
        report = campaign / "online_observation_summary.json"
        block_reason = f"adaptação online terminou com código {code}"
        try:
            summary = _observation_summary(
                campaign, adaptation, bootstrap, calibration,
                min_applied_actions=args.min_applied_actions,
            )
        except (OSError, sqlite3.Error, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            summary = {
                "schema": "greenran.tasam.online_observation.v1",
                "metric_scope": "simulation",
                "causal_comparison": False,
                "valid": False,
                "summary_error": str(exc),
                "campaign_dir": str(campaign),
                "adaptation_dir": str(adaptation),
            }
        summary["valid"] = False
        summary["block_reason"] = block_reason
        summary["active_checkpoint"] = str(bootstrap)
        summary["checkpoint_promoted"] = False
        manifest["status"] = "adaptation_blocked"
        manifest["block_reason"] = block_reason
        manifest["observation_report"] = str(report)
        manifest["finished_at"] = int(time.time())
        _write(report, summary)
        _write(campaign / "learning_meter.json", summary.get("learning_meter", {
            "schema": "greenran.tasam.learning_meter.v1",
            "status": "BLOCKED",
            "learning_meter": 0.0,
            "reason": block_reason,
        }))
        _write(campaign / "campaign_manifest.json", manifest)
        return code
    if args.dry_run:
        trained = campaign / "adaptation_online" / "active_checkpoint_placeholder"
    else:
        try:
            trained = _active_checkpoint(adaptation, args.min_applied_actions)
        except (OSError, RuntimeError, sqlite3.Error, json.JSONDecodeError) as exc:
            manifest["status"] = "adaptation_blocked"
            manifest["block_reason"] = str(exc)
            report = campaign / "online_observation_summary.json"
            try:
                summary = _observation_summary(
                    campaign, adaptation, bootstrap, calibration,
                    min_applied_actions=args.min_applied_actions,
                )
            except (OSError, sqlite3.Error, json.JSONDecodeError, KeyError, TypeError, ValueError) as summary_exc:
                summary = {
                    "schema": "greenran.tasam.online_observation.v1",
                    "metric_scope": "simulation",
                    "causal_comparison": False,
                    "valid": False,
                    "block_reason": str(exc),
                    "summary_error": str(summary_exc),
                    "campaign_dir": str(campaign),
                    "adaptation_dir": str(adaptation),
                }
            summary["valid"] = False
            summary["block_reason"] = str(exc)
            summary["active_checkpoint"] = str(bootstrap)
            summary["checkpoint_promoted"] = False
            manifest["observation_report"] = str(report)
            manifest["finished_at"] = int(time.time())
            _write(report, summary)
            _write(campaign / "learning_meter.json", summary.get("learning_meter", {
                "schema": "greenran.tasam.learning_meter.v1",
                "status": "BLOCKED",
                "learning_meter": 0.0,
                "reason": str(exc),
            }))
            _write(campaign / "campaign_manifest.json", manifest)
            return 1
    manifest["trained_checkpoint"] = str(trained)

    if args.observe_only:
        report = campaign / "online_observation_summary.json"
        summary = _observation_summary(
            campaign, adaptation, trained, calibration,
            min_applied_actions=args.min_applied_actions,
        )
        manifest["observation_report"] = str(report)
        manifest["status"] = "adaptation_finished" if summary["valid"] else "adaptation_blocked"
        manifest["finished_at"] = int(time.time())
        _write(report, summary)
        _write(campaign / "learning_meter.json", summary["learning_meter"])
        _write(campaign / "campaign_manifest.json", manifest)
        return 0 if summary["valid"] else 1

    shadow = campaign / "shadow_gate"
    code = _run(_arm("combined_shadow", shadow, trained, args), dry_run=args.dry_run, env=runtime_env)
    manifest["phases"]["shadow_gate"] = {"run_dir": str(shadow), "exit_code": code}
    if code != 0 and not args.dry_run:
        _write(campaign / "campaign_manifest.json", manifest)
        return code

    gate_dir = campaign / "control_gate"
    gate_command = [
        sys.executable, str(GATE), "--checkpoint", str(trained),
        "--shadow-db", str(shadow / "rapp_data_lake.db"),
        "--output-dir", str(gate_dir), "--window", str(args.decisions),
        "--min-samples", str(args.decisions), "--min-positive-rate", "0.80",
        "--require-economic-head",
    ]
    code = _run(gate_command, dry_run=args.dry_run, env=runtime_env)
    manifest["phases"]["control_gate"] = {"run_dir": str(gate_dir), "exit_code": code}
    if code != 0 and not args.dry_run:
        _write(campaign / "campaign_manifest.json", manifest)
        return code

    baseline = campaign / "baseline_rapp_only"
    treatment = campaign / "treatment_frozen"
    pair_id = f"tasam-economic-seed-{args.seed}-v1"
    baseline_cmd = _arm("rapp_only", baseline, trained, args)
    treatment_cmd = _arm("combined", treatment, trained, args, gate=gate_dir / "marl_control_gate.json")
    baseline_cmd.extend(["--pairing-schedule-id", pair_id])
    treatment_cmd.extend(["--pairing-schedule-id", pair_id])
    code = _run(baseline_cmd, dry_run=args.dry_run, env=runtime_env)
    manifest["phases"]["baseline_rapp_only"] = {"run_dir": str(baseline), "exit_code": code}
    if code == 0 or args.dry_run:
        code = _run(treatment_cmd, dry_run=args.dry_run, env=runtime_env)
    manifest["phases"]["treatment_frozen"] = {"run_dir": str(treatment), "exit_code": code}

    report = campaign / "tasam_asgard_comparison.json"
    if code == 0 or args.dry_run:
        eval_command = [
            sys.executable, str(EVALUATOR), "--baseline", str(baseline),
            "--treatment", str(treatment), "--seed", str(args.seed),
            "--expected-sim-time", str(args.sim_time),
            "--expected-wall-time", str(args.wall_time), "--energy-calibration", str(calibration),
            "--metric-scope", "simulation", "--output", str(report),
        ]
        code = _run(eval_command, dry_run=args.dry_run, env=runtime_env)
    manifest["report"] = str(report)
    manifest["readable_report"] = str(report.with_suffix(".html"))
    manifest["status"] = "finished" if code == 0 else "failed"
    manifest["finished_at"] = int(time.time())
    _write(campaign / "campaign_manifest.json", manifest)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--seed", type=int, default=47)
    # The economic learner needs a sustained CLEAR/ADVISORY corpus.  The
    # categorical balanced curriculum deliberately contains hard pulses and
    # is not an appropriate default for applied-action replay.
    parser.add_argument("--profile", default="tasam_training_balanced_v3")
    parser.add_argument("--wall-time", type=float, default=3000.0)
    parser.add_argument("--adaptation-wall-time", type=float, default=43200.0)
    parser.add_argument("--sim-time", type=float, default=1200.0)
    parser.add_argument(
        "--native-fidelity", dest="native_fidelity", action="store_true", default=True,
        help="usar evidência nativa agregada v5 (padrão da campanha ASGARD)",
    )
    parser.add_argument(
        "--legacy-fidelity", dest="native_fidelity", action="store_false",
        help="somente compatibilidade histórica; não aprova evidência v9",
    )
    parser.add_argument("--performance-min-rtf", type=float, default=0.016)
    parser.add_argument("--adaptation-decisions", type=int, default=1200)
    parser.add_argument("--adaptation-max-decisions", type=int, default=1200)
    parser.add_argument("--decisions", type=int, default=300)
    parser.add_argument("--min-free-gib", type=float, default=10.0)
    parser.add_argument("--startup-min-free-gib", type=float, default=15.8)
    parser.add_argument("--min-new-snapshots", type=int, default=60)
    parser.add_argument("--min-trainable-transitions", type=int, default=180)
    parser.add_argument("--epochs-per-update", type=int, default=2)
    parser.add_argument("--controller-poll-seconds", type=float, default=10.0)
    parser.add_argument("--shadow-min-decisions", type=int, default=300)
    parser.add_argument("--stage-window-decisions", type=int, default=300)
    parser.add_argument("--max-rollout-fraction", type=float, default=0.50)
    parser.add_argument("--min-economic-transitions", type=int, default=180)
    parser.add_argument("--min-applied-actions", type=int, default=180)
    parser.add_argument("--economic-update-min-transitions", type=int, default=64)
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--vehicle-profile-manifest", type=Path, default=None)
    parser.add_argument(
        "--require-vehicle-feasibility", action="store_true",
        help="falhar antes de criar a campanha sem baseline veicular aprovado para este perfil",
    )
    parser.add_argument(
        "--benchmark-manifest", type=Path, default=None,
        help="manifesto do benchmark v9 usado para validar o mesmo binário",
    )
    parser.add_argument(
        "--warm-start", action="store_true",
        help="marca o checkpoint informado como inicialização; nenhuma evidência econômica do pai é reutilizada",
    )
    parser.add_argument("--warm-start-parent", type=Path, default=None)
    parser.add_argument("--parent-was-promoted", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
