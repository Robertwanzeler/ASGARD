#!/usr/bin/env python3
"""Refresh MARL shadow runtime evaluation and control gate manifests periodically."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))
from greenran_paths import RAPP_DB_PATH

from evaluate_marl_shadow_runtime import evaluate_runtime_window, write_payload as write_runtime_payload
from evaluate_marl_control_gate import evaluate_control_gate, write_payload as write_gate_payload

DEFAULT_RUNTIME_OUTPUT = PROJECT_ROOT / 'runs' / 'sac_bootstrap' / 'marl_shadow_runtime_eval_latest.json'
DEFAULT_GATE_OUTPUT = PROJECT_ROOT / 'runs' / 'sac_bootstrap' / 'marl_control_gate_latest.json'
DEFAULT_STATUS_OUTPUT = PROJECT_ROOT / 'runs' / 'sac_bootstrap' / 'marl_runtime_gate_watch_status.json'


def refresh_once(args) -> dict:
    runtime_payload = evaluate_runtime_window(
        db_path=args.db,
        window=args.window,
        min_samples=args.min_samples,
        min_checkpoint_coverage=args.min_checkpoint_coverage,
    )
    write_runtime_payload(runtime_payload, args.runtime_output)

    gate_payload = evaluate_control_gate(
        tasam_eval_path=args.tasam_eval,
        runtime_eval_path=args.runtime_output,
        manual_approval_path=args.manual_approval,
    )
    write_gate_payload(gate_payload, args.gate_output)

    status = {
        'updated_at': int(time.time()),
        'db': str(args.db),
        'runtime_output': str(args.runtime_output),
        'gate_output': str(args.gate_output),
        'runtime_readiness': runtime_payload.get('readiness', 'unknown'),
        'gate_status': ((gate_payload.get('gate') or {}).get('status', 'unknown')),
        'allow_control_trial': bool(((gate_payload.get('gate') or {}).get('allow_control_trial', False))),
        'sample_count': int(((runtime_payload.get('summary') or {}).get('sample_count', 0) or 0)),
        'latest_policy_id': str(((runtime_payload.get('summary') or {}).get('latest_policy_id', '') or '')),
    }
    args.status_output.parent.mkdir(parents=True, exist_ok=True)
    args.status_output.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding='utf-8')
    return {
        'runtime': runtime_payload,
        'gate': gate_payload,
        'status': status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Refresh MARL shadow runtime and control-gate manifests periodically.')
    parser.add_argument('--db', default=str(RAPP_DB_PATH), help='SQLite Data Lake path')
    parser.add_argument('--window', type=int, default=300, help='Recent comparison window')
    parser.add_argument('--min-samples', type=int, default=120, help='Minimum runtime samples for readiness')
    parser.add_argument('--min-checkpoint-coverage', type=float, default=0.90, help='Minimum checkpoint-backed share')
    parser.add_argument('--tasam-eval', default=str(PROJECT_ROOT / 'runs' / 'sac_bootstrap' / 'tasam_candidate_evaluation_latest.json'), help='TA-SAM candidate evaluation manifest')
    parser.add_argument('--manual-approval', default=str(PROJECT_ROOT / 'runs' / 'sac_bootstrap' / 'marl_control_trial_approval.json'), help='Optional manual approval manifest')
    parser.add_argument('--runtime-output', default=str(DEFAULT_RUNTIME_OUTPUT), help='Runtime evaluation output path')
    parser.add_argument('--gate-output', default=str(DEFAULT_GATE_OUTPUT), help='Control gate output path')
    parser.add_argument('--status-output', default=str(DEFAULT_STATUS_OUTPUT), help='Watcher status output path')
    parser.add_argument('--refresh', type=float, default=15.0, help='Refresh interval in seconds')
    parser.add_argument('--once', action='store_true', help='Run one refresh and exit')
    args = parser.parse_args()

    args.db = Path(args.db)
    args.tasam_eval = Path(args.tasam_eval)
    args.manual_approval = Path(args.manual_approval)
    args.runtime_output = Path(args.runtime_output)
    args.gate_output = Path(args.gate_output)
    args.status_output = Path(args.status_output)

    if args.once:
        payload = refresh_once(args)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    while True:
        payload = refresh_once(args)
        print(json.dumps(payload['status'], ensure_ascii=False))
        time.sleep(max(float(args.refresh), 1.0))


if __name__ == '__main__':
    raise SystemExit(main())
