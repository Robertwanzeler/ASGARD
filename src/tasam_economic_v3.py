"""Economic action v3 primitives used by the ASGARD v10 runtime.

The module is deliberately independent from the orchestrator and from ns-3.
It provides the validation/projection rules that must be shared by the
checkpoint builder, E2 bundle, replay and tests.  Legacy v1/v2 callers keep
their existing contracts; v3 is opt-in and fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping


CONTRACT = "economic_action_v3_per_du_sleep"
ENERGY_STAIRCASE_CONTRACT = "greenran.tasam.v2x.energy_staircase.v1"
ENERGY_STAIRCASE_PROBE_CONTRACT = "greenran.tasam.v2x.energy_staircase.v2_safe_probe"
ENERGY_STAIRCASE_PROBE_10_CONTRACT = "greenran.tasam.v2x.energy_staircase.v3_safe_probe_10"
POWER_LEVELS_V3 = (0, *range(25, 101, 5))
ACTIVE_POWER_LEVELS_V3 = tuple(range(25, 101, 5))
DU_CELL_IDS = (2, 3, 4)


class EconomicActionV3Error(ValueError):
    """Raised when a v3 action cannot be safely represented."""


def _ceil_power_step(value: float) -> int:
    """Quantize a positive safe floor upward to the actuator's 5% grid."""
    return max(25, min(100, int(math.ceil(float(value) / 5.0) * 5)))


def energy_staircase_levels(
    safe_floor_percent: Any,
    *,
    allow_sleep: bool = False,
) -> tuple[int, ...]:
    """Return the auditable floor-to-115% candidate ladder for one DU.

    The floor is supplied by native telemetry or a validated checkpoint.  The
    helper never invents a floor: non-numeric, non-positive and non-finite
    values are rejected.  Sleep is an explicit candidate and is not mixed
    with the active-power ladder.
    """
    try:
        floor = float(safe_floor_percent)
    except (TypeError, ValueError) as exc:
        raise EconomicActionV3Error("safe power floor is required") from exc
    if not math.isfinite(floor) or floor <= 0.0 or floor > 100.0:
        raise EconomicActionV3Error("safe power floor must be in (0,100]")
    levels = tuple(dict.fromkeys(
        _ceil_power_step(min(100.0, floor * multiplier))
        for multiplier in (1.0, 1.05, 1.10, 1.15)
    ))
    return ((0,) + levels) if allow_sleep else levels


def build_energy_staircase(
    safe_floor_percent_by_cell: Mapping[Any, Any] | None,
    *,
    allow_sleep: bool = False,
) -> dict[int, tuple[int, ...]]:
    """Build a complete, per-DU staircase from native safe-floor evidence."""
    if not isinstance(safe_floor_percent_by_cell, Mapping):
        raise EconomicActionV3Error("per-DU safe power floors are required")
    result: dict[int, tuple[int, ...]] = {}
    for cell_id in DU_CELL_IDS:
        value = safe_floor_percent_by_cell.get(cell_id, safe_floor_percent_by_cell.get(str(cell_id)))
        result[cell_id] = energy_staircase_levels(value, allow_sleep=allow_sleep)
    return result


