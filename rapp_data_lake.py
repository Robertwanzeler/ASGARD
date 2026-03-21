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
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DB_PATH = "/tmp/rapp_data_lake.db"


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
        """Conecta ao banco SQLite"""
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
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
                UNIQUE(timestamp)
            )
        """)
        
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
        
        self.conn.commit()
        print(f"[DataLake] Tabelas inicializadas em {self.db_path}")
    
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
        """
        if timestamp is None:
            timestamp = int(time.time())
        
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
        
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO decisions_history 
                (timestamp, datetime, decision, reason, confidence, pattern, 
                 agent_override, energy_state, slicer_state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, dt_str, decision_str, reason, confidence, pattern,
                  agent_override, energy_state, slicer_state))
            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar decisão: {e}")
    
    def record_extended_metric(self, timestamp=None, sim_time_s=0, cell_id=0,
                               global_worst_latency=0, global_avg_latency=0,
                               global_min_latency=0, global_max_latency=0,
                               global_jitter=0, packet_loss=0,
                               total_active_ues=0, total_active_cameras=0,
                               total_critical=0, total_tx_bytes=0, total_rx_bytes=0,
                               total_tx_pdus=0, total_rx_pdus=0, throughput_kbps=0,
                               energy_state=None, slicer_state=None):
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
            energy_state: Estado do Energy Saver
            slicer_state: Estado do SLICER
        """
        if timestamp is None:
            timestamp = int(time.time())
        
        dt = datetime.fromtimestamp(timestamp)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        
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
                 energy_state, slicer_state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, dt_str, sim_time_s, cell_id,
                  global_worst_latency, global_avg_latency,
                  global_min_latency, global_max_latency,
                  global_jitter, packet_loss,
                  total_active_ues, total_active_cameras, total_critical,
                  total_tx_bytes, total_rx_bytes,
                  total_tx_pdus, total_rx_pdus, throughput_kbps,
                  energy_state, slicer_state))
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
                     mcs_avg, tb_size_avg, is_critical)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (timestamp, ue.get('imsi'), ue.get('device_type'), ue.get('cell_id'),
                      ue.get('latency_us'), ue.get('latency_avg_us'), ue.get('latency_min_us'), ue.get('latency_max_us'),
                      ue.get('jitter_us'), ue.get('pdu_size_avg'),
                      ue.get('tx_bytes', 0), ue.get('rx_bytes', 0), ue.get('tx_pdus', 0), ue.get('rx_pdus', 0),
                      ue.get('throughput_kbps'), ue.get('packet_count', 0),
                      ue.get('mcs_avg', 0), ue.get('tb_size_avg', 0), 
                      1 if ue.get('is_critical') else 0))
            self.conn.commit()
        except Exception as e:
            print(f"[DataLake] ERRO ao registrar métricas de UE: {e}")
    
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
            slicer_state=slicer_state
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
        
        cursor.execute("""
            SELECT timestamp, latency_us, cameras_active, critical_cameras,
                   energy_state, slicer_state
            FROM metrics_history
            WHERE datetime >= datetime('now', '-' || ? || ' minutes')
            ORDER BY timestamp DESC
        """, (minutes,))
        
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
                results['blocked'] = count
            elif decision == 'ALLOWED':
                results['allowed'] = count
            elif decision == 'CONDITIONAL':
                results['conditional'] = count
            
            results['by_reason'][reason] = count
        
        return results
    
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
        
        # Primeiro e último registro
        cursor.execute("SELECT MIN(datetime), MAX(datetime) FROM metrics_history")
        row = cursor.fetchone()
        
        return {
            'metrics_count': metrics_count,
            'decisions_count': decisions_count,
            'first_record': row[0],
            'last_record': row[1],
            'db_size_bytes': os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        }
    
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
