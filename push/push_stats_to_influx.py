#!/usr/bin/env python3
"""
GreenRAN O-RAN - Push Stats to InfluxDB
========================================

Script para enviar métricas do ns-3 para InfluxDB (Grafana).

Funcionamento:
1. Lê stats files do ns-3 continuamente
2. Formata para InfluxDB line protocol
3. Envia via HTTP para localhost:8086
4. Grafana lê de InfluxDB e mostra dashboards

Uso:
    python3 push_stats_to_influx.py
    python3 push_stats_to_influx.py --interval 5 --db influx

Arquivos lidos:
    - DlE2PdcpStats.txt: Latência PDCP por UE
    - DlE2RlcStats.txt: Buffer RLC
    - cu-up-cell-*.txt: CU-UP stats (throughput)
"""

import os
import sys
import time
import argparse
import requests
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from greenran_paths import NS3_DIR, as_str
from greenran_runtime import load_runtime_config

RUNTIME_CONFIG = load_runtime_config()

# Configurações padrão
DEFAULT_INFLUX_HOST = RUNTIME_CONFIG["monitoring"]["influxdb_host"]
DEFAULT_INFLUX_PORT = int(RUNTIME_CONFIG["monitoring"]["influxdb_port"])
DEFAULT_INFLUX_DB = RUNTIME_CONFIG["monitoring"]["influxdb_db"]
DEFAULT_INFLUX_USER = "admin"
DEFAULT_INFLUX_PASSWORD = "admin"
DEFAULT_INTERVAL = int(RUNTIME_CONFIG["monitoring"]["push_interval_seconds"])
DEFAULT_STATS_DIR = as_str(NS3_DIR)


