# Protocolo Experimental da Trilha `article00`

Este documento fixa o protocolo experimental da trilha `article00` sem tirar a
trilha do modo experimental.

Ele serve para padronizar:

- seeds;
- numero de amostras;
- checkpoints;
- thresholds;
- artefatos esperados;
- subconjunto atualmente executavel.

Arquivos relacionados:

- [ARTICLE00_STATUS_EXPERIMENTAL.md](/home/robert/orange_nuclear/docs/ARTICLE00_STATUS_EXPERIMENTAL.md:1)
- [ARTICLE00_DATASET_AUDITORIA.md](/home/robert/orange_nuclear/docs/ARTICLE00_DATASET_AUDITORIA.md:1)
- [ARTICLE00_METODO.md](/home/robert/orange_nuclear/docs/ARTICLE00_METODO.md:1)
- [ARTICLE00_EXPERIMENTO_D.md](/home/robert/orange_nuclear/docs/ARTICLE00_EXPERIMENTO_D.md:1)

## Princípio

O protocolo abaixo define o **alvo experimental oficial** da trilha `article00`.

Isso nao significa que tudo ja esta implementado. Significa apenas que, quando
as pecas forem sendo implementadas, elas devem convergir para esta matriz.

## Matriz Oficial

### Seeds oficiais

- `42`
- `43`
- `44`
- `45`
- `46`

Motivo:

- alinhar com a pratica multiseed ja usada na trilha GreenRAN;
- reduzir risco de leitura baseada em uma unica inicializacao.

### Tamanhos de dataset

- `450` amostras: cenario oficial de comparacao com o paper
- `700`, `3000` e `5000` amostras: exploracao complementar historica

Motivo:

- `450` e o ponto metodologicamente mais importante para comparacao;
- os tamanhos maiores foram uteis para explorar estabilidade, mas nao sao o
  cenario final de referencia da trilha.

### Suavizacao temporal

- `0.82` como valor experimental inicial

Status:

- mantido por padrao do scaffold atual;
- ainda sujeito a revisao apos confrontar melhor o paper.

### Checkpoints de epoca

- `50`
- `100`
- `200`
- `400`
- `600`
- `800`
- `1000`

Motivo:

- alinhar com a leitura de convergencia usada na trilha GreenRAN;
- permitir curvas comparaveis com as figuras centrais.

### Thresholds oficiais

- `0.2`
- `0.5`
- `0.9`

Observacao:

- o threshold `0.5` continua sendo o default inicial do scaffold;
- a versao final deve avaliar pelo menos esses tres cortes.

Observacao do `Experimento D`:

- melhorias de metodo devem preservar esse conjunto de thresholds;
- o foco do `Experimento D` e mexer no trainer, nao no protocolo do paper.

## Artefatos Esperados

### Dataset

Por seed:

- `runs/article00/datasets/seed_<seed>/timeseries.csv`
- `runs/article00/datasets/seed_<seed>/article00_metadata.json`
- `runs/article00/datasets/seed_<seed>/article00_graph_reference.json`

### Treino

Por seed:

- `article00_training_manifest.json`
- `training_summary.json`
- `reconstructed_adjacency.json`
- `conflict_labels.json`
- `checkpoints/`

Observacao:

- o `training_summary.json` e os artefatos centrais ja existem;
- o protocolo ainda continua experimental, porque a validacao metodologica
  completa do paper ainda nao foi fechada.

### Figuras

Esperadas como conjunto oficial:

- `reconstruction_f1_vs_epochs.png`
- `reconstruction_f1_vs_threshold.png`
- `implicit_f1_vs_epochs.png`
- `implicit_f1_vs_threshold.png`
- `indirect_f1_vs_epochs.png`
- `indirect_f1_vs_threshold.png`

### Relatorios

- `aggregate_report.json`
- `article00_summary.md`
- comparadores contra a trilha GreenRAN

## Subconjunto Atualmente Executavel

Hoje, sem implementar a parte temporal completa, o subconjunto realmente
executavel e:

1. gerar dataset sintetico com `generate_article00_dataset.py`
2. treinar o temporal GraphSAGE com `train_graphsage_article00.py`
3. rodar o wrapper `run_article00_experiments.py`
4. gerar figuras e relatorios agregados

Isso produz:

- dataset;
- treino temporal isolado;
- adjacencia reconstruida;
- rotulos de conflito;
- figuras e relatorios;
- estrutura de saida;
- comparacao organizacional entre trilhas.

Nao produz ainda:

- homologacao final do metodo;
- equivalencia metodologica fechada com o paper;
- evidencia final para artigo sem ressalvas.

## Configuracoes de Referencia Atuais

### Melhor cenario geral

- `450` amostras
- `200` epochs
- `selection_threshold = 0.2`
- `hidden_dim = 24`
- `embed_dim = 24`
- `dropout = 0.05`
- `temporal_radius = 2`
- `temporal_decay = 0.7`
- `fp_penalty_weight = 0.4`
- `tp_reward_weight = 0.2`
- `selection_mode = composite`
- resultado agregado: `1.0 / 1.0 / 1.0`

### Melhor cenario estrito em `0.5`

- `450` amostras
- `200` epochs
- `selection_threshold = 0.5`
- `hidden_dim = 32`
- `embed_dim = 32`
- `dropout = 0.05`
- `temporal_radius = 3`
- `temporal_decay = 0.7`
- `fp_penalty_weight = 0.3`
- `tp_reward_weight = 0.2`
- `hard_positive_weight = 0.29`
- `hard_positive_focus_kpis = K2`
- resultado agregado: `0.988889 / 1.0 / 1.0`

## Regras de Leitura

Enquanto a trilha estiver experimental:

- manifests valem como evidencia de organizacao, nao de performance;
- datasets valem como base sintetica, nao como validacao metodologica;
- thresholds e epocas valem como protocolo-alvo, nao como experimento fechado.

## Criterio de Congelamento

Este protocolo deve ser considerado congelado quando:

1. o gerador sintetico for homologado contra o PDF;
2. o trainer temporal estiver implementado;
3. a reconstrução por correlacao e threshold existir;
4. as figuras oficiais puderem ser geradas nesse mesmo envelope.

## Conclusao

O objetivo deste documento e evitar improviso quando a trilha `article00`
avancar.

Ela continua experimental, mas agora com uma matriz oficial de reproducao.
