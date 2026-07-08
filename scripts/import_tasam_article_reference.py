#!/usr/bin/env python3
"""Import a notebook-produced TA-SAM article reference bundle into a structured JSON summary."""

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "runs" / "external_references"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import TA-SAM article notebook reference bundle")
    parser.add_argument("--zip", required=True, help="ZIP bundle generated from the notebook experiment")
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Directory where the structured reference summary will be written",
    )
    return parser


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_percent(value: str) -> float:
    return _safe_float(str(value).strip().replace("%", "").replace("+", ""), 0.0)


def _read_zip_members(zip_path: Path) -> dict[str, str]:
    members: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            members[Path(info.filename).name] = archive.read(info.filename).decode("utf-8")
    return members


def _require_match(pattern: str, text: str, label: str) -> re.Match[str]:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        raise ValueError(f"unable to parse {label}")
    return match


def _parse_report(report_text: str) -> dict[str, Any]:
    du_count = int(_require_match(r"\|\s*Número de DUs\s*\|\s*(\d+)\s*\|", report_text, "du_count").group(1))
    ue_count = int(_require_match(r"\|\s*Número de UEs\s*\|\s*(\d+)\s*\|", report_text, "ue_count").group(1))
    rb_per_du = int(_require_match(r"\|\s*RBs por DU\s*\|\s*(\d+)\s*\|", report_text, "rb_per_du").group(1))
    bandwidth_mhz = _safe_float(
        _require_match(r"\|\s*Largura de banda\s*\|\s*([\d.,]+)\s*MHz\s*\|", report_text, "bandwidth_mhz").group(1).replace(",", ".")
    )
    distance_m = _safe_float(
        _require_match(r"\|\s*Distância entre sites\s*\|\s*([\d.,]+)\s*m\s*\|", report_text, "distance_m").group(1).replace(",", ".")
    )
    mobility_match = _require_match(r"\|\s*Mobilidade\s*\|\s*([\d.,]+)[–-]([\d.,]+)\s*m/s\s*\|", report_text, "mobility")
    mobility_min = _safe_float(mobility_match.group(1).replace(",", "."))
    mobility_max = _safe_float(mobility_match.group(2).replace(",", "."))
    sim_steps = int(_require_match(r"Time simTime = Seconds\((\d+)\);", report_text, "sim_steps").group(1))
    alpha_init = _safe_float(_require_match(r"INIT_ALPHA = ([\d.]+);", report_text, "alpha_init").group(1))
    target_entropy = _safe_float(_require_match(r"TARGET_ENTROPY = (-?[\d.]+);", report_text, "target_entropy").group(1))
    target_mmtc = _safe_float(_require_match(r"TARGET_THPT_MMTC = ([\deE.+-]+);", report_text, "target_mmtc").group(1))
    zeta = _safe_float(_require_match(r"ZETA = ([\d.]+);", report_text, "zeta").group(1))
    qos_min = _safe_float(_require_match(r"QOS_MIN = ([\d.]+);", report_text, "qos_min").group(1))
    rho_schedule_steps = int(_require_match(r"RHO_SCHEDULE_STEPS = (\d+);", report_text, "rho_schedule_steps").group(1))

    slice_rows = re.findall(
        r"\|\s*(eMBB|mMTC|URLLC)\s*\|\s*([\d,\.]+)\s*([A-Za-z/]+)\s*\|\s*([\d,\.]+%)\s*\|\s*([^|]+)\|\s*([^|]+)\|",
        report_text,
    )
    slices: list[dict[str, Any]] = []
    for name, rate_value, rate_unit, duty_cycle, packet_size, ue_count_raw in slice_rows:
        slices.append(
            {
                "name": name,
                "rate_value": _safe_float(rate_value.replace(",", ".")),
                "rate_unit": rate_unit.strip(),
                "duty_cycle_pct": _parse_percent(duty_cycle),
                "packet_size": packet_size.strip(),
                "approx_ue_count": ue_count_raw.strip(),
            }
        )

    return {
        "article_title": "Task Specific Sharpness Aware O-RAN Resource Management using Multi Agent Reinforcement Learning",
        "scenario": {
            "du_count": du_count,
            "ue_count": ue_count,
            "rb_per_du": rb_per_du,
            "bandwidth_mhz": bandwidth_mhz,
            "distance_between_sites_m": distance_m,
            "mobility_speed_mps": {"min": mobility_min, "max": mobility_max},
            "sim_steps": sim_steps,
            "slices": slices,
        },
        "training_method": {
            "actor_hidden_dims": [300, 400, 400],
            "critic_hidden_dims": [300, 400, 400],
            "activation": "tanh",
            "learning_rate": 1e-4,
            "gamma": 0.99,
            "tau": 0.005,
            "batch_size": 128,
            "sam": {
                "rho_start": 0.5,
                "rho_final": 0.01,
                "selector": "td_error_variance",
                "selector_threshold": 0.1,
                "rho_schedule_steps": rho_schedule_steps,
            },
            "final_tuning": {
                "alpha_init": alpha_init,
                "target_entropy": target_entropy,
                "target_mmtc_bps": target_mmtc,
                "zeta": zeta,
                "qos_min": qos_min,
            },
        },
    }


