import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_tasam_network_campaign import (
    _metric_alignment_tokens,
    _scheduler_resource_stats,
    _service_scores,
    aggregate,
    pair_result,
)
from drlexp.src.drl.online_greenran_marl_env import build_balanced_vehicle_energy_reward
from src.energy_calibration import integrate_energy_events, load_calibration, state_power_w


def make_db(root: Path, *, assistant: bool, metric_count: int = 65) -> None:
    root.mkdir(parents=True, exist_ok=True)
    db = root / "rapp_data_lake.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        create table decisions_history (
            id integer primary key, ta_sam_actuation_applied integer default 0,
            armd_enabled integer default 0, armd_override_applied integer default 0,
            control_trial_reason text, ran_completion_ratio real, ai_completion_ratio real,
            effective_policy_algorithm text, effective_policy_source text,
            control_trial_mode text, utilization_ratio real
        );
        create table extended_metrics (
            id integer primary key, collector_mode text, proxy_latency_sample_count real,
            pdcp_stale integer, latency_p95_per_ue_us real, cvar_per_ue_us real,
            throughput_kbps real, global_packet_loss_rate real
        );
        create table ue_metrics (
            id integer primary key, device_type text, latency_us real, throughput_kbps real,
            packet_loss_percent real, tx_bytes real, rx_bytes real
        );
        create table energy_commands (
            id integer primary key, timestamp integer, datetime text, command text,
            power_percent integer, ru_count integer, mmwave_count integer, reason text
        );
        """
    )
    for i in range(1, 66):
        conn.execute(
            """insert into decisions_history
               (id, ta_sam_actuation_applied, armd_enabled, armd_override_applied,
                control_trial_reason, ran_completion_ratio, ai_completion_ratio,
                effective_policy_algorithm, effective_policy_source,
                control_trial_mode, utilization_ratio)
               values (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)""",
            (i, int(assistant), int(assistant), "canary selected" if assistant else "disabled", 1.0, 0.8 + (0.01 if assistant else 0), "TA-SAM-MARL" if assistant else "HEURISTIC", "checkpoint" if assistant else "heuristic_baseline", "assistant_only_control" if assistant else "disabled", 0.8),
        )
        if i <= metric_count:
            conn.execute(
                "insert into extended_metrics values (?, 'pdcp_real', 0, 0, ?, ?, ?, ?)",
                (i, 79_000 if assistant else 80_000, 119_000 if assistant else 120_000, 100_000, 0.001),
            )
        for j in range(3):
            conn.execute("insert into ue_metrics values (?, 'camera', 50_000, 25_000, 0.1, 100, 99.9)", (i * 100 + j,))
        for j in range(5):
            conn.execute("insert into ue_metrics values (?, 'vehicle', 10_000, 1_600, 0.1, 100, 99.9)", (i * 100 + 20 + j,))
        for j in range(12):
            conn.execute("insert into ue_metrics values (?, 'sensor', 100_000, 10, 1.0, 100, 99.0)", (i * 100 + 40 + j,))
        conn.execute(
            "insert into energy_commands values (?, ?, ?, 'POWER_DOWN', 50, 1, 1, 'test')",
            (i, 1_700_000_000 + i, f"2023-11-14 00:00:{i:02d}"),
        )
    conn.commit()
    conn.close()


class TestTasamNetworkEvaluation(unittest.TestCase):
    def test_scheduler_resource_trace_counts_data_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            trace_dir = run_dir / "ns3_traces"
            trace_dir.mkdir()
            (trace_dir / "EnbSchedAllocTraces.txt").write_text(
                "frame\tsubF\tslot\trnti\tfirstSym\tnumSym\ttype\ttddMode\tretxNum\tccId\n"
                "0\t0\t0\t0\t0\t1\t2\t1\t0\t0\n"
                "0\t0\t0\t7\t0\t4\t0\t1\t0\t0\n"
                "0\t0\t1\t7\t0\t2\t0\t2\t1\t0\n",
                encoding="utf-8",
            )
            stats = _scheduler_resource_stats(run_dir)
            self.assertTrue(stats["valid"])
            self.assertEqual(stats["row_count"], 3)
            self.assertEqual(stats["data_allocation_events"], 2)
            self.assertEqual(stats["data_symbols"], 6)
            self.assertEqual(stats["dl_allocation_events"], 1)
            self.assertEqual(stats["ul_allocation_events"], 1)
            self.assertEqual(stats["retx_allocation_events"], 1)
            self.assertFalse(stats["physical_prb_count_available"])

    def test_metric_alignment_uses_sim_time_and_occurrence(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("create table metrics (sim_time_s real)")
        conn.executemany("insert into metrics values (?)", [(0.2,), (0.2,), (0.3,)])
        rows = conn.execute("select * from metrics").fetchall()
        tokens, scheme = _metric_alignment_tokens(rows)
        self.assertEqual(scheme, "sim_time_occurrence")
        self.assertEqual(tokens, ["sim:0.200#0", "sim:0.200#1", "sim:0.300#0"])
        conn.close()

    def test_calibrated_power_is_monotonic_and_integrates_to_joules(self):
        calibration = load_calibration()
        full = state_power_w(calibration, 1, 1, 100)
        eco = state_power_w(calibration, 1, 1, 25)
        self.assertGreater(full, eco)
        result = integrate_energy_events(
            [
                {"timestamp_ns": 0, "ru_count": 1, "mmwave_count": 1, "power_percent": 100},
                {"timestamp_ns": 2_000_000_000, "ru_count": 1, "mmwave_count": 1, "power_percent": 25},
            ],
            calibration,
            end_timestamp_ns=4_000_000_000,
        )
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["energy_j"], 2 * full + 2 * eco)
        self.assertAlmostEqual(result["average_power_w"], (full + eco) / 2)

    def test_sqlite_row_nanosecond_timestamp_is_not_scaled_twice(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("create table energy (timestamp integer, timestamp_ns integer, ru_count integer, mmwave_count integer, power_percent integer)")
        conn.execute("insert into energy values (?, ?, ?, ?, ?)", (1_700_000_000, 1_700_000_000_000_000_000, 1, 1, 100))
        row = conn.execute("select * from energy").fetchone()
        result = integrate_energy_events(
            [row], load_calibration(), end_timestamp_ns=1_700_000_001_000_000_000
        )
        self.assertAlmostEqual(result["duration_s"], 1.0)
        conn.close()

    def test_inactive_sensor_rows_are_not_counted_as_sla_failures(self):
        rows = [
            {
                "device_type": "sensor", "latency_us": 100_000, "throughput_kbps": 10,
                "packet_loss_percent": 1.0, "tx_bytes": 100, "rx_bytes": 99,
            },
            {
                "device_type": "sensor", "latency_us": 0, "throughput_kbps": 0,
                "packet_loss_percent": None, "tx_bytes": 0, "rx_bytes": 0,
            },
        ]
        metric_rows = [{"latency_p95_per_ue_us": 100_000, "global_packet_loss_rate": 0.001}]
        self.assertEqual(_service_scores(rows, metric_rows)["sensor"], 1.0)

    def test_pair_uses_real_pdcp_and_composite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_db(root / "baseline", assistant=False)
            make_db(root / "assistant", assistant=True)
            pair = pair_result(root / "baseline", root / "assistant", 46, 1, "drl_allowed_only_v1")
            self.assertTrue(pair["valid"])
            self.assertEqual(pair["scenario_profile"], "drl_allowed_only_v1")
            self.assertGreater(pair["deltas"]["composite_index"], 0)
            self.assertIn("energy_efficiency_proxy", pair["component_deltas"])
            self.assertGreater(pair["component_deltas"]["p95"], 0.0)
            self.assertGreater(pair["component_deltas"]["cvar"], 0.0)
            self.assertAlmostEqual(
                pair["weighted_component_contributions"]["sla"],
                0.65 * pair["component_deltas"]["sla"],
            )

    def test_aggregate_rejects_missing_or_invalid_pair(self):
        payload = aggregate([{"valid": False, "deltas": {}}])
        self.assertFalse(payload["approved"])
        self.assertEqual(payload["valid_pair_count"], 0)

    def test_pair_rejects_metric_window_shorter_than_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_db(root / "baseline", assistant=False, metric_count=44)
            make_db(root / "assistant", assistant=True, metric_count=65)
            pair = pair_result(root / "baseline", root / "assistant", 45, 1)
            self.assertFalse(pair["valid"])
            self.assertFalse(pair["metrics_aligned"]["baseline"])
            self.assertTrue(pair["metrics_aligned"]["assistant"])
            self.assertFalse(pair["metrics_aligned"]["same_real_pdcp_rows"])

    def test_pair_allows_one_real_snapshot_gap_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_db(root / "baseline", assistant=False, metric_count=65)
            make_db(root / "assistant", assistant=True, metric_count=64)
            pair = pair_result(root / "baseline", root / "assistant", 45, 1, allow_metric_gap=True)
            self.assertTrue(pair["valid"])
            self.assertTrue(pair["metrics_aligned"]["comparison_aligned_with_one_snapshot_gap"])

    def test_pair_allows_one_real_snapshot_gap_with_explicit_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_db(root / "baseline", assistant=False, metric_count=65)
            make_db(root / "assistant", assistant=True, metric_count=64)
            pair = pair_result(root / "baseline", root / "assistant", 45, 1, max_metric_gap=1)
            self.assertTrue(pair["valid"])
            alignment = pair["metrics_aligned"]
            self.assertEqual(alignment["max_metric_gap"], 1)
            self.assertEqual(alignment["metric_row_gap"], 1)
            self.assertTrue(alignment["gap_tolerated"])
            self.assertEqual(alignment["common_aligned_real_pdcp_rows"], 64)

    def test_pair_rejects_gap_above_explicit_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_db(root / "baseline", assistant=False, metric_count=65)
            make_db(root / "assistant", assistant=True, metric_count=63)
            pair = pair_result(root / "baseline", root / "assistant", 45, 1, max_metric_gap=1)
            self.assertFalse(pair["valid"])
            self.assertFalse(pair["metrics_aligned"]["gap_within_limit"])

    def test_pair_report_records_no_tolerance_for_equal_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_db(root / "baseline", assistant=False, metric_count=65)
            make_db(root / "assistant", assistant=True, metric_count=65)
            pair = pair_result(root / "baseline", root / "assistant", 45, 1, max_metric_gap=1)
            self.assertTrue(pair["valid"])
            alignment = pair["metrics_aligned"]
            self.assertEqual(alignment["baseline_real_pdcp_rows"], 65)
            self.assertEqual(alignment["assistant_real_pdcp_rows"], 65)
            self.assertFalse(alignment["gap_tolerated"])

    def test_energy_delta_is_relative_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_db(root / "baseline", assistant=False, metric_count=65)
            make_db(root / "assistant", assistant=True, metric_count=65)
            pair = pair_result(root / "baseline", root / "assistant", 45, 1)
            self.assertAlmostEqual(pair["deltas"]["energy_relative_saving"], 0.0)
            self.assertLessEqual(abs(pair["deltas"]["energy_balanced_delta"]), 0.10)

    def test_balanced_reward_prioritizes_vehicle_and_full_budget_penalty(self):
        slices = {
            "eMBB": {"completion_ratio": 1.0, "qos_pressure": 0.1, "demand": 0.3, "min_qos_met": 1.0},
            "mMTC": {"completion_ratio": 1.0, "qos_pressure": 0.1, "demand": 0.2, "min_qos_met": 1.0},
            "URLLC": {"completion_ratio": 1.0, "qos_pressure": 0.1, "demand": 0.2, "min_qos_met": 1.0},
        }
        metrics = {"cvar_per_ue_us": 5_000.0, "global_packet_loss_rate": 0.0}
        lean, _ = build_balanced_vehicle_energy_reward(
            slices, metrics, {"usable_budget": 1.0, "slice_allocation": {"eMBB": 0.3, "mMTC": 0.2, "URLLC": 0.2}}
        )
        full, components = build_balanced_vehicle_energy_reward(
            slices, metrics, {"usable_budget": 1.0, "slice_allocation": {"eMBB": 0.6, "mMTC": 0.3, "URLLC": 0.1}}
        )
        self.assertEqual(components["vehicle_latency_score"], 1.0)
        self.assertGreater(lean, full)
        self.assertGreater(components["resource_use_penalty"], 0.0)

    def test_balanced_reward_penalizes_relative_cvar_regression(self):
        slices = {
            "eMBB": {"completion_ratio": 1.0, "qos_pressure": 0.1, "demand": 0.3, "min_qos_met": 1.0},
            "mMTC": {"completion_ratio": 1.0, "qos_pressure": 0.1, "demand": 0.2, "min_qos_met": 1.0},
            "URLLC": {"completion_ratio": 1.0, "qos_pressure": 0.1, "demand": 0.2, "min_qos_met": 1.0},
        }
        action = {"usable_budget": 1.0, "slice_allocation": {"eMBB": 0.3, "mMTC": 0.2, "URLLC": 0.2}}
        healthy, healthy_components = build_balanced_vehicle_energy_reward(
            slices,
            {"cvar_per_ue_us": 1_500.0, "cvar_reference_ms": 1.5, "vehicle_latency_ms": 8.0},
            action,
        )
        regressed, regressed_components = build_balanced_vehicle_energy_reward(
            slices,
            {"cvar_per_ue_us": 4_800.0, "cvar_reference_ms": 1.5, "vehicle_latency_ms": 8.0},
            action,
        )
        self.assertEqual(healthy_components["tail_risk_penalty"], 0.0)
        self.assertGreater(regressed_components["tail_risk_penalty"], 0.0)
        self.assertLess(regressed, healthy)

    def test_balanced_reward_power_cost_is_only_applied_when_service_is_safe(self):
        slices = {
            "eMBB": {"completion_ratio": 1.0, "qos_pressure": 0.0, "demand": 0.3, "min_qos_met": 1.0},
            "mMTC": {"completion_ratio": 1.0, "qos_pressure": 0.0, "demand": 0.2, "min_qos_met": 1.0},
            "URLLC": {"completion_ratio": 1.0, "qos_pressure": 0.0, "demand": 0.2, "min_qos_met": 1.0},
        }
        metrics = {"cvar_per_ue_us": 5_000.0, "global_packet_loss_rate": 0.0}
        low, low_components = build_balanced_vehicle_energy_reward(
            slices, metrics, {"usable_budget": 1.0, "power_percent": 25.0, "slice_allocation": {"eMBB": 0.3, "mMTC": 0.2, "URLLC": 0.2}}
        )
        high, high_components = build_balanced_vehicle_energy_reward(
            slices, metrics, {"usable_budget": 1.0, "power_percent": 100.0, "slice_allocation": {"eMBB": 0.3, "mMTC": 0.2, "URLLC": 0.2}}
        )
        self.assertGreater(low, high)
        self.assertEqual(low_components["power_cost_penalty"], 0.0)
        self.assertGreater(high_components["power_cost_penalty"], 0.0)

        blocked, blocked_components = build_balanced_vehicle_energy_reward(
            slices, metrics, {"usable_budget": 1.0, "power_percent": 100.0, "allocation_state": "BLOCKED", "slice_allocation": {"eMBB": 0.3, "mMTC": 0.2, "URLLC": 0.2}}
        )
        self.assertEqual(blocked_components["power_cost_penalty"], 0.0)
        self.assertEqual(blocked_components["power_penalty_gated_by_service"], 0.0)

    def test_balanced_reward_completion_shortfall_is_monotonic(self):
        healthy = {
            "eMBB": {"completion_ratio": 1.0, "qos_pressure": 0.0, "demand": 0.3, "min_qos_met": 1.0},
            "mMTC": {"completion_ratio": 1.0, "qos_pressure": 0.0, "demand": 0.2, "min_qos_met": 1.0},
            "URLLC": {"completion_ratio": 1.0, "qos_pressure": 0.0, "demand": 0.2, "min_qos_met": 1.0},
        }
        short = {**healthy, "eMBB": {**healthy["eMBB"], "completion_ratio": 0.70}, "mMTC": {**healthy["mMTC"], "completion_ratio": 0.40}}
        action = {"usable_budget": 1.0, "power_percent": 25.0, "slice_allocation": {"eMBB": 0.3, "mMTC": 0.2, "URLLC": 0.2}}
        good, _ = build_balanced_vehicle_energy_reward(healthy, {"cvar_per_ue_us": 5_000.0}, action)
        bad, components = build_balanced_vehicle_energy_reward(short, {"cvar_per_ue_us": 5_000.0}, action)
        self.assertLess(bad, good)
        self.assertGreater(components["completion_shortfall_penalty"], 0.0)


if __name__ == "__main__":
    unittest.main()