class InfluxDBPusher:
    """Classe para push de métricas para InfluxDB."""
    
    def __init__(self, host="localhost", port=8086, db="influx", 
                 user="admin", password="admin"):
        self.host = host
        self.port = port
        self.db = db
        self.user = user
        self.password = password
        self.base_url = f"http://{host}:{port}"
        self.write_url = f"{self.base_url}/write?db={db}"
        
        # Controle de linhas já processadas
        self.processed_lines = {}
        
        # Headers armazenados por arquivo
        self.file_headers = {}
        
    def push_line_protocol(self, lines):
        """Envia dados no formato InfluxDB line protocol."""
        if not lines:
            return True
            
        try:
            data = "\n".join(lines)
            response = requests.post(
                self.write_url,
                data=data,
                headers={"Content-Type": "application/octet-stream"},
                timeout=5
            )
            return response.status_code == 204
        except Exception as e:
            print(f"[InfluxDB] Erro ao enviar: {e}")
            return False
    
    def read_new_lines(self, file_path):
        """Lê apenas as linhas novas do arquivo."""
        if not os.path.exists(file_path):
            return []
        
        # Inicializar contador se necessário
        if file_path not in self.processed_lines:
            self.processed_lines[file_path] = 0
        
        try:
            with open(file_path, 'r') as f:
                all_lines = f.readlines()
            
            # Se é a primeira leitura, extrair e armazenar o cabeçalho
            if file_path not in self.file_headers and len(all_lines) > 0:
                first_line = all_lines[0].strip()
                if ',' in first_line:
                    self.file_headers[file_path] = [col.strip().lower() for col in first_line.split(',')]
                elif '\t' in first_line:
                    self.file_headers[file_path] = [col.strip().lower() for col in first_line.split('\t')]
                else:
                    self.file_headers[file_path] = []
                # Pular a primeira linha (cabeçalho) na primeira leitura
                self.processed_lines[file_path] = 1
            
            # Pegar apenas linhas novas
            new_lines = all_lines[self.processed_lines[file_path]:]
            self.processed_lines[file_path] = len(all_lines)
            
            return new_lines
        except Exception as e:
            print(f"[File] Erro ao ler {file_path}: {e}")
            return []
    
    def parse_pdcp_stats(self, lines):
        """
        Parse DlE2PdcpStats.txt
        Formato: start end CellId IMSI RNTI LCID nTxPDUs TxBytes nRxPDUs RxBytes delay stdDev min max PduSize stdDev min max
        Colunas: [0]start [1]end [2]CellId [3]IMSI [4]RNTI [5]LCID [6]nTxPDUs [7]TxBytes [8]nRxPDUs [9]RxBytes [10]delay
        """
        measurements = []
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('%'):
                continue
            
            parts = line.split('\t')
            if len(parts) < 11:
                continue
            
            try:
                start = float(parts[0])
                end = float(parts[1])
                cell_id = int(parts[2])
                imsi = int(parts[3])
                tx_bytes = int(parts[7])
                rx_bytes = int(parts[9])
                delay = float(parts[10])  # delay em segundos (coluna 10!)
                
                # Converter delay para ms
                delay_ms = delay * 1000
                
                # Timestamp em nanoseconds
                ts = int(time.time() * 1e9)
                
                # Latência por UE
                measurements.append(
                    f"ue_latency,imsi={imsi},cellid={cell_id} delay_ms={delay_ms:.2f} {ts}"
                )
                
                # Throughput por UE
                measurements.append(
                    f"ue_throughput,imsi={imsi},cellid={cell_id} tx_bytes={tx_bytes},rx_bytes={rx_bytes} {ts}"
                )
                
            except (ValueError, IndexError) as e:
                continue
        
        return measurements
    
    def parse_rlc_stats(self, lines):
        """
        Parse DlE2RlcStats.txt
        Formato: start end CellId IMSI RNTI LCID txBytes rxBytes delay stdDev min max
        """
        measurements = []
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('%'):
                continue
            
            parts = line.split('\t')
            if len(parts) < 10:
                continue
            
            try:
                cell_id = int(parts[2])
                imsi = int(parts[3])
                tx_bytes = int(parts[6])
                rx_bytes = int(parts[7])
                delay = float(parts[8]) if len(parts) > 8 else 0
                
                ts = int(time.time() * 1e9)
                
                measurements.append(
                    f"ue_rlc_buffer,imsi={imsi},cellid={cell_id} tx_bytes={tx_bytes},rx_bytes={rx_bytes} {ts}"
                )
                
            except (ValueError, IndexError):
                continue
        
        return measurements
    
    def parse_cu_up_stats(self, lines, cell_id, file_path=None):
        """
        Parse cu-up-cell-*.txt - Formato específico para dashboards
        
        O dashboard espera medições como (lowercase!):
        - cu-up-cell-1_drb.pdcpsdudelaydl (cellaveragelatency)
        - cu-up-cell-1_m_pdcpbytesdl (celldltxvolume)
        """
        measurements = []
        
        # Usar header armazenado se disponível
        if file_path and file_path in self.file_headers:
            header = self.file_headers[file_path]
        else:
            return measurements
        
        if header is None:
            return measurements
        
        # Processar linhas de dados
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Detectar separador
            if ',' in line:
                parts = line.split(',')
            else:
                parts = line.split('\t')
            
            # Pular linhas que parecem cabeçalho
            if not parts[0].replace('.', '').isdigit():
                continue
            
            if len(parts) < 2:
                continue
            
            ts = int(time.time() * 1e9)
            
            # Para cada coluna
            for i, col_name in enumerate(header):
                if i >= len(parts):
                    break
                
                # Pular colunas irrelevantes
                if col_name in ['timestamp', 'ueimsicomplete', '']:
                    continue
                
                try:
                    value_str = parts[i].strip()
                    if not value_str:
                        continue
                    value = float(value_str)
                    
                    # Criar nome da medição - USAR UNDERSCORE ao invés de espaço
                    # Para compatibilidade com Grafana
                    raw_measurement = f"cu-up-cell-{cell_id}_{col_name}"
                    # Substituir espaços e parênteses por underscore
                    measurement = raw_measurement.replace(' ', '_').replace('(', '_').replace(')', '_').replace('__', '_').rstrip('_')
                    
                    measurements.append(
                        f'{measurement} value={value:.6f} {ts}'
                    )
                    
                except ValueError:
                    continue
        
        return measurements
    
    def parse_du_cell_stats(self, lines, cell_id, file_path=None):
        """
        Parse du-cell-*.txt - Formato específico para dashboards
        
        Formato do arquivo du-cell-X.txt:
        timestamp,ueImsiComplete,dlPrbUsage,RRU.PrbUsedDl,DRB.MeanActiveUeDl,...
        
        O dashboard espera medições como (lowercase!):
        - du-cell-2_dlprbusage
        - du-cell-2_rru.prbuseddl
        - du-cell-2_drb.meanactiveuedl
        """
        measurements = []
        
        # Usar header armazenado se disponível
        if file_path and file_path in self.file_headers:
            header = self.file_headers[file_path]
        else:
            # Fallback: tentar extrair header da primeira linha
            header = None
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if ',' in line:
                    parts = line.split(',')
                else:
                    parts = line.split('\t')
                # Verificar se parece um cabeçalho
                if not parts[0].replace('.', '').isdigit():
                    header = [col.strip().lower() for col in parts]
                break
        
        if header is None:
            return measurements
        
        # Processar linhas de dados
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Detectar separador
            if ',' in line:
                parts = line.split(',')
            else:
                parts = line.split('\t')
            
            # Pular linhas que parecem cabeçalho
            if not parts[0].replace('.', '').isdigit():
                continue
            
            if len(parts) < 2:
                continue
            
            ts = int(time.time() * 1e9)
            
            # Para cada coluna
            for i, col_name in enumerate(header):
                if i >= len(parts):
                    break
                
                # Pular colunas irrelevantes
                if col_name in ['timestamp', 'ueimsicomplete', 'plmid', 'nrcellid', '']:
                    continue
                
                try:
                    value_str = parts[i].strip()
                    if not value_str:
                        continue
                    value = float(value_str)
                    
                    # Criar nome da medição
                    measurement = f"du-cell-{cell_id}_{col_name}"
                    
                    measurements.append(
                        f'{measurement} value={value:.6f} {ts}'
                    )
                    
                except ValueError:
                    continue
        
        return measurements
    
    def calculate_cell_aggregates(self):
        """Calcula agregados por célula (latência média, UEs ativos)."""
        # Esta função seria chamada após processar todos os UEs
        # Por simplicidade, retornamos lista vazia por enquanto
        return []


