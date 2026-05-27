# EE-DRL-GreenRAN Runtime - Melhorias no Predictor

> ⚠️ **Nota (Maio 2026):** Este documento cobre exclusivamente o **legado A3C/SBiLSTM**. A nova geração **CAORA-SAC** (Seção "Migração SAC" abaixo) reformula o runtime com alocação compartilhada de recursos RAN/AI. O conteúdo A3C abaixo é mantido como referência histórica.

## Escopo (Legado A3C)

Esta rodada melhorou o comportamento do **EE-DRL-GreenRAN** em runtime sem alterar a politica do `rApp`.

Arquivos principais:

- `src/rapp_drl_predictor.py`
- `scripts/smoke_drl_sequence.py`
- `scripts/summarize_drl_trace.py`
- `apps/app2_monitoramento/backend/simulate_sensors.py`

O `src/rapp_orchestrator.py` nao foi modificado para estas melhorias.

Referencia metodologica da trilha:

- `SBiLSTM + A3C`
- artigo base: `Energy-Efficient Deep Reinforcement Learning Assisted Resource Allocation for 5G-RAN Slicing`

## O que mudou no predictor

### 1. Estado enriquecido

O predictor passou a derivar localmente campos que antes chegavam zerados ou artificiais:

- `hour_sin`, `hour_cos`
- `camera_ratio`, `critical_ue_ratio`
- `cvar_trend`, `cvar_acceleration`
- `variance_ms2`

Isso reduz mismatch entre treino e runtime sem depender de mudancas no `rApp`.

### 2. Warmup menos nervoso

Nos primeiros ciclos:

- a predicao da `SBiLSTM` passa por blend com o `cvar_ms` atual
- a decisao nao fica tao sensivel a historico curto
- foi adicionado relaxamento explicito para o caso `healthy warmup`

Resultado:

- a DRL deixou de sair `CONDITIONAL` no primeiro ciclo saudavel apenas por inseguranca do ator

### 3. Confianca e estabilidade

A confianca da A3C deixou de usar so o `argmax` do softmax e passou a combinar:

- probabilidade da melhor acao
- margem entre primeira e segunda acao
- entropia
- valor do critico
- fator de warmup

Tambem foram adicionados:

- suavizacao temporal de `predicted_cvar_ms`
- histerese leve
- memoria local de predições

### 4. Score continuo de risco

O predictor agora calcula:

- `risk_score`
- `risk_band`

Esse score usa:

- `predicted_cvar_ms`
- `cvar_ms`
- `latency_p95_ms`
- `cvar_trend`
- `packet_loss_pct`
- `critical_ue_ratio`
- `variance_ms2`

O score continuo passou a influenciar:

- calibracao da decisao final
- transicao entre `ALLOWED`, `CONDITIONAL` e `BLOCKED`
- selecao da politica de energia

### 5. Trace de runtime

Quando ativado por ambiente:

```bash
GREENRAN_DRL_TRACE=1
GREENRAN_DRL_TRACE_FILE=/tmp/drl_predictor_trace.jsonl
```

o predictor grava um JSONL com:

- entrada resumida
- `raw_predicted_cvar_ms`
- `predicted_cvar_ms`
- `a3c_decision_raw`
- `a3c_decision`
- `final_decision`
- `policy_action`
- `actor_confidence`
- `actor_entropy`
- `actor_margin`
- `critic_value`
- `warmup_factor`
- `stability_factor`
- `risk_score`
- `risk_band`
- `hysteresis_applied`
- `calibrated`

## Scripts de apoio

### Smoke test

`scripts/smoke_drl_sequence.py`

Usa uma sequencia sintetica curta para validar:

- warmup
- estabilizacao
- histerese
- recuperacao

### Resumo do trace

`scripts/summarize_drl_trace.py`

Resume o JSONL da DRL com:

- decisoes finais
- decisoes do ator
- politicas aplicadas
- calibracao
- histerese
- bandas de risco
- medias por decisao

## Ajuste no App2

Para reduzir oscilacao espuria no `APP2_GUARD`, o simulador de sensores foi suavizado em:

- `apps/app2_monitoramento/backend/simulate_sensors.py`

Foi adicionada persistencia de conectividade entre ciclos:

- sensor `ok` tende a continuar `ok`
- sensor `error` tende a demorar um pouco mais para recuperar

Isso reduz flapping aleatorio, mas ainda preserva variacao real.

## Leitura correta do runtime

### Quando a DRL aparece

A DRL so influencia a politica quando:

- `CAMERA_GUARD` nao esta ativo
- `APP2_GUARD` nao esta ativo
- as regras de prioridade deixam a hierarquia passar para ML/DRL

### Quando o dashboard de DRL engana

O painel DRL do dashboard:

- faz parsing das linhas `[rApp DRL]` do log
- mostra o que a DRL sugeriu
- nao representa sozinho a decisao final do sistema

Ou seja:

