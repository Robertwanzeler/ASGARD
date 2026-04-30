# Estrutura Alvo da Trilha `article00`

Este documento define a estrutura minima recomendada para aproximar a trilha
atual de `GraphSAGE` ao metodo descrito em
[`/home/robert/Downloads/artigo00.pdf`](</home/robert/Downloads/artigo00.pdf>),
sem substituir o pipeline GreenRAN ja existente.

O objetivo aqui nao e alterar o runtime do projeto, e sim organizar uma trilha
paralela, reproduzivel e fiel ao artigo:

- grafo temporal por passo de tempo;
- features temporais de parametros e KPIs;
- encoder `GraphSAGE`;
- reconstrucao de adjacencia por correlacao + threshold;
- rotulagem de conflitos `direct`, `indirect` e `implicit`;
- geracao de figuras e tabelas do artigo.

## Principio de Organizacao

A recomendacao e manter duas trilhas separadas:

- `GreenRAN experimental`: pipeline atual baseado em datasets exportados dos
  cenarios do projeto;
- `article00 faithful`: reproducao metodologica do artigo em cima de um modelo
  sintetico e controlado.

Isso evita quebrar a trilha atual enquanto a reproducao do paper ainda estiver
em ajuste.

## Arvore Minima Recomendada

```text
docs/
├── ARTICLE00_EXPERIMENTOS.md
├── ARTICLE00_METODO.md
└── ESTUDO_ARTIGOS_BASE.md

training/
├── train_graphsage_conflicts.py
└── train_graphsage_article00.py

scripts/
├── run_article00_experiments.py
├── generate_article00_dataset.py
├── generate_graphsage_article00_figures.py
└── compare_graphsage_greenran_vs_article00.py

runs/
└── article00/
    ├── datasets/
    ├── training/
    ├── figures/
    └── reports/
```

## Arquivos e Conteudo Esperado

### `docs/ARTICLE00_EXPERIMENTOS.md`

Funcao:
- ser o guia operacional da trilha `article00`.

Deve conter:
- objetivo do artigo;
- topologia do modelo de conflito;
- definicao do dataset sintetico;
- comandos de geracao;
- comandos de treino;
- comandos de geracao de figuras;
- convencao de saida em `runs/article00/`.

Status esperado:
- documento curto e totalmente operacional.

### `docs/ARTICLE00_METODO.md`

Funcao:
- traduzir o paper para uma especificacao tecnica do repositorio.

Deve conter:
- definicao do grafo temporal `G_T = (V_T, E_T)`;
- definicao do vetor de features `x_t`;
- regra de vizinhanca `v_{t-1}, v_t, v_{t+1}`;
- objetivo de treino descrito no artigo;
- regra de reconstrucao da adjacencia por correlacao e threshold;
- passo de pos-processamento que injeta arestas conhecidas `A-P` e `A-K`;
- definicoes formais de `direct`, `indirect` e `implicit`.

Status esperado:
- especificacao metodologica, nao tutorial.

### `docs/ESTUDO_ARTIGOS_BASE.md`

Funcao:
- relacionar `article00` com o restante da hierarquia do projeto.

Deve conter:
- o que vem do paper;
- o que e adaptacao GreenRAN;
- o que ja esta implementado;
- o que ainda e desvio metodologico.

Status esperado:
- documento de alinhamento conceitual.

### `training/train_graphsage_article00.py`

Funcao:
- trainer fiel ao paper, separado do trainer atual.

Entradas esperadas:
- dataset temporal sintetico;
- definicao de parametros e KPIs;
- configuracao de epocas, hidden dim, embed dim, threshold.

Saidas esperadas:
- checkpoints;
- embeddings finais;
- matriz de correlacao;
- adjacencia reconstruida;
- metricas de reconstrucao;
- metricas de rotulagem de conflito.

Deve implementar:
- construcao do grafo temporal;
- encoder `GraphSAGE`;
- treino orientado a reconstrucao temporal;
- reconstrucao da adjacencia final;
- avaliacao por `precision`, `recall`, `F1`.

Nao deve:
- depender do dataset exportado do GreenRAN como fonte primaria.

### `training/train_graphsage_conflicts.py`

Funcao atual:
- manter a trilha GreenRAN atual.

