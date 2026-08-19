# SAC Real Collection Playbook

## Status

Este playbook foi arquivado.

A trilha `SAC/AWAC single-agent` nao faz mais parte da pilha oficial de DRL.

## Referencia ativa

Use as trilhas TA-SAM:

- `scripts/run_tasam_greenran_real.py`
- `scripts/run_tasam_article_reproduction.py`
- `scripts/run_tasam_legacy_real.py`

Na linha `tasam_article_reproduction`, a fonte oficial e a coleta real do
`ns-3`, sem gerador sintetico e sem TA-SAM implementado no `rApp`.

Observacao operacional:

- a coleta legada enviesada foi retirada da trilha ativa;
- a base oficial em uso deve ser informada por `GREENRAN_TASAM_ACTIVE_DB`
  e, quando aplicavel, `GREENRAN_TASAM_ACTIVE_STATE_DIR`;
- na falta de override, o root oficial ativo continua sendo
  `runs/tasam_article_ns3_collection`.

Operacao oficial da coleta:

- subir `scripts/run_tasam_article_ns3_collection.sh`
- banco primario: `runs/tasam_article_ns3_collection/rapp_data_lake.db`
- snapshots periodicos: `runs/tasam_article_ns3_collection/db_snapshots`
- export derivado vivo: `runs/tasam_article_ns3_collection/tasam_article_export`