- `DRL = ALLOWED`
- nao implica necessariamente
- `decisao final do rApp = ALLOWED`

porque as guardas de camera/App2 podem sobrescrever depois.

## Estado validado

Nos testes desta rodada, o comportamento final ficou assim:

- em rede saudavel:
  - `Decision: ALLOWED`
  - `Power: REDUCE`
  - politica final frequentemente `POWER_DOWN_ECO`
- no primeiro ciclo saudavel:
  - o predictor deixou de sair `CONDITIONAL` por warmup inseguro
- em fase ruim de camera:
  - a camera continua dominando corretamente com `FULL_POWER`
- em fase ruim do App2:
  - o `rApp` ainda pode aplicar `APP2_GUARD`, como esperado

## Comandos uteis

### Smoke test local

```bash
cd /home/robert/orange_nuclear
GREENRAN_DRL_TRACE=1 \
GREENRAN_DRL_TRACE_FILE=/tmp/drl_predictor_trace.jsonl \
./drlexp/.venv/bin/python scripts/smoke_drl_sequence.py
```

### Resumir trace

```bash
cd /home/robert/orange_nuclear
python3 scripts/summarize_drl_trace.py \
  --trace-file /tmp/drl_predictor_trace.jsonl \
  --output-json /tmp/drl_trace_summary.json
```

### Validacao real curta

Rodar o `rApp` com trace ligado por alguns ciclos:

```bash
cd /home/robert/orange_nuclear
GREENRAN_DRL_TRACE=1 \
GREENRAN_DRL_TRACE_FILE=/tmp/drl_predictor_trace_runtime.jsonl \
python3 ./src/rapp_orchestrator.py --synthetic 0 --interval 5
```

## Conclusao (Legado A3C)

A DRL ficou melhor em runtime sem alterar a politica do `rApp`.

O ganho principal foi:

- menos conservadorismo espurio em cenario saudavel
- mais estabilidade temporal
- mais rastreabilidade por trace
- melhor leitura de risco no proprio predictor

O limite que permanece intencional:

- camera e App2 continuam podendo sobrescrever a DRL quando entram em guarda ou violacao.

---

## Migração SAC (Nova Geração)

> **Nota:** A partir de Maio 2026, a arquitetura DRL migrou de A3C (energy on/off) para SAC/AWAC (alocação compartilhada de recursos). Esta seção documenta as diferenças de runtime.

### Diferenças Runtime: A3C vs SAC

| Aspecto | A3C (Legado) | SAC (Nova Geração) |
|---------|-------------|-------------------|
| **Arquivo policy** | `src/rapp_drl_predictor.py` | `src/rapp_rl_policy.py` + `src/rapp_sac_resource_model.py` |
| **Ambiente** | Embedido no predictor | `drlexp/src/drl/caora_sac_environment.py` |
| **Estado** | `cvar_ms`, `packet_loss_pct`, ... (10+ features) | `[d_ran, d_ai, r_ran, r_ai]` (4 floats) |
| **Ação** | 3 discretas (FULL_POWER, POWER_DOWN, FULL_POWER_GUARD) | 2 contínuas (δ_r_ran, δ_r_ai) |
| **Orçamento** | Ilimitado | Compartilhado: r_max = r_ran + r_ai |
| **Seleção** | Fixa no orchestrator | `GREENRAN_RL_POLICY=legacy_a3c\|sac\|awac` |
| **Treino** | Online A3C | Offline SAC/AWAC (bootstrap com traces) |

### Fluxo Runtime SAC (Alvo)

```
1. compute_shared_resource_snapshot()  → [d_ran, d_ai, r_ran, r_ai]
2. SACResourceAllocationPolicy.decide() → [δ_r_ran, δ_r_ai]
3. Aplica alocação: r_ran += δ_r_ran, r_ai += δ_r_ai (respeitando r_max)
4. Data Lake registra em resource_allocation_history
```

### Estado da Migração

| Componente | Status |
|------------|--------|
| Ambiente CAORA-SAC | ✅ Pronto |
| Trainer SAC/AWAC offline | ✅ Pronto |
| Resource model runtime | ✅ Pronto |
| Interface genérica `BaseRLPolicy` | ✅ Pronto |
| Export traces reais | ✅ Pronto |
| Checkpoints treinados | ✅ offline_sac + offline_awac |
| Integração runtime completa | 🟡 Pendente (ainda consome energy commands) |
| Testes unitários | 🔴 Pendente |
| Figuras CAORA | 🔴 Pendente |

### Arquivos

- `src/rapp_rl_policy.py` — Interface genérica com adaptadores A3C e SAC
- `src/rapp_sac_resource_model.py` — Modelo de demanda compartilhada
- `drlexp/src/drl/caora_sac_environment.py` — Ambiente gym
- `drlexp/training/train_sac.py` — Treino SAC/AWAC
- `scripts/export_sac_workload_trace.py` — Exporta traces reais do Data Lake
