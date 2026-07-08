#!/usr/bin/env python3
"""
Monitor de treino TA-SAM para GreenRAN.

Trilhas suportadas:
  - treino offline do artigo: `tasam_marl_summary.json`
  - treino online TA-SAM fiel: `online_tasam_marl_summary.json`

Uso:
    python3 scripts/monitor_training.py --dir runs/tasam_article_reproduction/tasam_selective
    python3 scripts/monitor_training.py --dir runs/sac_bootstrap/online_tasam_marl --refresh 3
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ONLINE_DIR = ROOT / "runs" / "sac_bootstrap" / "online_tasam_marl"


def read_offline_summary(dir_path: Path) -> dict | None:
    path = dir_path / "tasam_marl_summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def read_online_summary(dir_path: Path) -> dict | None:
    path = dir_path / "online_tasam_marl_summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def read_training_log(dir_path: Path) -> list[str]:
    history_path = dir_path / "online_tasam_marl_history.jsonl"
    if history_path.exists():
        try:
            rows = []
            for line in history_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                rows.append(
                    "ep={episode} step={global_step} ret={episode_return:.4f} replay={replay_size} actor={actor_loss:.4f} critic={critic_loss:.4f} td={effective_td_var_threshold:.4f}".format(
                        episode=int(payload.get("episode", 0) or 0),
                        global_step=int(payload.get("global_step", 0) or 0),
                        episode_return=float(payload.get("episode_return", 0.0) or 0.0),
                        replay_size=int(payload.get("replay_size", 0) or 0),
                        actor_loss=float(payload.get("actor_loss", 0.0) or 0.0),
                        critic_loss=float(payload.get("critic_loss", 0.0) or 0.0),
                        effective_td_var_threshold=float(payload.get("effective_td_var_threshold", 0.0) or 0.0),
                    )
                )
            return rows
        except (json.JSONDecodeError, OSError, ValueError):
            return []
    log_path = dir_path / "training_log.txt"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text().strip().split("\n")
        return [l for l in lines if l.strip()]
    except OSError:
        return []


def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def _show_recent_lines(log_lines: list[str], count: int = 12) -> None:
    recent = log_lines[-count:] if log_lines else []
    if not recent:
        print("  (nenhum history/log de treino encontrado ainda)")
        return
    for line in recent:
        print(f"  {line}")


def show_offline_dashboard(summary: dict, log_lines: list[str]) -> None:
    os.system("clear" if os.name == "posix" else "cls")
    print("=" * 68)
    print("  GREENRAN TA-SAM - MONITOR DE TREINO OFFLINE")
    print("=" * 68)

    final_metrics = summary.get("final_metrics", {}) or {}
    history = summary.get("history", []) or []
    print(
        "  Backend: {}  |  Modo SAM: {}  |  Track: {}".format(
            summary.get("trainer_backend", "unknown"),
            summary.get("sam_mode", "unknown"),
            summary.get("experiment_track", "unknown"),
        )
    )
    print(f"  Trace: {summary.get('trace_jsonl', 'N/A')}")
    print("-" * 68)
    print(f"  Epochs: {summary.get('epochs', 0)}")
    print(
        "  Topologia: du_count={} du_state_dim={} global_state_dim={}".format(
            summary.get("du_count", 0),
            summary.get("du_state_dim", 0),
            summary.get("global_state_dim", 0),
        )
    )
    print(
        "  SAM: actor_rho={} -> {} | critic_rho={} -> {}".format(
            summary.get("actor_sam_rho", 0.0),
            summary.get("actor_sam_rho_final", 0.0),
            summary.get("critic_sam_rho", 0.0),
            summary.get("critic_sam_rho_final", 0.0),
        )
    )
    print(
        "  Otimizacao: lr={} alpha_lr={} gamma={} tau={}".format(
            summary.get("lr", "n/a"),
            summary.get("alpha_lr", "n/a"),
            summary.get("gamma", "n/a"),
            summary.get("tau", "n/a"),
        )
    )

    print("\n  [Final]")
    print(
        "  actor={:.4f} critic={:.4f} eval={:.4f} cum={:.2f} alpha={:.4f}".format(
            float(final_metrics.get("actor_loss", 0.0) or 0.0),
            float(final_metrics.get("critic_loss", 0.0) or 0.0),
            float(final_metrics.get("eval_return", 0.0) or 0.0),
            float(final_metrics.get("cumulative_return", 0.0) or 0.0),
            float(final_metrics.get("alpha", 0.0) or 0.0),
        )
    )
    print(
        "  selected={:.2f} td_var={:.4f} warmup={}".format(
            float(final_metrics.get("selected_fraction", 0.0) or 0.0),
            float(final_metrics.get("effective_td_var_threshold", 0.0) or 0.0),
            bool(final_metrics.get("warmup", False)),
        )
    )

    if history:
        first_metrics = history[0]
        print("\n  [Evolucao]")
        print(
            "  actor: {:.4f} -> {:.4f}".format(
                float(first_metrics.get("actor_loss", 0.0) or 0.0),
                float(final_metrics.get("actor_loss", 0.0) or 0.0),
            )
        )
        print(
            "  critic: {:.4f} -> {:.4f}".format(
                float(first_metrics.get("critic_loss", 0.0) or 0.0),
                float(final_metrics.get("critic_loss", 0.0) or 0.0),
            )
        )
        print(
            "  eval: {:.4f} -> {:.4f}".format(
                float(first_metrics.get("eval_return", 0.0) or 0.0),
                float(final_metrics.get("eval_return", 0.0) or 0.0),
            )
        )

    print("\n  [Log recente]")
    _show_recent_lines(log_lines)
    print("-" * 68)
    print("=" * 68)


def show_online_dashboard(summary: dict | None, log_lines: list[str], start_time: float, episodes: int) -> None:
    os.system("clear" if os.name == "posix" else "cls")
    print("=" * 68)
    print("  GREENRAN TA-SAM - MONITOR DE TREINO ONLINE")
    print("=" * 68)

    elapsed = time.time() - start_time

    # Mostra ultimas linhas do log
    recent = log_lines[-20:] if log_lines else []
    if recent:
        print(f"  Episodios: {len(recent)} no log  |  tempo decorrido: {format_time(elapsed)}")
        print("-" * 68)
        for line in recent:
            print(f"  {line}")
    else:
        print("  (aguardando primeira avaliacao...)")
        print(f"  tempo decorrido: {format_time(elapsed)}")

    # Se ja terminou, mostra summary
    if summary:
        print("-" * 68)
        final = summary.get("final_eval", {})
        print(f"  FINALIZADO - {summary.get('completed_episodes', 0)} episodios, {summary.get('total_steps', 0)} passos")
        print(f"  mean_return_last_100={summary.get('mean_return_last_100', 0):.2f}")
        print(f"  track={summary.get('experiment_track', 'unknown')}")
        print(
            "  eval: ret={ret:.2f}  eMBB={embb:.1f}%  mMTC={mmtc:.1f}%  URLLC={urllc:.1f}%".format(
                ret=float(final.get("mean_return", 0.0) or 0.0),
                embb=float(final.get("mean_embb_completion", 0.0) or 0.0) * 100.0,
                mmtc=float(final.get("mean_mmtc_completion", 0.0) or 0.0) * 100.0,
                urllc=float(final.get("mean_urllc_completion", 0.0) or 0.0) * 100.0,
            )
        )
        print(f"  checkpoint: {summary.get('resume_checkpoint', 'N/A')}")

    print("=" * 68)


def show_waiting_dashboard(dir_path: Path, start_time: float) -> None:
    os.system("clear" if os.name == "posix" else "cls")
    print("=" * 68)
    print("  GREENRAN TA-SAM - MONITOR DE TREINO ONLINE")
    print("=" * 68)
    print(f"  Diretorio oficial: {dir_path}")
    print("  Status: aguardando criacao do diretório/artefatos do treino online")
    print(f"  tempo decorrido: {format_time(time.time() - start_time)}")
    print("=" * 68)


def detect_mode(dir_path: Path) -> str | None:
    if (dir_path / "tasam_marl_summary.json").exists():
        return "offline"
    if (dir_path / "online_tasam_marl_summary.json").exists():
        return "online"
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="Monitor de treino TA-SAM GreenRAN")
    p.add_argument(
        "--dir",
        "-d",
        default=str(DEFAULT_ONLINE_DIR),
        help="Diretorio de saida do treino (padrao: trilha online oficial)",
    )
    p.add_argument("--refresh", "-r", type=int, default=5, help="Intervalo de atualizacao (segundos)")
    args = p.parse_args()

    dir_path = Path(args.dir)

    mode = detect_mode(dir_path)
    if mode is None:
        if dir_path.exists():
            print(f"Aviso: nenhum summary encontrado em '{args.dir}' (offline ou online)")
            print("Monitorando log...")
        else:
            print(f"Monitorando diretório oficial ainda nao criado: '{args.dir}'")

    start_time = time.time()
    episodes = 0
    last_lines_count = 0

    try:
        while True:
            if not dir_path.exists():
                show_waiting_dashboard(dir_path, start_time)
                time.sleep(args.refresh)
                continue

            if mode is None:
                mode = detect_mode(dir_path)

            log_lines = read_training_log(dir_path)

            if mode == "offline" or (mode is None and (dir_path / "tasam_marl_summary.json").exists()):
                summary = read_offline_summary(dir_path)
                if summary:
                    show_offline_dashboard(summary, log_lines)
                    break
            elif mode == "online":
                summary = read_online_summary(dir_path)
                show_online_dashboard(summary, log_lines, start_time, episodes)
                if summary:
                    break
            else:
                summary = read_online_summary(dir_path) or read_offline_summary(dir_path)
                if summary:
                    mode = "online" if "completed_episodes" in summary or "total_episodes" in summary else "offline"
                    continue
                show_online_dashboard(None, log_lines, start_time, 0)

            if len(log_lines) != last_lines_count:
                last_lines_count = len(log_lines)
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        print("\nMonitor encerrado.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
