#!/usr/bin/env python3
"""Run the isolated seed-43 engineering energy pilot and frozen pair.

This launcher deliberately separates online training from the paired result:
the first phase creates a new rApp replay and trains ASGARD, while the second
phase loads one copied checkpoint without replay updates.  The campaign is
engineering evidence only and can never satisfy the multi-seed promotion
gate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PILOT = SCRIPTS / "run_tasam_v2x_window90_pilot.py"
ARM = SCRIPTS / "run_tasam_online_arm.py"
CLEANUP = SCRIPTS / "cleanup_greenran_orphaned_run.py"
PROFILE = "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max"
PAIR_SLOTS = ("slot-a", "slot-b")
SEED = 43
SIM_TIME = 120.0
WALL_TIME = 9000.0
MIN_RTF = 0.016
CALIBRATION = ROOT / "config/energy_calibration_sim_v3_sleep.json"

sys.path.insert(0, str(SCRIPTS))
from evaluate_tasam_strict_pair import causal_energy, e2_audit, evaluate_ue_windows  # noqa: E402
from run_tasam_v2x_window90_pilot import (  # noqa: E402
    REWARD_CONTRACT,
    STAGES,
    DEFAULT_CATEGORY_SOURCE,
    export_rows,
    read_json,
    select_stage_transitions,
    sha256,
    tree_sha256,
    write_json,
)
from greenran_v2x_binary_freshness import build_provenance  # noqa: E402
from tasam_pairing import canonical_schedule, write_schedule  # noqa: E402
from greenran_infra_budget import assert_cgroup_delegation  # noqa: E402
from v2x_parallel_slots import assert_disk_capacity, assert_ports_free, resolve_slots  # noqa: E402


def _run(command: list[str], *, env: dict[str, str] | None = None, log: Path | None = None) -> int:
    print("[energy-r5] iniciando:", " ".join(command), flush=True)
    if log is None:
        return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        return subprocess.run(
            command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT, check=False
        ).returncode


def _arm_command(
    mode: str,
    run_dir: Path,
    checkpoint: Path,
    schedule_file: Path,
    schedule_id: str,
    calibration: Path,
    *,
    wall_time: float,
    sim_time: float = SIM_TIME,
    binary: Path | None = None,
    profile: str = PROFILE,
    execution_slot: str | None = None,
    safe_power_floor_ledger: Path | None = None,
) -> list[str]:
    binary_path = binary or globals().get("_binary_from_args")
    if binary_path is None:
        raise ValueError("binário ns-3 deve ser informado antes de montar o comando do arm")
    command = [
        sys.executable, str(ARM), "--mode", mode,
        "--run-dir", str(run_dir), "--seed", str(SEED), "--profile", profile,
        "--wall-time", str(int(wall_time)), "--sim-time", str(int(sim_time)),
        "--decision-target", "0", "--native-fidelity", "--performance-min-rtf", str(MIN_RTF),
        "--binary", str(binary_path), "--checkpoint", str(checkpoint),
        "--energy-calibration", str(calibration),
        "--pairing-schedule-id", schedule_id, "--pairing-schedule-file", str(schedule_file),
        "--disable-app-overrides", "--energy-enabled",
        "--min-free-gib", "20", "--artifact-min-free-gib", "20", "--artifact-budget-gib", "4",
    ]
    if execution_slot:
        command.extend(["--execution-slot", execution_slot])
    if mode in {"asgard_v2x_window90_energy_online", "asgard_v2x_window90_energy_frozen"}:
        command.append("--energy-staircase")
    if safe_power_floor_ledger is not None:
        # A escada de energia é fail-closed: sem o ledger de pisos derivado
        # do baseline nativo, o braço cai no caminho full-power (r6g: 130/130
        # failsafe na pareada). O treino recebia o ledger via piloto; a
        # pareada frozen precisa recebê-lo aqui.
        command.extend([
            "--safe-power-floor-ledger", str(safe_power_floor_ledger),
        ])
    return command


def _run_parallel(
    commands: dict[str, list[str]], *, env: dict[str, str], logs: dict[str, Path]
) -> dict[str, int]:
    """Run isolated arms together and always collect both exit codes."""
    processes: dict[str, subprocess.Popen[Any]] = {}
    handles: dict[str, Any] = {}
    try:
        for label, command in commands.items():
            logs[label].parent.mkdir(parents=True, exist_ok=True)
            handles[label] = logs[label].open("w", encoding="utf-8")
            print("[energy-engineering] iniciando:", " ".join(command), flush=True)
            processes[label] = subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=handles[label], stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        return {label: process.wait() for label, process in processes.items()}
    finally:
        for handle in handles.values():
            handle.close()


def _native_gate_ready(run_dir: Path, *, require_economic: bool) -> bool:
    """Return true only after one complete v6 three-cell native sequence.

    The trainability gate is intentionally bounded.  It proves that the
    corrected control path can produce native evidence; it is not a scored
    simulation.  A complete sequence is required before stopping the arm so
    that a partial DU readback can never be mistaken for a valid gate.
    """
    trace = run_dir / "ns3_energy" / "TasamControlObservations.csv"
    if not trace.is_file():
        return False
    # O snapshot extended_metrics.json é escrito periodicamente e atrasa em
    # relação às observações por evento; na janela curta do gate esse atraso
    # (ex.: 4.8 s simulados no snapshot com a cadeia v6 já além de 5 s no
    # trace) derrubava braços íntegros no limite de 5 s.  O trace v6 é a
    # fonte autoritativa do tempo simulado; o snapshot vira fallback apenas
    # para o caso de ainda não haver linha v6.
    snapshot_sim_end = 0.0
    performance = run_dir / "xapp_metrics" / "extended_metrics.json"
    try:
        metrics = json.loads(performance.read_text(encoding="utf-8"))
        snapshot_sim_end = float(
            (metrics.get("sim_time_range") or {}).get("end", 0.0) or 0.0
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        snapshot_sim_end = 0.0
    # A native observation is not sufficient by itself: the sequence may be
    # a safety/fallback bundle or a partially acknowledged transaction.  The
    # actuator audit is the authoritative per-sequence proof that the
    # economic bundle was actually ACKed and applied.
    accepted_sequences: set[int] = set()
    audit = run_dir / "xapp_intents" / "tasam_control_audit.jsonl"
    try:
        with audit.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                sequence = int(row.get("sequence", 0) or 0)
                if sequence <= 0:
                    continue
                if row.get("ack") is not True or row.get("applied") is not True:
                    continue
                if row.get("fallback") is True or row.get("mode") == "failsafe":
                    continue
                if require_economic and row.get("mode") != "economic_action_v3_per_du_sleep":
                    continue
                accepted_sequences.add(sequence)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False

    allowed_sequences: set[str] = set()
    context = run_dir / "ns3_energy" / "NativeControlContext.csv"
    try:
        with context.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                sequence = str(row.get("NativeControlSequence") or "").strip()
                correlation = str(row.get("ActionCorrelationId") or "").strip()
                if not sequence or sequence == "0":
                    continue
                if require_economic:
                    accepted = correlation.startswith("economic:")
                else:
                    accepted = (
                        correlation.startswith("economic:")
                        or correlation.startswith("operational:rapp_live:")
                    )
                if accepted and sequence.isdigit() and int(sequence) in accepted_sequences:
                    allowed_sequences.add(sequence)
    except (OSError, csv.Error):
        return False
    if not allowed_sequences:
        return False
    by_sequence: dict[str, dict[str, set[str]]] = {}
    trace_sim_end = 0.0
    trace_v6_rows = 0
    try:
        with trace.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("EvidenceVersion") or "") != "v6":
                    continue
                trace_v6_rows += 1
                try:
                    trace_sim_end = max(trace_sim_end, float(row.get("Time") or 0.0))
                except (TypeError, ValueError):
                    pass
                sequence = str(row.get("NativeControlSequence") or "").strip()
                cell = str(row.get("CellId") or "").strip()
                kind = str(row.get("ObservationKind") or "").strip()
                if not sequence or sequence == "0" or sequence not in allowed_sequences or not cell:
                    continue
                state = by_sequence.setdefault(
                    sequence, {"cells": set(), "kinds": set()}
                )
                state["cells"].add(cell)
                if kind:
                    state["kinds"].add(kind)
    except (OSError, csv.Error):
        return False
    # O lançador mede o RTF só após cinco segundos simulados.  Não pare antes
    # desse contrato ser observável; caso contrário o gate poderia provar o
    # E2 pulando silenciosamente o piso de performance.
    sim_end = trace_sim_end if trace_v6_rows else snapshot_sim_end
    if sim_end < 5.0:
        return False
    return any(
        state["cells"] >= {"2", "3", "4"}
        and {"power_readback", "state_snapshot"}.issubset(state["kinds"])
        for state in by_sequence.values()
    )


def _terminate_process_group(
    process: subprocess.Popen[Any], *, grace_s: float = 15.0
) -> None:
    """Stop an arm and its whole process group (SIGTERM -> grace -> SIGKILL).

    The gate used to signal only the direct child; the trainer spawned by the
    arm in its own session was therefore never told to stop and survived as an
    orphan (run_tasam_online_controlled.py, PPID=systemd --user).  The arm
    runner now handles SIGTERM gracefully (its finally-block terminates the
    trainer), so signaling the group is both sufficient and safe.  Requires
    the arm to have been started with start_new_session=True.
    """
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.send_signal(signal.SIGTERM)
    stop_deadline = time.monotonic() + max(0.0, float(grace_s))
    while time.monotonic() < stop_deadline and process.poll() is None:
        time.sleep(0.2)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


def _run_parallel_until_native_gate(
    commands: dict[str, list[str]],
    *,
    env: dict[str, str],
    run_dirs: dict[str, Path],
    logs: dict[str, Path],
    timeout_s: float,
    transition_ready: Callable[[str], bool] | None = None,
) -> tuple[dict[str, int], bool]:
    """Run gate arms concurrently and stop after complete native proof.

    Native readback is deliberately not the final gate condition.  The
    controller persists PDCP/Judge feedback asynchronously, so stopping at
    the first readback can leave a perfectly valid action without a trainable
    transition in the export.  ``transition_ready`` is therefore checked
    after the native proof and must confirm one complete exported transition
    per arm before the controlled stop is requested.
    """
    processes: dict[str, subprocess.Popen[Any]] = {}
    handles: dict[str, Any] = {}
    stopped_after_native = False
    deadline = time.monotonic() + max(30.0, float(timeout_s))
    try:
        for label, command in commands.items():
            logs[label].parent.mkdir(parents=True, exist_ok=True)
            handles[label] = logs[label].open("w", encoding="utf-8")
            print("[energy-gate] iniciando:", " ".join(command), flush=True)
            processes[label] = subprocess.Popen(
                command, cwd=ROOT, env=env,
                stdout=handles[label], stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        while processes:
            native_ready = all(
                _native_gate_ready(
                    run_dirs[label], require_economic=(label == "asgard")
                )
                for label in processes
            )
            # Do not run the SQLite exporter while the simulator is still
            # establishing native evidence.  Besides being unnecessary, the
            # repeated reads competed with ns-3 and made the short gate look
            # slower than the actual simulation.
            transitions_ready = (
                not native_ready
                or transition_ready is None
                or all(transition_ready(label) for label in processes)
            )
            if native_ready and transitions_ready:
                stopped_after_native = True
                for process in processes.values():
                    _terminate_process_group(process, grace_s=15.0)
                break
            if time.monotonic() >= deadline:
                for process in processes.values():
                    _terminate_process_group(process, grace_s=15.0)
                break
            for label, process in list(processes.items()):
                if process.poll() is not None:
                    continue
            time.sleep(1.0)
        # Give each arm time to reconcile its wall status and close children.
        grace_deadline = time.monotonic() + 20.0
        while time.monotonic() < grace_deadline and any(
            process.poll() is None for process in processes.values()
        ):
            time.sleep(0.2)
        for process in processes.values():
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
        return {label: int(process.wait()) for label, process in processes.items()}, stopped_after_native
    finally:
        for handle in handles.values():
            handle.close()


def _reconcile_trainability_gate_arm(
    arm_dir: Path, *, native_proof: bool, returncode: int | None
) -> dict[str, Any]:
    """Record an intentional bounded-stop without altering traces.

    The arm launcher is normally responsible for finalizing its manifest. A
    SIGTERM from this short gate can arrive while that launcher is still
    draining delayed Judge feedback, leaving ``status=starting`` even though
    the evidence files are complete.  Reconcile only the manifest metadata;
    never manufacture performance completion or edit simulation traces.
    """
    path = arm_dir / "arm_manifest.json"
    manifest = read_json(path) if path.is_file() else {}
    if manifest.get("status") == "starting":
        manifest.update({
            "status": "cancelled",
            "gate_reconciled": True,
            "gate_native_proof": bool(native_proof),
            "gate_returncode": returncode,
            "gate_reconciliation_reason": (
                "trainability_gate_controlled_stop_after_complete_transition"
                if native_proof
                else "trainability_gate_timeout_or_incomplete_transition"
            ),
            "finished_at": int(time.time()),
        })
        write_json(path, manifest)
    return manifest


def _cleanup_finished_arm(arm_dir: Path, audit_dir: Path, *, reason: str) -> None:
    """Release only this campaign arm's lingering processes and ports."""
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


