# Experimento D da Trilha `article00`

Este documento registra a ideia e o escopo do `Experimento D` da trilha
experimental `article00`.

Objetivo:

- melhorar o desempenho com `450` amostras;
- manter os thresholds do artigo: `0.2`, `0.5`, `0.9`;
- evitar alterar o protocolo central do paper mais do que o necessario;
- atacar o problema pelo lado da modelagem, nao pelo lado de mudar o criterio
  de avaliacao.

## Motivacao

Depois do `Experimento C`, a trilha melhorou bastante em `indirect`, mas ainda
ficou abaixo do artigo no conjunto agregado de metricas.

O principal gargalo observado foi:

- excesso de arestas espurias em alguns seeds;
- suporte temporal possivelmente simples demais;
- selecao de melhor checkpoint muito focada em uma unica leitura do problema.

## Hipotese

O `Experimento D` assume que ainda e possivel ganhar desempenho em `450`
amostras sem mexer nos thresholds oficiais do artigo, desde que o trainer fique
mais informativo em tres pontos:

1. suporte temporal mais rico;
2. loss mais estruturada;
3. selecao de checkpoint mais equilibrada.

## Componentes Do Experimento

### 1. Janela temporal expandida

Em vez de usar apenas a vizinhanca imediata:

- `t-1`
- `t+1`

o trainer pode considerar tambem:

- `t-2`
- `t+2`

com peso decaindo pela distancia temporal.

Objetivo:

- estabilizar a informacao temporal;
- reduzir dependencia excessiva de variacoes locais;
- melhorar a inferencia de correlacao estrutural.

### 2. Loss hibrida

O `Experimento C` introduziu penalizacao de falso positivo.

O `Experimento D` completa isso com tres partes:

- `reconstruction_loss`
- `fp_penalty_loss`
- `tp_reward_loss`

Ideia:

- continuar punindo pares `P-K` falsos com correlacao alta;
- incentivar pares `P-K` verdadeiros a manter correlacao alta;
- evitar que o modelo fique apenas conservador demais.

### 3. Selecao composta de checkpoint

Em vez de escolher o melhor checkpoint apenas por uma metrica unica, o
`Experimento D` pode usar um score composto baseado em:

- `parameter_kpi_f1`
- `indirect_f1`
- `implicit_f1`

Objetivo:

- evitar que o melhor checkpoint para um alvo piore demais os demais;
- aproximar a leitura daquilo que se deseja como melhora de metodo.

## O Que O Experimento D Nao Faz

O `Experimento D` nao deve:

- mudar os thresholds do artigo;
- integrar qualquer coisa ao GreenRAN operacional;
- substituir a trilha original do paper;
- maquiar metricas com regras manuais de pos-processamento.

## Configuracao-Alvo Inicial

O primeiro envelope a ser testado para o `Experimento D` e:

- `samples = 450`
- `epochs = 200`
- `thresholds = 0.2,0.5,0.9`
- `fp_penalty_weight = 0.4`
- `fp_penalty_margin = 0.1`

Sobre isso, entram as novas variacoes:

- `temporal_radius = 2`
- `temporal_decay < 1.0`
- `tp_reward_weight > 0`
- score composto de selecao

## Criterio De Sucesso

O `Experimento D` sera considerado promissor se, com `450` amostras, conseguir:

- manter `indirect_f1 = 1.0`;
- elevar `parameter_kpi_f1`;
- elevar `implicit_f1`;
- reduzir variacao entre seeds.

## Resultado Atual

O `Experimento D` primeiro convergiu para um cenario vencedor geral com:

- `samples = 450`
- `epochs = 200`
- `thresholds = 0.2,0.5,0.9`
- `selection_threshold = 0.2`
- `hidden_dim = 24`
- `embed_dim = 24`
- `dropout = 0.05`
- `temporal_radius = 2`
- `temporal_decay = 0.7`
- `fp_penalty_weight = 0.4`
- `fp_penalty_margin = 0.1`
- `tp_reward_weight = 0.2`
- `tp_reward_margin = 0.5`
- `selection_mode = composite`

No agregado multiseed:

- `parameter_kpi_f1 = 1.0`
- `indirect_f1 = 1.0`
- `implicit_f1 = 1.0`

## Extensoes Posteriores

Depois do `Experimento D` base, a trilha ganhou extensoes para atacar o caso
estrito de `threshold = 0.5`, sem integrar nada ao GreenRAN:

- `hard_positive_loss` focada em arestas verdadeiras `P-K`;
- foco opcional por KPI, usado principalmente em `K2`;
- `hard_negative_loss` experimental para conter arestas falsas no mesmo KPI;
- aumento de capacidade do encoder para `hidden_dim = 32` e `embed_dim = 32`.

O melhor cenario estrito em `threshold = 0.5` ficou:

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

No agregado multiseed desse cenario:

- `parameter_kpi_f1 = 0.988889`
- `indirect_f1 = 1.0`
- `implicit_f1 = 1.0`

## Conclusao

O `Experimento D` deixou de ser apenas uma hipotese de melhoria e passou a ser
o nucleo do melhor cenario experimental da trilha `article00`, sem mexer nos
thresholds oficiais do artigo.
