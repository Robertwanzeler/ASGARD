#!/usr/bin/env python3
"""Validate and acknowledge the local per-UE A1 policy contract.

This is deliberately a small local consumer, not a simulated scheduler.  It
checks that the policy emitted by the rApp preserves every UE floor and that
the individual reservations add up to the aggregate RAN/AI decision.  A
physical Near-RT RIC/scheduler can replace this process later while keeping
the same ACK contract.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any


EPSILON = 1e-8


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _finite_nonnegative(value: Any) -> bool:
    try:
        return math.isfinite(float(value)) and float(value) >= -EPSILON
    except (TypeError, ValueError):
        return False


def validate_per_ue_policy(policy: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    block = policy.get("per_ue_resource_policy") if isinstance(policy, dict) else None
    if not isinstance(block, dict):
        return False, "per_ue_resource_policy ausente", {}

    allocations = block.get("allocations")
    if not isinstance(allocations, list) or not allocations:
        return False, "lista de alocações por UE ausente ou vazia", {}

    seen: set[str] = set()
    sums = {"ran": 0.0, "ai": 0.0}
    violations = 0
    for index, item in enumerate(allocations):
        if not isinstance(item, dict):
            return False, f"alocação {index} não é objeto", {}
        ue_id = str(item.get("ue_id", "") or "").strip()
        if not ue_id:
            return False, f"alocação {index} sem ue_id", {}
        if ue_id in seen:
            return False, f"ue_id duplicado: {ue_id}", {}
        seen.add(ue_id)

        domain = str(item.get("domain", "") or "").strip().lower()
        if domain not in sums:
            return False, f"domínio inválido para {ue_id}: {domain!r}", {}
        floor = item.get("floor_share")
        allocated = item.get("allocated_share")
        if not _finite_nonnegative(floor) or not _finite_nonnegative(allocated):
            return False, f"reserva não finita/não negativa para {ue_id}", {}
        if float(allocated) + EPSILON < float(floor) or not bool(item.get("floor_met", False)):
            violations += 1
        sums[domain] += float(allocated)

    declared_violations = int(block.get("floor_violation_count", violations) or 0)
    # The producer's count is an audit field; a mismatch means the payload
    # changed between calculation and consumption.
    if declared_violations != violations:
        return False, (
            "floor_violation_count inconsistente "
            f"(declarado={declared_violations}, calculado={violations})"
        ), {"allocation_count": len(allocations), "floor_violation_count": violations, "sums": sums}
    if violations:
        return False, f"{violations} violação(ões) de piso por UE", {
            "allocation_count": len(allocations),
            "floor_violation_count": violations,
            "sums": sums,
        }

    for domain in ("ran", "ai"):
        if domain in block and abs(float(block.get(domain) or 0.0) - sums[domain]) > EPSILON:
            return False, (
                f"soma {domain} divergente "
                f"(declarada={float(block.get(domain) or 0.0):.12f}, "
                f"calculada={sums[domain]:.12f})"
            ), {"allocation_count": len(allocations), "floor_violation_count": 0, "sums": sums}

    return True, "alocação por UE validada; soma e pisos preservados", {
        "allocation_count": len(allocations),
        "floor_violation_count": 0,
        "sums": sums,
    }


def _update_data_lake(db_path: Path, policy_id: str, status: str, timestamp: float, reason: str) -> int:
    if not policy_id or not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        rows = conn.execute(
            "SELECT timestamp, snapshot_json FROM resource_allocation_history "
            "WHERE per_ue_policy_id = ?",
            (policy_id,),
        ).fetchall()
        for row_timestamp, snapshot_json in rows:
            snapshot = _read_json_from_text(snapshot_json)
            if not isinstance(snapshot, dict):
                snapshot = {}
            snapshot["per_ue_application_status"] = status
            snapshot["per_ue_ack_timestamp"] = timestamp
            snapshot["per_ue_ack_reason"] = reason
            conn.execute(
                "UPDATE resource_allocation_history SET "
                "per_ue_application_status = ?, per_ue_ack_timestamp = ?, "
                "per_ue_ack_reason = ?, snapshot_json = ? WHERE timestamp = ?",
                (status, timestamp, reason, json.dumps(snapshot, ensure_ascii=False), row_timestamp),
            )
        conn.commit()
        conn.close()
        return len(rows)
    except (OSError, sqlite3.Error) as exc:
        print(f"[PER_UE_CONSUMER] Data Lake ainda não disponível: {exc}", flush=True)
        return 0


def _read_json_from_text(value: Any) -> Any:
    try:
        return json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def consume_once(state_dir: Path) -> dict[str, Any] | None:
    policy_dir = state_dir / "rapp_policies"
    policy_path = policy_dir / "slice_policy.json"
    ack_path = policy_dir / "slice_policy_ack.json"
    policy = _read_json(policy_path)
    if not isinstance(policy, dict) or not isinstance(policy.get("per_ue_resource_policy"), dict):
        return None

    block = policy["per_ue_resource_policy"]
    policy_id = str(block.get("policy_id") or policy.get("policy_id") or "")
    valid, reason, details = validate_per_ue_policy(policy)
    status = "acknowledged" if valid else "rejected"
    previous_ack = _read_json(ack_path, {})
    same_ack = (
        isinstance(previous_ack, dict)
        and previous_ack.get("policy_id") == policy_id
        and previous_ack.get("application_status") == status
    )
    timestamp = float(previous_ack.get("timestamp") or time.time()) if same_ack else time.time()
    ack = {
        "schema": "greenran.per_ue_policy_ack.v1",
        "acknowledged": valid,
        "timestamp": timestamp,
        "policy_id": policy_id,
        "application_status": status,
        "allocation_count": details.get("allocation_count", 0),
        "floor_violation_count": details.get("floor_violation_count", 0),
        "reason": reason,
    }
    if not same_ack:
        _write_json_atomic(ack_path, ack)

    if block.get("application_status") != status or block.get("ack_timestamp") != timestamp or block.get("ack_reason") != reason:
        block = dict(block)
        block["application_status"] = status
        block["ack_timestamp"] = timestamp
        block["ack_reason"] = reason
        policy["per_ue_resource_policy"] = block
        _write_json_atomic(policy_path, policy)

    rows_updated = _update_data_lake(
        state_dir / "rapp_data_lake.db", policy_id, status, timestamp, reason
    )
    return {"policy_id": policy_id, "status": status, "reason": reason, "rows_updated": rows_updated}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--poll-s", type=float, default=0.5)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    state_dir = args.state_dir.expanduser().resolve()
    last_log_key = None
    while True:
        result = consume_once(state_dir)
        if result:
            log_key = (result["policy_id"], result["status"], result["rows_updated"])
            if log_key != last_log_key:
                print(
                    "[PER_UE_CONSUMER] "
                    f"policy={result['policy_id']} status={result['status']} "
                    f"rows={result['rows_updated']} reason={result['reason']}",
                    flush=True,
                )
                last_log_key = log_key
        if args.once:
            return 0
        time.sleep(max(0.05, float(args.poll_s)))


if __name__ == "__main__":
    raise SystemExit(main())
