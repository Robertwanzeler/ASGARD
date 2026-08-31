"""Validated local policy source for the GreenRAN rApp arbitration layer."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = PROJECT_ROOT / "config" / "greenran_external_network_authority.json"
POLICY_SCHEMA = "greenran.rapp.policy.v1"


def _clamp(value: Any, lower: float, upper: float, default: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = default
    return min(max(value, lower), upper)


def _safe_read(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class RAppPolicySource:
    """Loads a local JSON policy and keeps the last valid version in memory."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("GREENRAN_RAPP_POLICY_FILE", DEFAULT_POLICY_PATH)).resolve()
        self._mtime_ns: int | None = None
        self._policy: Dict[str, Any] | None = None
        self._error = ""

    @property
    def policy(self) -> Dict[str, Any] | None:
        self.refresh()
        return deepcopy(self._policy) if self._policy is not None else None

    def refresh(self) -> Dict[str, Any]:
        try:
            stat = self.path.stat()
        except OSError as exc:
            self._error = f"policy file unavailable: {exc}"
            return self._status()

        if self._policy is not None and self._mtime_ns == stat.st_mtime_ns:
            return self._status()

        try:
            payload = _safe_read(self.path)
            normalized = self._validate(payload)
        except Exception as exc:
            self._error = str(exc)
            return self._status()

        self._policy = normalized
        self._mtime_ns = stat.st_mtime_ns
        self._error = ""
        return self._status()

    def _validate(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("policy must be a JSON object")
        if payload.get("schema") != POLICY_SCHEMA:
            raise ValueError(f"unsupported policy schema: {payload.get('schema', '')}")
        if payload.get("mode") != "shadow_only":
            raise ValueError("only shadow_only policy mode is allowed in this phase")
        if payload.get("data_source") != "real_only":
            raise ValueError("policy must declare data_source=real_only")

        policy = deepcopy(payload)
        authority = policy.setdefault("authority", {})
        authority["kind"] = str(authority.get("kind", "operator_editable_json"))
        authority["precedence"] = str(authority.get("precedence", "last_resort_only"))
        primary_decision_makers = authority.get("primary_decision_makers", ["armd", "ta_sam"])
        if not isinstance(primary_decision_makers, list) or set(primary_decision_makers) != {"armd", "ta_sam"}:
            raise ValueError("authority.primary_decision_makers must contain exactly armd and ta_sam")
        authority["primary_decision_makers"] = list(primary_decision_makers)
        if authority["precedence"] != "last_resort_only":
            raise ValueError("external policy precedence must be last_resort_only")

        arbitration = policy.setdefault("arbitration", {})
        arbitration["enabled"] = bool(arbitration.get("enabled", True))
        arbitration["tie_margin"] = _clamp(arbitration.get("tie_margin", 0.02), 0.0, 0.25, 0.02)
        arbitration["low_confidence_margin"] = _clamp(
            arbitration.get("low_confidence_margin", 0.70), 0.0, 1.0, 0.70
        )

        safety = policy.setdefault("safety", {})
        if safety.get("allow_control", False) is not False:
            raise ValueError("external policy cannot enable control in shadow phase")
        if safety.get("use_proxy", False) is not False:
            raise ValueError("external policy cannot enable proxy data")
        safety["allow_control"] = False
        safety["use_proxy"] = False
        safety["fallback"] = str(safety.get("fallback", "live_allocator"))

        priorities = (policy.setdefault("network_policy", {}).setdefault("priorities", {}))
        if not isinstance(priorities, dict) or not priorities:
            raise ValueError("network_policy.priorities must be a non-empty object")
        for name, value in priorities.items():
            try:
                priorities[name] = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid priority for {name}") from exc

        last_resort = arbitration.setdefault("last_resort", {})
        allowed_winners = {"armd", "ta_sam", "live_allocator"}
        for domain, default in (("energy", "armd"), ("resources", "ta_sam"), ("unresolved", "live_allocator")):
            last_resort[domain] = str(last_resort.get(domain, default))
            if last_resort[domain] not in allowed_winners:
                raise ValueError(f"invalid last_resort winner for {domain}: {last_resort[domain]}")
        policy["policy_id"] = str(policy.get("policy_id", "greenran_shared_arbitration_v1"))
        return policy

    def _status(self) -> Dict[str, Any]:
        return {
            "available": self._policy is not None,
            "policy_id": (self._policy or {}).get("policy_id", ""),
            "path": str(self.path),
            "schema": (self._policy or {}).get("schema", ""),
            "authority_kind": ((self._policy or {}).get("authority") or {}).get("kind", ""),
            "authority_precedence": ((self._policy or {}).get("authority") or {}).get("precedence", ""),
            "error": self._error,
            "policy": deepcopy(self._policy) if self._policy is not None else {},
        }