def main():
    parser = argparse.ArgumentParser(description="Push ns-3 stats to InfluxDB for Grafana")
    parser.add_argument("--host", default=DEFAULT_INFLUX_HOST, help="InfluxDB host")
    parser.add_argument("--port", type=int, default=DEFAULT_INFLUX_PORT, help="InfluxDB port")
    parser.add_argument("--db", default=DEFAULT_INFLUX_DB, help="InfluxDB database")
    parser.add_argument("--user", default=DEFAULT_INFLUX_USER, help="InfluxDB user")
    parser.add_argument("--password", default=DEFAULT_INFLUX_PASSWORD, help="InfluxDB password")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="Push interval (seconds)")
    parser.add_argument("--stats-dir", default=DEFAULT_STATS_DIR, help="Stats directory")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("  GreenRAN - Push Stats to InfluxDB")
    print("=" * 60)
    print(f"  InfluxDB: {args.host}:{args.port}/{args.db}")
    print(f"  Stats Dir: {args.stats_dir}")
    print(f"  Interval: {args.interval}s")
    print("=" * 60)
    
    # Inicializar pusher
    pusher = InfluxDBPusher(
        host=args.host,
        port=args.port,
        db=args.db,
        user=args.user,
        password=args.password
    )
    
    # Arquivos de stats
    pdcp_file = os.path.join(args.stats_dir, "DlE2PdcpStats.txt")
    pdcp_lte_file = os.path.join(args.stats_dir, "DlE2PdcpStatsLte.txt")
    rlc_file = os.path.join(args.stats_dir, "DlE2RlcStats.txt")
    rlc_lte_file = os.path.join(args.stats_dir, "DlE2RlcStatsLte.txt")
    
    # Arquivos CU-UP
    cu_up_files = []
    for f in os.listdir(args.stats_dir):
        if f.startswith("cu-up-cell-") and f.endswith(".txt"):
            cell_id = f.replace("cu-up-cell-", "").replace(".txt", "")
            cu_up_files.append((os.path.join(args.stats_dir, f), cell_id))
    
    # Arquivos DU
    du_files = []
    for f in os.listdir(args.stats_dir):
        if f.startswith("du-cell-") and f.endswith(".txt"):
            cell_id = f.replace("du-cell-", "").replace(".txt", "")
            du_files.append((os.path.join(args.stats_dir, f), cell_id))
    
    print(f"\n  Arquivos monitorados:")
    print(f"    - {pdcp_file}")
    print(f"    - {pdcp_lte_file}")
    print(f"    - {rlc_file}")
    print(f"    - {rlc_lte_file}")
    for cu_file, cell_id in cu_up_files:
        print(f"    - {cu_file} (Cell {cell_id})")
    for du_file, cell_id in du_files:
        print(f"    - {du_file} (Cell {cell_id})")
    
    print("\n  Aguardando dados...")
    
    cycle = 0
    total_pushed = 0
    last_data_time = 0
    SIMULATION_INACTIVE_TIMEOUT = 60
    
    def is_simulation_active(stats_dir):
        """Verifica se a simulação está ativa verificando timestamps dos arquivos"""
        import os
        key_files = ['DlPdcpStats.txt', 'DlMacStats.txt', 'DlRlcStats.txt']
        now = time.time()
        
        for f in key_files:
            filepath = os.path.join(stats_dir, f)
            if os.path.exists(filepath):
                mtime = os.path.getmtime(filepath)
                if (now - mtime) < SIMULATION_INACTIVE_TIMEOUT:
                    return True
        return False
    
    try:
        while True:
            cycle += 1
            
            if not is_simulation_active(args.stats_dir):
                if cycle % 12 == 0:
                    print(f"  [Cycle {cycle}] Simulação inativa - aguardando...")
                time.sleep(args.interval)
                continue
            
            all_measurements = []
            
            # 1. Processar PDCP stats (latência por UE)
            pdcp_lines = pusher.read_new_lines(pdcp_file)
            if pdcp_lines:
                pdcp_measurements = pusher.parse_pdcp_stats(pdcp_lines)
                all_measurements.extend(pdcp_measurements)
            
            # 2. Processar PDCP LTE stats
            pdcp_lte_lines = pusher.read_new_lines(pdcp_lte_file)
            if pdcp_lte_lines:
                pdcp_lte_measurements = pusher.parse_pdcp_stats(pdcp_lte_lines)
                all_measurements.extend(pdcp_lte_measurements)
            
            # 3. Processar RLC stats
            rlc_lines = pusher.read_new_lines(rlc_file)
            if rlc_lines:
                rlc_measurements = pusher.parse_rlc_stats(rlc_lines)
                all_measurements.extend(rlc_measurements)
            
            # 4. Processar RLC LTE stats
            rlc_lte_lines = pusher.read_new_lines(rlc_lte_file)
            if rlc_lte_lines:
                rlc_lte_measurements = pusher.parse_rlc_stats(rlc_lte_lines)
                all_measurements.extend(rlc_lte_measurements)
            
            # 5. Processar CU-UP stats
            for cu_file, cell_id in cu_up_files:
                cu_lines = pusher.read_new_lines(cu_file)
                if cu_lines:
                    cu_measurements = pusher.parse_cu_up_stats(cu_lines, cell_id, cu_file)
                    all_measurements.extend(cu_measurements)
            
            # 6. Processar DU stats
            for du_file, cell_id in du_files:
                du_lines = pusher.read_new_lines(du_file)
                if du_lines:
                    du_measurements = pusher.parse_du_cell_stats(du_lines, cell_id, du_file)
                    all_measurements.extend(du_measurements)
            
            # 7. Enviar para InfluxDB
            if all_measurements:
                success = pusher.push_line_protocol(all_measurements)
                if success:
                    total_pushed += len(all_measurements)
                    if cycle % 12 == 0:  # Log a cada minuto
                        print(f"  [Cycle {cycle}] Enviadas {len(all_measurements)} métricas (total: {total_pushed})")
                else:
                    print(f"  [Cycle {cycle}] ERRO ao enviar métricas!")
            else:
                if cycle % 12 == 0:
                    print(f"  [Cycle {cycle}] Nenhuma métrica nova")
            
            # Aguardar próximo ciclo
            time.sleep(args.interval)
            
    except KeyboardInterrupt:
        print(f"\n\n  Parado pelo usuário.")
        print(f"  Total de métricas enviadas: {total_pushed}")
        sys.exit(0)
    except Exception as e:
        print(f"\n  ERRO: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
