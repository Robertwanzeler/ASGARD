# Padrao DRL Espelhado no ARMD-GreenRAN

## Arquivado

Este documento era especifico das linhas antigas de DRL do projeto:

- `EE-DRL`
- `A3C / SBiLSTM`
- `CAORA`
- `SAC / AWAC`

Essas linhas sairam do escopo oficial para manter apenas a familia do artigo:

- `Task-Specific Sharpness-Aware O-RAN Resource Management Using MARL`

Estado atual:

- o cenario GreenRAN atual foi preservado;
- `ARMD-GreenRAN` e `GraphSAGE` nao foram alterados;
- a DRL oficial passou a ter apenas tres trilhas TA-SAM;
- os detalhes ativos ficam em [docs/RELATORIO_DRL_GREENRAN_IMPLEMENTACAO.md](/home/robert/orange_nuclear/docs/RELATORIO_DRL_GREENRAN_IMPLEMENTACAO.md:1) e [config/tasam_drl_tracks.json](/home/robert/orange_nuclear/config/tasam_drl_tracks.json:1).
