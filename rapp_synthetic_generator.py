#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp Synthetic Data Generator
=============================================

Responsabilidade: Gerar dados sintéticos para treinar o Pattern Engine
- Simula 1 semana de dados (168 horas)
- Padrões sazonais realistas:
  - Segunda-Sexta: Dia útil normal
  - Noite (22:00-06:00): Baixa atividade
  - Sábado-Domingo: 50% da atividade

Uso:
    from rapp_synthetic_generator import SyntheticDataGenerator
    
    gen = SyntheticDataGenerator()
    gen.generate_week_data()
    gen.load_into_data_lake()
"""

import sys
import random
import time
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, '/home/robert/orange_nuclear')
from rapp_data_lake import DataLake

DAY_NAMES = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']

# Períodos do dia
PERIODS = {
    'night': {'start': 22, 'end': 6},      # Noite - baixa atividade
    'morning': {'start': 6, 'end': 12},     # Manhã - atividade crescente
    'business': {'start': 12, 'end': 18},    # Horário comercial - ativo
    'evening': {'start': 18, 'end': 22},    # Noite - atividade decrescente
}


class SyntheticDataGenerator:
    """
    Gerador de dados sintéticos para simulação de padrões sazonais.
    
    Cenários simulados:
    - Dia útil vs fim de semana
    - Horário comercial vs noite
    - Períodos de rajadas (câmeras Ligadas/Desligadas)
    - SLA violations ocasionais
    """
    
    def __init__(self, seed=None):
        """
        Inicializa o gerador.
        
        Args:
            seed: Seed para random (para reprodutibilidade)
        """
        if seed is not None:
            random.seed(seed)
        
        self.data = []
    
    def get_period(self, hour):
        """Retorna o período do dia baseado na hora."""
        if 22 <= hour or hour < 6:
            return 'night'
        elif 6 <= hour < 12:
            return 'morning'
        elif 12 <= hour < 18:
            return 'business'
        else:
            return 'evening'
    
    def is_weekend(self, day_of_week):
        """Retorna True se for fim de semana."""
        return day_of_week >= 5
    
    def generate_camera_activity(self, hour, day_of_week):
        """
        Gera número de câmeras ativas baseado no horário.
        
        Returns:
            int: 0-3 câmeras
        """
        period = self.get_period(hour)
        is_wknd = self.is_weekend(day_of_week)
        
        if period == 'night':
            # Noite: 67% chance de cameras OFF
            if is_wknd:
                return random.choice([0, 0, 0, 1])  # Fim de semana ainda menos
            else:
                return random.choice([0, 0, 1])  # Dia útil com alguma vigilância
        
        elif period == 'morning':
            # Manhã: ativação gradual
            if is_wknd:
                return random.choice([0, 1, 1, 2])
            else:
                return random.choice([1, 2, 2, 3])
        
        elif period == 'business':
            # Horário comercial: máxima atividade
            if is_wknd:
                return random.choice([1, 2, 2, 3])
            else:
                return random.choice([2, 3, 3, 3])
        
        else:  # evening
            # Noite: decrescendo
            if is_wknd:
                return random.choice([0, 1, 1])
            else:
                return random.choice([1, 2, 2])
    
    def generate_latency(self, hour, day_of_week, cameras_active):
        """
        Gera latência baseada no horário e câmeras ativas.
        
        Returns:
            float: Latência em microsegundos
        """
        period = self.get_period(hour)
        is_wknd = self.is_weekend(day_of_week)
        
        # Base: latência baixa sem cameras
        if cameras_active == 0:
            base_latency = random.uniform(1000, 5000)  # 1-5ms
            return base_latency
        
        # Com câmeras, latência depende do período
        if period == 'night':
            # Noite: latência controlada
            base = 5000 + cameras_active * 2000  # 5-11ms
            variance = random.uniform(-1000, 3000)
        
        elif period == 'morning':
            # Manhã: crescente
            base = 10000 + cameras_active * 5000  # 15-25ms
            variance = random.uniform(-2000, 5000)
        
        elif period == 'business':
            # Horário comercial: pode ter congestionamento
            if cameras_active >= 2:
                # Possível SLA violation
                if random.random() < 0.3:  # 30% chance
                    base = random.uniform(100000, 300000)  # 100-300ms (VIOLATION!)
                else:
                    base = 20000 + cameras_active * 10000  # 40-50ms
            else:
                base = 15000 + cameras_active * 5000
            variance = random.uniform(-5000, 10000)
        
        else:  # evening
            # Noite: diminuindo
            base = 8000 + cameras_active * 4000  # 12-20ms
            variance = random.uniform(-2000, 3000)
        
        # Fim de semana: latência geralmente menor
        if is_wknd and period != 'night':
            base = base * 0.6
        
        latency = base + variance
        return max(1000, latency)  # Mínimo 1ms
    
    def generate_energy_state(self, hour, day_of_week, latency, cameras):
        """
        Gera estado do Energy Saver.
        
        Returns:
            str: 'NORMAL', 'INTERVENTION', ou 'ENERGY_SAVE'
        """
        period = self.get_period(hour)
        
        # Se latência > 100ms = INTERVENTION
        if latency > 100000:
            return 'INTERVENTION'
        
        # Se noite e sem cameras = ENERGY_SAVE
        if period == 'night' and cameras == 0:
            return 'ENERGY_SAVE'
        
        # Se fim de semana noite = ENERGY_SAVE
        if self.is_weekend(day_of_week) and period == 'night':
            return 'ENERGY_SAVE'
        
        return 'NORMAL'
    
    def generate_slicer_state(self, latency, cameras):
        """
        Gera estado do SLICER.
        
        Returns:
            str: 'NORMAL', 'WARNING', 'CRITICAL', ou 'IDLE'
        """
        if cameras == 0:
            return 'IDLE'
        
        if latency > 100000:  # 100ms
            return 'CRITICAL'
        elif latency > 50000:  # 50ms
            return 'WARNING'
        else:
            return 'NORMAL'
    
    def generate_decision(self, slicer_state, energy_state, hour):
        """
        Gera decisão do rApp.
        
        Returns:
            str: 'BLOCKED', 'ALLOWED', ou 'CONDITIONAL'
        """
        period = self.get_period(hour)
        
        if slicer_state in ['CRITICAL', 'WARNING']:
            return 'BLOCKED'
        
        if period == 'night' and self.is_weekend(datetime.now().weekday()):
            return 'ALLOWED'
        
        if energy_state == 'ENERGY_SAVE':
            return 'ALLOWED'
        
        return 'CONDITIONAL'
    
    def generate_week_data(self, resolution_minutes=5, days=7):
        """
        Gera dados de uma semana completa.
        
        Args:
            resolution_minutes: Resolução temporal (default 5 min)
            days: Número de dias a gerar
        
        Returns:
            List de dicts com dados gerados
        """
        print(f"[Generator] Gerando {days} dias de dados ({resolution_minutes}min resolução)...")
        
        self.data = []
        start_time = datetime.now() - timedelta(days=days)
        
        total_minutes = days * 24 * 60
        steps = total_minutes // resolution_minutes
        
        for step in range(steps):
            current_time = start_time + timedelta(minutes=step * resolution_minutes)
            timestamp = int(current_time.timestamp())
            
            hour = current_time.hour
            day_of_week = current_time.weekday()
            
            cameras = self.generate_camera_activity(hour, day_of_week)
            latency = self.generate_latency(hour, day_of_week, cameras)
            energy_state = self.generate_energy_state(hour, day_of_week, latency, cameras)
            slicer_state = self.generate_slicer_state(latency, cameras)
            decision = self.generate_decision(slicer_state, energy_state, hour)
            
            record = {
                'timestamp': timestamp,
                'datetime': current_time.isoformat(),
                'hour': hour,
                'day_of_week': day_of_week,
                'day_name': DAY_NAMES[day_of_week],
                'period': self.get_period(hour),
                'is_weekend': self.is_weekend(day_of_week),
                'cameras_active': cameras,
                'critical_cameras': 1 if cameras >= 2 and latency > 100000 else 0,
                'latency_us': latency,
                'latency_ms': latency / 1000,
                'energy_state': energy_state,
                'slicer_state': slicer_state,
                'decision': decision
            }
            
            self.data.append(record)
        
        print(f"[Generator] Gerados {len(self.data)} registros")
        return self.data
    
    def print_statistics(self):
        """Imprime estatísticas dos dados gerados."""
        if not self.data:
            print("[Generator] Nenhum dado gerado. Execute generate_week_data() primeiro.")
            return
        
        print("\n" + "=" * 60)
        print("Estatísticas dos Dados Sintéticos")
        print("=" * 60)
        
        # Por dia da semana
        print("\n[Por Dia da Semana]")
        by_day = defaultdict(list)
        for d in self.data:
            by_day[d['day_name']].append(d)
        
        for day_name in DAY_NAMES:
            records = by_day.get(day_name, [])
            if records:
                avg_cameras = sum(r['cameras_active'] for r in records) / len(records)
                avg_latency = sum(r['latency_us'] for r in records) / len(records)
                violations = sum(1 for r in records if r['slicer_state'] == 'CRITICAL')
                print(f"  {day_name:10}: Cameras={avg_cameras:.1f}, "
                      f"Latência={avg_latency/1000:.0f}ms, "
                      f"Violations={violations}")
        
        # Por período
        print("\n[Por Período do Dia]")
        by_period = defaultdict(list)
        for d in self.data:
            by_period[d['period']].append(d)
        
        for period in ['night', 'morning', 'business', 'evening']:
            records = by_period.get(period, [])
            if records:
                avg_cameras = sum(r['cameras_active'] for r in records) / len(records)
                avg_latency = sum(r['latency_us'] for r in records) / len(records)
                violations = sum(1 for r in records if r['slicer_state'] == 'CRITICAL')
                print(f"  {period:10}: Cameras={avg_cameras:.1f}, "
                      f"Latência={avg_latency/1000:.0f}ms, "
                      f"Violations={violations}")
        
        # Por hora (heatmap simplificado)
        print("\n[Heatmap de Atividade por Hora]")
        by_hour = defaultdict(lambda: {'cameras': [], 'latency': []})
        for d in self.data:
            by_hour[d['hour']]['cameras'].append(d['cameras_active'])
            by_hour[d['hour']]['latency'].append(d['latency_us'])
        
        print("     ", end="")
        for h in range(24):
            marker = "+" if h % 3 == 0 else " "
            print(f"{marker}", end="")
        print()
        
        print("     ", end="")
        for h in range(24):
            print(f"{h % 10}", end="")
        print()
        print("     " + "-" * 24)
        
        for dow_idx, dow_name in enumerate(DAY_NAMES):
            day_records = [d for d in self.data if d['day_of_week'] == dow_idx]
            print(f"  {dow_name[:3]}: ", end="")
            for h in range(24):
                hour_records = [d for d in day_records if d['hour'] == h]
                if hour_records:
                    avg = sum(r['cameras_active'] for r in hour_records) / len(hour_records)
                    if avg == 0:
                        print(".", end="")
                    elif avg <= 1:
                        print("░", end="")
                    elif avg <= 2:
                        print("▒", end="")
                    else:
                        print("█", end="")
                else:
                    print(" ", end="")
            print()
        
        print("\n  Legenda: .=0 cameras, ░=0-1, ▒=1-2, █=2-3 cameras")
        
        # Decisões
        print("\n[Decisões do rApp]")
        decisions = defaultdict(int)
        for d in self.data:
            decisions[d['decision']] += 1
        
        total = len(self.data)
        for decision, count in sorted(decisions.items()):
            pct = count / total * 100
            print(f"  {decision:12}: {count:5} ({pct:5.1f}%)")
        
        print("\n" + "=" * 60)
    
    def load_into_data_lake(self, data_lake=None, db_path="/tmp/rapp_data_lake.db"):
        """
        Carrega os dados gerados no Data Lake.
        
        Args:
            data_lake: Instância de DataLake (ou cria nova)
            db_path: Caminho do banco se não passar DataLake
        """
        if not self.data:
            print("[Generator] Nenhum dado para carregar. Execute generate_week_data() primeiro.")
            return False
        
        if data_lake is None:
            data_lake = DataLake(db_path)
            created_dl = True
        else:
            created_dl = False
        
        print(f"[Generator] Carregando {len(self.data)} registros no Data Lake...")
        
        for record in self.data:
            # Registra métrica
            data_lake.record_metric(
                timestamp=record['timestamp'],
                latency_us=record['latency_us'],
                cameras_active=record['cameras_active'],
                critical_cameras=record['critical_cameras'],
                energy_state=record['energy_state'],
                slicer_state=record['slicer_state']
            )
            
            # Registra decisão
            decision = {
                'energy_saver': record['decision'],
                'reason': f"synthetic_{record['energy_state'].lower()}",
                'confidence': 0.8,
                'pattern': record['period'],
                'agent_override': False,
                'energy_state': record['energy_state'],
                'slicer_state': record['slicer_state']
            }
            data_lake.record_decision(decision, record['timestamp'])
        
        print(f"[Generator] Dados carregados com sucesso!")
        
        if created_dl:
            data_lake.close()
        
        return True
    
    def export_to_csv(self, filepath="/tmp/rapp_synthetic_data.csv"):
        """
        Exporta dados para CSV.
        
        Args:
            filepath: Caminho do arquivo de saída
        """
        if not self.data:
            print("[Generator] Nenhum dado para exportar.")
            return None
        
        import csv
        
        keys = ['timestamp', 'datetime', 'hour', 'day_of_week', 'day_name',
                'period', 'is_weekend', 'cameras_active', 'critical_cameras',
                'latency_us', 'latency_ms', 'energy_state', 'slicer_state', 'decision']
        
        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(self.data)
        
        print(f"[Generator] Dados exportados para {filepath}")
        return filepath
    
    def generate_bursty_scenario(self, duration_hours=24):
        """
        Gera um cenário específico com rajadas (bursty traffic).
        
        Simula o cenário real do GreenRAN:
        - Câmeras ligando/desligando em rajadas
        - Períodos de congestionamento
        - SLA violations visíveis
        
        Args:
            duration_hours: Duração em horas
        
        Returns:
            List de registros
        """
        print(f"[Generator] Gerando cenário BUSTY ({duration_hours}h)...")
        
        self.data = []
        start_time = datetime.now() - timedelta(hours=duration_hours)
        
        for minute in range(duration_hours * 60):
            current_time = start_time + timedelta(minutes=minute)
            timestamp = int(current_time.timestamp())
            
            hour = current_time.hour
            day_of_week = current_time.weekday()
            
            # Simula rajadas de câmera
            # Ciclo: 1-2 min ON, 3-4 min OFF
            cycle_position = minute % 5
            if cycle_position < 2:
                cameras = random.choice([2, 3])  # Rajada ON
            else:
                cameras = random.randint(0, 1)  # Rajada OFF
            
            # Simula congestionamento periódico
            # A cada ~10 min, há uma rajada que causa latência alta
            burst_cycle = minute % 10
            if burst_cycle < 2 and cameras >= 2:
                latency = random.uniform(100000, 500000)  # SLA VIOLATION
            else:
                latency = self.generate_latency(hour, day_of_week, cameras)
            
            energy_state = self.generate_energy_state(hour, day_of_week, latency, cameras)
            slicer_state = self.generate_slicer_state(latency, cameras)
            decision = self.generate_decision(slicer_state, energy_state, hour)
            
            record = {
                'timestamp': timestamp,
                'datetime': current_time.isoformat(),
                'hour': hour,
                'day_of_week': day_of_week,
                'day_name': DAY_NAMES[day_of_week],
                'period': self.get_period(hour),
                'is_weekend': self.is_weekend(day_of_week),
                'cameras_active': cameras,
                'critical_cameras': 1 if cameras >= 2 and latency > 100000 else 0,
                'latency_us': latency,
                'latency_ms': latency / 1000,
                'energy_state': energy_state,
                'slicer_state': slicer_state,
                'decision': decision,
                'scenario': 'bursty'
            }
            
            self.data.append(record)
        
        print(f"[Generator] Cenário BUSTY gerado: {len(self.data)} registros")
        return self.data
    
    def generate_simulation_snapshot(self, cycles=10, interval_seconds=5):
        """
        Gera um snapshot do comportamento simulado em tempo real.
        
        Útil para testar o loop principal do rApp.
        
        Args:
            cycles: Número de ciclos a gerar
            interval_seconds: Intervalo entre ciclos
        
        Returns:
            Generator que yield registros em tempo quase-real
        """
        print(f"[Generator] Gerando snapshot de {cycles} ciclos...")
        
        base_time = datetime.now()
        
        for cycle in range(cycles):
            # Simula variação ao longo do tempo
            cycle_hour = (base_time + timedelta(seconds=cycle * interval_seconds)).hour
            day_of_week = (base_time + timedelta(seconds=cycle * interval_seconds)).weekday()
            
            cameras = self.generate_camera_activity(cycle_hour, day_of_week)
            latency = self.generate_latency(cycle_hour, day_of_week, cameras)
            energy_state = self.generate_energy_state(cycle_hour, day_of_week, latency, cameras)
            slicer_state = self.generate_slicer_state(latency, cameras)
            decision = self.generate_decision(slicer_state, energy_state, cycle_hour)
            
            yield {
                'cycle': cycle,
                'timestamp': int((base_time + timedelta(seconds=cycle * interval_seconds)).timestamp()),
                'hour': cycle_hour,
                'day_of_week': day_of_week,
                'cameras_active': cameras,
                'latency_us': latency,
                'energy_state': energy_state,
                'slicer_state': slicer_state,
                'decision': decision
            }


def main():
    """Teste do Gerador de Dados Sintéticos"""
    print("=" * 70)
    print("rApp Synthetic Data Generator - Teste")
    print("=" * 70)
    
    # Cria gerador
    gen = SyntheticDataGenerator(seed=42)  # Seed para reprodutibilidade
    
    # Gera dados de uma semana
    print("\n[1] Gerando dados de 1 semana...")
    gen.generate_week_data(resolution_minutes=5, days=7)
    
    # Estatísticas
    gen.print_statistics()
    
    # Exporta para CSV
    print("\n[2] Exportando para CSV...")
    csv_path = gen.export_to_csv()
    
    # Carrega no Data Lake
    print("\n[3] Carregando no Data Lake...")
    gen.load_into_data_lake()
    
    # Cenário BUSTY
    print("\n[4] Gerando cenário BUSTY (24h)...")
    gen.generate_bursty_scenario(duration_hours=24)
    
    # Snapshot de teste
    print("\n[5] Gerando snapshot de simulação...")
    for i, snapshot in enumerate(gen.generate_simulation_snapshot(cycles=10)):
        print(f"    Cycle {i}: cameras={snapshot['cameras_active']}, "
              f"latency={snapshot['latency_us']/1000:.0f}ms, "
              f"state={snapshot['slicer_state']}")
    
    print("\n" + "=" * 70)
    print("Teste concluído!")
    print("=" * 70)


if __name__ == "__main__":
    main()
