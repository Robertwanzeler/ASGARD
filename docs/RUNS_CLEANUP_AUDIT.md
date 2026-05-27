# Auditoria de Limpeza de `runs/`

## Resumo

A pasta `runs/` esta misturando quatro tipos diferentes de artefato:

- pacotes finais de artigo;
- resultados canonicos de GraphSAGE/ARMD e DRL;
- execucoes historicas do runtime GreenRAN;
- snapshots pesados de rodadas de experimento.

O maior desperdicio de espaco hoje **nao** esta nos pacotes finais de artigo. Ele esta em:

- `runs/experimentos_conflitos/` -> `12G`
- dezenas de `runs/<timestamp>_greenran_v2` -> `90M` a `300M` cada
- `state_snapshot/` dentro dessas execucoes
- `snapshots_before/`, `snapshots_after/` e `snapshots_after_control/` dentro das rodadas dos conflitos

## O Que Manter

Estas pastas ainda fazem sentido como referencia principal ou pacote final:

- [runs/article00](/home/robert/orange_nuclear/runs/article00:1)
  - pacote editorial principal do ARMD
  - contem `comparison_figures/`, `figures/` e `reports/`
- [runs/graphsage_article00_protocol](/home/robert/orange_nuclear/runs/graphsage_article00_protocol:1)
  - parece ser o resultado canonico principal do protocolo GraphSAGE
  - tem `protocol_manifest.json`, `protocol_summary.*` e os thresholds completos `0.2`, `0.5`, `0.9`
- [runs/graphsage_article00_hybrid_final](/home/robert/orange_nuclear/runs/graphsage_article00_hybrid_final:1)
  - pelo nome e pelos sumarios, parece ser consolidacao final da trilha hibrida
- [runs/graphsage_article00_calibration](/home/robert/orange_nuclear/runs/graphsage_article00_calibration:1)
- [runs/graphsage_article00_calibration_family](/home/robert/orange_nuclear/runs/graphsage_article00_calibration_family:1)
- [runs/graphsage_article00_calibration_vehicle_recovery](/home/robert/orange_nuclear/runs/graphsage_article00_calibration_vehicle_recovery:1)
  - pequenos e potencialmente uteis para explicar tuning/calibracao do ARMD
- [runs/vehicle_graphsage](/home/robert/orange_nuclear/runs/vehicle_graphsage:1)
  - trilha veicular separada, ainda util se App3 continuar no escopo
- [runs/eedrl_greenran_final](/home/robert/orange_nuclear/runs/eedrl_greenran_final:1)
  - pacote final da trilha DRL
- [runs/existing_data](/home/robert/orange_nuclear/runs/existing_data:1)
  - datasets/grafos materializados de base

## O Que Arquivar, Nao Manter no Dia a Dia

Estas pastas ainda podem ter valor historico, mas nao precisam ficar no caminho principal do repositório ou da rotina local:

- todas as execucoes `runs/<timestamp>_greenran_v2`
- todas as execucoes `runs/<timestamp>_greenran_carla_ns3`
- [runs/experimentos_conflitos](/home/robert/orange_nuclear/runs/experimentos_conflitos:1)
- [runs/experimentos_conflitos_app12_clean](/home/robert/orange_nuclear/runs/experimentos_conflitos_app12_clean:1)
- [runs/experimentos_conflitos_app1_throughput_clean](/home/robert/orange_nuclear/runs/experimentos_conflitos_app1_throughput_clean:1)
- [runs/experimentos_conflitos_vehicle_clean](/home/robert/orange_nuclear/runs/experimentos_conflitos_vehicle_clean:1)
- [runs/experimentos_conflitos_vehicle_recovery_clean](/home/robert/orange_nuclear/runs/experimentos_conflitos_vehicle_recovery_clean:1)

Motivo:

- sao resultados de execucao e coleta;
- ocupam muito espaco;
- boa parte do valor esta nos manifestos, sumarios e datasets agregados, nao nos snapshots brutos completos.

## O Que Parece Duplicado ou de Baixo Valor

### Variantes editoriais duplicadas do ARMD

Dentro de [runs/article00](/home/robert/orange_nuclear/runs/article00:1), estas subpastas parecem variantes concorrentes de um mesmo conjunto de comparacoes:

- `comparison_figures/`
- `comparison_figures_chosen_per_block/`
- `comparison_figures_chosen_per_block_v2/`
- `comparison_figures_multiseed_mean/`
- `comparison_figures_seed43_unified/`

Recomendacao:

- manter **uma** versao como canonica;
- arquivar ou remover as outras, se o artigo final ja escolheu uma referencia.

### Reruns e subconjuntos secundarios do GraphSAGE

Estas pastas parecem subconjuntos ou reruns secundarios da trilha principal `graphsage_article00_protocol`:

- [runs/graphsage_article00_protocol_last_two](/home/robert/orange_nuclear/runs/graphsage_article00_protocol_last_two:1)
- [runs/graphsage_article00_protocol_clean_remaining](/home/robert/orange_nuclear/runs/graphsage_article00_protocol_clean_remaining:1)
- [runs/graphsage_article00_protocol_vehicle_clean](/home/robert/orange_nuclear/runs/graphsage_article00_protocol_vehicle_clean:1)
- [runs/graphsage_article00_protocol_app12_clean](/home/robert/orange_nuclear/runs/graphsage_article00_protocol_app12_clean:1)

Recomendacao:

- manter so se estiverem citadas no texto final, em notebook de analise, ou em script de reproducao;
- caso contrario, sao boas candidatas a arquivar fora do caminho principal.

### Smoke test sem valor final

- [runs/graphsage_article00_protocol_vehicle_clean_smoke](/home/robert/orange_nuclear/runs/graphsage_article00_protocol_vehicle_clean_smoke:1)

Motivo:

- pelo nome e pela arvore, e um smoke run parcial;
- nao parece pacote final nem sweep completo.

Recomendacao:

- candidata forte a descarte.

## O Que Nao Presta Para Nada no Estado Atual

Estas classes de artefato sao as mais fracas em valor duradouro:

### 1. `state_snapshot/` das execucoes do runtime

Exemplo tipico: `runs/<timestamp>_greenran_v2/state_snapshot`

So esse bloco pesa `142M`, enquanto:

- `logs/` pesa `4K`
- `meta/` pesa `16K`

Ou seja: quase todo o tamanho da execucao esta no snapshot bruto.

Conteudo tipico:

- `rapp_data_lake.db`
- `ns3.log`
- `extended_metrics.json`
- varios logs de app/xApp
- `vehicles.json`, `video_analyses.json`, `uploaded_videos.json`

Recomendacao:

- se voce quiser manter a execucao como registro, preserve apenas `meta/` e talvez 1 ou 2 arquivos-chave;
- `state_snapshot/` inteiro e candidato forte a descarte para execucoes antigas.

### 2. `snapshots_before/`, `snapshots_after/`, `snapshots_after_control/` de cada rodada

Exemplo: [runs/experimentos_conflitos/20260511_150501_conflict_protocol/app1_throughput/rounds](/home/robert/orange_nuclear/runs/experimentos_conflitos/20260511_150501_conflict_protocol/app1_throughput/rounds:1)

Achado:

- ha `round_01` ate `round_33`, quase todas com `130M` cada
- cada round repete:
  - `conflict_dataset.csv`
  - `conflict_graph.json`
  - `conflict_report.json`
  - `round_summary.json`
  - `round_window.json`
  - `snapshots_before/`
  - `snapshots_after/`
  - `snapshots_after_control/`

Recomendacao:

- manter `round_summary.json`, `conflict_report.json` e o manifesto geral;
- descartar os `snapshots_*` de rodadas antigas que ja viraram sumario agregado;
- se preciso, preservar apenas 1 round exemplar por cenario.

### 3. Pastas minimas de tentativa ou placeholder

Exemplos:

- varias execucoes de `28K`
- `runs/graphsage_article00_protocol_app12_clean` com `76K`

Recomendacao:

- revisar rapido e descartar se nao forem alvo de script algum;
- em geral, isso e lixo de tentativa, nao resultado cientifico.

## Priorizacao de Limpeza

### Prioridade 1

Maior ganho de espaco com menor risco:

