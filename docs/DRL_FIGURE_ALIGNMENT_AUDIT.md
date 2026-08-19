# Auditoria de Alinhamento das Figuras DRL vs. ARMD-GreenRAN

## Arquivado

Este documento cobria o pacote legado `EE-DRL-GreenRAN`. Essa linha foi removida
da pilha oficial de DRL do projeto para manter apenas a familia:

- `Task-Specific Sharpness-Aware O-RAN Resource Management Using MARL`
- cenario GreenRAN atual preservado
- cenario do artigo
- cenario-base de referencia do artigo

As decisoes oficiais da trilha DRL agora estao concentradas em:

- [docs/RELATORIO_DRL_GREENRAN_IMPLEMENTACAO.md](/home/robert/orange_nuclear/docs/RELATORIO_DRL_GREENRAN_IMPLEMENTACAO.md:1)
- [config/tasam_drl_tracks.json](/home/robert/orange_nuclear/config/tasam_drl_tracks.json:1)

O `ARMD-GreenRAN` permanece fora desta consolidacao e nao foi alterado.

Motivo:

- ha dois formatos verticais e dois horizontais;
- elas nao repetem o mesmo canvas;
- a familia suplementar nao esta congelada em um template visual unico.

Acao recomendada:

- separar um template unico para suplementares verticais;
- separar outro, se necessario, para comparativos horizontais;
- evitar misturar formatos no mesmo bloco editorial.

### 5. Nao usar como referencia final de artigo

As figuras em `runs/eedrl_greenran_final/figures/` sao uteis como consolidacao de artefatos, mas **nao** devem ser tratadas como familia final de artigo no mesmo nivel do ARMD.

- `decision_distribution.png` -> `2100x900`
- `energia_comparacao.png` -> `1467x887`
- `energy_efficiency.png` -> `1500x900`
- `mae_evolucao.png` -> `1786x887`
- `matriz_conflitos.png` -> `2045x934`
- `reward_by_zone.png` -> `1500x900`
- `reward_comparison.png` -> `1500x900`
- `reward_function.png` -> `1787x1537`
- `summary_metrics.png` -> `2100x1500`

Status: `artefatos de consolidacao, nao padrao final`

Motivo:

- o proprio materializador do pacote copia esses PNGs diretamente de `drlexp/charts`;
- ha grande variacao de resolucao;
- o conjunto nao foi produzido por um gerador editorial unico.

## Julgamento Final por Grupo

- `Arquitetura`: pronta para artigo, mas precisa padronizacao visual se o objetivo for igualar o ARMD.
- `Quantitativas Fig. 8-9`: sao as mais proximas do padrao desejado.
- `Quantitativas Fig. 5-7 e 10`: corretas no conteudo, mas ainda heterogeneas.
- `Suplementares`: precisam de rework claro.
- `figures/` consolidadas: nao usar como referencia final do paper.

## Prioridade de Rework

1. Padronizar `fig05` a `fig10` num unico gerador quantitativo.
2. Congelar um canvas unico para `fig01` a `fig04`.
3. Regerar `suppA` a `suppD` com templates fixos por classe de figura.
4. Manter `runs/eedrl_greenran_final/figures/` apenas como acervo tecnico, nao como saida editorial final.

## Conclusao

Se o criterio for "mesmo padrao de pacote e narrativa do ARMD-GreenRAN", o DRL esta de acordo.

Se o criterio for "mesmo padrao visual/editorial das imagens do ARMD-GreenRAN", o DRL ainda nao esta fechado. Hoje ele esta **coerente como pacote**, mas **heterogeneo como familia de figuras**.

O contrato de correcao desse desvio esta em [DRL_ARMD_FIGURE_STANDARD.md](/home/robert/orange_nuclear/docs/DRL_ARMD_FIGURE_STANDARD.md:1).