def staircase_candidate(
    safe_floor_percent_by_cell: Mapping[Any, Any] | None,
    requested_power_by_cell: Mapping[Any, Any] | None,
    *,
    allow_sleep: bool = False,
    state: Mapping[Any, Any] | None = None,
    healthy: bool = False,
    healthy_required: int = 3,
    critical: bool = False,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Project one TA-SAM request onto the safe ladder.

    A healthy observation advances one rung only after ``healthy_required``
    consecutive healthy decisions.  Any critical/incomplete observation
    restores the last confirmed rung.  This function is pure so the caller
    can persist the returned state atomically with the decision.
    """
    ladders = build_energy_staircase(safe_floor_percent_by_cell, allow_sleep=allow_sleep)
    requested = normalize_power_by_cell(requested_power_by_cell)
    previous = dict(state or {})
    streak = int(previous.get("healthy_streak", 0) or 0)
    streak = streak + 1 if healthy and not critical else 0
    required = max(1, int(healthy_required))
    last_confirmed = dict(previous.get("last_confirmed_by_cell") or {})
    rung_by_cell = dict(previous.get("rung_by_cell") or {})
    selected: dict[int, int] = {}
    for cell_id in DU_CELL_IDS:
        ladder = ladders[cell_id]
        active_ladder = tuple(level for level in ladder if level != 0)
        if critical or not healthy:
            current = int(last_confirmed.get(str(cell_id), last_confirmed.get(cell_id, active_ladder[-1])))
        else:
            current = int(rung_by_cell.get(str(cell_id), rung_by_cell.get(cell_id, active_ladder[-1])))
            if streak >= required:
                position = active_ladder.index(current) if current in active_ladder else len(active_ladder) - 1
                current = active_ladder[max(0, position - 1)]
        # The staircase controls the auditable candidate.  The raw actor
        # request is deliberately not allowed to skip the conservative
        # start or the three-decision confirmation gate.
        requested_value = requested[cell_id]
        sleep_selected = bool(allow_sleep and requested_value == 0 and not critical)
        if sleep_selected:
            current = 0
        elif current not in active_ladder:
            current = active_ladder[-1]
        selected[cell_id] = current
        rung_by_cell[cell_id] = current
        if healthy and not critical:
            last_confirmed[cell_id] = current
    next_state = {
        "contract": ENERGY_STAIRCASE_CONTRACT,
        "healthy_streak": streak,
        "healthy_required": required,
        "rung_by_cell": {str(k): int(v) for k, v in rung_by_cell.items()},
        "last_confirmed_by_cell": {str(k): int(v) for k, v in last_confirmed.items()},
        "allow_sleep": bool(allow_sleep),
    }
    return selected, next_state


def safe_probe_staircase_candidate(
    requested_power_by_cell: Mapping[Any, Any] | None,
    *,
    state: Mapping[Any, Any] | None = None,
    healthy: bool = False,
    critical: bool = False,
    healthy_required: int = 3,
    step_percent: int | None = None,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Descend in a versioned step only after three healthy observations.

    A historical rApp command at 100% is a safe reference, not a measured RF
    minimum. Any failed, critical, or incomplete observation restores every
    DU to 100%; a lower rung is committed only after three healthy outcomes.
    """
    normalize_power_by_cell(requested_power_by_cell)
    previous = dict(state or {})
    default_step = 10 if previous.get("contract") == ENERGY_STAIRCASE_PROBE_10_CONTRACT else 5
    try:
        step = int(step_percent if step_percent is not None else previous.get("step_percent", default_step))
    except (TypeError, ValueError):
        step = 5
    if step not in {5, 10}:
        raise EconomicActionV3Error("safe probe step must be 5 or 10 percent")
    required = max(1, int(healthy_required))
    confirmed_raw = previous.get("last_confirmed_by_cell") or {}
    trial_raw = previous.get("trial_by_cell") or {}
    confirmed = {
        cell: int(confirmed_raw.get(str(cell), confirmed_raw.get(cell, 100)))
        for cell in DU_CELL_IDS
    }
    trial = {
        cell: int(trial_raw.get(str(cell), trial_raw.get(cell, confirmed[cell])))
        for cell in DU_CELL_IDS
    }
    streak = int(previous.get("trial_healthy_streak", 0) or 0)
    if critical or not healthy:
        selected = {cell: 100 for cell in DU_CELL_IDS}
        confirmed = dict(selected)
        trial = dict(selected)
        streak = 0
        reason = "critical_or_incomplete_restore_full_power"
    else:
        streak += 1
        if streak >= required:
            for cell in DU_CELL_IDS:
                if trial[cell] < confirmed[cell]:
                    confirmed[cell] = trial[cell]
                trial[cell] = max(25, confirmed[cell] - step)
            streak = 0
        selected = dict(trial)
        reason = "healthy_probe_hold_or_descend"
    return selected, {
        "contract": (
            ENERGY_STAIRCASE_PROBE_10_CONTRACT
            if step == 10 else ENERGY_STAIRCASE_PROBE_CONTRACT
        ),
        "step_percent": step,
        "healthy_required": required,
        "trial_healthy_streak": streak,
        "last_confirmed_by_cell": {str(cell): confirmed[cell] for cell in DU_CELL_IDS},
        "trial_by_cell": {str(cell): trial[cell] for cell in DU_CELL_IDS},
        "probe_reason": reason,
    }


def safe_probe_staircase_selected(
    state: Mapping[Any, Any] | None,
) -> dict[int, int]:
    """Return the next safe-probe command without consuming an observation.

    The control loop can issue several planning cycles while an E2/PDCP
    observation is still in flight.  Planning must therefore be a read-only
    operation: only the finalizer that owns the correlated native observation
    is allowed to advance the staircase.
    """
    previous = dict(state or {})
    trial_raw = (
        previous.get("next_selected_power_by_cell")
        or previous.get("trial_by_cell")
        or previous.get("last_confirmed_by_cell")
        or {}
    )
    selected: dict[int, int] = {}
    for cell in DU_CELL_IDS:
        raw = trial_raw.get(str(cell), trial_raw.get(cell, 100))
        try:
            selected[cell] = quantize_power_percent_v3(raw)
        except EconomicActionV3Error:
            # Corrupted controller state must fail closed rather than creating
            # an unverified low-power command.
            selected[cell] = 100
    return selected


def safe_probe_staircase_observe(
    state: Mapping[Any, Any] | None,
    *,
    observation_sequence: Any,
    healthy: bool,
    critical: bool,
    healthy_required: int | None = None,
    step_percent: int | None = None,
) -> dict[str, Any]:
    """Advance a safe probe exactly once for its correlated E2/PDCP result.

    ``observation_sequence`` is the native control sequence that issued the
    candidate.  Duplicate, late, or re-finalized observations are ignored so
    an asynchronous controller cannot count the same healthy result more than
    once.  Incomplete evidence is deliberately treated as critical and
    restores full power in the next command.
    """
    previous = dict(state or {})
    try:
        sequence = int(observation_sequence)
    except (TypeError, ValueError):
        sequence = -1
    try:
        processed = int(previous.get("last_processed_observation_sequence", -1))
    except (TypeError, ValueError):
        processed = -1
    if sequence < 0 or sequence <= processed:
        return previous

    required = (
        int(healthy_required)
        if healthy_required is not None
        else int(previous.get("healthy_required", 3) or 3)
    )
    selected, next_state = safe_probe_staircase_candidate(
        {cell: 100 for cell in DU_CELL_IDS},
        state=previous,
        healthy=bool(healthy),
        critical=bool(critical),
        healthy_required=max(1, required),
        step_percent=step_percent,
    )
    next_state["next_selected_power_by_cell"] = {
        str(cell): int(selected[cell]) for cell in DU_CELL_IDS
    }
    next_state["last_processed_observation_sequence"] = sequence
    next_state["last_observation_healthy"] = bool(healthy and not critical)
    next_state["last_observation_critical"] = bool(critical or not healthy)
    return next_state


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
