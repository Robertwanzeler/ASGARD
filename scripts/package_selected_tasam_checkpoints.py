#!/usr/bin/env python3
"""Package selected TA-SAM checkpoints into deployable self-contained directories."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "runs" / "sac_bootstrap" / "tasam_selected_greenran"
CHECKPOINT_GLOB = "tasam_marl_*.pt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package selected TA-SAM checkpoints")
    parser.add_argument("--primary-checkpoint-dir", required=True, help="Balanced primary checkpoint directory")
    parser.add_argument("--secondary-checkpoint-dir", required=True, help="Aggressive secondary checkpoint directory")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Output root for packaged checkpoints")
    return parser


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _checkpoint_epoch(checkpoint_dir: Path) -> int:
    match = re.search(r"epoch_(\d+)", checkpoint_dir.name)
    if not match:
        raise ValueError(f"unable to parse epoch from {checkpoint_dir}")
    return int(match.group(1))


def _checkpoint_rank(metrics: dict[str, Any], epoch: int) -> tuple[float, float, float, int]:
    return (
        _safe_float(metrics.get("eval_return"), 0.0),
        _safe_float(metrics.get("cumulative_return"), 0.0),
        -_safe_float(metrics.get("critic_loss"), float("inf")),
        -int(epoch),
    )


def _load_parent_summary(checkpoint_dir: Path) -> tuple[Path, dict[str, Any]]:
    run_dir = checkpoint_dir.parents[1]
    summary_path = run_dir / "tasam_marl_summary.json"
    return run_dir, json.loads(summary_path.read_text(encoding="utf-8"))


def _best_checkpoint_until(checkpoint_records: list[dict[str, Any]], selected_epoch: int) -> dict[str, Any] | None:
    eligible = [record for record in checkpoint_records if int(record.get("epoch", 0) or 0) <= selected_epoch]
    if not eligible:
        return None
    best = max(
        eligible,
        key=lambda item: _checkpoint_rank(item.get("metrics") or {}, int(item.get("epoch", 0) or 0)),
    )
    return {
        "epoch": int(best.get("epoch", 0) or 0),
        "checkpoint_dir": best.get("checkpoint_dir", ""),
        "summary_path": best.get("summary_path", ""),
        "metrics": best.get("metrics") or {},
    }


def build_selected_summary(
    parent_summary: dict[str, Any],
    *,
    checkpoint_dir: Path,
    role: str,
    usage_profile: str,
) -> dict[str, Any]:
    selected_epoch = _checkpoint_epoch(checkpoint_dir)
    history = [row for row in list(parent_summary.get("history") or []) if int(row.get("epoch", 0) or 0) <= selected_epoch]
    if not history:
        raise ValueError(f"missing history row for epoch {selected_epoch}")
    final_metrics = next((row for row in history if int(row.get("epoch", 0) or 0) == selected_epoch), history[-1])
    checkpoint_records = [
        row for row in list(parent_summary.get("checkpoint_records") or []) if int(row.get("epoch", 0) or 0) <= selected_epoch
    ]
    best_checkpoint = _best_checkpoint_until(checkpoint_records, selected_epoch)

    payload = dict(parent_summary)
    payload["epochs"] = selected_epoch
    payload["completed_epochs"] = selected_epoch
    payload["target_epochs"] = selected_epoch
    payload["history"] = history
    payload["checkpoint_records"] = checkpoint_records
    payload["best_checkpoint"] = best_checkpoint
    payload["final_metrics"] = final_metrics
    payload["stopped_early"] = False
    payload["stop_reason"] = ""
    payload["selection_role"] = role
    payload["selection_usage_profile"] = usage_profile
    payload["selection_source_checkpoint_dir"] = str(checkpoint_dir.resolve())
    payload["selection_source_run_completed_epochs"] = int(parent_summary.get("completed_epochs", parent_summary.get("epochs", 0)) or 0)
    payload["selection_source_run_stopped_early"] = bool(parent_summary.get("stopped_early", False))
    payload["selection_source_run_stop_reason"] = str(parent_summary.get("stop_reason", "") or "")
    payload["selection_parent_best_checkpoint"] = parent_summary.get("best_checkpoint")
    return payload


def package_checkpoint(checkpoint_dir: Path, *, output_root: Path, role: str, usage_profile: str) -> dict[str, Any]:
    run_dir, parent_summary = _load_parent_summary(checkpoint_dir)
    selected_epoch = _checkpoint_epoch(checkpoint_dir)
    seed_label = checkpoint_dir.parents[2].name
    package_dir = output_root / f"{role}_{usage_profile}_{seed_label}_epoch_{selected_epoch:04d}"
    package_dir.mkdir(parents=True, exist_ok=True)

    for path in checkpoint_dir.iterdir():
        if path.is_file():
            shutil.copy2(path, package_dir / path.name)

    summary = build_selected_summary(parent_summary, checkpoint_dir=checkpoint_dir, role=role, usage_profile=usage_profile)
    summary_path = package_dir / "tasam_marl_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    final = summary.get("final_metrics") or {}
    return {
        "role": role,
        "usage_profile": usage_profile,
        "package_dir": str(package_dir.resolve()),
        "summary_path": str(summary_path.resolve()),
        "source_checkpoint_dir": str(checkpoint_dir.resolve()),
        "source_run_dir": str(run_dir.resolve()),
        "selected_epoch": selected_epoch,
        "eval_return": _safe_float(final.get("eval_return"), 0.0),
        "critic_loss": _safe_float(final.get("critic_loss"), 0.0),
        "selected_fraction": _safe_float(final.get("selected_fraction"), 0.0),
        "alpha": _safe_float(final.get("alpha"), 0.0),
    }


def write_manifest(output_root: Path, primary: dict[str, Any], secondary: dict[str, Any]) -> tuple[Path, Path]:
    manifest = {
        "schema": "greenran.tasam_selected_checkpoint_manifest.v1",
        "decision": {
            "primary_role": "balanced_use",
            "secondary_role": "aggressive_benchmark",
            "primary_reason": "Lower-risk operational candidate for the current GreenRAN scenario",
            "secondary_reason": "Higher-return candidate kept for benchmark and stress comparison",
        },
        "primary": primary,
        "secondary": secondary,
    }
    json_path = output_root / "tasam_selected_checkpoint_manifest.json"
    md_path = output_root / "tasam_selected_checkpoint_manifest.md"
    json_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# TA-SAM Selected Checkpoints",
                "",
                f"- primary: `{primary['package_dir']}`",
                f"  - profile: {primary['usage_profile']}",
                f"  - eval_return: {primary['eval_return']:.4f}",
                f"  - critic_loss: {primary['critic_loss']:.4f}",
                f"  - selected_fraction: {primary['selected_fraction']:.4f}",
                f"- secondary: `{secondary['package_dir']}`",
                f"  - profile: {secondary['usage_profile']}",
                f"  - eval_return: {secondary['eval_return']:.4f}",
                f"  - critic_loss: {secondary['critic_loss']:.4f}",
                f"  - selected_fraction: {secondary['selected_fraction']:.4f}",
                "",
                "The primary checkpoint is intended for balanced GreenRAN use.",
                "The secondary checkpoint is kept as the aggressive benchmark candidate.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return json_path, md_path


def main() -> int:
    args = build_parser().parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    primary = package_checkpoint(
        Path(args.primary_checkpoint_dir),
        output_root=output_root,
        role="primary",
        usage_profile="balanced_use",
    )
    secondary = package_checkpoint(
        Path(args.secondary_checkpoint_dir),
        output_root=output_root,
        role="secondary",
        usage_profile="aggressive_benchmark",
    )
    json_path, md_path = write_manifest(output_root, primary, secondary)
    print(json.dumps({"manifest_json": str(json_path), "manifest_markdown": str(md_path), "primary": primary["package_dir"], "secondary": secondary["package_dir"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
