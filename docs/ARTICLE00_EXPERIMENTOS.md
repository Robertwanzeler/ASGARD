# Protocolo Experimental Artigo00

Este documento descreve como o GreenRAN coleta dados para comparar o cenario
atual com a linha metodologica do `artigo00`, sem afirmar que a implementacao
ja reproduz o treino `GraphSAGE` do artigo.

## Objetivo

O protocolo existe para gerar:

- janelas temporais limpas por rodada;
- datasets de conflito sem mistura entre rodadas;
- grafo operacional exportado pelo rApp;
- matriz aprendida a partir dos dados exportados;
- conjuntos comparaveis com os tamanhos `50`, `150` e `450` usados no artigo.

## O que ja existe

- `scripts/export_conflict_dataset.py`
  - exporta CSV e grafo de conflitos;
  - aceita `--since-ts` e `--until-ts` para recorte exato da rodada.
- `scripts/learn_conflict_matrix.py`
  - aprende uma matriz operacional heuristica a partir do CSV;
  - compara o grafo aprendido com o grafo operacional.
- `scripts/run_article00_experiments.py`
  - orquestra o protocolo inteiro por cenarios e rodadas;
  - gera artefatos por rodada, por cenario e do experimento completo.

## O que ainda nao existe

Ainda nao existe treino `GraphSAGE/GNN` por epocas.

Por isso:

- os tamanhos `50`, `150`, `450` estao alinhados ao artigo;
- o `threshold=0.5` esta alinhado ao artigo;
- a referencia a `600 epochs` fica registrada apenas como meta futura;
- o learner atual continua heuristico/estatistico.

Em outras palavras:

- hoje: `coleta + reconstrucao heuristica do grafo`;
- depois: `treino GraphSAGE com epocas e metricas comparativas`.

## Cenarios experimentais

O runner cobre `7` cenarios:

1. `baseline_saude`
2. `app1_throughput`
3. `app1_latencia`
4. `app2_degradado_leve`
5. `app2_degradado_critico`
6. `conflito_implicito`
7. `recuperacao`

## Modo automatico

O runner suporta dois niveis de automacao.

### `--auto`

Remove os prompts de `Enter` e deixa a coleta seguir ate o fim.

### `--auto-switch`

Aplica um perfil de cenario automaticamente via arquivo de controle:

- arquivo: `/tmp/article00_scenario_control.json`
- consumidor `App1/rApp`: override logico dos KPIs de camera;
- consumidor `App2`: override do simulador de sensores.

Isso faz com que o nome do cenario corresponda a uma condicao operacional
realmente diferente dentro do experimento.

## O que o `--auto-switch` altera

### App1 / rApp

O arquivo de controle sobrescreve temporariamente:

- `throughput_mbps` minimo por camera;
- `avg_throughput_mbps`;
- `latency_ms`;
- `active_cameras`;
- `critical_cameras`;
- `throughput_ready`.

Esse override e consumido em:

- `src/rapp_orchestrator.py`
- `apps/app1_vigilancia/backend/services.py`

Com isso, a decisao do rApp e a interface/API do App1 ficam coerentes entre si.

### App2

O simulador de sensores passa a obedecer:

- `connected_sensors`;
- `error_sensors`;
- `low_battery_sensors`;
- `packet_loss_percent`;
- `delivery_success_percent` indiretamente;
- `avg_latency_ms`;
- `avg_rssi_dbm`;
- `avg_battery_percent`;
- `avg_power_mw`;
- `network_utilization_percent`.

Esse override e consumido em:

- `apps/app2_monitoramento/backend/simulate_sensors.py`

## Perfis aplicados

### `baseline_saude`

- App1 com throughput minimo acima de `30 Mbps`;
- latencia baixa;
- App2 com `17/17` sensores e packet loss baixo.

### `app1_throughput`

- App1 com throughput minimo em faixa de guarda ou bloqueio;
- App2 saudavel;
- objetivo: evidenciar conflito por throughput.

### `app1_latencia`

- App1 com latencia em faixa de guarda ou bloqueio;
- App2 saudavel;
- objetivo: evidenciar conflito por latencia.

### `app2_degradado_leve`

- App2 em torno de `16/17` sensores;
- packet loss moderado;
- delivery perto do limiar de guarda.

### `app2_degradado_critico`

- App2 abaixo da faixa segura;
- packet loss alto;
- delivery abaixo do minimo esperado.

### `conflito_implicito`

- KPIs globais podem permanecer saudaveis;
- App1 local degrada;
- objetivo: mostrar que CVaR/P95 global nao bastam.

### `recuperacao`

- rodada comeca degradada;
- no meio da janela, o controle volta para saudavel;
- objetivo: observar histerese, guarda e estabilizacao.

## Comandos recomendados

Coleta automatica completa:

```bash
python3 scripts/run_article00_experiments.py --rounds 10 --duration 120 --auto --auto-switch
```

Coleta automatica com pausas entre rodadas e cenarios:

```bash
python3 scripts/run_article00_experiments.py \
  --rounds 10 \
  --duration 120 \
  --auto \
  --auto-switch \
  --round-gap 15 \
  --scenario-gap 30
```

Rodar apenas um cenario:

```bash
python3 scripts/run_article00_experiments.py \
  --scenario baseline_saude \
  --rounds 10 \
  --duration 120 \
  --auto \
  --auto-switch
```

## Saidas

Cada execucao gera:

- `experiment_manifest.json`
- `experiment_round_summary.csv`
- `experiment_report.json`

Por cenario:

- `scenario_manifest.json`
- `round_summary.csv`
- `scenario_report.json`

Por rodada:

- `conflict_dataset.csv`
- `conflict_graph.json`
- `conflict_adjacency.json`
- `conflict_report.json`
- snapshots antes e depois

O diretorio raiz fica em:

- `runs/article00_experiments/<timestamp>_artigo00_protocol/`

## Leitura correta dos resultados

O que pode ser comparado com o artigo agora:

- volume de amostras coletadas;
- estabilidade da reconstrucao do grafo;
- crescimento de arestas `confirmed_by_data`;
- ocorrencia de conflitos diretos, indiretos e implicitos;
- comportamento por cenario.

O que ainda nao pode ser comparado de forma direta:

- acuracia final de `GraphSAGE`;
- curva de convergencia por epocas;
- resultados de treino com `600 epochs`.

## Limite importante

O `--auto-switch` automatiza o cenario logico do experimento em App1, App2 e
rApp. Ele nao reprograma o `ns-3` em tempo real.

Para o objetivo atual, isso e suficiente porque a comparacao com o artigo esta
centrada em:

- eventos de conflito;
- relacao entre agente, parametro e KPI;
- arbitragem do rApp;
- reconstrucao do grafo.

## Proximo passo cientifico

Depois de acumular massa de dados suficiente:

1. consolidar datasets maiores por cenario;
2. medir queda de arestas `weak_or_low_support`;
3. introduzir treino `GraphSAGE/GNN`;
4. comparar reconstrucao aprendida com o grafo operacional.
