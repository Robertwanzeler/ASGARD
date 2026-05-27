#!/usr/bin/env python3
"""
Materialize the EE-DRL-GreenRAN package in a single run directory.

This mirrors the idea used in the ARMD/GraphSAGE track:
- one stable package directory under runs/
- copied figures
- copied core artifacts
- summary JSON/Markdown manifest
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "runs" / "eedrl_greenran_final"

SOURCE_FIGURES = [
    PROJECT_ROOT / "drlexp" / "charts" / "energia_comparacao.png",
    PROJECT_ROOT / "drlexp" / "charts" / "mae_evolucao.png",
    PROJECT_ROOT / "drlexp" / "charts" / "matriz_conflitos.png",
    PROJECT_ROOT / "drlexp" / "charts" / "reward_function.png",
    PROJECT_ROOT / "drlexp" / "charts" / "rewards" / "decision_distribution.png",
    PROJECT_ROOT / "drlexp" / "charts" / "rewards" / "energy_efficiency.png",
    PROJECT_ROOT / "drlexp" / "charts" / "rewards" / "reward_by_zone.png",
    PROJECT_ROOT / "drlexp" / "charts" / "rewards" / "reward_comparison.png",
    PROJECT_ROOT / "drlexp" / "charts" / "rewards" / "summary_metrics.png",
]

SOURCE_ARTIFACTS = [
    PROJECT_ROOT / "drlexp" / "models" / "evaluation_metrics.json",
    PROJECT_ROOT / "drlexp" / "models" / "sbilstm" / "best_model.pt",
    PROJECT_ROOT / "drlexp" / "models" / "a3c" / "actor_v7.pt",
    PROJECT_ROOT / "drlexp" / "models" / "a3c" / "critic_v7.pt",
    PROJECT_ROOT / "drlexp" / "config" / "drl_config.yaml",
    PROJECT_ROOT / "drlexp" / "docs" / "COMPARACAO_RF_DRL.md",
    PROJECT_ROOT / "docs" / "Energy-Efficient_Deep_Reinforcement_Learning_Assisted_Resource_Allocation_for_5G-RAN_Slicing.pdf",
]


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def copy_existing(files: list[Path], target_dir: Path) -> list[dict]:
    copied = []
    target_dir.mkdir(parents=True, exist_ok=True)
    for source in files:
        if not source.exists():
            continue
        destination = target_dir / source.name
        shutil.copy2(source, destination)
        copied.append(
            {
                "name": source.name,
                "source": str(source),
                "destination": str(destination),
            }
        )
    return copied


def build_summary(output_dir: Path, figures: list[dict], artifacts: list[dict], metrics: dict) -> dict:
    sbilstm = metrics.get("sbilstm", {})
    a3c = metrics.get("a3c", {})
    f1 = metrics.get("f1_score", {})
    return {
        "schema": "greenran.eedrl_greenran_package.v1",
        "display_name": "EE-DRL-GreenRAN",
        "reference_title": "Energy-Efficient Deep Reinforcement Learning Assisted Resource Allocation for 5G-RAN Slicing",
        "reference_short": "EE-DRL-RA / IEEE TVT 2022",
        "output_dir": str(output_dir),
        "figures_dir": str(output_dir / "figures"),
        "artifacts_dir": str(output_dir / "artifacts"),
        "figure_count": len(figures),
        "artifact_count": len(artifacts),
        "figures": figures,
        "artifacts": artifacts,
        "metrics": {
            "sbilstm_mae_ms": sbilstm.get("mae_ms"),
            "sbilstm_rmse_ms": sbilstm.get("rmse_ms"),
            "sbilstm_r2": sbilstm.get("r2"),
            "sbilstm_inference_time_ms": sbilstm.get("inference_time_ms"),
            "a3c_allowed_percent": a3c.get("allowed_percent"),
            "a3c_conditional_percent": a3c.get("conditional_percent"),
            "a3c_blocked_percent": a3c.get("blocked_percent"),
            "a3c_inference_time_ms": a3c.get("inference_time_ms"),
            "f1_score": f1.get("f1_score"),
            "precision": f1.get("precision"),
            "recall": f1.get("recall"),
        },
        "notes": [
            "This package organizes the DRL track in the same spirit as the ARMD-GreenRAN final package.",
            "The copied PNG figures come from drlexp/charts and drlexp/charts/rewards.",
            "This package is a consolidation layer; it does not retrain models.",
            "The canonical paper-style figure mapping lives under runs/eedrl_greenran_final/paper_figures.",
        ],
    }


def write_markdown(summary: dict, path: Path) -> None:
    metrics = summary.get("metrics", {})
    lines = [
        "# EE-DRL-GreenRAN",
        "",
        f"- Reference: `{summary['reference_short']}`",
        f"- Figures: `{summary['figure_count']}`",
        f"- Artifacts: `{summary['artifact_count']}`",
        "",
        "## Metrics",
        "",
        f"- `SBiLSTM MAE (ms)`: `{metrics.get('sbilstm_mae_ms')}`",
        f"- `SBiLSTM RMSE (ms)`: `{metrics.get('sbilstm_rmse_ms')}`",
        f"- `SBiLSTM R²`: `{metrics.get('sbilstm_r2')}`",
        f"- `A3C allowed %`: `{metrics.get('a3c_allowed_percent')}`",
        f"- `A3C conditional %`: `{metrics.get('a3c_conditional_percent')}`",
        f"- `A3C blocked %`: `{metrics.get('a3c_blocked_percent')}`",
        f"- `F1`: `{metrics.get('f1_score')}`",
        f"- `Precision`: `{metrics.get('precision')}`",
        f"- `Recall`: `{metrics.get('recall')}`",
        "",
        "## Notes",
        "",
    ]
    for note in summary.get("notes", []):
        lines.append(f"- {note}")
    lines.extend([
        "",
        "## Figures",
        "",
    ])
    for item in summary.get("figures", []):
        lines.append(f"- `{item['name']}`")
    lines.extend(["", "## Artifacts", ""])
    for item in summary.get("artifacts", []):
        lines.append(f"- `{item['name']}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize the EE-DRL-GreenRAN package.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output_dir = args.output_root.resolve()
    figures_dir = output_dir / "figures"
    artifacts_dir = output_dir / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)

    figures = copy_existing(SOURCE_FIGURES, figures_dir)
    artifacts = copy_existing(SOURCE_ARTIFACTS, artifacts_dir)
    metrics = load_json(PROJECT_ROOT / "drlexp" / "models" / "evaluation_metrics.json")

    summary = build_summary(output_dir, figures, artifacts, metrics)
    summary_json = output_dir / "eedrl_greenran_summary.json"
    summary_md = output_dir / "eedrl_greenran_summary.md"

    summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    write_markdown(summary, summary_md)

    print(
        json.dumps(
            {
                "summary_json": str(summary_json),
                "summary_md": str(summary_md),
                "figures_dir": str(figures_dir),
                "artifacts_dir": str(artifacts_dir),
                "figure_count": len(figures),
                "artifact_count": len(artifacts),
                "display_name": summary["display_name"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
