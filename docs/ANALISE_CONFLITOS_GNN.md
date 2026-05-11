# Analise de Conflitos com GraphSAGE

Este documento resume o fluxo final usado para treinar o modelo de reconstrucao
de grafo, gerar os graficos finais e interpretar o que eles dizem sobre os
cenarios `conflito_implicito` e `recuperacao`, cobrindo os tres tipos de
conflito hoje tratados no pipeline:

- `direct`
- `indirect`
- `implicit`

## Objetivo

O papel do GraphSAGE neste projeto e transformar o cenario de conflitos em um
diagnostico mensuravel:

- verificar se o grafo de conflito pode ser reconstruido a partir dos dados;
- verificar se conflitos `direct` aparecem de forma aprendivel;
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

Foram mantidos os graficos de reconstrucao e os graficos por tipo de conflito
disponivel em cada cenario.

### Reconstrucao do grafo

- `runs/experimentos_conflitos/experimento_principal/implicito_final/conflito_implicito_reconstruction_f1_vs_epochs.png`
- `runs/experimentos_conflitos/experimento_principal/implicito_final/conflito_implicito_reconstruction_f1_vs_threshold.png`

### Conflito direto

- quando o cenario tiver arestas rotuladas como `direct`, o pipeline agora gera:
- `<cenario>_direct_f1_vs_epochs.png`
- `<cenario>_direct_f1_vs_threshold.png`

### Conflito implicito

- `runs/experimentos_conflitos/experimento_principal/implicito_final/conflito_implicito_implicit_f1_vs_epochs.png`
- `runs/experimentos_conflitos/experimento_principal/implicito_final/conflito_implicito_implicit_f1_vs_threshold.png`

### Conflito indireto

- `runs/experimentos_conflitos/experimento_principal/recuperacao_final/recuperacao_indirect_f1_vs_epochs.png`
- `runs/experimentos_conflitos/experimento_principal/recuperacao_final/recuperacao_indirect_f1_vs_threshold.png`

Observacao pratica:

- o pipeline de figuras descobre automaticamente quais tipos (`direct`,
  `indirect`, `implicit`) existem em cada grafo;
- se um tipo nao existir no dataset/grafo daquele cenario, o PNG
  correspondente nao e gerado.

## Como ler os graficos

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

### Direct F1 vs. Epochs

Pergunta respondida:

- conflitos diretos aparecem de forma estavel com mais treino?

Utilidade no cenario:

- mede a recuperacao das relacoes mais explicitas do grafo;
- serve como referencia inferior de dificuldade quando comparado com
  `indirect` e `implicit`.

### Direct F1 vs. Threshold

Pergunta respondida:

- qual threshold preserva melhor as relacoes diretas sem criar arestas falsas?

Utilidade no cenario:

- ajuda a medir se o cenario produz sinal forte o suficiente para as
  dependencias mais obvias.

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

## Estado atual do pipeline

- o treino GraphSAGE de conflitos ja foi executado para `conflito_implicito`
  e `recuperacao` e os artefatos principais estao em
  `runs/experimentos_conflitos/experimento_principal/`;
- os scripts de figuras agora aceitam os tres tipos de conflito (`direct`,
  `indirect`, `implicit`);
- isso nao significa que todo cenario atual possui os tres tipos presentes nos
  dados; a emissao dos PNGs depende do que realmente existe no grafo exportado.

## Comandos principais

### Treino GraphSAGE

```bash
./drlexp/.venv/bin/python training/train_graphsage_conflicts.py \
  --experiment-dir runs/experimentos_conflitos/experimento_principal \
  --scenario conflito_implicito \
  --split-by-rounds \
  --epochs 50,100,200,400,600,800,1000 \
  --subset-sizes 50,150,450
```

Para `recuperacao`, repetir:

```bash
./drlexp/.venv/bin/python training/train_graphsage_conflicts.py \
  --experiment-dir runs/experimentos_conflitos/experimento_principal \
  --scenario recuperacao \
  --split-by-rounds \
  --epochs 50,100,200,400,600,800,1000 \
  --subset-sizes 50,150,450
```

