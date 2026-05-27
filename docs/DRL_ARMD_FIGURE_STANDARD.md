# Padrao DRL Espelhado no ARMD-GreenRAN

> ⚠️ **Nota (Maio 2026):** Este documento originalmente cobre as figuras do **legado A3C/SBiLSTM**. Com a migração para **CAORA-SAC**, novas figuras são necessárias (Seção "Figuras CAORA-SAC (Nova Geração)" abaixo). O padrão editorial definido aqui se aplica igualmente às novas figuras SAC.

## Objetivo

Este documento define o que significa, em termos praticos, colocar as figuras do `EE-DRL-GreenRAN` no **mesmo padrao** do `ARMD-GreenRAN`.

Aqui, "mesmo padrao" nao significa mesma mensagem tecnica. Significa a mesma disciplina de producao editorial:

- um gerador principal unico;
- uma familia fixa de estilos;
- uma familia fixa de layouts;
- canvas congelado por classe de figura;
- nomes de saida previsiveis;
- referencia base congelada;
- variacao apenas nos dados GreenRAN.

## O Padrao ARMD Que Deve Ser Espelhado

O ARMD hoje funciona como referencia porque a familia principal de figuras e rigidamente controlada em codigo:

- estilos centrais em [scripts/generate_article00_armd_comparison_figures.py](/home/robert/orange_nuclear/scripts/generate_article00_armd_comparison_figures.py:44)
- layouts centrais em [scripts/generate_article00_armd_comparison_figures.py](/home/robert/orange_nuclear/scripts/generate_article00_armd_comparison_figures.py:186)
- nomes de saida fixos em [scripts/generate_article00_armd_comparison_figures.py](/home/robert/orange_nuclear/scripts/generate_article00_armd_comparison_figures.py:155)
- exportacao padronizada em [scripts/generate_article00_armd_comparison_figures.py](/home/robert/orange_nuclear/scripts/generate_article00_armd_comparison_figures.py:491) e [scripts/generate_article00_armd_comparison_figures.py](/home/robert/orange_nuclear/scripts/generate_article00_armd_comparison_figures.py:552)
- referencia congelada em [runs/article00/comparison_figures/comparison_summary.json](/home/robert/orange_nuclear/runs/article00/comparison_figures/comparison_summary.json:1)

Os elementos centrais do padrao ARMD sao:

- `font.family = serif`
- `font.size = 7`
- `axes.titlesize = 8`
- `axes.labelsize = 7`
- `legend.fontsize = 5.2`
- `xtick.labelsize = 6`
- `ytick.labelsize = 6`
- `linewidth = 1.15`
- `markersize = 4.0`
- `grid = "--"` com `linewidth = 0.45` e `alpha = 0.28`
- `spine linewidth = 0.8`
- legenda inferior centralizada
- `dpi = 260`
- `figsize` principal unico: `(5.8, 4.6)`

Esse molde aparece diretamente no plotter do ARMD em [scripts/generate_article00_armd_comparison_figures.py](/home/robert/orange_nuclear/scripts/generate_article00_armd_comparison_figures.py:491).

## O Que o DRL Deve Herdar Exatamente

Para dizer que o DRL esta no mesmo padrao do ARMD, a trilha DRL deve obedecer estas regras:

### 1. Um gerador quantitativo principal

As figuras `fig05` a `fig10` precisam sair de um **unico gerador quantitativo**, sem copiar PNG pronto de `drlexp/charts`.

Motivo:

- o ARMD nao materializa familia principal por copia de PNG heterogeneo;
- ele gera a familia em codigo, com layout controlado.

Hoje o desvio esta em [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:55) e no pacote consolidado em [scripts/materialize_eedrl_greenran_package.py](/home/robert/orange_nuclear/scripts/materialize_eedrl_greenran_package.py:23).

### 2. Um dicionario central de estilo

O DRL deve ter um bloco equivalente a `STYLES` do ARMD para:

- cores oficiais do baseline e do GreenRAN;
- tipos de linha;
- marcadores;
- `markerfacecolor`;
- largura de linha;
- largura de borda do marcador.

Hoje o DRL espalha isso em varias funcoes:

- [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:381)
- [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:414)
- [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:434)
- [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:457)
- [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:491)
- [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:523)

### 3. Um dicionario central de layout

O DRL deve ter um bloco equivalente a `LAYOUTS` do ARMD para todas as figuras principais:

- `fig05` a `fig10`: `figsize = (5.8, 4.6)`
- margem inferior, esquerda, direita e superior congeladas
- posicao da legenda congelada
- rotacao de ticks congelada quando necessario

No ARMD isso esta centralizado em [scripts/generate_article00_armd_comparison_figures.py](/home/robert/orange_nuclear/scripts/generate_article00_armd_comparison_figures.py:186).

