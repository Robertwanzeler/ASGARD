#!/usr/bin/env python3
"""
GreenRAN O-RAN - Extended Metrics Collector
==========================================
Reads all available stats from ns-3 simulation and generates comprehensive JSON metrics.

Sources:
    - DlPdcpStats.txt: PDCP layer metrics (ground truth for latency)
    - DlMacStats.txt: MAC layer metrics (MCS, TB size)
    - DlRlcStats.txt: RLC layer metrics
    - cu-up-cell-*.txt: CU-UP metrics (throughput)

Usage:
    python3 csv_to_metrics.py [--input-dir DIR] [--output FILE] [--extended-output FILE] [--poll-interval SECONDS]
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
import threading
import signal
from collections import defaultdict

DEFAULT_INPUT_DIR = "/home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran"
DEFAULT_OUTPUT_FILE = "/tmp/xapp_metrics/metrics.json"
DEFAULT_EXTENDED_OUTPUT_FILE = "/tmp/xapp_metrics/extended_metrics.json"
DEFAULT_POLL_INTERVAL = 1.0

CAMERA_IMSI_RANGE = (1, 3)
SENSOR_IMSI_RANGE = (4, 50)
UE_IMSI_RANGE = (51, 100)

class ExtendedMetricsCollector:
    def __init__(self, input_dir, output_file, extended_output_file, poll_interval):
        self.input_dir = Path(input_dir)
        self.output_file = output_file
        self.extended_output_file = extended_output_file
        self.poll_interval = poll_interval
        self.running = True
        self.lock = threading.Lock()
        
        # Persistência de UEs (Memória de 5 segundos)
        self.active_ues_cache = {} # imsi -> last_seen_timestamp
        self.activity_window = 5.0 # 5 segundos
        
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        os.makedirs(os.path.dirname(extended_output_file), exist_ok=True)
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        self.running = False
    
    def get_device_type(self, imsi):
        """Determine device type from IMSI"""
        try:
            imsi_num = int(imsi)
            if CAMERA_IMSI_RANGE[0] <= imsi_num <= CAMERA_IMSI_RANGE[1]:
                return "camera"
            elif SENSOR_IMSI_RANGE[0] <= imsi_num <= SENSOR_IMSI_RANGE[1]:
                return "sensor"
            elif UE_IMSI_RANGE[0] <= imsi_num <= UE_IMSI_RANGE[1]:
                return "background"
        except ValueError:
            pass
        return "background"
    
    def process_pdcp_stats(self, filepath):
        """Process DlPdcpStats.txt - Ground truth for latency
        
        Format (tab-separated):
        start  end  CellId  IMSI  RNTI  LCID  nTxPDUs  TxBytes  nRxPDUs  RxBytes  delay  stdDev  min  max  PduSize  stdDev  min  max
        """
        if not filepath.exists():
            return []
        
        metrics = []
        
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    if line.startswith('%') or line.startswith('start') or not line.strip():
                        continue
                    
                    parts = line.strip().split('\t')
                    if len(parts) < 15:
                        continue
                    
                    try:
                        time_start = float(parts[0])
                        time_end = float(parts[1])
                        cell_id = int(parts[2])
                        imsi = parts[3]
                        rnti = int(parts[4])
                        lcid = int(parts[5])
                        n_tx_pdus = int(parts[6])
                        tx_bytes = int(parts[7])
                        n_rx_pdus = int(parts[8])
                        rx_bytes = int(parts[9])
                        delay_s = float(parts[10])
                        delay_stddev = float(parts[11])
                        delay_min = float(parts[12])
                        delay_max = float(parts[13])
                        pdu_size = int(parts[14])
                        
                        metrics.append({
                            'time_start': time_start,
                            'time_end': time_end,
                            'cell_id': cell_id,
                            'imsi': imsi,
                            'rnti': rnti,
                            'lcid': lcid,
                            'n_tx_pdus': n_tx_pdus,
                            'tx_bytes': tx_bytes,
                            'n_rx_pdus': n_rx_pdus,
                            'rx_bytes': rx_bytes,
                            'delay_s': delay_s,
                            'delay_us': delay_s * 1_000_000,
                            'delay_stddev_s': delay_stddev,
                            'delay_stddev_us': delay_stddev * 1_000_000,
                            'delay_min_s': delay_min,
                            'delay_min_us': delay_min * 1_000_000,
                            'delay_max_s': delay_max,
                            'delay_max_us': delay_max * 1_000_000,
                            'pdu_size': pdu_size,
                            'device_type': self.get_device_type(imsi)
                        })
                    except (ValueError, IndexError):
                        continue
                        
        except Exception as e:
            print(f"[CSV_METRICS] Error reading PDCP stats: {e}")
        
        return metrics
    
    def process_mac_stats(self, filepath):
        """Process DlMacStats.txt - MAC layer metrics
        
        Format (tab-separated):
        time  cellId  IMSI  frame  sframe  RNTI  mcsTb1  sizeTb1  mcsTb2  sizeTb2  ccId
        """
        if not filepath.exists():
            return []
        
        metrics = []
        
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    if line.startswith('%') or line.startswith('time') or not line.strip():
                        continue
                    
                    parts = line.strip().split('\t')
                    if len(parts) < 11:
                        continue
                    
                    try:
                        time_val = float(parts[0])
                        cell_id = int(parts[1])
                        imsi = parts[2]
                        frame = int(parts[3])
                        sframe = int(parts[4])
                        rnti = int(parts[5])
                        mcs_tb1 = int(parts[6]) if parts[6] else 0
                        size_tb1 = int(parts[7]) if parts[7] else 0
                        mcs_tb2 = int(parts[8]) if parts[8] else 0
                        size_tb2 = int(parts[9]) if parts[9] else 0
                        cc_id = int(parts[10]) if len(parts) > 10 and parts[10] else 0
                        
                        metrics.append({
                            'time': time_val,
                            'cell_id': cell_id,
                            'imsi': imsi,
                            'frame': frame,
                            'sframe': sframe,
                            'rnti': rnti,
                            'mcs_tb1': mcs_tb1,
                            'size_tb1': size_tb1,
                            'mcs_tb2': mcs_tb2,
                            'size_tb2': size_tb2,
                            'cc_id': cc_id,
                            'device_type': self.get_device_type(imsi)
                        })
                    except (ValueError, IndexError):
                        continue
                        
        except Exception as e:
            print(f"[CSV_METRICS] Error reading MAC stats: {e}")
        
        return metrics
    
    def process_rlc_stats(self, filepath):
        """Process DlRlcStats.txt - RLC layer metrics"""
        if not filepath.exists():
            return []
        
        metrics = []
        
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    if line.startswith('%') or line.startswith('start') or not line.strip():
                        continue
                    
                    parts = line.strip().split('\t')
                    if len(parts) < 12:
                        continue
                    
                    try:
                        time_start = float(parts[0])
                        time_end = float(parts[1])
                        cell_id = int(parts[2])
                        imsi = parts[3]
                        rnti = int(parts[4])
                        
                        metrics.append({
                            'time_start': time_start,
                            'time_end': time_end,
                            'cell_id': cell_id,
                            'imsi': imsi,
                            'rnti': rnti,
                            'device_type': self.get_device_type(imsi)
                        })
                    except (ValueError, IndexError):
                        continue
                        
        except Exception as e:
            print(f"[CSV_METRICS] Error reading RLC stats: {e}")
        
        return metrics
    
    def percentile(self, data, p):
        """Retorna percentil p (0-100)"""
        if not data:
            return 0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        return sorted_data[min(idx, len(sorted_data)-1)]
    
    def percentile_5(self, data):
        return self.percentile(data, 5)
    
    def percentile_95(self, data):
        return self.percentile(data, 95)
    
    def min_nonzero(self, data):
        """Retorna menor valor > 0, ou 0 se todos forem zero"""
        nonzero = [x for x in data if x > 0]
        return min(nonzero) if nonzero else 0
    
    def median(self, data):
        """Retorna mediana dos dados"""
        if not data:
            return 0
        sorted_data = sorted(data)
        n = len(sorted_data)
        if n % 2 == 0:
            return (sorted_data[n//2 - 1] + sorted_data[n//2]) / 2
        else:
            return sorted_data[n//2]
    
    def aggregate_metrics(self, pdcp_metrics, mac_metrics):
        """Aggregate all metrics into comprehensive JSON"""
        
        result = {
            'timestamp': int(time.time() * 1000),
            'timestamp_iso': time.strftime('%Y-%m-%d %H:%M:%S'),
            'sim_time_range': {},
            'ue_metrics': {},
            'cell_metrics': {},
            'global_metrics': {
                'global_worst_latency_us': 0.0,
                'global_avg_latency_us': 0.0,
                'global_min_latency_us': float('inf'),
                'global_max_latency_us': 0.0,
                'global_jitter_us': 0.0,
                'global_packet_loss_rate': 0.0,
                'total_active_ues': 0,
                'total_active_cameras': 0,
                'total_critical_ues': 0,
                'total_tx_bytes': 0,
                'total_rx_bytes': 0,
                'total_tx_pdus': 0,
                'total_rx_pdus': 0,
                'throughput_kbps': 0.0
            },
            'active_cameras': 0,
            'critical_cameras': 0
        }
        
        if not pdcp_metrics:
            return result
        
        current_time = pdcp_metrics[0].get('time_start', 0)
        recent_window = 30.0
        recent_metrics = [m for m in pdcp_metrics if (current_time - m.get('time_start', 0)) <= recent_window]
        
        if not recent_metrics:
            return result
        
        result['sim_time_range'] = {
            'start': recent_metrics[0].get('time_start', 0),
            'end': recent_metrics[-1].get('time_start', 0),
            'window_s': recent_window
        }
        
        ue_data = defaultdict(lambda: {
            'latencies': [],
            'tx_bytes': 0,
            'rx_bytes': 0,
            'tx_pdus': 0,
            'rx_pdus': 0,
            'jitters': [],
            'pdu_sizes': [],
            'lat_min': float('inf'),
            'lat_max': 0.0,
            'device_type': 'unknown'
        })
        
        all_latencies = []
        all_jitters = []
        
        # Atualizar cache de atividade dos UEs
        current_sim_time = recent_metrics[-1]['time_end'] if recent_metrics else self.last_processed_time
        for m in recent_metrics:
            self.active_ues_cache[m['imsi']] = m['time_end']
            
        # Remover UEs inativos (mais de 5 segundos sem pacotes)
        active_imsis = [imsi for imsi, last_seen in self.active_ues_cache.items() 
                       if (current_sim_time - last_seen) < self.activity_window]
        self.active_ues_cache = {imsi: self.active_ues_cache[imsi] for imsi in active_imsis}

        for m in recent_metrics:
            imsi = m['imsi']
            ue_data[imsi]['latencies'].append(m['delay_us'])
            ue_data[imsi]['tx_bytes'] += m['tx_bytes']
            ue_data[imsi]['rx_bytes'] += m['rx_bytes']
            ue_data[imsi]['tx_pdus'] += m['n_tx_pdus']
            ue_data[imsi]['rx_pdus'] += m['n_rx_pdus']
            ue_data[imsi]['jitters'].append(m['delay_stddev_us'])
            ue_data[imsi]['pdu_sizes'].append(m['pdu_size'])
            ue_data[imsi]['lat_min'] = min(ue_data[imsi]['lat_min'], m['delay_min_us'])
            ue_data[imsi]['lat_max'] = max(ue_data[imsi]['lat_max'], m['delay_max_us'])
            ue_data[imsi]['device_type'] = m['device_type']
            ue_data[imsi]['cell_id'] = m['cell_id']
            
            all_latencies.append(m['delay_us'])
            all_jitters.append(m['delay_stddev_us'])
            
        # Garantir que UEs no cache mas sem tráfego recente também sejam processados
        for imsi in active_imsis:
            if imsi not in ue_data:
                ue_data[imsi]['device_type'] = self.get_device_type(imsi)
                ue_data[imsi]['latencies'] = [] # Ativo mas sem dados novos
        
        camera_count = 0
        camera_critical_count = 0
        critical_count = 0
        total_tx_bytes = 0
        total_rx_bytes = 0
        worst_latency = 0
        worst_camera_latency = 0
        
        SLA_THRESHOLD_US = 100000
        
        for imsi, data in ue_data.items():
            if not data['latencies']:
                continue
            
            avg_latency = sum(data['latencies']) / len(data['latencies'])
            max_latency = max(data['latencies'])
            avg_jitter = sum(data['jitters']) / len(data['jitters']) if data['jitters'] else 0
            avg_pdu_size = sum(data['pdu_sizes']) / len(data['pdu_sizes']) if data['pdu_sizes'] else 0
            
            throughput_bytes = data['tx_bytes'] + data['rx_bytes']
            throughput_kbps = (throughput_bytes * 8) / (recent_window * 1000) if recent_window > 0 else 0
            
            result['ue_metrics'][imsi] = {
                'device_type': data['device_type'],
                'cell_id': data.get('cell_id', 0),
                'latency_us': max_latency,
                'latency_avg_us': avg_latency,
                'latency_min_us': data['lat_min'] if data['lat_min'] != float('inf') else 0,
                'latency_max_us': data['lat_max'],
                'jitter_us': avg_jitter,
                'pdu_size_avg': avg_pdu_size,
                'tx_bytes': data['tx_bytes'],
                'rx_bytes': data['rx_bytes'],
                'tx_pdus': data['tx_pdus'],
                'rx_pdus': data['rx_pdus'],
                'throughput_kbps': throughput_kbps,
                'packet_count': len(data['latencies']),
                'is_critical': max_latency >= SLA_THRESHOLD_US
            }
            
            total_tx_bytes += data['tx_bytes']
            total_rx_bytes += data['rx_bytes']
            
            if data['device_type'] == 'camera':
                camera_count += 1
                if max_latency >= SLA_THRESHOLD_US:
                    camera_critical_count += 1
                if max_latency > worst_camera_latency:
                    worst_camera_latency = max_latency
            
            if max_latency >= SLA_THRESHOLD_US:
                critical_count += 1
            
            if max_latency > worst_latency:
                worst_latency = max_latency
        
        global_avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0
        global_avg_jitter = sum(all_jitters) / len(all_jitters) if all_jitters else 0
        
        # Filtrar valores zero para métricas de min (valores 0 são artefatos)
        all_latencies_nonzero = [l for l in all_latencies if l > 0]
        global_min_latency = min(all_latencies_nonzero) if all_latencies_nonzero else 0
        global_max_latency = max(all_latencies) if all_latencies else 0
        
        # Métricas robustas (percentis) - usar apenas valores não-zero
        latency_p5 = self.percentile_5(all_latencies_nonzero) if all_latencies_nonzero else 0
        latency_p95 = self.percentile_95(all_latencies)
        latency_min_nonzero = self.min_nonzero(all_latencies)
        latency_median = self.median(all_latencies_nonzero) if all_latencies_nonzero else 0
        
        # NOVO: Calcular métricas POR UE para capturar UEs críticos
        # Isso corrige o problema onde P95 agregado não reflete UEs com latência alta
        ue_avg_latencies = []
        for imsi, data in ue_data.items():
            if data['latencies']:
                # Usar a MÉDIA de cada UE (não máximo)
                ue_avg = sum(data['latencies']) / len(data['latencies'])
                ue_avg_latencies.append(ue_avg)
        
        # CVaR e Variância agora usam latência POR UE
        if ue_avg_latencies:
            # P95 por UE - captura quando 5% dos UEs têm latência alta
            latency_p95_per_ue = self.percentile_95(ue_avg_latencies)
            
            # Variância por UE - mede dispersão entre UEs
            ue_mean = sum(ue_avg_latencies) / len(ue_avg_latencies)
            variance_per_ue = sum((x - ue_mean) ** 2 for x in ue_avg_latencies) / len(ue_avg_latencies)
            
            # CVaR por UE - média dos 5% piores UEs
            sorted_ue_latencies = sorted(ue_avg_latencies)
            cvar_idx = int(len(sorted_ue_latencies) * 0.95)
            cvar_per_ue = sum(sorted_ue_latencies[cvar_idx:]) / len(sorted_ue_latencies[cvar_idx:]) if cvar_idx < len(sorted_ue_latencies) else sorted_ue_latencies[-1]
        else:
            latency_p95_per_ue = latency_p95
            variance_per_ue = 0
            cvar_per_ue = 0
        
        total_throughput = total_tx_bytes + total_rx_bytes
        total_throughput_kbps = (total_throughput * 8) / (recent_window * 1000) if recent_window > 0 else 0
        
        result['global_metrics'] = {
            'global_worst_latency_us': worst_latency,
            'global_worst_camera_latency_us': worst_camera_latency,
            'global_avg_latency_us': global_avg_latency,
            'global_min_latency_us': global_min_latency,
            'global_max_latency_us': global_max_latency,
            'global_jitter_us': global_avg_jitter,
            'global_packet_loss_rate': 0.0,
            'total_active_ues': len(ue_data),
            'total_active_cameras': camera_count,
            'total_critical_ues': critical_count,
            'total_tx_bytes': total_tx_bytes,
            'total_rx_bytes': total_rx_bytes,
            'total_tx_pdus': sum(d['tx_pdus'] for d in ue_data.values()),
            'total_rx_pdus': sum(d['rx_pdus'] for d in ue_data.values()),
            'throughput_kbps': total_throughput_kbps,
            # Métricas robustas
            'latency_p5_us': latency_p5,
            'latency_p95_us': latency_p95,
            'latency_min_nonzero_us': latency_min_nonzero,
            'latency_median_us': latency_median,
            # NOVO: Métricas POR UE (capturam UEs críticos)
            'latency_p95_per_ue_us': latency_p95_per_ue,
            'variance_per_ue_us2': variance_per_ue,
            'cvar_per_ue_us': cvar_per_ue,
            'ue_count': len(ue_avg_latencies)
        }
        
        result['active_cameras'] = camera_count
        result['critical_cameras'] = camera_critical_count
        result['critical_ues'] = critical_count
        
        if mac_metrics:
            mac_by_imsi = defaultdict(lambda: {'mcs': [], 'tb_sizes': []})
            all_mcs = []
            all_tb_sizes = []
            
            for m in mac_metrics[-1000:]:
                imsi = m['imsi']
                mac_by_imsi[imsi]['mcs'].append(m['mcs_tb1'])
                mac_by_imsi[imsi]['tb_sizes'].append(m['size_tb1'] + m['size_tb2'])
                all_mcs.append(m['mcs_tb1'])
                all_tb_sizes.append(m['size_tb1'] + m['size_tb2'])
            
            # Métricas globais de MAC
            if all_mcs:
                result['global_metrics']['global_mcs_avg'] = sum(all_mcs) / len(all_mcs) if all_mcs else 0
                result['global_metrics']['global_mcs_min'] = min(all_mcs) if all_mcs else 0
                result['global_metrics']['global_mcs_max'] = max(all_mcs) if all_mcs else 0
                result['global_metrics']['global_tb_size_avg'] = sum(all_tb_sizes) / len(all_tb_sizes) if all_tb_sizes else 0
            
            for imsi, data in mac_by_imsi.items():
                if imsi in result['ue_metrics']:
                    result['ue_metrics'][imsi]['mcs_avg'] = sum(data['mcs']) / len(data['mcs']) if data['mcs'] else 0
                    result['ue_metrics'][imsi]['tb_size_avg'] = sum(data['tb_sizes']) / len(data['tb_sizes']) if data['tb_sizes'] else 0
        
        return result
    
    def write_metrics(self, metrics, filepath):
        """Write metrics to JSON file"""
        try:
            with open(filepath, 'w') as f:
                json.dump(metrics, f, indent=2)
        except Exception as e:
            print(f"[CSV_METRICS] Error writing metrics to {filepath}: {e}")
    
    def write_standard_metrics(self, extended_metrics):
        """Write standard metrics for xApps consumption"""
        result = {
            'timestamp': extended_metrics.get('timestamp', int(time.time() * 1000)),
            'cells': {
                'aggregated': {
                    'ues': {},
                    'cell_average_latency_us': extended_metrics.get('global_metrics', {}).get('global_avg_latency_us', 0),
                    'worst_latency_us': extended_metrics.get('global_metrics', {}).get('global_worst_latency_us', 0),
                    'latency_p95_us': extended_metrics.get('global_metrics', {}).get('latency_p95_us', 0),
                    'active_ues': extended_metrics.get('global_metrics', {}).get('total_active_ues', 0),
                    'time_window_s': extended_metrics.get('sim_time_range', {}).get('window_s', 30)
                }
            },
            'global_worst_latency_us': extended_metrics.get('global_metrics', {}).get('global_worst_latency_us', 0),
            'latency_p95_us': extended_metrics.get('global_metrics', {}).get('latency_p95_us', 0),
            'active_cameras': extended_metrics.get('active_cameras', 0),
            'critical_cameras': extended_metrics.get('critical_cameras', 0)
        }
        
        for imsi, ue_data in extended_metrics.get('ue_metrics', {}).items():
            result['cells']['aggregated']['ues'][imsi] = {
                'latency_us': ue_data.get('latency_us', 0),
                'throughput_kbps': ue_data.get('throughput_kbps', 0),
                'packets': ue_data.get('packet_count', 0),
                'type': ue_data.get('device_type', 'unknown')
            }
        
        return result
    
    def run(self):
        """Main loop"""
        pdcp_file = self.input_dir / "DlPdcpStats.txt"
        mac_file = self.input_dir / "DlMacStats.txt"
        rlc_file = self.input_dir / "DlRlcStats.txt"
        
        print(f"[CSV_METRICS] Starting Extended Metrics Collector")
        print(f"[CSV_METRICS] PDCP: {pdcp_file}")
        print(f"[CSV_METRICS] MAC:  {mac_file}")
        print(f"[CSV_METRICS] RLC:  {rlc_file}")
        print(f"[CSV_METRICS] Output: {self.output_file}")
        print(f"[CSV_METRICS] Extended: {self.extended_output_file}")
        
        iteration = 0
        while self.running:
            iteration += 1
            
            pdcp_metrics = self.process_pdcp_stats(pdcp_file)
            mac_metrics = self.process_mac_stats(mac_file)
            rlc_metrics = self.process_rlc_stats(rlc_file)
            
            if pdcp_metrics:
                extended = self.aggregate_metrics(pdcp_metrics, mac_metrics)
                
                self.write_metrics(extended, self.extended_output_file)
                
                standard = self.write_standard_metrics(extended)
                self.write_metrics(standard, self.output_file)
                
                gm = extended.get('global_metrics', {})
                if iteration % 5 == 0:
                    print(f"[CSV_METRICS] iter={iteration} "
                          f"lat={gm.get('global_worst_latency_us', 0)/1000:.1f}ms "
                          f"avg={gm.get('global_avg_latency_us', 0)/1000:.1f}ms "
                          f"cams={extended.get('active_cameras', 0)} "
                          f"critical={extended.get('critical_cameras', 0)} "
                          f"ues={gm.get('total_active_ues', 0)} "
                          f"tp={gm.get('throughput_kbps', 0):.0f}kbps")
            else:
                if iteration % 20 == 0:
                    print(f"[CSV_METRICS] Waiting for data... (iter {iteration})")
            
            time.sleep(self.poll_interval)
        
        print("[CSV_METRICS] Shutdown")


def main():
    parser = argparse.ArgumentParser(description='GreenRAN Extended Metrics Collector')
    parser.add_argument('--input-dir', '-i', default=DEFAULT_INPUT_DIR)
    parser.add_argument('--output', '-o', default=DEFAULT_OUTPUT_FILE)
    parser.add_argument('--extended-output', '-e', default=DEFAULT_EXTENDED_OUTPUT_FILE)
    parser.add_argument('--poll-interval', '-p', type=float, default=DEFAULT_POLL_INTERVAL)
    args = parser.parse_args()
    
    collector = ExtendedMetricsCollector(
        args.input_dir,
        args.output,
        args.extended_output,
        args.poll_interval
    )
    collector.run()


if __name__ == '__main__':
    main()
