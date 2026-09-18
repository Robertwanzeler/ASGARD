#!/usr/bin/env python3
"""Collect comparable host-infrastructure metrics for one GreenRAN arm."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from greenran_infra_budget import CgroupV2Controller, GROUPS, ResourceAccumulator

SCHEMA = "greenran.infrastructure.metrics.v1"


def _artifact_bytes(run_dir: Path, excluded: Path) -> int:
    total = 0
    for path in run_dir.rglob("*"):
        try:
            if path.is_file() and path.resolve() != excluded.resolve():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _management_bytes(audit_path: Path) -> int:
    total = 0
    try:
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if isinstance(row, dict):
                total += max(0, int(row.get("management_tx_bytes", 0) or 0))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return total


class InfrastructureMonitor:
    def __init__(self, run_dir: Path | str, *, cgroup_root: Path | str | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.output = self.run_dir / "infrastructure_metrics.json"
        # An explicitly supplied root is used by tests and by privileged
        # deployments.  In the managed smoke environment the launcher can
        # intentionally run without kernel cgroup enforcement; in that case
        # zero-valued counters must not be presented as measured savings.
        self.cgroup_enforced = cgroup_root is not None or os.environ.get(
            "GREENRAN_CGROUP_ENFORCE", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.controller = CgroupV2Controller(cgroup_root) if cgroup_root else CgroupV2Controller()
        self.accumulators = {name: ResourceAccumulator() for name in GROUPS}
        self.first: dict[str, dict[str, int]] = {}
        self.latest: dict[str, dict[str, Any]] = {}
        self.errors: list[str] = []
        if not self.cgroup_enforced:
            self.errors.append("cgroup_enforcement_disabled:host_infra_counters_unavailable")

    def sample(self) -> None:
        for name in GROUPS:
            try:
                raw = self.controller.snapshot(name)
                if name not in self.first:
                    self.first[name] = raw
                self.latest[name] = self.accumulators[name].observe(raw)
            except (OSError, ValueError) as exc:
                self.errors.append(f"{name}:{exc}")

    def report(self) -> dict[str, Any]:
        groups: dict[str, dict[str, float | int]] = {}
        totals = {
            "cpu_usage_usec": 0,
            "memory_byte_seconds": 0.0,
            "memory_peak_bytes": 0,
            "management_tx_bytes": _management_bytes(
                self.run_dir / "xapp_intents" / "tasam_control_audit.jsonl"
            ),
            "io_bytes": 0,
            "artifact_bytes": _artifact_bytes(self.run_dir, self.output),
        }
        for name in GROUPS:
            first = self.first.get(name, {})
            latest = self.latest.get(name, {})
            cpu = max(0, int(latest.get("cpu_usage_usec", 0)) - int(first.get("cpu_usage_usec", 0)))
            read_delta = max(0, int(latest.get("io_read_bytes", 0)) - int(first.get("io_read_bytes", 0)))
            write_delta = max(0, int(latest.get("io_write_bytes", 0)) - int(first.get("io_write_bytes", 0)))
            memory_bs = max(0.0, float(latest.get("memory_byte_seconds", 0.0)))
            memory_peak = max(0, int(latest.get("observed_memory_peak_bytes", 0)))
            groups[name] = {
                "cpu_usage_usec": cpu,
                "memory_byte_seconds": memory_bs,
                "memory_peak_bytes": memory_peak,
                "io_read_bytes": read_delta,
                "io_write_bytes": write_delta,
                "io_bytes": read_delta + write_delta,
            }
            totals["cpu_usage_usec"] += cpu
            totals["memory_byte_seconds"] += memory_bs
            totals["memory_peak_bytes"] += memory_peak
            totals["io_bytes"] += read_delta + write_delta
        complete = (
            self.cgroup_enforced
            and len(self.first) == len(GROUPS)
            and len(self.latest) == len(GROUPS)
        )
        return {
            "schema": SCHEMA,
            "complete": complete,
            "enforced": self.cgroup_enforced,
            "collection_mode": "cgroup_v2" if self.cgroup_enforced else "unavailable",
            "clock": "host_monotonic",
            "groups": groups,
            "totals": totals,
            "errors": self.errors[-50:],
        }

    def write(self) -> None:
        payload = self.report()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--cgroup-root", type=Path)
    args = parser.parse_args()
    if not math.isfinite(args.duration_seconds) or args.duration_seconds <= 0:
        parser.error("duration-seconds must be positive")
    monitor = InfrastructureMonitor(args.run_dir, cgroup_root=args.cgroup_root)
    deadline = time.monotonic() + args.duration_seconds
    while True:
        monitor.sample()
        monitor.write()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(max(0.1, args.interval_seconds), remaining))
    return 0 if monitor.report()["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
