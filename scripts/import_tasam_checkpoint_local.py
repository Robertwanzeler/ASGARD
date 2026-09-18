#!/usr/bin/env python3
"""Import and freeze the compatible TA-SAM checkpoint into the local workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

try:
    from run_tasam_online_arm import _validate_checkpoint, checkpoint_fingerprint
except ModuleNotFoundError:
    from scripts.run_tasam_online_arm import _validate_checkpoint, checkpoint_fingerprint

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = ROOT / "runs/tasam_local_checkpoint_seed47_20260906"


def _runtime_fingerprint(path: Path) -> str:
    """Hash model files while excluding this import bookkeeping manifest."""
    digest = hashlib.sha256()
    files = sorted(
        candidate for candidate in path.rglob("*")
        if candidate.is_file() and candidate.name != "local_import_manifest.json"
    )
    for candidate in files:
        relative = candidate.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(candidate.stat().st_size.to_bytes(8, "big"))
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def import_checkpoint(source: Path, destination: Path) -> dict:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise SystemExit(f"checkpoint de origem ausente: {source}")
    try:
        destination.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise SystemExit(f"destino precisa estar no workspace local: {destination}") from exc
    _validate_checkpoint(source)
    source_fingerprint = _runtime_fingerprint(source)
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            try:
                _validate_checkpoint(destination)
                destination_fingerprint = _runtime_fingerprint(destination)
            except SystemExit as exc:
                raise SystemExit(f"destino local já existe e não é compatível: {destination}: {exc}") from exc
            if destination_fingerprint != source_fingerprint:
                raise SystemExit("destino local existe, mas diverge do checkpoint de origem; nenhum arquivo foi sobrescrito")
    else:
        shutil.copytree(source, destination)
    _validate_checkpoint(destination)
    destination_fingerprint = _runtime_fingerprint(destination)
    if destination_fingerprint != source_fingerprint:
        raise SystemExit("falha de integridade: fingerprint local diferente da origem")
    manifest = {
        "schema": "greenran.tasam.local_checkpoint_import.v1",
        "source_fingerprint": source_fingerprint,
        "local_fingerprint": destination_fingerprint,
        "source_used_only_during_import": True,
        "runtime_checkpoint": str(destination),
        "files": sorted(str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()),
    }
    (destination / "local_import_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", type=Path, required=True,
        help="checkpoint de origem usado somente nesta migração explícita",
    )
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    print(json.dumps(import_checkpoint(args.source, args.destination), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
