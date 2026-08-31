#!/usr/bin/env python3
"""Detailed, read-only monitor for the TA-SAM online GreenRAN round."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import shutil
import sys
import time
from pathlib import Path
from typing import Any


RESET = "\033[0m"
COLORS = {
    "cyan": "\033[1;36m",
    "blue": "\033[1;34m",
    "green": "\033[1;32m",
    "yellow": "\033[1;33m",
    "red": "\033[1;31m",
    "dim": "\033[2m",
}

STAGES = (
    "allowed_bootstrap",
    "allowed_stable",
    "camera_conditional",
    "camera_blocked",
    "vehicle_conditional",
    "vehicle_blocked",
    "app2_conditional",
    "app2_blocked",
    "allowed_recovery",
)
DEFAULT_TARGET_TRANSITIONS = 1500


def paint(value: Any, color: str, enabled: bool = True) -> str:
    text = str(value)
    return f"{COLORS[color]}{text}{RESET}" if enabled else text


def mark(ok: bool, yes: str = "OK", no: str = "ATENÇÃO", enabled: bool = True) -> str:
    return paint(yes if ok else no, "green" if ok else "red", enabled)


def fmt(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/d"


def progress_bar(current: int, target: int, width: int = 30) -> str:
    ratio = min(1.0, max(0.0, current / max(1, target)))
    filled = int(width * ratio)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def pid_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def db_snapshot(db: Path) -> dict[str, Any]:
    empty = {
        "snapshots": 0,
        "metrics": 0,
        "decisions": 0,
        "comparisons": 0,
        "judge": 0,
        "envelope_applied": 0,
        "envelope_supported": False,
        "tasam_checkpoint": 0,
        "tasam_mixed": 0,
        "tasam_heuristic": 0,
        "tasam_valid": 0,
        "envelope_last_minute": 0,
        "tasam_checkpoint_last_minute": 0,
        "tasam_mixed_last_minute": 0,
        "tasam_heuristic_last_minute": 0,
        "tasam_valid_last_minute": 0,
        "floor_bad": 0,
        "pdcp_real": False,
        "proxy": 0,
        "real_samples": 0,
        "latest": {},
        "latest_decision": {},
        "latest_allocation": {},
        "stage_counts": {stage: 0 for stage in STAGES},
        "rows_last_minute": 0,
        "decisions_last_minute": 0,
    }
    if not db.exists():
        return empty
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            result = dict(empty)
            decision_columns = {
                row[1] for row in conn.execute("pragma table_info(decisions_history)").fetchall()
            }
            result["envelope_supported"] = "tasam_policy_envelope_applied" in decision_columns
            result["snapshots"] = int(conn.execute("select count(*) from marl_global_state_history").fetchone()[0] or 0)
            result["metrics"] = int(conn.execute("select count(*) from extended_metrics").fetchone()[0] or 0)
            result["decisions"] = int(conn.execute("select count(*) from decisions_history").fetchone()[0] or 0)
            result["comparisons"] = int(conn.execute("select count(*) from marl_shadow_comparison_history").fetchone()[0] or 0)
            try:
                result["judge"] = int(conn.execute("select count(*) from judge_outcome_history").fetchone()[0] or 0)
            except sqlite3.OperationalError:
                pass
            if result["envelope_supported"]:
                try:
                    result["envelope_applied"] = int(conn.execute(
                        "select coalesce(sum(tasam_policy_envelope_applied), 0) from decisions_history"
                    ).fetchone()[0] or 0)
                except sqlite3.OperationalError:
                    pass
                try:
                    row = conn.execute(
                        """
                        select
                          coalesce(sum(case when tasam_source='checkpoint' then 1 else 0 end), 0),
                          coalesce(sum(case when tasam_source='mixed' then 1 else 0 end), 0),
                          coalesce(sum(case when tasam_source='heuristic' then 1 else 0 end), 0),
                          coalesce(sum(case when tasam_proposal_valid=1 then 1 else 0 end), 0),
                          coalesce(sum(case when tasam_policy_envelope_applied=1 and timestamp >= ? then 1 else 0 end), 0)
                        from decisions_history
                        """, (int(time.time()) - 60,),
                    ).fetchone()
                    if row:
                        result["tasam_checkpoint"], result["tasam_mixed"], result["tasam_heuristic"], result["tasam_valid"], result["envelope_last_minute"] = [int(value or 0) for value in row]
                    row = conn.execute(
                        """
                        select
                          coalesce(sum(case when tasam_source='checkpoint' then 1 else 0 end), 0),
                          coalesce(sum(case when tasam_source='mixed' then 1 else 0 end), 0),
                          coalesce(sum(case when tasam_source='heuristic' then 1 else 0 end), 0),
                          coalesce(sum(case when tasam_proposal_valid=1 then 1 else 0 end), 0)
                        from decisions_history where timestamp >= ?
                        """, (int(time.time()) - 60,),
                    ).fetchone()
                    if row:
                        result["tasam_checkpoint_last_minute"], result["tasam_mixed_last_minute"], result["tasam_heuristic_last_minute"], result["tasam_valid_last_minute"] = [int(value or 0) for value in row]
                except sqlite3.OperationalError:
                    pass
            try:
                row = conn.execute(
                    """
                    select collector_mode, real_latency_sample_count,
                           proxy_latency_sample_count, pdcp_stale,
                           latency_p95_us, cvar_per_ue_us, throughput_kbps,
                           timestamp, sim_time_s, throughput_source,
                           total_active_ues, total_active_cameras
                    from extended_metrics order by timestamp desc limit 1
                    """
                ).fetchone()
                if row:
                    result["latest"] = dict(zip((
                        "collector_mode", "real_samples", "proxy_samples", "pdcp_stale",
                        "p95_us", "cvar_us", "throughput_kbps", "timestamp",
                        "sim_time_s", "throughput_source", "active_ues", "active_cameras",
                    ), row))
                    result["proxy"] = int(row[2] or 0)
                    result["real_samples"] = int(row[1] or 0)
                    result["pdcp_real"] = row[0] == "pdcp_real"
            except sqlite3.OperationalError:
                pass
            try:
                result["floor_bad"] = int(conn.execute("select count(*) from resource_allocation_history where floor_feasible=0 or per_ue_floor_violation_count>0").fetchone()[0] or 0)
            except sqlite3.OperationalError:
                pass
            try:
                rows = conn.execute(
                    """
                    select collection_event_stage_name, count(*)
                    from decisions_history group by collection_event_stage_name
                    """
                ).fetchall()
                for stage, count in rows:
                    if stage in result["stage_counts"]:
                        result["stage_counts"][stage] = int(count or 0)
            except sqlite3.OperationalError:
                pass
            try:
                latest_sql = """
                    select timestamp, datetime, decision, reason, confidence,
                           collection_event_stage_name, allocation_state,
                           healthy_streak, selected_assistant, tasam_source,
                           tasam_proposal_valid, tasam_confidence
                """
                latest_keys = [
                    "timestamp", "datetime", "decision", "reason", "confidence",
                    "stage", "allocation_state", "healthy_streak", "selected_assistant",
                    "tasam_source", "tasam_proposal_valid", "tasam_confidence",
                ]
                if result["envelope_supported"]:
                    latest_sql = latest_sql.replace(
                        "tasam_proposal_valid, tasam_confidence",
                        "tasam_proposal_valid, tasam_policy_envelope_applied, tasam_confidence",
                    )
                    latest_keys.insert(-1, "envelope_applied")
                row = conn.execute(latest_sql + " from decisions_history order by timestamp desc limit 1").fetchone()
                if row:
                    result["latest_decision"] = dict(zip(latest_keys, row))
            except sqlite3.OperationalError:
                pass
            try:
                row = conn.execute(
                    """
                    select timestamp, allocation_state, r_ran, r_ai,
                           floor_total_ran, floor_total_ai,
                           per_ue_floor_violation_count, floor_feasible
                    from resource_allocation_history order by timestamp desc limit 1
                    """
                ).fetchone()
                if row:
                    result["latest_allocation"] = dict(zip((
                        "timestamp", "allocation_state", "r_ran", "r_ai",
                        "floor_total_ran", "floor_total_ai", "floor_violations",
                        "floor_feasible",
                    ), row))
            except sqlite3.OperationalError:
                pass
            now = int(time.time())
            try:
                result["rows_last_minute"] = int(conn.execute(
                    "select count(*) from extended_metrics where timestamp >= ?", (now - 60,)
                ).fetchone()[0] or 0)
                result["decisions_last_minute"] = int(conn.execute(
                    "select count(*) from decisions_history where timestamp >= ?", (now - 60,)
                ).fetchone()[0] or 0)
            except sqlite3.OperationalError:
                pass
            return result
    except sqlite3.Error:
        return empty


def rate_and_eta(db: Path, count: int, target: int = DEFAULT_TARGET_TRANSITIONS) -> tuple[float, str]:
    if not db.exists() or count <= 1:
        return 0.0, "calculando"
    try:
        with sqlite3.connect(str(db)) as conn:
            # Ignore long pauses/restarts. The latest 20 snapshots represent
            # the currently active runtime and give a stable ETA.
            rows = conn.execute("select timestamp from marl_global_state_history order by id desc limit 20").fetchall()
        values = [float(row[0]) for row in rows]
        if len(values) < 2 or values[0] <= values[-1]:
            return 0.0, "calculando"
        rate = (len(values) - 1) / (values[0] - values[-1])
        remaining = max(target - count, 0)
        if rate <= 0:
            return 0.0, "calculando"
        return rate, time.strftime("%Hh%Mm", time.gmtime(min(remaining / rate, 86399)))
    except sqlite3.Error:
        return 0.0, "calculando"


def recent_export(state_dir: Path) -> dict[str, Any]:
    candidates = sorted(state_dir.glob("candidates/candidate_*/recent_export_summary.json"))
    return read_json(candidates[-1]) if candidates else {}


def candidate_summary(status: dict[str, Any]) -> dict[str, Any]:
    checkpoint = status.get("candidate_checkpoint")
    if not checkpoint:
        return {}
    path = Path(str(checkpoint)) / "tasam_marl_summary.json"
    summary = read_json(path)
    metrics = summary.get("final_metrics") or {}
    return {
        "epochs": summary.get("completed_epochs", summary.get("epochs")),
        "target_epochs": summary.get("target_epochs"),
        "eval_return": metrics.get("eval_return"),
        "critic_loss": metrics.get("critic_loss"),
        "action_var_mean": metrics.get("action_var_mean"),
        "selected_fraction": metrics.get("selected_fraction"),
        "policy_entropy": metrics.get("policy_entropy"),
        "sam_mode": summary.get("sam_mode"),
    }


def age_text(path: Path) -> str:
    try:
        age = max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return "ausente"
    if age < 2:
        return "agora"
    if age < 60:
        return f"{age:.0f}s atrás"
    return f"{age / 60:.1f}min atrás"


def age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def training_snapshot(state_dir: Path) -> dict[str, Any]:
    """Read the true-online writer status and normalize its progress fields."""
    path = state_dir / "tasam_true_online_real" / "true_online_status.json"
    payload = read_json(path)
    target = int(
        payload.get("min_trainable_transitions")
        or payload.get("target_transitions")
        or DEFAULT_TARGET_TRANSITIONS
    )
    return {
        "path": path,
        "status": str(payload.get("status", "unknown") or "unknown"),
        "reason": str(payload.get("reason", "") or ""),
        "written": int(payload.get("written_transitions", 0) or 0),
        "target": target,
        "updates": int(payload.get("updates_completed", 0) or 0),
        "trace": str(payload.get("trace_jsonl", "") or ""),
    }


def controlled_snapshot(state_dir: Path, status: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Expose the versioned controller separately from the generic exporter."""
    payload = status or state
    return {
        "updates": int(payload.get("updates_completed", 0) or 0),
        "update_status": str((payload.get("last_update_result") or {}).get("status", "idle") or "idle"),
        "rollback_count": int(payload.get("rollback_count", 0) or 0),
        "reward_enabled": bool(payload.get("reward_enabled", False)),
        "candidate_shadow": bool(payload.get("candidate_checkpoint")),
        "candidate_shadow_started": int(payload.get("candidate_shadow_started_decisions", 0) or 0),
        "candidate_shadow_until": int(payload.get("candidate_shadow_until_decisions", 0) or 0),
    }


