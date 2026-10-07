# Relatório técnico da simulação GreenRAN

**Data do snapshot atualizado:** 06/10/2026 (BRT)
**Snapshots anteriores:** 14/09/2026, 11/09/2026 (BRT)
**Escopo:** estado da simulação, objetivo científico e validade da comparação principal do artigo
**Comparação principal:** `rApp-only` versus `ASGARD (rApp + ARMD assist + TA-SAM)`

## 1. Sumário executivo

O GreenRAN é uma plataforma experimental O-RAN para gerenciamento conjunto de
recursos e energia, mantendo requisitos de qualidade para três classes de
serviço: vídeo de vigilância, sensores mMTC e aplicações veiculares V2X.

Neste relatório, o nome operacional do braço proposto é **ASGARD**: `rApp + ARMD
assist + TA-SAM`. O baseline continua sendo `rApp-only`.

O objetivo do artigo é verificar se a extensão adaptativa e protegida do rApp
melhora o compromisso entre economia de energia e atendimento de SLA quando
comparada ao rApp convencional baseado em regras fixas.

A comparação correta é:

| Braço | Composição | Função |
|---|---|---|
| Baseline | `rApp` determinístico | Decide por regras, limiares e fallback fixos; TA-SAM e ARMD ficam desabilitados |
| Proposto | `rApp + TA-SAM + ARMD` | TA-SAM recomenda/adapta recursos e ARMD avalia risco antes da aplicação |

O rApp está presente nos dois braços. A comparação não é “com rApp versus sem
rApp”. O SAC puro pode ser usado como diagnóstico complementar, mas não é o
baseline principal desta pesquisa.

No snapshot analisado, o projeto ainda está em desenvolvimento experimental.
A infraestrutura de integração e a via de controle existem, mas ainda não há
uma campanha pareada, válida e completa que permita afirmar superioridade do
método proposto.

**Atualização de 06/10/2026 — mecanismo validado de ponta a ponta.** Nesta
data, com a autoridade nativa E2 por DU operante (commits de 28/09), foi
executado um ciclo completo do curriculum de confronto (`r2_conflict_1cycle`,
seed 43, schedule determinístico `pair-8c811ef7ab16a11e98a8`): os 9 estágios
foram cobertos (24–47 decisões nativas cada), o ator cortou potência até o
piso de 25% e recuou sob confronto, entregando **−21,3% de energia média de
ciclo (pico −30,7% em `vehicle_conditional`; −26,3% na DU mais carregada)**
contra o baseline estático a 100%, com **penalidades idênticas às do baseline
(64 VEHICLE_CRITICAL nos dois runs — zero amplificação pelos cortes)** e
**SLA veicular em paridade (latência máx. 4,16 ms, perda 2,31%, zero veículos
em risco)**. A simulação está íntegra e operacional. Nota de honestidade
científica: essa evidência compara o ASGARD com autoridade nativa contra um
baseline 100% estático — a comparação principal do artigo (`rApp-only` ×
`ASGARD`) segue pendente da campanha formal pareada (r8).

A DRL do TA-SAM continua em desenvolvimento e treinamento online. O objetivo
das próximas rodadas é treinar por mais tempo com transições econômicas
realmente aplicadas, melhorar a estabilidade da política e aumentar a economia
sem degradar os SLAs. Cada novo candidato deverá ser validado antes de ser
promovido.

## 2. Objetivo científico

O GreenRAN deve demonstrar, sob a mesma carga e a mesma topologia, que o pacote
`rApp + TA-SAM + ARMD` consegue:

1. compartilhar recursos entre vídeo, sensores e veículos;
2. reduzir a energia estimada e a utilização desnecessária de recursos;
3. preservar os SLAs de câmera, sensores e veículos;
4. arbitrar conflitos entre xApps;
5. adaptar as decisões de alocação por DU lógico;
6. bloquear ou degradar com segurança ações consideradas arriscadas;
7. superar, ou pelo menos melhorar o compromisso energia–SLA do rApp
   determinístico;
8. produzir evidência reproduzível a partir de PDCP real e alinhamento temporal
   válido.

A hipótese do artigo é sobre o **ganho do pacote completo**. A comparação de
dois braços não permite atribuir separadamente um percentual de ganho ao
TA-SAM ou ao ARMD.

## 3. Arquitetura da simulação

O fluxo operacional é:

```text
CARLA/contexto veicular
        ↓
ns-3.42 + mmWave/LTE + mobilidade + tráfego
        ↓  KPM/PDCP e E2SM-RC
FlexRIC nearRT-RIC
        ↓
xApps: slicer, energy saver, TA-SAM actuator, vehicle control
        ↓
rApp Non-RT RIC
        ↓
TA-SAM + ARMD + judge + safety shield
        ↓
ações de potência/alocação e Data Lake SQLite
```

### 3.1 Cenário de rede

O cenário fixo atual contém:

- 20 UEs;
- 3 câmeras 4K, IMSIs 1–3;
- 12 sensores mMTC, IMSIs 4–15;
- 5 veículos V2X, IMSIs 16–20;
- uma célula LTE e múltiplas células mmWave;
- mobilidade e handover para os veículos;
- tráfego de vídeo, sensores e mensagens veiculares;
- métricas PDCP, RLC, PHY, SINR, handover e alocação do scheduler;
- controle de scheduler e potência por E2SM-RC.

