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
import sqlite3
import time
import json

try:
    from .greenran_marl_topology import build_du_state_snapshot_from_resource_snapshot
except ImportError:
    from greenran_marl_topology import build_du_state_snapshot_from_resource_snapshot
from datetime import datetime, timedelta
from pathlib import Path

from greenran_paths import RAPP_DB_PATH

DEFAULT_DB_PATH = str(RAPP_DB_PATH)


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
                decision TEXT NOT NULL,
                reason TEXT,
                confidence REAL,
                pattern TEXT,
                agent_override INTEGER DEFAULT 0,
                energy_state TEXT,
                slicer_state TEXT,
                ml_decision TEXT,
                ml_confidence REAL,
                ml_predicted_cvar_ms REAL,
                ml_influenced INTEGER DEFAULT 0,
                armd_enabled INTEGER DEFAULT 0,
                armd_mode TEXT,
                armd_scenario TEXT,
                armd_source TEXT,
                armd_confidence REAL DEFAULT 0,
                armd_override_applied INTEGER DEFAULT 0,
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
                UNIQUE(timestamp)
            )
        """)
        existing_decision_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(decisions_history)").fetchall()
        }
        for column_name, column_def in (
            ("armd_enabled", "INTEGER DEFAULT 0"),
            ("armd_mode", "TEXT"),
            ("armd_scenario", "TEXT"),
            ("armd_source", "TEXT"),
            ("armd_confidence", "REAL DEFAULT 0"),
            ("armd_override_applied", "INTEGER DEFAULT 0"),
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
                UNIQUE(timestamp)
            )
        """)
        
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
                reason TEXT
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_energy_timestamp 
            ON energy_commands(timestamp)
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

        decision_str = decision.get('energy_saver', 'UNKNOWN')
        reason = decision.get('reason', '')
        confidence = decision.get('confidence', 0.0)
        pattern = decision.get('pattern', '')
        agent_override = 1 if decision.get('agent_override', False) else 0
        energy_state = decision.get('energy_state', '')
        slicer_state = decision.get('slicer_state', '')

        # ML prediction data
        ml_prediction = decision.get('ml_rf_prediction', {})
        ml_decision = ml_prediction.get('decision', '')
        ml_confidence = ml_prediction.get('confidence', 0.0)
        ml_predicted_cvar = ml_prediction.get('predicted_cvar_ms', 0.0)
        ml_influenced = 1 if decision.get('ml_influenced', False) else 0
        armd_enabled = 1 if decision.get('armd_enabled', False) else 0
        armd_mode = decision.get('armd_mode', '')
        armd_scenario = decision.get('armd_scenario', '')
        armd_source = decision.get('armd_source', '')
        armd_confidence = decision.get('armd_confidence', 0.0)
        armd_override_applied = 1 if decision.get('armd_override_applied', False) else 0
        rl_policy_runtime = decision.get('rl_policy_runtime', {}) or {}
        resource_allocation = decision.get('resource_allocation', {}) or {}
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
            cursor.execute("""
                INSERT OR REPLACE INTO decisions_history
                (timestamp, datetime, decision, reason, confidence, pattern,
                 agent_override, energy_state, slicer_state,
                 ml_decision, ml_confidence, ml_predicted_cvar_ms, ml_influenced,
                 armd_enabled, armd_mode, armd_scenario, armd_source, armd_confidence, armd_override_applied,
                 rl_policy_id, rl_policy_family, rl_policy_algorithm, resource_controller_id,
                 resource_budget, usable_budget, ran_demand, ai_demand, ran_allocation, ai_allocation,
                 ran_completion_ratio, ai_completion_ratio, utilization_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, dt_str, decision_str, reason, confidence, pattern,
                  agent_override, energy_state, slicer_state,
                  ml_decision, ml_confidence, ml_predicted_cvar, ml_influenced,
                  armd_enabled, armd_mode, armd_scenario, armd_source, armd_confidence, armd_override_applied,
                  rl_policy_id, rl_policy_family, rl_policy_algorithm, resource_controller_id,
                  resource_budget, usable_budget, ran_demand, ai_demand, ran_allocation, ai_allocation,
                  ran_completion_ratio, ai_completion_ratio, utilization_ratio))
            self.conn.commit()
            if resource_allocation:
                self.record_resource_allocation_snapshot(resource_allocation, timestamp=timestamp)
            self.record_conflict_from_decision(decision, timestamp=timestamp)
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar decisão: {e}")

    def record_resource_allocation_snapshot(self, snapshot, timestamp=None):
        """Persist the CAORA-style resource-allocation snapshot for SAC training."""
        if not isinstance(snapshot, dict) or not snapshot:
            return

        if timestamp is None:
            timestamp = int(time.time())

        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO resource_allocation_history
                (timestamp, datetime, controller_id, target_policy_id, decision_domain,
                 action_semantics, resource_budget, usable_budget, d_ran, d_ai, r_ran, r_ai,
                 delta_r_ran, delta_r_ai, ran_completion_ratio, ai_completion_ratio,
                 utilization_ratio, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(snapshot, ensure_ascii=False),
            ))
            self.conn.commit()
            self.record_article_marl_state(snapshot, timestamp=timestamp)
            self.record_marl_shadow_comparison(snapshot, timestamp=timestamp)
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar snapshot de recursos: {e}")

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
                 live_score, shadow_score, score_delta,
                 live_ran_completion_est, shadow_ran_completion_est,
                 live_ai_completion_est, shadow_ai_completion_est,
                 live_total_shortfall, shadow_total_shortfall,
                 live_budget_gap, shadow_budget_gap,
                 delta_r_ran, delta_r_ai, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                               extended_metrics=None):
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
                 latency_p95_per_ue_us, variance_per_ue_us2, cvar_per_ue_us, ue_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, dt_str, sim_time_s, cell_id,
                  global_worst_latency, global_avg_latency,
                  global_min_latency, global_max_latency,
                  global_jitter, packet_loss,
                  total_active_ues, total_active_cameras, total_critical,
                  total_tx_bytes, total_rx_bytes,
                  total_tx_pdus, total_rx_pdus, throughput_kbps,
                  energy_state, slicer_state,
                  latency_p5_us, latency_p95_us, latency_min_nonzero_us, valid_samples,
                  latency_p95_per_ue, variance_per_ue, cvar_per_ue, ue_count))
            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar métrica estendida: {e}")
    
    def record_ue_metrics(self, timestamp=None, ue_metrics_list=None):
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
                     connectivity, gateway_id, domain, mobility_profile)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                      ue.get('domain'), ue.get('mobility_profile')))
            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar métricas de UE: {e}")
    
    def record_energy_command(self, command, power_percent=100, ru_count=2, mmwave_count=1, reason=""):
        """
        Registra comando de energia enviado ao xApp Energy Saver.
        
        Args:
            command: Comando enviado (FULL_POWER, CONDITIONAL_REDUCE, etc)
            power_percent: Porcentagem de potência (100, 70, 50, 25, 10)
            ru_count: Número de RUs ativas
            mmwave_count: Número de mmWave ativas
            reason: Motivo do comando
        """
        timestamp = int(time.time())
        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO energy_commands 
                (timestamp, datetime, command, power_percent, ru_count, mmwave_count, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, dt_str, command, power_percent, ru_count, mmwave_count, reason))
            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar comando de energia: {e}")

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
        
        self.record_extended_metric(
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
                'ue_count': ue_count
            }
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
                    'is_critical': ue_data.get('is_critical', False)
                })
            except (ValueError, TypeError):
                continue
        
        if ue_list:
            self.record_ue_metrics(timestamp, ue_list)
    
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
            SELECT datetime, throughput_kbps, global_packet_loss_rate, 
                   global_jitter_us, total_tx_bytes, total_rx_bytes,
                   cvar_per_ue_us, variance_per_ue_us2
            FROM extended_metrics
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'datetime': row[0],
                'throughput_kbps': row[1],
                'global_packet_loss_rate': row[2],
                'global_jitter_us': row[3],
                'total_tx_bytes': row[4],
                'total_rx_bytes': row[5],
                'cvar_per_ue_us': row[6],
                'variance_per_ue_us2': row[7],
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
