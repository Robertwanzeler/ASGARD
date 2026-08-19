#!/usr/bin/env python3
"""Run a multi-seed TA-SAM offline epoch sweep with checkpoint aggregation."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'runs' / 'tasam_article_epoch_sweep'


def parse_csv_ints(value: str) -> tuple[int, ...]:
    values = tuple(int(part.strip()) for part in value.split(',') if part.strip())
    if not values:
        raise argparse.ArgumentTypeError('expected at least one integer value')
    if any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError('values must be positive integers')
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run TA-SAM offline epoch sweep with multiple seeds')
    parser.add_argument('--trace-jsonl', required=True, help='Offline trace JSONL to train on')
    parser.add_argument('--output-root', default=str(DEFAULT_OUTPUT), help='Sweep output directory')
    parser.add_argument('--seeds', type=parse_csv_ints, default=(42, 43, 44, 45, 46), help='Comma-separated seed list')
    parser.add_argument('--mode', default='tasam_selective', choices=('no_sam', 'tasam_selective'), help='Mode for this GreenRAN comparison sweep')
    parser.add_argument('--train-python', default=None, help='Training Python executable')
    parser.add_argument('--max-epochs', type=int, default=200, help='Maximum epochs per seed')
    parser.add_argument('--checkpoint-every', type=int, default=10, help='Checkpoint cadence')
    parser.add_argument('--milestone-epochs', default='25,50,100,150,200', help='Epoch checkpoints to always preserve')
    parser.add_argument('--resume-ignore-early-stop', action='store_true', help='Force resume even when the saved checkpoint had stopped_early=true')
    parser.add_argument('--early-stop-patience-checkpoints', type=int, default=2, help='Stop after this many plateau checkpoints')
    parser.add_argument('--early-stop-min-epoch', type=int, default=50, help='Do not early-stop before this epoch')
    parser.add_argument('--early-stop-min-improvement-pct', type=float, default=2.0, help='Minimum eval_return improvement to reset plateau')
    parser.add_argument('--epochs', type=int, default=25, help='Compatibility base epochs value passed to the trainer')
    parser.add_argument('--trainer-backend', default='article_sac', choices=('article_sac',), help='Trainer backend')
    parser.add_argument('--lr', type=float, default=1e-4, help='Learning rate')
    parser.add_argument('--alpha-lr', type=float, default=1e-4, help='Entropy-temperature learning rate')
    parser.add_argument('--td-var-threshold', type=float, default=0.01, help='Selective SAM threshold')
    parser.add_argument('--min-selected-fraction', type=float, default=0.10, help='Minimum selected fraction')
    parser.add_argument('--warmup-epochs', type=int, default=2, help='Warmup epochs')
    parser.add_argument('--bc-weight', type=float, default=0.0, help='Behavior cloning anchor weight')
    parser.add_argument('--value-weight', type=float, default=0.0, help='Value weight')
    parser.add_argument('--gamma', type=float, default=0.99, help='Discount factor')
    parser.add_argument('--tau', type=float, default=0.01, help='Target critic soft-update factor')
    parser.add_argument('--alpha-init', type=float, default=0.03, help='Initial entropy temperature')
    parser.add_argument('--target-entropy-scale', type=float, default=1.0, help='Target entropy scale')
    parser.add_argument('--batch-size', type=int, default=128, help='Mini-batch size')
    parser.add_argument('--actor-update-interval', type=int, default=1, help='Actor update interval')
    parser.add_argument('--article-hidden', action='store_true', default=True, help='Use paper hidden layout')
    parser.add_argument('--no-article-hidden', action='store_false', dest='article_hidden', help='Disable paper hidden layout')
    parser.add_argument('--activation', default='tanh', choices=('relu', 'tanh'), help='Hidden-layer activation')
    parser.add_argument('--actor-sam-rho', type=float, default=0.5, help='Actor initial SAM rho')
    parser.add_argument('--actor-sam-rho-final', type=float, default=0.01, help='Actor final SAM rho')
    parser.add_argument('--critic-sam-rho', type=float, default=0.5, help='Critic initial SAM rho')
    parser.add_argument('--critic-sam-rho-final', type=float, default=0.01, help='Critic final SAM rho')
    parser.add_argument('--dry-run', action='store_true', help='Print commands without executing them')
    parser.add_argument('--expected-topology-id', default='greenran_fixed_marl_v1', help='Topology required by the GreenRAN policy')
    parser.add_argument('--expected-du-count', type=int, default=3, help='Logical DU count required by the GreenRAN policy')
    parser.add_argument('--min-transitions', type=int, default=1500, help='Minimum valid transitions required by the quality gate')
    parser.add_argument('--skip-quality-gate', action='store_true', help='Skip the dataset gate only for a smoke run')
    return parser


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _checkpoint_rank(record: dict[str, Any]) -> tuple[float, float, float, int]:
    metrics = record.get('metrics') or {}
    return (
        _safe_float(metrics.get('eval_return'), 0.0),
        _safe_float(metrics.get('cumulative_return'), 0.0),
        -_safe_float(metrics.get('critic_loss'), float('inf')),
        -int(record.get('epoch', 0) or 0),
    )


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {'mean': 0.0, 'stdev': 0.0}
    if len(values) == 1:
        return {'mean': float(values[0]), 'stdev': 0.0}
    return {'mean': float(statistics.mean(values)), 'stdev': float(statistics.pstdev(values))}


def _seed_dir(output_root: Path, seed: int) -> Path:
    return output_root / f'seed_{int(seed):04d}'


def _mode_dir(output_root: Path, seed: int, mode: str) -> Path:
    return _seed_dir(output_root, seed) / mode


def build_train_command(args: argparse.Namespace, seed: int) -> list[str]:
    train_python = args.train_python or sys.executable
    mode_dir = _mode_dir(Path(args.output_root), seed, args.mode)
    cmd = [
        train_python,
        str(ROOT / 'drlexp' / 'training' / 'train_tasam_marl.py'),
        '--trace-jsonl',
        str(Path(args.trace_jsonl).resolve()),
        '--output-dir',
        str(mode_dir),
        '--epochs',
        str(args.epochs),
        '--max-epochs',
        str(args.max_epochs),
        '--trainer-backend',
        args.trainer_backend,
        '--sam-mode',
        args.mode,
        '--lr',
        str(args.lr),
        '--alpha-lr',
        str(args.alpha_lr),
        '--td-var-threshold',
        str(args.td_var_threshold),
        '--min-selected-fraction',
        str(args.min_selected_fraction),
        '--warmup-epochs',
        str(args.warmup_epochs),
        '--bc-weight',
        str(args.bc_weight),
        '--value-weight',
        str(args.value_weight),
        '--gamma',
        str(args.gamma),
        '--tau',
        str(args.tau),
        '--alpha-init',
        str(args.alpha_init),
        '--target-entropy-scale',
        str(args.target_entropy_scale),
        '--batch-size',
        str(args.batch_size),
        '--actor-update-interval',
        str(args.actor_update_interval),
        '--activation',
        args.activation,
        '--actor-sam-rho',
        str(args.actor_sam_rho),
        '--actor-sam-rho-final',
        str(args.actor_sam_rho_final),
        '--critic-sam-rho',
        str(args.critic_sam_rho),
        '--critic-sam-rho-final',
        str(args.critic_sam_rho_final),
        '--seed',
        str(seed),
        '--checkpoint-every',
        str(args.checkpoint_every),
        '--milestone-epochs',
        args.milestone_epochs,
        '--resume',
    ]
    if args.resume_ignore_early_stop:
        cmd.append('--resume-ignore-early-stop')
    cmd.extend(
        [
            '--early-stop-patience-checkpoints',
            str(args.early_stop_patience_checkpoints),
            '--early-stop-min-epoch',
            str(args.early_stop_min_epoch),
            '--early-stop-min-improvement-pct',
            str(args.early_stop_min_improvement_pct),
        ]
    )
    if args.article_hidden:
        cmd.append('--article-hidden')
    return cmd


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _collect_checkpoint_records(mode_dir: Path) -> list[dict[str, Any]]:
    summary = _load_json(mode_dir / 'tasam_marl_summary.json')
    checkpoints = list(summary.get('checkpoint_records') or [])
    if checkpoints:
        return checkpoints

    records: list[dict[str, Any]] = []
    for checkpoint_dir in sorted((mode_dir / 'checkpoints').glob('epoch_*')):
        checkpoint_summary = _load_json(checkpoint_dir / 'tasam_marl_summary.json')
        if not checkpoint_summary:
            continue
        metrics = checkpoint_summary.get('final_metrics') or {}
        records.append(
            {
                'epoch': int(metrics.get('epoch', 0) or 0),
                'checkpoint_dir': str(checkpoint_dir.resolve()),
                'summary_path': str((checkpoint_dir / 'tasam_marl_summary.json').resolve()),
                'metrics': metrics,
            }
        )
    return sorted(records, key=lambda item: int(item.get('epoch', 0) or 0))


def build_sweep_summary(output_root: Path, trace_jsonl: Path, mode: str, seeds: tuple[int, ...]) -> dict[str, Any]:
    seed_rows: list[dict[str, Any]] = []
    checkpoints_by_epoch: dict[int, list[dict[str, Any]]] = {}
    global_best: dict[str, Any] | None = None

    for seed in seeds:
        mode_dir = _mode_dir(output_root, seed, mode)
        summary = _load_json(mode_dir / 'tasam_marl_summary.json')
        if not summary:
            continue
        checkpoint_records = _collect_checkpoint_records(mode_dir)
        for record in checkpoint_records:
            epoch = int(record.get('epoch', 0) or 0)
            checkpoints_by_epoch.setdefault(epoch, []).append(
                {
                    'seed': int(seed),
                    'epoch': epoch,
                    'checkpoint_dir': record.get('checkpoint_dir', ''),
                    'summary_path': record.get('summary_path', ''),
                    'metrics': record.get('metrics') or {},
                }
            )
            candidate = checkpoints_by_epoch[epoch][-1]
            if global_best is None or _checkpoint_rank(candidate) > _checkpoint_rank(global_best):
                global_best = candidate

        best_checkpoint = summary.get('best_checkpoint') or None
        seed_rows.append(
            {
                'seed': int(seed),
                'run_dir': str(mode_dir.resolve()),
                'completed_epochs': int(summary.get('completed_epochs', summary.get('epochs', 0)) or 0),
                'target_epochs': int(summary.get('target_epochs', summary.get('epochs', 0)) or 0),
                'stopped_early': bool(summary.get('stopped_early', False)),
                'stop_reason': str(summary.get('stop_reason', '') or ''),
                'checkpoint_count': len(checkpoint_records),
                'best_checkpoint': best_checkpoint,
            }
        )

    epoch_aggregate: list[dict[str, Any]] = []
    best_epoch_by_mean: dict[str, Any] | None = None
    for epoch in sorted(checkpoints_by_epoch):
        rows = checkpoints_by_epoch[epoch]
        eval_stats = _stats([_safe_float((row.get('metrics') or {}).get('eval_return'), 0.0) for row in rows])
        cumulative_stats = _stats([_safe_float((row.get('metrics') or {}).get('cumulative_return'), 0.0) for row in rows])
        critic_stats = _stats([_safe_float((row.get('metrics') or {}).get('critic_loss'), 0.0) for row in rows])
        action_var_stats = _stats([_safe_float((row.get('metrics') or {}).get('action_var_mean'), 0.0) for row in rows])
        selected_stats = _stats([_safe_float((row.get('metrics') or {}).get('selected_fraction'), 0.0) for row in rows])
        payload = {
            'epoch': int(epoch),
            'seed_count': len(rows),
            'eval_return_mean': eval_stats['mean'],
            'eval_return_stdev': eval_stats['stdev'],
            'cumulative_return_mean': cumulative_stats['mean'],
            'cumulative_return_stdev': cumulative_stats['stdev'],
            'critic_loss_mean': critic_stats['mean'],
            'critic_loss_stdev': critic_stats['stdev'],
            'action_var_mean': action_var_stats['mean'],
            'action_var_stdev': action_var_stats['stdev'],
            'selected_fraction_mean': selected_stats['mean'],
            'selected_fraction_stdev': selected_stats['stdev'],
        }
        epoch_aggregate.append(payload)
        candidate = {
            'epoch': int(epoch),
            'metrics': {
                'eval_return': payload['eval_return_mean'],
                'cumulative_return': payload['cumulative_return_mean'],
                'critic_loss': payload['critic_loss_mean'],
            },
        }
        if best_epoch_by_mean is None or _checkpoint_rank(candidate) > _checkpoint_rank(best_epoch_by_mean):
            best_epoch_by_mean = candidate

    return {
        'schema': 'greenran.tasam_epoch_sweep.v1',
        'output_root': str(output_root.resolve()),
        'trace_jsonl': str(trace_jsonl.resolve()),
        'mode': mode,
        'seeds': [int(seed) for seed in seeds],
        'seed_count': len(seed_rows),
        'seed_runs': seed_rows,
        'epoch_aggregate': epoch_aggregate,
        'best_epoch_by_mean': best_epoch_by_mean,
        'best_global_checkpoint': global_best,
    }


def write_sweep_summary(output_root: Path, payload: dict[str, Any]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / 'offline_epoch_sweep_summary.json').write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )


def main() -> int:
    args = build_parser().parse_args()
    output_root = Path(args.output_root)
    trace_jsonl = Path(args.trace_jsonl)
    if not args.dry_run and not trace_jsonl.exists():
        raise SystemExit(f'trace not found: {trace_jsonl}')

    if not args.dry_run and not args.skip_quality_gate:
        quality_report = output_root / 'tasam_dataset_quality.json'
        validate_cmd = [
            sys.executable,
            str(ROOT / 'scripts' / 'validate_tasam_dataset.py'),
            '--trace-jsonl', str(trace_jsonl),
            '--output-json', str(quality_report),
            '--expected-topology-id', str(args.expected_topology_id),
            '--expected-du-count', str(args.expected_du_count),
            '--min-transitions', str(args.min_transitions),
        ]
        print(' '.join(validate_cmd), flush=True)
        subprocess.run(validate_cmd, cwd=ROOT, check=True)

    for seed in args.seeds:
        cmd = build_train_command(args, seed)
        print(' '.join(cmd), flush=True)
        if not args.dry_run:
            subprocess.run(cmd, cwd=ROOT, check=True)
            summary = build_sweep_summary(output_root, trace_jsonl, args.mode, args.seeds)
            write_sweep_summary(output_root, summary)

    if args.dry_run:
        return 0

    summary = build_sweep_summary(output_root, trace_jsonl, args.mode, args.seeds)
    write_sweep_summary(output_root, summary)
    print(
        json.dumps(
            {
                'seed_count': summary.get('seed_count', 0),
                'best_epoch_by_mean': ((summary.get('best_epoch_by_mean') or {}).get('epoch')),
                'best_global_checkpoint': ((summary.get('best_global_checkpoint') or {}).get('checkpoint_dir', '')),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