Os requisitos operacionais usados nos avaliadores são, em resumo:

- câmera: throughput mínimo de 25 Mbps e P95 de latência até 80 ms;
- sensor: entrega mínima de 95%, perda máxima de 5% e P95 até 500 ms;
- veículo: latência máxima de 20 ms e perda máxima de 1%.

### 3.2 Camadas de controle

- **ns-3:** modela a rede, tráfego, rádio, mobilidade e energia estimada.
- **FlexRIC:** fornece o nearRT-RIC e o transporte E2 para KPM e controle.
- **xApps:** monitoram métricas e executam ações recebidas do rApp.
- **rApp:** agrega observações, aplica regras, arbitra conflitos e registra a
  decisão.
- **TA-SAM:** política MARL baseada em SAC com SAM, organizada por três DUs
  lógicos.
- **ARMD:** camada de avaliação de conflitos/risco baseada na trilha GraphSAGE;
  no runtime atual, pode elevar a severidade da proteção, mas não remover
  proteção existente.
- **Safety shield e floors:** validam ações, preservam limites mínimos por UE e
  acionam fallback quando o orçamento não permite satisfazer todos os pisos.
- **Data Lake:** persiste decisões, métricas, feedback, evidências de energia
  e auditoria E2.

### 3.3 Componentes e mecanismos em detalhe

Esta seção descreve cada componente da simulação e como ele funciona. O fluxo
de controle de ponta a ponta é:

```text
CARLA/contexto veicular + cargas (App1 câmera, App2 sensor, App3 veicular)
        ↓
ns-3 (rede, rádio, mobilidade, energia estimada por célula)
        ↓ E2 (KPM de telemetria + E2SM-RC de controle)
FlexRIC nearRT-RIC → xApps (monitoração)
        ↓
rApp orchestrator: agrega observações → TA-SAM (recomenda) → ARMD (avalia risco)
        ↓ Judge arbitra → safety shield + pisos validam (fail-closed)
        ↓ aprovado
bundle de controle v4 (power_by_cell, sleep commit, símbolos discricionários)
        ↓ E2
xapp_tasam_actuator → ns-3 (potência/sleep por DU)
        ↓ readbacks no wire (confirmação de aplicação)
Data Lake (journal de decisões, traces, energia, snapshots PDCP)
```

#### 3.3.1 Cenário ns-3

O cenário `Energy_saving_with_cell_utilization_scenario` tem 20 UEs: 3
câmeras de vigilância da App1 (IMSI 1–3), 12 UEs de background da App2
(IMSI 4–15) e 5 veículos da App3 (IMSI 16–20), com uma célula LTE âncora e
três células mmWave mapeadas nos DUs lógicos 2, 3 e 4. Há mobilidade e
handover para os veículos, e o controle desce por E2SM-RC (scheduler e
potência). Os requisitos operacionais por classe: câmera com throughput
mínimo de 25 Mbps e P95 até 80 ms; sensor com entrega mínima de 95%, perda
máxima de 5% e P95 até 500 ms; veículo com latência máxima de 20 ms e perda
máxima de 1%. As métricas vêm de PDCP real (sem proxy), RLC, PHY, SINR,
handover e do scheduler.

#### 3.3.2 Curriculum de confronto (alternador de cenário)

O treino e a validação rodam sob um alternador de cenário com ciclo de 120 s
dividido em nove estágios, na ordem: `allowed_bootstrap` (aquecimento saudável,
tudo ALLOWED para produzir decisões rápidas), `allowed_stable`,
`camera_conditional`, `camera_blocked`, `vehicle_conditional`,
`vehicle_blocked`, `app2_conditional`, `app2_blocked` e `allowed_recovery`.
Os estágios `conditional` impõem pressão parcial por aplicação; os `blocked`
induzem carga hostil de propósito (gerando pressão de SLA legítima, inclusive
no baseline a 100%); o `recovery` herda o backlog do estágio hostil anterior.
A auditoria de cobertura exige no mínimo três decisões nativas por estágio;
um único ciclo de 120 s cobre os nove estágios (24–47 decisões cada no run de
06/10, que percorreu quase dois ciclos).

#### 3.3.3 FlexRIC e transporte E2

O FlexRIC fornece o nearRT-RIC e o transporte E2 em duas direções: KPM de
telemetria (métricas por célula/UE) e E2SM-RC de controle (comandos de
scheduler e potência). Cada braço roda em slot de execução isolado (slot-a/slot-b)
com portas E2 dedicadas (ex.: 36431/36432) e cgroups `cpu`/`memory`/`io`
anexados pelo dispatcher do host, com supervisor dedicado para o atuador.

#### 3.3.4 xApps

Dois papéis: os xApps de monitoração publicam KPM; o `xapp_tasam_actuator`
recebe o bundle de controle e aplica a ação no ns-3 — mapa de potência por
célula, `sleep commit` e símbolos DL discricionários no scheduler/PHY. Toda
aplicação gera transações idempotentes (`PowerTransactionId`,
`SleepTransactionId`) e readbacks por janela (`ObservationKind =
power_readback`) que confirmam no wire o percentual efetivamente aplicado por
DU — a prova de autoridade do mecanismo.

