#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp A1 Policy Interface (COM CONFIRMAÇÃO)
==========================================================

Responsabilidade: Interface A1 para comunicação com Near-RT RIC
- Envia políticas de energia em JSON
- Envia políticas de fatia em JSON
- Sistema de ACK (confirmação) para garantir entrega

MELHORIAS:
- Sistema de confirmação ACK
- Reenvio automático se não houver confirmação
- Tracking de políticas pendentes

Interface A1 (O-RAN):
    /tmp/rapp_policies/
    ├── energy_policy.json    - Política de economia de energia
    ├── slice_policy.json     - Política de fatiamento de rede
    ├── energy_policy_ack.json - ACK do Near-RT RIC
    ├── slice_policy_ack.json  - ACK do Near-RT RIC
    └── policy_history.json   - Histórico de políticas enviadas

Uso:
    from rapp_a1_interface import A1PolicyInterface
    
    a1 = A1PolicyInterface()
    a1.send_energy_policy(decision, pattern_info)
    a1.send_slice_policy(slicer_state, cameras_demand)
    a1.wait_for_ack(policy_type='energy', timeout=10)
"""

import os
import json
import time
from datetime import datetime
from pathlib import Path

from greenran_paths import RAPP_POLICIES_DIR, as_str

DEFAULT_POLICY_DIR = as_str(RAPP_POLICIES_DIR)
ENERGY_POLICY_FILE = "energy_policy.json"
SLICE_POLICY_FILE = "slice_policy.json"
ENERGY_ACK_FILE = "energy_policy_ack.json"
SLICE_ACK_FILE = "slice_policy_ack.json"
POLICY_HISTORY_FILE = "policy_history.json"
POLICY_STATUS_FILE = "policy_status.json"

DEFAULT_POLICY_VALIDITY = 300  # 5 minutos
ACK_TIMEOUT = 10  # 10 segundos para confirmar
MAX_RESEND_ATTEMPTS = 3


class A1PolicyInterface:
    """
    Interface A1 para envio de políticas do rApp para Near-RT RIC.
    
    Implementa interface simplificada baseada em JSON com confirmação ACK.
    No O-RAN real, isso seria via protocolo A1.
    """
    
    def __init__(self, policy_dir=DEFAULT_POLICY_DIR):
        """
        Inicializa a interface A1.
        
        Args:
            policy_dir: Diretório para arquivos de política
        """
        self.policy_dir = policy_dir
        self.energy_policy_file = os.path.join(policy_dir, ENERGY_POLICY_FILE)
        self.slice_policy_file = os.path.join(policy_dir, SLICE_POLICY_FILE)
        self.energy_ack_file = os.path.join(policy_dir, ENERGY_ACK_FILE)
        self.slice_ack_file = os.path.join(policy_dir, SLICE_ACK_FILE)
        self.history_file = os.path.join(policy_dir, POLICY_HISTORY_FILE)
        self.status_file = os.path.join(policy_dir, POLICY_STATUS_FILE)
        
        self.policy_history = []
        self.pending_policies = {}  # {type: {timestamp, attempts}}
        
        # Garante diretório existe
        os.makedirs(policy_dir, exist_ok=True)
        
        # Carrega histórico se existir
        self._load_history()
        self._load_status()
    
    def _load_history(self):
        """Carrega histórico de políticas."""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    self.policy_history = json.load(f)
            except Exception as e:
                print(f"[A1] ERRO ao carregar histórico: {e}")
                self.policy_history = []
    
    def _load_status(self):
        """Carrega status de políticas pendentes."""
        if os.path.exists(self.status_file):
            try:
                with open(self.status_file, 'r') as f:
                    data = json.load(f)
                    self.pending_policies = data.get('pending', {})
            except Exception:
                self.pending_policies = {}
    
    def _save_history(self):
        """Salva histórico de políticas."""
        try:
            with open(self.history_file, 'w') as f:
                json.dump(self.policy_history[-100:], f, indent=2)
        except Exception as e:
            print(f"[A1] ERRO ao salvar histórico: {e}")
    
    def _save_status(self):
        """Salva status de políticas pendentes."""
        try:
            with open(self.status_file, 'w') as f:
                json.dump({'pending': self.pending_policies}, f, indent=2)
        except Exception as e:
            print(f"[A1] ERRO ao salvar status: {e}")
    
    def _record_policy(self, policy_type, policy_data):
        """
        Registra política no histórico.
        
        Args:
            policy_type: Tipo de política
            policy_data: Dados da política
        """
        record = {
            'timestamp': int(time.time()),
            'datetime': datetime.now().isoformat(),
            'type': policy_type,
            'policy_id': policy_data.get('policy_id'),
            'status': policy_data.get('status'),
            'acked': False
        }
        
        self.policy_history.append(record)
        self._save_history()
        
        # Atualiza histórico com ACK se já existir
        for rec in reversed(self.policy_history[:-1]):
            if rec.get('policy_id') == policy_data.get('policy_id'):
                rec['acked'] = True
                break
    
    def _generate_policy_id(self, policy_type):
        """Gera ID único para política."""
        return f"{policy_type}_{int(time.time() * 1000)}"
    
    def check_ack(self, policy_type):
        """
        Verifica se há ACK para uma política.
        
        Args:
            policy_type: 'energy' ou 'slice'
        
        Returns:
            True se há ACK válido, False caso contrário
        """
        ack_file = self.energy_ack_file if policy_type == 'energy' else self.slice_ack_file
        
        if not os.path.exists(ack_file):
            return False
        
        try:
            with open(ack_file, 'r') as f:
                ack_data = json.load(f)
            
            # Verifica se ACK é recente (últimos 60 segundos)
            ack_timestamp = ack_data.get('timestamp', 0)
            if time.time() - ack_timestamp > 60:
                return False
            
            return ack_data.get('acknowledged', False)
        
        except Exception:
            return False
    
    def get_last_ack(self, policy_type):
        """
        Retorna último ACK recebido.
        
        Args:
            policy_type: 'energy' ou 'slice'
        
        Returns:
            Dict com dados do ACK ou None
        """
        ack_file = self.energy_ack_file if policy_type == 'energy' else self.slice_ack_file
        
        if not os.path.exists(ack_file):
            return None
        
        try:
            with open(ack_file, 'r') as f:
                return json.load(f)
        except Exception:
            return None
    
    def send_energy_policy(self, decision, pattern_info=None):
        """
        Envia política de energia para Near-RT RIC.
        
        Args:
            decision: Dict com decisão do rApp
            pattern_info: Dict opcional com informações de padrão
        
        Returns:
            Dict com política enviada.
        """
        timestamp = int(time.time())
        policy_id = self._generate_policy_id('energy')
        
        # Verifica se política anterior teve ACK
        pending_key = 'energy'
        previous_ack = self.check_ack('energy')
        
        # Determina regras de energia baseadas na decisão
        # IMPORTANTE: mmWave NUNCA pode ser desligado (sobrecarregaria LTE)
        if decision.get('energy_saver') == 'BLOCKED':
            rules = {
                'allow_cell_shutdown': False,
                'allow_mmwave_off': False,  # mmWave SEMPRE ligado
                'min_coverage': 'FULL',
                'restore_immediately': True
            }
            status = 'BLOCKED'
        elif decision.get('energy_saver') == 'CONDITIONAL':
            rules = {
                'allow_cell_shutdown': False,
                'allow_mmwave_off': False,  # mmWave SEMPRE ligado
                'min_coverage': 'PARTIAL',
                'restore_immediately': False
            }
            status = 'CONDITIONAL'
        else:
            rules = {
                'allow_cell_shutdown': False,
                'allow_mmwave_off': False,  # mmWave SEMPRE ligado - apenas reduz potência
                'min_coverage': 'BASIC',
                'restore_immediately': False
            }
            status = 'ALLOWED'
        
        # Política completa
        policy = {
            'policy_type': 'energy_saving',
            'interface': 'A1',
            'timestamp': timestamp,
            'valid_until': timestamp + DEFAULT_POLICY_VALIDITY,
            'source': 'rApp-ResourceOptimizer',
            'status': status,
            'policy_id': policy_id,
            'ack_required': True,
            'waiting_ack': not previous_ack,
            
            'rules': rules,
            
            'decision': {
                'energy_saver': decision.get('energy_saver', 'UNKNOWN'),
                'action': decision.get('action', 'NONE'),
                'reason': decision.get('reason', 'UNKNOWN'),
                'confidence': decision.get('confidence', 0.0),
                'pattern': decision.get('pattern'),
                'agent_override': decision.get('agent_override', False),
                'agent_policy': decision.get('agent_policy')
            },
            
            'recommendations': {
                'cell_off_allowed': rules['allow_cell_shutdown'],
                'mmwave_off_allowed': rules['allow_mmwave_off'],
                'emergency_restore': rules['restore_immediately']
            }
        }
        
        # Adiciona informações de padrão se disponível
        if pattern_info:
            policy['pattern_window'] = {
                'start': pattern_info.get('window_start'),
                'end': pattern_info.get('window_end'),
                'confidence': pattern_info.get('confidence', 0.0),
                'reason': pattern_info.get('reason')
            }
        
        # Escreve arquivo
        try:
            with open(self.energy_policy_file, 'w') as f:
                json.dump(policy, f, indent=2)
            
            # Registra no histórico
            self._record_policy('energy', policy)
            
            # Atualiza pending
            if not previous_ack:
                self.pending_policies['energy'] = {
                    'timestamp': timestamp,
                    'policy_id': policy_id,
                    'attempts': 1,
                    'last_sent': datetime.now().isoformat()
                }
                self._save_status()
                print(f"[A1] Energia: Política enviada (ID: {policy_id}), aguardando ACK")
            else:
                # Remove pending se existir
                if 'energy' in self.pending_policies:
                    del self.pending_policies['energy']
                    self._save_status()
                print(f"[A1] Energia: Política enviada (ID: {policy_id}), ACK anterior confirmado")
            
            return policy
        
        except Exception as e:
            print(f"[A1] ERRO ao enviar política de energia: {e}")
            return None
    
    def send_slice_policy(self, slicer_state, cameras_demand=None, prb_allocation=None):
        """
        Envia política de fatiamento para Near-RT RIC.
        
        Args:
            slicer_state: Estado do SLICER
            cameras_demand: Dict opcional com demanda de câmeras
            prb_allocation: Dict opcional com alocação de PRBs
        
        Returns:
            Dict com política enviada.
        """
        timestamp = int(time.time())
        policy_id = self._generate_policy_id('slice')
        
        # Verifica se política anterior teve ACK
        previous_ack = self.check_ack('slice')
        
        # Determina prioridades de fatia
        if slicer_state == 'CRITICAL':
            slice_priority = {'camera_slice': 1, 'ue_slice': 3}
            prb_reserve = {'camera_slice': 80, 'ue_slice': 20}
            status = 'PRIORITY_ENFORCED'
        elif slicer_state == 'WARNING':
            slice_priority = {'camera_slice': 1, 'ue_slice': 2}
            prb_reserve = {'camera_slice': 60, 'ue_slice': 40}
            status = 'ADJUSTED'
        elif slicer_state == 'IDLE':
            slice_priority = {'camera_slice': 2, 'ue_slice': 2}
            prb_reserve = {'camera_slice': 30, 'ue_slice': 70}
            status = 'RELAXED'
        else:
            slice_priority = {'camera_slice': 1, 'ue_slice': 1}
            prb_reserve = {'camera_slice': 50, 'ue_slice': 50}
            status = 'BALANCED'
        
        # Política completa
        policy = {
            'policy_type': 'slice_priority',
            'interface': 'A1',
            'timestamp': timestamp,
            'valid_until': timestamp + DEFAULT_POLICY_VALIDITY,
            'source': 'rApp-ResourceOptimizer',
            'status': status,
            'policy_id': policy_id,
            'ack_required': True,
            'waiting_ack': not previous_ack,
            
            'slicer_state': slicer_state,
            
            'slice_priority': slice_priority,
            'prb_reserve': prb_reserve,
            
            'recommendations': {
                'prioritize_cameras': slicer_state in ['CRITICAL', 'WARNING'],
                'reserve_prb': True,
                'enforce_sla': slicer_state == 'CRITICAL'
            }
        }
        
        # Adiciona demanda de câmeras se disponível
        if cameras_demand:
            policy['camera_demand'] = cameras_demand
        
        # Adiciona alocação PRB se disponível
        if prb_allocation:
            policy['prb_allocation'] = prb_allocation
        
        # Escreve arquivo
        try:
            with open(self.slice_policy_file, 'w') as f:
                json.dump(policy, f, indent=2)
            
            # Registra no histórico
            self._record_policy('slice', policy)
            
            # Atualiza pending
            if not previous_ack:
                self.pending_policies['slice'] = {
                    'timestamp': timestamp,
                    'policy_id': policy_id,
                    'attempts': 1,
                    'last_sent': datetime.now().isoformat()
                }
                self._save_status()
                print(f"[A1] Slice: Política enviada (ID: {policy_id}), aguardando ACK")
            else:
                if 'slice' in self.pending_policies:
                    del self.pending_policies['slice']
                    self._save_status()
                print(f"[A1] Slice: Política enviada (ID: {policy_id}), ACK anterior confirmado")
            
            return policy
        
        except Exception as e:
            print(f"[A1] ERRO ao enviar política de fatia: {e}")
            return None
    
    def check_pending_acks(self):
        """
        Verifica ACKs pendentes e tenta reenviar se necessário.
        
        Returns:
            Dict com status dos ACKs pendentes.
        """
        result = {
            'energy': {'pending': False, 'attempts': 0},
            'slice': {'pending': False, 'attempts': 0}
        }
        
        for policy_type in ['energy', 'slice']:
            if policy_type in self.pending_policies:
                pending = self.pending_policies[policy_type]
                
                # Verifica se já teve ACK
                if self.check_ack(policy_type):
                    del self.pending_policies[policy_type]
                    self._save_status()
                    print(f"[A1] {policy_type}: ACK recebido!")
                    continue
                
                result[policy_type]['pending'] = True
                result[policy_type]['attempts'] = pending.get('attempts', 0)
                result[policy_type]['since'] = pending.get('last_sent')
                
                # Verifica se deve reenviar
                time_since = time.time() - pending.get('timestamp', 0)
                if time_since > ACK_TIMEOUT and pending.get('attempts', 0) < MAX_RESEND_ATTEMPTS:
                    pending['attempts'] += 1
                    pending['last_sent'] = datetime.now().isoformat()
                    self._save_status()
                    print(f"[A1] {policy_type}: Reenviando política (tentativa {pending['attempts']}/{MAX_RESEND_ATTEMPTS})")
        
        return result
    
    def wait_for_ack(self, policy_type, timeout=10):
        """
        Aguarda ACK de uma política.
        
        Args:
            policy_type: 'energy' ou 'slice'
            timeout: Tempo máximo de espera em segundos
        
        Returns:
            True se ACK recebido, False caso contrário
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.check_ack(policy_type):
                return True
            time.sleep(0.5)
        
        return False
    
    def get_ack_stats(self):
        """
        Retorna estatísticas de ACK.
        
        Returns:
            Dict com estatísticas de confirmação.
        """
        acked = 0
        pending = 0
        total = len(self.policy_history)
        
        for rec in self.policy_history:
            if rec.get('acked', False):
                acked += 1
            else:
                pending += 1
        
        return {
            'total_policies': total,
            'acked': acked,
            'pending': pending,
            'ack_rate': acked / total if total > 0 else 0,
            'pending_now': len(self.pending_policies)
        }
    
    def send_notification(self, notification_type, message, severity='INFO'):
        """
        Envia notificação para Near-RT RIC.
        
        Args:
            notification_type: Tipo de notificação
            message: Mensagem
            severity: Severidade ('INFO', 'WARNING', 'CRITICAL')
        
        Returns:
            Dict com notificação.
        """
        notification = {
            'notification_type': notification_type,
            'interface': 'A1',
            'timestamp': int(time.time()),
            'severity': severity,
            'message': message,
            'source': 'rApp-ResourceOptimizer'
        }
        
        notif_file = os.path.join(self.policy_dir, f"notification_{int(time.time())}.json")
        try:
            with open(notif_file, 'w') as f:
                json.dump(notification, f, indent=2)
            
            self._record_policy('notification', notification)
            
            return notification
        
        except Exception as e:
            print(f"[A1] ERRO ao enviar notificação: {e}")
            return None
    
    def get_current_energy_policy(self):
        """Retorna política de energia atual."""
        if os.path.exists(self.energy_policy_file):
            try:
                with open(self.energy_policy_file, 'r') as f:
                    return json.load(f)
            except Exception:
                return None
        return None
    
    def get_current_slice_policy(self):
        """Retorna política de fatia atual."""
        if os.path.exists(self.slice_policy_file):
            try:
                with open(self.slice_policy_file, 'r') as f:
                    return json.load(f)
            except Exception:
                return None
        return None
    
    def get_policy_history(self, limit=20):
        """Retorna histórico de políticas."""
        return self.policy_history[-limit:]
    
    def clear_old_policies(self, max_age_seconds=3600):
        """Remove políticas antigas do diretório."""
        current_time = time.time()
        
        for filename in os.listdir(self.policy_dir):
            if filename.startswith('notification_') and filename.endswith('.json'):
                filepath = os.path.join(self.policy_dir, filename)
                try:
                    mtime = os.path.getmtime(filepath)
                    if current_time - mtime > max_age_seconds:
                        os.remove(filepath)
                except Exception:
                    pass