- remover `state_snapshot/` das execucoes antigas `greenran_v2` e `greenran_carla_ns3`
- remover `snapshots_before/`, `snapshots_after/`, `snapshots_after_control/` dentro de `runs/experimentos_conflitos/`

### Prioridade 2

Reduzir duplicacao editorial e reruns:

- escolher uma unica pasta canonica em `runs/article00/comparison_figures*`
- arquivar `graphsage_article00_protocol_last_two`
- arquivar `graphsage_article00_protocol_clean_remaining`
- arquivar `graphsage_article00_protocol_vehicle_clean`
- remover `graphsage_article00_protocol_vehicle_clean_smoke`

### Prioridade 3

Historico bruto que pode ir para cold storage:

- mover para fora do repositorio as execucoes timestampadas antigas
- mover para fora do repositorio os experimentos de conflito antigos muito grandes

## Recomendacao Final

Se o objetivo e preservar o que ainda vale para o artigo e para reproducao:

### Manter no repositorio local principal

- `runs/article00`
- `runs/graphsage_article00_protocol`
- `runs/graphsage_article00_hybrid_final`
- `runs/graphsage_article00_calibration*`
- `runs/vehicle_graphsage`
- `runs/eedrl_greenran_final`
- `runs/existing_data`

### Arquivar fora do fluxo principal

- `runs/<timestamp>_greenran_*`
- `runs/experimentos_conflitos*`
- `runs/graphsage_article00_protocol_last_two`
- `runs/graphsage_article00_protocol_clean_remaining`
- `runs/graphsage_article00_protocol_vehicle_clean`

### Candidatas fortes a descarte

- `runs/graphsage_article00_protocol_vehicle_clean_smoke`
- `state_snapshot/` de execucoes antigas
- `snapshots_*` de rounds antigos
- variantes duplicadas de `comparison_figures*` em `runs/article00`, depois que uma unica versao for escolhida como canonica

## Limpeza Executada em 2026-05-21

Foi executada uma limpeza conservadora, sem remover manifests, reports, datasets ou pacotes finais de artigo.

Removido:

- `117` diretorios `state_snapshot/`
- `1003` diretorios `snapshots_before/`, `snapshots_after/` e `snapshots_after_control/`
- [runs/graphsage_article00_protocol_vehicle_clean_smoke](/home/robert/orange_nuclear/runs/graphsage_article00_protocol_vehicle_clean_smoke:1)

Impacto:

- `runs/` antes: `24G`
- `runs/` depois: `562M`

Mantido intencionalmente:

- `scenario_report.json`
- `protocol_summary.*`
- `protocol_manifest.json`
- `comparison_figures*`
- `runs/article00`
- `runs/graphsage_article00_protocol`
- `runs/graphsage_article00_hybrid_final`
- `runs/graphsage_article00_calibration*`
- `runs/vehicle_graphsage`
- `runs/eedrl_greenran_final`

Limpeza adicional executada:

- removidas as variantes duplicadas:
  - `runs/article00/comparison_figures_chosen_per_block`
  - `runs/article00/comparison_figures_chosen_per_block_v2`
  - `runs/article00/comparison_figures_multiseed_mean`
  - `runs/article00/comparison_figures_seed43_unified`

Consolidacao adicional executada:

- os cenarios melhorados de:
  - `runs/graphsage_article00_protocol_last_two`
  - `runs/graphsage_article00_protocol_clean_remaining`
  - `runs/graphsage_article00_protocol_vehicle_clean`
- foram promovidos para [runs/graphsage_article00_protocol](/home/robert/orange_nuclear/runs/graphsage_article00_protocol:1)
- o sumario canonico `protocol_summary.json` foi regenerado
- o pacote [runs/graphsage_article00_hybrid_final](/home/robert/orange_nuclear/runs/graphsage_article00_hybrid_final:1) foi regenerado usando apenas:
  - `protocol`
  - `calibration_v1`
  - `calibration_vehicle_recovery`
  - `family_v1`

Removido apos consolidacao:

- `runs/graphsage_article00_protocol_last_two`
- `runs/graphsage_article00_protocol_clean_remaining`
- `runs/graphsage_article00_protocol_vehicle_clean`

Estado apos essa etapa:

- `runs/` caiu de `562M` para `423M`
