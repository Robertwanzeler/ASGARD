"""Slot configuration, leases and preflight checks for concurrent V2X arms."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "greenran_v2x_parallel_slots.json"
LEASE_DIR = ROOT / "runs" / ".greenran_v2x_slot_leases"


@dataclass(frozen=True)
class V2XSlot:
    slot_id: str
    e2_term_port: int
    e2_xapp_port: int
    e2_local_port: int
    app_port_offset: int

    @property
    def cgroup_relative_root(self) -> str:
        return f"slots/{self.slot_id}"

    def as_environment(self, cgroup_base: Path | None = None) -> dict[str, str]:
        env = {
            "GREENRAN_V2X_EXECUTION_SLOT": self.slot_id,
            "GREENRAN_E2_TERM_PORT": str(self.e2_term_port),
            "GREENRAN_E2_XAPP_PORT": str(self.e2_xapp_port),
            "GREENRAN_E2_LOCAL_PORT": str(self.e2_local_port),
            "GREENRAN_PORT_OFFSET": str(self.app_port_offset),
            "GREENRAN_CGROUP_SLOT_RELATIVE_ROOT": self.cgroup_relative_root,
        }
        if cgroup_base is not None:
            env["GREENRAN_CGROUP_ROOT"] = str(cgroup_base / self.cgroup_relative_root)
        return env


def load_slot_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "greenran.v2x.parallel_slots.v1":
        raise ValueError("configuração de slots V2X incompatível")
    slots = payload.get("slots")
    if not isinstance(slots, list) or len(slots) != int(payload.get("max_parallel", 0)):
        raise ValueError("configuração de slots V2X incompleta")
    return payload


def resolve_slots(raw: str | Iterable[str] | None, path: Path = CONFIG_PATH) -> tuple[V2XSlot, ...]:
    payload = load_slot_config(path)
    requested = None if raw is None else ([item.strip() for item in raw.split(",")] if isinstance(raw, str) else list(raw))
    available = {
        item["slot_id"]: V2XSlot(
            slot_id=item["slot_id"],
            e2_term_port=int(item["e2_term_port"]),
            e2_xapp_port=int(item["e2_xapp_port"]),
            e2_local_port=int(item["e2_local_port"]),
            app_port_offset=int(item["app_port_offset"]),
        )
        for item in payload["slots"]
    }
    names = list(available) if requested is None else requested
    if not names or len(names) > int(payload["max_parallel"]):
        raise ValueError("quantidade de slots V2X fora do limite")
    if len(set(names)) != len(names) or any(name not in available for name in names):
        raise ValueError("slot V2X desconhecido ou duplicado")
    slots = tuple(available[name] for name in names)
    ports = [port for slot in slots for port in (slot.e2_term_port, slot.e2_xapp_port, slot.e2_local_port)]
    if len(ports) != len(set(ports)):
        raise ValueError("portas V2X duplicadas")
    return slots


def free_gib(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 ** 3)


def required_free_gib(active_slots: int, path: Path = ROOT / "runs") -> float:
    payload = load_slot_config()
    return float(payload["minimum_free_gib"]) + active_slots * float(payload["artifact_reserve_gib_per_slot"])


def assert_disk_capacity(path: Path, active_slots: int) -> None:
    required = required_free_gib(active_slots, path)
    available = free_gib(path)
    if available < required:
        raise RuntimeError(f"espaço insuficiente para {active_slots} slots: {available:.2f} GiB < {required:.2f} GiB")


def _sctp_listeners() -> set[int]:
    try:
        result = subprocess.run(["ss", "-H", "-n", "-lA", "sctp"], capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return set()
    ports: set[int] = set()
    for token in result.stdout.split():
        if ":" not in token:
            continue
        tail = token.rsplit(":", 1)[-1]
        if tail.isdigit():
            ports.add(int(tail))
    return ports


def assert_ports_free(slot: V2XSlot) -> None:
    listeners = _sctp_listeners()
    for port in (slot.e2_term_port, slot.e2_xapp_port, slot.e2_local_port):
        if port in listeners:
            raise RuntimeError(f"porta SCTP reservada pelo slot {slot.slot_id} já está ocupada: {port}")
    # App APIs are TCP, and checking them catches a stale process before it
    # can contaminate the slot's control plane.
    for port in (5100 + slot.app_port_offset, 5200 + slot.app_port_offset, 5300 + slot.app_port_offset):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"porta TCP de aplicação ocupada no {slot.slot_id}: {port}")


class SlotLease:
    def __init__(self, slot: V2XSlot, campaign_dir: Path, *, cgroup_base: Path | None = None) -> None:
        self.slot = slot
        self.campaign_dir = campaign_dir.resolve()
        self.cgroup_base = cgroup_base
        self.handle = None
        self.path = LEASE_DIR / f"{slot.slot_id}.lock"
        self.contract_path = self.campaign_dir / "slot_lease.json"
        self.contract: dict[str, Any] | None = None
        self.published = False

    def __enter__(self) -> "SlotLease":
        LEASE_DIR.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError(f"slot V2X ocupado: {self.slot.slot_id}") from exc
        try:
            self.handle.seek(0)
            previous = self.handle.read().strip()
            if previous:
                try:
                    previous_contract = json.loads(previous)
                except json.JSONDecodeError:
                    previous_contract = {}
                if previous_contract.get("status") == "acquired":
                    previous_pid = previous_contract.get("pid")
                    try:
                        if previous_pid and int(previous_pid) != os.getpid():
                            os.kill(int(previous_pid), 0)
                            raise RuntimeError(
                                f"slot V2X mantém PID ativo: {self.slot.slot_id}/{previous_pid}"
                            )
                    except ProcessLookupError:
                        pass
            assert_ports_free(self.slot)
            if self.campaign_dir.exists():
                existing = {item.name for item in self.campaign_dir.iterdir()}
                if existing:
                    raise RuntimeError(f"diretório do slot não é novo: {self.campaign_dir}")
            self.campaign_dir.mkdir(parents=True, exist_ok=True)
            self.contract = {
                "schema": "greenran.v2x.slot_lease.v1",
                "status": "acquired",
                "slot_id": self.slot.slot_id,
                "ports": {
                    "e2_term": self.slot.e2_term_port,
                    "e2_xapp": self.slot.e2_xapp_port,
                    "e2_local_base": self.slot.e2_local_port,
                },
                "cgroup": str((self.cgroup_base / self.slot.cgroup_relative_root) if self.cgroup_base else self.slot.cgroup_relative_root),
                "campaign_dir": str(self.campaign_dir),
                "pid": os.getpid(),
                "heartbeat": None,
                "acquired_at": time.time(),
            }
            return self
        except BaseException:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
            raise

    def publish_campaign_contract(self) -> None:
        """Publish lease evidence after the child has claimed an empty run-dir."""
        if self.contract is None:
            raise RuntimeError("lease V2X não adquirido")
        self.campaign_dir.mkdir(parents=True, exist_ok=True)
        self.contract_path.write_text(json.dumps(self.contract, indent=2) + "\n", encoding="utf-8")
        self.published = True

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.handle is not None:
            try:
                contract = json.loads(self.contract_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                contract = dict(self.contract or {
                    "schema": "greenran.v2x.slot_lease.v1", "slot_id": self.slot.slot_id,
                })
            contract.update({"status": "released", "released_at": time.time()})
            if self.published or self.contract_path.exists():
                self.contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.write(json.dumps({"status": "released", "slot_id": self.slot.slot_id, "released_at": time.time()}) + "\n")
            self.handle.flush()
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
