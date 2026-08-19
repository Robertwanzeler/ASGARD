#!/usr/bin/env python3
"""Evaluate TA-SAM MARL checkpoint summaries and classify promotion readiness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Evaluate TA-SAM candidate summaries')
    parser.add_argument('--runs-root', default='runs/sac_bootstrap', help='Root directory containing tasam_marl_* runs')
    parser.add_argument('--output', default='runs/sac_bootstrap/tasam_candidate_evaluation_latest.json', help='Output JSON path')
    return parser


def classify(summary: dict) -> dict:
    history = summary.get('history', []) or []
    final = summary.get('final_metrics', {}) or {}
    critic_loss = float(final.get('critic_loss', 1.0) or 1.0)
    selected_fraction = float(final.get('selected_fraction', 0.0) or 0.0)
    bc_loss = float(final.get('bc_loss', 1.0) or 1.0)
    action_var_mean = float(final.get('action_var_mean', 0.0) or 0.0)
    nonzero_epochs = sum(1 for h in history if float(h.get('selected_agents', 0.0) or 0.0) > 0.0)
    post_warmup = [h for h in history if not bool(h.get('warmup', False))]
    post_warmup_nonzero = sum(1 for h in post_warmup if float(h.get('selected_agents', 0.0) or 0.0) > 0.0)
    all_post_warmup_full = bool(post_warmup) and all(float(h.get('selected_fraction', 0.0) or 0.0) >= 0.99 for h in post_warmup)
    effective_thresholds = [float(h.get('effective_td_var_threshold', 0.0) or 0.0) for h in post_warmup]
    dynamic_threshold_active = any(v > 0.0 for v in effective_thresholds)

    readiness = 'not_ready'
    reasons = []
    if nonzero_epochs == 0:
        reasons.append('sem atualizacao de atores')
    selector_still_full = all_post_warmup_full
    if critic_loss > 5e-4:
        reasons.append('critic_loss acima do alvo de shadow')
    if post_warmup_nonzero == 0:
        reasons.append('sem atualizacao apos warmup')
    if bc_loss > 0.08:
        reasons.append('bc_loss alto; ator ainda nao imita a alocacao viva')
    if action_var_mean < 0.002:
        reasons.append('ator colapsado para acoes quase uniformes')

    hard_reasons = [r for r in reasons if r != 'seletor ainda em modo sempre-seleciona']
    if not hard_reasons and selector_still_full:
        if critic_loss <= 5e-4 and bc_loss <= 0.02 and action_var_mean >= 0.01 and len(history) >= 5:
            readiness = 'shadow_ready'
            reasons = ['seletor ainda em modo sempre-seleciona']
        else:
            reasons = hard_reasons + ['seletor ainda em modo sempre-seleciona']
    elif not reasons:
        if critic_loss <= 3e-4 and bc_loss <= 0.04 and 0.05 <= selected_fraction <= 0.25 and action_var_mean >= 0.005 and dynamic_threshold_active and len(history) >= 5:
            readiness = 'control_candidate'
        else:
            readiness = 'shadow_ready'
    elif nonzero_epochs > 0 and post_warmup_nonzero > 0 and bc_loss <= 0.12:
        readiness = 'bootstrap_partial'

    return {
        'trace_jsonl': summary.get('trace_jsonl', ''),
        'epochs': summary.get('epochs', 0),
        'du_count': summary.get('du_count', 0),
        'final_metrics': final,
        'nonzero_epochs': nonzero_epochs,
        'post_warmup_nonzero_epochs': post_warmup_nonzero,
        'dynamic_threshold_active': dynamic_threshold_active,
        'readiness': readiness,
        'reasons': reasons,
        'promote_shadow': readiness in {'shadow_ready', 'control_candidate'},
        'promote_control_candidate': readiness == 'control_candidate',
    }


def main() -> int:
    args = build_parser().parse_args()
    runs_root = Path(args.runs_root)
    output = Path(args.output)
    summaries = sorted(runs_root.glob('tasam_marl_*/tasam_marl_summary.json'))
    evaluations = []
    for summary_path in summaries:
        try:
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        record = classify(summary)
        record['run_dir'] = str(summary_path.parent.resolve())
        record['summary_path'] = str(summary_path.resolve())
        evaluations.append(record)

    best = None
    scored = []
    for ev in evaluations:
        final = ev.get('final_metrics', {}) or {}
        score = 0.0
        if ev['readiness'] == 'control_candidate':
            score += 3.0
        elif ev['readiness'] == 'shadow_ready':
            score += 2.0
        elif ev['readiness'] == 'bootstrap_partial':
            score += 1.0
        score += max(0.0, 0.001 - float(final.get('critic_loss', 1.0) or 1.0))
        score += max(0.0, 0.10 - float(final.get('bc_loss', 1.0) or 1.0))
        score += float(final.get('action_var_mean', 0.0) or 0.0)
        scored.append((score, ev))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        best = scored[0][1]

    payload = {
        'runs_root': str(runs_root.resolve()),
        'evaluated_runs': evaluations,
        'best_run': best,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'evaluated': len(evaluations), 'best_readiness': (best or {}).get('readiness', 'none'), 'best_run': (best or {}).get('run_dir', '')}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