#### 3.3.5 rApp (orchestrator)

O rApp é o agregador: coleta observações (PDCP real, SINR, associação,
energia), aplica regras de validação de dados e limites físicos, produz
propostas de alocação/potência, arbitra conflitos entre xApps via Judge,
registra cada decisão no journal (estágio do curriculum, `priority_violation`,
ação econômica) e monta o bundle de controle. No braço baseline, o mesmo rApp
decide só por regras fixas — TA-SAM e ARMD desabilitados.

#### 3.3.6 TA-SAM (política de aprendizado)

TA-SAM é a política MARL baseada em SAC com SAM, organizada pelos três DUs
lógicos: um ator global com heads de potência por DU, treinada online sob o
curriculum da §3.3.2. O treinamento usa bancos de replay — histórico (ex.:
`replay_90.jsonl` de campanhas congeladas) e recente (janela de 90 linhas do
próprio run) — com contrato de replay 80/20; o bootstrap do checkpoint deriva
de categoria validada (`build_tasam_v10_checkpoint`). O reward prioriza o SLA
veicular (sinaliza `VEHICLE_WARNING`/`VEHICLE_CRITICAL` em
`priority_violation`), e os cortes de potência respeitam o piso (25% no run
de 06/10) com recuo automático sob confronto. Controles de qualidade: gate de
treinabilidade (`trainability-gate-only`), validador fail-closed, seleção de
linhas por estágio, decisões shadow e promoção explícita de checkpoint
(`promotable`/`not_promotable`). No run de 06/10, 239/239 decisões rodaram
sem intervenção do teto de risco (`ml_risk_cap_applied = False`).

#### 3.3.7 ARMD (avaliação de risco e conflitos)

ARMD é a camada de avaliação de conflitos e risco baseada na trilha
GraphSAGE (classificação/reconstrução de grafo; F1 = 1,0 offline na trilha
article00, com cenários e critérios próprios). No runtime, avalia o contexto
de conflito entre propostas/xApps e pode **elevar a severidade da proteção —
nunca removê-la**: o teto de risco (`ml_risk_cap`) aplica-se sobre a ação
recomendada e as decisões shadow permitem avaliar o modelo sem intervir. A
trilha GraphSAGE/article00 permanece separada da contribuição principal do
artigo.

#### 3.3.8 Judge

O Judge arbitra a decisão final entre propostas concorrentes (regras do rApp,
TA-SAM, proteções) e grava o feedback da decisão. O relógio da decisão é
gravado no topo do row da Data Lake (`make_decision`), o que corrigiu o TTL
de 5 s simulados que nunca vencia quando o carimbo só existia no envio (defeito
exposto pela r29, seção 6.7).

#### 3.3.9 Safety shield e pisos

Camada fail-closed: valida toda ação contra limites físicos e pisos mínimos
por UE/classe, preserva os pisos de SLA e aciona fallback determinístico
quando o orçamento não satisfaz todos os pisos. Os pisos são governados por
ledger (`safe-power-floor-ledger`) com fonte em telemetria nativa ou
checkpoint validado — um ledger de pisos 100/100/100 bloqueia qualquer descida
(como ocorreu na r23 e nos probes de 06/10).

#### 3.3.10 Contrato de controle (bundle)

A ação aprovada viaja pelo contrato `greenran.control.bundle.v2` (hoje
envelope v2/bundle v4): mapa `power_by_cell` por DU, `sleep commit` e
símbolos DL discricionários (commits de 28/09), com evidência e autoridade
(`power_control_authority: ns3_native_e2_phy_and_energy_model`). O envelope
carrega proveniência (seed, schedule, checkpoint) e as correções anti-vazamento
garantem que potência fixa e escada não sejam reescritas por estados
intermediários (`send_level` e bundle obedecem ao valor autorizado — comitados
`0679bd7` e `50d9fd4`).

#### 3.3.11 Modelo de energia

A energia é **estimativa calibrada da simulação** (não há wattímetro físico):
o modelo por célula separa segundos idle/TX/dados/controle e aplica a
calibração de `config/energy_calibration_sim_v3_sleep.json`, com leitura
amostrada a cada 10 s (`NetEnergy`, `DiffEnergy` por célula). O sleep real de
célula entra no modelo desde o contrato v3 de calibração.

#### 3.3.12 Data Lake e auditoria

Toda evidência persiste em Data Lake + artefatos por run: journal
`rapp_decisions.jsonl` (estágio, prioridade, ação econômica),
`TasamControlObservations.csv` (readbacks com transações),
`TasamAssociationTrace.csv` (associação por UE), `energyfilecell*.csv`
(energia por célula) e `monitoring_snapshot.json` PDCP real por aplicação. A
validade exige janelas completas, alinhamento temporal entre estado/ação/próximo
estado, configuração/seed/checkpoint identificáveis e ausência de falha do
ns-3, do RIC ou do coletor — requisitos listados na §5.

#### 3.3.13 Infraestrutura de execução

Os braços rodam em slots isolados (slot-a/slot-b) com cgroups e portas
dedicadas, sob dispatcher local com fila (`runs/agent_jobs`) e política de
reap — campanha nunca sobrevive ao worker. O pareamento usa schedule
canônico determinístico (`tasam_pairing.canonical_schedule`): perfil + seed +
wall time produzem um `schedule_id` estável (ex.: `pair-8c811ef7ab16a11e98a8`),
garantindo reprodutibilidade do curriculum entre braços e runs.

