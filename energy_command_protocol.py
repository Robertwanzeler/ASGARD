#!/usr/bin/env python3
"""
GreenRAN O-RAN - Energy Command Protocol
=========================================

Protocolo de comunicação entre rApp (Python) e xApp Energy Saver (C++).

Arquitetura:
    rApp (Cérebro) → energy_command.json → xApp Energy Saver (Atuador)

Formato JSON:
    {
        "action": "FULL_POWER|REDUCE_POWER|POWER_DOWN|MAINTAIN",
        "power_level": 0-100,
        "timestamp": unix_timestamp,
        "ttl_seconds": 5,
        "reason": "string explicativa"
    }

Ações:
    FULL_POWER    → RU=2, mmWave=1 (potência máxima)
    REDUCE_POWER  → RU=2, mmWave=0 (reduzir mmWave)
    POWER_DOWN    → RU=1, mmWave=0 (economia máxima)
    MAINTAIN      → Manter estado atual

Watchdog (no xApp):
    Se não receber comando em TTL segundos → FULL_POWER (fail-safe)

Uso:
    from energy_command_protocol import EnergyCommand
    
    cmd = EnergyCommand()
    cmd.write_command("REDUCE_POWER", 50, "CVaR=45ms, slope=-2ms/s")
"""

import json
import time
import os

# Caminhos de comunicação
ENERGY_COMMAND_PATH = "/tmp/xapp_intents/energy_command.json"
ENERGY_INTENT_PATH = "/tmp/xapp_intents/energy_saver.txt"

# Ações válidas
ACTIONS = {
    'FULL_POWER': {
        'ru_count': 2,
        'mmwave_count': 1,
        'power_level': 100,
        'description': 'Potência máxima - RU=2, mmWave=1'
    },
    'REDUCE_POWER': {
        'ru_count': 2,
        'mmwave_count': 0,
        'power_level': 50,
        'description': 'Reduzir potência - RU=2, mmWave=0'
    },
    'POWER_DOWN': {
        'ru_count': 1,
        'mmwave_count': 0,
        'power_level': 25,
        'description': 'Economia máxima - RU=1, mmWave=0'
    },
    'MAINTAIN': {
        'ru_count': -1,  # -1 = manter atual
        'mmwave_count': -1,
        'power_level': -1,
        'description': 'Manter estado atual'
    }
}

DEFAULT_TTL = 5  # Watchdog timeout em segundos


