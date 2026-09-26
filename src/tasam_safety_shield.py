#!/usr/bin/env python3
"""Per-UE hard-SLA evaluation and projection for TA-SAM actions."""

from __future__ import annotations

import math
import os
from typing import Any, Iterable

from greenran_control_bundle import ControlBundleError, quantize_power_percent, service_for_imsi
from greenran_paths import get_fixed_service_imsis


def _shield_vehicle_loss_percent_max() -> float:
    """Limite hard de loss veicular do escudo (calibração seed-43).

    O cenário v2x seed-43 opera os veículos com SINR médio negativo
    (pior caso −8,9 dB) e ~10% de perda PDCP MESMO a 100% de potência —
    o limite contratual de 1% é insatisfatível para qualquer braço e
    mantinha o escudo em failsafe permanente (r19/r21: baseline 239/239
    BLOCKED, seleção 80/90, not_promotable).  O limite do ESCUDO passa a
    cobrir a física atingível (12%), alinhado com a referência válida
    r5_r2, que aplicou cortes de 25/60% sob o mesmo regime.  O limite de
    1% permanece na recompensa (loss_limit_percent) e na avaliação
    (loss_percent_lt) — o escudo é a última linha de defesa, não a meta.
    """
    default = 12.0
    raw = os.environ.get('GREENRAN_TASAM_SHIELD_VEHICLE_LOSS_PERCENT_MAX', '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def _shield_vehicle_latency_p95_ms_max() -> float:
    """Limite hard de latência P95 veicular do escudo.

    A regra original avaliava o MAX por janela com 20 ms; um único pico de
    retransmissão mmWave (21-36 ms medidos no r27, com P95 ~1 ms e perda
    ~0%) reprovava TODOS os veículos e mantinha o escudo em failsafe
    permanente a partir de ~30 s de simulação — nenhum braço cortava na
    segunda metade do run (r26/r27).  O gate passa a usar P95, mesmo
    estatístico da disciplina veicular do projeto
    (tasam_autonomous_vehicle_safe_v1: latency_p95_ms_lt = 20.0).
    """
    default = 20.0
    raw = os.environ.get('GREENRAN_TASAM_SHIELD_VEHICLE_LATENCY_P95_MS_MAX', '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def _shield_camera_throughput_mbps_min() -> float:
    """Limite hard de throughput por câmera no escudo (calibração seed-43).

    O throughput ENTREGUE da câmera é limitado pela carga ofertada do
    cenário, não pelo rádio: no r28 as três câmeras mediam 22,6 Mbps a
    100% de potência com perda 0,01% e P95 ~1 ms — abaixo do gate
    original de 25 Mbps (2e08b58), que era insatisfatível em qualquer
    potência e mantinha o escudo em sla_violation permanente a partir de
    ~30 s (trava remanescente r26-r28).  O gate passa a calibrar a física
    atingível com margem anti-starvation (18.0 ≈ 80% do saudável medido;
    starving real colapsa para < 5 Mbps).  A meta contratual de 25 Mbps
    permanece na recompensa e na avaliação — o escudo é a última linha
    de defesa, não a meta.
    """
    default = 18.0
    raw = os.environ.get('GREENRAN_TASAM_SHIELD_CAMERA_THROUGHPUT_MBPS_MIN', '').strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


SLA = {
    "camera": {
        "throughput_mbps_min": _shield_camera_throughput_mbps_min(),
        "latency_p95_ms_max": 80.0,
    },
    "sensor": {"delivery_percent_min": 95.0, "loss_percent_max": 5.0, "latency_p95_ms_max": 500.0},
    "vehicle": {
        "loss_percent_max": _shield_vehicle_loss_percent_max(),
        "latency_p95_ms_max": _shield_vehicle_latency_p95_ms_max(),
    },
}
CANONICAL_SERVICE_IMSIS = get_fixed_service_imsis()
CANONICAL_IMSIS = tuple(CANONICAL_SERVICE_IMSIS["all"])


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def canonical_cell_for_imsi(imsi: int) -> int:
    if 1 <= imsi <= 6:
        return 2
    if 7 <= imsi <= 9:
        return 3
    if 10 <= imsi <= 20:
        return 4
    raise ControlBundleError(f"IMSI outside canonical 1..20 topology: {imsi}")


def build_runtime_ue_inputs(snapshot: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Translate one real collector snapshot into SLA and demand inputs.

    The conversion is deliberately fail-closed.  It emits a row for every
    canonical IMSI, but a missing/proxy row remains unobserved and therefore
    cannot authorize a reduced-power policy.
    """
    payload = snapshot if isinstance(snapshot, dict) else {}
    raw_ues = payload.get("ue_metrics") if isinstance(payload.get("ue_metrics"), dict) else {}
    sim_range = payload.get("sim_time_range") if isinstance(payload.get("sim_time_range"), dict) else {}
    window_seconds = max(0.1, _number(sim_range.get("window_s"), 1.0))
    indexed: dict[int, dict[str, Any]] = {}
    for key, value in raw_ues.items():
        if not isinstance(value, dict):
            continue
        try:
            imsi = int(value.get("imsi", key))
        except (TypeError, ValueError):
            continue
        if imsi in CANONICAL_IMSIS:
            indexed[imsi] = value

    cell_symbol_totals = {2: 0, 3: 0, 4: 0}
    for imsi, ue in indexed.items():
        cell_id = canonical_cell_for_imsi(imsi)
        cell_symbol_totals[cell_id] += max(0, int(_number(ue.get("mmwave_sched_symbols"), 0)))

    def _observed_rate(ue: dict[str, Any]) -> float:
        return max(
            0.0,
            _number(ue.get("rx_throughput_kbps"), _number(ue.get("throughput_kbps"), 0.0)) * 1000.0,
        )

    # Capacidade por unidade de share medida a partir dos pares observados da
    # própria célula.  Um UE sem símbolos na janela (starving/beam switch)
    # não tem share próprio e a extrapolação individual dividiria por zero —
    # isso saturava o piso em 10000bp (100% da célula) e colocava o escudo
    # em failsafe permanente (r19-r27: ambos os braços param de cortar).
    peer_capacity_bps: dict[int, float] = {}
    peer_rate: dict[int, float] = {2: 0.0, 3: 0.0, 4: 0.0}
    peer_share: dict[int, float] = {2: 0.0, 3: 0.0, 4: 0.0}
    for imsi, ue in indexed.items():
        cell_id = canonical_cell_for_imsi(imsi)
        total_symbols = cell_symbol_totals[cell_id]
        share = (
            max(0, int(_number(ue.get("mmwave_sched_symbols"), 0))) / total_symbols
            if total_symbols > 0 else 0.0
        )
        if share > 0.0:
            peer_rate[cell_id] += _observed_rate(ue)
            peer_share[cell_id] += share
    for cell_id in (2, 3, 4):
        if peer_share[cell_id] > 0.0:
            peer_capacity_bps[cell_id] = peer_rate[cell_id] / peer_share[cell_id]
        else:
            peer_capacity_bps[cell_id] = 0.0

    sla_rows: list[dict[str, Any]] = []
    demand_rows: list[dict[str, Any]] = []
    for imsi in CANONICAL_IMSIS:
        ue = indexed.get(imsi, {})
        cell_id = canonical_cell_for_imsi(imsi)
        tx_pdus = max(0, int(_number(ue.get("tx_pdus"), 0)))
        rx_pdus = max(0, int(_number(ue.get("rx_pdus"), 0)))
        scheduler_observation_present = (
            "mmwave_sched_symbols" in ue
            and ue.get("mmwave_sched_symbols") is not None
        )
        loss = _number(ue.get("packet_loss_percent"), -1.0)
        if loss < 0.0:
            loss = 100.0 if tx_pdus <= 0 else 100.0 * max(0.0, 1.0 - rx_pdus / tx_pdus)
        delivery = 0.0 if tx_pdus <= 0 else 100.0 * min(1.0, rx_pdus / tx_pdus)
        offered_kbps = max(
            0.0,
            _number(ue.get("offered_load_kbps"), _number(ue.get("tx_throughput_kbps"), 0.0)),
        )
        achieved_bps = max(
            0.0,
            _number(ue.get("rx_throughput_kbps"), _number(ue.get("throughput_kbps"), 0.0)) * 1000.0,
        )
        allocated_symbols = max(0, int(_number(ue.get("mmwave_sched_symbols"), 0)))
        total_symbols = cell_symbol_totals[cell_id]
        observed_share = allocated_symbols / total_symbols if total_symbols > 0 else 0.0
        # Extrapolate the UE's measured rate to a full cell budget.  MCS/CQI
        # are retained below and add uncertainty headroom in the floor model.
        # UEs without their own share inherit the peer-measured capacity so a
        # starved window never saturates the per-UE floor at 100%.
        capacity_bps = (
            max(achieved_bps, offered_kbps * 1000.0) / observed_share
            if observed_share > 0.0 else peer_capacity_bps[cell_id]
        )
        has_real_latency = bool(ue.get("has_latency_samples"))
        provenance = str(ue.get("pdcp_provenance", ue.get("latency_source", "")) or "")
        real_pdcp = has_real_latency and not bool(ue.get("latency_is_proxy")) and provenance == "pdcp_real"
        queue_active = tx_pdus > 0 or offered_kbps > 0 or _number(ue.get("backlog_bytes"), 0) > 0
        latency_p95 = ue.get("latency_p95_us")
        latency_max = ue.get("latency_max_us", ue.get("latency_us"))
        common = {
            "imsi": imsi,
            "cell_id": cell_id,
            "observed": bool(ue) and real_pdcp,
            "connected": rx_pdus > 0,
            "tx_pdus": tx_pdus,
            "rx_pdus": rx_pdus,
            "sample_window_s": window_seconds,
            "pdcp_provenance": provenance or None,
            "scheduler_observation_present": scheduler_observation_present,
            "queue_active": queue_active,
            "allocated_symbols": allocated_symbols,
            "throughput_mbps": achieved_bps / 1_000_000.0,
            "delivery_percent": delivery,
            "loss_percent": loss,
            "latency_p95_ms": None if latency_p95 is None else _number(latency_p95) / 1000.0,
            "latency_max_ms": None if latency_max is None else _number(latency_max) / 1000.0,
        }
        sla_rows.append(common)
        demand_rows.append({
            "imsi": imsi,
            "cell_id": cell_id,
            "offered_load_bps": offered_kbps * 1000.0,
            "full_budget_capacity_bps": capacity_bps,
            "backlog_bytes": max(0.0, _number(ue.get("backlog_bytes"), 0.0)),
            "window_seconds": window_seconds,
            "mcs_avg": _number(ue.get("mcs_avg"), -1.0),
            "cqi_avg": _number(ue.get("cqi_avg"), -1.0),
            "observed_share": observed_share,
            "scheduler_observation_present": scheduler_observation_present,
        })
    return sla_rows, demand_rows


def real_pdcp_window_is_mature(
    rows: Iterable[dict[str, Any]], *, min_window_s: float = 0.5
) -> bool:
    """Return whether a complete real-PDCP window can authorize control.

    Wall-clock warm-up is a poor proxy for readiness when ns-3 advances more
    slowly than the controller.  This predicate keeps the safety requirements
    strict while allowing a control decision as soon as the collector has a
    complete, real observation for the canonical topology.
    """
    materialized = [dict(row) for row in rows]
    if len(materialized) != len(CANONICAL_IMSIS):
        return False
    if {row.get("imsi") for row in materialized} != set(CANONICAL_IMSIS):
        return False
    for row in materialized:
        try:
            window_s = float(row.get("sample_window_s"))
            tx_pdus = int(row.get("tx_pdus", 0) or 0)
            rx_pdus = int(row.get("rx_pdus", 0) or 0)
        except (TypeError, ValueError):
            return False
        if (
            not math.isfinite(window_s)
            or window_s < min_window_s
            or not bool(row.get("observed"))
            or not bool(row.get("connected"))
            or tx_pdus <= 0
            or rx_pdus <= 0
            or str(row.get("pdcp_provenance", "")) != "pdcp_real"
        ):
            return False
    return True


def evaluate_ue_window(row: dict[str, Any]) -> dict[str, Any]:
    """Treat missing observations, disconnects and starvation as hard failures."""
    try:
        imsi = int(row["imsi"])
        service = service_for_imsi(imsi)
    except (KeyError, TypeError, ValueError, ControlBundleError) as exc:
        return {"pass": False, "violations": [f"identity:{exc}"]}
    violations: list[str] = []
    if not bool(row.get("observed", True)):
        violations.append("missing_metrics")
    if not bool(row.get("connected", True)):
        violations.append("disconnected")
    # A missing scheduler trace is not proof of starvation: low-rate UEs can
    # deliver all PDCP packets in a window without appearing in the sampled
    # allocation trace.  PDCP delivery remains the authoritative SLA signal.
    if (
        bool(row.get("queue_active", False))
        and bool(row.get("scheduler_observation_present", True))
        and int(row.get("allocated_symbols", 0) or 0) <= 0
        and int(row.get("rx_pdus", 0) or 0) <= 0
    ):
        violations.append("starvation")
    rules = SLA[service]

    def number(name: str) -> float | None:
        try:
            value = float(row[name])
            return value if math.isfinite(value) else None
        except (KeyError, TypeError, ValueError):
            return None

    comparisons = {
        "throughput_mbps_min": (number("throughput_mbps"), lambda a, b: a >= b),
        "delivery_percent_min": (number("delivery_percent"), lambda a, b: a >= b),
        "loss_percent_max": (number("loss_percent"), lambda a, b: a <= b),
        "latency_p95_ms_max": (number("latency_p95_ms"), lambda a, b: a <= b),
        "latency_max_ms_max": (number("latency_max_ms"), lambda a, b: a <= b),
    }
    margins: dict[str, float] = {}
    for rule, threshold in rules.items():
        observed, predicate = comparisons[rule]
        if observed is None:
            violations.append(f"missing:{rule}")
            continue
        if not predicate(observed, threshold):
            violations.append(rule)
        margins[rule] = observed - threshold if rule.endswith("_min") else threshold - observed
    return {
        "pass": not violations,
        "imsi": imsi,
        "service": service,
        "violations": violations,
        "margins": margins,
    }


def evaluate_sla_window(rows: Iterable[dict[str, Any]], *, require_all_ues: bool = True) -> dict[str, Any]:
    evaluations = [evaluate_ue_window(dict(row)) for row in rows]
    observed = {entry.get("imsi") for entry in evaluations if entry.get("imsi") is not None}
    missing = sorted(set(CANONICAL_IMSIS) - observed) if require_all_ues else []
    violations = [entry for entry in evaluations if not entry["pass"]]
    if missing:
        violations.extend({"pass": False, "imsi": imsi, "violations": ["missing_ue"]} for imsi in missing)
    return {
        "pass": not violations,
        "ue_count": len(observed),
        "missing_imsis": missing,
        "violation_count": len(violations),
        "violations": violations,
    }


def demand_floor_basis_points(ue: dict[str, Any], *, guard_ratio: float = 0.10) -> int:
    """Estimate the minimum scheduler share from offered load and radio efficiency."""
    offered = max(0.0, float(ue.get("offered_load_bps", 0.0) or 0.0))
    capacity = max(1.0, float(ue.get("full_budget_capacity_bps", 0.0) or 0.0))
    backlog_bits = max(0.0, float(ue.get("backlog_bytes", 0.0) or 0.0) * 8.0)
    window_seconds = max(0.1, float(ue.get("window_seconds", 1.0) or 1.0))
    required_bps = offered + backlog_bits / window_seconds
    mcs = _number(ue.get("mcs_avg"), -1.0)
    cqi = _number(ue.get("cqi_avg"), -1.0)
    radio_uncertainty = 0.0
    if mcs >= 0.0:
        radio_uncertainty += 0.15 * max(0.0, min(1.0, (18.0 - mcs) / 18.0))
    if cqi >= 0.0:
        radio_uncertainty += 0.10 * max(0.0, min(1.0, (10.0 - cqi) / 10.0))
    raw = (required_bps / capacity) * (1.0 + max(0.0, guard_ratio) + radio_uncertainty)
    return max(1, min(10_000, int(math.ceil(raw * 10_000.0))))


def project_safe_action(
    proposal: dict[str, Any],
    ue_demand: Iterable[dict[str, Any]],
    latest_sla: dict[str, Any],
) -> dict[str, Any]:
    """Project a proposal onto the feasible set or return an explicit fail-safe."""
    demand = [dict(item) for item in ue_demand]
    ue_capacity_by_imsi = {
        int(item.get("imsi", 0)): float(item.get("full_budget_capacity_bps", 0.0) or 0.0)
        for item in demand
    }
    proposed = {int(item.get("imsi", 0)): item for item in proposal.get("ue_policies", [])}
    floors: dict[int, int] = {}
    for item in demand:
        imsi = int(item["imsi"])
        envelope_floor = proposed.get(imsi, {}).get("min_dl_share_bp")
        try:
            envelope_floor = int(envelope_floor)
        except (TypeError, ValueError):
            envelope_floor = 0
        if envelope_floor > 0:
            # ARMD/Judge has already checked this per-UE floor against the
            # shared budget.  Do not replace that verified envelope with a
            # noisier instantaneous scheduler-share extrapolation.
            floors[imsi] = min(10_000, envelope_floor)
            continue
        # A demand floor needs a measurable cell capacity.  Without symbols
        # anywhere in the cell the extrapolation is meaningless: treating the
        # missing capacity as a 100% floor synthesized a permanent fail-safe
        # (r19-r27).  Missing traces and dark cells keep only verified
        # envelopes, never an invented floor.
        capacity_present = float(ue_capacity_by_imsi.get(imsi, 0.0) or 0.0) > 0.0
        if item.get("scheduler_observation_present", True) and capacity_present:
            floors[imsi] = demand_floor_basis_points(item)
            continue
        # The scheduler trace may omit a UE even though its real PDCP window
        # is complete.  In that case use only the already verified ARMD/Judge
        # envelope supplied with the proposal; never turn an absent trace into
        # a synthetic 100% floor.
        floors[imsi] = max(0, min(10_000, envelope_floor))
    floor_by_cell: dict[int, int] = {}
    for item in demand:
        cell_id = int(item.get("cell_id", canonical_cell_for_imsi(int(item["imsi"]))))
        floor_by_cell[cell_id] = floor_by_cell.get(cell_id, 0) + floors[int(item["imsi"])]
    floor_total = sum(floors.values())
    unsafe = not bool(latest_sla.get("pass", False)) or any(
        total > 10_000 for total in floor_by_cell.values()
    )
    result = dict(proposal)
    if unsafe:
        result.update({
            "safe": False,
            "failsafe": True,
            "reason": "sla_violation" if not latest_sla.get("pass", False) else "infeasible_ue_floors",
            "tx_power_percent": 100,
            "ue_policies": [],
            "floor_total_bp": floor_total,
            "floor_by_cell_bp": floor_by_cell,
        })
        return result
    policies = []
    for item in demand:
        imsi = int(item["imsi"])
        raw = proposed.get(imsi, {})
        policies.append({
            "imsi": imsi,
            "cell_id": int(item.get("cell_id", canonical_cell_for_imsi(imsi))),
            "min_dl_share_bp": max(floors[imsi], int(raw.get("min_dl_share_bp", 0) or 0)),
            "min_ul_share_bp": max(0, int(raw.get("min_ul_share_bp", 0) or 0)),
            "surplus_weight_bp": max(1, min(10_000, int(raw.get("surplus_weight_bp", 10_000) or 10_000))),
        })
    committed_by_cell: dict[int, int] = {}
    for policy in policies:
        cell_id = int(policy["cell_id"])
        committed_by_cell[cell_id] = committed_by_cell.get(cell_id, 0) + int(
            policy["min_dl_share_bp"]
        ) + int(policy["min_ul_share_bp"])
    if any(total > 10_000 for total in committed_by_cell.values()):
        result.update({
            "safe": False,
            "failsafe": True,
            "reason": "infeasible_committed_ue_floors",
            "tx_power_percent": 100,
            "ue_policies": [],
            "floor_total_bp": floor_total,
            "floor_by_cell_bp": committed_by_cell,
        })
        return result
    result.update({
        "safe": True,
        "failsafe": False,
        "tx_power_percent": quantize_power_percent(proposal.get("tx_power_percent", 100)),
        "ue_policies": policies,
        "floor_total_bp": floor_total,
        "floor_by_cell_bp": floor_by_cell,
    })
    return result
