# Auditoria: GraphSAGE GreenRAN vs `article00`

Este documento separa a trilha atual de `GraphSAGE` do GreenRAN da trilha
`article00`, para evitar mistura entre resultados ja obtidos e a reproducao
metodologica ainda em construcao.

## Escopo

Referencia metodologica local:

- [`/home/robert/Downloads/artigo00.pdf`](</home/robert/Downloads/artigo00.pdf>)

Documentos relacionados no repositorio:

- [ANALISE_CONFLITOS_GNN.md](/home/robert/orange_nuclear/docs/ANALISE_CONFLITOS_GNN.md:1)
- [ARTICLE00_METODO.md](/home/robert/orange_nuclear/docs/ARTICLE00_METODO.md:1)
- [ARTICLE00_EXPERIMENTOS.md](/home/robert/orange_nuclear/docs/ARTICLE00_EXPERIMENTOS.md:1)
- [ARTICLE00_STATUS_EXPERIMENTAL.md](/home/robert/orange_nuclear/docs/ARTICLE00_STATUS_EXPERIMENTAL.md:1)

## Separacao Das Trilhas

### Trilha GreenRAN atual

Esta e a trilha que ja possui treino, artefatos e graficos finais:

- trainer: [train_graphsage_conflicts.py](/home/robert/orange_nuclear/training/train_graphsage_conflicts.py:1)
- doc principal: [ANALISE_CONFLITOS_GNN.md](/home/robert/orange_nuclear/docs/ANALISE_CONFLITOS_GNN.md:1)
- diretorio-base: [experimento_principal](/home/robert/orange_nuclear/runs/experimentos_conflitos/experimento_principal)

Caracteristicas:

- usa grafo de suporte por coocorrencia de linhas;
- usa features agregadas por no;
- usa decoder supervisionado para reconstrucao de arestas;
- esta alinhada ao experimento GreenRAN, nao a uma reproducao fiel do paper.

### Trilha `article00`

Esta e a trilha paralela criada para reproducao metodologica do paper:

- gerador sintetico: [generate_article00_dataset.py](/home/robert/orange_nuclear/scripts/generate_article00_dataset.py:1)
- trainer temporal experimental: [train_graphsage_article00.py](/home/robert/orange_nuclear/training/train_graphsage_article00.py:1)
- runner experimental: [run_article00_experiments.py](/home/robert/orange_nuclear/scripts/run_article00_experiments.py:1)
- gerador de figuras experimental: [generate_graphsage_article00_figures.py](/home/robert/orange_nuclear/scripts/generate_graphsage_article00_figures.py:1)

Caracteristicas:

- dataset sintetico temporal separado em `runs/article00/`;
- treino temporal, reconstrucao e rotulagem em ambiente isolado;
- figuras e relatorios agregados por seed;
- status assumido de `experimental_temporal_graphsage`;
- sem integracao ao runtime e sem alegacao de fidelidade completa ao paper.

## Estado Atual Da Trilha GreenRAN

### O que ja esta forte

Os artefatos da trilha GreenRAN ja sustentam uma analise experimental propria.

Casos principais no agregado:

- `conflito_implicito/subset_150`: F1 `1.0`, melhor epoca `600`, em [training_summary.json](/home/robert/orange_nuclear/runs/experimentos_conflitos/experimento_principal/conflito_implicito/subset_150/training_summary.json:1)
- `conflito_implicito/subset_450`: F1 `1.0`, melhor epoca `600`, em [training_summary.json](/home/robert/orange_nuclear/runs/experimentos_conflitos/experimento_principal/conflito_implicito/subset_450/training_summary.json:1)
- `recuperacao/subset_150`: F1 `1.0`, melhor epoca `600`, em [training_summary.json](/home/robert/orange_nuclear/runs/experimentos_conflitos/experimento_principal/recuperacao/subset_150/training_summary.json:1)

Esses tres resultados sao fortes como evidencia de que a trilha atual aprende
estrutura de conflito nos datasets exportados do GreenRAN.

### Onde ha fragilidade

O caso `recuperacao/subset_450` no agregado principal nao ficou consistente com
os demais:

- F1 `0.571429`, precisao `0.454545`, recall `0.769231`, melhor epoca `600`, em [training_summary.json](/home/robert/orange_nuclear/runs/experimentos_conflitos/experimento_principal/recuperacao/subset_450/training_summary.json:1)

Esse caso mostra generalizacao pior no corte principal e ja sugere que o cenario
de `recuperacao` e mais sensivel a split e a fase do experimento.

### Leitura dos seeds

Nos seeds de `conflito_implicito/subset_450`, ha variacao real entre execucoes:

- seed `42`: F1 `1.0`
- seed `43`: F1 `0.727273`
- seed `44`: F1 `0.727273`
- seed `45`: F1 `1.0`
- seed `46`: F1 `0.769231`

Nos seeds de `recuperacao/subset_450`, o comportamento ficou muito melhor:

- seeds `42` a `46`: F1 final `1.0`, com melhor epoca `1000`

Leitura pratica:

- `conflito_implicito` parece aprendivel, mas ainda sensivel a inicializacao em
  parte da trilha multiseed;