class EnergyCommand:
    """
    Classe para gerenciar comandos de energia do rApp para o xApp.
    """
    
    def __init__(self, command_path=None):
        """
        Inicializa o protocolo de comando.
        
        Args:
            command_path: Caminho do arquivo JSON (padrão: /tmp/xapp_intents/energy_command.json)
        """
        self.command_path = command_path or ENERGY_COMMAND_PATH
        self.intent_path = ENERGY_INTENT_PATH
        
        # Criar diretório se não existir
        os.makedirs(os.path.dirname(self.command_path), exist_ok=True)
    
    def write_command(self, action, power_level=None, reason="", ttl=None):
        """
        Escreve comando de energia para o xApp.
        
        Args:
            action: Ação desejada (FULL_POWER, REDUCE_POWER, POWER_DOWN, MAINTAIN)
            power_level: Nível de potência 0-100 (opcional, usa default da ação)
            reason: String explicativa do motivo
            ttl: Timeout em segundos (padrão: 5s)
        
        Returns:
            bool: True se escrita com sucesso
        """
        if action not in ACTIONS:
            print(f"[EnergyProtocol] ERRO: Ação '{action}' inválida")
            return False
        
        action_info = ACTIONS[action]
        
        # Usar power_level fornecido ou default da ação
        if power_level is None:
            power_level = action_info['power_level']
        
        if ttl is None:
            ttl = DEFAULT_TTL
        
        command = {
            'action': action,
            'power_level': power_level,
            'ru_count': action_info['ru_count'],
            'mmwave_count': action_info['mmwave_count'],
            'timestamp': int(time.time()),
            'ttl_seconds': ttl,
            'reason': reason,
            'version': '1.0'
        }
        
        try:
            # Escrever com atomicidade (write + rename)
            temp_path = self.command_path + '.tmp'
            with open(temp_path, 'w') as f:
                json.dump(command, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            
            # Renomear atomicamente
            os.replace(temp_path, self.command_path)
            
            print(f"[EnergyProtocol] Comando enviado: {action} (power={power_level}%, TTL={ttl}s)")
            print(f"[EnergyProtocol] Motivo: {reason}")
            
            return True
            
        except Exception as e:
            print(f"[EnergyProtocol] ERRO ao escrever comando: {e}")
            return False
    
    def read_command(self):
        """
        Lê comando atual do arquivo JSON.
        
        Returns:
            dict: Comando atual ou None se erro
        """
        try:
            with open(self.command_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except Exception as e:
            print(f"[EnergyProtocol] ERRO ao ler comando: {e}")
            return None
    
    def read_intent(self):
        """
        Lê intent atual do xApp Energy Saver.
        
        Returns:
            dict: Intent atual ou None se erro
        """
        try:
            with open(self.intent_path, 'r') as f:
                content = f.read()
            
            # Parse do formato KEY=VALUE
            intent = {}
            for line in content.split('\n'):
                if '=' in line and not line.startswith('=') and not line.startswith('|'):
                    key, value = line.split('=', 1)
                    intent[key.strip()] = value.strip()
            
            return intent
            
        except FileNotFoundError:
            return None
        except Exception as e:
            print(f"[EnergyProtocol] ERRO ao ler intent: {e}")
            return None
    
    def is_command_valid(self):
        """
        Verifica se o comando atual ainda é válido (dentro do TTL).
        
        Returns:
            bool: True se comando ainda válido
        """
        command = self.read_command()
        if command is None:
            return False
        
        now = int(time.time())
        timestamp = command.get('timestamp', 0)
        ttl = command.get('ttl_seconds', DEFAULT_TTL)
        
        return (now - timestamp) < ttl
    
    def send_full_power(self, reason=""):
        """Envia comando FULL_POWER."""
        return self.write_command('FULL_POWER', reason=reason)
    
    def send_reduce_power(self, reason=""):
        """Envia comando REDUCE_POWER."""
        return self.write_command('REDUCE_POWER', reason=reason)
    
    def send_power_down(self, reason=""):
        """Envia comando POWER_DOWN."""
        return self.write_command('POWER_DOWN', reason=reason)
    
    def send_maintain(self, reason=""):
        """Envia comando MAINTAIN."""
        return self.write_command('MAINTAIN', reason=reason)


def main():
    """Teste do protocolo."""
    print("=" * 60)
    print("Energy Command Protocol - Teste")
    print("=" * 60)
    
    cmd = EnergyCommand()
    
    # Teste 1: FULL_POWER
    print("\n[1] Enviando FULL_POWER:")
    cmd.send_full_power(reason="Teste inicial")
    
    # Teste 2: Ler comando
    print("\n[2] Lendo comando:")
    current = cmd.read_command()
    if current:
        print(f"    Ação: {current['action']}")
        print(f"    Power: {current['power_level']}%")
        print(f"    TTL: {current['ttl_seconds']}s")
    
    # Teste 3: REDUCE_POWER
    print("\n[3] Enviando REDUCE_POWER:")
    cmd.send_reduce_power(reason="CVaR=45ms, slope=-2ms/s")
    
    # Teste 4: Verificar validade
    print("\n[4] Verificando validade:")
    print(f"    Válido: {cmd.is_command_valid()}")
    
    print("\n" + "=" * 60)
    print("Teste concluído!")
    print("=" * 60)


if __name__ == "__main__":
    main()
