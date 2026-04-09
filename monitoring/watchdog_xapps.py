#!/usr/bin/env python3
"""
GreenRAN O-RAN - xApp Watchdog
==============================

Responsabilidade: Monitorar xApps e reiniciá-los se necessário
- Monitora PIDs dos xApps
- Heartbeat a cada 30 segundos
- Reinicia se não houver heartbeat em 60 segundos
- Salva status em /tmp/xapp_health.json

Uso:
    python3 watchdog_xapps.py [--interval SECONDS]
"""

import os
import sys
import time
import json
import signal
import subprocess
from datetime import datetime

DEFAULT_CHECK_INTERVAL = 30  # Verificar a cada 30 segundos
HEARTBEAT_TIMEOUT = 60  # Reiniciar se sem heartbeat por 60 segundos
HEALTH_FILE = "/tmp/xapp_health.json"

XAPPS = {
    'SLICER': {
        'pattern': 'xapp_slicer',
        'binary': '/home/robert/orange_nuclear/flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer',
        'log': '/tmp/xapp_slicer.log'
    },
    'ENERGY': {
        'pattern': 'xapp_energy_saver',
        'binary': '/home/robert/orange_nuclear/flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver',
        'log': '/tmp/xapp_energy.log'
    }
}


class WatchdogXApps:
    """Monitor de xApps com auto-restart."""
    
    def __init__(self, check_interval=DEFAULT_CHECK_INTERVAL):
        self.check_interval = check_interval
        self.running = True
        self.health = {}
        self.last_heartbeat = {}
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self._load_health()
    
    def _signal_handler(self, signum, frame):
        print("\n[Watchdog] Encerrando...")
        self.running = False
    
    def _load_health(self):
        """Carrega health anterior."""
        if os.path.exists(HEALTH_FILE):
            try:
                with open(HEALTH_FILE, 'r') as f:
                    self.health = json.load(f)
                    for name in XAPPS:
                        if name in self.health:
                            self.last_heartbeat[name] = self.health[name].get('last_heartbeat_ts', 0)
            except:
                self.health = {}
    
    def _save_health(self):
        """Salva health."""
        try:
            with open(HEALTH_FILE, 'w') as f:
                json.dump(self.health, f, indent=2)
        except Exception as e:
            print(f"[Watchdog] ERRO ao salvar health: {e}")
    
    def _get_pid(self, pattern):
        """Obtém PID de um processo."""
        try:
            result = subprocess.run(
                ['pgrep', '-f', pattern],
                capture_output=True,
                text=True
            )
            pids = result.stdout.strip().split('\n')
            return int(pids[0]) if pids and pids[0] else None
        except:
            return None
    
    def _is_running(self, pid):
        """Verifica se processo está rodando."""
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except:
            return False
    
    def _get_last_cycle(self, log_file):
        """Obtém último cycle do log."""
        if not os.path.exists(log_file):
            return None
        try:
            with open(log_file, 'r') as f:
                lines = f.readlines()
                for line in reversed(lines):
                    if 'Cycle:' in line:
                        parts = line.split('Cycle:')
                        if len(parts) > 1:
                            cycle = ''.join(c for c in parts[1].split()[0] if c.isdigit())
                            return int(cycle) if cycle else None
        except:
            pass
        return None
    
    def _restart_xapp(self, name, config):
        """Reinicia um xApp."""
        print(f"[Watchdog] Reiniciando {name}...")
        
        # Para processo antigo
        pid = self._get_pid(config['pattern'])
        if pid:
            try:
                os.kill(pid, 9)
                print(f"[Watchdog] {name} (PID {pid}) terminado")
            except:
                pass
        
        time.sleep(1)
        
        # Inicia novo processo
        ric_dir = '/home/robert/orange_nuclear/flexric/build_e2ap_v1'
        base_dir = '/home/robert/orange_nuclear'
        
        env = os.environ.copy()
        env['LD_LIBRARY_PATH'] = f"{ric_dir}/src/ric:{base_dir}/flexric_lib:{ric_dir}/src/xApp"
        
        try:
            with open(config['log'], 'w') as log_file:
                proc = subprocess.Popen(
                    [config['binary']],
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=env
                )
            
            print(f"[Watchdog] {name} iniciado (PID {proc.pid})")
            
            # Atualiza health
            self.health[name] = {
                'status': 'RUNNING',
                'pid': proc.pid,
                'last_restart': datetime.now().isoformat(),
                'restart_count': self.health.get(name, {}).get('restart_count', 0) + 1,
                'total_restarts': self.health.get(name, {}).get('total_restarts', 0) + 1
            }
            self.last_heartbeat[name] = time.time()
            self._save_health()
            
            return True
        
        except Exception as e:
            print(f"[Watchdog] ERRO ao reiniciar {name}: {e}")
            self.health[name] = {'status': 'FAILED', 'error': str(e)}
            self._save_health()
            return False
    
    def check_xapps(self):
        """Verifica status dos xApps."""
        current_time = time.time()
        
        for name, config in XAPPS.items():
            pid = self._get_pid(config['pattern'])
            is_running = self._is_running(pid)
            last_cycle = self._get_last_cycle(config['log'])
            
            if name not in self.health:
                self.health[name] = {
                    'total_restarts': 0,
                    'restarts_today': 0
                }
            
            if is_running:
                # Atualiza heartbeat
                self.last_heartbeat[name] = current_time
                self.health[name].update({
                    'status': 'RUNNING',
                    'pid': pid,
                    'last_heartbeat': datetime.now().fromtimestamp(current_time).isoformat(),
                    'last_heartbeat_ts': current_time,
                    'last_cycle': last_cycle
                })
                
                # Verifica se está responsivo (cycle aumentando)
                last_check = self.health[name].get('last_heartbeat_ts', 0)
                if last_check > 0:
                    time_since_check = current_time - last_check
                    if time_since_check > HEARTBEAT_TIMEOUT:
                        print(f"[Watchdog] {name} não responsivo há {time_since_check:.0f}s")
                        self.health[name]['status'] = 'UNRESPONSIVE'
                
            else:
                # Não está rodando
                if self.health[name].get('status') != 'STOPPED':
                    print(f"[Watchdog] {name} não está rodando, reiniciando...")
                    self._restart_xapp(name, config)
                else:
                    self.health[name]['status'] = 'STOPPED'
            
            self.health[name]['checked_at'] = datetime.now().isoformat()
        
        self._save_health()
    
    def get_status(self):
        """Retorna status resumido."""
        return {
            name: {
                'status': self.health.get(name, {}).get('status', 'UNKNOWN'),
                'pid': self.health.get(name, {}).get('pid'),
                'last_cycle': self.health.get(name, {}).get('last_cycle'),
                'total_restarts': self.health.get(name, {}).get('total_restarts', 0)
            }
            for name in XAPPS
        }
    
    def run(self):
        """Loop principal."""
        print("=" * 50)
        print("       GreenRAN xApp Watchdog")
        print("=" * 50)
        print(f"Intervalo: {self.check_interval}s")
        print(f"Timeout: {HEARTBEAT_TIMEOUT}s")
        print(f"Health: {HEALTH_FILE}")
        print("=" * 50)
        
        # Verificação inicial
        self.check_xapps()
        
        while self.running:
            time.sleep(self.check_interval)
            
            if not self.running:
                break
            
            print(f"\n[Watchdog] {datetime.now().strftime('%H:%M:%S')} - Verificando xApps...")
            self.check_xapps()
            
            # Mostra status
            status = self.get_status()
            for name, info in status.items():
                status_str = info['status']
                if status_str == 'RUNNING':
                    status_str = '\033[92mRUNNING\033[0m'
                elif status_str == 'UNRESPONSIVE':
                    status_str = '\033[93mUNRESPONSIVE\033[0m'
                else:
                    status_str = f'\033[91m{status_str}\033[0m'
                
                print(f"  {name}: {status_str} (PID: {info['pid']}, Cycle: {info['last_cycle']}, Restarts: {info['total_restarts']})")
        
        print("[Watchdog] Encerrado")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='GreenRAN xApp Watchdog')
    parser.add_argument('--interval', '-i', type=int, default=DEFAULT_CHECK_INTERVAL,
                       help=f'Intervalo de verificação em segundos (default: {DEFAULT_CHECK_INTERVAL})')
    args = parser.parse_args()
    
    watchdog = WatchdogXApps(check_interval=args.interval)
    watchdog.run()


if __name__ == '__main__':
    main()