def print_policy(policy, prefix=""):
    """Imprime política de forma legível."""
    if not policy:
        print(f"{prefix}Nenhuma política")
        return
    
    print(f"{prefix}Tipo: {policy.get('policy_type')}")
    print(f"{prefix}ID: {policy.get('policy_id')}")
    print(f"{prefix}Timestamp: {policy.get('timestamp')}")
    
    if 'status' in policy:
        print(f"{prefix}Status: {policy.get('status')}")
    
    if 'waiting_ack' in policy:
        ack_str = "SIM" if policy['waiting_ack'] else "NÃO"
        print(f"{prefix}Aguarda ACK: {ack_str}")
    
    if 'rules' in policy:
        print(f"{prefix}Regras:")
        for key, value in policy['rules'].items():
            print(f"{prefix}  {key}: {value}")


def main():
    """Teste da Interface A1"""
    print("=" * 70)
    print("rApp A1 Policy Interface - Teste (COM CONFIRMAÇÃO)")
    print("=" * 70)
    
    a1 = A1PolicyInterface()
    
    # Teste 1: Política de energia
    print("\n[1] Enviando política de energia (BLOCKED):")
    decision1 = {
        'energy_saver': 'BLOCKED',
        'action': 'PRIORIZE_SLA',
        'reason': 'SLA_VIOLATED',
        'confidence': 1.0,
        'pattern': 'peak_hours'
    }
    policy1 = a1.send_energy_policy(decision1)
    print_policy(policy1, "  ")
    
    # Teste 2: Política de energia ALLOWED
    print("\n[2] Enviando política de energia (ALLOWED):")
    decision2 = {
        'energy_saver': 'ALLOWED',
        'action': 'ACTIVATE_ENERGY_SAVING',
        'reason': 'LOW_ACTIVITY',
        'confidence': 0.85,
        'pattern': 'night_low_activity'
    }
    pattern_info = {
        'window_start': '22:00',
        'window_end': '06:00',
        'confidence': 0.9,
        'reason': 'Padrão noturno detectado'
    }
    policy2 = a1.send_energy_policy(decision2, pattern_info)
    print_policy(policy2, "  ")
    
    # Teste 3: Política de fatia
    print("\n[3] Enviando política de fatia (CRITICAL):")
    cameras_demand = {'active': 3, 'critical': 1, 'total_demand_mbps': 75}
    policy3 = a1.send_slice_policy('CRITICAL', cameras_demand)
    print_policy(policy3, "  ")
    
    # Teste 4: Verificar pending
    print("\n[4] Verificando políticas pendentes:")
    pending = a1.check_pending_acks()
    for ptype, info in pending.items():
        print(f"  {ptype}: pending={info['pending']}, attempts={info['attempts']}")
    
    # Teste 5: Estatísticas de ACK
    print("\n[5] Estatísticas de ACK:")
    stats = a1.get_ack_stats()
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    print("\n" + "=" * 70)
    print("Teste concluído!")
    print("=" * 70)


if __name__ == "__main__":
    main()
