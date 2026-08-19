# Trilha `article00`: Metodo Alvo

Este documento resume o metodo que a trilha `article00` deve reproduzir dentro
do repositorio.

## Referencia

- [`/home/robert/Downloads/artigo00.pdf`](</home/robert/Downloads/artigo00.pdf>)

Titulo do paper:

- `Learning and Reconstructing Conflicts in O-RAN: A Graph Neural Network Approach`

## Elementos Centrais do Metodo

### 1. Grafo heterogeneo de conflito

O problema e descrito sobre tres tipos de entidades:

- `A`: xApps/plugins;
- `P`: parametros de controle;
- `K`: KPIs.

O objetivo e reconstruir a adjacencia do grafo de conflito, especialmente as
relacoes menos obvias entre `P` e `K`.

### 2. Grafo temporal

O paper nao treina sobre coocorrencia estatica de eventos. Ele define um grafo
temporal:

- cada vertice representa um passo de tempo `v_t`;
- cada vertice carrega um vetor de features `x_t`;
- a dependencia temporal e local: `v_(t-1)`, `v_t`, `v_(t+1)`.

### 3. Features temporais

O vetor `x_t` representa os valores observados de:

- parametros `P`;
- KPIs `K`.

Ou seja, a trilha `article00` precisa manter a serie temporal dos valores, e
nao apenas agregados por no ou por dataset.

### 4. Encoder GraphSAGE

O GraphSAGE agrega informacao da vizinhanca temporal e atualiza os embeddings de
cada vertice temporal.

O resultado final do treino deve produzir embeddings que preservem:

- dependencias temporais;
- dependencias entre parametros e KPIs;
- estrutura latente necessaria para reconstruir o grafo.

### 5. Reconstrucao da adjacencia

Depois do treino:

- os embeddings finais sao comparados entre si;
- a correlacao entre features/embeddings gera uma matriz de similaridade;
- um threshold binariza essa matriz;
- a adjacencia reconstruida e completada com as arestas conhecidas `A-P` e
  `A-K`.

### 6. Rotulagem dos conflitos

As definicoes centrais do paper sao:

- `direct`: dois xApps atuam sobre o mesmo parametro;
- `indirect`: parametros distintos afetam o mesmo KPI;
- `implicit`: cadeia logica `A -> P -> K -> A -> P`.

Na trilha `article00`, a rotulagem ideal deve acontecer sobre o grafo
reconstruido, e nao apenas sobre um grafo exportado ja anotado.

## O Que a Trilha Atual Faz

A trilha atual do GraphSAGE no repositorio:

- usa um grafo de suporte por coocorrencia de linhas;
- usa feature engineering agregado por no;
- usa decoder supervisionado para link prediction.

Isso e util para o GreenRAN, mas nao reproduz a formulacao temporal do paper.

## Meta de Convergencia

Para dizer que a trilha `article00` esta fiel ao metodo, o repositorio deve
chegar a este ponto:

1. dataset temporal sintetico controlado;
2. grafo temporal explicito;
3. trainer dedicado do GraphSAGE temporal;
4. reconstrucao por correlacao + threshold;
5. pos-processamento com arestas conhecidas;
6. rotulagem de conflitos em cima do grafo reconstruido.