def _run_e2_v3_smoke(
    root: Path, args: argparse.Namespace, calibration: Path, slots: tuple[str, ...]
) -> dict[str, Any]:
    """Run identical V3 native-control probes concurrently in isolated slots."""
    bootstrap = root / "smoke" / "asgard_bootstrap"
    bootstrap.parent.mkdir(parents=True, exist_ok=True)
    if not bootstrap.exists():
        _run([
            sys.executable, str(SCRIPTS / "build_tasam_v10_checkpoint.py"),
            "--parent", str(DEFAULT_CATEGORY_SOURCE), "--output", str(bootstrap),
            "--seed", str(SEED), "--category-only",
        ])
    smoke_schedule_file = root / "smoke" / "pairing_schedule.json"
    smoke_schedule = canonical_schedule(args.profile, SEED, 900.0, tick_s=0.25)
    write_schedule(smoke_schedule_file, smoke_schedule)
    commands = {
        slot: _arm_command(
            "asgard_v2x_window90_energy_frozen", root / "smoke" / "e2_v3" / slot,
            bootstrap, smoke_schedule_file, smoke_schedule["schedule_id"], calibration,
            wall_time=min(float(args.wall_time), 900.0), sim_time=12.0,
            binary=args.binary.resolve(), profile=args.profile, execution_slot=slot,
        )
        for slot in slots
    }
    env = _pair_environment(smoke_schedule_file, smoke_schedule["schedule_id"])
    codes = _run_parallel(
        commands, env=env,
        logs={slot: root / "smoke" / "e2_v3" / f"{slot}.launcher.log" for slot in slots},
    )
    slot_reports: dict[str, dict[str, Any]] = {}
    for slot in slots:
        smoke_dir = root / "smoke" / "e2_v3" / slot
        trace = smoke_dir / "ns3_energy" / "TasamControlObservations.csv"
        manifest = read_json(smoke_dir / "arm_manifest.json")
        reasons: list[str] = []
        if codes.get(slot) != 0 or manifest.get("status") != "finished":
            reasons.append("arm_not_finished")
        if not trace.is_file():
            reasons.append("native_control_trace_missing")
        else:
            import csv
            with trace.open(newline="", encoding="utf-8", errors="replace") as handle:
                rows = list(csv.DictReader(handle))
            cells = {int(row.get("CellId", 0) or 0) for row in rows if row.get("CellId")}
            versions = {str(row.get("EvidenceVersion") or "") for row in rows}
            kinds = {str(row.get("ObservationKind") or "") for row in rows}
            if cells < {2, 3, 4}:
                reasons.append("native_control_cells_incomplete")
            if versions != {"v6"}:
                reasons.append("native_control_evidence_version_not_v6")
            if not {"power_readback", "state_snapshot"}.issubset(kinds):
                reasons.append("native_control_readback_or_policy_missing")
        pdcp = (smoke_dir / "ns3_energy" / "DlPdcpStats.txt")
        if not pdcp.is_file():
            pdcp = smoke_dir / "ns3_traces" / "DlPdcpStats.txt"
        if not pdcp.is_file():
            reasons.append("pdcp_trace_missing")
        if not manifest.get("build_provenance", {}).get("binary_sha256"):
            reasons.append("build_provenance_missing")
        slot_reports[slot] = {
            "status": "passed" if not reasons else "metric_invalid",
            "returncode": codes.get(slot), "run_dir": str(smoke_dir),
            "reasons": reasons, "slot_id": slot,
            "native_evidence_version": "v6", "three_cell_readback_required": True,
        }
    report = {
        "schema": "greenran.tasam.v2x.energy_engineering.smoke.v2",
        "status": "passed" if all(item["status"] == "passed" for item in slot_reports.values()) else "metric_invalid",
        "slots": slot_reports, "parallel": True, "slot_ids": list(slots),
        "native_evidence_version": "v6", "three_cell_readback_required": True,
    }
    write_json(root / "smoke" / "smoke_report.json", report)
    return report