No DRL, hoje as figuras principais usam tamanhos diferentes:

- `fig05`: `(10.5, 5.8)`
- `fig06`: `(10.5, 5.8)`
- `fig07`: `(8.8, 5.6)`
- `fig08`: `(5.8, 4.6)`
- `fig09`: `(5.8, 4.6)`
- `fig10`: `(5.8, 4.6)`

Isso aparece em [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:381), [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:414), [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:434), [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:457), [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:491) e [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:523).

### 4. Exportacao padronizada

O DRL deve usar a mesma disciplina de exportacao do ARMD:

- `dpi = 260`
- `bbox_inches = "tight"`
- familia principal com resolucao final consistente

Hoje o DRL salva com `dpi = 220` em [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:211).

### 5. Referencia oficial congelada

No ARMD, as curvas-base do artigo ficam congeladas e o GreenRAN muda por cima. O DRL deve fazer o equivalente:

- historico oficial `SBiLSTM` congelado;
- historico oficial `A3C` congelado;
- metricas oficiais congeladas;
- dados do runtime GreenRAN derivados de artefato oficial, nao de PNG legado.

Os pontos de entrada oficiais ja existem:

- `sbilstm_training_history.json`
- `a3c_training_history.json`
- `evaluation_metrics.json`

Eles sao usados em [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:39), [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:42), [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:46) e [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:49), mas a familia ainda nao esta toda subordinada a esse principio.

## Padrao Recomendado por Classe de Figura

### Figuras Principais Quantitativas

Escopo:

- `fig05_sbilstm_mae_evolution.png`
- `fig06_a3c_reward_function.png`
- `fig07_energy_efficiency_rf_vs_a3c.png`
- `fig08_decision_distribution.png`
- `fig09_reward_by_zone.png`
- `fig10_summary_metrics.png`

Padrao:

- `figsize = (5.8, 4.6)`
- `dpi = 260`
- fonte serif com o mesmo conjunto tipografico do ARMD
- titulo em `8`
- labels em `7`
- ticks em `6`
- legenda inferior centralizada quando houver duas series comparativas
- `grid` identico ao ARMD
- `spines` com `0.8`

Observacao:

- `fig08` e `fig09` ja estao mais proximas desse molde;
- `fig05`, `fig06` e `fig07` precisam ser replotadas no mesmo canvas do bloco principal;
- `fig10` precisa manter o mesmo canvas e a mesma margem, mesmo com rotacao de rótulos.

### Figuras Arquiteturais

Escopo:

- `fig01_eedrl_greenran_system_model.png`
- `fig02_eedrl_greenran_timescales.png`
- `fig03_eedrl_greenran_components.png`
- `fig04_eedrl_greenran_state_action.png`

Padrao:

- usar uma familia unica de canvas horizontal, nao quatro dimensoes diferentes;
- mesma tipografia em todos os diagramas;
- mesma espessura de caixa, borda e seta;
- mesma hierarquia de cores por camada funcional;
- mesma regra de titulo.

Padrao recomendado:

- uma unica dimensao horizontal para os quatro diagramas;
- exportacao tambem em `dpi = 260`;
- titulo com mesmo peso e mesmo tamanho;
- caixas e setas parametrizadas por constantes.

Observacao:

- o ARMD nao tem este bloco arquitetural, entao aqui o espelhamento deve ser de **disciplina editorial**, nao de formato identico ao chart de comparacao.

### Figuras Suplementares

Escopo:

- `suppA_sbilstm_learning_curves.png`
- `suppB_sbilstm_residual_histograms.png`
- `suppC_article_vs_greenran_learning.png`
- `suppD_article_vs_greenran_histograms.png`

Padrao:

- duas familias fixas apenas:
- vertical comparativa;
- horizontal lado a lado.

Padrao recomendado:

- `suppA` e `suppB` com o mesmo `figsize`
- `suppC` e `suppD` com o mesmo `figsize`
- mesma tipografia, mesmas margens e mesmo `dpi`

Hoje isso ja quase existe no codigo, mas ainda nao esta declarado como contrato editorial.

## Scripts Que Precisam Mudar

### 1. [scripts/generate_eedrl_greenran_paper_figures.py](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:1)

Este e o arquivo principal a ajustar.

Mudancas necessarias:

- criar `DRL_STYLES`, equivalente ao `STYLES` do ARMD;
- criar `DRL_LAYOUTS`, equivalente ao `LAYOUTS` do ARMD;
- trocar `save_fig()` para `dpi = 260`;
- fazer `fig05`, `fig06` e `fig07` usarem o mesmo layout base de `fig08` e `fig09`;
- declarar layouts fixos para arquitetura;
- declarar layouts fixos para suplementares;
- impedir que a familia principal dependa visualmente de PNG legado.

Trechos afetados:

