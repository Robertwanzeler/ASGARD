#!/usr/bin/env python3
"""Run and audit the native, non-promotable ASGARD conflict probe.

The probe deliberately keeps the simulator alive for one continuous run of up
to three curriculum cycles.  Stage names are an agenda only; a stage counts as
covered only when the native traces contain the corresponding observed domain.
This launcher does not alter the traffic/radio scenario and never treats a
proxy metric or a missing confirmation as a successful action.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARM = ROOT / "scripts" / "run_tasam_online_arm.py"
EXPORTER = ROOT / "scripts" / "export_tasam_article_dataset.py"
BOOTSTRAP = ROOT / "scripts" / "build_tasam_v10_checkpoint.py"
DEFAULT_BINARY = ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build-v9-optimized/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-optimized"
DEFAULT_CATEGORY_SOURCE = ROOT / "runs/tasam_asgard_adaptation_seed47_20260914_v9_fidelity3/initial_checkpoint_applied_action_v2"
DEFAULT_BASELINE = ROOT / "runs/tasam_v2x_energy_pair_seed43_20261005_r23"
DEFAULT_CALIBRATION = ROOT / "config/energy_calibration_sim_v3_sleep.json"
STAIRCASE_CONFIG = ROOT / "config/greenran_v2x_energy_staircase_10pct.json"
PROFILE = "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max"
SEED = 43
CYCLE_SECONDS = 120.0
MAX_CYCLES = 3
SIM_TIME = CYCLE_SECONDS * MAX_CYCLES
WALL_TIME = 27000.0
MIN_RTF = 0.016
STAGES = (
    "allowed_bootstrap", "allowed_stable", "camera_conditional", "camera_blocked",
    "vehicle_conditional", "vehicle_blocked", "app2_conditional", "app2_blocked",
    "allowed_recovery",
)
REWARD_CONTRACT = "greenran.tasam.v2x.reward_adaptive.v1"
ECONOMIC_CONTRACT = "economic_action_v3_per_du_sleep"
STAIRCASE_CONTRACT = "greenran.tasam.v2x.energy_staircase.v3_safe_probe_10"
EVIDENCE_VERSION = "v6"
EXPECTED_CELLS = {2, 3, 4}
VEHICLE_IMSIS = {16, 17, 18, 19, 20}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha256(path: Path) -> str:
    if not path.is_dir():
        return ""
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(bytes.fromhex(sha256(item)))
    return digest.hexdigest()


def _baseline_root(source: Path) -> Path:
    source = source.resolve()
    if (source / "baseline" / "arm").is_dir():
        return source
    if (source / "training" / "baseline" / "arm").is_dir():
        return source / "training"
    raise SystemExit(f"baseline histórico não encontrado em {source}")


def validate_history(source: Path, profile: str) -> dict[str, Any]:
    root = _baseline_root(source)
    arm_manifest = read_json(root / "baseline" / "arm" / "arm_manifest.json", {})
    selection = read_json(root / "baseline" / "selection_manifest.json", {})
    replay = root / "baseline" / "replay_90.jsonl"
    if arm_manifest.get("seed") != SEED or arm_manifest.get("profile") != profile:
        raise SystemExit("baseline histórico tem seed/perfil incompatíveis")
    if arm_manifest.get("real_only_collection") is not True or arm_manifest.get("scenario_control_override_allowed") is not False:
        raise SystemExit("baseline histórico não é real-only")
    if selection.get("complete") is not True or int(selection.get("selected_total", 0)) != 90:
        raise SystemExit("baseline histórico não possui 90 transições completas")
    if not replay.is_file():
        raise SystemExit("replay_90.jsonl ausente na baseline histórica")
    rows = [line for line in replay.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 90:
        raise SystemExit(f"replay histórico tem {len(rows)} linhas; esperado 90")
    return {
        "root": str(root),
        "arm_manifest": str(root / "baseline" / "arm" / "arm_manifest.json"),
        "arm_manifest_sha256": sha256(root / "baseline" / "arm" / "arm_manifest.json"),
        "selection_manifest": str(root / "baseline" / "selection_manifest.json"),
        "selection_manifest_sha256": sha256(root / "baseline" / "selection_manifest.json"),
        "replay": str(replay),
        "replay_sha256": sha256(replay),
        "rows": len(rows),
    }


def validate_fixed(args: argparse.Namespace) -> None:
    if args.profile != PROFILE:
        raise SystemExit(f"perfil obrigatório: {PROFILE}")
    if args.seed != SEED or args.sim_time != SIM_TIME:
        raise SystemExit(f"o piloto exige seed 43 e sim-time={int(SIM_TIME)}")
    if args.decision_target != 0 or args.performance_min_rtf != MIN_RTF:
        raise SystemExit("o piloto exige decision-target=0 e RTF mínimo 0.016")
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        raise SystemExit(f"binário release ausente/não executável: {args.binary}")
    if not args.category_source.is_dir():
        raise SystemExit(f"fonte da cabeça categórica ausente: {args.category_source}")
    if not args.calibration.is_file():
        raise SystemExit(f"calibração energética ausente: {args.calibration}")


def build_bootstrap(args: argparse.Namespace, target: Path) -> None:
    if target.is_dir() and any(target.iterdir()):
        return
    subprocess.run([
        sys.executable, str(BOOTSTRAP), "--parent", str(args.category_source),
        "--output", str(target), "--seed", str(SEED), "--category-only",
    ], cwd=ROOT, check=True)


def arm_command(args: argparse.Namespace, arm_dir: Path, checkpoint: Path, history: Path, recent: Path, floor: Path) -> list[str]:
    command = [
        sys.executable, str(ARM),
        "--mode", "asgard_v2x_window90_energy_online",
        "--run-dir", str(arm_dir), "--seed", str(SEED), "--profile", args.profile,
        "--wall-time", str(int(args.wall_time)), "--sim-time", str(int(args.sim_time)),
        "--decision-target", "0", "--native-fidelity",
        "--performance-min-rtf", str(MIN_RTF), "--binary", str(args.binary),
        "--checkpoint", str(checkpoint), "--execution-slot", args.execution_slot,
        "--disable-app-overrides", "--energy-enabled", "--energy-calibration", str(args.calibration),
        "--energy-staircase", "--adaptive-energy-probe",
        "--safe-power-floor-ledger", str(floor),
        "--experience-bank", str(history), "--recent-experience-bank", str(recent),
        "--replay-rows", "90", "--min-new-snapshots", "18",
        "--min-trainable-transitions", "90", "--epochs-per-update", "1",
        "--controller-poll-seconds", "5", "--max-rollout-fraction", "1.0",
        "--min-economic-transitions", "0", "--economic-update-min-transitions", "0",
        "--min-free-gib", "20", "--artifact-min-free-gib", "20", "--artifact-budget-gib", "4",
    ]
    return command


def freeze_active_checkpoint(arm_dir: Path, target: Path, *, decision_count: int, state: dict[str, Any]) -> dict[str, Any] | None:
    active = Path(str(state.get("active_checkpoint") or "")).resolve()
    required = ("tasam_marl_actors.pt", "tasam_marl_checkpoint_meta.json", "tasam_marl_summary.json")
    if not active.is_dir() or any(not (active / name).is_file() for name in required):
        return None
    if target.exists():
        selection = read_json(target.parent / "selection_manifest.json", {})
        return selection or None
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(active, target)
    summary = read_json(target / "tasam_marl_summary.json", {})
    selection = {
        "schema": "greenran.tasam.v2x.conflict_probe.freeze.v1",
        "source_checkpoint": str(active),
        "source_checkpoint_sha256": tree_sha256(active),
        "frozen_checkpoint": str(target),
        "frozen_checkpoint_sha256": tree_sha256(target),
        "updates_completed": int(state.get("updates_completed", 0) or 0),
        "freeze_decision_count": int(decision_count),
        "freeze_stage": state.get("stage", ""),
        "final_metrics": summary.get("final_metrics", {}),
        "evaluation_frozen": True,
        "promotion_eligible": False,
    }
    write_json(target.parent / "selection_manifest.json", selection)
    return selection


def supervise_arm(args: argparse.Namespace, arm_dir: Path, checkpoint: Path, history: Path, recent: Path, floor: Path, frozen: Path) -> tuple[int, dict[str, Any]]:
    arm_dir.mkdir(parents=True, exist_ok=True)
    log = arm_dir.parent / "conflict_probe_arm.log"
    command = arm_command(args, arm_dir, checkpoint, history, recent, floor)
    child_env = os.environ.copy()
    # The active r1 process is already loaded with the historical 5% probe.
    # This setting is intentionally applied only to the next conflict-probe
    # arm and is recorded in its manifest/config hash.
    child_env["GREENRAN_TASAM_ENERGY_STEP_PERCENT"] = "10"
    with log.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            command, cwd=ROOT, env=child_env, stdout=handle,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        freeze_written = False
        while process.poll() is None:
            state = read_json(arm_dir / "online_state.json", {})
            updates = int(state.get("updates_completed", 0) or 0)
            if updates >= 5 and not freeze_written:
                selection = freeze_active_checkpoint(
                    arm_dir, frozen, decision_count=int(state.get("decision_count", 0) or 0), state=state
                )
                freeze_written = selection is not None
            time.sleep(5)
        state = read_json(arm_dir / "online_state.json", {})
    # If the process ended immediately after the fifth update, seal the copy.
    if int(state.get("updates_completed", 0) or 0) >= 5 and not frozen.exists():
        freeze_active_checkpoint(arm_dir, frozen, decision_count=int(state.get("decision_count", 0) or 0), state=state)
    return int(process.returncode or 0), state


def export_arm(args: argparse.Namespace, arm_dir: Path) -> Path:
    raw = arm_dir / "raw_export.jsonl"
    command = [
        sys.executable, str(EXPORTER), "--db", str(arm_dir / "rapp_data_lake.db"),
        "--output-jsonl", str(raw), "--summary-json", str(arm_dir / "raw_export_summary.json"),
        "--e2-audit-jsonl", str(arm_dir / "xapp_intents" / "tasam_control_audit.jsonl"),
        "--reward-contract", REWARD_CONTRACT, "--energy-enabled",
        "--energy-calibration", str(args.calibration), "--allocation-total-head-enabled",
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    return raw


def _sim_time(row: dict[str, Any]) -> float:
    for payload in (row.get("next_metrics"), row.get("metrics")):
        if isinstance(payload, dict):
            try:
                return float(payload.get("sim_time_s"))
            except (TypeError, ValueError):
                pass
    return 0.0


def _native_cells(row: dict[str, Any]) -> set[int]:
    applied = (row.get("economic_action") or {}).get("applied") or {}
    values = applied.get("native_cell_ids") or applied.get("cell_ids") or []
    cells = set()
    for value in values:
        try:
            cells.add(int(value))
        except (TypeError, ValueError):
            continue
    if not cells:
        powers = applied.get("power_percent_by_cell") or {}
        for value in powers:
            try:
                cells.add(int(value))
            except (TypeError, ValueError):
                continue
    return cells


def _pdcp_rows(db: Path) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not db.is_file():
        return result
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        for row in connection.execute("select * from ue_metrics"):
            result[int(row["timestamp"])].append(dict(row))
        connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return defaultdict(list)
    return result


def _ue_evidence(row: dict[str, Any], ue_index: dict[int, list[dict[str, Any]]]) -> tuple[bool, dict[str, Any]]:
    quality = row.get("collection_quality") or {}
    try:
        timestamp = int(quality.get("next_metric_timestamp") or 0)
    except (TypeError, ValueError):
        timestamp = 0
    entries = {int(item.get("imsi")): item for item in ue_index.get(timestamp, []) if item.get("imsi") is not None}
    result: dict[str, Any] = {"timestamp": timestamp, "by_imsi": {}, "missing_imsis": []}
    valid = True
    for imsi in sorted(VEHICLE_IMSIS):
        item = entries.get(imsi)
        if not item:
            result["missing_imsis"].append(imsi)
            valid = False
            continue
        try:
            tx = float(item.get("tx_pdus") or 0.0)
            rx = float(item.get("rx_pdus") or 0.0)
            loss = float(item.get("packet_loss_percent")) / 100.0
            p95 = float(item.get("latency_p95_us") or item.get("latency_us")) / 1000.0
            proxy = int(item.get("latency_is_proxy") or 0)
            provenance = str(item.get("pdcp_provenance") or "")
        except (TypeError, ValueError):
            valid = False
            continue
        item_result = {"tx_pdus": tx, "rx_pdus": rx, "loss": loss, "p95_ms": p95, "proxy": proxy, "provenance": provenance}
        result["by_imsi"][str(imsi)] = item_result
        if tx <= 0 or rx < 0 or rx > tx or loss >= 0.01 or p95 >= 20.0 or proxy != 0 or provenance != "pdcp_real":
            valid = False
    result["valid"] = valid
    return valid, result


def _conflict_domain(row: dict[str, Any]) -> str:
    context = row.get("conflict_context") or {}
    latest = context.get("latest") or {}
    text = " ".join(str(value).lower() for value in [
        latest.get("affected_service"), latest.get("target_agent"), latest.get("affected_kpi"),
        latest.get("conflict_type"), (row.get("decision") or {}).get("reason"),
    ] if value)
    if "camera" in text:
        return "camera"
    if "vehicle" in text or "veicular" in text or "app3" in text or "ego" in text:
        return "vehicle"
    if "app2" in text or "sensor" in text:
        return "app2"
    return ""


def _expected_domain(stage: str) -> str:
    if stage.startswith("camera_"):
        return "camera"
    if stage.startswith("vehicle_"):
        return "vehicle"
    if stage.startswith("app2_"):
        return "app2"
    return "global"


def _native_e2(row: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    action = row.get("economic_action") or {}
    applied = action.get("applied") or {}
    energy = row.get("energy_evidence") or {}
    if row.get("scenario_control_override") is not False:
        reasons.append("scenario_control_override")
    if row.get("economic_action_contract") not in {ECONOMIC_CONTRACT, ""} and action.get("contract") != ECONOMIC_CONTRACT:
        reasons.append("economic_contract_mismatch")
    if action.get("actuation_confirmed") is not True:
        reasons.append("actuation_not_confirmed")
    if energy.get("native") is not True or energy.get("e2_ack") is not True:
        reasons.append("energy_native_ack_incomplete")
    if str(energy.get("evidence_version") or "") != EVIDENCE_VERSION:
        reasons.append("evidence_version_missing")
    if _native_cells(row) != EXPECTED_CELLS:
        reasons.append("native_cells_incomplete")
    if not action.get("native_control_sequence"):
        reasons.append("native_control_sequence_missing")
    if not action.get("correlation_id") and not energy.get("action_correlation_id"):
        reasons.append("correlation_missing")
    return not reasons, reasons


def classify_row(row: dict[str, Any], ue_index: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    e2_ok, e2_reasons = _native_e2(row)
    quality = row.get("collection_quality") or {}
    pdcp_ok = (
        quality.get("collector_mode") == "pdcp_real"
        and float(quality.get("proxy_latency_sample_count", 1) or 1) == 0.0
        and (row.get("next_metrics") or {}).get("collector_mode") == "pdcp_real"
    )
    ue_ok, ue = _ue_evidence(row, ue_index)
    if not pdcp_ok:
        e2_reasons.append("pdcp_not_real_or_proxy")
    if not ue_ok:
        e2_reasons.append("per_ue_sla_or_pdcp_invalid")
    stage = str(row.get("decision_stage_name") or row.get("scenario_stage") or "")
    action = row.get("economic_action") or {}
    applied = action.get("applied") or {}
    powers = applied.get("power_percent_by_cell") or {}
    powers = {str(k): float(v) for k, v in powers.items() if isinstance(v, (int, float)) or str(v).replace(".", "", 1).isdigit()}
    reduced = bool(powers) and any(value < 100.0 for value in powers.values())
    feedback = row.get("judge_feedback") or {}
    reason_text = " ".join(str(value).lower() for value in [
        feedback.get("economic_invalid_reason"), feedback.get("economic_outcome_invalid_reason"),
        action.get("outcome_invalid_reason"), (row.get("decision") or {}).get("reason"),
    ] if value)
    critical = any(token in reason_text for token in ("critical", "violation", "loss_strict", "starvation", "outage", "harq"))
    if row.get("tasam_action_applied") and reduced and (critical or not ue_ok):
        classification = "unsafe"
    elif not e2_ok or not pdcp_ok or not ue_ok:
        classification = "unscorable"
    elif reduced:
        classification = "safe_efficient"
    else:
        classification = "safe_conservative"
    return {
        "decision_id": (row.get("decision") or {}).get("id", row.get("decision_id")),
        "sim_time_s": _sim_time(row),
        "cycle": int((row.get("decision") or {}).get("collection_event_cycle") or 0),
        "stage": stage,
        "expected_domain": _expected_domain(stage),
        "observed_conflict_domain": _conflict_domain(row),
        "native_conflict_observed": bool(_conflict_domain(row)) if not stage.startswith("allowed_") else True,
        "classification": classification,
        "e2_complete": e2_ok,
        "e2_reasons": e2_reasons,
        "pdcp_complete": pdcp_ok and ue_ok,
        "ue_evidence": ue,
        "power_percent_by_du": powers,
        "power_reduced": reduced,
        "fallback": bool(action.get("safety_override")) or "fallback" in reason_text,
        "energy_native": bool((row.get("energy_evidence") or {}).get("native")) and bool((row.get("energy_evidence") or {}).get("e2_ack")),
        "power_w": (row.get("energy_evidence") or {}).get("power_w"),
        "reference_power_w": (row.get("energy_evidence") or {}).get("reference_power_w"),
        "reward_contract": row.get("reward_contract"),
        "scenario_control_override": row.get("scenario_control_override"),
    }


def audit_rows(raw: Path, arm_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with raw.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    ue_index = _pdcp_rows(arm_dir / "rapp_data_lake.db")
    return [classify_row(row, ue_index) for row in rows]


def coverage_report(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    cycles: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        cycle_key = str(decision.get("cycle") or 0)
        stage = decision.get("stage") or "unknown"
        entry = cycles.setdefault(cycle_key, {"stages": {}, "complete": True})
        stage_entry = entry["stages"].setdefault(stage, {"native_complete": 0, "conflicts": 0, "classifications": Counter()})
        if decision.get("e2_complete") and decision.get("pdcp_complete"):
            stage_entry["native_complete"] += 1
        if decision.get("native_conflict_observed") and decision.get("expected_domain") != "global":
            if decision.get("observed_conflict_domain") == decision.get("expected_domain"):
                stage_entry["conflicts"] += 1
        stage_entry["classifications"].update([decision.get("classification", "unscorable")])
    complete_cycles: list[str] = []
    for cycle, payload in cycles.items():
        ok = True
        for stage in STAGES:
            item = payload["stages"].get(stage, {"native_complete": 0, "conflicts": 0})
            if item.get("native_complete", 0) < 3:
                ok = False
            if stage.endswith("conditional") or stage.endswith("blocked"):
                if item.get("conflicts", 0) < 1:
                    ok = False
            item["classifications"] = dict(item.get("classifications", {}))
            payload["stages"][stage] = item
        # Keep unexpected/extra stage labels serializable as well.  They are
        # evidence of a provenance mismatch, not a reason to silently drop
        # rows from the audit.
        for item in payload["stages"].values():
            item["classifications"] = dict(item.get("classifications", {}))
        payload["complete"] = ok
        if ok:
            complete_cycles.append(cycle)
    return {
        "schema": "greenran.tasam.v2x.conflict_probe.coverage.v1",
        "stage_order": list(STAGES),
        "required_native_decisions_per_stage": 3,
        "cycles": cycles,
        "complete_cycles": complete_cycles,
        "complete": bool(complete_cycles),
    }


def energy_projection(decisions: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, Any]:
    by_cycle: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        if decision.get("classification") == "safe_efficient" and decision.get("energy_native"):
            by_cycle[str(decision.get("cycle") or 0)].append(decision)
    values: list[float] = []
    cycle_details: dict[str, Any] = {}
    for cycle, rows in by_cycle.items():
        if cycle not in coverage.get("complete_cycles", []):
            continue
        rows.sort(key=lambda item: float(item.get("sim_time_s") or 0.0))
        saved = 0.0
        duration = 0.0
        for index, row in enumerate(rows):
            current = float(row.get("sim_time_s") or 0.0)
            nxt = float(rows[index + 1].get("sim_time_s") or (current + 1.0)) if index + 1 < len(rows) else current + 1.0
            dt = max(0.0, min(nxt - current, 12.0))
            power = float(row.get("power_w") or 0.0)
            reference = float(row.get("reference_power_w") or 0.0)
            if reference > power >= 0.0:
                saved += (reference - power) * dt / 3600.0
                duration += dt
        if duration > 0.0:
            wh_per_second = saved / duration
            values.append(wh_per_second)
            cycle_details[cycle] = {"safe_efficient_rows": len(rows), "saved_wh": saved, "duration_s": duration, "wh_saved_per_sim_second": wh_per_second}
    result: dict[str, Any] = {
        "schema": "greenran.tasam.v2x.conflict_probe.energy_projection.v1",
        "eligible_only_safe_efficient": True,
        "coverage_complete": bool(coverage.get("complete")),
        "unsafe_decision_observed": any(item.get("classification") == "unsafe" for item in decisions),
        "cycles": cycle_details,
    }
    if values and coverage.get("complete") and not result["unsafe_decision_observed"]:
        result["projection"] = {
            "wh_saved_per_sim_second_min": min(values),
            "wh_saved_per_sim_second_median": statistics.median(values),
            "wh_saved_per_sim_second_max": max(values),
            "complete_cycle_count": len(values),
        }
    else:
        result["projection_blocked_reason"] = "coverage_incomplete_or_unsafe_or_no_safe_efficient_interval"
    return result


def report_status(decisions: list[dict[str, Any]], coverage: dict[str, Any], selection: dict[str, Any] | None, arm_rc: int) -> str:
    if any(item.get("classification") == "unsafe" for item in decisions):
        return "unsafe_decision_observed"
    if any(item.get("scenario_control_override") is not False for item in decisions):
        return "metric_invalid"
    if not decisions or any(item.get("classification") == "unscorable" for item in decisions) and not coverage.get("complete"):
        return "metric_invalid"
    updates = int((selection or {}).get("updates_completed", 0) or 0)
    if updates < 5:
        return "pilot_training_incomplete"
    if not coverage.get("complete"):
        return "coverage_incomplete"
    if not selection or not selection.get("evaluation_frozen"):
        return "pilot_training_incomplete"
    if not any(item.get("classification") == "safe_efficient" for item in decisions):
        return "coverage_incomplete"
    return "pilot_complete" if arm_rc == 0 else "pilot_training_incomplete"


def run(args: argparse.Namespace) -> int:
    validate_fixed(args)
    root = args.output_root.resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"output-root não está vazio: {root}")
    root.mkdir(parents=True, exist_ok=True)
    history = validate_history(args.baseline_source, args.profile)
    bootstrap = root / "bootstrap" / "asgard"
    build_bootstrap(args, bootstrap)
    floor = Path(history["root"]) / "baseline" / "safe_power_floor_ledger.json"
    if not floor.is_file():
        raise SystemExit("ledger de piso seguro da baseline histórica ausente")
    arm_dir = root / "online" / "arm"
    recent = root / "online" / "recent_replay.jsonl"
    frozen = root / "frozen" / "asgard"
    manifest = {
        "schema": "greenran.tasam.v2x.conflict_probe.manifest.v1",
        "campaign_kind": "conflict_probe_v1",
        "status": "running",
        "promotion_eligible": False,
        "scientific_decision": "not_promotable",
        "seed": SEED,
        "profile": args.profile,
        "sim_time_s": SIM_TIME,
        "cycle_duration_s": CYCLE_SECONDS,
        "max_cycles": MAX_CYCLES,
        "decision_target": 0,
        "scenario_control_override": False,
        "native_pdcp_required": True,
        "evidence_version": EVIDENCE_VERSION,
        "economic_action_contract": ECONOMIC_CONTRACT,
        "energy_staircase_contract": STAIRCASE_CONTRACT,
        "energy_staircase_step_percent": 10,
        "energy_staircase_config": str(STAIRCASE_CONFIG),
        "energy_staircase_config_sha256": sha256(STAIRCASE_CONFIG),
        "reward_contract": REWARD_CONTRACT,
        "stages": list(STAGES),
        "binary": str(args.binary),
        "binary_sha256": sha256(args.binary),
        "category_source": str(args.category_source),
        "category_source_sha256": tree_sha256(args.category_source),
        "bootstrap": str(bootstrap),
        "bootstrap_sha256": tree_sha256(bootstrap),
        "baseline_history": history,
        "execution_slot": args.execution_slot,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(root / "campaign_manifest.json", manifest)
    arm_rc, state = supervise_arm(args, arm_dir, bootstrap, Path(history["replay"]), recent, floor, frozen)
    try:
        raw = export_arm(args, arm_dir)
    except (OSError, subprocess.CalledProcessError) as exc:
        # A killed/partial arm must still leave a machine-readable result;
        # never turn a missing exporter output into a successful empty audit.
        status = "metric_invalid"
        report = {
            "schema": "greenran.tasam.v2x.conflict_probe.report.v1",
            "campaign_kind": "conflict_probe_v1",
            "status": status,
            "scientific_decision": "not_promotable",
            "promotion_eligible": False,
            "arm_exit_code": arm_rc,
            "export_error": str(exc),
            "updates_completed": int(state.get("updates_completed", 0) or 0),
        }
        manifest.update({"status": status, "arm_exit_code": arm_rc, "online_state": state})
        write_json(root / "campaign_manifest.json", manifest)
        write_json(root / "campaign_report.json", report)
        return 2
    decisions = audit_rows(raw, arm_dir)
    with (root / "decision_audit.jsonl").open("w", encoding="utf-8") as handle:
        for item in decisions:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    coverage = coverage_report(decisions)
    projection = energy_projection(decisions, coverage)
    selection = read_json(frozen.parent / "selection_manifest.json", {})
    status = report_status(decisions, coverage, selection, arm_rc)
    manifest.update({
        "status": status,
        "arm_exit_code": arm_rc,
        "online_state": state,
        "frozen_checkpoint": selection,
        "decision_count": len(decisions),
        "updates_completed": int(state.get("updates_completed", 0) or 0),
    })
    report = {
        "schema": "greenran.tasam.v2x.conflict_probe.report.v1",
        "campaign_kind": "conflict_probe_v1",
        "status": status,
        "scientific_decision": "not_promotable",
        "promotion_eligible": False,
        "cycle_count_observed": len(coverage.get("cycles", {})),
        "decision_count": len(decisions),
        "updates_completed": int(state.get("updates_completed", 0) or 0),
        "coverage": coverage,
        "frozen_checkpoint": selection,
        "energy_projection": projection,
        "classification_counts": dict(Counter(item.get("classification") for item in decisions)),
        "native_conflict_counts": dict(Counter(item.get("observed_conflict_domain") for item in decisions if item.get("observed_conflict_domain"))),
        "safety": {
            "proxy_rows": sum(1 for item in decisions if not item.get("pdcp_complete")),
            "unsafe_rows": sum(1 for item in decisions if item.get("classification") == "unsafe"),
            "unscorable_rows": sum(1 for item in decisions if item.get("classification") == "unscorable"),
        },
    }
    write_json(root / "coverage_matrix.json", coverage)
    write_json(root / "energy_projection.json", projection)
    write_json(root / "campaign_manifest.json", manifest)
    write_json(root / "campaign_report.json", report)
    return 0 if status == "pilot_complete" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--sim-time", type=float, default=SIM_TIME)
    parser.add_argument("--wall-time", type=float, default=WALL_TIME)
    parser.add_argument("--decision-target", type=int, default=0)
    parser.add_argument("--performance-min-rtf", type=float, default=MIN_RTF)
    parser.add_argument("--category-source", type=Path, default=DEFAULT_CATEGORY_SOURCE)
    parser.add_argument("--baseline-source", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--execution-slot", choices=("slot-a", "slot-b"), default="slot-b")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
