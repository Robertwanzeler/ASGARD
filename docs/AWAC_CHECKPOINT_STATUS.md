# AWAC Checkpoint Status

Data de referência: `2026-05-25`

## Produção

O checkpoint que permanece **padrão no runtime** é:

- [offline_awac_20260524_refresh/sac_actor_offline.pt](/home/robert/orange_nuclear/runs/sac_bootstrap/offline_awac_20260524_refresh/sac_actor_offline.pt:1)

Motivo:

- `val_action_mae = 0.0022859678`
- melhor erro de ação de validação entre os checkpoints AWAC atuais
- já está integrado em [rapp_rl_policy.py](/home/robert/orange_nuclear/src/rapp_rl_policy.py:1)

Resumo do rollout:

- `rollout_reward = 42218.8017`
- `avg_utilization = 0.7748`
- `avg_ran_completion = 0.9906`
- `avg_ai_completion = 0.8664`

## Candidato Secundário

O melhor candidato secundário mais recente é:

- [offline_awac_20260525_1123/sac_actor_offline.pt](/home/robert/orange_nuclear/runs/sac_bootstrap/offline_awac_20260525_1123/sac_actor_offline.pt:1)

Resumo:

- `trace_points = 23492`
- `val_action_mae = 0.0028361671`
- `chosen_policy_source = awac`

Resumo do rollout:

- `rollout_reward = 74596.9628`
- `avg_utilization = 0.7778`
- `avg_ran_completion = 0.9895`
- `avg_ai_completion = 0.8630`

## Decisão Atual

Apesar do `rollout_reward` maior no treino `20260525_1123`, ele **não substitui** o checkpoint `refresh` porque:

- o critério principal atual é `val_action_mae`
- nesse critério, o `refresh` ainda é melhor
- a diferença de rollout não compensa piora no ajuste da política

## Comparação Curta

`offline_awac_20260524_refresh`

- `val_action_mae = 0.002286`
- `rollout_reward = 42218.80`
- `status = production`

`offline_awac_20260525_1123`

- `val_action_mae = 0.002836`
- `rollout_reward = 74596.96`
- `status = secondary_candidate`

## Próximo Critério para Troca

Só promover um novo checkpoint para produção se ele:

- bater o `refresh` em `val_action_mae`
- mantiver `avg_ran_completion` no mesmo patamar
- não degradar claramente `avg_ai_completion`

## Pipeline Automático

O ciclo automático para avaliar se já vale uma nova rodada AWAC está em:

- [run_awac_refresh_cycle.py](/home/robert/orange_nuclear/scripts/run_awac_refresh_cycle.py:1)

Uso rápido:

- só medir a coleta atual, sem treinar:
  - `python3 scripts/run_awac_refresh_cycle.py --dry-run`
- exportar, treinar e comparar automaticamente quando já houver crescimento suficiente:
  - `python3 scripts/run_awac_refresh_cycle.py`

Saída principal:

- [awac_refresh_cycle_latest.json](/home/robert/orange_nuclear/runs/sac_bootstrap/awac_refresh_cycle_latest.json:1)

Esse script **não promove** checkpoint automaticamente no runtime. Ele só decide:

- `promote_candidate`
- `keep_production`
