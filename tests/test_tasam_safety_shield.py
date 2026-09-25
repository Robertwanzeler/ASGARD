import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tasam_safety_shield import (  # noqa: E402
    build_runtime_ue_inputs,
    demand_floor_basis_points,
    evaluate_sla_window,
    project_safe_action,
    real_pdcp_window_is_mature,
)
import tasam_safety_shield  # noqa: E402


def _healthy_rows():
    rows = []
    for imsi in range(1, 21):
        common = {"imsi": imsi, "observed": True, "connected": True, "queue_active": True, "allocated_symbols": 1}
        if imsi <= 3:
            common.update(throughput_mbps=25.0, latency_p95_ms=80.0)
        elif imsi <= 15:
            common.update(delivery_percent=95.0, loss_percent=5.0, latency_p95_ms=500.0)
        else:
            common.update(loss_percent=1.0, latency_max_ms=20.0)
        rows.append(common)
    return rows


def test_all_twenty_ues_must_meet_absolute_sla():
    assert evaluate_sla_window(_healthy_rows())["pass"] is True
    bad = _healthy_rows()
    bad[15]["latency_max_ms"] = 20.01
    report = evaluate_sla_window(bad)
    assert report["pass"] is False
    assert report["violations"][0]["imsi"] == 16


def test_missing_metric_and_starvation_are_hard_failures():
    rows = _healthy_rows()
    del rows[0]["throughput_mbps"]
    rows[1]["allocated_symbols"] = 0
    report = evaluate_sla_window(rows)
    reasons = {reason for item in report["violations"] for reason in item["violations"]}
    assert "missing:throughput_mbps_min" in reasons
    assert "starvation" in reasons


def test_floor_uses_offered_load_backlog_and_guard():
    floor = demand_floor_basis_points({
        "imsi": 1,
        "offered_load_bps": 25_000_000,
        "full_budget_capacity_bps": 100_000_000,
        "backlog_bytes": 0,
        "window_seconds": 1,
    })
    assert floor == 2750


def test_infeasible_floor_projects_to_full_power_failsafe():
    demand = [
        {"imsi": imsi, "offered_load_bps": 10, "full_budget_capacity_bps": 10}
        for imsi in range(1, 21)
    ]
    result = project_safe_action(
        {"tx_power_percent": 25, "ue_policies": []}, demand, {"pass": True}
    )
    assert result["failsafe"] is True
    assert result["tx_power_percent"] == 100
    assert result["ue_policies"] == []


def test_runtime_input_rejects_proxy_or_missing_scheduler_observation():
    snapshot = {
        "sim_time_range": {"window_s": 1.0},
        "ue_metrics": {
            str(imsi): {
                "has_latency_samples": True,
                "latency_is_proxy": imsi == 1,
                "pdcp_provenance": "proxy" if imsi == 1 else "pdcp_real",
                "latency_p95_us": 1_000,
                "latency_max_us": 2_000,
                "tx_pdus": 10,
                "rx_pdus": 0 if imsi == 2 else 10,
                "tx_throughput_kbps": 25_000 if imsi <= 3 else 100,
                "rx_throughput_kbps": 25_000 if imsi <= 3 else 100,
                "mmwave_sched_symbols": 0 if imsi == 2 else 10,
            }
            for imsi in range(1, 21)
        },
    }
    sla_rows, demand_rows = build_runtime_ue_inputs(snapshot)
    report = evaluate_sla_window(sla_rows)
    reasons = {
        reason for violation in report["violations"] for reason in violation["violations"]
    }
    assert "missing_metrics" in reasons
    assert "starvation" in reasons
    assert len(demand_rows) == 20


def test_real_pdcp_window_can_end_slow_simulator_warmup():
    snapshot = {
        "sim_time_range": {"window_s": 0.8},
        "ue_metrics": {
            str(imsi): {
                "has_latency_samples": True,
                "pdcp_provenance": "pdcp_real",
                "latency_p95_us": 1_000,
                "latency_max_us": 2_000,
                "tx_pdus": 10,
                "rx_pdus": 10,
                "tx_throughput_kbps": 25_000 if imsi <= 3 else 100,
                "rx_throughput_kbps": 25_000 if imsi <= 3 else 100,
                "mmwave_sched_symbols": 10,
            }
            for imsi in range(1, 21)
        },
    }
    rows, _ = build_runtime_ue_inputs(snapshot)
    assert real_pdcp_window_is_mature(rows) is True

    rows[0]["sample_window_s"] = 0.4
    assert real_pdcp_window_is_mature(rows) is False


