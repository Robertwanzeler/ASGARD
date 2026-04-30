# Auditoria Experimental do Dataset `article00`

Este documento compara o gerador atual do dataset `article00` com o metodo
descrito no [artigo00.pdf](</home/robert/Downloads/artigo00.pdf>).

O objetivo aqui nao e homologar a reproducao final. O objetivo e deixar claro o
que ja esta alinhado, o que ainda e aproximacao e o que ainda nao foi
implementado.

Arquivos relacionados:

- [generate_article00_dataset.py](/home/robert/orange_nuclear/scripts/generate_article00_dataset.py:1)
- [train_graphsage_article00.py](/home/robert/orange_nuclear/training/train_graphsage_article00.py:1)
- [ARTICLE00_METODO.md](/home/robert/orange_nuclear/docs/ARTICLE00_METODO.md:1)

## Resumo Executivo

Hoje o gerador atual:

- ja cria uma serie temporal separada para a trilha `article00`;
- ja modela `4 xApps`, `7 parametros` e `4 KPIs`;
- ja registra arestas conhecidas `A-P` e `A-K`;
- ja exporta o grafo temporal `G_T` em arquivo separado;
- ainda nao garante fidelidade total das equacoes do paper;
- ainda precisa ser tratado como `experimental`.

## Checklist Paper vs Gerador Atual

| Item | Paper | Gerador atual | Status |
|---|---|---|---|
| Entidades | `4 xApps`, `7 parametros`, `4 KPIs` | implementado em `KNOWN_APP_PARAMETER_EDGES`, `KNOWN_APP_KPI_EDGES` e `PARAMETER_RANGES` | alinhado |
| Serie temporal | observacoes por tempo `t` | `timeseries.csv` com `time_index` e colunas `P1..P7`, `K1..K4` | alinhado |
| Grafo temporal explicito | vertices `v_t` com vizinhos `t-1`, `t`, `t+1` | exportado em `article00_temporal_graph.json` como cadeia temporal entre instantes consecutivos | parcial |
| Vetor de features `x_t` | valores temporais de `P` e `K` por instante | presente em `timeseries.csv` | alinhado |
| Equacoes dos KPIs | derivadas do paper | transcricao de primeira passada em `compute_kpis()` | parcial |
| Arestas conhecidas `A-P` | conhecidas a priori | presentes em `build_reference_graph()` | alinhado |
| Arestas conhecidas `A-K` | conhecidas a priori | presentes em `build_reference_graph()` | alinhado |
| Arestas `P-K` | deveriam emergir da modelagem/reconstrucao | hoje ja sao registradas no `graph_reference.json` como referencia sintetica | parcial |
| Parametrizacao de ruido/inercia | depende da formulacao do paper | `smoothing=0.82` por decisao experimental local | parcial |
| Evidencia de fidelidade | precisa bater com o metodo completo | ainda nao existe | faltando |

## O Que Ja Esta Alinhado

### Topologia basica

O gerador atual ja respeita a cardinalidade estrutural do paper:

- `A1..A4`
- `P1..P7`
- `K1..K4`

Isso aparece em [generate_article00_dataset.py](/home/robert/orange_nuclear/scripts/generate_article00_dataset.py:24), [generate_article00_dataset.py](/home/robert/orange_nuclear/scripts/generate_article00_dataset.py:35) e [generate_article00_dataset.py](/home/robert/orange_nuclear/scripts/generate_article00_dataset.py:42).

### Serie temporal separada

O dataset atual ja nasce como serie temporal em `timeseries.csv`, com um
`time_index` por amostra e os valores de `P` e `K` por instante. Isso esta
coerente com a necessidade de um vetor `x_t` por tempo.

### Relacoes conhecidas

O gerador ja materializa:

- arestas conhecidas `A-P`
- arestas conhecidas `A-K`

isso e util porque o paper tambem trata essas relacoes como conhecimento
estrutural conhecido.

### Grafo temporal exportado

O gerador atual tambem exporta um arquivo dedicado com o grafo temporal:

- `article00_temporal_graph.json`

Esse arquivo materializa um vertice por instante temporal e uma cadeia de
arestas entre instantes consecutivos. Isso ja coloca a trilha mais proxima da
formulacao temporal do paper, embora a homologacao metodologica ainda dependa
de validacao fina.

## O Que Ainda E Aproximacao

### Equacoes dos KPIs

As equacoes atuais em [generate_article00_dataset.py](/home/robert/orange_nuclear/scripts/generate_article00_dataset.py:82) estao marcadas no proprio codigo como:

- `First-pass transcription`
- `should be reviewed before publication use`

Isso significa que:

- a estrutura geral da ideia esta la;
- a fidelidade numerica ainda nao foi homologada contra o PDF.

### Ranges dos parametros

Os ranges definidos em [generate_article00_dataset.py](/home/robert/orange_nuclear/scripts/generate_article00_dataset.py:24) sao razoaveis para um scaffold experimental, mas ainda nao estao justificados no repositorio como sendo os mesmos ranges do paper.

### Inercia temporal

O fator `smoothing=0.82` ajuda a criar continuidade temporal, mas hoje ele e uma
decisao de simulacao local. Ainda nao esta documentado como derivado do paper.

## O Que Ainda Falta

### Homologacao do grafo temporal

O gerador ja exporta o grafo temporal `G_T`, mas ainda falta demonstrar que a
forma como ele esta sendo usado no trainer e metodologicamente equivalente ao
que o paper pretende com a vizinhanca temporal.

Hoje a trilha usa uma cadeia temporal entre instantes consecutivos. Isso e
suficiente para o experimento atual, mas ainda nao e evidencia final de
fidelidade integral ao artigo.

### Separacao entre referencia e alvo reconstruido

O `article00_graph_reference.json` hoje ja registra arestas `P-K` como
referencia sintetica. Isso e util para avaliacao, mas nao deve ser confundido
com o grafo reconstruido pelo metodo.

Na implementacao futura, vai ser importante separar:

- `ground truth synthetic graph`
- `reconstructed graph`
- `post-processed graph with known edges`

### Rotulagem de conflitos

O gerador atual nao produz ainda:

- rotulos `direct`
- rotulos `indirect`
- rotulos `implicit`

Ele so prepara a base estrutural para isso.

## Status Recomendado

O gerador atual deve continuar sendo tratado como:

- `experimental`
- `dataset scaffold`
- `not paper-validated`

## Criterios Para Homologar o Gerador

Antes de dizer que o dataset esta fiel ao paper, ainda precisa existir:

1. revisao linha por linha das equacoes contra o PDF;
2. justificativa dos ranges de `P1..P7`;
3. decisao documentada sobre o parametro de inercia temporal;
4. homologacao do uso do grafo temporal `G_T` contra o paper;
5. separacao clara entre grafo de referencia e grafo reconstruido.

## Conclusao

O gerador atual ja e suficiente para abrir a trilha `article00` como
experimento separado.

Ele ainda nao e suficiente para dizer que a reproducao do paper esta pronta.

Essa e a leitura correta hoje.
