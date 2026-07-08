#!/usr/bin/env python3
"""
Deploy um checkpoint TA-SAM MARL no shadow runtime.

1. Valida os arquivos do checkpoint
2. Gera/atualiza tasam_candidate_evaluation_latest.json
3. Shadow recarrega automaticamente em ~5 min

Uso:
    python3 scripts/deploy_shadow_checkpoint.py \
        --checkpoint-dir runs/sac_bootstrap/tasam_marl_v3

    # Forcar readiness (padrao: shadow_ready)
    python3 scripts/deploy_shadow_checkpoint.py \
        --checkpoint-dir runs/sac_bootstrap/tasam_marl_v3 \
        --readiness shadow_ready
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "runs" / "sac_bootstrap" / "tasam_candidate_evaluation_latest.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deploy TA-SAM checkpoint no shadow runtime")
    p.add_argument("--checkpoint-dir", "-d", required=True, help="Diretorio do checkpoint (ex: runs/sac_bootstrap/tasam_marl_v3)")
    p.add_argument("--readiness", default="shadow_ready", choices=["not_ready", "shadow_ready", "control_candidate"],
                   help="Nivel de readiness do checkpoint (padrao: shadow_ready)")
    p.add_argument("--force", "-f", action="store_true", help="Forca deploy mesmo sem validate")
    return p.parse_args()


def validate_checkpoint(checkpoint_dir: Path) -> list[str]:
    errors: list[str] = []
    required = ["tasam_marl_actors.pt", "tasam_marl_checkpoint_meta.json"]
    for fname in required:
        path = checkpoint_dir / fname
        if not path.exists():
            errors.append(f"Arquivo ausente: {fname}")
        elif path.stat().st_size == 0:
            errors.append(f"Arquivo vazio: {fname}")

    # Validar meta
    meta_path = checkpoint_dir / "tasam_marl_checkpoint_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if not isinstance(meta.get("du_count"), int) or meta["du_count"] < 1:
                errors.append("meta.du_count invalido")
            if not isinstance(meta.get("du_state_dim"), int) or meta["du_state_dim"] < 1:
                errors.append("meta.du_state_dim invalido")
        except (json.JSONDecodeError, OSError):
            errors.append("meta.json invalido")
    return errors


def _load_summary(checkpoint_dir: Path) -> dict | None:
    summary_path = checkpoint_dir / "tasam_marl_summary.json"
    if not summary_path.exists():
        return None
    try:
        return json.loads(summary_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _compute_history_metrics(summary: dict) -> dict:
    history = summary.get("history", []) or []
    post_warmup = [h for h in history if not bool(h.get("warmup", False))]
    nonzero_epochs = sum(1 for h in history if _safe_float(h.get("selected_agents"), 0.0) > 0.0)
    post_warmup_nonzero = sum(1 for h in post_warmup if _safe_float(h.get("selected_agents"), 0.0) > 0.0)
    effective_thresholds = [float(h.get("effective_td_var_threshold", 0.0) or 0.0) for h in post_warmup]
    dynamic_active = any(v > 0.0 for v in effective_thresholds)
    return {
        "nonzero_epochs": nonzero_epochs,
        "post_warmup_nonzero_epochs": post_warmup_nonzero,
        "dynamic_threshold_active": dynamic_active,
    }




def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_readiness(checkpoint_dir: Path, readiness: str) -> list[str]:
    """Validate that a manual deploy label is compatible with checkpoint metrics."""
    if readiness == "not_ready":
        return []

    summary = _load_summary(checkpoint_dir)
    if summary is None:
        return ["tasam_marl_summary.json ausente; nao e possivel validar readiness"]

    final = summary.get("final_metrics", {}) or {}
    history = summary.get("history", []) or []
    post_warmup = [h for h in history if not bool(h.get("warmup", False))]
    post_warmup_nonzero = sum(1 for h in post_warmup if _safe_float(h.get("selected_agents"), 0.0) > 0.0)
    critic_loss = _safe_float(final.get("critic_loss"), 1.0)
    selected_fraction = _safe_float(final.get("selected_fraction"), 0.0)
    action_var_mean = _safe_float(final.get("action_var_mean"), 0.0)
    bc_loss = _safe_float(final.get("bc_loss"), 1.0)

    errors: list[str] = []
    if readiness in {"shadow_ready", "control_candidate"}:
        if critic_loss > 5e-4:
            errors.append(f"critic_loss acima de shadow_ready: {critic_loss:.6g} > 5e-4")
        if post_warmup_nonzero <= 0:
            errors.append("sem atualizacao de atores apos warmup")
        if action_var_mean < 0.002:
            errors.append(f"action_var_mean baixo: {action_var_mean:.6g} < 0.002")

    if readiness == "control_candidate":
        if critic_loss > 3e-4:
            errors.append(f"critic_loss acima de control_candidate: {critic_loss:.6g} > 3e-4")
        if bc_loss > 0.04:
            errors.append(f"bc_loss acima de control_candidate: {bc_loss:.6g} > 0.04")
        if not (0.05 <= selected_fraction <= 0.25):
            errors.append(f"selected_fraction fora da faixa de controle: {selected_fraction:.4f} not in [0.05, 0.25]")
        if action_var_mean < 0.005:
            errors.append(f"action_var_mean baixo para control: {action_var_mean:.6g} < 0.005")

    return errors


def _blocking_errors(errors: list[str]) -> bool:
    """Returns True if there are errors that --force cannot bypass."""
    critical = ["sem atualizacao de atores apos warmup", "action_var_mean baixo"]
    for e in errors:
        for keyword in critical:
            if keyword in e:
                return True
    return False


def build_manifest_entry(checkpoint_dir: Path, readiness: str) -> dict:
    meta_path = checkpoint_dir / "tasam_marl_checkpoint_meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    summary_path = checkpoint_dir / "tasam_marl_summary.json"
    final_metrics = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
            final_metrics = summary.get("final_metrics", {})
        except (json.JSONDecodeError, OSError):
            pass

    # Computar metricas reais do historico
    history_metrics = _compute_history_metrics(summary) if summary_path.exists() else {
        "nonzero_epochs": 0, "post_warmup_nonzero_epochs": 0, "dynamic_threshold_active": False,
    }

    return {
        "trace_jsonl": str((checkpoint_dir / "trace.jsonl").resolve()) if (checkpoint_dir / "trace.jsonl").exists() else "",
        "epochs": meta.get("epochs", 0),
        "du_count": meta.get("du_count", 0),
        "final_metrics": final_metrics,
        "nonzero_epochs": history_metrics["nonzero_epochs"],
        "post_warmup_nonzero_epochs": history_metrics["post_warmup_nonzero_epochs"],
        "dynamic_threshold_active": history_metrics["dynamic_threshold_active"],
        "readiness": readiness,
        "reasons": [f"deploy manual via deploy_shadow_checkpoint.py ({readiness})"],
        "promote_shadow": readiness in ("shadow_ready", "control_candidate"),
        "promote_control_candidate": readiness == "control_candidate",
        "run_dir": str(checkpoint_dir.resolve()),
        "summary_path": str(summary_path.resolve()) if summary_path.exists() else "",
    }


def main() -> int:
    args = parse_args()
    checkpoint_dir = Path(args.checkpoint_dir).resolve()

    if not checkpoint_dir.exists():
        print(f"Erro: diretorio '{checkpoint_dir}' nao encontrado")
        return 1

    # Validar
    errors = validate_checkpoint(checkpoint_dir)
    errors.extend(validate_readiness(checkpoint_dir, args.readiness))
    if errors:
        print("ERROS de validacao:")
        for e in errors:
            print(f"  - {e}")
        if _blocking_errors(errors):
            print("ERROS CRITICOS: nao e possivel fazer deploy mesmo com --force")
            return 1
        if not args.force:
            print("Use --force para ignorar e fazer deploy mesmo assim")
            return 1
        print("Ignorando erros nao-criticos (--force ativo)")

    # Construir entry
    entry = build_manifest_entry(checkpoint_dir, args.readiness)

    # Carregar manifest existente ou criar novo
    manifest: dict = {"runs_root": str(PROJECT_ROOT / "runs" / "sac_bootstrap")}
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    evaluated = manifest.get("evaluated_runs", [])
    # Remover entry anterior com o mesmo run_dir se existir
    evaluated = [e for e in evaluated if e.get("run_dir") != entry["run_dir"]]
    evaluated.append(entry)
    manifest["evaluated_runs"] = evaluated
    manifest["best_run"] = entry

    # Escrever
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Checkpoint deployado: {checkpoint_dir}")
    print(f"  readiness: {args.readiness}")
    print(f"  run_dir: {entry['run_dir']}")
    print(f"  promote_shadow: {entry['promote_shadow']}")
    print(f"  Manifest: {MANIFEST_PATH}")
    print()
    print("Shadow recarregara o novo checkpoint em ~5 min (ciclo de avaliacao)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
