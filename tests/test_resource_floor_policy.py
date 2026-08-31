import json
import tempfile
import unittest
from pathlib import Path

from src.rapp_data_lake import DataLake
from src.rapp_orchestrator import RappResourceOptimizer
from src.rapp_sac_resource_model import apply_baseline_resource_band, apply_per_ue_allocation, compute_shared_resource_snapshot, enforce_resource_floor, enforce_resource_state


def _config():
    return json.loads(Path("config/core/runtime.json").read_text(encoding="utf-8"))["shared_resources"]


def _metrics():
    return (
        {
            "active_cameras": 3,
            "observed_cameras": 3,
            "critical_cameras": 0,
            "throughput_mbps": 35.0,
            "throughput_ready": True,
            "latency_ms": 20.0,
        },
        {
            "total_sensors": 12,
            "connected_ratio": 1.0,
            "delivery_success_percent": 100.0,
            "avg_latency_ms": 100.0,
        },
        {
            "total_vehicles": 5,
            "max_latency_ms": 5.0,
            "max_packet_loss_percent": 0.0,
            "high_risk_vehicles": 0,
            "medium_risk_vehicles": 0,
            "degraded_autonomy_vehicles": 0,
        },
        {"cvar_us": 50_000.0, "p95_us": 30_000.0},
    )


