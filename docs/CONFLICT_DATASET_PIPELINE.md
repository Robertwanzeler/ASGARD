# GreenRAN Conflict Dataset Pipeline

Este pipeline transforma as decisoes do rApp em um dataset de conflitos e em
um grafo operacional. Ele e o passo intermediario entre regras explicitas de
arbitragem e o treino do grafo aprendido com GraphSAGE.

Para o fluxo final de treino, multiseed e interpretacao dos graficos, ver
tambem:

- `docs/ANALISE_CONFLITOS_GNN.md`

## Objetivo

Gerar artefatos para analise e treinamento:

- CSV tabular com eventos de conflito, KPIs, decisao do rApp, mitigacao e
  contexto de rede.
- JSON de grafo com nos e arestas agregadas por peso, tipo de conflito e KPI.

## Comando

```bash
python3 scripts/export_conflict_dataset.py --hours 24
```

Saidas padrao:

- `/tmp/greenran_conflict_dataset.csv`
- `/tmp/greenran_conflict_graph.json`

Tambem e possivel escolher caminhos:

```bash
python3 scripts/export_conflict_dataset.py \
  --hours 6 \
  --dataset /tmp/conflicts_6h.csv \
  --graph /tmp/conflict_graph_6h.json
```

## Dataset

Cada linha representa um evento de conflito observado:

- `source_agent`: agente ou xApp associado a origem do conflito.
- `parameter`: parametro de controle envolvido.
- `affected_service`: App1, App2 ou outro servico afetado.
- `affected_kpi`: KPI que cruzou limiar ou faixa de guarda.
- `observed_value` e `threshold_value`: valor observado e limiar.
- `observed_delta_from_threshold`: distancia ate o limiar.
- `previous_observed_value`: ultimo valor observado para o mesmo KPI.
- `observed_delta_from_previous`: variacao do KPI em relacao ao evento anterior.
- `mitigation_action`: acao aplicada pelo rApp.
- `latest_energy_command` e `latest_power_percent`: comando de energia mais recente.
- `cvar_ms`, `p95_ms`, `throughput_mbps`: contexto global da rede.
- `ml_decision`, `ml_confidence`, `ml_predicted_cvar_ms`: contexto ML.

## Grafo

O JSON segue o schema `greenran.conflict_graph.v1`:

```text
agent -> parameter -> kpi -> rApp-ResourceOptimizer -> mitigation -> service
```

Tipos de nos:

- `agent`
- `parameter`
- `kpi`
- `arbiter`
- `mitigation`
- `service`

Tipos de arestas:

- `controls`
- `affects`
- `belongs_to`
- `triggers_arbitration`
- `mitigates`
- `protects`

## Uso esperado

Este pipeline permite:

- auditar se a hierarquia App1 > App2 > CVaR > ML > DRL esta sendo respeitada;
- medir quais KPIs geram mais conflitos;
- alimentar a visualizacao de conflitos;
- preparar os dados para a matriz de adjacencia aprendida.

O proximo passo tecnico e treinar o reconstrutor GraphSAGE sobre os datasets e
avaliar reconstrucao, conflito implicito e conflito indireto.

## Aprendizado da matriz

O script abaixo consome o CSV exportado e o grafo operacional para gerar uma
matriz de adjacencia aprendida:

```bash
python3 scripts/learn_conflict_matrix.py
```

Saidas padrao:

- `/tmp/greenran_conflict_adjacency.json`
- `/tmp/greenran_conflict_report.json`

O objetivo e classificar as arestas em:

- `confirmed_by_data`
- `weak_or_low_support`
- `spurious_in_baseline`
- `emergent_from_data`

Com isso, o projeto passa a ter duas camadas:

1. grafo operacional definido pelas decisoes do rApp;
2. grafo aprendido a partir dos eventos exportados.

## Protocolo experimental por cenarios

O fluxo completo de coleta por cenarios e rodadas fica em:

```bash
python3 scripts/run_conflict_experiments.py
```

Esse runner:

- coleta por janelas temporais exatas;
- exporta dataset e grafo por rodada;
- aprende a matriz por rodada;
- exporta datasets completos por cenario;
- gera subsets `50`, `150` e `450`.

### Modo automatico

Para rodar sem prompts:

```bash
python3 scripts/run_conflict_experiments.py --rounds 10 --duration 120 --auto
```

Para rodar sem prompts e com troca automatica de cenario:

```bash
python3 scripts/run_conflict_experiments.py --rounds 10 --duration 120 --auto --auto-switch
```

Com `--auto-switch`, o runner escreve um arquivo de controle em `/tmp/` e aplica
perfis logicos nos componentes abaixo:

- `src/rapp_orchestrator.py`
- `apps/app1_vigilancia/backend/services.py`
- `apps/app2_monitoramento/backend/simulate_sensors.py`

Isso garante que os cenarios do protocolo nao sejam apenas rotulos, mas
condicoes operacionais controladas dentro do experimento.

## Treino GraphSAGE

Depois da coleta, o treino pode ser executado com:

```bash
./drlexp/.venv/bin/python training/train_graphsage_conflicts.py \
  --experiment-dir runs/experimentos_conflitos/experimento_principal \
  --epochs 50,100,200,400,600,800,1000 \
  --subset-sizes 50,150,450
```

O script grava artefatos em:

- subdiretorios de treino por seed e por cenario.

Por caso treinado:

- `training_summary.json`
- `history.csv`
- checkpoints `epoch_*.pt`

Relatorio agregado:

- `aggregate_report.json`

## Estado atual

Hoje o pipeline ja cobre:

- coleta por rodadas;
- export por cenario;
- subsets `50`, `150`, `450`;
- treino GraphSAGE por epocas;
- avaliacao multiseed;
- graficos finais para:
  - reconstrucao;
  - conflito implicito;
  - conflito indireto.

Os detalhes finais de leitura dos cenarios e dos 6 graficos principais ficam em:

- `docs/ANALISE_CONFLITOS_GNN.md`