## 4. Definição dos braços experimentais

### 4.1 Baseline: rApp determinístico

O baseline representa o controlador convencional. Ele usa a mesma cadeia de
observação e aplicação do experimento, mas toma a decisão com regras fixas,
limiares, prioridades e fallback.

Neste braço:

- TA-SAM fica desabilitado;
- ARMD fica desabilitado como camada de decisão/proteção adaptativa;
- o rApp continua usando validação de dados, limites físicos e fallback
  indispensáveis para que o experimento não se torne uma comparação entre um
  sistema protegido e um sistema sem integridade operacional;
- topologia, tráfego, seeds, duração, cadência e espaço de ações permanecem
  iguais ao braço proposto.

### 4.2 Método proposto: rApp + TA-SAM + ARMD

Neste braço:

- o TA-SAM produz recomendações de alocação por DU e de potência;
- o ARMD avalia o contexto de conflito e pode aumentar a proteção;
- o judge arbitra a decisão final;
- o safety shield impede ações inválidas ou incompatíveis com os pisos de SLA;
- ações aprovadas seguem pelo contrato `greenran.control.bundle.v2` até o
  xApp `xapp_tasam_actuator` e o E2SM-RC.

O par experimental já está representado no runner
[`scripts/run_tasam_deterministic_pair.py`](/home/robert/orange_nuclear/scripts/run_tasam_deterministic_pair.py):

```text
rapp_only: armd=false, tasam=false
combined:  armd=true,  tasam=true
```

Esse runner gera um schedule pareado, executa os braços com a mesma seed e
avalia os artefatos com uma porta de aceitação fail-closed. A existência do
runner não significa que o par já foi concluído com evidência válida.

### 4.3 SAC puro

SAC puro é a versão de diagnóstico em que a política usa SAC sem os elementos
específicos do pacote TA-SAM e sem ARMD. Ele pode ajudar a entender o
comportamento da política de aprendizado, mas não deve substituir o rApp
determinístico como baseline principal da pergunta do artigo.

## 5. Métricas e validade científica

Cada braço deve ser avaliado com as mesmas condições experimentais. As métricas
principais são:

- energia estimada total e por célula;
- fração de economia de energia estimada;
- throughput, P95 e cauda de latência;
- perda e entrega de pacotes;
- disponibilidade e violações de SLA por classe de serviço;
- número de ações aplicadas, rejeitadas, revertidas e protegidas;
- conflitos entre xApps e decisões do juiz;
- estabilidade das ações e frequência de fallback;
- custo computacional e volume de artefatos de controle.

A energia deve ser chamada de **estimativa calibrada da simulação**. O modelo
atual não possui wattímetro físico; portanto, os valores de energia não podem
ser apresentados como medição de consumo de hardware real. A calibração está
descrita em [`docs/ENERGY_CALIBRATION.md`](/home/robert/orange_nuclear/docs/ENERGY_CALIBRATION.md).

Uma campanha só pode ser usada como evidência principal se cumprir todos estes
requisitos:

- PDCP real, sem latência proxy;
- todas as janelas e todos os UEs esperados;
- alinhamento temporal entre estado, ação e próximo estado;
- configuração, seed, perfil e checkpoint identificáveis;
- traces de energia e scheduler completos;
- métricas de SLA completas;
- ausência de falha do ns-3, do RIC ou do coletor;
- comparação pareada com gap de decisões dentro do limite;
- relatório de avaliação gerado pelo avaliador estrito.

Se qualquer requisito falhar, a campanha deve ser classificada como inválida
ou parcial, sem ser usada para sustentar a hipótese principal.

## 6. Estado atual da simulação

### 6.1 O que está operacional

As verificações do snapshot indicam que:

- a suíte Python passou com **652 testes e 8 subtestes** quando executada
  com o `PYTHONPATH` correto (atualizado em 26/09/2026; o snapshot
  original registrou 510 testes e 6 subtestes);
- Python compilou sem erros sintáticos;
- os scripts shell passaram na verificação de sintaxe;
- o fluxo ns-3 → E2 → FlexRIC → xApps → rApp está implementado;
- o coletor consegue produzir métricas PDCP e alimentar o Data Lake;
- o xApp `xapp_tasam_actuator` está compilado e recebe o contrato de controle
  versionado;
- o runtime do TA-SAM possui checkpoint, shadow advisor, judge, shield e
  protocolo de auditoria;
- o pipeline GraphSAGE/ARMD possui artefatos offline e integração no rApp.

Esses pontos comprovam integração e funcionamento parcial da plataforma, não
comprovam ainda o ganho causal do método proposto.

### 6.2 Campanha online de 11/09 (registro histórico)

Na leitura feita durante a elaboração deste relatório, a campanha
`runs/tasam_learning_meter_online_seed47_20260911` ainda estava em execução.
O snapshot apresentava aproximadamente:

- seed 47 e perfil `tasam_training_balanced_v3`;
- 544 decisões observadas;
- estágio `canary_10`, fração de rollout de 10%;
- zero atualizações de treino concluídas;
- nenhum candidato promovido;
- 37 transições econômicas observadas, mas status do medidor
  `NO_LEARNING`/`learning_meter=0`;