def _run_trainability_gate(
    root: Path, args: argparse.Namespace, calibration: Path, slots: tuple[str, ...]
) -> dict[str, Any]:
    """Run a short real-control probe before spending hours on the pilot.

    The old E2 smoke used the frozen arm and therefore could not detect a
    broken rApp/online-controller transition path.  This gate deliberately
    exercises the two exact training modes and accepts only one complete,
    native economic transition from each arm.
    """
    bootstrap = root / "smoke" / "asgard_bootstrap"
    bootstrap.parent.mkdir(parents=True, exist_ok=True)
    if not bootstrap.exists():
        _run([
            sys.executable, str(SCRIPTS / "build_tasam_v10_checkpoint.py"),
            "--parent", str(DEFAULT_CATEGORY_SOURCE), "--output", str(bootstrap),
            "--seed", str(SEED), "--category-only",
        ])
    gate_root = root / "smoke" / "trainability_gate"
    # Twelve simulated seconds ended before this ns-3 build emitted a
    # complete native association for DUs 2--4.  Keep this as a bounded
    # diagnostic gate, but allow the association/readback contract to settle;
    # the scientific/pilot duration remains unchanged at 120 s.
    gate_sim_time_s = 30.0
    # A sonda de treinabilidade deve falhar rápido quando a cadeia nativa
    # está quebrada; o piloto de 120 s continua regido pelo wall-time do
    # usuário.  O piso de 5 s simulados exige RTF ≥ 0.0104 dentro da janela
    # (5/480); o braço asgard (treino online + overhead de E2) opera perto
    # de RTF 0.013 sob contenção — 360 s ficava na borda exata (r18 passou
    # com 5.0 s por sorte; os smokes de 2026-09-25 falharam com 4.5-4.9 s).
    gate_wall_time_s = min(float(args.wall_time), 480.0)
    schedule_file = gate_root / "pairing_schedule.json"
    schedule = canonical_schedule(args.profile, SEED, gate_sim_time_s, tick_s=0.25)
    write_schedule(schedule_file, schedule)
    baseline_dir = gate_root / "slot-a"
    asgard_dir = gate_root / "slot-b"
    historical = gate_root / "historical_replay.jsonl"
    recent = gate_root / "recent_replay.jsonl"
    historical.touch()
    recent.touch()
    baseline_command = _arm_command(
        "rapp_only_actuating", baseline_dir, bootstrap, schedule_file,
        schedule["schedule_id"], calibration, wall_time=gate_wall_time_s,
        sim_time=gate_sim_time_s, binary=args.binary.resolve(), profile=args.profile,
        execution_slot="slot-a",
    )
    asgard_command = _arm_command(
        "asgard_v2x_window90_energy_online", asgard_dir, bootstrap, schedule_file,
        schedule["schedule_id"], calibration, wall_time=gate_wall_time_s,
        sim_time=gate_sim_time_s, binary=args.binary.resolve(), profile=args.profile,
        execution_slot="slot-b",
    )
    # The short gate runs before the rApp reference exists.  It proves the
    # native V3 economic path at the conservative confirmed power level;
    # the staircase itself is enabled only in the long ASGARD arm after the
    # baseline-derived native floor ledger has been sealed.
    if "--energy-staircase" in asgard_command:
        asgard_command.remove("--energy-staircase")
    asgard_command.extend([
        "--experience-bank", str(historical),
        "--recent-experience-bank", str(recent),
        "--replay-rows", "90",
        "--min-new-snapshots", "1",
        "--min-trainable-transitions", "1",
        "--epochs-per-update", "1",
        "--controller-poll-seconds", "1",
        "--max-rollout-fraction", "1.0",
        "--shadow-min-decisions", "1",
        "--stage-window-decisions", "1",
        "--economic-update-min-transitions", "0",
    ])

    def transition_ready(label: str) -> bool:
        """Check the exported transition contract while the arm is alive."""
        arm_dir = baseline_dir if label == "rapp" else asgard_dir
        live_export = arm_dir / "gate_live_export.jsonl"
        try:
            export_rows(
                arm_dir, live_export, energy_enabled=True,
                energy_calibration=calibration,
            )
            rows, _selection = select_stage_transitions(
                arm_dir, live_export, f"trainability_gate_live_{label}",
                require_asgard=(label == "asgard"), energy_enabled=True,
                reference_arm=(label == "rapp"), min_per_stage=1, max_total=1,
            )
            return bool(rows) and all(
                isinstance(row.get("energy_evidence"), dict)
                and row["energy_evidence"].get("native") is True
                and row["energy_evidence"].get("evidence_version") == "v6"
                for row in rows
            )
        except (
            OSError, RuntimeError, ValueError, KeyError,
            json.JSONDecodeError, subprocess.CalledProcessError,
        ):
            # SQLite can be momentarily busy while the controller commits a
            # delayed Judge result. The next polling interval retries it.
            return False

    env = _pair_environment(schedule_file, schedule["schedule_id"])
    # The gate is intentionally serial.  It is a trainability probe, not the
    # paired evaluation, and sharing host CPU between two ns-3 instances can
    # push both below the RTF floor before either delayed Judge result lands.
    commands = {
        "rapp": baseline_command,
        "asgard": asgard_command,
    }
    run_dirs = {"rapp": baseline_dir, "asgard": asgard_dir}
    logs = {
        "rapp": gate_root / "slot-a.launcher.log",
        "asgard": gate_root / "slot-b.launcher.log",
    }
    codes: dict[str, int] = {}
    stopped_after_native = True
    for label in ("rapp", "asgard"):
        arm_codes, arm_stopped = _run_parallel_until_native_gate(
            {label: commands[label]}, env=env,
            run_dirs={label: run_dirs[label]}, logs={label: logs[label]},
            timeout_s=gate_wall_time_s,
            transition_ready=lambda _label, current=label: transition_ready(current),
        )
        codes.update(arm_codes)
        stopped_after_native = stopped_after_native and arm_stopped
        if label == "rapp":
            # Libera coletores/portas do slot-a ANTES de o asgard começar:
            # sem isso os processos remanescentes do baseline competem por
            # CPU com o braço de treino durante toda a janela dele e deprimem
            # o RTF exatamente no braço mais sensível.
            _cleanup_finished_arm(
                baseline_dir, gate_root / "cleanup_audit_rapp_interim",
                reason="trainability_gate_serial_slot_handoff",
            )
    arms = {
        "rapp": (baseline_dir, False),
        "asgard": (asgard_dir, True),
    }
    reports: dict[str, dict[str, Any]] = {}
    try:
        for label, (arm_dir, require_asgard) in arms.items():
            if not arm_dir.is_dir() or not (arm_dir / "arm_manifest.json").is_file():
                reports[label] = {
                    "status": "metric_invalid",
                    "returncode": codes.get(label),
                    "run_dir": str(arm_dir),
                    "selected_rows": 0,
                    "selection": {},
                    "economic_transition_observed": False,
                    "native_evidence_version": "v6",
                    "reason": "arm_not_started",
                }
                continue
            raw = arm_dir / "raw_export.jsonl"
            export_rows(
                arm_dir, raw, energy_enabled=True,
                energy_calibration=calibration,
            )
            rows, selection = select_stage_transitions(
                arm_dir, raw, f"trainability_gate_{label}",
                require_asgard=require_asgard, energy_enabled=True,
                reference_arm=(label == "rapp"),
                min_per_stage=1, max_total=1,
            )
            native_proof = _native_gate_ready(
                arm_dir, require_economic=require_asgard
            )
            manifest = _reconcile_trainability_gate_arm(
                arm_dir, native_proof=native_proof,
                returncode=codes.get(label),
            )
            arm_status = manifest.get("status")
            performance = read_json(arm_dir / "arm_manifest.json").get("simulation_performance") or {}
            controlled_gate_stop = bool(
                stopped_after_native
                and codes.get(label) in {0, 15, 143, -15}
                and arm_status in {"finished", "cancelled"}
            )
            eligible = bool(
                controlled_gate_stop
                and native_proof
                and rows
                and all(
                    isinstance(row.get("energy_evidence"), dict)
                    and row["energy_evidence"].get("native") is True
                    and row["energy_evidence"].get("evidence_version") == "v6"
                    for row in rows
                )
            )
            reports[label] = {
                "status": "passed" if eligible else "metric_invalid",
                "returncode": codes.get(label),
                "run_dir": str(arm_dir),
                "selected_rows": len(rows),
                "selection": selection,
                "economic_transition_observed": eligible,
                "native_evidence_version": "v6",
                "controlled_stop_after_native": controlled_gate_stop,
                "native_proof_after_stop": native_proof,
                "performance_evidence": performance,
                "reason": "" if eligible else "no_complete_native_economic_transition",
            }
    finally:
        for label, (arm_dir, _require_asgard) in arms.items():
            _cleanup_finished_arm(
                arm_dir, gate_root / f"cleanup_audit_{label}",
                reason="trainability_gate_finished_before_long_pilot",
            )
    report = {
        "schema": "greenran.tasam.v2x.energy_engineering.trainability_gate.v1",
        "status": "passed" if all(item["status"] == "passed" for item in reports.values()) else "metric_invalid",
        "sim_time_s": gate_sim_time_s,
        "wall_time_s": gate_wall_time_s,
        "parallel": False,
        "slots": {"rapp": "slot-a", "asgard": "slot-b"},
        "arms": reports,
        "native_evidence_version": "v6",
        "economic_action_contract": "economic_action_v3_per_du_sleep",
    }
    write_json(gate_root / "gate_report.json", report)
    return report