- `recuperacao` melhorou bastante quando rodada em configuracao multiseed/final,
  o que entra em tensao com o agregado principal.

## Inconsistencias Que Precisam Ficar Claras

Hoje existem inconsistencias entre artefatos e configuracoes reportadas:

- o [aggregate_report.json](/home/robert/orange_nuclear/runs/experimentos_conflitos/experimento_principal/aggregate_report.json:1) registra `subset_sizes = 150,450` e epocas `50,100,200,400,600`;
- os diretorios multiseed tambem contem `subset_50`;
- os diretórios multiseed usam checkpoints ate `1000` epocas;
- portanto, o agregado principal e a trilha multiseed nao representam o mesmo
  envelope experimental.

Isso nao invalida os resultados. Mas impede tratar todo o pacote como uma unica
evidencia uniforme sem explicar a diferenca entre:

- agregado principal;
- finais multiseed;
- curvas/graficos gerados em configuracao posterior.

## Estado Atual Da Trilha `article00`

### O que existe de verdade

A trilha `article00` ja tem base organizada e executavel:

- dataset sintetico em [seed_42](/home/robert/orange_nuclear/runs/article00/datasets/seed_42) e [seed_43](/home/robert/orange_nuclear/runs/article00/datasets/seed_43)
- resumo de treino temporal em [training_summary.json](/home/robert/orange_nuclear/runs/article00/training/seed_42/training_summary.json:1)
- relatorio agregado em [aggregate_report.json](/home/robert/orange_nuclear/runs/article00/reports/aggregate_report.json:1)
- manifesto de comparacao em [graphsage_track_compare.json](/home/robert/orange_nuclear/runs/article00/reports/graphsage_track_compare.json:1)
- figuras experimentais em [runs/article00/figures](/home/robert/orange_nuclear/runs/article00/figures)

Os artefatos atuais deixam isso explicito:

- `status = experimental_temporal_graphsage`
- a trilha ja implementa grafo temporal, reconstrucao e rotulagem
- a parte pendente agora e validacao metodologica, nao ausencia de pipeline

### O que passou a existir

A trilha `article00` agora ja tem um resultado final robusto no protocolo
multiseed oficial do seu melhor cenario experimental geral:

- `450` amostras
- `200` epochs
- thresholds `0.2,0.5,0.9`
- `hidden_dim = 24`
- `embed_dim = 24`
- `dropout = 0.05`
- `temporal_radius = 2`
- `temporal_decay = 0.7`
- `fp_penalty_weight = 0.4`
- `tp_reward_weight = 0.2`
- `selection_mode = composite`

No agregado de `6` seeds, esse cenario alcancou:

- `parameter_kpi_f1 = 1.0`
- `indirect_f1 = 1.0`
- `implicit_f1 = 1.0`

Ela tambem passou a ter um melhor cenario estrito em `threshold = 0.5`:

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

No agregado desse cenario:

- `parameter_kpi_f1 = 0.988889`
- `indirect_f1 = 1.0`
- `implicit_f1 = 1.0`

### O que ainda nao existe

A trilha `article00` ainda nao tem:

- validacao fechada das equacoes e da topologia base contra o paper;
- homologacao metodologica final contra o texto do paper;
- evidencias metodologicas suficientes para artigo sem ressalvas.

### Leitura correta

Hoje a trilha `article00` e:

- implementacao temporal experimental forte;
- trilha paralela separada;
- evidencia experimental robusta, mas ainda nao homologacao metodologica final.

## O Que Ja E Defensavel

Ja e defensavel afirmar que:

- existe uma trilha GreenRAN de GraphSAGE funcional e com resultados concretos;
- essa trilha e experimental e focada nos datasets exportados do GreenRAN;
- existe uma trilha `article00` separada para reproducao do paper;
- a trilha `article00` ainda nao esta metodologicamente homologada contra o paper.

## O Que Nao Deve Ser Afirmado Ainda

Ainda nao e defensavel afirmar que:

- a trilha atual do GreenRAN reproduz fielmente o `artigo00`;
- a trilha `article00` ja valida o paper;
- os resultados do pacote inteiro sao uniformes entre agregado principal e
  multiseed;
- o `GraphSAGE` esta pronto para integracao operacional no GreenRAN.

## Recomendacao De Uso

Para escrita, apresentacao ou artigo, a separacao recomendada hoje e:

1. usar a trilha GreenRAN como experimento proprio de reconstrucao de conflitos;
2. tratar `article00` como trilha metodologica em preparacao;
3. sempre explicitar quando um grafico vem do agregado principal e quando vem do
   pacote multiseed;
4. nao misturar resultados GreenRAN com alegacao de reproducao fiel do paper.

## Conclusao

O repositório hoje tem duas coisas diferentes:

- uma trilha GreenRAN que ja produz artefatos e resultados reais;
- uma trilha `article00` que ja implementa o nucleo temporal, mas ainda precisa
  de validacao metodologica para ser tratada como reproducao fiel do paper.

Essa separacao precisa ser mantida. Ela protege tanto a validade dos resultados
atuais quanto a clareza do que ainda falta fazer.