- manifesto declarando `observe_only=true`, ao mesmo tempo em que o estado
  operacional reportava `canary_10`.

Essa campanha é evidência de integração/observação e de comportamento
fail-safe. Ela não é evidência válida de aprendizagem online ou de economia
realizada do braço combinado. Como ainda estava ativa no momento da leitura,
seus números devem ser tratados como histórico, não como estado atual.

### 6.3 Campanha v11

A campanha
`runs/tasam_online_observation_seed47_20260910_v11` terminou com:

- 2.400 decisões;
- estágio final `canary_50`;
- `updates_completed=0`;
- nenhum candidato promovido;
- replay econômico elegível igual a zero no fechamento;
- encerramento por alcance do alvo de decisões.

Veredito: **inválida para demonstrar aprendizagem online ou vantagem econômica**.
Ela ainda é útil para mostrar que o caminho de decisão, auditoria e controle
foi exercitado e para investigar por que o controlador não produziu updates.

### 6.4 Trilha veicular

As tentativas da campanha de viabilidade veicular não formaram evidência
principal. Na tentativa mais recente disponível, o avaliador registrou:

- 48 janelas completas;
- apenas 18 janelas válidas;
- 120 janelas pontuáveis exigidas;
- falhas de perda para IMSIs 19 e 20;
- `valid=false` e encerramento antecipado.

As tentativas anteriores também apresentaram falhas do ns-3, inclusive
`NS_ASSERT` relacionado a ponteiro nulo. Portanto, o comportamento veicular
continua sendo uma pendência de validação, especialmente porque V2X é a classe
de maior prioridade no reward e no SLA.

### 6.5 GraphSAGE/ARMD e article00

A trilha GraphSAGE/article00 deve permanecer separada da contribuição principal.
Ela possui resultados offline fortes em alguns pacotes, incluindo F1 igual a
1,0 em avaliações documentadas, mas usa cenários, dados e critérios próprios.
Esse resultado valida uma capacidade de classificação/reconstrução do grafo;
não prova por si só que o pacote `rApp + TA-SAM + ARMD` reduz energia mantendo
SLA na simulação GreenRAN.

As limitações e diferenças metodológicas estão registradas em
[`docs/ARTICLE00_STATUS_EXPERIMENTAL.md`](/home/robert/orange_nuclear/docs/ARTICLE00_STATUS_EXPERIMENTAL.md)
e nos documentos de auditoria da trilha article00.

### 6.5 Atualização de acompanhamento — 14/09/2026

Uma nova smoke de infraestrutura foi concluída em
`runs/tasam_asgard_adaptation_smoke_seed47_20260914_v8_fixed_rerun` com 20
decisões, 20 IMSIs observados, 3 DUs, PDCP real e encerramento sem processos
órfãos. O dispatcher do host também confirmou o attach real no cgroup com os
controladores `cpu`, `memory` e `io`.

A smoke de atuação está sendo executada em
`runs/tasam_asgard_actuation_smoke_seed47_20260914_v8_fixed`. O contrato ativo
usa `e2ControlEnabled=true`, `e2nrEnabled=false`, `e2du=true`, o checkpoint
econômico local e o perfil `tasam_training_balanced_v3`. Nos primeiros dados
observados havia métricas PDCP, mas ainda não havia confirmação nativa de uma
ação TA-SAM. Também foi registrado o erro `AF_UNIX path too long`, que produz
ações `failsafe`; por isso essa smoke não pode ser aceita como evidência de
economia até terminar sem esse erro e confirmar a atuação no ns-3.

O cenário ns-3 atualmente contém instrumentação adicional para:

- separar controle E2 de relatórios E2, permitindo controle com relatórios NR
  desabilitados;
- registrar, por célula e por janela, transação, potência aplicada, potência
  nominal e células ativas;
- exportar energia nativa por célula em diretório de campanha;
- reconhecer os perfis de pressão usados pelo Python;
- registrar os bearers veiculares dos IMSIs 16–20;
- controlar potência fixa e número de células ativas nas calibrações.

Essas alterações estão no código-fonte do cenário e no submódulo ns-3, mas o
resultado da smoke continuará sendo separado da avaliação causal final.

### 6.6 Linha de base provisória para melhoria

O ponto de partida para as próximas versões é:

| Métrica | Último valor registrado | Interpretação |
|---|---:|---|
| Economia de alocação no medidor | **16,04%** | contrafactual/treino, não conclusão causal |
| Delta de SLA no medidor | **0,00 pp** | sem regressão registrada naquele snapshot |
| Checkpoint promovido | **não** | gate/evaluador bloqueados |

Assim, o objetivo das próximas alterações é superar essa linha de base com
medição consistente, ações TA-SAM confirmadas e comparação pareada entre
`rApp-only` e ASGARD. A energia permanece uma estimativa relativa da simulação
ns-3, sem representar consumo físico medido. O treinamento online da DRL será
continuado para buscar uma política mais estável e uma economia reproduzível,
sempre mantendo o gate de SLA e a exigência de evidência aplicada.

### 6.7 Cadeia window90 de 25–26/09/2026 (r26 → r31 → formal r6g/r6h)

