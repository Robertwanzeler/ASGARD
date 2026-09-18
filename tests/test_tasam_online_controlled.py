import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.run_tasam_online_controlled import (
    advance_rollout,
    build_sqlite_economic_replay,
    build_replay,
    build_persistent_bank_replay,
    count_decisions,
    count_snapshots,
    migrate_legacy_candidate_shadow_state,
    persist_experience_bank,
    normalize_state_contract,
    publish_manifests,
    publish_learning_meter,
    _economic_candidate_gate,
    _candidate_shadow_gate,
    _checkpoint_temporal_dim,
    prune_candidate_artifacts,
    runtime_guard,
)
from rapp_data_lake import DataLake
from drlexp.src.drl.ta_sam_marl_sac import POWER_LEVELS, _safe_power_target, load_marl_transition_trace


class TasamOnlineControlledTests(unittest.TestCase):
    def test_repeated_online_polling_closes_sqlite_connections(self):
        """Long wall-clock polling must not exhaust the controller FD limit."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "polling.db"
            with sqlite3.connect(database) as conn:
                conn.execute("create table marl_global_state_history (timestamp integer)")
                conn.execute("create table decisions_history (id integer)")
                conn.execute("create table resource_allocation_history ("
                             "floor_feasible integer, per_ue_floor_violation_count integer, "
                             "floor_total_ran real, floor_total_ai real, usable_budget real, timestamp integer)")
                conn.execute("create table marl_shadow_comparison_history (score_delta real, timestamp integer)")

            args = SimpleNamespace(db=database, state_dir=root / "adaptation_online")
            state = {"min_economic_transitions": 180, "active_checkpoint": ""}
            before = len(list(Path("/proc/self/fd").iterdir()))
            for _ in range(250):
                count_snapshots(database)
                count_decisions(database)
                runtime_guard(database, window=3)
                publish_learning_meter(args, state)
            after = len(list(Path("/proc/self/fd").iterdir()))
            self.assertLessEqual(after, before + 3)

    @staticmethod
    def _economic_sqlite_fixture(
        database: Path, *, complete_observed_state: bool = True
    ) -> tuple[int, int]:
        """Build a minimal durable v1 row and its canonical MARL states."""
        source_timestamp, observed_timestamp = 100, 101
        action = {
            "contract": "applied_action_v2",
            "application_status": "applied",
            "live_candidate": {"power_w": 120.0, "total_allocation": 0.8},
            "applied": {
                "power_percent": 50.0, "power_w": 100.0,
                "ran_allocation": 0.30, "ai_allocation": 0.30,
                "total_allocation": 0.60, "usable_budget": 1.0,
                "du_actions": [[0.2, 0.3, 0.5]] * 3,
            },
            "command_sent": True,
            "actuation_confirmed": True,
            "actuation_confirmation_source": "ns3_native_observation",
            "native_control_sequence": 7,
            "native_observation": {
                "evidence_version": "v2",
                "transaction_id": 7,
                "power_transaction_id": 7,
                "cells": [1, 2, 3],
                "observed_power_percent": 50.0,
            },
            "tasam_operating_permission": True,
            "armd_safety_level": "CLEAR",
            "armd_role": "advisory",
            "topology_valid": True,
            "pdcp_loss_coverage": {"valid": True},
        }
        feedback = {
            "feedback_status": "observed",
            "economic_action": action,
            "economic_action_alignment_valid": True,
            "economic_transition_eligible": True,
            "economic_training_eligible": True,
            "economic_promotion_eligible": True,
            "economic_application_status": "applied",
            "tasam_action_applied": True,
            "armd_safety_level": "CLEAR",
            "topology_valid": True,
            "pdcp_loss_coverage": {"valid": True},
            "tasam_online_reward": 0.4,
            "tasam_observed_verdict": "ALLOWED",
            "tasam_predicted_verdict": "ALLOWED",
            # This must not be copied into the trainer input.  It models the
            # formerly huge full decision blob kept only for audit in v1.
            "economic_transition": {"audit_blob": "x" * 100_000},
        }
        decision = {
            "decision_id": 7,
            "timestamp": source_timestamp,
            "tasam_checkpoint_valid": True,
            "tasam_fallback_used": False,
            "economic_action": action,
            "resource_allocation": {
                "usable_budget": 1.0, "r_ran": 0.4, "r_ai": 0.4,
                "d_ran": 0.4, "d_ai": 0.4,
                "floor_total_ran": 0.2, "floor_total_ai": 0.2,
            },
        }
        raw = json.dumps({
            "schema": "greenran.tasam.economic_transition.v1",
            "decision": decision,
            "next_decision": {"timestamp": observed_timestamp},
            "judge_feedback": feedback,
        })
        with sqlite3.connect(database) as conn:
            conn.execute(
                """
                CREATE TABLE tasam_economic_transition_history (
                    decision_id integer primary key, decision_timestamp integer,
                    observed_timestamp integer, source_metric_snapshot_id integer,
                    observed_metric_snapshot_id integer, economic_action_contract text,
                    economic_application_status text, economic_transition_eligible integer,
                    economic_training_eligible integer, economic_promotion_eligible integer,
                    realized_energy_saving_fraction real,
                    realized_allocation_saving_fraction real, tasam_online_reward real,
                    tasam_energy_reward real, tasam_allocation_reward real,
                    tasam_sla_penalty real, calibration_version text,
                    transition_json text, transition_sha256 text
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE marl_global_state_history (
                    timestamp integer primary key, state_vector_json text, usable_budget real
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE marl_du_state_history (
                    timestamp integer, du_id text, role text, primary_slice text,
                    slice_mix_json text, state_vector_json text
                )
                """
            )
            conn.execute(
                "insert into tasam_economic_transition_history values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (7, source_timestamp, observed_timestamp, 1, 2,
                 "applied_action_v2", "applied", 1, 1, 1, 0.1, 0.0, 0.4,
                 0.1, 0.0, 0.0, "sim_native_v2", raw, "digest"),
            )
            for timestamp in (source_timestamp, observed_timestamp):
                conn.execute(
                    "insert into marl_global_state_history values (?, ?, ?)",
                    (timestamp, json.dumps([float(timestamp)] * 13), 1.0),
                )
                ids = ("du_camera_edge", "du_sensor_mixed", "du_vehicle_edge")
                if timestamp == observed_timestamp and not complete_observed_state:
                    ids = ids[:-1]
                for index, du_id in enumerate(ids):
                    conn.execute(
                        "insert into marl_du_state_history values (?, ?, ?, ?, ?, ?)",
                        (timestamp, du_id, "role", ("eMBB", "mMTC", "URLLC")[index],
                         json.dumps({"eMBB": 0.3, "mMTC": 0.3, "URLLC": 0.4}),
                         json.dumps([float(index)] * 13)),
                    )
        return source_timestamp, observed_timestamp

    def test_sqlite_economic_replay_rehydrates_exact_marl_states_compactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "lake.db"
            self._economic_sqlite_fixture(database)
            replay = root / "replay.jsonl"
            result = build_sqlite_economic_replay(database, replay, seed=47, max_rows=64)
            records = load_marl_transition_trace(replay)
            self.assertEqual(result["rehydrated_rows"], 1)
            self.assertEqual(result["eligible_rows"], 1)
            self.assertEqual(len(records), 1)
            self.assertEqual(len(records[0].du_states), 3)
            self.assertTrue(all(len(state) == 13 for state in records[0].du_states))
            self.assertEqual(len(records[0].global_state), 13)
            self.assertTrue(all(len(state) == 13 for state in records[0].next_du_states))
            self.assertEqual(len(records[0].next_global_state), 13)
            self.assertLess(result["temporary_bytes"], 20_000)
            self.assertNotIn("audit_blob", replay.read_text(encoding="utf-8"))

    def test_sqlite_economic_replay_rejects_incomplete_exact_next_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "lake.db"
            self._economic_sqlite_fixture(database, complete_observed_state=False)
            replay = root / "replay.jsonl"
            result = build_sqlite_economic_replay(database, replay, seed=47, max_rows=64)
            self.assertEqual(result["eligible_rows"], 0)
            self.assertEqual(result["rehydrated_rows"], 0)
            self.assertEqual(result["rejected_reasons"], {"observed_du_count": 1})
            self.assertEqual(replay.read_text(encoding="utf-8"), "")

    def test_data_lake_persists_compact_economic_transition_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "lake.db"
            lake = DataLake(str(database))
            try:
                action = {
                    "contract": "applied_action_v2",
                    "application_status": "applied",
                    "applied": {
                        "power_percent": 50.0, "ran_allocation": 0.3,
                        "ai_allocation": 0.3, "usable_budget": 1.0,
                        "du_actions": [[0.2, 0.3, 0.5]] * 3,
                    },
                    "live_candidate": {"power_w": 100.0, "total_allocation": 0.7},
                }
                marl_state = {
                    "topology_id": "greenran_fixed_marl_v1",
                    "global_state": {"state_vector": [0.0] * 13, "usable_budget": 1.0},
                    "du_states": [
                        {
                            "du_id": du_id, "role": "role", "primary_slice": slice_id,
                            "slice_mix": {"eMBB": 0.3, "mMTC": 0.3, "URLLC": 0.4},
                            "state_vector": [float(index)] * 13,
                        }
                        for index, (du_id, slice_id) in enumerate((
                            ("du_camera_edge", "eMBB"),
                            ("du_sensor_mixed", "mMTC"),
                            ("du_vehicle_edge", "URLLC"),
                        ))
                    ],
                }
                decision = {
                    "decision_id": 9, "timestamp": 100, "metric_snapshot_id": 10,
                    "tasam_checkpoint_valid": True, "tasam_fallback_used": False,
                    "energy_model_version": "sim_native_v2_experimental",
                    "economic_action": action,
                    "resource_allocation": {
                        "usable_budget": 1.0, "r_ran": 0.4, "r_ai": 0.4,
                        "article_marl_state": marl_state,
                    },
                    "large_audit_only_field": "x" * 100_000,
                }
                feedback = {
                    "economic_action": action, "economic_application_status": "applied",
                    "economic_transition_eligible": True, "economic_training_eligible": True,
                    "economic_promotion_eligible": True, "tasam_online_reward": 0.2,
                    "economic_transition": {"large_audit_only_field": "x" * 100_000},
                }
                self.assertTrue(lake.record_economic_transition(
                    9,
                    {
                        "decision": decision,
                        "next_decision": {
                            "timestamp": 101, "metric_snapshot_id": 11,
                            "resource_allocation": {
                                "usable_budget": 1.0, "article_marl_state": marl_state,
                            },
                        },
                    },
                    feedback,
                    observed_metric_id=11,
                ))
                row = lake.conn.execute(
                    "select replay_schema, replay_compact_bytes, calibration_version, transition_json from tasam_economic_transition_history where decision_id=9"
                ).fetchone()
                self.assertEqual(row[0], "greenran.tasam.economic_transition.v2")
                self.assertLess(int(row[1]), 10_000)
                self.assertEqual(row[2], "sim_native_v2_experimental")
                payload = json.loads(row[3])
                self.assertNotIn("large_audit_only_field", row[2])
                self.assertIn("economic_action", payload)
                self.assertEqual(
                    lake.conn.execute(
                        "select count(*) from marl_du_state_history where timestamp in (100, 101)"
                    ).fetchone()[0],
                    6,
                )
                columns = {entry[1] for entry in lake.conn.execute(
                    "pragma table_info(tasam_economic_transition_history)"
                )}
                self.assertTrue({"replay_schema", "replay_compact_bytes"}.issubset(columns))
            finally:
                lake.close()

    @staticmethod
    def _valid_transition(transition_id: int) -> dict:
        return {
            "topology_id": "seed47-3du",
            "timestamp": float(transition_id),
            "next_timestamp": float(transition_id) + 1.0,
            "collection_quality": {
                "valid_for_training": True,
                "collector_mode": "pdcp_real",
                "pdcp_real": True,
                "proxy_latency_sample_count": 0,
                "metric_alignment_valid": True,
                "sim_reset": False,
                "current_metric_skew_s": 0,
                "next_metric_skew_s": 0,
            },
            "next_metrics": {"cvar_per_ue_us": 1.0},
            "judge_feedback": {
                "tasam_category_error": True,
                "tasam_predicted_verdict": "CONDITIONAL",
                "tasam_observed_verdict": "ALLOWED",
            },
            "tasam_training_category_credit": -2.0,
            "tasam_training_reward": -2.0,
        }

    def test_candidate_training_uses_checkpoint_temporal_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp)
            (checkpoint / "tasam_marl_checkpoint_meta.json").write_text(
                json.dumps({"temporal_dim": 0}), encoding="utf-8"
            )
            self.assertEqual(_checkpoint_temporal_dim(checkpoint), 0)
            (checkpoint / "tasam_marl_checkpoint_meta.json").write_text(
                json.dumps({"temporal_dim": 10}), encoding="utf-8"
            )
            self.assertEqual(_checkpoint_temporal_dim(checkpoint), 10)

    def test_experience_bank_persists_rewards_and_deduplicates_transitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl"
            rows = [self._valid_transition(1), self._valid_transition(2)]
            source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            bank = root / "experience_bank.jsonl"

            first = persist_experience_bank(bank, source, "round_a")
            second = persist_experience_bank(bank, source, "round_a")

            persisted = [json.loads(line) for line in bank.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(first["rows_added"], 2)
            self.assertEqual(second["rows_added"], 0)
            self.assertEqual(len(persisted), 2)
            self.assertEqual(persisted[0]["tasam_training_reward"], -2.0)
            self.assertTrue(persisted[0]["tasam_experience_id"])
            self.assertEqual(
                json.loads((root / "experience_bank.manifest.json").read_text(encoding="utf-8"))["unique_transitions"],
                2,
            )

    def test_runtime_guard_does_not_rollback_on_infeasible_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "runtime.db"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "create table resource_allocation_history ("
                    "floor_feasible integer, per_ue_floor_violation_count integer, "
                    "floor_total_ran real, floor_total_ai real, usable_budget real, timestamp integer)"
                )
                conn.executemany(
                    "insert into resource_allocation_history values (?, ?, ?, ?, ?, ?)",
                    [(0, 33, 0.58, 0.36, 0.83, index) for index in range(3)],
                )

            guard = runtime_guard(db, window=3)

            self.assertFalse(guard["rollback"])
            self.assertEqual(guard["floor_violations"], 0)
            self.assertEqual(guard["infeasible_floor_constraints"], 3)
            self.assertIn("inviáveis", guard["reason"])

    def test_runtime_guard_ignores_counterfactual_shadow_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "shadow_only.db"
            with sqlite3.connect(db) as conn:
                conn.execute("create table resource_allocation_history ("
                             "floor_feasible integer, per_ue_floor_violation_count integer, "
                             "floor_total_ran real, floor_total_ai real, usable_budget real, timestamp integer)")
                conn.execute("insert into resource_allocation_history values (1, 0, 0.2, 0.2, 1.0, 1)")
                conn.execute("create table marl_shadow_comparison_history (score_delta real, timestamp integer)")
                for index in range(3):
                    conn.execute("insert into marl_shadow_comparison_history values (-1.0, ?)", (index,))
            guard = runtime_guard(db, window=3)
            self.assertFalse(guard["rollback"])
            self.assertIsNone(guard["avg_score_delta"])

    def test_runtime_guard_rolls_back_on_feasible_floor_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "runtime.db"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "create table resource_allocation_history ("
                    "floor_feasible integer, per_ue_floor_violation_count integer, "
                    "floor_total_ran real, floor_total_ai real, usable_budget real, timestamp integer)"
                )
                conn.executemany(
                    "insert into resource_allocation_history values (?, ?, ?, ?, ?, ?)",
                    [(0, 1, 0.40, 0.30, 0.90, index) for index in range(3)],
                )

            guard = runtime_guard(db, window=3)

            self.assertTrue(guard["rollback"])
            self.assertEqual(guard["floor_violations"], 3)
            self.assertEqual(guard["infeasible_floor_constraints"], 0)

    def test_persistent_bank_replay_is_capped_without_loading_all_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bank = root / "experience_bank.jsonl"
            rows = []
            for index in range(30):
                row = self._valid_transition(index)
                observed = "CONDITIONAL" if index < 20 else "ALLOWED"
                row["judge_feedback"]["tasam_observed_verdict"] = observed
                row["judge_feedback"]["tasam_category_error"] = index % 2 == 0
                row["tasam_power_percent"] = 100.0
                if 20 <= index < 23:
                    row["judge_feedback"]["tasam_error_components"] = {
                        "ran_completion": 0.80,
                        "ai_completion": 0.60,
                        "underallocation_penalty": 0.40,
                        "completion_shortfall_penalty": 0.40,
                        "service_error": 0.0,
                        "tail_latency_error": 0.0,
                    }
                rows.append(row)
            bank.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            result = build_persistent_bank_replay(
                bank, root / "replay.jsonl", seed=47, max_rows=10, category_error_repeat=1
            )
            replay_rows = [
                json.loads(line) for line in (root / "replay.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(result["unique_available"], 30)
            self.assertEqual(result["total"], 10)
            self.assertEqual(len(replay_rows), 10)
            self.assertEqual(result["replay_quotas"]["conditional_transition_quota"], 4)
            self.assertEqual(result["replay_quotas"]["underallocation_quota"], 3)
            self.assertEqual(result["replay_quotas"]["high_power_safe_quota"], 2)
            self.assertEqual(result["replay_quotas"]["correct_recent_quota"], 1)

    def test_online_only_replay_can_be_built_without_historical_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recent = root / "recent.jsonl"
            recent.write_text(
                json.dumps(
                    {
                        "collection_quality": {
                            "valid_for_training": True,
                            "collector_mode": "pdcp_real",
                            "pdcp_real": True,
                            "proxy_latency_sample_count": 0,
                            "metric_alignment_valid": True,
                            "sim_reset": False,
                            "current_metric_skew_s": 0,
                            "next_metric_skew_s": 0,
                        },
                        "next_metrics": {"cvar_per_ue_us": 1.0},
                        "judge_feedback": {"reward": 0.5},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = build_replay(
                root / "historical-does-not-exist.jsonl",
                recent,
                root / "replay.jsonl",
                seed=45,
                max_rows=10,
            )
            self.assertEqual(result["historical_valid"], 0)
            self.assertEqual(result["recent_valid"], 1)
            self.assertEqual(result["total"], 1)

    def test_category_error_replay_keeps_all_rows_and_repeats_errors_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recent = root / "recent.jsonl"
            rows = []
            for index, category_error in enumerate((True, False, True), start=1):
                rows.append({
                    "transition_id": index,
                    "collection_quality": {
                        "valid_for_training": True,
                        "collector_mode": "pdcp_real",
                        "pdcp_real": True,
                        "proxy_latency_sample_count": 0,
                        "metric_alignment_valid": True,
                        "sim_reset": False,
                        "current_metric_skew_s": 0,
                        "next_metric_skew_s": 0,
                    },
                    "next_metrics": {"cvar_per_ue_us": 1.0},
                    "judge_feedback": {"tasam_category_error": category_error},
                })
            recent.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            first = build_replay(
                root / "missing-history.jsonl",
                recent,
                root / "replay-first.jsonl",
                seed=47,
                max_rows=5,
                category_error_repeat=1,
            )
            second = build_replay(
                root / "missing-history.jsonl",
                recent,
                root / "replay-second.jsonl",
                seed=47,
                max_rows=5,
                category_error_repeat=1,
            )
            self.assertEqual(first, second)
            self.assertEqual(first["total"], 5)
            self.assertEqual(first["category_error_in_base"], 2)
            self.assertEqual(first["category_error_repeated"], 2)
            self.assertEqual(
                (root / "replay-first.jsonl").read_text(encoding="utf-8"),
                (root / "replay-second.jsonl").read_text(encoding="utf-8"),
            )

    def test_capped_replay_reserves_conditional_states_and_keeps_ten_percent_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recent = root / "recent.jsonl"
            rows = []
            for index in range(20):
                error = index < 14
                predicted = "BLOCKED" if index < 8 else ("CONDITIONAL" if error else "ALLOWED")
                observed = "ALLOWED" if index < 8 else ("CONDITIONAL" if error else "ALLOWED")
                rows.append({
                    "transition_id": index,
                    "collection_quality": {
                        "valid_for_training": True, "collector_mode": "pdcp_real", "pdcp_real": True,
                        "proxy_latency_sample_count": 0, "metric_alignment_valid": True,
                        "sim_reset": False, "current_metric_skew_s": 0, "next_metric_skew_s": 0,
                    },
                    "next_metrics": {"cvar_per_ue_us": 1.0},
                    "judge_feedback": {
                        "tasam_category_error": error,
                        "tasam_predicted_verdict": predicted,
                        "tasam_observed_verdict": observed,
                    },
                    "tasam_power_percent": 100.0,
                    "tasam_error_components": ({
                        "ran_completion": 0.80,
                        "ai_completion": 0.60,
                        "underallocation_penalty": 0.40,
                        "completion_shortfall_penalty": 0.40,
                    } if 14 <= index < 16 else {}),
                })
            recent.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            result = build_replay(
                root / "missing-history.jsonl", recent, root / "replay.jsonl",
                seed=47, max_rows=10, category_error_repeat=1,
            )
            self.assertEqual(result["total"], 10)
            self.assertEqual(result["category_error_available"], 14)
            self.assertEqual(result["category_error_selected_unique"], 4)
            self.assertEqual(result["category_error_repeated"], 0)
            self.assertEqual(result["category_error_in_replay"], 4)
            self.assertEqual(result["prioritization"], "energy_focused_40_30_20_10_v1")
            quotas = result["replay_quotas"]
            self.assertEqual(quotas["conditional_transition_selected_unique"], 4)
            self.assertEqual(quotas["underallocation_selected_unique"], 2)
            self.assertEqual(quotas["underallocation_repeated"], 1)
            self.assertEqual(quotas["high_power_safe_selected_unique"], 2)
            self.assertEqual(quotas["correct_recent_selected_unique"], 1)

    def test_legacy_replay_row_is_augmented_to_active_checkpoint_contract(self):
        row = {
            "scenario_stage": "camera_blocked",
            "next_scenario_stage": "allowed_recovery",
            "decision": {"decision": "BLOCKED"},
            "global_state": {"state_vector": [0.0] * 10},
            "next_global_state": {"state_vector": [0.0] * 10},
            "du_states": [{"state_vector": [0.0] * 10} for _ in range(3)],
            "next_du_states": [{"state_vector": [0.0] * 10} for _ in range(3)],
        }

        self.assertTrue(normalize_state_contract(row, 13, 13))
        self.assertEqual(row["global_state"]["state_vector"][-3:], [0.0, 0.0, 1.0])
        self.assertEqual(row["next_global_state"]["state_vector"][-3:], [1.0, 0.0, 0.0])
        self.assertTrue(all(len(du["state_vector"]) == 13 for du in row["du_states"]))
        self.assertTrue(all(len(du["state_vector"]) == 13 for du in row["next_du_states"]))

    def test_power_target_comes_from_observed_real_feedback(self):
        def payload(observed, **components):
            return {
                "judge_feedback": {
                    "feedback_status": "observed",
                    "tasam_observed_verdict": observed,
                    "tasam_error_components": components,
                }
            }

        self.assertEqual(_safe_power_target(payload("BLOCKED")), POWER_LEVELS.index(100.0))
        self.assertEqual(_safe_power_target(payload("CONDITIONAL")), POWER_LEVELS.index(60.0))
        self.assertEqual(
            _safe_power_target(
                payload(
                    "ALLOWED",
                    ran_completion=1.0,
                    ai_completion=1.0,
                    service_error=0.0,
                    tail_latency_error=0.0,
                    packet_loss_error=0.0,
                )
            ),
            0,
        )
        self.assertEqual(_safe_power_target({"judge_feedback": {"feedback_status": "missing"}}), -1)

    def test_inconsistent_13d_category_tail_is_repaired_from_decision_and_observation_stages(self):
        row = {
            "decision_stage_name": "vehicle_conditional",
            "observed_stage_name": "allowed_recovery",
            "global_state": {"state_vector": [0.0] * 10 + [1.0, 0.0, 0.0]},
            "next_global_state": {"state_vector": [0.0] * 10 + [0.0, 0.0, 1.0]},
            "du_states": [{"state_vector": [0.0] * 10 + [1.0, 0.0, 0.0]} for _ in range(3)],
            "next_du_states": [{"state_vector": [0.0] * 10 + [0.0, 0.0, 1.0]} for _ in range(3)],
        }

        self.assertTrue(normalize_state_contract(row, 13, 13))
        self.assertEqual(row["global_state"]["state_vector"][-3:], [0.0, 1.0, 0.0])
        self.assertEqual(row["next_global_state"]["state_vector"][-3:], [1.0, 0.0, 0.0])
        self.assertEqual(row["state_category_source"], {"current": "decision_stage_name", "next": "observed_stage_name"})
        self.assertFalse(row["state_category_consistent"])
        self.assertTrue(row["state_category_repaired"])
        self.assertTrue(row["stage_boundary_feedback"])

    def test_promotion_publishes_consistent_full_gate_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate_0001" / "tasam_selective"
            candidate.mkdir(parents=True)
            (candidate / "tasam_marl_actors.pt").write_bytes(b"checkpoint")
            (candidate / "tasam_marl_checkpoint_meta.json").write_text(
                json.dumps({"du_count": 3}), encoding="utf-8"
            )
            (candidate / "tasam_marl_summary.json").write_text(
                json.dumps({"completed_epochs": 3, "final_metrics": {}}),
                encoding="utf-8",
            )

            args = SimpleNamespace(
                promotion_window=30,
                rollout_manifest=root / "online_rollout.json",
                eval_manifest=root / "online_eval_manifest.json",
                control_gate=root / "online_control_gate.json",
                candidate_eval_manifest=root / "online_candidate_eval_manifest.json",
            )
            state = {
                "stage": "canary_50",
                "rollout_fraction": 0.50,
                "active_checkpoint": str(root / "old"),
                "candidate_checkpoint": str(candidate),
                "candidate_promoted": False,
                "stage_started_decisions": 100,
            }

            advance_rollout(args, state, decision_count=130, guard={"rollback": False})

            self.assertEqual(state["stage"], "full")
            self.assertEqual(state["rollout_fraction"], 1.0)
            self.assertEqual(state["active_checkpoint"], str(candidate))
            self.assertEqual(state["candidate_checkpoint"], "")

            rollout = json.loads(args.rollout_manifest.read_text(encoding="utf-8"))
            self.assertEqual(rollout["rollout"]["stage"], "full")
            self.assertEqual(rollout["rollout"]["checkpoint"], str(candidate))

            gate = json.loads(args.control_gate.read_text(encoding="utf-8"))
            self.assertEqual(gate["gate"]["status"], "full")
            self.assertTrue(gate["gate"]["allow_control_trial"])
            self.assertEqual(gate["gate"]["runtime_readiness"], "online_guarded")

    def test_economic_candidate_promotes_at_canary_ceiling_with_quantization_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / "active"
            candidate = root / "candidate_0032"
            for directory in (active, candidate):
                directory.mkdir(parents=True)
                (directory / "tasam_marl_actors.pt").write_bytes(b"checkpoint")
                (directory / "tasam_marl_checkpoint_meta.json").write_text(
                    json.dumps({
                        "du_count": 3,
                        "economic_action_contract": "applied_action_v2",
                        "total_budget_fraction_bounds": [0.0, 1.0],
                        "economic_replay": {
                            "eligible_transitions": 200,
                            "economic_promotion_eligible_transitions": 200,
                            "applied_actions": 200,
                            "projected_applied_alignment_rate": 1.0,
                            "promotion_beneficial_rate": 1.0,
                            "mean_realized_energy_saving_fraction": 0.10,
                            "mean_realized_allocation_saving_fraction": -0.0005,
                            "calibration_version": "sim_native_v2_experimental",
                            "calibration_version_valid": True,
                        },
                    }), encoding="utf-8"
                )
                (directory / "tasam_marl_summary.json").write_text(
                    json.dumps({"final_metrics": {
                        "conditional_f1": 0.8,
                        "conditional_recall": 0.8,
                        "category_accuracy": 0.8,
                        "training_reward_mean": 0.1,
                        "category_loss": 0.1,
                    }}), encoding="utf-8"
                )
            (active / "tasam_marl_summary.json").write_text(
                json.dumps({"final_metrics": {
                    "conditional_f1": 0.7,
                    "conditional_recall": 0.7,
                    "category_accuracy": 0.7,
                    "training_reward_mean": 0.0,
                    "category_loss": 0.2,
                }}), encoding="utf-8"
            )
            shadow_evaluation = root / "candidate_shadow_evaluation.json"
            shadow_evaluation.write_text(json.dumps({
                "candidates": {
                    "active": {
                        "checkpoint_dir": str(active.resolve()),
                        "checkpoint_loaded": True,
                        "summary": {"avg_causal_score_delta_vs_rapp": 0.0},
                    },
                    "candidate": {
                        "checkpoint_dir": str(candidate.resolve()),
                        "checkpoint_loaded": True,
                        "summary": {"avg_causal_score_delta_vs_rapp": 0.0},
                        "candidate_vs_active": {
                            "samples": 30,
                            "avg_causal_score_delta": -0.0005,
                            "avg_ran_completion_delta": 0.0,
                            "avg_ai_completion_delta": 0.0,
                        },
                    },
                }
            }), encoding="utf-8")

            args = SimpleNamespace(
                promotion_window=30,
                rollout_manifest=root / "online_rollout.json",
                eval_manifest=root / "online_eval_manifest.json",
                control_gate=root / "online_control_gate.json",
                candidate_eval_manifest=root / "online_candidate_eval_manifest.json",
            )
            state = {
                "stage": "canary_50",
                "rollout_fraction": 0.50,
                "max_rollout_fraction": 0.50,
                "active_checkpoint": str(active),
                "candidate_checkpoint": str(candidate),
                "candidate_promoted": False,
                "candidate_shadow_checkpoint": str(candidate),
                "candidate_shadow_started_decisions": 100,
                "candidate_shadow_until_decisions": 130,
                "economic_action_contract": "applied_action_v2",
                "promotion_window": 30,
                "candidate_shadow_evaluation": str(shadow_evaluation),
                "min_economic_transitions": 180,
            }

            economic_ok, gate = _economic_candidate_gate(state, candidate)
            self.assertTrue(economic_ok, gate)
            advance_rollout(args, state, decision_count=130, guard={"rollback": False})

            self.assertTrue(state["candidate_promoted"])
            self.assertTrue(state["active_checkpoint_promoted"])
            self.assertEqual(state["last_promoted_checkpoint"], str(candidate.resolve()))
            self.assertEqual(state["promotion_count"], 1)
            self.assertEqual(state["active_checkpoint"], str(candidate.resolve()))
            self.assertEqual(state["stage"], "canary_50")
            self.assertEqual(state["rollout_fraction"], 0.50)

    def test_retention_normalizes_checkpoint_leaf_and_preserves_promoted_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates"
            promoted = candidates / "candidate_0009" / "tasam_selective"
            rejected = candidates / "candidate_0010" / "tasam_selective"
            for checkpoint in (promoted, rejected):
                checkpoint.mkdir(parents=True)
                (checkpoint / "tasam_marl_actors.pt").write_bytes(b"actor")
                (checkpoint / "tasam_marl_checkpoint_meta.json").write_text("{}")
                (checkpoint / "tasam_marl_summary.json").write_text("{}")
            args = SimpleNamespace(state_dir=root)
            state = {
                "active_checkpoint": str(promoted),
                "last_promoted_checkpoint": str(promoted),
                "active_checkpoint_promoted": True,
                "candidate_checkpoint": str(rejected),
                "last_rejected_checkpoint": str(rejected),
                "last_candidate_rejection": {"candidate": str(rejected)},
            }
            result = prune_candidate_artifacts(args, state)
            self.assertEqual(result["removed"], 0)
            self.assertTrue((promoted / "tasam_marl_actors.pt").is_file())
            self.assertTrue((rejected / "tasam_marl_actors.pt").is_file())

    def test_retention_fails_closed_when_promoted_checkpoint_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates"
            candidates.mkdir()
            args = SimpleNamespace(state_dir=root)
            state = {
                "active_checkpoint": str(candidates / "candidate_0009" / "tasam_selective"),
                "active_checkpoint_promoted": True,
            }
            with self.assertRaises(RuntimeError):
                prune_candidate_artifacts(args, state)

    def test_economic_candidate_rejects_allocation_worse_than_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "tasam_marl_checkpoint_meta.json").write_text(
                json.dumps({
                    "economic_action_contract": "applied_action_v2",
                    "total_budget_fraction_bounds": [0.0, 1.0],
                    "economic_replay": {
                        "eligible_transitions": 200,
                        "economic_promotion_eligible_transitions": 200,
                        "applied_actions": 200,
                        "projected_applied_alignment_rate": 1.0,
                        "promotion_beneficial_rate": 1.0,
                        "mean_realized_energy_saving_fraction": 0.10,
                        "mean_realized_allocation_saving_fraction": -0.0011,
                        "calibration_version": "sim_native_v2_experimental",
                        "calibration_version_valid": True,
                    },
                }), encoding="utf-8"
            )
            ok, gate = _economic_candidate_gate(
                {"economic_action_contract": "applied_action_v2", "min_economic_transitions": 180},
                candidate,
            )
            self.assertFalse(ok)
            self.assertFalse(gate["checks"]["mean_realized_allocation_saving_nonnegative"])

    def test_economic_v2_rollout_stops_at_fifty_percent_without_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / "bootstrap"
            active.mkdir()
            (active / "tasam_marl_actors.pt").write_bytes(b"bootstrap")
            (active / "tasam_marl_checkpoint_meta.json").write_text("{}", encoding="utf-8")
            (active / "tasam_marl_summary.json").write_text("{}", encoding="utf-8")
            args = SimpleNamespace(
                promotion_window=30,
                shadow_min_decisions=300,
                stage_window_decisions=300,
                rollout_manifest=root / "online_rollout.json",
                eval_manifest=root / "online_eval_manifest.json",
                control_gate=root / "online_control_gate.json",
                candidate_eval_manifest=root / "online_candidate_eval_manifest.json",
            )
            state = {
                "stage": "shadow",
                "rollout_fraction": 0.0,
                "stage_started_decisions": 0,
                "active_checkpoint": str(active),
                "candidate_checkpoint": "",
                "candidate_promoted": False,
                "economic_action_contract": "applied_action_v2",
                "max_rollout_fraction": 0.50,
            }
            for decisions, expected_stage, expected_fraction in (
                (300, "canary_10", 0.10),
                (600, "canary_25", 0.25),
                (900, "canary_50", 0.50),
                (1200, "canary_50", 0.50),
            ):
                advance_rollout(args, state, decision_count=decisions, guard={"rollback": False})
                self.assertEqual(state["stage"], expected_stage)
                self.assertEqual(state["rollout_fraction"], expected_fraction)

    def test_full_rollout_keeps_active_policy_during_candidate_shadow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate_0002" / "tasam_selective"
            candidate.mkdir(parents=True)
            (candidate / "tasam_marl_actors.pt").write_bytes(b"checkpoint")
            (candidate / "tasam_marl_checkpoint_meta.json").write_text(
                json.dumps({"du_count": 3}), encoding="utf-8"
            )
            (candidate / "tasam_marl_summary.json").write_text(
                json.dumps({"completed_epochs": 3, "final_metrics": {}}), encoding="utf-8"
            )
            active = root / "active" / "tasam_selective"
            active.mkdir(parents=True)
            for name, payload in {
                "tasam_marl_actors.pt": b"active",
                "tasam_marl_checkpoint_meta.json": json.dumps({"du_count": 3}),
                "tasam_marl_summary.json": json.dumps({"completed_epochs": 3}),
            }.items():
                (active / name).write_bytes(payload if isinstance(payload, bytes) else payload.encode())

            args = SimpleNamespace(
                promotion_window=30,
                rollout_manifest=root / "online_rollout.json",
                eval_manifest=root / "online_eval_manifest.json",
                control_gate=root / "online_control_gate.json",
                candidate_eval_manifest=root / "online_candidate_eval_manifest.json",
            )
            state = {
                "stage": "full",
                "rollout_fraction": 1.0,
                "active_checkpoint": str(active),
                "candidate_checkpoint": str(candidate),
                "candidate_promoted": False,
            }

            advance_rollout(args, state, decision_count=100, guard={"rollback": False})
            self.assertEqual(state["stage"], "full")
            self.assertEqual(state["rollout_fraction"], 1.0)
            self.assertEqual(state["active_checkpoint"], str(active))
            self.assertEqual(state["candidate_checkpoint"], str(candidate))
            self.assertEqual(state["candidate_shadow_started_decisions"], 100)
            rollout = json.loads(args.rollout_manifest.read_text(encoding="utf-8"))
            self.assertEqual(rollout["rollout"]["checkpoint"], str(active))
            self.assertEqual(rollout["rollout"]["candidate"], str(candidate))
            self.assertEqual(rollout["rollout"]["fraction"], 1.0)

            advance_rollout(args, state, decision_count=130, guard={"rollback": False})
            self.assertEqual(state["stage"], "full")
            self.assertEqual(state["rollout_fraction"], 1.0)
            self.assertEqual(state["active_checkpoint"], str(candidate))
            self.assertEqual(state["candidate_checkpoint"], "")
            promoted = json.loads(args.eval_manifest.read_text(encoding="utf-8"))
            self.assertEqual(promoted["best_run"]["run_dir"], str(candidate.resolve()))

    def test_legacy_shadow_state_migrates_without_resetting_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(promotion_window=30)
            state = {
                "stage": "shadow",
                "rollout_fraction": 0.0,
                "active_checkpoint": str(root / "candidate_0001"),
                "candidate_checkpoint": str(root / "candidate_0002"),
                "candidate_promoted": False,
                "stage_started_decisions": 1009,
                "updates_completed": 2,
            }

            self.assertTrue(migrate_legacy_candidate_shadow_state(args, state, 1027))
            self.assertEqual(state["stage"], "full")
            self.assertEqual(state["rollout_fraction"], 1.0)
            self.assertEqual(state["candidate_shadow_started_decisions"], 1009)
            self.assertEqual(state["candidate_shadow_until_decisions"], 1039)
            self.assertEqual(state["updates_completed"], 2)

    def test_active_manifest_is_not_replaced_by_pending_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / "active"
            candidate = root / "candidate"
            for directory in (active, candidate):
                directory.mkdir()
                (directory / "tasam_marl_actors.pt").write_bytes(b"checkpoint")
                (directory / "tasam_marl_checkpoint_meta.json").write_text(
                    json.dumps({"du_count": 3}), encoding="utf-8"
                )
                (directory / "tasam_marl_summary.json").write_text("{}", encoding="utf-8")
            args = SimpleNamespace(
                updates_completed=2,
                eval_manifest=root / "online_eval_manifest.json",
                control_gate=root / "online_control_gate.json",
                candidate_eval_manifest=root / "online_candidate_eval_manifest.json",
                promotion_window=30,
            )
            state = {
                "stage": "full",
                "rollout_fraction": 1.0,
                "active_checkpoint": str(active),
                "candidate_checkpoint": str(candidate),
            }
            publish_manifests(args, state, active, "control_candidate")
            payload = json.loads(args.eval_manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["best_run"]["run_dir"], str(active.resolve()))


if __name__ == "__main__":
    unittest.main()
