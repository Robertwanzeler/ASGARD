# ASGARD — Autonomous Sustainable Green RAN

Plataforma de pesquisa O-RAN para **economia de energia em RAN 5G com garantia de SLA V2X**: simulação ns-3/mmWave controlada em loop fechado por rApp (Non-RT RIC), xApps (Near-RT RIC) e aprendizado por reforço multi-agente (TA-SAM MARL).

```
CARLA ─┐
ns-3 mmWave + agentes E2 ──SCTP──▶ FlexRIC nearRT-RIC ──▶ xApps C (slicer / energy_saver / tasam_actuator)
   │ traces CSV                        ▲                            ▲
   ▼                                   │ A1 (JSON+ACK)              │ socket Unix (bundles v2/v3/v4)
csv_to_metrics ──▶ /tmp ──▶ rapp_orchestrator (Non-RT RIC) ────────┘
   │                     ├─ RandomForest (decisão + CVaR) · GraphSAGE-ARMD (conflitos) · Judge
   └──▶ Data Lake SQLite ──▶ treino TA-SAM MARL ──▶ shadow ──▶ gate manual ──▶ controle canário
```

O atuador TA-SAM traduz bundles de controle em **E2SM-RC**: potência por célula (25–100%, passo 5%), sleep coordenado por DU (commit 0%), handover e envelope de símbolos DL discricionários — com evidência nativa (PDCP real, ack E2, readback) auditada no Data Lake.

## Estrutura

| Diretório | Conteúdo |
|---|---|
| `src/` | Plano de controle Python: orquestrador rApp, Data Lake (SQLite), ML predictor, Judge, ARMD, TA-SAM (shield/staircase/calibração), ponte CARLA, dashboards Flask |
| `ns-O-RAN-flexric/` | Simulador: fork ns-3.42 O-RAN com `mmwave` custom (energia/sleep/TA-SAM), `nr` (5G-LENA), `oran-interface` (E2), `e2sim-kpmv3` |
| `flexric/` | FlexRIC 2.0.0 (EURECOM) + xApps custom (`tasam_actuator`, `energy_saver`, `slicer`) |
| `drlexp/`, `training/`, `models/` | TA-SAM MARL (SAC multi-agente), RandomForest/GraphSAGE, pipelines e checkpoints |
| `apps/` | Apps de borda: vigilância (câmeras 4K), monitoramento (sensores), veicular |
| `config/` | Runtime, SLAs, calibrações de energia, perfis TA-SAM/V2X, políticas |
| `scripts/`, `monitoring/`, `systemd/` | Runtime completo, campanhas TA-SAM/ASGARD, watchdogs, serviços |
| `tests/` | ~122 suítes pytest (contratos, gates, SLA, campanhas) |
| `docs/` | ~45 documentos técnicos + guias de instalação/debug |

## Começo rápido

```bash
# suíte de testes (o que a CI roda)
./scripts/run_python_tests.sh

# runtime completo (RIC + ns-3 + xApps + rApp + dashboards)
./run_greenran_complete.sh

# dashboard web
# http://127.0.0.1:5000
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
