"""Canonical schedule and storage preflight for deterministic TA-SAM pairs."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from .collection_event_alternator import PROFILES
except ImportError:
    from collection_event_alternator import PROFILES


EXPECTED_STAGES = (
    "allowed_bootstrap",
    "allowed_stable",
    "camera_conditional",
    "camera_blocked",
    "vehicle_conditional",
    "vehicle_blocked",
    "app2_conditional",
    "app2_blocked",
    "allowed_recovery",
)


def canonical_schedule(profile: str, seed: int, wall_time: float, *, tick_s: float = 0.25) -> dict[str, Any]:
    stages = PROFILES[profile]
    stage_specs = [{"index": i, "name": stage.name, "duration_s": stage.duration_s} for i, stage in enumerate(stages, 1)]
    canonical = {
        "profile": profile,
        "seed": int(seed),
        "wall_time_s": float(wall_time),
        "tick_s": float(tick_s),
        "stages": stage_specs,
        "expected_stages": list(EXPECTED_STAGES),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    schedule_id = "pair-" + hashlib.sha256(encoded).hexdigest()[:20]
    slots = max(1, int(round(float(wall_time) / float(tick_s))))
    return {
        "schema": "greenran.tasam_pairing_schedule.v1",
        "schedule_id": schedule_id,
        **canonical,
        "snapshot_slots": slots,
        "snapshot_sequence_format": f"{schedule_id}:snapshot:{{ordinal:06d}}",
    }


def write_schedule(path: Path, schedule: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(schedule, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def storage_is_rw(path: Path) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            ["findmnt", "-T", str(path), "-n", "-o", "OPTIONS"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"findmnt falhou: {exc}"
    options = completed.stdout.strip()
    if completed.returncode != 0 or not options:
        return False, "filesystem não identificado"
    tokens = {item.strip().lower() for item in options.split(",")}
    return "rw" in tokens and "ro" not in tokens, options


def free_gib(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)
