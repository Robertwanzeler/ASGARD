#!/usr/bin/env python3
"""Audit and conservatively compact historical GreenRAN run artefacts.

The tool is deliberately opt-in: without ``--apply`` it writes only an audit
manifest.  With ``--apply`` it will never remove a run root, source code, or a
protected ARMD/current campaign.  Every deletion is listed in the manifest
before it is attempted.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "greenran.runs_cleanup.v1"
ACTIVE_TOKENS = (
    "ns3.42",
    "rapp_orchestrator",
    "csv_to_metrics",
    "run_tasam",
    "wall_clock_supervisor",
)
CURRENT_EXACT = {
    "tasam_article_ns3_collection",
    "tasam_economic_checkpoint_seed47_20260908",
    "tasam_local_checkpoint_seed47_20260906",
    "tasam_agent_online_economic_seed47_20260909_v3",
}
CURRENT_PREFIXES = (
    "tasam_local_causal_pilot_",
    "tasam_causal_",
    "tasam_energy_calibration_",
)
# ASGARD adaptation/evidence is the current research line.  Keep every
# completed retry immutable during conservative space recovery, including
# retries which ended in a disk-guard failure and therefore do not expose a
# "running" status in their manifests.
ASGARD_PROTECTED_PREFIXES = (
    "tasam_asgard_adaptation_",
    "tasam_asgard_smoke_",
)
ARMD_PREFIXES = ("greenran_tasam_e2_active_",)
COMPACTION_PREFIXES = (
    "tasam_block_state_",
    "tasam_greenran_online_",
    "tasam_greenran_clean_",
    "tasam_greenran_train_",
    "tasam_greenran_targeted_",
    "tasam_greenran_real_only_",
    "tasam_greenran_diversity_",
    "tasam_greenran_article_train_",
    "tasam_greenran_3du_",
    "tasam_ab_cross_validation_",
    "tasam_online_economic_",
    # Observation-only campaigns are finalized diagnostics.  Their raw ns-3
    # traces and temporary replay/candidate artefacts can be compacted after
    # the retention window while the campaign root, manifest, final summary,
    # and initial checkpoint remain intact.
    "tasam_online_observation_",
    "tasam_learning_meter_",
    "tasam_seed45_",
    "tasam_cvar_",
    "tasam_operational_",
    "greenran_shadow_",
    "greenran_autonomous_",
    "greenran_tasam_3du_article_adapted_",
    "tasam_missing_",
)
COMPACT_DIR_NAMES = {
    "ns3_traces": "ns-3/PDCP trace reproduzível",
    "replay_buffer": "replay buffer intermediário",
    "tasam_true_online_real": "trace online bruto",
    ".work": "exportação temporária",
    "judge_reward_traces": "traces brutos de recompensa",
    "tasam_article_export": "exportação derivada de artigo",
}
VERBOSE_LOG_MIN_BYTES = 64 * 1024 * 1024
DATE_RE = re.compile(r"20\d{6}")


def bytes_in(path: Path) -> int:
    """Return allocated bytes using the native disk scanner.

    ``du`` is substantially faster than traversing very large trace trees from
    Python and reports the allocated space that this cleanup can actually free.
    """
    if path.is_symlink():
        return path.lstat().st_size
    result = subprocess.run(
        ["du", "-sk", "--", str(path)], text=True, capture_output=True, check=True
    )
    return int(result.stdout.split()[0]) * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def campaign_date(root: Path) -> datetime | None:
    match = DATE_RE.search(root.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(), "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def json_files(root: Path) -> Iterable[Path]:
    yield from root.glob("*.json")
    for name in ("online_status.json", "campaign_manifest.json", "arm_manifest.json"):
        candidate = root / name
        if candidate.exists():
            yield candidate


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def protected_reasons(root: Path) -> list[str]:
    reasons: list[str] = []
    if root.name in CURRENT_EXACT or root.name.startswith(CURRENT_PREFIXES):
        reasons.append("campanha/checkpoint atual")
    if root.name.startswith(ASGARD_PROTECTED_PREFIXES):
        reasons.append("campanha ASGARD protegida")
    if root.name.startswith(ARMD_PREFIXES) or (root / "arm_manifest.json").exists():
        reasons.append("campanha ARMD dedicada")
    # A dedicated ARMD experiment may expose its mode only through its layout.
    if any(child.is_dir() and "armd" in child.name.lower() for child in root.iterdir()):
        reasons.append("layout de experimento ARMD")
    return reasons


def has_active_status(root: Path) -> bool:
    for path in json_files(root):
        payload = read_json(path)
        if not payload:
            continue
        for key in ("status", "state"):
            value = str(payload.get(key, "")).strip().lower()
            if value in {"running", "active", "starting", "pending"}:
                return True
    return False


def active_greenran_processes() -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            command = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace"
            )
        except OSError:
            continue
        if any(token in command for token in ACTIVE_TOKENS):
            found.append({"pid": proc.name, "command": command})
    return found


def under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def candidate_references(root: Path) -> set[Path]:
    references: set[Path] = set()
    # Candidate selection is recorded by the online control-state files.  Do
    # not parse every JSON artefact: some historical exports are multi-gigabyte
    # datasets that have no checkpoint references.
    state_files = []
    for filename in ("online_status.json", "online_state.json", "online_control_gate.json"):
        state_files.extend(root.rglob(filename))
    for path in state_files:
        payload = read_json(path)
        if not payload:
            continue
        # ``history`` records every candidate ever evaluated.  It is audit
        # information, not a retention request.  Keep only checkpoints which
        # the final state identifies as active/safe for rollback.
        for key in ("active_checkpoint", "last_good_checkpoint", "candidate_checkpoint"):
            value = payload.get(key)
            if isinstance(value, str) and "/candidates/candidate_" in value:
                references.add(Path(value).resolve())
    return references


def add_candidate(
    candidates: list[dict[str, Any]],
    path: Path,
    reason: str,
    *,
    allow_protected: bool = False,
    measure: bool = True,
) -> None:
    resolved = path.resolve()
    if not path.exists() or any(under(resolved, Path(item["path"])) for item in candidates if Path(item["path"]).exists()):
        return
    candidates[:] = [item for item in candidates if not under(Path(item["path"]), resolved)]
    candidates.append(
        {
            "path": str(resolved),
            # Thousands of small epoch checkpoints are intentionally measured
            # only at deletion time; measuring each one makes an audit slower
            # than the cleanup itself.
            "bytes": bytes_in(resolved) if measure else 0,
            "reason": reason,
            "allow_protected": allow_protected,
        }
    )


def duplicate_snapshots(root: Path, candidates: list[dict[str, Any]]) -> None:
    # The article collection is the only campaign with an explicitly verified
    # duplicate snapshot in this cleanup.  Do not spend hours hashing unrelated
    # historical databases merely because their sizes happen to match.
    if root.name != "tasam_article_ns3_collection":
        return
    snapshots = root / "db_snapshots"
    latest_manifest = snapshots / "latest_snapshot.json"
    if not snapshots.is_dir() or not latest_manifest.exists():
        return
    manifest = read_json(latest_manifest) or {}
    latest = Path(str(manifest.get("latest_snapshot", ""))).resolve()
    files = [path for path in snapshots.glob("*.db") if path.is_file()]
    by_size: dict[int, list[Path]] = {}
    for path in files:
        by_size.setdefault(path.stat().st_size, []).append(path)
    # Hash only equal-sized candidates.  This keeps a normal audit fast even
    # when historical campaigns retain many distinct multi-gigabyte snapshots.
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        hashes: dict[str, list[Path]] = {}
        for path in same_size:
            hashes.setdefault(sha256(path), []).append(path)
        for identical in hashes.values():
            if len(identical) < 2:
                continue
            keep = latest if latest in identical else max(identical, key=lambda item: item.stat().st_mtime)
            for path in identical:
                if path != keep:
                    add_candidate(
                        candidates,
                        path,
                        f"snapshot SQLite duplicado de {keep.name}",
                        allow_protected=True,
                    )


def add_old_campaign_candidates(root: Path, candidates: list[dict[str, Any]]) -> None:
    retained_candidates = candidate_references(root)
    for current, dirs, files in os.walk(root, topdown=True):
        current_path = Path(current)
        if current_path.name in COMPACT_DIR_NAMES:
            add_candidate(candidates, current_path, COMPACT_DIR_NAMES[current_path.name])
            dirs[:] = []
            continue
        if current_path.name == "candidates":
            for child in list(dirs):
                candidate = current_path / child
                if not any(under(reference, candidate) for reference in retained_candidates):
                    add_candidate(candidates, candidate, "checkpoint candidato não promovido")
            dirs[:] = [child for child in dirs if not (current_path / child).exists()]
        if current_path.name == "checkpoints":
            epochs = sorted(
                (current_path / child for child in dirs if child.startswith("epoch_")),
                key=lambda item: item.name,
            )
            for epoch in epochs[:-1]:
                add_candidate(
                    candidates,
                    epoch,
                    "checkpoint intermediário de época",
                    measure=False,
                )
        for file_name in files:
            file_path = current_path / file_name
            if file_path.suffix == ".log" and file_path.stat().st_size >= VERBOSE_LOG_MIN_BYTES:
                add_candidate(candidates, file_path, "log verboso reproduzível")

    # Cross-validation stores per-repetition raw runs under profiles; a compact
    # evidence bundle is written before this directory is removed.
    if root.name.startswith("tasam_ab_cross_validation") and (root / "profiles").is_dir():
        add_candidate(candidates, root / "profiles", "repetições brutas de validação cruzada")


def is_compaction_family(root: Path) -> bool:
    return root.name.startswith(COMPACTION_PREFIXES)


def bundle_evidence(root: Path, audit_dir: Path) -> Path:
    """Keep compact JSON/CSV evidence before deleting cross-validation profiles."""
    import tarfile

    destination = audit_dir / f"{root.name}_metrics_evidence.tar.gz"
    with tarfile.open(destination, "w:gz") as archive:
        for path in root.rglob("*"):
            if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
                continue
            if path.suffix.lower() not in {".json", ".jsonl", ".csv", ".md", ".txt"}:
                continue
            try:
                archive.add(path, arcname=str(path.relative_to(root)))
            except FileNotFoundError:
                # A resumed audit may refer to a file already removed by its
                # own earlier pass.  The remaining evidence is still valid.
                continue
    return destination


def build_manifest(runs_dir: Path, target_free_gb: float) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=5)
    free_before = shutil.disk_usage(runs_dir).free
    target_bytes = int(target_free_gb * 1_000_000_000)
    inventory: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for root in sorted(path for path in runs_dir.iterdir() if path.is_dir() and not path.name.startswith("cleanup_audit_")):
        reasons = protected_reasons(root)
        status_active = has_active_status(root)
        dated = campaign_date(root)
        root_bytes = bytes_in(root)
        inventory.append(
            {
                "path": str(root.resolve()),
                "bytes": root_bytes,
                "protected_reasons": reasons,
                "active_status": status_active,
                "campaign_date": dated.isoformat() if dated else None,
            }
        )
        duplicate_snapshots(root, candidates)
        if reasons or status_active:
            continue
        # A blocked diagnostic campaign can be compacted before the normal
        # five-day retention window once its manifest is final.  This is
        # intentionally narrow: active/recent ASGARD and causal campaigns do
        # not qualify.
        final_blocked_learning_meter = False
        if root.name.startswith("tasam_learning_meter_"):
            campaign_manifest = read_json(root / "campaign_manifest.json")
            final_blocked_learning_meter = str((campaign_manifest or {}).get("status", "")) == "adaptation_blocked"
        is_old = (dated is not None and dated < cutoff) or final_blocked_learning_meter
        if is_old and is_compaction_family(root):
            add_old_campaign_candidates(root, candidates)
    candidates.sort(key=lambda item: item["bytes"], reverse=True)
    return {
        "schema": SCHEMA,
        "created_at": now.isoformat(),
        "runs_dir": str(runs_dir.resolve()),
        "target_free_gb": target_free_gb,
        "target_free_bytes": int(target_free_gb * 1_000_000_000),
        "free_before_bytes": free_before,
        "target_reached": free_before >= target_bytes,
        "active_processes": active_greenran_processes(),
        "inventory": inventory,
        "candidates": candidates,
    }


def has_open_file(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["lsof", "-t", "--", str(path)], text=True, capture_output=True, check=False
        )
    except FileNotFoundError:
        return False
    return bool(result.stdout.strip())


def validate_duplicate_snapshot(path: Path) -> None:
    latest_manifest = path.parent / "latest_snapshot.json"
    payload = read_json(latest_manifest)
    if not payload:
        raise RuntimeError(f"manifesto de snapshot ausente: {latest_manifest}")
    latest = Path(str(payload.get("latest_snapshot", ""))).resolve()
    if path.resolve() == latest or not latest.is_file():
        raise RuntimeError(f"snapshot não é mais um duplicado removível: {path}")
    if sha256(path) != sha256(latest):
        raise RuntimeError(f"hash do snapshot mudou; limpeza bloqueada: {path}")


def apply_manifest(manifest: dict[str, Any], audit_dir: Path) -> None:
    runs_dir = Path(manifest["runs_dir"])
    active = active_greenran_processes()
    if active:
        details = "; ".join(
            f"pid={item['pid']} cmd={item['command']}" for item in active[:12]
        )
        suffix = "" if len(active) <= 12 else f"; ... (+{len(active) - 12} processos)"
        raise RuntimeError(
            "processos GreenRAN ativos; limpeza bloqueada: " + details + suffix
        )
    target_free = int(
        manifest.get(
            "target_free_bytes",
            float(manifest.get("target_free_gb", manifest.get("target_free_gib", 50)))
            * 1_000_000_000,
        )
    )
    # Resuming an interrupted cleanup must retain the audit trail already
    # written by a previous invocation.
    removed: list[dict[str, Any]] = list(manifest.get("removed", []))
    blocked: list[dict[str, Any]] = list(manifest.get("blocked", []))
    protected = {
        entry["path"]
        for entry in manifest["inventory"]
        if entry["protected_reasons"]
    }
    for item in manifest["candidates"]:
        if shutil.disk_usage(runs_dir).free >= target_free:
            break
        path = Path(item["path"])
        if not path.exists():
            continue
        campaign = next((root for root in runs_dir.iterdir() if under(path, root)), None)
        if campaign is None or not under(path, runs_dir) or path == campaign:
            raise RuntimeError(f"alvo fora do escopo seguro: {path}")
        if str(campaign.resolve()) in protected and not item["allow_protected"]:
            continue
        if has_open_file(path):
            raise RuntimeError(f"arquivo ainda aberto; limpeza bloqueada: {path}")
        if item["reason"].startswith("snapshot SQLite duplicado"):
            validate_duplicate_snapshot(path)
        if item["reason"] == "repetições brutas de validação cruzada":
            item["evidence_bundle"] = str(bundle_evidence(campaign, audit_dir))
        size = bytes_in(path)
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except PermissionError as exc:
            # Historical jobs may have been created as nobody/root.  A
            # non-privileged cleanup must skip those artefacts and continue
            # with independently audited user-owned candidates; it must never
            # broaden authority or report a partial removal as successful.
            blocked.append({**item, "reason_blocked": f"permission_denied:{exc}"})
            manifest["blocked"] = blocked
            (audit_dir / "cleanup_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            continue
        removed.append({**item, "bytes_removed": size})
        manifest["removed"] = removed
        manifest["free_current_bytes"] = shutil.disk_usage(runs_dir).free
        (audit_dir / "cleanup_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    manifest["removed"] = removed
    manifest["blocked"] = blocked
    manifest["free_after_bytes"] = shutil.disk_usage(runs_dir).free
    manifest["target_reached"] = manifest["free_after_bytes"] >= target_free


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument(
        "--target-free-gb",
        "--target-free-gib",
        dest="target_free_gb",
        type=float,
        default=50.0,
        help="meta decimal em GB; --target-free-gib permanece como alias",
    )
    parser.add_argument("--apply", action="store_true", help="remove only the audited candidates")
    args = parser.parse_args()
    runs_dir = args.runs_dir.resolve()
    if not runs_dir.is_dir():
        raise SystemExit(f"runs-dir inexistente: {runs_dir}")
    lock_handle = None
    try:
        if args.apply:
            lock_handle = (runs_dir / ".greenran_cleanup.lock").open("a+")
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SystemExit("outra limpeza GreenRAN já está em execução") from exc
        audit_dir = runs_dir / f"cleanup_audit_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        audit_dir.mkdir(mode=0o755)
        manifest = build_manifest(runs_dir, args.target_free_gb)
        manifest["mode"] = "apply" if args.apply else "audit"
        (audit_dir / "cleanup_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if args.apply:
            apply_manifest(manifest, audit_dir)
            (audit_dir / "cleanup_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({
            "audit_dir": str(audit_dir),
            "candidates": len(manifest["candidates"]),
            "candidate_bytes": sum(item["bytes"] for item in manifest["candidates"]),
            "free_bytes": shutil.disk_usage(runs_dir).free,
            "target_reached": manifest.get("target_reached", False),
        }, indent=2))
        return 0
    finally:
        if lock_handle is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
