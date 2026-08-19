#!/usr/bin/env python3
"""
Helpers compartilhados para consumidores de políticas A1.

O lado rApp já gera os arquivos de política. Este módulo padroniza a leitura
do bloco ARMD para observabilidade e reação explícita nos consumidores Python.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from greenran_paths import RAPP_POLICIES_DIR


def _safe_read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def load_current_policies(policy_dir: Path | None = None) -> Dict[str, Dict[str, Any]]:
    base_dir = Path(policy_dir or RAPP_POLICIES_DIR)
    energy_policy = _safe_read_json(base_dir / "energy_policy.json")
    slice_policy = _safe_read_json(base_dir / "slice_policy.json")
    return {
        "energy_policy": energy_policy,
        "slice_policy": slice_policy,
        "armd": summarize_armd_policy(energy_policy, slice_policy),
    }


def summarize_armd_policy(energy_policy: Dict[str, Any], slice_policy: Dict[str, Any]) -> Dict[str, Any]:
    armd_energy = energy_policy.get("armd", {}) if isinstance(energy_policy, dict) else {}
    armd_slice = slice_policy.get("armd", {}) if isinstance(slice_policy, dict) else {}
    active = armd_energy if armd_energy else armd_slice

    if not isinstance(active, dict) or not active:
        return {
            "present": False,
            "scenario": "",
            "domain": "",
            "source": "",
            "confidence": 0.0,
            "override_applied": False,
            "mode": "",
            "attention_level": "none",
            "reason": "",
            "policy_scope": [],
        }

    expected_energy = active.get("expected_energy_saver", "") or energy_policy.get("decision", {}).get("energy_saver", "")
    if active.get("override_applied"):
        attention_level = "override"
    elif expected_energy == "BLOCKED":
        attention_level = "critical"
    elif expected_energy == "CONDITIONAL":
        attention_level = "guard"
    else:
        attention_level = "informational"

    scope = []
    if armd_energy:
        scope.append("energy")
    if armd_slice:
        scope.append("slice")

    return {
        "present": True,
        "scenario": active.get("scenario", ""),
        "domain": active.get("domain", ""),
        "source": active.get("source", ""),
        "confidence": float(active.get("confidence", 0.0) or 0.0),
        "override_applied": bool(active.get("override_applied", False)),
        "mode": active.get("mode", ""),
        "reason": active.get("reason", ""),
        "expected_energy_saver": expected_energy,
        "expected_action": active.get("expected_action", ""),
        "attention_level": attention_level,
        "policy_scope": scope,
    }
