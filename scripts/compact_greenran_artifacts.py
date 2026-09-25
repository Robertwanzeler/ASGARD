#!/usr/bin/env python3
"""Audit and compact only reproducible GreenRAN traces, replays and logs.

The audit is written before any source file is removed.  Archives are stored
inside their campaign, so the evidence remains local and recoverable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cleanup_greenran_runs import build_manifest, has_open_file


MIN_BYTES = 1024 * 1024
ARCHIVE_DIR = ".greenran_compacted"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _protected_roots(runs_dir: Path) -> set[Path]:
    inventory = build_manifest(runs_dir, target_free_gb=0).get("inventory", [])
    return {
        Path(item["path"]).resolve()
        for item in inventory
        if item.get("protected_reasons")
    }


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _eligible(path: Path, root: Path, protected: set[Path]) -> bool:
    if not path.is_file() or path.stat().st_size < MIN_BYTES:
        return False
    if any(_under(path, item) for item in protected):
        return False
    relative = path.relative_to(root)
    parts = {part.lower() for part in relative.parts}
    text = str(relative).lower()
    if "final_dataset" in parts or "reports" in parts or "metrics" in parts:
        return False
    if "ns3_traces" in parts or "replay_buffer" in parts or "tasam_true_online_real" in parts:
        return True
    if path.suffix.lower() in {".log", ".out", ".err"}:
        return True
    return "trace" in path.name.lower() and path.suffix.lower() in {".jsonl", ".txt", ".csv"}


def _resolve_campaigns(runs_dir: Path, raw_campaigns: list[str]) -> list[Path]:
    if not raw_campaigns:
        return [
            campaign for campaign in sorted(runs_dir.iterdir())
            if campaign.is_dir() and not campaign.name.startswith("cleanup_audit_")
        ]
    resolved: list[Path] = []
    root = runs_dir.resolve()
    for raw in raw_campaigns:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if candidate == root or not candidate.is_dir():
            raise ValueError(f"campanha de compactação inválida: {candidate}")
        if not _under(candidate, root) or candidate.name.startswith("cleanup_audit_"):
            raise ValueError(f"campanha fora do escopo de runs: {candidate}")
        resolved.append(candidate)
    if len(set(resolved)) != len(resolved):
        raise ValueError("campanhas de compactação duplicadas")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(runs_dir: Path, campaigns: list[Path] | None = None) -> list[dict[str, Any]]:
    protected = _protected_roots(runs_dir)
    entries: list[dict[str, Any]] = []
    for campaign in campaigns or _resolve_campaigns(runs_dir, []):
        for path in campaign.rglob("*"):
            if _eligible(path, runs_dir, protected):
                entries.append({
                    "path": str(path.resolve()),
                    "campaign": str(campaign.resolve()),
                    "relative_to_campaign": str(path.relative_to(campaign)),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                    "reason": "reproducible_trace_replay_or_log",
                    "protected": False,
                })
    return sorted(entries, key=lambda item: item["bytes"], reverse=True)


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _archive_group(campaign: Path, entries: list[dict[str, Any]], archive_root: Path) -> dict[str, Any]:
    archive_dir = campaign / ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / f"artifacts_{archive_root.name}.tar.zst"
    relative_paths = [item["relative_to_campaign"] for item in entries]
    command = ["tar", "--zstd", "-cf", str(archive), "-C", str(campaign), *relative_paths]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not archive.is_file():
        raise RuntimeError(f"falha ao compactar {campaign}: {completed.stderr[-1000:]}")
    with tarfile.open(archive, mode="r:*" ) as handle:
        members = {member.name for member in handle.getmembers()}
    if set(relative_paths) - members:
        raise RuntimeError(f"arquivo compactado incompleto: {archive}")
    removed = []
    for item in entries:
        path = Path(item["path"])
        if has_open_file(path):
            raise RuntimeError(f"arquivo ainda aberto; compactação bloqueada: {path}")
        if item.get("sha256") and _sha256(path) != item["sha256"]:
            raise RuntimeError(f"arquivo mudou depois da auditoria: {path}")
        size = path.stat().st_size
        path.unlink()
        removed.append({**item, "bytes_removed": size, "archive": str(archive)})
    return {
        "campaign": str(campaign),
        "archive": str(archive),
        "archive_bytes": archive.stat().st_size,
        "members": len(removed),
        "removed": removed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--target-free-gb", type=float, default=25.0)
    parser.add_argument(
        "--campaign", action="append", default=[],
        help="campanha explícita dentro de runs; pode ser repetido para restringir a limpeza",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    runs_dir = args.runs_dir.resolve()
    if not runs_dir.is_dir():
        raise SystemExit(f"runs-dir inexistente: {runs_dir}")
    try:
        campaigns = _resolve_campaigns(runs_dir, args.campaign)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    audit_dir = runs_dir / f"cleanup_audit_{_utc_stamp()}"
    audit_dir.mkdir(mode=0o755)
    entries = _inventory(runs_dir, campaigns)
    manifest: dict[str, Any] = {
        "schema": "greenran.cleanup.compaction.v1",
        "created_at": int(time.time()),
        "mode": "apply" if args.apply else "audit",
        "runs_dir": str(runs_dir),
        "target_free_bytes": int(args.target_free_gb * 1_000_000_000),
        "selection": "non-protected ns3_traces/replay_buffer/tasam_true_online_real/log/trace files only",
        "campaign_scope": [str(path) for path in campaigns],
        "candidates": entries,
        "groups": [],
        "removed": [],
        "free_before_bytes": os.statvfs(runs_dir).f_bavail * os.statvfs(runs_dir).f_frsize,
    }
    manifest_path = audit_dir / "compaction_manifest.json"
    _write(manifest_path, manifest)
    if args.apply:
        grouped: dict[Path, list[dict[str, Any]]] = defaultdict(list)
        selected_bytes = 0
        for entry in entries:
            if manifest["free_before_bytes"] + selected_bytes >= manifest["target_free_bytes"]:
                break
            grouped[Path(entry["campaign"])].append(entry)
            selected_bytes += int(entry["bytes"])
        for campaign, group in grouped.items():
            if os.statvfs(runs_dir).f_bavail * os.statvfs(runs_dir).f_frsize >= manifest["target_free_bytes"]:
                break
            result = _archive_group(campaign, group, audit_dir)
            manifest["groups"].append({key: value for key, value in result.items() if key != "removed"})
            manifest["removed"].extend(result["removed"])
            _write(manifest_path, manifest)
        manifest["free_after_bytes"] = os.statvfs(runs_dir).f_bavail * os.statvfs(runs_dir).f_frsize
        manifest["target_reached"] = manifest["free_after_bytes"] >= manifest["target_free_bytes"]
        _write(manifest_path, manifest)
        if not manifest["target_reached"]:
            raise SystemExit(
                "compactação concluída sem atingir a meta; revisar o manifesto antes de outra seleção"
            )
    print(json.dumps({
        "audit_dir": str(audit_dir),
        "candidates": len(entries),
        "candidate_bytes": sum(item["bytes"] for item in entries),
        "free_before_bytes": manifest["free_before_bytes"],
        "free_after_bytes": manifest.get("free_after_bytes"),
        "target_reached": manifest.get("target_reached", False),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
