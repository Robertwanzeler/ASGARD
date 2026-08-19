#!/usr/bin/env python3
"""
GreenRAN O-RAN - Energy Command Protocol
========================================

Protocolo de comunicação entre rApp (Python) e xApp Energy Saver (C++).

IMPORTANTE: O cenário GreenRAN usa:
    - 1 RU (LTE)
    - 1 mmWave
    mmWave NUNCA pode ser desligado (sobrecarregaria LTE)

Arquitetura:
    rApp (Cérebro) → energy_command.json → xApp Energy Saver (Atuador)

Formato JSON:
    {
        "action": "FULL_POWER|REDUCE_POWER|CONDITIONAL_REDUCE|POWER_DOWN|POWER_DOWN_ECO|MAINTAIN",
        "power_level": 0-100,
        "timestamp": unix_timestamp,
        "ttl_seconds": 5,
        "reason": "string explicativa"
    }

Ações:
    FULL_POWER        → RU=1, mmWave=1, 100% (potência máxima)
    CONDITIONAL_REDUCE → RU=1, mmWave=1, 70% (economia moderada)
    POWER_DOWN        → RU=1, mmWave=1, 50% (economia média)
    POWER_DOWN_ECO    → RU=1, mmWave=1, 25% (economia alta)
    MAINTAIN          → Manter estado atual

Watchdog (no xApp):
    Se não receber comando em TTL segundos → FULL_POWER (fail-safe)

Uso:
    from energy_command_protocol import EnergyCommand
    
    cmd = EnergyCommand()
    cmd.write_command("CONDITIONAL_REDUCE", 70, "CVaR=45ms, slope=-2ms/s")
"""

import json
import time
import os
import socket

from greenran_paths import ENERGY_COMMAND_PATH, ENERGY_INTENT_PATH, as_str

# Caminhos de comunicação
ENERGY_COMMAND_PATH = as_str(ENERGY_COMMAND_PATH)
ENERGY_INTENT_PATH = as_str(ENERGY_INTENT_PATH)
SOCKET_PATH = "/tmp/energy_saver.sock"

# ... (ACTIONS definition remains the same) ...

# Ações válidas
# Cenário real: 1 RU (LTE) + 1 mmWave = 2 torres
# IMPORTANTE: mmWave NUNCA pode ser desligado (sobrecarregaria LTE)
ACTIONS = {
    'FULL_POWER': {
        'ru_count': 1,
        'mmwave_count': 1,
        'power_level': 100,
        'description': 'Potência máxima - RU=1, mmWave=1'
    },
    'CONDITIONAL_REDUCE': {
        'ru_count': 1,
        'mmwave_count': 1,
        'power_level': 70,  # 70% - economia moderada
        'ttl_seconds': 3,
        'description': 'Economia moderada - RU=1, mmWave=1, 70% potência'
    },
    'REDUCE_POWER': {
        'ru_count': 1,
        'mmwave_count': 1,
        'power_level': 70,
        'ttl_seconds': 3,
        'description': 'Alias legado de CONDITIONAL_REDUCE'
    },
    'POWER_DOWN': {
        'ru_count': 1,
        'mmwave_count': 1,
        'power_level': 50,  # 50% - economia média
        'ttl_seconds': 5,
        'description': 'Economia média - RU=1, mmWave=1, 50% potência'
    },
    'POWER_DOWN_ECO': {
        'ru_count': 1,
        'mmwave_count': 1,
        'power_level': 25,  # 25% - economia alta
        'ttl_seconds': 5,
        'description': 'Modo ECO - RU=1, mmWave=1, 25% potência'
    },
    'MAINTAIN': {
        'ru_count': -1,
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
    
    def __init__(self, command_path=None, data_lake=None):
        """
        Inicializa o protocolo de comando.
        
        Args:
            command_path: Caminho do arquivo JSON (padrão: /tmp/xapp_intents/energy_command.json)
            data_lake: Instância do DataLake para gravar comandos (opcional)
        """
        self.command_path = command_path or ENERGY_COMMAND_PATH
        self.intent_path = ENERGY_INTENT_PATH
        self.data_lake = data_lake
        
        # Criar diretório se não existir
        os.makedirs(os.path.dirname(self.command_path), exist_ok=True)
    
    def write_command(self, action, power_level=None, reason="", ttl=None):
        """
        Envia comando de energia via Socket para o xApp.
        """
        if action not in ACTIONS:
            print(f"[EnergyProtocol] ERRO: Ação '{action}' inválida")
            return False
        
        action_info = ACTIONS[action]
        
        # Usar power_level fornecido ou default da ação
        if power_level is None:
            power_level = action_info['power_level']
        
        if ttl is None:
            ttl = action_info.get('ttl_seconds', DEFAULT_TTL)
        
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
            # Enviar via Socket
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(SOCKET_PATH)
            sock.sendall(json.dumps(command).encode('utf-8'))
            sock.close()
            
            print(f"[EnergyProtocol] Comando enviado via Socket: {action}")
            
            # Gravar no DataLake se disponível
            if self.data_lake:
                self.data_lake.record_energy_command(
                    command=action,
                    power_percent=power_level,
                    ru_count=action_info['ru_count'],
                    mmwave_count=action_info['mmwave_count'],
                    reason=reason
                )
            
            return True
            
        except Exception as e:
            print(f"[EnergyProtocol] ERRO ao enviar comando via Socket: {e}. Fallback para arquivo.")
            # Fallback para o modo arquivo original
            try:
                temp_path = self.command_path + '.tmp'
                with open(temp_path, 'w') as f:
                    json.dump(command, f, indent=2)
                os.replace(temp_path, self.command_path)
                
                if self.data_lake:
                    self.data_lake.record_energy_command(
                        command=action,
                        power_percent=power_level,
                        ru_count=action_info['ru_count'],
                        mmwave_count=action_info['mmwave_count'],
                        reason=reason
                    )
                return True
            except Exception as e2:
                print(f"[EnergyProtocol] ERRO no Fallback: {e2}")
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
        """Envia comando FULL_POWER - 100% potência."""
        return self.write_command('FULL_POWER', reason=reason)
    
    def send_reduce_power(self, reason=""):
        """Envia comando CONDITIONAL_REDUCE - 70% potência."""
        return self.write_command('CONDITIONAL_REDUCE', reason=reason)
    
    def send_conditional_reduce(self, reason=""):
        """Envia comando CONDITIONAL_REDUCE - 70% potência."""
        return self.write_command('CONDITIONAL_REDUCE', reason=reason)
    
    def send_power_down(self, reason=""):
        """Envia comando POWER_DOWN - 50% potência."""
        return self.write_command('POWER_DOWN', reason=reason)
    
    def send_power_down_eco(self, reason=""):
        """Envia comando POWER_DOWN_ECO - 25% potência."""
        return self.write_command('POWER_DOWN_ECO', reason=reason)
    
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
    
    # Teste 3: CONDITIONAL_REDUCE (70%)
    print("\n[3] Enviando CONDITIONAL_REDUCE:")
    cmd.send_conditional_reduce(reason="CVaR=45ms, slope=-2ms/s")
    
    # Teste 4: Verificar validade
    print("\n[4] Verificando validade:")
    print(f"    Válido: {cmd.is_command_valid()}")
    
    print("\n" + "=" * 60)
    print("Teste concluído!")
    print("=" * 60)


if __name__ == "__main__":
    main()
