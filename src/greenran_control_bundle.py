#!/usr/bin/env python3
"""Versioned, fail-safe control contract for the TA-SAM E2 actuator."""

from __future__ import annotations

import json
import math
import os
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

try:
    from .greenran_infra_budget import build_physical_budget, validate_physical_budget
except ImportError:
    from greenran_infra_budget import build_physical_budget, validate_physical_budget

try:
    from .tasam_economic_v3 import (
        CONTRACT as ECONOMIC_ACTION_V3_CONTRACT,
        EconomicActionV3Error,
        normalize_power_by_cell,
        quantize_power_percent_v3,
    )
except ImportError:
    from tasam_economic_v3 import (
        CONTRACT as ECONOMIC_ACTION_V3_CONTRACT,
        EconomicActionV3Error,
        normalize_power_by_cell,
        quantize_power_percent_v3,
    )

try:
    from .greenran_paths import (
        TASAM_CONTROL_ACK_PATH,
        TASAM_CONTROL_AUDIT_PATH,
        TASAM_CONTROL_BUNDLE_PATH,
        TASAM_CONTROL_SOCKET_PATH,
    )
except ImportError:
    from greenran_paths import (
        TASAM_CONTROL_ACK_PATH,
        TASAM_CONTROL_AUDIT_PATH,
        TASAM_CONTROL_BUNDLE_PATH,
        TASAM_CONTROL_SOCKET_PATH,
    )


SCHEMA = "greenran.control.bundle.v2"
V3_SCHEMA = "greenran.control.bundle.v3"
V4_SCHEMA = "greenran.control.bundle.v4"
ACK_SCHEMA = "greenran.control.ack.v2"
POWER_LEVELS = tuple(range(25, 101, 5))
UE_COUNT = 20


class ControlBundleError(ValueError):
    """Raised when a control bundle cannot be applied safely."""


def quantize_power_percent(value: Any) -> int:
    """Clamp power to the safe 25..100 range and quantize to 5% steps."""
    try:
        requested = float(value)
    except (TypeError, ValueError):
        requested = 100.0
    requested = max(25.0, min(100.0, requested))
    return int(max(25, min(100, 5 * round(requested / 5.0))))


def power_percent_to_dbm(power_percent: Any, max_dbm: float = 30.0) -> float:
    """Convert a percentage of linear RF power to dBm."""
    pct = quantize_power_percent(power_percent)
    return float(max_dbm) + 10.0 * math.log10(pct / 100.0)


def service_for_imsi(imsi: int) -> str:
    if 1 <= imsi <= 3:
        return "camera"
    if 4 <= imsi <= 15:
        return "sensor"
    if 16 <= imsi <= 20:
        return "vehicle"
    raise ControlBundleError(f"IMSI outside canonical 1..20 topology: {imsi}")


def _basis_points(value: Any, field: str) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise ControlBundleError(f"{field} must be numeric") from exc
    if not 0 <= parsed <= 10_000:
        raise ControlBundleError(f"{field} must be between 0 and 10000")
    return parsed


def _validate_sleep_transition(
    transition: Any,
    *,
    normalized_power: dict[int, int],
) -> dict[str, Any]:
    """Validate the explicit, fail-closed sleep transaction used by v4."""
    if not isinstance(transition, dict):
        raise ControlBundleError("sleep_transition is required for a v4 sleep operation")
    phase = str(transition.get("phase") or "").strip().lower()
    if phase not in {"drain", "commit", "wake"}:
        raise ControlBundleError("sleep_transition phase must be drain, commit or wake")
    sleep_transaction_id = str(transition.get("sleep_transaction_id") or "").strip()
    if not sleep_transaction_id:
        raise ControlBundleError("sleep_transition requires sleep_transaction_id")
    try:
        source = int(transition.get("source_cell_id"))
    except (TypeError, ValueError) as exc:
        raise ControlBundleError("sleep_transition requires a canonical source_cell_id") from exc
    if source not in normalized_power:
        raise ControlBundleError("sleep_transition source is not a managed DU")
    plan = transition.get("handover_plan") or []
    if phase == "drain":
        if normalized_power[source] == 0:
            raise ControlBundleError("drain keeps the source DU active until native confirmation")
        if not isinstance(plan, list) or not plan:
            raise ControlBundleError("drain requires a non-empty handover_plan")
        seen: set[int] = set()
        for item in plan:
            if not isinstance(item, dict):
                raise ControlBundleError("handover_plan entries must be objects")
            try:
                imsi = int(item.get("imsi"))
                target = int(item.get("target_cell_id"))
            except (TypeError, ValueError) as exc:
                raise ControlBundleError("handover_plan requires IMSI and target cell") from exc
            if imsi in seen or not 1 <= imsi <= UE_COUNT or target not in normalized_power or target == source:
                raise ControlBundleError("handover_plan is malformed")
            seen.add(imsi)
        if any(normalized_power[cell] != 100 for cell in normalized_power if cell != source):
            raise ControlBundleError("drain temporarily keeps destination DUs at 100%")
    elif phase == "commit":
        if normalized_power[source] != 0:
            raise ControlBundleError("sleep commit must set the confirmed source DU to zero")
        required = ("handover_confirmed", "pdcp_window_valid", "association_valid")
        if any(not transition.get(key) for key in required):
            raise ControlBundleError("sleep commit requires native handover, association and PDCP confirmation")
        if float(transition.get("pdcp_window_s", 0.0) or 0.0) < 10.0:
            raise ControlBundleError("sleep commit requires a 10 second PDCP confirmation window")
    elif normalized_power[source] != 100:
        raise ControlBundleError("sleep wake must restore the source DU to 100%")
    normalized = dict(transition)
    normalized.update({
        "phase": phase,
        "sleep_transaction_id": sleep_transaction_id,
        "source_cell_id": source,
        "handover_plan": plan,
    })
    return normalized


