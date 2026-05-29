#!/usr/bin/env python3
"""Build an operational control gate manifest for TA-SAM MARL shadow promotion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from rapp_marl_control_gate import compute_control_gate, _load_json

DEFAULT_TASAM_EVAL = PROJECT_ROOT / 'runs' / 'sac_bootstrap' / 'tasam_candidate_evaluation_latest.json'
DEFAULT_RUNTIME_EVAL = PROJECT_ROOT / 'runs' / 'sac_bootstrap' / 'marl_shadow_runtime_eval_latest.json'
DEFAULT_MANUAL_APPROVAL = PROJECT_ROOT / 'runs' / 'sac_bootstrap' / 'marl_control_trial_approval.json'
DEFAULT_OUTPUT = PROJECT_ROOT / 'runs' / 'sac_bootstrap' / 'marl_control_gate_latest.json'


def evaluate_control_gate(
    tasam_eval_path: str | Path = DEFAULT_TASAM_EVAL,
    runtime_eval_path: str | Path = DEFAULT_RUNTIME_EVAL,
    manual_approval_path: str | Path = DEFAULT_MANUAL_APPROVAL,
) -> Dict[str, Any]:
    tasam_eval = _load_json(tasam_eval_path)
    runtime_eval = _load_json(runtime_eval_path)
    manual_approval = _load_json(manual_approval_path)
    gate = compute_control_gate(tasam_eval, runtime_eval, manual_approval)
    return {
        'tasam_eval_path': str(tasam_eval_path),
        'runtime_eval_path': str(runtime_eval_path),
        'manual_approval_path': str(manual_approval_path),
        'gate': gate,
    }


def write_payload(payload: Dict[str, Any], output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='Evaluate operational gate for TA-SAM control trial candidacy.')
    parser.add_argument('--tasam-eval', default=str(DEFAULT_TASAM_EVAL), help='TA-SAM candidate evaluation manifest')
    parser.add_argument('--runtime-eval', default=str(DEFAULT_RUNTIME_EVAL), help='MARL shadow runtime evaluation manifest')
    parser.add_argument('--manual-approval', default=str(DEFAULT_MANUAL_APPROVAL), help='Optional manual approval JSON path')
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT), help='Output gate manifest path')
    args = parser.parse_args()

    payload = evaluate_control_gate(
        tasam_eval_path=args.tasam_eval,
        runtime_eval_path=args.runtime_eval,
        manual_approval_path=args.manual_approval,
    )
    write_payload(payload, args.output)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
