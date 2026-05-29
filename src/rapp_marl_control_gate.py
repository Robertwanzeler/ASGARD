#!/usr/bin/env python3
"""Operational gate for promoting TA-SAM MARL from shadow observation to control trial."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _load_json(path: str | Path | None) -> dict:
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return {}


def _extract_policy_tag(policy_id: str) -> str:
    policy_id = str(policy_id or '').strip()
    if ':' in policy_id:
        return policy_id.split(':', 1)[1]
    return policy_id


def _approval_valid(approval: dict, expected_policy_id: str, expected_run_dir: str) -> Tuple[bool, str]:
    if not approval:
        return False, 'manual approval file missing'
    if not bool(approval.get('approved', False)):
        return False, 'manual approval not granted'
    approved_policy_id = str(approval.get('approved_policy_id', '') or '')
    approved_run_dir = str(approval.get('approved_run_dir', '') or '')
    if approved_policy_id and approved_policy_id != expected_policy_id:
        return False, 'manual approval policy_id mismatch'
    if approved_run_dir and approved_run_dir != expected_run_dir:
        return False, 'manual approval run_dir mismatch'
    expires_at = str(approval.get('expires_at', '') or '').strip()
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry < now:
                return False, 'manual approval expired'
        except Exception:
            return False, 'manual approval expiry invalid'
    return True, 'manual approval valid'


def compute_control_gate(
    tasam_eval: Dict[str, Any] | None,
    runtime_eval: Dict[str, Any] | None,
    manual_approval: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    tasam_eval = tasam_eval or {}
    runtime_eval = runtime_eval or {}
    manual_approval = manual_approval or {}
    reasons: List[str] = []

    best_run = tasam_eval.get('best_run') or {}
    training_ready = bool(best_run.get('promote_shadow', False))
    control_candidate_ready = bool(best_run.get('promote_control_candidate', False))
    training_readiness = str(best_run.get('readiness', 'unknown') or 'unknown')
    best_run_dir = str(best_run.get('run_dir', '') or '')

    runtime_readiness = str(runtime_eval.get('readiness', 'unknown') or 'unknown')
    runtime_summary = runtime_eval.get('summary') or {}
    runtime_policy_id = str(runtime_summary.get('latest_policy_id', '') or '')
    policy_tag = _extract_policy_tag(runtime_policy_id)
    expected_tag = Path(best_run_dir).name if best_run_dir else ''

    if not training_ready:
        reasons.append('TA-SAM training candidate not approved for shadow promotion')
        return {
            'status': 'blocked',
            'allow_shadow': False,
            'allow_control_trial': False,
            'manual_approval_required': True,
            'manual_approval_valid': False,
            'training_readiness': training_readiness,
            'runtime_readiness': runtime_readiness,
            'policy_id': runtime_policy_id,
            'run_dir': best_run_dir,
            'reasons': reasons,
        }

    if expected_tag and policy_tag and policy_tag != expected_tag:
        reasons.append('runtime policy does not match the best evaluated TA-SAM checkpoint')
        return {
            'status': 'shadow_only',
            'allow_shadow': True,
            'allow_control_trial': False,
            'manual_approval_required': True,
            'manual_approval_valid': False,
            'training_readiness': training_readiness,
            'runtime_readiness': runtime_readiness,
            'policy_id': runtime_policy_id,
            'run_dir': best_run_dir,
            'reasons': reasons,
        }

    if not control_candidate_ready:
        reasons.append('TA-SAM training is not yet a control candidate')
        return {
            'status': 'shadow_only',
            'allow_shadow': True,
            'allow_control_trial': False,
            'manual_approval_required': True,
            'manual_approval_valid': False,
            'training_readiness': training_readiness,
            'runtime_readiness': runtime_readiness,
            'policy_id': runtime_policy_id,
            'run_dir': best_run_dir,
            'reasons': reasons,
        }

    if runtime_readiness not in {'shadow_outperforming', 'control_trial_candidate'}:
        reasons.append('runtime shadow evaluation is not outperforming the live allocator yet')
        return {
            'status': 'shadow_only',
            'allow_shadow': True,
            'allow_control_trial': False,
            'manual_approval_required': True,
            'manual_approval_valid': False,
            'training_readiness': training_readiness,
            'runtime_readiness': runtime_readiness,
            'policy_id': runtime_policy_id,
            'run_dir': best_run_dir,
            'reasons': reasons,
        }

    approval_valid, approval_reason = _approval_valid(manual_approval, runtime_policy_id, best_run_dir)
    reasons.append(approval_reason)
    if approval_valid:
        status = 'trial_approved'
        allow_control_trial = True
    else:
        status = 'trial_candidate'
        allow_control_trial = False

    return {
        'status': status,
        'allow_shadow': True,
        'allow_control_trial': allow_control_trial,
        'manual_approval_required': True,
        'manual_approval_valid': approval_valid,
        'training_readiness': training_readiness,
        'runtime_readiness': runtime_readiness,
        'policy_id': runtime_policy_id,
        'run_dir': best_run_dir,
        'reasons': reasons,
    }


__all__ = ['compute_control_gate', '_load_json']
