import json
import csv
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.run_tasam_online_controlled import _economic_row_is_eligible
from src.rapp_data_lake import DataLake
from src.rapp_orchestrator import (
    RappResourceOptimizer,
    _native_bundle_cell_power_percent,
    causal_energy_saving_fraction,
)
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
    def test_native_v3_power_reaches_empty_du_without_policy_rows(self):
        power = {2: 90, 3: 90, 4: 90}
        self.assertEqual(
            _native_bundle_cell_power_percent(
                power, 2, 90, economic_power_command=True,
                bootstrap_pending=False, cell_policies=[], sleep_transition=None,
            ),
            90,
        )
        self.assertEqual(
            _native_bundle_cell_power_percent(
                power, 2, 90, economic_power_command=False,
                bootstrap_pending=False, cell_policies=[], sleep_transition=None,
            ),
            100,
        )

    def test_native_control_intent_is_persisted_before_transport(self):
        decision = {}
        contract = {"correlation_id": "economic:test:1"}
        bundle = {
            "sequence": 7,
            "sim_time_s": 12.5,
            "ttl_ms": 12000,
            "power_percent_by_cell": {"2": 70, "3": 75, "4": 80},
            "cells": [
                {"cell_id": 2, "tx_power_percent": 70},
                {"cell_id": 3, "tx_power_percent": 75},
                {"cell_id": 4, "tx_power_percent": 80},
            ],
        }

        persisted = RappResourceOptimizer._persist_native_control_intent(
            decision, contract, bundle
        )

        self.assertEqual(decision["tasam_control_intent_status"], "intent_persisted")
        self.assertEqual(contract["native_control_intent_status"], "intent_persisted")
        self.assertEqual(contract["native_control_intent"]["cell_ids"], [2, 3, 4])
        self.assertEqual(persisted["power_percent_by_cell"], {"2": 70, "3": 75, "4": 80})
        bundle["cells"][0]["tx_power_percent"] = 100
        self.assertEqual(contract["native_control_bundle"]["cells"][0]["tx_power_percent"], 70)

    def test_energy_reward_uses_native_pre_action_not_synthetic_candidate(self):
        observed = causal_energy_saving_fraction(430.0, 383.5)
        synthetic = causal_energy_saving_fraction(197.5, 383.5)
        self.assertGreater(observed, 0.0)
        self.assertLess(synthetic, 0.0)

    def test_native_pre_action_and_symbol_state_require_all_three_dus(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake = DataLake(Path(tmp) / "native.db")
            for cell in (2, 3, 4):
                lake.conn.execute(
                    """INSERT INTO tasam_control_observations
                       (source_path, sim_time_s, cell_id, transaction_id,
                        power_transaction_id, active_ues, tx_power_percent,
                        tx_power_dbm, observation_kind, native_allocated_dl_symbols,
                        native_dl_symbol_capacity, power_lease_fresh, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("native.csv", 10.0, cell, cell, cell, 20, 75.0,
                     10.0, "power_readback", 700, 1000, 1, 1),
                )
                lake.conn.execute(
                    """INSERT INTO tasam_control_observations
                       (source_path, sim_time_s, cell_id, transaction_id,
                        power_transaction_id, active_ues, tx_power_percent,
                        tx_power_dbm, observation_kind, native_allocated_dl_symbols,
                        native_dl_symbol_capacity, power_lease_fresh, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("native.csv", 10.0, cell, cell, cell, 20, 75.0,
                     10.0, "state_snapshot", 700, 1000, 1, 1),
                )
            lake.conn.commit()
            self.assertTrue(lake.latest_native_power_state()["valid"])
            self.assertTrue(lake.latest_native_symbol_state()["valid"])
            lake.conn.execute(
                "UPDATE tasam_control_observations "
                "SET native_allocated_dl_symbols=0, native_dl_symbol_capacity=0"
            )
            lake.conn.commit()
            # A complete zero-capacity snapshot is still authoritative native
            # state before the first scheduler policy materializes.  It must
            # not deadlock the first TA-SAM bundle; no synthetic budget is
            # inferred from it.
            self.assertTrue(lake.latest_native_symbol_state()["valid"])
            lake.conn.execute(
                "DELETE FROM tasam_control_observations WHERE cell_id=4"
            )
            lake.conn.commit()
            self.assertFalse(lake.latest_native_power_state()["valid"])

    def test_current_native_replay_accepts_empty_du_allocation_snapshot(self):
        row = _row(
            economic_action_contract="economic_action_v3_per_du_sleep",
            economic_application_status="applied",
        )
        row["economic_action"]["contract"] = "economic_action_v3_per_du_sleep"
        row["decision"] = {
            "tasam_fallback_used": False,
            "tasam_checkpoint_valid": True,
        }
        row["economic_action"]["native_observation"] = {
            "evidence_version": "v5",
            "transaction_id": 7,
            "native_control_sequence": 7,
            "action_correlation_id": "economic:test",
            "cell_ids": [2, 3, 4],
            "observations": [
                {
                    "cell_id": 2,
                    "power_readback_sim_time_s": 2.0,
                    "policy_active_sim_time_s": 2.0,
                    "tx_power_percent": 35.0,
                    "native_allocation_fraction": None,
                    "active_ues": 0,
                    "active_dl_symbol_capacity": 0,
                },
                {
                    "cell_id": 3,
                    "power_readback_sim_time_s": 2.0,
                    "policy_active_sim_time_s": 2.0,
                    "tx_power_percent": 75.0,
                    "native_allocation_fraction": 0.5,
                    "active_ues": 1,
                    "active_dl_symbol_capacity": 10,
                },
                {
                    "cell_id": 4,
                    "power_readback_sim_time_s": 2.0,
                    "policy_active_sim_time_s": 2.0,
                    "tx_power_percent": 30.0,
                    "native_allocation_fraction": None,
                    "active_ues": 0,
                    "active_dl_symbol_capacity": 0,
                },
            ],
        }
        with patch.dict(
            "os.environ",
            {"GREENRAN_NATIVE_EVIDENCE_VERSION": "v5"},
            clear=False,
        ):
            self.assertTrue(_economic_row_is_eligible(row, current_native_only=True))

    def test_current_native_replay_accepts_v6_sequence_on_action(self):
        row = _row(
            economic_action_contract="economic_action_v3_per_du_sleep",
            economic_application_status="applied",
        )
        row["economic_action"]["contract"] = "economic_action_v3_per_du_sleep"
        row["economic_action"]["native_control_sequence"] = 8
        row["decision"] = {
            "tasam_fallback_used": False,
            "tasam_checkpoint_valid": True,
        }
        row["economic_action"]["native_observation"] = {
            "evidence_version": "v6",
            "transaction_id": 8,
            "action_correlation_id": "economic:v6",
            "cell_ids": [2, 3, 4],
            "observations": [
                {
                    "cell_id": cell_id,
                    "native_control_sequence": 8,
                    "power_readback_sim_time_s": 2.0,
                    "policy_active_sim_time_s": 2.0,
                    "tx_power_percent": 75.0,
                    "native_allocation_fraction": None,
                    "active_ues": 0,
                    "active_dl_symbol_capacity": 0,
                }
                for cell_id in (2, 3, 4)
            ],
        }
        with patch.dict(
            "os.environ",
            {"GREENRAN_NATIVE_EVIDENCE_VERSION": "v6"},
            clear=False,
        ):
            self.assertTrue(_economic_row_is_eligible(row, current_native_only=True))

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
