# Figuras de Comparacao `article00` vs `ARMD-GreenRAN`

Este fluxo separa explicitamente:

- `Random` = baseline do `artigo00`
- `GraphSAGE-CL` = artigo base
- `ARMD-GreenRAN` = metodo proposto

## Regra de manutencao

O arquivo fixo do artigo base e:

- [article00_reference_fixed.json](/home/robert/orange_nuclear/config/article00_reference_fixed.json:1)

O arquivo editavel do metodo proposto e:

- [armd_greenran_series.json](/home/robert/orange_nuclear/config/armd_greenran_series.json:1)

Regra pratica:

- nao mexer no arquivo fixo do artigo;
- atualizar apenas o arquivo do `ARMD-GreenRAN`.

## Geracao

```bash
./drlexp/.venv/bin/python scripts/generate_article00_armd_comparison_figures.py
```

Saida:

- `runs/article00/comparison_figures/comparison_reconstruction_threshold_0_5.png`
- `runs/article00/comparison_figures/comparison_reconstruction_dataset_450_thresholds.png`
- `runs/article00/comparison_figures/comparison_indirect_threshold_0_5.png`

## Observacao

Os valores atuais do `ARMD-GreenRAN` foram atualizados para o melhor cenario
estrito em `threshold = 0.5`, com:

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

Os comparativos tambem preservam a leitura do melhor cenario geral em
`threshold = 0.2` dentro do grafico de sensibilidade por threshold.

Se o `ARMD-GreenRAN` mudar, ajuste apenas:

- [armd_greenran_series.json](/home/robert/orange_nuclear/config/armd_greenran_series.json:1)
