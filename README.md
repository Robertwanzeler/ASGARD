# ASGARD — Autonomous Sustainable Green RAN

![CI](https://github.com/Robertwanzeler/ASGARD/actions/workflows/ci.yml/badge.svg)
![versão](https://img.shields.io/badge/vers%C3%A3o-3.36-blue)

## O que é o ASGARD

O ASGARD é um **piloto O-RAN completo de autonomia energética**: um rApp (Non-RT RIC) orquestra aprendizado por reforço multi-agente (TA-SAM — SAC com atores Dirichlet e otimização *sharpness-aware*) que decide, epoch a epoch, a potência e o sleep de cada célula mmWave — e atua essa decisão via contratos versionados (`greenran.control.bundle v2/v3/v4`) em um xApp atuador que a traduz em **E2SM-RC nativo** no simulador ns-3.

O que o distingue não é a IA, mas a **arquitetura de segurança que a cerca**: juiz determinístico, guardiã ARMD que só escala severidade, escudo de SLA por UE, transações fail-closed com TTL e fallback `FULL_POWER`, e uma cadeia de promoção **shadow → gate manual → canário 10% → controle**. Nenhuma transição vale sem evidência nativa: PDCP real, ack E2, readback do rádio e auditoria assinada (sha256).

## O que ele faz

1. **Fecha o loop O-RAN completo** — KPM (CU-CP/CU-UP/DU) sobem do ns-3 pelo E2/SCTP real; a decisão desce pelo E2 até o rádio. Nada é mock.
2. **Decide com MARL, executa com contrato** — potência 25–100% (quantização de 5%), sleep por DU (`SLEEP_COMMIT`), envelope de símbolos DL discricionários, reserva GBR veicular no scheduler.
3. **Protege por construção** — escudo de SLA por UE (pode forçar 100%), piso SLA-only em máquina de estados, envelope dinâmico com ledger assinado, guarda `real_only` (proíbe proxy/stale).
4. **Promove política com disciplina** — shadow (não atua) → readiness `shadow_outperforming` → aprovação manual com expiração → canário 10% com rollback → controle; medidor de aprendizado que só pontua decisão aplicada.
5. **Audita tudo** — `TasamControlObservations.csv`, ack/readback nativos, `sleep_calibration_evidence.json`, contratos científicos validados na CI.
6. **Experimenta com rigor** — arm runner de braços A/B (`rapp_only`, `combined`, `fixed_100_native`, `native_sleep_calibration`), seeds, avaliação pareada, orçamentos cgroup-v2.

## Arquitetura

```
CARLA ─┐
ns-3 mmWave + agentes E2 ──SCTP──▶ FlexRIC nearRT-RIC ──▶ xApps C
   │ traces CSV                        ▲                     │
   ▼                                   │ A1 (JSON+ACK)       │ socket Unix + bundles v2/v3/v4
csv_to_metrics (1 s) ──▶ /tmp ──▶ rapp_orchestrator (Non-RT, 5 s)
   │                     ├─ RandomForest + retreino 2×/dia   ├─ Judge determinístico
   │                     ├─ ARMD (GraphSAGE congelado)       ├─ TA-SAM shadow evaluator
   └──▶ Data Lake SQLite ──▶ treino TA-SAM MARL ──▶ promoção em fases ──▶ controle
```

### Camada 1 — Simulação nativa (`ns-O-RAN-flexric/`)

Fork ns-3.42 O-RAN (`mmwave` + `nr`/5G-LENA + `sionna` + `oran-interface`/e2sim) com instrumentação própria:

- **Autoridade TA-SAM v4 no device mmWave**: fila `PendingTasamControl` (PREPARE/COMMIT/CLEAR/POWER) processada na thread da simulação — ack de transporte ≠ estado do rádio
- **Envelope de símbolos** no scheduler flex-TTI: contabilidade *mandatory vs discretionary vs withheld*, políticas por UE (`minDlShareBp`), reserva GBR veicular com telemetria
- **Modelo de energia nativo por eNB** (`mmwave-radio-energy-model-enb`) com registro causal dos cortes de potência
- **E2 de verdade**: KPM CU-CP/CU-UP/DU via E2AP/SCTP (ASN.1), traces nativos `Tasam*`/`Vehicle*` por epoch
- Contexto veicular 2D via ponte CARLA (`carla_bridge` + mapper IMSI↔veículo)

### Camada 2 — Near-RT RIC (`flexric/`)

FlexRIC 2.0.0 (EURECOM, E2AP v2) + xApps em C:

- `tasam_actuator` — escuta socket Unix, valida e traduz bundles v2/v3/v4 em **E2SM-RC** (estilos scheduler/mobility/energy; `SET_POWER`, `SLEEP_COMMIT`, `HANDOVER`)
- `energy_saver`, `slicer`, vehicle safety — ciclo de vida gerenciado pelo rApp com cgroups v2

### Camada 3 — Non-RT RIC (`src/`)

O orquestrador (`rapp_orchestrator`, loop de 5 s) compõe especialistas independentes:

| Componente | Papel |
|---|---|
| Data Lake (SQLite, 25+ tabelas) | Persistência auditável: métricas, decisões, observações TA-SAM, conflitos, outcomes |
| RandomForest (`rapp_ml_predictor`) | Decisão runtime + previsão de CVaR, retreino 2×/dia via manifest |
| ARMD (`rapp_armd_runtime`) | GraphSAGE congelado classifica cenário (ALLOWED/CONDITIONAL/BLOCKED) — **só pode escalar severidade**, confiança mínima 0.85 (treinado sobre grafos tipados do sistema — ver seção ARMD) |
| TA-SAM shadow (`rapp_marl_shadow`) | Avalia política MARL congelada contra o alocador vivo **sem atuar** |
| Judge (`rapp_judge`) | Arbitragem determinística: prioridades de rede, ordem de segurança, reward V2X adaptativo |
| A1 + AgentOpenRAN | Políticas JSON com ACK; intenções do usuário viram políticas técnicas |
| XAppManager | Ciclo de vida dos xApps com orçamentos cgroup-v2 |

### ARMD — o guardião que nasce dos grafos

O ARMD não é um classificador treinado em métricas soltas: ele é treinado sobre **grafos do próprio sistema**, construídos em duas frentes:

- **Protocolo `article00`** — séries temporais + referência de grafo por seed (10 seeds: 42–47, 52, 53, 61, 62), com reconstrução temporal e de adjacência
- **Datasets de conflitos** (subsets 50/150/450) — nós tipados (`agent / parameter / kpi / service / mitigation / arbiter`) ligados por arestas de interferência, com features de pressão P1–P7 e KPIs K1–K4

O encoder é **GraphSAGE temporal em PyTorch puro** (sem DGL/PyG): aprende reconstruindo as séries (MSE) e **reconstruindo a própria adjacência do grafo** por correlação com threshold — ou seja, aprende a estrutura do sistema, não só seus valores. Um **link predictor** sobre o mesmo encoder detecta conflitos **diretos, indiretos e implícitos** entre agentes de IA: arestas que ainda nem aconteceram.

Em produção, o pacote híbrido roda **congelado** (`rapp_armd_runtime`): classifica o cenário vivo (ALLOWED/CONDITIONAL/BLOCKED) como assistente do rApp, com confiança mínima 0.85 e autoridade assimétrica — **só pode escalar severidade, nunca reduzir**. As adjacências que a GNN aprendeu ficam visíveis no dashboard `/conflicts`: arestas "quentes" confirmadas vs. baixo suporte.

### Camada 4 — Infraestrutura auditável

- **Orçamentos físicos cgroup-v2** por grupo: `simulator`, `ric_xapps`, `rapp_armd`, `tasam`, `collectors`
- **Arm runner** de braços reprodutíveis (train_no_armd / rapp_only / combined / fixed_100_native / native_sleep_calibration) com provenance do binário ns-3
- **Dispatcher autônomo** de campanhas + unidades systemd (piloto causal, calibração de energia, autochain v10)
- IPC por sockets Unix + arquivos JSON com TTL e ack; portas E2 validadas sem colisão

## Cadeia de segurança

| Mecanismo | Garantia |
|---|---|
| Default `shadow_only` | MARL nunca atua sem promoção explícita |
| Gate manual (`rapp_marl_control_gate`) | Aprovação humana com validade/expiração; exige readiness `shadow_outperforming` |
| Trial canário (`rapp_control_trial`) | 10% do tráfego, rollback guard automático |
| Escudo de SLA por UE (`tasam_safety_shield`) | Última linha de defesa — pode substituir a ação por 100% |
| Piso SLA-only (`tasam_sla_floor`) | Máquina de estados: 100%→25%, passos de 10%, 3 janelas sadias, teto 1.15× |
| Envelope dinâmico (`tasam_dynamic_floor`) | Descida de 5 pp só após 5 janelas sadias; recuo de 10 pp; **ledger assinado sha256** |
| TTL + watchdog (`energy_command_protocol`) | Comando sem ack em 5 s → fallback `FULL_POWER` no xApp |
| Guarda `real_only` | Transições válidas exigem PDCP real — sem proxy, sem stale |
| Calibração nativa de sleep | Transação fail-closed drain→commit→readback, timeout 720 s |
| Medidor de aprendizado (`tasam_learning_meter`) | Só decisão **aplicada** pontua; shadow/rejeitada não conta |

## O loop, passo a passo

1. ns-3 exporta KPM e traces nativos por epoch (associação RRC, PDCP, SINR veicular)
2. `csv_to_metrics` (1 s) consolida com contexto CARLA → `/tmp` e Data Lake
3. Orquestrador (5 s) compõe: padrões, tendência, previsão RF, veredito ARMD, avaliação shadow TA-SAM
4. **Judge** arbitra as propostas por prioridade de rede e emite a decisão
5. Decisão vira bundle versionado → `tasam_actuator` → **E2SM-RC** no rádio simulado
6. Evidência volta: ack, readback, observações nativas, outcome do Judge → Data Lake
7. Treino offline consome o Data Lake → novo checkpoint → ciclo de promoção recomeça

## Estrutura

| Diretório | Conteúdo |
|---|---|
| `src/` | Plano de controle Python (orquestrador, Data Lake, ML, Judge, ARMD, TA-SAM, CARLA) |
| `ns-O-RAN-flexric/` | Simulador ns-3.42 O-RAN instrumentado (mmwave, nr, oran-interface, e2sim) |
| `flexric/` | FlexRIC 2.0.0 + xApps C custom |
| `drlexp/`, `training/`, `models/` | TA-SAM MARL, RandomForest/GraphSAGE, pipelines e checkpoints |
| `apps/` | App1 vigilância (4K), App2 monitoramento (sensores), App3 veicular |
| `config/` | Runtime, SLAs, calibrações de energia, perfis TA-SAM/V2X |
| `scripts/`, `monitoring/`, `systemd/` | Runtime, campanhas, watchdogs, serviços |
| `tests/` | ~122 suítes pytest (contratos, gates, SLA, campanhas) |
| `docs/` | ~45 documentos técnicos + [guia de instalação/debug](docs/GUIA_INSTALACAO_E_DEBUG.md) |

## Começo rápido

```bash
# suíte de testes (o que a CI roda)
./scripts/run_python_tests.sh

# runtime completo (RIC + ns-3 + xApps + rApp + dashboards)
./run_greenran_complete.sh

# dashboard web: http://127.0.0.1:5000
```

Instalação e debug do stack: [docs/GUIA_INSTALACAO_E_DEBUG.md](docs/GUIA_INSTALACAO_E_DEBUG.md) · [MANUAL_INSTALACAO_ORAN.md](MANUAL_INSTALACAO_ORAN.md) · [GUIA_DEBUG_NS3_FLEXRIC.md](GUIA_DEBUG_NS3_FLEXRIC.md)

## CI

`greenran-ci` (GitHub Actions): pytest, `compileall`, `bash -n` em todos os shell scripts, validação de contratos científicos e de `config/*.json`.

## Licença e origem

O stack de terceiros está vendido como diretórios comuns neste repositório (snapshot de trabalho):

- `ns-O-RAN-flexric/` — fork ns-3.42 O-RAN (inclui `mmwave-LENA-oran`, `e2sim-kpmv3`, `contrib/oran-interface`, `src/nr`)
- `flexric/` — FlexRIC 2.0.0 (EURECOM) com xApps custom
- `ns3-base/` — **não incluído**: ns-3 upstream vanilla, sem customização. Re-clonável com:
  `git clone https://gitlab.com/nsnam/ns-3-dev.git ns3-base`

Os créditos e licenças de cada componente permanecem nos repositórios originais (Orange-OpenSource, MinaYonan123, EURECOM/mosaic5g, nsnam). Uso acadêmico e de pesquisa; segurança conforme [SETUP_SECURITY.md](SETUP_SECURITY.md).
