import json
import csv
import tempfile
import unittest
from pathlib import Path

from scripts.run_tasam_online_controlled import _economic_row_is_eligible
from src.rapp_data_lake import DataLake
from src.tasam_learning_meter import build_learning_meter


def _row(**overrides):
    action = {
        "contract": "applied_action_v2",
        "application_status": "applied",
        "armd_safety_level": "CLEAR",
        "tasam_operating_permission": True,
        "economic_execution_mode": "economic",
        "actuation_confirmed": True,
        "actuation_confirmation_source": "correlated_energy_observation",
        "live_candidate": {"power_w": 100.0, "total_allocation": 1.0},
        "applied": {"power_w": 90.0, "total_allocation": 0.8},
    }
    value = {
        "economic_action_contract": "applied_action_v2",
        "economic_application_status": "applied",
        "economic_transition_eligible": True,
        "economic_training_eligible": True,
        "tasam_checkpoint_valid": True,
        "tasam_fallback_used": False,
        "tasam_action_applied": True,
        "economic_action": action,
        "judge_feedback": {
            "pdcp_loss_coverage": {"valid": True},
            "topology_valid": True,
            "tasam_action_applied": True,
            "economic_action_alignment_valid": True,
        },
        "armd_safety_level": "CLEAR",
        "tasam_operating_permission": True,
    }
    value.update(overrides)
    return value


