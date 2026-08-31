#!/usr/bin/env python3
"""Summarize real-only fairness during the GreenRAN contention profile."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _group_summary(ue_metrics: dict, group: str) -> dict:
    rows = [
        value for value in ue_metrics.values()
        if str(value.get("device_type", "")).strip().lower() == group
    ]
    observed = [row for row in rows if float(row.get("throughput_kbps", 0.0) or 0.0) > 0.0]
    latencies = [float(row.get("latency_us", 0.0) or 0.0) / 1000.0 for row in observed if float(row.get("latency_us", 0.0) or 0.0) > 0.0]
    throughput = sum(float(row.get("throughput_kbps", 0.0) or 0.0) for row in rows)
    return {
        "active": len(rows),
        "observed": len(observed),
        "throughput_kbps": round(throughput, 3),
        "min_latency_ms": round(min(latencies), 3) if latencies else None,
        "max_latency_ms": round(max(latencies), 3) if latencies else None,
        "critical": sum(bool(row.get("is_critical")) for row in rows),
        "starvation": bool(rows and not observed),
    }


def _latest_stage(log_path: Path) -> str:
    if not log_path.exists():
        return "unknown"
    stage = "unknown"
    pattern = re.compile(r"\[RAN_PRESSURE\].*?stage=([^ ]+)")
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.search(line)
        if match:
            stage = match.group(1)
    return stage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    metrics_path = args.state_dir / "xapp_metrics" / "extended_metrics.json"
    wall_status_path = args.state_dir / "wall_clock_status.json"
    if not metrics_path.exists():
        print(json.dumps({"status": "waiting_for_metrics"}, ensure_ascii=False))
        return 0

    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    global_metrics = payload.get("global_metrics", {})
    groups = {group: _group_summary(payload.get("ue_metrics", {}), group) for group in ("camera", "sensor", "vehicle")}
    starvation = [group for group, summary in groups.items() if summary["starvation"]]
    result = {
        "stage": _latest_stage(args.state_dir / "ns3.log"),
        "real_only": global_metrics.get("proxy_latency_sample_count", 0) == 0,
        "cvar_ms": round(float(global_metrics.get("cvar_per_ue_us", 0.0) or 0.0) / 1000.0, 3),
        "p95_per_ue_ms": round(float(global_metrics.get("latency_p95_per_ue_us", 0.0) or 0.0) / 1000.0, 3),
        "critical_ues": int(global_metrics.get("total_critical_ues", 0) or 0),
        "groups": groups,
        "starvation_groups": starvation,
        "fairness_ok": not starvation,
    }
    if wall_status_path.exists():
        try:
            wall_status = json.loads(wall_status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            wall_status = {}
        if isinstance(wall_status, dict):
            result["wall_clock"] = {
                key: wall_status.get(key)
                for key in (
                    "phase",
                    "started_at",
                    "shutdown_started_at",
                    "finished_at",
                    "requested_duration_s",
                    "elapsed_s",
                    "remaining_s",
                    "final_export_code",
                )
                if key in wall_status
            }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
