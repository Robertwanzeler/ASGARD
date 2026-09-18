# Relatório de Estado do Projeto — GreenRAN O-RAN

**Data:** 10/09/2026, 11h41 (BRT)
**Escopo:** fotografia completa do projeto "como está hoje" — arquitetura, componentes, DRL (TA-SAM), grafos (GraphSAGE/ARMD), xApps, o que funciona, o que está quebrado e pendências.

---

## 1. Sumário executivo

O GreenRAN é uma plataforma O-RAN de gerenciamento de energia/recursos com garantia de QoS para três serviços (vigilância 4K > veicular > sensores mMTC), sustentada por:

- **Simulação**: ns-3.42 (mmWave + LTE, EN-DC) com agente E2 embutido, conectado ao FlexRIC (near-RT RIC);
- **Controle**: rApp Python (Non-RT RIC) que arbitra conflitos entre xApps e comanda potência/recursos;
- **IA**: TA-SAM MARL (SAC multiagente + Sharpness-Aware Minimization) em rollout controlado e GraphSAGE/ARMD (reconstrução de conflitos, F1=1.0 congelado) como escudo de segurança;
- **Aplicações**: App1-Vigilância, App2-Monitoramento, App3-Veicular (com CARLA 0.9.16).

**Novidades da semana (6–10/set):** novo xApp **`xapp_tasam_actuator`** (aplica controle real via E2SM-RC: fatias de scheduler e potência por célula), protocolo **`greenran.control.bundle.v2`** no rApp, ns-3 recompilado com handlers novos (+593 linhas modificadas), e campanha de observação online seed 47 (v6–v11) executando atuações com escudo ARMD.

**Estado geral:** runtime operacional e em evolução ativa. Há **muito trabalho não commitado** (~30 arquivos na raiz, 14 no core do FlexRIC, cenário ns-3), uma campanha v11 concluída porém **inválida por critério de aceitação** (updates=0), e pequenas quebras conhecidas (`run_ns3.sh` apontando para binário inexistente; binário do energy_saver desatualizado em relação ao fonte).

---

## 2. Arquitetura atual

```
┌────────────────────────────────────────────────────────────────────┐
│ CARLA 0.9.16 (mock|real) ──bridge──► veículos → IMSI 16–20         │
└──────────────────────────────┬─────────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│ ns-3.42 — Energy_saving_with_cell_utilization_scenario             │
│ 1 eNB LTE (cellId 1) + 4 gNB mmWave (2–5) · 3,5 GHz · 100 MHz      │
│ 20 UEs: 3 câmeras (1–3) · 12 sensores (4–15) · 5 veículos (16–20)  │
│ Perfis de pressão: drl_article_v1, tasam_training_balanced_v1,     │
│   drl_article_conflict_forced_v1 (+fast/smoke)                     │
│ Agentes E2 embutidos (e2sim-kpmv3: E2AP v1.01 + KPM v3 + RC v1.03) │
└───────────────▲──────────────────────────────▲─────────────────────┘
        KPM 0,1s│SCTP 36421             RC Ctrl│SCTP 36421
┌───────────────┴──────────────────────────────┴─────────────────────┐
│ FlexRIC nearRT-RIC (build_e2ap_v1) — SMs em flexric_lib/           │
│ iApp com patch local: broadcast KPM 1→N xApps                      │
└───────────────▲────────────────────────────────────────────────────┘
        E42 36422│
┌───────────────┴────────────────────────────────────────────────────┐
│ xApps: xapp_slicer · xapp_energy_saver · xapp_tasam_actuator (NOVO)│
│        + vehicle_control (Python)                                  │
└───────────────▲────────────────────────────────────────────────────┘
     sockets/files│ (/tmp/sockets/*.sock, /tmp/xapp_intents/*)
┌───────────────┴────────────────────────────────────────────────────┐
│ rApp (src/rapp_orchestrator.py, ciclo 5 s, decisão em 6 estágios)  │
│ Trend → Pattern → CVaR → ML(RF) → MARL Shadow/TA-SAM → Judge       │
│ Escudos: ARMD (GraphSAGE congelado) + Safety Shield + pisos per-UE │
│ Novo: greenran_control_bundle.py → tasam_actuator (E2SM-RC real)   │
└───────────────▲────────────────────────────────────────────────────┘
                ▼
   Data Lake SQLite (/tmp/rapp_data_lake.db, WAL)
   → Dashboard Flask :5000 · Grafana :3000/3001 · InfluxDB :8086
```

---

## 3. Estado por componente

### 3.1 Simulação ns-3 (`ns-O-RAN-flexric/mmwave-LENA-oran`)

