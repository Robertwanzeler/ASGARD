#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp xApp Manager
===================================

Responsabilidade: Controla o ciclo de vida dos xApps
- Iniciar/Parar processos de xApps
- Verificar status de execução
- Garantir fallback do Slicer se rApp parar

Arquitetura:
    rApp → XAppManager → xApp_Slicer (sempre ativo)
                       → xApp_Energy (controlado pelo rApp)

Uso:
    from rapp_xapp_manager import XAppManager
    
    xm = XAppManager()
    xm.start("slicer")           # Inicia Slicer (prioridade)
    xm.start("energy_saver")      # Inicia Energy se permitido
    xm.stop("energy_saver")       # Para Energy se necessário
    xm.is_running("slicer")        # Verifica status
"""

import os
import sys
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from greenran_paths import (
    PROJECT_ROOT,
    FLEXRIC_DIR,
    FLEXRIC_LIB_DIR,
    FLEXRIC_BUILD_DIR,
    XAPP_SLICER_LOG_PATH,
    XAPP_ENERGY_LOG_PATH,
    XAPP_VEHICLE_LOG_PATH,
    XAPP_SLICER_PID_PATH,
    XAPP_ENERGY_PID_PATH,
    XAPP_VEHICLE_PID_PATH,
    STATE_DIR,
    as_str,
)

BASE_DIR = as_str(PROJECT_ROOT)
FLEXRIC_DIR = as_str(FLEXRIC_DIR)
FLEXRIC_LIB = as_str(FLEXRIC_LIB_DIR)
FLEXRIC_BUILD = as_str(FLEXRIC_BUILD_DIR)

XAPP_PATHS = {
    "slicer": f"{FLEXRIC_BUILD}/examples/xApp/c/xapp_slicer",
    "energy_saver": f"{FLEXRIC_BUILD}/examples/xApp/c/xapp_energy_saver",
    "vehicle_control": f"{BASE_DIR}/src/xapp_vehicle_control.py",
}

XAPP_BUILD_DIR_CANDIDATES = [
    Path(FLEXRIC_BUILD),
    Path(FLEXRIC_DIR) / "build",
]

XAPP_LOG_PATHS = {
    "slicer": as_str(XAPP_SLICER_LOG_PATH),
    "energy_saver": as_str(XAPP_ENERGY_LOG_PATH),
    "vehicle_control": as_str(XAPP_VEHICLE_LOG_PATH),
}

XAPP_PID_PATHS = {
    "slicer": as_str(XAPP_SLICER_PID_PATH),
    "energy_saver": as_str(XAPP_ENERGY_PID_PATH),
    "vehicle_control": as_str(XAPP_VEHICLE_PID_PATH),
}


class XAppManager:
    """
    Gerenciador de ciclo de vida dos xApps.
    
    Funcionalidades:
    - Iniciar xApp como subprocesso
    - Parar xApp via signals
    - Verificar se xApp está rodando
    - Fallback: Slicer continua se rApp morrer
    """
    
    def __init__(self, base_dir=BASE_DIR):
        """
        Inicializa o XAppManager.
        
        Args:
            base_dir: Diretório base do projeto
        """
        self.base_dir = base_dir
        self.flexric_dir = f"{base_dir}/flexric"
        self.flexric_lib = f"{base_dir}/flexric_lib"
        self.flexric_build = f"{self.flexric_dir}/build_e2ap_v1"
        
        self.processes = {}
        self.unavailable_xapps = set()
        self.ld_library_path = f"{self.flexric_build}/src/ric:{self.flexric_lib}:{self.flexric_build}/src/xApp"
        
        self.config_file = f"{base_dir}/flexric/flexric.conf"
        
        os.makedirs(as_str(STATE_DIR), exist_ok=True)
        
        print("[XAppManager] Inicializado")
    
    def _get_env(self):
        """Retorna environment com LD_LIBRARY_PATH configurado."""
        env = os.environ.copy()
        env['LD_LIBRARY_PATH'] = self.ld_library_path
        return env

    def _candidate_binary_paths(self, xapp_name):
        if xapp_name == "vehicle_control":
            return [Path(XAPP_PATHS[xapp_name])]

        binary_name = Path(XAPP_PATHS[xapp_name]).name
        candidates = []
        for build_dir in XAPP_BUILD_DIR_CANDIDATES:
            candidates.extend([
                build_dir / 'examples' / 'xApp' / 'c' / binary_name,
                build_dir / 'examples' / 'xApp' / 'c' / xapp_name / binary_name,
                build_dir / 'examples' / 'xApp' / 'c' / ('slicer' if xapp_name == 'slicer' else 'energy_saver') / binary_name,
            ])
        candidates.append(Path(XAPP_PATHS[xapp_name]))
        unique = []
        seen = set()
        for candidate in candidates:
            candidate = candidate.resolve() if candidate.is_absolute() else candidate
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique

    def _resolve_binary_path(self, xapp_name):
        for candidate in self._candidate_binary_paths(xapp_name):
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    def is_available(self, xapp_name):
        if xapp_name in self.unavailable_xapps:
            return False
        return self._resolve_binary_path(xapp_name) is not None
    
    def start(self, xapp_name):
        """
        Inicia um xApp.
        
        Args:
            xapp_name: 'slicer' ou 'energy_saver'
        
        Returns:
            bool: True se iniciou com sucesso
        """
        if xapp_name not in XAPP_PATHS:
            print(f"[XAppManager] ERRO: xApp desconhecido '{xapp_name}'")
            return False
        
        if xapp_name in self.unavailable_xapps:
            return False

        if self.is_running(xapp_name):
            print(f"[XAppManager] {xapp_name} já está rodando (PID: {self.get_pid(xapp_name)})")
            return True
        
        binary_path = self._resolve_binary_path(xapp_name)
        log_path = XAPP_LOG_PATHS[xapp_name]
        pid_path = XAPP_PID_PATHS[xapp_name]
        
        if not binary_path:
            self.unavailable_xapps.add(xapp_name)
            tried = ', '.join(str(p) for p in self._candidate_binary_paths(xapp_name))
            print(f"[XAppManager] AVISO: xApp '{xapp_name}' indisponível neste build; candidatos verificados: {tried}")
            return False
        
        try:
            if xapp_name == "vehicle_control":
                command = [sys.executable, binary_path, "--interval", "2"]
            else:
                command = [binary_path, "-c", self.config_file, "-p", f"{self.flexric_lib}/"]

            with open(log_path, 'w') as log_file:
                process = subprocess.Popen(
                    command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=self._get_env(),
                    preexec_fn=os.setsid
                )
            
            self.processes[xapp_name] = process
            
            with open(pid_path, 'w') as f:
                f.write(str(process.pid))
            
            print(f"[XAppManager] {xapp_name} iniciado (PID: {process.pid})")
            return True
            
        except Exception as e:
            print(f"[XAppManager] ERRO ao iniciar {xapp_name}: {e}")
            return False
    
    def stop(self, xapp_name, timeout=2):
        """
        Para um xApp.
        
        Args:
            xapp_name: 'slicer' ou 'energy_saver'
            timeout: Segundos para esperar graceful shutdown
        
        Returns:
            bool: True se parou com sucesso
        """
        if xapp_name not in XAPP_PATHS:
            print(f"[XAppManager] ERRO: xApp desconhecido '{xapp_name}'")
            return False
        
        if not self.is_running(xapp_name):
            print(f"[XAppManager] {xapp_name} não está rodando")
            return True
        
        process = self.processes.get(xapp_name)
        pid = self.get_pid(xapp_name)
        
        if process is None and pid is None:
            print(f"[XAppManager] {xapp_name} não está rodando")
            return True
        
        try:
            if process:
                pgid = os.getpgid(process.pid)
                os.killpg(pgid, signal.SIGTERM)
            elif pid:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            
            time.sleep(0.2)

            if self.is_running(xapp_name):
                print(f"[XAppManager] SIGTERM não funcionou, enviando SIGKILL...")
                if process:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                elif pid:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                time.sleep(0.2)
            
            self._cleanup(xapp_name)
            print(f"[XAppManager] {xapp_name} parado")
            return True
            
        except ProcessLookupError:
            print(f"[XAppManager] {xapp_name} já estava parado")
            self._cleanup(xapp_name)
            return True
        except Exception as e:
            print(f"[XAppManager] ERRO ao parar {xapp_name}: {e}")
            return False
    
    def is_running(self, xapp_name):
        """
        Verifica se xApp está rodando.
        
        Args:
            xapp_name: 'slicer' ou 'energy_saver'
        
        Returns:
            bool: True se está rodando
        """
        pid = self.get_pid(xapp_name)
        
        if pid is None:
            return False
        
        # Verificar se o processo é zumbi/defunct
        try:
            with open(f'/proc/{pid}/status', 'r') as f:
                status = f.read()
                # Processos zumbis têm "State: Z (zombie)"
                if 'State: Z' in status:
                    print(f"[XAppManager] {xapp_name} (PID: {pid}) é zumbi - limpando")
                    self._cleanup(xapp_name)
                    return False
        except (FileNotFoundError, PermissionError):
            pass
        
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            self._cleanup(xapp_name)
            return False
    
    def get_pid(self, xapp_name):
        """
        Obtém PID do xApp.
        
        Args:
            xapp_name: 'slicer' ou 'energy_saver'
        
        Returns:
            int: PID ou None se não está rodando
        """
        pid_path = XAPP_PID_PATHS.get(xapp_name)
        
        if pid_path and os.path.exists(pid_path):
            try:
                with open(pid_path, 'r') as f:
                    return int(f.read().strip())
            except:
                return None
        
        if xapp_name in self.processes and self.processes[xapp_name]:
            return self.processes[xapp_name].poll()
        
        return None
    
    def restart(self, xapp_name):
        """
        Reinicia um xApp.
        
        Args:
            xapp_name: 'slicer' ou 'energy_saver'
        
        Returns:
            bool: True se reiniciou com sucesso
        """
        print(f"[XAppManager] Reiniciando {xapp_name}...")
        self.stop(xapp_name)
        time.sleep(0.3)
        return self.start(xapp_name)
    
    def _cleanup(self, xapp_name):
        """Remove arquivos PID e limpa processo."""
        pid_path = XAPP_PID_PATHS.get(xapp_name)
        
        if pid_path and os.path.exists(pid_path):
            try:
                os.remove(pid_path)
            except:
                pass
        
        if xapp_name in self.processes:
            del self.processes[xapp_name]
    
    def cleanup_zombies(self):
        """Limpa TODOS os processos zumbis de xApps."""
        import subprocess
        print("[XAppManager] Limpando processos zumbis...")

        if os.environ.get("GREENRAN_CLEAN_SCOPE", "global") != "instance":
            for pattern in ['xapp_slicer', 'xapp_energy_sav', 'xapp_vehicle_control.py', 'run_slicer', 'run_energy', 'VehicleControl']:
                try:
                    subprocess.run(['pkill', '-9', '-f', pattern],
                                  capture_output=True, timeout=2)
                except Exception:
                    pass
        else:
            for xapp_name, pid_path in XAPP_PID_PATHS.items():
                if not os.path.exists(pid_path):
                    continue
                try:
                    with open(pid_path, 'r') as f:
                        pid = int(f.read().strip())
                    os.kill(pid, signal.SIGKILL)
                    print(f"[XAppManager] Processo {xapp_name} PID {pid} finalizado")
                except Exception:
                    pass
        
        # Remover todos os PID files antigos
        for xapp_name, pid_path in XAPP_PID_PATHS.items():
            if os.path.exists(pid_path):
                try:
                    os.remove(pid_path)
                    print(f"[XAppManager] PID file {pid_path} removido")
                except:
                    pass
        
        print("[XAppManager] Limpeza de zumbis concluída")
    
    def stop_all(self):
        """Para todos os xApps."""
        print("[XAppManager] Parando todos os xApps...")
        for xapp_name in list(XAPP_PATHS.keys()):
            self.stop(xapp_name)
    
    def get_status(self):
        """
        Retorna status de todos os xApps.
        
        Returns:
            dict: Status de cada xApp
        """
        status = {}
        for xapp_name in XAPP_PATHS.keys():
            status[xapp_name] = {
                'running': self.is_running(xapp_name),
                'pid': self.get_pid(xapp_name)
            }
        return status
    
    def wait_for_ready(self, xapp_name, timeout=10):
        """
        Espera xApp ficar pronto (conectar ao RIC).
        
        Args:
            xapp_name: 'slicer' ou 'energy_saver'
            timeout: Segundos máximo para esperar
        
        Returns:
            bool: True se ficou pronto a tempo
        """
        print(f"[XAppManager] Aguardando {xapp_name} ficar pronto...")
        
        log_path = XAPP_LOG_PATHS.get(xapp_name)
        start = time.time()
        
        while time.time() - start < timeout:
            if os.path.exists(log_path):
                try:
                    with open(log_path, 'r') as f:
                        content = f.read()
                        if 'Connected' in content or 'registered' in content.lower() or 'ready' in content.lower():
                            print(f"[XAppManager] {xapp_name} pronto!")
                            return True
                except:
                    pass
            
            if not self.is_running(xapp_name):
                print(f"[XAppManager] {xapp_name} morreu!")
                return False
            
            time.sleep(0.2)
        
        print(f"[XAppManager] Timeout esperando {xapp_name}")
        return False


if __name__ == '__main__':
    xm = XAppManager()
    
    print("\n=== XAppManager - Teste ===\n")
    
    print("Status inicial:")
    for name, info in xm.get_status().items():
        print(f"  {name}: {'RODANDO' if info['running'] else 'PARADO'} (PID: {info['pid']})")
    
    print("\nIniciando Slicer...")
    xm.start("slicer")
    
    print("\nStatus após iniciar Slicer:")
    for name, info in xm.get_status().items():
        print(f"  {name}: {'RODANDO' if info['running'] else 'PARADO'} (PID: {info['pid']})")
    
    print("\nParando Slicer...")
    xm.stop("slicer")
    
    print("\nStatus final:")
    for name, info in xm.get_status().items():
        print(f"  {name}: {'RODANDO' if info['running'] else 'PARADO'} (PID: {info['pid']})")
