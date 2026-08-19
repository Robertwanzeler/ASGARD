#!/usr/bin/env python3
"""
Compare the current GreenRAN GraphSAGE track with the scaffolded article00 track.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def exists(path: Path) -> bool:
    return path.exists()


def main() -> None:
    article00 = {
        "docs": {
            "ARTICLE00_EXPERIMENTOS.md": exists(PROJECT_ROOT / "docs" / "ARTICLE00_EXPERIMENTOS.md"),
            "ARTICLE00_METODO.md": exists(PROJECT_ROOT / "docs" / "ARTICLE00_METODO.md"),
            "ESTUDO_ARTIGOS_BASE.md": exists(PROJECT_ROOT / "docs" / "ESTUDO_ARTIGOS_BASE.md"),
        },
        "scripts": {
            "generate_article00_dataset.py": exists(PROJECT_ROOT / "scripts" / "generate_article00_dataset.py"),
            "run_article00_experiments.py": exists(PROJECT_ROOT / "scripts" / "run_article00_experiments.py"),
            "generate_graphsage_article00_figures.py": exists(
                PROJECT_ROOT / "scripts" / "generate_graphsage_article00_figures.py"
            ),
        },
        "training": {
            "train_graphsage_article00.py": exists(PROJECT_ROOT / "training" / "train_graphsage_article00.py"),
        },
    }

    greenran = {
        "docs": {
            "ANALISE_CONFLITOS_GNN.md": exists(PROJECT_ROOT / "docs" / "ANALISE_CONFLITOS_GNN.md"),
            "CONFLICT_DATASET_PIPELINE.md": exists(PROJECT_ROOT / "docs" / "CONFLICT_DATASET_PIPELINE.md"),
        },
        "scripts": {
            "generate_graphsage_paper_multiseed.py": exists(PROJECT_ROOT / "scripts" / "generate_graphsage_paper_multiseed.py"),
        },
        "training": {
            "train_graphsage_conflicts.py": exists(PROJECT_ROOT / "training" / "train_graphsage_conflicts.py"),
        },
    }

    report = {
        "schema": "greenran.graphsage_track_compare.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "greenran_track": greenran,
        "article00_track": article00,
        "notes": [
            "This report compares file presence and organization only.",
            "It does not claim methodological equivalence between the two tracks.",
        ],
    }

    output_path = PROJECT_ROOT / "runs" / "article00" / "reports" / "graphsage_track_compare.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    print(f"Track comparison report written to: {output_path}")


if __name__ == "__main__":
    main()