- **Cenário principal:** `scratch/Energy_saving_with_cell_utilization_scenario.cc` — **modificado (+164 linhas vs. último commit, não commitado)**.
- **Binários recompilados em 07/09** (3 variantes): `ns3.42-Energy_saving_with_cell_utilization_scenario{,-default,-debug}`.
- **Modificações de código não commitadas** (suporte a controle fino via E2SM-RC):
  - `src/mmwave/model/mmwave-enb-net-device.cc` (+264 linhas)
  - `src/mmwave/model/mmwave-flex-tti-mac-scheduler.cc` (+204 linhas — consistente com os novos parâmetros `MIN_DL_BP / MIN_UL_BP / WEIGHT_BP` do actuador)
  - `src/mmwave/model/mmwave-radio-energy-model-enb.{cc,h}`, `lte-enb-net-device.{cc,h}`, `lte-enb-mac.cc`, `component-carrier.cc`
- **Topologia:** eNB LTE no centro (2000,2000,3), 3 gNBs em círculo raio 500 m; câmeras estáticas raio ~180–215 m; sensores raio 220–270 m; 5 veículos em faixa y=2025±12 movendo-se a 2–4 m/s (geram handovers reais).
- **Tráfego DL base:** câmeras 1000 B/320 µs (25 Mb/s cada), sensores 128 B/10 ms, veículos 800 B/4 ms (1,6 Mb/s cada) ≈ 84 Mb/s.
- **Modelo de energia:** correntes DeepSleep 86,3 A / RX 138,9 A / TX 742,2 A @ 5 V — **energia é estimativa/proxy** (sem wattmeter físico; `physical_meter_available: false`).
- **Controle de célula (legado):** RC `Energy_state` (style 300) → `SetBSTX(0)` zera TxPower+NoiseFigure (deep-sleep); religamento via E2 não implementado nesse caminho.

### 3.2 FlexRIC + xApps

**RIC:** `flexric/build_e2ap_v1/examples/ric/nearRT-RIC` (19/ago), SCTP 36421 (E2) e 36422 (E42), SMs `.so` em `flexric_lib/`. Patch local no iApp: **broadcast de indicações KPM para todos os xApps subscritos** (por `ran_func_id`).

**Roster de xApps (4):**

| xApp | Papel | Estado hoje |
|---|---|---|
| `xapp_slicer` (29/ago) | Monitor SLA câmera (P95 ≥ 80 ms → CRITICAL/PRB 100%; IDLE/25%; NORMAL/50%). Lê `/tmp/xapp_metrics/metrics.json` (poll 100 ms); não assina KPM | ✅ funcional |
| `xapp_energy_saver` (29/ago) | Atuador puro: recebe comando JSON no socket; watchdog TTL 3–5 s → FULL_POWER fail-safe | ⚠️ **binário desatualizado**: fonte mudou CONDITIONAL_REDUCE 70%→60%, binário ainda tem 70% (confirmado via `strings`) — precisa recompilar |
| **`xapp_tasam_actuator` (NOVO, 06/set)** | **Atuador E2 real**: protocolo `greenran.control.bundle.v2` no socket `/tmp/sockets/tasam_control.sock` → converte em E2SM-RC Control: **style 2 (Scheduler)** = `MIN_DL_BP(1)/MIN_UL_BP(2)/WEIGHT_BP(3)`; **style 300 (Energy)** = `SET_POWER(2)` com `POWER_PERCENT(8)` por célula (`CELL_ID(7)`). Semântica transacional `PREPARE(1)/COMMIT(2)/CLEAR(3)` + `TTL_MS(6)` | ✅ compilado (10,4 MB); **ainda não documentado em .md** |
| `vehicle_control` (Python) | Controle veicular do rApp | ✅ funcional |

**Core do FlexRIC:** 14 arquivos modificados não commitados (`msg_handler_ric.c`, `rc_sm/*`, `act_proc.*`, `e42_xapp.c`, ...) — suporte ao protocolo v2 do actuador.

**Observação importante:** a mitigação no iApp (`/tmp/slicer_critical_mode` + `mitigation_enabled`) **existe mas está desativada por default** (`e42_iapp.c`); a arbitração SLICER×ENERGY hoje é 100% responsabilidade do rApp.

### 3.3 rApp (Non-RT RIC) — `src/`

