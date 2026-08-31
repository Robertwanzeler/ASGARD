#!/usr/bin/env python3
"""Painel compacto para acompanhar a coleta limpa TA-SAM em tempo real.

Este painel é somente leitura: não inicia, reinicia ou encerra nenhum serviço.
Use Ctrl-C para sair.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

try:
    from monitor_tasam_targeted_collection import record_is_valid_target
except ImportError:  # pragma: no cover - allows direct module reuse
    record_is_valid_target = None


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
TARGETS = {stage: 500 for stage in STAGES}
TOTAL_TARGET = sum(TARGETS.values())

STAGE_LABELS = {
    "allowed_bootstrap": "allowed bootstrap",
    "allowed_stable": "allowed stable",
    "camera_conditional": "camera cond.",
    "camera_blocked": "camera blocked",
    "vehicle_conditional": "vehicle cond.",
    "vehicle_blocked": "vehicle blocked",
    "app2_conditional": "app2 cond.",
    "app2_blocked": "app2 blocked",
    "allowed_recovery": "allowed recovery",
}

LINE_PATTERN = re.compile(
    r"(?P<body>.*?)(?:total=(?P<total>\d+)/(?P<target>\d+)\s+v(?:álidas?|alidas?)=(?P<valid>\d+)\s+rejeitadas?=(?P<rejected>\d+))"
)
STAGE_PATTERN = re.compile(r"(?P<stage>[a-z0-9_]+)=(?P<count>\d+)/(?P<target>\d+)")


def clear_screen() -> None:
    if sys_is_tty():
        print("\033[2J\033[H", end="")


def sys_is_tty() -> bool:
    return bool(getattr(__import__("sys"), "stdout").isatty())


def read_last_monitor_line(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        return next((line.strip() for line in reversed(lines) if line.strip()), "")
    except OSError:
        return ""


def read_first_monitor_timestamp(path: Path) -> float:
    """Use o primeiro timestamp do monitor para a ETA não depender do painel."""
    if not path.exists():
        return 0.0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
                if match:
                    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
    except (OSError, ValueError):
        pass
    return 0.0


def parse_progress(line: str) -> dict[str, object]:
    counts = {stage: 0 for stage in STAGES}
    for match in STAGE_PATTERN.finditer(line):
        stage = match.group("stage")
        if stage in counts:
            counts[stage] = int(match.group("count"))
    match = LINE_PATTERN.search(line)
    if match:
        total = int(match.group("total"))
        target = int(match.group("target"))
        valid = int(match.group("valid"))
        rejected = int(match.group("rejected"))
        timestamp = line.split("]", 1)[0].lstrip("[")
    else:
        total = sum(counts.values())
        target = TOTAL_TARGET
        valid = total
        rejected = 0
        timestamp = ""
    return {
        "counts": counts,
        "total": total,
        "target": target,
        "valid": valid,
        "rejected": rejected,
        "timestamp": timestamp,
    }


def read_final_summary(state_dir: Path) -> dict[str, object] | None:
    path = state_dir / "targeted_export" / "targeted_valid_summary.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    counts = payload.get("valid_counts")
    if not isinstance(counts, dict) or not payload.get("training_ready_for_merge"):
        return None
    normalized = {stage: int(counts.get(stage, 0) or 0) for stage in STAGES}
    return {
        "counts": normalized,
        "total": int(payload.get("written_rows") or sum(normalized.values())),
        "target": TOTAL_TARGET,
        "valid": int(payload.get("written_rows") or sum(normalized.values())),
        "rejected": sum((payload.get("rejected_counts") or {}).values()),
        "timestamp": str(payload.get("generated_at") or ""),
    }


def read_current_monitor_log(state_dir: Path) -> Path:
    """Choose the most recently updated monitor log for this state directory."""
    candidates = list(state_dir.glob("targeted_monitor*.log"))
    if not candidates:
        return state_dir / "targeted_monitor.log"
    return max(candidates, key=lambda path: path.stat().st_mtime)


def progress_bar(current: int, target: int, width: int = 30) -> str:
    ratio = min(1.0, max(0.0, current / max(1, target)))
    filled = int(width * ratio)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def read_last_decision(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            lines = handle.readlines()
        for raw in reversed(lines):
            if raw.strip():
                return json.loads(raw.decode("utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def read_raw_stage_stats(state_dir: Path) -> dict[str, Counter[str]]:
    """Return raw/valid/rejection counts from the monitor's latest export.

    The exporter rewrites this file while the collection is running, so
    malformed trailing lines are intentionally ignored. This function is
    read-only and never affects the collector or the targeted monitor.
    """
    stats = {
        "raw": Counter(),
        "valid": Counter(),
        "rejected": Counter(),
        "skews": defaultdict(list),
    }
    path = state_dir / "targeted_export" / ".work" / "latest_raw_export.jsonl"
    if not path.exists() or record_is_valid_target is None:
        return stats
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                stage = str(record.get("scenario_stage") or "")
                if stage not in STAGES:
                    continue
                stats["raw"][stage] += 1
                alignment = record.get("metrics_alignment") or {}
                for side in ("current", "next"):
                    skew = (alignment.get(side) or {}).get("skew_s")
                    try:
                        stats["skews"][stage].append(float(skew))
                    except (TypeError, ValueError):
                        pass
                keep, reason = record_is_valid_target(record)
                if keep:
                    stats["valid"][stage] += 1
                else:
                    stats["rejected"][f"{stage}:{reason}"] += 1
    except OSError:
        return stats
    return stats


def process_health(state_dir: Path) -> dict[str, bool]:
    try:
        output = subprocess.run(
            ["ps", "-eo", "args="], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return {}
    return {
        "coleta": "collection_event_alternator.py" in output and str(state_dir) in output,
        "ns-3": "Energy_saving_with_cell_utilization_scenario" in output,
        "métricas": "csv_to_metrics.py" in output and str(state_dir) in output,
        "xApp slicer": "xapp_slicer" in output,
        "xApp energy": "xapp_energy_saver" in output,
        "monitor": "monitor_tasam_targeted_collection.py" in output and str(state_dir) in output,
    }


def recent_real_errors(state_dir: Path) -> int:
    pattern = re.compile(
        r"\b(traceback|segmentation fault|address already in use|fatal error|no such file)\b"
        r"|^\s*(error|exception)\b",
        re.IGNORECASE,
    )
    count = 0
    for path in state_dir.glob("*.log"):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
        except OSError:
            continue
        count += sum(1 for line in lines if pattern.search(line))
    return count


def eta_text(total: int, started_at: float) -> str:
    if total <= 0 or started_at <= 0:
        return "calculando"
    rate = total / max(1.0, time.time() - started_at)
    remaining = max(0, TOTAL_TARGET - total)
    if rate <= 0:
        return "calculando"
    eta = timedelta(seconds=int(remaining / rate))
    return f"{rate:.2f}/s | ETA ~{eta}"


def render(
    state_dir: Path,
    progress: dict[str, object],
    decision: dict[str, object],
    started_at: float,
    raw_stats: dict[str, Counter[str]],
) -> None:
    clear_screen()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = int(progress["total"])
    target = int(progress["target"] or TOTAL_TARGET)
    valid = int(progress["valid"])
    rejected = int(progress["rejected"])
    pct = 100.0 * total / max(1, target)

    state = "CONCLUÍDA" if total >= target else "EM EXECUÇÃO"
    print(f"TA-SAM | COLETA LIMPA GREENRAN | {state} | painel somente leitura")
    print(f"Atualizado: {now}   Estado: {state_dir.name}")
    print(f"\nGERAL  {progress_bar(total, target)}  {total}/{target} ({pct:5.1f}%)")
    print(f"       válidas={valid}  rejeitadas={rejected}  {eta_text(total, started_at)}")
    print("\nSTAGES (meta de 500 cada)")
    for offset in range(0, len(STAGES), 3):
        row = []
        for stage in STAGES[offset : offset + 3]:
            count = int(progress["counts"][stage])
            row.append(f"{STAGE_LABELS[stage]:17} {count:>3}/500 {100.0 * count / 500:5.1f}%")
        print("   " + "   ".join(row))

    print("\nRASTREABILIDADE DO EXPORTADOR (bruto → válido)")
    for stage in STAGES:
        raw = int(raw_stats["raw"][stage])
        valid = int(raw_stats["valid"][stage])
        rejection_items = [
            f"{key.split(':', 1)[1]}={count}"
            for key, count in raw_stats["rejected"].items()
            if key.startswith(f"{stage}:")
        ]
        suffix = f" | rejeições: {', '.join(rejection_items)}" if rejection_items else ""
        skews = raw_stats["skews"].get(stage, [])
        distance = f" | distância máx={max(skews):.1f}s" if skews else ""
        warning = " | ATENÇÃO: há bruto, mas nenhum válido" if raw > 0 and valid == 0 else ""
        print(f"   {STAGE_LABELS[stage]:17} {raw:>3} bruto → {valid:>3} válidos{distance}{warning}{suffix}")

    print("\nÚLTIMA DECISÃO DO rApp")
    if decision:
        print(
            f"   {decision.get('datetime', '?')} | {decision.get('decision', '?')} | "
            f"{decision.get('action', '?')} | motivo: {decision.get('priority_violation') or decision.get('reason', '?')}"
        )
        print(
            f"   ARMD={'ON' if decision.get('armd_enabled') else 'OFF'}  "
            f"TA-SAM={'ON' if decision.get('tasam_enabled') else 'OFF'}  "
            f"vencedor={decision.get('advisor_arbitration_winner', '?')}"
        )
    else:
        print("   ainda não disponível")

    health = process_health(state_dir)
    print("\nSERVIÇOS")
    print("   " + " | ".join(f"{name}: {'OK' if ok else 'PARADO'}" for name, ok in health.items()))
    errors = recent_real_errors(state_dir)
    print(f"   erros reais recentes nos .log: {errors}")
    print("\nCtrl-C para sair | atualização automática")


def main() -> int:
    parser = argparse.ArgumentParser(description="Painel da coleta limpa TA-SAM")
    parser.add_argument(
        "--state-dir",
        default="/home/robert/orange_nuclear/runs/tasam_greenran_clean_all_20260807",
        help="diretório de estado da coleta",
    )
    parser.add_argument("--interval", type=float, default=5.0, help="segundos entre atualizações")
    parser.add_argument("--once", action="store_true", help="imprimir uma vez e sair")
    args = parser.parse_args()

    state_dir = Path(args.state_dir).expanduser().resolve()
    monitor_log = read_current_monitor_log(state_dir)
    started_at = read_first_monitor_timestamp(monitor_log) or time.time()
    while True:
        # The targeted monitor can spend longer than the display interval
        # rewriting a large SQLite export. Prefer the raw export's validated
        # counts so an old monitor log cannot make the dashboard show a stale
        # run (for example 225 rows after a reboot).
        raw_stats = read_raw_stage_stats(state_dir)
        raw_valid_total = sum(int(raw_stats["valid"][stage]) for stage in STAGES)
        if raw_valid_total:
            bounded_counts = {
                stage: min(500, int(raw_stats["valid"][stage])) for stage in STAGES
            }
            total = sum(bounded_counts.values())
            progress = {
                "counts": bounded_counts,
                "total": total,
                "target": TOTAL_TARGET,
                "valid": total,
                "rejected": sum(raw_stats["rejected"].values()),
                "timestamp": "",
            }
        else:
            monitor_log = read_current_monitor_log(state_dir)
            progress = parse_progress(read_last_monitor_line(monitor_log))
            if int(progress["total"]) == 0:
                progress = read_final_summary(state_dir) or progress
        decision = read_last_decision(state_dir / "rapp_decisions.jsonl")
        render(state_dir, progress, decision, started_at, raw_stats)
        if args.once:
            return 0
        try:
            time.sleep(max(1.0, args.interval))
        except KeyboardInterrupt:
            print("\nPainel encerrado; a coleta continua em execução.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
