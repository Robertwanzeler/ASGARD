# Relatório técnico da simulação GreenRAN

**Data do snapshot atualizado:** 14/09/2026 (BRT)
**Snapshot histórico incorporado:** 11/09/2026 (BRT)
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

Como referência de desenvolvimento, os registros históricos do treino online
mostraram uma economia energética simulada máxima de **35,46%** (aproximadamente
36%) contra o candidato live do rApp. Em outro resumo da mesma linha
experimental, também foi registrado **12,60%**, enquanto o resumo operacional
filtrado apontou **6,75%** de economia realizada. Esses valores serão usados
como referências provisórias para orientar as melhorias seguintes, não como
validação causal do ASGARD: as campanhas terminaram com avaliação bloqueada e
sem checkpoint promovido. A divergência entre os medidores é uma pendência de
medição que precisa ser resolvida antes de transformar qualquer valor em
resultado científico.

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
| Melhor economia energética histórica no medidor | **35,46% (~36%)** | melhor referência observada no treino, ainda não causal |
| Economia energética máxima no medidor | **12,60%** | referência provisória de desenvolvimento |
| Economia energética realizada no resumo operacional | **6,75%** | estimativa filtrada, ainda não validada no par final |
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

## 7. Riscos de reprodutibilidade

O repositório está em estado de desenvolvimento com alterações não
commitadas na raiz, no FlexRIC e no ns-3. Isso inclui mudanças no cenário, nos
handlers E2SM-RC, no scheduler, no modelo de energia, no rApp e nos scripts de
campanha. O estado atual não deve ser tratado como release reprodutível até
que essas mudanças sejam organizadas e versionadas.

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

### Fase C — comparação principal

- gerar um schedule pareado com as mesmas seeds, cargas, mobilidade e duração;
- executar `rapp_only` e `combined` em diretórios independentes;
- avaliar energia estimada, SLA, violações, ações e conflitos;
- repetir para as seeds definidas no protocolo;
- apresentar média, dispersão e diferença pareada;
- usar SAC puro apenas se for necessário para diagnóstico adicional.

### Fase D — conclusão

Só declarar vantagem do método proposto se o braço combinado mostrar melhora
estatisticamente defensável no objetivo energia–SLA sem violar os critérios de
validade. Caso contrário, o artigo deve apresentar o resultado como integração
experimental, resultado inconclusivo ou evidência de trade-off, conforme o
veredito do avaliador.

## 9. Conclusão atualizada

O objetivo do projeto é comparar um **rApp determinístico** com o sistema
proposto **rApp + TA-SAM + ARMD**, sob condições idênticas, verificando se a
adaptação e a proteção reduzem a energia estimada sem degradar os SLAs.

A plataforma já possui os principais componentes da simulação e passou nos
testes automatizados do núcleo. O melhor registro histórico do medidor foi de
35,46% de economia energética simulada, mas os resultados online ainda não
formam um par causal válido: os checkpoints não foram promovidos, a evidência
nativa de atuação ainda está sendo fechada e a smoke atual apresentou erro de
caminho de socket. A DRL/TA-SAM permanece em treinamento online para melhorar
a estabilidade e tornar a economia reproduzível. A trilha veicular também
continua pendente nos critérios de completude. Assim, a conclusão técnica neste
momento é:

> **A integração está operacional em nível de desenvolvimento e já possui uma
> linha de base provisória de economia simulada, mas a hipótese principal do
> artigo ainda não foi validada por uma comparação pareada válida entre
> `rApp-only` e `ASGARD (rApp + ARMD + TA-SAM)`.**

## Referências internas

- [`docs/RELATORIO_ESTADO_PROJETO_2026-09-10.md`](/home/robert/orange_nuclear/docs/RELATORIO_ESTADO_PROJETO_2026-09-10.md)
- [`docs/RELATORIO_UNIFICADO_ARMD_TASAM.md`](/home/robert/orange_nuclear/docs/RELATORIO_UNIFICADO_ARMD_TASAM.md)
- [`docs/ENERGY_CALIBRATION.md`](/home/robert/orange_nuclear/docs/ENERGY_CALIBRATION.md)
- [`scripts/evaluate_tasam_strict_pair.py`](/home/robert/orange_nuclear/scripts/evaluate_tasam_strict_pair.py)
- [`scripts/run_tasam_deterministic_pair.py`](/home/robert/orange_nuclear/scripts/run_tasam_deterministic_pair.py)
