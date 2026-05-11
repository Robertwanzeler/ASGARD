# NS3 Article00 Temporal Pipeline

Este fluxo prepara o `ns-3` para rodar a mesma metodologia temporal usada na
trilha sintética `article00`, mas sobre dados coletados do runtime GreenRAN.

## O que o pipeline faz

1. sobe `nearRT-RIC + ns-3 + csv_to_metrics + rApp + apps`;
2. coleta conflitos por cenário com `run_conflict_experiments.py`;
3. converte o dataset coletado para o formato temporal do `article00`;
4. treina o `train_graphsage_article00.py` no dataset convertido;
5. agrega os resultados multi-seed e gera figuras.

## Wrapper pronto

O wrapper principal é:

- [run_ns3_article00_pipeline.sh](/home/robert/orange_nuclear/scripts/run_ns3_article00_pipeline.sh:1)

## Comando direto

```bash
./scripts/run_ns3_article00_pipeline.sh \
  --scenario conflito_implicito \
  --rounds 10 \
  --duration 120 \
  --samples 450 \
  --seeds 42,43,44,45,46,47
```

Valores suportados para `--samples`:

- `50`
- `150`
- `450`
- `0` para usar o export completo

## Artefatos

Coleta bruta:

- `runs/experimentos_conflitos/<timestamp>_conflict_protocol`

Trilha temporal convertida:

- `runs/ns3_article00/<scenario>/datasets/samples_<n>`
- `runs/ns3_article00/<scenario>/training`
- `runs/ns3_article00/<scenario>/reports`
- `runs/ns3_article00/<scenario>/figures`

## Scripts envolvidos

- [generate_ns3_article00_dataset.py](/home/robert/orange_nuclear/scripts/generate_ns3_article00_dataset.py:1)
- [run_ns3_article00_experiments.py](/home/robert/orange_nuclear/scripts/run_ns3_article00_experiments.py:1)
- [run_ns3_article00_pipeline.sh](/home/robert/orange_nuclear/scripts/run_ns3_article00_pipeline.sh:1)
- [train_graphsage_article00.py](/home/robert/orange_nuclear/training/train_graphsage_article00.py:1)

## Observacao metodologica

Esse fluxo reutiliza o trainer temporal do `article00`, mas os parametros do
`ns-3` sao convertidos para sinais proxy derivados dos eventos coletados
(`power`, `confidence`, `mitigation`, `severity`). Ou seja: o pipeline esta
pronto para rodar, mas a calibracao final desses proxies ainda e parte da
validacao experimental.
