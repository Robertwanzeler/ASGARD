# Veredito de Conformidade da Trilha `article00`

Este documento consolida o veredito atual sobre a conformidade da trilha
experimental `article00` com o [artigo00.pdf](</home/robert/Downloads/artigo00.pdf>).

Escopo desta leitura:

- estrutura da trilha;
- gerador de dataset;
- trainer temporal `GraphSAGE`;
- reconstrucao do grafo;
- rotulagem de conflitos;
- estado metodologico geral.

Arquivos-base:

- [generate_article00_dataset.py](/home/robert/orange_nuclear/scripts/generate_article00_dataset.py:1)
- [train_graphsage_article00.py](/home/robert/orange_nuclear/training/train_graphsage_article00.py:1)
- [ARTICLE00_DATASET_AUDITORIA.md](/home/robert/orange_nuclear/docs/ARTICLE00_DATASET_AUDITORIA.md:1)
- [ARTICLE00_DESENHO_TECNICO.md](/home/robert/orange_nuclear/docs/ARTICLE00_DESENHO_TECNICO.md:1)

## Veredito Executivo

A trilha `article00` hoje esta:

- `conforme` na estrutura experimental principal;
- `forte experimentalmente` na implementacao metodologica;
- `ainda nao homologada` como reproducao final fiel do paper.

Em termos praticos:

- ja da para tratar a trilha como experimento serio e separado;
- ainda nao da para afirmar que ela reproduz o artigo de forma integral.

## Tabela de Veredito

| Item | Veredito | Observacao |
|---|---|---|
| Trilha separada do GreenRAN | conforme | continua isolada do runtime e do `rApp` |
| Cardinalidade `4 xApps / 7 P / 4 KPIs` | conforme | estrutura bate com o paper |
| Serie temporal `P1..P7 + K1..K4` | conforme | `timeseries.csv` alinhado ao vetor temporal `x_t` |
| Arestas conhecidas `A-P` e `A-K` | conforme | estao materializadas no grafo de referencia |
| Exportacao de grafo temporal | conforme | existe `article00_temporal_graph.json` |
| Suporte temporal no trainer | conforme | trainer usa vizinhanca temporal sobre os instantes |
| Reconstrucao `P-K` por correlacao + threshold | forte experimentalmente | o melhor cenario atual ja obteve F1 agregado `1.0` em `450` amostras e `200` epochs |
| Rotulagem `direct / indirect / implicit` | forte experimentalmente | o melhor cenario atual ja obteve `indirect = 1.0` e `implicit = 1.0` no agregado multiseed |
| Equacoes dos KPIs | parcial | parecem alinhadas, mas ainda pedem checagem fina contra o PDF |
| Procedimento exato do paper | divergente | o trainer atual e uma aproximacao experimental, nao uma prova de identidade metodologica |
| Resultados prontos para afirmar reproducao fiel | parcial | os resultados experimentais estao muito fortes, mas a homologacao metodologica contra o texto do paper ainda nao esta fechada |

## O Que Ja Bate Bem Com o Artigo

- a trilha esta isolada da stack operacional do GreenRAN;
- o universo estrutural do problema esta correto;
- o dataset ja nasce como serie temporal;
- o trainer ja trabalha sobre um suporte temporal;
- a comparacao experimental com `Random`, `GraphSAGE-CL` e `ARMD-GreenRAN`
  ja esta organizada como trilha separada.

## O Que Bate So Parcialmente

### Reconstrucao do grafo

Hoje a trilha reconstrui relacoes `P-K` a partir da correlacao dos sinais
reconstruidos e aplica threshold.

Isso esta proximo da ideia do paper e e util para o experimento, mas ainda
precisa ser defendido como equivalencia metodologica, nao apenas como escolha
razoavel de implementacao.

### Rotulagem de conflitos

As classes:

- `direct`
- `indirect`
- `implicit`

ja existem no trainer atual, mas a homologacao final ainda depende de mostrar
que a derivacao bate exatamente com a semantica usada no artigo.

### Equacoes de KPI

As equacoes atuais parecem ser uma traducao funcional do texto-base, mas ainda
falta uma revisao numerica fina para cravar conformidade total.

## O Que Ainda Diverge

### Trainer metodologico

O trainer atual e um `TemporalGraphSAGEAutoencoder` com perda `MSE` de
reconstrucao de features.

Isso significa que:

- ele ja implementa uma trilha temporal real;
- ele ainda nao prova identidade exata com todos os detalhes do procedimento do
  artigo.

### Homologacao final

Ainda faltam:

1. checagem linha por linha das equacoes contra o PDF;
2. congelamento do protocolo experimental oficial;
3. validacao multiseed mais forte;
4. consolidacao dos resultados como reproducao metodologica, nao apenas como
   experimento inspirado no paper.

## Resultado Atual Mais Forte

O melhor envelope experimental geral da trilha `article00` e:

- `450` amostras
- `200` epochs
- thresholds `0.2, 0.5, 0.9`
- `selection_threshold = 0.2`
- `hidden_dim = 24`
- `embed_dim = 24`
- `dropout = 0.05`
- `temporal_radius = 2`
- `temporal_decay = 0.7`
- `fp_penalty_weight = 0.4`
- `tp_reward_weight = 0.2`
- `selection_mode = composite`

No agregado dos `6` seeds:

- `parameter_kpi_f1 = 1.0`
- `indirect_f1 = 1.0`
- `implicit_f1 = 1.0`

## Resultado Atual Mais Forte em `threshold = 0.5`

Para comparacao estrita com o melhor caso reportado do paper em `threshold = 0.5`,
o melhor envelope atual ficou:

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

No agregado dos `6` seeds:

- `parameter_kpi_f1 = 0.988889`
- `indirect_f1 = 1.0`
- `implicit_f1 = 1.0`

## Conclusao Final

O veredito correto hoje e:

- `sim`, a trilha `article00` esta bem alinhada com o paper em arquitetura e
  ja alcancou um cenario experimental muito forte;
- `nao`, ainda nao esta em ponto de ser declarada reproducao final
  integralmente fiel ao artigo apenas por questao de performance, e sim por
  ainda faltar homologacao metodologica fina contra o texto do paper.

Essa e a formulacao mais tecnicamente defensavel no estado atual do repositorio.