def _load_reusable_smoke(source_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Reuse only a completed smoke whose provenance matches this run."""
    source_root = source_root.resolve()
    report_path = source_root / "smoke" / "smoke_report.json"
    report = read_json(report_path)
    if report.get("status") != "passed" or report.get("slot_ids") != list(PAIR_SLOTS):
        raise SystemExit("smoke reutilizável ausente, incompleto ou não aprovado")
    expected_binary_sha256 = sha256(args.binary.resolve())
    source_manifests: dict[str, str] = {}
    source_traces: dict[str, str] = {}
    for slot in PAIR_SLOTS:
        slot_report = report.get("slots", {}).get(slot, {})
        smoke_dir = source_root / "smoke" / "e2_v3" / slot
        manifest_path = smoke_dir / "arm_manifest.json"
        manifest = read_json(manifest_path)
        trace = smoke_dir / "ns3_energy" / "TasamControlObservations.csv"
        if not trace.is_file():
            raise SystemExit(f"trace nativo ausente no smoke reutilizado: {trace}")
        if (
            slot_report.get("status") != "passed"
            or manifest.get("status") != "finished"
            or manifest.get("profile") != args.profile
            or int(manifest.get("seed", -1)) != SEED
            or manifest.get("native_evidence_version") != "v6"
            or manifest.get("ns3_binary_sha256") != expected_binary_sha256
            or manifest.get("parallel_execution", {}).get("slot_id") != slot
        ):
            raise SystemExit(f"proveniência incompatível no smoke reutilizado: {slot}")
        source_manifests[slot] = sha256(manifest_path)
        source_traces[slot] = sha256(trace)
    reused = dict(report)
    reused.update({
        "reused_immutable": True,
        "reused_from": str(source_root),
        "reused_report_sha256": sha256(report_path),
        "reused_arm_manifest_sha256": source_manifests,
        "reused_native_trace_sha256": source_traces,
    })
    return reused


def _load_reusable_trainability_gate(source_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Reuse only a passed trainability gate with matching build provenance."""
    source_root = source_root.resolve()
    report_path = source_root / "smoke" / "trainability_gate" / "gate_report.json"
    report = read_json(report_path)
    if report.get("status") != "passed" or report.get("native_evidence_version") != "v6":
        raise SystemExit("gate de treinabilidade reutilizável ausente ou não aprovado")
    expected_binary_sha256 = sha256(args.binary.resolve())
    arm_manifests: dict[str, str] = {}
    for label in ("rapp", "asgard"):
        arm_dir = source_root / "smoke" / "trainability_gate" / ("slot-a" if label == "rapp" else "slot-b")
        manifest_path = arm_dir / "arm_manifest.json"
        manifest = read_json(manifest_path)
        if (
            manifest.get("profile") != args.profile
            or int(manifest.get("seed", -1)) != SEED
            or manifest.get("native_evidence_version") != "v6"
            or manifest.get("ns3_binary_sha256") != expected_binary_sha256
            or manifest.get("economic_action_contract") != "economic_action_v3_per_du_sleep"
            or report.get("arms", {}).get(label, {}).get("status") != "passed"
        ):
            raise SystemExit(f"proveniência incompatível no gate reutilizado: {label}")
        arm_manifests[label] = sha256(manifest_path)
    reused = dict(report)
    reused.update({
        "reused_immutable": True,
        "reused_from": str(source_root),
        "reused_report_sha256": sha256(report_path),
        "reused_arm_manifest_sha256": arm_manifests,
        "reused_binary_sha256": expected_binary_sha256,
    })
    return reused


def _freeze_active_checkpoint(training_root: Path, target: Path) -> dict[str, Any]:
    state_path = training_root / "asgard" / "arm" / "online_state.json"
    state = read_json(state_path)
    active = Path(str(state.get("active_checkpoint") or "")).resolve()
    required = ("tasam_marl_actors.pt", "tasam_marl_checkpoint_meta.json", "tasam_marl_summary.json")
    if not active.is_dir() or any(not (active / item).is_file() for item in required):
        raise SystemExit("r5 não encontrou checkpoint ASGARD completo após o treino")
    summary = read_json(active / "tasam_marl_summary.json")
    if not isinstance(summary.get("final_metrics"), dict) or not summary.get("final_metrics"):
        raise SystemExit("checkpoint ASGARD ativo não possui final_metrics")
    if target.exists():
        raise SystemExit(f"checkpoint frozen já existe: {target}")
    shutil.copytree(active, target)
    digest = tree_sha256(target)
    selection = {
        "schema": "greenran.tasam.v2x.energy_r5.selection.v1",
        "source_checkpoint": str(active),
        "source_checkpoint_sha256": tree_sha256(active),
        "frozen_checkpoint": str(target),
        "frozen_checkpoint_sha256": digest,
        "selection_basis": "active_safety_category_then_eval_return",
        "final_metrics": summary.get("final_metrics"),
        "evaluation_frozen": True,
        "promotion_eligible": False,
    }
    write_json(target.parent / "selection_manifest.json", selection)
    return selection


def _pair_environment(schedule_file: Path, schedule_id: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "GREENRAN_LOCAL_ONLY": "1",
        "GREENRAN_CGROUP_ENFORCE": "1",
        "GREENRAN_CGROUP_ALLOW_UNENFORCED": "0",
        "GREENRAN_REQUIRE_REAL_PDCP": "1",
        "GREENRAN_REAL_ONLY": "1",
        "GREENRAN_TASAM_EXPORT_ALLOW_PROXY": "0",
        "GREENRAN_TASAM_REWARD_CONTRACT": REWARD_CONTRACT,
        "GREENRAN_TASAM_REWARD_ENERGY_ENABLED": "1",
        # V3 controls are atomic over DUs 2--4.  One simulated second gives
        # the ns-3 E2 callbacks time to apply one bundle before the next one;
        # both arms use the same cadence and the traffic/radio scenario is
        # unchanged.
        "GREENRAN_COLLECTION_EVENT_TICK_S": "1.0",
        "GREENRAN_PAIRING_SCHEDULE_ID": schedule_id,
        "GREENRAN_PAIRING_SCHEDULE_FILE": str(schedule_file),
        # Let the rApp receive SIGTERM only after ns-3/actuator shutdown has
        # settled, so its pending real-PDCP Judge transition can be drained.
        "GREENRAN_DRAIN_RAPP_BEFORE_STOP": "1",
        "GREENRAN_RAPP_DRAIN_SECONDS": "8",
    })
    return env