def _non_comment_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def _section_after_marker(lines: list[str], marker: str) -> list[str]:
    try:
        start = lines.index(marker)
    except ValueError as exc:
        raise ValueError(f"marker not found: {marker}") from exc
    collected: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("window,") or line.startswith("metric,") or line.startswith("metric,sac,ta_sam"):
            break
        collected.append(line)
    return collected


def _collect_sections(lines: list[str], header: str) -> list[list[str]]:
    sections: list[list[str]] = []
    current: list[str] | None = None
    headers = {"window,sac_steps,sac_alpha,sac_q0,sac_q1,sac_q2,ta_steps,ta_alpha,ta_q0,ta_q1,ta_q2,delta_q0", "metric,value", "metric,sac,ta_sam"}
    for line in lines:
        if line == header:
            if current is not None:
                sections.append(current)
            current = [line]
            continue
        if line in headers:
            if current is not None:
                sections.append(current)
            current = None
            if line == header:
                current = [line]
            continue
        if current is not None:
            current.append(line)
    if current is not None:
        sections.append(current)
    return sections


def _csv_rows(lines: list[str]) -> list[dict[str, str]]:
    if not lines:
        return []
    reader = csv.DictReader(lines)
    return [dict(row) for row in reader]


def _parse_summary_csv(summary_text: str) -> dict[str, Any]:
    lines = _non_comment_lines(summary_text)
    window_sections = _collect_sections(lines, "window,sac_steps,sac_alpha,sac_q0,sac_q1,sac_q2,ta_steps,ta_alpha,ta_q0,ta_q1,ta_q2,delta_q0")
    metric_value_sections = _collect_sections(lines, "metric,value")
    metric_compare_sections = _collect_sections(lines, "metric,sac,ta_sam")

    windows = _csv_rows(window_sections[0])
    last100 = _csv_rows(metric_value_sections[0])
    global_rows = _csv_rows(metric_value_sections[1])
    rb_rows = _csv_rows(metric_compare_sections[0])
    learning_rows = _csv_rows(metric_compare_sections[1])

    def kv(rows: list[dict[str, str]], key_field: str, value_field: str) -> dict[str, str]:
        return {row[key_field]: row[value_field] for row in rows}

    last100_map = kv(last100, "metric", "value")
    global_map = kv(global_rows, "metric", "value")
    rb_map = {row["metric"]: {"sac": row["sac"], "ta_sam": row["ta_sam"]} for row in rb_rows}
    learning_map = {row["metric"]: {"sac": row["sac"], "ta_sam": row["ta_sam"]} for row in learning_rows}

    parsed_windows = []
    for row in windows:
        parsed_windows.append(
            {
                "window": row["window"],
                "sac": {
                    "steps": int(row["sac_steps"]),
                    "alpha": _safe_float(row["sac_alpha"]),
                    "q0": _safe_float(row["sac_q0"]),
                    "q1": _safe_float(row["sac_q1"]),
                    "q2": _safe_float(row["sac_q2"]),
                },
                "tasam": {
                    "steps": int(row["ta_steps"]),
                    "alpha": _safe_float(row["ta_alpha"]),
                    "q0": _safe_float(row["ta_q0"]),
                    "q1": _safe_float(row["ta_q1"]),
                    "q2": _safe_float(row["ta_q2"]),
                },
                "delta_q0_pct": _parse_percent(row["delta_q0"]),
            }
        )

    return {
        "windows": parsed_windows,
        "last100": {
            "sac_q0": _safe_float(last100_map.get("sac_q0_last100")),
            "tasam_q0": _safe_float(last100_map.get("ta_q0_last100")),
            "delta_q0_pct": _parse_percent(last100_map.get("delta_q0_last100", "0")),
            "sac_q1": _safe_float(last100_map.get("sac_q1_last100")),
            "tasam_q1": _safe_float(last100_map.get("ta_q1_last100")),
            "sac_q2": _safe_float(last100_map.get("sac_q2_last100")),
            "tasam_q2": _safe_float(last100_map.get("ta_q2_last100")),
            "sac_alpha_final": _safe_float(last100_map.get("sac_alpha_final")),
            "tasam_alpha_final": _safe_float(last100_map.get("ta_alpha_final")),
            "sac_rho_final": _safe_float(last100_map.get("sac_rho_final")),
            "tasam_rho_final": _safe_float(last100_map.get("ta_rho_final")),
        },
        "global": {
            "sac_q0": _safe_float(global_map.get("sac_q0_global")),
            "tasam_q0": _safe_float(global_map.get("ta_q0_global")),
            "delta_q0_pct": _parse_percent(global_map.get("delta_q0_global", "0")),
            "sac_q1": _safe_float(global_map.get("sac_q1_global")),
            "tasam_q1": _safe_float(global_map.get("ta_q1_global")),
            "sac_q2": _safe_float(global_map.get("sac_q2_global")),
            "tasam_q2": _safe_float(global_map.get("ta_q2_global")),
        },
        "rb_last200": {
            "sac": {
                "embb": int(rb_map["rb_slice0_embb"]["sac"]),
                "mmtc": int(rb_map["rb_slice1_mmtc"]["sac"]),
                "urllc": int(rb_map["rb_slice2_urllc"]["sac"]),
                "total": int(rb_map["rb_total"]["sac"]),
            },
            "tasam": {
                "embb": int(rb_map["rb_slice0_embb"]["ta_sam"]),
                "mmtc": int(rb_map["rb_slice1_mmtc"]["ta_sam"]),
                "urllc": int(rb_map["rb_slice2_urllc"]["ta_sam"]),
                "total": int(rb_map["rb_total"]["ta_sam"]),
            },
        },
        "learning": {
            "catastrophic_forgetting": learning_map.get("catastrophic_forgetting", {}),
            "peak_q0": learning_map.get("peak_q0", {}),
            "final_q0_trend": learning_map.get("final_q0_trend", {}),
            "delta_peak_to_final_pct": {
                "sac": _parse_percent(learning_map["delta_peak_to_final"]["sac"]),
                "tasam": _parse_percent(learning_map["delta_peak_to_final"]["ta_sam"]),
            },
            "exploration_trend": learning_map.get("exploration_trend", {}),
        },
    }


