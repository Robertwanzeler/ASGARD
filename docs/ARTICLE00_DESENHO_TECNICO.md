# Desenho Tecnico Experimental da Trilha `article00`

Este documento fixa o desenho tecnico da trilha `article00` e destaca o que ja
foi implementado versus o que ainda permanece como alvo metodologico.

Referencias:

- [artigo00.pdf](</home/robert/Downloads/artigo00.pdf>)
- [ARTICLE00_METODO.md](/home/robert/orange_nuclear/docs/ARTICLE00_METODO.md:1)
- [ARTICLE00_PROTOCOLO_EXPERIMENTAL.md](/home/robert/orange_nuclear/docs/ARTICLE00_PROTOCOLO_EXPERIMENTAL.md:1)
- [ARTICLE00_EXPERIMENTO_D.md](/home/robert/orange_nuclear/docs/ARTICLE00_EXPERIMENTO_D.md:1)

## Objetivo

Traduzir o paper para uma arquitetura implementavel dentro do repositorio, sem
misturar essa trilha com o pipeline atual do GreenRAN.

## Pipeline Alvo

1. gerar a serie temporal sintetica
2. construir o grafo temporal `G_T`
3. montar o vetor `x_t` por vertice temporal
4. treinar o encoder `GraphSAGE`
5. obter embeddings finais por tempo
6. reconstruir a adjacencia por correlacao + threshold
7. injetar arestas conhecidas `A-P` e `A-K`
8. rotular `direct`, `indirect` e `implicit`
9. computar metricas e gerar figuras

## Estado Atual

No estado atual do repositorio, a trilha ja implementa:

- geracao da serie temporal sintetica;
- exportacao do grafo temporal `G_T`;
- treino temporal com `GraphSAGE`;
- reconstrucao `P-K` por correlacao + threshold;
- pos-processamento com arestas conhecidas `A-P` e `A-K`;
- rotulagem `direct`, `indirect` e `implicit`.

O que ainda falta nao e a existencia do pipeline, e sim sua homologacao fina
contra o paper.

## Linha Atual De Melhoria

A linha atual de melhoria do trainer, sem mexer nos thresholds do artigo, e o
`Experimento D`:

- janela temporal expandida;
- loss hibrida com recompensa de verdadeiro positivo;
- selecao composta de checkpoint.

Essa linha existe para tentar melhorar:

- reconstrucao `P-K`;
- `indirect`;
- `implicit`;

sem alterar o protocolo principal de avaliacao.

## Estrutura de Dados Alvo

### Serie temporal de entrada

Arquivo atual:

- `timeseries.csv`

Colunas esperadas:

- `time_index`
- `P1..P7`
- `K1..K4`

### Grafo temporal `G_T`

Representacao alvo:

- um vertice `v_t` por instante `t`
- arestas temporais entre `v_{t-1}`, `v_t`, `v_{t+1}`

Estrutura conceitual:

```text
v0 -- v1 -- v2 -- v3 -- ... -- vT
```

Vizinhanca minima por vertice:

- passado imediato
- estado atual
- futuro imediato

## Vetor de Features `x_t`

Para cada vertice `v_t`, o vetor alvo deve conter:

- `P1..P7`
- `K1..K4`

Representacao conceitual:

```text
x_t = [P1_t, P2_t, P3_t, P4_t, P5_t, P6_t, P7_t, K1_t, K2_t, K3_t, K4_t]
```

## Encoder Alvo

O encoder futuro deve:

- agregar informacao da vizinhanca temporal;
- atualizar embedding de cada `v_t`;
- preservar dependencia temporal e dependencia estrutural.

Pseudocodigo conceitual:

```text
for each epoch:
  for each temporal vertex v_t:
    neighborhood = {v_(t-1), v_t, v_(t+1)}
    h_t = GraphSAGE(neighborhood, x_t)
```

## Reconstrucao de Adjacencia

Depois do treino:

1. coletar embeddings finais
2. computar matriz de similaridade/correlacao
3. aplicar threshold
4. obter adjacencia binaria reconstruida

Pseudocodigo conceitual:

```text
H = final_embeddings
S = correlation(H)
A_hat = binarize(S, threshold)
```

## Pos-processamento

Sobre `A_hat`, o pipeline deve:

1. injetar arestas conhecidas `A-P`
2. injetar arestas conhecidas `A-K`
3. manter `P-K` como relacoes reconstruidas/inferidas

Isso evita confundir conhecimento estrutural conhecido com inferencia do modelo.

## Rotulagem de Conflitos

A partir do grafo reconstruido:

- `direct`: dois xApps atuam no mesmo parametro
- `indirect`: parametros distintos afetam o mesmo KPI
- `implicit`: cadeia `A -> P -> K -> A -> P`

Pseudocodigo conceitual:

```text
for each pair of xApps:
  if share_parameter():
    label direct
  elif affect_same_kpi():
    label indirect
  elif chain_exists(A, P, K, A, P):
    label implicit
```

## Criterios Ja Cumpridos

O trainer atual ja cumpre:

1. ler `timeseries.csv` e construir/usar `G_T` explicitamente;
2. treinar embeddings temporais com encoder GraphSAGE;
3. gravar adjacencia reconstruida por threshold;
4. gravar rotulos `direct`, `indirect`, `implicit`;
5. produzir `training_summary.json` com metricas;
6. produzir artefatos em `runs/article00/training/seed_<seed>/`.

## Criterios De Aceite Para Sair do Modo Experimental

Mesmo com esses pontos implementados, a trilha so deve sair do modo
experimental quando:

1. o gerador sintetico estiver auditado contra o PDF
2. o protocolo experimental estiver congelado
3. os resultados multiseed forem reproduziveis
4. as figuras oficiais forem geradas no mesmo envelope experimental
5. a comparacao com a trilha GreenRAN estiver documentada

## O Que Nao Fazer

Enquanto essa trilha nao cumprir os criterios acima, nao deve:

- entrar no `rApp`
- ser usada no runtime
- substituir `train_graphsage_conflicts.py`
- ser apresentada como reproducao final do paper

## Conclusao

O desenho tecnico experimental agora esta definido.

O passo seguinte nao e mais sair da organizacao para a implementacao basica,
porque isso ja existe. O passo seguinte e homologar metodologicamente a trilha
mantendo a separacao entre:

- trilha GreenRAN atual
- trilha `article00` fiel ao paper