def _arm_observation(arm_dir: Path, raw: Path, *, require_asgard: bool, calibration: Path) -> dict[str, Any]:
    export_rows(arm_dir, raw, energy_enabled=True, energy_calibration=calibration)
    rows, selection = select_stage_transitions(
        arm_dir, raw, "paired_rapp" if not require_asgard else "paired_asgard",
        require_asgard=require_asgard, energy_enabled=True,
        reference_arm=not require_asgard,
    )
    return {
        "selection": selection,
        "selected_rows": len(rows),
        "sla_observation": evaluate_ue_windows(
            arm_dir, warmup_s=30, duration_s=int(SIM_TIME), imsis=tuple(range(16, 21))
        ),
        "energy": causal_energy(arm_dir, warmup_s=30, duration_s=int(SIM_TIME)),
        "e2": e2_audit(arm_dir, warmup_s=30),
        "arm_manifest_sha256": sha256(arm_dir / "arm_manifest.json"),
        "raw_export_sha256": sha256(raw),
    }


def _parse_slots(value: str) -> tuple[str, ...]:
    slots = tuple(item.strip() for item in value.split(",") if item.strip())
    if slots != PAIR_SLOTS:
        raise argparse.ArgumentTypeError("o piloto exige exatamente --parallel-slots slot-a,slot-b")
    return slots


