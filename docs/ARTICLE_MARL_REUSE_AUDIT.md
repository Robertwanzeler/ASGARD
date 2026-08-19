# Article MARL Reuse Audit

## Veredito atual

O reuso aprovado da DRL no projeto fica restrito ao eixo `TA-SAM MARL`.

As tres trilhas oficiais sao:

1. `greenran_tasam`
2. `tasam_article_reproduction`
3. `tasam_reference_base`

## Manter

- `src/greenran_marl_topology.py`
- `src/rapp_rl_policy.py`
- `src/rapp_marl_shadow.py`
- `src/rapp_marl_control_gate.py`
- `src/rapp_sac_resource_model.py`
- `drlexp/src/drl/ta_sam_marl.py`
- `drlexp/src/drl/ta_sam_marl_sac.py`
- `drlexp/src/drl/online_greenran_marl_env.py`
- `drlexp/training/train_tasam_marl.py`
- `drlexp/training/train_online_tasam_marl.py`
- `scripts/run_tasam_greenran_real.py`
- `scripts/run_tasam_article_reproduction.py`
- `scripts/run_tasam_legacy_real.py`

## Remover da pilha oficial

- `A3C`
- `SBiLSTM`
- `AWAC`
- `SAC single-agent`
- `CAORA`

## Restricoes mantidas

- nao alterar `ARMD-GreenRAN / GraphSAGE`
- nao alterar a arquitetura do cenario GreenRAN atual
- manter a linha 2 baseada em coleta real `ns-3`, sem TA-SAM implementado no `rApp`
