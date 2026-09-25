#!/usr/bin/env python3
"""Run the non-promotable 90-observation rApp x ASGARD V2X pilot.

The simulator always runs the complete 120 s curriculum.  Only after an arm
finishes do we select the first ten complete, native transitions from each of
the nine stages.  A short or invalid stage is reported; it is never filled by
another stage or by synthetic rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
EXPORTER = ROOT / "scripts" / "export_tasam_article_dataset.py"
BOOTSTRAP = ROOT / "scripts" / "build_tasam_v10_checkpoint.py"
CLEANUP = ROOT / "scripts" / "cleanup_greenran_orphaned_run.py"
DEFAULT_BINARY = ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build-v9-optimized/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-optimized"
DEFAULT_CATEGORY_SOURCE = ROOT / "runs/tasam_asgard_adaptation_seed47_20260914_v9_fidelity3/initial_checkpoint_applied_action_v2"
PROFILE = "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback"
BASELINE_MAX_PROFILE = "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max"
ALLOWED_PROFILES = (PROFILE, BASELINE_MAX_PROFILE)
SEED = 43
SIM_TIME = 120.0
WALL_TIME = 9000.0
MIN_RTF = 0.016
STAGES = (
    "allowed_bootstrap", "allowed_stable", "camera_conditional", "camera_blocked",
    "vehicle_conditional", "vehicle_blocked", "app2_conditional", "app2_blocked",
    "allowed_recovery",
)
BASELINE_CONTRACT = "rapp_only_actuating_window90"
ASGARD_CONTRACT = "asgard_v2x_window90_online"
ASGARD_ENERGY_CONTRACT = "asgard_v2x_window90_energy_online"
REPLAY_SCHEMA = "greenran.tasam.v2x.window90.replay_80_20.v1"
REWARD_CONTRACT = "greenran.tasam.v2x.reward_adaptive.v1"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest() if path.exists() else ""


def tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.is_dir():
        return ""
    for item in sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts):
        digest.update(str(item.relative_to(path)).encode())
        digest.update(bytes.fromhex(sha256(item)))
    return digest.hexdigest()


def validate_fixed(args: argparse.Namespace) -> None:
    if args.profile not in ALLOWED_PROFILES:
        raise SystemExit(f"perfil obrigatório: um de {ALLOWED_PROFILES}")
    if args.seed != SEED or args.sim_time != SIM_TIME or args.wall_time != WALL_TIME:
        raise SystemExit("piloto exige seed 43, 120 s simulados e 9000 s de parede")
    if args.decision_target != 0 or args.performance_min_rtf != MIN_RTF:
        raise SystemExit("piloto exige decision-target=0 e RTF mínimo 0.016")
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        raise SystemExit(f"binário release ausente/não executável: {args.binary}")
    if not args.category_source.is_dir():
        raise SystemExit(f"checkpoint da cabeça categórica ausente: {args.category_source}")


def build_bootstrap(args: argparse.Namespace, output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        return
    subprocess.run([
        sys.executable, str(BOOTSTRAP),
        "--parent", str(args.category_source),
        "--output", str(output),
        "--seed", str(SEED),
        "--category-only",
    ], cwd=ROOT, check=True)


def run_arm(
    args: argparse.Namespace,
    arm_dir: Path,
    mode: str,
    checkpoint: Path,
    historical: Path | None = None,
    recent: Path | None = None,
    safe_power_floor_ledger: Path | None = None,
) -> int:
    command = [
        sys.executable, str(ARM),
        "--mode", mode,
        "--run-dir", str(arm_dir),
        "--seed", str(SEED),
        "--profile", args.profile,
        "--wall-time", str(int(WALL_TIME)),
        "--sim-time", str(int(SIM_TIME)),
        "--decision-target", "0",
        "--native-fidelity",
        "--performance-min-rtf", str(MIN_RTF),
        "--binary", str(args.binary),
        "--checkpoint", str(checkpoint),
        "--min-free-gib", "20",
        "--artifact-min-free-gib", "20",
        "--artifact-budget-gib", "4",
    ]
    if args.execution_slot:
        command.extend(["--execution-slot", args.execution_slot])
    if mode in {
        "rapp_only_actuating", "asgard_v2x_window90_online",
        "asgard_v2x_window90_energy_online",
    }:
        # Both arms consume only native observations.  The ASGARD arm must
        # not inherit the scenario-control override merely because it is the
        # online learner; otherwise its recent replay is ineligible.
        command.append("--disable-app-overrides")
    if args.energy_enabled:
        command.append("--energy-enabled")
        command.extend(["--energy-calibration", str(args.energy_calibration)])
    if args.energy_staircase and mode in {
        "asgard_v2x_window90_online", "asgard_v2x_window90_energy_online",
        "asgard_v2x_window90_energy_frozen",
    }:
        command.append("--energy-staircase")
        if safe_power_floor_ledger is not None:
            command.extend(["--safe-power-floor-ledger", str(safe_power_floor_ledger)])
    if mode != "rapp_only_actuating":
        command.extend([
            "--experience-bank", str(historical or arm_dir.parent / "baseline_replay.jsonl"),
            "--recent-experience-bank", str(recent or arm_dir / "recent_replay.jsonl"),
            "--replay-rows", "90",
            "--min-new-snapshots", "18",
            "--min-trainable-transitions", "90",
            "--epochs-per-update", "1",
            "--controller-poll-seconds", "5",
            "--max-rollout-fraction", "1.0",
        ])
    print("[window90] iniciando:", " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT).returncode


def cleanup_finished_arm(arm_dir: Path, audit_dir: Path, *, reason: str) -> None:
    """Drain only the exact arm process tree before reusing its slot.

    The arm's traces and database are already sealed by ``run_arm``.  The
    state-scoped cleanup removes lingering collectors/RICs without touching
    those artifacts, which is required when the next arm reuses the same
    execution slot.
    """
    if not arm_dir.is_dir():
        return
    result = subprocess.run(
        [
            sys.executable, str(CLEANUP),
            "--state-dir", str(arm_dir),
            "--audit-dir", str(audit_dir),
            "--reason", reason,
            "--execute",
        ],
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"limpeza controlada do arm falhou: {arm_dir}")


def export_rows(
    arm_dir: Path,
    output: Path,
    *,
    energy_enabled: bool = False,
    energy_calibration: Path | None = None,
) -> None:
    db = arm_dir / "rapp_data_lake.db"
    audit = arm_dir / "xapp_intents" / "tasam_control_audit.jsonl"
    if not db.is_file():
        output.write_text("", encoding="utf-8")
        return
    subprocess.run([
        sys.executable, str(EXPORTER),
        "--db", str(db), "--output-jsonl", str(output),
        "--e2-audit-jsonl", str(audit), "--max-step-gap-s", "60",
        "--max-sim-reset-gap-s", "1", "--limit", "10000",
        "--reward-contract", REWARD_CONTRACT, "--include-invalid",
    ] + (["--energy-enabled", "--energy-calibration", str(energy_calibration)] if energy_enabled and energy_calibration else [])
    , cwd=ROOT, check=True)


def _native_control_ids(arm_dir: Path) -> tuple[set[int], dict[int, dict[str, Any]]]:
    context_ids: set[int] = set()
    observations: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "kinds": set(), "correlations": set(), "cells": set(),
            "power_by_cell": {},
        }
    )
    context = arm_dir / "ns3_energy" / "NativeControlContext.csv"
    context_by_sequence: dict[int, dict[str, Any]] = {}
    if context.is_file():
        with context.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                try:
                    decision_id = int(row.get("DecisionId", 0) or 0)
                except ValueError:
                    continue
                if decision_id:
                    context_ids.add(decision_id)
                    try:
                        sequence = int(row.get("NativeControlSequence", 0) or 0)
                    except (TypeError, ValueError):
                        sequence = 0
                    if sequence > 0:
                        context_by_sequence[sequence] = {
                            "decision_id": decision_id,
                            "correlation": str(row.get("ActionCorrelationId") or ""),
                        }
    trace = arm_dir / "ns3_energy" / "TasamControlObservations.csv"
    if trace.is_file():
        with trace.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                try:
                    decision_id = int(row.get("DecisionId", 0) or 0)
                except ValueError:
                    continue
                try:
                    sequence = int(row.get("NativeControlSequence", 0) or 0)
                except (TypeError, ValueError):
                    sequence = 0
                context_row = context_by_sequence.get(sequence) if sequence > 0 else None
                if not decision_id and context_row:
                    decision_id = int(context_row["decision_id"])
                if not decision_id:
                    continue
                observations[decision_id]["kinds"].add(str(row.get("ObservationKind") or ""))
                if row.get("ActionCorrelationId"):
                    observations[decision_id]["correlations"].add(str(row["ActionCorrelationId"]))
                elif context_row and context_row.get("correlation"):
                    observations[decision_id]["correlations"].add(str(context_row["correlation"]))
                try:
                    cell_id = int(row.get("CellId", 0) or 0)
                except (TypeError, ValueError):
                    cell_id = 0
                if cell_id:
                    observations[decision_id]["cells"].add(cell_id)
                    try:
                        power = float(row.get("TxPowerPercent"))
                    except (TypeError, ValueError):
                        power = None
                    if power is not None and power == power and power >= 0.0:
                        observations[decision_id]["power_by_cell"][cell_id] = power
    return context_ids, observations


def build_safe_power_floor_ledger(arm_dir: Path, output: Path) -> dict[str, Any]:
    """Derive a conservative floor only from complete native rApp readbacks."""
    _context_ids, observations = _native_control_ids(arm_dir)
    confirmed: list[dict[int, float]] = []
    for item in observations.values():
        correlations = item.get("correlations", set())
        if not any(str(value).startswith("operational:rapp_live:") for value in correlations):
            continue
        if not (
            item.get("cells", set()) >= {2, 3, 4}
            and {"power_readback", "state_snapshot"}.issubset(item.get("kinds", set()))
            and set(item.get("power_by_cell", {})) >= {2, 3, 4}
        ):
            continue
        confirmed.append({cell: float(item["power_by_cell"][cell]) for cell in (2, 3, 4)})
    if not confirmed:
        report = {
            "schema": "greenran.tasam.v2x.safe_power_floor.v1",
            "status": "invalid",
            "source": "native_rapp_readback",
            "reason": "no_complete_rapp_live_three_cell_readback",
            "safe_floor_percent_by_cell": {},
            "confirmed_sequences": 0,
        }
    else:
        report = {
            "schema": "greenran.tasam.v2x.safe_power_floor.v1",
            "status": "validated",
            "source": "native_rapp_readback_minimum_per_cell",
            "safe_floor_percent_by_cell": {
                str(cell): min(sample[cell] for sample in confirmed)
                for cell in (2, 3, 4)
            },
            "confirmed_sequences": len(confirmed),
            "trace_sha256": sha256(arm_dir / "ns3_energy" / "TasamControlObservations.csv"),
        }
    write_json(output, report)
    return report


def _native_pdcp_trace_exists(arm_dir: Path) -> bool:
    """Accept only the current native bundle or its legacy colocated trace."""
    return any(
        (arm_dir / relative).is_file()
        for relative in (
            "ns3_energy/DlPdcpStats.txt",
            "ns3_traces/DlPdcpStats.txt",
        )
    )


def _rows(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not path.is_file():
        return result
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                result.append(value)
    return result


def _transition_id(row: dict[str, Any], phase: str) -> str:
    return f"window90|{phase}|{SEED}|{row.get('timestamp')}|{row.get('decision_id')}"


def select_stage_transitions(
    arm_dir: Path,
    raw_export: Path,
    phase: str,
    *,
    require_asgard: bool = False,
    reference_arm: bool = False,
    energy_enabled: bool = False,
    min_per_stage: int = 10,
    max_total: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from greenran_v2x_adaptive_reward import compose_adaptive_reward
    from greenran_v2x_window90 import validate_online_transition

    context_ids, observations = _native_control_ids(arm_dir)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected = Counter()
    for row in _rows(raw_export):
        stage = str(row.get("decision_stage_name") or row.get("scenario_stage") or "")
        if stage not in STAGES:
            continue
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        try:
            decision_id = int(row.get("decision_id") or decision.get("id") or 0)
        except (TypeError, ValueError):
            decision_id = 0
        quality = dict(row.get("collection_quality") or {})
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        native = (
            quality.get("collector_mode") == "pdcp_real"
            and bool(quality.get("pdcp_real"))
            and float(quality.get("proxy_latency_sample_count", 0) or 0) == 0.0
            and _native_pdcp_trace_exists(arm_dir)
        )
        control = observations.get(decision_id, {})
        kinds = control.get("kinds", set())
        native_ack = bool(
            decision_id in context_ids
            and {"power_readback", "state_snapshot"}.issubset(kinds)
            and control.get("cells", set()) >= {2, 3, 4}
        )
        ack = native_ack or (
            bool(decision.get("e2_ack_complete"))
            and bool(decision.get("native_readback_observed"))
        )
        if energy_enabled:
            ack = ack and (
                native_ack or bool(decision.get("e2_cell_ack_complete"))
            )
            ack = ack and {"power_readback", "state_snapshot"}.issubset(kinds)
            ack = ack and control.get("cells", set()) >= {2, 3, 4}
        else:
            ack = ack and "power_readback" in kinds
        ack = ack and decision_id in context_ids
        action_correlation_id = str(
            decision.get("action_correlation_id")
            or next(iter(sorted(control.get("correlations", set()))), "")
        )
        correlated = bool(
            decision_id
            and action_correlation_id
            and action_correlation_id in control.get("correlations", set())
        )
        if reference_arm:
            correlated = correlated and action_correlation_id.startswith("operational:")
        valid = bool(
            native and ack and correlated
            and row.get("scenario_control_override") is False
            and quality.get("metric_alignment_valid") is True
            and not quality.get("sim_reset")
            and row.get("next_metrics") is not None
        )
        if valid and require_asgard:
            live_row = dict(row)
            live_row["action_correlation_valid"] = True
            valid, _ = validate_online_transition(
                live_row, require_adaptive_reward=True, require_judge=True
            )
        if not valid:
            rejected["invalid_transition"] += 1
            continue
        observation = {
            "collector_mode": "pdcp_real", "pdcp_real": True,
            "proxy_latency_sample_count": 0, "e2_ack_complete": True,
            "decision_correlation_valid": True,
            "vehicle_metrics": {
                "max_latency_ms": float(metrics.get("latency_p95_per_ue_us", 0) or 0) / 1000.0,
                "packet_loss_percent": float(metrics.get("global_packet_loss_rate", 0) or 0) * 100.0,
            },
            "camera_metrics": {}, "app2_metrics": {},
        }
        energy_evidence = row.get("energy_evidence") if isinstance(row.get("energy_evidence"), dict) else {}
        if reference_arm and energy_enabled:
            # The rApp remains the unrestricted reference. Native readback is
            # valid paired evidence, but this row is never an ASGARD replay
            # transition and never receives a TA-SAM economic label.
            power_by_cell = control.get("power_by_cell", {})
            energy_evidence = {
                "native": True,
                "reference_only": True,
                "e2_ack": True,
                "evidence_version": "v6",
                "action_correlation_id": action_correlation_id,
                "power_percent_by_cell": {
                    str(cell): value for cell, value in sorted(power_by_cell.items())
                },
                "power_percent": (
                    sum(power_by_cell.values()) / len(power_by_cell)
                    if power_by_cell else None
                ),
            }
        observation["energy_evidence"] = energy_evidence
        reward = compose_adaptive_reward(
            None, observation, {"schema": REWARD_CONTRACT},
            energy_enabled=energy_enabled,
            energy_evidence=energy_evidence,
        )
        # The rApp reference arm is allowed to lack a TA-SAM reward-energy
        # snapshot: it is the unrestricted control whose integrated ns-3
        # energy is measured later.  The ASGARD arm must still prove native
        # energy evidence before any selected transition can train it.
        if energy_enabled and not energy_evidence.get("native", False):
            rejected["energy_evidence_invalid"] += 1
            continue
        item = dict(row)
        quality.update({
            "collector_mode": "pdcp_real", "pdcp_real": True,
            "proxy_latency_sample_count": 0, "e2_ack_complete": True,
            "decision_correlation_valid": True, "valid_for_training": True,
            "pilot_native_evidence": True,
        })
        item.update({
            "collection_quality": quality,
            "reward_contract": REWARD_CONTRACT,
            "adaptive_reward": reward,
            "energy_evidence": energy_evidence,
            "reward_weight_snapshot": reward.get("snapshot"),
            "tasam_continuous_reward": reward.get("continuous_reward"),
            "tasam_training_reward": reward.get("reward"),
            "replay_phase": phase,
            "replay_episode": "window90_seed43",
            "replay_seed": SEED,
            "replay_timestamp": str(row.get("timestamp")),
            "replay_partition": "training",
            "official_evaluation": False,
            "evaluation_frozen": False,
            "scenario_control_override": False,
            "e2_ack_complete": True,
            "action_correlation_valid": True,
            "tasam_experience_id": _transition_id(row, phase),
            "economic_reference_eligible": bool(reference_arm),
            "economic_transition_eligible": bool(
                not reference_arm and row.get("economic_transition_eligible") is True
            ),
        })
        grouped[stage].append(item)

    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for stage in STAGES:
        if max_total is not None and len(selected) >= max_total:
            counts[stage] = 0
            continue
        remaining = max_total - len(selected) if max_total is not None else min_per_stage
        chosen = grouped.get(stage, [])[:min(min_per_stage, remaining)]
        counts[stage] = len(chosen)
        selected.extend(chosen)
        if len(chosen) < min_per_stage and max_total is None:
            rejected[f"stage_short:{stage}"] += 1
    expected_total = max_total if max_total is not None else min_per_stage * len(STAGES)
    report = {
        "expected_per_stage": min_per_stage,
        "max_total": max_total,
        "selected_total": len(selected),
        "per_stage": counts,
        "complete": len(selected) == expected_total and (
            max_total is not None or all(counts[s] == min_per_stage for s in STAGES)
        ),
        "rejected": dict(rejected),
        "native_pdcp": bool(selected) and all(r["collection_quality"].get("collector_mode") == "pdcp_real" for r in selected),
        "proxy_count": sum(float(r.get("collection_quality", {}).get("proxy_latency_sample_count", 0) or 0) for r in selected),
        "ack_count": sum(1 for r in selected if r.get("e2_ack_complete") is True),
        "require_asgard": require_asgard,
    }
    return selected, report


def write_bank(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "schema": "greenran.tasam.v2x.window90.replay_source.v1",
        "path": str(path), "rows": len(rows), "sha256": sha256(path),
        "seeds": sorted({int(row.get("replay_seed")) for row in rows}),
        "stages": dict(Counter(str(row.get("replay_phase")) for row in rows)),
        "evaluation_seeds_excluded": not any(int(row.get("replay_seed", -1)) in {45, 46, 47} for row in rows),
    }


def arm_summary(arm_dir: Path, returncode: int, selection: dict[str, Any]) -> dict[str, Any]:
    manifest = read_json(arm_dir / "arm_manifest.json")
    performance = manifest.get("simulation_performance") if isinstance(manifest.get("simulation_performance"), dict) else {}
    return {
        "returncode": returncode,
        "status": manifest.get("status", ""),
        "sim_time_observed_s": performance.get("sim_time_observed_s"),
        "wall_time_observed_s": performance.get("wall_time_observed_s"),
        "rtf": performance.get("rtf"),
        "selection": selection,
        "arm_manifest_sha256": sha256(arm_dir / "arm_manifest.json"),
    }


def validate_reusable_baseline(
    source: Path, *, expected_profile: str
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Validate a completed rApp arm before referencing it from a new run."""
    arm = source / "baseline" / "arm"
    selection_path = source / "baseline" / "selection_manifest.json"
    bank_path = source / "baseline" / "replay_90.jsonl"
    manifest = read_json(arm / "arm_manifest.json")
    selection = read_json(selection_path)
    performance = manifest.get("simulation_performance") or {}
    if (
        manifest.get("status") != "finished"
        or manifest.get("seed") != SEED
        or manifest.get("profile") != expected_profile
        or manifest.get("real_only_collection") is not True
        or manifest.get("scenario_control_override_allowed") is not False
        or performance.get("valid") is not True
    ):
        raise SystemExit("baseline de referência não satisfaz o contrato real-only")
    if (
        selection.get("complete") is not True
        or selection.get("selected_total") != 90
        or selection.get("native_pdcp") is not True
        or selection.get("proxy_count") is None
        or float(selection.get("proxy_count")) != 0.0
        or selection.get("ack_count") != 90
    ):
        raise SystemExit("seleção da baseline de referência não é 9x10 válida")
    rows = _rows(bank_path)
    if len(rows) != 90 or any(
        row.get("scenario_control_override") is not False
        or (row.get("collection_quality") or {}).get("collector_mode") != "pdcp_real"
        or int(row.get("replay_seed", -1)) != SEED
        for row in rows
    ):
        raise SystemExit("banco replay_90 da baseline de referência é inválido")
    return manifest, rows, selection