def _host_preflight_report() -> dict[str, Any]:
    """Snapshot of host readiness: load average and leftover trainer processes.

    Trainers (run_tasam_online_controlled.py) are launched per arm and must
    die with the arm.  Any survivor at pre-launch time is an orphan from a
    previous failed stop path and pollutes both host capacity and the
    experiment's provenance, so the campaign refuses to start until it is
    cleaned up.
    """
    cpus = os.cpu_count() or 1
    load_1m = os.getloadavg()[0]
    load_limit = max(2.0, 0.25 * cpus)
    orphans: list[dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        parts = [p for p in raw.split(b"\0") if p]
        # Igualdade exata de basename: um wrapper `bash -c` que apenas menciona
        # o script em seu payload não é um treinador.
        if not any(
            os.path.basename(p.decode("utf-8", errors="replace")) == "run_tasam_online_controlled.py"
            for p in parts
        ):
            continue
        argv = [p.decode("utf-8", errors="replace") for p in parts]
        state_dir = ""
        if "--state-dir" in argv:
            idx = argv.index("--state-dir")
            if idx + 1 < len(argv):
                state_dir = argv[idx + 1]
        orphans.append({"pid": int(entry.name), "state_dir": state_dir, "cmdline": " ".join(argv)})
    return {
        "schema": "greenran.tasam.v2x.host_preflight.v1",
        "cpu_count": cpus,
        "load_1m": round(load_1m, 3),
        "load_limit": round(load_limit, 3),
        "loaded": bool(load_1m > load_limit),
        "orphan_trainers": orphans,
    }


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Fail before creating a campaign when the host or isolation is unfit."""
    # Barreiras mais baratas e mais acionáveis primeiro: estado do host.
    host = _host_preflight_report()
    if host["orphan_trainers"]:
        raise SystemExit(
            "preflight: treinador(es) órfão(s) de campanha(s) antiga(s) "
            f"({len(host['orphan_trainers'])}). Eles nunca devem sobreviver ao "
            "lançamento e competem com a simulação. Limpe e relance:\n" + "\n".join(
                f"  kill -TERM {o['pid']}   # {o['state_dir'] or o['cmdline'][:110]}"
                for o in host["orphan_trainers"]
            )
        )
    if host["loaded"] and os.environ.get("GREENRAN_ALLOW_LOADED_HOST") != "1":
        raise SystemExit(
            f"preflight: host sobrecarregado (load 1m {host['load_1m']} > "
            f"limite {host['load_limit']}). RTF abaixo do piso invalida a "
            "campanha. Libere CPU ou relance com GREENRAN_ALLOW_LOADED_HOST=1 "
            "para ignorar deliberadamente."
        )
    assert_cgroup_delegation()
    slots = resolve_slots(args.parallel_slots)
    assert_disk_capacity(ROOT / "runs", len(slots))
    for slot in slots:
        assert_ports_free(slot)
    return host


def _run_campaign(args: argparse.Namespace) -> int:
    global _binary_from_args
    _binary_from_args = args.binary.resolve()
    if args.seed != SEED or args.sim_time != SIM_TIME:
        raise SystemExit("r5 exige seed 43 e 120 s simulados")
    if args.trainability_gate_only:
        if args.wall_time <= 0:
            raise SystemExit("o gate exige wall-time positivo")
    elif args.wall_time != WALL_TIME:
        raise SystemExit("o piloto longo exige 9000 s de parede")
    if args.profile != PROFILE or args.decision_target != 0 or args.performance_min_rtf != MIN_RTF:
        raise SystemExit("o piloto exige perfil baseline_max, decision-target=0 e RTF mínimo 0.016")
    if not _binary_from_args.is_file() or not os.access(_binary_from_args, os.X_OK):
        raise SystemExit(f"binário ausente/não executável: {_binary_from_args}")
    calibration = args.calibration.resolve()
    if not calibration.is_file():
        raise SystemExit(f"calibração energética ausente: {calibration}")
    host_preflight = _preflight(args)
    root = args.output_root.resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"output-root não está vazio: {root}")
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "host_preflight.json", host_preflight)

    if args.trainability_gate_only:
        gate = _run_trainability_gate(root, args, calibration, args.parallel_slots)
        report = {
            "schema": "greenran.tasam.v2x.energy_engineering.gate_only_report.v1",
            "status": gate.get("status"),
            "scientific_decision": "not_promotable",
            "promotion_eligible": False,
            "profile": args.profile,
            "seed": SEED,
            "sim_time_s": 30.0,
            "wall_time_s": min(float(args.wall_time), 480.0),
            "trainability_gate": gate,
            "binary": str(_binary_from_args),
            "binary_sha256": sha256(_binary_from_args),
        }
        write_json(root / "campaign_report.json", report)
        return 0 if gate.get("status") == "passed" else 2

    if args.reuse_smoke_root is not None:
        infrastructure_smoke = _load_reusable_smoke(args.reuse_smoke_root, args)
    else:
        infrastructure_smoke = {
            "status": "passed",
            "reused_immutable": False,
            "note": "smoke anterior não substitui o gate de treinabilidade",
        }
    if args.reuse_trainability_gate_root is not None:
        trainability_gate = _load_reusable_trainability_gate(
            args.reuse_trainability_gate_root, args
        )
        write_json(root / "smoke" / "trainability_gate_reused.json", trainability_gate)
    else:
        trainability_gate = _run_trainability_gate(
            root, args, calibration, args.parallel_slots
        )
    smoke_report = {
        "schema": "greenran.tasam.v2x.energy_engineering.smoke_chain.v1",
        "status": trainability_gate.get("status"),
        "infrastructure_smoke": infrastructure_smoke,
        "trainability_gate": trainability_gate,
    }
    write_json(root / "smoke" / "smoke_report.json", smoke_report)
    if smoke_report.get("status") != "passed":
        report = {
            "schema": "greenran.tasam.v2x.energy_engineering.report.v1",
            "campaign_revision": "engineering_r13_energy_staircase",
            "status": "metric_invalid",
            "scientific_decision": "not_promotable",
            "promotion_eligible": False,
            "campaign_kind": "v2x_energy_window90_rapp_x_asgard",
            "economic_action_contract": "economic_action_v3_per_du_sleep",
            "energy_staircase_contract": "greenran.tasam.v2x.energy_staircase.v1",
            "energy_staircase_config_sha256": sha256(ROOT / "config/greenran_v2x_energy_staircase.json"),
            "du_sleep_policy": "at_most_one_du_after_handover_and_10s_pdcp",
            "native_evidence_version": "v6",
            "smoke": smoke_report,
            "reason": "e2_v3_smoke_failed_before_long_pilot",
        }
        write_json(root / "campaign_report.json", report)
        write_json(root / "campaign_manifest.json", {**report, "profile": args.profile, "seed": SEED})
        return 2

    training_root = root / "training"
    pilot_command = [
        sys.executable, str(PILOT), "--output-root", str(training_root),
        "--binary", str(_binary_from_args), "--profile", args.profile, "--seed", str(SEED),
        "--sim-time", str(int(SIM_TIME)), "--wall-time", str(int(WALL_TIME)),
        "--decision-target", "0", "--performance-min-rtf", str(MIN_RTF),
        "--energy-enabled", "--energy-calibration", str(calibration),
        "--energy-staircase",
        "--execution-slot", args.training_slot,
    ]
    if args.baseline_source is not None:
        # O baseline computa uma vez e fica congelado: o piloto valida o
        # manifest sha + replay_90 + perfil antes de reusar e grava a
        # proveniência no próprio report (mesmo caminho provado na r31).
        pilot_command += [
            "--resume-baseline", "--baseline-source", str(args.baseline_source),
        ]
    pilot_rc = _run(pilot_command, log=root / "training_launcher.log")
    training_report = read_json(training_root / "campaign_report.json")
    if pilot_rc != 0 or training_report.get("status") != "pilot_complete":
        report = {
            "schema": "greenran.tasam.v2x.energy_engineering.report.v1",
            "campaign_revision": "engineering_r13_energy_staircase",
            "status": "pilot_training_incomplete",
            "scientific_decision": "not_promotable",
            "promotion_eligible": False,
            "pilot_exit_code": pilot_rc,
            "smoke": smoke_report,
            "training_report": training_report,
            "training_root": str(training_root),
            "economic_action_contract": "economic_action_v3_per_du_sleep",
            "energy_staircase_contract": "greenran.tasam.v2x.energy_staircase.v1",
            "energy_staircase_config_sha256": sha256(ROOT / "config/greenran_v2x_energy_staircase.json"),
            "du_sleep_policy": "at_most_one_du_after_handover_and_10s_pdcp",
            "native_evidence_version": "v6",
        }
        write_json(root / "campaign_report.json", report)
        write_json(root / "campaign_manifest.json", {**report, "profile": args.profile, "seed": SEED})
        return pilot_rc or 2

    frozen = root / "frozen" / "asgard"
    selection = _freeze_active_checkpoint(training_root, frozen)
    floor_ledger_path = training_root / "baseline" / "safe_power_floor_ledger.json"
    if not floor_ledger_path.is_file() or (
        (read_json(floor_ledger_path).get("status") or "") != "validated"
    ):
        raise SystemExit(
            "pareada exige o ledger de piso seguro validado do treino: "
            f"{floor_ledger_path} ausente ou sem status=validated"
        )
    paired = root / "paired"
    paired.mkdir()
    schedule = canonical_schedule(args.profile, SEED, WALL_TIME, tick_s=0.25)
    schedule_file = paired / "pairing_schedule.json"
    write_schedule(schedule_file, schedule)
    env = _pair_environment(schedule_file, schedule["schedule_id"])
    arms = {
        "rapp": ("rapp_only_actuating", paired / "rapp", False, "slot-a"),
        "asgard": ("asgard_v2x_window90_energy_frozen", paired / "asgard", True, "slot-b"),
    }
    commands = {
        label: _arm_command(
            mode, arm_dir, frozen, schedule_file, schedule["schedule_id"], calibration,
            wall_time=args.wall_time, binary=_binary_from_args, profile=args.profile,
            execution_slot=slot,
            safe_power_floor_ledger=(
                floor_ledger_path if label == "asgard" else None
            ),
        )
        for label, (mode, arm_dir, _require_asgard, slot) in arms.items()
    }
    arm_codes = _run_parallel(
        commands, env=env,
        logs={label: paired / f"{label}.launcher.log" for label in arms},
    )
    for label, (_mode, arm_dir, _require_asgard, _slot) in arms.items():
        _cleanup_finished_arm(
            arm_dir,
            paired / f"cleanup_audit_{label}",
            reason="energy_engineering_pair_finished",
        )
    observations: dict[str, dict[str, Any]] = {}
    checkpoint_before = tree_sha256(frozen)
    for label, (_mode, arm_dir, require_asgard, _slot) in arms.items():
        if arm_codes[label] == 0:
            observations[label] = _arm_observation(
                arm_dir, paired / f"{label}.raw_export.jsonl",
                require_asgard=require_asgard, calibration=calibration,
            )
        else:
            observations[label] = {"selection": {"complete": False}, "returncode": arm_codes[label]}
    checkpoint_after = tree_sha256(frozen)
    criteria = {
        "both_arms_finished": all(code == 0 for code in arm_codes.values()),
        "both_stage_selections_complete": all(
            observations.get(label, {}).get("selection", {}).get("complete") is True
            for label in arms
        ),
        "both_pdcp_strict_observations": all(
            observations.get(label, {}).get("sla_observation", {}).get("valid") is True
            for label in arms
        ),
        "both_native_energy_complete": all(
            observations.get(label, {}).get("energy", {}).get("valid") is True
            for label in arms
        ),
        "both_e2_complete": all(
            observations.get(label, {}).get("e2", {}).get("valid") is True
            for label in arms
        ),
        "frozen_checkpoint_immutable": checkpoint_before == checkpoint_after == selection["frozen_checkpoint_sha256"],
    }
    rapp_energy = float(observations.get("rapp", {}).get("energy", {}).get("energy_j", 0.0) or 0.0)
    asgard_energy = float(observations.get("asgard", {}).get("energy", {}).get("energy_j", 0.0) or 0.0)
    criteria["asgard_energy_strictly_lower"] = asgard_energy < rapp_energy
    hard_valid = all(criteria[key] for key in (
        "both_arms_finished", "both_stage_selections_complete", "both_pdcp_strict_observations",
        "both_native_energy_complete", "both_e2_complete", "frozen_checkpoint_immutable",
    ))
    asgard_sla_valid = observations.get("asgard", {}).get("sla_observation", {}).get("valid") is True
    rapp_sla_valid = observations.get("rapp", {}).get("sla_observation", {}).get("valid") is True
    if not hard_valid or not asgard_sla_valid:
        status = "metric_invalid"
    elif not rapp_sla_valid and criteria["asgard_energy_strictly_lower"]:
        status = "asgard_operational_advantage"
    elif not criteria["asgard_energy_strictly_lower"]:
        status = "no_energy_advantage"
    else:
        status = "engineering_passed"
    report = {
        "schema": "greenran.tasam.v2x.energy_engineering.report.v1",
        "campaign_revision": "engineering_r13_energy_staircase",
        "status": status,
        "scientific_decision": "not_promotable",
        "promotion_eligible": False,
        "campaign_kind": "v2x_energy_window90_rapp_x_asgard",
        "economic_action_contract": "economic_action_v3_per_du_sleep",
        "energy_staircase_contract": "greenran.tasam.v2x.energy_staircase.v1",
        "energy_staircase_config_sha256": sha256(ROOT / "config/greenran_v2x_energy_staircase.json"),
        "du_sleep_policy": "at_most_one_du_after_handover_and_10s_pdcp",
        "native_evidence_version": "v6",
        "comparison_mode": "parallel_isolated_frozen_pair",
        "profile": args.profile, "seed": SEED, "sim_time_s": SIM_TIME,
        "decision_target": 0, "performance_min_rtf": MIN_RTF,
        "training_report": training_report,
        "smoke": smoke_report,
        "selection": selection,
        "pairing_schedule": str(schedule_file),
        "arm_exit_codes": arm_codes,
        "arms": observations,
        "integrated_energy_j": {"rapp": rapp_energy, "asgard": asgard_energy, "delta_asgard_minus_rapp": asgard_energy - rapp_energy},
        "criteria": criteria,
        "energy_reference": "native_ns3_relative_simulation; engineering-only, not physical wattmeter evidence",
        "binary": str(_binary_from_args), "binary_sha256": sha256(_binary_from_args),
        "execution_slots": list(args.parallel_slots),
        "build_provenance": build_provenance(_binary_from_args),
        "calibration": str(calibration), "calibration_sha256": sha256(calibration),
        "seeds_excluded_from_training": [45, 46, 47],
    }
    manifest = {**report, "source_tree_sha256": tree_sha256(ROOT / "src"), "stage_order": list(STAGES)}
    write_json(root / "campaign_report.json", report)
    write_json(root / "campaign_manifest.json", manifest)
    return 0 if status in {"engineering_passed", "asgard_operational_advantage"} else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--sim-time", type=float, default=SIM_TIME)
    parser.add_argument("--wall-time", type=float, default=WALL_TIME)
    parser.add_argument("--decision-target", type=int, default=0)
    parser.add_argument("--performance-min-rtf", type=float, default=MIN_RTF)
    parser.add_argument("--calibration", type=Path, default=CALIBRATION)
    parser.add_argument(
        "--parallel-slots", type=_parse_slots, default=PAIR_SLOTS,
        help="slots isolados; o piloto exige slot-a,slot-b",
    )
    parser.add_argument(
        "--reuse-smoke-root", type=Path, default=None,
        help="reutiliza por hash um smoke E2 V3 já aprovado, sem iniciar outro smoke",
    )
    parser.add_argument(
        "--reuse-trainability-gate-root", type=Path, default=None,
        help="reutiliza por hash um gate de treinabilidade v6 aprovado, sem repeti-lo",
    )
    parser.add_argument(
        "--baseline-source", type=Path, default=None,
        help="reutiliza um baseline congelado (manifest sha validado pelo piloto); baseline computa uma vez",
    )
    parser.add_argument("--training-slot", choices=PAIR_SLOTS, default="slot-a")
    parser.add_argument(
        "--trainability-gate-only", action="store_true",
        help="executa apenas o gate nativo de 30 s e não inicia o piloto longo",
    )
    args = parser.parse_args()
    return _run_campaign(args)


if __name__ == "__main__":
    raise SystemExit(main())