def validate_bundle(bundle: dict[str, Any], *, require_all_ues: bool = True) -> dict[str, Any]:
    """Validate and normalize an actuator bundle without mutating the input."""
    if not isinstance(bundle, dict) or bundle.get("schema") not in {SCHEMA, V3_SCHEMA, V4_SCHEMA}:
        raise ControlBundleError(f"expected schema {SCHEMA}, {V3_SCHEMA} or {V4_SCHEMA}")
    v3 = bundle.get("schema") in {V3_SCHEMA, V4_SCHEMA}
    v4 = bundle.get("schema") == V4_SCHEMA
    if v3 and bundle.get("economic_action_contract") != ECONOMIC_ACTION_V3_CONTRACT:
        raise ControlBundleError(
            f"v3 bundle requires economic_action_contract={ECONOMIC_ACTION_V3_CONTRACT}"
        )
    policy_id = str(bundle.get("policy_id", "")).strip()
    if not policy_id:
        raise ControlBundleError("policy_id is required")
    try:
        sequence = int(bundle["sequence"])
        issued_at_ns = int(bundle["issued_at_ns"])
        ttl_ms = int(bundle["ttl_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ControlBundleError("sequence, issued_at_ns and ttl_ms are required integers") from exc
    if sequence <= 0 or ttl_ms < 100 or ttl_ms > 60_000:
        raise ControlBundleError("invalid sequence or TTL")

    cells = bundle.get("cells")
    if not isinstance(cells, list) or not cells:
        raise ControlBundleError("at least one cell is required")
    normalized_cells: list[dict[str, Any]] = []
    seen_imsis: set[int] = set()
    # Dispositivos MC (useMcUeDevices=true) aparecem legitimamente em dois
    # DUs; duplicata com a flag de overlap é contrato, não erro (restringir
    # a v3 rejeitava todo bundle econômico da campanha v6 — r24).
    allow_native_mc_overlap = bool(
        bundle.get("association_mode") == "native_rrc_mc_overlap"
    )
    for raw_cell in cells:
        if not isinstance(raw_cell, dict):
            raise ControlBundleError("cell entries must be objects")
        cell_id = int(raw_cell.get("cell_id", 0))
        if cell_id <= 0:
            raise ControlBundleError("cell_id must be positive")
        policies = raw_cell.get("ue_policies") or []
        if not isinstance(policies, list):
            raise ControlBundleError("ue_policies must be a list")
        normalized_ues = []
        for raw_ue in policies:
            imsi = int(raw_ue.get("imsi", 0))
            service = service_for_imsi(imsi)
            if imsi in seen_imsis and not allow_native_mc_overlap:
                raise ControlBundleError(f"duplicate IMSI {imsi}")
            seen_imsis.add(imsi)
            normalized_ues.append({
                "imsi": imsi,
                "service": service,
                "min_dl_share_bp": _basis_points(raw_ue.get("min_dl_share_bp", 0), "min_dl_share_bp"),
                "min_ul_share_bp": _basis_points(raw_ue.get("min_ul_share_bp", 0), "min_ul_share_bp"),
                "surplus_weight_bp": _basis_points(raw_ue.get("surplus_weight_bp", 10_000), "surplus_weight_bp"),
            })
        committed_floor = sum(
            ue["min_dl_share_bp"] + ue["min_ul_share_bp"] for ue in normalized_ues
        )
        if committed_floor > 10_000:
            raise ControlBundleError(
                f"cell {cell_id} scheduler floors are infeasible: {committed_floor} bp"
            )
        try:
            tx_power = (
                quantize_power_percent_v3(raw_cell.get("tx_power_percent", 100))
                if v3 else quantize_power_percent(raw_cell.get("tx_power_percent", 100))
            )
        except EconomicActionV3Error as exc:
            raise ControlBundleError(str(exc)) from exc
        if v4:
            discretionary = _basis_points(
                raw_cell.get("max_discretionary_dl_symbols_bp"),
                "max_discretionary_dl_symbols_bp",
            )
        else:
            discretionary = None
        normalized_cells.append({
            "cell_id": cell_id,
            "tx_power_percent": tx_power,
            "ue_policies": normalized_ues,
            **({"max_discretionary_dl_symbols_bp": discretionary} if v4 else {}),
        })
    if require_all_ues and seen_imsis != set(range(1, UE_COUNT + 1)):
        missing = sorted(set(range(1, UE_COUNT + 1)) - seen_imsis)
        raise ControlBundleError(f"bundle must cover all 20 UEs; missing={missing}")

    if v3:
        try:
            normalized_power = normalize_power_by_cell(
                bundle.get("power_percent_by_cell")
                or {cell.get("cell_id"): cell.get("tx_power_percent") for cell in cells}
            )
        except EconomicActionV3Error as exc:
            raise ControlBundleError(str(exc)) from exc
        cell_power = {int(cell["cell_id"]): int(cell["tx_power_percent"]) for cell in normalized_cells}
        if cell_power != normalized_power:
            raise ControlBundleError("power_percent_by_cell diverges from per-cell bundle power")
        sleeping = [cell for cell, power in normalized_power.items() if power == 0]
        if v4 and bundle.get("sleep_transition"):
            normalized_sleep_transition = _validate_sleep_transition(
                bundle.get("sleep_transition"), normalized_power=normalized_power,
            )
        else:
            normalized_sleep_transition = None
        if sleeping and v4 and normalized_sleep_transition is None:
            raise ControlBundleError("v4 sleeping DU requires sleep_transition commit evidence")
        if sleeping and not v4:
            sleep = bundle.get("du_sleep") or {}
            if not isinstance(sleep, dict):
                raise ControlBundleError("du_sleep evidence is required for a sleeping DU")
            required = ("source_cell_id", "handover_confirmed", "pdcp_window_valid", "association_valid")
            if any(not sleep.get(key) for key in required):
                raise ControlBundleError(
                    "sleep requires confirmed handover, association and a valid PDCP window"
                )
            if int(sleep.get("source_cell_id")) not in sleeping:
                raise ControlBundleError("sleep source does not match the zero-power DU")
            if float(sleep.get("pdcp_window_s", 0.0) or 0.0) < 10.0:
                raise ControlBundleError("DU sleep requires a 10 second PDCP confirmation window")
        if v4 and normalized_sleep_transition is not None and (
            normalized_sleep_transition["phase"] == "commit"
        ):
            source = int(normalized_sleep_transition["source_cell_id"])
            source_policies = next(
                (cell["ue_policies"] for cell in normalized_cells if cell["cell_id"] == source),
                None,
            )
            if source_policies is None or source_policies:
                raise ControlBundleError(
                    "sleep commit requires an empty source-DU scheduler snapshot"
                )

    normalized = dict(bundle)
    normalized_infra = validate_physical_budget(bundle.get("infra"))
    normalized.update({
        "schema": V4_SCHEMA if v4 else V3_SCHEMA if v3 else SCHEMA,
        "policy_id": policy_id,
        "sequence": sequence,
        "issued_at_ns": issued_at_ns,
        "ttl_ms": ttl_ms,
        "mode": str(bundle.get("mode", "combined")),
        "cells": normalized_cells,
        "infra": normalized_infra,
    })
    if v3:
        normalized["power_percent_by_cell"] = {
            str(cell): int(power) for cell, power in normalized_power.items()
        }
        normalized["economic_action_contract"] = ECONOMIC_ACTION_V3_CONTRACT
    if v4 and normalized_sleep_transition is not None:
        normalized["sleep_transition"] = normalized_sleep_transition
    return normalized


def failsafe_bundle(
    *, sequence: int, cell_ids: Iterable[int], reason: str, sim_time_s: float | None = None
) -> dict[str, Any]:
    """Build the only automatic fallback: full power and stock scheduling."""
    now_ns = time.time_ns()
    bundle = {
        "schema": SCHEMA,
        "policy_id": f"failsafe-{uuid.uuid4().hex}",
        "sequence": int(sequence),
        "issued_at_ns": now_ns,
        "ttl_ms": 5_000,
        "mode": "failsafe",
        "reason": str(reason),
        "cells": [
            {"cell_id": int(cell_id), "tx_power_percent": 100, "ue_policies": []}
            for cell_id in cell_ids
        ],
        "infra": build_physical_budget(1.0, unrestricted=True),
    }
    if sim_time_s is not None:
        bundle["sim_time_s"] = float(sim_time_s)
    return bundle


class ControlBundleClient:
    """Send one atomic policy to the TA-SAM xApp; never silently fake an ACK."""

    def __init__(
        self,
        socket_path: Path | str = TASAM_CONTROL_SOCKET_PATH,
        shadow_path: Path | str = TASAM_CONTROL_BUNDLE_PATH,
        ack_path: Path | str = TASAM_CONTROL_ACK_PATH,
        audit_path: Path | str = TASAM_CONTROL_AUDIT_PATH,
        timeout_seconds: float | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.shadow_path = Path(shadow_path)
        self.ack_path = Path(ack_path)
        self.audit_path = Path(audit_path)
        # The E2 control path has its own bounded RIC acknowledgement timer
        # (currently 3 s).  A 1 s socket timeout races that timer and turns a
        # valid control into an artificial failsafe.  Keep it configurable for
        # tests, but leave enough time for the real E2/RC round trip.
        if timeout_seconds is None:
            timeout_seconds = os.environ.get("GREENRAN_TASAM_CONTROL_TIMEOUT_S", "5.0")
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)

    def _audit(self, payload: dict[str, Any]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as audit:
            audit.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            audit.flush()
            os.fsync(audit.fileno())

    def send(self, bundle: dict[str, Any], *, integration: bool = True) -> dict[str, Any]:
        normalized = validate_bundle(bundle, require_all_ues=bundle.get("mode") != "failsafe")
        self._atomic_json(self.shadow_path, normalized)
        if not integration:
            return {
                "schema": ACK_SCHEMA,
                "ack": False,
                "applied": False,
                "shadow_only": True,
                "policy_id": normalized["policy_id"],
                "sequence": normalized["sequence"],
            }
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_seconds)
                sock.connect(str(self.socket_path))
                encoded = json.dumps(normalized, separators=(",", ":")).encode("utf-8")
                sock.sendall(encoded)
                raw = sock.recv(16_384)
            ack = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._audit({
                "schema": ACK_SCHEMA,
                "policy_id": normalized["policy_id"],
                "sequence": normalized["sequence"],
                "mode": normalized["mode"],
                "fallback": normalized["mode"] == "failsafe",
                "ack": False,
                "applied": False,
                "observed_confirmations": 0,
                "sim_time_s": normalized.get("sim_time_s"),
                "error": str(exc),
            })
            raise ControlBundleError(f"TA-SAM actuator unavailable: {exc}") from exc
        ack["mode"] = normalized["mode"]
        ack["fallback"] = normalized["mode"] == "failsafe" or bool(ack.get("fallback"))
        ack["sim_time_s"] = normalized.get("sim_time_s")
        ack["management_tx_bytes"] = len(encoded) + len(raw)
        ack["ttl_ms"] = normalized["ttl_ms"]
        ack["requested_cells"] = [
            {
                "cell_id": cell["cell_id"],
                "tx_power_percent": cell["tx_power_percent"],
                "expected_ues": len(cell["ue_policies"]),
            }
            for cell in normalized["cells"]
        ]
        native_version = os.environ.get("GREENRAN_NATIVE_EVIDENCE_VERSION", "v3").strip()
        cell_ack_complete = True
        if native_version in {"v5", "v6"}:
            cell_results = ack.get("cell_results")
            confirmed_cells = {
                int(item.get("cell_id")) for item in cell_results or []
                if isinstance(item, dict)
                and item.get("scheduler_ack") is True
                and item.get("power_ack") is True
                and item.get("cell_id") is not None
            }
            # Um failsafe é o estado seguro por definição: o ACK global
            # (applied=True) já confirma a aplicação.  Exigir acks per-célula
            # de um failsafe rejeita applied=True e flipa a decisão para
            # BLOCKED silenciosamente (r23: 47/47), mascarando a causa.
            requires_cell_acks = normalized["mode"] != "failsafe"
            cell_ack_complete = (
                confirmed_cells >= {2, 3, 4}
                if requires_cell_acks
                else bool(ack.get("applied"))
            )
            ack["cell_ack_complete"] = cell_ack_complete
        if (
            ack.get("schema") != ACK_SCHEMA
            or ack.get("policy_id") != normalized["policy_id"]
            or int(ack.get("sequence", -1)) != normalized["sequence"]
            or not ack.get("ack")
            or not ack.get("applied")
            or int(ack.get("observed_confirmations", 0) or 0) <= 0
            or not cell_ack_complete
        ):
            self._audit(ack)
            raise ControlBundleError(f"invalid or incomplete E2 ACK: {ack}")
        self._atomic_json(self.ack_path, ack)
        self._audit(ack)
        return ack
