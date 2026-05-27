# SAC Real Collection Playbook

Este procedimento assume que o objetivo e treinar e validar o controlador no **mesmo cenario real** que sera usado no artigo. Por isso, a regra operacional e simples:

- nao alterar carga UDP
- nao alterar periodicidade E2
- nao alterar dinamica do `ns-3`
- aceitar que o `sim_time` avanca devagar

## 1. Subir o cenario real

Use sempre o runtime com `AWAC` online:

```bash
cd /home/robert/orange_nuclear
GREENRAN_RL_POLICY=awac GREENRAN_APP2_REAL_SENSOR_WAIT_SECONDS=90 bash scripts/run_drl_article_real_collection.sh
```

Isso sobe:

- `ns-3`
- `csv_to_metrics`
- `rApp`
- App1/App2/App3
- alternador de eventos
- observabilidade

## 2. Acompanhar a coleta

Watcher recomendado:

```bash
cd /home/robert/orange_nuclear
bash scripts/watch_sac_real_collection.sh
```

O watcher mostra:

- `sim_time`
- ultima alocacao
- controlador ativo
- quantidade de linhas exportadas
- diversidade de `d_ran`, `d_ai`, `ran_completion`, `utilization`

## 3. O que observar durante a execucao

Os sinais principais sao:

- `sim_time_range.end`
  - confirma se o cenario esta avancando
- `controller_id`
  - deve aparecer `caora_awac_actor` quando o `AWAC` estiver aplicando
- `ran_completion_ratio`
  - precisa sair do `1.0` constante
- `d_ran`
  - precisa variar de verdade
- `utilization_ratio`
  - precisa ter cobertura suficiente

## 4. Quando **nao** parar

Continue coletando se um ou mais destes pontos ainda forem verdade:

- `CSV rows < 1000`
- `ran_completion_ratio` com `unique = 1`
- `d_ran` com pouca diversidade
- `utilization_ratio` com pouca diversidade

Na pratica, se o watcher ainda disser:

- `dataset ainda pequeno para treino forte`
- `ran_completion ainda sem diversidade real`
- `d_ran ainda com pouca cobertura`

entao ainda nao e uma boa hora para retreinar.

## 5. Quando exportar

O watcher ja reexporta automaticamente, mas voce pode fazer manualmente:

```bash
cd /home/robert/orange_nuclear
python3 scripts/export_sac_workload_trace.py \
  --db /tmp/rapp_data_lake.db \
  --output-csv runs/sac_bootstrap/workload_trace_real.csv
```

Use isso:

- antes de uma rodada de treino
- antes de encerrar uma coleta longa
- quando quiser congelar um snapshot do dataset

## 6. Quando retreinar

Retreine quando estas condicoes ja estiverem razoaveis:

- `ran_completion_ratio` com mais de `1` valor
- `d_ran` com diversidade util
- `utilization_ratio` com diversidade util
- volume de linhas suficiente para uma rodada nova

Treino atual:

```bash
cd /home/robert/orange_nuclear
drlexp/.venv/bin/python drlexp/training/train_sac.py \
  --workload-csv runs/sac_bootstrap/workload_trace_real.csv \
  --output-dir runs/sac_bootstrap/offline_awac_latest
```

## 7. Quando uma rodada “presta”

Para o treinamento offline atual, uma rodada passa a prestar quando:

- o `AWAC` aparece no runtime
- o `sim_time` continua andando
- o CSV cresce continuamente
- `ran_completion_ratio` deixa de ser constante
- o lado RAN entra em disputa real

## 8. Se o `sim_time` parecer travado

Cheque:

```bash
python3 -c "import json; d=json.load(open('/tmp/xapp_metrics/extended_metrics.json')); print(d.get('timestamp_iso'), d.get('sim_time_range'))"
```

Interpretacao:

- `sim_time` parado no mesmo valor por muito tempo real: problema operacional
- `sim_time` subindo devagar: custo real do cenario, mas coleta ainda valida

## 9. Encerrar com seguranca

```bash
cd /home/robert/orange_nuclear
bash scripts/stop_all.sh
```

Depois, se quiser congelar o dataset final da rodada:

```bash
cd /home/robert/orange_nuclear
python3 scripts/export_sac_workload_trace.py \
  --db /tmp/rapp_data_lake.db \
  --output-csv runs/sac_bootstrap/workload_trace_real.csv
```

## 10. Regra operacional final

Se a meta for o artigo no **cenario real**, a estrategia correta e:

- nao acelerar artificialmente o cenario
- deixar a coleta rodar no ambiente fiel
- retreinar quando o dataset real tiver cobertura suficiente
- avaliar o `AWAC` nesse mesmo regime
