#!/usr/bin/env python3
"""Consolidate TA-SAM results across GreenRAN dataset/training scenarios."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

MODES = ("no_sam", "l2", "actor_sam", "critic_sam", "both_sam", "tasam_selective")
SCENARIOS = {
    "raw": "runs/tasam_greenran_real_full25",
    "article_faithful": "runs/tasam_greenran_real_article_faithful",
    "equal": "runs/tasam_greenran_real_article_rho/equal",
    "non_equal": "runs/tasam_greenran_real_article_rho/non_equal",
    "dynamic": "runs/tasam_greenran_real_article_rho/dynamic",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare TA-SAM scenarios")
    parser.add_argument(
        "--output-root",
        default="runs/tasam_greenran_comparison",
        help="Directory for consolidated outputs",
    )
    return parser


def load_summary(path: Path) -> dict[str, Any] | None:
    summary_path = path / "tasam_marl_summary.json"
    if not summary_path.exists():
        return None
    return json.loads(summary_path.read_text(encoding="utf-8"))


def row_for(scenario: str, mode: str, summary: dict[str, Any]) -> dict[str, Any]:
    final = summary.get("final_metrics") or {}
    return {
        "scenario": scenario,
        "mode": mode,
        "trace_jsonl": summary.get("trace_jsonl"),
        "epochs": summary.get("epochs"),
        "trainer_backend": summary.get("trainer_backend"),
        "eval_return": final.get("eval_return"),
        "cumulative_return": final.get("cumulative_return"),
        "selected_fraction": final.get("selected_fraction"),
        "td_var_mean": final.get("td_var_mean"),
        "td_var_max": final.get("td_var_max"),
        "action_var_mean": final.get("action_var_mean"),
        "policy_entropy": final.get("policy_entropy"),
        "alpha": final.get("alpha"),
        "actor_sam_rho": summary.get("actor_sam_rho"),
        "actor_sam_rho_final": summary.get("actor_sam_rho_final"),
        "critic_sam_rho": summary.get("critic_sam_rho"),
        "critic_sam_rho_final": summary.get("critic_sam_rho_final"),
    }


def ranking(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            float(row.get("eval_return") or 0.0),
            float(row.get("cumulative_return") or 0.0),
        ),
        reverse=True,
    )
    return [
        {
            "rank": idx + 1,
            "mode": row["mode"],
            "eval_return": row["eval_return"],
            "cumulative_return": row["cumulative_return"],
        }
        for idx, row in enumerate(ordered)
    ]


def recommendation(payload: dict[str, Any]) -> dict[str, Any]:
    scenario_rows = payload["scenario_rows"]
    best_raw = ranking(scenario_rows["raw"])[0]
    best_equal = ranking(scenario_rows["equal"])[0]
    best_non_equal = ranking(scenario_rows["non_equal"])[0]
    best_dynamic = ranking(scenario_rows["dynamic"])[0]
    best_article = max(
        [best_equal | {"scenario": "equal"}, best_non_equal | {"scenario": "non_equal"}, best_dynamic | {"scenario": "dynamic"}],
        key=lambda row: (float(row["eval_return"] or 0.0), float(row["cumulative_return"] or 0.0)),
    )
    return {
        "report_primary": {
            "scenario": best_article["scenario"],
            "mode": best_article["mode"],
            "reason": "Best article-faithful rho scenario by eval_return/cumulative_return",
        },
        "report_comparators": [
            {"scenario": "non_equal", "mode": "critic_sam"},
            {"scenario": "non_equal", "mode": "both_sam"},
        ],
        "greenran_operational_baseline": {
            "scenario": "raw",
            "mode": best_raw["mode"],
            "reason": "Best mixed-regime operational result on current GreenRAN trace",
        },
        "shadow_candidate": {
            "scenario": best_article["scenario"],
            "mode": best_article["mode"],
            "reason": "Best article-faithful candidate; use only in controlled shadow evaluation first",
        },
    }


def markdown_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Scenario | Rank | Mode | Eval Return | Cumulative Return | Selected Fraction | TD Var Mean | Action Var Mean | Policy Entropy |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario in ("raw", "article_faithful", "equal", "non_equal", "dynamic"):
        ordered = sorted(
            rows,
            key=lambda row: (
                row["scenario"] != scenario,
                -(float(row.get("eval_return") or 0.0)),
                -(float(row.get("cumulative_return") or 0.0)),
            ),
        )
        scenario_rows = [row for row in ordered if row["scenario"] == scenario]
        for idx, row in enumerate(scenario_rows, start=1):
            lines.append(
                "| {scenario} | {rank} | {mode} | {eval_return:.4f} | {cumulative_return:.2f} | {selected_fraction:.4f} | {td_var_mean:.6f} | {action_var_mean:.6f} | {policy_entropy:.6f} |".format(
                    scenario=scenario,
                    rank=idx,
                    mode=row["mode"],
                    eval_return=float(row.get("eval_return") or 0.0),
                    cumulative_return=float(row.get("cumulative_return") or 0.0),
                    selected_fraction=float(row.get("selected_fraction") or 0.0),
                    td_var_mean=float(row.get("td_var_mean") or 0.0),
                    action_var_mean=float(row.get("action_var_mean") or 0.0),
                    policy_entropy=float(row.get("policy_entropy") or 0.0),
                )
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = build_parser().parse_args()
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    scenario_rows: dict[str, list[dict[str, Any]]] = {}
    for scenario, run_root in SCENARIOS.items():
        scenario_root = Path(run_root)
        collected: list[dict[str, Any]] = []
        for mode in MODES:
            summary = load_summary(scenario_root / mode)
            if summary:
                row = row_for(scenario, mode, summary)
                rows.append(row)
                collected.append(row)
        scenario_rows[scenario] = collected

    payload = {
        "schema": "greenran.tasam_scenario_comparison.v1",
        "scenario_roots": SCENARIOS,
        "rows": rows,
        "scenario_rankings": {scenario: ranking(srows) for scenario, srows in scenario_rows.items()},
        "recommendation": recommendation({"scenario_rows": scenario_rows}),
    }

    json_path = output_root / "tasam_scenario_comparison.json"
    csv_path = output_root / "tasam_scenario_comparison.csv"
    md_path = output_root / "tasam_scenario_comparison.md"

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["scenario", "mode"])
        writer.writeheader()
        writer.writerows(rows)
    md_path.write_text(markdown_table(rows), encoding="utf-8")

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(json_path)
    print(csv_path)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