def _parse_tail_csv(text: str) -> dict[str, Any]:
    reader = csv.DictReader(text.splitlines())
    rows = [row for row in reader]
    if not rows:
        raise ValueError("tail csv is empty")
    first = rows[0]
    last = rows[-1]
    return {
        "step_count": len(rows),
        "step_range": {"start": int(first["step"]), "end": int(last["step"])},
        "alpha_range": {"start": _safe_float(first["alpha"]), "end": _safe_float(last["alpha"])},
        "rho_range": {"start": _safe_float(first["rho"]), "end": _safe_float(last["rho"])},
        "q0_range": {"min": min(_safe_float(row["q0_avg"]) for row in rows), "max": max(_safe_float(row["q0_avg"]) for row in rows)},
    }


def build_reference_payload(zip_path: Path) -> dict[str, Any]:
    members = _read_zip_members(zip_path)
    report_text = members["relatorio_metodologia_tasam.md"]
    summary_text = members["resultados_experimentos_tasam.csv"]
    tasam_tail_text = members["tasam_ultimos100.csv"]
    sac_tail_text = members["sac_ultimos100.csv"]

    report = _parse_report(report_text)
    summary = _parse_summary_csv(summary_text)
    tasam_tail = _parse_tail_csv(tasam_tail_text)
    sac_tail = _parse_tail_csv(sac_tail_text)

    return {
        "schema": "greenran.tasam_external_article_reference.v1",
        "source": "external_article_reference",
        "source_type": "zip_report_bundle",
        "scenario_family": "article_reference",
        "label": zip_path.stem,
        "artifact_path": str(zip_path.resolve()),
        "article": {"title": report["article_title"], "id": "arXiv:2511.15002"},
        "scenario": report["scenario"],
        "training_method": report["training_method"],
        "results": summary,
        "tails": {"tasam": tasam_tail, "sac": sac_tail},
        "limitations": {
            "has_checkpoint": False,
            "has_resume_checkpoint": False,
            "has_runtime_export": False,
            "comparable_scope": "method_behavior_only",
        },
        "derived": {
            "tasam_advantage_last100_pct": summary["last100"]["delta_q0_pct"],
            "tasam_advantage_global_pct": summary["global"]["delta_q0_pct"],
            "sac_peak_to_final_pct": summary["learning"]["delta_peak_to_final_pct"]["sac"],
            "ta_peak_to_final_pct": summary["learning"]["delta_peak_to_final_pct"]["tasam"],
            "tasam_reduces_forgetting": summary["learning"]["delta_peak_to_final_pct"]["tasam"] > summary["learning"]["delta_peak_to_final_pct"]["sac"],
            "tasam_beats_sac_last100": summary["last100"]["tasam_q0"] > summary["last100"]["sac_q0"],
        },
    }


