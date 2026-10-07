#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp Data Lake
================================

Responsabilidade: Armazenamento de histórico de métricas e decisões
- Persistência em SQLite (sobrevive reinicializações)
- Dados para treinamento de ML
- Estatísticas por hora/dia da semana

Banco de Dados:
    /tmp/rapp_data_lake.db

Tabelas:
    - metrics_history: timestamp, latency_us, cameras_active, critical_cameras, energy_state
    - decisions_history: timestamp, decision, reason, confidence, pattern, agent_override
    - hourly_stats: hour, day_of_week, avg_latency, avg_cameras, sample_count, energy_blocked_count
    - daily_stats: date, day_of_week, avg_latency, avg_cameras, total_energy_saves, total_blocks

Uso:
    from rapp_data_lake import DataLake
    dl = DataLake()
    dl.record_metric(timestamp, latency, cameras, state)
    dl.record_decision(decision)
    stats = dl.get_hourly_stats()
"""

import os
import csv
import sqlite3
import time
import json
import re
import hashlib
import io
import math

try:
    from .greenran_marl_topology import build_du_state_snapshot_from_resource_snapshot
except ImportError:
    from greenran_marl_topology import build_du_state_snapshot_from_resource_snapshot
from datetime import datetime, timedelta
from pathlib import Path

from greenran_paths import RAPP_DB_PATH, TASAM_NATIVE_CONTROL_OBSERVATIONS_PATH, get_fixed_service_imsis
try:
    from .energy_calibration import integrate_energy_events, load_calibration, state_power_w, sleep_state_power_w
except ImportError:
    from energy_calibration import integrate_energy_events, load_calibration, state_power_w, sleep_state_power_w

DEFAULT_DB_PATH = str(RAPP_DB_PATH)


def _json_safe_copy(value, default=None):
    """Return a compact JSON-safe copy without retaining a live reference."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return default


def _selected_json_fields(payload, fields):
    if not isinstance(payload, dict):
        return {}
    result = {}
    for field in fields:
        if field in payload and payload[field] is not None:
            result[field] = _json_safe_copy(payload[field])
    return result