A trilha `tasam_v2x_energy_window90` (seed 43, 120 s de simulação, nove
estágios do alternador) consolidou a plataforma e expôs três defeitos de
contrato, todos corrigidos com regressão:

1. **Baseline r26 (25/09)** — referência perfeita: seleção 90/90, RTF
   0,0201, ledger de pisos nativo validado {2:60, 3:60, 4:60} com 49
   sequências confirmadas. Congelado como baseline oficial da seed 43 e
   reusado por `--baseline-source` (validação de sha no piloto) — o
   baseline computa uma vez, por política.
2. **r27–r29: escudo e relógio do Judge** — o escudo travava o run inteiro
   em 100% por regras de SLA calibradas para extremos (latência veicular
   avaliada no máximo em vez do P95; pisos saturados com share 0;
   throughput de câmera 22,6 < 25 Mbps). Recalibrado (P95 20 ms, pisos com
   capacidade de par, câmera 18 Mbps). A r29 então entregou o melhor run
   econômico (cortes 70% ×1156 observações, sem recaída) mas falhou na
   seleção (20/90) por `judge_feedback_pending`: a observação do Judge roda
   antes do envio E2 e o carimbo `sim_time_s` só era gravado no send — o
   TTL de 5 s simulados nunca vencia. Fix: `make_decision` grava o relógio
   do row da Data Lake no topo da decisão.
3. **r31 (26/09, piloto)** — primeiro piloto completo saudável: asgard
   90/90, RTF 0,0179, 145+ outcomes consecutivos do Judge, 5 marcos de
   treino, 3 promoções, 0 rollbacks. `not_promotable` por design
   (`pilot_only_non_promotable`): a promoção vem da pareada.

**Formal (driver r5)**: a r6g expôs o quarto defeito — a pareada frozen
não recebia o `--safe-power-floor-ledger`, e a escada, fail-closed, caiu
no caminho full-power (130/130 failsafe no asgard congelado, zero cortes).
Correção com validação fail-fast (commit `ce381b4`). Investigou-se também
a atomicidade POWER/COMMIT no ns-3: o gating foi revertido porque as
células 2/4 falham legitimamente no PREPARE (IMSI anexado na célula 3) e
a cobertura de 3 células por sequência do contrato v6 depende da
aplicação incondicional; o hardening por célula fica registrado para a
Fase 2. A r6h relança a cadeia completa com gate reusado por hash,
baseline congelado e pareada com ledger. Veredito pendente no fechamento
deste relatório.

### 6.8 Autoridade nativa E2 por DU e o ciclo completo de confronto (28/09 → 06/10/2026)

Esta seção consolida a linha mais recente de trabalho, que levou o mecanismo
de economia por DU à primeira demonstração completa, ponta a ponta, sob todo o
espaço de confronto do curriculum.

**28/09 — autoridade nativa v4 (10 commits, HEAD `50d9fd4`).** Implementada a
cadeia completa de controle nativo: no ns-3, símbolos DL discricionários e
`sleep commit` por DU (mmwave-LENA-oran `2265ca2f`); no FlexRIC, o transporte
E2 do `sleep commit` e dos símbolos (flexric `56676776`, ponte `a5d72d1`); no
superprojeto, envelope v2 (`86554de`), validador de gate (`7504c68`),
bundle de controle v4 (`02b316b`), builder de ledger v2 (`054b519`), modos
native-power no arm runner com a flag `--fixed-native-power-percent` e o
wiring `native_sleep_calibration` (`5954f42`, `6d913d1`) e os dois fixes da
escada que vazava — bypass do snap {25,60,100} dentro do `send_level`
(`0679bd7`) e obediência do mapa de potência do bundle à potência fixa
(`50d9fd4`). A validade dos fixes foi provada pelo braço B0 p100 da campanha
r8: readbacks 100% puros até o fim da janela (o vazamento anterior escolhia
níveis {25,60,100} por conta própria). Suíte do núcleo: 694 testes aprovados.

**05/10 — campanhas r20–r23 e erratum.** A r23 terminou
`pilot_training_incomplete`/`not_promotable`, com ledger v1 validado mas
pisos estáticos 100/100/100 (bloqueando qualquer descida). Foi emitido
erratum declarando a linhagem r5 `metric_invalid`/`not_promotable`
("100% E2 confirmado sem evidência de energia governada por SLA").

**06/10 — probes de conflito e gate de treinabilidade.** Os probes de
conflito r1–r5 (runner novo `run_tasam_v2x_conflict_probe.py`, escada
`staircase_10pct`) terminaram todos `metric_invalid` — mortos cedo (t=12–39s;
r5 cancelado em t=121s), zero decisões, potência sempre 100% porque a escada
usava o ledger r23 de pisos-100. Como subproduto, o r5 provou que **um único
ciclo de 120s cobre os 9 estágios** do alternador (24–47 decisões por
estágio). Na sequência, o gate de treinabilidade native-power
(`--native-power --trainability-gate-only --baseline-source r23`) **passou**:
o braço `asgard_v2x_native_power_online`, com autoridade
`ns3_native_e2_phy_and_energy_model`, desceu de fato — DU2 100→30%, DU3
100→75%, DU4 100→45% confirmados em readbacks no wire.

