#!/usr/bin/env python3
"""Evaluate recent MARL shadow runtime comparisons against the live allocator."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))
from greenran_paths import RAPP_DB_PATH

DEFAULT_DB = Path(RAPP_DB_PATH)
DEFAULT_OUTPUT = PROJECT_ROOT / 'runs' / 'sac_bootstrap' / 'marl_shadow_runtime_eval_latest.json'


def classify(summary: dict, min_samples: int, min_checkpoint_coverage: float) -> tuple[str, list[str]]:
    reasons = []
    sample_count = int(summary.get('sample_count', 0) or 0)
    checkpoint_coverage = float(summary.get('checkpoint_coverage', 0.0) or 0.0)
    positive_score_rate = float(summary.get('positive_score_rate', 0.0) or 0.0)
    avg_score_delta = float(summary.get('avg_score_delta', 0.0) or 0.0)
    avg_ran_delta = float(summary.get('avg_ran_completion_delta', 0.0) or 0.0)
    avg_ai_delta = float(summary.get('avg_ai_completion_delta', 0.0) or 0.0)
    recommend_rate = float(summary.get('recommend_rate', 0.0) or 0.0)

    if sample_count < min_samples:
        reasons.append(f'insufficient samples: {sample_count} < {min_samples}')
        return 'insufficient_data', reasons
    if checkpoint_coverage < min_checkpoint_coverage:
        reasons.append(f'checkpoint coverage below threshold: {checkpoint_coverage:.3f} < {min_checkpoint_coverage:.3f}')
        return 'insufficient_checkpoint_coverage', reasons
    if positive_score_rate >= 0.60 and avg_score_delta > 0.01 and avg_ran_delta >= 0.0 and avg_ai_delta >= -0.02:
        reasons.append('shadow outperforms live on proxy score with acceptable completion deltas')
        if recommend_rate >= 0.50:
            reasons.append('runtime recommendation rate is consistently positive')
            return 'control_trial_candidate', reasons
        return 'shadow_outperforming', reasons
    if positive_score_rate >= 0.50 and avg_score_delta > 0.0:
        reasons.append('shadow shows promising score improvements but not enough operational margin yet')
        return 'promising', reasons
    reasons.append('shadow is not beating the live allocator consistently on the proxy metrics')
    return 'not_beating_live', reasons


def build_summary(rows: list[sqlite3.Row]) -> dict:
    sample_count = len(rows)
    if not sample_count:
        return {}
    checkpoint_rows = [row for row in rows if (row['source'] or '') in {'checkpoint', 'mixed'}]
    positive_rows = [row for row in rows if float(row['score_delta'] or 0.0) > 0.01]
    recommend_rows = [row for row in rows if int(row['recommend_shadow'] or 0) == 1]
    latest = rows[0]
    avg_score_delta = sum(float(row['score_delta'] or 0.0) for row in rows) / sample_count
    avg_ran_delta = sum(float(row['shadow_ran_completion_est'] or 0.0) - float(row['live_ran_completion_est'] or 0.0) for row in rows) / sample_count
    avg_ai_delta = sum(float(row['shadow_ai_completion_est'] or 0.0) - float(row['live_ai_completion_est'] or 0.0) for row in rows) / sample_count
    return {
        'sample_count': sample_count,
        'checkpoint_coverage': round(len(checkpoint_rows) / sample_count, 4),
        'positive_score_rate': round(len(positive_rows) / sample_count, 4),
        'recommend_rate': round(len(recommend_rows) / sample_count, 4),
        'avg_score_delta': round(avg_score_delta, 6),
        'avg_ran_completion_delta': round(avg_ran_delta, 4),
        'avg_ai_completion_delta': round(avg_ai_delta, 4),
        'latest_policy_id': latest['policy_id'] or '',
        'latest_source': latest['source'] or '',
        'latest_readiness': latest['checkpoint_readiness'] or '',
        'latest_score_delta': round(float(latest['score_delta'] or 0.0), 6),
        'latest_recommend_shadow': bool(latest['recommend_shadow']),
        'latest_datetime': latest['datetime'],
    }


def evaluate_runtime_window(
    db_path: str | Path = DEFAULT_DB,
    window: int = 300,
    min_samples: int = 120,
    min_checkpoint_coverage: float = 0.90,
) -> Dict[str, Any]:
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                timestamp,
                datetime,
                policy_id,
                source,
                checkpoint_readiness,
                available,
                recommend_shadow,
                live_score,
                shadow_score,
                score_delta,
                live_ran_completion_est,
                shadow_ran_completion_est,
                live_ai_completion_est,
                shadow_ai_completion_est
            FROM marl_shadow_comparison_history
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (int(window),),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    summary = build_summary(rows)
    readiness, reasons = classify(summary, min_samples, min_checkpoint_coverage) if summary else ('no_data', ['no MARL shadow runtime comparisons found'])
    return {
        'db': str(db_path),
        'window': int(window),
        'min_samples': int(min_samples),
        'min_checkpoint_coverage': float(min_checkpoint_coverage),
        'readiness': readiness,
        'reasons': reasons,
        'summary': summary,
    }


def write_payload(payload: Dict[str, Any], output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='Evaluate MARL shadow runtime history against the live allocator.')
    parser.add_argument('--db', default=str(DEFAULT_DB), help='SQLite Data Lake path')
    parser.add_argument('--window', type=int, default=300, help='Number of most recent comparisons to inspect')
    parser.add_argument('--min-samples', type=int, default=120, help='Minimum rows required for promotion analysis')
    parser.add_argument('--min-checkpoint-coverage', type=float, default=0.90, help='Minimum share of checkpoint-backed rows')
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT), help='JSON output path')
    parser.add_argument('--once', action='store_true', help='Print summary only; still writes output')
    args = parser.parse_args()

    payload = evaluate_runtime_window(
        db_path=args.db,
        window=args.window,
        min_samples=args.min_samples,
        min_checkpoint_coverage=args.min_checkpoint_coverage,
    )
    write_payload(payload, args.output)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