- **Ciclo de decisão (5 s):** precedência de serviço (câmera 25/30 Mbps · 80/100 ms → veículo 20/10 ms · 1/0,5% → App2 85–95% · 10/5% · 90/95%) → Trend (slope OLS 5 min) → Pattern (score sazonal) → CVaR (40/120/250 ms × slope ±0,01/2,0/5,0 ms/s; ECO 25% quando CVaR<40 e P95<60) → ML RF (50 features; validação ML×real) → MARL Shadow/TA-SAM → Judge.
- **Judge determinístico:** modos `competitive` (default) e `cooperative_hierarchy` (opt-in); empate ≤ 0,02 favorece TA-SAM; ARMD só escala proteção.
- **NOVO — `greenran_control_bundle.py` (271 linhas):** contrato versionado fail-safe para o actuador TA-SAM: potência quantizada 25–100% em passos de 5%, conversão %→dBm (`30 dBm + 10·log10(pct/100)`), mapa IMSI→serviço (1–3 câmera, 4–15 sensor, 16–20 veículo), ACK/audit paths.
- **NOVO — `tasam_safety_shield.py`:** camada de segurança das atuações.
- **ARMD (`rapp_armd_runtime.py`):** carrega `runs/graphsage_article00_hybrid_final` (F1=1.0 em 10 cenários × 5 seeds); modo `assist`, confiança mínima 0,85, **só escalona severidade** (nunca reduz proteção).
- **Pisos per-UE:** ledger câmera/sensor/veículo, prioridade veicular 1,5×; baseline fixa ALLOWED 0,603/0,397 · CONDITIONAL 0,643/0,357 · BLOCKED 0,725/0,275 (RAN/AI).
- **~30 arquivos modificados não commitados** (orchestrator, judge, shadow, resource model, controlled trainer, exporters — trabalho da campanha TA-SAM em andamento).

### 3.4 DRL — TA-SAM MARL (estado atual)

**Pilha ativa** (implementação própria em PyTorch puro, sem SB3): SAC multiagente CTDE — 3 atores Dirichlet (um por DU lógico), crítico centralizado twin, entropia auto-ajustada, **SAM seletivo** (ρ escala com variância TD, agenda 0,5→0,01), cabeça ordinal de categoria (ALLOWED/CONDITIONAL/BLOCKED).

