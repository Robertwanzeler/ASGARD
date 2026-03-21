#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp Agent-Al-OpenRAN Interface
==============================================

Responsabilidade: Interface para tradução de intenções de rede
- Recebe intenções de alto nível do Agent-Al-Usuário
- Traduz para políticas técnicas para os xApps
- Suporta múltiplos templates de intenção

Interface:
    /tmp/agent_intent.json - Intenções recebidas do Agent-Al
    /tmp/rapp_agent_status.json - Status de resposta do rApp

Templates de Intenção:
    - PRIORIZAR_VIGILANCIA: Garante recursos para câmeras
    - ECONOMIA_NOTURNA: Permite desligamento noturno
    - BALANCEADO: Equilibra SLA e energia
    - MAXIMA_ECONOMIA: Prioriza economia sobre tudo

Uso:
    from rapp_agent_openran import AgentOpenRAN
    
    agent = AgentOpenRAN()
    intent = agent.read_intent()
    policy = agent.translate_to_policy(intent)
    agent.apply_policy(current_decision, policy)
"""

import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_INTENT_FILE = "/tmp/agent_intent.json"
DEFAULT_STATUS_FILE = "/tmp/rapp_agent_status.json"

# Templates de intenção do O-RAN
INTENT_TEMPLATES = {
    'PRIORIZAR_VIGILANCIA': {
        'name': 'Priorizar Vigilância',
        'description': 'Garante recursos máximos para câmeras de segurança',
        'priority': 'CRITICAL',
        'slicer_action': 'ALLOCATE_MAX_PRB',
        'min_prb_for_cameras': 80,  # 80%
        'block_energy_save': True,
        'keep_all_resources': True,
        'response_time': 'IMMEDIATE',
        'valid_duration_minutes': 60,
        'parameters': {
            'camera_priority': 'MAX',
            'ue_priority': 'MIN',
            'sla_threshold_ms': 50,  # Mais estrito
            'allow_energy_save': False
        }
    },
    
    'ECONOMIA_NOTURNA': {
        'name': 'Economia Noturna',
        'description': 'Permite desligamento de células durante a noite',
        'priority': 'NORMAL',
        'time_window': {
            'start': '22:00',
            'end': '06:00',
            'days': [0, 1, 2, 3, 4, 5, 6]  # Todos os dias
        },
        'allow_cell_shutdown': True,
        'allow_mmwave_off': True,
        'keep_min_cameras': 1,
        'response_time': 'SCHEDULED',
        'valid_duration_minutes': 480,  # 8 horas
        'parameters': {
            'min_coverage': 'BASIC',
            'emergency_restore': True,
            'camera_priority': 'PRESERVE_ONE'
        }
    },
    
    'BALANCEADO': {
        'name': 'Modo Balanceado',
        'description': 'Equilibra SLA e economia de energia',
        'priority': 'NORMAL',
        'slicer_action': 'MAINTAIN',
        'min_prb_for_cameras': 50,  # 50%
        'block_energy_save': False,
        'allow_energy_save': True,
        'response_time': 'ADAPTIVE',
        'valid_duration_minutes': 120,
        'parameters': {
            'sla_threshold_ms': 100,
            'energy_save_threshold_ms': 20,
            'min_cameras_active': 1
        }
    },
    
    'MAXIMA_ECONOMIA': {
        'name': 'Máxima Economia',
        'description': 'Prioriza economia de energia sobre todas as outras métricas',
        'priority': 'LOW',
        'allow_cell_shutdown': True,
        'allow_mmwave_off': True,
        'keep_min_cameras': 0,
        'block_energy_save': False,
        'response_time': 'OPPORTUNISTIC',
        'valid_duration_minutes': 240,
        'parameters': {
            'aggressive_shutdown': True,
            'min_coverage': 'EMERGENCY_ONLY',
            'sla_threshold_ms': 200,  # SLA mais tolerante
            'restore_on_sla_breach': True
        }
    },
    
    'MODO_EMERGENCIA': {
        'name': 'Modo Emergência',
        'description': 'Ativa todos os recursos disponíveis',
        'priority': 'MAXIMUM',
        'slicer_action': 'ALLOCATE_MAX_PRB',
        'min_prb_for_cameras': 100,  # 100%
        'block_energy_save': True,
        'keep_all_resources': True,
        'response_time': 'IMMEDIATE',
        'valid_duration_minutes': 30,
        'parameters': {
            'camera_priority': 'MAX',
            'emergency_mode': True,
            'disable_all_shutdown': True
        }
    },
    
    'HORARIO_COMERCIAL': {
        'name': 'Horário Comercial',
        'description': 'Configura para horário de trabalho (08:00-18:00)',
        'priority': 'HIGH',
        'time_window': {
            'start': '08:00',
            'end': '18:00',
            'days': [0, 1, 2, 3, 4]  # Segunda a sexta
        },
        'min_prb_for_cameras': 60,
        'block_energy_save': True,
        'keep_all_resources': True,
        'response_time': 'SCHEDULED',
        'valid_duration_minutes': 600,
        'parameters': {
            'camera_priority': 'HIGH',
            'ue_priority': 'HIGH',
            'sla_threshold_ms': 75
        }
    }
}


class AgentOpenRAN:
    """
    Interface Agent-Al-OpenRAN para tradução de intenções.
    
    Recebe intenções de alto nível e traduz para políticas
    técnicas que o rApp pode aplicar aos xApps.
    """
    
    def __init__(self, intent_file=DEFAULT_INTENT_FILE, status_file=DEFAULT_STATUS_FILE):
        """
        Inicializa a interface do Agent.
        
        Args:
            intent_file: Caminho do arquivo de intenção
            status_file: Caminho do arquivo de status
        """
        self.intent_file = intent_file
        self.status_file = status_file
        self.current_intent = None
        self.current_policy = None
        self.intent_history = []
        
        # Garante diretórios
        os.makedirs(os.path.dirname(intent_file) or '/tmp', exist_ok=True)
        os.makedirs(os.path.dirname(status_file) or '/tmp', exist_ok=True)
    
    def read_intent(self):
        """
        Lê intenção do arquivo JSON.
        
        Returns:
            Dict com a intenção ou None se não houver intenção.
        """
        if not os.path.exists(self.intent_file):
            return None
        
        try:
            with open(self.intent_file, 'r') as f:
                intent = json.load(f)
            
            # Valida intenção
            if 'intent' not in intent:
                return None
            
            self.current_intent = intent
            return intent
        
        except Exception as e:
            print(f"[Agent-Al] ERRO ao ler intenção: {e}")
            return None
    
    def translate_to_policy(self, intent):
        """
        Traduz intenção para política técnica.
        
        Args:
            intent: Dict com campo 'intent' contendo o tipo de intenção
        
        Returns:
            Dict com política técnica para o rApp.
        """
        if intent is None:
            return None
        
        intent_type = intent.get('intent', 'UNKNOWN')
        
        # Busca template
        template = INTENT_TEMPLATES.get(intent_type)
        if not template:
            print(f"[Agent-Al] Intenção desconhecida: {intent_type}")
            return None
        
        # Copia template como base
        policy = template.copy()
        
        # Sobrescreve com parâmetros específicos da intenção
        if 'parameters' in intent:
            policy['parameters'].update(intent['parameters'])
        
        # Calcula validade
        valid_duration = intent.get('valid_duration_minutes', 
                                   template.get('valid_duration_minutes', 60))
        policy['valid_until'] = int(time.time()) + (valid_duration * 60)
        policy['received_at'] = int(time.time())
        
        self.current_policy = policy
        return policy
    
    def is_policy_valid(self, policy=None):
        """
        Verifica se a política atual ainda é válida.
        
        Args:
            policy: Política a verificar (ou usa current_policy)
        
        Returns:
            bool indicando se a política é válida.
        """
        if policy is None:
            policy = self.current_policy
        
        if policy is None:
            return False
        
        valid_until = policy.get('valid_until', 0)
        return time.time() < valid_until
    
    def apply_policy(self, current_decision, policy=None):
        """
        Aplica política do Agent à decisão do rApp.
        
        Args:
            current_decision: Decisão atual do rApp
            policy: Política do Agent (ou usa current_policy)
        
        Returns:
            Nova decisão com a política do Agent aplicada.
        """
        if policy is None:
            policy = self.current_policy
        
        if policy is None or not self.is_policy_valid(policy):
            return current_decision
        
        # Verifica se deve aplicar horário
        if 'time_window' in policy:
            if not self._is_within_time_window(policy['time_window']):
                return current_decision
        
        # Aplica política
        new_decision = current_decision.copy()
        new_decision['agent_override'] = True
        new_decision['agent_policy'] = policy.get('name', 'UNKNOWN')
        new_decision['agent_priority'] = policy.get('priority', 'NORMAL')
        
        # Aplica regras específicas
        if policy.get('block_energy_save'):
            new_decision['energy_saver'] = 'BLOCKED'
            new_decision['action'] = 'AGENT_OVERRIDE'
            new_decision['reason'] = f"Agent-Al: {policy.get('name', 'Policy')}"
            new_decision['confidence'] = 1.0
        
        elif policy.get('allow_energy_save'):
            if new_decision.get('energy_saver') == 'CONDITIONAL':
                new_decision['energy_saver'] = 'ALLOWED'
                new_decision['reason'] = 'Agent-Al: Economia permitida'
        
        # Atualiza timestamps
        new_decision['agent_applied_at'] = int(time.time())
        new_decision['agent_valid_until'] = policy.get('valid_until', 0)
        
        return new_decision
    
    def _is_within_time_window(self, time_window):
        """
        Verifica se está dentro da janela de tempo.
        
        Args:
            time_window: Dict com start, end, days
        
        Returns:
            bool indicando se está na janela.
        """
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        current_day = now.weekday()  # 0=segunda
        
        # Converte hora para minutos
        start_parts = time_window['start'].split(':')
        end_parts = time_window['end'].split(':')
        
        start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
        end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])
        current_minutes = current_hour * 60 + current_minute
        
        # Verifica dia
        if current_day not in time_window.get('days', list(range(7))):
            return False
        
        # Verifica hora
        if start_minutes <= end_minutes:
            # Mesmo dia (ex: 08:00-18:00)
            return start_minutes <= current_minutes <= end_minutes
        else:
            # Atravessa meia-noite (ex: 22:00-06:00)
            return current_minutes >= start_minutes or current_minutes <= end_minutes
    
    def get_active_policy(self):
        """
        Retorna política ativa se houver.
        
        Returns:
            Policy ativa ou None.
        """
        if self.current_policy and self.is_policy_valid():
            return self.current_policy
        return None
    
    def write_status(self, status=None):
        """
        Escreve status de resposta para o Agent-Al.
        
        Args:
            status: Dict com status (ou usa status padrão)
        """
        if status is None:
            status = {
                'timestamp': int(time.time()),
                'received_intent': self.current_intent,
                'policy_applied': self.current_policy is not None,
                'policy_valid': self.is_policy_valid(),
                'decision_modified': False
            }
        
        try:
            with open(self.status_file, 'w') as f:
                json.dump(status, f, indent=2)
        except Exception as e:
            print(f"[Agent-Al] ERRO ao escrever status: {e}")
    
    def create_intent_file(self, intent_type, valid_minutes=60, parameters=None):
        """
        Cria um arquivo de intenção para simulação/teste.
        
        Args:
            intent_type: Tipo de intenção (ex: 'PRIORIZAR_VIGILANCIA')
            valid_minutes: Minutos de validade
            parameters: Parâmetros adicionais
        
        Returns:
            Dict com a intenção criada.
        """
        intent = {
            'timestamp': int(time.time()),
            'intent': intent_type,
            'valid_duration_minutes': valid_minutes
        }
        
        if parameters:
            intent['parameters'] = parameters
        
        try:
            with open(self.intent_file, 'w') as f:
                json.dump(intent, f, indent=2)
            print(f"[Agent-Al] Intenção criada: {intent_type}")
            return intent
        except Exception as e:
            print(f"[Agent-Al] ERRO ao criar intenção: {e}")
            return None
    
    def clear_intent(self):
        """Remove arquivo de intenção."""
        try:
            if os.path.exists(self.intent_file):
                os.remove(self.intent_file)
                print("[Agent-Al] Intenção removida")
        except Exception as e:
            print(f"[Agent-Al] ERRO ao remover intenção: {e}")
    
    def get_intent_templates(self):
        """
        Retorna templates de intenção disponíveis.
        
        Returns:
            Dict com templates.
        """
        return INTENT_TEMPLATES
    
    def log_intent(self, intent, policy, applied):
        """
        Registra intenção no histórico.
        
        Args:
            intent: Intenção recebida
            policy: Política traduzida
            applied: Se foi aplicada
        """
        self.intent_history.append({
            'timestamp': int(time.time()),
            'intent': intent.get('intent') if intent else None,
            'policy_name': policy.get('name') if policy else None,
            'applied': applied
        })


def simulate_agent_commands():
    """
    Simula comandos do Agent-Al para teste.
    """
    agent = AgentOpenRAN()
    
    print("=" * 70)
    print("Simulação de Comandos Agent-Al-OpenRAN")
    print("=" * 70)
    
    # Lista templates disponíveis
    print("\n[1] Templates de Intenção Disponíveis:")
    templates = agent.get_intent_templates()
    for key, template in templates.items():
        print(f"  - {key}: {template['name']}")
        print(f"    {template['description']}")
    
    # Simula comando 1: PRIORIZAR_VIGILANCIA
    print("\n[2] Simulando: PRIORIZAR_VIGILANCIA")
    intent1 = agent.create_intent_file('PRIORIZAR_VIGILANCIA', valid_minutes=60)
    read_intent = agent.read_intent()
    policy1 = agent.translate_to_policy(read_intent)
    print(f"  Política: {policy1['name']}")
    print(f"  Prioridade: {policy1['priority']}")
    print(f"  min_prb_for_cameras: {policy1['min_prb_for_cameras']}%")
    print(f"  block_energy_save: {policy1['block_energy_save']}")
    
    # Simula aplicação a decisão
    print("\n[3] Aplicando política a decisão existente:")
    test_decision = {
        'energy_saver': 'CONDITIONAL',
        'action': 'AWAIT_DATA',
        'reason': 'Aguardando dados',
        'confidence': 0.6
    }
    print(f"  Antes: {test_decision}")
    modified_decision = agent.apply_policy(test_decision, policy1)
    print(f"  Depois: {modified_decision}")
    
    # Simula comando 2: ECONOMIA_NOTURNA
    print("\n[4] Simulando: ECONOMIA_NOTURNA")
    agent.clear_intent()
    intent2 = agent.create_intent_file('ECONOMIA_NOTURNA', valid_minutes=480)
    read_intent2 = agent.read_intent()
    policy2 = agent.translate_to_policy(read_intent2)
    print(f"  Política: {policy2['name']}")
    print(f"  Janela: {policy2['time_window']['start']} - {policy2['time_window']['end']}")
    print(f"  allow_cell_shutdown: {policy2['allow_cell_shutdown']}")
    print(f"  keep_min_cameras: {policy2['keep_min_cameras']}")
    
    # Simula comando 3: MODO_EMERGENCIA
    print("\n[5] Simulando: MODO_EMERGENCIA")
    agent.clear_intent()
    intent3 = agent.create_intent_file('MODO_EMERGENCIA', valid_minutes=30)
    read_intent3 = agent.read_intent()
    policy3 = agent.translate_to_policy(read_intent3)
    print(f"  Política: {policy3['name']}")
    print(f"  Prioridade: {policy3['priority']}")
    print(f"  min_prb_for_cameras: {policy3['min_prb_for_cameras']}%")
    print(f"  keep_all_resources: {policy3['keep_all_resources']}")
    
    # Limpa
    agent.clear_intent()
    
    print("\n" + "=" * 70)
    print("Simulação concluída!")
    print("=" * 70)


def main():
    """Teste do Agent-Al-OpenRAN"""
    simulate_agent_commands()


if __name__ == "__main__":
    main()