class TestTasamEconomicEvidenceContract(unittest.TestCase):
    def test_explicit_unconfirmed_action_is_rejected(self):
        row = _row()
        row["economic_action"]["actuation_confirmed"] = False
        self.assertFalse(_economic_row_is_eligible(row))

    def test_diagnostic_or_permission_false_is_rejected(self):
        diagnostic = _row()
        diagnostic["economic_action"]["economic_execution_mode"] = "diagnostic"
        self.assertFalse(_economic_row_is_eligible(diagnostic))
        permission = _row()
        permission["economic_action"]["tasam_operating_permission"] = False
        self.assertFalse(_economic_row_is_eligible(permission))

    def test_learning_meter_does_not_use_shadow_power_as_treatment(self):
        row = {
            "economic_application_status": "applied",
            "tasam_actuation_applied": 1,
            "economic_training_eligible": 1,
            "economic_promotion_eligible": 1,
            "tasam_checkpoint_valid": 1,
            "tasam_evidence_valid": 1,
            "tasam_fallback_used": 0,
            "economic_safety_isolated": 0,
            "topology_valid": 1,
            "armd_safety_level": "CLEAR",
            "tasam_operating_permission": 1,
            "economic_execution_mode": "economic",
            "actuation_confirmed": 1,
            "realized_energy_saving_fraction": 0.1,
            "realized_allocation_saving_fraction": 0.1,
            "tasam_sla_penalty": 0.0,
            "economic_action_json": json.dumps({
                "actuation_confirmed": True,
                "live_candidate": {"power_w": 100.0, "total_allocation": 1.0},
                "applied": {"power_w": 90.0, "total_allocation": 0.9},
            }),
        }
        meter = build_learning_meter([row], {"ml_enabled": True, "retrain_enabled": True, "updates_completed": 1})
        self.assertEqual(meter["comparison"]["rows"][0]["treatment_power_w"], 90.0)

    def test_energy_command_confirmation_columns_are_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            lake = DataLake(str(Path(directory) / "evidence.db"))
            lake.record_energy_command(
                "POWER_DOWN_ECO", power_percent=25, ru_count=1, mmwave_count=1,
                action_correlation_id="corr-1", application_status="pending_confirmation",
            )
            lake.update_energy_command_application(
                "corr-1", "verified", actuation_confirmed=True,
                confirmation_source="correlated_energy_observation",
                observed_power_percent=25.0, observed_power_w=90.0,
                observed_ru_count=1, observed_mmwave_count=1,
                confirmation_decision_id=2,
            )
            row = lake.conn.execute(
                "SELECT command_sent, actuation_confirmed, "
                "actuation_confirmation_source, confirmation_decision_id "
                "FROM energy_commands WHERE action_correlation_id='corr-1'"
            ).fetchone()
            self.assertEqual(tuple(row), (1, 1, "correlated_energy_observation", 2))
            lake.close()

    def test_delayed_feedback_preserves_static_checkpoint_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            lake = DataLake(str(Path(directory) / "evidence.db"))
            decision_id = lake.record_decision({
                "energy_saver": "ALLOWED",
                "tasam_source": "checkpoint",
                "tasam_valid": True,
                "tasam_checkpoint_valid": True,
                "tasam_fallback_used": False,
                "tasam_evidence_valid": True,
                "economic_application_status": "pending_confirmation",
                "economic_action_contract": "applied_action_v2",
            })
            self.assertIsNotNone(decision_id)
            lake.update_decision_economic_outcome(
                decision_id,
                {
                    "economic_application_status": "applied",
                    "actuation_confirmed": True,
                    "tasam_action_applied": True,
                    "economic_transition_eligible": True,
                    "economic_action": {
                        "contract": "applied_action_v2",
                        "application_status": "applied",
                        "actuation_confirmed": True,
                        "native_observation": {"valid": True},
                        "observed_power_percent": 45.0,
                        "observed_power_w": 566.0,
                        "observed_mmwave_count": 3,
                    },
                },
            )
            row = lake.conn.execute(
                "SELECT tasam_checkpoint_valid, tasam_fallback_used, "
                "tasam_evidence_valid FROM decisions_history WHERE id=?",
                (decision_id,),
            ).fetchone()
            self.assertEqual(tuple(row), (1, 0, 1))
            lake.close()

    def test_native_v5_ingest_is_incremental_and_requires_correlation(self):
        header = [
            "Time", "CellId", "SchedulerTransactionId", "PowerTransactionId",
            "ActiveUes", "TxPowerPercent", "TxPowerDbm", "NominalTxPowerDbm",
            "ObservationKind", "PolicyActive", "PolicyExpiryTime",
            "SourceGeneration", "AssociationEpoch", "ActiveDlSymbols",
            "ActiveDlSymbolCapacity", "CampaignId", "EvidenceVersion",
            "CampaignGeneration", "DecisionId", "ActionCorrelationId",
            "NativeControlSequence", "NativeAllocationSource",
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "evidence.db"
            trace = root / "TasamControlObservations.csv"
            rows = []
            for cell in (2, 3, 4):
                common = [
                    "1.0", str(cell), "7", "7", "20", "50", "20", "23",
                    "power_readback", "1", "6.0", "generation-1", "epoch-1",
                    "10", "20", "campaign-1", "v5", "generation-1", "4",
                    "corr-7", "7", "tasam_native_aggregate_v1",
                ]
                rows.append(common)
                policy = list(common)
                policy[8] = "state_snapshot"
                rows.append(policy)
            with trace.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(rows)

            lake = DataLake(str(database))
            self.assertEqual(lake.ingest_native_control_observations(trace), 6)
            self.assertEqual(lake.ingest_native_control_observations(trace), 0)
            confirmation = lake.confirm_native_control_observation(
                7, 50, (2, 3, 4), sim_time_s=1.0, ttl_s=2.0,
                source_generation="generation-1", require_evidence_version="v5",
                campaign_id="campaign-1", action_correlation_id="corr-7",
            )
            self.assertTrue(confirmation["valid"], confirmation)
            wrong = lake.confirm_native_control_observation(
                7, 50, (2, 3, 4), sim_time_s=1.0, ttl_s=2.0,
                source_generation="generation-1", require_evidence_version="v5",
                campaign_id="campaign-1", action_correlation_id="wrong-correlation",
            )
            self.assertFalse(wrong["valid"])
            lake.close()


if __name__ == "__main__":
    unittest.main()