Papel futuro:
- continuar como baseline do pipeline atual;
- nao ser reescrito para virar `article00`.

Motivo:
- ele hoje usa coocorrencia de linhas e feature engineering agregado;
- isso e util para o projeto, mas nao e a mesma formulacao do paper.

### `scripts/generate_article00_dataset.py`

Funcao:
- gerar o dataset sintetico do `article00`.

Deve conter:
- modelo com `4 xApps`, `7 parametros`, `4 KPIs`;
- ranges dos parametros;
- formulas dos KPIs;
- geracao dos passos temporais;
- exportacao do dataset em formato claro e reutilizavel.

Saidas esperadas:
- `timeseries.csv` ou `timeseries.jsonl`;
- metadados do experimento;
- relacao de parametros e KPIs usados.

### `scripts/run_article00_experiments.py`

Funcao:
- orquestrar a reproducao completa do paper.

Pipeline esperado:
1. gerar dataset sintetico;
2. treinar `train_graphsage_article00.py`;
3. reconstruir o grafo;
4. rotular conflitos;
5. salvar metricas e relatorios;
6. chamar a geracao de figuras finais.

Parametros esperados:
- numero de amostras;
- seed;
- epocas;
- threshold;
- pasta de saida.

### `scripts/generate_graphsage_article00_figures.py`

Funcao:
- produzir as figuras da trilha `article00`.

Deve gerar:
- reconstrucao F1 vs epochs;
- reconstrucao F1 vs threshold;
- indirect F1 vs epochs;
- indirect F1 vs threshold;
- implicit F1 vs epochs;
- implicit F1 vs threshold;
- tabelas resumo por seed e por configuracao.

### `scripts/compare_graphsage_greenran_vs_article00.py`

Funcao:
- comparar a trilha fiel ao paper com a trilha atual do GreenRAN.

Deve responder:
- quanto a formulacao atual diverge do paper;
- se os resultados do GreenRAN sao mais fortes ou mais fracos;
- se o ganho vem do metodo ou do cenario.

## Estrutura de Saida Recomendada

```text
runs/article00/
├── datasets/
│   ├── seed_42/
│   └── seed_43/
├── training/
│   ├── seed_42/
│   │   ├── checkpoints/
│   │   ├── training_summary.json
│   │   ├── reconstructed_adjacency.json
│   │   └── conflict_labels.json
│   └── seed_43/
├── figures/
│   ├── reconstruction_f1_vs_epochs.png
│   ├── reconstruction_f1_vs_threshold.png
│   ├── implicit_f1_vs_epochs.png
│   ├── implicit_f1_vs_threshold.png
│   ├── indirect_f1_vs_epochs.png
│   └── indirect_f1_vs_threshold.png
└── reports/
    ├── aggregate_report.json
    └── article00_summary.md
```

## Ordem Recomendada de Implementacao

1. Criar `generate_article00_dataset.py`.
2. Criar `train_graphsage_article00.py`.
3. Criar `run_article00_experiments.py`.
4. Criar `generate_graphsage_article00_figures.py`.
5. Documentar `ARTICLE00_EXPERIMENTOS.md`.
6. So depois comparar com `train_graphsage_conflicts.py`.

## Criterios Minimos para Dizer que a Trilha Esta Fiel

- usa grafo temporal por timestamp, nao coocorrencia estatica;
- usa features temporais de `P` e `K`;
- reconstrucao segue o fluxo embeddings -> correlacao -> threshold;
- injeta arestas conhecidas `A-P` e `A-K` no pos-processamento;
- aplica definicoes formais de `direct`, `indirect` e `implicit`;
- gera artefatos reprodutiveis em `runs/article00/`;
- consegue reproduzir pelo menos as curvas centrais descritas no paper.

## Diagnostico Final

Hoje o repositorio tem:
- uma trilha `GraphSAGE` funcional;
- resultados e figuras;
- documentacao parcial;
- referencias ao `article00`;
- ausencia dos fontes especificos dessa trilha.

Portanto, o passo correto nao e forcar a trilha atual a virar paper-faithful.
O passo correto e criar uma trilha paralela `article00`, organizada e
reprodutivel, e so depois comparar as duas abordagens.
