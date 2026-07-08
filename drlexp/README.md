# GreenRAN TA-SAM DRL

Pacote DRL consolidado para o eixo `Task-Specific Sharpness-Aware O-RAN Resource Management Using MARL`.

## Trilhas oficiais

1. `greenran_tasam`
   Adaptacao TA-SAM sobre o cenario operacional atual do GreenRAN.

2. `tasam_article_reproduction`
   Reproducao do cenario do artigo TA-SAM a partir de coleta real do `ns-3`.

3. `tasam_reference_base`
   Trilha de referencia/base usada para comparacao metodologica dentro do mesmo eixo TA-SAM.

## Escopo do pacote

- treino TA-SAM scaffold: `drlexp/src/drl/ta_sam_marl.py`
- treino TA-SAM article-SAC/MARL: `drlexp/src/drl/ta_sam_marl_sac.py`
- coleta oficial da linha 2: `scripts/run_tasam_article_ns3_collection.sh`
- runners principais:
  - `drlexp/training/train_tasam_marl.py`
  - `drlexp/training/train_online_tasam_marl.py`
  - `scripts/run_tasam_article_reproduction.py`

## Runner online fiel ao artigo no cenario GreenRAN

Para manter o cenario atual do GreenRAN (`cameras`, `sensores`, `UEs/veiculos`)
mas trocar o regime de treino para o formato do artigo (`atores por DU`,
`critico global`, `replay buffer`, `SAM seletivo`, `rho` dinamico), use:

- `drlexp/training/train_online_tasam_marl.py`

Esse runner usa o ambiente `OnlineGreenRANMARLEnv`, preserva a topologia logica
do cenario (`du_camera_edge`, `du_sensor_mixed`, `du_vehicle_edge`) e exporta
checkpoints no mesmo formato TA-SAM (`tasam_marl_actors.pt`,
`tasam_marl_critic*.pt`, `tasam_marl_checkpoint_meta.json`).

## Base ativa de coleta

A coleta legada enviesada foi retirada da trilha ativa.

Para operar os runners TA-SAM sobre a coleta oficial atual, use:

- `GREENRAN_TASAM_ACTIVE_DB`
- `GREENRAN_TASAM_ACTIVE_STATE_DIR`

Na ausencia desses overrides, a trilha ativa cai no root oficial:

- `runs/tasam_article_ns3_collection`

## Fora de escopo

As linhas antigas `A3C`, `SBiLSTM`, `AWAC` e `SAC single-agent` nao fazem mais parte da pilha oficial de DRL.
