#!/usr/bin/env python3
"""Run one reproducible GreenRAN arm for the TA-SAM energy campaign.

The three supported arms are deliberately explicit:

``train_no_armd``
    ARMD disabled; TA-SAM acts in full-control mode and learns only from
    transitions produced by this run.
``rapp_only``
    ARMD and TA-SAM disabled; the native rApp is the only controller.
``combined``
    ARMD provides context/protection, TA-SAM proposes, and the rApp Judge
    applies the assistant package from a frozen pre-trained checkpoint.

This launcher is intentionally separate from the historical validation
launchers.  It does not rewrite v8/v9 artifacts and refuses to use a path
whose name identifies ARMD.
"""

from __future__ import annotations

import argparse
import atexit
from contextlib import closing
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from greenran_infra_budget import (  # noqa: E402
    CgroupV2Controller,
    InfraBudgetError,
    SYSTEMD_USER_SCOPE_BACKEND,
    assert_cgroup_delegation,
    build_physical_budget,
    probe_systemd_user_scope,
)
from greenran_infra_monitor import InfrastructureMonitor  # noqa: E402
from energy_calibration import load_calibration  # noqa: E402
from tasam_dynamic_floor import (  # noqa: E402
    DYNAMIC_FLOOR_CONTRACT,
    DynamicFloorError,
    load_baseline_signature,
    load_dynamic_floor_ledger,
)
from tasam_native_sleep_calibration import execute_native_sleep_calibration  # noqa: E402
try:
    from greenran_v2x_binary_freshness import assert_v2x_binary_fresh, build_provenance  # noqa: E402
except ModuleNotFoundError:
    from scripts.greenran_v2x_binary_freshness import assert_v2x_binary_fresh, build_provenance  # noqa: E402
try:
    from run_tasam_online_controlled import prune_candidate_artifacts  # noqa: E402
except ModuleNotFoundError:
    from scripts.run_tasam_online_controlled import prune_candidate_artifacts  # noqa: E402
try:
    from v2x_parallel_slots import SlotLease, assert_disk_capacity, resolve_slots  # noqa: E402
except ModuleNotFoundError:
    from scripts.v2x_parallel_slots import SlotLease, assert_disk_capacity, resolve_slots  # noqa: E402
WALL_RUNNER = ROOT / "scripts" / "run_greenran_tasam_3du_wall10m.sh"
ONLINE_CONTROLLER = ROOT / "scripts" / "run_tasam_online_controlled.py"
DECISION_TARGET_WATCHER = ROOT / "scripts" / "stop_on_decision_target.py"
DEFAULT_CHECKPOINT = ROOT / "runs/tasam_local_checkpoint_seed47_20260906"
PROFILE = "tasam_training_balanced_v3"
BASELINE_MAX_PROFILE = "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max"
PARALLEL_PAIR_RESOURCE_PROFILE = "parallel_pair_v1"
MODES = {
    "train_no_armd", "rapp_only", "rapp_only_actuating", "fixed_100_native",
    "native_sleep_calibration", "combined",
    "combined_shadow", "combined_online", "combined_actuation_smoke",
    # Article-faithful V2X arms.  They are separate from the legacy economic
    # modes so their replay, SAM choice and promotion criteria cannot leak
    # into historical campaigns.
    "sac_l2_online", "sac_l2_frozen", "tasam_v2x_online", "tasam_v2x_frozen",
    "asgard_v2x_window90_online",
    "asgard_v2x_window90_energy_online", "asgard_v2x_window90_energy_frozen",
    "asgard_v2x_window90_energy_dynamic",
}
V2X_ENERGY_MODES = {
    "asgard_v2x_window90_energy_online", "asgard_v2x_window90_energy_frozen",
    "asgard_v2x_window90_energy_dynamic",
}
CALIBRATION_FIXED_POWER_LEVELS = {25, 45, 70, 100}
V2X_WINDOW90_ONLINE_MODES = {
    "asgard_v2x_window90_online",
    "asgard_v2x_window90_energy_online",
    "asgard_v2x_window90_energy_dynamic",
}


def _is_v6_v2x_profile(profile: str) -> bool:
    """Return true for the versioned V6 V2X profiles, including V6.1."""
    normalized = str(profile or "").strip().lower()
    return normalized.startswith("tasam_training_balanced_v6") and "_v2x_" in normalized
PROTECTED_MARKERS = ("v8_full_control", "v9_directional")
EXPECTED_NS3_BINARY = "ns3.42-Energy_saving_with_cell_utilization_scenario"
OPTIMIZED_NS3_BINARY = ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build-v9-optimized/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-optimized"
DEFAULT_NS3_BINARY = (
    OPTIMIZED_NS3_BINARY
    if OPTIMIZED_NS3_BINARY.is_file()
    else ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default"
)
EXPECTED_NS3_BINARIES = {
    EXPECTED_NS3_BINARY,
    f"{EXPECTED_NS3_BINARY}-default",
    f"{EXPECTED_NS3_BINARY}-optimized",
}
LOCAL_RUNTIME_ROOT = ROOT.resolve()
DEFAULT_BENCHMARK_MANIFEST = (
    ROOT / "runs/tasam_asgard_performance_benchmark_seed47_20260915_v9_fidelity8"
    / "arm_manifest.json"
)