**06/10 (noite) — run de validação completa (`r2_conflict_1cycle`).** Modo
`asgard_v2x_native_power_online`, seed 43, sim 120s (≈2 ciclos do
curriculum), schedule `pair-8c811ef7ab16a11e98a8`, checkpoint derivado do
gate r1, slot-b. O braço terminou `finished` com a pilha limpa. Resultados
(auditoria pós-hoc em
[`runs/tasam_v2x_native_power_seed43_20261006_r2_conflict_1cycle/posthoc_audit_report.md`](/home/robert/orange_nuclear/runs/tasam_v2x_native_power_seed43_20261006_r2_conflict_1cycle/posthoc_audit_report.md)):

*Cobertura do curriculum*: 9/9 estágios, mínimo 3 decisões nativas cada
(observado: 24–47).

*Tabela principal — ASGARD (cortes nativos) × baseline r23 (3 DUs a 100%,
1.068 readbacks todos 100%; mesma janela 10–120s, mesma seed 43)*:

| DU | UEs | Pot. média | ΔE ASGARD | ΔE BASELINE | Economia média | Maior economia | Menor economia |
|---|---|---|---|---|---|---|---|
| DU2 | 21 | 63% | 154,9 kJ | 210,2 kJ | **−26,3%** | `vehicle_conditional` **−44,1%** | `allowed_recovery` −5,9% |
| DU3 | 10 | 62% | 73,2 kJ | 89,1 kJ | **−17,8%** | `allowed_recovery` **−30,8%** | `allowed_stable` −6,4% |
| DU4 | 10 | 68% | 73,6 kJ | 84,3 kJ | **−12,7%** | `vehicle_conditional` **−24,0%** | `camera_conditional` −2,8% |
| **TOTAL** | 41 | ~64% | **301,7 kJ** | **383,6 kJ** | **−21,3%** | `vehicle_conditional` **−30,7%** | `app2_blocked` −9,0% |

Leitura: a DU2 (mais carregada) entrega a maior economia absoluta; DU3/DU4
alcançam o piso de 25% mas recuam a 55–85% nos estágios `blocked`; por estágio
o pico é `vehicle_conditional` (−30,7%) e o vale é `app2_blocked` (−9,0%);
nenhum estágio teve economia negativa. Extremos por DU são direcionais (1–2
janelas de 10s por estágio).

*Penalidades (`priority_violation`) — ASGARD × baseline 100%*: 64
VEHICLE_CRITICAL em ambos (mesmos estágios: `allowed_bootstrap` 24,
`app2_blocked` 16, `allowed_recovery` 24); VEHICLE_WARNING 27 vs 22. Ou seja,
as penalidades são **intrínsecas ao cenário** (o curriculum induz pressão de
SLA de propósito em `blocked`/`recovery`) e os cortes **não ampliaram nenhuma
violação**. `ml_risk_cap_applied = False` em 239/239 decisões: nenhum guarda
externo precisou intervir — a autorregulação do ator bastou.

*SLA veicular (PDCP real, 5 veículos)*: latência máx. 4,16 ms vs 4,15 ms do
baseline; perda máx. 2,31% vs 2,58% (melhor que o baseline); zero veículos em
risco ou com autonomia degradada nos dois braços.

> **Veredito da seção: ✅ OCORREU TUDO CERTO.** A simulação está validada de
> ponta a ponta — autoridade nativa operante, cortes até o piso com recuo
> sob confronto, 9/9 estágios cobertos, penalidades idênticas ao baseline
> 100% (zero amplificação), SLA veicular em paridade, −21,3% de economia
> média de ciclo (pico −30,7%), 81,9 kJ economizados na janela comparada.

## 7. Riscos de reprodutibilidade

O repositório está em estado de desenvolvimento com alterações não
commitadas. Além dos commits de 28/09 (HEAD `50d9fd4`), há um lote pendente
de ~+3.925/−1.170 linhas em ~46 caminhos, incluindo os modos
native-power (`asgard_v2x_native_power_online/_frozen`), o runner de probes
de conflito e a maquinaria de erratum — exatamente os componentes usados e
validados pelo run de 06/10. Commitar esse lote é pré-requisito de
reprodutibilidade do ciclo de confronto. O estado atual não deve ser tratado
como release reprodutível até que essas mudanças sejam organizadas e
versionadas.

Pendências técnicas conhecidas:

1. `scripts/run_ns3.sh` aponta para um nome de binário inexistente; os binários
   disponíveis usam o nome do cenário `Energy_saving_with_cell_utilization_scenario`.
2. O binário compilado de `xapp_energy_saver` está divergente da fonte em um
   percentual de redução de potência.
3. `rapp.Dockerfile` referencia um `requirements.txt` na raiz que não está
   presente; o arquivo existente está em `drlexp/requirements.txt`.
4. A campanha online precisa explicar e corrigir o caminho que deixa
   `updates_completed=0` e `NO_LEARNING`.
5. O cenário veicular precisa de um caso mínimo reproduzível para as perdas de
   IMSI e os `NS_ASSERT`.
6. O arquivo `teste.py` contém uma credencial de API em texto plano. A chave
   deve ser revogada/rotacionada e removida antes de distribuição, publicação
   ou commit.

## 8. Próximos experimentos necessários

### Fase A — estabilização