class TestResourceFloorPolicy(unittest.TestCase):
    def test_allowed_keeps_nonzero_floor_for_all_ues(self):
        snapshot = compute_shared_resource_snapshot(*_metrics(), _config(), {}, "ALLOWED")
        self.assertEqual(snapshot["allocation_state"], "ALLOWED")
        self.assertTrue(snapshot["floor_feasible"])
        self.assertEqual(snapshot["r_ran"], snapshot["floor_total_ran"])
        self.assertEqual(snapshot["r_ai"], snapshot["floor_total_ai"])
        self.assertEqual(len(snapshot["per_ue_floor"]), 20)
        self.assertTrue(all(entry["floor_share"] > 0 for entry in snapshot["per_ue_floor"]))

    def test_only_critical_and_blocked_add_reinforcement_above_floor(self):
        metrics = _metrics()
        conditional = compute_shared_resource_snapshot(*metrics, _config(), {}, "CONDITIONAL")
        critical = compute_shared_resource_snapshot(*metrics, _config(), {}, "CRITICAL")
        blocked = compute_shared_resource_snapshot(*metrics, _config(), {}, "BLOCKED")
        self.assertEqual(conditional["reinforcement_ran"] + conditional["reinforcement_ai"], 0.0)
        self.assertGreater(critical["reinforcement_ran"] + critical["reinforcement_ai"], 0.0)
        self.assertGreater(
            blocked["reinforcement_ran"] + blocked["reinforcement_ai"],
            critical["reinforcement_ran"] + critical["reinforcement_ai"],
        )
        self.assertGreaterEqual(blocked["r_ran"], blocked["floor_total_ran"])
        self.assertGreaterEqual(blocked["r_ai"], blocked["floor_total_ai"])

    def test_armd_noncritical_proposal_uses_the_same_floor_as_tasam(self):
        optimizer = RappResourceOptimizer.__new__(RappResourceOptimizer)
        optimizer.cycle = 1
        snapshot = compute_shared_resource_snapshot(*_metrics(), _config(), {}, "CONDITIONAL")
        proposal = optimizer._build_armd_proposal(
            {
                "timestamp": 123,
                "priority_violation": "THROUGHPUT_WARNING",
                "energy_saver": "CONDITIONAL",
                "action": "FULL_POWER_GUARD",
            },
            {
                "available": True,
                "proposal_present": True,
                "proposal_valid": True,
                "domain": "camera",
                "scenario": "proactive_sla_guard",
                "expected_energy_saver": "CONDITIONAL",
                "expected_action": "FULL_POWER_GUARD",
                "confidence": 0.8,
                "priority_violation": "THROUGHPUT_WARNING",
                "safety_veto": False,
                "critical_violation": False,
            },
            snapshot,
        )

        allocation = proposal["resource_allocation"]
        self.assertEqual(proposal["resource_mode"], "floor_only")
        self.assertEqual(allocation["r_ran"], allocation["floor_total_ran"])
        self.assertEqual(allocation["r_ai"], allocation["floor_total_ai"])
        self.assertEqual(allocation["reinforcement_ran"], 0.0)
        self.assertEqual(allocation["reinforcement_ai"], 0.0)

    def test_healthy_armd_cycle_is_an_explicit_floor_proposal(self):
        optimizer = RappResourceOptimizer.__new__(RappResourceOptimizer)
        optimizer.cycle = 1
        decision = {
            "energy_saver": "ALLOWED",
            "action": "REDUCE_POWER",
            "camera_metrics": {"active_cameras": 0},
            "vehicle_metrics": {"available": False, "total_vehicles": 0},
            "network_health": {"cvar_us": 10_000.0, "p95_us": 20_000.0},
        }
        armd_advice = {
            "available": True,
            "proposal_present": True,
            "proposal_valid": True,
            "scenario": "greenran_global_noop",
            "proposal_kind": "neutral_noop",
            "source": "runtime_neutral",
            "confidence": 0.0,
        }
        tasam = {
            "valid": True,
            "feasible": True,
            "resource_advice": {"enabled": True, "delta_r_ran_vs_live": 0.0},
        }

        advice = optimizer._build_proactive_sla_guard_advice(decision, armd_advice, tasam)
        self.assertEqual(advice["scenario"], "greenran_floor_minimum")
        self.assertEqual(advice["proposal_kind"], "resource_floor")
        self.assertEqual(advice["source"], "runtime_floor")
        self.assertFalse(advice["safety_veto"])

        snapshot = compute_shared_resource_snapshot(*_metrics(), _config(), {}, "ALLOWED")
        proposal = optimizer._build_armd_proposal(decision, advice, snapshot)
        allocation = proposal["resource_allocation"]
        self.assertTrue(proposal["valid"])
        self.assertEqual(proposal["resource_mode"], "floor_only")
        self.assertEqual(allocation["r_ran"], allocation["floor_total_ran"])
        self.assertEqual(allocation["r_ai"], allocation["floor_total_ai"])
        self.assertEqual(allocation["reinforcement_ran"], 0.0)
        self.assertEqual(allocation["reinforcement_ai"], 0.0)

    def test_recovery_returns_to_floor_after_two_healthy_cycles(self):
        metrics = _metrics()
        blocked = compute_shared_resource_snapshot(*metrics, _config(), {}, "BLOCKED")
        first_healthy = compute_shared_resource_snapshot(*metrics, _config(), blocked, "ALLOWED")
        second_healthy = compute_shared_resource_snapshot(*metrics, _config(), first_healthy, "ALLOWED")
        self.assertEqual(first_healthy["healthy_streak"], 1)
        self.assertEqual(first_healthy["r_ran"], first_healthy["floor_total_ran"])
        self.assertEqual(first_healthy["r_ai"], first_healthy["floor_total_ai"])
        self.assertEqual(second_healthy["healthy_streak"], 2)
        self.assertEqual(second_healthy["r_ran"], second_healthy["floor_total_ran"])
        self.assertEqual(second_healthy["r_ai"], second_healthy["floor_total_ai"])

    def test_final_state_change_recomputes_reinforcement_scale(self):
        metrics = _metrics()
        snapshot = compute_shared_resource_snapshot(*metrics, _config(), {}, "ALLOWED")
        snapshot["allocation_state"] = "BLOCKED"
        promoted = enforce_resource_state(snapshot)
        self.assertEqual(promoted["allocation_state"], "BLOCKED")
        self.assertGreater(promoted["reinforcement_ran"] + promoted["reinforcement_ai"], 0.0)

    def test_baseline_uses_fixed_state_band_instead_of_floor(self):
        metrics = _metrics()
        snapshot = compute_shared_resource_snapshot(*metrics, _config(), {}, "CONDITIONAL")
        baseline = apply_baseline_resource_band(snapshot, "CONDITIONAL", _config())
        self.assertEqual(baseline["allocation_mode"], "baseline_fixed_band_v1")
        self.assertTrue(baseline["baseline_band_applied"])
        self.assertGreater(baseline["r_ran"], baseline["floor_total_ran"])
        self.assertGreater(baseline["r_ai"], baseline["floor_total_ai"])
        self.assertAlmostEqual(baseline["r_ran"] + baseline["r_ai"], baseline["usable_budget"])
        self.assertEqual(baseline["reinforcement_ran"], 0.0)
        self.assertEqual(baseline["reinforcement_ai"], 0.0)
        self.assertEqual(baseline["per_ue_allocation"], [])
        self.assertEqual(baseline["per_ue_application_status"], "baseline_not_applicable")

    def test_per_ue_allocation_preserves_floor_and_aggregate_totals(self):
        snapshot = compute_shared_resource_snapshot(*_metrics(), _config(), {}, "BLOCKED")
        allocated = apply_per_ue_allocation(snapshot)
        self.assertTrue(allocated["per_ue_floor_applied"])
        self.assertEqual(allocated["per_ue_floor_violation_count"], 0)
        self.assertTrue(allocated["per_ue_floor_feasible"])
        self.assertEqual(len(allocated["per_ue_allocation"]), len(snapshot["per_ue_floor"]))
        for domain in ("ran", "ai"):
            entries = [item for item in allocated["per_ue_allocation"] if item["domain"] == domain]
            self.assertAlmostEqual(sum(item["allocated_share"] for item in entries), allocated[f"r_{domain}"], places=10)
            self.assertTrue(all(item["floor_met"] for item in entries))

    def test_per_ue_allocation_reports_infeasible_domain_without_false_guarantee(self):
        snapshot = compute_shared_resource_snapshot(*_metrics(), _config(), {}, "ALLOWED")
        snapshot["r_ai"] = 0.0
        allocated = apply_per_ue_allocation(snapshot)
        ai_entries = [item for item in allocated["per_ue_allocation"] if item["domain"] == "ai"]
        self.assertGreater(allocated["per_ue_floor_violation_count"], 0)
        self.assertFalse(allocated["per_ue_floor_feasible"])
        self.assertTrue(all(not item["floor_met"] for item in ai_entries))

    def test_infeasible_floor_is_reported_without_false_guarantee(self):
        config = _config()
        config.update({"sla_floor_demand_fraction": 1.0, "ran_min_active_demand": 0.8, "ai_min_active_demand": 0.8})
        snapshot = compute_shared_resource_snapshot(*_metrics(), config, {}, "ALLOWED")
        self.assertFalse(snapshot["floor_feasible"])
        constrained = enforce_resource_floor(snapshot, 0.0, 0.0)
        self.assertFalse(constrained["floor_feasible"])

    def test_datalake_persists_floor_audit_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "rapp.db")
            lake = DataLake(database)
            snapshot = apply_per_ue_allocation(
                compute_shared_resource_snapshot(*_metrics(), _config(), {}, "CONDITIONAL")
            )
            lake.record_resource_allocation_snapshot(snapshot, timestamp=123)
            row = lake.conn.execute(
                "SELECT allocation_state, floor_total_ran, floor_total_ai, reinforcement_ai, per_ue_floor_json, per_ue_allocation_json, per_ue_floor_violation_count, per_ue_application_status FROM resource_allocation_history"
            ).fetchone()
            self.assertEqual(row[0], "CONDITIONAL")
            self.assertGreater(row[1], 0.0)
            self.assertGreater(row[2], 0.0)
            self.assertEqual(row[3], 0.0)
            self.assertEqual(len(json.loads(row[4])), 20)
            self.assertEqual(len(json.loads(row[5])), 20)
            self.assertEqual(row[6], 0)
            self.assertEqual(row[7], "computed")


if __name__ == "__main__":
    unittest.main()
