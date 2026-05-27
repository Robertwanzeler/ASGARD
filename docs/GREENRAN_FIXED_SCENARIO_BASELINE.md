# GreenRAN Fixed Scenario Baseline

## Objetivo

Congelar o cenario operacional do GreenRAN para as proximas rodadas de coleta, treino e integracao, sem mudar:

- arquitetura GreenRAN;
- ARMD-GreenRAN;
- papeis de `App1`, `App2` e `App3`;
- topologia logica do runtime.

## Baseline Canonico

### ns-3 principal

- Cenario principal: `scenario-greenran`
- Runtime integrado: [scripts/run_greenran_v2.sh](/home/robert/orange_nuclear/scripts/run_greenran_v2.sh:88)

### App1 / trafego de cameras

- `3` cameras ativas
- IMSIs reservados para cameras: `1-3`
- SLA operacional:
  - throughput alvo `>= 25 Mbps`
  - warning de throughput `< 30 Mbps`
  - warning de latencia `>= 60 ms`
  - violacao de latencia `>= 80 ms`

Fontes:

- [docs/DOCUMENTACAO_GREENRAN.md](/home/robert/orange_nuclear/docs/DOCUMENTACAO_GREENRAN.md:1048)
- [tests/test_armd_runtime_integration.py](/home/robert/orange_nuclear/tests/test_armd_runtime_integration.py:21)
- [src/rapp_orchestrator.py](/home/robert/orange_nuclear/src/rapp_orchestrator.py:980)

### UEs do ns-3

- `12` UEs totais no baseline ns-3
- composicao:
  - `3` cameras
  - `9` UEs background

Fontes:

- [docs/DOCUMENTACAO_GREENRAN.md](/home/robert/orange_nuclear/docs/DOCUMENTACAO_GREENRAN.md:1048)
- [docs/README_GREENRAN.md](/home/robert/orange_nuclear/docs/README_GREENRAN.md:83)

### App3 / veiculos

- teto operacional de `5` veiculos no runtime integrado
- mapeamento CARLA -> IMSI iniciado em `16`
- faixa reservada para App3: `IMSI 16-20`

Fontes:

- [scripts/run_greenran_v2.sh](/home/robert/orange_nuclear/scripts/run_greenran_v2.sh:434)
- [src/carla_ns3_mapper.py](/home/robert/orange_nuclear/src/carla_ns3_mapper.py:128)
- [tests/test_app3_data_lake_integration.py](/home/robert/orange_nuclear/tests/test_app3_data_lake_integration.py:40)

### Parametros de rede canonicos

- `S1-U DataRate`: `15 Mbps`
- `S1-U Delay`: `5 ms`
- `P2P Link`: `30 Mbps, 20 ms`
- `Buffer RLC/PDCP`: `20 MB`
- cameras com `OffTime=3s`
- background `bursty` com `On=1s, Off=10s`

Fonte:

- [docs/DOCUMENTACAO_GREENRAN.md](/home/robert/orange_nuclear/docs/DOCUMENTACAO_GREENRAN.md:1042)

## Mapeamento Funcional

- `App1-Vigilancia` -> cameras / perfil proximo de `eMBB`
- `App2-Monitoramento` -> sensores / perfil proximo de `mMTC`
- `App3-Veicular` -> veiculos / perfil proximo de `URLLC`

## Divergencias Ja Resolvidas

### `10 UEs + 3 cameras`

Alguns scripts legados ainda descrevem `10 UEs + 3 cameras`, por exemplo:

- [scripts/run_scenario_base.sh](/home/robert/orange_nuclear/scripts/run_scenario_base.sh:6)

Esse texto nao deve ser tratado como fonte de verdade do runtime integrado atual.

### `12 UEs` versus `IMSI 16-20`

Nao ha contradicao operacional aqui:

- o baseline ns-3 atual documentado usa `12` UEs;
- a trilha integrada com CARLA reserva uma faixa separada para veiculos a partir do `IMSI 16`;
- isso deixa espaco explicito para App3 sem redefinir o baseline de cameras.

## O Que Fica Congelado

- `3` cameras
- `12` UEs no cenario ns-3 base
- `5` veiculos como teto do App3 integrado
- `IMSI 1-3` para cameras
- `IMSI 16-20` para veiculos
- gargalo `S1-U = 15 Mbps`
- ARMD-GreenRAN mantido como camada fixa

## Proximo Passo de Implementacao

O proximo passo seguro e transformar este baseline em um manifesto de configuracao do runtime, para que:

- dashboard;
- orchestrator;
- coleta;
- App3 mapper;
- e testes

leiam os mesmos numeros sem depender de comentarios ou defaults espalhados.