- exportacao: [linha 211](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:211)
- estilo base: [linha 217](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:217)
- geracao de `fig05` a `fig10`: [linhas 381-529](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:381)
- suplementares: [linhas 620-701](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:620)
- comparativos suplementares: [linha 701](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:701)
- arquitetura: [linhas 720-840](/home/robert/orange_nuclear/scripts/generate_eedrl_greenran_paper_figures.py:720)

### 2. [scripts/materialize_eedrl_greenran_package.py](/home/robert/orange_nuclear/scripts/materialize_eedrl_greenran_package.py:1)

Este script nao deve ser a fonte visual da familia principal.

Mudancas necessarias:

- manter este script apenas como materializador de pacote;
- tratar `runs/eedrl_greenran_final/figures/` como acervo tecnico;
- nao comunicar os PNGs copiados como equivalente editorial das figuras principais do article package.

Trecho afetado:

- origem das figuras copiadas: [linhas 23-33](/home/robert/orange_nuclear/scripts/materialize_eedrl_greenran_package.py:23)

### 3. [docs/DRL_FIGURE_ALIGNMENT_AUDIT.md](/home/robert/orange_nuclear/docs/DRL_FIGURE_ALIGNMENT_AUDIT.md:1)

Este arquivo ja faz o diagnostico. Ele deve continuar como laudo e apontar para esta especificacao como contrato de correcao.

## Regra Simples de Aceite

O DRL so deve ser declarado "no mesmo padrao do ARMD-GreenRAN" quando:

1. `fig05` a `fig10` forem geradas por um unico molde visual.
2. Todas as figuras principais quantitativas usarem o mesmo `figsize`.
3. A exportacao principal usar o mesmo `dpi` do ARMD.
4. A familia principal nao depender de PNG heterogeneo copiado como fonte visual final.
5. As suplementares tiverem no maximo duas familias fixas de layout.
6. As arquiteturais tiverem um canvas horizontal congelado por contrato.

## Conclusao

O ARMD nao e apenas "bonito"; ele e padronizado porque o padrao esta codificado. Para o DRL chegar no mesmo nivel, o passo correto nao e retocar PNG por PNG. O passo correto e transformar o gerador DRL em uma trilha com:

- constantes centrais;
- layouts centrais;
- exportacao central;
- dados oficiais como unica fonte canonica.

---

## Figuras CAORA-SAC (Nova Geração)

### Escopo

Com a migração A3C → SAC, novas figuras editoriais são necessárias para documentar a arquitetura e resultados do **CAORA-SAC/AWAC**.

### Figuras Propostas

| ID | Nome | Conteúdo | Tipo |
|----|------|----------|------|
| `fig11_caora_architecture` | Arquitetura CAORA | Diagrama estado [d_ran,d_ai,r_ran,r_ai] → ação [δ_r_ran,δ_r_ai] → budget r_max | Arquitetural |
| `fig12_sac_reward_convergence` | Convergência SAC | Reward ao longo dos timesteps de treino offline | Quantitativa |
| `fig13_awac_vs_sac_comparison` | SAC vs AWAC | Comparativo de convergência e estabilidade entre algoritmos | Quantitativa |
| `fig14_resource_allocation_heatmap` | Alocação recursos | Heatmap d_ran vs d_ai → alocação ótima r_ran/r_ai | Quantitativa |
| `fig15_a3c_vs_sac_benchmark` | A3C vs SAC | Comparativo final: energy on/off vs resource sharing | Quantitativa |

### Padrão Recomendado

Todas as figuras SAC devem seguir o **mesmo padrão editorial** definido neste documento:

- **Quantitativas** (`fig12`–`fig15`): `figsize = (5.8, 4.6)`, `dpi = 260`, fonte serif, grid `--`, legenda inferior centralizada
- **Arquitetural** (`fig11`): mesmo canvas horizontal congelado das figuras `fig01`–`fig04`
- **Dados oficiais**: usar checkpoints `runs/sac_bootstrap/offline_sac/` e `offline_awac/` como fonte canônica
- **Exportação**: `dpi = 260`, `bbox_inches = "tight"`

### Gerador

As figuras SAC devem sair de um **único gerador quantitativo**, análogo ao `generate_eedrl_greenran_paper_figures.py` do A3C:

```bash
python scripts/generate_caora_sac_figures.py \
    --checkpoint-dir runs/sac_bootstrap/ \
    --output-dir runs/caora_figures/
```

### Regras de Aceite (Complementares)

Além das 6 regras da seção "Regra Simples de Aceite", o SAC deve atender:

7. `fig11` a `fig15` geradas por um único molde visual.
8. Algoritmo SAC e AWAC plotados com séries distintas (cores/linhas oficiais).
9. Comparativo A3C vs SAC (`fig15`) usando dados congelados de ambos os checkpoints.
