# Auditoria de Alinhamento das Figuras DRL vs. ARMD-GreenRAN

## Resumo

As figuras do pacote `EE-DRL-GreenRAN` estao alinhadas com o `ARMD-GreenRAN` no nivel de organizacao do pacote, nomenclatura editorial e intencao de mapeamento com o artigo. Elas **nao** estao no mesmo padrao visual final do ARMD, porque ainda misturam:

- figuras arquiteturais geradas em um estilo proprio;
- figuras quantitativas curadas de `drlexp/charts`;
- resolucoes e aspect ratios diferentes;
- canvas suplementares sem padrao unico.

No ARMD, o padrao e mais rigido: ha um gerador unico com `STYLES`, `OUTPUT_NAMES` e familia fixa de saida, e os PNGs principais saem todos com a mesma resolucao (`1455x1032`).

## Base da Auditoria

- Gerador DRL: [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:3)
- Pacote DRL: [scripts/materialize_eedrl_greenran_package.py](/home/robert/orange_nuclear/scripts/materialize_eedrl_greenran_package.py:7)
- Gerador ARMD: [scripts/generate_article00_armd_comparison_figures.py](/home/robert/orange_nuclear/scripts/generate_article00_armd_comparison_figures.py:3)
- Manifesto DRL: [runs/eedrl_greenran_final/paper_figures/paper_figures_summary.json](/home/robert/orange_nuclear/runs/eedrl_greenran_final/paper_figures/paper_figures_summary.json:1)
- Manifesto ARMD: [runs/article00/comparison_figures/comparison_summary.json](/home/robert/orange_nuclear/runs/article00/comparison_figures/comparison_summary.json:1)

## Referencia ARMD

O ARMD-GreenRAN esta no padrao desejado para artigo pelos seguintes motivos:

- usa gerador unico e deterministico;
- congela as curvas de referencia em JSON fixo;
- atualiza so a serie `ARMD-GreenRAN`;
- mantem os seis PNGs principais com a mesma resolucao.

Figuras ARMD principais:

- `comparison_reconstruction_threshold_0_5.png` -> `1455x1032`
- `comparison_reconstruction_dataset_450_thresholds.png` -> `1455x1032`
- `comparison_indirect_threshold_0_5.png` -> `1455x1032`
- `comparison_indirect_dataset_450_thresholds.png` -> `1455x1032`
- `comparison_implicit_threshold_0_5.png` -> `1455x1032`
- `comparison_implicit_dataset_450_thresholds.png` -> `1455x1032`

## Classificacao das Figuras DRL

### 1. Alinhadas na estrutura, mas fora do padrao visual ARMD

Estas figuras cumprem o papel editorial e foram geradas especificamente para o pacote DRL, mas nao seguem o mesmo canvas e nao formam uma familia visual tao rigida quanto o ARMD.

- `fig01_eedrl_greenran_system_model.png` -> `2004x1215`
- `fig02_eedrl_greenran_timescales.png` -> `2004x910`
- `fig03_eedrl_greenran_components.png` -> `1953x1113`
- `fig04_eedrl_greenran_state_action.png` -> `1953x1079`

Status: `parcialmente alinhadas`

Motivo:

- sao figuras geradas para o artigo;
- mantem narrativa coerente com a adaptacao GreenRAN;
- mas variam em altura, proporcao e composicao;
- nao repetem o mesmo molde visual do conjunto ARMD.

Acao recomendada:

- padronizar canvas unico para toda a familia arquitetural;
- definir grid, margens, tipografia e legenda fixos;
- exportar no mesmo alvo de resolucao da trilha ARMD, ou em outro alvo unico igualmente congelado.

### 2. Mais proximas do padrao ARMD

Estas figuras quantitativas ja sao as mais proximas do padrao do ARMD porque usam a trilha oficial do pacote DRL e, em parte, compartilham estilo de eixo inspirado no ARMD.

- `fig08_decision_distribution.png` -> `1250x822`
- `fig09_reward_by_zone.png` -> `1231x821`

Status: `quase alinhadas`

Motivo:

- possuem dimensoes muito proximas entre si;
- representam metricas derivadas do runtime/Data Lake atual;
- podem ser regeneradas de forma mais limpa a partir de artefatos oficiais.

Acao recomendada:

- regenerar ambas num canvas unico fixo;
- travar titulo, escala, legenda e espessura de linha;
- usá-las como base do padrao quantitativo DRL.

### 3. Editorialmente corretas, mas ainda heterogeneas

Estas figuras estao corretas no pacote, mas ainda nao combinam com o nivel de padronizacao visual do ARMD.

- `fig05_sbilstm_mae_evolution.png` -> `1935x1169`
- `fig06_a3c_reward_function.png` -> `1955x1169`
- `fig07_energy_efficiency_rf_vs_a3c.png` -> `1645x1094`
- `fig10_summary_metrics.png` -> `1237x747`

Status: `alinhadas no conteudo, nao no padrao visual`

Motivo:

- o manifesto DRL as marca como `ready`;
- mas as dimensoes variam muito;
- a trilha ainda herda heterogeneidade dos charts curados;
- `fig10` destoa mais ainda do restante do bloco quantitativo.

Acao recomendada:

- refazer as quatro por um unico gerador de figuras;
- manter fonte, grid, escala, legenda e largura de figura identicos;
- evitar copiar PNG pronto de `drlexp/charts` quando o dado oficial puder ser plotado diretamente.

### 4. Suplementares fora do padrao ARMD

As figuras suplementares nao estao no mesmo padrao de familia do ARMD.

- `suppA_sbilstm_learning_curves.png` -> `1235x1598`
- `suppB_sbilstm_residual_histograms.png` -> `1260x1598`
- `suppC_article_vs_greenran_learning.png` -> `2116x1109`
- `suppD_article_vs_greenran_histograms.png` -> `2083x1109`

Status: `precisam de rework`

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