def xapp_snapshot(state_dir: Path) -> dict[str, dict[str, Any]]:
    """Normalize xApp health, including file-mode optional sockets."""
    payload = read_json(state_dir / "xapp_health.json")
    result = {}
    for name in ("SLICER", "ENERGY", "VEHICLE"):
        item = payload.get(name) or {}
        result[name] = {
            "status": str(item.get("status", "UNKNOWN") or "UNKNOWN"),
            "mode": str(item.get("transport_mode", item.get("mode", "unknown")) or "unknown"),
            "required": bool(item.get("socket_required", False)),
            "socket": str(item.get("socket_status", item.get("socket", "")) or ""),
            "cycle": item.get("last_cycle"),
        }
    return result


def render(args: argparse.Namespace) -> None:
    state_dir = args.state_dir
    status_path = state_dir / "online_status.json"
    status = read_json(status_path)
    state = read_json(state_dir / "online_state.json")
    db_info = db_snapshot(state_dir / "rapp_data_lake.db")
    training = training_snapshot(state_dir)
    controlled = controlled_snapshot(state_dir, status, state)
    xapps = xapp_snapshot(state_dir)
    scenario = read_json(state_dir / "article00_scenario_control.json")
    effective_status = status or state
    target = training["target"]
    written = training["written"] or db_info["decisions"]
    rate, eta = rate_and_eta(state_dir / "rapp_data_lake.db", written, target)
    guard = effective_status.get("guard") or {}
    rollout = effective_status.get("rollout") or {}
    update = effective_status.get("last_update_result") or {}
    metric_path = state_dir / "xapp_metrics" / "extended_metrics.json"
    trace_path = state_dir / "tasam_true_online_real" / "tasam_true_online_real_trace.jsonl"
    status_age = age_text(status_path)
    metric_age = age_seconds(metric_path)
    runtime_alive = str(effective_status.get("status", "")).lower() == "running" and (
        age_seconds(status_path) is not None and age_seconds(status_path) < 90
    )
    process_state = {
        "runtime": runtime_alive,
        "rApp": pid_alive(state_dir / "rapp.pid") or runtime_alive,
        "ns-3": pid_alive(state_dir / "ns3_supervisor.pid") or (runtime_alive and metric_age is not None and metric_age < 90),
        "controlador": pid_alive(state_dir / "online_controller.pid") or runtime_alive,
    }
    export = recent_export(state_dir)
    candidate = candidate_summary(effective_status)
    rollout_stage = str(rollout.get("stage", effective_status.get("stage", "")))
    stage_started = int(effective_status.get("stage_started_decisions", 0) or 0)
    candidate_shadow_started = int(
        effective_status.get("candidate_shadow_started_decisions", 0)
        or rollout.get("candidate_shadow_started_decisions", 0)
        or 0
    )
    post_candidate_decisions = max(0, db_info["decisions"] - candidate_shadow_started)
    new_since_update = max(0, db_info["snapshots"] - int(effective_status.get("last_update_snapshot_count", 0) or 0))
    progress = min(100.0, max(0.0, (written / target * 100.0) if target else 0.0))
    color = not args.no_color
    width = 106
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")
    print(paint("╔" + "═" * width + "╗", "cyan", color))
    title = f" TA-SAM ONLINE | GREENRAN | SEED 45 | {state_dir.name} "
    print(paint("║" + title.center(width) + "║", "cyan", color))
    print(paint("╚" + "═" * width + "╝", "cyan", color))
    status_ok = str(effective_status.get("status", "")).lower() == "running" and runtime_alive
    print(f"Execução: {mark(status_ok, 'RUNNING', 'PARADO/SEM PULSO', color)} | status={status_age} | métricas={age_text(metric_path)}")
    print("Runtime: " + "  ".join(f"{name}={mark(value, enabled=color)}" for name, value in process_state.items()))
    print()
    print(paint("[ PROGRESSO DO TREINO ONLINE ]", "blue", color))
    print(f"  {progress_bar(written, target)} {written:,}/{target:,} ({progress:.1f}%) | faltam={max(target - written, 0):,}")
    print(f"  cadência={rate:.2f}/s | ETA ~{eta} | últimas 60s: métricas={db_info['rows_last_minute']} decisões={db_info['decisions_last_minute']}")
    envelope_text = (
        f"{db_info['envelope_applied']:,} persistidos"
        if db_info['envelope_supported']
        else "n/d (schema anterior; entra no próximo restart)"
    )
    print(f"  SQLite: métricas={db_info['metrics']:,} snapshots={db_info['snapshots']:,} decisões={db_info['decisions']:,} judge={db_info['judge']:,} shadow={db_info['comparisons']:,} ARMD→TA-SAM={envelope_text}")
    print(f"  controlador online: updates={controlled['updates']} ({controlled['update_status']}) | rollback={controlled['rollback_count']} | desde último update={new_since_update:,}")
    print(f"  exporter genérico: {training['status']} | transições válidas={training['written']:,}/{training['target']:,}")
    if training["reason"]:
        print(f"  motivo: {training['reason']}")
    fraction = float(rollout.get('fraction', effective_status.get('rollout_fraction', 0)) or 0) * 100
    rollout_label = rollout.get('stage', effective_status.get('stage', 'n/d'))
    print(f"  rollout={paint(f'{rollout_label} ({fraction:.0f}%)', 'green' if fraction >= 100 else 'yellow', color)}")
    print()
    print(paint("[ CENÁRIO E POLÍTICA ]", "blue", color))
    stage = scenario.get("collection_event_stage_name", scenario.get("scenario", status.get("stage", "n/d")))
    print(f"  perfil={scenario.get('collection_event_profile', 'n/d')} | ciclo={scenario.get('collection_event_cycle', 'n/d')} | estágio={stage} | restante={fmt(scenario.get('stage_remaining_s'), 1)}s")
    print(f"  checkpoint ativo: {effective_status.get('active_checkpoint', 'n/d')}")
    print(f"  último update={update.get('status', 'idle')} | rollback_count={effective_status.get('rollback_count', 0)}")
    print(f"  recompensa={mark(bool(effective_status.get('reward_enabled', True)), 'ATIVA', 'DESATIVADA', color)} | fonte={effective_status.get('reward_source', 'rApp Judge')}")
    print(f"  modo de crédito={effective_status.get('reward_mode', 'n/d')}")
    if effective_status.get('candidate_checkpoint'):
        print(f"  candidato: {effective_status.get('candidate_checkpoint')}")
        if candidate_shadow_started:
            shadow_until = int(
                effective_status.get("candidate_shadow_until_decisions", 0)
                or rollout.get("candidate_shadow_until_decisions", 0)
                or candidate_shadow_started + 30
            )
            shadow_total = max(1, shadow_until - candidate_shadow_started)
            print(
                f"  TA-SAM ativo: aplicação=100% | candidato shadow: "
                f"{min(post_candidate_decisions, shadow_total)}/{shadow_total} decisões"
            )
    if candidate:
        print("Qualidade do candidato:")
        print(f"  treino={candidate.get('epochs', 'n/d')}/{candidate.get('target_epochs', 'n/d')} épocas | modo={candidate.get('sam_mode', 'n/d')}")
        print(f"  eval_return={fmt(candidate.get('eval_return'))} | critic_loss={fmt(candidate.get('critic_loss'))} | ação_var={fmt(candidate.get('action_var_mean'), 4)}")
        print(f"  selected_fraction={fmt(candidate.get('selected_fraction'))} | entropia={fmt(candidate.get('policy_entropy'))}")
        try:
            if float(candidate.get("action_var_mean")) < 0.001:
                print("  ALERTA: variância muito baixa; possível colapso para ação quase única.")
        except (TypeError, ValueError):
            pass
    if effective_status.get("candidate_checkpoint") and candidate_shadow_started:
        print(f"Validação shadow pós-candidato: {post_candidate_decisions}/30 decisões")
    print()
    latest = db_info["latest"]
    print(paint("[ TELEMETRIA REAL / PROVENIÊNCIA ]", "blue", color))
    print(f"  collector_mode={latest.get('collector_mode', 'n/d')} | PDCP real={mark(db_info['pdcp_real'], 'SIM', 'NÃO', color)} | proxy={mark(db_info['proxy'] == 0, '0', str(db_info['proxy']), color)} | stale={latest.get('pdcp_stale', 'n/d')}")
    print(f"  amostras reais={db_info['real_samples']} | P95={fmt(float(latest.get('p95_us', 0) or 0) / 1000)} ms | CVaR={fmt(float(latest.get('cvar_us', 0) or 0) / 1000)} ms | throughput={fmt(float(latest.get('throughput_kbps', 0) or 0) / 1000)} Mbps")
    print(f"  fonte throughput={latest.get('throughput_source', 'n/d')} | UEs={latest.get('active_ues', 'n/d')} | câmeras={latest.get('active_cameras', 'n/d')} | sim_time={fmt(latest.get('sim_time_s'), 1)}s")
    floor_label = 'OK' if db_info['floor_bad'] == 0 else f"{db_info['floor_bad']} violações"
    allocation = db_info.get("latest_allocation") or {}
    print(f"  piso/rollback={mark(db_info['floor_bad'] == 0 and not guard.get('rollback', False), floor_label, floor_label, color)} | RAN={fmt(float(allocation.get('r_ran', 0) or 0) * 100, 1)}% | IA={fmt(float(allocation.get('r_ai', 0) or 0) * 100, 1)}%")
    print()
    print(paint("[ COBERTURA DOS 9 ESTÁGIOS ]", "blue", color))
    current_stage = str(stage)
    stage_cells = []
    for stage_name in STAGES:
        count = db_info["stage_counts"].get(stage_name, 0)
        marker = "*" if stage_name == current_stage else " "
        stage_cells.append(f"{marker}{stage_name[:19]:19}={count:4d}")
    for index in range(0, len(stage_cells), 3):
        print("  " + " | ".join(stage_cells[index:index + 3]))
    print()
    print(paint("[ GUARDAS ONLINE ]", "blue", color))
    guard_ok = not guard.get('rollback', False) and int(guard.get('floor_violations', 0) or 0) == 0 and db_info['floor_bad'] == 0
    print(f"  estado={mark(guard_ok, 'SEM VIOLAÇÕES', 'ATENÇÃO', color)} | floor_violations={guard.get('floor_violations', 0)} | critical_streak={guard.get('critical_streak', 0)} | rollback={guard.get('rollback', False)}")
    print(f"  score_delta_médio={fmt(guard.get('avg_score_delta'), 4)} | motivo={guard.get('reason', 'n/d')}")
    print()
    print(paint("[ JUDGE / ASSISTENTES / xApps ]", "blue", color))
    latest_decision = db_info.get("latest_decision") or {}
    print(f"  última decisão={latest_decision.get('decision', 'n/d')} | estágio={latest_decision.get('stage', 'n/d')} | confiança={fmt(float(latest_decision.get('confidence', 0) or 0) * 100, 1)}%")
    print(
        f"  TA-SAM fonte (histórico): checkpoint={db_info['tasam_checkpoint']:,} | mixed={db_info['tasam_mixed']:,} | "
        f"heuristic={db_info['tasam_heuristic']:,} | válidas={db_info['tasam_valid']:,}"
    )
    print(
        f"  TA-SAM fonte (últimos 60s): checkpoint={db_info['tasam_checkpoint_last_minute']:,} | "
        f"mixed={db_info['tasam_mixed_last_minute']:,} | heuristic={db_info['tasam_heuristic_last_minute']:,} | "
        f"válidas={db_info['tasam_valid_last_minute']:,} | envelopes={db_info['envelope_last_minute']:,}"
    )
    print(
        f"  última origem={latest_decision.get('tasam_source', 'n/d')} | "
        f"proposta_válida={latest_decision.get('tasam_proposal_valid', 'n/d')} | "
        f"envelope={latest_decision.get('envelope_applied', 'n/d')} | "
        f"selecionado={latest_decision.get('selected_assistant', 'n/d')} | "
        f"confiança TA-SAM={fmt(float(latest_decision.get('tasam_confidence', 0) or 0) * 100, 1)}%"
    )
    if latest_decision.get("reason"):
        print(f"  razão: {latest_decision.get('reason')}")
    if rollout_stage == "shadow" and not effective_status.get("candidate_checkpoint"):
        print("  ARMD + TA-SAM calculados em todas as decisões; aplicação ainda 0% durante o shadow.")
    elif effective_status.get("candidate_checkpoint") and candidate_shadow_started:
        print("  TA-SAM ativo aplicado em 100%; novo candidato somente em shadow, sem alterar o tráfego.")
    elif rollout_stage == "full":
        print("  ARMD + TA-SAM calculados e aplicados em 100% das decisões elegíveis; rApp Judge final.")
    else:
        print("  ARMD + TA-SAM calculados em todas as decisões; aplicação limitada pelo canário.")
    for name, item in xapps.items():
        socket_label = "obrigatório" if item["required"] else "opcional"
        print(f"  {name:7}={item['status']:<12} modo={item['mode']:<12} socket={socket_label:<10} ciclo={item['cycle'] if item['cycle'] is not None else 'n/d'}")
    if export:
        print()
        print(f"  exportação: bruto={export.get('candidate_snapshots', 'n/d')} | válidos={export.get('written_transitions', 'n/d')} | feedback={export.get('judge_feedback_coverage', 'n/d')}")
        print(f"  crédito TA-SAM médio={export.get('tasam_credit_mean', 'n/d')} | fontes={export.get('reward_source_counts', {})}")
    try:
        disk = shutil.disk_usage(state_dir)
        free_gb = disk.free / (1024 ** 3)
    except OSError:
        free_gb = 0.0
    try:
        trace_mb = trace_path.stat().st_size / (1024 ** 2)
    except OSError:
        trace_mb = 0.0
    print()
    print(paint("[ ARTEFATOS ]", "blue", color))
    print(f"  trace={trace_mb:.2f} MiB | db={(state_dir / 'rapp_data_lake.db').stat().st_size / (1024 ** 2):.2f} MiB" if (state_dir / 'rapp_data_lake.db').exists() else "  trace/db=ausentes")
    print(f"  disco livre={paint(f'{free_gb:.2f} GB', 'green' if free_gb >= 5 else 'red', color)} | cenário={age_text(state_dir / 'article00_scenario_control.json')}")
    print()
    print(paint("Ctrl+C fecha somente este painel; a coleta e o treino online continuam.", "dim", color), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-color", action="store_true", help="Desativa as cores ANSI")
    args = parser.parse_args()
    while True:
        render(args)
        if args.once:
            return 0
        time.sleep(max(args.interval, 2.0))


if __name__ == "__main__":
    raise SystemExit(main())