def _short_socket_dir(run_dir: Path) -> Path:
    """Return a collision-resistant socket directory below AF_UNIX limits."""
    digest = hashlib.sha256(str(run_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path("/tmp") / "greenran-sockets" / digest


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _reconcile_wall_status(
    run_dir: Path,
    wall_status: int,
    reason: str,
    *,
    completion_verified: bool,
) -> None:
    """Close a wall supervisor status left running by a finite ns-3 exit."""
    path = run_dir / "wall_clock_status.json"
    status = _read_json(path)
    if not status or status.get("phase") != "running":
        return
    now = time.time()
    started = status.get("started_at")
    status.update({
        # A SIGTERM is not proof that ns-3 finished its requested simulation.
        # It is "finished" only when a separate, contract-specific completion
        # proof has been established (finite sim-time or an allowed watcher).
        "phase": "finished" if wall_status == 0 or completion_verified else "cancelled",
        "finished_at": datetime.fromtimestamp(now, tz=timezone.utc).astimezone().isoformat(timespec="seconds"),
        "elapsed_s": status.get("elapsed_s", 0.0),
        "remaining_s": 0.0,
        "reconciled_by_arm": True,
        "reconciliation_reason": reason or (
            "wall_time_complete" if wall_status == 0
            else ("verified_cooperative_completion" if completion_verified else "external_termination")
        ),
        "wall_runner_exit_code": wall_status,
    })
    _write_json(path, status)


def _free_gib(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024 ** 3)


def _directory_size_bytes(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _native_actuation_evidence(
    run_dir: Path, *, strict_v5: bool | None = None
) -> dict[str, Any]:
    """Summarize independent ns-3 actuation evidence for the E2 smoke.

    A clean arm exit, an ACK, or a written control bundle is not enough for
    this smoke.  The native trace must contain a non-zero transaction and the
    Data Lake must have correlated at least one confirmation.
    """
    trace = run_dir / "ns3_energy" / "TasamControlObservations.csv"
    node_manifest_path = run_dir / "ns3_energy" / "E2NodeManifest.json"
    transactions: set[int] = set()
    scheduler_transactions: set[int] = set()
    power_transactions: set[int] = set()
    native_cells_by_tx: dict[int, dict[str, set[int]]] = {}
    native_identity_by_tx: dict[int, dict[str, set[Any]]] = {}
    # A transaction can remain visible in the native trace while a newer
    # scheduler snapshot is being published.  Aggregate identity only within
    # one coherent (sequence, decision, correlation) context; taking the
    # union across the whole transaction would incorrectly combine a stale
    # policy row from the previous decision with the current power readback.
    native_contexts_by_tx: dict[
        int, dict[tuple[int, int, str], dict[str, set[int]]]
    ] = {}
    rows = 0
    arm_manifest = _read_json(run_dir / "arm_manifest.json")
    required_version = str(
        arm_manifest.get("native_evidence_version")
        or os.environ.get("GREENRAN_NATIVE_EVIDENCE_VERSION", "v3")
    )
    # v4 remains readable for historical diagnostics.  Any current v5 arm is
    # strict: the three-DU identity manifest and per-row correlation metadata
    # are part of the confirmation boundary, not optional decorations.
    if strict_v5 is None:
        strict_v5 = required_version == "v5"
    manifest = _read_json(node_manifest_path)
    expected_generation = f"{run_dir.name}:native-{required_version}"
    manifest_failures: list[str] = []
    if strict_v5:
        if manifest.get("schema") != "greenran.ns3.e2_node_manifest.v1":
            manifest_failures.append("manifest_schema")
        if str(manifest.get("campaign_id") or "") != run_dir.name:
            manifest_failures.append("manifest_campaign_id")
        if str(manifest.get("evidence_version") or "") != "v5":
            manifest_failures.append("manifest_evidence_version")
        if str(manifest.get("source_generation") or "") != expected_generation:
            manifest_failures.append("manifest_source_generation")
        nodes = manifest.get("nodes")
        if not isinstance(nodes, list) or len(nodes) != 3:
            manifest_failures.append("manifest_node_count")
            nodes = []
        cell_ids: list[int] = []
        node_ids: list[int] = []
        for node in nodes:
            if not isinstance(node, dict):
                manifest_failures.append("manifest_node_record")
                continue
            try:
                cell_ids.append(int(node["cell_id"]))
                node_ids.append(int(node["ns3_node_id"]))
                if int(node["e2_node_id"]) != int(node["ns3_node_id"]):
                    manifest_failures.append("manifest_e2_ns3_identity")
            except (KeyError, TypeError, ValueError):
                manifest_failures.append("manifest_node_identity")
            if str(node.get("control_protocol") or "") != "e2_rc":
                manifest_failures.append("manifest_control_protocol")
            if node.get("rc_control_supported") is not True:
                manifest_failures.append("manifest_rc_control")
        if set(cell_ids) != {2, 3, 4} or len(set(cell_ids)) != 3:
            manifest_failures.append("manifest_cells")
        if len(set(node_ids)) != len(node_ids):
            manifest_failures.append("manifest_node_ids")
    try:
        import csv
        with trace.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                rows += 1
                try:
                    transaction = int(row.get("SchedulerTransactionId", 0) or 0)
                    power_transaction = int(row.get("PowerTransactionId", 0) or 0)
                    cell_id = int(row.get("CellId", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if transaction > 0:
                    scheduler_transactions.add(transaction)
                kind = str(row.get("ObservationKind") or "")
                version = str(row.get("EvidenceVersion") or "")
                if version != required_version:
                    continue
                trace_campaign = str(row.get("CampaignId") or "")
                if trace_campaign and trace_campaign != str(run_dir.name):
                    continue
                trace_generation = str(row.get("SourceGeneration") or "")
                if trace_generation and trace_generation != expected_generation:
                    continue
                if strict_v5 and (
                    trace_campaign != run_dir.name
                    or trace_generation != expected_generation
                    or version != "v5"
                ):
                    continue
                # The native trace deliberately separates transport/power
                # identity from scheduler identity.  A power_readback row
                # may have SchedulerTransactionId=0 while the later active
                # policy snapshot has both IDs populated.  Correlate both
                # observations through PowerTransactionId, falling back to
                # SchedulerTransactionId for older state-only rows.
                transaction_key = power_transaction or transaction
                if strict_v5 and transaction_key > 0:
                    try:
                        sequence = int(row.get("NativeControlSequence", 0) or 0)
                        decision_id = int(row.get("DecisionId", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    correlation = str(row.get("ActionCorrelationId") or "").strip()
                    if sequence <= 0 or decision_id <= 0 or not correlation:
                        continue
                    identity = native_identity_by_tx.setdefault(
                        transaction_key,
                        {"sequence": set(), "decision": set(), "correlation": set()},
                    )
                    identity["sequence"].add(sequence)
                    identity["decision"].add(decision_id)
                    identity["correlation"].add(correlation)
                    context = native_contexts_by_tx.setdefault(transaction_key, {}).setdefault(
                        (sequence, decision_id, correlation),
                        {"power": set(), "policy": set()},
                    )
                    if kind == "power_readback" and power_transaction > 0:
                        context["power"].add(cell_id)
                    elif kind == "state_snapshot" and str(row.get("PolicyActive") or "0") in {
                        "1", "true", "True"
                    }:
                        context["policy"].add(cell_id)
                if kind == "power_readback" and power_transaction > 0:
                    state = native_cells_by_tx.setdefault(
                        power_transaction, {"power": set(), "policy": set()}
                    )
                    state["power"].add(cell_id)
                    power_transactions.add(power_transaction)
                elif kind == "state_snapshot" and transaction_key > 0:
                    state = native_cells_by_tx.setdefault(
                        transaction_key, {"power": set(), "policy": set()}
                    )
                    if str(row.get("PolicyActive") or "0") in {"1", "true", "True"}:
                        state["policy"].add(cell_id)
                for candidate, state in native_cells_by_tx.items():
                    if state["power"] >= {2, 3, 4} and state["policy"] >= {2, 3, 4}:
                        transactions.add(candidate)
    except (OSError, csv.Error):
        pass
    confirmed = 0
    confirmed_sequences: set[int] = set()
    try:
        import sqlite3
        database = run_dir / "rapp_data_lake.db"
        with closing(sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)) as conn:
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(energy_commands)")
            }
            if "native_control_sequence" in columns:
                origin_filter = ""
                if "action_origin" in columns:
                    origin_filter = " AND action_origin IN ('ta_sam', 'joint')"
                command_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(energy_commands)")}
                strict_columns = {"application_status", "observed_power_percent", "native_observation_version"}
                if strict_columns.issubset(command_columns):
                    # This smoke proves the native actuation chain, not the
                    # economic-trainability gate.  A confirmed native action
                    # may still be marked ``invalid`` for replay (for
                    # example, projected/applied alignment or SLA evidence),
                    # and that must not erase the fact that ns-3 observed the
                    # selected TA-SAM command.  Economic promotion remains
                    # strict elsewhere.
                    confirmation_sql = f"""SELECT native_control_sequence
                                           FROM energy_commands
                                          WHERE actuation_confirmed=1
                                            AND observed_power_percent IS NOT NULL
                                            AND native_observation_version=?
                                            AND native_control_sequence IS NOT NULL
                                            {origin_filter}"""
                    confirmation_rows = conn.execute(confirmation_sql, (required_version,))
                else:
                    # Old fixture/databases remain diagnosable, but modern
                    # campaigns always have the strict columns above.
                    confirmation_rows = conn.execute(
                        f"""SELECT native_control_sequence FROM energy_commands
                            WHERE actuation_confirmed=1 AND native_control_sequence IS NOT NULL
                            {origin_filter}"""
                    )
                confirmed_sequences = {
                    int(row[0]) for row in confirmation_rows if int(row[0] or 0) > 0
                }
                confirmed = sum(1 for sequence in confirmed_sequences if sequence in transactions)
            else:
                # Historical databases are readable for diagnostics only and
                # cannot approve v9 native actuation.
                confirmed = 0
    except (OSError, sqlite3.Error):
        confirmed = 0
    identity_valid = {}
    for transaction, identity in native_identity_by_tx.items():
        identity_valid[transaction] = any(
            context["power"] >= {2, 3, 4}
            and context["policy"] >= {2, 3, 4}
            for context in native_contexts_by_tx.get(transaction, {}).values()
        )
    valid_transactions = {
        transaction for transaction in transactions
        if not strict_v5 or identity_valid.get(transaction, False)
    }
    valid_sequences = {
        sequence
        for transaction, contexts in native_contexts_by_tx.items()
        if transaction in valid_transactions
        for sequence, _decision, _correlation in contexts
        if contexts[(sequence, _decision, _correlation)]["power"] >= {2, 3, 4}
        and contexts[(sequence, _decision, _correlation)]["policy"] >= {2, 3, 4}
    }
    confirmed_valid = sum(
        1 for sequence in confirmed_sequences
        if sequence in (valid_sequences if strict_v5 else valid_transactions)
    )
    return {
        "trace_path": str(trace),
        "node_manifest_path": str(node_manifest_path),
        "node_manifest": manifest,
        "node_manifest_valid": not manifest_failures,
        "node_manifest_failures": manifest_failures,
        "trace_rows": rows,
        "nonzero_transactions": sorted(transactions),
        "scheduler_transactions": sorted(scheduler_transactions),
        "power_transactions": sorted(power_transactions),
        "native_transaction_observed": bool(transactions),
        "confirmed_native_sequences": sorted(confirmed_sequences),
        "correlated_confirmation_count": confirmed_valid,
        "native_confirmation_only": bool(transactions),
        "native_cells_by_transaction": {
            str(key): {name: sorted(values) for name, values in value.items()}
            for key, value in sorted(native_cells_by_tx.items())
        },
        "native_identity_by_transaction": {
            str(key): {
                name: sorted(values) for name, values in value.items()
            }
            for key, value in sorted(native_identity_by_tx.items())
        },
        "native_coherent_contexts_by_transaction": {
            str(transaction): [
                {
                    "native_control_sequence": sequence,
                    "decision_id": decision_id,
                    "action_correlation_id": correlation,
                    "power_cells": sorted(context["power"]),
                    "policy_cells": sorted(context["policy"]),
                    "valid": bool(
                        context["power"] >= {2, 3, 4}
                        and context["policy"] >= {2, 3, 4}
                    ),
                }
                for (sequence, decision_id, correlation), context
                in sorted(contexts.items())
            ]
            for transaction, contexts in sorted(native_contexts_by_tx.items())
        },
        "native_valid_sequences": sorted(valid_sequences),
        "identity_valid_by_transaction": {
            str(key): bool(value) for key, value in sorted(identity_valid.items())
        },
        "evidence_version": required_version,
        "valid": (
            bool(valid_transactions)
            and confirmed_valid > 0
            and (not strict_v5 or not manifest_failures)
        ),
        "invalid_reason": (
            "e2_node_manifest_invalid" if manifest_failures and strict_v5
            else ("native_identity_incomplete" if transactions and not valid_transactions and strict_v5 else "")
        ),
    }


def _native_actuation_feedback_ready(
    run_dir: Path, evidence: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Require a real post-action PDCP outcome before stopping the smoke.

    Native PHY confirmation and a transport ACK are necessary but not
    sufficient: the actuation smoke must also let the rApp observe a fresh
    PDCP window and close the corresponding economic transition. Reading the
    canonical transition history here avoids stopping the parent watchdog in
    the gap between native confirmation and delayed Judge feedback.
    """
    result: dict[str, Any] = {
        "valid": False,
        "decision_ids": [],
        "reason": "economic_feedback_pending",
    }
    try:
        import sqlite3

        database = run_dir / "rapp_data_lake.db"
        if not database.is_file():
            result["reason"] = "economic_database_missing"
            return result
        with closing(sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)) as conn:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(tasam_economic_transition_history)")
            }
            required = {
                "decision_id", "economic_application_status",
                "economic_transition_eligible", "transition_json",
            }
            if not required.issubset(columns):
                result["reason"] = "economic_history_schema_incomplete"
                return result
            rows = conn.execute(
                """SELECT decision_id, transition_json
                     FROM tasam_economic_transition_history
                    WHERE economic_application_status='applied'
                      AND economic_transition_eligible=1
                    ORDER BY created_at, decision_id"""
            ).fetchall()
    except (OSError, ValueError, TypeError):
        result["reason"] = "economic_history_unreadable"
        return result

    valid_ids: list[int] = []
    native_sequences = set()
    if isinstance(evidence, dict):
        native_sequences = {
            int(value)
            for value in evidence.get("native_valid_sequences", [])
            if str(value).lstrip("-").isdigit() and int(value) > 0
        }
    for decision_id, transition_text in rows:
        try:
            transition = json.loads(transition_text or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        provenance = transition.get("decision_provenance") or {}
        action = transition.get("economic_action") or {}
        native = action.get("native_observation") or {}
        coverage = action.get("pdcp_loss_coverage") or {}
        observed_imsis = {
            str(value) for value in coverage.get("observed_imsis", [])
        }
        expected_imsis = {
            str(value) for value in coverage.get("expected_imsis", [])
        }
        confirmation = bool(
            action.get("actuation_confirmed") is True
            and action.get("application_status") == "applied"
            and str(action.get("native_evidence_version") or native.get("evidence_version") or "") == "v5"
            and bool(native.get("valid"))
            and bool(coverage.get("valid"))
            and observed_imsis == expected_imsis
            and bool(coverage.get("groups", {}).get("camera"))
            and bool(coverage.get("groups", {}).get("sensor"))
            and bool(coverage.get("groups", {}).get("vehicle"))
            and provenance.get("tasam_checkpoint_valid") is True
            and provenance.get("tasam_fallback_used") is False
        )
        if not confirmation:
            continue
        sequence = native.get("native_control_sequence")
        if native_sequences and sequence is not None:
            try:
                if int(sequence) not in native_sequences:
                    continue
            except (TypeError, ValueError):
                continue
        try:
            valid_ids.append(int(decision_id))
        except (TypeError, ValueError):
            continue
    if valid_ids:
        result.update({"valid": True, "decision_ids": valid_ids, "reason": "ok"})
    return result


def _ns3_failure_detected(run_dir: Path) -> str:
    """Detect a simulator crash even when the wall wrapper exits cleanly."""
    for name in ("ns3.log", "ns3_supervisor_launch.log", "collection_runtime.log"):
        path = run_dir / name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "NS_ASSERT failed" in text or "NS_FATAL" in text:
            return f"ns3_crash:{name}"
        for line in text.splitlines():
            marker = "[NS3_SUPERVISOR] ns3 exited code "
            if marker in line:
                code = line.split(marker, 1)[1].split(" ", 1)[0]
                if code not in {"0", "0\r"}:
                    return f"ns3_exit_code:{code}"
    return ""


def _simulation_performance_evidence(
    run_dir: Path,
    elapsed_s: float,
    *,
    min_rtf: float = 0.10,
    minimum_sim_time_s: float = 0.0,
) -> dict[str, Any]:
    """Measure simulator-clock progress without treating wall cycles as data."""
    latest = _read_json(run_dir / "xapp_metrics" / "extended_metrics.json")
    sim_range = latest.get("sim_time_range") if isinstance(latest, dict) else {}
    try:
        sim_end = float((sim_range or {}).get("end", 0.0) or 0.0)
    except (TypeError, ValueError):
        sim_end = 0.0
    elapsed = max(0.001, float(elapsed_s or 0.0))
    rtf = sim_end / elapsed
    measurement_complete = sim_end >= max(0.0, float(minimum_sim_time_s))
    valid = measurement_complete and rtf >= float(min_rtf)
    return {
        "schema": "greenran.simulation_performance_evidence.v1",
        "sim_time_observed_s": sim_end,
        "wall_time_observed_s": elapsed,
        "rtf": rtf,
        "min_rtf": float(min_rtf),
        "measurement_start_sim_s": float(minimum_sim_time_s),
        "measurement_complete": measurement_complete,
        "valid": bool(valid),
        "reason": (
            "ok" if valid else
            ("performance_measurement_incomplete" if not measurement_complete
             else "simulation_performance_infeasible")
        ),
        "source": str(run_dir / "xapp_metrics" / "extended_metrics.json"),
    }


def _validate_run_dir(run_dir: Path) -> None:
    parts = [part.lower() for part in run_dir.parts]
    if any("armd" in part and part != "train_no_armd" for part in parts):
        raise SystemExit(f"run-dir relacionado a ARMD recusado: {run_dir}")
    if any(marker.lower() in part for part in parts for marker in PROTECTED_MARKERS):
        raise SystemExit(f"artefato protegido recusado: {run_dir}")


def _validate_fixed_native_power_percent(value: int, mode: str) -> int:
    """Validate the fixed-native calibration percent for the energy baseline arm.

    The physics calibration runs (ledger v2) need exact 45/70/25 on the wire,
    so only the 5%-grid between 25 and 100 is accepted, and only in the
    fixed_100_native mode.
    """
    try:
        fixed = int(value)
    except (TypeError, ValueError):
        raise SystemExit(
            "--fixed-native-power-percent exige inteiro em passos de 5 entre 25 e 100"
        )
    if not 25 <= fixed <= 100 or fixed % 5:
        raise SystemExit(
            "--fixed-native-power-percent exige inteiro em passos de 5 entre 25 e 100"
        )
    if fixed != 100 and mode != "fixed_100_native":
        raise SystemExit(
            "--fixed-native-power-percent só é permitido no modo fixed_100_native"
        )
    return fixed


def _validate_local_path(path: Path, label: str) -> Path:
    """Reject runtime inputs/outputs outside the local project workspace."""
    resolved = path.resolve()
    try:
        resolved.relative_to(LOCAL_RUNTIME_ROOT)
    except ValueError as exc:
        raise SystemExit(
            f"{label} precisa estar no workspace local {LOCAL_RUNTIME_ROOT}: {resolved}"
        ) from exc
    return resolved


def _resolve_energy_calibration(path: Path | None) -> dict[str, Any]:
    """Load one local, versioned energy model before a run begins."""
    selected = (path or (ROOT / "config" / "energy_calibration.json")).resolve()
    _validate_local_path(selected, "calibração energética")
    try:
        return load_calibration(selected)
    except ValueError as exc:
        raise SystemExit(f"calibração energética recusada: {exc}") from exc


def _validate_checkpoint(checkpoint: Path, *, require_economic_head: bool = False) -> None:
    """Reject legacy checkpoints before they can silently fall back."""
    metadata_path = checkpoint / "tasam_marl_checkpoint_meta.json"
    if not metadata_path.is_file():
        raise SystemExit(f"metadados do checkpoint TA-SAM ausentes: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"metadados do checkpoint TA-SAM inválidos: {metadata_path}: {exc}") from exc
    v10 = metadata.get("economic_action_contract") == "economic_action_v3_per_du_sleep"
    expected = {
        "du_count": 3,
        "du_state_dim": 13,
        "global_state_dim": 13,
        "joint_action_dim": 14 if v10 else 12,
    }
    actual = {key: metadata.get(key) for key in expected}
    if actual != expected:
        raise SystemExit(
            "checkpoint TA-SAM incompatível com os 9 estágios: "
            f"esperado {expected}, encontrado {actual} em {metadata_path}"
        )
    if v10 and metadata.get("global_action_dim") != 5:
        raise SystemExit("checkpoint v10 requer global_action_dim=5")
    if not metadata.get("uses_global_energy_infra_actor"):
        raise SystemExit("checkpoint TA-SAM não possui o ator global de energia/infraestrutura")
    global_actor = checkpoint / str(metadata.get("global_actor_path", "tasam_marl_global_actor.pt"))
    if not global_actor.is_file():
        raise SystemExit(f"ator global TA-SAM ausente: {global_actor}")
    required_heads = {
        "category_head_path": "cabeça de categoria",
        "power_head_path": "cabeça de potência",
        "allocation_head_path": "cabeça de alocação",
    }
    missing = []
    for metadata_key, label in required_heads.items():
        relative = str(metadata.get(metadata_key, "") or "")
        if not relative or not (checkpoint / relative).is_file():
            missing.append(f"{label} ({metadata_key})")
    if missing:
        raise SystemExit(
            "checkpoint TA-SAM sem cabeças atuais de categoria/potência/alocação: "
            + ", ".join(missing)
        )
    outputs = list(metadata.get("allocation_head_outputs") or [])
    if require_economic_head and v10 and metadata.get("economic_action_contract") != "economic_action_v3_per_du_sleep":
        raise SystemExit("checkpoint v10 sem contrato economic_action_v3_per_du_sleep")
    if require_economic_head and (
        metadata.get("allocation_head_output_dim") != 3
        or "total_budget_fraction" not in outputs
    ):
        raise SystemExit(
            "checkpoint econômico incompatível: allocation_head_output_dim=3 e "
            "total_budget_fraction são obrigatórios"
        )
    if require_economic_head and (
        metadata.get("economic_action_contract") not in {
            "applied_action_v2", "economic_action_v3_per_du_sleep"
        }
        or metadata.get("total_budget_fraction_bounds") != [0.0, 1.0]
    ):
        raise SystemExit(
            "checkpoint econômico não usa um contrato v2/v3 com "
            "total_budget_fraction limitado a [0, 1]"
        )
    if require_economic_head and metadata.get("economic_safety_isolation") != "blocked_and_critical_v1":
        raise SystemExit(
            "checkpoint econômico sem isolamento econômico obrigatório "
            "blocked_and_critical_v1"
        )


def _validate_vehicle_profile_manifest(
    path: Path, *, expected_profile: str | None = None
) -> dict[str, Any]:
    path = path.resolve()
    try:
        path.relative_to(LOCAL_RUNTIME_ROOT)
    except ValueError as exc:
        raise SystemExit(f"manifesto veicular fora do workspace local: {path}") from exc
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"manifesto veicular inválido: {path}: {exc}") from exc
    erratum_path = path.parent / "assessment_erratum_v1.json"
    if erratum_path.is_file():
        try:
            erratum = json.loads(erratum_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"errata veicular inválida: {erratum_path}") from exc
        if erratum.get("correction", {}).get("promotion_eligible") is False:
            raise SystemExit("campanha veicular possui errata não promocionável")
    allowed = {4000, 6000, 8000, 12000, 16000}
    try:
        interval = int(payload.get("selected_interval_us") or 0)
    except (TypeError, ValueError):
        interval = 0
    if (
        payload.get("schema") not in {
            "greenran.autonomous_vehicle_feasibility.v1",
            "greenran.autonomous_vehicle_feasibility.v2",
            "greenran.autonomous_vehicle_feasibility.v3",
            "greenran.autonomous_vehicle_feasibility.v4",
        }
        or payload.get("status") != "passed"
        or interval not in allowed
    ):
        raise SystemExit("manifesto veicular precisa ser uma seleção aprovada da v12")
    if payload.get("schema") == "greenran.autonomous_vehicle_feasibility.v2":
        provenance = payload.get("provenance") or {}
        contract = provenance.get("metric_contract") or {}
        if (
            contract.get("pdcp_source") != "native_pdcp_trace_unique_sim_epochs"
            or contract.get("collector_mode") != "pdcp_real"
            or contract.get("proxy_allowed") is not False
        ):
            raise SystemExit("manifesto veicular v2 não comprova PDCP real sem proxy")
    if payload.get("schema") == "greenran.autonomous_vehicle_feasibility.v3":
        contract = payload.get("metric_contract")
        if (
            contract != "per_pdu_cohort_v1"
            or payload.get("connectivity_mode") not in {"mmwave_only", "lte_anchored_mc"}
            or payload.get("scheduler_policy") != "gbr_debt_rr_v1"
            or int(payload.get("loss_grace_ms", 0)) != 1000
        ):
            raise SystemExit("manifesto veicular v3 não comprova o contrato PDCP por PDU")
    if payload.get("schema") == "greenran.autonomous_vehicle_feasibility.v4":
        provenance = payload.get("provenance") or {}
        native_contract = provenance.get("metric_contract") or {}
        matrix = payload.get("multi_seed_validation") or {}
        if (
            payload.get("scientific_decision") != "approved"
            or payload.get("promotion_eligible") is not True
            or payload.get("metric_contract") != "per_pdu_cohort_v1"
            or payload.get("scheduler_policy") != "gbr_debt_rr_v1"
            or int(payload.get("loss_grace_ms", 0)) != 1000
            or native_contract.get("pdcp_source") != "native_pdcp_pdu_tx_rx"
            or native_contract.get("collector_mode") != "pdcp_real"
            or native_contract.get("proxy_allowed") is not False
            or matrix.get("valid") is not True
            or tuple(matrix.get("required_seeds") or []) != (45, 46, 47)
            or tuple(matrix.get("complete_seeds") or []) != (45, 46, 47)
            or matrix.get("seed47_reused_from_phase1") is not True
            or matrix.get("provenance_compatible") is not True
        ):
            raise SystemExit("manifesto veicular v4 não comprova baseline multi-seed PDCP real")
    if expected_profile is not None and payload.get("profile") != expected_profile:
        raise SystemExit(
            "manifesto veicular pertence a outro perfil: "
            f"esperado={expected_profile} obtido={payload.get('profile')}"
        )
    return payload


def _validate_control_gate(path: Path, checkpoint: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"gate de controle ausente ou inválido: {path}: {exc}") from exc
    gate = payload.get("gate") if isinstance(payload, dict) else None
    if not isinstance(gate, dict) or gate.get("allow_control_trial") is not True:
        raise SystemExit(f"gate não autoriza assistant_only_control: {path}")
    if gate.get("status") != "trial_approved" or gate.get("manual_approval_valid") is not True:
        raise SystemExit(f"gate não possui aprovação manual válida: {path}")
    approved_run_dir = str(gate.get("run_dir", "") or "")
    if approved_run_dir and Path(approved_run_dir).resolve() != checkpoint.resolve():
        raise SystemExit(
            f"gate aponta para checkpoint diferente: gate={approved_run_dir} checkpoint={checkpoint.resolve()}"
        )
    approval_path = Path(str(payload.get("manual_approval_path", "") or ""))
    if not approval_path.is_file():
        raise SystemExit(f"manifesto de aprovação manual ausente: {approval_path}")
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        expires_at = str(approval.get("expires_at", "") or "")
        if approval.get("approved") is not True or not expires_at:
            raise ValueError("approval incompleto")
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < datetime.now(timezone.utc):
            raise ValueError("approval expirado")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"aprovação manual inválida ou expirada: {approval_path}: {exc}") from exc


def _write_actuation_smoke_gate(path: Path, checkpoint: Path) -> None:
    """Authorize only the bounded, non-promotional actuation smoke.

    The scientific control gate remains mandatory for ``combined`` and is
    never synthesized here. The actuation smoke is a separate operational
    check whose sole purpose is to prove that one selected TA-SAM proposal
    reaches the native E2/PHY evidence path at the declared 10% canary.
    """
    _write_json(path, {
        "schema": "greenran.tasam.actuation_smoke_gate.v1",
        "gate": {
            "allow_control_trial": True,
            "status": "trial_approved",
            "manual_approval_valid": True,
            "run_dir": str(checkpoint.resolve()),
            "non_promotional": True,
            "max_fraction": 0.10,
            "promotion_allowed": False,
        },
        "checkpoint": str(checkpoint.resolve()),
        "purpose": "native_actuation_smoke_only",
    })


def _validate_runtime_contract(env: dict[str, str], mode: str, checkpoint: Path) -> None:
    """Fail closed on a simulator/configuration outside the causal pilot."""
    if env.get("GREENRAN_LOCAL_ONLY", "0") == "1":
        external_values = [
            (key, value) for key, value in env.items()
            if "/run/media/" in str(value)
        ]
        if external_values:
            details = "; ".join(f"{key}={value}" for key, value in external_values)
            raise SystemExit(f"modo local-only recusou caminho do HD externo: {details}")
    binary = Path(env.get("GREENRAN_NS3_BIN", ""))
    if binary.name not in EXPECTED_NS3_BINARIES or not binary.is_file() or not os.access(binary, os.X_OK):
        raise SystemExit(
            f"binário ns-3 inválido ou ausente: esperado {EXPECTED_NS3_BINARY} executável, encontrado {binary}"
        )
    expected = {
        "GREENRAN_NS3_UE_COUNT": "20",
        "GREENRAN_NS3_MMWAVE_ENB_NODES": "3",
        "GREENRAN_NS3_E2NR_ENABLED": "false",
        "GREENRAN_NS3_E2DU_ENABLED": "true",
        "GREENRAN_REAL_ONLY": "1",
        "GREENRAN_REQUIRE_REAL_PDCP": "1",
        "GREENRAN_TASAM_EXPORT_ALLOW_PROXY": "0",
    }
    mismatches = [
        f"{key}={env.get(key)!r} (esperado {value!r})"
        for key, value in expected.items()
        if env.get(key) != value
    ]
    if mismatches:
        raise SystemExit("contrato do piloto inválido: " + "; ".join(mismatches))
    if env.get("GREENRAN_NS3_NATIVE_AGGREGATED_EVIDENCE") == "1":
        required_version = "v6" if (
            mode in V2X_ENERGY_MODES
            or env.get("GREENRAN_NATIVE_EVIDENCE_VERSION") == "v6"
        ) else "v5"
        if env.get("GREENRAN_NATIVE_EVIDENCE_VERSION") != required_version:
            raise SystemExit(
                "evidência nativa incompatível: "
                f"o modo {mode} exige GREENRAN_NATIVE_EVIDENCE_VERSION={required_version}"
            )
        if env.get("GREENRAN_NATIVE_TRACE_PROFILE") != "v9_fidelity":
            raise SystemExit(
                "perfil de trace nativo incompatível: esperado v9_fidelity"
            )
    if mode in {
        "combined", "combined_online", "combined_actuation_smoke",
        "sac_l2_online", "sac_l2_frozen", "tasam_v2x_online", "tasam_v2x_frozen",
        "asgard_v2x_window90_online", *V2X_ENERGY_MODES,
    }:
        if mode == "combined_actuation_smoke" and env.get("GREENRAN_NS3_E2_CONTROL_ENABLED") != "1":
            raise SystemExit("smoke de atuação exige GREENRAN_NS3_E2_CONTROL_ENABLED=1")
        if mode in {"combined_online", "sac_l2_online", "tasam_v2x_online", *V2X_WINDOW90_ONLINE_MODES} and (
            env.get("GREENRAN_ML_ENABLED") != "1"
            or env.get("GREENRAN_ML_RETRAIN_ENABLED", "").lower() != "true"
        ):
            raise SystemExit(
                "contrato do treino online inválido: "
                "GREENRAN_ML_ENABLED=1 e GREENRAN_ML_RETRAIN_ENABLED=true são obrigatórios"
            )
        gate_path = Path(env.get("GREENRAN_MARL_CONTROL_GATE_MANIFEST", ""))
        if mode == "combined":
            _validate_control_gate(gate_path, checkpoint)
        if mode == "combined_online":
            benchmark_path = Path(env.get("GREENRAN_TASAM_BENCHMARK_MANIFEST", ""))
            try:
                benchmark = _read_json(benchmark_path)
            except Exception:
                benchmark = {}
            performance = benchmark.get("simulation_performance", benchmark)
            benchmark_binary_hash = str(benchmark.get("ns3_binary_sha256") or "")
            runtime_binary_hash = file_sha256(binary) if binary.is_file() else ""
            benchmark_valid = bool(
                (
                    benchmark.get("schema") == "greenran.simulation_performance_benchmark.v1"
                    and benchmark.get("valid") is True
                )
                or (
                    benchmark.get("schema") == "greenran.tasam_online_arm.v1"
                    and benchmark.get("status") == "finished"
                    and benchmark.get("native_trace_profile") == "v9_fidelity"
                    and benchmark.get("native_aggregated_evidence") is True
                )
            )
            if (
                not benchmark_valid
                or not isinstance(performance, dict)
                or performance.get("valid") is not True
                or float(performance.get("rtf", 0.0) or 0.0) < 0.016
                or (
                    benchmark_binary_hash
                    and runtime_binary_hash
                    and benchmark_binary_hash != runtime_binary_hash
                )
            ):
                raise SystemExit(
                    "benchmark de desempenho ns-3 ausente, incompatível ou abaixo de RTF 0,016; "
                    "adaptação econômica bloqueada"
                )


def checkpoint_fingerprint(checkpoint: Path) -> str:
    """Hash every checkpoint file, including names and contents, deterministically."""
    digest = hashlib.sha256()
    files = sorted(
        path for path in checkpoint.rglob("*")
        if path.is_file() and path.name != "local_import_manifest.json"
    )
    if not files:
        raise SystemExit(f"checkpoint TA-SAM vazio: {checkpoint}")
    for path in files:
        relative = path.relative_to(checkpoint).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_curriculum(checkpoint: Path) -> dict[str, Any]:
    """Expose synthetic-head provenance without changing runtime behaviour."""
    metadata = _read_json(checkpoint / "tasam_marl_checkpoint_meta.json")
    curriculum = metadata.get("category_head_pretraining")
    return curriculum if isinstance(curriculum, dict) else {}


def _common_env(
    run_dir: Path,
    seed: int,
    profile: str,
    wall_time: float,
    sim_time: float = 600.0,
    *,
    decision_target: int = 0,
    pairing_schedule_id: str = "",
    pairing_schedule_file: Path | None = None,
    control_gate: Path | None = None,
    control_fraction: float | None = None,
    max_rollout_fraction: float | None = None,
    artifact_budget_gib: float = 4.0,
    artifact_min_free_gib: float = 10.0,
    actuation_enabled: bool = False,
    native_fidelity: bool = False,
    disable_app_overrides: bool = False,
    infra_resource_profile: str | None = None,
    execution_slot_env: dict[str, str] | None = None,
) -> dict[str, str]:
    sim_time_value = float(sim_time)
    sim_time_text = (
        str(int(sim_time_value))
        if sim_time_value.is_integer()
        else str(sim_time_value)
    )
    env = dict(os.environ)
    if execution_slot_env:
        env.update(execution_slot_env)
    env.update(
        {
            "GREENRAN_STATE_DIR": str(run_dir),
            # Keep sockets outside the long campaign path.  The files and
            # audit evidence remain in ``run_dir``; only the transient IPC
            # endpoints use this short path.
            "GREENRAN_SOCKET_DIR": str(_short_socket_dir(run_dir)),
            "GREENRAN_V2X_EXECUTION_SLOT": env.get("GREENRAN_V2X_EXECUTION_SLOT", "serial"),
            "GREENRAN_E2_TERM_PORT": env.get("GREENRAN_E2_TERM_PORT", "36421"),
            "GREENRAN_E2_XAPP_PORT": env.get("GREENRAN_E2_XAPP_PORT", "36422"),
            "GREENRAN_E2_LOCAL_PORT": env.get("GREENRAN_E2_LOCAL_PORT", "38470"),
            "GREENRAN_PORT_OFFSET": env.get("GREENRAN_PORT_OFFSET", "0"),
            "GREENRAN_FIXED_SCENARIO_CONFIG": str(ROOT / "config/greenran_fixed_scenario.json"),
            "GREENRAN_SIM_TIME": sim_time_text,
            "GREENRAN_WALL_TIME_LIMIT_SECONDS": str(int(wall_time)),
            "GREENRAN_WALL_KEEP_RIC": "0",
            "GREENRAN_CLEAN_SCOPE": "instance",
            "GREENRAN_COLLECTION_EVENT_PROFILE": profile,
            "GREENRAN_RAN_PRESSURE_PROFILE": profile,
            # Economic decisions must be keyed to a new simulation window,
            # never to controller wall-clock cycles.  Legacy collection modes
            # may still override this explicitly, but current ASGARD modes
            # use the simulator clock by default.
            # v9 decisions, native observations and PDCP feedback must share
            # the simulator clock.  Using wall-clock cycles here can repeat
            # the same PDCP window while the simulator is still advancing,
            # which would create false economic transitions.  Legacy callers
            # can opt out explicitly by using a pre-v3 evidence contract.
            "GREENRAN_COLLECTION_EVENT_TIME_SOURCE": (
                "sim"
                if native_fidelity or os.environ.get("GREENRAN_NATIVE_EVIDENCE_VERSION", "v3") == "v3"
                else os.environ.get("GREENRAN_COLLECTION_EVENT_TIME_SOURCE", "wall")
            ),
            "GREENRAN_COLLECTION_EVENT_TICK_S": os.environ.get(
                "GREENRAN_COLLECTION_EVENT_TICK_S", "0.25"
            ),
            "GREENRAN_COLLECTION_EVENT_CYCLES": "0",
            "GREENRAN_COLLECTION_DISABLE_APP_OVERRIDES": "1" if disable_app_overrides else "0",
            "GREENRAN_REAL_ONLY": "1",
            "GREENRAN_REQUIRE_REAL_PDCP": "1",
            "GREENRAN_TASAM_EXPORT_ALLOW_PROXY": "0",
            "GREENRAN_START_RIC": os.environ.get("GREENRAN_START_RIC", "1"),
            "GREENRAN_ORCHESTRATOR_INTERVAL": "2",
            "GREENRAN_PDCP_STALE_SECONDS": "600",
            "GREENRAN_NS3_UE_COUNT": "20",
            "GREENRAN_NS3_CAMERA_UE_COUNT": "3",
            "GREENRAN_NS3_VEHICLE_UE_COUNT": "5",
            "GREENRAN_NS3_MMWAVE_ENB_NODES": "3",
            # The 20-UE smoke path is intentionally pinned to the stable
            # E2-DU report.  E2-NR is opt-in because the current ns-3 build
            # can terminate before PDCP collection starts when that path is
            # enabled.
            "GREENRAN_NS3_E2NR_ENABLED": os.environ.get("GREENRAN_NS3_E2NR_ENABLED", "false"),
            "GREENRAN_NS3_E2DU_ENABLED": os.environ.get("GREENRAN_NS3_E2DU_ENABLED", "true"),
            "GREENRAN_NS3_E2CUUP_ENABLED": "false",
            "GREENRAN_NS3_E2CUCP_ENABLED": "false",
            "GREENRAN_NS3_E2_CONTROL_ENABLED": (
                "1" if actuation_enabled else
                os.environ.get("GREENRAN_NS3_E2_CONTROL_ENABLED", "0")
            ),
            "GREENRAN_NATIVE_EVIDENCE_VERSION": "v5" if native_fidelity else "v3",
            "GREENRAN_NS3_NATIVE_AGGREGATED_EVIDENCE": "1" if native_fidelity else "0",
            "GREENRAN_NS3_NATIVE_EVIDENCE_PERIOD_MS": "500" if native_fidelity else "100",
            "GREENRAN_NATIVE_TRACE_PROFILE": "v9_fidelity" if native_fidelity else "legacy_minimal",
            "GREENRAN_CAMPAIGN_ID": run_dir.name,
            "GREENRAN_NATIVE_SOURCE_GENERATION": f"{run_dir.name}:native-v5" if native_fidelity else f"{run_dir.name}:native-v3",
            "GREENRAN_TASAM_MIN_SIM_ADVANCE": "0.5",
            "GREENRAN_TASAM_BENCHMARK_MANIFEST": os.environ.get(
                "GREENRAN_TASAM_BENCHMARK_MANIFEST",
                str(DEFAULT_BENCHMARK_MANIFEST) if DEFAULT_BENCHMARK_MANIFEST.is_file() else "",
            ),
            # File-based E2 report export is not the native control/evidence
            # channel and is prohibitively expensive for the 600 s smoke.
            # v9 keeps PDCP and native control observations, but disables the
            # unrelated E2 file logger for every modern arm.
            "GREENRAN_NS3_ENABLE_E2_FILE_LOGGING": (
                "false"
                if native_fidelity or os.environ.get("GREENRAN_NATIVE_EVIDENCE_VERSION", "v3") == "v3"
                else os.environ.get("GREENRAN_NS3_ENABLE_E2_FILE_LOGGING", "true")
            ),
            "GREENRAN_TASAM_EXPECTED_E2_NODES": "3",
            "GREENRAN_TASAM_ACTUATOR_STATUS_PATH": str(
                run_dir / "tasam_actuator_status.json"
            ),
            "GREENRAN_E2_NODE_MANIFEST": str(
                run_dir / "ns3_energy" / "E2NodeManifest.json"
            ),
            # Preserve an explicit topology choice made by a protected
            # capability check or vehicle manifest.  The historical default
            # remains MC, but v12's dedicated V2X bearer path must be able to
            # request the supported non-MC UE devices without being silently
            # overwritten here.
            "GREENRAN_NS3_USE_MC_UE_DEVICES": os.environ.get("GREENRAN_NS3_USE_MC_UE_DEVICES", "true"),
            "GREENRAN_NS3_BIN": os.environ.get(
                "GREENRAN_NS3_BIN",
                str(DEFAULT_NS3_BINARY),
            ),
            "GREENRAN_NS3_ENABLE_ENERGY_CSV": "1" if native_fidelity else os.environ.get(
                "GREENRAN_NS3_ENABLE_ENERGY_CSV", "0"
            ),
            "GREENRAN_NS3_ENERGY_OUTPUT_DIR": str(run_dir / "ns3_energy"),
            # Sidecar consumed by the ns-3 thread to label native v5 rows
            # with the decision/correlation that preceded the E2 packet.
            # It is metadata only; PHY readback + active policy remain the
            # confirmation boundary.
            "GREENRAN_NS3_NATIVE_CONTROL_CONTEXT_PATH": str(
                run_dir / "ns3_energy" / "NativeControlContext.csv"
            ),
            # Real-PDCP collection requires the scenario's bearer traces.  The
            # causal pilot keeps the expensive article export disabled and
            # limits DB snapshots, but must not disable the source evidence
            # used to validate SLA and throughput.
            "GREENRAN_NS3_ENABLE_TRACES": os.environ.get("GREENRAN_NS3_ENABLE_TRACES", "1"),
            "GREENRAN_NS3_NATIVE_MINIMAL_TRACES": "0" if native_fidelity else "1",
            # Fidelity arms represent one finite ns-3 experiment.  The
            # legacy supervisor restarts a cleanly finished simulation for
            # long-lived collection services; doing that here would erase
            # the completed 600 s trace and prevent the performance gate
            # from observing a terminal run.
            "GREENRAN_NS3_SINGLE_RUN": "1" if native_fidelity else os.environ.get(
                "GREENRAN_NS3_SINGLE_RUN", "0"
            ),
            "GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH": os.environ.get("GREENRAN_NS3_ENABLE_TRACES_AFTER_ATTACH", "0"),
            "GREENRAN_EXPERIMENT_PROFILE": profile,
            "GREENRAN_EXPERIMENT_SEED": str(seed),
            "GREENRAN_NS3_RNG_RUN": str(seed),
            # A smoke comparison must be a frozen-checkpoint observation;
            # online retraining belongs to a later controlled experiment.
            "GREENRAN_TASAM_TRUE_ONLINE_ENABLED": "0",
            "GREENRAN_TASAM_TRUE_ONLINE_EXTERNAL_CONTROLLER": "1",
            # The economic online controller is the only campaign component
            # allowed to retrain.  Frozen/shadow/rApp arms stay disabled and
            # combined_online overrides both flags below explicitly.
            "GREENRAN_ML_ENABLED": "0",
            "GREENRAN_ML_RETRAIN_ENABLED": "false",
            # Economic comparisons require real kernel cgroup-v2 limits. A
            # missing delegation is rejected before the run directory exists.
            "GREENRAN_CGROUP_ENFORCE": "1",
            "GREENRAN_CGROUP_ALLOW_UNENFORCED": "0",
            "GREENRAN_LOCAL_ONLY": "1",
            # The controlled controller below is the sole online learner.
            "GREENRAN_ONLINE_UPDATE_OWNER": "run_tasam_online_controlled.py",
            # The controlled learner always starts in shadow.  The
            # controller alone advances 0 -> 10 -> 25 -> 50 after validated
            # windows; a stale environment must not silently enable control.
            "GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION": "0.0",
            "GREENRAN_TASAM_FORCE_FULL_ROLLOUT": "0",
            "GREENRAN_TASAM_REQUIRE_CHECKPOINT": "1",
            "GREENRAN_TASAM_EXPLICIT_STATE_FEATURE": "1",
            "GREENRAN_MARL_SHADOW_ENABLE": "1",
            "GREENRAN_TASAM_EXPORT_ENABLED": "0",
            "GREENRAN_DB_SNAPSHOT_ENABLED": "0",
            "GREENRAN_DB_SNAPSHOT_RETENTION": "2",
            "GREENRAN_DB_SNAPSHOT_INTERVAL": "1800",
            "GREENRAN_ARTIFACT_BUDGET_GIB": str(max(0.0, float(artifact_budget_gib))),
            "GREENRAN_ARTIFACT_MIN_FREE_GIB": str(max(0.0, float(artifact_min_free_gib))),
            "NS_GLOBAL_VALUE": f"RngRun={seed}",
        }
    )
    if profile == BASELINE_MAX_PROFILE:
        # The protected baseline is the only arm allowed to request the
        # enlarged simulator envelope. MC/LTE are explicit so stale shell
        # variables cannot silently disable recovery.
        selected_resource_profile = infra_resource_profile or "baseline_max_v1"
        scope_budget = build_physical_budget(
            1.0, unrestricted=True, resource_profile=selected_resource_profile
        )
        scope_prefix = f"greenran-{run_dir.name}".replace("_", "-")[:80]
        env.update({
            "GREENRAN_INFRA_RESOURCE_PROFILE": selected_resource_profile,
            "GREENRAN_CGROUP_BACKEND": SYSTEMD_USER_SCOPE_BACKEND,
            "GREENRAN_CGROUP_SCOPE_PREFIX": scope_prefix,
            "GREENRAN_NS3_USE_MC_UE_DEVICES": "true",
            "GREENRAN_NS3_E2LTE_ENABLED": "true",
            # E2-LTE carries fallback observability.  Keep the unstable
            # E2-NR path disabled for the native rApp contract.
            "GREENRAN_NS3_E2NR_ENABLED": "false",
            "GREENRAN_NS3_E2DU_ENABLED": "true",
            "GREENRAN_V2X_FALLBACK_POLICY": "mmwave_primary_lte_risk_fallback_v2",
            "GREENRAN_V2X_LINK_METRIC_CONTRACT": "vehicle_link_state_v2",
        })
        for group, limits in scope_budget["groups"].items():
            key = group.upper()
            env[f"GREENRAN_CGROUP_SCOPE_{key}_CPU_QUOTA_US"] = str(limits["cpu_quota_us"])
            env[f"GREENRAN_CGROUP_SCOPE_{key}_MEMORY_HIGH_BYTES"] = str(limits["memory_high_bytes"])
            env[f"GREENRAN_CGROUP_SCOPE_{key}_IO_WEIGHT"] = str(limits["io_weight"])
    env["GREENRAN_TASAM_ONLINE_ROLLOUT_MANIFEST"] = str(run_dir / "online_rollout.json")
    env["GREENRAN_TASAM_EVAL_MANIFEST"] = str(run_dir / "online_eval_manifest.json")
    env["GREENRAN_MARL_CONTROL_GATE_MANIFEST"] = str(control_gate or (run_dir / "online_control_gate.json"))
    if int(decision_target or 0) > 0:
        env["GREENRAN_DECISION_TARGET"] = str(int(decision_target))
    else:
        env.pop("GREENRAN_DECISION_TARGET", None)
    if pairing_schedule_id:
        env["GREENRAN_PAIRING_SCHEDULE_ID"] = str(pairing_schedule_id)
    else:
        env.pop("GREENRAN_PAIRING_SCHEDULE_ID", None)
    if pairing_schedule_file:
        env["GREENRAN_PAIRING_SCHEDULE_FILE"] = str(pairing_schedule_file)
    else:
        env.pop("GREENRAN_PAIRING_SCHEDULE_FILE", None)
    if control_fraction is not None:
        env["GREENRAN_CONTROL_TRIAL_FRACTION"] = str(float(control_fraction))
    for key in ("GREENRAN_ASSISTANT_DECISION_MODE", "GREENRAN_TASAM_CHECKPOINT", "GREENRAN_TASAM_HISTORICAL_TRACE"):
        env.pop(key, None)
    return env


def mode_contract(mode: str) -> dict[str, Any]:
    if mode == "sac_l2_online":
        return {
            "armd_mode": "off",
            "tasam_enabled": True,
            "tasam_mode": "tasam_full_control",
            "controller_enabled": True,
            "actuation_enabled": True,
            "frozen_checkpoint": False,
            "historical_replay": True,
            "article_method": "sac_l2",
            "sam_mode": "l2",
            "l2_weight": 0.0001,
            "replay_contract": "greenran.tasam.v2x.replay_80_20.v1",
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "description": "baseline SAC-L2 com adaptação online V2X, sem SAM e sem ARMD",
        }
    if mode == "sac_l2_frozen":
        return {
            "armd_mode": "off",
            "tasam_enabled": True,
            "tasam_mode": "tasam_full_control",
            "controller_enabled": False,
            "actuation_enabled": True,
            "frozen_checkpoint": True,
            "historical_replay": False,
            "article_method": "sac_l2",
            "sam_mode": "l2",
            "l2_weight": 0.0001,
            "replay_contract": "greenran.tasam.v2x.replay_80_20.v1",
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "description": "avaliação V2X congelada do baseline SAC-L2",
        }
    if mode == "tasam_v2x_online":
        return {
            "armd_mode": "assist",
            "tasam_enabled": True,
            "tasam_mode": "assistant_only_control",
            "assistant_decision_mode": "cooperative_hierarchy",
            "controller_enabled": True,
            "actuation_enabled": True,
            "frozen_checkpoint": False,
            "historical_replay": True,
            "article_method": "ta_sam_selective",
            "sam_mode": "tasam_selective",
            "l2_weight": 0.0,
            "replay_contract": "greenran.tasam.v2x.replay_80_20.v1",
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "description": "TA-SAM seletivo online V2X com ARMD, Judge e safety shield",
        }
    if mode == "asgard_v2x_window90_online":
        return {
            "armd_mode": "assist",
            "tasam_enabled": True,
            "tasam_mode": "assistant_only_control",
            "assistant_decision_mode": "cooperative_hierarchy",
            "controller_enabled": True,
            "actuation_enabled": True,
            "frozen_checkpoint": False,
            "historical_replay": True,
            "article_method": "ta_sam_selective",
            "sam_mode": "tasam_selective",
            "l2_weight": 0.0,
            "replay_contract": "greenran.tasam.v2x.window90.replay_80_20.v1",
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "pilot_rollout_100": True,
            "description": "piloto TA-SAM online V2X 90 observações com rollout integral e safety shield",
        }
    if mode == "asgard_v2x_window90_energy_online":
        return {
            "armd_mode": "assist",
            "tasam_enabled": True,
            "tasam_mode": "assistant_only_control",
            "assistant_decision_mode": "cooperative_hierarchy",
            "controller_enabled": True,
            "actuation_enabled": True,
            "frozen_checkpoint": False,
            "historical_replay": True,
            "article_method": "ta_sam_selective",
            "sam_mode": "tasam_selective",
            "l2_weight": 0.0,
            "replay_contract": "greenran.tasam.v2x.window90.replay_80_20.v1",
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "economic_action_contract": "economic_action_v3_per_du_sleep",
            "pilot_rollout_100": True,
            "energy_mode": True,
            "description": "TA-SAM online V2X com atuação econômica V3 e energia nativa",
        }
    if mode == "asgard_v2x_window90_energy_dynamic":
        return {
            "armd_mode": "assist",
            "tasam_enabled": True,
            "tasam_mode": "assistant_only_control",
            "assistant_decision_mode": "cooperative_hierarchy",
            "controller_enabled": True,
            "actuation_enabled": True,
            "frozen_checkpoint": False,
            "historical_replay": True,
            "article_method": "ta_sam_selective",
            "sam_mode": "tasam_selective",
            "l2_weight": 0.0,
            "replay_contract": "greenran.tasam.v2x.window90.replay_80_20.v1",
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "economic_action_contract": "economic_action_v3_per_du_sleep",
            "dynamic_floor_contract": DYNAMIC_FLOOR_CONTRACT,
            "pilot_rollout_100": True,
            "energy_mode": True,
            "description": (
                "ASGARD online V2X com proposta por DU limitada pelo piso "
                "dinâmico causal e safety isolation soberano"
            ),
        }
    if mode == "asgard_v2x_window90_energy_frozen":
        return {
            "armd_mode": "assist",
            "tasam_enabled": True,
            "tasam_mode": "assistant_only_control",
            "assistant_decision_mode": "cooperative_hierarchy",
            "controller_enabled": False,
            "actuation_enabled": True,
            "frozen_checkpoint": True,
            "historical_replay": False,
            "article_method": "ta_sam_selective",
            "sam_mode": "tasam_selective",
            "l2_weight": 0.0,
            "replay_contract": "greenran.tasam.v2x.window90.replay_80_20.v1",
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "economic_action_contract": "economic_action_v3_per_du_sleep",
            "pilot_rollout_100": True,
            "energy_mode": True,
            "description": "avaliação congelada TA-SAM V2X com energia nativa",
        }
    if mode == "tasam_v2x_frozen":
        return {
            "armd_mode": "assist",
            "tasam_enabled": True,
            "tasam_mode": "assistant_only_control",
            "assistant_decision_mode": "cooperative_hierarchy",
            "controller_enabled": False,
            "actuation_enabled": True,
            "frozen_checkpoint": True,
            "historical_replay": False,
            "article_method": "ta_sam_selective",
            "sam_mode": "tasam_selective",
            "l2_weight": 0.0,
            "replay_contract": "greenran.tasam.v2x.replay_80_20.v1",
            "reward_contract": "greenran.tasam.v2x.reward_adaptive.v1",
            "description": "avaliação V2X congelada TA-SAM com ARMD, Judge e safety shield",
        }
    if mode == "train_no_armd":
        return {
            "armd_mode": "off",
            "tasam_enabled": True,
            "tasam_mode": "tasam_full_control",
            "controller_enabled": True,
            "actuation_enabled": True,
            "frozen_checkpoint": False,
            "historical_replay": False,
            "description": "treino online integral TA-SAM sem ARMD",
        }
    if mode == "rapp_only":
        return {
            "armd_mode": "off",
            "tasam_enabled": False,
            "tasam_mode": "shadow",
            "controller_enabled": False,
            "actuation_enabled": False,
            "frozen_checkpoint": False,
            "historical_replay": False,
            "description": "rApp nativa sem ARMD e sem TA-SAM",
        }
    if mode == "rapp_only_actuating":
        # Baseline comparável do artigo: a mesma ladder de regras da rApp
        # nativa, porém ATUANDO via E2 (mesmo caminho socket do ASGARD),
        # sem ARMD e sem TA-SAM.  O rapp_only clássico permanece apenas
        # observador por contrato histórico.
        return {
            "armd_mode": "off",
            "tasam_enabled": False,
            "tasam_mode": "shadow",
            "controller_enabled": False,
            "actuation_enabled": True,
            "frozen_checkpoint": False,
            "historical_replay": False,
            "description": "rApp nativa atuante (baseline com E2), sem ARMD e sem TA-SAM",
        }
    if mode == "fixed_100_native":
        return {
            "armd_mode": "off",
            "tasam_enabled": False,
            "tasam_mode": "shadow",
            "controller_enabled": False,
            "actuation_enabled": True,
            "frozen_checkpoint": False,
            "historical_replay": False,
            "economic_action_contract": "economic_action_v3_per_du_sleep",
            "energy_mode": True,
            "description": "baseline energético fixo: três DUs em 100% via E2",
        }
    if mode == "native_sleep_calibration":
        return {
            "armd_mode": "off",
            "tasam_enabled": False,
            "tasam_mode": "shadow",
            "controller_enabled": False,
            "actuation_enabled": True,
            "frozen_checkpoint": False,
            "historical_replay": False,
            "economic_action_contract": "economic_action_v3_per_du_sleep",
            "energy_mode": True,
            "description": (
                "calibração nativa de sleep: um drain/commit por DU fora do "
                "ciclo do rApp, evidência para o ledger v2"
            ),
        }
    if mode == "combined":
        return {
            "armd_mode": "assist",
            "tasam_enabled": True,
            "tasam_mode": "assistant_only_control",
            "assistant_decision_mode": "cooperative_hierarchy",
            "controller_enabled": False,
            "actuation_enabled": True,
            "frozen_checkpoint": True,
            "historical_replay": False,
            "description": "rApp + ARMD + TA-SAM com checkpoint congelado",
        }
    if mode == "combined_online":
        return {
            "armd_mode": "assist",
            "tasam_enabled": True,
            "tasam_mode": "assistant_only_control",
            "assistant_decision_mode": "cooperative_hierarchy",
            "controller_enabled": True,
            "actuation_enabled": True,
            "frozen_checkpoint": False,
            "historical_replay": False,
            "description": "rApp + ARMD + TA-SAM com adaptação online e cabeça econômica",
        }
    if mode == "combined_actuation_smoke":
        return {
            "armd_mode": "assist",
            "tasam_enabled": True,
            "tasam_mode": "assistant_only_control",
            "assistant_decision_mode": "cooperative_hierarchy",
            "controller_enabled": False,
            "actuation_enabled": True,
            "frozen_checkpoint": True,
            "historical_replay": False,
            "description": "smoke de atuação E2 com ARMD assist e checkpoint congelado",
        }
    if mode == "combined_shadow":
        return {
            "armd_mode": "assist",
            "tasam_enabled": True,
            "tasam_mode": "shadow",
            "assistant_decision_mode": "cooperative_hierarchy",
            "controller_enabled": False,
            "actuation_enabled": False,
            "frozen_checkpoint": True,
            "historical_replay": False,
            "description": "rApp + ARMD + TA-SAM em shadow, sem aplicar controle",
        }
    raise ValueError(f"modo desconhecido: {mode}")


def build_environment(
    mode: str,
    run_dir: Path,
    seed: int,
    profile: str,
    wall_time: float,
    sim_time: float = 600.0,
    *,
    decision_target: int = 0,
    pairing_schedule_id: str = "",
    pairing_schedule_file: Path | None = None,
    control_gate: Path | None = None,
    control_fraction: float | None = None,
    max_rollout_fraction: float | None = None,
    vehicle_profile_manifest: Path | None = None,
    artifact_budget_gib: float = 4.0,
    artifact_min_free_gib: float = 10.0,
    native_fidelity: bool = False,
    energy_enabled: bool = False,
    energy_staircase: bool = False,
    safe_power_floor_ledger: Path | None = None,
    dynamic_floor_ledger: Path | None = None,
    baseline_signature: Path | None = None,
    fixed_native_power_percent: int = 100,
    disable_app_overrides: bool = False,
    infra_resource_profile: str | None = None,
    execution_slot_env: dict[str, str] | None = None,
) -> dict[str, str]:
    contract = mode_contract(mode)
    env = _common_env(
        run_dir,
        seed,
        profile,
        wall_time,
        sim_time,
        decision_target=decision_target,
        pairing_schedule_id=pairing_schedule_id,
        pairing_schedule_file=pairing_schedule_file,
        control_gate=control_gate,
        control_fraction=control_fraction,
        max_rollout_fraction=max_rollout_fraction,
        artifact_budget_gib=artifact_budget_gib,
        artifact_min_free_gib=artifact_min_free_gib,
        actuation_enabled=bool(contract["actuation_enabled"]),
        native_fidelity=native_fidelity,
        disable_app_overrides=disable_app_overrides,
        infra_resource_profile=infra_resource_profile,
        execution_slot_env=execution_slot_env,
    )
    env["GREENRAN_ARMD_MODE"] = str(contract["armd_mode"])
    # The dispatcher/user service may inherit the historical file/shadow
    # transport from its environment.  Actuation modes must use the native
    # E2 socket; otherwise GREENRAN_TASAM_E2_CONTROL=1 only records bundles
    # and never starts the actuator.  Shadow and rApp-only modes keep their
    # existing non-actuating transport contract.
    if contract.get("actuation_enabled"):
        env["GREENRAN_XAPP_MODE"] = "socket"
    if vehicle_profile_manifest is not None:
        vehicle_profile = _validate_vehicle_profile_manifest(
            vehicle_profile_manifest, expected_profile=profile
        )
        env["GREENRAN_VEHICLE_PROFILE_MANIFEST"] = str(vehicle_profile_manifest.resolve())
        env["GREENRAN_NS3_VEHICLE_PACKET_INTERVAL_US"] = str(int(vehicle_profile["selected_interval_us"]))
        # The legacy MC bearer path in this ns-3/mmWave fork does not support
        # dedicated EPS activation for McUeNetDevice.  The v12 vehicle
        # contract uses the supported mmWave UE bearer path explicitly.
        env["GREENRAN_NS3_USE_MC_UE_DEVICES"] = "false"
    if profile == BASELINE_MAX_PROFILE:
        # A baseline-max run must use the real LTE anchor even if the caller
        # inherited a legacy vehicle manifest that requested non-MC devices.
        env["GREENRAN_NS3_USE_MC_UE_DEVICES"] = "true"
        env["GREENRAN_NS3_E2LTE_ENABLED"] = "true"
        env["GREENRAN_NS3_E2NR_ENABLED"] = "false"
        env["GREENRAN_NS3_E2DU_ENABLED"] = "true"
    env["GREENRAN_TASAM_ADVISOR_ENABLED"] = "1" if contract["tasam_enabled"] else "0"
    env["GREENRAN_TASAM_ADVISOR_MODE"] = str(contract["tasam_mode"])
    env["GREENRAN_CONTROL_TRIAL_ENABLED"] = "1" if contract["actuation_enabled"] else "0"
    env["GREENRAN_TASAM_REQUIRE_CHECKPOINT"] = "1" if contract["tasam_enabled"] else "0"
    if contract.get("reward_contract"):
        env["GREENRAN_TASAM_REWARD_CONTRACT"] = str(contract["reward_contract"])
        env["GREENRAN_TASAM_REWARD_ENERGY_ENABLED"] = "1" if energy_enabled else "0"
    else:
        env.pop("GREENRAN_TASAM_REWARD_CONTRACT", None)
        env.pop("GREENRAN_TASAM_REWARD_ENERGY_ENABLED", None)
    # The rApp reference is also an energy-observation arm when requested.
    # It must use the same V3/native readback path, while remaining free of
    # TA-SAM's floor/headroom envelope.
    if mode in {"rapp_only_actuating", "fixed_100_native", "native_sleep_calibration"} and energy_enabled:
        env["GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT"] = "economic_action_v3_per_du_sleep"
    if mode == "fixed_100_native":
        # Calibration directive, not a live candidate: the orchestrator must
        # honor the exact requested percent (5%-step) instead of snapping to
        # the legacy {25, 60, 100} ladder, which would turn 45 into 60 and
        # 70 into 60 in the physics calibration runs.
        env["GREENRAN_TASAM_FIXED_NATIVE_POWER_PERCENT"] = str(
            int(fixed_native_power_percent)
        )
    elif contract.get("economic_action_contract"):
        env["GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT"] = str(
            contract["economic_action_contract"]
        )
    if mode in V2X_ENERGY_MODES or (energy_enabled and _is_v6_v2x_profile(profile)):
        # v6 is intentionally distinct from the historical v5 trace: a
        # current energy arm requires a complete three-DU confirmation.
        env["GREENRAN_NATIVE_EVIDENCE_VERSION"] = "v6"
        env["GREENRAN_NATIVE_SOURCE_GENERATION"] = f"{run_dir.name}:native-v6"
        env["GREENRAN_NS3_NATIVE_AGGREGATED_EVIDENCE"] = "1"
        env["GREENRAN_TASAM_ECONOMIC_HEAD_ENABLED"] = "1" if mode in V2X_ENERGY_MODES else "0"
        env["GREENRAN_TASAM_RESOURCE_FLOOR_POLICY"] = "floor_to_115_percent_v1"
        env["GREENRAN_TASAM_ECONOMIC_SAFETY_ISOLATION_REQUIRED"] = "1"
        env["GREENRAN_TASAM_ECONOMIC_SAFETY_ISOLATION"] = "blocked_and_critical_v1"
        if energy_staircase:
            env["GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT"] = (
                "greenran.tasam.v2x.energy_staircase.v1"
            )
            env["GREENRAN_TASAM_ENERGY_STAIRCASE_HEALTHY_REQUIRED"] = "3"
            env["GREENRAN_TASAM_ALLOW_DU_SLEEP"] = "1"
            if safe_power_floor_ledger is not None:
                env["GREENRAN_TASAM_SAFE_POWER_FLOOR_LEDGER"] = str(
                    safe_power_floor_ledger.resolve()
                )
        if mode == "asgard_v2x_window90_energy_frozen":
            env["GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION"] = "1.0"
            env["GREENRAN_TASAM_PILOT_FULL_ROLLOUT"] = "1"
            env["GREENRAN_ONLINE_UPDATE_OWNER"] = "frozen_checkpoint_evaluation"
        if mode == "asgard_v2x_window90_energy_dynamic":
            if dynamic_floor_ledger is None or baseline_signature is None:
                raise ValueError(
                    "dynamic ASGARD mode requires ledger v2 and r26 baseline signature"
                )
            env.pop("GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT", None)
            env["GREENRAN_TASAM_ALLOW_DU_SLEEP"] = "1"
            env["GREENRAN_TASAM_DYNAMIC_FLOOR_CONTRACT"] = DYNAMIC_FLOOR_CONTRACT
            env["GREENRAN_TASAM_RESOURCE_FLOOR_POLICY"] = (
                "adaptive_energy_envelope_v2"
            )
            env["GREENRAN_TASAM_DYNAMIC_FLOOR_LEDGER"] = str(
                dynamic_floor_ledger.resolve()
            )
            env["GREENRAN_TASAM_BASELINE_SIGNATURE"] = str(
                baseline_signature.resolve()
            )
            env["GREENRAN_TASAM_DYNAMIC_FLOOR_STATE"] = str(
                run_dir / "dynamic_floor_state.json"
            )
    # ``_common_env`` is also used by the historical full-control trainer.
    # An assistant-only online adaptation must never inherit that override:
    # it starts with the declared 10% canary and only its controller may
    # advance a promoted candidate through later rollout stages.
    env["GREENRAN_TASAM_FORCE_FULL_ROLLOUT"] = "1" if mode in {"train_no_armd", "sac_l2_online"} else "0"
    if not contract["controller_enabled"]:
        env["GREENRAN_TASAM_TRUE_ONLINE_ENABLED"] = "0"
        env["GREENRAN_TASAM_TRUE_ONLINE_EXTERNAL_CONTROLLER"] = "0"
        env["GREENRAN_ONLINE_UPDATE_OWNER"] = "frozen_checkpoint_evaluation"
    if mode == "combined_shadow":
        # Shadow must retain the live rApp allocation.  A rollout fraction of
        # 1.0 would let the Judge replace that allocation even though E2
        # actuation is disabled, contaminating the observational arm.
        env["GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION"] = "0.0"
    if mode == "combined_actuation_smoke":
        env["GREENRAN_CONTROL_TRIAL_FRACTION"] = str(
            control_fraction if control_fraction is not None else "0.10"
        )
        env["GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION"] = "0.10"
        # Operational smoke probe only: preserve the declared 10% rollout,
        # while selecting every tenth eligible proposal deterministically so
        # native actuation evidence is reproducible in a short run.
        env["GREENRAN_TASAM_ACTUATION_SMOKE_PROBE"] = "1"
        env["GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT"] = os.environ.get(
            "GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT",
            "applied_action_v2",
        )
        env["GREENRAN_TASAM_ECONOMIC_HEAD_ENABLED"] = "1"
        env["GREENRAN_CONTROL_TRIAL_TARGET_DECISIONS"] = "30"
        env["GREENRAN_CONTROL_TRIAL_ROLLING_WINDOW"] = "10"
        env["GREENRAN_CONTROL_TRIAL_STATE"] = str(run_dir / "control_trial_state.json")
    if mode in {"combined_online", "sac_l2_online", "tasam_v2x_online", *V2X_WINDOW90_ONLINE_MODES}:
        # Do not inherit config/core/runtime.json's historical disabled
        # defaults.  This flag is campaign-local and is persisted in the
        # learning meter so a run cannot be mistaken for online learning.
        env["GREENRAN_ML_ENABLED"] = "1"
        env["GREENRAN_ML_RETRAIN_ENABLED"] = "true"
        env["GREENRAN_TASAM_ONLINE_UPDATE_OWNER"] = "run_tasam_online_controlled.py"
        env["GREENRAN_TASAM_ONLINE_REPLAY_CONTRACT"] = str(contract.get("replay_contract", "legacy"))
        env["GREENRAN_TASAM_ONLINE_SAM_MODE"] = str(contract.get("sam_mode", "tasam_selective"))
        env["GREENRAN_TASAM_ONLINE_L2_WEIGHT"] = str(contract.get("l2_weight", 0.0))
        env["GREENRAN_CONTROL_TRIAL_FRACTION"] = "1.0" if mode in {"sac_l2_online", *V2X_WINDOW90_ONLINE_MODES} else "0.10"
        # The control-trial guard follows the controller-owned rollout
        # manifest, but never exceeds the economic campaign cap.
        env["GREENRAN_TASAM_MAX_ROLLOUT_FRACTION"] = os.environ.get(
            "GREENRAN_TASAM_MAX_ROLLOUT_FRACTION",
            str(max(0.0, min(float(max_rollout_fraction if max_rollout_fraction is not None else (1.0 if mode in {"sac_l2_online", *V2X_WINDOW90_ONLINE_MODES} else 0.50)), 1.0))),
        )
        env["GREENRAN_CONTROL_TRIAL_ENABLED"] = "1"
        env["GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION"] = "1.0" if mode in V2X_WINDOW90_ONLINE_MODES else "0.10"
        if mode in V2X_WINDOW90_ONLINE_MODES:
            env["GREENRAN_TASAM_PILOT_FULL_ROLLOUT"] = "1"
        if mode == "combined_online":
            env["GREENRAN_TASAM_ECONOMIC_HEAD_ENABLED"] = "1"
            env["GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT"] = os.environ.get(
                "GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT", "applied_action_v2"
            )
            env["GREENRAN_TASAM_ECONOMIC_SAFETY_ISOLATION_REQUIRED"] = "1"
            env["GREENRAN_TASAM_ECONOMIC_SAFETY_ISOLATION"] = "blocked_and_critical_v1"
            env["GREENRAN_TASAM_ECONOMIC_BOOTSTRAP_POWER"] = os.environ.get(
                "GREENRAN_TASAM_ECONOMIC_BOOTSTRAP_POWER", "25"
            )
    env["GREENRAN_TASAM_E2_CONTROL"] = "1" if contract["actuation_enabled"] else "0"
    if contract.get("assistant_decision_mode"):
        env["GREENRAN_ASSISTANT_DECISION_MODE"] = str(contract["assistant_decision_mode"])
    if mode == "combined":
        env["GREENRAN_CONTROL_TRIAL_FRACTION"] = str(control_fraction if control_fraction is not None else os.environ.get("GREENRAN_CONTROL_TRIAL_FRACTION", "0.10"))
        env["GREENRAN_CONTROL_TRIAL_TARGET_DECISIONS"] = os.environ.get("GREENRAN_CONTROL_TRIAL_TARGET_DECISIONS", "300")
        env["GREENRAN_CONTROL_TRIAL_ROLLING_WINDOW"] = os.environ.get("GREENRAN_CONTROL_TRIAL_ROLLING_WINDOW", "30")
        env["GREENRAN_CONTROL_TRIAL_CRITICAL_STREAK"] = os.environ.get("GREENRAN_CONTROL_TRIAL_CRITICAL_STREAK", "3")
        env["GREENRAN_CONTROL_TRIAL_MIN_CONFIDENCE"] = os.environ.get("GREENRAN_CONTROL_TRIAL_MIN_CONFIDENCE", "0.60")
        env["GREENRAN_CONTROL_TRIAL_MIN_RAN_DELTA"] = os.environ.get("GREENRAN_CONTROL_TRIAL_MIN_RAN_DELTA", "-0.01")
        env["GREENRAN_CONTROL_TRIAL_MIN_AI_DELTA"] = os.environ.get("GREENRAN_CONTROL_TRIAL_MIN_AI_DELTA", "-0.02")
        env["GREENRAN_CONTROL_TRIAL_STATE"] = str(run_dir / "control_trial_state.json")
    if mode in {"combined_online", "sac_l2_online", "tasam_v2x_online", *V2X_WINDOW90_ONLINE_MODES}:
        env["GREENRAN_CONTROL_TRIAL_TARGET_DECISIONS"] = os.environ.get(
            "GREENRAN_CONTROL_TRIAL_TARGET_DECISIONS",
        "90" if mode in V2X_WINDOW90_ONLINE_MODES else "600",
        )
        env["GREENRAN_CONTROL_TRIAL_ROLLING_WINDOW"] = os.environ.get("GREENRAN_CONTROL_TRIAL_ROLLING_WINDOW", "30")
        env["GREENRAN_CONTROL_TRIAL_CRITICAL_STREAK"] = os.environ.get("GREENRAN_CONTROL_TRIAL_CRITICAL_STREAK", "3")
        env["GREENRAN_CONTROL_TRIAL_MIN_CONFIDENCE"] = os.environ.get("GREENRAN_CONTROL_TRIAL_MIN_CONFIDENCE", "0.60")
        env["GREENRAN_CONTROL_TRIAL_MIN_RAN_DELTA"] = os.environ.get("GREENRAN_CONTROL_TRIAL_MIN_RAN_DELTA", "-0.01")
        env["GREENRAN_CONTROL_TRIAL_MIN_AI_DELTA"] = os.environ.get("GREENRAN_CONTROL_TRIAL_MIN_AI_DELTA", "-0.02")
        env["GREENRAN_CONTROL_TRIAL_STATE"] = str(run_dir / "control_trial_state.json")
    return env


def _controller_command(args: argparse.Namespace) -> list[str]:
    contract = mode_contract(args.mode)
    command = [
        sys.executable,
        str(ONLINE_CONTROLLER),
        "--state-dir",
        str(args.run_dir),
        "--checkpoint",
        str(args.checkpoint),
        "--historical-trace",
        str(args.experience_bank or args.run_dir / "historical_replay_disabled.jsonl"),
        "--min-new-snapshots",
        str(args.min_new_snapshots),
        "--min-trainable-transitions",
        str(args.min_trainable_transitions),
        "--replay-rows",
        str(args.replay_rows),
        "--epochs-per-update",
        str(args.epochs_per_update),
        "--poll-seconds",
        str(args.controller_poll_seconds),
        "--seed",
        str(args.seed),
        "--replay-policy",
        (
            "v2x_window90"
            if contract.get("replay_contract") == "greenran.tasam.v2x.window90.replay_80_20.v1"
            else "v2x_80_20"
            if contract.get("replay_contract") == "greenran.tasam.v2x.replay_80_20.v1"
            else "legacy"
        ),
        "--sam-mode",
        str(contract.get("sam_mode", "tasam_selective")),
        "--l2-weight",
        str(contract.get("l2_weight", 0.0)),
        "--learning-rate",
        "0.0001",
    ]
    if contract.get("reward_contract"):
        command.extend(["--reward-contract", str(contract["reward_contract"])])
    if args.prioritize_category_errors:
        command.extend([
            "--prioritize-category-errors",
            "--category-error-repeat",
            str(args.category_error_repeat),
        ])
    command.extend([
        "--category-loss-weight", str(args.category_loss_weight),
        "--category-head-hidden-dim", str(args.category_head_hidden_dim),
    ])
    if args.experience_bank:
        command.extend(["--experience-bank", str(args.experience_bank)])
    if getattr(args, "recent_experience_bank", None):
        command.extend(["--recent-experience-bank", str(args.recent_experience_bank)])
    if args.mode in {"train_no_armd", "combined", "combined_actuation_smoke"}:
        if not args.experience_bank:
            command.append("--online-only")
    if args.mode == "combined_online":
        command.append("--online-only")
    if args.mode == "combined_online":
        command.extend([
            "--allocation-head-output-dim", "3",
            "--max-rollout-fraction", str(args.max_rollout_fraction),
            "--shadow-min-decisions", str(args.shadow_min_decisions),
        "--stage-window-decisions", str(args.stage_window_decisions),
        "--min-economic-transitions", str(args.min_economic_transitions),
        "--economic-update-min-transitions", str(getattr(args, "economic_update_min_transitions", 64)),
        "--sqlite-economic-replay",
        ])
    if args.mode in V2X_WINDOW90_ONLINE_MODES:
        command.extend([
            "--max-rollout-fraction", "1.0",
            "--shadow-min-decisions", "1",
            "--stage-window-decisions", "18",
            "--update-milestones", "18,36,54,72,90",
        ])
    if args.mode in {"asgard_v2x_window90_energy_online", "asgard_v2x_window90_energy_dynamic"}:
        command.extend([
            "--allocation-head-output-dim", "3",
            "--global-action-dim", "5",
            "--economic-update-min-transitions", "0",
        ])
    return command


def _decision_target_watcher_command(args: argparse.Namespace) -> list[str]:
    """Build the state-scoped watcher that ends a target-bounded arm early."""
    return [
        sys.executable,
        str(DECISION_TARGET_WATCHER),
        "--state-dir", str(args.run_dir),
        "--db", str(args.run_dir / "rapp_data_lake.db"),
        "--target", str(int(args.decision_target)),
        "--poll-seconds", "0.5",
        "--wall-only",
    ]


def _decision_journal_count(run_dir: Path) -> int:
    """Read the cheap append-only progress signal used by the parent watchdog."""
    try:
        with (run_dir / "rapp_decisions.jsonl").open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _write_decision_target_request(
    run_dir: Path,
    target: int,
    *,
    source: str,
    reason: str = "decision_target_reached",
) -> None:
    """Request a cooperative wall-supervisor stop for this run only."""
    request = run_dir / "decision_target_stop.json"
    payload = {
        "schema": "greenran.decision_target_stop.v1",
        "reason": str(reason),
        "target": int(target),
        "observed_decisions": _decision_journal_count(run_dir),
        "source": source,
        "requested_at": time.time(),
    }
    temporary = request.with_suffix(request.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(request)


def _parent_decision_target_watch(
    run_dir: Path,
    target: int,
    stop_event: threading.Event,
    result: dict[str, Any],
    *,
    min_target: int = 0,
    stop_after_promotion: bool = False,
) -> None:
    """Redundant in-process watchdog so a child watcher cannot strand wall-clock."""
    while not stop_event.wait(0.5):
        count = _decision_journal_count(run_dir)
        if stop_after_promotion and count >= int(min_target):
            state = _read_json(run_dir / "online_state.json")
            if bool(state.get("candidate_promoted", False)):
                try:
                    _write_decision_target_request(
                        run_dir,
                        count,
                        source="arm_parent_promotion_watchdog",
                        reason="candidate_promoted_after_minimum",
                    )
                    result.update({
                        "target": int(count), "decisions": count, "requested": True,
                        "reason": "candidate_promoted_after_minimum",
                    })
                    with (run_dir / "decision_target.log").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({
                            "source": "arm_parent_promotion_watchdog",
                            "target": int(count), "decisions": count,
                            "requested": True,
                            "reason": "candidate_promoted_after_minimum",
                        }, ensure_ascii=False) + "\n")
                        log.flush()
                except OSError as exc:
                    result.update({"target": int(count), "decisions": count, "requested": False, "error": str(exc)})
                return
        if count < int(target):
            continue
        try:
            _write_decision_target_request(run_dir, target, source="arm_parent_watchdog")
            result.update({"target": int(target), "decisions": count, "requested": True})
            with (run_dir / "decision_target.log").open("a", encoding="utf-8") as log:
                log.write(json.dumps({
                    "source": "arm_parent_watchdog",
                    "target": int(target),
                    "decisions": count,
                    "requested": True,
                }, ensure_ascii=False) + "\n")
                log.flush()
        except OSError as exc:
            result.update({"target": int(target), "decisions": count, "requested": False, "error": str(exc)})
        return


def _parent_native_actuation_watch(
    run_dir: Path,
    stop_event: threading.Event,
    result: dict[str, Any],
) -> None:
    """Stop the bounded actuation smoke after independent native proof.

    The actuation smoke must not wait for the full decision target once the
    selected TA-SAM action has been observed by ns-3 and correlated with the
    Data Lake.  This watcher is intentionally limited to the smoke mode and
    never promotes a checkpoint or marks an economic transition trainable.
    """
    while not stop_event.wait(0.5):
        evidence = _native_actuation_evidence(run_dir)
        if not evidence.get("valid"):
            continue
        feedback = _native_actuation_feedback_ready(run_dir, evidence)
        if not feedback.get("valid"):
            continue
        count = _decision_journal_count(run_dir)
        try:
            _write_decision_target_request(
                run_dir,
                max(1, count),
                source="arm_parent_actuation_watchdog",
                reason="native_actuation_confirmed",
            )
            payload = {
                "source": "arm_parent_actuation_watchdog",
                "decisions": count,
                "requested": True,
                "reason": "native_actuation_confirmed",
                "evidence": evidence,
                "feedback": feedback,
            }
            result.update(payload)
            with (run_dir / "decision_target.log").open("a", encoding="utf-8") as log:
                log.write(json.dumps(payload, ensure_ascii=False) + "\n")
                log.flush()
        except OSError as exc:
            result.update({
                "decisions": count,
                "requested": False,
                "reason": "native_actuation_confirmed",
                "error": str(exc),
                "evidence": evidence,
            })
        return


def _parent_native_sleep_calibration(
    run_dir: Path,
    socket_dir: Path,
    stop_event: threading.Event,
    result: dict[str, Any],
) -> None:
    """Run the one-shot native drain/commit calibration inside a live arm.

    The calibration module stays passive until the E2 actuator socket and
    the native traces exist, then performs exactly one sleep transaction
    outside the rApp decision cycle.  The arm continues to full sim time so
    the wake path is also covered by the traces used by the ledger builder.
    """
    socket_path = socket_dir / "tasam_control.sock"
    association = run_dir / "ns3_energy" / "TasamAssociationTrace.csv"
    pdcp = run_dir / "ns3_energy" / "VehiclePdcpPduTrace.csv"
    readiness_deadline = time.monotonic() + 600.0
    while not stop_event.is_set() and time.monotonic() < readiness_deadline:
        if socket_path.exists() and association.exists() and pdcp.exists():
            break
        stop_event.wait(1.0)
    if not (socket_path.exists() and association.exists() and pdcp.exists()):
        result.update({
            "status": "runtime_not_ready",
            "socket": str(socket_path),
            "association_trace": str(association),
            "pdcp_trace": str(pdcp),
        })
        return
    # Give the runtime one association snapshot cycle so the calibration
    # reads a complete three-DU baseline before selecting the source DU.
    stop_event.wait(5.0)
    try:
        execute_native_sleep_calibration(
            run_dir, socket_dir, stop_event, result
        )
    except Exception as exc:  # noqa: BLE001 - evidence stays fail-closed
        result.update({"status": "calibration_error", "error": str(exc)})


def _terminate_group(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.2)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run(args: argparse.Namespace) -> int:
    args.run_dir = args.run_dir.resolve()
    args.checkpoint = args.checkpoint.resolve()
    if (
        args.mode == "asgard_v2x_window90_energy_dynamic"
        and args.run_dir.exists()
    ):
        raise SystemExit(f"modo dynamic não reutiliza run-dir existente: {args.run_dir}")
    _validate_local_path(args.run_dir, "run-dir")
    _validate_local_path(args.checkpoint, "checkpoint")

    # Sem este handler, o SIGTERM/SIGKILL do gate encerra o interpretador sem
    # executar o bloco finally abaixo, e o trainer (sessão própria,
    # start_new_session=True) sobrevive como órfão re-parentado.  O primeiro
    # sinal dispara SystemExit(128+signum) para desenrolar pelo finally (que
    # encerra controller/wall_runner via _terminate_group) mantendo o código
    # de saída 143 esperado pelo gate; sinais repetidos são ignorados para
    # não interromper a limpeza.
    shutdown_signalled = threading.Event()

    def _graceful_shutdown_signal(signum: int, _frame: Any) -> None:
        if shutdown_signalled.is_set():
            return
        shutdown_signalled.set()
        raise SystemExit(128 + int(signum))

    signal.signal(signal.SIGTERM, _graceful_shutdown_signal)
    signal.signal(signal.SIGINT, _graceful_shutdown_signal)

    dynamic_ledger_contract: dict[str, Any] | None = None
    if args.mode == "asgard_v2x_window90_energy_dynamic" and args.binary is None:
        raise SystemExit("modo dynamic exige --binary explícito para validar a proveniência física")
    if args.binary is not None:
        args.binary = _validate_local_path(args.binary, "binário ns-3")
        if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
            raise SystemExit(f"binário ns-3 ausente ou não executável: {args.binary}")
        if args.mode in V2X_ENERGY_MODES or (
            args.energy_enabled and args.profile.startswith("tasam_training_balanced_v6_v2x")
        ):
            # The energy V6 contract is tied to the current scenario/scheduler/
            # PDCP evidence producers.  Refuse a stale executable before a
            # campaign directory or manifest is created.
            assert_v2x_binary_fresh(args.binary)
        os.environ["GREENRAN_NS3_BIN"] = str(args.binary)
    if args.energy_calibration is not None:
        args.energy_calibration = _validate_local_path(args.energy_calibration, "calibração energética")
    if args.safe_power_floor_ledger is not None:
        args.safe_power_floor_ledger = _validate_local_path(
            args.safe_power_floor_ledger, "ledger de piso seguro"
        )
        if not args.safe_power_floor_ledger.is_file():
            raise SystemExit(
                f"ledger de piso seguro ausente: {args.safe_power_floor_ledger}"
            )
    if args.dynamic_floor_ledger is not None:
        args.dynamic_floor_ledger = _validate_local_path(
            args.dynamic_floor_ledger, "ledger v2 do piso dinâmico"
        )
    if args.baseline_signature is not None:
        args.baseline_signature = _validate_local_path(
            args.baseline_signature, "assinatura SLA r26"
        )
    fixed_native_power = _validate_fixed_native_power_percent(
        args.fixed_native_power_percent, args.mode
    )
    if args.mode == "asgard_v2x_window90_energy_dynamic":
        if args.dynamic_floor_ledger is None or args.baseline_signature is None:
            raise SystemExit(
                "modo dynamic exige --dynamic-floor-ledger e --baseline-signature"
            )
        if not args.dynamic_floor_ledger.is_file() or not args.baseline_signature.is_file():
            raise SystemExit("ledger v2 ou assinatura SLA r26 ausente")
        try:
            load_baseline_signature(
                args.baseline_signature,
                expected_seed=args.seed,
                expected_profile=args.profile,
                strict_contract=True,
            )
            dynamic_ledger_contract = load_dynamic_floor_ledger(
                args.dynamic_floor_ledger,
                expected_seed=args.seed,
                expected_profile=args.profile,
                baseline_signature_path=args.baseline_signature,
            )
        except DynamicFloorError as exc:
            raise SystemExit(f"contrato do piso dinâmico inválido: {exc}") from exc
    elif args.dynamic_floor_ledger is not None or args.baseline_signature is not None:
        raise SystemExit(
            "ledger v2 e assinatura SLA são exclusivos do modo dynamic"
        )
    energy_calibration = _resolve_energy_calibration(args.energy_calibration)
    if args.control_gate:
        args.control_gate = _validate_local_path(args.control_gate, "control-gate")
    if args.mode in {"sac_l2_online", "tasam_v2x_online", *V2X_WINDOW90_ONLINE_MODES}:
        if args.experience_bank is None:
            raise SystemExit("treino V2X do artigo exige --experience-bank histórico")
        if args.mode in V2X_WINDOW90_ONLINE_MODES and args.replay_rows != 90:
            raise SystemExit("piloto window90 exige replay de 90 transições (72/18)")
        if args.mode not in V2X_WINDOW90_ONLINE_MODES and args.replay_rows != 600:
            raise SystemExit("treino V2X do artigo exige replay de 600 transições (480/120)")
        if args.prioritize_category_errors:
            raise SystemExit("treino V2X do artigo não permite duplicação/priorização no replay 80/20")
    if args.disable_app_overrides and args.mode not in {
        "rapp_only", "rapp_only_actuating", "fixed_100_native", "asgard_v2x_window90_online",
        "asgard_v2x_window90_energy_online", "asgard_v2x_window90_energy_frozen",
        "asgard_v2x_window90_energy_dynamic",
        "tasam_v2x_frozen",
    }:
        raise SystemExit(
            "--disable-app-overrides só é permitido nos braços rApp-only e no piloto ASGARD window90"
        )
    slot_env: dict[str, str] = {}
    slot_lease: SlotLease | None = None
    parallel_slot = None
    lease_held_by_parent = os.environ.get("GREENRAN_SLOT_LEASE_HELD", "") == "1"
    if args.execution_slot:
        if args.profile != BASELINE_MAX_PROFILE:
            raise SystemExit(
                "--execution-slot exige o perfil V2X baseline_max para manter a proveniência do cenário"
            )
        slots = resolve_slots(args.execution_slot)
        if len(slots) != 1:
            raise SystemExit("cada arm online deve reservar exatamente um execution-slot")
        parallel_slot = slots[0]
        assert_disk_capacity(ROOT / "runs", 2)
        slot_env = parallel_slot.as_environment()
    else:
        # The feasibility runner owns the lease and launches this arm as a
        # child.  In that arrangement the child intentionally does not take
        # the same flock again, but it must still inherit the slot's resource
        # profile for its manifest and systemd scope.  Ports alone are not
        # sufficient evidence of parallel isolation.
        inherited_slot = os.environ.get("GREENRAN_V2X_EXECUTION_SLOT", "").strip()
        if inherited_slot in {"slot-a", "slot-b"}:
            if args.profile != BASELINE_MAX_PROFILE:
                raise SystemExit("slot herdado exige o perfil V2X baseline_max")
            parallel_slot = resolve_slots(inherited_slot)[0]
            slot_env = parallel_slot.as_environment()
    arm_resource_profile = (
        PARALLEL_PAIR_RESOURCE_PROFILE
        if parallel_slot is not None
        else ("baseline_max_v1" if args.profile == BASELINE_MAX_PROFILE else "standard")
    )
    _validate_run_dir(args.run_dir)
    # ``disk_usage`` requires an existing path.  Creating only the requested
    # campaign parent keeps the new run isolated and never modifies an older
    # experiment directory.
    args.run_dir.parent.mkdir(parents=True, exist_ok=True)
    if args.mode not in {"rapp_only", "rapp_only_actuating", "fixed_100_native"}:
        required = args.checkpoint / "tasam_marl_actors.pt"
        if not required.is_file():
            raise SystemExit(f"checkpoint TA-SAM ausente: {required}")
        _validate_checkpoint(
            args.checkpoint,
            require_economic_head=args.mode in {
                "combined_online", "combined_actuation_smoke",
                "asgard_v2x_window90_energy_online", "asgard_v2x_window90_energy_frozen",
                "asgard_v2x_window90_energy_dynamic",
            },
        )
        if args.mode == "asgard_v2x_window90_energy_dynamic":
            physics = (dynamic_ledger_contract or {}).get("physics_evidence") or {}
            if file_sha256(args.binary) != str(physics.get("binary_sha256") or ""):
                raise SystemExit("binário ns-3 difere do hash selado pela curva A/B/C")
            if checkpoint_fingerprint(args.checkpoint) != str(
                (dynamic_ledger_contract or {}).get("initial_checkpoint_sha256") or ""
            ):
                raise SystemExit("checkpoint inicial difere do checkpoint r26 selado")
    # Delegation is verified before creating the run parent or manifest. A
    # campaign launched by ``robert`` must either receive real cgroup-v2
    # limits or stop without leaving a partial experiment behind.
    scope_preflight: dict[str, Any] = {}
    if args.profile == BASELINE_MAX_PROFILE:
        baseline_budget = build_physical_budget(
            1.0, unrestricted=True, resource_profile=arm_resource_profile
        )
        scope_preflight = probe_systemd_user_scope(baseline_budget["groups"]["simulator"])
        if not scope_preflight.get("valid"):
            raise SystemExit(
                "backend systemd --user não aplicou o envelope baseline_max_v1: "
                + str(scope_preflight.get("reason") or scope_preflight.get("stderr") or "unknown")
            )
    else:
        try:
            assert_cgroup_delegation()
        except InfraBudgetError as exc:
            raise SystemExit(str(exc)) from exc
    minimum_free = float(args.min_free_gib)
    available = _free_gib(args.run_dir.parent)
    if available < minimum_free:
        raise SystemExit(
            f"espaço livre insuficiente para execução longa: {available:.2f} GiB < {minimum_free:.2f} GiB; "
            "libere espaço em /home antes de iniciar a campanha"
        )
    if args.run_dir.exists() and any(args.run_dir.iterdir()):
        raise SystemExit(f"run-dir não está vazio: {args.run_dir}")
    if parallel_slot is not None and not lease_held_by_parent:
        slot_lease = SlotLease(parallel_slot, args.run_dir)
        slot_lease.__enter__()
        slot_lease.publish_campaign_contract()
        atexit.register(slot_lease.__exit__, None, None, None)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    env = build_environment(
        args.mode,
        args.run_dir,
        args.seed,
        args.profile,
        args.wall_time,
        args.sim_time,
        native_fidelity=args.native_fidelity,
        decision_target=args.decision_target,
        pairing_schedule_id=args.pairing_schedule_id,
        pairing_schedule_file=args.pairing_schedule_file,
        control_gate=args.control_gate,
        control_fraction=args.control_fraction,
        max_rollout_fraction=args.max_rollout_fraction,
        vehicle_profile_manifest=args.vehicle_profile_manifest,
        artifact_budget_gib=args.artifact_budget_gib,
        artifact_min_free_gib=args.artifact_min_free_gib,
        energy_enabled=bool(args.energy_enabled),
        energy_staircase=bool(args.energy_staircase),
        safe_power_floor_ledger=args.safe_power_floor_ledger,
        dynamic_floor_ledger=args.dynamic_floor_ledger,
        baseline_signature=args.baseline_signature,
        fixed_native_power_percent=fixed_native_power,
        disable_app_overrides=bool(args.disable_app_overrides),
        infra_resource_profile=arm_resource_profile,
        execution_slot_env=slot_env,
    )
    if parallel_slot is not None:
        # Keep the isolation contract authoritative even when the parent
        # process inherited a stale baseline profile.  A slot arm must never
        # silently consume the single-arm 12-CPU/8-GiB envelope.
        env["GREENRAN_INFRA_RESOURCE_PROFILE"] = PARALLEL_PAIR_RESOURCE_PROFILE
        parallel_budget = build_physical_budget(
            1.0, unrestricted=True, resource_profile=PARALLEL_PAIR_RESOURCE_PROFILE
        )
        for group, limits in parallel_budget["groups"].items():
            key = group.upper()
            env[f"GREENRAN_CGROUP_SCOPE_{key}_CPU_QUOTA_US"] = str(limits["cpu_quota_us"])
            env[f"GREENRAN_CGROUP_SCOPE_{key}_MEMORY_HIGH_BYTES"] = str(limits["memory_high_bytes"])
            env[f"GREENRAN_CGROUP_SCOPE_{key}_IO_WEIGHT"] = str(limits["io_weight"])
        if env.get("GREENRAN_INFRA_RESOURCE_PROFILE") != arm_resource_profile:
            raise SystemExit("envelope paralelo V2X não pôde ser aplicado ao arm")
    if args.mode == "combined_actuation_smoke" and args.control_gate is None:
        # This is deliberately narrower than the scientific control gate:
        # it authorizes only the 10% native-actuation smoke and cannot be
        # reused to promote a checkpoint or start a causal pair.
        _write_actuation_smoke_gate(
            Path(env["GREENRAN_MARL_CONTROL_GATE_MANIFEST"]), args.checkpoint
        )
    env["GREENRAN_ENERGY_CALIBRATION_PATH"] = str(energy_calibration["calibration_path"])
    checkpoint_metadata = _read_json(args.checkpoint / "tasam_marl_checkpoint_meta.json")
    if checkpoint_metadata.get("economic_action_contract") in {
        "applied_action_v2", "economic_action_v3_per_du_sleep"
    }:
        env["GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT"] = checkpoint_metadata[
            "economic_action_contract"
        ]
    contract = mode_contract(args.mode)
    _validate_runtime_contract(env, args.mode, args.checkpoint)
    checkpoint_before = checkpoint_fingerprint(args.checkpoint) if contract["tasam_enabled"] else ""
    if contract["tasam_enabled"]:
        env["GREENRAN_TASAM_CHECKPOINT"] = str(args.checkpoint)
        env["GREENRAN_TASAM_TRUE_ONLINE_INIT_CHECKPOINT"] = str(args.checkpoint)
        env["GREENRAN_TASAM_TRUE_ONLINE_SEED"] = str(args.seed)
    else:
        env.pop("GREENRAN_TASAM_CHECKPOINT", None)
        env.pop("GREENRAN_TASAM_TRUE_ONLINE_INIT_CHECKPOINT", None)
        env.pop("GREENRAN_TASAM_TRUE_ONLINE_SEED", None)
    manifest = {
        "schema": "greenran.tasam_online_arm.v1",
        "mode": args.mode,
        "article_method": contract.get("article_method", ""),
        "training_mode": "online_adaptive" if contract["controller_enabled"] else "frozen_evaluation",
        "replay_contract": contract.get("replay_contract", ""),
        "replay_ratio": "80/20" if contract.get("replay_contract") else "",
        "reward_contract": contract.get("reward_contract", ""),
        "reward_energy_enabled": bool(args.energy_enabled),
        "energy_staircase_contract": env.get("GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT", ""),
        "energy_staircase_config": str(ROOT / "config/greenran_v2x_energy_staircase.json")
        if env.get("GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT") else "",
        "energy_staircase_config_sha256": file_sha256(ROOT / "config/greenran_v2x_energy_staircase.json")
        if env.get("GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT") and (ROOT / "config/greenran_v2x_energy_staircase.json").is_file() else "",
        "energy_staircase_healthy_required": int(
            env.get("GREENRAN_TASAM_ENERGY_STAIRCASE_HEALTHY_REQUIRED", "0")
        ),
        "safe_power_floor_ledger": env.get("GREENRAN_TASAM_SAFE_POWER_FLOOR_LEDGER", ""),
        "safe_power_floor_ledger_sha256": file_sha256(
            Path(env["GREENRAN_TASAM_SAFE_POWER_FLOOR_LEDGER"])
        ) if env.get("GREENRAN_TASAM_SAFE_POWER_FLOOR_LEDGER") else "",
        "dynamic_floor_contract": env.get("GREENRAN_TASAM_DYNAMIC_FLOOR_CONTRACT", ""),
        "fixed_native_power_percent": int(env.get("GREENRAN_TASAM_FIXED_NATIVE_POWER_PERCENT", "0") or 0)
        if args.mode == "fixed_100_native" else None,
        "dynamic_floor_ledger": env.get("GREENRAN_TASAM_DYNAMIC_FLOOR_LEDGER", ""),
        "dynamic_floor_ledger_sha256": file_sha256(
            Path(env["GREENRAN_TASAM_DYNAMIC_FLOOR_LEDGER"])
        ) if env.get("GREENRAN_TASAM_DYNAMIC_FLOOR_LEDGER") else "",
        "baseline_signature": env.get("GREENRAN_TASAM_BASELINE_SIGNATURE", ""),
        "baseline_signature_sha256": file_sha256(
            Path(env["GREENRAN_TASAM_BASELINE_SIGNATURE"])
        ) if env.get("GREENRAN_TASAM_BASELINE_SIGNATURE") else "",
        "dynamic_floor_state": env.get("GREENRAN_TASAM_DYNAMIC_FLOOR_STATE", ""),
        "du_sleep_allowed": env.get("GREENRAN_TASAM_ALLOW_DU_SLEEP") == "1",
        "economic_action_contract": env.get("GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT", ""),
        "tasam_resource_headroom_ratio": 0.15 if args.mode in {
            "asgard_v2x_window90_online", "asgard_v2x_window90_energy_online",
            "asgard_v2x_window90_energy_frozen", "asgard_v2x_window90_energy_dynamic",
        } else None,
        "resource_floor_policy": env.get("GREENRAN_TASAM_RESOURCE_FLOOR_POLICY", ""),
        "reward_weight_snapshot": {
            "max_energy_weight": 0.30,
            "ewma_previous": 0.75,
            "ewma_current": 0.25,
            "healthy_exit_decisions": 3,
            "v2x_base": 0.45,
            "v2x_risk_slope": 0.35,
            "equity_base": 0.25,
            "equity_risk_slope": 0.20,
        } if contract.get("reward_contract") else {},
        "sam_mode": contract.get("sam_mode", ""),
        "l2_weight": contract.get("l2_weight", 0.0),
        "real_only_collection": bool(args.disable_app_overrides),
        "scenario_control_override_allowed": not bool(args.disable_app_overrides),
        "seed": args.seed,
        "profile": args.profile,
        "expected_stages": [
            "allowed_bootstrap", "allowed_stable", "camera_conditional", "camera_blocked",
            "vehicle_conditional", "vehicle_blocked", "app2_conditional", "app2_blocked",
            "allowed_recovery",
        ],
        "wall_time_s": args.wall_time,
        "sim_time_s": args.sim_time,
        "decision_target": int(args.decision_target or 0),
        "artifact_budget_gib": float(args.artifact_budget_gib),
        "artifact_min_free_gib": float(args.artifact_min_free_gib),
        "pairing_schedule_id": args.pairing_schedule_id,
        "pairing_schedule_file": str(args.pairing_schedule_file) if args.pairing_schedule_file else "",
        "initial_checkpoint": str(args.checkpoint),
        "checkpoint_sha256_before": checkpoint_before,
        "category_curriculum": _checkpoint_curriculum(args.checkpoint) if contract["tasam_enabled"] else {},
        "contract": contract,
        "economic_action_contract": env.get("GREENRAN_TASAM_ECONOMIC_ACTION_CONTRACT", ""),
        "native_evidence_version": env.get("GREENRAN_NATIVE_EVIDENCE_VERSION", ""),
        "native_trace_profile": env.get(
            "GREENRAN_NATIVE_TRACE_PROFILE",
            "native_minimal" if env.get("GREENRAN_NS3_NATIVE_MINIMAL_TRACES") == "1" else "full",
        ),
        "native_aggregated_evidence": env.get("GREENRAN_NS3_NATIVE_AGGREGATED_EVIDENCE") == "1",
        "native_evidence_period_ms": int(env.get("GREENRAN_NS3_NATIVE_EVIDENCE_PERIOD_MS", "100")),
        "parallel_execution": {
            "schema": "greenran.v2x.parallel_execution.v1",
            "slot_id": env.get("GREENRAN_V2X_EXECUTION_SLOT", "serial"),
            "e2_term_port": int(env.get("GREENRAN_E2_TERM_PORT", "36421")),
            "e2_xapp_port": int(env.get("GREENRAN_E2_XAPP_PORT", "36422")),
            "e2_local_port": int(env.get("GREENRAN_E2_LOCAL_PORT", "38470")),
            "app_port_offset": int(env.get("GREENRAN_PORT_OFFSET", "0")),
            "cgroup_root": env.get("GREENRAN_CGROUP_ROOT", ""),
            "slot_config_sha256": file_sha256(ROOT / "config/greenran_v2x_parallel_slots.json")
            if (ROOT / "config/greenran_v2x_parallel_slots.json").is_file() else "",
        },
        "resource_envelope": {
            "profile": arm_resource_profile,
            "requested_simulator_cpu_quota_us": build_physical_budget(
                1.0, unrestricted=True, resource_profile=arm_resource_profile
            )["groups"]["simulator"]["cpu_quota_us"],
            "requested_simulator_cpu_period_us": 100000,
            "requested_simulator_cpu_count": build_physical_budget(
                1.0, unrestricted=True, resource_profile=arm_resource_profile
            )["groups"]["simulator"]["cpu_quota_us"] // 100000,
            "requested_simulator_memory_high_bytes": build_physical_budget(
                1.0, unrestricted=True, resource_profile=arm_resource_profile
            )["groups"]["simulator"]["memory_high_bytes"],
            "control_groups_standard": True,
        },
        "cgroup_backend": env.get("GREENRAN_CGROUP_BACKEND", "delegated_v2"),
        "systemd_user_scope_preflight": scope_preflight,
        "performance_min_rtf": float(args.performance_min_rtf),
        "ns3_binary": env.get("GREENRAN_NS3_BIN", ""),
        "ns3_binary_sha256": (
            file_sha256(Path(env["GREENRAN_NS3_BIN"]))
            if Path(env.get("GREENRAN_NS3_BIN", "")).is_file()
            else ""
        ),
        "build_provenance": (
            build_provenance(Path(env["GREENRAN_NS3_BIN"]))
            if (
                args.energy_enabled and args.profile.startswith("tasam_training_balanced_v6")
                and Path(env.get("GREENRAN_NS3_BIN", "")).is_file()
            )
            else {}
        ),
        "e2_control_enabled": env.get("GREENRAN_NS3_E2_CONTROL_ENABLED") == "1",
        "e2_file_logging_enabled": env.get("GREENRAN_NS3_ENABLE_E2_FILE_LOGGING") == "true",
        "vehicle_profile_manifest": env.get("GREENRAN_VEHICLE_PROFILE_MANIFEST", ""),
        "vehicle_packet_interval_us": int(env["GREENRAN_NS3_VEHICLE_PACKET_INTERVAL_US"])
        if env.get("GREENRAN_NS3_VEHICLE_PACKET_INTERVAL_US") else None,
        "energy_model": {
            "path": str(energy_calibration["calibration_path"]),
            "schema": str(energy_calibration.get("schema", "")),
            "version": str(energy_calibration.get("calibration_version", "")),
            "reference_source": str(energy_calibration.get("energy_reference_source", "")),
            "absolute_scale_valid": bool(energy_calibration.get("absolute_scale_valid", False)),
            "physical_wattmeter": bool(energy_calibration.get("physical_wattmeter_available", False)),
        },
        "protected_paths": list(PROTECTED_MARKERS),
        "status": "planned" if args.dry_run else "starting",
        "created_at": int(time.time()),
    }
    _write_json(args.run_dir / "arm_manifest.json", manifest)
    if args.dry_run:
        print(json.dumps({"manifest": str(args.run_dir / "arm_manifest.json"), "env": {key: env[key] for key in sorted(env) if key.startswith("GREENRAN_") or key == "NS_GLOBAL_VALUE"}, "controller": _controller_command(args) if contract["controller_enabled"] else None}, indent=2, ensure_ascii=False))
        return 0

    cgroup_requested = env.get("GREENRAN_CGROUP_ENFORCE", "0").strip().lower() in {"1", "true", "yes", "on"}
    cgroup_enforced = False
    cgroup_note = "disabled_by_smoke_policy"
    if cgroup_requested:
        try:
            resource_profile = arm_resource_profile
            budget = build_physical_budget(
                1.0,
                unrestricted=(resource_profile in {"baseline_max_v1", PARALLEL_PAIR_RESOURCE_PROFILE}),
                resource_profile=resource_profile,
            )
            cgroup_backend = env.get("GREENRAN_CGROUP_BACKEND", "delegated_v2")
            if cgroup_backend == SYSTEMD_USER_SCOPE_BACKEND:
                scope_preflight = probe_systemd_user_scope(budget["groups"]["simulator"])
                if not scope_preflight.get("valid"):
                    raise InfraBudgetError(
                        "systemd_user_scope_preflight_failed: "
                        + str(scope_preflight.get("reason") or scope_preflight.get("stderr") or "unknown")
                    )
                cgroup_enforced = True
                cgroup_note = "systemd_user_scope_per_service"
                manifest["systemd_user_scope_preflight"] = scope_preflight
            else:
                CgroupV2Controller().apply(budget)
                cgroup_enforced = True
                cgroup_note = "kernel_cgroup_v2_applied"
            manifest["resource_envelope"].update({
                "profile": resource_profile,
                "effective_limits": budget["groups"],
                "effective": True,
            })
        except InfraBudgetError as exc:
            allow_unenforced = env.get("GREENRAN_CGROUP_ALLOW_UNENFORCED", "0").strip().lower() in {"1", "true", "yes", "on"}
            if not allow_unenforced:
                manifest.update({"status": "failed", "invalid_reason": f"cgroup_setup:{exc}"})
                _write_json(args.run_dir / "arm_manifest.json", manifest)
                return 4
            env["GREENRAN_CGROUP_ENFORCE"] = "0"
            cgroup_note = f"unenforced:{exc}"
    manifest["cgroup_enforcement"] = {
        "requested": cgroup_requested,
        "active": cgroup_enforced,
        "backend": env.get("GREENRAN_CGROUP_BACKEND", "delegated_v2"),
        "note": cgroup_note,
    }
    _write_json(args.run_dir / "arm_manifest.json", manifest)

    infra_monitor = InfrastructureMonitor(args.run_dir)
    infra_stop = threading.Event()
    disk_guard_stop = threading.Event()
    disk_guard_result: dict[str, Any] = {}
    artifact_budget_bytes = int(
        max(0.0, float(env.get("GREENRAN_ARTIFACT_BUDGET_GIB", "4"))) * (1024 ** 3)
    )
    artifact_min_free_gib = float(env.get("GREENRAN_ARTIFACT_MIN_FREE_GIB", str(minimum_free)))

    def monitor_infrastructure() -> None:
        last_disk_check = 0.0
        last_artifact_prune = 0.0
        while not infra_stop.is_set():
            infra_monitor.sample()
            infra_monitor.write()
            now = time.monotonic()
            if now - last_disk_check >= 2.0 and not disk_guard_result:
                last_disk_check = now
                free_gib = _free_gib(args.run_dir.parent)
                used_bytes = _directory_size_bytes(args.run_dir)
                if (
                    artifact_budget_bytes > 0
                    and used_bytes >= int(artifact_budget_bytes * 0.75)
                    and now - last_artifact_prune >= 30.0
                ):
                    state_path = args.run_dir / "online_state.json"
                    state = _read_json(state_path)
                    if isinstance(state, dict):
                        try:
                            retention = prune_candidate_artifacts(args, state)
                            state["last_artifact_prune"] = retention
                            _write_json(state_path, state)
                            last_artifact_prune = now
                        except (OSError, ValueError, TypeError) as exc:
                            _write_json(args.run_dir / "artifact_prune_error.json", {"error": str(exc)})
                if free_gib < artifact_min_free_gib or (
                    artifact_budget_bytes > 0 and used_bytes > artifact_budget_bytes
                ):
                    reason = (
                        "free_space_below_floor"
                        if free_gib < artifact_min_free_gib
                        else "artifact_budget_exceeded"
                    )
                    disk_guard_result.update({
                        "reason": reason,
                        "free_gib": round(free_gib, 3),
                        "min_free_gib": artifact_min_free_gib,
                        "artifact_bytes": used_bytes,
                        "artifact_budget_bytes": artifact_budget_bytes,
                        "requested_at": time.time(),
                    })
                    _write_json(args.run_dir / "disk_budget_stop.json", disk_guard_result)
                    _write_decision_target_request(
                        args.run_dir,
                        int(args.decision_target or 0),
                        source="disk_budget_guard",
                        reason="disk_budget_guard",
                    )
                    break
            infra_stop.wait(1.0)

    infra_thread = threading.Thread(target=monitor_infrastructure, name="greenran-infra", daemon=True)
    infra_thread.start()

    controller: subprocess.Popen[Any] | None = None
    decision_watcher: subprocess.Popen[Any] | None = None
    decision_watcher_exit_code: int | None = None
    decision_target_thread: threading.Thread | None = None
    decision_target_stop: threading.Event | None = None
    decision_target_result: dict[str, Any] = {}
    native_actuation_thread: threading.Thread | None = None
    native_actuation_stop: threading.Event | None = None
    native_actuation_result: dict[str, Any] = {}
    sleep_calibration_thread: threading.Thread | None = None
    sleep_calibration_stop: threading.Event | None = None
    sleep_calibration_result: dict[str, Any] = {"status": "not_started"}
    wall_status = 0
    wall_runner: subprocess.Popen[Any] | None = None
    arm_started_monotonic = time.monotonic()
    controller_log = (args.run_dir / "online_controller.log").open("w", encoding="utf-8")
    try:
        if contract["controller_enabled"]:
            controller = subprocess.Popen(
                _controller_command(args),
                cwd=ROOT,
                env=env,
                stdout=controller_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        with (args.run_dir / "collection_runtime.log").open("w", encoding="utf-8") as log:
            wall_runner = subprocess.Popen(
                ["bash", str(WALL_RUNNER)], cwd=ROOT, env=env,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
            if args.mode == "native_sleep_calibration":
                sleep_calibration_stop = threading.Event()
                sleep_calibration_thread = threading.Thread(
                    target=_parent_native_sleep_calibration,
                    args=(
                        args.run_dir,
                        Path(env["GREENRAN_SOCKET_DIR"]),
                        sleep_calibration_stop,
                        sleep_calibration_result,
                    ),
                    name="greenran-native-sleep-calibration",
                    daemon=True,
                )
                sleep_calibration_thread.start()
            if int(args.decision_target or 0) > 0:
                watcher_log = (args.run_dir / "decision_target.log").open("w", encoding="utf-8")
                try:
                    # Keep the historical state-scoped subprocess watcher, but
                    # also monitor the append-only decision journal in this
                    # parent.  The parent fallback guarantees that a stuck or
                    # prematurely terminated child cannot leave the wall
                    # supervisor running until the full wall-clock limit.
                    decision_target_stop = threading.Event()
                    decision_target_thread = threading.Thread(
                        target=_parent_decision_target_watch,
                        args=(args.run_dir, int(args.decision_target), decision_target_stop, decision_target_result),
                        kwargs={
                            "min_target": int(getattr(args, "min_decision_target", 0) or 0),
                            "stop_after_promotion": bool(getattr(args, "stop_after_promotion", False)),
                        },
                        name="greenran-decision-target-parent",
                        daemon=True,
                    )
                    decision_target_thread.start()
                    if args.mode == "combined_actuation_smoke":
                        native_actuation_stop = threading.Event()
                        native_actuation_thread = threading.Thread(
                            target=_parent_native_actuation_watch,
                            args=(args.run_dir, native_actuation_stop, native_actuation_result),
                            name="greenran-native-actuation-watchdog",
                            daemon=True,
                        )
                        native_actuation_thread.start()
                    watcher_env = dict(env)
                    watcher_env["PYTHONUNBUFFERED"] = "1"
                    decision_watcher = subprocess.Popen(
                        _decision_target_watcher_command(args), cwd=ROOT, env=watcher_env,
                        stdout=watcher_log, stderr=subprocess.STDOUT, start_new_session=True,
                    )
                    (args.run_dir / "decision_target_supervisor.pid").write_text(
                        f"{decision_watcher.pid}\n", encoding="ascii"
                    )
                    wall_status = int(wall_runner.wait())
                    try:
                        decision_watcher_exit_code = int(decision_watcher.wait(timeout=15.0))
                    except subprocess.TimeoutExpired:
                        _terminate_group(decision_watcher)
                        decision_watcher_exit_code = decision_watcher.returncode
                finally:
                    watcher_log.close()
            else:
                wall_status = int(wall_runner.wait())
    finally:
        if decision_target_stop is not None:
            decision_target_stop.set()
        if decision_target_thread is not None:
            decision_target_thread.join(timeout=2.0)
        if native_actuation_stop is not None:
            native_actuation_stop.set()
        if native_actuation_thread is not None:
            native_actuation_thread.join(timeout=2.0)
        if sleep_calibration_stop is not None:
            sleep_calibration_stop.set()
        if sleep_calibration_thread is not None:
            # The calibration transaction may take up to its own internal
            # deadline; a graceful drain gives the ledger its evidence file.
            sleep_calibration_thread.join(timeout=30.0)
        _terminate_group(decision_watcher)
        # Saída anômala (ex.: SIGTERM do gate antes do wait() retornar):
        # encerra também o wall runner e todo o seu grupo (ns-3/RIC), que
        # caso contrário sobreviveria ao braço.  No fluxo normal o wait()
        # já retornou (poll() != None) e este é no-op.
        _terminate_group(wall_runner)
        _terminate_group(controller)
        controller_log.close()
        infra_stop.set()
        disk_guard_stop.set()
        infra_thread.join(timeout=3.0)
        infra_monitor.sample()
        infra_monitor.write()
    feedback_status = _read_json(args.run_dir / "feedback_integrity.json")
    feedback_drained = feedback_status.get("feedback_drained")
    feedback_valid = (
        feedback_drained is True
        if args.mode == "combined_actuation_smoke"
        else feedback_drained is not False
    )
    checkpoint_after = checkpoint_fingerprint(args.checkpoint) if contract["tasam_enabled"] else ""
    checkpoint_frozen = (
        not contract["frozen_checkpoint"] or
        (bool(checkpoint_before) and checkpoint_after == checkpoint_before)
    )
    performance_evidence = _simulation_performance_evidence(
        args.run_dir,
        time.monotonic() - arm_started_monotonic,
        min_rtf=args.performance_min_rtf,
        # The independent performance benchmark enforces the 5 s warm-up
        # window.  The actuation smoke is intentionally allowed to stop as
        # soon as one native, three-DU confirmation plus its PDCP feedback is
        # available; requiring 5 s here would turn a valid bounded proof into
        # a false performance failure.
        minimum_sim_time_s=(
            5.0
            if (
                args.native_fidelity
                and not args.smoke
                and args.mode != "combined_actuation_smoke"
            )
            else 0.0
        ),
    )
    stop_request = _read_json(args.run_dir / "decision_target_stop.json")
    stop_reason = str(stop_request.get("reason") or "")
    stop_source = str(stop_request.get("source") or "")
    target_reached = False
    if int(args.decision_target or 0) > 0:
        decisions_path = args.run_dir / "rapp_decisions.jsonl"
        try:
            if decisions_path.is_file():
                with decisions_path.open("r", encoding="utf-8") as handle:
                    target_reached = sum(1 for _ in handle) >= int(args.decision_target)
        except OSError:
            target_reached = False
    allowed_stop_sources = {
        "stop_on_decision_target",
        "arm_parent_watchdog",
        "arm_parent_promotion_watchdog",
    }
    cooperative_target_stop = bool(
        target_reached
        and stop_reason == "decision_target_reached"
        and stop_source in allowed_stop_sources
    )
    promotion_stop_valid = bool(
        bool(getattr(args, "stop_after_promotion", False))
        and stop_reason == "candidate_promoted_after_minimum"
        and stop_source == "arm_parent_promotion_watchdog"
        and int(stop_request.get("observed_decisions", 0) or 0)
        >= int(getattr(args, "min_decision_target", 0) or 0)
        and bool(_read_json(args.run_dir / "online_state.json").get("candidate_promoted", False))
    )
    native_actuation_stop_valid = bool(
        args.mode == "combined_actuation_smoke"
        and stop_reason == "native_actuation_confirmed"
        and stop_source == "arm_parent_actuation_watchdog"
        and native_actuation_result.get("requested") is True
        and native_actuation_result.get("feedback", {}).get("valid") is True
    )
    # A v9_fidelity benchmark/smoke is a finite single-run ns-3 execution.
    # Its wall wrapper is intentionally terminated after the ns-3 supervisor
    # exits cleanly, so SIGTERM/143 from that wrapper is expected even though
    # no decision-target watcher requested it.  The terminal performance
    # evidence and the absence of an active ns-3 supervisor are the proof of
    # this shutdown; do not accept an arbitrary 143.
    finite_simulation_completion = bool(
        args.native_fidelity
        and performance_evidence.get("valid")
        # A performance sample after five seconds is enough to validate RTF,
        # but never enough to claim that a 120 s/600 s simulation completed.
        and float(performance_evidence.get("sim_time_observed_s", 0.0) or 0.0)
        # The native performance recorder may report the terminal sample one
        # scheduler tick below the requested value (e.g. 44.9 for a 45 s
        # arm).  Use the same 250 ms completion tolerance as the feasibility
        # evaluator; this does not relax any scored PDCP window or SLA gate.
        >= float(args.sim_time) - 0.25
        and wall_status in {143, -15, 15}
        and not (args.run_dir / "ns3_supervisor.pid").exists()
    )
    # The wall supervisor itself normally exits cleanly (0) after forwarding
    # the cooperative request; ns-3 is the process that records 143.  Accept
    # either side of that controlled shutdown, but only with a validated
    # target or promotion request.
    expected_target_termination = bool(
        wall_status in {0, 143, -15, 15}
        and (
            cooperative_target_stop
            or promotion_stop_valid
            or native_actuation_stop_valid
            or finite_simulation_completion
        )
    )
    _reconcile_wall_status(
        args.run_dir,
        wall_status,
        stop_reason,
        completion_verified=expected_target_termination,
    )
    externally_interrupted = bool(
        wall_status in {143, -15, 15} and not expected_target_termination
    )
    invalid_reason = ""
    if disk_guard_result:
        invalid_reason = f"disk_budget_guard:{disk_guard_result.get('reason', 'unknown')}"
    else:
        ns3_failure = _ns3_failure_detected(args.run_dir)
        # The target watcher deliberately terminates ns-3 after the requested
        # number of decisions.  A promotion stop is also valid, but only when
        # its explicit contract was verified above. Every other non-zero ns-3
        # exit remains a hard failure.
        if ns3_failure and not (
            expected_target_termination and ns3_failure == "ns3_exit_code:143"
        ):
            invalid_reason = ns3_failure
    if (
        args.mode in {
            "combined_shadow", "combined_actuation_smoke", "combined_online",
            "sac_l2_online", "sac_l2_frozen", "tasam_v2x_online", "tasam_v2x_frozen",
            "asgard_v2x_window90_online", "asgard_v2x_window90_energy_online",
            "asgard_v2x_window90_energy_frozen", "asgard_v2x_window90_energy_dynamic",
        }
        and not performance_evidence.get("valid")
        and not invalid_reason
    ):
        invalid_reason = "simulation_performance_infeasible"
    if (
        not invalid_reason
        and not feedback_valid
        and args.mode != "combined_actuation_smoke"
    ):
        invalid_reason = "missing_real_pdcp_feedback"
    elif not invalid_reason and not checkpoint_frozen:
        invalid_reason = "frozen_checkpoint_mutated"
    online_adaptation_state = _read_json(args.run_dir / "online_state.json")
    dynamic_floor_state = _read_json(args.run_dir / "dynamic_floor_state.json")
    online_updates_completed = int(
        online_adaptation_state.get("updates_completed", 0) or 0
    )
    active_online_checkpoint = str(
        online_adaptation_state.get("active_checkpoint")
        or online_adaptation_state.get("candidate_checkpoint")
        or online_adaptation_state.get("last_good_checkpoint")
        or ""
    )
    active_online_checkpoint_sha256 = (
        checkpoint_fingerprint(Path(active_online_checkpoint))
        if active_online_checkpoint and Path(active_online_checkpoint).is_dir()
        else ""
    )
    recorded_checkpoint_after = (
        active_online_checkpoint_sha256
        if args.mode == "asgard_v2x_window90_energy_dynamic"
        and active_online_checkpoint_sha256
        else checkpoint_after
    )
    if args.mode == "asgard_v2x_window90_energy_dynamic" and not invalid_reason:
        if online_updates_completed < 1:
            invalid_reason = "dynamic_asgard_online_update_missing"
        elif int(dynamic_floor_state.get("actor_influenced_decisions", 0) or 0) < 1:
            invalid_reason = "dynamic_asgard_authority_influence_missing"
        else:
            floor_values = (
                dynamic_floor_state.get("floor_percent_by_cell") or {}
            ).values()
            try:
                below_calibrated_floor = any(float(value) < 25.0 for value in floor_values)
            except (TypeError, ValueError):
                below_calibrated_floor = True
            if below_calibrated_floor:
                invalid_reason = "dynamic_floor_below_calibrated_25_percent"
    native_actuation = {}
    native_actuation_feedback = {}
    if args.mode == "combined_actuation_smoke":
        native_actuation = _native_actuation_evidence(
            args.run_dir,
            strict_v5=bool(args.native_fidelity),
        )
        native_actuation_feedback = _native_actuation_feedback_ready(
            args.run_dir, native_actuation
        )
        # The bounded smoke only promises one selected native action and its
        # subsequent real-PDCP window.  Other shadow decisions may still be
        # pending when the watchdog stops the run and are not evidence of a
        # failed actuation. Keep the broader drain result visible, but accept
        # the smoke when its required scoped feedback is complete.
        if native_actuation_feedback.get("valid"):
            feedback_valid = True
        if not native_actuation.get("valid") and not invalid_reason:
            invalid_reason = str(
                native_actuation.get("invalid_reason")
                or "native_actuation_confirmation_missing"
            )
        elif not native_actuation_feedback.get("valid") and not invalid_reason:
            invalid_reason = str(
                native_actuation_feedback.get("reason")
                or "missing_real_pdcp_feedback"
            )
    wall_execution_valid = wall_status == 0 or expected_target_termination
    manifest.update(
        {
            "status": (
                "cancelled" if externally_interrupted else
                ("finished" if wall_execution_valid and feedback_valid and checkpoint_frozen and not disk_guard_result and not invalid_reason else "failed")
            ),
            "wall_runner_exit_code": wall_status,
            "target_reached": target_reached,
            "expected_target_termination": expected_target_termination,
            "finite_simulation_completion": finite_simulation_completion,
            "stop_reason": stop_reason,
            "stop_source": stop_source,
            "promotion_stop_valid": promotion_stop_valid,
            "actuation_stop_valid": native_actuation_stop_valid,
            "controller_exit_code": controller.returncode if controller is not None else None,
            "decision_target_watcher_exit_code": decision_watcher_exit_code,
            "decision_target_parent_watchdog": decision_target_result,
            "native_actuation_parent_watchdog": native_actuation_result,
            "native_sleep_calibration": sleep_calibration_result,
            "feedback_drained": feedback_drained,
            "feedback_integrity_valid": feedback_valid,
            "native_actuation_feedback": native_actuation_feedback,
            "checkpoint_sha256_after": recorded_checkpoint_after,
            "checkpoint_frozen_verified": checkpoint_frozen,
            "online_updates_completed": online_updates_completed,
            "active_online_checkpoint": active_online_checkpoint,
            "active_online_checkpoint_sha256": active_online_checkpoint_sha256,
            "dynamic_floor_evidence": dynamic_floor_state,
            "invalid_reason": invalid_reason,
            "native_actuation_evidence": native_actuation,
            "simulation_performance": performance_evidence,
            "artifact_guard": {
                "budget_gib": artifact_budget_bytes / (1024 ** 3) if artifact_budget_bytes else None,
                "min_free_gib": artifact_min_free_gib,
                "triggered": bool(disk_guard_result),
                **disk_guard_result,
            },
            "finished_at": int(time.time()),
        }
    )
    _write_json(args.run_dir / "arm_manifest.json", manifest)
    online_status_path = args.run_dir / "online_status.json"
    online_status = _read_json(online_status_path)
    online_status.update(
        {
            "status": (
                "cancelled" if externally_interrupted else
                ("finished" if wall_execution_valid and feedback_valid and not invalid_reason else "invalid")
            ),
            "stopped_at": int(time.time()),
            "finished_at": int(time.time()),
            "shutdown_reason": (
                stop_reason
                if expected_target_termination and stop_reason
                else ("external_termination" if externally_interrupted else
                      ("finite_simulation_completed" if finite_simulation_completion else
                      ("wall_time_complete" if wall_status == 0 else "wall_runner_failed"))
                )
            ),
            "feedback_drained": feedback_drained,
            "simulation_performance": performance_evidence,
        }
    )
    _write_json(online_status_path, online_status)
    online_state_path = args.run_dir / "online_state.json"
    online_state = _read_json(online_state_path)
    if online_state:
        online_state.update({
            "status": (
                "cancelled" if externally_interrupted else
                ("finished" if wall_execution_valid and feedback_valid else "invalid")
            ),
            "finished_at": int(time.time()),
            "shutdown_reason": online_status["shutdown_reason"],
            "simulation_performance": performance_evidence,
        })
        _write_json(online_state_path, online_state)
    if not wall_execution_valid:
        return wall_status
    if disk_guard_result:
        return 6
    if invalid_reason.startswith("ns3_"):
        return 7
    if invalid_reason == "native_actuation_confirmation_missing":
        return 8
    if invalid_reason == "simulation_performance_infeasible":
        return 9
    if not feedback_valid:
        return 3
    return 0 if checkpoint_frozen else 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(MODES), required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--wall-time", type=float, default=600.0)
    parser.add_argument("--sim-time", type=float, default=600.0)
    parser.add_argument(
        "--native-fidelity", action="store_true",
        help="usa evidência nativa agregada v5 e perfil v9_fidelity",
    )
    parser.add_argument(
        "--smoke", action="store_true",
        help="aceita execução curta de smoke sem exigir a janela de desempenho de 5 s",
    )
    parser.add_argument(
        "--performance-min-rtf", type=float, default=0.10,
        help="RTF mínimo; v9_fidelity usa 0.016",
    )
    parser.add_argument(
        "--decision-target",
        type=int,
        default=0,
        help="encerra o loop do rApp após este número de decisões; 0 mantém somente o wall-clock",
    )
    parser.add_argument(
        "--min-decision-target", type=int, default=0,
        help="mínimo de decisões antes de permitir parada antecipada após promoção",
    )
    parser.add_argument(
        "--stop-after-promotion", action="store_true",
        help="para cooperativamente após o mínimo quando um candidato for promovido",
    )
    parser.add_argument("--pairing-schedule-id", default="")
    parser.add_argument("--pairing-schedule-file", type=Path, default=None)
    parser.add_argument(
        "--execution-slot", choices=("slot-a", "slot-b"), default=None,
        help="reserva um slot SCTP/cgroup exclusivo para execução paralela; omitido mantém serial",
    )
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument(
        "--binary", type=Path, default=None,
        help="binário ns-3 explícito para este arm; quando omitido usa GREENRAN_NS3_BIN/default",
    )
    parser.add_argument("--energy-calibration", type=Path, default=None)
    parser.add_argument("--experience-bank", type=Path, default=None)
    parser.add_argument(
        "--recent-experience-bank", type=Path, default=None,
        help="banco privado de transições recentes; obrigatório no replay V2X 80/20",
    )
    parser.add_argument(
        "--disable-app-overrides", action="store_true",
        help="coleta rApp-only: preserva rótulos de estágio, mas bloqueia overrides sintéticos",
    )
    parser.add_argument("--control-gate", type=Path, default=None, help="gate aprovado para o braço combined")
    parser.add_argument("--control-fraction", type=float, default=0.10, help="fração canary do assistant_only_control")
    parser.add_argument("--min-free-gib", type=float, default=10.0)
    parser.add_argument(
        "--artifact-budget-gib", type=float, default=4.0,
        help="limite de artefatos da rodada; 0 desativa somente este limite",
    )
    parser.add_argument(
        "--artifact-min-free-gib", type=float, default=10.0,
        help="piso de espaço livre que encerra a rodada de forma cooperativa",
    )
    parser.add_argument("--min-new-snapshots", type=int, default=60)
    parser.add_argument("--min-trainable-transitions", type=int, default=180)
    parser.add_argument("--replay-rows", type=int, default=600)
    parser.add_argument("--epochs-per-update", type=int, default=2)
    parser.add_argument("--prioritize-category-errors", action="store_true")
    parser.add_argument("--category-error-repeat", type=int, default=1)
    parser.add_argument("--category-loss-weight", type=float, default=0.5)
    parser.add_argument("--category-head-hidden-dim", type=int, default=64)
    parser.add_argument("--controller-poll-seconds", type=float, default=10.0)
    parser.add_argument("--shadow-min-decisions", type=int, default=30)
    parser.add_argument("--stage-window-decisions", type=int, default=30)
    parser.add_argument("--max-rollout-fraction", type=float, default=1.0)
    parser.add_argument("--min-economic-transitions", type=int, default=0)
    parser.add_argument("--economic-update-min-transitions", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--vehicle-profile-manifest", type=Path, default=None)
    parser.add_argument(
        "--energy-enabled", action="store_true",
        help="habilita somente a subfase E2 de energia; o gate V2X estrito mantém isto desligado",
    )
    parser.add_argument(
        "--energy-staircase", action="store_true",
        help="habilita a escada segura V2X por DU e o candidato explícito de sono",
    )
    parser.add_argument(
        "--safe-power-floor-ledger", type=Path, default=None,
        help="ledger nativo validado de piso seguro por DU para a escada energética",
    )
    parser.add_argument(
        "--dynamic-floor-ledger", type=Path, default=None,
        help="ledger v2 imutável do piso dinâmico da campanha",
    )
    parser.add_argument(
        "--baseline-signature", type=Path, default=None,
        help="relatório strict-pair r26 que fornece a assinatura SLA diferencial",
    )
    parser.add_argument(
        "--fixed-native-power-percent", type=int, default=100,
        help="potência nativa fixa (%%, passo 5, 25..100) do braço fixed_100_native",
    )
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
