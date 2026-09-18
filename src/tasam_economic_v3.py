"""Economic action v3 primitives used by the ASGARD v10 runtime.

The module is deliberately independent from the orchestrator and from ns-3.
It provides the validation/projection rules that must be shared by the
checkpoint builder, E2 bundle, replay and tests.  Legacy v1/v2 callers keep
their existing contracts; v3 is opt-in and fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


CONTRACT = "economic_action_v3_per_du_sleep"
POWER_LEVELS_V3 = (0, *range(25, 101, 5))
ACTIVE_POWER_LEVELS_V3 = tuple(range(25, 101, 5))
DU_CELL_IDS = (2, 3, 4)


class EconomicActionV3Error(ValueError):
    """Raised when a v3 action cannot be safely represented."""


def quantize_power_percent_v3(value: Any) -> int:
    """Quantize one DU power command.

    ``0`` is the only sleeping value.  Any active value must be at least 25%
    and aligned to a 5% step.  Values are rounded to the nearest supported
    level, but values in the forbidden active band (0, 25) are rejected rather
    than silently teaching the policy an uncalibrated operating point.
    """
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EconomicActionV3Error("DU power must be numeric") from exc
    if number == 0.0:
        return 0
    if number < 25.0 or number > 100.0:
        raise EconomicActionV3Error("active DU power must be in [25,100] or exactly 0")
    quantized = int(5 * round(number / 5.0))
    if quantized < 25 or quantized > 100:
        raise EconomicActionV3Error("quantized DU power is outside [25,100]")
    return quantized


def normalize_power_by_cell(values: Mapping[Any, Any] | None) -> dict[int, int]:
    """Validate independent power for cells 2, 3 and 4.

    At most one DU may sleep at a time; this is a safety invariant before any
    handover protocol is considered.
    """
    if not isinstance(values, Mapping):
        raise EconomicActionV3Error("power_percent_by_cell is required")
    normalized: dict[int, int] = {}
    for cell_id in DU_CELL_IDS:
        raw = values.get(cell_id, values.get(str(cell_id)))
        if raw is None:
            raise EconomicActionV3Error(f"missing power for cell {cell_id}")
        normalized[cell_id] = quantize_power_percent_v3(raw)
    sleeping = [cell for cell, power in normalized.items() if power == 0]
    if len(sleeping) > 1:
        raise EconomicActionV3Error("no more than one DU may sleep simultaneously")
    return normalized


def project_total_budget_fraction(
    requested: Any, floor_total: Any, *, capacity_budget: float = 1.0
) -> tuple[float | None, dict[str, Any]]:
    """Project the economic total onto the verified SLA floor and capacity.

    Unlike the historical projection, v3 always exposes full capacity as the
    policy ceiling.  Current demand remains an observation/state feature.
    """
    try:
        floor = float(floor_total)
        capacity = float(capacity_budget)
        target = float(requested)
    except (TypeError, ValueError) as exc:
        return None, {"valid": False, "reason": "non_numeric_budget"}
    if not (0.0 <= capacity <= 1.0) or floor < 0.0 or floor > capacity:
        return None, {
            "valid": False,
            "reason": "sla_floor_infeasible",
            "floor_total": floor,
            "capacity_budget": capacity,
        }
    target = max(0.0, min(capacity, target))
    projected = max(floor, target)
    return projected, {
        "valid": True,
        "reason": "capacity_budget_with_sla_floor",
        "capacity_budget": capacity,
        "floor_total": floor,
        "requested_total_budget_fraction": target,
        "projected_total_budget_fraction": projected,
    }


def realized_economic_reward(
    energy_saving: Any,
    allocation_saving: Any,
    *,
    soft_sla_shortfall_penalty: Any = 0.0,
    hard_safety_penalty: Any = 0.0,
) -> dict[str, float]:
    """Calculate the bilateral realized reward, with bounded components."""
    def bounded(value: Any) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        return max(-1.0, min(1.0, value))

    energy = bounded(energy_saving)
    allocation = bounded(allocation_saving)
    soft = max(0.0, min(1.0, bounded(soft_sla_shortfall_penalty)))
    hard = max(0.0, min(1.0, bounded(hard_safety_penalty)))
    reward = -1.0 if hard >= 1.0 else max(
        -1.0, min(1.0, 0.55 * energy + 0.45 * allocation - soft - hard)
    )
    return {
        "energy_saving": energy,
        "allocation_saving": allocation,
        "soft_sla_shortfall_penalty": soft,
        "hard_safety_penalty": hard,
        "reward": reward,
    }


@dataclass
class DUSleepCoordinator:
    """Small explicit state machine for one-DU sleep requests.

    The actual handover is performed by ns-3/E2.  This state machine prevents
    the controller from claiming a sleeping DU before the required native
    handover and one full PDCP window are observed.
    """

    active_cells: tuple[int, ...] = DU_CELL_IDS
    sleeping_cell: int | None = None
    pending_cell: int | None = None
    source_ues: tuple[int, ...] = ()
    destination_cells: tuple[int, ...] = ()
    handover_confirmed: bool = False
    pdcp_window_confirmed: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)

    def request_sleep(self, source_cell: int, ue_imsis: list[int], destination_cells: list[int]) -> dict[str, Any]:
        source_cell = int(source_cell)
        destinations = tuple(sorted({int(cell) for cell in destination_cells}))
        if source_cell not in DU_CELL_IDS:
            raise EconomicActionV3Error("sleep source must be a canonical DU cell")
        if self.pending_cell is not None or self.sleeping_cell is not None:
            raise EconomicActionV3Error("a DU sleep transition is already active")
        if len(self.active_cells) - 1 < 2:
            raise EconomicActionV3Error("at least two DUs must remain active")
        if not destinations or source_cell in destinations:
            raise EconomicActionV3Error("sleep requires distinct destination DUs")
        self.pending_cell = source_cell
        self.source_ues = tuple(sorted({int(imsi) for imsi in ue_imsis}))
        self.destination_cells = destinations
        self.handover_confirmed = False
        self.pdcp_window_confirmed = False
        event = {
            "event": "sleep_requested",
            "source_cell_id": source_cell,
            "ue_imsis": list(self.source_ues),
            "destination_cells": list(destinations),
            "required_pdcp_window_s": 10.0,
        }
        self.history.append(event)
        return dict(event)

    def confirm_handover(self, *, association_valid: bool, remaining_ues: list[int] | None = None) -> bool:
        if self.pending_cell is None or not association_valid or remaining_ues:
            self.history.append({"event": "handover_rejected", "reason": "association_or_ue_incomplete"})
            return False
        self.handover_confirmed = True
        self.history.append({"event": "handover_confirmed", "source_cell_id": self.pending_cell})
        return True

    def confirm_pdcp_window(self, *, duration_s: float, sla_valid: bool, all_ues_present: bool) -> bool:
        if not self.handover_confirmed or float(duration_s) < 10.0 or not sla_valid or not all_ues_present:
            self.history.append({"event": "pdcp_window_rejected", "reason": "post_handover_window_invalid"})
            return False
        self.pdcp_window_confirmed = True
        self.history.append({"event": "pdcp_window_confirmed", "duration_s": float(duration_s)})
        return True

    def commit_sleep(self) -> dict[str, Any]:
        if self.pending_cell is None or not self.handover_confirmed or not self.pdcp_window_confirmed:
            raise EconomicActionV3Error("cannot commit DU sleep before handover and PDCP confirmation")
        self.sleeping_cell = self.pending_cell
        self.active_cells = tuple(cell for cell in self.active_cells if cell != self.sleeping_cell)
        event = {"event": "sleep_committed", "cell_id": self.sleeping_cell, "active_cells": list(self.active_cells)}
        self.history.append(event)
        self.pending_cell = None
        return dict(event)

    def abort(self, reason: str) -> dict[str, Any]:
        event = {"event": "sleep_aborted", "reason": str(reason), "source_cell_id": self.pending_cell}
        self.history.append(event)
        self.pending_cell = None
        self.handover_confirmed = False
        self.pdcp_window_confirmed = False
        return event