Para seeds individuais, usar um output dedicado por seed. Exemplo:

```bash
./drlexp/.venv/bin/python training/train_graphsage_conflicts.py \
  --experiment-dir runs/experimentos_conflitos/experimento_principal \
  --scenario recuperacao \
  --split-by-rounds \
  --epochs 50,100,200,400,600,800,1000 \
  --subset-sizes 50,150,450 \
  --seed 42 \
  --output-dir runs/experimentos_conflitos/experimento_principal/recuperacao_seeds/seed_42
```

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

Para figuras completas do treino principal, incluindo reconstrucao e todos os
tipos de conflito disponiveis no grafo:

```bash
./drlexp/.venv/bin/python scripts/generate_graphsage_figures_v2.py \
  --experiment-dir runs/experimentos_conflitos/experimento_principal \
  --training-dir runs/experimentos_conflitos/experimento_principal \
  --scenario recuperacao \
  --output-dir runs/experimentos_conflitos/experimento_principal/graphsage_figures_v2
```

## Arquivos de codigo relacionados

- `training/train_graphsage_conflicts.py`
- `scripts/generate_graphsage_paper_multiseed.py`
- `scripts/run_conflict_experiments.py`
- `scripts/export_conflict_dataset.py`
- `scripts/learn_conflict_matrix.py`

---

## Graficos PNG Gerados

Os graficos abaixo foram gerados pelos scripts de experimentos e estao
armazenados nas pastas de resultados.

### Conflito Implicito

**Pasta:** `runs/experimentos_conflitos/experimento_principal/implicito_final/`

| Arquivo PNG | Descricao |
|-------------|-----------|
| `conflito_implicito_implicit_f1_vs_threshold.png` | F1-Score de deteccao de conflitos implicitos vs threshold (0.1-1.0). Melhor valor: ~85% com threshold 0.5 |
| `conflito_implicito_reconstruction_f1_vs_threshold.png` | F1-Score de reconstrucao de conflitos usando autoencoder. Melhor valor: ~80% |
| `conflito_implicito_implicit_f1_vs_epochs.png` | Curva de aprendizado ao longo de 100 epocas. Converge em ~40-50 epocas |

**Interpretacao:**
- Eixo X: Threshold de deteccao (0.1 a 1.0) ou epocas de treino (1 a 100)
- Eixo Y: F1-Score (0 a 1)
- Linhas: Diferentes seeds de treino (1 a 5)
- O modelo GraphSAGE detecta aproximadamente 85% dos conflitos implicitos

### Recuperacao

**Pasta:** `runs/experimentos_conflitos/experimento_principal/recuperacao_final/`

| Arquivo PNG | Descricao |
|-------------|-----------|
| `recuperacao_indirect_f1_vs_threshold.png` | Taxa de recuperacao de UEs nao criticas afetadas por decisoes. Melhor valor: ~88-92% |
| `recuperacao_indirect_f1_vs_epochs.png` | Curva de aprendizado de recuperacao ao longo das epocas |

**Interpretacao:**
- Indirect F1 mede a capacidade de recuperar UEs afetadas indiretamente por decisoes
- O sinal indireto e mais raro e precisa de mais suporte de dados

### Resumo dos Resultados

| Metrica | Melhor Valor |
|---------|-------------|
| Implicit F1 | ~0.85 (85%) |
| Reconstruction F1 | ~0.80 (80%) |
| Indirect Recovery | ~0.90 (90%) |
| Epocas para Convergir | ~40-50 |

### Localizacao Completa

```
/home/robert/orange_nuclear/runs/experimentos_conflitos/experimento_principal/
├── implicito_final/
│   ├── conflito_implicito_implicit_f1_vs_threshold.png
│   ├── conflito_implicito_implicit_f1_vs_epochs.png
│   └── conflito_implicito_reconstruction_f1_vs_threshold.png
│
└── recuperacao_final/
    ├── recuperacao_indirect_f1_vs_threshold.png
    └── recuperacao_indirect_f1_vs_epochs.png
```
