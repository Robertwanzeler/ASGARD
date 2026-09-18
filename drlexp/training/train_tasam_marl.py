#!/usr/bin/env python3
"""Train TA-SAM MARL backends for GreenRAN article reproduction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TRAINING_DIR = Path(__file__).resolve().parent
DRLEXP_SRC = TRAINING_DIR.parent / 'src'
sys.path.insert(0, str(DRLEXP_SRC))

import torch

from drl.ta_sam_marl import TASAMMultiAgentTrainer as ScaffoldTASAMMultiAgentTrainer
from drl.ta_sam_marl import load_marl_trace as load_scaffold_trace
from drl.ta_sam_marl_sac import TASAMArticleSACTrainer, load_marl_transition_trace


def parse_hidden_dims(value: str) -> tuple[int, ...]:
    dims = tuple(int(part.strip()) for part in value.split(',') if part.strip())
    if not dims:
        raise argparse.ArgumentTypeError('expected at least one hidden dimension')
    if any(dim <= 0 for dim in dims):
        raise argparse.ArgumentTypeError('hidden dimensions must be positive integers')
    return dims


def parse_epoch_points(value: str) -> tuple[int, ...]:
    values = sorted({int(part.strip()) for part in value.split(',') if part.strip()})
    if not values:
        return ()
    if any(value <= 0 for value in values):
        raise argparse.ArgumentTypeError('epoch checkpoints must be positive integers')
    return tuple(values)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _checkpoint_rank(metrics: dict[str, Any], epoch: int) -> tuple[float, float, float, int]:
    return (
        _safe_float(metrics.get('eval_return'), 0.0),
        _safe_float(metrics.get('cumulative_return'), 0.0),
        -_safe_float(metrics.get('critic_loss'), float('inf')),
        -int(epoch),
    )


def _improvement_pct(current: float, previous: float) -> float:
    current = _safe_float(current, 0.0)
    previous = _safe_float(previous, 0.0)
    if previous == 0.0:
        return 100.0 if current > 0.0 else 0.0
    return ((current - previous) / abs(previous)) * 100.0


def _stable_against_previous(current_metrics: dict[str, Any], previous_metrics: dict[str, Any] | None) -> bool:
    if previous_metrics is None:
        return True
    current_critic = _safe_float(current_metrics.get('critic_loss'), 0.0)
    previous_critic = max(_safe_float(previous_metrics.get('critic_loss'), 0.0), 1e-12)
    current_action_var = _safe_float(current_metrics.get('action_var_mean'), 0.0)
    previous_action_var = _safe_float(previous_metrics.get('action_var_mean'), 0.0)
    current_selected = _safe_float(current_metrics.get('selected_fraction'), 0.0)

    critic_ok = current_critic <= previous_critic * 1.5
    action_var_ok = previous_action_var <= 0.0 or current_action_var >= previous_action_var * 0.5
    selected_ok = current_selected > 0.0
    return critic_ok and action_var_ok and selected_ok


def _should_export_checkpoint(
    epoch: int,
    *,
    total_epochs: int,
    checkpoint_every: int,
    milestone_epochs: tuple[int, ...],
) -> bool:
    if epoch >= int(total_epochs):
        return True
    if epoch in milestone_epochs:
        return True
    return checkpoint_every > 0 and (epoch % checkpoint_every == 0)


def _write_history_jsonl(path: Path, history: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in history)
    path.write_text(text, encoding='utf-8')


def _append_history_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + '\n')


def _build_summary(
    args: argparse.Namespace,
    *,
    output_dir: Path,
    trace_jsonl: str,
    total_epochs: int,
    completed_epochs: int,
    du_count: int,
    du_state_dim: int,
    global_state_dim: int,
    actor_hidden_dims: tuple[int, ...],
    critic_hidden_dims: tuple[int, ...],
    rhos: dict[str, float],
    history: list[dict[str, Any]],
    checkpoint_records: list[dict[str, Any]],
    best_checkpoint: dict[str, Any] | None,
    stopped_early: bool,
    stop_reason: str,
    resume_checkpoint: Path,
    resume_ignore_early_stop: bool = False,
    resumed_from_stop_reason: str = '',
) -> dict[str, Any]:
    final_metrics = history[-1] if history else {}
    return {
        'trace_jsonl': str(Path(trace_jsonl).resolve()),
        'epochs': completed_epochs,
        'completed_epochs': completed_epochs,
        'target_epochs': total_epochs,
        'du_count': du_count,
        'du_state_dim': du_state_dim,
        'global_state_dim': global_state_dim,
        'trainer_backend': args.trainer_backend,
        'article_faithful_algorithm': args.trainer_backend == 'article_sac',
        'experiment_track': 'offline_trace',
        'sam_mode': args.sam_mode,
        'sam_rho': args.sam_rho,
        'sam_rho_final': args.sam_rho_final if args.sam_rho_final is not None else args.sam_rho,
        'actor_sam_rho': rhos['actor_rho'],
        'actor_sam_rho_final': rhos['actor_rho_final'],
        'critic_sam_rho': rhos['critic_rho'],
        'critic_sam_rho_final': rhos['critic_rho_final'],
        'l2_weight': args.l2_weight,
        'actor_hidden_dims': list(actor_hidden_dims),
        'critic_hidden_dims': list(critic_hidden_dims),
        'activation': args.activation,
        'requested_td_var_threshold': args.td_var_threshold,
        'min_selected_fraction': args.min_selected_fraction,
        'warmup_epochs': args.warmup_epochs,
        'bc_weight': args.bc_weight,
        'value_weight': args.value_weight,
        'gamma': args.gamma,
        'tau': args.tau,
        'alpha_init': args.alpha_init,
        'alpha_lr': args.alpha_lr,
        'target_entropy_scale': args.target_entropy_scale,
        'batch_size': args.batch_size,
        'updates_per_epoch': args.updates_per_epoch,
        'actor_update_interval': args.actor_update_interval,
        'category_loss_weight': args.category_loss_weight,
        'category_head_hidden_dim': args.category_head_hidden_dim,
        'temporal_dim': args.temporal_dim,
        'power_head_hidden_dim': args.power_head_hidden_dim,
        'power_head_lr': args.power_head_lr,
        'power_head_steps': args.power_head_steps,
        'allocation_head_hidden_dim': args.allocation_head_hidden_dim,
        'allocation_head_lr': args.allocation_head_lr,
        'allocation_head_steps': args.allocation_head_steps,
        'seed': args.seed,
        'checkpoint_every': args.checkpoint_every,
        'milestone_epochs': list(args.milestone_epochs),
        'resume_enabled': bool(args.resume),
        'resume_checkpoint': str(resume_checkpoint.resolve()),
        'resume_ignore_early_stop': bool(resume_ignore_early_stop),
        'resumed_from_stop_reason': str(resumed_from_stop_reason),
        'early_stop_patience_checkpoints': args.early_stop_patience_checkpoints,
        'early_stop_min_epoch': args.early_stop_min_epoch,
        'early_stop_min_improvement_pct': args.early_stop_min_improvement_pct,
        'stopped_early': bool(stopped_early),
        'stop_reason': str(stop_reason),
        'checkpoint_records': checkpoint_records,
        'best_checkpoint': best_checkpoint,
        'history_jsonl': str((output_dir / 'epoch_history.jsonl').resolve()),
        'checkpoint_root': str((output_dir / 'checkpoints').resolve()),
        'final_metrics': final_metrics,
        'history': history,
    }


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def _checkpoint_record(
    *,
    epoch: int,
    checkpoint_dir: Path,
    metrics: dict[str, Any],
    previous_checkpoint_metrics: dict[str, Any] | None,
    best_checkpoint: dict[str, Any] | None,
    plateau_streak: int,
    min_epoch: int,
    improvement_threshold_pct: float,
    patience_checkpoints: int,
) -> tuple[dict[str, Any], dict[str, Any] | None, int, bool, str]:
    current_eval = _safe_float(metrics.get('eval_return'), 0.0)
    previous_best_eval = _safe_float((best_checkpoint or {}).get('metrics', {}).get('eval_return'), 0.0)
    stable = _stable_against_previous(metrics, previous_checkpoint_metrics)
    improvement_pct = _improvement_pct(current_eval, previous_best_eval)

    if epoch < min_epoch:
        status = 'pre_plateau'
        plateau_streak = 0
    elif best_checkpoint is None:
        status = 'improving'
        plateau_streak = 0
    elif not stable:
        status = 'unstable'
        plateau_streak = 0
    elif current_eval > previous_best_eval and improvement_pct >= improvement_threshold_pct:
        status = 'improving'
        plateau_streak = 0
    else:
        plateau_streak += 1
        status = 'plateau'

    record = {
        'epoch': int(epoch),
        'checkpoint_dir': str(checkpoint_dir.resolve()),
        'summary_path': str((checkpoint_dir / 'tasam_marl_summary.json').resolve()),
        'metrics': metrics,
        'status': status,
        'stable_vs_previous': bool(stable),
        'improvement_pct_vs_best': float(improvement_pct),
        'plateau_streak': int(plateau_streak),
    }

    stop_now = False
    stop_reason = ''
    if (
        patience_checkpoints > 0
        and epoch >= min_epoch
        and status == 'plateau'
        and plateau_streak >= patience_checkpoints
    ):
        stop_now = True
        stop_reason = (
            f'plateau_after_epoch_{epoch}: '
            f'{plateau_streak} checkpoints sem ganho >= {improvement_threshold_pct:.2f}% em eval_return'
        )
        record['status'] = 'converged'

    candidate = {
        'epoch': int(epoch),
        'checkpoint_dir': str(checkpoint_dir.resolve()),
        'summary_path': str((checkpoint_dir / 'tasam_marl_summary.json').resolve()),
        'metrics': metrics,
    }
    if best_checkpoint is None or _checkpoint_rank(metrics, epoch) > _checkpoint_rank(best_checkpoint.get('metrics') or {}, int(best_checkpoint.get('epoch', 0) or 0)):
        best_checkpoint = candidate

    return record, best_checkpoint, plateau_streak, stop_now, stop_reason


def _save_resume_checkpoint(
    path: Path,
    *,
    trainer: TASAMArticleSACTrainer,
    history: list[dict[str, Any]],
    checkpoint_records: list[dict[str, Any]],
    best_checkpoint: dict[str, Any] | None,
    completed_epochs: int,
    total_epochs: int,
    stopped_early: bool,
    stop_reason: str,
) -> None:
    payload = {
        'trainer_state': trainer.training_state_dict(),
        'history': history,
        'checkpoint_records': checkpoint_records,
        'best_checkpoint': best_checkpoint,
        'completed_epochs': int(completed_epochs),
        'target_epochs': int(total_epochs),
        'stopped_early': bool(stopped_early),
        'stop_reason': str(stop_reason),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Train TA-SAM MARL backends for GreenRAN')
    parser.add_argument('--trace-jsonl', required=True, help='Input MARL trace JSONL path')
    parser.add_argument('--output-dir', required=True, help='Output directory')
    parser.add_argument('--epochs', type=int, default=25, help='Training epochs')
    parser.add_argument('--lr', type=float, default=1e-4, help='Learning rate (artigo)')
    parser.add_argument('--alpha-lr', type=float, default=1e-4, help='Entropy-temperature learning rate for article_sac')
    parser.add_argument('--trainer-backend', choices=('article_sac', 'scaffold'), default='article_sac', help='Training backend')
    parser.add_argument('--sam-mode', choices=('tasam_selective', 'no_sam', 'l2', 'actor_sam', 'critic_sam', 'both_sam'), default='tasam_selective', help='Article ablation/training mode')
    parser.add_argument('--sam-rho', type=float, default=0.5, help='Fallback initial SAM rho')
    parser.add_argument('--sam-rho-final', type=float, default=0.01, help='Fallback final rho for the TA-SAM schedule')
    parser.add_argument('--actor-sam-rho', type=float, default=None, help='Actor initial rho override')
    parser.add_argument('--actor-sam-rho-final', type=float, default=None, help='Actor final rho override')
    parser.add_argument('--critic-sam-rho', type=float, default=None, help='Critic initial rho override')
    parser.add_argument('--critic-sam-rho-final', type=float, default=None, help='Critic final rho override')
    parser.add_argument('--l2-weight', type=float, default=0.0, help='L2 regularization weight used by the l2 baseline')
    parser.add_argument('--article-hidden', action='store_true', help='Use the larger 300,400,400 hidden layout described in the paper')
    parser.add_argument('--actor-hidden-dims', type=parse_hidden_dims, default=(64, 64), help='Comma-separated actor hidden dimensions')
    parser.add_argument('--critic-hidden-dims', type=parse_hidden_dims, default=(96, 96), help='Comma-separated critic hidden dimensions')
    parser.add_argument('--activation', choices=('relu', 'tanh'), default='tanh', help='Hidden-layer activation')
    parser.add_argument('--td-var-threshold', type=float, default=0.01, help='Selective SAM threshold over TD proxy variance')
    parser.add_argument('--min-selected-fraction', type=float, default=0.10, help='Minimum fraction of agent updates selected each epoch')
    parser.add_argument('--warmup-epochs', type=int, default=2, help='Force actor updates during early epochs before selective SAM takes over')
    parser.add_argument('--bc-weight', type=float, default=0.0, help='Behavior-cloning anchor weight (0 keeps the backend article-faithful)')
    parser.add_argument('--value-weight', type=float, default=0.0, help='Legacy scaffold-only value weight')
    parser.add_argument('--gamma', type=float, default=0.99, help='Discount factor for article_sac')
    parser.add_argument('--tau', type=float, default=0.01, help='Target critic soft-update factor for article_sac')
    parser.add_argument('--alpha-init', type=float, default=0.03, help='Initial entropy temperature for article_sac')
    parser.add_argument('--target-entropy-scale', type=float, default=1.0, help='Scale applied to joint-action target entropy')
    parser.add_argument('--batch-size', type=int, default=128, help='Mini-batch size for article_sac')
    parser.add_argument('--updates-per-epoch', type=int, default=None, help='Gradient updates per epoch for article_sac')
    parser.add_argument('--actor-update-interval', type=int, default=1, help='Update actors every N critic steps in article_sac')
    parser.add_argument('--category-loss-weight', type=float, default=0.5, help='Auxiliary observed-category loss weight')
    parser.add_argument('--category-head-hidden-dim', type=int, default=64, help='Hidden dimension of the ordinal category head')
    parser.add_argument('--temporal-dim', type=int, default=10, help='Optional internal temporal-context width; external state remains 13-D')
    parser.add_argument('--power-head-hidden-dim', type=int, default=64, help='Hidden dimension of the discrete power head')
    parser.add_argument('--power-head-lr', type=float, default=0.001, help='Learning rate for the discrete power head')
    parser.add_argument('--power-head-steps', type=int, default=10, help='Supervised steps for the discrete power head')
    parser.add_argument('--allocation-head-hidden-dim', type=int, default=64, help='Hidden dimension of the aggregate RAN/IA allocation head')
    parser.add_argument('--allocation-head-lr', type=float, default=0.001, help='Learning rate for the aggregate RAN/IA allocation head')
    parser.add_argument('--allocation-head-steps', type=int, default=10, help='Supervised steps for the aggregate RAN/IA allocation head')
    parser.add_argument('--allocation-head-output-dim', type=int, choices=(2, 3), default=2, help='Allocation outputs: 2 legacy split values or 3 including total_budget_fraction')
    parser.add_argument('--global-action-dim', type=int, choices=(3, 5), default=3, help='Global action width; v10 uses 5 independent DU powers plus RAN share and total budget')
    parser.add_argument('--seed', type=int, default=42, help='Random seed')
    parser.add_argument('--max-epochs', type=int, default=None, help='Maximum training epochs; defaults to --epochs for compatibility')
    parser.add_argument('--checkpoint-every', type=int, default=0, help='Save intermediate checkpoints every N epochs')
    parser.add_argument('--milestone-epochs', type=parse_epoch_points, default=(), help='Comma-separated epoch checkpoints to always preserve')
    parser.add_argument('--resume', action='store_true', help='Resume from output-dir/resume_checkpoint.pt when available')
    parser.add_argument('--resume-checkpoint', default=None, help='Optional resume checkpoint path; defaults inside output-dir')
    parser.add_argument('--resume-ignore-early-stop', action='store_true', help='Resume training even if the saved checkpoint had stopped_early=true')
    parser.add_argument('--init-checkpoint-dir', default=None, help='Optional actor checkpoint directory used to initialize a new candidate update')
    parser.add_argument('--early-stop-patience-checkpoints', type=int, default=0, help='Stop after this many plateau checkpoints; 0 disables early stop')
    parser.add_argument('--early-stop-min-epoch', type=int, default=0, help='Do not evaluate plateau stopping before this epoch')
    parser.add_argument('--early-stop-min-improvement-pct', type=float, default=0.0, help='Minimum eval_return improvement percentage to reset plateau')
    return parser


def resolve_hidden_dims(args: argparse.Namespace) -> tuple[tuple[int, ...], tuple[int, ...]]:
    actor_hidden_dims = (300, 400, 400) if args.article_hidden else args.actor_hidden_dims
    critic_hidden_dims = (300, 400, 400) if args.article_hidden else args.critic_hidden_dims
    return actor_hidden_dims, critic_hidden_dims


def resolve_rhos(args: argparse.Namespace) -> dict[str, float]:
    actor_rho = args.actor_sam_rho if args.actor_sam_rho is not None else args.sam_rho
    actor_rho_final = args.actor_sam_rho_final if args.actor_sam_rho_final is not None else args.sam_rho_final
    critic_rho = args.critic_sam_rho if args.critic_sam_rho is not None else args.sam_rho
    critic_rho_final = args.critic_sam_rho_final if args.critic_sam_rho_final is not None else args.sam_rho_final
    return {
        'actor_rho': actor_rho,
        'actor_rho_final': actor_rho_final,
        'critic_rho': critic_rho,
        'critic_rho_final': critic_rho_final,
    }


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_root = output_dir / 'checkpoints'
    history_jsonl = output_dir / 'epoch_history.jsonl'
    summary_path = output_dir / 'tasam_marl_summary.json'
    resume_checkpoint = Path(args.resume_checkpoint) if args.resume_checkpoint else (output_dir / 'resume_checkpoint.pt')
    total_epochs = int(args.max_epochs or args.epochs)
    if total_epochs <= 0:
        raise SystemExit('target epochs must be positive')
    actor_hidden_dims, critic_hidden_dims = resolve_hidden_dims(args)
    rhos = resolve_rhos(args)
    if args.trainer_backend != 'article_sac' and (
        args.resume
        or args.checkpoint_every > 0
        or args.milestone_epochs
        or args.early_stop_patience_checkpoints > 0
    ):
        raise SystemExit('checkpoint/resume/early-stop workflow is currently supported only for article_sac')

    if args.trainer_backend == 'scaffold':
        records = load_scaffold_trace(args.trace_jsonl)
        if not records:
            raise SystemExit('No MARL records found in trace file')
        du_count = len(records[0].du_states)
        du_state_dim = len(records[0].du_states[0])
        global_state_dim = len(records[0].global_state)
        trainer = ScaffoldTASAMMultiAgentTrainer(
            du_count=du_count,
            du_state_dim=du_state_dim,
            global_state_dim=global_state_dim,
            lr=args.lr,
            rho=args.sam_rho,
            rho_final=args.sam_rho_final,
            sam_mode=args.sam_mode,
            l2_weight=args.l2_weight,
            actor_hidden_dims=actor_hidden_dims,
            critic_hidden_dims=critic_hidden_dims,
            activation=args.activation,
            global_action_dim=args.global_action_dim,
        )
    else:
        records = load_marl_transition_trace(args.trace_jsonl)
        if not records:
            raise SystemExit('No MARL transitions found in trace file')
        du_count = len(records[0].du_states)
        du_state_dim = len(records[0].du_states[0])
        global_state_dim = len(records[0].global_state)
        trainer = TASAMArticleSACTrainer(
            du_count=du_count,
            du_state_dim=du_state_dim,
            global_state_dim=global_state_dim,
            lr=args.lr,
            alpha_lr=args.alpha_lr,
            gamma=args.gamma,
            tau=args.tau,
            alpha_init=args.alpha_init,
            target_entropy_scale=args.target_entropy_scale,
            actor_rho=rhos['actor_rho'],
            actor_rho_final=rhos['actor_rho_final'],
            critic_rho=rhos['critic_rho'],
            critic_rho_final=rhos['critic_rho_final'],
            sam_mode=args.sam_mode,
            l2_weight=args.l2_weight,
            actor_hidden_dims=actor_hidden_dims,
            critic_hidden_dims=critic_hidden_dims,
            activation=args.activation,
            batch_size=args.batch_size,
            updates_per_epoch=args.updates_per_epoch,
            actor_update_interval=args.actor_update_interval,
            category_loss_weight=args.category_loss_weight,
            category_head_hidden_dim=args.category_head_hidden_dim,
            temporal_dim=max(0, int(args.temporal_dim)),
            power_head_hidden_dim=args.power_head_hidden_dim,
            power_head_lr=args.power_head_lr,
            power_head_steps=args.power_head_steps,
            allocation_head_hidden_dim=args.allocation_head_hidden_dim,
            allocation_head_lr=args.allocation_head_lr,
            allocation_head_steps=args.allocation_head_steps,
            allocation_head_output_dim=args.allocation_head_output_dim,
            global_action_dim=args.global_action_dim,
            seed=args.seed,
        )

    # Online updates are intentionally written to an isolated candidate
    # directory.  Initializing only the actors from the immutable active
    # checkpoint keeps the active policy read-only while still making each
    # candidate a genuine continuation of the validated TA-SAM policy.
    if args.init_checkpoint_dir and not (args.resume and resume_checkpoint.exists()):
        init_dir = Path(args.init_checkpoint_dir)
        actor_path = init_dir / 'tasam_marl_actors.pt'
        if not actor_path.is_file():
            raise SystemExit(f'initial actor checkpoint not found: {actor_path}')
        actor_state = torch.load(actor_path, map_location='cpu', weights_only=False)
        try:
            trainer.actors.load_state_dict(actor_state)
        except (RuntimeError, TypeError) as exc:
            raise SystemExit(f'initial actor checkpoint incompatible: {exc}') from exc
        global_actor_path = init_dir / 'tasam_marl_global_actor.pt'
        if global_actor_path.is_file():
            global_actor_state = torch.load(global_actor_path, map_location='cpu', weights_only=False)
            try:
                trainer.global_actor.load_state_dict(global_actor_state)
            except (RuntimeError, TypeError) as exc:
                raise SystemExit(f'initial global actor checkpoint incompatible: {exc}') from exc
        category_path = init_dir / 'tasam_marl_category_head.pt'
        if category_path.is_file():
            category_state = torch.load(category_path, map_location='cpu', weights_only=False)
            try:
                trainer.category_head.load_state_dict(category_state, strict=False)
            except (RuntimeError, TypeError) as exc:
                raise SystemExit(f'initial category checkpoint incompatible: {exc}') from exc
        allocation_path = init_dir / 'tasam_marl_allocation_head.pt'
        if allocation_path.is_file():
            allocation_state = torch.load(allocation_path, map_location='cpu', weights_only=False)
            try:
                trainer.allocation_head.load_state_dict(allocation_state, strict=False)
            except (RuntimeError, TypeError) as exc:
                raise SystemExit(f'initial allocation checkpoint incompatible: {exc}') from exc

    history: list[dict[str, Any]] = []
    checkpoint_records: list[dict[str, Any]] = []
    best_checkpoint: dict[str, Any] | None = None
    plateau_streak = 0
    completed_epochs = 0
    stopped_early = False
    stop_reason = ''
    resumed_from_stop_reason = ''
    if args.resume and resume_checkpoint.exists():
        resume_payload = torch.load(resume_checkpoint, map_location='cpu', weights_only=False)
        trainer.load_training_state_dict((resume_payload or {}).get('trainer_state') or {})
        history = list((resume_payload or {}).get('history') or [])
        checkpoint_records = list((resume_payload or {}).get('checkpoint_records') or [])
        best_checkpoint = (resume_payload or {}).get('best_checkpoint') or None
        completed_epochs = int((resume_payload or {}).get('completed_epochs', 0) or 0)
        stopped_early = bool((resume_payload or {}).get('stopped_early', False))
        stop_reason = str((resume_payload or {}).get('stop_reason', '') or '')
        plateau_streak = int((checkpoint_records[-1] or {}).get('plateau_streak', 0) or 0) if checkpoint_records else 0
        if stopped_early and args.resume_ignore_early_stop:
            resumed_from_stop_reason = stop_reason
            stopped_early = False
            stop_reason = ''
        _write_history_jsonl(history_jsonl, history)
    else:
        history_jsonl.unlink(missing_ok=True)

    if completed_epochs >= total_epochs or stopped_early:
        summary = _build_summary(
            args,
            output_dir=output_dir,
            trace_jsonl=args.trace_jsonl,
            total_epochs=total_epochs,
            completed_epochs=completed_epochs,
            du_count=du_count,
            du_state_dim=du_state_dim,
            global_state_dim=global_state_dim,
            actor_hidden_dims=actor_hidden_dims,
            critic_hidden_dims=critic_hidden_dims,
            rhos=rhos,
            history=history,
            checkpoint_records=checkpoint_records,
            best_checkpoint=best_checkpoint,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
            resume_checkpoint=resume_checkpoint,
            resume_ignore_early_stop=bool(args.resume_ignore_early_stop),
            resumed_from_stop_reason=resumed_from_stop_reason,
        )
        _write_summary(summary_path, summary)
        if history:
            trainer.export_checkpoint(output_dir, metadata=summary)
        print(json.dumps({'completed_epochs': completed_epochs, 'stopped_early': stopped_early, 'stop_reason': stop_reason}, indent=2))
        return 0

    for epoch in range(completed_epochs + 1, total_epochs + 1):
        print(f"Epoch {epoch}/{total_epochs}...", end=' ', flush=True)
        metrics = trainer.train_epoch(
            records,
            td_var_threshold=args.td_var_threshold,
            min_selected_fraction=args.min_selected_fraction,
            warmup=epoch <= args.warmup_epochs,
            bc_weight=args.bc_weight,
            value_weight=args.value_weight,
            epoch_progress=(epoch - 1) / max(total_epochs - 1, 1),
        )
        metrics['epoch'] = epoch
        metrics['warmup'] = epoch <= args.warmup_epochs
        history.append(metrics)
        _append_history_jsonl(history_jsonl, metrics)
        print(
            f"actor={metrics['actor_loss']:.4f} "
            f"critic={metrics['critic_loss']:.4f} "
            f"eval={metrics.get('eval_return', 0.0):.4f} "
            f"cum={metrics.get('cumulative_return', 0.0):.2f} "
            f"alpha={metrics.get('alpha', 0.0):.4f} "
            f"selected={metrics['selected_fraction']:.2f} "
            f"td_var={metrics['effective_td_var_threshold']:.4f}"
        )
        completed_epochs = epoch

        if _should_export_checkpoint(
            epoch,
            total_epochs=total_epochs,
            checkpoint_every=int(args.checkpoint_every or 0),
            milestone_epochs=tuple(int(value) for value in args.milestone_epochs),
        ):
            checkpoint_dir = checkpoint_root / f'epoch_{epoch:04d}'
            previous_checkpoint_metrics = (checkpoint_records[-1] or {}).get('metrics') if checkpoint_records else None
            record, best_checkpoint, plateau_streak, stop_now, computed_stop_reason = _checkpoint_record(
                epoch=epoch,
                checkpoint_dir=checkpoint_dir,
                metrics=metrics,
                previous_checkpoint_metrics=previous_checkpoint_metrics,
                best_checkpoint=best_checkpoint,
                plateau_streak=plateau_streak,
                min_epoch=int(args.early_stop_min_epoch or 0),
                improvement_threshold_pct=float(args.early_stop_min_improvement_pct or 0.0),
                patience_checkpoints=int(args.early_stop_patience_checkpoints or 0),
            )
            checkpoint_records.append(record)
            summary = _build_summary(
                args,
                output_dir=output_dir,
                trace_jsonl=args.trace_jsonl,
                total_epochs=total_epochs,
                completed_epochs=completed_epochs,
                du_count=du_count,
                du_state_dim=du_state_dim,
                global_state_dim=global_state_dim,
                actor_hidden_dims=actor_hidden_dims,
                critic_hidden_dims=critic_hidden_dims,
                rhos=rhos,
                history=history,
                checkpoint_records=checkpoint_records,
                best_checkpoint=best_checkpoint,
                stopped_early=bool(stop_now),
                stop_reason=computed_stop_reason,
                resume_checkpoint=resume_checkpoint,
                resume_ignore_early_stop=bool(args.resume_ignore_early_stop),
                resumed_from_stop_reason=resumed_from_stop_reason,
            )
            trainer.export_checkpoint(checkpoint_dir, metadata=summary)
            _save_resume_checkpoint(
                resume_checkpoint,
                trainer=trainer,
                history=history,
                checkpoint_records=checkpoint_records,
                best_checkpoint=best_checkpoint,
                completed_epochs=completed_epochs,
                total_epochs=total_epochs,
                stopped_early=bool(stop_now),
                stop_reason=computed_stop_reason,
            )
            if stop_now:
                stopped_early = True
                stop_reason = computed_stop_reason

        summary = _build_summary(
            args,
            output_dir=output_dir,
            trace_jsonl=args.trace_jsonl,
            total_epochs=total_epochs,
            completed_epochs=completed_epochs,
            du_count=du_count,
            du_state_dim=du_state_dim,
            global_state_dim=global_state_dim,
            actor_hidden_dims=actor_hidden_dims,
            critic_hidden_dims=critic_hidden_dims,
            rhos=rhos,
            history=history,
            checkpoint_records=checkpoint_records,
            best_checkpoint=best_checkpoint,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
            resume_checkpoint=resume_checkpoint,
            resume_ignore_early_stop=bool(args.resume_ignore_early_stop),
            resumed_from_stop_reason=resumed_from_stop_reason,
        )
        _write_summary(summary_path, summary)
        if stopped_early:
            break

    summary = _build_summary(
        args,
        output_dir=output_dir,
        trace_jsonl=args.trace_jsonl,
        total_epochs=total_epochs,
        completed_epochs=completed_epochs,
        du_count=du_count,
        du_state_dim=du_state_dim,
        global_state_dim=global_state_dim,
        actor_hidden_dims=actor_hidden_dims,
        critic_hidden_dims=critic_hidden_dims,
        rhos=rhos,
        history=history,
        checkpoint_records=checkpoint_records,
        best_checkpoint=best_checkpoint,
        stopped_early=stopped_early,
        stop_reason=stop_reason,
        resume_checkpoint=resume_checkpoint,
        resume_ignore_early_stop=bool(args.resume_ignore_early_stop),
        resumed_from_stop_reason=resumed_from_stop_reason,
    )
    trainer.export_checkpoint(output_dir, metadata=summary)
    _write_summary(summary_path, summary)
    _save_resume_checkpoint(
        resume_checkpoint,
        trainer=trainer,
        history=history,
        checkpoint_records=checkpoint_records,
        best_checkpoint=best_checkpoint,
        completed_epochs=completed_epochs,
        total_epochs=total_epochs,
        stopped_early=stopped_early,
        stop_reason=stop_reason,
    )
    print(json.dumps(summary['final_metrics'], indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
