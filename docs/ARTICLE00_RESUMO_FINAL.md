# `article00`: Resumo Final

Este e o documento executivo da trilha `article00`.

Referencia externa:

- [`/home/robert/Downloads/artigo00.pdf`](</home/robert/Downloads/artigo00.pdf>)

Documentos tecnicos consolidados:

- [ARTICLE00_METODO_E_EXPERIMENTOS.md](/home/robert/orange_nuclear/docs/ARTICLE00_METODO_E_EXPERIMENTOS.md:1)

## Objetivo

Manter uma trilha experimental separada do GreenRAN operacional para reproduzir,
adaptar e comparar a ideia do `artigo00` contra o metodo `ARMD-GreenRAN`.

Essa trilha:

- nao entra no runtime do GreenRAN;
- nao substitui a trilha principal de conflitos do projeto;
- serve para reproducao metodologica, comparacao e figuras de artigo.

## Melhor Resultado Geral

Cenario final mais forte do metodo:

- `450 samples`
- `200 epochs`
- `threshold = 0.2`
- `parameter_kpi_f1 = 1.0`
- `indirect_f1 = 1.0`
- `implicit_f1 = 1.0`

Referencia:

- [aggregate_report.json](/home/robert/orange_nuclear/runs/article00/reports/aggregate_report.json:1)

## Melhor Resultado Estrito Em `threshold = 0.5`

Melhor ponto encontrado para comparacao direta mais conservadora:

- `450 samples`
- `200 epochs`
- `hidden_dim = 32`
- `embed_dim = 32`
- `dropout = 0.05`
- `temporal_radius = 3`
- `temporal_decay = 0.7`
- `fp_penalty_weight = 0.3`
- `tp_reward_weight = 0.2`
- `hard_positive_weight = 0.29`
- `hard_positive_focus_kpis = K2`

Resultado:

- `parameter_kpi_f1 = 0.988889`
- `indirect_f1 = 1.0`
- `implicit_f1 = 1.0`

## Comparacao Com O Artigo

Leitura honesta:

- no melhor cenario geral do metodo, o `ARMD-GreenRAN` supera o artigo;
- no cenario estrito de `threshold = 0.5`, o metodo iguala `indirect` e
  `implicit`, e fica muito proximo de `1.0` na reconstrucao total;
- a vantagem principal veio de melhor modelagem temporal e melhor funcao de
  treino, nao de simplesmente aumentar o numero de epochs.

## O Que Melhorou O Metodo

Os ganhos em apenas `200 epochs` vieram principalmente de:

- janela temporal expandida;
- perda com penalizacao de falso positivo;
- recompensa de verdadeiro positivo;
- hard-positive focado em `K2` no caso estrito de `0.5`;
- selecao composta de checkpoint;
- capacidade maior do encoder no melhor ponto de `0.5`.

## Onde Estao Os Artefatos

Relatorios:

- [runs/article00/reports](/home/robert/orange_nuclear/runs/article00/reports)

Figuras comparativas:

- [runs/article00/comparison_figures](/home/robert/orange_nuclear/runs/article00/comparison_figures)

Pasta organizada para artigo:

- [graficos_selecionados_artigo](/home/robert/orange_nuclear/runs/article00/graficos_selecionados_artigo)

Nessa pasta, os graficos foram separados em:

- `threshold 0.2`
- `threshold 0.5`
- `threshold 0.9`
- `dataset 50`
- `dataset 150`
- `dataset 450`

## Regra De Interpretacao Dos Graficos

- quando o grafico e `Epochs for Threshold(...)`, o ponto de referencia e
  `Random`;
- quando o grafico e `Epochs for Dataset(...)`, o ponto de referencia e
  `No Threshold`.

## Status

Status atual da trilha:

- experimental forte;
- separada do GreenRAN;
- com artefatos e comparativos consolidados;
- ainda nao homologada como reproducao metodologica final do paper.
