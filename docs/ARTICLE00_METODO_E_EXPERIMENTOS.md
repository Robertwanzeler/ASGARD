# `article00`: Metodo E Experimentos

Este e o documento tecnico consolidado da trilha `article00`.

Resumo executivo:

- [ARTICLE00_RESUMO_FINAL.md](/home/robert/orange_nuclear/docs/ARTICLE00_RESUMO_FINAL.md:1)

Referencia externa:

- [`/home/robert/Downloads/artigo00.pdf`](</home/robert/Downloads/artigo00.pdf>)

## Escopo

A trilha `article00` foi criada para:

- gerar dataset sintetico temporal;
- treinar um `GraphSAGE` temporal isolado;
- reconstruir relacoes `P-K`;
- rotular conflitos `direct`, `indirect` e `implicit`;
- produzir comparativos com `Random`, `GraphSAGE-CL` e `ARMD-GreenRAN`.

Ela nao integra o runtime do GreenRAN.

## Pipeline

Arquivos principais:

- gerador de dataset:
  - [generate_article00_dataset.py](/home/robert/orange_nuclear/scripts/generate_article00_dataset.py:1)
- trainer:
  - [train_graphsage_article00.py](/home/robert/orange_nuclear/training/train_graphsage_article00.py:1)
- runner:
  - [run_article00_experiments.py](/home/robert/orange_nuclear/scripts/run_article00_experiments.py:1)
- gerador de figuras:
  - [generate_graphsage_article00_figures.py](/home/robert/orange_nuclear/scripts/generate_graphsage_article00_figures.py:1)
- gerador dos comparativos:
  - [generate_article00_armd_comparison_figures.py](/home/robert/orange_nuclear/scripts/generate_article00_armd_comparison_figures.py:1)

## Estrutura De Saida

```text
runs/article00/
├── datasets/
├── training/
├── reports/
├── figures/
└── comparison_figures/
```

## Principais Mudancas Que Fizeram O Metodo Convergir

### 1. Modelagem temporal melhor

Os melhores cenarios usaram:

- `temporal_radius = 2` ou `3`
- `temporal_decay = 0.7`

Isso deu mais contexto temporal para separar melhor as relacoes do grafo.

### 2. Perda mais informada

Entraram termos para:

- penalizar falso positivo;
- recompensar verdadeiro positivo;
- forcar arestas reais criticas a cruzarem o threshold no caso mais dificil.

No caso estrito de `threshold = 0.5`, o gargalo residual estava concentrado em
arestas ligadas ao `K2`, especialmente no `seed 43`. O uso de
`hard_positive_focus_kpis = K2` foi o passo que destravou esse caso.

### 3. Selecao composta de checkpoint

Em vez de selecionar por uma metrica so, passou-se a usar:

- reconstrucao;
- `indirect`;
- `implicit`.

Isso evitou otimizar demais um alvo e perder o resto.

### 4. Capacidade do encoder

O melhor cenario estrito em `0.5` apareceu com:

- `hidden_dim = 32`
- `embed_dim = 32`
- `dropout = 0.05`

## Protocolo Experimental Consolidado

### Melhor cenario geral

- `450 samples`
- `200 epochs`
- `threshold = 0.2`
- `1.0 / 1.0 / 1.0`

Referencia estavel:

- [article00_final_reference_summary.json](/home/robert/orange_nuclear/runs/article00/reports/article00_final_reference_summary.json:1)

### Melhor cenario estrito em `0.5`

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
- `0.988889 / 1.0 / 1.0`

### Comparacao consolidada com o artigo

| Metrica | Artigo base | ARMD-GreenRAN | Leitura |
|---|---|---|---|
| Reconstrucao geral | `1.0` com `450 samples`, `600 epochs`, `threshold 0.5` | `1.0` com `450 samples`, `200 epochs`, `threshold 0.2` | melhor cenario geral do metodo |
| Reconstrucao estrita em `0.5` | `1.0` com `450 samples`, `600 epochs` | `0.988889` com `450 samples`, `200 epochs` | quase igual |
| `Indirect` | `>= 600 epochs` para `1.0` | `1.0` com `450 samples`, `200 epochs` | melhor em eficiencia |
| `Implicit` | `1.0` com `450 samples`, `200 epochs`, `threshold 0.5` | `1.0` com `450 samples`, `200 epochs` | igualado |

### Corridas adicionais de dataset

Tambem foram rodadas variantes para:

- `50 samples`
- `150 samples`
- `450 samples`

Esses artefatos alimentam os comparativos por `Dataset`.

### Exploracao adicional de `threshold = 0.9`

Foi aberta uma trilha separada em `runs/article00_threshold_09/` para testar
melhorias especificas de `threshold = 0.9`, sem sobrescrever a trilha principal
de `article00`.

O resultado final dessa exploracao foi:

- melhora parcial em reconstrucao em alguns candidatos;
- melhora parcial em `implicit` em alguns candidatos;
- ausencia de ganho consistente no agregado completo;
- nenhuma configuracao suficientemente forte para substituir os cenarios
  principais em `0.2` ou `0.5`.

Decisao final:

- manter `0.9` apenas como referencia de sensibilidade mais rigida;
- nao promover esses runs aos docs/graficos principais.

## Graficos Consolidados

Graficos comparativos principais:

- [runs/article00/comparison_figures](/home/robert/orange_nuclear/runs/article00/comparison_figures)

Pasta organizada para consulta:

- [graficos_selecionados_artigo](/home/robert/orange_nuclear/runs/article00/graficos_selecionados_artigo)

Separacao principal:

- `reconstrucao`
- `indirect`
- `implicit`

Dentro de cada metrica:

- `threshold 0.2`
- `threshold 0.5`
- `threshold 0.9`
- `dataset 50`
- `dataset 150`
- `dataset 450`

Regra dos pontos de referencia:

- `Epochs for Threshold(...)` usa `Random`;
- `Epochs for Dataset(...)` usa `No Threshold`.

Observacao sobre relatorios:

- [aggregate_report.json](/home/robert/orange_nuclear/runs/article00/reports/aggregate_report.json:1)
  e [article00_run_multiseed.json](/home/robert/orange_nuclear/runs/article00/reports/article00_run_multiseed.json:1)
  refletem a ultima corrida executada;
- para citar o melhor cenario consolidado da trilha, usar
  [article00_final_reference_summary.json](/home/robert/orange_nuclear/runs/article00/reports/article00_final_reference_summary.json:1).

## Limites

Mesmo com o desempenho forte, a trilha continua:

- experimental;
- separada do GreenRAN operacional;
- ainda sem homologacao metodologica final do paper.

Ela ja e suficiente para comparacao experimental forte, mas nao deve ser vendida
como reproducao metodologica final sem a etapa de homologacao.