def _markdown(payload: dict[str, Any]) -> str:
    scenario = payload["scenario"]
    method = payload["training_method"]
    results = payload["results"]
    derived = payload["derived"]
    return (
        f"# TA-SAM External Article Reference\n\n"
        f"- source: `{payload['artifact_path']}`\n"
        f"- scenario: {scenario['du_count']} DUs, {scenario['ue_count']} UEs, {scenario['bandwidth_mhz']} MHz, {scenario['sim_steps']} steps\n"
        f"- method: SAC + selective SAM, rho {method['sam']['rho_start']} -> {method['sam']['rho_final']}, threshold={method['sam']['selector_threshold']}\n"
        f"- last100 advantage: {derived['tasam_advantage_last100_pct']:.1f}%\n"
        f"- global advantage: {derived['tasam_advantage_global_pct']:.1f}%\n"
        f"- forgetting delta (SAC): {derived['sac_peak_to_final_pct']:.1f}%\n"
        f"- forgetting delta (TA-SAM): {derived['ta_peak_to_final_pct']:.1f}%\n"
        f"- reduces forgetting: {'yes' if derived['tasam_reduces_forgetting'] else 'no'}\n"
        f"- comparable scope: {payload['limitations']['comparable_scope']}\n"
        f"\n## Final Window\n\n"
        f"- SAC q0 last100: {results['last100']['sac_q0']:.4f}\n"
        f"- TA-SAM q0 last100: {results['last100']['tasam_q0']:.4f}\n"
        f"- SAC alpha final: {results['last100']['sac_alpha_final']:.4f}\n"
        f"- TA-SAM alpha final: {results['last100']['tasam_alpha_final']:.4f}\n"
    )


def main() -> int:
    args = build_parser().parse_args()
    zip_path = Path(args.zip)
    output_root = Path(args.output_root) / zip_path.stem
    output_root.mkdir(parents=True, exist_ok=True)

    payload = build_reference_payload(zip_path)
    json_path = output_root / "tasam_external_article_reference.json"
    md_path = output_root / "tasam_external_article_reference.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "label": payload["label"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
