# Trilha `article00`: Execucao Minima

Este documento descreve a trilha minima para reproduzir a base metodologica do
`artigo00` em paralelo ao pipeline atual do GreenRAN.

Referencia do paper:

- [`/home/robert/Downloads/artigo00.pdf`](</home/robert/Downloads/artigo00.pdf>)

Auditoria relacionada:

- [ARTICLE00_AUDITORIA_RESULTADOS.md](/home/robert/orange_nuclear/docs/ARTICLE00_AUDITORIA_RESULTADOS.md:1)
- [ARTICLE00_DATASET_AUDITORIA.md](/home/robert/orange_nuclear/docs/ARTICLE00_DATASET_AUDITORIA.md:1)
- [ARTICLE00_VEREDITO_CONFORMIDADE.md](/home/robert/orange_nuclear/docs/ARTICLE00_VEREDITO_CONFORMIDADE.md:1)
- [ARTICLE00_PROTOCOLO_EXPERIMENTAL.md](/home/robert/orange_nuclear/docs/ARTICLE00_PROTOCOLO_EXPERIMENTAL.md:1)
- [ARTICLE00_DESENHO_TECNICO.md](/home/robert/orange_nuclear/docs/ARTICLE00_DESENHO_TECNICO.md:1)
- [ARTICLE00_COMPARISON_FIGURES.md](/home/robert/orange_nuclear/docs/ARTICLE00_COMPARISON_FIGURES.md:1)

## Objetivo

Isolar uma trilha `article00` com:

- dataset sintetico temporal;
- trainer dedicado;
- artefatos de treino e relatorio;
- figuras separadas da trilha atual do GreenRAN.

## Estado Atual

Ja existe no repositorio:

- trilha `article00` separada, com dataset sintetico temporal;
- trainer temporal dedicado;
- relatorios agregados por seed;
- figuras comparativas separadas da trilha GreenRAN;
- resultados finais fortes em `450` amostras e `200` epochs.

Essa trilha continua experimental e sem integracao ao runtime do GreenRAN, mas
ja nao esta mais no estado de scaffold inicial.

## Pipeline Minimo

### 1. Gerar o dataset sintetico

```bash
python3 scripts/generate_article00_dataset.py \
  --samples 600 \
  --seed 42
```

Saida padrao:

```text
runs/article00/datasets/seed_42/
├── article00_graph_reference.json
├── article00_metadata.json
└── timeseries.csv
```

### 2. Rodar o treino temporal da trilha `article00`

```bash
./drlexp/.venv/bin/python training/train_graphsage_article00.py \
  --dataset-dir runs/article00/datasets/seed_42
```

No estado atual, este script ja implementa a trilha temporal experimental:

- constroi o grafo temporal por timestamp;
- treina o `GraphSAGE` temporal com perda MSE;
- reconstrui a adjacencia `P-K` por correlacao + threshold;
- aplica pos-processamento com arestas conhecidas `A-P` e `A-K`;
- rotula conflitos `direct`, `indirect` e `implicit`.

Ele continua isolado do GreenRAN operacional e nao substitui o trainer
principal do projeto.

### 3. Rodar o pipeline minimo

```bash
python3 scripts/run_article00_experiments.py \
  --samples 600 \
  --seeds 42,43,44,45,46
```

Esse runner:

1. gera o dataset;
2. chama o trainer `article00`;
3. agrega relatorios por seed;
4. chama a geracao de figuras experimentais.

### 4. Gerar as figuras experimentais

```bash
./drlexp/.venv/bin/python scripts/generate_graphsage_article00_figures.py \
  --training-root runs/article00/training
```

No estado atual, esse script ja plota as seis figuras principais da trilha
`article00` em `runs/article00/figures/` e tambem registra um manifesto simples
dos artefatos encontrados.

## Convencao de Saida

```text
runs/article00/
├── datasets/
├── training/
├── figures/
└── reports/
```

## Resultado Atual Mais Forte

Hoje existem dois pontos de referencia importantes:

- melhor cenario geral do metodo:
  - `450` amostras
  - `200` epochs
  - `threshold = 0.2`
  - `parameter_kpi_f1 = 1.0`
  - `indirect_f1 = 1.0`
  - `implicit_f1 = 1.0`

- melhor cenario estrito em `threshold = 0.5`:
  - `450` amostras
  - `200` epochs
  - `hidden_dim = 32`
  - `embed_dim = 32`
  - `dropout = 0.05`
  - `temporal_radius = 3`
  - `temporal_decay = 0.7`
  - `fp_penalty_weight = 0.3`
  - `tp_reward_weight = 0.2`
  - `hard_positive_weight = 0.29`
  - `hard_positive_focus_kpis = K2`
  - `parameter_kpi_f1 = 0.988889`
  - `indirect_f1 = 1.0`
  - `implicit_f1 = 1.0`

## Observacao Metodologica

O gerador e o trainer atuais seguem bem a estrutura do paper, mas a trilha
continua experimental. Antes de tratar isso como reproducao metodologica final
em artigo, ainda e preciso:

- revisar as formulas do modelo sintetico contra o PDF;
- validar a topologia `A-P` e `A-K` contra a figura do paper;
- fechar a homologacao metodologica, nao apenas a performance experimental;
- separar claramente resultado experimental atual de evidencia final de artigo.
