# ML Runtime Melhorias

Este documento resume as melhorias aplicadas no pipeline de ML do GreenRAN sem alterar a politica do `rApp`.

## Objetivo

Ajustar a ML para:

- ficar causal no runtime;
- reduzir leakage entre treino e inferencia;
- parar de gerar `BLOCKED` espurio em estado saudavel;
- manter endurecimento rapido quando o risco previsto sobe.

## Arquivos Alterados

- [src/rapp_ml_predictor.py](/home/robert/orange_nuclear/src/rapp_ml_predictor.py)
- [training/train_ml_model.py](/home/robert/orange_nuclear/training/train_ml_model.py)
- artefatos atualizados em [models](/home/robert/orange_nuclear/models)

## Mudancas no Predictor

### 1. Lags causais no runtime

Antes, `lag_1` e afins acabavam usando o valor atual em vez do valor anterior. Isso criava mismatch entre treino e runtime.

Agora:

- `cvar_lag_1` representa `t-1`;
- `throughput_lag_1` representa `t-1`;
- `packet_loss_lag_1` representa `t-1`;
- `latency_lag_1` representa `t-1`.

Os historicos sao atualizados apenas **depois** da construcao das features do ciclo atual.

### 2. Rolling e trends alinhados com o treino

As features de:

- `cvar_rolling_*`
- `cvar_trend`
- `cvar_acceleration`

passaram a ser calculadas com o historico anterior ao ciclo atual, mantendo consistencia com o pipeline de treino.

### 3. Reequilibrio classificador vs regressor

No runtime, a decisao final da ML agora trata o regressor como arbitro principal em regime claramente saudavel.

Regra adicionada:

- se `predicted_cvar_ms` e `current_cvar_p95` estiverem em faixa saudavel;
- e nao houver alerta de deterioracao forte;
- entao um `classifier_decision = BLOCKED/CONDITIONAL` pode ser relaxado para `ALLOWED`.

Isso evita bloqueios espurios em rede saudavel.

## Mudancas no Treino

### 1. Split temporal

O treino deixou de usar `train_test_split` aleatorio e passou a usar holdout temporal.

Isso reduz leakage temporal e gera metricas mais honestas.

### 2. Cross-validation temporal

A validacao cruzada passou a usar `TimeSeriesSplit`, com escalonamento por fold.

### 3. Poda de features enviesadas

Foram removidas do modelo:

- `energy_history`
- `hour_sin`
- `hour_cos`
- `is_night`
- `is_weekend`
- `total_active_ues`

Motivo:

- `energy_history` induzia a ML a aprender a politica anterior, nao o estado da rede;
- features de horario estavam puxando viés operacional em vez de causalidade de rede;
- `total_active_ues` nao agregava valor real no conjunto atual.

## Resultado do Retreino

Modelo atualizado com base em `/tmp/rapp_data_lake.db`.

Leitura principal:

- o classificador ficou mais honesto e numericamente pior, como esperado apos remover leakage;
- o regressor continuou forte;
- o predictor passou a usar o regressor para evitar pessimismos falsos em estado saudavel.

## Validacao no Cenario Saudavel

No runtime vivo observado apos as mudancas:

- `ml_decision = ALLOWED`
- `ml_confidence ~= 0.64 - 0.66`
- `ml_predicted_cvar_ms ~= 34.1 - 34.5`
- concordancia com regras e estado final `ALLOWED`

Trecho observado:

- `[rApp ML] ALLOWED`
- `Concordancia ML=ALLOWED == Regras=ALLOWED`
- `POWER_DOWN_ECO`

## Leitura Correta

Essas melhorias nao tornam a ML soberana.

Ela continua:

- subordinada a `CAMERA_GUARD`;
- subordinada a `APP2_GUARD`;
- combinada com as regras e com a DRL dentro do `rApp`.

O que melhorou foi:

- a qualidade da inferencia da propria ML;
- a consistencia entre treino e runtime;
- a reducao de falso `BLOCKED` em rede saudavel.
