"""Reject V2X runs whose ns-3 executable predates its evidence producers.

The strict V2X contract depends on traces emitted by the scenario, the GBR
scheduler and the PDCP/bearer connector.  A successful process launch is not
scientific evidence if it used an executable built before any of those sources.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V2X_EVIDENCE_SOURCES = (
    ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/scratch/Energy_saving_with_cell_utilization_scenario.cc",
    ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/src/mmwave/helper/mmwave-bearer-stats-connector.cc",
    ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/src/mmwave/model/mmwave-flex-tti-mac-scheduler.cc",
)


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_provenance(binary: Path) -> dict[str, object]:
    """Return hashes for the executable, loaded mmWave library and V2X sources."""
    binary = binary.resolve()
    lib_candidates = [
        binary.parents[1] / "lib" / "libns3.42-mmwave-optimized.so",
        binary.parents[1] / "lib" / "libns3.42-mmwave.so",
        binary.parents[2] / "lib" / "libns3.42-mmwave-optimized.so",
        binary.parents[2] / "lib" / "libns3.42-mmwave.so",
    ]
    library = next((path for path in lib_candidates if path.is_file()), None)
    return {
        "binary": str(binary),
        "binary_sha256": _sha256(binary) if binary.is_file() else "",
        "mmwave_library": str(library) if library else "",
        "mmwave_library_sha256": _sha256(library) if library else "",
        "sources": {
            str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path):
            _sha256(path) for path in V2X_EVIDENCE_SOURCES if path.is_file()
        },
        "evidence_version": "v6",
    }


def stale_v2x_binary_sources(binary: Path, *, sources: Iterable[Path] = V2X_EVIDENCE_SOURCES) -> tuple[Path, ...]:
    """Return evidence-producing sources newer than *binary*.

    Missing sources are deliberately ignored here: the normal build is the
    authority for its dependency graph, while this guard only detects a known
    invalid provenance relationship before a campaign directory is created.
    """
    resolved_binary = binary.resolve()
    if not resolved_binary.is_file():
        return ()
    binary_mtime_ns = resolved_binary.stat().st_mtime_ns
    return tuple(
        source.resolve()
        for source in sources
        if source.is_file() and source.stat().st_mtime_ns > binary_mtime_ns
    )


def assert_v2x_binary_fresh(binary: Path, *, sources: Iterable[Path] = V2X_EVIDENCE_SOURCES) -> None:
    """Fail closed when the executable cannot produce the declared traces."""
    stale = stale_v2x_binary_sources(binary, sources=sources)
    if not stale:
        return
    listed = ", ".join(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path) for path in stale)
    build_dir = ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran"
    raise SystemExit(
        "binário ns-3 desatualizado para a telemetria V2X; fontes mais novas: "
        f"{listed}. Recompile antes da campanha: cd {build_dir} && ./ns3 build"
    )
