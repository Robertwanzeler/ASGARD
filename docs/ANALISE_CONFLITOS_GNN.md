# Analise de Conflitos com GraphSAGE

Este documento resume o fluxo final usado para treinar o modelo de reconstrucao
de grafo, gerar os graficos finais e interpretar o que eles dizem sobre os
cenarios `conflito_implicito` e `recuperacao`.

## Objetivo

O papel do GraphSAGE neste projeto e transformar o cenario de conflitos em um
diagnostico mensuravel:

- verificar se o grafo de conflito pode ser reconstruido a partir dos dados;
- verificar se conflitos `implicit` e `indirect` aparecem de forma aprendivel;
- medir quanto treino e quantos dados sao necessarios;
- medir se o cenario esta bem montado ou se ainda produz sinal fraco.

Os graficos nao sao o objetivo final. Eles servem para dizer:

- se o cenario produz estrutura consistente;
- se a coleta esta suficiente;
- se o threshold escolhido esta razoavel;
- se o comportamento muda por fase ou por regime.

## Cenarios principais

Foram mantidos dois cenarios principais:

- `conflito_implicito`
- `recuperacao`

Eles foram usados porque cobrem dois comportamentos diferentes:

- `conflito_implicito`: caso mais limpo e estavel;
- `recuperacao`: caso dinamico, com transicao de fase no fim.

## Splits adotados

### Conflito implicito

Foi usado holdout temporal por rodadas.

Diretorios-base:

- `runs/experimentos_conflitos/experimento_principal/implicito_seeds`
- `runs/experimentos_conflitos/experimento_principal/implicito_final`

Leitura pratica:

- o modelo ve rodadas iniciais e e avaliado em rodadas posteriores;
- esse cenario generalizou bem sem precisar de ajuste adicional de fase.

### Recuperacao

Foi usado split por fase, e nao um corte temporal bruto.

Diretorios-base:

- `runs/experimentos_conflitos/experimento_principal/recuperacao_seeds`
- `runs/experimentos_conflitos/experimento_principal/recuperacao_final`

Leitura pratica:

- o fim do cenario introduz nos e relacoes raras;
- quando a fase final fica invisivel para o treino, o modelo inventa arestas;
- quando o treino inclui a transicao correta, a generalizacao melhora.

## Multiseed

Os graficos finais foram gerados com `5` seeds:

- `42`
- `43`
- `44`
- `45`
- `46`

As barras de erro representam a variacao entre seeds.

Interpretacao:

- barra pequena: comportamento estavel;
- barra grande: o threshold ou o cenario esta sensivel a inicializacao.

## Epochs e thresholds finais

Os checkpoints finais foram avaliados em:

- epochs: `50, 100, 200, 400, 600, 800, 1000`
- thresholds: `No Threshold`, `0.2`, `0.5`, `0.9`

## Graficos finais mantidos

Foram mantidos apenas os 6 graficos usados como referencia principal:

### Reconstrucao do grafo

- `runs/experimentos_conflitos/experimento_principal/implicito_final/conflito_implicito_reconstruction_f1_vs_epochs.png`
- `runs/experimentos_conflitos/experimento_principal/implicito_final/conflito_implicito_reconstruction_f1_vs_threshold.png`

### Conflito implicito

- `runs/experimentos_conflitos/experimento_principal/implicito_final/conflito_implicito_implicit_f1_vs_epochs.png`
- `runs/experimentos_conflitos/experimento_principal/implicito_final/conflito_implicito_implicit_f1_vs_threshold.png`

### Conflito indireto

- `runs/experimentos_conflitos/experimento_principal/recuperacao_final/recuperacao_indirect_f1_vs_epochs.png`
- `runs/experimentos_conflitos/experimento_principal/recuperacao_final/recuperacao_indirect_f1_vs_threshold.png`

## Como ler os 6 graficos

### Reconstruction F1 vs. Epochs

Pergunta respondida:

- o modelo melhora com mais treino?
- o ganho com `50`, `150` e `450` satura ou continua?

Utilidade no cenario:

- mostra se a estrutura do conflito esta aprendivel;
- ajuda a decidir se o dataset esta pequeno demais.

### Reconstruction F1 vs. Threshold

Pergunta respondida:

- qual threshold produz melhor equilibrio entre falso positivo e falso negativo?

Utilidade no cenario:

- controla agressividade da poda das arestas;
- ajuda a decidir o corte para analise final do grafo.

### Implicit F1 vs. Epochs

Pergunta respondida:

- o modelo aprende conflitos implicitos com mais treino?

Utilidade no cenario:

- valida se o `conflito_implicito` realmente gera dependencia oculta aprendivel.

### Implicit F1 vs. Threshold

Pergunta respondida:

- qual threshold preserva relacoes implicitas sem inflar arestas espurias?

Utilidade no cenario:

- ajuda a separar dependencia real de ruido.

### Indirect F1 vs. Epochs

Pergunta respondida:

- conflitos indiretos aparecem com mais treino e mais dados?

Utilidade no cenario:

- mostra se o `recuperacao` esta produzindo conflito indireto observavel.

### Indirect F1 vs. Threshold

Pergunta respondida:

- qual threshold ajuda a filtrar falso positivo nas relacoes indiretas?

Utilidade no cenario:

- importante no `recuperacao`, onde relacoes indiretas sao mais sensiveis a ruido.

## O que os resultados mostraram

### Conflito implicito

- o cenario e aprendivel;
- o modelo reconstrui o grafo com boa convergencia;
- o `implicit` e util como validacao do cenario;
- esse caso e o melhor para comparar estrutura e convergencia.

### Recuperacao

- o comportamento depende da fase;
- o corte temporal bruto subestimava o modelo;
- o split por fase representou melhor o cenario;
- o `indirect` ficou mais fraco nos subsets menores e so apareceu de forma clara
  em `450`, o que indica que o sinal indireto e mais raro e precisa de mais
  suporte.

## Comandos principais

### Treino GraphSAGE

```bash
./drlexp/.venv/bin/python training/train_graphsage_conflicts.py \
  --experiment-dir runs/experimentos_conflitos/experimento_principal \
  --scenario conflito_implicito \
  --epochs 50,100,200,400,600,800,1000 \
  --subset-sizes 50,150,450
```

Para `recuperacao`, usar o split coerente com a fase observada no cenario.

### Geracao dos graficos finais

```bash
./drlexp/.venv/bin/python scripts/generate_graphsage_paper_multiseed.py \
  --experiment-dir runs/experimentos_conflitos/experimento_principal \
  --scenario conflito_implicito \
  --training-dir runs/experimentos_conflitos/experimento_principal/implicito_seeds/seed_42 \
  --training-dir runs/experimentos_conflitos/experimento_principal/implicito_seeds/seed_43 \
  --training-dir runs/experimentos_conflitos/experimento_principal/implicito_seeds/seed_44 \
  --training-dir runs/experimentos_conflitos/experimento_principal/implicito_seeds/seed_45 \
  --training-dir runs/experimentos_conflitos/experimento_principal/implicito_seeds/seed_46 \
  --output-dir runs/experimentos_conflitos/experimento_principal/implicito_final
```

Para `recuperacao`, trocar `--scenario` e usar os caminhos em
`recuperacao_seeds` e `recuperacao_final`.

## Arquivos de codigo relacionados

- `training/train_graphsage_conflicts.py`
- `scripts/generate_graphsage_paper_multiseed.py`
- `scripts/run_conflict_experiments.py`
- `scripts/export_conflict_dataset.py`
- `scripts/learn_conflict_matrix.py`
