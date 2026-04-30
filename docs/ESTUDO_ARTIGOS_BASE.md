# Estudo dos Artigos Base

Este documento organiza a relacao entre os artigos base e as trilhas atuais do
repositorio.

## Article00

Referencia local:

- [`/home/robert/Downloads/artigo00.pdf`](</home/robert/Downloads/artigo00.pdf>)

Tema:

- aprendizado e reconstrucao de conflitos em O-RAN;
- uso de `GraphSAGE`;
- foco em relacoes ocultas entre `xApps`, parametros e KPIs.

Papel no projeto:

- base metodologica para a trilha de conflito aprendido;
- nao e o runtime operacional do GreenRAN;
- serve como trilha de pesquisa e validacao.

## Trilha Atual do GreenRAN

A trilha atual do projeto esta mais proxima de:

- exportacao de datasets de conflitos observados;
- reconstrucao de grafo sobre artefatos do GreenRAN;
- avaliacao empirica com cenarios `conflito_implicito` e `recuperacao`.

Isso aproxima o projeto do paper em intencao, mas nao em formulacao exata.

## Divergencia Principal

O paper:

- usa grafo temporal por passo de tempo;
- usa features temporais de parametros e KPIs;
- reconstrui a adjacencia a partir de embeddings e correlacao.

O repositorio hoje:

- usa dataset de conflitos exportado;
- usa coocorrencia de linhas para construir suporte;
- usa feature engineering agregado por no;
- usa um decoder supervisionado de link prediction.

## Leitura Correta do Estado Atual

Portanto:

- o `GraphSAGE` atual nao esta ausente;
- tambem nao esta "fiel ao article00";
- ele e uma adaptacao GreenRAN da ideia do paper.

## Direcao Recomendada

Manter duas trilhas:

- `GreenRAN experimental`
  - preserva o que ja funciona hoje.
- `article00 faithful`
  - reproduz o metodo do paper de forma isolada.

Esse desenho evita regressao e permite comparacao honesta entre:

- a adaptacao atual do projeto;
- a reproducao metodologica do paper.