def _compact_judge_feedback_payload(decision_id, feedback, observation=None):
    """Return the bounded delayed-feedback record used by the Judge table.

    The full decision/next-decision pair belongs in the canonical economic
    transition table.  Keeping it inside ``feedback_json`` duplicated large
    MARL proposals once per decision and made a 1,300-row run exceed a GiB.
    Historical v1 rows remain readable; new rows use this bounded v2 shape.
    """
    feedback = feedback if isinstance(feedback, dict) else {}
    observation = observation if isinstance(observation, dict) else {}
    fields = (
        "feedback_status", "outcome_observed", "observed_metric_id",
        "pdcp_metric_snapshot_id", "pdcp_loss_coverage", "topology_valid",
        "tasam_action_applied", "economic_action_alignment_valid",
        "economic_action_contract", "economic_application_status",
        "economic_transition_eligible", "economic_training_eligible",
        "economic_promotion_eligible", "economic_execution_mode",
        "economic_safety_isolated", "economic_safety_isolation_reason",
        "economic_outcome_invalid_reason", "economic_invalid_reason",
        "armd_safety_level", "armd_role", "realized_energy_saving_fraction",
        "realized_allocation_saving_fraction", "tasam_online_reward",
        "tasam_energy_reward", "tasam_allocation_reward", "tasam_sla_penalty",
        "tasam_observed_verdict", "tasam_predicted_verdict",
        "tasam_error_components", "energy_model_version",
        "correct_verdict", "outcome_reward", "severity_penalty",
        "armd_credit", "tasam_credit", "tasam_state_credit",
        "tasam_resource_credit", "tasam_observed_error",
        "tasam_continuous_reward", "tasam_reward_source", "credit_assignment",
        "energy_evidence",
        "reason", "decision_stage_name", "observed_stage_name",
        "stage_boundary_feedback", "nominal_expected_verdict",
    )
    economic_action = feedback.get("economic_action") or {}
    action_fields = (
        "contract", "application_status", "correlation_id",
        "native_control_sequence", "actuation_confirmed",
        "actuation_confirmation_source", "native_observation",
        "economic_transition_eligible", "economic_training_eligible",
        "economic_promotion_eligible", "economic_execution_mode",
        "economic_safety_isolated", "economic_safety_isolation_reason",
        "outcome_invalid_reason", "rejection_reason", "safety_override",
        "armd_safety_level", "armd_role", "armd_advisory_only",
        "armd_hard_veto", "topology_valid", "realized_energy_saving_fraction",
        "realized_allocation_saving_fraction", "proposed", "projected",
        "applied", "live_candidate",
    )
    observation_fields = (
        "observed_metric_id", "metric_snapshot_id", "timestamp",
        "correct_verdict", "observed", "degraded", "critical_violation",
        "priority_violation", "reason", "decision_stage_name",
        "observed_stage_name", "stage_boundary_feedback",
        "nominal_expected_verdict",
    )
    payload = {
        "schema": "greenran.rapp_judge_feedback.v2",
        "decision_id": int(decision_id) if decision_id else None,
        "feedback": _selected_json_fields(feedback, fields),
        "economic_action": _selected_json_fields(economic_action, action_fields),
        "observation": _selected_json_fields(observation, observation_fields),
        "economic_transition_ref": {
            "decision_id": int(decision_id) if decision_id else None,
            "observed_metric_id": feedback.get("observed_metric_id")
            or feedback.get("pdcp_metric_snapshot_id")
            or observation.get("observed_metric_id"),
            "source": "tasam_economic_transition_history",
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # This is a safety invariant, not a truncation mechanism: all fields are
    # explicitly allow-listed above, so exceeding the bound indicates a new
    # oversized nested field was introduced and should fail the write loudly.
    if len(encoded.encode("utf-8")) > 32 * 1024:
        raise ValueError("compact Judge feedback exceeds 32 KiB")
    return payload


def _compact_economic_transition_payload(decision_id, decision, next_decision, feedback, action):
    """Persist the economic contract without copying whole decision snapshots.

    The canonical MARL state remains in ``marl_*_state_history``.  Storing it
    once avoids the multi-gigabyte duplicate transition bank that previously
    exhausted the campaign artifact budget.  The controller rehydrates only
    exact timestamps when it prepares a temporary trainer replay.
    """
    action_fields = (
        "contract", "application_status", "correlation_id",
        "native_control_sequence", "actuation_confirmed",
        "actuation_confirmation_source", "native_observation",
        "economic_transition_eligible", "economic_training_eligible",
        "economic_promotion_eligible", "economic_execution_mode",
        "economic_safety_isolated", "economic_safety_isolation_reason",
        "economic_isolation_source", "outcome_invalid_reason",
        "rejection_reason", "safety_override", "armd_safety_level",
        "armd_role", "armd_advisory_only", "armd_hard_veto",
        "tasam_operating_permission", "topology_valid",
        "pdcp_loss_coverage", "pdcp_metric_snapshot_id",
        "realized_energy_saving_fraction", "realized_allocation_saving_fraction",
        "proposed", "projected", "applied", "live_candidate",
    )
    feedback_fields = (
        "feedback_status", "outcome_observed", "observed_metric_id",
        "pdcp_metric_snapshot_id", "pdcp_loss_coverage", "topology_valid",
        "tasam_action_applied", "economic_action_alignment_valid",
        "economic_action_contract", "economic_application_status",
        "native_control_sequence", "actuation_confirmed",
        "economic_transition_eligible", "economic_training_eligible",
        "economic_promotion_eligible", "economic_execution_mode",
        "economic_safety_isolated", "economic_safety_isolation_reason",
        "economic_outcome_invalid_reason", "armd_safety_level", "armd_role",
        "realized_energy_saving_fraction", "realized_allocation_saving_fraction",
        "tasam_online_reward", "tasam_energy_reward",
        "tasam_allocation_reward", "tasam_sla_penalty",
        "tasam_observed_verdict", "tasam_predicted_verdict",
        "tasam_error_components", "energy_model_version", "energy_evidence",
    )
    decision_fields = (
        "decision_id", "timestamp", "metric_snapshot_id", "topology_id",
        "tasam_checkpoint_valid", "tasam_fallback_used", "tasam_source",
        "tasam_valid", "tasam_evidence_valid", "tasam_action_applied", "topology_valid",
        "economic_action_contract", "economic_application_status",
        "economic_transition_eligible", "economic_training_eligible",
        "economic_promotion_eligible", "economic_execution_mode",
        "economic_safety_isolated", "armd_safety_level", "armd_role",
        "energy_model_version", "native_evidence_ingest", "decision_stage_name",
        "allocation_state",
    )
    next_fields = (
        "timestamp", "metric_snapshot_id", "topology_id", "decision_stage_name",
        "allocation_state", "energy_model_version",
    )
    allocation_fields = (
        "usable_budget", "resource_budget", "r_ran", "r_ai", "d_ran", "d_ai",
        "floor_total_ran", "floor_total_ai", "floor_feasible", "floor_verified",
        "allocation_state", "ran_completion_ratio", "ai_completion_ratio",
        "ran_components", "ai_components", "live_energy_observation",
        "live_power_percent", "live_ru_count", "live_mmwave_count",
        "scenario_stage", "state_category",
    )
    allocation = decision.get("resource_allocation") or decision.get("action") or {}
    return {
        "schema": "greenran.tasam.economic_transition.v2",
        "decision_id": int(decision_id),
        "decision_provenance": _selected_json_fields(decision, decision_fields),
        "next_decision_provenance": _selected_json_fields(next_decision, next_fields),
        "resource_allocation": _selected_json_fields(allocation, allocation_fields),
        "economic_action": _selected_json_fields(action, action_fields),
        "judge_feedback": _selected_json_fields(feedback, feedback_fields),
    }


class DataLake:
    """
    SQLite Data Lake para persistência de métricas e decisões.
    Suporta análise de padrões sazonais.
    """
    
    def __init__(self, db_path=DEFAULT_DB_PATH):
        self.db_path = db_path
        self.conn = None
        self._ensure_directory()
        self._connect()
        self._create_tables()
    
    def _ensure_directory(self):
        """Garante que o diretório do banco existe"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
    
    def _connect(self):
        """Conecta ao banco SQLite com otimizações"""
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row

            # Enable WAL mode for better concurrency
            self.conn.execute("PRAGMA journal_mode=WAL")

            # Performance optimizations
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            self.conn.execute("PRAGMA temp_store=MEMORY")
        except Exception as e:
            print(f"[DataLake] ERRO ao conectar: {e}")
            raise

    def _migrate_judge_outcome_timestamp_key(self, cursor):
        """Remove the legacy timestamp-only uniqueness from judge feedback.

        Older databases used ``UNIQUE(decision_timestamp)``.  That key is not
        sufficient when the rApp emits more than one decision in a second and
        can replace an unrelated delayed outcome.  New databases do not add
        that constraint; existing databases are rebuilt transactionally while
        preserving every row.  Legacy rows retain a nullable decision_id and
        are matched by timestamp only when no exact id is available.
        """
        table = "judge_outcome_history"
        indexes = cursor.execute(f"PRAGMA index_list({table})").fetchall()
        timestamp_unique = False
        for index in indexes:
            if not int(index[2] or 0):
                continue
            index_name = str(index[1])
            columns = [
                str(row[2])
                for row in cursor.execute(f"PRAGMA index_info({index_name})").fetchall()
            ]
            if columns == ["decision_timestamp"]:
                timestamp_unique = True
                break
        if not timestamp_unique:
            return

        schema_row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not schema_row or not schema_row[0]:
            return
        new_schema = str(schema_row[0]).replace(
            "CREATE TABLE judge_outcome_history",
            "CREATE TABLE judge_outcome_history_new",
            1,
        )
        new_schema = re.sub(
            r",\s*UNIQUE\s*\(\s*decision_timestamp\s*\)",
            "",
            new_schema,
            flags=re.IGNORECASE,
        )
        cursor.execute("DROP INDEX IF EXISTS idx_judge_outcome_decision_timestamp")
        cursor.execute(
            "ALTER TABLE judge_outcome_history RENAME TO judge_outcome_history_legacy"
        )
        cursor.execute(new_schema)
        old_columns = {
            str(row[1])
            for row in cursor.execute("PRAGMA table_info(judge_outcome_history_legacy)").fetchall()
        }
        new_columns = {
            str(row[1])
            for row in cursor.execute("PRAGMA table_info(judge_outcome_history_new)").fetchall()
        }
        common = sorted(old_columns & new_columns)
        if common:
            names = ", ".join(common)
            cursor.execute(
                f"INSERT INTO judge_outcome_history_new ({names}) "
                f"SELECT {names} FROM judge_outcome_history_legacy"
            )
        cursor.execute("DROP TABLE judge_outcome_history_legacy")
        cursor.execute(
            "ALTER TABLE judge_outcome_history_new RENAME TO judge_outcome_history"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_judge_outcome_decision_timestamp "
            "ON judge_outcome_history(decision_timestamp)"
        )
    
    def _create_tables(self):
        """Cria tabelas do Data Lake"""
        cursor = self.conn.cursor()
        
        # Tabela de métricas históricas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                latency_us REAL NOT NULL,
                cameras_active INTEGER NOT NULL,
                critical_cameras INTEGER DEFAULT 0,
                energy_state TEXT,
                slicer_state TEXT,
                UNIQUE(timestamp)
            )
        """)
        
        # Tabela de decisões históricas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decisions_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                metric_snapshot_id INTEGER,
                snapshot_sequence_id TEXT,
                pairing_schedule_id TEXT,
                decision TEXT NOT NULL,
                reason TEXT,
                confidence REAL,
                pattern TEXT,
                agent_override INTEGER DEFAULT 0,
                energy_state TEXT,
                slicer_state TEXT,
                collection_event_stage_name TEXT,
                collection_event_target_domain TEXT,
                collection_event_cycle INTEGER DEFAULT 0,
                collection_event_stage_index INTEGER DEFAULT 0,
                collection_event_generated_at INTEGER DEFAULT 0,
                collection_event_stage_authoritative INTEGER DEFAULT 0,
                ml_decision TEXT,
                ml_confidence REAL,
                ml_predicted_cvar_ms REAL,
                ml_influenced INTEGER DEFAULT 0,
                armd_enabled INTEGER DEFAULT 0,
                armd_proposal_present INTEGER DEFAULT 0,
                armd_proposal_valid INTEGER DEFAULT 0,
                armd_proposal_kind TEXT,
                armd_actuation_applied INTEGER DEFAULT 0,
                armd_proposal_json TEXT,
                armd_mode TEXT,
                armd_scenario TEXT,
                armd_source TEXT,
                armd_confidence REAL DEFAULT 0,
                armd_override_applied INTEGER DEFAULT 0,
                armd_safety_level TEXT DEFAULT 'UNKNOWN',
                armd_role TEXT DEFAULT 'advisory',
                armd_advisory_only INTEGER DEFAULT 1,
                armd_hard_veto INTEGER DEFAULT 0,
                tasam_operating_permission INTEGER DEFAULT 0,
                tasam_envelope_min_power REAL,
                tasam_envelope_max_power REAL,
                economic_isolation_source TEXT DEFAULT '',
                tasam_enabled INTEGER DEFAULT 0,
                tasam_proposal_present INTEGER DEFAULT 0,
                tasam_proposal_valid INTEGER DEFAULT 0,
                tasam_proposal_kind TEXT,
                tasam_proposal_json TEXT,
                tasam_mode TEXT,
                tasam_policy_id TEXT,
                tasam_source TEXT,
                tasam_confidence REAL DEFAULT 0,
                tasam_valid INTEGER DEFAULT 0,
                tasam_checkpoint_valid INTEGER DEFAULT 0,
                tasam_fallback_used INTEGER DEFAULT 0,
                tasam_evidence_valid INTEGER DEFAULT 0,
                live_allocator_algorithm TEXT DEFAULT '',
                live_power_percent REAL,
                shadow_power_percent REAL,
                live_power_w REAL,
                shadow_power_w REAL,
                energy_saving_fraction REAL DEFAULT 0,
                resource_saving_fraction REAL DEFAULT 0,
                causal_score_delta REAL DEFAULT 0,
                energy_model_version TEXT DEFAULT '',
                tasam_would_influence INTEGER DEFAULT 0,
                tasam_energy_decision TEXT,
                tasam_energy_action TEXT,
                tasam_power_percent REAL DEFAULT 100,
                tasam_power_applied_percent REAL DEFAULT 100,
                power_safety_override_reason TEXT DEFAULT '',
                economic_action_contract TEXT DEFAULT '',
                economic_execution_mode TEXT DEFAULT 'diagnostic',
                economic_safety_isolated INTEGER DEFAULT 0,
                economic_safety_isolation_reason TEXT DEFAULT '',
                economic_application_status TEXT DEFAULT '',
                economic_rejection_reason TEXT DEFAULT '',
                actuation_confirmed INTEGER DEFAULT 0,
                actuation_confirmation_source TEXT DEFAULT '',
                observed_power_percent REAL,
                observed_power_w REAL,
                observed_ru_count INTEGER,
                observed_mmwave_count INTEGER,
                confirmation_decision_id INTEGER,
                economic_outcome_invalid_reason TEXT DEFAULT '',
                pdcp_coverage_json TEXT DEFAULT '{}',
                topology_valid INTEGER DEFAULT 0,
                pdcp_metric_snapshot_id INTEGER,
                economic_transition_eligible INTEGER DEFAULT 0,
                economic_training_eligible INTEGER DEFAULT 0,
                economic_promotion_eligible INTEGER DEFAULT 0,
                realized_energy_saving_fraction REAL,
                realized_allocation_saving_fraction REAL,
                tasam_online_reward REAL,
                tasam_energy_reward REAL,
                tasam_allocation_reward REAL,
                tasam_sla_penalty REAL,
                economic_action_json TEXT DEFAULT '{}',
                tasam_power_cost_penalty REAL DEFAULT 0,
                tasam_completion_shortfall_penalty REAL DEFAULT 0,
                tasam_underallocation_penalty REAL DEFAULT 0,
                tasam_allocation_target_ran REAL DEFAULT 0,
                tasam_allocation_target_ai REAL DEFAULT 0,
                tasam_allocation_predicted_ran REAL DEFAULT 0,
                tasam_allocation_predicted_ai REAL DEFAULT 0,
                tasam_allocation_prediction_loss REAL DEFAULT 0,
                tasam_allocation_target_source TEXT DEFAULT '',
                tasam_allocation_target_feasible INTEGER DEFAULT 1,
                tasam_reward_components_json TEXT DEFAULT '{}',
                native_evidence_ingest_json TEXT DEFAULT '{}',
                advisor_arbitration_mode TEXT,
                advisor_arbitration_present INTEGER DEFAULT 0,
                advisor_proposal_pair_complete INTEGER DEFAULT 0,
                advisor_rapp_final_decision TEXT,
                advisor_rapp_final_action TEXT,
                advisor_arbitration_winner TEXT,
                advisor_arbitration_score REAL DEFAULT 0,
                rapp_judge_mode TEXT,
                rapp_judge_conflict_type TEXT,
                rapp_judge_reason TEXT,
                selected_assistant TEXT,
                selected_proposal_id TEXT,
                proposal_applied_exactly INTEGER DEFAULT 0,
                external_last_resort_used INTEGER DEFAULT 0,
                selected_proposal_json TEXT,
                rl_policy_id TEXT,
                rl_policy_family TEXT,
                rl_policy_algorithm TEXT,
                resource_controller_id TEXT,
                resource_budget REAL DEFAULT 0,
                usable_budget REAL DEFAULT 0,
                ran_demand REAL DEFAULT 0,
                ai_demand REAL DEFAULT 0,
                ran_allocation REAL DEFAULT 0,
                ai_allocation REAL DEFAULT 0,
                ran_completion_ratio REAL DEFAULT 0,
                ai_completion_ratio REAL DEFAULT 0,
                utilization_ratio REAL DEFAULT 0,
                allocation_state TEXT DEFAULT 'ALLOWED',
                healthy_streak INTEGER DEFAULT 0,
                floor_total_ran REAL DEFAULT 0,
                floor_total_ai REAL DEFAULT 0,
                reinforcement_ran REAL DEFAULT 0,
                reinforcement_ai REAL DEFAULT 0,
                floor_feasible INTEGER DEFAULT 1,
                per_ue_floor_json TEXT DEFAULT '{}',
                resource_floor_policy TEXT DEFAULT 'sla_per_ue_v1',
                network_improvement_pct REAL DEFAULT 0,
                cvar_improvement_pct REAL DEFAULT 0,
                p95_improvement_pct REAL DEFAULT 0,
                baseline_cvar_us REAL DEFAULT 0,
                baseline_p95_us REAL DEFAULT 0,
                improvement_source TEXT,
                improvement_valid INTEGER DEFAULT 0,
                ta_sam_actuation_applied INTEGER DEFAULT 0,
                tasam_actuation_applied INTEGER DEFAULT 0,
                training_run_invalid INTEGER DEFAULT 0,
                invalid_reason TEXT DEFAULT '',
                tasam_policy_envelope_applied INTEGER DEFAULT 0,
                tasam_policy_envelope_source TEXT,
                tasam_policy_envelope_json TEXT DEFAULT '{}',
                control_trial_mode TEXT,
                effective_policy_algorithm TEXT,
                effective_policy_source TEXT,
                control_trial_reason TEXT,
                UNIQUE(timestamp)
            )
        """)
        existing_decision_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(decisions_history)").fetchall()
        }
        for column_name, column_def in (
            ("metric_snapshot_id", "INTEGER"),
            ("snapshot_sequence_id", "TEXT"),
            ("pairing_schedule_id", "TEXT"),
            ("armd_enabled", "INTEGER DEFAULT 0"),
            ("collection_event_stage_name", "TEXT"),
            ("collection_event_target_domain", "TEXT"),
            ("collection_event_cycle", "INTEGER DEFAULT 0"),
            ("collection_event_stage_index", "INTEGER DEFAULT 0"),
            ("collection_event_generated_at", "INTEGER DEFAULT 0"),
            ("collection_event_stage_authoritative", "INTEGER DEFAULT 0"),
            ("armd_proposal_present", "INTEGER DEFAULT 0"),
            ("armd_proposal_valid", "INTEGER DEFAULT 0"),
            ("armd_proposal_kind", "TEXT"),
            ("armd_actuation_applied", "INTEGER DEFAULT 0"),
            ("armd_proposal_json", "TEXT"),
            ("armd_mode", "TEXT"),
            ("armd_scenario", "TEXT"),
            ("armd_source", "TEXT"),
            ("armd_confidence", "REAL DEFAULT 0"),
            ("armd_override_applied", "INTEGER DEFAULT 0"),
            ("armd_safety_level", "TEXT DEFAULT 'UNKNOWN'"),
            ("armd_role", "TEXT DEFAULT 'advisory'"),
            ("armd_advisory_only", "INTEGER DEFAULT 1"),
            ("armd_hard_veto", "INTEGER DEFAULT 0"),
            ("tasam_operating_permission", "INTEGER DEFAULT 0"),
            ("tasam_envelope_min_power", "REAL"),
            ("tasam_envelope_max_power", "REAL"),
            ("economic_isolation_source", "TEXT DEFAULT ''"),
            ("tasam_enabled", "INTEGER DEFAULT 0"),
            ("tasam_proposal_present", "INTEGER DEFAULT 0"),
            ("tasam_proposal_valid", "INTEGER DEFAULT 0"),
            ("tasam_proposal_kind", "TEXT"),
            ("tasam_proposal_json", "TEXT"),
            ("tasam_mode", "TEXT"),
            ("tasam_policy_id", "TEXT"),
            ("tasam_source", "TEXT"),
            ("tasam_confidence", "REAL DEFAULT 0"),
            ("tasam_valid", "INTEGER DEFAULT 0"),
            ("tasam_checkpoint_valid", "INTEGER DEFAULT 0"),
            ("tasam_fallback_used", "INTEGER DEFAULT 0"),
            ("tasam_evidence_valid", "INTEGER DEFAULT 0"),
            ("live_allocator_algorithm", "TEXT DEFAULT ''"),
            ("live_power_percent", "REAL"),
            ("shadow_power_percent", "REAL"),
            ("live_power_w", "REAL"),
            ("shadow_power_w", "REAL"),
            ("energy_saving_fraction", "REAL DEFAULT 0"),
            ("resource_saving_fraction", "REAL DEFAULT 0"),
            ("causal_score_delta", "REAL DEFAULT 0"),
            ("energy_model_version", "TEXT DEFAULT ''"),
            ("tasam_would_influence", "INTEGER DEFAULT 0"),
            ("tasam_energy_decision", "TEXT"),
            ("tasam_energy_action", "TEXT"),
            ("tasam_power_percent", "REAL DEFAULT 100"),
            ("tasam_power_applied_percent", "REAL DEFAULT 100"),
            ("power_safety_override_reason", "TEXT DEFAULT ''"),
            ("economic_action_contract", "TEXT DEFAULT ''"),
            ("economic_execution_mode", "TEXT DEFAULT 'diagnostic'"),
            ("economic_safety_isolated", "INTEGER DEFAULT 0"),
            ("economic_safety_isolation_reason", "TEXT DEFAULT ''"),
            ("economic_application_status", "TEXT DEFAULT ''"),
            ("economic_rejection_reason", "TEXT DEFAULT ''"),
            ("actuation_confirmed", "INTEGER DEFAULT 0"),
            ("actuation_confirmation_source", "TEXT DEFAULT ''"),
            ("observed_power_percent", "REAL"),
            ("observed_power_w", "REAL"),
            ("observed_ru_count", "INTEGER"),
            ("observed_mmwave_count", "INTEGER"),
            ("confirmation_decision_id", "INTEGER"),
            ("economic_outcome_invalid_reason", "TEXT DEFAULT ''"),
            ("pdcp_coverage_json", "TEXT DEFAULT '{}'"),
            ("topology_valid", "INTEGER DEFAULT 0"),
            ("pdcp_metric_snapshot_id", "INTEGER"),
            ("economic_transition_eligible", "INTEGER DEFAULT 0"),
            ("economic_training_eligible", "INTEGER DEFAULT 0"),
            ("economic_promotion_eligible", "INTEGER DEFAULT 0"),
            ("realized_energy_saving_fraction", "REAL"),
            ("realized_allocation_saving_fraction", "REAL"),
            ("tasam_online_reward", "REAL"),
            ("tasam_energy_reward", "REAL"),
            ("tasam_allocation_reward", "REAL"),
            ("tasam_sla_penalty", "REAL"),
            ("economic_action_json", "TEXT DEFAULT '{}'"),
            ("tasam_power_cost_penalty", "REAL DEFAULT 0"),
            ("tasam_completion_shortfall_penalty", "REAL DEFAULT 0"),
            ("tasam_underallocation_penalty", "REAL DEFAULT 0"),
            ("tasam_allocation_target_ran", "REAL DEFAULT 0"),
            ("tasam_allocation_target_ai", "REAL DEFAULT 0"),
            ("tasam_allocation_predicted_ran", "REAL DEFAULT 0"),
            ("tasam_allocation_predicted_ai", "REAL DEFAULT 0"),
            ("tasam_allocation_prediction_loss", "REAL DEFAULT 0"),
            ("tasam_allocation_target_source", "TEXT DEFAULT ''"),
            ("tasam_allocation_target_feasible", "INTEGER DEFAULT 1"),
            ("tasam_reward_components_json", "TEXT DEFAULT '{}'"),
            ("native_evidence_ingest_json", "TEXT DEFAULT '{}'"),
            ("advisor_arbitration_mode", "TEXT"),
            ("advisor_arbitration_present", "INTEGER DEFAULT 0"),
            ("advisor_proposal_pair_complete", "INTEGER DEFAULT 0"),
            ("advisor_rapp_final_decision", "TEXT"),
            ("advisor_rapp_final_action", "TEXT"),
            ("advisor_arbitration_winner", "TEXT"),
            ("advisor_arbitration_score", "REAL DEFAULT 0"),
            ("rapp_judge_mode", "TEXT"),
            ("rapp_judge_conflict_type", "TEXT"),
            ("rapp_judge_reason", "TEXT"),
            ("selected_assistant", "TEXT"),
            ("selected_proposal_id", "TEXT"),
            ("proposal_applied_exactly", "INTEGER DEFAULT 0"),
            ("external_last_resort_used", "INTEGER DEFAULT 0"),
            ("selected_proposal_json", "TEXT"),
            ("rl_policy_id", "TEXT"),
            ("rl_policy_family", "TEXT"),
            ("rl_policy_algorithm", "TEXT"),
            ("resource_controller_id", "TEXT"),
            ("resource_budget", "REAL DEFAULT 0"),
            ("usable_budget", "REAL DEFAULT 0"),
            ("ran_demand", "REAL DEFAULT 0"),
            ("ai_demand", "REAL DEFAULT 0"),
            ("ran_allocation", "REAL DEFAULT 0"),
            ("ai_allocation", "REAL DEFAULT 0"),
            ("ran_completion_ratio", "REAL DEFAULT 0"),
            ("ai_completion_ratio", "REAL DEFAULT 0"),
            ("utilization_ratio", "REAL DEFAULT 0"),
            ("allocation_state", "TEXT DEFAULT 'ALLOWED'"),
            ("healthy_streak", "INTEGER DEFAULT 0"),
            ("floor_total_ran", "REAL DEFAULT 0"),
            ("floor_total_ai", "REAL DEFAULT 0"),
            ("reinforcement_ran", "REAL DEFAULT 0"),
            ("reinforcement_ai", "REAL DEFAULT 0"),
            ("floor_feasible", "INTEGER DEFAULT 1"),
            ("per_ue_floor_json", "TEXT DEFAULT '{}'"),
            ("resource_floor_policy", "TEXT DEFAULT 'sla_per_ue_v1'"),
            ("network_improvement_pct", "REAL DEFAULT 0"),
            ("cvar_improvement_pct", "REAL DEFAULT 0"),
            ("p95_improvement_pct", "REAL DEFAULT 0"),
            ("baseline_cvar_us", "REAL DEFAULT 0"),
            ("baseline_p95_us", "REAL DEFAULT 0"),
            ("improvement_source", "TEXT"),
            ("improvement_valid", "INTEGER DEFAULT 0"),
            ("ta_sam_actuation_applied", "INTEGER DEFAULT 0"),
            ("tasam_actuation_applied", "INTEGER DEFAULT 0"),
            ("training_run_invalid", "INTEGER DEFAULT 0"),
            ("invalid_reason", "TEXT DEFAULT ''"),
            ("tasam_policy_envelope_applied", "INTEGER DEFAULT 0"),
            ("tasam_policy_envelope_source", "TEXT"),
            ("tasam_policy_envelope_json", "TEXT DEFAULT '{}'"),
            ("control_trial_mode", "TEXT"),
            ("effective_policy_algorithm", "TEXT"),
            ("effective_policy_source", "TEXT"),
            ("control_trial_reason", "TEXT"),
        ):
            if column_name not in existing_decision_columns:
                cursor.execute(f"ALTER TABLE decisions_history ADD COLUMN {column_name} {column_def}")
        
        # Tabela de estatísticas por hora
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hourly_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour INTEGER NOT NULL,
                day_of_week INTEGER NOT NULL,
                avg_latency REAL DEFAULT 0,
                min_latency REAL DEFAULT 0,
                max_latency REAL DEFAULT 0,
                avg_cameras REAL DEFAULT 0,
                sample_count INTEGER DEFAULT 0,
                energy_blocked_count INTEGER DEFAULT 0,
                energy_allowed_count INTEGER DEFAULT 0,
                UNIQUE(hour, day_of_week)
            )
        """)
        
        # Tabela de estatísticas por dia
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                day_of_week INTEGER NOT NULL,
                avg_latency REAL DEFAULT 0,
                min_latency REAL DEFAULT 0,
                max_latency REAL DEFAULT 0,
                avg_cameras REAL DEFAULT 0,
                total_energy_saves INTEGER DEFAULT 0,
                total_blocks INTEGER DEFAULT 0,
                total_cycles INTEGER DEFAULT 0
            )
        """)
        
        # Índices para performance
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_metrics_timestamp 
            ON metrics_history(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_decisions_timestamp 
            ON decisions_history(timestamp)
        """)
        
        # Tabela de métricas estendidas (novo)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS extended_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                sim_time_s REAL,
                cell_id INTEGER,
                global_worst_latency_us REAL,
                global_avg_latency_us REAL,
                global_min_latency_us REAL,
                global_max_latency_us REAL,
                global_jitter_us REAL,
                global_packet_loss_rate REAL,
                total_active_ues INTEGER,
                total_active_cameras INTEGER,
                total_critical_ues INTEGER,
                total_tx_bytes INTEGER,
                total_rx_bytes INTEGER,
                total_tx_pdus INTEGER,
                total_rx_pdus INTEGER,
                throughput_kbps REAL,
                energy_state TEXT,
                slicer_state TEXT,
                latency_p5_us REAL,
                latency_p95_us REAL,
                latency_min_nonzero_us REAL,
                valid_samples INTEGER,
                valid_samples_nonzero INTEGER,
                zero_samples INTEGER,
                -- Novas métricas POR UE (corrigem problema de P95 agregado)
                latency_p95_per_ue_us REAL DEFAULT 0,
                variance_per_ue_us2 REAL DEFAULT 0,
                cvar_per_ue_us REAL DEFAULT 0,
                ue_count INTEGER DEFAULT 0,
                collector_mode TEXT DEFAULT '',
                throughput_source TEXT DEFAULT '',
                real_latency_sample_count INTEGER DEFAULT 0,
                proxy_latency_sample_count INTEGER DEFAULT 0,
                pdcp_stale INTEGER DEFAULT 0,
                rlc_stale INTEGER DEFAULT 0,
                mac_stale INTEGER DEFAULT 0,
                pdcp_trace_age_s REAL DEFAULT 0,
                rlc_trace_age_s REAL DEFAULT 0,
                mac_trace_age_s REAL DEFAULT 0,
                pdcp_latest_sim_time_s REAL DEFAULT 0,
                decision_id INTEGER,
                UNIQUE(timestamp)
            )
        """)
        existing_extended_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(extended_metrics)").fetchall()
        }
        for column_name, column_def in (
            ("decision_id", "INTEGER"),
            ("collector_mode", "TEXT DEFAULT ''"),
            ("throughput_source", "TEXT DEFAULT ''"),
            ("real_latency_sample_count", "INTEGER DEFAULT 0"),
            ("proxy_latency_sample_count", "INTEGER DEFAULT 0"),
            ("pdcp_stale", "INTEGER DEFAULT 0"),
            ("rlc_stale", "INTEGER DEFAULT 0"),
            ("mac_stale", "INTEGER DEFAULT 0"),
            ("pdcp_trace_age_s", "REAL DEFAULT 0"),
            ("rlc_trace_age_s", "REAL DEFAULT 0"),
            ("mac_trace_age_s", "REAL DEFAULT 0"),
            ("pdcp_latest_sim_time_s", "REAL DEFAULT 0"),
        ):
            if column_name not in existing_extended_columns:
                cursor.execute(f"ALTER TABLE extended_metrics ADD COLUMN {column_name} {column_def}")
        
        # Tabela de métricas por UE (novo)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ue_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                imsi INTEGER,
                device_type TEXT,
                cell_id INTEGER,
                latency_us REAL,
                latency_avg_us REAL,
                latency_min_us REAL,
                latency_max_us REAL,
                jitter_us REAL,
                pdu_size_avg REAL,
                tx_bytes INTEGER,
                rx_bytes INTEGER,
                tx_pdus INTEGER,
                rx_pdus INTEGER,
                throughput_kbps REAL,
                packet_count INTEGER,
                mcs_avg REAL,
                tb_size_avg REAL,
                is_critical INTEGER
            )
        """)
        existing_ue_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(ue_metrics)").fetchall()
        }
        for column_name, column_def in (
            ("sim_time_s", "REAL DEFAULT 0"),
            ("latency_p95_us", "REAL"),
            ("has_latency_samples", "INTEGER DEFAULT 0"),
            ("latency_is_proxy", "INTEGER DEFAULT 0"),
            ("pdcp_provenance", "TEXT DEFAULT ''"),
            ("offered_load_kbps", "REAL DEFAULT 0"),
            ("backlog_bytes", "INTEGER DEFAULT 0"),
            ("packet_loss_percent", "REAL"),
            ("vehicle_id", "TEXT"),
            ("vehicle_role", "TEXT"),
            ("autonomy_state", "TEXT"),
            ("risk_state", "TEXT"),
            ("speed_mps", "REAL"),
            ("heading_deg", "REAL"),
            ("lane_id", "TEXT"),
            ("waypoint_id", "TEXT"),
            ("position_x", "REAL"),
            ("position_y", "REAL"),
            ("position_z", "REAL"),
            ("connectivity", "TEXT"),
            ("gateway_id", "TEXT"),
            ("domain", "TEXT"),
            ("mobility_profile", "TEXT"),
        ):
            if column_name not in existing_ue_columns:
                cursor.execute(f"ALTER TABLE ue_metrics ADD COLUMN {column_name} {column_def}")
        
        # Índices para métricas extendidas
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_extended_timestamp 
            ON extended_metrics(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_extended_decision_id
            ON extended_metrics(decision_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ue_timestamp 
            ON ue_metrics(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ue_imsi 
            ON ue_metrics(imsi)
        """)
        
        # Tabela de comandos de energia (novo)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS energy_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                command TEXT NOT NULL,
                power_percent INTEGER DEFAULT 100,
                ru_count INTEGER DEFAULT 2,
                mmwave_count INTEGER DEFAULT 1,
                reason TEXT,
                timestamp_ns INTEGER,
                power_w REAL,
                calibration_version TEXT,
                requested_power_percent REAL,
                applied_power_percent REAL,
                power_safety_override_reason TEXT DEFAULT '',
                native_sim_energy_j REAL,
                native_sim_power_w REAL,
                energy_reference_source TEXT DEFAULT '',
                absolute_scale_valid INTEGER DEFAULT 0,
                physical_wattmeter_available INTEGER DEFAULT 0,
                calibration_corpus_id TEXT DEFAULT '',
                calibration_fit_error REAL,
                calibration_rank_valid INTEGER DEFAULT 0,
                decision_id INTEGER,
                action_correlation_id TEXT DEFAULT '',
                native_control_sequence INTEGER,
                action_origin TEXT DEFAULT '',
                application_status TEXT DEFAULT '',
                command_sent INTEGER DEFAULT 0,
                actuation_confirmed INTEGER DEFAULT 0,
                actuation_confirmation_source TEXT DEFAULT '',
                observed_power_percent REAL,
                observed_power_w REAL,
                observed_ru_count INTEGER,
                observed_mmwave_count INTEGER,
                confirmation_decision_id INTEGER,
                observed_allocation_fraction REAL,
                native_active_dl_symbols INTEGER,
                native_dl_symbol_capacity INTEGER,
                native_cell_ids_json TEXT DEFAULT '[]',
                native_observation_version TEXT DEFAULT ''
            )
        """)
        existing_energy_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(energy_commands)").fetchall()
        }
        for column_name, column_def in (
            ("timestamp_ns", "INTEGER"),
            ("power_w", "REAL"),
            ("calibration_version", "TEXT"),
            ("requested_power_percent", "REAL"),
            ("applied_power_percent", "REAL"),
            ("power_safety_override_reason", "TEXT DEFAULT ''"),
            ("native_sim_energy_j", "REAL"),
            ("native_sim_power_w", "REAL"),
            ("energy_reference_source", "TEXT DEFAULT ''"),
            ("absolute_scale_valid", "INTEGER DEFAULT 0"),
            ("physical_wattmeter_available", "INTEGER DEFAULT 0"),
            ("calibration_corpus_id", "TEXT DEFAULT ''"),
            ("calibration_fit_error", "REAL"),
            ("calibration_rank_valid", "INTEGER DEFAULT 0"),
            ("decision_id", "INTEGER"),
            ("action_correlation_id", "TEXT DEFAULT ''"),
            ("native_control_sequence", "INTEGER"),
            ("action_origin", "TEXT DEFAULT ''"),
            ("application_status", "TEXT DEFAULT ''"),
            ("command_sent", "INTEGER DEFAULT 0"),
            ("actuation_confirmed", "INTEGER DEFAULT 0"),
            ("actuation_confirmation_source", "TEXT DEFAULT ''"),
            ("observed_power_percent", "REAL"),
            ("observed_power_w", "REAL"),
            ("observed_ru_count", "INTEGER"),
            ("observed_mmwave_count", "INTEGER"),
            ("confirmation_decision_id", "INTEGER"),
            ("observed_allocation_fraction", "REAL"),
            ("native_active_dl_symbols", "INTEGER"),
            ("native_dl_symbol_capacity", "INTEGER"),
            ("native_cell_ids_json", "TEXT DEFAULT '[]'"),
            ("native_observation_version", "TEXT DEFAULT ''"),
        ):
            if column_name not in existing_energy_columns:
                cursor.execute(f"ALTER TABLE energy_commands ADD COLUMN {column_name} {column_def}")
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_energy_timestamp 
            ON energy_commands(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_energy_action_correlation
            ON energy_commands(action_correlation_id)
        """)

        # Independent ns-3 evidence for a TA-SAM control transaction. A
        # command ACK proves transport; this table proves what ns-3 observed
        # at the cell/PHY boundary.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasam_control_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                sim_time_s REAL NOT NULL,
                cell_id INTEGER NOT NULL,
                transaction_id INTEGER NOT NULL,
                power_transaction_id INTEGER NOT NULL DEFAULT 0,
                scheduler_transaction_id INTEGER NOT NULL DEFAULT 0,
                active_ues INTEGER,
                tx_power_percent REAL,
                tx_power_dbm REAL,
                nominal_tx_power_dbm REAL,
                observation_kind TEXT DEFAULT 'legacy_snapshot',
                policy_active INTEGER DEFAULT 0,
                policy_expiry_sim_time REAL,
                source_generation TEXT DEFAULT '',
                association_epoch TEXT DEFAULT '',
                native_allocated_dl_symbols INTEGER,
                native_dl_symbol_capacity INTEGER,
                requested_discretionary_dl_symbols_bp INTEGER,
                applied_discretionary_dl_symbols_bp INTEGER,
                mandatory_dl_symbols INTEGER,
                discretionary_dl_symbols INTEGER,
                withheld_dl_symbols INTEGER,
                sleep_transaction_id TEXT DEFAULT '',
                native_allocation_source TEXT DEFAULT '',
                native_allocation_fraction REAL,
                campaign_id TEXT DEFAULT '',
                campaign_generation TEXT DEFAULT '',
                decision_id INTEGER,
                action_correlation_id TEXT DEFAULT '',
                native_control_sequence INTEGER,
                evidence_version TEXT DEFAULT 'v1',
                power_lease_fresh INTEGER NOT NULL DEFAULT 1,
                imported_at INTEGER NOT NULL,
                UNIQUE(source_path, sim_time_s, cell_id, scheduler_transaction_id,
                       power_transaction_id, observation_kind)
            )
        """)
        existing_observation_columns = {
            row[1] for row in cursor.execute(
                "PRAGMA table_info(tasam_control_observations)"
            ).fetchall()
        }
        for column_name, column_def in (
            ("power_transaction_id", "INTEGER NOT NULL DEFAULT 0"),
            ("scheduler_transaction_id", "INTEGER NOT NULL DEFAULT 0"),
            ("nominal_tx_power_dbm", "REAL"),
            ("observation_kind", "TEXT DEFAULT 'legacy_snapshot'"),
            ("policy_active", "INTEGER DEFAULT 0"),
            ("policy_expiry_sim_time", "REAL"),
            ("source_generation", "TEXT DEFAULT ''"),
            ("association_epoch", "TEXT DEFAULT ''"),
            ("native_allocated_dl_symbols", "INTEGER"),
            ("native_dl_symbol_capacity", "INTEGER"),
            ("requested_discretionary_dl_symbols_bp", "INTEGER"),
            ("applied_discretionary_dl_symbols_bp", "INTEGER"),
            ("mandatory_dl_symbols", "INTEGER"),
            ("discretionary_dl_symbols", "INTEGER"),
            ("withheld_dl_symbols", "INTEGER"),
            ("sleep_transaction_id", "TEXT DEFAULT ''"),
            ("native_allocation_source", "TEXT DEFAULT ''"),
            ("native_allocation_fraction", "REAL"),
            ("campaign_id", "TEXT DEFAULT ''"),
            ("campaign_generation", "TEXT DEFAULT ''"),
            ("decision_id", "INTEGER"),
            ("action_correlation_id", "TEXT DEFAULT ''"),
            ("native_control_sequence", "INTEGER"),
            ("evidence_version", "TEXT DEFAULT 'v1'"),
            ("power_lease_fresh", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if column_name not in existing_observation_columns:
                cursor.execute(
                    f"ALTER TABLE tasam_control_observations ADD COLUMN "
                    f"{column_name} {column_def}"
                )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasam_native_ingest_state (
                source_path TEXT PRIMARY KEY,
                inode INTEGER NOT NULL DEFAULT 0,
                byte_offset INTEGER NOT NULL DEFAULT 0,
                header TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                reset_count INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
        """)
        existing_ingest_columns = {
            row[1] for row in cursor.execute(
                "PRAGMA table_info(tasam_native_ingest_state)"
            ).fetchall()
        }
        for column_name, column_def in (
            ("last_error", "TEXT NOT NULL DEFAULT ''"),
            ("reset_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column_name not in existing_ingest_columns:
                cursor.execute(
                    f"ALTER TABLE tasam_native_ingest_state ADD COLUMN "
                    f"{column_name} {column_def}"
                )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasam_native_evidence_ingest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                campaign_id TEXT DEFAULT '',
                source_generation TEXT DEFAULT '',
                evidence_version TEXT DEFAULT '',
                inode INTEGER DEFAULT 0,
                start_offset INTEGER DEFAULT 0,
                end_offset INTEGER DEFAULT 0,
                imported_rows INTEGER DEFAULT 0,
                invalid_rows INTEGER DEFAULT 0,
                reset_detected INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                sim_time_s REAL,
                created_at INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasam_native_ingest_event
            ON tasam_native_evidence_ingest(source_path, created_at)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasam_native_associations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                sim_time_s REAL NOT NULL,
                cell_id INTEGER NOT NULL,
                rnti INTEGER NOT NULL,
                imsi INTEGER NOT NULL,
                association_epoch TEXT DEFAULT '',
                campaign_id TEXT DEFAULT '',
                source_generation TEXT DEFAULT '',
                evidence_version TEXT DEFAULT 'v1',
                imported_at INTEGER NOT NULL,
                UNIQUE(source_path, sim_time_s, cell_id, rnti, imsi, association_epoch)
            )
        """)
        existing_association_columns = {
            row[1] for row in cursor.execute(
                "PRAGMA table_info(tasam_native_associations)"
            ).fetchall()
        }
        for column_name, column_def in (
            ("campaign_id", "TEXT DEFAULT ''"),
            ("source_generation", "TEXT DEFAULT ''"),
            # v5 association rows carry the same control identity used by
            # the power/policy trace.  These are additive so v1-v4 traces
            # remain readable for diagnostics, but strict v5 validators can
            # reject rows that cannot be correlated to a decision.
            ("transaction_id", "INTEGER DEFAULT 0"),
            ("native_control_sequence", "INTEGER"),
            ("decision_id", "INTEGER"),
            ("action_correlation_id", "TEXT DEFAULT ''"),
        ):
            if column_name not in existing_association_columns:
                cursor.execute(
                    f"ALTER TABLE tasam_native_associations ADD COLUMN "
                    f"{column_name} {column_def}"
                )
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasam_native_association_lookup
            ON tasam_native_associations(cell_id, imsi, sim_time_s)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasam_native_association_identity
            ON tasam_native_associations(campaign_id, source_generation,
                                         transaction_id, native_control_sequence,
                                         decision_id, action_correlation_id)
        """)
        # A change-based UE trace cannot express an empty cell.  Snapshot
        # rows make a successful drain observable without treating absence of
        # an IMSI row as proof of handover.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasam_native_association_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                sim_time_s REAL NOT NULL,
                cell_id INTEGER NOT NULL,
                association_epoch TEXT DEFAULT '',
                attached_ue_count INTEGER NOT NULL,
                campaign_id TEXT DEFAULT '',
                source_generation TEXT DEFAULT '',
                evidence_version TEXT DEFAULT 'v1',
                transaction_id INTEGER DEFAULT 0,
                native_control_sequence INTEGER,
                decision_id INTEGER,
                action_correlation_id TEXT DEFAULT '',
                sleep_transaction_id TEXT DEFAULT '',
                imported_at INTEGER NOT NULL,
                UNIQUE(source_path, sim_time_s, cell_id, association_epoch)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasam_native_association_snapshot_lookup
            ON tasam_native_association_snapshots(cell_id, sim_time_s)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasam_observation_transaction
            ON tasam_control_observations(power_transaction_id, scheduler_transaction_id,
                                          cell_id, sim_time_s)
        """)

        # A command can be confirmed several controller cycles after its ACK.
        # Keeping this state in SQLite makes confirmation restart-safe and
        # prevents a single in-memory slot from discarding late native rows.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasam_pending_native_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                decision_id INTEGER NOT NULL,
                action_correlation_id TEXT NOT NULL UNIQUE,
                native_control_sequence INTEGER NOT NULL,
                issued_sim_time_s REAL,
                ttl_s REAL NOT NULL DEFAULT 5.0,
                expected_cells_json TEXT NOT NULL,
                expected_power_percent TEXT,
                status TEXT NOT NULL DEFAULT 'pending_confirmation',
                confirmation_json TEXT DEFAULT '{}',
                invalid_reason TEXT DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasam_pending_status
            ON tasam_pending_native_actions(status, native_control_sequence)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resource_allocation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                controller_id TEXT,
                target_policy_id TEXT,
                decision_domain TEXT,
                action_semantics TEXT,
                resource_budget REAL DEFAULT 0,
                usable_budget REAL DEFAULT 0,
                d_ran REAL DEFAULT 0,
                d_ai REAL DEFAULT 0,
                r_ran REAL DEFAULT 0,
                r_ai REAL DEFAULT 0,
                delta_r_ran REAL DEFAULT 0,
                delta_r_ai REAL DEFAULT 0,
                ran_completion_ratio REAL DEFAULT 0,
                ai_completion_ratio REAL DEFAULT 0,
                utilization_ratio REAL DEFAULT 0,
                allocation_state TEXT DEFAULT 'ALLOWED',
                healthy_streak INTEGER DEFAULT 0,
                floor_total_ran REAL DEFAULT 0,
                floor_total_ai REAL DEFAULT 0,
                reinforcement_ran REAL DEFAULT 0,
                reinforcement_ai REAL DEFAULT 0,
                floor_feasible INTEGER DEFAULT 1,
                topology_valid INTEGER DEFAULT 0,
                per_ue_floor_json TEXT DEFAULT '{}',
                per_ue_allocation_json TEXT DEFAULT '[]',
                per_ue_floor_violation_count INTEGER DEFAULT 0,
                per_ue_application_status TEXT DEFAULT 'not_applicable',
                per_ue_policy_id TEXT DEFAULT '',
                per_ue_ack_timestamp REAL DEFAULT 0,
                per_ue_ack_reason TEXT DEFAULT '',
                tasam_allocation_target_ran REAL DEFAULT 0,
                tasam_allocation_target_ai REAL DEFAULT 0,
                tasam_allocation_predicted_ran REAL DEFAULT 0,
                tasam_allocation_predicted_ai REAL DEFAULT 0,
                tasam_allocation_prediction_loss REAL DEFAULT 0,
                tasam_allocation_target_source TEXT DEFAULT '',
                tasam_allocation_target_feasible INTEGER DEFAULT 1,
                resource_floor_policy TEXT DEFAULT 'sla_per_ue_v1',
                snapshot_json TEXT,
                UNIQUE(timestamp)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_resource_alloc_timestamp
            ON resource_allocation_history(timestamp)
        """)
        existing_resource_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(resource_allocation_history)").fetchall()
        }
        for column_name, column_def in (
            ("usable_budget", "REAL DEFAULT 0"),
            ("allocation_state", "TEXT DEFAULT 'ALLOWED'"),
            ("healthy_streak", "INTEGER DEFAULT 0"),
            ("floor_total_ran", "REAL DEFAULT 0"),
            ("floor_total_ai", "REAL DEFAULT 0"),
            ("reinforcement_ran", "REAL DEFAULT 0"),
            ("reinforcement_ai", "REAL DEFAULT 0"),
            ("floor_feasible", "INTEGER DEFAULT 1"),
            ("topology_valid", "INTEGER DEFAULT 0"),
            ("per_ue_floor_json", "TEXT DEFAULT '{}'"),
            ("per_ue_allocation_json", "TEXT DEFAULT '[]'"),
            ("per_ue_floor_violation_count", "INTEGER DEFAULT 0"),
            ("per_ue_application_status", "TEXT DEFAULT 'not_applicable'"),
            ("per_ue_policy_id", "TEXT DEFAULT ''"),
            ("per_ue_ack_timestamp", "REAL DEFAULT 0"),
            ("per_ue_ack_reason", "TEXT DEFAULT ''"),
            ("tasam_allocation_target_ran", "REAL DEFAULT 0"),
            ("tasam_allocation_target_ai", "REAL DEFAULT 0"),
            ("tasam_allocation_predicted_ran", "REAL DEFAULT 0"),
            ("tasam_allocation_predicted_ai", "REAL DEFAULT 0"),
            ("tasam_allocation_prediction_loss", "REAL DEFAULT 0"),
            ("tasam_allocation_target_source", "TEXT DEFAULT ''"),
            ("tasam_allocation_target_feasible", "INTEGER DEFAULT 1"),
            ("resource_floor_policy", "TEXT DEFAULT 'sla_per_ue_v1'"),
        ):
            if column_name not in existing_resource_columns:
                cursor.execute(f"ALTER TABLE resource_allocation_history ADD COLUMN {column_name} {column_def}")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS marl_global_state_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                topology_id TEXT,
                logical_du_count INTEGER DEFAULT 0,
                total_demand REAL DEFAULT 0,
                usable_budget REAL DEFAULT 0,
                state_vector_json TEXT,
                snapshot_json TEXT,
                UNIQUE(timestamp)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_marl_global_timestamp
            ON marl_global_state_history(timestamp)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS marl_slice_state_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                slice_id TEXT NOT NULL,
                ue_count INTEGER DEFAULT 0,
                demand REAL DEFAULT 0,
                allocation REAL DEFAULT 0,
                qos_pressure REAL DEFAULT 0,
                completion_ratio REAL DEFAULT 0,
                min_qos_met REAL DEFAULT 0,
                budget_share REAL DEFAULT 0,
                snapshot_json TEXT,
                UNIQUE(timestamp, slice_id)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_marl_slice_timestamp
            ON marl_slice_state_history(timestamp)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS marl_du_state_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                du_id TEXT NOT NULL,
                role TEXT,
                primary_slice TEXT,
                ue_count INTEGER DEFAULT 0,
                demand_share REAL DEFAULT 0,
                allocation_share REAL DEFAULT 0,
                slice_mix_json TEXT,
                state_vector_json TEXT,
                snapshot_json TEXT,
                UNIQUE(timestamp, du_id)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_marl_du_timestamp
            ON marl_du_state_history(timestamp)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS marl_shadow_comparison_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                policy_id TEXT,
                source TEXT,
                checkpoint_readiness TEXT,
                available INTEGER DEFAULT 0,
                recommend_shadow INTEGER DEFAULT 0,
                live_score REAL DEFAULT 0,
                shadow_score REAL DEFAULT 0,
                score_delta REAL DEFAULT 0,
                base_sla_resource_score_delta REAL DEFAULT 0,
                causal_score_delta REAL DEFAULT 0,
                tasam_checkpoint_valid INTEGER DEFAULT 0,
                tasam_fallback_used INTEGER DEFAULT 0,
                tasam_evidence_valid INTEGER DEFAULT 0,
                live_allocator_algorithm TEXT DEFAULT '',
                live_power_percent REAL,
                shadow_power_percent REAL,
                live_power_w REAL,
                shadow_power_w REAL,
                energy_saving_fraction REAL DEFAULT 0,
                resource_saving_fraction REAL DEFAULT 0,
                energy_model_version TEXT DEFAULT '',
                energy_valid INTEGER DEFAULT 0,
                resource_valid INTEGER DEFAULT 0,
                native_sim_energy_j REAL,
                native_sim_power_w REAL,
                energy_reference_source TEXT DEFAULT '',
                absolute_scale_valid INTEGER DEFAULT 0,
                physical_wattmeter_available INTEGER DEFAULT 0,
                infra_resource_index REAL,
                infra_resource_saving_fraction REAL,
                calibration_corpus_id TEXT DEFAULT '',
                calibration_fit_error REAL,
                calibration_rank_valid INTEGER DEFAULT 0,
                live_ran_completion_est REAL DEFAULT 0,
                shadow_ran_completion_est REAL DEFAULT 0,
                live_ai_completion_est REAL DEFAULT 0,
                shadow_ai_completion_est REAL DEFAULT 0,
                live_total_shortfall REAL DEFAULT 0,
                shadow_total_shortfall REAL DEFAULT 0,
                live_budget_gap REAL DEFAULT 0,
                shadow_budget_gap REAL DEFAULT 0,
                delta_r_ran REAL DEFAULT 0,
                delta_r_ai REAL DEFAULT 0,
                snapshot_json TEXT,
                UNIQUE(timestamp)
            )
        """)
        existing_shadow_columns = {
            row[1] for row in cursor.execute(
                "PRAGMA table_info(marl_shadow_comparison_history)"
            ).fetchall()
        }
        for column_name, column_def in (
            ("base_sla_resource_score_delta", "REAL DEFAULT 0"),
            ("causal_score_delta", "REAL DEFAULT 0"),
            ("tasam_checkpoint_valid", "INTEGER DEFAULT 0"),
            ("tasam_fallback_used", "INTEGER DEFAULT 0"),
            ("tasam_evidence_valid", "INTEGER DEFAULT 0"),
            ("live_allocator_algorithm", "TEXT DEFAULT ''"),
            ("live_power_percent", "REAL"),
            ("shadow_power_percent", "REAL"),
            ("live_power_w", "REAL"),
            ("shadow_power_w", "REAL"),
            ("energy_saving_fraction", "REAL DEFAULT 0"),
            ("resource_saving_fraction", "REAL DEFAULT 0"),
            ("energy_model_version", "TEXT DEFAULT ''"),
            ("energy_valid", "INTEGER DEFAULT 0"),
            ("resource_valid", "INTEGER DEFAULT 0"),
            ("native_sim_energy_j", "REAL"),
            ("native_sim_power_w", "REAL"),
            ("energy_reference_source", "TEXT DEFAULT ''"),
            ("absolute_scale_valid", "INTEGER DEFAULT 0"),
            ("physical_wattmeter_available", "INTEGER DEFAULT 0"),
            ("infra_resource_index", "REAL"),
            ("infra_resource_saving_fraction", "REAL"),
            ("calibration_corpus_id", "TEXT DEFAULT ''"),
            ("calibration_fit_error", "REAL"),
            ("calibration_rank_valid", "INTEGER DEFAULT 0"),
        ):
            if column_name not in existing_shadow_columns:
                cursor.execute(
                    f"ALTER TABLE marl_shadow_comparison_history ADD COLUMN {column_name} {column_def}"
                )
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_marl_shadow_comparison_timestamp
            ON marl_shadow_comparison_history(timestamp)
        """)

        # Snapshot agregado da App2-Monitoramento.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app2_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                total_sensors INTEGER DEFAULT 0,
                active_sensors INTEGER DEFAULT 0,
                connected_sensors INTEGER DEFAULT 0,
                error_sensors INTEGER DEFAULT 0,
                low_battery_sensors INTEGER DEFAULT 0,
                gateways_count INTEGER DEFAULT 0,
                connectivity_modes_count INTEGER DEFAULT 0,
                avg_temperature_c REAL DEFAULT 0,
                avg_humidity_percent REAL DEFAULT 0,
                avg_soil_conductivity REAL DEFAULT 0,
                avg_battery_percent REAL DEFAULT 0,
                avg_power_mw REAL DEFAULT 0,
                packet_loss_percent REAL DEFAULT 0,
                tx_packets INTEGER DEFAULT 0,
                rx_packets INTEGER DEFAULT 0,
                lost_packets INTEGER DEFAULT 0,
                avg_latency_ms REAL DEFAULT 0,
                avg_rssi_dbm REAL DEFAULT 0,
                network_utilization_percent REAL DEFAULT 0,
                delivery_success_percent REAL DEFAULT 100,
                alerts_count INTEGER DEFAULT 0,
                snapshot_json TEXT,
                UNIQUE(timestamp)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_app2_snapshots_timestamp
            ON app2_snapshots(timestamp)
        """)

        # Leituras individuais da App2 para historico central no Data Lake.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app2_sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                sensor_id INTEGER,
                sensor_type TEXT,
                unit TEXT,
                value REAL,
                status TEXT,
                domain TEXT,
                connectivity TEXT,
                gateway_id TEXT,
                battery_percent REAL,
                latency_ms REAL,
                packet_loss_percent REAL,
                rssi_dbm REAL,
                power_mw REAL,
                tx_interval_s REAL,
                packets_tx INTEGER DEFAULT 0,
                UNIQUE(timestamp, sensor_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_app2_sensor_readings_timestamp
            ON app2_sensor_readings(timestamp)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_app2_sensor_readings_sensor_id
            ON app2_sensor_readings(sensor_id)
        """)

        # Snapshot agregado da App3-Veicular.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app3_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                total_vehicles INTEGER DEFAULT 0,
                ego_present INTEGER DEFAULT 0,
                high_risk_vehicles INTEGER DEFAULT 0,
                medium_risk_vehicles INTEGER DEFAULT 0,
                degraded_autonomy_vehicles INTEGER DEFAULT 0,
                max_latency_ms REAL DEFAULT 0,
                max_packet_loss_percent REAL DEFAULT 0,
                max_speed_mps REAL DEFAULT 0,
                snapshot_json TEXT,
                UNIQUE(timestamp)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_app3_snapshots_timestamp
            ON app3_snapshots(timestamp)
        """)

        # Eventos de conflito O-RAN (artigo00): xApps/agentes -> parâmetros -> KPIs.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conflict_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                datetime TEXT NOT NULL,
                source_agent TEXT,
                target_agent TEXT,
                conflict_type TEXT NOT NULL,
                parameter TEXT,
                affected_service TEXT,
                affected_kpi TEXT,
                observed_value REAL,
                threshold_value REAL,
                decision TEXT,
                mitigation_action TEXT,
                reason TEXT,
                confidence REAL,
                graph_path TEXT,
                UNIQUE(timestamp, conflict_type, affected_kpi)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_conflict_events_timestamp
            ON conflict_events(timestamp)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_conflict_events_type
            ON conflict_events(conflict_type)
        """)

        # Delayed feedback from the rApp judge.  The outcome is stored in a
        # separate table because the real observation arrives after the
        # original decision has already been persisted.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS judge_outcome_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER,
                decision_timestamp INTEGER NOT NULL,
                observed_timestamp INTEGER NOT NULL,
                selected_assistant TEXT DEFAULT '',
                correct_verdict TEXT DEFAULT 'UNKNOWN',
                observed INTEGER DEFAULT 0,
                outcome_reward REAL DEFAULT 0,
                severity_penalty REAL DEFAULT 0,
                armd_credit REAL DEFAULT 0,
                tasam_credit REAL DEFAULT 0,
                armd_state_credit REAL DEFAULT 0,
                tasam_state_credit REAL DEFAULT 0,
                tasam_resource_credit REAL DEFAULT 0,
                tasam_observed_error REAL DEFAULT 0,
                tasam_continuous_reward REAL DEFAULT 0,
                tasam_reward_source TEXT DEFAULT '',
                tasam_error_components_json TEXT DEFAULT '{}',
                tasam_action_applied INTEGER DEFAULT 0,
                tasam_category_credit REAL DEFAULT 0,
                tasam_category_penalty REAL DEFAULT 0,
                tasam_category_error INTEGER DEFAULT 0,
                tasam_training_category_credit REAL DEFAULT 0,
                tasam_training_category_penalty REAL DEFAULT 0,
                tasam_training_reward REAL DEFAULT 0,
                tasam_predicted_verdict TEXT DEFAULT '',
                tasam_observed_verdict TEXT DEFAULT '',
                credit_assignment TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                feedback_json TEXT,
                observed_metric_id INTEGER,
                feedback_status TEXT DEFAULT 'observed',
                feedback_missing_reason TEXT DEFAULT ''
            )
        """)
        existing_judge_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(judge_outcome_history)").fetchall()
        }
        for column_name, column_def in (
            ("decision_id", "INTEGER"),
            ("decision_stage_name", "TEXT DEFAULT ''"),
            ("observed_stage_name", "TEXT DEFAULT ''"),
            ("stage_boundary_feedback", "INTEGER DEFAULT 0"),
            ("nominal_expected_verdict", "TEXT DEFAULT 'UNKNOWN'"),
            ("tasam_observed_error", "REAL DEFAULT 0"),
            ("tasam_continuous_reward", "REAL DEFAULT 0"),
            ("tasam_reward_source", "TEXT DEFAULT ''"),
            ("tasam_error_components_json", "TEXT DEFAULT '{}'"),
            ("tasam_action_applied", "INTEGER DEFAULT 0"),
            ("tasam_category_credit", "REAL DEFAULT 0"),
            ("tasam_category_penalty", "REAL DEFAULT 0"),
            ("tasam_category_error", "INTEGER DEFAULT 0"),
            ("tasam_training_category_credit", "REAL DEFAULT 0"),
            ("tasam_training_category_penalty", "REAL DEFAULT 0"),
            ("tasam_training_reward", "REAL DEFAULT 0"),
            ("tasam_predicted_verdict", "TEXT DEFAULT ''"),
            ("tasam_observed_verdict", "TEXT DEFAULT ''"),
            ("observed_metric_id", "INTEGER"),
            ("feedback_status", "TEXT DEFAULT 'observed'"),
            ("feedback_missing_reason", "TEXT DEFAULT ''"),
        ):
            if column_name not in existing_judge_columns:
                cursor.execute(f"ALTER TABLE judge_outcome_history ADD COLUMN {column_name} {column_def}")

        self._migrate_judge_outcome_timestamp_key(cursor)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_judge_outcome_decision_timestamp
            ON judge_outcome_history(decision_timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_judge_outcome_decision_id
            ON judge_outcome_history(decision_id)
        """)

        # Canonical persistent economic replay.  JSONL traces are useful as
        # short-lived trainer inputs, but the SQLite row is the durable source
        # of truth and survives controller polling windows and restarts.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasam_economic_transition_history (
                decision_id INTEGER PRIMARY KEY,
                decision_timestamp INTEGER NOT NULL,
                observed_timestamp INTEGER NOT NULL,
                source_metric_snapshot_id INTEGER,
                observed_metric_snapshot_id INTEGER,
                economic_action_contract TEXT DEFAULT '',
                economic_application_status TEXT DEFAULT '',
                economic_transition_eligible INTEGER DEFAULT 0,
                economic_training_eligible INTEGER DEFAULT 0,
                economic_promotion_eligible INTEGER DEFAULT 0,
                realized_energy_saving_fraction REAL,
                realized_allocation_saving_fraction REAL,
                tasam_online_reward REAL,
                tasam_energy_reward REAL,
                tasam_allocation_reward REAL,
                tasam_sla_penalty REAL,
                calibration_version TEXT DEFAULT '',
                replay_schema TEXT DEFAULT 'greenran.tasam.economic_transition.v1',
                replay_compact_bytes INTEGER DEFAULT 0,
                transition_json TEXT NOT NULL,
                transition_sha256 TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        existing_economic_transition_columns = {
            row[1] for row in cursor.execute(
                "PRAGMA table_info(tasam_economic_transition_history)"
            ).fetchall()
        }
        for column_name, column_def in (
            ("replay_schema", "TEXT DEFAULT 'greenran.tasam.economic_transition.v1'"),
            ("replay_compact_bytes", "INTEGER DEFAULT 0"),
        ):
            if column_name not in existing_economic_transition_columns:
                cursor.execute(
                    f"ALTER TABLE tasam_economic_transition_history "
                    f"ADD COLUMN {column_name} {column_def}"
                )
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasam_economic_transition_eligible
            ON tasam_economic_transition_history(economic_training_eligible, decision_id)
        """)

        # Additional indexes for performance
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_extended_metrics_timestamp
            ON extended_metrics(timestamp)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_decisions_timestamp
            ON decisions_history(timestamp)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_extended_metrics_cvar
            ON extended_metrics(cvar_per_ue_us)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ue_metrics_timestamp
            ON ue_metrics(timestamp)
        """)

        self.conn.commit()
        print(f"[DataLake] Tabelas e índices inicializados em {self.db_path}")
    
    def record_metric(self, timestamp=None, latency_us=0, cameras_active=0, 
                      critical_cameras=0, energy_state=None, slicer_state=None):
        """
        Registra uma métrica no histórico.
        
        Args:
            timestamp: Unix timestamp (default: now)
            latency_us: Latência em microssegundos
            cameras_active: Número de câmeras ativas
            critical_cameras: Número de câmeras críticas
            energy_state: Estado do Energy Saver
            slicer_state: Estado do SLICER
        
        Nota: Registros com latência_us = 0 são ignorados (dados inválidos).
        """
        if timestamp is None:
            timestamp = int(time.time())
        
        if latency_us == 0:
            return
        
        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO metrics_history 
                (timestamp, datetime, latency_us, cameras_active, critical_cameras, 
                 energy_state, slicer_state)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, dt_str, latency_us, cameras_active, critical_cameras,
                  energy_state, slicer_state))
            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar métrica: {e}")
    
    def record_decision(self, decision=None, timestamp=None):
        """
        Registra uma decisão do rApp.

        Args:
            decision: Dict com campos:
                - decision: 'BLOCKED', 'ALLOWED', 'CONDITIONAL'
                - reason: 'SLA_VIOLATED', 'LOW_ACTIVITY', etc
                - confidence: float 0-1
                - pattern: tipo de padrão detectado
                - agent_override: bool
                - energy_state: estado do energy saver
                - slicer_state: estado do slicer
                - ml_rf_prediction: dict com predição ML
                - ml_influenced: bool
            timestamp: Unix timestamp (default: now)
        """
        if timestamp is None:
            timestamp = int(time.time())

        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        if decision is None:
            decision = {}
        decision_id = None
        try:
            metric_snapshot_id = int(decision.get('metric_snapshot_id') or 0) or None
        except (TypeError, ValueError):
            metric_snapshot_id = None
        snapshot_sequence_id = str(
            decision.get('snapshot_sequence_id')
            or (f"pdcp:{metric_snapshot_id}" if metric_snapshot_id is not None else "")
        )

        decision_str = decision.get('energy_saver', 'UNKNOWN')
        reason = decision.get('reason', '')
        confidence = decision.get('confidence', 0.0)
        pattern = decision.get('pattern', '')
        agent_override = 1 if decision.get('agent_override', False) else 0
        energy_state = decision.get('energy_state', '')
        slicer_state = decision.get('slicer_state', '')
        collection_event_stage_name = str(decision.get('collection_event_stage_name', '') or '')
        collection_event_target_domain = str(decision.get('collection_event_target_domain', '') or '')
        try:
            collection_event_cycle = int(decision.get('collection_event_cycle', 0) or 0)
        except (TypeError, ValueError):
            collection_event_cycle = 0
        try:
            collection_event_stage_index = int(decision.get('collection_event_stage_index', 0) or 0)
        except (TypeError, ValueError):
            collection_event_stage_index = 0
        try:
            collection_event_generated_at = int(decision.get('collection_event_generated_at', 0) or 0)
        except (TypeError, ValueError):
            collection_event_generated_at = 0
        collection_event_stage_authoritative = 1 if decision.get('collection_event_stage_authoritative', False) else 0

        # ML prediction data
        ml_prediction = decision.get('ml_rf_prediction', {})
        ml_decision = ml_prediction.get('decision', '')
        ml_confidence = ml_prediction.get('confidence', 0.0)
        ml_predicted_cvar = ml_prediction.get('predicted_cvar_ms', 0.0)
        ml_influenced = 1 if decision.get('ml_influenced', False) else 0
        armd_enabled = 1 if decision.get('armd_enabled', False) else 0
        armd_proposal_present = 1 if decision.get('armd_proposal_present', False) else 0
        armd_proposal_valid = 1 if decision.get('armd_proposal_valid', False) else 0
        armd_proposal_kind = decision.get('armd_proposal_kind', '')
        armd_actuation_applied = 1 if decision.get('armd_actuation_applied', False) else 0
        armd_proposal_json = json.dumps(decision.get('armd_proposal') or {}, ensure_ascii=False, sort_keys=True)
        armd_mode = decision.get('armd_mode', '')
        armd_scenario = decision.get('armd_scenario', '')
        armd_source = decision.get('armd_source', '')
        armd_confidence = decision.get('armd_confidence', 0.0)
        armd_override_applied = 1 if decision.get('armd_override_applied', False) else 0
        armd_safety_level = str(decision.get('armd_safety_level', 'UNKNOWN') or 'UNKNOWN')
        armd_role = str(decision.get('armd_role', 'advisory') or 'advisory')
        armd_advisory_only = 1 if decision.get('armd_advisory_only', armd_role == 'advisory') else 0
        armd_hard_veto = 1 if decision.get('armd_hard_veto', armd_safety_level == 'HARD_VETO') else 0
        tasam_operating_permission = 1 if decision.get('tasam_operating_permission', False) else 0
        tasam_envelope = decision.get('tasam_policy_envelope') or {}
        tasam_envelope_min_power = decision.get('tasam_envelope_min_power', tasam_envelope.get('min_power_percent'))
        tasam_envelope_max_power = decision.get('tasam_envelope_max_power', tasam_envelope.get('max_power_percent'))
        economic_isolation_source = str(decision.get('economic_isolation_source', '') or '')
        tasam_advisor = decision.get('tasam_advisor', {}) or {}
        advisor_arbitration = decision.get('advisor_arbitration', {}) or {}
        tasam_enabled = 1 if decision.get('tasam_enabled', tasam_advisor.get('enabled', False)) else 0
        tasam_proposal_present = 1 if decision.get('tasam_proposal_present', False) else 0
        tasam_proposal_valid = 1 if decision.get('tasam_proposal_valid', False) else 0
        tasam_proposal_kind = decision.get('tasam_proposal_kind', '')
        tasam_proposal_json = json.dumps(decision.get('tasam_proposal') or {}, ensure_ascii=False, sort_keys=True)
        tasam_mode = decision.get('tasam_mode', tasam_advisor.get('mode', ''))
        tasam_policy_id = decision.get('tasam_policy_id', tasam_advisor.get('policy_id', ''))
        tasam_source = decision.get('tasam_source', tasam_advisor.get('source', ''))
        tasam_confidence = float(decision.get('tasam_confidence', tasam_advisor.get('confidence', 0.0)) or 0.0)
        tasam_valid = 1 if decision.get('tasam_valid', tasam_advisor.get('valid', False)) else 0
        tasam_checkpoint_valid = 1 if decision.get(
            'tasam_checkpoint_valid', (decision.get('tasam_source', '') == 'checkpoint' and tasam_proposal_valid)
        ) else 0
        tasam_fallback_used = 1 if decision.get(
            'tasam_fallback_used', decision.get('tasam_source', '') in {'heuristic', 'mixed'}
        ) else 0
        tasam_evidence_valid = 1 if decision.get('tasam_evidence_valid', False) else 0
        rl_policy_runtime = decision.get('rl_policy_runtime', {}) or {}
        live_allocator_algorithm = str(
            decision.get('live_allocator_algorithm', rl_policy_runtime.get('algorithm', '')) or ''
        )
        tasam_would_influence = 1 if decision.get('tasam_would_influence', tasam_advisor.get('would_influence', False)) else 0
        tasam_energy_decision = decision.get('tasam_energy_decision', ((tasam_advisor.get('energy_advice') or {}).get('decision', '')))
        tasam_energy_action = decision.get('tasam_energy_action', ((tasam_advisor.get('energy_advice') or {}).get('action', '')))
        advisor_arbitration_mode = advisor_arbitration.get('mode', '')
        advisor_arbitration_present = 1 if advisor_arbitration.get('arbitration_present', False) else 0
        advisor_proposal_pair_complete = 1 if advisor_arbitration.get('proposal_pair_complete', False) else 0
        advisor_rapp_final_decision = advisor_arbitration.get('rapp_final_decision', '')
        advisor_rapp_final_action = advisor_arbitration.get('rapp_final_action', '')
        advisor_arbitration_winner = advisor_arbitration.get('winner', '')
        advisor_arbitration_score = float(
            max(
                advisor_arbitration.get('armd_score', 0.0) or 0.0,
                advisor_arbitration.get('tasam_score', 0.0) or 0.0,
            )
        )
        rapp_judge_mode = str((decision.get('rapp_judge_result') or {}).get('mode', '') or '')
        rapp_judge_conflict_type = str(decision.get('rapp_judge_conflict_type', '') or '')
        rapp_judge_reason = str(decision.get('rapp_judge_reason', '') or '')
        selected_assistant = str(decision.get('selected_assistant', '') or '')
        selected_proposal_id = str(decision.get('selected_proposal_id', '') or '')
        proposal_applied_exactly = 1 if decision.get('proposal_applied_exactly', False) else 0
        external_last_resort_used = 1 if decision.get('external_last_resort_used', False) else 0
        selected_proposal_json = json.dumps(decision.get('selected_assistant_proposal') or {}, ensure_ascii=False, sort_keys=True)
        resource_allocation = decision.get('resource_allocation', {}) or {}
        energy_advice = tasam_advisor.get('energy_advice') or {}
        reward_components = decision.get('tasam_reward_components') or decision.get('reward_components') or {}
        tasam_power_percent = float(
            decision.get('tasam_power_percent', energy_advice.get('power_percent', resource_allocation.get('power_percent', 100.0))) or 100.0
        )
        tasam_power_applied_percent = float(
            decision.get('tasam_power_applied_percent', tasam_power_percent) or tasam_power_percent
        )
        causal_comparison = ((decision.get('resource_allocation') or {}).get('marl_shadow') or {}).get('comparison') or {}
        live_power_percent = decision.get('live_power_percent', causal_comparison.get('live_power_percent'))
        shadow_power_percent = decision.get('shadow_power_percent', causal_comparison.get('shadow_power_percent'))
        live_power_w = decision.get('live_power_w', causal_comparison.get('live_power_w'))
        shadow_power_w = decision.get('shadow_power_w', causal_comparison.get('shadow_power_w'))
        energy_saving_fraction = float(decision.get('energy_saving_fraction', causal_comparison.get('energy_saving_fraction', 0.0)) or 0.0)
        resource_saving_fraction = float(decision.get('resource_saving_fraction', causal_comparison.get('resource_saving_fraction', 0.0)) or 0.0)
        causal_score_delta = float(decision.get('causal_score_delta', causal_comparison.get('causal_score_delta', 0.0)) or 0.0)
        energy_model_version = str(decision.get('energy_model_version', causal_comparison.get('energy_model_version', '')) or '')
        power_safety_override_reason = str(
            decision.get('power_safety_override_reason', '') or ''
        )
        economic_action = decision.get('economic_action') or {}
        if not isinstance(economic_action, dict):
            economic_action = {}
        economic_action_contract = str(
            decision.get('economic_action_contract', economic_action.get('contract', '')) or ''
        )
        economic_application_status = str(
            decision.get('economic_application_status', economic_action.get('application_status', '')) or ''
        )
        economic_rejection_reason = str(
            decision.get('economic_rejection_reason', economic_action.get('rejection_reason', '')) or ''
        )
        actuation_confirmed = 1 if decision.get(
            'actuation_confirmed', economic_action.get('actuation_confirmed', False)
        ) else 0
        actuation_confirmation_source = str(
            decision.get(
                'actuation_confirmation_source',
                economic_action.get('actuation_confirmation_source', ''),
            ) or ''
        )
        observed_power_percent = decision.get(
            'observed_power_percent', economic_action.get('observed_power_percent')
        )
        observed_power_w = decision.get(
            'observed_power_w', economic_action.get('observed_power_w')
        )
        observed_ru_count = decision.get(
            'observed_ru_count', economic_action.get('observed_ru_count')
        )
        observed_mmwave_count = decision.get(
            'observed_mmwave_count', economic_action.get('observed_mmwave_count')
        )
        confirmation_decision_id = decision.get(
            'confirmation_decision_id', economic_action.get('confirmation_decision_id')
        )
        economic_outcome_invalid_reason = str(
            decision.get(
                'economic_outcome_invalid_reason',
                economic_action.get('outcome_invalid_reason', decision.get('economic_invalid_reason', '')),
            ) or ''
        )
        pdcp_coverage = decision.get('pdcp_loss_coverage', economic_action.get('pdcp_loss_coverage', {})) or {}
        if not isinstance(pdcp_coverage, dict):
            pdcp_coverage = {}
        topology_valid = 1 if decision.get(
            'topology_valid', resource_allocation.get('topology_valid', False)
        ) else 0
        try:
            pdcp_metric_snapshot_id = int(
                decision.get('pdcp_metric_snapshot_id') or decision.get('observed_metric_id') or 0
            ) or None
        except (TypeError, ValueError):
            pdcp_metric_snapshot_id = None
        economic_transition_eligible = 1 if decision.get(
            'economic_transition_eligible', economic_action.get('economic_transition_eligible', False)
        ) else 0
        tasam_power_cost_penalty = float(
            decision.get('tasam_power_cost_penalty', reward_components.get('power_cost_penalty', 0.0)) or 0.0
        )
        tasam_completion_shortfall_penalty = float(
            decision.get('tasam_completion_shortfall_penalty', reward_components.get('completion_shortfall_penalty', 0.0)) or 0.0
        )
        tasam_underallocation_penalty = float(
            decision.get('tasam_underallocation_penalty', reward_components.get('underallocation_penalty', 0.0)) or 0.0
        )
        marl_shadow = resource_allocation.get('marl_shadow') or {}
        allocation_head = marl_shadow.get('allocation_head') or {}
        allocation_projection = marl_shadow.get('allocation_projection') or {}
        tasam_allocation_target_ran = float(
            decision.get('tasam_allocation_target_ran', allocation_projection.get('ran_min_share', 0.0)) or 0.0
        )
        tasam_allocation_target_ai = float(
            decision.get('tasam_allocation_target_ai', allocation_projection.get('ai_min_share', 0.0)) or 0.0
        )
        tasam_allocation_predicted_ran = float(
            decision.get('tasam_allocation_predicted_ran', allocation_head.get('predicted_ran_share', 0.0)) or 0.0
        )
        tasam_allocation_predicted_ai = float(
            decision.get('tasam_allocation_predicted_ai', allocation_head.get('predicted_ai_share', 0.0)) or 0.0
        )
        tasam_allocation_prediction_loss = float(
            decision.get('tasam_allocation_prediction_loss', allocation_head.get('prediction_loss', 0.0)) or 0.0
        )
        tasam_allocation_target_source = str(
            decision.get('tasam_allocation_target_source', 'runtime_completion_projection' if allocation_projection.get('applied') else '') or ''
        )
        tasam_allocation_target_feasible = 1 if allocation_projection.get('feasible', True) else 0
        tasam_reward_components_json = json.dumps(reward_components, ensure_ascii=False, sort_keys=True)
        rl_policy_id = rl_policy_runtime.get('policy_id', '')
        rl_policy_family = rl_policy_runtime.get('family', '')
        rl_policy_algorithm = rl_policy_runtime.get('algorithm', '')
        resource_controller_id = resource_allocation.get('controller_id', '')
        resource_budget = float(resource_allocation.get('resource_budget', 0.0) or 0.0)
        usable_budget = float(resource_allocation.get('usable_budget', resource_budget) or resource_budget)
        ran_demand = float(resource_allocation.get('d_ran', 0.0) or 0.0)
        ai_demand = float(resource_allocation.get('d_ai', 0.0) or 0.0)
        ran_allocation = float(resource_allocation.get('r_ran', 0.0) or 0.0)
        ai_allocation = float(resource_allocation.get('r_ai', 0.0) or 0.0)
        ran_completion_ratio = float(resource_allocation.get('ran_completion_ratio', 0.0) or 0.0)
        ai_completion_ratio = float(resource_allocation.get('ai_completion_ratio', 0.0) or 0.0)
        utilization_ratio = float(resource_allocation.get('utilization_ratio', 0.0) or 0.0)
        allocation_state = str(resource_allocation.get('allocation_state', decision.get('energy_saver', 'ALLOWED')) or 'ALLOWED').upper()
        healthy_streak = int(resource_allocation.get('healthy_streak', 0) or 0)
        floor_total_ran = float(resource_allocation.get('floor_total_ran', 0.0) or 0.0)
        floor_total_ai = float(resource_allocation.get('floor_total_ai', 0.0) or 0.0)
        reinforcement_ran = float(resource_allocation.get('reinforcement_ran', 0.0) or 0.0)
        reinforcement_ai = float(resource_allocation.get('reinforcement_ai', 0.0) or 0.0)
        floor_feasible = 1 if resource_allocation.get('floor_feasible', True) else 0
        per_ue_floor_json = json.dumps(resource_allocation.get('per_ue_floor') or [], ensure_ascii=False, sort_keys=True)
        resource_floor_policy = str(resource_allocation.get('floor_policy', decision.get('resource_floor_policy', 'sla_per_ue_v1')) or 'sla_per_ue_v1')
        network_health = decision.get('network_health', {}) or {}
        network_improvement_pct = float(network_health.get('network_improvement_pct', 0.0) or 0.0)
        cvar_improvement_pct = float(network_health.get('cvar_improvement_pct', 0.0) or 0.0)
        p95_improvement_pct = float(network_health.get('p95_improvement_pct', 0.0) or 0.0)
        baseline_cvar_us = float(network_health.get('baseline_cvar_us', 0.0) or 0.0)
        baseline_p95_us = float(network_health.get('baseline_p95_us', 0.0) or 0.0)
        improvement_source = str(network_health.get('improvement_source', '') or '')
        improvement_valid = 1 if network_health.get('improvement_valid', False) else 0
        ta_sam_actuation_applied = 1 if decision.get('ta_sam_actuation_applied', False) else 0
        tasam_actuation_applied = 1 if decision.get('tasam_actuation_applied', decision.get('ta_sam_actuation_applied', False)) else 0
        training_run_invalid = 1 if decision.get('training_run_invalid', False) else 0
        invalid_reason = str(decision.get('invalid_reason', '') or '')
        tasam_policy_envelope = decision.get('tasam_policy_envelope') or (
            (decision.get('tasam_advisor') or {}).get('armd_policy_envelope') or {}
        )
        tasam_policy_envelope_applied = 1 if (
            decision.get('tasam_policy_envelope_applied', tasam_policy_envelope.get('applied', False))
        ) else 0
        tasam_policy_envelope_source = str(
            decision.get('tasam_policy_envelope_source', 'armd' if tasam_policy_envelope_applied else '') or ''
        )
        tasam_policy_envelope_json = json.dumps(tasam_policy_envelope, ensure_ascii=False, sort_keys=True)
        control_trial_mode = str(decision.get('control_trial_mode', '') or '')
        effective_policy_algorithm = str(decision.get('effective_policy_algorithm', '') or '')
        effective_policy_source = str(decision.get('effective_policy_source', '') or '')
        control_trial_reason = str(decision.get('control_trial_reason', '') or '')

        # DEBUG: Log dos dados de ML que chegam
        if ml_decision:
            print(f"[DataLake DEBUG] ML Decision: '{ml_decision}', Confidence: {ml_confidence}, CVaR: {ml_predicted_cvar}")
        else:
            # Tentar obter do pattern_analysis como fallback
            pattern_analysis = decision.get('pattern_analysis', {})
            if pattern_analysis:
                pattern_action = pattern_analysis.get('recommended_action', '')
                if pattern_action:
                    ml_decision = pattern_action
                    ml_confidence = pattern_analysis.get('confidence', 0.0)
                    print(f"[DataLake DEBUG] ML from pattern_analysis: '{ml_decision}', Confidence: {ml_confidence}")

        try:
            cursor = self.conn.cursor()
            columns = [
                'timestamp', 'datetime', 'metric_snapshot_id', 'snapshot_sequence_id', 'pairing_schedule_id', 'decision', 'reason', 'confidence', 'pattern',
                'agent_override', 'energy_state', 'slicer_state',
                'collection_event_stage_name', 'collection_event_target_domain',
                'collection_event_cycle', 'collection_event_stage_index',
                'collection_event_generated_at', 'collection_event_stage_authoritative',
                'ml_decision', 'ml_confidence', 'ml_predicted_cvar_ms', 'ml_influenced',
                'armd_enabled', 'armd_proposal_present', 'armd_proposal_valid', 'armd_proposal_kind', 'armd_actuation_applied', 'armd_proposal_json',
                'armd_mode', 'armd_scenario', 'armd_source', 'armd_confidence', 'armd_override_applied',
                'armd_safety_level', 'armd_role', 'armd_advisory_only', 'armd_hard_veto',
                'tasam_operating_permission', 'tasam_envelope_min_power', 'tasam_envelope_max_power',
                'economic_isolation_source',
                'tasam_enabled', 'tasam_proposal_present', 'tasam_proposal_valid', 'tasam_proposal_kind', 'tasam_proposal_json',
                'tasam_mode', 'tasam_policy_id', 'tasam_source', 'tasam_confidence',
                'tasam_valid', 'tasam_checkpoint_valid', 'tasam_fallback_used', 'tasam_evidence_valid',
                'live_allocator_algorithm', 'tasam_would_influence', 'tasam_energy_decision', 'tasam_energy_action',
                'tasam_power_percent', 'tasam_power_applied_percent', 'power_safety_override_reason',
                'economic_action_contract', 'economic_application_status', 'economic_rejection_reason',
                'actuation_confirmed', 'actuation_confirmation_source', 'observed_power_percent',
                'observed_power_w', 'observed_ru_count', 'observed_mmwave_count',
                'confirmation_decision_id',
                'economic_outcome_invalid_reason', 'pdcp_coverage_json', 'topology_valid',
                'pdcp_metric_snapshot_id',
                'economic_transition_eligible', 'economic_action_json',
                'live_power_percent', 'shadow_power_percent', 'live_power_w', 'shadow_power_w',
                'energy_saving_fraction', 'resource_saving_fraction', 'causal_score_delta', 'energy_model_version',
                'tasam_power_cost_penalty', 'tasam_completion_shortfall_penalty',
                'tasam_underallocation_penalty', 'tasam_allocation_target_ran', 'tasam_allocation_target_ai',
                'tasam_allocation_predicted_ran', 'tasam_allocation_predicted_ai',
                'tasam_allocation_prediction_loss', 'tasam_allocation_target_source',
                'tasam_allocation_target_feasible', 'tasam_reward_components_json',
                'native_evidence_ingest_json',
                'advisor_arbitration_mode', 'advisor_arbitration_present', 'advisor_proposal_pair_complete',
                'advisor_rapp_final_decision', 'advisor_rapp_final_action',
                'advisor_arbitration_winner', 'advisor_arbitration_score',
                'rapp_judge_mode', 'rapp_judge_conflict_type', 'rapp_judge_reason',
                'selected_assistant', 'selected_proposal_id', 'proposal_applied_exactly',
                'external_last_resort_used', 'selected_proposal_json',
                'rl_policy_id', 'rl_policy_family', 'rl_policy_algorithm', 'resource_controller_id',
                'resource_budget', 'usable_budget', 'ran_demand', 'ai_demand', 'ran_allocation', 'ai_allocation',
                'ran_completion_ratio', 'ai_completion_ratio', 'utilization_ratio',
                'allocation_state', 'healthy_streak', 'floor_total_ran', 'floor_total_ai',
                'reinforcement_ran', 'reinforcement_ai', 'floor_feasible', 'per_ue_floor_json',
                'resource_floor_policy',
                'network_improvement_pct', 'cvar_improvement_pct', 'p95_improvement_pct',
                'baseline_cvar_us', 'baseline_p95_us', 'improvement_source', 'improvement_valid',
                'ta_sam_actuation_applied', 'tasam_policy_envelope_applied',
                'tasam_actuation_applied', 'training_run_invalid', 'invalid_reason',
                'tasam_policy_envelope_source', 'tasam_policy_envelope_json',
                'control_trial_mode', 'effective_policy_algorithm',
                'effective_policy_source', 'control_trial_reason',
            ]
            values = [
                timestamp, dt_str, metric_snapshot_id, snapshot_sequence_id,
                str(decision.get('pairing_schedule_id', '') or ''), decision_str, reason, confidence, pattern,
                agent_override, energy_state, slicer_state,
                collection_event_stage_name, collection_event_target_domain,
                collection_event_cycle, collection_event_stage_index,
                collection_event_generated_at, collection_event_stage_authoritative,
                ml_decision, ml_confidence, ml_predicted_cvar, ml_influenced,
                armd_enabled, armd_proposal_present, armd_proposal_valid, armd_proposal_kind, armd_actuation_applied, armd_proposal_json,
                armd_mode, armd_scenario, armd_source, armd_confidence, armd_override_applied,
                armd_safety_level, armd_role, armd_advisory_only, armd_hard_veto,
                tasam_operating_permission, tasam_envelope_min_power, tasam_envelope_max_power,
                economic_isolation_source,
                tasam_enabled, tasam_proposal_present, tasam_proposal_valid, tasam_proposal_kind, tasam_proposal_json,
                tasam_mode, tasam_policy_id, tasam_source, tasam_confidence,
                tasam_valid, tasam_checkpoint_valid, tasam_fallback_used, tasam_evidence_valid,
                live_allocator_algorithm, tasam_would_influence, tasam_energy_decision, tasam_energy_action,
                tasam_power_percent, tasam_power_applied_percent, power_safety_override_reason,
                economic_action_contract, economic_application_status, economic_rejection_reason,
                actuation_confirmed, actuation_confirmation_source, observed_power_percent,
                observed_power_w, observed_ru_count, observed_mmwave_count,
                confirmation_decision_id,
                economic_outcome_invalid_reason, json.dumps(pdcp_coverage, ensure_ascii=False, sort_keys=True),
                topology_valid, pdcp_metric_snapshot_id,
                economic_transition_eligible, json.dumps(economic_action, ensure_ascii=False, sort_keys=True),
                live_power_percent, shadow_power_percent, live_power_w, shadow_power_w,
                energy_saving_fraction, resource_saving_fraction, causal_score_delta, energy_model_version,
                tasam_power_cost_penalty, tasam_completion_shortfall_penalty,
                tasam_underallocation_penalty, tasam_allocation_target_ran, tasam_allocation_target_ai,
                tasam_allocation_predicted_ran, tasam_allocation_predicted_ai,
                tasam_allocation_prediction_loss, tasam_allocation_target_source,
                tasam_allocation_target_feasible, tasam_reward_components_json,
                json.dumps(decision.get('native_evidence_ingest') or {}, ensure_ascii=False, sort_keys=True),
                advisor_arbitration_mode, advisor_arbitration_present, advisor_proposal_pair_complete,
                advisor_rapp_final_decision, advisor_rapp_final_action,
                advisor_arbitration_winner, advisor_arbitration_score,
                rapp_judge_mode, rapp_judge_conflict_type, rapp_judge_reason,
                selected_assistant, selected_proposal_id, proposal_applied_exactly,
                external_last_resort_used, selected_proposal_json,
                rl_policy_id, rl_policy_family, rl_policy_algorithm, resource_controller_id,
                resource_budget, usable_budget, ran_demand, ai_demand, ran_allocation, ai_allocation,
                ran_completion_ratio, ai_completion_ratio, utilization_ratio,
                allocation_state, healthy_streak, floor_total_ran, floor_total_ai,
                reinforcement_ran, reinforcement_ai, floor_feasible, per_ue_floor_json,
                resource_floor_policy,
                network_improvement_pct, cvar_improvement_pct, p95_improvement_pct,
                baseline_cvar_us, baseline_p95_us, improvement_source, improvement_valid,
                ta_sam_actuation_applied, tasam_policy_envelope_applied,
                tasam_actuation_applied, training_run_invalid, invalid_reason,
                tasam_policy_envelope_source, tasam_policy_envelope_json,
                control_trial_mode, effective_policy_algorithm,
                effective_policy_source, control_trial_reason,
            ]
            placeholders = ', '.join('?' for _ in columns)
            cursor.execute(
                f"INSERT OR REPLACE INTO decisions_history ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            decision_id = int(cursor.lastrowid or 0) or None
            if decision_id is not None:
                # These fields are deliberately normalized from the nested
                # action contract as well as persisted in JSON.  Delayed
                # feedback updates the same columns below, so reports and
                # replay do not need to parse historical blobs.
                economic_execution_mode = str(
                    decision.get(
                        'economic_execution_mode',
                        economic_action.get('economic_execution_mode', 'diagnostic'),
                    ) or 'diagnostic'
                )
                economic_safety_isolated = 1 if decision.get(
                    'economic_safety_isolated',
                    economic_action.get('economic_safety_isolated', False),
                ) else 0
                economic_safety_reason = str(
                    decision.get(
                        'economic_safety_isolation_reason',
                        economic_action.get('economic_safety_isolation_reason', ''),
                    ) or ''
                )
                realized_energy = economic_action.get('realized_energy_saving_fraction')
                realized_allocation = economic_action.get('realized_allocation_saving_fraction')
                cursor.execute(
                    """
                    UPDATE decisions_history
                       SET economic_execution_mode = ?,
                           economic_safety_isolated = ?,
                           economic_safety_isolation_reason = ?,
                           economic_training_eligible = ?,
                           economic_promotion_eligible = ?,
                           realized_energy_saving_fraction = ?,
                           realized_allocation_saving_fraction = ?,
                           tasam_online_reward = ?,
                           tasam_energy_reward = ?,
                           tasam_allocation_reward = ?,
                           tasam_sla_penalty = ?
                     WHERE id = ?
                    """,
                    (
                        economic_execution_mode,
                        economic_safety_isolated,
                        economic_safety_reason,
                        1 if decision.get('economic_training_eligible', False) else 0,
                        1 if decision.get('economic_promotion_eligible', False) else 0,
                        realized_energy,
                        realized_allocation,
                        decision.get('tasam_online_reward'),
                        decision.get('tasam_energy_reward'),
                        decision.get('tasam_allocation_reward'),
                        decision.get('tasam_sla_penalty'),
                        decision_id,
                    ),
                )
            self.conn.commit()
            if decision_id is not None:
                correlation_id = str(
                    economic_action.get('correlation_id') or decision.get('economic_action_correlation_id', '') or ''
                )
                if correlation_id:
                    self.associate_energy_command_decision(correlation_id, decision_id)
            if resource_allocation:
                self.record_resource_allocation_snapshot(resource_allocation, timestamp=timestamp)
            self.record_conflict_from_decision(decision, timestamp=timestamp)
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar decisão: {e}")
        return decision_id

    def record_resource_allocation_snapshot(self, snapshot, timestamp=None):
        """Persist the resource-allocation snapshot used by the TA-SAM DRL line."""
        if not isinstance(snapshot, dict) or not snapshot:
            return

        if timestamp is None:
            timestamp = int(time.time())

        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        marl_shadow = snapshot.get('marl_shadow') or {}
        allocation_head = marl_shadow.get('allocation_head') or {}
        allocation_projection = marl_shadow.get('allocation_projection') or {}

        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO resource_allocation_history
                (timestamp, datetime, controller_id, target_policy_id, decision_domain,
                 action_semantics, resource_budget, usable_budget, d_ran, d_ai, r_ran, r_ai,
                 delta_r_ran, delta_r_ai, ran_completion_ratio, ai_completion_ratio,
                 utilization_ratio, allocation_state, healthy_streak, floor_total_ran,
                 floor_total_ai, reinforcement_ran, reinforcement_ai, floor_feasible, topology_valid,
                 per_ue_floor_json, per_ue_allocation_json,
                 per_ue_floor_violation_count, per_ue_application_status,
                 per_ue_policy_id, per_ue_ack_timestamp, per_ue_ack_reason,
                 tasam_allocation_target_ran, tasam_allocation_target_ai,
                 tasam_allocation_predicted_ran, tasam_allocation_predicted_ai,
                 tasam_allocation_prediction_loss, tasam_allocation_target_source,
                 tasam_allocation_target_feasible,
                 resource_floor_policy, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                dt_str,
                snapshot.get('controller_id', ''),
                snapshot.get('target_policy_id', ''),
                snapshot.get('decision_domain', ''),
                snapshot.get('action_semantics', ''),
                float(snapshot.get('resource_budget', 0.0) or 0.0),
                float(snapshot.get('usable_budget', snapshot.get('resource_budget', 0.0)) or 0.0),
                float(snapshot.get('d_ran', 0.0) or 0.0),
                float(snapshot.get('d_ai', 0.0) or 0.0),
                float(snapshot.get('r_ran', 0.0) or 0.0),
                float(snapshot.get('r_ai', 0.0) or 0.0),
                float(snapshot.get('delta_r_ran', 0.0) or 0.0),
                float(snapshot.get('delta_r_ai', 0.0) or 0.0),
                float(snapshot.get('ran_completion_ratio', 0.0) or 0.0),
                float(snapshot.get('ai_completion_ratio', 0.0) or 0.0),
                float(snapshot.get('utilization_ratio', 0.0) or 0.0),
                str(snapshot.get('allocation_state', 'ALLOWED') or 'ALLOWED').upper(),
                int(snapshot.get('healthy_streak', 0) or 0),
                float(snapshot.get('floor_total_ran', 0.0) or 0.0),
                float(snapshot.get('floor_total_ai', 0.0) or 0.0),
                float(snapshot.get('reinforcement_ran', 0.0) or 0.0),
                float(snapshot.get('reinforcement_ai', 0.0) or 0.0),
                1 if snapshot.get('floor_feasible', True) else 0,
                1 if snapshot.get('topology_valid', False) else 0,
                json.dumps(snapshot.get('per_ue_floor') or [], ensure_ascii=False, sort_keys=True),
                json.dumps(snapshot.get('per_ue_allocation') or [], ensure_ascii=False, sort_keys=True),
                int(snapshot.get('per_ue_floor_violation_count', 0) or 0),
                str(snapshot.get('per_ue_application_status', 'not_applicable') or 'not_applicable'),
                str(snapshot.get('per_ue_policy_id', '') or ''),
                float(snapshot.get('per_ue_ack_timestamp', 0.0) or 0.0),
                str(snapshot.get('per_ue_ack_reason', '') or ''),
                float(snapshot.get('tasam_allocation_target_ran', allocation_projection.get('ran_min_share', 0.0)) or 0.0),
                float(snapshot.get('tasam_allocation_target_ai', allocation_projection.get('ai_min_share', 0.0)) or 0.0),
                float(snapshot.get('tasam_allocation_predicted_ran', allocation_head.get('predicted_ran_share', 0.0)) or 0.0),
                float(snapshot.get('tasam_allocation_predicted_ai', allocation_head.get('predicted_ai_share', 0.0)) or 0.0),
                float(snapshot.get('tasam_allocation_prediction_loss', allocation_head.get('prediction_loss', 0.0)) or 0.0),
                str(snapshot.get('tasam_allocation_target_source', allocation_projection.get('reason', '')) or ''),
                1 if snapshot.get('tasam_allocation_target_feasible', allocation_projection.get('feasible', True)) else 0,
                str(snapshot.get('floor_policy', 'sla_per_ue_v1') or 'sla_per_ue_v1'),
                json.dumps(snapshot, ensure_ascii=False),
            ))
            self.conn.commit()
            self.record_article_marl_state(snapshot, timestamp=timestamp)
            self.record_marl_shadow_comparison(snapshot, timestamp=timestamp)
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar snapshot de recursos: {e}")

    def record_judge_outcome(
        self,
        decision,
        feedback,
        observation=None,
        observed_timestamp=None,
        transition=None,
    ):
        """Persist delayed ARMD/TA-SAM credit after a real observation."""
        if not isinstance(decision, dict) or not isinstance(feedback, dict):
            return
        decision_timestamp = int(decision.get('timestamp', 0) or 0)
        if decision_timestamp <= 0:
            return
        observation = observation if isinstance(observation, dict) else {}
        if observed_timestamp is None:
            observed_timestamp = int(observation.get('timestamp', time.time()) or time.time())
        judge_result = decision.get('rapp_judge_result') or {}
        selected = str(
            decision.get('selected_assistant')
            or judge_result.get('selected_advocate', '')
            or ''
        )
        try:
            decision_id = int(decision.get('decision_id') or decision.get('id') or 0) or None
        except (TypeError, ValueError):
            decision_id = None
        try:
            observed_metric_id = int(
                feedback.get('observed_metric_id')
                or observation.get('observed_metric_id')
                or observation.get('metric_snapshot_id')
                or 0
            ) or None
        except (TypeError, ValueError):
            observed_metric_id = None
        feedback_status = str(feedback.get('feedback_status', 'observed') or 'observed')
        feedback_missing_reason = str(feedback.get('feedback_missing_reason', '') or '')
        if decision_id is not None:
            self.conn.execute(
                "DELETE FROM judge_outcome_history WHERE decision_id = ? AND feedback_status = 'missing'",
                (decision_id,),
            )
        decision_stage_name = str(
            feedback.get('decision_stage_name')
            or decision.get('collection_event_stage_name', '')
            or ''
        )
        observed_stage_name = str(feedback.get('observed_stage_name', '') or '')
        stage_boundary_feedback = 1 if feedback.get('stage_boundary_feedback', False) else 0
        nominal_expected_verdict = str(
            feedback.get('nominal_expected_verdict', 'UNKNOWN') or 'UNKNOWN'
        )
        try:
            compact_feedback = _compact_judge_feedback_payload(decision_id, feedback, observation)
            self.conn.execute("""
                INSERT INTO judge_outcome_history
                (decision_id, decision_timestamp, observed_timestamp, selected_assistant,
                correct_verdict, observed, outcome_reward, severity_penalty,
                 armd_credit, tasam_credit, armd_state_credit,
                 tasam_state_credit, tasam_resource_credit, tasam_observed_error,
                 tasam_continuous_reward, tasam_reward_source,
                 tasam_error_components_json, tasam_action_applied,
                 tasam_category_credit, tasam_category_penalty,
                tasam_category_error, tasam_training_category_credit,
                tasam_training_category_penalty, tasam_training_reward,
                tasam_predicted_verdict,
                tasam_observed_verdict,
                 credit_assignment, reason, feedback_json,
                 decision_stage_name, observed_stage_name,
                 stage_boundary_feedback, nominal_expected_verdict,
                 observed_metric_id, feedback_status, feedback_missing_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision_id,
                decision_timestamp,
                int(observed_timestamp),
                selected,
                str(feedback.get('correct_verdict', 'UNKNOWN') or 'UNKNOWN'),
                1 if feedback.get('outcome_observed', False) else 0,
                float(feedback.get('outcome_reward', 0.0) or 0.0),
                float(feedback.get('severity_penalty', 0.0) or 0.0),
                float(feedback.get('armd_credit', 0.0) or 0.0),
                float(feedback.get('tasam_credit', 0.0) or 0.0),
                float(feedback.get('armd_state_credit', 0.0) or 0.0),
                float(feedback.get('tasam_state_credit', 0.0) or 0.0),
                float(feedback.get('tasam_resource_credit', 0.0) or 0.0),
                float(feedback.get('tasam_observed_error', 0.0) or 0.0),
                float(feedback.get('tasam_continuous_reward', 0.0) or 0.0),
                str(feedback.get('tasam_reward_source', '') or ''),
                json.dumps(feedback.get('tasam_error_components') or {}, ensure_ascii=False, sort_keys=True),
                1 if feedback.get('tasam_action_applied', decision.get('ta_sam_actuation_applied', False)) else 0,
                float(feedback.get('tasam_category_credit', feedback.get('tasam_state_credit', 0.0)) or 0.0),
                float(feedback.get('tasam_category_penalty', 0.0) or 0.0),
                1 if feedback.get('tasam_category_error', False) else 0,
                float(feedback.get('tasam_training_category_credit', feedback.get('tasam_category_credit', 0.0)) or 0.0),
                float(feedback.get('tasam_training_category_penalty', feedback.get('tasam_category_penalty', 0.0)) or 0.0),
                float(feedback.get('tasam_training_reward', feedback.get('tasam_training_category_credit', feedback.get('tasam_category_credit', 0.0))) or 0.0),
                str(feedback.get('tasam_predicted_verdict', '') or ''),
                str(feedback.get('tasam_observed_verdict', feedback.get('correct_verdict', '')) or ''),
                str(feedback.get('credit_assignment', '') or ''),
                str(observation.get('reason', '') or ''),
                json.dumps(compact_feedback, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                decision_stage_name,
                observed_stage_name,
                stage_boundary_feedback,
                nominal_expected_verdict,
                observed_metric_id,
                feedback_status,
                feedback_missing_reason,
            ))
            self.conn.commit()
            if decision_id is not None:
                self.update_decision_economic_outcome(
                    decision_id,
                    feedback,
                    observed_metric_id=observed_metric_id,
                    transition=transition,
                )
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar feedback do juiz: {e}")

    def update_decision_economic_outcome(
        self, decision_id, feedback, observed_metric_id=None, transition=None
    ):
        """Attach delayed, real-PDCP economic evidence to the original decision."""
        if not decision_id or not isinstance(feedback, dict):
            return False
        economic_action = feedback.get('economic_action') or {}
        if not isinstance(economic_action, dict):
            economic_action = {}
        coverage = feedback.get('pdcp_loss_coverage', economic_action.get('pdcp_loss_coverage', {})) or {}
        if not isinstance(coverage, dict):
            coverage = {}
        try:
            metric_id = int(
                observed_metric_id
                or feedback.get('pdcp_metric_snapshot_id')
                or feedback.get('observed_metric_id')
                or 0
            ) or None
        except (TypeError, ValueError):
            metric_id = None
        try:
            action_status = str(
                feedback.get('economic_application_status')
                or economic_action.get('application_status')
                or ''
            )
            # Delayed feedback is intentionally compact and may omit static
            # provenance that was already persisted with the decision.  Do
            # not turn an omitted field into a false value when closing the
            # pending native action: preserve the original decision flags,
            # while still honoring an explicit value supplied by the
            # validator.
            existing_provenance = self.conn.execute(
                """
                SELECT tasam_checkpoint_valid, tasam_fallback_used,
                       tasam_evidence_valid
                  FROM decisions_history
                 WHERE id = ?
                """,
                (int(decision_id),),
            ).fetchone()
            existing_checkpoint_valid = bool(existing_provenance[0]) if existing_provenance else False
            existing_fallback_used = bool(existing_provenance[1]) if existing_provenance else False
            existing_evidence_valid = bool(existing_provenance[2]) if existing_provenance else False
            actuation_confirmed = bool(
                feedback.get('actuation_confirmed', economic_action.get('actuation_confirmed', False))
            )
            # The command/ACK path is not evidence of application.  Keep the
            # normalized decision aligned with the delayed native observation:
            # only an observed, applied TA-SAM action may be marked applied.
            ta_sam_actuation_applied = bool(
                feedback.get('tasam_action_applied', feedback.get('ta_sam_actuation_applied', False))
                and actuation_confirmed
                and action_status == 'applied'
            )
            native_observation = economic_action.get('native_observation') or {}
            if not isinstance(native_observation, dict):
                native_observation = {}
            tasam_evidence_valid = bool(
                (
                    feedback['tasam_evidence_valid']
                    if 'tasam_evidence_valid' in feedback
                    else economic_action['tasam_evidence_valid']
                    if 'tasam_evidence_valid' in economic_action
                    else existing_evidence_valid
                )
                and actuation_confirmed
                and native_observation.get('valid', True)
            )
            checkpoint_valid = bool(
                feedback['tasam_checkpoint_valid']
                if 'tasam_checkpoint_valid' in feedback
                else economic_action['tasam_checkpoint_valid']
                if 'tasam_checkpoint_valid' in economic_action
                else existing_checkpoint_valid
            )
            fallback_used = bool(
                feedback['tasam_fallback_used']
                if 'tasam_fallback_used' in feedback
                else economic_action['tasam_fallback_used']
                if 'tasam_fallback_used' in economic_action
                else existing_fallback_used
            )
            economic_action.setdefault('tasam_checkpoint_valid', checkpoint_valid)
            economic_action.setdefault('tasam_fallback_used', fallback_used)
            economic_action.setdefault('tasam_evidence_valid', tasam_evidence_valid)
            observed_power_percent = (
                economic_action.get('observed_power_percent') if actuation_confirmed else None
            )
            energy_model_version = str(
                feedback.get('energy_model_version')
                or economic_action.get('energy_model_version')
                or ''
            )
            realized_energy = feedback.get(
                'realized_energy_saving_fraction', economic_action.get('realized_energy_saving_fraction')
            )
            realized_allocation = feedback.get(
                'realized_allocation_saving_fraction', economic_action.get('realized_allocation_saving_fraction')
            )
            causal_delta = feedback.get(
                'causal_score_delta', economic_action.get('causal_score_delta')
            )
            self.conn.execute(
                """
                UPDATE decisions_history
                       SET economic_outcome_invalid_reason = ?,
                       economic_transition_eligible = ?,
                       economic_action_json = ?,
                       economic_action_contract = ?,
                       economic_application_status = ?,
                       economic_rejection_reason = ?,
                       pdcp_coverage_json = ?,
                       topology_valid = ?,
                       pdcp_metric_snapshot_id = ?,
                       economic_execution_mode = ?,
                       economic_safety_isolated = ?,
                       economic_safety_isolation_reason = ?,
                       economic_training_eligible = ?,
                       economic_promotion_eligible = ?,
                       tasam_checkpoint_valid = ?,
                       tasam_fallback_used = ?,
                       tasam_evidence_valid = ?,
                       ta_sam_actuation_applied = ?,
                       tasam_actuation_applied = ?,
                       energy_saving_fraction = COALESCE(?, energy_saving_fraction),
                       resource_saving_fraction = COALESCE(?, resource_saving_fraction),
                       causal_score_delta = COALESCE(?, causal_score_delta),
                       energy_model_version = ?,
                       live_power_percent = ?,
                       live_power_w = ?,
                       tasam_power_applied_percent = ?,
                       actuation_confirmed = ?,
                       actuation_confirmation_source = ?,
                       observed_power_percent = ?,
                       observed_power_w = ?,
                       observed_ru_count = ?,
                       observed_mmwave_count = ?,
                       confirmation_decision_id = ?,
                       realized_energy_saving_fraction = ?,
                       realized_allocation_saving_fraction = ?,
                       tasam_online_reward = ?,
                       tasam_energy_reward = ?,
                       tasam_allocation_reward = ?,
                       tasam_sla_penalty = ?
                 WHERE id = ?
                """,
                (
                    str(
                        feedback.get('economic_outcome_invalid_reason')
                        or feedback.get('economic_invalid_reason')
                        or economic_action.get('outcome_invalid_reason', '')
                        or ''
                    ),
                    1 if feedback.get('economic_transition_eligible', False) else 0,
                    json.dumps(economic_action, ensure_ascii=False, sort_keys=True),
                    str(
                        feedback.get('economic_action_contract')
                        or economic_action.get('contract')
                        or ''
                    ),
                    action_status,
                    str(
                        feedback.get('economic_rejection_reason')
                        or economic_action.get('rejection_reason')
                        or feedback.get('economic_outcome_invalid_reason')
                        or feedback.get('economic_invalid_reason')
                        or ''
                    ),
                    json.dumps(coverage, ensure_ascii=False, sort_keys=True),
                    1 if feedback.get('topology_valid', coverage.get('topology_valid', False)) else 0,
                    metric_id,
                    str(
                        feedback.get('economic_execution_mode')
                        or economic_action.get('economic_execution_mode')
                        or ('safety_isolated' if feedback.get('economic_safety_isolated') else 'economic')
                    ),
                    1 if feedback.get(
                        'economic_safety_isolated',
                        economic_action.get('economic_safety_isolated', False),
                    ) else 0,
                    str(
                        feedback.get('economic_safety_isolation_reason')
                        or economic_action.get('economic_safety_isolation_reason', '')
                        or ''
                    ),
                    1 if feedback.get('economic_training_eligible', False) else 0,
                    1 if feedback.get('economic_promotion_eligible', False) else 0,
                    1 if checkpoint_valid else 0,
                    1 if fallback_used else 0,
                    1 if tasam_evidence_valid else 0,
                    1 if ta_sam_actuation_applied else 0,
                    1 if ta_sam_actuation_applied else 0,
                    realized_energy,
                    realized_allocation,
                    causal_delta,
                    energy_model_version,
                    feedback.get(
                        'live_power_percent',
                        economic_action.get('live_candidate', {}).get('power_percent'),
                    ),
                    feedback.get(
                        'live_power_w',
                        economic_action.get('live_candidate', {}).get('power_w'),
                    ),
                    observed_power_percent,
                    1 if actuation_confirmed else 0,
                    str(
                        feedback.get(
                            'actuation_confirmation_source',
                            economic_action.get('actuation_confirmation_source', ''),
                        ) or ''
                    ),
                    observed_power_percent,
                    economic_action.get('observed_power_w'),
                    economic_action.get('observed_ru_count'),
                    economic_action.get('observed_mmwave_count'),
                    economic_action.get('confirmation_decision_id'),
                    feedback.get('realized_energy_saving_fraction', economic_action.get('realized_energy_saving_fraction')),
                    feedback.get('realized_allocation_saving_fraction', economic_action.get('realized_allocation_saving_fraction')),
                    feedback.get('tasam_online_reward'),
                    feedback.get('tasam_energy_reward'),
                    feedback.get('tasam_allocation_reward'),
                    feedback.get('tasam_sla_penalty'),
                    int(decision_id),
                ),
            )
            transition = transition or feedback.get('economic_transition')
            # The economic history is the replay corpus, not a dump of every
            # categorical/shadow observation.  Keep those observations in
            # judge_outcome_history and decisions_history, but only close a
            # durable economic transition after a real applied-action result.
            transition_status = str(
                feedback.get('economic_application_status')
                or (feedback.get('economic_action') or {}).get('application_status')
                or ''
            )
            if (
                isinstance(transition, dict)
                and feedback.get('economic_transition_eligible', False)
                and transition_status == 'applied'
            ):
                self.record_economic_transition(
                    decision_id,
                    transition,
                    feedback,
                    observed_metric_id=metric_id,
                )
            self.conn.commit()
            return self.conn.total_changes > 0
        except (sqlite3.Error, TypeError, ValueError):
            return False

    def record_economic_transition(self, decision_id, transition, feedback, observed_metric_id=None):
        """Persist one fully observed economic transition idempotently."""
        if not decision_id or not isinstance(transition, dict) or not isinstance(feedback, dict):
            return False
        action_preview = feedback.get('economic_action') or {}
        if not isinstance(action_preview, dict):
            action_preview = {}
        if (
            not feedback.get('economic_transition_eligible', False)
            or str(
                feedback.get('economic_application_status')
                or action_preview.get('application_status')
                or ''
            ) != 'applied'
        ):
            return False
        decision = transition.get('decision') or {}
        next_decision = transition.get('next_decision') or {}
        if not isinstance(decision, dict) or not isinstance(next_decision, dict):
            return False
        action = feedback.get('economic_action') or decision.get('economic_action') or {}
        if not isinstance(action, dict):
            action = {}
        try:
            decision_timestamp = int(decision.get('timestamp') or 0)
            observed_timestamp = int(next_decision.get('timestamp') or time.time())
        except (TypeError, ValueError):
            return False

        # A decision may be persisted between the periodic MARL-state writes.
        # Store its already-computed state again under the *decision's exact
        # timestamp* so the durable economic row has an unambiguous source
        # and destination.  We never use a nearest-state fallback in replay.
        def capture_exact_state(event, timestamp):
            allocation = event.get('resource_allocation') or event.get('action') or {}
            marl_state = allocation.get('article_marl_state') if isinstance(allocation, dict) else None
            if not isinstance(marl_state, dict):
                return False
            du_states = marl_state.get('du_states') or []
            global_state = marl_state.get('global_state') or {}
            if not isinstance(du_states, list) or not isinstance(global_state, dict):
                return False
            self.record_article_marl_state(
                {
                    'article_marl_state': marl_state,
                    'usable_budget': allocation.get('usable_budget', allocation.get('resource_budget', 0.0)),
                },
                timestamp=timestamp,
            )
            return True

        source_state_captured = capture_exact_state(decision, decision_timestamp)
        observed_state_captured = capture_exact_state(next_decision, observed_timestamp)
        payload = _compact_economic_transition_payload(
            decision_id, decision, next_decision, feedback, action
        )
        payload['exact_state_capture'] = {
            'source': source_state_captured,
            'observed': observed_state_captured,
            'matching': 'timestamp_exact',
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        try:
            source_metric_id = int(decision.get('metric_snapshot_id') or 0) or None
            observed_id = int(
                observed_metric_id
                or feedback.get('pdcp_metric_snapshot_id')
                or next_decision.get('metric_snapshot_id')
                or 0
            ) or None
            self.conn.execute(
                """
                INSERT OR REPLACE INTO tasam_economic_transition_history
                (decision_id, decision_timestamp, observed_timestamp,
                 source_metric_snapshot_id, observed_metric_snapshot_id,
                 economic_action_contract, economic_application_status,
                 economic_transition_eligible, economic_training_eligible,
                 economic_promotion_eligible, realized_energy_saving_fraction,
                 realized_allocation_saving_fraction, tasam_online_reward,
                 tasam_energy_reward, tasam_allocation_reward, tasam_sla_penalty,
                 calibration_version, replay_schema, replay_compact_bytes,
                 transition_json, transition_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(decision_id), decision_timestamp, observed_timestamp,
                    source_metric_id, observed_id,
                    str(feedback.get('economic_action_contract') or action.get('contract') or ''),
                    str(feedback.get('economic_application_status') or action.get('application_status') or ''),
                    1 if feedback.get('economic_transition_eligible', False) else 0,
                    1 if feedback.get('economic_training_eligible', False) else 0,
                    1 if feedback.get('economic_promotion_eligible', False) else 0,
                    feedback.get('realized_energy_saving_fraction'),
                    feedback.get('realized_allocation_saving_fraction'),
                    feedback.get('tasam_online_reward'),
                    feedback.get('tasam_energy_reward'),
                    feedback.get('tasam_allocation_reward'),
                    feedback.get('tasam_sla_penalty'),
                    str(
                        feedback.get('energy_model_version')
                        or action.get('energy_model_version')
                        or decision.get('energy_model_version')
                        or decision.get('tasam_energy_model_version')
                        or ''
                    ),
                    str(payload.get('schema') or ''),
                    len(encoded.encode('utf-8')),
                    encoded, digest, int(time.time()),
                ),
            )
            return True
        except (sqlite3.Error, TypeError, ValueError):
            return False

    def record_missing_judge_outcome(self, decision, reason="shutdown_before_next_real_snapshot"):
        """Persist an explicit missing-feedback marker for a pending decision."""
        if not isinstance(decision, dict):
            return False
        feedback = {
            "outcome_observed": False,
            "feedback_status": "missing",
            "feedback_missing_reason": str(reason),
            "correct_verdict": "UNKNOWN",
            "tasam_predicted_verdict": "UNKNOWN",
            "tasam_observed_verdict": "UNKNOWN",
            "tasam_category_error": True,
            "tasam_category_credit": -1.0,
            "tasam_category_penalty": 1.0,
            "tasam_training_category_credit": -2.0,
            "tasam_training_category_penalty": 2.0,
            "tasam_training_reward": -2.0,
            "tasam_continuous_reward": -1.0,
            "tasam_observed_error": 1.0,
            "tasam_reward_source": "missing_real_feedback_max_penalty",
            "tasam_action_applied": bool(decision.get("ta_sam_actuation_applied", False)),
        }
        self.record_judge_outcome(
            decision,
            feedback,
            observation={"reason": str(reason), "correct_verdict": "UNKNOWN"},
            observed_timestamp=int(time.time()),
        )
        return True

    def record_marl_shadow_comparison(self, snapshot, timestamp=None):
        """Persist runtime comparison between live allocator and MARL shadow allocator."""
        if not isinstance(snapshot, dict) or not snapshot:
            return

        marl_shadow = snapshot.get('marl_shadow') or {}
        if not isinstance(marl_shadow, dict) or not marl_shadow:
            return
        comparison = marl_shadow.get('comparison') or {}
        if not isinstance(comparison, dict) or not comparison:
            return

        if timestamp is None:
            timestamp = int(time.time())

        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO marl_shadow_comparison_history
                (timestamp, datetime, policy_id, source, checkpoint_readiness, available, recommend_shadow,
                 live_score, shadow_score, score_delta, base_sla_resource_score_delta, causal_score_delta,
                 tasam_checkpoint_valid, tasam_fallback_used, tasam_evidence_valid, live_allocator_algorithm,
                 live_power_percent, shadow_power_percent, live_power_w, shadow_power_w,
                 energy_saving_fraction, resource_saving_fraction, energy_model_version, energy_valid, resource_valid,
                 native_sim_energy_j, native_sim_power_w, energy_reference_source,
                 absolute_scale_valid, physical_wattmeter_available, infra_resource_index,
                 infra_resource_saving_fraction, calibration_corpus_id, calibration_fit_error,
                 calibration_rank_valid,
                 live_ran_completion_est, shadow_ran_completion_est,
                 live_ai_completion_est, shadow_ai_completion_est,
                 live_total_shortfall, shadow_total_shortfall,
                 live_budget_gap, shadow_budget_gap,
                 delta_r_ran, delta_r_ai, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                dt_str,
                str(marl_shadow.get('policy_id', '') or ''),
                str(marl_shadow.get('source', '') or ''),
                str(marl_shadow.get('checkpoint_readiness', '') or ''),
                1 if marl_shadow.get('available', False) else 0,
                1 if comparison.get('recommend_shadow', False) else 0,
                float(comparison.get('live_score', 0.0) or 0.0),
                float(comparison.get('shadow_score', 0.0) or 0.0),
                float(comparison.get('score_delta', 0.0) or 0.0),
                float(comparison.get('base_sla_resource_score_delta', comparison.get('score_delta', 0.0)) or 0.0),
                float(comparison.get('causal_score_delta', 0.0) or 0.0),
                1 if marl_shadow.get('tasam_checkpoint_valid', False) else 0,
                1 if marl_shadow.get('tasam_fallback_used', False) else 0,
                1 if marl_shadow.get('tasam_evidence_valid', False) else 0,
                str(snapshot.get('live_allocator_algorithm', '') or ''),
                comparison.get('live_power_percent'),
                comparison.get('shadow_power_percent'),
                comparison.get('live_power_w'),
                comparison.get('shadow_power_w'),
                float(comparison.get('energy_saving_fraction', 0.0) or 0.0),
                float(comparison.get('resource_saving_fraction', 0.0) or 0.0),
                str(comparison.get('energy_model_version', '') or ''),
                1 if comparison.get('energy_valid', False) else 0,
                1 if comparison.get('resource_valid', False) else 0,
                comparison.get('native_sim_energy_j'),
                comparison.get('native_sim_power_w'),
                str(comparison.get('energy_reference_source', '') or ''),
                1 if comparison.get('absolute_scale_valid', False) else 0,
                1 if comparison.get('physical_wattmeter_available', False) else 0,
                comparison.get('infra_resource_index'),
                comparison.get('infra_resource_saving_fraction'),
                str(comparison.get('calibration_corpus_id', '') or ''),
                comparison.get('calibration_fit_error'),
                1 if comparison.get('calibration_rank_valid', False) else 0,
                float(comparison.get('live_ran_completion_est', 0.0) or 0.0),
                float(comparison.get('shadow_ran_completion_est', 0.0) or 0.0),
                float(comparison.get('live_ai_completion_est', 0.0) or 0.0),
                float(comparison.get('shadow_ai_completion_est', 0.0) or 0.0),
                float(comparison.get('live_total_shortfall', 0.0) or 0.0),
                float(comparison.get('shadow_total_shortfall', 0.0) or 0.0),
                float(comparison.get('live_budget_gap', 0.0) or 0.0),
                float(comparison.get('shadow_budget_gap', 0.0) or 0.0),
                float(marl_shadow.get('delta_r_ran_vs_live', 0.0) or 0.0),
                float(marl_shadow.get('delta_r_ai_vs_live', 0.0) or 0.0),
                json.dumps({'marl_shadow': marl_shadow, 'comparison': comparison}, ensure_ascii=False),
            ))
            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar comparação MARL shadow: {e}")

    def record_article_marl_state(self, snapshot, timestamp=None):
        """Persist article-aligned MARL state for global, slice and DU views."""
        if not isinstance(snapshot, dict) or not snapshot:
            return

        marl_state = snapshot.get('article_marl_state')
        if not isinstance(marl_state, dict) or not marl_state:
            marl_state = build_du_state_snapshot_from_resource_snapshot(snapshot)

        if timestamp is None:
            timestamp = int(time.time())

        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        global_state = marl_state.get('global_state', {}) or {}
        slice_state = marl_state.get('slice_state', {}) or {}
        du_states = marl_state.get('du_states', []) or []

        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO marl_global_state_history
                (timestamp, datetime, topology_id, logical_du_count, total_demand, usable_budget,
                 state_vector_json, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                dt_str,
                str(marl_state.get('topology_id', global_state.get('topology_id', 'unknown')) or 'unknown'),
                int(global_state.get('logical_du_count', len(du_states)) or len(du_states)),
                float(global_state.get('total_demand', 0.0) or 0.0),
                float(global_state.get('usable_budget', snapshot.get('usable_budget', 0.0)) or 0.0),
                json.dumps(global_state.get('state_vector', []), ensure_ascii=False),
                json.dumps(global_state, ensure_ascii=False),
            ))

            for slice_id, payload in slice_state.items():
                payload = payload or {}
                cursor.execute("""
                    INSERT OR REPLACE INTO marl_slice_state_history
                    (timestamp, datetime, slice_id, ue_count, demand, allocation, qos_pressure,
                     completion_ratio, min_qos_met, budget_share, snapshot_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    timestamp,
                    dt_str,
                    str(slice_id),
                    int(payload.get('ue_count', 0) or 0),
                    float(payload.get('demand', 0.0) or 0.0),
                    float(payload.get('allocation', 0.0) or 0.0),
                    float(payload.get('qos_pressure', 0.0) or 0.0),
                    float(payload.get('completion_ratio', 0.0) or 0.0),
                    float(payload.get('min_qos_met', 0.0) or 0.0),
                    float(payload.get('budget_share', 0.0) or 0.0),
                    json.dumps(payload, ensure_ascii=False),
                ))

            for du_payload in du_states:
                du_payload = du_payload or {}
                cursor.execute("""
                    INSERT OR REPLACE INTO marl_du_state_history
                    (timestamp, datetime, du_id, role, primary_slice, ue_count, demand_share, allocation_share,
                     slice_mix_json, state_vector_json, snapshot_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    timestamp,
                    dt_str,
                    str(du_payload.get('du_id', 'unknown') or 'unknown'),
                    str(du_payload.get('role', '') or ''),
                    str(du_payload.get('primary_slice', '') or ''),
                    int(du_payload.get('ue_count', 0) or 0),
                    float(du_payload.get('demand_share', 0.0) or 0.0),
                    float(du_payload.get('allocation_share', 0.0) or 0.0),
                    json.dumps(du_payload.get('slice_mix', {}), ensure_ascii=False),
                    json.dumps(du_payload.get('state_vector', []), ensure_ascii=False),
                    json.dumps(du_payload, ensure_ascii=False),
                ))
            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar estado MARL: {e}")

    def record_app3_snapshot(self, snapshot, timestamp=None):
        """Registra snapshot agregado do App3 para fallback histórico do domínio veicular."""
        if not isinstance(snapshot, dict) or not snapshot:
            return

        simulation = snapshot.get("simulation", {}) or {}
        vehicles = snapshot.get("vehicles", {}) or {}
        network = snapshot.get("network", {}) or {}

        if timestamp is None:
            timestamp_text = simulation.get("timestamp_iso")
            if timestamp_text:
                try:
                    timestamp = int(datetime.fromisoformat(str(timestamp_text)).timestamp())
                except (TypeError, ValueError, OSError):
                    timestamp = None

        if timestamp is None:
            timestamp = int(time.time())

        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO app3_snapshots
                (timestamp, datetime, total_vehicles, ego_present,
                 high_risk_vehicles, medium_risk_vehicles, degraded_autonomy_vehicles,
                 max_latency_ms, max_packet_loss_percent, max_speed_mps, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                dt_str,
                int(vehicles.get("total_vehicles", 0) or 0),
                1 if vehicles.get("ego_present", False) else 0,
                int(vehicles.get("high_risk_vehicles", 0) or 0),
                int(vehicles.get("medium_risk_vehicles", 0) or 0),
                int(vehicles.get("degraded_autonomy_vehicles", 0) or 0),
                float(vehicles.get("max_latency_ms", network.get("max_latency_ms", 0.0)) or 0.0),
                float(vehicles.get("max_packet_loss_percent", network.get("max_packet_loss_percent", 0.0)) or 0.0),
                float(vehicles.get("max_speed_mps", network.get("max_speed_mps", 0.0)) or 0.0),
                json.dumps(snapshot, ensure_ascii=False),
            ))
            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar snapshot App3: {e}")

    def _derive_conflict_event(self, decision, timestamp):
        """
        Deriva um evento de conflito O-RAN a partir da decisão do rApp.

        Mapeamento inspirado no artigo00:
        - indireto: uma ação/política altera um parâmetro que afeta KPI de aplicação;
        - implícito: KPIs globais parecem saudáveis, mas um KPI específico da aplicação
          está em risco e precisa bloquear ML/energia.
        """
        priority = decision.get('priority_violation')
        if not priority:
            return None

        reason = decision.get('reason', '')
        network_health = decision.get('network_health') or {}
        cvar_us = float(network_health.get('cvar_us', 0) or 0)
        global_kpi_masks_app_kpi = 0 < cvar_us < 40000
        conflict_type = 'implicit' if global_kpi_masks_app_kpi else 'indirect'

        base = {
            'timestamp': timestamp,
            'datetime': datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S"),
            'source_agent': 'xApp2-EnergySaver',
            'target_agent': 'rApp-ResourceOptimizer',
            'conflict_type': conflict_type,
            'parameter': 'energy_policy',
            'affected_service': 'network',
            'affected_kpi': 'unknown',
            'observed_value': None,
            'threshold_value': None,
            'decision': decision.get('energy_saver', 'UNKNOWN'),
            'mitigation_action': decision.get('action', 'NONE'),
            'reason': reason,
            'confidence': float(decision.get('confidence', 0) or 0),
            'graph_path': '',
        }

        if priority in ('THROUGHPUT', 'THROUGHPUT_WARNING', 'LATENCY', 'LATENCY_WARNING'):
            camera = decision.get('camera_metrics') or {}
            is_latency = priority in ('LATENCY', 'LATENCY_WARNING')
            base.update({
                'source_agent': 'rApp-CVaR/ML-Arbiter' if global_kpi_masks_app_kpi else 'xApp2-EnergySaver',
                'target_agent': 'xApp1-RANSlicer',
                'parameter': 'global_health_policy' if global_kpi_masks_app_kpi else 'energy_policy',
                'affected_service': 'App1-Vigilancia',
                'affected_kpi': 'camera_latency_ms' if is_latency else 'camera_throughput_mbps',
                'observed_value': float(camera.get('latency_ms', 0) if is_latency else camera.get('throughput_mbps', 0) or 0),
                'threshold_value': 80.0 if priority == 'LATENCY' else 60.0 if priority == 'LATENCY_WARNING' else 25.0 if priority == 'THROUGHPUT' else 30.0,
                'graph_path': (
                    'CVaR/ML global KPI -> energy recommendation -> App1 KPI -> rApp mitigation'
                    if global_kpi_masks_app_kpi
                    else 'xApp2-EnergySaver -> energy_policy -> App1 KPI -> xApp1-RANSlicer/rApp'
                ),
            })
            return base

        if priority in ('APP2_MTC_CRITICAL', 'APP2_MTC_WARNING'):
            app2 = decision.get('app2_metrics') or {}
            affected_kpi = 'app2_connected_ratio'
            observed_value = float(app2.get('connected_ratio', 0) or 0) * 100.0
            threshold_value = 85.0 if priority == 'APP2_MTC_CRITICAL' else 95.0

            reason_lower = reason.lower()
            if 'packet loss' in reason_lower:
                affected_kpi = 'app2_packet_loss_percent'
                observed_value = float(app2.get('packet_loss_percent', 0) or 0)
                threshold_value = 10.0 if priority == 'APP2_MTC_CRITICAL' else 5.0
            elif 'entrega' in reason_lower:
                affected_kpi = 'app2_delivery_success_percent'
                observed_value = float(app2.get('delivery_success_percent', 0) or 0)
                threshold_value = 90.0 if priority == 'APP2_MTC_CRITICAL' else 95.0
            elif 'latência' in reason_lower or 'latencia' in reason_lower:
                affected_kpi = 'app2_avg_latency_ms'
                observed_value = float(app2.get('avg_latency_ms', 0) or 0)
                threshold_value = 1000.0 if priority == 'APP2_MTC_CRITICAL' else 500.0
            elif 'bateria' in reason_lower:
                affected_kpi = 'app2_avg_battery_percent'
                observed_value = float(app2.get('avg_battery_percent', 0) or 0)
                threshold_value = 15.0 if priority == 'APP2_MTC_CRITICAL' else 25.0

            base.update({
                'source_agent': 'rApp-CVaR/ML-Arbiter' if global_kpi_masks_app_kpi else 'xApp2-EnergySaver',
                'target_agent': 'App2-Monitoramento',
                'parameter': 'global_health_policy' if global_kpi_masks_app_kpi else 'energy_policy',
                'affected_service': 'App2-Monitoramento',
                'affected_kpi': affected_kpi,
                'observed_value': observed_value,
                'threshold_value': threshold_value,
                'graph_path': (
                    'CVaR/ML global KPI -> energy recommendation -> App2 mMTC KPI -> rApp mitigation'
                    if global_kpi_masks_app_kpi
                    else 'xApp2-EnergySaver -> energy_policy/slice resources -> App2 mMTC KPI -> rApp'
                ),
            })
            return base

        if priority in ('VEHICLE_CRITICAL', 'VEHICLE_WARNING'):
            vehicle = decision.get('vehicle_metrics') or {}
            affected_kpi = 'ego_latency_ms'
            observed_value = float(vehicle.get('max_latency_ms', 0) or 0)
            threshold_value = 100.0 if priority == 'VEHICLE_CRITICAL' else 50.0

            reason_lower = reason.lower()
            if 'packet loss' in reason_lower:
                affected_kpi = 'vehicle_packet_loss_percent'
                observed_value = float(vehicle.get('max_packet_loss_percent', 0) or 0)
                threshold_value = 5.0 if priority == 'VEHICLE_CRITICAL' else 2.0
            elif 'autonomia' in reason_lower:
                affected_kpi = 'vehicle_degraded_autonomy_count'
                observed_value = float(vehicle.get('degraded_autonomy_vehicles', 0) or 0)
                threshold_value = 1.0
            elif 'risco alto' in reason_lower:
                affected_kpi = 'vehicle_high_risk_count'
                observed_value = float(vehicle.get('high_risk_vehicles', 0) or 0)
                threshold_value = 1.0
            elif 'risco médio' in reason_lower or 'risco medio' in reason_lower:
                affected_kpi = 'vehicle_medium_risk_count'
                observed_value = float(vehicle.get('medium_risk_vehicles', 0) or 0)
                threshold_value = 1.0

            base.update({
                'source_agent': 'rApp-CVaR/ML-Arbiter' if global_kpi_masks_app_kpi else 'xApp-VehicleSafety',
                'target_agent': 'App3-Veicular',
                'parameter': 'global_health_policy' if global_kpi_masks_app_kpi else 'vehicle_priority_policy',
                'affected_service': 'App3-Veicular',
                'affected_kpi': affected_kpi,
                'observed_value': observed_value,
                'threshold_value': threshold_value,
                'graph_path': (
                    'CVaR/ML global KPI -> vehicle priority policy -> App3 vehicular KPI -> rApp mitigation'
                    if global_kpi_masks_app_kpi
                    else 'xApp-VehicleSafety -> vehicle_priority_policy -> App3 vehicular KPI -> rApp'
                ),
            })
            return base

        return None

    def record_conflict_event(self, event):
        """Registra um evento de conflito O-RAN."""
        if not event:
            return False

        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO conflict_events
                (timestamp, datetime, source_agent, target_agent, conflict_type,
                 parameter, affected_service, affected_kpi, observed_value,
                 threshold_value, decision, mitigation_action, reason,
                 confidence, graph_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.get('timestamp'),
                event.get('datetime'),
                event.get('source_agent'),
                event.get('target_agent'),
                event.get('conflict_type'),
                event.get('parameter'),
                event.get('affected_service'),
                event.get('affected_kpi'),
                event.get('observed_value'),
                event.get('threshold_value'),
                event.get('decision'),
                event.get('mitigation_action'),
                event.get('reason'),
                event.get('confidence'),
                event.get('graph_path'),
            ))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar conflito: {e}")
            return False

    def record_conflict_from_decision(self, decision, timestamp=None):
        """Deriva e registra conflito a partir de uma decisão do rApp."""
        if timestamp is None:
            timestamp = int(time.time())
        return self.record_conflict_event(self._derive_conflict_event(decision or {}, timestamp))
    
    def record_extended_metric(self, timestamp=None, sim_time_s=0, cell_id=0,
                               global_worst_latency=0, global_avg_latency=0,
                               global_min_latency=0, global_max_latency=0,
                               global_jitter=0, packet_loss=0,
                               total_active_ues=0, total_active_cameras=0,
                               total_critical=0, total_tx_bytes=0, total_rx_bytes=0,
                               total_tx_pdus=0, total_rx_pdus=0, throughput_kbps=0,
                               energy_state=None, slicer_state=None,
                               latency_p5_us=0, latency_p95_us=0,
                               latency_min_nonzero_us=0, valid_samples=0,
                               extended_metrics=None, decision_id=None):
        """
        Registra métricas estendidas no Data Lake.
        
        Args:
            timestamp: Unix timestamp (default: now)
            sim_time_s: Tempo de simulação em segundos
            cell_id: ID da célula
            global_worst_latency: Pior latência global em us
            global_avg_latency: Latência média global em us
            global_min_latency: Melhor latência global em us
            global_max_latency: Pior latência global em us
            global_jitter: Jitter global em us
            packet_loss: Taxa de perda de pacotes
            total_active_ues: Total de UEs ativas
            total_active_cameras: Total de câmeras ativas
            total_critical: Total de UEs críticas
            total_tx_bytes: Total de bytes transmitidos
            total_rx_bytes: Total de bytes recebidos
            total_tx_pdus: Total de PDUs transmitidos
            total_rx_pdus: Total de PDUs recebidos
            throughput_kbps: Throughput em kbps
            extended_metrics: Dict com métricas POR UE (opcional)
            energy_state: Estado do Energy Saver
            slicer_state: Estado do SLICER
            latency_p5_us: Percentil 5 de latência (métrica robusta)
            latency_p95_us: Percentil 95 de latência (métrica robusta)
            latency_min_nonzero_us: Menor latência > 0
            valid_samples: Número de amostras válidas
        """
        if timestamp is None:
            timestamp = int(time.time())
        
        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        
        latency_p95_per_ue = extended_metrics.get('latency_p95_per_ue_us', 0) if extended_metrics else 0
        variance_per_ue = extended_metrics.get('variance_per_ue_us2', 0) if extended_metrics else 0
        cvar_per_ue = extended_metrics.get('cvar_per_ue_us', 0) if extended_metrics else 0
        ue_count = extended_metrics.get('ue_count', 0) if extended_metrics else 0
        collector_mode = extended_metrics.get('collector_mode', '') if extended_metrics else ''
        throughput_source = extended_metrics.get('throughput_source', '') if extended_metrics else ''
        real_latency_sample_count = extended_metrics.get('real_latency_sample_count', 0) if extended_metrics else 0
        proxy_latency_sample_count = extended_metrics.get('proxy_latency_sample_count', 0) if extended_metrics else 0
        pdcp_stale = int(bool(extended_metrics.get('pdcp_stale', False))) if extended_metrics else 0
        rlc_stale = int(bool(extended_metrics.get('rlc_stale', False))) if extended_metrics else 0
        mac_stale = int(bool(extended_metrics.get('mac_stale', False))) if extended_metrics else 0
        pdcp_trace_age_s = extended_metrics.get('pdcp_trace_age_s', 0) if extended_metrics else 0
        rlc_trace_age_s = extended_metrics.get('rlc_trace_age_s', 0) if extended_metrics else 0
        mac_trace_age_s = extended_metrics.get('mac_trace_age_s', 0) if extended_metrics else 0
        pdcp_latest_sim_time_s = extended_metrics.get('pdcp_latest_sim_time_s', 0) if extended_metrics else 0
        
        MAX_VALID_LATENCY_US = 500000
        
        if global_worst_latency > MAX_VALID_LATENCY_US:
            print(f"[DataLake] IGNORANDO outliers: latency={global_worst_latency/1000:.1f}ms > 500ms")
            return
        
        if total_active_ues == 0:
            print(f"[DataLake] IGNORANDO: sem UEs ativas")
            return
        
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO extended_metrics 
                (timestamp, datetime, sim_time_s, cell_id,
                 global_worst_latency_us, global_avg_latency_us,
                 global_min_latency_us, global_max_latency_us,
                 global_jitter_us, global_packet_loss_rate,
                 total_active_ues, total_active_cameras, total_critical_ues,
                 total_tx_bytes, total_rx_bytes,
                 total_tx_pdus, total_rx_pdus, throughput_kbps,
                energy_state, slicer_state,
                 latency_p5_us, latency_p95_us, latency_min_nonzero_us, valid_samples,
                 latency_p95_per_ue_us, variance_per_ue_us2, cvar_per_ue_us, ue_count,
                 collector_mode, throughput_source, real_latency_sample_count, proxy_latency_sample_count,
                 pdcp_stale, rlc_stale, mac_stale,
                 pdcp_trace_age_s, rlc_trace_age_s, mac_trace_age_s, pdcp_latest_sim_time_s,
                 decision_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, dt_str, sim_time_s, cell_id,
                  global_worst_latency, global_avg_latency,
                  global_min_latency, global_max_latency,
                  global_jitter, packet_loss,
                  total_active_ues, total_active_cameras, total_critical,
                  total_tx_bytes, total_rx_bytes,
                  total_tx_pdus, total_rx_pdus, throughput_kbps,
                  energy_state, slicer_state,
                  latency_p5_us, latency_p95_us, latency_min_nonzero_us, valid_samples,
                  latency_p95_per_ue, variance_per_ue, cvar_per_ue, ue_count,
                  collector_mode, throughput_source, real_latency_sample_count, proxy_latency_sample_count,
                  pdcp_stale, rlc_stale, mac_stale,
                  pdcp_trace_age_s, rlc_trace_age_s, mac_trace_age_s, pdcp_latest_sim_time_s,
                  decision_id))
            self.conn.commit()
            row = self.conn.execute(
                "SELECT id FROM extended_metrics WHERE timestamp = ?",
                (timestamp,),
            ).fetchone()
            return int(row[0]) if row else None
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar métrica estendida: {e}")
            return None

    def associate_extended_metric_decision(self, metric_id, decision_id):
        """Associate an already persisted real snapshot with one decision."""
        try:
            metric_id = int(metric_id or 0)
            decision_id = int(decision_id or 0)
        except (TypeError, ValueError):
            return False
        if metric_id <= 0 or decision_id <= 0:
            return False
        try:
            self.conn.execute(
                "UPDATE extended_metrics SET decision_id = ? WHERE id = ?",
                (decision_id, metric_id),
            )
            self.conn.commit()
            return self.conn.total_changes > 0
        except Exception as e:
            print(f"[DataLake] ERRO ao associar snapshot à decisão: {e}")
            return False
    
    def record_ue_metrics(self, timestamp=None, ue_metrics_list=None, sim_time_s=0.0):
        """
        Registra métricas de múltiplos UEs.
        
        Args:
            timestamp: Unix timestamp (default: now)
            ue_metrics_list: Lista de dicts com métricas por UE
        """
        if timestamp is None:
            timestamp = int(time.time())
        
        if not ue_metrics_list:
            return
        
        try:
            cursor = self.conn.cursor()
            for ue in ue_metrics_list:
                cursor.execute("""
                    INSERT INTO ue_metrics 
                    (timestamp, imsi, device_type, cell_id,
                     latency_us, latency_avg_us, latency_min_us, latency_max_us,
                     jitter_us, pdu_size_avg,
                     tx_bytes, rx_bytes, tx_pdus, rx_pdus,
                     throughput_kbps, packet_count,
                     mcs_avg, tb_size_avg, is_critical,
                     packet_loss_percent, vehicle_id, vehicle_role, autonomy_state, risk_state,
                     speed_mps, heading_deg, lane_id, waypoint_id,
                     position_x, position_y, position_z,
                     connectivity, gateway_id, domain, mobility_profile,
                     sim_time_s, latency_p95_us, has_latency_samples,
                     latency_is_proxy, pdcp_provenance, offered_load_kbps, backlog_bytes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (timestamp, ue.get('imsi'), ue.get('device_type'), ue.get('cell_id'),
                      ue.get('latency_us'), ue.get('latency_avg_us'), ue.get('latency_min_us'), ue.get('latency_max_us'),
                      ue.get('jitter_us'), ue.get('pdu_size_avg'),
                      ue.get('tx_bytes', 0), ue.get('rx_bytes', 0), ue.get('tx_pdus', 0), ue.get('rx_pdus', 0),
                      ue.get('throughput_kbps'), ue.get('packet_count', 0),
                      ue.get('mcs_avg', 0), ue.get('tb_size_avg', 0),
                      1 if ue.get('is_critical') else 0,
                      ue.get('packet_loss_percent'),
                      ue.get('vehicle_id'), ue.get('vehicle_role'),
                      ue.get('autonomy_state'), ue.get('risk_state'),
                      ue.get('speed_mps'), ue.get('heading_deg'),
                      ue.get('lane_id'), ue.get('waypoint_id'),
                      (ue.get('position') or {}).get('x'),
                      (ue.get('position') or {}).get('y'),
                      (ue.get('position') or {}).get('z'),
                      ue.get('connectivity'), ue.get('gateway_id'),
                      ue.get('domain'), ue.get('mobility_profile'),
                      float(ue.get('sim_time_s', sim_time_s) or 0.0),
                      ue.get('latency_p95_us', ue.get('latency_us')),
                      1 if ue.get('has_latency_samples') else 0,
                      1 if ue.get('latency_is_proxy') else 0,
                      ue.get('pdcp_provenance', ''),
                      ue.get('offered_load_kbps', ue.get('tx_throughput_kbps', 0)),
                      ue.get('backlog_bytes', 0)))
            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar métricas de UE: {e}")
    
    def record_energy_command(
        self,
        command,
        power_percent=100,
        ru_count=2,
        mmwave_count=1,
        reason="",
        timestamp_ns=None,
        requested_power_percent=None,
        power_safety_override_reason="",
        decision_id=None,
        action_correlation_id="",
        native_control_sequence=None,
        action_origin="",
        application_status="",
    ):
        """
        Registra comando de energia enviado ao xApp Energy Saver.
        
        Args:
            command: Comando enviado (FULL_POWER, CONDITIONAL_REDUCE, etc)
            power_percent: Porcentagem de potência (100, 70, 50, 25, 10)
            ru_count: Número de RUs ativas
            mmwave_count: Número de mmWave ativas
            reason: Motivo do comando
        """
        timestamp_ns = int(timestamp_ns or time.time_ns())
        timestamp = timestamp_ns // 1_000_000_000
        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        calibration = {}
        try:
            calibration = load_calibration()
            if (
                calibration.get("schema") == "greenran.energy_calibration.v3"
                and mmwave_count in {1, 2, 3}
            ):
                power_w = sleep_state_power_w(
                    calibration,
                    active_cells=int(mmwave_count),
                    power_percent=power_percent,
                )
            else:
                power_w = state_power_w(calibration, ru_count, mmwave_count, power_percent)
            calibration_version = str(calibration.get("calibration_version", ""))
        except ValueError as exc:
            print(f"[DataLake] WRN: calibração energética indisponível: {exc}")
            power_w = None
            calibration_version = ""
        
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO energy_commands 
                (timestamp, datetime, command, power_percent, ru_count, mmwave_count, reason,
                 timestamp_ns, power_w, calibration_version,
                 requested_power_percent, applied_power_percent, power_safety_override_reason,
                native_sim_energy_j, native_sim_power_w, energy_reference_source,
                absolute_scale_valid, physical_wattmeter_available, calibration_corpus_id,
                 calibration_fit_error, calibration_rank_valid,
                 decision_id, action_correlation_id, action_origin, application_status,
                 native_control_sequence,
                 command_sent, actuation_confirmed, actuation_confirmation_source,
                 observed_power_percent, observed_power_w, observed_ru_count,
                 observed_mmwave_count, confirmation_decision_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, dt_str, command, power_percent, ru_count, mmwave_count, reason,
                  timestamp_ns, power_w, calibration_version,
                  power_percent if requested_power_percent is None else requested_power_percent,
                  power_percent, power_safety_override_reason,
                  None, None,
                  "ns3_device_energy_model" if calibration.get("schema") == "greenran.energy_calibration.v2" else "calibrated_model",
                  1 if calibration.get("absolute_scale_valid", False) else 0,
                  1 if calibration.get("physical_wattmeter_available", False) else 0,
                  str(calibration.get("calibration_corpus_id", "") or ""),
                  calibration.get("fit_error"),
                  1 if calibration.get("calibration_rank_valid", False) else 0,
                  decision_id, str(action_correlation_id or ''), str(action_origin or ''),
                  str(application_status or ''),
                  native_control_sequence,
                  1, 0, '', None, None, None, None, None))
            command_id = int(cursor.lastrowid or 0) or None
            self.conn.commit()
            return command_id
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar comando de energia: {e}")
            return None

    def associate_energy_command_decision(self, correlation_id, decision_id):
        """Bind an already-issued command to its persisted rApp decision."""
        if not correlation_id or not decision_id:
            return None
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "UPDATE energy_commands SET decision_id=? WHERE action_correlation_id=?",
                (int(decision_id), str(correlation_id)),
            )
            self.conn.commit()
            return int(cursor.rowcount or 0)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            print(f"[DataLake] ERRO ao associar comando energético à decisão: {exc}")
            return None

    def update_energy_command_application(
        self,
        correlation_id,
        status,
        *,
        actuation_confirmed=None,
        confirmation_source='',
        observed_power_percent=None,
        observed_power_w=None,
        observed_ru_count=None,
        observed_mmwave_count=None,
        confirmation_decision_id=None,
        observed_allocation_fraction=None,
        native_active_dl_symbols=None,
        native_dl_symbol_capacity=None,
        native_cell_ids=None,
        native_observation_version=None,
    ):
        """Persist command status and optional confirmation from a later window."""
        if not correlation_id:
            return None
        try:
            cursor = self.conn.cursor()
            assignments = ["application_status=?"]
            # Keep the low-level method backward compatible with historical
            # callers that used ``verified``.  Current orchestrator paths
            # pass the canonical ``applied`` state explicitly only after the
            # native confirmation validator succeeds.
            values = [str(status or '')]
            if actuation_confirmed is not None:
                assignments.append("actuation_confirmed=?")
                values.append(1 if actuation_confirmed else 0)
            if confirmation_source:
                assignments.append("actuation_confirmation_source=?")
                values.append(str(confirmation_source))
            if observed_power_percent is not None:
                assignments.append("observed_power_percent=?")
                values.append(observed_power_percent)
            if observed_power_w is not None:
                assignments.append("observed_power_w=?")
                values.append(observed_power_w)
            if observed_ru_count is not None:
                assignments.append("observed_ru_count=?")
                values.append(observed_ru_count)
            if observed_mmwave_count is not None:
                assignments.append("observed_mmwave_count=?")
                values.append(observed_mmwave_count)
            if confirmation_decision_id is not None:
                assignments.append("confirmation_decision_id=?")
                values.append(confirmation_decision_id)
            if observed_allocation_fraction is not None:
                assignments.append("observed_allocation_fraction=?")
                values.append(observed_allocation_fraction)
            if native_active_dl_symbols is not None:
                assignments.append("native_active_dl_symbols=?")
                values.append(native_active_dl_symbols)
            if native_dl_symbol_capacity is not None:
                assignments.append("native_dl_symbol_capacity=?")
                values.append(native_dl_symbol_capacity)
            if native_cell_ids is not None:
                assignments.append("native_cell_ids_json=?")
                values.append(json.dumps(list(native_cell_ids), sort_keys=True))
            if native_observation_version is not None:
                assignments.append("native_observation_version=?")
                values.append(str(native_observation_version))
            values.append(str(correlation_id))
            cursor.execute(
                f"UPDATE energy_commands SET {', '.join(assignments)} "
                "WHERE action_correlation_id=?",
                tuple(values),
            )
            self.conn.commit()
            return int(cursor.rowcount or 0)
        except sqlite3.Error as exc:
            print(f"[DataLake] ERRO ao atualizar status do comando energético: {exc}")
            return None

    def energy_command_for_correlation(self, correlation_id):
        """Return the exact post-actuation command for one decision token."""
        if not correlation_id:
            return {}
        try:
            row = self.conn.execute(
                "SELECT * FROM energy_commands WHERE action_correlation_id=? "
                "ORDER BY timestamp_ns DESC, id DESC LIMIT 1",
                (str(correlation_id),),
            ).fetchone()
        except sqlite3.Error:
            return {}
        return {str(key): row[key] for key in row.keys()} if row is not None else {}

    def peek_next_decision_id(self):
        """Return the next local decision id without inserting a decision.

        The ns-3 native trace is emitted asynchronously, before the decision
        row is committed by the orchestrator.  A single orchestrator owns an
        arm database, so reserving the next monotonically increasing id as a
        trace hint is deterministic; the later SQLite insert remains the
        authoritative decision record.
        """
        try:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM decisions_history"
            ).fetchone()
            return int(row[0] or 1) if row else 1
        except (sqlite3.Error, TypeError, ValueError):
            return 1

    def ingest_native_control_observations(self, source_path=None):
        """Import complete CSV lines incrementally without treating them as an ACK."""
        self._last_native_import_invalid_rows = 0
        self._last_native_import_partial = False
        self._last_native_import_error = ""
        if source_path:
            path = Path(source_path)
        else:
            output_dir = os.environ.get("GREENRAN_NS3_ENERGY_OUTPUT_DIR")
            if output_dir:
                path = Path(output_dir) / "TasamControlObservations.csv"
            else:
                state_dir = Path(os.environ.get("GREENRAN_STATE_DIR", str(RAPP_DB_PATH.parent)))
                path = state_dir / "ns3_energy" / "TasamControlObservations.csv"
        if not path.is_file():
            return 0
        imported = 0
        resolved_path = str(path.resolve())
        try:
            # A native callback can arrive while the controller is replacing
            # the context sidecar. Reconcile identity by native sequence once
            # the sidecar is complete; this never creates hardware evidence.
            context_by_sequence = {}
            context_path = path.parent / "NativeControlContext.csv"
            if context_path.is_file():
                import csv as _csv
                with context_path.open(newline="", encoding="utf-8", errors="replace") as context_file:
                    for context_row in _csv.DictReader(context_file):
                        try:
                            sequence = int(context_row.get("NativeControlSequence", 0) or 0)
                            decision_id = int(context_row.get("DecisionId", 0) or 0)
                        except (TypeError, ValueError):
                            continue
                        correlation = str(context_row.get("ActionCorrelationId") or "").strip()
                        if sequence <= 0 or decision_id <= 0 or not correlation:
                            continue
                        context_by_sequence[sequence] = {
                            "decision_id": decision_id,
                            "correlation": correlation,
                            "campaign": str(context_row.get("CampaignId") or "").strip(),
                            "generation": str(context_row.get("SourceGeneration") or "").strip(),
                        }
            for sequence, context in context_by_sequence.items():
                self.conn.execute(
                    """
                    UPDATE tasam_control_observations
                       SET decision_id=COALESCE(NULLIF(decision_id, 0), ?),
                           action_correlation_id=COALESCE(NULLIF(action_correlation_id, ''), ?),
                           campaign_id=CASE WHEN campaign_id IS NULL OR campaign_id='' THEN ? ELSE campaign_id END,
                           campaign_generation=CASE WHEN campaign_generation IS NULL OR campaign_generation='' THEN ? ELSE campaign_generation END
                     WHERE native_control_sequence=?
                    """,
                    (
                        context["decision_id"], context["correlation"],
                        context["campaign"], context["generation"], sequence,
                    ),
                )
            if context_by_sequence:
                self.conn.commit()
            import csv
            stat = path.stat()
            inode = int(getattr(stat, "st_ino", 0) or 0)
            state = self.conn.execute(
                "SELECT inode, byte_offset, header, reset_count FROM tasam_native_ingest_state "
                "WHERE source_path=?", (resolved_path,)
            ).fetchone()
            previous_inode = int(state[0]) if state else 0
            previous_offset = int(state[1]) if state else 0
            header = str(state[2]) if state else ""
            reset_count = int(state[3] or 0) if state else 0
            if previous_inode != inode or stat.st_size < previous_offset:
                previous_offset = 0
                header = ""
                reset_count += 1
            with path.open("rb") as handle:
                raw_header = handle.readline()
                if not raw_header:
                    return 0
                if not header:
                    header = raw_header.decode("utf-8", errors="replace")
                header_end = handle.tell()
                start = max(header_end, previous_offset)
                handle.seek(start)
                body = handle.read()
            if not body:
                self.conn.execute(
                    """INSERT INTO tasam_native_ingest_state
                       (source_path, inode, byte_offset, header, last_error,
                        reset_count, updated_at)
                       VALUES (?, ?, ?, ?, '', ?, ?)
                       ON CONFLICT(source_path) DO UPDATE SET
                         inode=excluded.inode, byte_offset=excluded.byte_offset,
                         header=excluded.header, last_error='',
                         reset_count=excluded.reset_count, updated_at=excluded.updated_at""",
                    (resolved_path, inode, start, header, reset_count, int(time.time())),
                )
                self.conn.commit()
                return 0
            last_newline = body.rfind(b"\n")
            if last_newline < 0:
                self._last_native_import_partial = True
                return 0
            complete = body[:last_newline + 1]
            new_offset = start + len(complete)
            text = io.StringIO(header + complete.decode("utf-8", errors="replace"))
            for row in csv.DictReader(text):
                    try:
                        scheduler_transaction = int(
                            row.get("SchedulerTransactionId", row.get("TransactionId", 0)) or 0
                        )
                        power_transaction = int(row.get("PowerTransactionId", 0) or 0)
                        nominal_power = row.get("NominalTxPowerDbm")
                        observation_kind = str(
                            row.get("ObservationKind") or "legacy_snapshot"
                        ).strip()
                        policy_active = str(
                            row.get("PolicyActive", "0") or "0"
                        ).strip().lower() in {"1", "true", "yes"}
                        expiry_raw = row.get("PolicyExpiryTime")
                        expiry = (
                            None if expiry_raw in (None, "") else float(expiry_raw)
                        )
                        values = (
                            float(row["Time"]), int(row["CellId"]),
                            scheduler_transaction, int(row["ActiveUes"]),
                            float(row["TxPowerPercent"]), float(row["TxPowerDbm"]),
                            power_transaction,
                            None if nominal_power in (None, "") else float(nominal_power),
                        observation_kind, 1 if policy_active else 0, expiry,
                            str(row.get("SourceGeneration") or "").strip(),
                            str(row.get("AssociationEpoch") or "").strip(),
                            None if row.get("ActiveDlSymbols") in (None, "") else int(row.get("ActiveDlSymbols")),
                            None if row.get("ActiveDlSymbolCapacity") in (None, "") else int(row.get("ActiveDlSymbolCapacity")),
                            str(row.get("NativeAllocationSource") or "").strip(),
                            str(row.get("CampaignId") or os.environ.get("GREENRAN_CAMPAIGN_ID", "")).strip(),
                            str(row.get("EvidenceVersion") or "").strip(),
                            str(row.get("CampaignGeneration") or row.get("SourceGeneration") or "").strip(),
                            (None if row.get("DecisionId") in (None, "") else int(row.get("DecisionId"))),
                            str(row.get("ActionCorrelationId") or "").strip(),
                            (None if row.get("NativeControlSequence") in (None, "") else int(row.get("NativeControlSequence"))),
                            (None if row.get("RequestedDiscretionaryDlSymbolsBp") in (None, "") else int(row.get("RequestedDiscretionaryDlSymbolsBp"))),
                            (None if row.get("AppliedDiscretionaryDlSymbolsBp") in (None, "") else int(row.get("AppliedDiscretionaryDlSymbolsBp"))),
                            (None if row.get("MandatoryDlSymbols") in (None, "") else int(row.get("MandatoryDlSymbols"))),
                            (None if row.get("DiscretionaryDlSymbols") in (None, "") else int(row.get("DiscretionaryDlSymbols"))),
                            (None if row.get("WithheldDlSymbols") in (None, "") else int(row.get("WithheldDlSymbols"))),
                            str(row.get("SleepTransactionId") or "").strip(),
                            1 if str(row.get("PowerLeaseFresh", "1") or "1").strip().lower()
                            in {"1", "true", "yes"} else 0,
                        )
                    except (KeyError, TypeError, ValueError):
                        self._last_native_import_invalid_rows += 1
                        continue
                    cursor = self.conn.execute(
                        """
                        INSERT OR IGNORE INTO tasam_control_observations
                          (source_path, sim_time_s, cell_id, transaction_id,
                           power_transaction_id, scheduler_transaction_id,
                           active_ues, tx_power_percent, tx_power_dbm,
                           nominal_tx_power_dbm, observation_kind, policy_active,
                           policy_expiry_sim_time, source_generation,
                           association_epoch, native_allocated_dl_symbols,
                           native_dl_symbol_capacity,
                           requested_discretionary_dl_symbols_bp,
                           applied_discretionary_dl_symbols_bp,
                           mandatory_dl_symbols, discretionary_dl_symbols,
                           withheld_dl_symbols, sleep_transaction_id,
                           native_allocation_source,
                           native_allocation_fraction,
                           campaign_id, campaign_generation, decision_id,
                           action_correlation_id, native_control_sequence,
                           evidence_version, power_lease_fresh, imported_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (resolved_path, values[0], values[1], values[2], values[6],
                         values[2], values[3], values[4], values[5], values[7],
                         values[8], values[9], values[10], values[11], values[12],
                         values[13], values[14],
                         values[22], values[23], values[24], values[25], values[26], values[27],
                         values[15],
                         (None if values[14] in (None, 0) else
                          max(0.0, min(1.0, float(values[13]) / float(values[14])))),
                         values[16], values[18], values[19], values[20], values[21],
                         values[17] or (
                             "v3" if values[8] in {"power_readback", "state_snapshot"} else
                             ("v2" if values[6] > 0 else "v1")
                         ), values[28], int(time.time())),
                    )
                    imported += int(cursor.rowcount or 0)
            self.conn.execute(
                """INSERT INTO tasam_native_ingest_state
                   (source_path, inode, byte_offset, header, last_error,
                    reset_count, updated_at)
                   VALUES (?, ?, ?, ?, '', ?, ?)
                   ON CONFLICT(source_path) DO UPDATE SET
                     inode=excluded.inode, byte_offset=excluded.byte_offset,
                     header=excluded.header, last_error='',
                     reset_count=excluded.reset_count, updated_at=excluded.updated_at""",
                (resolved_path, inode, new_offset, header, reset_count, int(time.time())),
            )
            self.conn.commit()
        except (OSError, sqlite3.Error, csv.Error) as exc:
            self._last_native_import_error = f"{type(exc).__name__}: {exc}"[:512]
            try:
                self.conn.execute(
                    """INSERT INTO tasam_native_ingest_state
                       (source_path, inode, byte_offset, header, last_error,
                        reset_count, updated_at)
                       VALUES (?, 0, 0, '', ?, 0, ?)
                       ON CONFLICT(source_path) DO UPDATE SET
                         last_error=excluded.last_error, updated_at=excluded.updated_at""",
                    (resolved_path, f"{type(exc).__name__}: {exc}"[:512], int(time.time())),
                )
                self.conn.commit()
            except sqlite3.Error:
                pass
            return imported
        return imported

    def ingest_native_association_observations(self, source_path=None):
        """Import the native ``(sim_time, cell, RNTI, IMSI)`` trace.

        Association evidence is deliberately separate from scheduler and
        power observations.  It is append-only, idempotent and read only for
        the collector: an association row can explain a cell mapping but can
        never by itself confirm an economic action.
        """
        if source_path:
            path = Path(source_path)
        else:
            output_dir = os.environ.get("GREENRAN_NS3_ENERGY_OUTPUT_DIR")
            if output_dir:
                path = Path(output_dir) / "TasamAssociationTrace.csv"
            else:
                state_dir = Path(os.environ.get("GREENRAN_STATE_DIR", str(RAPP_DB_PATH.parent)))
                path = state_dir / "ns3_energy" / "TasamAssociationTrace.csv"
        self._last_native_import_invalid_rows = 0
        self._last_native_import_partial = False
        self._last_native_import_error = ""
        if not path.is_file():
            return 0
        imported = 0
        resolved_path = str(path.resolve())
        try:
            import csv
            stat = path.stat()
            inode = int(getattr(stat, "st_ino", 0) or 0)
            state = self.conn.execute(
                "SELECT inode, byte_offset, header, reset_count FROM tasam_native_ingest_state "
                "WHERE source_path=?", (resolved_path,)
            ).fetchone()
            previous_inode = int(state[0]) if state else 0
            previous_offset = int(state[1]) if state else 0
            header = str(state[2]) if state else ""
            reset_count = int(state[3] or 0) if state else 0
            if previous_inode != inode or stat.st_size < previous_offset:
                previous_offset = 0
                header = ""
                reset_count += 1
            with path.open("rb") as handle:
                raw_header = handle.readline()
                if not raw_header:
                    return 0
                if not header:
                    header = raw_header.decode("utf-8", errors="replace")
                header_end = handle.tell()
                start = max(header_end, previous_offset)
                handle.seek(start)
                body = handle.read()
            if not body:
                return 0
            last_newline = body.rfind(b"\n")
            if last_newline < 0:
                self._last_native_import_partial = True
                return 0
            complete = body[:last_newline + 1]
            new_offset = start + len(complete)
            text = io.StringIO(header + complete.decode("utf-8", errors="replace"))
            for row in csv.DictReader(text):
                observation_kind = str(row.get("ObservationKind") or "ue_association").strip()
                if observation_kind == "cell_snapshot":
                    try:
                        sim_time = float(row["Time"])
                        cell_id = int(row["CellId"])
                        attached = int(row.get("AttachedUeCount", 0) or 0)
                        transaction_id = int(
                            row.get("TransactionId")
                            or row.get("PowerTransactionId")
                            or row.get("SchedulerTransactionId")
                            or 0
                        )
                        native_control_sequence = (
                            None if row.get("NativeControlSequence") in (None, "")
                            else int(row.get("NativeControlSequence"))
                        )
                        decision_id = (
                            None if row.get("DecisionId") in (None, "")
                            else int(row.get("DecisionId"))
                        )
                    except (KeyError, TypeError, ValueError):
                        self._last_native_import_invalid_rows += 1
                        continue
                    if cell_id not in (2, 3, 4) or attached < 0 or not math.isfinite(sim_time):
                        self._last_native_import_invalid_rows += 1
                        continue
                    cursor = self.conn.execute(
                        """INSERT OR IGNORE INTO tasam_native_association_snapshots
                           (source_path, sim_time_s, cell_id, association_epoch,
                            attached_ue_count, campaign_id, source_generation,
                            evidence_version, transaction_id, native_control_sequence,
                            decision_id, action_correlation_id, sleep_transaction_id,
                            imported_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            resolved_path, sim_time, cell_id,
                            str(row.get("AssociationEpoch") or "").strip(), attached,
                            str(row.get("CampaignId") or os.environ.get("GREENRAN_CAMPAIGN_ID", "")).strip(),
                            str(row.get("SourceGeneration") or "").strip(),
                            str(row.get("EvidenceVersion") or "v1").strip(), transaction_id,
                            native_control_sequence, decision_id,
                            str(row.get("ActionCorrelationId") or "").strip(),
                            str(row.get("SleepTransactionId") or "").strip(), int(time.time()),
                        ),
                    )
                    imported += int(cursor.rowcount or 0)
                    continue
                try:
                    sim_time = float(row["Time"])
                    cell_id = int(row["CellId"])
                    rnti = int(row["Rnti"])
                    imsi = int(row["Imsi"])
                    epoch = str(row.get("AssociationEpoch") or "").strip()
                    campaign_id = str(
                        row.get("CampaignId")
                        or os.environ.get("GREENRAN_CAMPAIGN_ID", "")
                    ).strip()
                    source_generation = str(row.get("SourceGeneration") or "").strip()
                    transaction_id = int(
                        row.get("TransactionId")
                        or row.get("PowerTransactionId")
                        or row.get("SchedulerTransactionId")
                        or 0
                    )
                    native_control_sequence = (
                        None if row.get("NativeControlSequence") in (None, "")
                        else int(row.get("NativeControlSequence"))
                    )
                    decision_id = (
                        None if row.get("DecisionId") in (None, "")
                        else int(row.get("DecisionId"))
                    )
                    action_correlation_id = str(
                        row.get("ActionCorrelationId") or ""
                    ).strip()
                except (KeyError, TypeError, ValueError):
                    self._last_native_import_invalid_rows += 1
                    continue
                if not all(math.isfinite(value) for value in (sim_time,)):
                    continue
                cursor = self.conn.execute(
                    """INSERT OR IGNORE INTO tasam_native_associations
                       (source_path, sim_time_s, cell_id, rnti, imsi,
                       association_epoch, campaign_id, source_generation,
                        evidence_version, transaction_id, native_control_sequence,
                        decision_id, action_correlation_id, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (resolved_path, sim_time, cell_id, rnti, imsi, epoch,
                     campaign_id, source_generation,
                     str(row.get("EvidenceVersion") or "v1").strip(),
                     transaction_id, native_control_sequence, decision_id,
                     action_correlation_id, int(time.time())),
                )
                imported += int(cursor.rowcount or 0)
            self.conn.execute(
                """INSERT INTO tasam_native_ingest_state
                   (source_path, inode, byte_offset, header, last_error,
                    reset_count, updated_at)
                   VALUES (?, ?, ?, ?, '', ?, ?)
                   ON CONFLICT(source_path) DO UPDATE SET
                     inode=excluded.inode, byte_offset=excluded.byte_offset,
                     header=excluded.header, last_error='',
                     reset_count=excluded.reset_count, updated_at=excluded.updated_at""",
                (resolved_path, inode, new_offset, header, reset_count, int(time.time())),
            )
            self.conn.commit()
        except (OSError, sqlite3.Error, csv.Error) as exc:
            self._last_native_import_error = f"{type(exc).__name__}: {exc}"[:512]
            try:
                self.conn.execute(
                    """INSERT INTO tasam_native_ingest_state
                       (source_path, inode, byte_offset, header, last_error,
                        reset_count, updated_at)
                       VALUES (?, 0, 0, '', ?, 0, ?)
                       ON CONFLICT(source_path) DO UPDATE SET
                         last_error=excluded.last_error, updated_at=excluded.updated_at""",
                    (resolved_path, f"{type(exc).__name__}: {exc}"[:512], int(time.time())),
                )
                self.conn.commit()
            except sqlite3.Error:
                pass
            return imported
        return imported

    def ingest_native_evidence_cycle(
        self, control_path=None, association_path=None, *,
        campaign_id=None, source_generation=None,
    ):
        """Ingest native evidence on every controller cycle.

        This method is intentionally separate from confirmation: importing a
        row proves only that ns-3 emitted an observation.  Confirmation still
        requires a matching pending action, all expected cells, a valid
        policy snapshot and the later PDCP window.  The small event ledger is
        used by the learning meter to distinguish an empty shadow bundle from
        an actual import failure.
        """
        def default_path(name, explicit):
            if explicit:
                return Path(explicit)
            output_dir = os.environ.get("GREENRAN_NS3_ENERGY_OUTPUT_DIR")
            if output_dir:
                return Path(output_dir) / name
            state_dir = Path(os.environ.get("GREENRAN_STATE_DIR", str(RAPP_DB_PATH.parent)))
            return state_dir / "ns3_energy" / name

        control = default_path("TasamControlObservations.csv", control_path)
        association = default_path("TasamAssociationTrace.csv", association_path)
        sources = ((control, self.ingest_native_control_observations),
                   (association, self.ingest_native_association_observations))
        results = []
        for path, importer in sources:
            resolved = str(path.resolve())
            before = 0
            state_before = self.conn.execute(
                "SELECT inode, byte_offset FROM tasam_native_ingest_state WHERE source_path=?",
                (resolved,),
            ).fetchone()
            if path.name.startswith("TasamControl"):
                table = "tasam_control_observations"
            else:
                table = "tasam_native_associations"
            try:
                before = int(self.conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE source_path=?", (resolved,)
                ).fetchone()[0] or 0)
            except sqlite3.Error:
                pass
            error = ""
            try:
                imported = int(importer(path) or 0)
            except Exception as exc:  # the cycle must report, not hide, an importer fault
                imported = 0
                error = f"{type(exc).__name__}: {exc}"[:512]
            invalid_rows = int(getattr(self, "_last_native_import_invalid_rows", 0) or 0)
            partial = bool(getattr(self, "_last_native_import_partial", False))
            importer_error = str(getattr(self, "_last_native_import_error", "") or "")
            if importer_error and not error:
                error = importer_error
            state_after = self.conn.execute(
                "SELECT inode, byte_offset, reset_count, last_error "
                "FROM tasam_native_ingest_state WHERE source_path=?",
                (resolved,),
            ).fetchone()
            after = before
            try:
                after = int(self.conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE source_path=?", (resolved,)
                ).fetchone()[0] or 0)
            except sqlite3.Error:
                pass
            reset = bool(
                state_before is not None and state_after is not None
                and (int(state_before[0] or 0) != int(state_after[0] or 0)
                     or int(state_after[1] or 0) < int(state_before[1] or 0))
            )
            missing = not path.is_file()
            if missing and state_after is None:
                error = "native_trace_missing"
            row = {
                "source_path": resolved,
                "campaign_id": str(campaign_id or os.environ.get("GREENRAN_CAMPAIGN_ID", "")),
                "source_generation": str(source_generation or os.environ.get("GREENRAN_NATIVE_SOURCE_GENERATION", "")),
                "evidence_version": str(os.environ.get("GREENRAN_NATIVE_EVIDENCE_VERSION", "")),
                "inode": int(state_after[0] or 0) if state_after else 0,
                "start_offset": int(state_before[1] or 0) if state_before else 0,
                "end_offset": int(state_after[1] or 0) if state_after else 0,
                "imported_rows": max(0, after - before),
                "invalid_rows": invalid_rows,
                "partial_line_pending": partial,
                "reset_detected": int(reset),
                "error": error or str(state_after[3] or "") if state_after else error,
            }
            if invalid_rows and not row["error"]:
                row["error"] = f"native_trace_invalid_rows:{invalid_rows}"
            results.append(row)
            if (
                row["imported_rows"] or row["invalid_rows"] or row["error"]
                or row["reset_detected"] or row["partial_line_pending"]
            ):
                self.conn.execute(
                    """INSERT INTO tasam_native_evidence_ingest
                       (source_path, campaign_id, source_generation, evidence_version,
                        inode, start_offset, end_offset, imported_rows, invalid_rows,
                        reset_detected, error, sim_time_s, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               (SELECT MAX(sim_time_s) FROM tasam_control_observations
                                WHERE source_path=?), ?)""",
                    (row["source_path"], row["campaign_id"], row["source_generation"],
                     row["evidence_version"], row["inode"], row["start_offset"],
                     row["end_offset"], row["imported_rows"], row["invalid_rows"],
                     row["reset_detected"], row["error"], row["source_path"], int(time.time())),
                )
        self.conn.commit()
        return {
            "schema": "greenran.tasam.native_evidence_cycle.v1",
            "campaign_id": str(campaign_id or os.environ.get("GREENRAN_CAMPAIGN_ID", "")),
            "source_generation": str(source_generation or os.environ.get("GREENRAN_NATIVE_SOURCE_GENERATION", "")),
            "evidence_version": str(os.environ.get("GREENRAN_NATIVE_EVIDENCE_VERSION", "")),
            "sources": results,
            "imported_control_rows": sum(r["imported_rows"] for r in results[:1]),
            "imported_association_rows": sum(r["imported_rows"] for r in results[1:]),
            "invalid_control_rows": sum(r["invalid_rows"] for r in results[:1]),
            "invalid_association_rows": sum(r["invalid_rows"] for r in results[1:]),
            "partial_lines_pending": any(
                bool(r.get("partial_line_pending")) for r in results
            ),
            "valid": all(not r["error"] for r in results),
        }

    def confirm_native_control_observation(
        self, transaction_id, expected_power_percent, expected_cell_ids,
        *, sim_time_s=None, ttl_s=5.0, source_generation=None,
        require_evidence_version="v3", campaign_id=None,
        action_correlation_id=None,
    ):
        """Confirm one command from independent PHY and scheduler evidence.

        A ``power_readback`` row proves the PHY changed.  A later
        ``state_snapshot`` row for the same cell/transaction proves that the
        scheduler policy was active.  Transport ACKs and legacy rows are not
        sufficient for current campaigns.
        """
        try:
            transaction_id = int(transaction_id)
            expected_power_by_cell = None
            if isinstance(expected_power_percent, dict):
                expected_power_by_cell = {
                    int(cell): float(value) for cell, value in expected_power_percent.items()
                }
                expected_power_percent = None
            else:
                expected_power_percent = float(expected_power_percent)
            expected_cells = {int(value) for value in expected_cell_ids}
        except (TypeError, ValueError):
            return {"valid": False, "reason": "native_observation_contract_invalid"}
        if not expected_cells:
            return {"valid": False, "reason": "native_observation_cells_missing"}
        if expected_power_by_cell is not None and set(expected_power_by_cell) != expected_cells:
            return {"valid": False, "reason": "native_observation_power_cell_map_incomplete"}
        if str(require_evidence_version) in {"v5", "v6"} and expected_cells != {2, 3, 4}:
            return {
                "valid": False,
                "reason": "native_observation_expected_three_du_cells",
            }
        self.ingest_native_control_observations()
        self.ingest_native_association_observations()
        try:
            rows = self.conn.execute(
                """
                SELECT cell_id, sim_time_s, active_ues, tx_power_percent, tx_power_dbm,
                       power_transaction_id, scheduler_transaction_id,
                       nominal_tx_power_dbm, observation_kind, policy_active,
                       policy_expiry_sim_time, source_generation, association_epoch,
                       native_allocated_dl_symbols, native_dl_symbol_capacity,
                       native_allocation_source, native_allocation_fraction,
                       campaign_id, evidence_version, campaign_generation,
                       decision_id, action_correlation_id, native_control_sequence,
                       power_lease_fresh
                  FROM tasam_control_observations
                 WHERE (power_transaction_id=? OR scheduler_transaction_id=?)
                   AND (? IS NULL OR campaign_id=?)
                 ORDER BY sim_time_s ASC, id ASC
                """, (transaction_id, transaction_id, campaign_id, campaign_id),
            ).fetchall()
        except sqlite3.Error:
            return {"valid": False, "reason": "native_observation_query_failed"}
        selected_power = {}
        selected_policy = {}
        stale_power_cells = set()
        for row in rows:
            cell_id = int(row[0])
            if cell_id not in expected_cells:
                continue
            if source_generation and str(row[11] or "") != str(source_generation):
                continue
            try:
                row_time = float(row[1])
                row_power = float(row[3])
                row_dbm = float(row[4])
            except (TypeError, ValueError):
                continue
            if not all(math.isfinite(value) for value in (row_time, row_power, row_dbm)):
                continue
            in_window = sim_time_s is None or (
                float(sim_time_s) <= row_time <= float(sim_time_s) + float(ttl_s)
            )
            if not in_window or str(row[18] or "") != str(require_evidence_version):
                continue
            if action_correlation_id and str(row[21] or "") != str(action_correlation_id):
                continue
            if str(require_evidence_version) in {"v5", "v6"}:
                # Current evidence is never allowed to silently inherit a
                # row from another campaign/generation or from a context
                # sidecar that was not attached to this control action.
                if not campaign_id or str(row[17] or "") != str(campaign_id):
                    continue
                if not source_generation or str(row[11] or "") != str(source_generation):
                    continue
                if not action_correlation_id or not str(row[21] or ""):
                    continue
                if int(row[22] or 0) != transaction_id:
                    continue
            if (
                int(row[5] or 0) == transaction_id
                and str(row[8] or "") == "power_readback"
                and math.isfinite(row_power)
                and math.isfinite(row_dbm)
                and abs(
                    row_power - (
                        expected_power_by_cell.get(cell_id)
                        if expected_power_by_cell is not None
                        else expected_power_percent
                    )
                ) <= 0.1
            ) and cell_id not in selected_power:
                if int(row[23] or 0) != 1:
                    stale_power_cells.add(cell_id)
                else:
                    selected_power[cell_id] = row
            if (
                str(row[8] or "") == "state_snapshot"
                and cell_id not in selected_policy
                and (
                    int(row[6] or 0) == transaction_id
                    or (
                        str(require_evidence_version) in {"v5", "v6"}
                        and int(row[5] or 0) == transaction_id
                    )
                )
            ):
                # A DU without attached UEs has no scheduler policy to
                # activate, but it still emits a truthful native state
                # snapshot for a per-cell power action.  The active scheduler
                # cells are checked below; inactive cells are not promoted to
                # scheduler evidence merely because power was changed.
                selected_policy[cell_id] = row
        if set(selected_power) != expected_cells or set(selected_policy) != expected_cells:
            reason = "native_observation_missing_cell"
            if stale_power_cells:
                reason = "native_observation_power_lease_stale"
            elif selected_power or selected_policy:
                reason = "native_observation_transaction_power_policy_or_window_mismatch"
            return {
                "valid": False,
                "reason": reason,
                "observed_power_cells": sorted(selected_power),
                "observed_policy_cells": sorted(selected_policy),
            }
        selected_times = [
            float(selected_power[cell][1]) for cell in expected_cells
        ] + [
            float(selected_policy[cell][1]) for cell in expected_cells
        ]
        if max(selected_times) - min(selected_times) > float(ttl_s):
            return {
                "valid": False,
                "reason": "native_observation_window_not_common",
                "observed_power_cells": sorted(selected_power),
                "observed_policy_cells": sorted(selected_policy),
            }
        invalid_policy_cells = []
        active_policy_cells = []
        for cell in expected_cells:
            policy_row = selected_policy[cell]
            policy_active = int(policy_row[9] or 0) == 1
            if policy_active:
                active_policy_cells.append(cell)
                if (
                    policy_row[14] in (None, 0)
                    or policy_row[16] is None
                    or not math.isfinite(float(policy_row[16]))
                ):
                    invalid_policy_cells.append(cell)
            if (
                str(require_evidence_version) in {"v5", "v6"}
                and str(policy_row[15] or "") != "tasam_native_aggregate_v1"
            ):
                invalid_policy_cells.append(cell)
        if invalid_policy_cells:
            return {
                "valid": False,
                "reason": "native_observation_allocation_missing",
                "observed_power_cells": sorted(selected_power),
                "observed_policy_cells": sorted(selected_policy),
                "invalid_policy_cells": sorted(set(invalid_policy_cells)),
            }
        native_energy_evidence = None
        native_power_mode = os.environ.get(
            "GREENRAN_TASAM_NATIVE_POWER_CONTROL", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if native_power_mode and str(require_evidence_version) == "v6":
            state_dir_raw = str(os.environ.get("GREENRAN_STATE_DIR", "") or "").strip()
            state_dir = Path(state_dir_raw).resolve() if state_dir_raw else None
            energy_dir = state_dir / "ns3_energy" if state_dir is not None else None
            if energy_dir is None or not energy_dir.is_dir():
                return {
                    "valid": False,
                    "reason": "native_energy_trace_missing",
                    "observed_power_cells": sorted(selected_power),
                    "observed_policy_cells": sorted(selected_policy),
                }
            native_energy_evidence = {}
            for cell in sorted(expected_cells):
                path = energy_dir / f"energyfilecell{cell}.csv"
                try:
                    with path.open(newline="", encoding="utf-8", errors="strict") as handle:
                        matching = [
                            row for row in csv.DictReader(handle)
                            if str(row.get("PowerTransactionId", "")).strip()
                            == str(transaction_id)
                        ]
                except (OSError, UnicodeError, csv.Error):
                    matching = []
                if not matching:
                    return {
                        "valid": False,
                        "reason": "native_energy_transaction_missing",
                        "energy_cell": cell,
                    }
                row = matching[-1]
                try:
                    tx_power = float(row["TxPowerPercent"])
                    model_power = float(row["ModelTxPowerPercent"])
                    tasam_power = float(row["TasamTxPowerPercent"])
                    lease_fresh = int(float(row["PowerLeaseFresh"]))
                    expected = (
                        expected_power_by_cell[cell]
                        if expected_power_by_cell is not None
                        else float(expected_power_percent)
                    )
                except (KeyError, TypeError, ValueError):
                    return {
                        "valid": False,
                        "reason": "native_energy_power_fields_invalid",
                        "energy_cell": cell,
                    }
                if (
                    not all(math.isfinite(value) for value in
                            (tx_power, model_power, tasam_power, expected))
                    or abs(tx_power - model_power) > 0.1
                    or abs(tx_power - tasam_power) > 0.1
                    or abs(tx_power - expected) > 0.1
                    or lease_fresh != 1
                ):
                    return {
                        "valid": False,
                        "reason": "native_energy_power_readback_mismatch",
                        "energy_cell": cell,
                        "tx_power_percent": tx_power,
                        "model_tx_power_percent": model_power,
                        "tasam_tx_power_percent": tasam_power,
                        "expected_power_percent": expected,
                        "power_lease_fresh": lease_fresh,
                    }
                native_energy_evidence[str(cell)] = {
                    "tx_power_percent": tx_power,
                    "model_tx_power_percent": model_power,
                    "tasam_tx_power_percent": tasam_power,
                    "power_lease_fresh": True,
                }
        powers = [float(selected_power[cell][3]) for cell in sorted(expected_cells)]
        return {
            "valid": True,
            "source": "ns3_tasam_control_observations",
            "campaign_id": str(campaign_id or ""),
            "source_generation": str(source_generation or ""),
            "transaction_id": transaction_id,
            "action_correlation_id": str(action_correlation_id or ""),
            "cell_ids": sorted(expected_cells),
            "power_percent": sum(powers) / len(powers),
            "power_percent_by_cell": {
                str(cell): float(selected_power[cell][3]) for cell in sorted(expected_cells)
            },
            "power_transaction_id": transaction_id,
            "scheduler_transaction_id": transaction_id,
            "policy_active_cells": sorted(active_policy_cells),
            "power_control_cells": sorted(expected_cells),
            "policy_scope": "serving_cells_only",
            "evidence_version": require_evidence_version,
            "native_energy_evidence": native_energy_evidence,
            "active_ues": sum(int(selected_policy[cell][2] or 0) for cell in expected_cells),
            "sim_time_s": max(float(selected_policy[cell][1]) for cell in expected_cells),
            "model_component_mode": "combined_radio_relative" if True else "separate_components",
            "observed_ru_count": None,
            "observed_mmwave_count": len(expected_cells),
            "active_dl_symbols": sum(
                int(selected_policy[cell][13] or 0) for cell in expected_cells
            ),
            "active_dl_symbol_capacity": sum(
                int(selected_policy[cell][14] or 0) for cell in expected_cells
            ),
            "native_allocation_fraction": (
                sum(float(selected_policy[cell][16] or 0.0) for cell in expected_cells)
                / len(expected_cells)
            ),
            "observations": [
                {
                    "cell_id": int(cell),
                    "power_readback_sim_time_s": float(selected_power[cell][1]),
                    "policy_active_sim_time_s": float(selected_policy[cell][1]),
                    "active_ues": int(selected_policy[cell][2] or 0),
                    "tx_power_percent": float(selected_power[cell][3]),
                    "tx_power_dbm": float(selected_power[cell][4]),
                    "power_transaction_id": int(selected_power[cell][5] or 0),
                    "power_lease_fresh": bool(int(selected_power[cell][23] or 0)),
                    "scheduler_transaction_id": int(selected_policy[cell][6] or 0),
                    "nominal_tx_power_dbm": selected_power[cell][7],
                    "active_dl_symbols": int(selected_policy[cell][13] or 0),
                    "active_dl_symbol_capacity": int(selected_policy[cell][14] or 0),
                    "native_allocation_source": selected_policy[cell][15],
                    "native_allocation_fraction": selected_policy[cell][16],
                    "campaign_id": selected_policy[cell][17],
                    "campaign_generation": selected_policy[cell][19],
                    "decision_id": selected_policy[cell][20],
                    "action_correlation_id": selected_policy[cell][21],
                    "native_control_sequence": selected_policy[cell][22],
                    "observation_kind": "power_readback+state_snapshot",
                    "evidence_version": require_evidence_version,
                }
                for cell in sorted(expected_cells)
            ],
        }

    def register_pending_native_action(
        self, campaign_id, decision_id, action_correlation_id,
        native_control_sequence, issued_sim_time_s, ttl_s,
        expected_cells, expected_power_percent,
    ):
        """Persist an E2 action before waiting for delayed native evidence.

        ``expected_power_percent`` accepts a scalar or a per-cell mapping.
        The per-cell mapping is the authoritative form: it must mirror the
        bundle that actually went on the wire (r6g evidence — the global
        summary said 100% while the wire carried a 70% per-DU candidate,
        which made every confirmation mismatch the native readback).
        """
        if not decision_id or not action_correlation_id or not native_control_sequence:
            return False
        now = int(time.time())
        if isinstance(expected_power_percent, dict):
            expected_power_payload = json.dumps(
                {str(cell): float(value) for cell, value in expected_power_percent.items()}
            )
        else:
            expected_power_payload = expected_power_percent
        try:
            self.conn.execute(
                """
                INSERT INTO tasam_pending_native_actions
                (campaign_id, decision_id, action_correlation_id,
                 native_control_sequence, issued_sim_time_s, ttl_s,
                 expected_cells_json, expected_power_percent, status,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_confirmation', ?, ?)
                ON CONFLICT(action_correlation_id) DO UPDATE SET
                  decision_id=excluded.decision_id,
                  updated_at=excluded.updated_at
                """,
                (
                    str(campaign_id or ""), int(decision_id), str(action_correlation_id),
                    int(native_control_sequence), issued_sim_time_s, float(ttl_s),
                    json.dumps(sorted({int(value) for value in expected_cells})),
                    expected_power_payload, now, now,
                ),
            )
            self.conn.commit()
            return True
        except (sqlite3.Error, TypeError, ValueError):
            return False

    def pending_native_expected_power(self, action_correlation_id):
        """Return the registered wire power for a pending action.

        Returns a ``{cell_id: percent}`` dict when the action was
        registered with a per-cell map (the authoritative wire payload),
        a scalar for legacy registrations, or ``None`` when unknown.
        """
        if not action_correlation_id:
            return None
        try:
            row = self.conn.execute(
                """
                SELECT expected_power_percent FROM tasam_pending_native_actions
                 WHERE action_correlation_id=?
                """, (str(action_correlation_id),),
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row or row[0] is None:
            return None
        raw = str(row[0]).strip()
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
        if isinstance(value, dict):
            try:
                return {int(cell): float(percent) for cell, percent in value.items()}
            except (TypeError, ValueError):
                return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def resolve_pending_native_action(
        self, action_correlation_id, status, confirmation=None, reason="",
    ):
        """Close a pending action exactly once after confirmation/expiry."""
        if not action_correlation_id:
            return False
        try:
            cursor = self.conn.execute(
                """
                UPDATE tasam_pending_native_actions
                   SET status=?, confirmation_json=?, invalid_reason=?, updated_at=?
                 WHERE action_correlation_id=? AND status='pending_confirmation'
                """,
                (
                    str(status), json.dumps(confirmation or {}, ensure_ascii=False, sort_keys=True),
                    str(reason or ""), int(time.time()), str(action_correlation_id),
                ),
            )
            self.conn.commit()
            return int(cursor.rowcount or 0) == 1
        except sqlite3.Error:
            return False

    def ensure_initial_energy_observation(self) -> None:
        """Seed a measured runtime state for the first causal comparison.

        The first rApp decision happens before the first actuator command of
        a fresh run.  Persisting the known full-power startup state gives the
        shadow comparator an observed baseline without pretending that the
        first TA-SAM proposal was already applied.
        """
        try:
            exists = self.conn.execute("SELECT 1 FROM energy_commands LIMIT 1").fetchone()
        except sqlite3.Error:
            exists = None
        if exists is None:
            self.record_energy_command(
                command="INITIAL_OBSERVED_STATE",
                power_percent=100,
                ru_count=1,
                mmwave_count=1,
                reason="initial runtime state observed before first decision",
            )

    def latest_energy_observation(self) -> dict:
        """Return the latest persisted energy state for causal comparison."""
        try:
            row = self.conn.execute(
                "SELECT * FROM energy_commands ORDER BY timestamp_ns DESC, id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            return {}
        if row is None:
            return {}
        return {str(key): row[key] for key in row.keys()}

    def _latest_native_rows_by_cell(self, observation_kind: str) -> list[dict]:
        """Return the newest native evidence row for each managed DU.

        This is deliberately a read-only view.  It never synthesizes a
        startup state and it keeps campaign/generation boundaries intact so a
        previous run cannot become the causal reference for a new action.
        """
        kind = str(observation_kind or '').strip()
        if not kind:
            return []
        filters = ["cell_id IN (2, 3, 4)", "observation_kind=?"]
        params: list[object] = [kind]
        campaign = str(os.environ.get('GREENRAN_CAMPAIGN_ID', '') or '').strip()
        generation = str(os.environ.get('GREENRAN_NATIVE_SOURCE_GENERATION', '') or '').strip()
        evidence = str(os.environ.get('GREENRAN_NATIVE_EVIDENCE_VERSION', '') or '').strip()
        if campaign:
            filters.append("campaign_id=?")
            params.append(campaign)
        if generation:
            filters.append("source_generation=?")
            params.append(generation)
        if evidence:
            filters.append("evidence_version=?")
            params.append(evidence)
        try:
            rows = self.conn.execute(
                f"""
                SELECT * FROM tasam_control_observations
                 WHERE {' AND '.join(filters)}
                 ORDER BY sim_time_s DESC, id DESC
                """,
                tuple(params),
            ).fetchall()
        except sqlite3.Error:
            return []
        selected: dict[int, dict] = {}
        for row in rows:
            try:
                cell = int(row['cell_id'])
            except (TypeError, ValueError, IndexError):
                continue
            if cell not in {2, 3, 4} or cell in selected:
                continue
            selected[cell] = {str(key): row[key] for key in row.keys()}
        return [selected[cell] for cell in (2, 3, 4) if cell in selected]

    def latest_native_power_state(self) -> dict:
        """Return a complete, fresh native power state for the three DUs."""
        try:
            self.ingest_native_control_observations()
        except Exception:
            # A partial native CSV write is not evidence.  The query below
            # will either return the last complete rows or an invalid state.
            pass
        rows = self._latest_native_rows_by_cell('power_readback')
        if len(rows) != 3:
            return {'valid': False, 'reason': 'native_pre_action_readback_incomplete', 'rows': rows}
        try:
            sim_times = [float(row['sim_time_s']) for row in rows]
            power = {int(row['cell_id']): float(row['tx_power_percent']) for row in rows}
            transactions = {int(row['cell_id']): int(row['power_transaction_id'] or 0) for row in rows}
            leases = {int(row['cell_id']): bool(int(row['power_lease_fresh'] or 0)) for row in rows}
        except (TypeError, ValueError, KeyError):
            return {'valid': False, 'reason': 'native_pre_action_readback_invalid', 'rows': rows}
        if (
            any(not math.isfinite(value) for value in sim_times + list(power.values()))
            or max(sim_times) - min(sim_times) > 5.0
            or any(value <= 0 for value in transactions.values())
            or not all(leases.values())
        ):
            return {'valid': False, 'reason': 'native_pre_action_readback_stale_or_expired', 'rows': rows}
        return {
            'valid': True,
            'sim_time_s': max(sim_times),
            'power_percent_by_cell': power,
            'power_transaction_ids': transactions,
            'power_lease_fresh_by_cell': leases,
            'rows': rows,
        }

    def latest_native_symbol_state(self) -> dict:
        """Return symbol-capacity/readback state for all managed DUs."""
        try:
            self.ingest_native_control_observations()
        except Exception:
            pass
        rows = self._latest_native_rows_by_cell('state_snapshot')
        if len(rows) != 3:
            return {'valid': False, 'reason': 'native_symbol_state_incomplete', 'rows': rows}
        try:
            sim_times = [float(row['sim_time_s']) for row in rows]
            capacities = {int(row['cell_id']): int(row['native_dl_symbol_capacity']) for row in rows}
            allocated = {int(row['cell_id']): int(row['native_allocated_dl_symbols'] or 0) for row in rows}
        except (TypeError, ValueError, KeyError):
            return {'valid': False, 'reason': 'native_symbol_state_invalid', 'rows': rows}
        if (
            any(not math.isfinite(value) for value in sim_times)
            or max(sim_times) - min(sim_times) > 5.0
            # Capacity zero is a valid native scheduler state before the
            # first policy has materialized.  The snapshot is still complete
            # readback; treating it as invalid here deadlocks bootstrap:
            # TA-SAM waits for symbols while the scheduler waits for the
            # first accepted TA-SAM bundle.  No synthetic budget is inferred
            # from this state; the bundle path still records the native zero
            # capacity and the next native observation must confirm use.
            or any(value < 0 for value in capacities.values())
            or any(value < 0 for value in allocated.values())
        ):
            return {'valid': False, 'reason': 'native_symbol_state_stale_or_invalid', 'rows': rows}
        return {
            'valid': True,
            'sim_time_s': max(sim_times),
            'capacity_by_cell': capacities,
            'allocated_by_cell': allocated,
            'rows': rows,
        }

    def real_pdcp_loss_coverage(self, metric_snapshot_id=None) -> dict:
        """Return complete canonical loss coverage for one real-PDCP window."""
        expected = get_fixed_service_imsis()
        expected_by_imsi = {
            str(imsi): service
            for service, values in expected.items()
            if service in {'camera', 'sensor', 'vehicle'}
            for imsi in values
        }
        expected_imsis = set(expected_by_imsi)
        try:
            columns = {str(row[1]) for row in self.conn.execute("PRAGMA table_info(ue_metrics)")}
            required = {"pdcp_provenance", "packet_loss_percent", "device_type", "timestamp"}
            if not required.issubset(columns):
                return {"valid": False, "groups": {}, "expected_imsis": sorted(expected_imsis), "reason": "ue_loss_columns_missing"}
            resolved_metric_id = None
            if metric_snapshot_id:
                try:
                    resolved_metric_id = int(metric_snapshot_id)
                except (TypeError, ValueError):
                    resolved_metric_id = None
            if resolved_metric_id:
                row = self.conn.execute(
                    "SELECT timestamp FROM extended_metrics WHERE id=?",
                    (resolved_metric_id,),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT MAX(timestamp) AS timestamp FROM ue_metrics WHERE pdcp_provenance='pdcp_real'"
                ).fetchone()
            timestamp = row[0] if row else None
            if timestamp is None:
                return {
                    "valid": False,
                    "groups": {service: False for service in ('camera', 'sensor', 'vehicle')},
                    "expected_imsis": sorted(expected_imsis),
                    "reason": "real_pdcp_loss_rows_missing",
                }
            rows = self.conn.execute(
                "SELECT imsi, device_type, packet_loss_percent, pdcp_provenance, latency_is_proxy FROM ue_metrics "
                "WHERE timestamp=? AND pdcp_provenance='pdcp_real'",
                (timestamp,),
            ).fetchall()
        except sqlite3.Error:
            return {"valid": False, "groups": {}, "reason": "ue_loss_query_failed"}
        observed = []
        duplicate_imsis = []
        invalid_rows = []
        service_mismatch = []
        for row in rows:
            imsi = str(row[0])
            observed.append(imsi)
            expected_service = expected_by_imsi.get(imsi)
            actual_service = str(row[1] or '').strip().lower()
            if expected_service and actual_service != expected_service:
                service_mismatch.append(imsi)
            if row[3] != 'pdcp_real' or row[4] or row[2] is None or imsi not in expected_by_imsi:
                invalid_rows.append(imsi)
        observed_set = set(observed)
        duplicate_imsis = sorted({imsi for imsi in observed if observed.count(imsi) > 1})
        groups = {
            service: all(
                imsi in observed_set
                and imsi not in invalid_rows
                for imsi, row_service in expected_by_imsi.items()
                if row_service == service
            )
            for service in ('camera', 'sensor', 'vehicle')
        }
        topology_valid = (
            len(rows) == len(expected_imsis)
            and observed_set == expected_imsis
            and not duplicate_imsis
            and not invalid_rows
            and not service_mismatch
        )
        valid = topology_valid and all(groups.values())
        reason = 'ok' if valid else (
            'pdcp_metric_snapshot_incomplete'
            if invalid_rows or service_mismatch else
            'canonical_topology_mismatch'
            if observed_set != expected_imsis or duplicate_imsis or len(rows) != len(expected_imsis)
            else 'real_pdcp_loss_group_missing'
        )
        return {
            "valid": valid,
            "groups": groups,
            "timestamp": int(timestamp),
            "metric_snapshot_id": resolved_metric_id,
            "expected_imsis": sorted(expected_imsis, key=lambda value: int(value)),
            "observed_imsis": sorted(observed_set, key=lambda value: int(value) if value.isdigit() else value),
            "duplicate_imsis": duplicate_imsis,
            "invalid_imsis": sorted(set(invalid_rows), key=lambda value: int(value) if value.isdigit() else value),
            "service_mismatch_imsis": sorted(set(service_mismatch), key=lambda value: int(value) if value.isdigit() else value),
            "topology_valid": topology_valid,
            "reason": reason,
        }

    def sla_violation_keys(self, metric_snapshot_id=None) -> dict:
        """Return strict-pair-compatible SLA keys for one real PDCP window.

        The runtime dynamic floor and the offline strict-pair evaluator must
        classify the same evidence.  This method deliberately mirrors the
        evaluator's per-IMSI thresholds and key shape ``(window, imsi,
        reason)`` while binding the rows to the metric snapshot that closes
        the previous E2 action.
        """
        expected_groups = get_fixed_service_imsis()
        expected = sorted({
            int(imsi)
            for service, values in expected_groups.items()
            if service in {'camera', 'sensor', 'vehicle'}
            for imsi in values
        })
        try:
            resolved_metric_id = int(metric_snapshot_id or 0) or None
        except (TypeError, ValueError):
            resolved_metric_id = None
        try:
            if resolved_metric_id:
                metric = self.conn.execute(
                    "SELECT timestamp FROM extended_metrics WHERE id=?",
                    (resolved_metric_id,),
                ).fetchone()
                timestamp = metric[0] if metric else None
            else:
                metric = self.conn.execute(
                    "SELECT MAX(timestamp) FROM ue_metrics WHERE pdcp_provenance='pdcp_real'"
                ).fetchone()
                timestamp = metric[0] if metric else None
            if timestamp is None:
                return {
                    "valid": False,
                    "reason": "real_pdcp_sla_rows_missing",
                    "metric_snapshot_id": resolved_metric_id,
                    "violation_keys": [],
                }
            rows = self.conn.execute(
                """SELECT id, imsi, throughput_kbps, latency_p95_us,
                          latency_max_us, packet_loss_percent, tx_pdus,
                          rx_pdus, backlog_bytes, latency_is_proxy,
                          pdcp_provenance, has_latency_samples, sim_time_s
                     FROM ue_metrics
                    WHERE timestamp=?
                    ORDER BY id""",
                (timestamp,),
            ).fetchall()
        except sqlite3.Error:
            return {
                "valid": False,
                "reason": "real_pdcp_sla_query_failed",
                "metric_snapshot_id": resolved_metric_id,
                "violation_keys": [],
            }

        by_imsi = {int(row[1] or 0): row for row in rows if int(row[1] or 0) in expected}
        sim_times = [float(row[12] or 0.0) for row in by_imsi.values()]
        window = int(math.floor(max(sim_times))) if sim_times else -1
        keys: set[tuple[int, int, str]] = set()
        details = []
        for imsi in expected:
            row = by_imsi.get(imsi)
            reasons = []
            if row is None:
                reasons.append("missing_ue_window")
            else:
                throughput = float(row[2] or 0.0)
                p95 = float(row[3] if row[3] is not None else row[4] or 0.0)
                packet_loss = row[5]
                tx_pdus = float(row[6] or 0.0)
                rx_pdus = float(row[7] or 0.0)
                backlog = float(row[8] or 0.0)
                real = (
                    int(row[9] or 0) == 0
                    and str(row[10] or "") == "pdcp_real"
                    and int(row[11] or 0) == 1
                )
                if not real:
                    reasons.append("non_real_or_missing_pdcp")
                loss = (
                    max(0.0, float(packet_loss))
                    if packet_loss is not None else
                    (100.0 if tx_pdus <= 0 else 100.0 * max(0.0, 1.0 - rx_pdus / tx_pdus))
                )
                if tx_pdus <= 0 or rx_pdus <= 0 or (backlog > 0 and throughput <= 0):
                    reasons.append("disconnected_or_unserved")
                if 1 <= imsi <= 3:
                    if throughput < 25_000:
                        reasons.append("camera_throughput")
                    if p95 > 80_000:
                        reasons.append("camera_p95")
                elif 4 <= imsi <= 15:
                    delivery = 0.0 if tx_pdus <= 0 else 100.0 * rx_pdus / tx_pdus
                    if delivery < 95.0:
                        reasons.append("sensor_delivery")
                    if loss > 5.0:
                        reasons.append("sensor_loss")
                    if p95 > 500_000:
                        reasons.append("sensor_p95")
                elif 16 <= imsi <= 20:
                    if p95 > 20_000:
                        reasons.append("vehicle_p95_latency")
                    if loss > 1.0:
                        reasons.append("vehicle_loss")
            for reason in reasons:
                keys.add((window, imsi, reason))
            if reasons:
                details.append({"window_s": window, "imsi": imsi, "reasons": reasons})

        complete = len(by_imsi) == len(expected) and window >= 0
        return {
            "valid": complete,
            "reason": "ok" if complete else "pdcp_metric_snapshot_incomplete",
            "timestamp": int(timestamp),
            "metric_snapshot_id": resolved_metric_id,
            "window_s": window,
            "expected_imsis": expected,
            "observed_imsis": sorted(by_imsi),
            "violation_keys": sorted(keys),
            "violations": details,
        }

    def sla_floor_window_health(self, metric_snapshot_id=None) -> dict:
        """Evaluate the all-UE window contract used by the SLA floor.

        The historical ``sla_violation_keys`` contract intentionally remains
        unchanged.  This stricter companion adds the V2X TX quota and rejects
        duplicate/incomplete rows before the floor state machine can descend.
        """
        base = self.sla_violation_keys(metric_snapshot_id)
        expected = set(range(1, 21))
        timestamp = base.get("timestamp")
        if timestamp is None:
            return {
                "valid": False,
                "reason": str(base.get("reason") or "sla_snapshot_invalid"),
                "complete": False,
                "window_id": base.get("window_s"),
                "metric_snapshot_id": base.get("metric_snapshot_id"),
                "violations": base.get("violations") or [],
            }
        try:
            rows = self.conn.execute(
                """SELECT imsi, tx_pdus, rx_pdus, latency_p95_us,
                          latency_max_us, latency_is_proxy, pdcp_provenance,
                          has_latency_samples, packet_loss_percent
                     FROM ue_metrics
                    WHERE timestamp=?
                    ORDER BY id""",
                (timestamp,),
            ).fetchall()
        except sqlite3.Error:
            return {
                "valid": False,
                "reason": "sla_floor_metric_query_failed",
                "window_id": base.get("window_s"),
                "metric_snapshot_id": base.get("metric_snapshot_id"),
                "violations": [],
            }
        by_imsi: dict[int, tuple] = {}
        duplicates: set[int] = set()
        violations = list(base.get("violations") or [])
        for row in rows:
            try:
                imsi = int(row[0])
            except (TypeError, ValueError):
                continue
            if imsi not in expected:
                continue
            if imsi in by_imsi:
                duplicates.add(imsi)
            by_imsi[imsi] = row
        if duplicates:
            violations.append({
                "window_s": base.get("window_s"),
                "imsi": 0,
                "reasons": ["duplicate_imsi_rows"],
                "duplicate_imsis": sorted(duplicates),
            })
        for imsi in sorted(expected):
            row = by_imsi.get(imsi)
            reasons = []
            if row is None:
                reasons.append("missing_ue_window")
            else:
                tx_pdus = float(row[1] or 0.0)
                rx_pdus = float(row[2] or 0.0)
                p95 = float(row[3] if row[3] is not None else row[4] or 0.0)
                real = (
                    int(row[5] or 0) == 0
                    and str(row[6] or "") == "pdcp_real"
                    and int(row[7] or 0) == 1
                )
                if not real:
                    reasons.append("non_real_or_missing_pdcp")
                if tx_pdus <= 0 or rx_pdus <= 0:
                    reasons.append("no_pdcp_traffic")
                if 16 <= imsi <= 20 and tx_pdus < 500:
                    reasons.append("vehicle_tx_below_500")
                if p95 < 0:
                    reasons.append("invalid_p95")
            if reasons:
                violations.append({
                    "window_s": base.get("window_s"),
                    "imsi": imsi,
                    "reasons": reasons,
                })
        complete = len(rows) == len(expected) and set(by_imsi) == expected and not duplicates
        valid = bool(complete and base.get("valid") and not base.get("violation_keys") and not violations)
        return {
            "valid": valid,
            "reason": "ok" if valid else "sla_floor_window_unhealthy",
            "window_id": base.get("window_s"),
            "window_s": base.get("window_s"),
            "timestamp": int(timestamp),
            "metric_snapshot_id": base.get("metric_snapshot_id"),
            "expected_imsis": sorted(expected),
            "observed_imsis": sorted(by_imsi),
            "duplicate_imsis": sorted(duplicates),
            "complete": complete,
            "violations": violations,
            "base_sla": base,
        }

    def record_app2_snapshot(self, snapshot=None, sensors=None, timestamp=None):
        """
        Registra snapshot agregado e leituras individuais da App2 no Data Lake.

        Args:
            snapshot: Dict do arquivo monitoring_snapshot.json
            sensors: Lista opcional do arquivo sensors/latest.json
            timestamp: Unix timestamp opcional
        """
        if not snapshot:
            return

        if sensors is None:
            sensors = []

        snapshot_ts = snapshot.get('timestamp')
        if timestamp is None:
            if isinstance(snapshot_ts, (int, float)):
                timestamp = int(snapshot_ts)
            elif isinstance(snapshot_ts, str):
                try:
                    timestamp = int(datetime.fromisoformat(snapshot_ts.replace('Z', '+00:00')).timestamp())
                except ValueError:
                    timestamp = int(time.time())
            else:
                timestamp = int(time.time())

        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        sensors_summary = snapshot.get('sensors', {}) or {}
        readings = snapshot.get('readings', {}) or {}
        network = snapshot.get('network', {}) or {}
        alerts = snapshot.get('alerts', []) or []

        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO app2_snapshots
                (timestamp, datetime, total_sensors, active_sensors, connected_sensors,
                 error_sensors, low_battery_sensors, gateways_count, connectivity_modes_count,
                 avg_temperature_c, avg_humidity_percent, avg_soil_conductivity,
                 avg_battery_percent, avg_power_mw, packet_loss_percent,
                 tx_packets, rx_packets, lost_packets, avg_latency_ms, avg_rssi_dbm,
                 network_utilization_percent, delivery_success_percent, alerts_count, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                dt_str,
                int(sensors_summary.get('total', 0) or 0),
                int(sensors_summary.get('active', 0) or 0),
                int(sensors_summary.get('connected', 0) or 0),
                int(sensors_summary.get('error', 0) or 0),
                int(sensors_summary.get('low_battery', 0) or 0),
                len(sensors_summary.get('gateways', []) or []),
                len(sensors_summary.get('connectivity_modes', []) or []),
                float(readings.get('avg_temperature_c', 0) or 0),
                float(readings.get('avg_humidity_percent', 0) or 0),
                float(readings.get('avg_soil_conductivity', 0) or 0),
                float(readings.get('avg_battery_percent', 0) or 0),
                float(readings.get('avg_power_mw', 0) or 0),
                float(network.get('packet_loss_percent', 0) or 0),
                int(network.get('tx_packets', 0) or 0),
                int(network.get('rx_packets', 0) or 0),
                int(network.get('lost_packets', 0) or 0),
                float(network.get('avg_latency_ms', 0) or 0),
                float(network.get('avg_rssi_dbm', 0) or 0),
                float(network.get('network_utilization_percent', 0) or 0),
                float(network.get('delivery_success_percent', 100) or 100),
                len(alerts),
                json.dumps(snapshot, ensure_ascii=True),
            ))

            if sensors:
                sensor_rows = []
                for sensor in sensors:
                    if not isinstance(sensor, dict):
                        continue
                    sensor_rows.append((
                        timestamp,
                        dt_str,
                        sensor.get('sensor_id'),
                        sensor.get('type') or sensor.get('sensor_type'),
                        sensor.get('unit'),
                        sensor.get('value'),
                        sensor.get('status'),
                        sensor.get('domain'),
                        sensor.get('connectivity'),
                        sensor.get('gateway_id'),
                        sensor.get('battery_percent'),
                        sensor.get('latency_ms'),
                        sensor.get('packet_loss_percent'),
                        sensor.get('rssi_dbm'),
                        sensor.get('power_mw'),
                        sensor.get('tx_interval_s'),
                        int(sensor.get('packets_tx', 0) or 0),
                    ))
                if sensor_rows:
                    cursor.executemany("""
                        INSERT OR REPLACE INTO app2_sensor_readings
                        (timestamp, datetime, sensor_id, sensor_type, unit, value, status,
                         domain, connectivity, gateway_id, battery_percent, latency_ms,
                         packet_loss_percent, rssi_dbm, power_mw, tx_interval_s, packets_tx)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, sensor_rows)

            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar snapshot da App2: {e}")

    def get_latest_app2_snapshot(self):
        """
        Retorna o snapshot mais recente da App2 salvo no Data Lake.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT datetime, total_sensors, active_sensors, connected_sensors,
                   error_sensors, low_battery_sensors, gateways_count,
                   connectivity_modes_count, avg_temperature_c, avg_humidity_percent,
                   avg_soil_conductivity, avg_battery_percent, avg_power_mw,
                   packet_loss_percent, tx_packets, rx_packets, lost_packets,
                   avg_latency_ms, avg_rssi_dbm, network_utilization_percent,
                   delivery_success_percent, alerts_count, snapshot_json
            FROM app2_snapshots
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        if not row:
            return {}

        snapshot_json = row[22]
        if snapshot_json:
            try:
                return json.loads(snapshot_json)
            except json.JSONDecodeError:
                pass

        return {
            'timestamp': row[0],
            'sensors': {
                'total': row[1],
                'active': row[2],
                'connected': row[3],
                'error': row[4],
                'low_battery': row[5],
                'gateways_count': row[6],
                'connectivity_modes_count': row[7],
            },
            'readings': {
                'avg_temperature_c': row[8],
                'avg_humidity_percent': row[9],
                'avg_soil_conductivity': row[10],
                'avg_battery_percent': row[11],
                'avg_power_mw': row[12],
            },
            'network': {
                'packet_loss_percent': row[13],
                'tx_packets': row[14],
                'rx_packets': row[15],
                'lost_packets': row[16],
                'avg_latency_ms': row[17],
                'avg_rssi_dbm': row[18],
                'network_utilization_percent': row[19],
                'delivery_success_percent': row[20],
            },
            'alerts_count': row[21],
        }

    def get_recent_app2_snapshots(self, hours=24, limit=120):
        """
        Retorna histórico recente agregado da App2.
        """
        cursor = self.conn.cursor()
        base_query = """
            SELECT datetime, total_sensors, connected_sensors, error_sensors,
                   low_battery_sensors, avg_temperature_c, avg_humidity_percent,
                   avg_soil_conductivity, avg_battery_percent, avg_power_mw,
                   packet_loss_percent, avg_latency_ms, avg_rssi_dbm,
                   network_utilization_percent, delivery_success_percent, alerts_count
            FROM app2_snapshots
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
        """

        cutoff = int(time.time()) - (hours * 3600)
        cursor.execute(base_query, (cutoff, limit))
        rows = cursor.fetchall()

        if not rows:
            # Quando o banco contém apenas dados históricos/backfill, ancora a
            # janela no snapshot mais recente salvo para manter relatórios úteis.
            cursor.execute("SELECT MAX(timestamp) FROM app2_snapshots")
            latest_timestamp = cursor.fetchone()[0]
            if latest_timestamp is not None:
                fallback_cutoff = int(latest_timestamp) - (hours * 3600)
                cursor.execute(base_query, (fallback_cutoff, limit))
                rows = cursor.fetchall()

        results = []
        for row in rows:
            results.append({
                'datetime': row[0],
                'total_sensors': row[1],
                'connected_sensors': row[2],
                'error_sensors': row[3],
                'low_battery_sensors': row[4],
                'avg_temperature_c': row[5],
                'avg_humidity_percent': row[6],
                'avg_soil_conductivity': row[7],
                'avg_battery_percent': row[8],
                'avg_power_mw': row[9],
                'packet_loss_percent': row[10],
                'avg_latency_ms': row[11],
                'avg_rssi_dbm': row[12],
                'network_utilization_percent': row[13],
                'delivery_success_percent': row[14],
                'alerts_count': row[15],
            })
        return results

    def get_app2_report(self, hours=24):
        """
        Consolida um relatório operacional simples da App2.
        """
        history = self.get_recent_app2_snapshots(hours=hours, limit=max(1, hours * 24))
        if not history:
            return {
                'window_hours': hours,
                'samples': 0,
                'status': 'no_data',
            }

        def _avg(key):
            values = [float(item.get(key, 0) or 0) for item in history]
            return round(sum(values) / len(values), 3) if values else 0.0

        def _min(key):
            values = [float(item.get(key, 0) or 0) for item in history]
            return round(min(values), 3) if values else 0.0

        def _max(key):
            values = [float(item.get(key, 0) or 0) for item in history]
            return round(max(values), 3) if values else 0.0

        latest = history[0]
        critical_samples = sum(1 for item in history if float(item.get('packet_loss_percent', 0) or 0) >= 10.0)
        warning_samples = sum(1 for item in history if int(item.get('alerts_count', 0) or 0) > 0)

        return {
            'window_hours': hours,
            'samples': len(history),
            'status': 'ok',
            'latest': latest,
            'network': {
                'avg_packet_loss_percent': _avg('packet_loss_percent'),
                'max_packet_loss_percent': _max('packet_loss_percent'),
                'avg_latency_ms': _avg('avg_latency_ms'),
                'max_latency_ms': _max('avg_latency_ms'),
                'avg_delivery_success_percent': _avg('delivery_success_percent'),
                'min_delivery_success_percent': _min('delivery_success_percent'),
                'avg_rssi_dbm': _avg('avg_rssi_dbm'),
                'avg_network_utilization_percent': _avg('network_utilization_percent'),
            },
            'sensors': {
                'avg_connected_sensors': _avg('connected_sensors'),
                'avg_error_sensors': _avg('error_sensors'),
                'max_error_sensors': _max('error_sensors'),
                'avg_low_battery_sensors': _avg('low_battery_sensors'),
            },
            'environment': {
                'avg_temperature_c': _avg('avg_temperature_c'),
                'min_temperature_c': _min('avg_temperature_c'),
                'max_temperature_c': _max('avg_temperature_c'),
                'avg_humidity_percent': _avg('avg_humidity_percent'),
                'avg_soil_conductivity': _avg('avg_soil_conductivity'),
                'avg_battery_percent': _avg('avg_battery_percent'),
                'avg_power_mw': _avg('avg_power_mw'),
            },
            'events': {
                'warning_samples': warning_samples,
                'critical_packet_loss_samples': critical_samples,
            },
        }
    
    def get_energy_stats(self, hours=24):
        """
        Retorna estatísticas de economia de energia.
        
        Args:
            hours: Horas para análise
            
        Returns:
            Dict com estatísticas de energia.
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT 
                command,
                COUNT(*) as count,
                AVG(power_percent) as avg_power,
                MIN(datetime) as first,
                MAX(datetime) as last
            FROM energy_commands
            WHERE timestamp >= ?
            GROUP BY command
            ORDER BY count DESC
        """, (int(time.time()) - (hours * 3600),))
        
        results = {
            'total_commands': 0,
            'by_command': {},
            'avg_power': 0,
            'total_savings_percent': 0
        }
        
        command_counts = []
        total_power = 0
        
        for row in cursor.fetchall():
            cmd = row[0]
            count = row[1]
            avg_power = row[2] or 100
            
            results['by_command'][cmd] = {
                'count': count,
                'avg_power_percent': avg_power,
                'first': row[3],
                'last': row[4]
            }
            
            results['total_commands'] += count
            command_counts.append((count, avg_power))
            total_power += avg_power * count
        
        if command_counts:
            results['avg_power'] = total_power / results['total_commands']
            baseline_power = 100
            results['total_savings_percent'] = baseline_power - results['avg_power']

        # Calibrated RU/mmWave estimate for the dashboard. The estimate is
        # explicitly marked as a model and never presented as a wattmeter.
        try:
            energy_rows = cursor.execute(
                "SELECT * FROM energy_commands WHERE timestamp >= ? ORDER BY timestamp, id",
                (int(time.time()) - (hours * 3600),),
            ).fetchall()
            calibration = load_calibration()
            end_ns = time.time_ns()
            energy = integrate_energy_events(energy_rows, calibration, end_timestamp_ns=end_ns)
            results['calibrated_energy'] = {
                **energy,
                'kind': 'calibrated_ru_mmwave_power_model',
                'physical_meter_available': False,
            }
            results['energy_j'] = energy.get('energy_j', 0.0)
            results['average_power_w'] = energy.get('average_power_w', 0.0)
            results['energy_duration_s'] = energy.get('duration_s', 0.0)
            results['calibration_version'] = calibration.get('calibration_version', '')
        except (sqlite3.Error, ValueError) as exc:
            results['calibrated_energy'] = {
                'valid': False,
                'kind': 'calibrated_ru_mmwave_power_model',
                'physical_meter_available': False,
                'reason': str(exc),
            }

        return results
    
    def record_extended_from_json(self, extended_json, energy_state=None, slicer_state=None):
        """
        Registra métricas estendidas diretamente de JSON.
        
        Args:
            extended_json: Dict com métricas do csv_to_metrics.py
            energy_state: Estado do Energy Saver
            slicer_state: Estado do SLICER
        """
        if not extended_json:
            return
        
        timestamp = int(time.time())
        gm = extended_json.get('global_metrics', {})
        sim_range = extended_json.get('sim_time_range', {})
        
        # Extrair métricas POR UE
        latency_p95_per_ue = gm.get('latency_p95_per_ue_us', 0)
        variance_per_ue = gm.get('variance_per_ue_us2', 0)
        cvar_per_ue = gm.get('cvar_per_ue_us', 0)
        ue_count = gm.get('ue_count', 0)
        
        metric_id = self.record_extended_metric(
            timestamp=timestamp,
            sim_time_s=sim_range.get('end', 0),
            cell_id=0,
            global_worst_latency=gm.get('global_worst_latency_us', 0),
            global_avg_latency=gm.get('global_avg_latency_us', 0),
            global_min_latency=gm.get('global_min_latency_us', 0),
            global_max_latency=gm.get('global_max_latency_us', 0),
            global_jitter=gm.get('global_jitter_us', 0),
            packet_loss=gm.get('global_packet_loss_rate', 0),
            total_active_ues=gm.get('total_active_ues', 0),
            total_active_cameras=gm.get('total_active_cameras', 0),
            total_critical=gm.get('total_critical_ues', 0),
            total_tx_bytes=gm.get('total_tx_bytes', 0),
            total_rx_bytes=gm.get('total_rx_bytes', 0),
            total_tx_pdus=gm.get('total_tx_pdus', 0),
            total_rx_pdus=gm.get('total_rx_pdus', 0),
            throughput_kbps=gm.get('throughput_kbps', 0),
            energy_state=energy_state,
            slicer_state=slicer_state,
            latency_p5_us=gm.get('latency_p5_us', 0),
            latency_p95_us=gm.get('latency_p95_us', 0),
            latency_min_nonzero_us=gm.get('latency_min_nonzero_us', 0),
            valid_samples=gm.get('valid_samples', 0),
            extended_metrics={
                'latency_p95_per_ue_us': latency_p95_per_ue,
                'variance_per_ue_us2': variance_per_ue,
                'cvar_per_ue_us': cvar_per_ue,
                'ue_count': ue_count,
                'collector_mode': gm.get('collector_mode', ''),
                'throughput_source': gm.get('throughput_source', ''),
                'real_latency_sample_count': gm.get('real_latency_sample_count', 0),
                'proxy_latency_sample_count': gm.get('proxy_latency_sample_count', 0),
                'pdcp_stale': gm.get('pdcp_stale', False),
                'rlc_stale': gm.get('rlc_stale', False),
                'mac_stale': gm.get('mac_stale', False),
                'pdcp_trace_age_s': gm.get('pdcp_trace_age_s', 0),
                'rlc_trace_age_s': gm.get('rlc_trace_age_s', 0),
                'mac_trace_age_s': gm.get('mac_trace_age_s', 0),
                'pdcp_latest_sim_time_s': gm.get('pdcp_latest_sim_time_s', 0),
            },
            decision_id=None,
        )
        
        ue_list = []
        for imsi, ue_data in extended_json.get('ue_metrics', {}).items():
            try:
                ue_list.append({
                    'imsi': int(imsi),
                    'device_type': ue_data.get('device_type'),
                    'cell_id': ue_data.get('cell_id'),
                    'latency_us': ue_data.get('latency_us', 0),
                    'latency_avg_us': ue_data.get('latency_avg_us', 0),
                    'latency_min_us': ue_data.get('latency_min_us', 0),
                    'latency_max_us': ue_data.get('latency_max_us', 0),
                    'latency_p95_us': ue_data.get('latency_p95_us', ue_data.get('latency_us', 0)),
                    'jitter_us': ue_data.get('jitter_us', 0),
                    'pdu_size_avg': ue_data.get('pdu_size_avg', 0),
                    'tx_bytes': ue_data.get('tx_bytes', 0),
                    'rx_bytes': ue_data.get('rx_bytes', 0),
                    'tx_pdus': ue_data.get('tx_pdus', 0),
                    'rx_pdus': ue_data.get('rx_pdus', 0),
                    'throughput_kbps': ue_data.get('throughput_kbps', 0),
                    'packet_count': ue_data.get('packet_count', 0),
                    'mcs_avg': ue_data.get('mcs_avg', 0),
                    'tb_size_avg': ue_data.get('tb_size_avg', 0),
                    'is_critical': ue_data.get('is_critical', False),
                    'packet_loss_percent': ue_data.get('packet_loss_percent'),
                    'has_latency_samples': ue_data.get('has_latency_samples', False),
                    'latency_is_proxy': ue_data.get('latency_is_proxy', False),
                    'pdcp_provenance': ue_data.get('pdcp_provenance', ''),
                    'offered_load_kbps': ue_data.get('tx_throughput_kbps', 0),
                    'backlog_bytes': ue_data.get('backlog_bytes', 0),
                    'sim_time_s': sim_range.get('end', 0),
                })
            except (ValueError, TypeError):
                continue
        
        if ue_list:
            self.record_ue_metrics(timestamp, ue_list, sim_time_s=sim_range.get('end', 0))
        return metric_id
    
    def get_hourly_stats(self, hours_back=24):
        """
        Retorna estatísticas agregadas por hora.
        
        Returns:
            List of dicts com campos:
                - hour: hora do dia (0-23)
                - day_of_week: dia da semana (0=segunda, 6=domingo)
                - avg_latency: latência média
                - avg_cameras: câmeras médias
                - sample_count: número de amostras
                - energy_blocked_count: vezes que energia foi bloqueada
        """
        cursor = self.conn.cursor()
        
        # Calcula estatísticas por hora e dia da semana
        cursor.execute("""
            SELECT 
                CAST(strftime('%H', datetime) AS INTEGER) as hour,
                CAST(strftime('%w', datetime) AS INTEGER) as day_of_week,
                AVG(latency_us) as avg_latency,
                MIN(latency_us) as min_latency,
                MAX(latency_us) as max_latency,
                AVG(cameras_active) as avg_cameras,
                COUNT(*) as sample_count
            FROM metrics_history
            WHERE datetime >= datetime('now', '-' || ? || ' hours')
            GROUP BY hour, day_of_week
            ORDER BY day_of_week, hour
        """, (hours_back,))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'hour': row[0],
                'day_of_week': row[1],
                'avg_latency': row[2] or 0,
                'min_latency': row[3] or 0,
                'max_latency': row[4] or 0,
                'avg_cameras': row[5] or 0,
                'sample_count': row[6]
            })
        
        return results
    
    def get_daily_stats(self, days_back=7):
        """
        Retorna estatísticas agregadas por dia.
        
        Returns:
            List of dicts com estatísticas diárias.
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT 
                date(datetime) as date,
                CAST(strftime('%w', datetime) AS INTEGER) as day_of_week,
                AVG(latency_us) as avg_latency,
                MIN(latency_us) as min_latency,
                MAX(latency_us) as max_latency,
                AVG(cameras_active) as avg_cameras,
                COUNT(*) as sample_count
            FROM metrics_history
            WHERE datetime >= date('now', '-' || ? || ' days')
            GROUP BY date(datetime)
            ORDER BY date
        """, (days_back,))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'date': row[0],
                'day_of_week': row[1],
                'avg_latency': row[2] or 0,
                'min_latency': row[3] or 0,
                'max_latency': row[4] or 0,
                'avg_cameras': row[5] or 0,
                'sample_count': row[6]
            })
        
        return results
    
    def get_pattern_data(self, hour=None, day_of_week=None):
        """
        Retorna dados para análise de padrões.
        
        Args:
            hour: Filtrar por hora específica
            day_of_week: Filtrar por dia da semana (0-6)
        
        Returns:
            Dict com estatísticas do padrão solicitado.
        """
        cursor = self.conn.cursor()
        
        query = """
            SELECT 
                CAST(strftime('%H', datetime) AS INTEGER) as hour,
                CAST(strftime('%w', datetime) AS INTEGER) as day_of_week,
                AVG(latency_us) as avg_latency,
                AVG(cameras_active) as avg_cameras,
                COUNT(*) as sample_count,
                SUM(CASE WHEN cameras_active = 0 THEN 1 ELSE 0 END) as camera_off_count
            FROM metrics_history
            WHERE 1=1
        """
        params = []
        
        if hour is not None:
            query += " AND CAST(strftime('%H', datetime) AS INTEGER) = ?"
            params.append(hour)
        
        if day_of_week is not None:
            query += " AND CAST(strftime('%w', datetime) AS INTEGER) = ?"
            params.append(day_of_week)
        
        query += " GROUP BY hour, day_of_week"
        
        cursor.execute(query, params)
        row = cursor.fetchone()
        
        if row:
            return {
                'hour': row[0],
                'day_of_week': row[1],
                'avg_latency': row[2] or 0,
                'avg_cameras': row[3] or 0,
                'sample_count': row[4],
                'camera_off_count': row[5],
                'camera_off_ratio': row[5] / row[4] if row[4] > 0 else 0
            }
        
        return None
    
    def get_recent_metrics(self, minutes=30):
        """
        Retorna métricas dos últimos N minutos.
        
        Args:
            minutes: Número de minutos para buscar
        
        Returns:
            List de métricas recentes.
        """
        cursor = self.conn.cursor()
        
        # Usar timestamp para comparação (mais preciso)
        import time
        cutoff_time = int(time.time()) - (minutes * 60)
        
        # Usar extended_metrics (tem dados mais recentes)
        cursor.execute("""
            SELECT timestamp, global_avg_latency_us, total_active_cameras, 
                   total_critical_ues, energy_state, slicer_state
            FROM extended_metrics
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
        """, (cutoff_time,))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'timestamp': row[0],
                'latency_us': row[1],
                'cameras_active': row[2],
                'critical_cameras': row[3],
                'energy_state': row[4],
                'slicer_state': row[5]
            })
        
        return results
    
    def get_decision_stats(self, hours=24):
        """
        Retorna estatísticas de decisões.
        
        Args:
            hours: Horas para análise
        
        Returns:
            Dict com contagens de decisões.
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT 
                decision,
                reason,
                COUNT(*) as count
            FROM decisions_history
            WHERE datetime >= datetime('now', '-' || ? || ' hours')
            GROUP BY decision, reason
        """, (hours,))
        
        results = {
            'total': 0,
            'blocked': 0,
            'allowed': 0,
            'conditional': 0,
            'by_reason': {}
        }
        
        for row in cursor.fetchall():
            decision = row[0]
            reason = row[1]
            count = row[2]
            
            results['total'] += count
            if decision == 'BLOCKED':
                results['blocked'] += count
            elif decision == 'ALLOWED':
                results['allowed'] += count
            elif decision == 'CONDITIONAL':
                results['conditional'] += count
            
            results['by_reason'][reason] = count
        
        return results

    def get_recent_conflicts(self, minutes=60, limit=50):
        """Retorna eventos recentes de conflito O-RAN."""
        cursor = self.conn.cursor()
        cutoff = int(time.time()) - minutes * 60

        cursor.execute("""
            SELECT timestamp, datetime, source_agent, target_agent, conflict_type,
                   parameter, affected_service, affected_kpi, observed_value,
                   threshold_value, decision, mitigation_action, reason,
                   confidence, graph_path
            FROM conflict_events
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (cutoff, limit))

        return [dict(row) for row in cursor.fetchall()]

    def get_conflict_stats(self, hours=24):
        """Retorna estatísticas agregadas dos conflitos O-RAN."""
        cursor = self.conn.cursor()
        cutoff = int(time.time()) - hours * 3600

        stats = {
            'total': 0,
            'direct': 0,
            'indirect': 0,
            'implicit': 0,
            'by_service': {},
            'by_kpi': {},
            'latest': None,
        }

        cursor.execute("""
            SELECT conflict_type, affected_service, affected_kpi, COUNT(*) as count
            FROM conflict_events
            WHERE timestamp >= ?
            GROUP BY conflict_type, affected_service, affected_kpi
        """, (cutoff,))

        for row in cursor.fetchall():
            conflict_type = row['conflict_type'] or 'unknown'
            service = row['affected_service'] or 'unknown'
            kpi = row['affected_kpi'] or 'unknown'
            count = int(row['count'] or 0)

            stats['total'] += count
            if conflict_type in stats:
                stats[conflict_type] += count
            stats['by_service'][service] = stats['by_service'].get(service, 0) + count
            stats['by_kpi'][kpi] = stats['by_kpi'].get(kpi, 0) + count

        cursor.execute("""
            SELECT timestamp, datetime, source_agent, target_agent, conflict_type,
                   affected_service, affected_kpi, observed_value, threshold_value,
                   mitigation_action, reason
            FROM conflict_events
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            stats['latest'] = dict(row)

        return stats
    
    def calculate_moving_average(self, metric='latency', window_minutes=30):
        """
        Calcula média móvel de uma métrica.
        
        Args:
            metric: 'latency' ou 'cameras'
            window_minutes: Janela de tempo
        
        Returns:
            Float com média móvel.
        """
        cursor = self.conn.cursor()
        
        if metric == 'latency':
            column = 'latency_us'
        else:
            column = 'cameras_active'
        
        cursor.execute(f"""
            SELECT AVG({column}) 
            FROM metrics_history
            WHERE datetime >= datetime('now', '-' || ? || ' minutes')
        """, (window_minutes,))
        
        row = cursor.fetchone()
        return row[0] if row and row[0] else 0

    def calculate_cvar(self, alpha=0.95, window_minutes=5):
        """
        Retorna o CVaR mais recente já calculado pelo coletor.
        
        Args:
            alpha: Mantido por compatibilidade
            window_minutes: Janela de tempo em minutos para filtrar dados
        
        Returns:
            Float com CVaR em microsegundos, ou None se não houver dados.
        """
        cursor = self.conn.cursor()
        
        cutoff = int(time.time()) - (window_minutes * 60)
        
        cursor.execute("""
            SELECT cvar_per_ue_us
            FROM extended_metrics
            WHERE cvar_per_ue_us < 500000
            AND cvar_per_ue_us > 0
            AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (cutoff,))

        row = cursor.fetchone()
        if row and row[0]:
            return row[0]

        cursor.execute("""
            SELECT latency_p95_per_ue_us
            FROM extended_metrics
            WHERE latency_p95_per_ue_us < 500000
            AND latency_p95_per_ue_us > 0
            AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (cutoff,))

        row = cursor.fetchone()
        if row and row[0]:
            return row[0]

        cursor.execute("""
            SELECT latency_p95_us
            FROM extended_metrics
            WHERE latency_p95_us < 500000
            AND latency_p95_us > 0
            AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (cutoff,))

        row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def calculate_variance(self, window_minutes=5):
        """
        Calcula a variância das latências na janela.
        
        USA variance_per_ue_us2 (Variância POR UE) em vez de latency_p95_us (agregado)
        para capturar corretamente a instabilidade da rede.
        
        Variância alta = rede instável (UEs com latências muito diferentes).
        Variância baixa = rede estável (UEs com latências similares).
        
        Args:
            window_minutes: Janela de tempo em minutos (não usado - usa últimos registros)
        
        Returns:
            Float com variância (µs²), ou None se não houver dados.
        """
        cursor = self.conn.cursor()
        
        # Usar variância POR UE (não agregado)
        cursor.execute("""
            SELECT variance_per_ue_us2
            FROM extended_metrics
            WHERE variance_per_ue_us2 < 50000000000000
            AND variance_per_ue_us2 > 0
            ORDER BY timestamp DESC
            LIMIT 100
        """)
        
        variances = [row[0] for row in cursor.fetchall() if row[0] > 0]
        
        if not variances:
            # Fallback para métricas agregadas se per-UE não disponível
            cursor.execute("""
                SELECT latency_p95_us
                FROM extended_metrics
                WHERE latency_p95_us < 500000
                AND latency_p95_us > 0
                ORDER BY timestamp DESC
            LIMIT 100
        """)
        
        variances = [row[0] for row in cursor.fetchall() if row[0] > 0]
        
        if not variances:
            # Fallback para métricas agregadas se per-UE não disponível
            cursor.execute("""
                SELECT latency_p95_us
                FROM extended_metrics
                WHERE latency_p95_us < 500000
                AND latency_p95_us > 0
                ORDER BY timestamp DESC
                LIMIT 100
            """)
            
            latencies = [row[0] for row in cursor.fetchall() if row[0] > 0]
            
            if len(latencies) < 2:
                return None
            
            mean = sum(latencies) / len(latencies)
            variance = sum((x - mean) ** 2 for x in latencies) / len(latencies)
            return variance
        
        # Retornar média das variâncias por UE
        if len(variances) < 1:
            return None
        
        mean_variance = sum(variances) / len(variances)
        return mean_variance

    def get_network_health(self, window_minutes=5):
        """
        Retorna métricas de saúde da rede para o rApp.
        
        USA métricas POR UE (cvar_per_ue_us) em vez de métricas agregadas
        para capturar corretamente os UEs críticos.
        
        Args:
            window_minutes: Janela de tempo em minutos para filtrar dados
        
        Returns:
            Dict com: median, p95, cvar, variance, stability_score
        """
        cursor = self.conn.cursor()
        
        cutoff = int(time.time()) - (window_minutes * 60)
        
        cursor.execute("""
            SELECT cvar_per_ue_us, variance_per_ue_us2, latency_p95_per_ue_us, ue_count
            FROM extended_metrics
            WHERE cvar_per_ue_us > 0
            AND timestamp >= ?
            ORDER BY timestamp DESC
        """, (cutoff,))
        
        rows = cursor.fetchall()
        cvar_values = []
        variance_values = []
        p95_values = []
        ue_counts = []
        
        for row in rows:
            if row[0] and row[0] > 0:
                cvar_values.append(row[0])
            if row[1] and row[1] > 0:
                variance_values.append(row[1])
            if row[2] and row[2] > 0:
                p95_values.append(row[2])
            if len(row) > 3 and row[3]:
                ue_counts.append(row[3])
        
        if not cvar_values:
            # Fallback para métricas agregadas se per-UE não disponível
            cursor.execute("""
                SELECT latency_p95_us
                FROM extended_metrics
                WHERE latency_p95_us < 500000
                AND latency_p95_us > 0
                AND timestamp >= ?
                ORDER BY timestamp DESC
            """, (cutoff,))
            cvar_values = [row[0] for row in cursor.fetchall() if row[0] > 0]
            
            if not cvar_values:
                return None
        
        n = len(cvar_values)
        latest_cvar = cvar_values[0] if cvar_values else 0
        latest_p95 = p95_values[0] if p95_values else 0
        latest_variance = variance_values[0] if variance_values else 0
        latest_ue_count = ue_counts[0] if ue_counts else 0

        cvar_sorted = sorted(cvar_values)
        median = cvar_sorted[n // 2] if n > 0 else 0
        p95 = latest_p95 if latest_p95 else (sorted(p95_values)[min(int(len(p95_values) * 0.95), len(p95_values) - 1)] if p95_values else 0)
        cvar = latest_cvar if latest_cvar else cvar_sorted[-1]
        variance = latest_variance if latest_variance else (sum(variance_values) / len(variance_values) if variance_values else 0)

        mean = sum(cvar_values) / n if n > 0 else 0
        cv = (variance ** 0.5) / mean if mean > 0 else 0
        stability_score = max(0, min(100, 100 - (cv * 100)))
        
        return {
            'median_us': median,
            'p95_us': p95,
            'cvar_us': cvar,
            'variance_us2': variance,
            'variance_us': variance ** 0.5,
            'stability_score': stability_score,
            'sample_count': n,
            'ue_count': latest_ue_count,
            'latest_cvar_us': latest_cvar,
            'window_median_cvar_us': median,
            'window_minutes': window_minutes
        }

    def export_to_json(self, filepath="/tmp/rapp_data_export.json"):
        """
        Exporta todos os dados para JSON.
        
        Args:
            filepath: Caminho do arquivo de saída
        """
        cursor = self.conn.cursor()
        
        # Métricas
        cursor.execute("SELECT * FROM metrics_history ORDER BY timestamp")
        metrics = [dict(row) for row in cursor.fetchall()]
        
        # Decisões
        cursor.execute("SELECT * FROM decisions_history ORDER BY timestamp")
        decisions = [dict(row) for row in cursor.fetchall()]
        
        data = {
            'export_time': datetime.now().isoformat(),
            'metrics_count': len(metrics),
            'decisions_count': len(decisions),
            'metrics': metrics,
            'decisions': decisions
        }
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        
        print(f"[DataLake] Dados exportados para {filepath}")
        return filepath
    
    def clear_old_data(self, days_to_keep=30):
        """
        Remove dados mais antigos que N dias.
        
        Args:
            days_to_keep: Número de dias para manter
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            DELETE FROM metrics_history 
            WHERE datetime < datetime('now', '-' || ? || ' days')
        """, (days_to_keep,))
        
        cursor.execute("""
            DELETE FROM decisions_history 
            WHERE datetime < datetime('now', '-' || ? || ' days')
        """, (days_to_keep,))

        cursor.execute("""
            DELETE FROM app2_snapshots
            WHERE datetime < datetime('now', '-' || ? || ' days')
        """, (days_to_keep,))

        cursor.execute("""
            DELETE FROM app2_sensor_readings
            WHERE datetime < datetime('now', '-' || ? || ' days')
        """, (days_to_keep,))
        
        self.conn.commit()
        print(f"[DataLake] Dados antigos removidos (mantidos últimos {days_to_keep} dias)")
    
    def get_database_stats(self):
        """
        Retorna estatísticas do banco de dados.

        Returns:
            Dict com estatísticas.
        """
        cursor = self.conn.cursor()
        
        # Contagem de registros
        cursor.execute("SELECT COUNT(*) FROM metrics_history")
        metrics_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM decisions_history")
        decisions_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM app2_snapshots")
        app2_snapshots_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM app2_sensor_readings")
        app2_sensor_readings_count = cursor.fetchone()[0]
        
        # Primeiro e último registro
        cursor.execute("SELECT MIN(datetime), MAX(datetime) FROM metrics_history")
        row = cursor.fetchone()
        
        return {
            'metrics_count': metrics_count,
            'decisions_count': decisions_count,
            'app2_snapshots_count': app2_snapshots_count,
            'app2_sensor_readings_count': app2_sensor_readings_count,
            'first_record': row[0],
            'last_record': row[1],
            'db_size_bytes': os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        }
    
    def get_latest_extended_metrics(self, limit=1):
        """
        Retorna as últimas métricas estendidas (para novas features ML).
        
        Args:
            limit: Número de registros a retornar
            
        Returns:
            List de dicts com métricas
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id, timestamp, datetime, throughput_kbps, global_packet_loss_rate,
                   global_jitter_us, total_tx_bytes, total_rx_bytes,
                   cvar_per_ue_us, variance_per_ue_us2, sim_time_s, decision_id,
                   collector_mode, throughput_source, pdcp_stale,
                   proxy_latency_sample_count, real_latency_sample_count
            FROM extended_metrics
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'metric_snapshot_id': row[0],
                'id': row[0],
                'timestamp': row[1],
                'datetime': row[2],
                'throughput_kbps': row[3],
                'global_packet_loss_rate': row[4],
                'global_jitter_us': row[5],
                'total_tx_bytes': row[6],
                'total_rx_bytes': row[7],
                'cvar_per_ue_us': row[8],
                'variance_per_ue_us2': row[9],
                'sim_time_s': row[10],
                'decision_id': row[11],
                'collector_mode': row[12],
                'throughput_source': row[13],
                'pdcp_stale': row[14],
                'proxy_latency_sample_count': row[15],
                'real_latency_sample_count': row[16],
            })
        return results
    
    def get_recent_decisions(self, minutes=5, limit=10):
        """
        Retorna decisões recentes (para energy_history).
        
        Args:
            minutes: Minutos para buscar
            limit: Número máximo de registros
            
        Returns:
            List de dicts com decisões
        """
        import time
        cursor = self.conn.cursor()
        cutoff_time = int(time.time()) - (minutes * 60)
        
        cursor.execute("""
            SELECT datetime, decision, energy_state, confidence
            FROM decisions_history
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (cutoff_time, limit))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'datetime': row[0],
                'decision': row[1],
                'energy_state': row[2],
                'confidence': row[3],
            })
        return results
    
    def calculate_slope(self, window_minutes=5):
        """
        Calcula slope da latência usando regressão linear simples.
        
        Retorna a "velocidade" de mudança da latência em µs/segundo.
        Positivo = latência subindo, Negativo = latência descendo.
        
        Args:
            window_minutes: Janela de tempo para análise
        
        Returns:
            dict com slope, intercept, r_squared, trend, confidence
        """
        metrics = self.get_recent_metrics(window_minutes)
        
        if not metrics or len(metrics) < 3:
            return {
                'slope_us_per_sec': 0,
                'slope_ms_per_sec': 0,
                'trend': 'unknown',
                'confidence': 0.0,
                'valid': False,
                'n_samples': len(metrics) if metrics else 0
            }
        
        # Preparar dados
        base_time = metrics[-1]['timestamp']
        x_values = []
        y_values = []
        
        for m in reversed(metrics):
            if m['latency_us'] > 0:
                x_values.append(m['timestamp'] - base_time)
                y_values.append(m['latency_us'])
        
        if len(x_values) < 3:
            return {
                'slope_us_per_sec': 0,
                'slope_ms_per_sec': 0,
                'trend': 'unknown',
                'confidence': 0.0,
                'valid': False,
                'n_samples': len(x_values)
            }
        
        # Regressão linear
        n = len(x_values)
        x_mean = sum(x_values) / n
        y_mean = sum(y_values) / n
        
        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
        denominator = sum((x - x_mean) ** 2 for x in x_values)
        
        if denominator == 0:
            return {
                'slope_us_per_sec': 0,
                'slope_ms_per_sec': 0,
                'trend': 'stable',
                'confidence': 0.0,
                'valid': False,
                'n_samples': n
            }
        
        slope = numerator / denominator
        intercept = y_mean - slope * x_mean
        
        # R-squared
        y_pred = [intercept + slope * x for x in x_values]
        ss_res = sum((y - yp) ** 2 for y, yp in zip(y_values, y_pred))
        ss_tot = sum((y - y_mean) ** 2 for y in y_values)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
        
        # Classificar tendência
        slope_ms = slope / 1000.0
        if slope_ms > 5:
            trend = 'rising_fast'
        elif slope_ms > 2:
            trend = 'rising_slow'
        elif slope_ms > 0.5:
            trend = 'stable'
        elif slope_ms > -2:
            trend = 'falling_slow'
        else:
            trend = 'falling_fast'
        
        # Confiança
        r2_weight = min(1.0, r_squared * 2)
        sample_weight = min(1.0, n / 20.0)
        confidence = (r2_weight * 0.6) + (sample_weight * 0.4)
        
        return {
            'slope_us_per_sec': slope,
            'slope_ms_per_sec': slope_ms,
            'intercept_us': intercept,
            'trend': trend,
            'confidence': round(confidence, 3),
            'r_squared': round(max(0, r_squared), 3),
            'valid': True,
            'n_samples': n
        }

    def cleanup_old_data(self, days=7):
        """
        Remove dados mais antigos que N dias.

        Args:
            days: Número de dias para manter (default: 7)
        """
        cutoff = int(time.time()) - (days * 86400)
        cursor = self.conn.cursor()

        try:
            # Clean extended_metrics
            cursor.execute("DELETE FROM extended_metrics WHERE timestamp < ?", (cutoff,))
            metrics_deleted = cursor.rowcount

            # Clean decisions_history
            cursor.execute("DELETE FROM decisions_history WHERE timestamp < ?", (cutoff,))
            decisions_deleted = cursor.rowcount

            # Clean ue_metrics
            cursor.execute("DELETE FROM ue_metrics WHERE timestamp < ?", (cutoff,))
            ue_deleted = cursor.rowcount

            # Clean energy_commands
            cursor.execute("DELETE FROM energy_commands WHERE timestamp < ?", (cutoff,))
            energy_deleted = cursor.rowcount

            # Clean app2 snapshots
            cursor.execute("DELETE FROM app2_snapshots WHERE timestamp < ?", (cutoff,))
            app2_snapshots_deleted = cursor.rowcount

            # Clean app2 sensor readings
            cursor.execute("DELETE FROM app2_sensor_readings WHERE timestamp < ?", (cutoff,))
            app2_sensor_readings_deleted = cursor.rowcount

            self.conn.commit()

            # Vacuum to reclaim space
            self.conn.execute("VACUUM")

            total = (
                metrics_deleted
                + decisions_deleted
                + ue_deleted
                + energy_deleted
                + app2_snapshots_deleted
                + app2_sensor_readings_deleted
            )
            if total > 0:
                print(f"[DataLake] Limpeza: {total} registros antigos removidos (> {days} dias)")

        except Exception as e:
            print(f"[DataLake] Erro na limpeza: {e}")

    def close(self):
        """Fecha conexão com o banco"""
        if self.conn:
            self.conn.close()
            print("[DataLake] Conexão fechada")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def main():
    """Teste do Data Lake"""
    print("=" * 60)
    print("rApp Data Lake - Teste")
    print("=" * 60)
    
    # Cria Data Lake
    dl = DataLake()
    
    # Registra métricas de teste
    print("\n[1] Registrando métricas de teste...")
    for i in range(10):
        timestamp = int(time.time()) - (i * 300)  # 5 minutos atrás
        latency = 50000 + (i * 10000)  # 50-140ms
        cameras = 3 - (i % 4)  # 0-3 câmeras
        dl.record_metric(timestamp, latency, cameras, 0 if cameras == 0 else 1)
    
    # Registra decisões de teste
    print("[2] Registrando decisões de teste...")
    for i in range(5):
        timestamp = int(time.time()) - (i * 600)
        dl.record_decision({
            'energy_saver': 'BLOCKED' if i % 2 == 0 else 'ALLOWED',
            'reason': 'SLA_VIOLATED' if i % 2 == 0 else 'LOW_ACTIVITY',
            'confidence': 0.9 if i % 2 == 0 else 0.7,
            'pattern': 'night_low_activity' if i % 2 == 1 else None,
            'agent_override': False
        }, timestamp)
    
    # Estatísticas
    print("\n[3] Estatísticas do banco:")
    stats = dl.get_database_stats()
    for key, value in stats.items():
        print(f"    {key}: {value}")
    
    # Média móvel
    print("\n[4] Média móvel (últimos 60 min):")
    ma_latency = dl.calculate_moving_average('latency', 60)
    ma_cameras = dl.calculate_moving_average('cameras', 60)
    print(f"    Latência: {ma_latency:.0f} us")
    print(f"    Câmeras: {ma_cameras:.1f}")
    
    # Estatísticas de decisões
    print("\n[5] Estatísticas de decisões (24h):")
    dec_stats = dl.get_decision_stats(24)
    for key, value in dec_stats.items():
        print(f"    {key}: {value}")
    
    dl.close()
    print("\n" + "=" * 60)
    print("Teste concluído!")
    print("=" * 60)


if __name__ == "__main__":
    main()