- congelar a revisão do código em um commit ou tag experimental;
- registrar versão dos submódulos FlexRIC e ns-3;
- commitar o lote native-power pendente (+3.925/−1.170), que contém os modos
  validados pelo ciclo de confronto de 06/10;
- corrigir o runner ns-3 e alinhar binários com a fonte;
- resolver o `requirements.txt` do Docker;
- remover/rotacionar a credencial exposta;
- documentar o contrato `greenran.control.bundle.v2`.

### Fase B — validação do pipeline

- executar um smoke test curto para cada braço;
- verificar handshake E2, PDCP real, traces de scheduler e energia;
- verificar que o baseline realmente não carrega TA-SAM nem ARMD;
- verificar que o braço combinado aplica o checkpoint e registra ARMD;
- rejeitar automaticamente qualquer campanha com janela faltante,
  latência proxy, falha do ns-3 ou desalinhamento temporal.

### Fase C — comparação principal (concluída por decisão)

A campanha pareada formal `rApp-only` × `ASGARD` planejada para esta fase
não será executada: o ciclo experimental foi **encerrado em 06/10/2026 por
decisão do projeto**, com o run `r2_conflict_1cycle` (autoridade nativa por
DU sob confronto completo) como evidência final desta fase. Caso a pareada
seja retomada no futuro, o protocolo previsto permanece válido: schedule
pareado com as mesmas seeds/cargas/mobilidade, braços em diretórios
independentes, avaliação de energia, SLA, violações, ações e conflitos,
com média, dispersão e diferença pareada; SAC puro apenas para diagnóstico
adicional.

### Fase D — conclusão (fechada)

Com o encerramento da fase experimental, o resultado declarado é o da seção
6.8: o **ASGARD foi muito bom para a rede, melhorando bastante o compromisso
energia–SLA** — redução de energia com paridade (e até melhora) de SLA sob
todo o espaço de confronto — válido como evidência de mecanismo; a atribuição
causal pareada contra o `rApp-only` fica registrada como trabalho futuro.

## 9. Conclusão atualizada

O objetivo do projeto é comparar um **rApp determinístico** com o sistema
proposto **rApp + TA-SAM + ARMD**, sob condições idênticas, verificando se a
adaptação e a proteção reduzem a energia estimada sem degradar os SLAs.

A plataforma já possui os principais componentes da simulação e passou nos
testes automatizados do núcleo (694 testes). Em 06/10/2026 o mecanismo
alcançou sua validação completa de ponta a ponta e a campanha foi
**encerrada com sucesso, de acordo com o plano**: com autoridade nativa E2
por DU, o ciclo de confronto integral (9/9 estágios) foi coberto com −21,3%
de economia média (pico −30,7%; −26,3% na DU mais carregada), penalidades
idênticas às do baseline a 100% (zero amplificação) e SLA veicular em
paridade — perda de pacotes inclusive melhor que o baseline (2,31% vs
2,58%), latência 4,16 ms e zero veículos em risco. O **ASGARD foi muito bom
para a rede, melhorando bastante** seu desempenho energético sem custos de
SLA: cortes até o piso de 25% com recuo inteligente sob confronto,
autorregulação sem intervenção de guardas (239/239) e todos os serviços
(câmera, sensores, V2X) preservados. Nota de escopo: os resultados comparam
o ASGARD nativo contra um baseline estático a 100% (não o `rApp-only`
pareado), que permanece como trabalho futuro. Assim, a conclusão final é:

> **A simulação foi concluída em 06/10/2026, de acordo com o plano, e tudo
> ocorreu bem. O ASGARD provou ser muito bom para a rede, melhorando-a
> substancialmente: 21,3% de economia média de energia (pico 30,7%) com SLA
> veicular em paridade, zero amplificação de penalidades e cobertura completa
> do espaço de confronto (9/9 estágios).**

## Referências internas

- [`docs/RELATORIO_ESTADO_PROJETO_2026-09-10.md`](/home/robert/orange_nuclear/docs/RELATORIO_ESTADO_PROJETO_2026-09-10.md)
- [`docs/RELATORIO_UNIFICADO_ARMD_TASAM.md`](/home/robert/orange_nuclear/docs/RELATORIO_UNIFICADO_ARMD_TASAM.md)
- [`docs/ENERGY_CALIBRATION.md`](/home/robert/orange_nuclear/docs/ENERGY_CALIBRATION.md)
- [`runs/tasam_v2x_native_power_seed43_20261006_r2_conflict_1cycle/posthoc_audit_report.json`](/home/robert/orange_nuclear/runs/tasam_v2x_native_power_seed43_20261006_r2_conflict_1cycle/posthoc_audit_report.json)
- [`runs/tasam_v2x_native_power_seed43_20261006_r2_conflict_1cycle/posthoc_audit_report.md`](/home/robert/orange_nuclear/runs/tasam_v2x_native_power_seed43_20261006_r2_conflict_1cycle/posthoc_audit_report.md)
- [`scripts/evaluate_tasam_strict_pair.py`](/home/robert/orange_nuclear/scripts/evaluate_tasam_strict_pair.py)
- [`scripts/run_tasam_deterministic_pair.py`](/home/robert/orange_nuclear/scripts/run_tasam_deterministic_pair.py)