- **Topologia `greenran_fixed_marl_v1`:** 3 DUs — `du_camera_edge` (eMBB 0,85/0,10/0,05), `du_sensor_mixed` (mMTC 0,35/0,55/0,10), `du_vehicle_edge` (URLLC 0,20/0,15/0,65).
- **Estado:** 10-D por DU + 10-D global (**13-D** com `GREENRAN_TASAM_EXPLICIT_STATE_FEATURE=1` — contrato atual da campanha).
- **Ação:** simplex eMBB/mMTC/URLLC por DU → agregação por demand_share → `eMBB→r_ran`, `mMTC+URLLC→r_ai`.
- **Recompensa:** QoS ponderado (veículo 55%, câmera 25%, sensor 20%) − penalidades over-alloc/shortage/min-QoS/vehicle-SLA/resource-use/**tail-risk CVaR (EWMA 0,90 referência, alvo 120 ms)** − loss.

**Campanha de observação online — seed 47, v6→v11 (09–10/set):**

| Métrica | v10 | **v11 (mais recente)** |
|---|---|---|
| Decisões | 650 | **2400** |
| Atuações TA-SAM aplicadas | 36 | **124** |
| Potência média aplicada | — | **25% (ECO)** |
| Potência média live vs shadow | — | 641,7 W vs 630,2 W (economia ~1,9%) |
| Escudo ARMD | — | 1415 HARD_VETO · 123 ADVISORY · 862 CLEAR |
| SLA (câmera/sensor/veículo) | — | 0,9996 / 0,9987 / 0,9411 |
| Estágio final | — | `canary_50` (fração 0,5), sem rollback |
| Veredito | inválida | **inválida** — `updates=0` (treino online não produziu checkpoint promovido) |

- Perfil: `tasam_training_economic_v4`; modelo de energia `sim_native_v2_36cell`; checkpoint inicial derivado de `runs/tasam_economic_checkpoint_seed47_20260908`.
- **Interpretação:** a via de atuação "applied action v2" funciona (124 atuações com escudo vetando 59% dos ciclos), mas **nenhum update de treino online completou** — é o principal ponto a investigar.
- Contexto metodológico (`docs/RELATORIO_UNIFICADO_ARMD_TASAM.md`): no match60 local pareado, **SAC puro venceu TA-SAM por +5,4%** de retorno médio; TA-SAM mostra-se mais estável/conservador (menor variância de ação e critic loss).
- Modo operacional: shadow/`assistant_only_control` com **control gate** (promoção manual + expiração); full-control apenas em experimentos isolados.

### 3.5 Grafos — GraphSAGE / ARMD

- **Duas trilhas**: (1) GreenRAN direta — reconstrução do grafo de conflitos (link prediction, 6 decoders por relação, features 32-D, co-ocorrência alinhada à cadeia causal); (2) **article00** — autoencoder temporal fiel ao paper (P1–P7/K1–K4, correlação→threshold, conflitos direct/indirect/implicit).
- **Pacote híbrido final congelado** (`runs/graphsage_article00_hybrid_final`): **F1 = 1,0 nos 10 cenários × 5 seeds** (threshold 0,5, subset 450, epoch-alvo 200); calibração foi decisiva em `recuperacao` e `vehicle_recovery`.
- 14 scripts de experimentação + 4 suítes de teste dedicadas.

### 3.6 Apps e observabilidade

- **App1-Vigilância** (:5100, prioridade 1) — câmeras 4K RTSP, anonimização, pipeline real/mock.
- **App2-Monitoramento** (:5200, prioridade 3) — 17 sensores mMTC semânticos + `simulate_sensors.py`.
- **App3-Veicular** (:5300, prioridade 2) — consome CARLA+ns-3 via `extended_metrics.json`; ego, risk_state, autonomia.
- **Dashboard Flask** (:5000): `/conflicts`, `/xapps`, `/drl`, `/ops` + API REST completa.
- **Docker**: Grafana :3000/3001 + InfluxDB :8086; `monitor_intents.sh` na raiz.

---

## 4. O que está funcionando × o que está quebrado

**✅ Funcionando**
- Runtime completo (`run_greenran_v2.sh`), handshake E2, coletor (`csv_to_metrics.py`, PDCP real + CVaR per-UE), rApp com decisão 6 estágios, judge, ARMD assist, shadow runtime com hot-reload, campanha online com escudo (v6–v11), pipeline GraphSAGE ponta-a-ponta, apps 1/2/3.

**❌ Quebrado / desatualizado**
1. **`scripts/run_ns3.sh`** — aponta para `ns3.42-scenario-greenran-debug`, que **não existe**; usar o binário `-default` manualmente (o doc `COMANDOS_NS3_RELATORIO.md` repete esse comando antigo).
2. **`xapp_energy_saver` binário** — fonte 60% vs binário 70% (recompilar).
3. **Campanha v11 inválida** — `updates_completed=0`; gatilho de update do controlador online não disparou (investigar watermark/snapshots).
4. Bugs conhecidos do cenário ns-3: `maxEC/totalcurrEC` sempre 0 em `gnbs.txt` (iterador nunca incrementado) e `m_startTime` do helper não inicializado.

**⚠️ Higiene/repo**
- `teste.py` contém **API key em texto plano** (cliente z.ai) — **rotacionar e remover**.
- `matrix.py/` (diretório vazio) e `conjunto` (0 bytes) — lixo; remover.
- `results/` vazio; `runs/` com 353+ diretórios de experimentos (artefatos, não código).

---

## 5. Git — trabalho não versionado (risco)

- **Raiz:** ~30 arquivos `M` sem commit (núcleo do rApp, DRL, scripts da campanha) + 3 commits novos no topo (`63aaf9d`, `3dcc2db`, `3632617` "tasam: aplicar penalidade categórica direcional").
- **flexric (submódulo):** 14 arquivos `M` + **novo `examples/xApp/c/tasam_actuator/` não rastreado** + CMakeLists modificado.
- **ns-O-RAN-flexric:** cenário + 10 arquivos de modelo `M` + submódulos (`oran-interface`, `src/nr`) com dirty state.

> Recomendação: commitar em ramos temáticos (actuador v2 / campanha econômica / handlers ns-3) antes de continuar experimentos — o estado atual só existe no working tree.

---

## 6. Como rodar hoje (ordem correta)

```
RIC → ns-3 (E2 embutida conecta sozinha) → coletor → xApps → rApp
```

```bash
cd /home/robert/orange_nuclear
mkdir -p /tmp/sockets /tmp/xapp_intents /tmp/xapp_metrics

# 1) RIC
export LD_LIBRARY_PATH=$PWD/flexric/build_e2ap_v1/src/ric:$PWD/flexric_lib:$PWD/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
./flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/ 2>&1 | tee /tmp/ric.log

# 2) ns-3 (binário -default; run_ns3.sh está quebrado)
cd ns-O-RAN-flexric/mmwave-LENA-oran
./build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default \
  --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=600 2>&1 | tee /tmp/ns3.log

# ✅ Checkpoint handshake E2:
grep -E "E2 node connected|SETUP-REQUEST|SETUP-RESPONSE|Registered E2 Nodes" /tmp/ric.log /tmp/ns3.log

# 3) Coletor (alimenta slicer e rApp)
python3 src/csv_to_metrics.py

# 4) xApps (cada um num terminal, com o LD_LIBRARY_PATH acima)
GREENRAN_SLICER_SOCKET_PATH=/tmp/sockets/slicer.sock \
  ./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer
GREENRAN_ENERGY_SOCKET_PATH=/tmp/sockets/energy_saver.sock \
  ./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver
./flexric/build_e2ap_v1/examples/xApp/c/xapp_tasam_actuator   # socket tasam_control.sock

# 5) rApp (o juiz)
python3 src/rapp_orchestrator.py --interval 5
```

- Suíte completa automática: `./scripts/run_greenran_v2.sh` · validação 60 s: `./test_cenario.sh` · parar tudo: `./stop_all.sh`.
- Pipeline de conflitos + GraphSAGE: `bash scripts/run_ns3_graphsage_pipeline.sh --scenario <slug>` (11 slugs: `baseline_saude`, `app1_throughput`, `app1_latencia`, `app2_degradado_leve/critico`, `conflito_implicito`, `recuperacao`, `vehicle_warning/critical/implicito/recovery`).

---

## 7. Pendências priorizadas

| # | Ação | Prioridade |
|---|---|---|
| 1 | Commitar working tree (raiz, flexric, ns-3) em ramos temáticos | 🔴 Alta |
| 2 | Investigar `updates=0` da campanha online (gatilho/watermark do controlador) | 🔴 Alta |
| 3 | Rotacionar API key exposta em `teste.py` e remover o arquivo | 🔴 Alta |
| 4 | Recompilar `xapp_energy_saver` (alinhar 60%) e regenerar binários do FlexRIC | 🟡 Média |
| 5 | Corrigir `scripts/run_ns3.sh` (apontar para `-default`) e atualizar `COMANDOS_NS3_RELATORIO.md` | 🟡 Média |
| 6 | Documentar formalmente o protocolo `control.bundle.v2` + tasam_actuator (este relatório inicia) | 🟡 Média |
| 7 | Remover lixo (`matrix.py/`, `conjunto`) | 🟢 Baixa |
| 8 | Corrigir bugs menores do cenário ns-3 (gnbs.txt maxEC/totalcurrEC, m_startTime) | 🟢 Baixa |

---

## 8. Timeline recente

| Data | Evento |
|---|---|
| 12–29/ago | Migração dos xApps para arquitetura "atuador puro" (G3); binários slicer/energy_saver |
| 31/ago | Commit "atualizar xApps"; doc `COMANDOS_NS3_RELATORIO.md`; fonte do energy_saver muda para 60% |
| 06/set | **Fonte + binário do `xapp_tasam_actuator`** (control bundle v2 → E2SM-RC) |
| 07/set | **ns-3 recompilado** (3 variantes) com handlers novos (scheduler + potência via RC) |
| 08/set | Checkpoint econômico seed 47 (`tasam_economic_checkpoint_seed47_20260908`) |
| 09–10/set | Campanha de observação online v6→v11 (seed 47, perfil econômico v4) — 124 atuações na v11, canary_50, sem rollback, inválida por updates=0 |
| 10/set 11:41 | Estado atual: nenhum RIC/ns-3 no ar; `csv_to_metrics.py` + `rapp_orchestrator.py --synthetic` ainda rodando de sessão anterior |

---

## 9. Anexos

**Contratos versionados ativos:** `greenran.control.bundle.v2` / `greenran.control.ack.v2` · `greenran.tasam_online_control_state.v2` · `greenran.tasam_online_arm.v1` · `greenran.tasam.online_observation.v1` · `greenran.conflict_graph.v1` · `greenran.article00_*` · `greenran.tasam_drl_tracks.v1`.

**Trilhas DRL oficiais** (`config/tasam_drl_tracks.json`): `greenran_tasam` (shadow_only) · `tasam_article_reproduction` · `tasam_reference_base`. Removidas (legado): EE-DRL-RA (A3C/SBiLSTM), SAC/AWAC single-agent, CAORA.

**Referências:** `docs/DOCUMENTACAO_GREENRAN.md` (doc operacional) · `docs/RELATORIO_UNIFICADO_ARMD_TASAM.md` (resultados ARMD×TA-SAM) · `docs/README_SIMULATION.md` (patch broadcast KPM) · `drlexp/README.md` (trilhas TA-SAM).
