#!/usr/bin/env python3
"""Physical cgroup-v2 budgets and auditable infrastructure counters."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


GROUPS = ("simulator", "ric_xapps", "rapp_armd", "tasam", "collectors")
CGROUP_ROOT = Path(os.environ.get("GREENRAN_CGROUP_ROOT", "/sys/fs/cgroup/greenran"))
REQUIRED_CONTROLLERS = ("cpu", "memory", "io")
CGROUP_ROOT_FILE = Path("/run/greenran-cgroup-root")
DELEGATION_BOOTSTRAP_HINT = (
    "delegação cgroup GreenRAN indisponível; execute uma única vez: "
    "sudo bash /home/robert/orange_nuclear/scripts/"
    "install_greenran_cgroup_delegation.sh"
)


class InfraBudgetError(RuntimeError):
    pass


def resolve_cgroup_root(root: Path | str | None = None) -> Path:
    """Resolve the bootstrapped per-user cgroup subtree.

    The kernel requires write access to the common ancestor of a process's
    current cgroup and its destination. The bootstrap therefore places the
    GreenRAN subtree under ``user@UID.service`` and records that path in
    ``/run``. A caller-provided root or environment override remains useful
    for tests and explicit diagnostics.
    """
    if root is not None:
        return Path(root)
    configured = os.environ.get("GREENRAN_CGROUP_ROOT", "").strip()
    if configured:
        return Path(configured)
    try:
        if CGROUP_ROOT_FILE.is_file():
            value = CGROUP_ROOT_FILE.read_text(encoding="utf-8").strip()
            if value:
                return Path(value)
    except OSError:
        pass
    return CGROUP_ROOT


def cgroup_delegation_status(root: Path | str | None = None) -> dict[str, Any]:
    """Inspect whether the fixed GreenRAN cgroup tree is usable by this user.

    This helper intentionally never writes under ``/sys/fs/cgroup``. The
    privileged bootstrap owns controller delegation and group creation; normal
    GreenRAN launches use this check before writing an experiment artifact.
    """
    hierarchy = resolve_cgroup_root(root)
    result: dict[str, Any] = {
        "schema": "greenran.cgroup_delegation.v1",
        "root": str(hierarchy),
        "required_controllers": list(REQUIRED_CONTROLLERS),
        "valid": False,
        "failures": [],
        "groups": {},
    }
    failures: list[str] = result["failures"]
    if not hierarchy.is_dir():
        failures.append(f"hierarquia ausente: {hierarchy}")
        return result

    def read_words(path: Path, label: str) -> set[str]:
        try:
            return set(path.read_text(encoding="ascii").split())
        except OSError as exc:
            failures.append(f"não foi possível ler {label}: {exc}")
            return set()

    controllers = read_words(hierarchy / "cgroup.controllers", "cgroup.controllers")
    enabled = read_words(hierarchy / "cgroup.subtree_control", "cgroup.subtree_control")
    result["controllers"] = sorted(controllers)
    result["enabled_controllers"] = sorted(enabled)
    missing_available = sorted(set(REQUIRED_CONTROLLERS) - controllers)
    missing_enabled = sorted(set(REQUIRED_CONTROLLERS) - enabled)
    if missing_available:
        failures.append(f"controladores indisponíveis: {missing_available}")
    if missing_enabled:
        failures.append(f"controladores não delegados: {missing_enabled}")
    if not os.access(hierarchy, os.W_OK | os.X_OK):
        failures.append(f"hierarquia não é gravável/executável pelo usuário atual: {hierarchy}")
    if not os.access(hierarchy / "cgroup.procs", os.W_OK):
        failures.append(f"cgroup.procs da hierarquia não é gravável: {hierarchy / 'cgroup.procs'}")
    common_ancestor_procs = hierarchy.parent / "cgroup.procs"
    result["common_ancestor"] = str(hierarchy.parent)
    result["common_ancestor_procs_writable"] = os.access(common_ancestor_procs, os.W_OK)
    if not result["common_ancestor_procs_writable"]:
        failures.append(
            f"cgroup.procs do ancestral comum não é gravável: {common_ancestor_procs}"
        )

    writable_files = ("cpu.max", "memory.high", "io.weight", "cgroup.procs")
    readable_files = ("cpu.stat", "memory.current", "memory.peak", "io.stat")
    for name in GROUPS:
        group = hierarchy / name
        group_result: dict[str, Any] = {
            "path": str(group),
            "exists": group.is_dir(),
            "writable": [],
            "readable": [],
            "failures": [],
        }
        result["groups"][name] = group_result
        if not group.is_dir():
            message = f"grupo ausente: {group}"
            group_result["failures"].append(message)
            failures.append(message)
            continue
        for filename in writable_files:
            path = group / filename
            if path.is_file() and os.access(path, os.W_OK):
                group_result["writable"].append(filename)
            else:
                message = f"{name}/{filename} não é gravável"
                group_result["failures"].append(message)
                failures.append(message)
        for filename in readable_files:
            path = group / filename
            if path.is_file() and os.access(path, os.R_OK):
                group_result["readable"].append(filename)
            else:
                message = f"{name}/{filename} não é legível"
                group_result["failures"].append(message)
                failures.append(message)
    result["valid"] = not failures
    return result


def assert_cgroup_delegation(root: Path | str | None = None) -> dict[str, Any]:
    """Return a valid delegation status or fail with the one-time remedy."""
    status = cgroup_delegation_status(root)
    if not status["valid"]:
        details = "; ".join(status["failures"])
        raise InfraBudgetError(f"{DELEGATION_BOOTSTRAP_HINT}. Detalhes: {details}")
    return status


def _clamp(value: Any, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = high
    return max(low, min(high, parsed))


def build_physical_budget(
    r_ai: Any = 1.0, *, unrestricted: bool = False,
    compute_fraction: Any | None = None, io_fraction: Any | None = None,
) -> dict[str, Any]:
    """Translate the abstract AI share into concrete cgroup-v2 limits.

    The simulator keeps two CPUs because throttling simulated time can bias a
    paired experiment.  Control-plane groups scale between 25% and 100% while
    retaining enough headroom for the 100 ms safety telemetry channel.
    """
    fraction = 1.0 if unrestricted else _clamp(r_ai, 0.25, 1.0)
    compute_scale = 1.0 if unrestricted else _clamp(
        fraction if compute_fraction is None else compute_fraction, 0.25, 1.0
    )
    io_scale = 1.0 if unrestricted else _clamp(
        fraction if io_fraction is None else io_fraction, 0.25, 1.0
    )
    period = 100_000
    baselines = {
        "simulator": (200_000, 2 * 1024**3, 100),
        "ric_xapps": (100_000, 768 * 1024**2, 100),
        "rapp_armd": (200_000, 1024 * 1024**2, 100),
        "tasam": (100_000, 1024 * 1024**2, 100),
        "collectors": (100_000, 512 * 1024**2, 100),
    }
    groups: dict[str, dict[str, int]] = {}
    for name, (quota, memory, io_weight) in baselines.items():
        scale = 1.0 if name == "simulator" else compute_scale
        groups[name] = {
            "cpu_quota_us": max(25_000, int(round(quota * scale))),
            "cpu_period_us": period,
            "memory_high_bytes": max(256 * 1024**2, int(round(memory * scale))),
            "io_weight": max(25, min(1000, int(round(io_weight * (1.0 if name == "simulator" else io_scale))))),
        }
    return {
        "schema": "greenran.infra.budget.v1",
        "mode": "unrestricted" if unrestricted else "bounded",
        "compute_fraction": compute_scale,
        "io_fraction": io_scale,
        "groups": groups,
        "telemetry": {"safety_period_ms": 100, "analytic_period_ms": 1000},
        "persistence": {"sqlite_batch_rows": 100, "compress_logs": True},
    }


def validate_physical_budget(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != "greenran.infra.budget.v1":
        raise InfraBudgetError("invalid infrastructure budget schema")
    groups = payload.get("groups")
    if not isinstance(groups, dict) or set(groups) != set(GROUPS):
        raise InfraBudgetError(f"infrastructure budget must contain {GROUPS}")
    normalized = dict(payload)
    normalized_groups: dict[str, dict[str, int]] = {}
    for name in GROUPS:
        raw = groups[name]
        values = {
            "cpu_quota_us": int(raw["cpu_quota_us"]),
            "cpu_period_us": int(raw["cpu_period_us"]),
            "memory_high_bytes": int(raw["memory_high_bytes"]),
            "io_weight": int(raw["io_weight"]),
        }
        if values["cpu_quota_us"] <= 0 or values["cpu_period_us"] <= 0:
            raise InfraBudgetError(f"invalid CPU limit for {name}")
        if values["memory_high_bytes"] < 256 * 1024**2:
            raise InfraBudgetError(f"memory.high below safety floor for {name}")
        if not 1 <= values["io_weight"] <= 10000:
            raise InfraBudgetError(f"invalid io.weight for {name}")
        normalized_groups[name] = values
    telemetry = dict(payload.get("telemetry") or {})
    if int(telemetry.get("safety_period_ms", 0)) != 100:
        raise InfraBudgetError("per-UE safety telemetry must remain at 100 ms")
    analytic = int(telemetry.get("analytic_period_ms", 0))
    if not 1000 <= analytic <= 5000:
        raise InfraBudgetError("analytic telemetry must remain between 1 and 5 seconds")
    normalized["groups"] = normalized_groups
    normalized["telemetry"] = telemetry
    return normalized


class CgroupV2Controller:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = resolve_cgroup_root(root)

    @staticmethod
    def available(root: Path | str | None = None) -> bool:
        path = resolve_cgroup_root(root)
        return (path.parent / "cgroup.controllers").exists() or (path / "cgroup.controllers").exists()

    def ensure_root(self) -> None:
        """Create the delegated hierarchy and enable only required controllers."""
        parent = self.root.parent
        controllers_path = parent / "cgroup.controllers"
        if not controllers_path.exists():
            # Unit-test/fake cgroup trees contain writable pseudo-files in the
            # children and do not need kernel controller delegation.
            self.root.mkdir(parents=True, exist_ok=True)
            return
        try:
            available = set(controllers_path.read_text(encoding="ascii").split())
            required = [name for name in ("cpu", "memory", "io") if name in available]
            missing = {"cpu", "memory", "io"} - set(required)
            if missing:
                raise InfraBudgetError(f"cgroup v2 controllers unavailable: {sorted(missing)}")

            # A privileged setup may already have delegated ``self.root`` to
            # the unprivileged runner.  In that case the parent cgroup files
            # are intentionally not writable by the runner.  Recognize the
            # delegation by checking that the controllers are available in the
            # delegated subtree and enabled for its children, then continue
            # without trying to rewrite the parent subtree_control file.
            delegated_controllers = self.root / "cgroup.controllers"
            delegated_subtree = self.root / "cgroup.subtree_control"
            if self.root.is_dir() and delegated_controllers.exists() and delegated_subtree.exists():
                delegated = set(delegated_controllers.read_text(encoding="ascii").split())
                enabled = set(delegated_subtree.read_text(encoding="ascii").split())
                if set(required).issubset(delegated) and set(required).issubset(enabled):
                    return

            self._write(parent / "cgroup.subtree_control", " ".join(f"+{name}" for name in required))
            self.root.mkdir(parents=True, exist_ok=True)
            root_subtree = self.root / "cgroup.subtree_control"
            if root_subtree.exists():
                self._write(root_subtree, " ".join(f"+{name}" for name in required))
        except OSError as exc:
            raise InfraBudgetError(f"cannot initialize cgroup hierarchy {self.root}: {exc}") from exc

    @staticmethod
    def _write(path: Path, value: str) -> None:
        path.write_text(value, encoding="ascii")

    def apply_group(self, name: str, limits: dict[str, int], pid: int | None = None) -> Path:
        if name not in GROUPS:
            raise InfraBudgetError(f"unknown cgroup {name}")
        group = self.root / name
        try:
            group.mkdir(parents=True, exist_ok=True)
            self._write(group / "cpu.max", f"{limits['cpu_quota_us']} {limits['cpu_period_us']}")
            self._write(group / "memory.high", str(limits["memory_high_bytes"]))
            self._write(group / "io.weight", f"default {limits['io_weight']}")
            if pid is not None:
                if int(pid) <= 0:
                    raise InfraBudgetError("PID must be positive")
                self._write(group / "cgroup.procs", str(int(pid)))
        except (OSError, KeyError, ValueError) as exc:
            raise InfraBudgetError(f"cannot apply cgroup {name}: {exc}") from exc
        return group

    def attach(self, name: str, pid: int) -> None:
        if name not in GROUPS or int(pid) <= 0:
            raise InfraBudgetError("invalid cgroup attachment")
        try:
            self._write(self.root / name / "cgroup.procs", str(int(pid)))
        except OSError as exc:
            raise InfraBudgetError(f"cannot attach PID {pid} to {name}: {exc}") from exc

    def apply(self, budget: dict[str, Any], pids: dict[str, int] | None = None) -> None:
        normalized = validate_physical_budget(budget)
        self.ensure_root()
        for name, limits in normalized["groups"].items():
            self.apply_group(name, limits, (pids or {}).get(name))

    def snapshot(self, name: str) -> dict[str, int]:
        group = self.root / name
        cpu = _parse_key_values(group / "cpu.stat")
        io = _parse_io_stat(group / "io.stat")
        return {
            "timestamp_ns": time.monotonic_ns(),
            "cpu_usage_usec": cpu.get("usage_usec", 0),
            "memory_current_bytes": _read_int(group / "memory.current"),
            "memory_peak_bytes": _read_int(group / "memory.peak"),
            "io_read_bytes": io[0],
            "io_write_bytes": io[1],
        }


def probe_cgroup_attach(root: Path | str | None = None) -> dict[str, Any]:
    """Prove that a child process can enter the delegated subtree.

    File permissions alone are insufficient for cgroup-v2 migration: the
    process also needs write access at the common ancestor of source and
    destination.  The short-lived child exits immediately and its temporary
    cgroup is removed by the parent, so no simulation or project artifact is
    created.
    """
    hierarchy = resolve_cgroup_root(root)
    assert_cgroup_delegation(hierarchy)
    probe = hierarchy / f".attach-probe-{os.getpid()}-{time.monotonic_ns()}"
    result: dict[str, Any] = {"root": str(hierarchy), "probe": str(probe), "valid": False}
    try:
        probe.mkdir(mode=0o700)
        child = os.fork()
        if child == 0:
            try:
                (probe / "cgroup.procs").write_text(str(os.getpid()), encoding="ascii")
                relative = "/" + probe.relative_to("/sys/fs/cgroup").as_posix()
                attached = f"0::{relative}" in Path("/proc/self/cgroup").read_text(encoding="ascii")
                os._exit(0 if attached else 1)
            except OSError:
                os._exit(1)
        _, status = os.waitpid(child, 0)
        result["child_exit_status"] = status
        result["valid"] = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        if not result["valid"]:
            result["reason"] = "child_attach_failed"
    except OSError as exc:
        result["reason"] = f"attach_probe_error:{exc}"
    finally:
        try:
            probe.rmdir()
        except OSError as exc:
            result["valid"] = False
            result["cleanup_error"] = str(exc)
    return result


@dataclass
class ResourceAccumulator:
    last_timestamp_ns: int | None = None
    last_memory_bytes: int = 0
    memory_byte_seconds: float = 0.0
    memory_peak_bytes: int = 0

    def observe(self, snapshot: dict[str, int]) -> dict[str, float | int]:
        timestamp = int(snapshot["timestamp_ns"])
        memory = int(snapshot["memory_current_bytes"])
        if self.last_timestamp_ns is not None and timestamp >= self.last_timestamp_ns:
            seconds = (timestamp - self.last_timestamp_ns) / 1_000_000_000.0
            self.memory_byte_seconds += seconds * (self.last_memory_bytes + memory) / 2.0
        self.last_timestamp_ns = timestamp
        self.last_memory_bytes = memory
        self.memory_peak_bytes = max(self.memory_peak_bytes, memory,
                                     int(snapshot.get("memory_peak_bytes", 0)))
        return {
            **snapshot,
            "memory_byte_seconds": self.memory_byte_seconds,
            "observed_memory_peak_bytes": self.memory_peak_bytes,
        }


def _read_int(path: Path) -> int:
    try:
        value = path.read_text(encoding="ascii").strip()
        return 0 if value == "max" else int(value)
    except (OSError, ValueError):
        return 0


def _parse_key_values(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            key, value = line.split(None, 1)
            result[key] = int(value)
    except (OSError, ValueError):
        pass
    return result


def _parse_io_stat(path: Path) -> tuple[int, int]:
    read_bytes = write_bytes = 0
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            for field in line.split()[1:]:
                key, value = field.split("=", 1)
                if key == "rbytes":
                    read_bytes += int(value)
                elif key == "wbytes":
                    write_bytes += int(value)
    except (OSError, ValueError):
        pass
    return read_bytes, write_bytes
