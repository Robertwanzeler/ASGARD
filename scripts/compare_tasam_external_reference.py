#!/usr/bin/env python3
"""Compare an external article reference bundle against local baseline and TA-SAM runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "runs" / "external_references" / "comparisons"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare external article reference with local TA-SAM runs")
    parser.add_argument("--external-reference-json", required=True, help="Structured JSON produced by import_tasam_article_reference.py")
    parser.add_argument("--local-baseline-summary", required=True, help="Local baseline tasam_marl_summary.json")
    parser.add_argument("--local-tasam-summary", required=True, help="Local TA-SAM tasam_marl_summary.json")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Directory for comparison outputs")
    return parser


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct_change(current: float, previous: float) -> float:
    current = _safe_float(current, 0.0)
    previous = _safe_float(previous, 0.0)
    if abs(previous) <= 1e-12:
        return 0.0
    return ((current - previous) / abs(previous)) * 100.0


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_local_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    history = list(summary.get("history") or [])
    final = summary.get("final_metrics") or {}
    if history:
        peak_row = max(history, key=lambda row: _safe_float(row.get("eval_return"), 0.0))
    else:
        peak_row = final
    best_checkpoint = summary.get("best_checkpoint") or {}
    best_metrics = best_checkpoint.get("metrics") or {}
    best_eval = _safe_float(best_metrics.get("eval_return"), _safe_float(peak_row.get("eval_return"), 0.0))
    final_eval = _safe_float(final.get("eval_return"), 0.0)
    peak_eval = _safe_float(peak_row.get("eval_return"), final_eval)
    return {
        "completed_epochs": int(summary.get("completed_epochs", summary.get("epochs", 0)) or 0),
        "target_epochs": int(summary.get("target_epochs", summary.get("epochs", 0)) or 0),
        "stopped_early": bool(summary.get("stopped_early", False)),
        "best_checkpoint_epoch": int(best_checkpoint.get("epoch", peak_row.get("epoch", final.get("epoch", 0))) or 0),
        "best_eval_return": best_eval,
        "final_epoch": int(final.get("epoch", 0) or 0),
        "final_eval_return": final_eval,
        "peak_eval_return": peak_eval,
        "peak_epoch": int(peak_row.get("epoch", final.get("epoch", 0)) or 0),
        "peak_to_final_pct": _pct_change(final_eval, peak_eval),
        "final_critic_loss": _safe_float(final.get("critic_loss"), 0.0),
        "final_selected_fraction": _safe_float(final.get("selected_fraction"), 0.0),
        "final_alpha": _safe_float(final.get("alpha"), 0.0),
        "trainer_backend": summary.get("trainer_backend", ""),
    }


def build_comparison_payload(external_reference: dict[str, Any], local_baseline: dict[str, Any], local_tasam: dict[str, Any]) -> dict[str, Any]:
    external = external_reference["derived"]
    baseline = extract_local_metrics(local_baseline)
    tasam = extract_local_metrics(local_tasam)
    local_best_advantage_pct = _pct_change(tasam["best_eval_return"], baseline["best_eval_return"])
    local_final_advantage_pct = _pct_change(tasam["final_eval_return"], baseline["final_eval_return"])
    local_reduces_forgetting = tasam["peak_to_final_pct"] > baseline["peak_to_final_pct"]

    payload = {
        "schema": "greenran.tasam_external_vs_local_comparison.v1",
        "external_reference": {
            "label": external_reference.get("label", ""),
            "scenario_family": external_reference.get("scenario_family", ""),
            "artifact_path": external_reference.get("artifact_path", ""),
            "tasam_advantage_last100_pct": external["tasam_advantage_last100_pct"],
            "tasam_advantage_global_pct": external["tasam_advantage_global_pct"],
            "sac_peak_to_final_pct": external["sac_peak_to_final_pct"],
            "ta_peak_to_final_pct": external["ta_peak_to_final_pct"],
            "tasam_reduces_forgetting": external["tasam_reduces_forgetting"],
        },
        "local_runs": {
            "baseline": baseline,
            "tasam": tasam,
        },
        "comparison": {
            "local_best_advantage_pct": local_best_advantage_pct,
            "local_final_advantage_pct": local_final_advantage_pct,
            "local_tasam_reduces_forgetting": local_reduces_forgetting,
            "method_direction_match": external["tasam_advantage_last100_pct"] > 0.0 and local_best_advantage_pct > 0.0,
            "stability_direction_match": external["tasam_reduces_forgetting"] == local_reduces_forgetting,
            "notes": [
                "external reference is article-scenario only",
                "local scenario remains GreenRAN-specific",
                "absolute eval_return values are not directly comparable across scenarios",
            ],
        },
    }
    return payload


def _markdown(payload: dict[str, Any]) -> str:
    ext = payload["external_reference"]
    base = payload["local_runs"]["baseline"]
    tasam = payload["local_runs"]["tasam"]
    cmp = payload["comparison"]
    return (
        "# External Article Reference vs Local GreenRAN\n\n"
        f"- external label: `{ext['label']}`\n"
        f"- external TA-SAM advantage last100: {ext['tasam_advantage_last100_pct']:.1f}%\n"
        f"- local best advantage: {cmp['local_best_advantage_pct']:.2f}%\n"
        f"- local final advantage: {cmp['local_final_advantage_pct']:.2f}%\n"
        f"- external reduces forgetting: {'yes' if ext['tasam_reduces_forgetting'] else 'no'}\n"
        f"- local reduces forgetting: {'yes' if cmp['local_tasam_reduces_forgetting'] else 'no'}\n"
        f"- method direction match: {'yes' if cmp['method_direction_match'] else 'no'}\n"
        f"- stability direction match: {'yes' if cmp['stability_direction_match'] else 'no'}\n"
        "\n## Local Runs\n\n"
        f"- baseline best/final eval: {base['best_eval_return']:.4f} / {base['final_eval_return']:.4f}\n"
        f"- tasam best/final eval: {tasam['best_eval_return']:.4f} / {tasam['final_eval_return']:.4f}\n"
        f"- baseline peak->final: {base['peak_to_final_pct']:.2f}%\n"
        f"- tasam peak->final: {tasam['peak_to_final_pct']:.2f}%\n"
    )


def main() -> int:
    args = build_parser().parse_args()
    external_path = Path(args.external_reference_json)
    baseline_path = Path(args.local_baseline_summary)
    tasam_path = Path(args.local_tasam_summary)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    payload = build_comparison_payload(_load_json(external_path), _load_json(baseline_path), _load_json(tasam_path))
    run_label = f"{tasam_path.parent.parent.name}_{tasam_path.parent.name}"
    slug = f"{external_path.stem}_vs_{run_label}"
    json_path = output_root / f"{slug}.json"
    md_path = output_root / f"{slug}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
