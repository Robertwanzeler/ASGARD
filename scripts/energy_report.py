#!/usr/bin/env python3
"""Relatorio de energia por celula: media ponderada de TxPowerPercent no tempo
a partir de TasamControlObservations.csv (state_snapshot / power_readback).

Uso: python3 scripts/energy_report.py runs/<dir> [runs/<dir2> ...]
Saida por run: % nominal medio por celula + total (proxy de energia consumida
pelos DUs mmWave; 100% = potencia nominal o tempo todo).
"""
import sys
import collections
from pathlib import Path


def energy_for_run(run_dir: Path):
    obs = run_dir / 'TasamControlObservations.csv'
    if not obs.is_file():
        obs = run_dir / 'ns3_energy' / 'TasamControlObservations.csv'
    if not obs.is_file():
        return None
    per_cell_time = collections.defaultdict(float)  # cell -> soma(percent * dt)
    per_cell_span = collections.defaultdict(float)  # cell -> dt total observado
    last = {}  # cell -> (t, percent)
    for raw in obs.read_text(errors='replace').splitlines():
        if not raw or raw.startswith('%'):
            continue
        f = raw.split(',')
        if len(f) < 6:
            continue
        try:
            t = float(f[0]); cell = int(f[1]); pct = float(f[5])
        except (ValueError, IndexError):
            continue
        prev = last.get(cell)
        if prev is not None:
            dt = max(0.0, min(t - prev[0], 2.0))  # ignora gaps grandes (boot/fim)
            per_cell_time[cell] += prev[1] * dt
            per_cell_span[cell] += dt
        last[cell] = (t, pct)
    if not per_cell_span:
        return None
    totals = {}
    for cell in sorted(per_cell_span):
        span = per_cell_span[cell]
        totals[cell] = per_cell_time[cell] / span if span else 0.0
    weighted = sum(per_cell_time.values()) / sum(per_cell_span.values())
    return totals, weighted


if __name__ == '__main__':
    for arg in sys.argv[1:]:
        run = Path(arg)
        res = energy_for_run(run)
        if res is None:
            print(f'{run.name}: sem TasamControlObservations.csv')
            continue
        totals, weighted = res
        cells = ' '.join(f'cell{c}={v:.1f}%' for c, v in totals.items())
        print(f'{run.name}: media ponderada={weighted:.2f}% | {cells}')
