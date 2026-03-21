#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp Orchestrator
==================================

Responsabilidade: Orquestrar xApps no Non-RT RIC
- Lê intenções do SLICER e ENERGY SAVER
- Aplica regra de PRIORIDADE: SLA > Energy Saving
- Escreve decisão para bloquear/permitir ENERGY SAVER

Interface:
    /tmp/xapp_intents/
    ├── slicer.txt          → xApp SLICER escreve
    ├── energy_saver.txt    → xApp ENERGY SAVER escreve
    └── rapp_decision.txt   → rApp escreve

Regra de Prioridade:
    SE SLICER = CRITICAL:
        → ENERGY_SAVER = BLOCKED
        → Ação: PRIORIZE_VIGILANCE
    
    SENÃO SE ENERGY_SAVER = ENERGY_SAVE:
        → ENERGY_SAVER = ALLOWED
        → Ação: ACTIVATE_ENERGY_SAVING
    
    SENÃO:
        → ENERGY_SAVER = ALLOWED
        → Ação: NORMAL_OPERATION

Usage:
    python3 rapp_orchestrator.py [--interval SECONDS]
"""

import os
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime
import threading
import signal

# Default paths
SLICER_INTENT_PATH = "/tmp/xapp_intents/slicer.txt"
ENERGY_INTENT_PATH = "/tmp/xapp_intents/energy_saver.txt"
RAPP_DECISION_PATH = "/tmp/xapp_intents/rapp_decision.txt"
DEFAULT_INTERVAL = 5  # seconds


class RappOrchestrator:
    def __init__(self, interval):
        self.interval = interval
        self.running = True
        self.decision_history = []
        
        # Statistics
        self.stats = {
            'total_cycles': 0,
            'blocked_count': 0,
            'allowed_count': 0,
            'sla_violations': 0,
            'energy_saves': 0
        }
        
        # Create directory
        os.makedirs("/tmp/xapp_intents", exist_ok=True)
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        print("[rApp] Received shutdown signal")
        self.running = False
    
    def read_slicer_intent(self):
        """Read SLICER intent from file"""
        try:
            if not os.path.exists(SLICER_INTENT_PATH):
                return None
            
            with open(SLICER_INTENT_PATH, 'r') as f:
                content = f.read()
            
            intent = {}
            for line in content.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    intent[key.strip()] = value.strip()
            
            return intent
        except Exception as e:
            print(f"[rApp] Error reading SLICER intent: {e}")
            return None
    
    def read_energy_intent(self):
        """Read ENERGY SAVER intent from file"""
        try:
            if not os.path.exists(ENERGY_INTENT_PATH):
                return None
            
            with open(ENERGY_INTENT_PATH, 'r') as f:
                content = f.read()
            
            intent = {}
            for line in content.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    intent[key.strip()] = value.strip()
            
            return intent
        except Exception as e:
            print(f"[rApp] Error reading ENERGY intent: {e}")
            return None
    
    def make_decision(self, slicer_intent, energy_intent):
        """
        Apply SLA priority rule:
        - If SLICER = CRITICAL → block ENERGY SAVER
        - If SLICER = WARNING → block ENERGY SAVER
        - If SLICER = NORMAL and ENERGY wants to save → allow
        - Otherwise → allow
        """
        decision = {
            'timestamp': int(time.time()),
            'energy_saver': 'ALLOWED',
            'action': 'NORMAL_OPERATION',
            'reason': 'BALANCED',
            'slicer_state': 'UNKNOWN',
            'energy_state': 'UNKNOWN'
        }
        
        # Get SLICER state
        if slicer_intent:
            slicer_state = slicer_intent.get('STATE', 'UNKNOWN')
            decision['slicer_state'] = slicer_state
            
            # SLA has PRIORITY over energy saving
            if slicer_state in ['CRITICAL', 'WARNING']:
                decision['energy_saver'] = 'BLOCKED'
                decision['action'] = 'PRIORIZE_VIGILANCE'
                decision['reason'] = 'SLA_VIOLATED'
                self.stats['sla_violations'] += 1
        
        # Get ENERGY state
        if energy_intent:
            energy_state = energy_intent.get('INTENT', 'UNKNOWN')
            decision['energy_state'] = energy_state
            
            # If ENERGY wants to save and SLICER allows
            if energy_state == 'ENERGY_SAVE' and decision['energy_saver'] == 'ALLOWED':
                decision['action'] = 'ACTIVATE_ENERGY_SAVING'
                decision['reason'] = 'LOW_UTILIZATION'
                self.stats['energy_saves'] += 1
        
        # If no SLICER data, allow energy saving
        if not slicer_intent and energy_intent:
            decision['reason'] = 'NO_SLA_DATA'
        
        return decision
    
    def write_decision(self, decision):
        """Write decision to file with visual formatting"""
        try:
            energy_status = decision['energy_saver']
            
            if energy_status == 'BLOCKED':
                status_display = "[BLOCKED]"
            else:
                status_display = "[ALLOWED]"
            
            reason_text = {
                'SLA_VIOLATED': 'SLICER CRITICAL - Latencia > 100ms',
                'LOW_UTILIZATION': 'Economia - Sem cameras + baixa latencia',
                'BALANCED': 'Normal - nenhum problema detectado',
                'NO_SLA_DATA': 'Aguardando dados do SLICER'
            }.get(decision['reason'], decision['reason'])
            
            slicer_status = ""
            if decision['slicer_state'] == 'CRITICAL':
                slicer_status = "PROBLEMA: Latencia > 100ms (SLA VIOLADO!)"
            elif decision['slicer_state'] == 'WARNING':
                slicer_status = "AVISO: Latencia 50-100ms"
            elif decision['slicer_state'] == 'NORMAL':
                slicer_status = "OK: Latencia < 100ms"
            elif decision['slicer_state'] == 'IDLE':
                slicer_status = "OK: Nenhuma camera ativa"
            else:
                slicer_status = "Aguardando dados..."
            
            energy_status_desc = ""
            if decision['energy_state'] == 'ENERGY_SAVE':
                energy_status_desc = "Quer desligar para economia"
            elif decision['energy_state'] == 'INTERVENTION':
                energy_status_desc = "Mantendo recursos ativos"
            elif decision['energy_state'] == 'NORMAL':
                energy_status_desc = "Operacao normal"
            else:
                energy_status_desc = "Aguardando dados..."
            
            with open(RAPP_DECISION_PATH, 'w') as f:
                f.write("=" * 65 + "\n")
                f.write("               rApp ORCHESTRATOR - DECISION\n")
                f.write("=" * 65 + "\n")
                f.write("\n")
                f.write("+--------------------- SLICER ------------------------+\n")
                f.write(f"|  Estado: {decision['slicer_state']:<12}                         |\n")
                f.write(f"|  Status: {slicer_status:<43} |\n")
                f.write("+----------------------------------------------------+\n")
                f.write("\n")
                f.write("+-------------------- ENERGY SAVER -------------------+\n")
                f.write(f"|  Intencao: {decision['energy_state']:<12}                      |\n")
                f.write(f"|  Status: {energy_status_desc:<45} |\n")
                f.write("+----------------------------------------------------+\n")
                f.write("\n")
                f.write("=" * 65 + "\n")
                f.write("                    rApp DECISION\n")
                f.write("=" * 65 + "\n")
                f.write(f"|  Energy Saver: {status_display:<20}                  |\n")
                f.write(f"|  Acao: {decision['action']:<48} |\n")
                f.write(f"|  Motivo: {reason_text:<47} |\n")
                f.write("=" * 65 + "\n")
                f.write("\n")
                f.write(f"rApp=orchestrator\n")
                f.write(f"TIMESTAMP={decision['timestamp']}\n")
                f.write(f"ENERGY_SAVER={decision['energy_saver']}\n")
                f.write(f"ACTION={decision['action']}\n")
                f.write(f"REASON={decision['reason']}\n")
                f.write(f"SLICER_STATE={decision['slicer_state']}\n")
                f.write(f"ENERGY_STATE={decision['energy_state']}\n")
                f.flush()
            
            if decision['energy_saver'] == 'BLOCKED':
                self.stats['blocked_count'] += 1
            else:
                self.stats['allowed_count'] += 1
            
            self.stats['total_cycles'] += 1
            
            return True
        except Exception as e:
            print(f"[rApp] Error writing decision: {e}")
            return False
    
    def run(self):
        """Main orchestration loop"""
        print("[rApp] Starting rApp Orchestrator")
        print(f"[rApp] Polling interval: {self.interval}s")
        print(f"[rApp] Monitoring: {SLICER_INTENT_PATH}")
        print(f"[rApp] Monitoring: {ENERGY_INTENT_PATH}")
        print(f"[rApp] Output: {RAPP_DECISION_PATH}")
        print("")
        
        cycle = 0
        
        while self.running:
            cycle += 1
            
            # Read intents
            slicer_intent = self.read_slicer_intent()
            energy_intent = self.read_energy_intent()
            
            # Make decision
            decision = self.make_decision(slicer_intent, energy_intent)
            
            # Write decision
            if self.write_decision(decision):
                # Log decision with colors
                energy_status = decision['energy_saver']
                action = decision['action']
                reason = decision['reason']
                
                if cycle % 5 == 0 or energy_status == 'BLOCKED':
                    if energy_status == 'BLOCKED':
                        status_display = f"\033[1;31m[BLOCKED]\033[0m"
                        reason_display = "\033[1;31mSLA CRITICAL - Latencia > 100ms\033[0m"
                    else:
                        status_display = f"\033[1;32m[ALLOWED]\033[0m"
                        reason_display = f"\033[1;32m{reason}\033[0m"
                    
                    slicer_info = ""
                    if decision['slicer_state'] == 'CRITICAL':
                        slicer_info = "\033[1;31m<- PROBLEMA: Latencia > 100ms!\033[0m"
                    elif decision['slicer_state'] == 'WARNING':
                        slicer_info = "\033[1;33m<- AVISO: Latencia 50-100ms\033[0m"
                    elif decision['slicer_state'] == 'NORMAL':
                        slicer_info = "\033[1;32m<- OK: Latencia < 100ms\033[0m"
                    elif decision['slicer_state'] == 'IDLE':
                        slicer_info = "\033[1;32m<- OK: Nenhuma camera ativa\033[0m"
                    else:
                        slicer_info = "<- Aguardando dados..."
                    
                    energy_info = ""
                    if decision['energy_state'] == 'ENERGY_SAVE':
                        energy_info = "<- Quer desligar para economia"
                    elif decision['energy_state'] == 'INTERVENTION':
                        energy_info = "<- Mantendo recursos ativos"
                    elif decision['energy_state'] == 'NORMAL':
                        energy_info = "<- Operacao normal"
                    else:
                        energy_info = "<- Aguardando dados..."
                    
                    print("")
                    print("=" * 65)
                    print("                    rApp ORCHESTRATOR")
                    print("=" * 65)
                    print(f"  Ciclo: {cycle}")
                    print("")
                    print("  +--------------------- SLICER ------------------------+")
                    print(f"  |  Estado: {decision['slicer_state']:<12}                         |")
                    print(f"  |  {slicer_info:<50} |")
                    print("  +----------------------------------------------------+")
                    print("")
                    print("  +-------------------- ENERGY SAVER -------------------+")
                    print(f"  |  Intencao: {decision['energy_state']:<12}                      |")
                    print(f"  |  {energy_info:<50} |")
                    print("  +----------------------------------------------------+")
                    print("")
                    print("=" * 65)
                    print("                      rApp DECISION")
                    print("=" * 65)
                    print(f"  |  Energy Saver: {status_display:<20}                  |")
                    print(f"  |  Acao: {decision['action']:<48} |")
                    print(f"  |  Motivo: {reason_display:<47} |")
                    print("=" * 65)
            
            time.sleep(self.interval)
        
        # Final stats
        print("")
        print("=" * 60)
        print("[rApp] Final Statistics:")
        print(f"  Total cycles: {self.stats['total_cycles']}")
        print(f"  BLOCKED: {self.stats['blocked_count']}")
        print(f"  ALLOWED: {self.stats['allowed_count']}")
        print(f"  SLA Violations: {self.stats['sla_violations']}")
        print(f"  Energy Saves: {self.stats['energy_saves']}")
        print("=" * 60)
        print("[rApp] Shutdown complete")


def main():
    parser = argparse.ArgumentParser(description='GreenRAN rApp Orchestrator')
    parser.add_argument(
        '--interval', '-i',
        type=float,
        default=DEFAULT_INTERVAL,
        help=f'Polling interval in seconds (default: {DEFAULT_INTERVAL})'
    )
    args = parser.parse_args()
    
    orchestrator = RappOrchestrator(args.interval)
    orchestrator.run()


if __name__ == '__main__':
    main()
