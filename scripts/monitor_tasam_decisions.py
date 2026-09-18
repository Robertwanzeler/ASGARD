#!/usr/bin/env python3
"""Read-only monitor for TA-SAM decisions and online-run health.

The monitor is safe to run while the simulator is writing SQLite/JSON files.
It never stops a process and never changes the run directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


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

RESET = "\033[0m"
COLORS = {
    "cyan": "\033[1;36m",
    "blue": "\033[1;34m",
    "green": "\033[1;32m",
    "yellow": "\033[1;33m",
    "red": "\033[1;31m",
    "dim": "\033[2m",
}


def color(text: Any, name: str, enabled: bool) -> str:
    value = str(text)
    return f"{COLORS[name]}{value}{RESET}" if enabled else value


def status(ok: bool, enabled: bool) -> str:
    return color("OK", "green", enabled) if ok else color("ATENÇÃO", "red", enabled)


def number(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/d"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def age(path: Path) -> str:
    try:
        seconds = max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return "ausente"
    if seconds < 2:
        return "agora"
    if seconds < 60:
        return f"{seconds:.0f}s atrás"
    return f"{seconds / 60:.1f}min atrás"


def is_recent(path: Path, limit_seconds: float = 30.0) -> bool:
    try:
        return max(0.0, time.time() - path.stat().st_mtime) <= limit_seconds
    except OSError:
        return False


def pid_state(path: Path) -> str:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return "parado"
    return f"ativo (PID {pid})"


def command_process_state(run_dir: Path, needle: str) -> str:
    """Find a process for this run without trusting an optional pid file."""
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            cmdline_path = Path("/proc") / entry / "cmdline"
            try:
                command = cmdline_path.read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except (OSError, ValueError):
                continue
            if needle in command and str(run_dir) in command:
                return f"ativo (PID {entry})"
    except OSError:
        pass
    return "parado"


def process_state(run_dir: Path, pid_filename: str, needle: str) -> str:
    state = pid_state(run_dir / pid_filename)
    return state if state != "parado" else command_process_state(run_dir, needle)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"pragma table_info({table})")}
    except sqlite3.Error:
        return set()


def count_where(conn: sqlite3.Connection, table: str, columns: set[str], condition: str) -> int:
    if not columns:
        return 0
    try:
        return int(conn.execute(f"select count(*) from {table} where {condition}").fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


def readonly_connection(db: Path) -> tuple[sqlite3.Connection, str]:
    """Open a result database on regular and lock-limited mounted filesystems.

    ``mode=ro`` remains the live-safe path. Some removable filesystems reject
    that URI even after a run has finished; immutable mode is a read-only
    fallback for those completed result databases.
    """
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        # SQLite can defer opening an unsupported mounted file until the first
        # statement, so force a harmless read before choosing this mode.
        connection.execute("pragma schema_version").fetchone()
        return connection, "mode=ro"
    except sqlite3.Error:
        try:
            connection.close()
        except (UnboundLocalError, sqlite3.Error):
            pass
        connection = sqlite3.connect(f"file:{db}?immutable=1", uri=True, timeout=1.0)
        connection.execute("pragma schema_version").fetchone()
        return connection, "immutable"


def db_snapshot(db: Path) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "available": False,
        "read_mode": "unavailable",
        "decisions": 0,
        "judge": 0,
        "feedback": 0,
        "valid": 0,
        "applied": 0,
        "exact": 0,
        "armd": 0,
        "invalid": 0,
        "fallback": 0,
        "shadow_or_canary": 0,
        "no_feedback": 0,
        "category_errors": 0,
        "boundary_feedback": 0,
        "mean_continuous": None,
        "mean_category": None,
        "mean_training_category": None,
        "mean_training": None,
        "mean_error": None,
        "stages": Counter(),
        "matrix": Counter(),
        "latest": {},
    }
    if not db.is_file():
        return empty
    conn: sqlite3.Connection | None = None
    try:
        conn, read_mode = readonly_connection(db)
        try:
            conn.execute("pragma busy_timeout=1000")
            decisions_columns = table_columns(conn, "decisions_history")
            judge_columns = table_columns(conn, "judge_outcome_history")
            if not decisions_columns:
                return empty
            result = dict(empty)
            result["available"] = True
            result["read_mode"] = read_mode
            result["decisions"] = int(conn.execute("select count(*) from decisions_history").fetchone()[0] or 0)
            result["valid"] = count_where(conn, "decisions_history", decisions_columns, "tasam_proposal_valid = 1")
            applied_condition = "tasam_actuation_applied = 1" if "tasam_actuation_applied" in decisions_columns else "ta_sam_actuation_applied = 1"
            result["applied"] = count_where(conn, "decisions_history", decisions_columns, applied_condition)
            result["exact"] = count_where(conn, "decisions_history", decisions_columns, "proposal_applied_exactly = 1")
            result["armd"] = count_where(conn, "decisions_history", decisions_columns, "armd_enabled = 1")
            result["invalid"] = count_where(conn, "decisions_history", decisions_columns, "tasam_proposal_valid = 0")
            result["fallback"] = count_where(conn, "decisions_history", decisions_columns, "selected_assistant = 'rapp_policy'")
            if "control_trial_mode" in decisions_columns:
                result["shadow_or_canary"] = count_where(
                    conn,
                    "decisions_history",
                    decisions_columns,
                    "lower(coalesce(control_trial_mode, '')) in ('shadow', 'canary', 'canary_hold', 'live_fallback')",
                )
            try:
                rows = conn.execute(
                    "select collection_event_stage_name, count(*) from decisions_history "
                    "group by collection_event_stage_name"
                ).fetchall()
                result["stages"] = Counter({str(stage or "unknown"): int(count or 0) for stage, count in rows})
            except sqlite3.Error:
                pass
            latest_columns = [
                "timestamp", "decision", "collection_event_stage_name",
                "tasam_proposal_valid", "proposal_applied_exactly",
                "selected_assistant", "tasam_source", "armd_enabled",
            ]
            latest_columns = [column for column in latest_columns if column in decisions_columns]
            if latest_columns:
                try:
                    row = conn.execute(
                        "select " + ", ".join(latest_columns) +
                        " from decisions_history order by id desc limit 1"
                    ).fetchone()
                    if row:
                        result["latest"] = dict(zip(latest_columns, row))
                except sqlite3.Error:
                    pass
            if not judge_columns:
                result["no_feedback"] = result["decisions"]
                return result
            result["judge"] = int(conn.execute("select count(*) from judge_outcome_history").fetchone()[0] or 0)
            result["feedback"] = count_where(conn, "judge_outcome_history", judge_columns, "observed = 1")
            result["category_errors"] = count_where(conn, "judge_outcome_history", judge_columns, "tasam_category_error = 1")
            result["boundary_feedback"] = count_where(conn, "judge_outcome_history", judge_columns, "stage_boundary_feedback = 1")
            result["no_feedback"] = max(0, result["decisions"] - result["feedback"])
            try:
                strong_column = (
                    "tasam_training_category_credit"
                    if "tasam_training_category_credit" in judge_columns
                    else "tasam_category_credit"
                )
                training_column = (
                    "tasam_training_reward"
                    if "tasam_training_reward" in judge_columns
                    else strong_column
                )
                values = conn.execute(
                    "select tasam_continuous_reward, tasam_category_credit, "
                    f"{strong_column}, {training_column}, tasam_observed_error "
                    "from judge_outcome_history where observed = 1"
                ).fetchall()
                continuous: list[float] = []
                category: list[float] = []
                strong_category: list[float] = []
                training: list[float] = []
                errors: list[float] = []
                for continuous_value, category_value, strong_value, training_value, error_value in values:
                    try:
                        continuous_value = float(continuous_value)
                        category_value = float(category_value)
                        continuous.append(continuous_value)
                        category.append(category_value)
                        strong_category.append(float(strong_value))
                        training.append(float(training_value))
                    except (TypeError, ValueError):
                        pass
                    try:
                        errors.append(float(error_value))
                    except (TypeError, ValueError):
                        pass
                if continuous:
                    result["mean_continuous"] = sum(continuous) / len(continuous)
                if category:
                    result["mean_category"] = sum(category) / len(category)
                if strong_category:
                    result["mean_training_category"] = sum(strong_category) / len(strong_category)
                if training:
                    result["mean_training"] = sum(training) / len(training)
                if errors:
                    result["mean_error"] = sum(errors) / len(errors)
            except sqlite3.Error:
                pass
            if {"tasam_predicted_verdict", "tasam_observed_verdict"}.issubset(judge_columns):
                try:
                    rows = conn.execute(
                        "select tasam_predicted_verdict, tasam_observed_verdict "
                        "from judge_outcome_history where observed = 1"
                    ).fetchall()
                    result["matrix"] = Counter(
                        f"{str(predicted or 'UNKNOWN')} → {str(observed or 'UNKNOWN')}"
                        for predicted, observed in rows
                    )
                except sqlite3.Error:
                    pass
            return result
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return empty


def render(run_dir: Path, no_color: bool) -> None:
    manifest = read_json(run_dir / "arm_manifest.json")
    online = read_json(run_dir / "online_status.json") or read_json(run_dir / "online_state.json")
    db = db_snapshot(run_dir / "rapp_data_lake.db")
    online_fresh = online.get("status") == "running" and is_recent(run_dir / "online_status.json")
    update = online.get("last_update_result") or {}
    replay = online.get("last_replay") or update.get("replay") or {}
    checkpoint_metrics: dict[str, Any] = {}
    active_checkpoint = str(online.get("active_checkpoint", "") or "")
    curriculum: dict[str, Any] = {}
    if active_checkpoint:
        summary = read_json(Path(active_checkpoint) / "tasam_marl_summary.json")
        checkpoint_metrics = summary.get("final_metrics") or {}
        metadata = read_json(Path(active_checkpoint) / "tasam_marl_checkpoint_meta.json")
        candidate = metadata.get("category_head_pretraining") or summary.get("category_curriculum")
        curriculum = candidate if isinstance(candidate, dict) else {}
    color_enabled = not no_color
    total = int(db["decisions"])
    valid = int(db["valid"])
    feedback = int(db["feedback"])
    healthy = bool(
        db["available"]
        and total > 0
        and valid == total
        and int(db["applied"]) == total
        and int(db["exact"]) == total
        and int(db["invalid"]) == 0
        and int(db["fallback"]) == 0
        and int(db["shadow_or_canary"]) == 0
    )
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")
    print(color("=" * 104, "cyan", color_enabled))
    print(color(f" TA-SAM DECISIONS | {run_dir.name} ".center(104), "cyan", color_enabled))
    print(color("=" * 104, "cyan", color_enabled))
    print(f"Manifesto: {manifest.get('status', 'n/d')} | criado={manifest.get('created_at', 'n/d')} | online_status={age(run_dir / 'online_status.json')}")
    process_items = [
        f"{label}={process_state(run_dir, filename, needle)}"
        for label, filename, needle in (
            ("runtime", "greenran_online_runtime.pid", "run_tasam_online_arm.py"),
            ("controlador", "online_controller.pid", "run_tasam_online_controlled.py"),
            ("rApp", "rapp.pid", "rapp_orchestrator.py"),
            ("ns-3", "ns3_supervisor.pid", "run_tasam_online_arm.py"),
        )
    ]
    if online_fresh:
        # The long runner may live in a separate terminal PID namespace.  A
        # fresh online status plus advancing SQLite is stronger evidence of
        # liveness than a stale PID file in this monitor namespace.
        process_items[0] = "runtime=ativo (online_status recente)"
        process_items[1] = "controlador=ativo (online_status recente)"
        process_items[3] = "ns-3=ativo (online_status recente)"
    print("Processos: " + " | ".join(process_items))
    print()
    print(color("[ INTEGRIDADE DA EXECUÇÃO ]", "blue", color_enabled))
    print(f"  execução sem falha estrutural: {status(healthy, color_enabled)} | banco={db['read_mode']}")
    print(f"  decisões={total} | propostas TA-SAM válidas={valid}/{total} | ações aplicadas={db['applied']}/{total} | exactas={db['exact']}/{total}")
    print(f"  ARMD habilitado={db['armd']}/{total} | fallback rApp={db['fallback']} | shadow/canary/fallback-classificado={db['shadow_or_canary']}")
    print(f"  feedbacks observados={feedback}/{total} | sem feedback={db['no_feedback']} | fronteira de estágio={db['boundary_feedback']}")
    print(
        f"  updates online={online.get('updates_completed', 0)} | último update={update.get('status', 'idle')} | "
        f"replay={replay.get('total', 'n/d')} | estratégia={replay.get('prioritization', online.get('replay_strategy', 'n/d'))}"
    )
    if curriculum:
        samples = curriculum.get("samples") or {}
        validation = (curriculum.get("training") or {}).get("best_validation") or {}
        print(
            f"  currículo categórico={curriculum.get('source', 'n/d')}:{curriculum.get('version', 'n/d')} | "
            f"amostras={samples.get('total', 'n/d')} | validação acurácia={number(validation.get('accuracy'))}"
        )
    if replay:
        print(
            f"  erros categóricos no replay={replay.get('category_error_in_replay', 'n/d')} | "
            f"cópias adicionais={replay.get('category_error_repeated', 'n/d')} | "
            f"disponíveis={replay.get('category_error_available', 'n/d')}"
        )
        quotas = replay.get("replay_quotas") or {}
        if quotas:
            print(
                f"  quotas replay: CONDITIONAL={quotas.get('conditional_selected_unique', 0)} "
                f"(+{quotas.get('conditional_repeated', 0)} rep.) | "
                f"outros_erros={quotas.get('other_error_selected_unique', 0)} "
                f"(+{quotas.get('other_error_repeated', 0)} rep.) | "
                f"acertos={quotas.get('correct_selected_unique', 0)} "
                f"(+{quotas.get('correct_repeated', 0)} rep.)"
            )
    print(
        f"  cabeça categórica: disponível={bool(checkpoint_metrics or active_checkpoint and (Path(active_checkpoint) / 'tasam_marl_category_head.pt').is_file())} | "
        f"acurácia={number(checkpoint_metrics.get('category_accuracy'))} | "
        f"perda={number(checkpoint_metrics.get('category_loss'))} | "
        f"erro direcional={number(checkpoint_metrics.get('category_directional_error_rate'))} | "
        f"CONDITIONAL P/R/F1={number(checkpoint_metrics.get('conditional_precision'))}/"
        f"{number(checkpoint_metrics.get('conditional_recall'))}/"
        f"{number(checkpoint_metrics.get('conditional_f1'))}"
    )
    print()
    print(color("[ RECOMPENSAS DO TREINO ]", "blue", color_enabled))
    print(
        f"  recompensa contínua média={number(db['mean_continuous'])} | "
        f"crédito categórico canônico={number(db['mean_category'])} | "
        f"crédito categórico forte={number(db['mean_training_category'])}"
    )
    print(f"  training_reward médio={number(db['mean_training'])} | erro observado médio={number(db['mean_error'])} | erros categóricos={db['category_errors']}")
    print("  regra: training_reward = min(recompensa_contínua, crédito_categórico_forte)")
    print()
    print(color("[ COBERTURA DOS 9 ESTÁGIOS ]", "blue", color_enabled))
    stages: Counter[str] = db["stages"]
    print("  " + " | ".join(f"{stage}={stages.get(stage, 0)}" for stage in STAGES))
    print()
    print(color("[ ÚLTIMA DECISÃO ]", "blue", color_enabled))
    latest = db["latest"]
    if latest:
        print(
            f"  decisão={latest.get('decision', 'n/d')} | estágio={latest.get('collection_event_stage_name', 'n/d')} | "
            f"TA-SAM válida={latest.get('tasam_proposal_valid', 'n/d')} | aplicada exatamente={latest.get('proposal_applied_exactly', 'n/d')}"
        )
        print(
            f"  origem TA-SAM={latest.get('tasam_source', 'n/d')} | selecionado={latest.get('selected_assistant', 'n/d')} | "
            f"ARMD={latest.get('armd_enabled', 'n/d')}"
        )
    else:
        print("  ainda não há decisões no banco")
    print()
    print(color("[ MATRIZ TA-SAM: PREVISTO → OBSERVADO ]", "blue", color_enabled))
    matrix: Counter[str] = db["matrix"]
    print("  " + (" | ".join(f"{key}={value}" for key, value in sorted(matrix.items())) if matrix else "ainda sem feedback categórico"))
    print()
    print(color("Somente leitura: Ctrl+C fecha o painel e não interrompe a simulação.", "dim", color_enabled), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    run_dir = args.state_dir.resolve()
    while True:
        render(run_dir, args.no_color)
        if args.once:
            return 0
        time.sleep(max(2.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
