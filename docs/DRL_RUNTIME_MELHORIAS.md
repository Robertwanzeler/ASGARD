# DRL Runtime - Melhorias no Predictor

## Escopo

Esta rodada melhorou o comportamento da DRL em runtime sem alterar a politica do `rApp`.

Arquivos principais:

- `src/rapp_drl_predictor.py`
- `scripts/smoke_drl_sequence.py`
- `scripts/summarize_drl_trace.py`
- `apps/app2_monitoramento/backend/simulate_sensors.py`

O `src/rapp_orchestrator.py` nao foi modificado para estas melhorias.

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

## Conclusao

A DRL ficou melhor em runtime sem alterar a politica do `rApp`.

O ganho principal foi:

- menos conservadorismo espurio em cenario saudavel
- mais estabilidade temporal
- mais rastreabilidade por trace
- melhor leitura de risco no proprio predictor

O limite que permanece intencional:

- camera e App2 continuam podendo sobrescrever a DRL quando entram em guarda ou violacao.
