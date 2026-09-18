#!/usr/bin/env python3
"""
ARMD-GreenRAN runtime advisor.

Integra o pacote híbrido final do GraphSAGE ao rApp como uma camada
operacional de classificação de cenário. O objetivo aqui não é executar o
treino online, mas congelar o conhecimento validado offline e reutilizá-lo
como:

1. explicação estruturada do cenário atual;
2. camada de proteção adicional que só pode escalar severidade;
3. trilha persistida no Data Lake e nos logs do rApp.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from greenran_paths import RUNS_DIR

DEFAULT_HYBRID_SUMMARY = RUNS_DIR / "graphsage_article00_hybrid_final" / "hybrid_final_summary.json"

SEVERITY_RANK = {
    "UNKNOWN": 0,
    "ALLOWED": 1,
    "CONDITIONAL": 2,
    "BLOCKED": 3,
}


@dataclass(frozen=True)
class ScenarioReference:
    scenario: str
    source: str
    threshold: float
    subset_size: int
    target_hits: int
    completed_seeds: int
    mean_f1_at_target_epoch: float


class ARMDRuntimeAdvisor:
    """Classifica o estado vivo do runtime nos cenários validados do ARMD."""

    def __init__(
        self,
        summary_path: Optional[Path] = None,
        mode: Optional[str] = None,
        min_confidence: Optional[float] = None,
    ) -> None:
        self.summary_path = Path(
            summary_path
            or os.environ.get("GREENRAN_ARMD_SUMMARY", DEFAULT_HYBRID_SUMMARY)
        ).resolve()
        self.mode = (mode or os.environ.get("GREENRAN_ARMD_MODE", "assist")).strip().lower()
        self.enabled = self.mode not in {"off", "disabled", "0", "false"}
        self.min_confidence = float(
            min_confidence
            if min_confidence is not None
            else os.environ.get("GREENRAN_ARMD_MIN_CONFIDENCE", "0.85")
        )
        self.scenarios: Dict[str, ScenarioReference] = {}
        self.load_error = ""
        self.loaded = False
        self.threshold = 0.5
        self.subset_size = 450
        self._vehicle_history = deque(maxlen=12)
        self._app2_history = deque(maxlen=12)
        self._load_summary()

    def _load_summary(self) -> None:
        if not self.summary_path.exists():
            self.load_error = f"hybrid summary not found: {self.summary_path}"
            return

        try:
            payload = json.loads(self.summary_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            self.load_error = str(exc)
            return

        self.threshold = float(payload.get("threshold", 0.5) or 0.5)
        self.subset_size = int(payload.get("subset_size", 450) or 450)
        selected = payload.get("selected", []) or []

        scenarios: Dict[str, ScenarioReference] = {}
        for item in selected:
            scenario = str(item.get("scenario", "")).strip()
            if not scenario:
                continue
            scenarios[scenario] = ScenarioReference(
                scenario=scenario,
                source=str(item.get("selected_source", "")),
                threshold=float(item.get("threshold", self.threshold) or self.threshold),
                subset_size=int(item.get("subset_size", self.subset_size) or self.subset_size),
                target_hits=int(item.get("target_hits", 0) or 0),
                completed_seeds=int(item.get("completed_seeds", 0) or 0),
                mean_f1_at_target_epoch=float(item.get("mean_f1_at_target_epoch", 0.0) or 0.0),
            )

        self.scenarios = scenarios
        self.loaded = bool(self.scenarios)
        if not self.loaded and not self.load_error:
            self.load_error = "no scenarios loaded from hybrid summary"

    def advise(
        self,
        *,
        decision: Dict,
        camera_metrics: Optional[Dict] = None,
        vehicle_metrics: Optional[Dict] = None,
        app2_metrics: Optional[Dict] = None,
        network_health: Optional[Dict] = None,
    ) -> Dict:
        advice = self._base_advice()
        if not self.enabled:
            advice["reason"] = "ARMD disabled"
            return advice
        if not self.loaded:
            advice["reason"] = self.load_error or "ARMD summary unavailable"
            return advice

        camera_metrics = camera_metrics or {}
        vehicle_metrics = vehicle_metrics or {}
        app2_metrics = app2_metrics or {}
        network_health = network_health or {}

        cvar_ms = float(network_health.get("cvar_us", 0) or 0) / 1000.0
        p95_ms = float(network_health.get("p95_us", 0) or 0) / 1000.0
        global_healthy = cvar_ms <= 40.0 and p95_ms <= 60.0
        vehicle_state = self._derive_vehicle_state(vehicle_metrics)
        app2_state = self._derive_app2_state(app2_metrics)
        self._vehicle_history.append(vehicle_state)
        self._app2_history.append(app2_state)

        candidates: List[Dict] = []
        candidates.extend(self._camera_candidates(camera_metrics))
        candidates.extend(self._app2_candidates(app2_metrics, app2_state, global_healthy))
        candidates.extend(self._vehicle_candidates(vehicle_metrics, vehicle_state, global_healthy))

        if not candidates:
            # A healthy/no-match cycle is still an ARMD proposal. It is a
            # neutral recommendation, not an absence of the assistant.
            advice.update(
                {
                    "available": True,
                    "proposal_present": True,
                    "proposal_valid": True,
                    "proposal_kind": "neutral_noop",
                    "scenario": "greenran_global_noop",
                    "domain": "global",
                    "expected_energy_saver": str(
                        decision.get("energy_saver", "CONDITIONAL") or "CONDITIONAL"
                    ).upper(),
                    "expected_action": str(
                        decision.get("action", "FULL_POWER_GUARD") or "FULL_POWER_GUARD"
                    ),
                    "source": "runtime_neutral",
                    "reason": "no validated ARMD risk matched; neutral proposal",
                    "safety_level": "CLEAR",
                    "role": "advisory",
                    "advisory_only": True,
                }
            )
            return advice

        best = max(candidates, key=self._candidate_sort_key)
        scenario_ref = self.scenarios.get(best["scenario"])
        if scenario_ref is None:
            advice["reason"] = f"scenario matched but not frozen in hybrid summary: {best['scenario']}"
            return advice

        advice.update(
            {
                "loaded": True,
                "available": True,
                "proposal_present": True,
                "proposal_valid": True,
                "proposal_kind": "contextual",
                "scenario": best["scenario"],
                "domain": best["domain"],
                "expected_energy_saver": best["energy_saver"],
                "expected_action": best["action"],
                "confidence": round(float(best["confidence"]), 4),
                "match_score": round(float(best["confidence"]), 4),
                "reason": best["reason"],
                "source": scenario_ref.source,
                "threshold": scenario_ref.threshold,
                "subset_size": scenario_ref.subset_size,
                "target_hits": scenario_ref.target_hits,
                "completed_seeds": scenario_ref.completed_seeds,
                "mean_f1_at_target_epoch": scenario_ref.mean_f1_at_target_epoch,
                "global_healthy": global_healthy,
                "evidence": best["evidence"],
                "suggests_stronger_protection": self._is_stronger(
                    best["energy_saver"],
                    decision.get("energy_saver", "UNKNOWN"),
                ),
            }
        )
        advice["safety_level"] = self._safety_level(advice)
        advice["role"] = "safety_enforcer" if advice["safety_level"] == "HARD_VETO" else "advisory"
        advice["advisory_only"] = advice["safety_level"] != "HARD_VETO"
        return advice

    def apply(self, decision: Dict, advice: Dict, *, mutate: bool = True) -> Dict:
        """Attach ARMD metadata and optionally apply its protection.

        In assistant-judge mode ``mutate`` is false: ARMD is a proposer and
        the rApp judge decides whether its complete proposal is applied.
        The legacy path keeps the original protection-only behavior.
        """
        decision["armd_enabled"] = self.enabled
        decision["armd_mode"] = self.mode
        decision["armd_loaded"] = advice.get("loaded", False)
        decision["armd_proposal_present"] = bool(
            self.enabled and advice.get("proposal_present", advice.get("available", False))
        )
        decision["armd_proposal_valid"] = bool(advice.get("proposal_valid", False))
        decision["armd_proposal_kind"] = advice.get("proposal_kind", "missing")
        decision["armd_scenario"] = advice.get("scenario", "")
        decision["armd_domain"] = advice.get("domain", "")
        decision["armd_source"] = advice.get("source", "")
        decision["armd_confidence"] = advice.get("confidence", 0.0)
        decision["armd_reason"] = advice.get("reason", "")
        decision["armd_expected_energy_saver"] = advice.get("expected_energy_saver", "")
        decision["armd_expected_action"] = advice.get("expected_action", "")
        safety_level = str(advice.get("safety_level", "UNKNOWN") or "UNKNOWN").upper()
        decision["armd_safety_level"] = safety_level
        decision["armd_role"] = (
            "safety_enforcer" if safety_level == "HARD_VETO" else "advisory"
        )
        decision["armd_advisory_only"] = safety_level != "HARD_VETO"
        decision["armd_hard_veto"] = safety_level == "HARD_VETO"
        decision["armd_override_applied"] = False
        decision["armd_match_score"] = advice.get("match_score", 0.0)
        decision["armd_threshold"] = advice.get("threshold", self.threshold)
        decision["armd_subset_size"] = advice.get("subset_size", self.subset_size)
        decision["armd_evidence"] = advice.get("evidence", [])

        if not mutate or not advice.get("available"):
            return decision

        if advice.get("confidence", 0.0) < self.min_confidence:
            return decision

        if self.mode not in {"assist", "enforce"}:
            return decision

        if not self._is_stronger(
            advice.get("expected_energy_saver", "UNKNOWN"),
            decision.get("energy_saver", "UNKNOWN"),
        ):
            return decision

        old_status = decision.get("energy_saver", "UNKNOWN")
        old_action = decision.get("action", "NONE")
        decision["energy_saver"] = advice["expected_energy_saver"]
        decision["action"] = advice["expected_action"]
        decision["confidence"] = max(float(decision.get("confidence", 0.0) or 0.0), float(advice["confidence"]))
        decision["reason"] = (
            f"ARMD({advice['scenario']}): {advice['reason']} | "
            f"escalado de {old_status}/{old_action}"
        )
        decision["armd_override_applied"] = True
        print(
            f"\033[1;34m[rApp ARMD] {advice['scenario']} → "
            f"{advice['expected_energy_saver']} ({advice['expected_action']}) "
            f"[antes={old_status}/{old_action}]\033[0m"
        )
        return decision

    def _camera_candidates(self, camera_metrics: Dict) -> List[Dict]:
        candidates: List[Dict] = []
        active_cameras = int(camera_metrics.get("active_cameras", 0) or 0)
        if active_cameras <= 0:
            return candidates

        throughput_ready = bool(camera_metrics.get("throughput_ready", False))
        throughput_mbps = float(camera_metrics.get("throughput_mbps", 0) or 0)
        latency_ms = float(camera_metrics.get("latency_ms", 0) or 0)

        if throughput_ready and throughput_mbps < 25.0:
            candidates.append(
                self._candidate(
                    scenario="app1_throughput",
                    domain="app1",
                    energy_saver="BLOCKED",
                    action="FULL_POWER",
                    confidence=1.0,
                    reason=f"throughput por câmera {throughput_mbps:.1f}Mbps < 25Mbps",
                    evidence=[f"tp={throughput_mbps:.1f}Mbps", f"cams={active_cameras}"],
                )
            )
        if latency_ms >= 80.0:
            candidates.append(
                self._candidate(
                    scenario="app1_latencia",
                    domain="app1",
                    energy_saver="BLOCKED",
                    action="FULL_POWER",
                    confidence=1.0,
                    reason=f"latência de câmera {latency_ms:.1f}ms >= 80ms",
                    evidence=[f"lat={latency_ms:.1f}ms", f"cams={active_cameras}"],
                )
            )
        return candidates

    def _app2_candidates(self, app2_metrics: Dict, app2_state: str, global_healthy: bool) -> List[Dict]:
        candidates: List[Dict] = []
        if not app2_metrics.get("available"):
            return candidates

        connected_ratio = float(app2_metrics.get("connected_ratio", 1.0) or 0.0)
        packet_loss = float(app2_metrics.get("packet_loss_percent", 0.0) or 0.0)
        delivery = float(app2_metrics.get("delivery_success_percent", 100.0) or 100.0)
        latency_ms = float(app2_metrics.get("avg_latency_ms", 0.0) or 0.0)

        if app2_state == "critical":
            reason = (
                f"App2 crítico: connected_ratio={connected_ratio:.0%}, "
                f"loss={packet_loss:.1f}%, delivery={delivery:.1f}%"
            )
            candidates.append(
                self._candidate(
                    scenario="app2_degradado_critico",
                    domain="app2",
                    energy_saver="BLOCKED",
                    action="FULL_POWER",
                    confidence=1.0,
                    reason=reason,
                    evidence=[
                        f"connected={connected_ratio:.0%}",
                        f"loss={packet_loss:.1f}%",
                        f"delivery={delivery:.1f}%",
                    ],
                )
            )
            if self._history_has(self._app2_history, "warning"):
                candidates.append(
                    self._candidate(
                        scenario="recuperacao",
                        domain="multiapp",
                        energy_saver="BLOCKED",
                        action="FULL_POWER",
                        confidence=0.9,
                        reason="App2 voltou a piorar durante janela de recuperação",
                        evidence=[f"lat={latency_ms:.0f}ms", f"connected={connected_ratio:.0%}"],
                    )
                )
        elif app2_state == "warning":
            candidates.append(
                self._candidate(
                    scenario="app2_degradado_leve",
                    domain="app2",
                    energy_saver="CONDITIONAL",
                    action="FULL_POWER_GUARD",
                    confidence=0.95,
                    reason=(
                        f"App2 em guarda: connected_ratio={connected_ratio:.0%}, "
                        f"loss={packet_loss:.1f}%, delivery={delivery:.1f}%"
                    ),
                    evidence=[
                        f"connected={connected_ratio:.0%}",
                        f"loss={packet_loss:.1f}%",
                        f"delivery={delivery:.1f}%",
                    ],
                )
            )
            if global_healthy:
                candidates.append(
                    self._candidate(
                        scenario="conflito_implicito",
                        domain="multiapp",
                        energy_saver="CONDITIONAL",
                        action="FULL_POWER_GUARD",
                        confidence=0.9,
                        reason="KPI global saudável com degradação localizada no App2",
                        evidence=[f"connected={connected_ratio:.0%}", "global=healthy"],
                    )
                )
            if self._history_has(self._app2_history, "critical"):
                candidates.append(
                    self._candidate(
                        scenario="recuperacao",
                        domain="multiapp",
                        energy_saver="CONDITIONAL",
                        action="FULL_POWER_GUARD",
                        confidence=0.92,
                        reason="App2 em recuperação após estado crítico recente",
                        evidence=[f"connected={connected_ratio:.0%}", "history=critical"],
                    )
                )
        return candidates

    def _vehicle_candidates(self, vehicle_metrics: Dict, vehicle_state: str, global_healthy: bool) -> List[Dict]:
        candidates: List[Dict] = []
        if not vehicle_metrics.get("available"):
            return candidates

        high_risk = int(vehicle_metrics.get("high_risk_vehicles", 0) or 0)
        medium_risk = int(vehicle_metrics.get("medium_risk_vehicles", 0) or 0)
        latency_ms = float(vehicle_metrics.get("max_latency_ms", 0.0) or 0.0)
        packet_loss = float(vehicle_metrics.get("max_packet_loss_percent", 0.0) or 0.0)

        if vehicle_state == "critical":
            candidates.append(
                self._candidate(
                    scenario="vehicle_critical",
                    domain="app3",
                    energy_saver="BLOCKED",
                    action="FULL_POWER",
                    confidence=1.0,
                    reason=(
                        f"veicular crítico: high_risk={high_risk}, "
                        f"lat={latency_ms:.1f}ms, loss={packet_loss:.1f}%"
                    ),
                    evidence=[
                        f"high_risk={high_risk}",
                        f"lat={latency_ms:.1f}ms",
                        f"loss={packet_loss:.1f}%",
                    ],
                )
            )
        elif vehicle_state == "warning":
            base_reason = (
                f"veicular em guarda: medium_risk={medium_risk}, "
                f"lat={latency_ms:.1f}ms, loss={packet_loss:.1f}%"
            )
            candidates.append(
                self._candidate(
                    scenario="vehicle_warning",
                    domain="app3",
                    energy_saver="CONDITIONAL",
                    action="FULL_POWER_GUARD",
                    confidence=0.95,
                    reason=base_reason,
                    evidence=[
                        f"medium_risk={medium_risk}",
                        f"lat={latency_ms:.1f}ms",
                        f"loss={packet_loss:.1f}%",
                    ],
                )
            )
            if global_healthy:
                candidates.append(
                    self._candidate(
                        scenario="vehicle_implicito",
                        domain="app3",
                        energy_saver="CONDITIONAL",
                        action="FULL_POWER_GUARD",
                        confidence=1.0,
                        reason="KPI global saudável com degradação localizada no App3",
                        evidence=[f"lat={latency_ms:.1f}ms", "global=healthy"],
                    )
                )
            if self._history_has(self._vehicle_history, "critical"):
                candidates.append(
                    self._candidate(
                        scenario="vehicle_recovery",
                        domain="app3",
                        energy_saver="CONDITIONAL",
                        action="FULL_POWER_GUARD",
                        confidence=0.95,
                        reason="App3 em recuperação após estado crítico recente",
                        evidence=[f"lat={latency_ms:.1f}ms", "history=critical"],
                    )
                )
        return candidates

    @staticmethod
    def _derive_vehicle_state(vehicle_metrics: Dict) -> str:
        if not vehicle_metrics.get("available"):
            return "none"
        high_risk = int(vehicle_metrics.get("high_risk_vehicles", 0) or 0)
        degraded = int(vehicle_metrics.get("degraded_autonomy_vehicles", 0) or 0)
        latency_ms = float(vehicle_metrics.get("max_latency_ms", 0.0) or 0.0)
        packet_loss = float(vehicle_metrics.get("max_packet_loss_percent", 0.0) or 0.0)
        medium_risk = int(vehicle_metrics.get("medium_risk_vehicles", 0) or 0)

        if high_risk > 0 or degraded > 0 or latency_ms >= 100.0 or packet_loss >= 5.0:
            return "critical"
        if medium_risk > 0 or latency_ms >= 50.0 or packet_loss >= 2.0:
            return "warning"
        return "healthy"

    @staticmethod
    def _derive_app2_state(app2_metrics: Dict) -> str:
        if not app2_metrics.get("available"):
            return "none"
        connected_ratio = float(app2_metrics.get("connected_ratio", 1.0) or 0.0)
        packet_loss = float(app2_metrics.get("packet_loss_percent", 0.0) or 0.0)
        delivery = float(app2_metrics.get("delivery_success_percent", 100.0) or 100.0)
        latency_ms = float(app2_metrics.get("avg_latency_ms", 0.0) or 0.0)
        error_ratio = float(app2_metrics.get("error_ratio", 0.0) or 0.0)
        battery = float(app2_metrics.get("avg_battery_percent", 100.0) or 100.0)

        if (
            connected_ratio < 0.85
            or packet_loss >= 10.0
            or delivery < 90.0
            or latency_ms >= 1000.0
            or error_ratio >= 0.20
            or battery < 15.0
        ):
            return "critical"
        if connected_ratio < 0.95 or packet_loss >= 5.0 or delivery < 95.0 or latency_ms >= 500.0:
            return "warning"
        return "healthy"

    @staticmethod
    def _history_has(history: Iterable[str], state: str) -> bool:
        return any(item == state for item in history)

    @staticmethod
    def _candidate_sort_key(candidate: Dict) -> tuple:
        return (
            float(candidate["confidence"]),
            SEVERITY_RANK.get(candidate["energy_saver"], 0),
        )

    @staticmethod
    def _is_stronger(expected: str, current: str) -> bool:
        return SEVERITY_RANK.get(expected, 0) > SEVERITY_RANK.get(current, 0)

    @staticmethod
    def _candidate(
        *,
        scenario: str,
        domain: str,
        energy_saver: str,
        action: str,
        confidence: float,
        reason: str,
        evidence: List[str],
    ) -> Dict:
        return {
            "scenario": scenario,
            "domain": domain,
            "energy_saver": energy_saver,
            "action": action,
            "confidence": confidence,
            "reason": reason,
            "evidence": evidence,
        }

    @staticmethod
    def _safety_level(advice: Dict) -> str:
        """Separate ARMD safety authority from its ordinal energy label."""
        scenario = str(advice.get("scenario", "") or "").lower()
        violation = str(advice.get("priority_violation", "") or "").upper()
        if advice.get("critical_violation"):
            return "HARD_VETO"
        if scenario in {
            "app1_throughput", "app1_latencia", "app2_degradado_critico",
            "vehicle_critical",
        }:
            return "HARD_VETO"
        if violation in {
            "THROUGHPUT", "LATENCY", "APP2_MTC_CRITICAL",
            "VEHICLE_CRITICAL", "CVAR_CRITICAL", "P95_CRITICAL",
        }:
            return "HARD_VETO"
        verdict = str(advice.get("expected_energy_saver", "") or "").upper()
        if verdict == "ALLOWED":
            return "CLEAR"
        if verdict == "CONDITIONAL":
            return "ADVISORY"
        return "UNKNOWN"

    def _base_advice(self) -> Dict:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "loaded": self.loaded,
            "available": False,
            "proposal_present": False,
            "proposal_valid": False,
            "proposal_kind": "missing",
            "scenario": "",
            "domain": "",
            "expected_energy_saver": "",
            "expected_action": "",
            "confidence": 0.0,
            "match_score": 0.0,
            "reason": "",
            "source": "",
            "threshold": self.threshold,
            "subset_size": self.subset_size,
            "target_hits": 0,
            "completed_seeds": 0,
            "mean_f1_at_target_epoch": 0.0,
            "global_healthy": False,
            "suggests_stronger_protection": False,
            "safety_level": "UNKNOWN",
            "role": "advisory",
            "advisory_only": True,
            "evidence": [],
            "summary_path": str(self.summary_path),
            "updated_at": int(time.time()),
        }