def test_feasibility_is_checked_per_cell_not_across_independent_cells():
    demand = [
        {
            "imsi": imsi,
            "cell_id": 2 if imsi <= 6 else 3 if imsi <= 9 else 4,
            "offered_load_bps": 1,
            "full_budget_capacity_bps": 20,
        }
        for imsi in range(1, 21)
    ]
    result = project_safe_action(
        {"tx_power_percent": 55, "ue_policies": []}, demand, {"pass": True}
    )
    assert result["safe"] is True
    assert result["floor_total_bp"] > 10_000
    assert all(total <= 10_000 for total in result["floor_by_cell_bp"].values())


def test_missing_scheduler_trace_uses_verified_envelope_not_synthetic_full_floor():
    demand = [
        {
            "imsi": imsi,
            "cell_id": 2 if imsi <= 6 else 3 if imsi <= 9 else 4,
            "offered_load_bps": 25_000_000 if imsi <= 3 else 100,
            "full_budget_capacity_bps": 0,
            "scheduler_observation_present": False,
        }
        for imsi in range(1, 21)
    ]
    policies = [
        {"imsi": imsi, "min_dl_share_bp": 500 if imsi <= 3 else 100}
        for imsi in range(1, 21)
    ]
    result = project_safe_action(
        {"tx_power_percent": 60, "ue_policies": policies},
        demand,
        {"pass": True},
    )
    assert result["safe"] is True
    assert result["floor_total_bp"] == 3 * 500 + 17 * 100


def test_verified_envelope_is_not_replaced_by_noisy_scheduler_extrapolation():
    demand = [
        {
            "imsi": imsi,
            "cell_id": 2 if imsi <= 6 else 3 if imsi <= 9 else 4,
            "offered_load_bps": 25_000_000 if imsi <= 3 else 100,
            "full_budget_capacity_bps": 1_000,
            "scheduler_observation_present": True,
        }
        for imsi in range(1, 21)
    ]
    policies = [
        {"imsi": imsi, "min_dl_share_bp": 500 if imsi <= 3 else 100}
        for imsi in range(1, 21)
    ]
    result = project_safe_action(
        {"tx_power_percent": 60, "ue_policies": policies},
        demand,
        {"pass": True},
    )
    assert result["safe"] is True
    assert result["floor_total_bp"] == 3 * 500 + 17 * 100


def _seed43_like_vehicle_rows():
    """Física do cenário seed-43: piores veículos a ~10% de loss a 100%."""
    rows = _healthy_rows()
    rows[15]["loss_percent"] = 10.0
    rows[17]["loss_percent"] = 9.6
    return rows


def test_vehicle_loss_no_cenario_seed43_passa_no_escudo():
    report = evaluate_sla_window(_seed43_like_vehicle_rows())
    assert report["pass"] is True


def test_vehicle_loss_acima_do_limite_do_escudo_falha():
    rows = _seed43_like_vehicle_rows()
    rows[15]["loss_percent"] = 20.0
    report = evaluate_sla_window(rows)
    assert report["pass"] is False
    assert any(v.get("imsi") == 16 for v in report["violations"])


def test_limite_do_escudo_e_ajustavel_por_ambiente(monkeypatch):
    monkeypatch.setenv("GREENRAN_TASAM_SHIELD_VEHICLE_LOSS_PERCENT_MAX", "2.0")
    import importlib

    import tasam_safety_shield

    importlib.reload(tasam_safety_shield)
    try:
        rows = _seed43_like_vehicle_rows()
        report = tasam_safety_shield.evaluate_sla_window(rows)
        assert report["pass"] is False
    finally:
        monkeypatch.delenv("GREENRAN_TASAM_SHIELD_VEHICLE_LOSS_PERCENT_MAX")
        importlib.reload(tasam_safety_shield)


def test_proposta_de_reducao_com_veiculos_a_10pct_nao_vai_a_failsafe():
    rows = _seed43_like_vehicle_rows()
    demand = [
        {
            "imsi": imsi,
            "offered_load_bps": 1_000_000.0,
            "full_budget_capacity_bps": 10_000_000.0,
            "backlog_bytes": 0,
            "window_seconds": 1.0,
            "mcs_avg": -1.0,
            "cqi_avg": -1.0,
            "scheduler_observation_present": False,
            "cell_id": 2,
        }
        for imsi in range(1, 21)
    ]
    sla = evaluate_sla_window(rows)
    proposal = {
        "tx_power_percent": 75,
        "ue_policies": [
            {"imsi": imsi, "min_dl_share_bp": 100} for imsi in range(1, 21)
        ],
    }
    result = project_safe_action(proposal, demand, sla)
    assert result.get("failsafe") is not True
