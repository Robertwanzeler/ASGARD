#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp A1 Policy Interface
=========================================

Responsabilidade: Interface A1 para comunicação com Near-RT RIC
- Envia políticas de energia em JSON
- Envia políticas de fatia em JSON
- Notificações de mudança de política

Interface A1 (O-RAN):
    /tmp/rapp_policies/
    ├── energy_policy.json    - Política de economia de energia
    ├── slice_policy.json     - Política de fatiamento de rede
    └── policy_history.json   - Histórico de políticas enviadas

Uso:
    from rapp_a1_interface import A1PolicyInterface
    
    a1 = A1PolicyInterface()
    a1.send_energy_policy(decision, pattern_info)
    a1.send_slice_policy(slicer_state, cameras_demand)
"""

import os
import json
import time
from datetime import datetime
from pathlib import Path

DEFAULT_POLICY_DIR = "/tmp/rapp_policies/"
ENERGY_POLICY_FILE = "energy_policy.json"
SLICE_POLICY_FILE = "slice_policy.json"
POLICY_HISTORY_FILE = "policy_history.json"

# Validade padrão das políticas (segundos)
DEFAULT_POLICY_VALIDITY = 300  # 5 minutos


class A1PolicyInterface:
    """
    Interface A1 para envio de políticas do rApp para Near-RT RIC.
    
    Implementa interface simplificada baseada em JSON.
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
        self.history_file = os.path.join(policy_dir, POLICY_HISTORY_FILE)
        
        self.policy_history = []
        
        # Garante diretório existe
        os.makedirs(policy_dir, exist_ok=True)
        
        # Carrega histórico se existir
        self._load_history()
    
    def _load_history(self):
        """Carrega histórico de políticas."""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    self.policy_history = json.load(f)
            except Exception as e:
                print(f"[A1] ERRO ao carregar histórico: {e}")
                self.policy_history = []
    
    def _save_history(self):
        """Salva histórico de políticas."""
        try:
            with open(self.history_file, 'w') as f:
                json.dump(self.policy_history[-100:], f, indent=2)  # Mantém últimos 100
        except Exception as e:
            print(f"[A1] ERRO ao salvar histórico: {e}")
    
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
            'data': policy_data
        }
        
        self.policy_history.append(record)
        self._save_history()
    
    def send_energy_policy(self, decision, pattern_info=None):
        """
        Envia política de energia para Near-RT RIC.
        
        Args:
            decision: Dict com decisão do rApp:
                - energy_saver: 'BLOCKED', 'ALLOWED', 'CONDITIONAL'
                - reason: Motivo da decisão
                - confidence: Confiança (0-1)
                - pattern: Tipo de padrão detectado
                - action: Ação recomendada
            
            pattern_info: Dict opcional com informações de padrão:
                - window_start: Início da janela de economia
                - window_end: Fim da janela de economia
                - confidence: Confiança no padrão
        
        Returns:
            Dict com política enviada.
        """
        timestamp = int(time.time())
        
        # Determina regras de energia baseadas na decisão
        if decision.get('energy_saver') == 'BLOCKED':
            rules = {
                'allow_cell_shutdown': False,
                'allow_mmwave_off': False,
                'min_coverage': 'FULL',
                'restore_immediately': True
            }
            status = 'BLOCKED'
        elif decision.get('energy_saver') == 'CONDITIONAL':
            rules = {
                'allow_cell_shutdown': False,
                'allow_mmwave_off': True,
                'min_coverage': 'PARTIAL',
                'restore_immediately': False
            }
            status = 'CONDITIONAL'
        else:  # ALLOWED
            rules = {
                'allow_cell_shutdown': True,
                'allow_mmwave_off': True,
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
            
            return policy
        
        except Exception as e:
            print(f"[A1] ERRO ao enviar política de energia: {e}")
            return None
    
    def send_slice_policy(self, slicer_state, cameras_demand=None, prb_allocation=None):
        """
        Envia política de fatiamento para Near-RT RIC.
        
        Args:
            slicer_state: Estado do SLICER ('NORMAL', 'WARNING', 'CRITICAL', 'IDLE')
            cameras_demand: Dict opcional com demanda de câmeras
            prb_allocation: Dict opcional com alocação de PRBs
        
        Returns:
            Dict com política enviada.
        """
        timestamp = int(time.time())
        
        # Determina prioridades de fatia
        if slicer_state == 'CRITICAL':
            slice_priority = {
                'camera_slice': 1,  # Máxima prioridade
                'ue_slice': 3
            }
            prb_reserve = {
                'camera_slice': 80,  # 80% para câmeras
                'ue_slice': 20
            }
            status = 'PRIORITY_ENFORCED'
        elif slicer_state == 'WARNING':
            slice_priority = {
                'camera_slice': 1,
                'ue_slice': 2
            }
            prb_reserve = {
                'camera_slice': 60,
                'ue_slice': 40
            }
            status = 'ADJUSTED'
        elif slicer_state == 'IDLE':
            slice_priority = {
                'camera_slice': 2,
                'ue_slice': 2
            }
            prb_reserve = {
                'camera_slice': 30,
                'ue_slice': 70
            }
            status = 'RELAXED'
        else:  # NORMAL
            slice_priority = {
                'camera_slice': 1,
                'ue_slice': 1
            }
            prb_reserve = {
                'camera_slice': 50,
                'ue_slice': 50
            }
            status = 'BALANCED'
        
        # Política completa
        policy = {
            'policy_type': 'slice_priority',
            'interface': 'A1',
            'timestamp': timestamp,
            'valid_until': timestamp + DEFAULT_POLICY_VALIDITY,
            'source': 'rApp-ResourceOptimizer',
            'status': status,
            
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
            
            return policy
        
        except Exception as e:
            print(f"[A1] ERRO ao enviar política de fatia: {e}")
            return None
    
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
        
        # Escreve arquivo de notificação
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
        """
        Retorna política de energia atual.
        
        Returns:
            Dict com política ou None.
        """
        if os.path.exists(self.energy_policy_file):
            try:
                with open(self.energy_policy_file, 'r') as f:
                    return json.load(f)
            except Exception:
                return None
        return None
    
    def get_current_slice_policy(self):
        """
        Retorna política de fatia atual.
        
        Returns:
            Dict com política ou None.
        """
        if os.path.exists(self.slice_policy_file):
            try:
                with open(self.slice_policy_file, 'r') as f:
                    return json.load(f)
            except Exception:
                return None
        return None
    
    def get_policy_history(self, limit=20):
        """
        Retorna histórico de políticas.
        
        Args:
            limit: Número máximo de registros
        
        Returns:
            List de políticas.
        """
        return self.policy_history[-limit:]
    
    def clear_old_policies(self, max_age_seconds=3600):
        """
        Remove políticas antigas do diretório.
        
        Args:
            max_age_seconds: Idade máxima em segundos
        """
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
    print(f"{prefix}Timestamp: {policy.get('timestamp')}")
    print(f"{prefix}Validade: {policy.get('valid_until')}")
    
    if 'status' in policy:
        print(f"{prefix}Status: {policy.get('status')}")
    
    if 'rules' in policy:
        print(f"{prefix}Regras:")
        for key, value in policy['rules'].items():
            print(f"{prefix}  {key}: {value}")
    
    if 'decision' in policy:
        print(f"{prefix}Decisão:")
        for key, value in policy['decision'].items():
            print(f"{prefix}  {key}: {value}")
    
    if 'slice_priority' in policy:
        print(f"{prefix}Prioridade de Fatia:")
        for key, value in policy['slice_priority'].items():
            print(f"{prefix}  {key}: {value}")


def main():
    """Teste da Interface A1"""
    print("=" * 70)
    print("rApp A1 Policy Interface - Teste")
    print("=" * 70)
    
    # Cria interface
    a1 = A1PolicyInterface()
    
    # Teste 1: Política de energia BLOCKED
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
    
    # Teste 3: Política de fatia CRITICAL
    print("\n[3] Enviando política de fatia (CRITICAL):")
    cameras_demand = {
        'active': 3,
        'critical': 1,
        'total_demand_mbps': 75
    }
    policy3 = a1.send_slice_policy('CRITICAL', cameras_demand)
    print_policy(policy3, "  ")
    
    # Teste 4: Política de fatia NORMAL
    print("\n[4] Enviando política de fatia (NORMAL):")
    policy4 = a1.send_slice_policy('NORMAL')
    print_policy(policy4, "  ")
    
    # Teste 5: Notificação
    print("\n[5] Enviando notificação:")
    notif = a1.send_notification('SLA_ALERT', 'SLA violation detected', 'WARNING')
    print(f"  Tipo: {notif.get('notification_type')}")
    print(f"  Severidade: {notif.get('severity')}")
    print(f"  Mensagem: {notif.get('message')}")
    
    # Teste 6: Histórico
    print("\n[6] Histórico de políticas:")
    history = a1.get_policy_history()
    for i, h in enumerate(history):
        print(f"  {i+1}. [{h['type']}] {h['datetime']}")
    
    # Teste 7: Recupera políticas atuais
    print("\n[7] Políticas atuais:")
    current_energy = a1.get_current_energy_policy()
    print("  Energia atual:", "BLOCKED" if current_energy.get('status') == 'BLOCKED' else "ALLOWED")
    
    current_slice = a1.get_current_slice_policy()
    print("  Fatia atual:", current_slice.get('slicer_state'))
    
    print("\n" + "=" * 70)
    print("Teste concluído!")
    print("=" * 70)


if __name__ == "__main__":
    main()
