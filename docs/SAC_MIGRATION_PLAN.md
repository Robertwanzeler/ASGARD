# SAC Migration Plan

> **Atualizado:** Maio 2026 — v3.1 do RELATORIO documenta o estado atual.
>
> **Artigo de referência:** Lotfi, F., Rajoli, H. & Afghah, F. "Task-Specific Sharpness-Aware O-RAN Resource Management using Multi-Agent Reinforcement Learning". IEEE TMLCN, 2025. arXiv:2511.15002.

## Goal

Migrate GreenRAN from the legacy `SBiLSTM + A3C` energy-control formulation to
the `SAC`/`AWAC` formulation for shared AI/RAN resource allocation, inspired by
Lotfi et al. (2025).

## What Stays

- `src/rapp_orchestrator.py` as the Non-RT RIC control point
- xApps, monitoring, Data Lake, logs and dashboards
- real collection pipeline (`csv_to_metrics`, `rapp_data_lake`)
- replay-buffer utility in `drlexp/src/drl/replay_buffer.py`

## What Becomes Legacy (Frozen)

- `src/rapp_drl_predictor.py` — A3C predictor
- `drlexp/training/train_a3c.py` — A3C trainer
- `drlexp/src/drl/gym_environment.py` — A3C gym env
- `drlexp/config/drl_config.yaml` — A3C config

Those files remain valid only for the old energy-command problem (Zhang et al. 2022).

## New Components (Implemented)

| Component | File | Status |
|-----------|------|--------|
| Generic RL policy interface | `src/rapp_rl_policy.py` | ✅ Implementado |
| SAC adapter | `src/rapp_rl_policy.py` (SACResourceAllocationPolicy) | ✅ Implementado |
| A3C legacy adapter | `src/rapp_rl_policy.py` (LegacyA3CPolicyAdapter) | ✅ Implementado |
| Shared resource model | `src/rapp_sac_resource_model.py` | ✅ Implementado |
| CAORA-SAC environment | `drlexp/src/drl/caora_sac_environment.py` | ✅ Implementado |
| SAC/AWAC trainer | `drlexp/training/train_sac.py` | ✅ Implementado |
| SAC config | `drlexp/config/sac_config.yaml` | ✅ Implementado |
| Export workload traces | `scripts/export_sac_workload_trace.py` | ✅ Implementado |
| SAC checkpoints | `runs/sac_bootstrap/offline_sac/` + `offline_awac/` | ✅ Treinado |
| Resource allocation table | Data Lake: `resource_allocation_history` | ✅ Criado |

## Gaps vs. Lotfi et al. (2025)

| Aspecto | Artigo | CAORA | Prioridade |
|---------|--------|-------|------------|
| Framework | **MARL** (multi-agente distribuído) | Single-agent | ⬜ Média |
| Algoritmo | SAC + **SAM** (Sharpness-Aware Minimization) | SAC/AWAC sem SAM | ⬜ Alta |
| Dynamic ρ scheduling | Adaptativo por agente | Apenas entropia fixa | ⬜ Baixa |
| Treino | Online com SAM | Offline bootstrap (BC + SAC/AWAC) | ⬜ Média |
| AWAC | Não consta no artigo | Implementado como extensão | — (próprio) |

## Integration Steps (Pending)

1. ⬜ Implementar **SAM** no treino SAC para alinhamento com o artigo.
2. ⬜ Change the rApp runtime to consume `resource_allocation` from Data Lake instead of energy commands.
3. ⬜ Write unit tests for `caora_sac_environment.py`, `rapp_sac_resource_model.py`, `rapp_rl_policy.py`.
4. ⬜ Create CAORA-SAC figures (`fig11`–`fig15`) following DRL_ARMD_FIGURE_STANDARD.md.
5. ⬜ Run hyperparameter sweep for SAC/AWAC reward coefficients (α, β, γ).
6. ⬜ Update article metrics to: RAN completion rate, AI completion rate, infrastructure utilization, priority preservation for RAN traffic.
7. ⬜ A/B test: compare A3C (legacy) vs SAC (new) in runtime.
8. ⬜ Avaliar viabilidade de migrar para **MARL** (multi-agente por slice).

## Runtime Switching

```bash
GREENRAN_RL_POLICY=legacy_a3c   # A3C original (frozen)
GREENRAN_RL_POLICY=sac           # SAC offline
GREENRAN_RL_POLICY=awac          # AWAC offline
```