def run(args: argparse.Namespace) -> int:
    validate_fixed(args)
    root = args.output_root.resolve()
    resuming = bool(args.resume_baseline)
    if root.exists() and any(root.iterdir()) and not resuming:
        raise SystemExit(f"output-root não está vazio: {root}")
    root.mkdir(parents=True, exist_ok=True)
    bootstrap = root / "asgard_bootstrap"
    build_bootstrap(args, bootstrap)
    protocol = {
        "schema": "greenran.tasam.v2x.window90.pilot.v1",
        "campaign_kind": (
            "v2x_energy_window90_rapp_x_asgard"
            if args.energy_enabled else "v2x_online_rapp_x_asgard_window90"
        ),
        "seed": SEED, "profile": args.profile, "sim_time_s": SIM_TIME,
        "wall_time_limit_s": WALL_TIME, "decision_target": 0,
        "native_fidelity": True, "performance_min_rtf": MIN_RTF,
        "energy_enabled": bool(args.energy_enabled), "stages": list(STAGES),
        "baseline_contract": BASELINE_CONTRACT,
        "asgard_contract": (
            ASGARD_ENERGY_CONTRACT if args.energy_enabled else ASGARD_CONTRACT
        ), "replay_contract": REPLAY_SCHEMA,
        "promotion_eligible": False,
        "resumed_complete_baseline": resuming,
        "baseline_source": str(args.baseline_source.resolve()) if args.baseline_source else "",
    }
    write_json(root / "protocol.json", protocol)

    baseline_arm = root / "baseline" / "arm"
    baseline_summary_arm = baseline_arm
    floor_ledger = root / "baseline" / "safe_power_floor_ledger.json"
    baseline_source_manifest: dict[str, Any] = {}
    if resuming:
        source = (args.baseline_source or root).resolve()
        baseline_manifest, baseline_rows, baseline_selection = validate_reusable_baseline(
            source, expected_profile=args.profile
        )
        baseline_summary_arm = source / "baseline" / "arm"
        floor_ledger = source / "baseline" / "safe_power_floor_ledger.json"
        baseline_source_manifest = {
            "campaign_root": str(source),
            "arm_manifest_sha256": sha256(source / "baseline" / "arm" / "arm_manifest.json"),
            "selection_manifest_sha256": sha256(source / "baseline" / "selection_manifest.json"),
            "replay_90_sha256": sha256(source / "baseline" / "replay_90.jsonl"),
        }
        baseline_rc = 0
    else:
        baseline_rc = run_arm(args, baseline_arm, "rapp_only_actuating", args.category_source)
        cleanup_finished_arm(
            baseline_arm,
            root / "cleanup_audit_baseline",
            reason="window90_baseline_arm_finished_before_slot_reuse",
        )
        baseline_raw = root / "baseline" / "raw_export.jsonl"
        export_rows(
            baseline_arm, baseline_raw, energy_enabled=args.energy_enabled,
            energy_calibration=args.energy_calibration,
        )
        baseline_rows, baseline_selection = select_stage_transitions(
            baseline_arm, baseline_raw, "baseline_rapp_only",
            energy_enabled=args.energy_enabled, reference_arm=True,
        )
        build_safe_power_floor_ledger(baseline_arm, floor_ledger)
    if resuming:
        baseline_bank = (args.baseline_source.resolve() / "baseline" / "replay_90.jsonl")
        bank_report = {
            "schema": "greenran.tasam.v2x.window90.replay_source.v1",
            "path": str(baseline_bank), "rows": len(baseline_rows),
            "sha256": sha256(baseline_bank), "reused_immutable": True,
        }
    else:
        baseline_bank = root / "baseline" / "replay_90.jsonl"
        bank_report = write_bank(baseline_bank, baseline_rows)
        write_json(root / "baseline" / "selection_manifest.json", baseline_selection)

    asgard_arm = root / "asgard" / "arm"
    recent_bank = root / "asgard" / "recent_replay.jsonl"
    asgard_mode = (
        "asgard_v2x_window90_energy_online"
        if args.energy_enabled else ASGARD_CONTRACT
    )
    asgard_rc = run_arm(
        args, asgard_arm, asgard_mode, bootstrap, baseline_bank, recent_bank,
        safe_power_floor_ledger=floor_ledger,
    )
    cleanup_finished_arm(
        asgard_arm,
        root / "cleanup_audit_asgard",
        reason="window90_asgard_arm_finished",
    )
    asgard_raw = root / "asgard" / "raw_export.jsonl"
    export_rows(
        asgard_arm, asgard_raw, energy_enabled=args.energy_enabled,
        energy_calibration=args.energy_calibration,
    )
    asgard_rows, asgard_selection = select_stage_transitions(
        asgard_arm, asgard_raw, "asgard_online",
        require_asgard=True, energy_enabled=args.energy_enabled,
    )
    write_json(root / "asgard" / "selection_manifest.json", asgard_selection)

    baseline_ok = baseline_rc == 0 and bool(baseline_selection.get("complete")) and baseline_bank.is_file() and len(baseline_rows) == 90
    asgard_ok = asgard_rc == 0 and bool(asgard_selection.get("complete"))
    update_status = read_json(asgard_arm / "online_state.json")
    updates = []
    for candidate in sorted((asgard_arm / "candidates").glob("candidate_*/replay_manifest.json")):
        updates.append(read_json(candidate))
    if baseline_ok and asgard_ok and len(updates) >= 5:
        status = "pilot_complete"
    elif asgard_rc != 0 and not asgard_arm.exists():
        status = "cancelled"
    elif not baseline_ok:
        status = "pilot_metric_invalid"
    else:
        status = "pilot_training_incomplete"
    report = {
        "schema": "greenran.tasam.v2x.window90.pilot_report.v1",
        "campaign_kind": (
            "v2x_energy_window90_rapp_x_asgard"
            if args.energy_enabled else "v2x_online_rapp_x_asgard_window90"
        ),
        "status": status,
        "scientific_decision": "not_promotable",
        "promotion_eligible": False,
        "resumed_complete_baseline": resuming,
        "baseline_provenance": baseline_source_manifest,
        "baseline": arm_summary(baseline_summary_arm, baseline_rc, baseline_selection),
        "asgard": arm_summary(asgard_arm, asgard_rc, asgard_selection),
        "replay": bank_report,
        "asgard_online_state": update_status,
        "replay_update_manifests": updates,
        "safe_power_floor_ledger": read_json(floor_ledger),
        "required_update_milestones": [18, 36, 54, 72, 90],
        "seeds_used": {"training": [43], "evaluation_excluded": [45, 46, 47]},
        "checkpoint": {
            "role": "pilot_only_non_promotable",
            "bootstrap": str(bootstrap), "bootstrap_sha256": tree_sha256(bootstrap),
            "category_source": str(args.category_source),
            "category_source_sha256": tree_sha256(args.category_source),
        },
        "contracts": {
            "baseline": BASELINE_CONTRACT,
            "asgard": ASGARD_ENERGY_CONTRACT if args.energy_enabled else ASGARD_CONTRACT,
            "replay": REPLAY_SCHEMA, "reward": REWARD_CONTRACT,
        },
    }
    manifest = {
        **report,
        "profile": args.profile, "seed": SEED, "sim_time_s": SIM_TIME,
        "binary": str(args.binary), "binary_sha256": sha256(args.binary),
        "code_sha256": sha256(Path(__file__)),
        "source_tree_sha256": tree_sha256(ROOT / "src"),
        "stage_order": list(STAGES),
    }
    write_json(root / "campaign_manifest.json", manifest)
    write_json(root / "campaign_report.json", report)
    return 0 if status == "pilot_complete" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "runs/tasam_v2x_window90_online_seed43_20260922_r1")
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--category-source", type=Path, default=DEFAULT_CATEGORY_SOURCE)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--sim-time", type=float, default=SIM_TIME)
    parser.add_argument("--wall-time", type=float, default=WALL_TIME)
    parser.add_argument("--decision-target", type=int, default=0)
    parser.add_argument("--performance-min-rtf", type=float, default=MIN_RTF)
    parser.add_argument(
        "--execution-slot", choices=("slot-a", "slot-b"), default=None,
        help="slot exclusivo usado pelos braços de coleta/treino",
    )
    parser.add_argument("--energy-enabled", action="store_true")
    parser.add_argument(
        "--energy-staircase", action="store_true",
        help="habilita a escada econômica V2X somente no braço TA-SAM",
    )
    parser.add_argument(
        "--energy-calibration", type=Path,
        default=ROOT / "config/energy_calibration_sim_v3_sleep.json",
        help="calibração versionada usada somente quando a energia é habilitada",
    )
    parser.add_argument(
        "--resume-baseline", action="store_true",
        help="reutiliza somente um arm baseline já terminado e válido; nunca reutiliza arm parcial",
    )
    parser.add_argument(
        "--baseline-source", type=Path, default=None,
        help="campanha anterior cuja baseline 9x10 selada será referenciada por hash",
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
