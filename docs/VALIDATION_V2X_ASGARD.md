# Validação V2X estrita e promoção do ASGARD

## Estado do gate

O repositório agora trata a validação como uma cadeia fail-closed:

1. o dispatcher valida a versão do módulo importado, o hash do código e o
   binário ns-3 efetivamente executado;
2. a campanha `vehicle_feasibility` só aceita métricas de PDCP nativo;
3. o perfil GBR veicular exige os IMSIs 16–20, QCI
   `GBR_V2X_MESSAGES`, GBR/MBR por UE e o marcador do scheduler;
4. ASGARD só pode ser enfileirado depois de um manifesto de baseline
   aprovado e com `final_metrics` preenchido no checkpoint pai.

O smoke de 19/09/2026 registrou evidência nativa para os cinco IMSIs, zero
linhas proxy, perda máxima de 0,1673%, P95 máximo de aproximadamente 1,21 ms,
598 PDUs transmitidas por UE e o marcador `[V2X_GBR_PRIORITY]` no log do
ns-3. O resultado está no [relatório do smoke r8](../runs/tasam_vehicle_feasibility_seed47_20260919_gbr_priority_smoke_r8/campaign_report.json).
O resultado do smoke não é uma aprovação científica: ele cobre uma
janela curta e serve apenas para verificar a integração.

A matriz científica continua pendente. Ela deve executar os intervalos
4000, 6000, 8000, 12000 e 16000 µs, com 30 janelas consecutivas por intervalo.
Se nenhum intervalo cumprir perda `<1%`, P95 `<20 ms`, pelo menos 500 PDUs por
UE/janela e origem PDCP real, o resultado correto é
`baseline_infeasible` e o ASGARD permanece bloqueado.

## Artefatos e contratos

Cada campanha cria, no próprio diretório, os seguintes artefatos:

- `campaign_manifest.json`: perfil, seed, intervalo, hashes e motivo do gate;
- `campaign_report.json`: validade, SLA, resultado por UE/janela e rejeição;
- `interval_*/ns3_energy/E2NodeManifest.json`;
- `interval_*/ns3_energy/TasamAssociationTrace.csv`;
- `interval_*/ns3_energy/TasamControlObservations.csv`;
- nas campanhas `window90`: `arm/ns3_energy/VehicleCellSinrTrace.csv`,
  `VehicleLinkTrace.csv`, `VehiclePdcpPduTrace.csv` e
  `VehicleSchedulerTrace.csv` (traces veiculares por braço).

## Cadeia formal window90 (26/09/2026)

A campanha formal (`scripts/run_tasam_v2x_energy_r5.py`) é uma cadeia de
quatro estágios com contratos fail-closed:

1. **Gate de treinabilidade** (30 s de sim): exige transição nativa
   econômica completa, PDCP real e cobertura das células 2/3/4 por
   sequência. Reusável por hash via `--reuse-trainability-gate-root`
   (o hash vincula binário + contrato; qualquer mudança no binário
   invalida o reuso).
2. **Piloto de treino**: baseline de referência + braço ASGARD online
   (RL + Judge). O champion ativo é congelado no fim.
3. **Freeze**: cópia do checkpoint ativo para `frozen/asgard` com
   `selection_manifest.json` (shas de origem e destino).
4. **Pareada frozen**: `rapp` (referência) vs `asgard` (campeão congelado,
   sem aprendizado) sob o mesmo `pairing_schedule` — a evidência causal
   do Δ energia. O piloto de treino não promove: `checkpoint.role` fica
   em `pilot_only_non_promotable` por design.

### Baseline congelado (política de reuso)

O baseline é computado **uma vez** e fica fixo. O baseline oficial da
seed 43 é `runs/tasam_v2x_energy_pair_seed43_20260925_r26/training`
(seleção 90/90, RTF 0,0201, ledger validado {2:60, 3:60, 4:60}, 49
sequências confirmadas). O driver repassa `--baseline-source` ao piloto,
que valida manifest sha + `replay_90` + perfil antes de aceitar e grava
a proveniência (shas) no próprio report. Sem o flag, o piloto mantém o
comportamento autocontido (baseline próprio).

### Ledger de piso seguro (fail-closed na pareada)

A escada de energia só corta potência com o
`safe_power_floor_ledger.json` derivado de readbacks nativos do baseline
(`status=validated`). Sem o ledger, o braço é *rejeitado por design*
para o caminho full-power. O treino sempre recebeu o ledger via piloto;
a pareada do driver não recebia — a formal r6g (26/09) registrou
**130/130 sequências em failsafe** no braço congelado (zero cortes,
100% de potência) contra a referência cortando a 60%, invalidando a
comparação. Correção: `_arm_command` recebe `safe_power_floor_ledger` e
o estágio pareado injeta `training/baseline/safe_power_floor_ledger.json`
com validação fail-fast (arquivo existente e `status=validated`).

### Relógio do Judge (contrato de observação)

A observação atrasada do Judge roda em `run()` **antes** do envio E2 do
ciclo, então o carimbo de tempo simulado precisa existir já na
**construção** da decisão (`make_decision` grava `decision['sim_time_s']`
a partir do row da Data Lake). Fontes tardias (camera_metrics fixo em
0.0, stamp no send) deixam `current_sim=None`, o TTL de 5 s simulados
nunca vence e os pendentes só fecham no drain do desligamento — evidência
r29: 45 outcomes (ids 1-3 e calda), seleção 20/90; após o fix (r31/r6g):
183+ outcomes consecutivos desde o primeiro ciclo.

### Atomicidade da transação E2 (hardening futuro)

O gating POWER-exige-COMMIT foi implementado e **revertido**: nas células
2/4 o `PrepareTasamSchedulerPolicy` falha legitimamente (o IMSI da
política está anexado na célula 3) e o gating pulava o POWER dessas
células, destruindo a cobertura de 3 células por sequência que o
contrato v6 exige (gate: `native_proof=False` com 15 sequências aceitas).
A aplicação incondicional por célula permanece, com rejeição pós-fato
via `ClearTasamControl`. O hardening de atomicidade (por célula, sem
quebrar o fan-out) fica registrado para a Fase 2.

Depois da aprovação da baseline, a comparação congelada é executada por
`scripts/run_tasam_asgard_paired_campaign.py` e avaliada por
`scripts/evaluate_asgard_paired_campaign.py`. O evaluator exige exatamente
15 pares (`seed ∈ {45,46,47}` × cinco repetições), calcula o IC t pareado de
95% para `energia_ASGARD - energia_rApp-only` e só retorna `approved` quando o
limite superior do IC é estritamente menor que zero. Checkpoints sem
`final_metrics`, replay importado ou pai promovido são bloqueados antes da
execução.

O controle dessa comparação é rapp_only_actuating: a rApp determinística atua
pelo mesmo caminho E2 do ASGARD, mas com ARMD e TA-SAM desligados. Assim, as
duas pontas precisam apresentar ACK/readback nativo para cada ação; a diferença
experimental é o ASGARD, não a existência do atuador.

O validador pode ser executado com:

```bash
python3 scripts/validate_scientific_contracts.py config
```

O runner oficial de testes é:

```bash
python3 scripts/run_python_tests.sh
```

Ele coleta os testes `unittest` existentes e os testes funcionais, falhando
quando um arquivo `test_*.py` não produz nenhum teste.

## Proveniência congelada da campanha atual

Os manifestos registram os seguintes commits observados (atualizados em
26/09/2026):

| Componente | Commit |
| --- | --- |
| `flexric` | `af9510334bd3363b01432127330fbe05cfd22ba1` |
| `ns-O-RAN-flexric` | `ded5aa046ca3d41fdb6956161173f44d0015d2c1` (mmwave-LENA-oran @ `70bf40f9`) |
| `ns3-base` | `013ab259d8d8646e7f91783c2e1cd4fae4f3e08a` |

Esses valores são registrados na proveniência dos artefatos; a promoção exige
que a árvore de submódulos usada na campanha seja fixada nesses commits.

## Separação operacional

- ARMD é a linha operacional madura e congelada.
- `article00` permanece experimental e não é fonte de métricas oficiais.
- TA-SAM/ASGARD só entra em promoção controlada após baseline viável,
  checkpoint pai aprovado, ACK E2 e comparação pareada completa.

Nenhum resultado atual declara vitória do ASGARD. A comparação com
`rApp-only` (seeds 45, 46 e 47, cinco repetições pareadas) permanece
condicionada ao gate de baseline e deverá avaliar energia integrada, SLA,
rollback, ACK, starvation, retorno e intervalo de confiança pareado.

## Limpeza de artefatos

A limpeza foi precedida por manifestos versionados e preservou checkpoints,
manifests, relatórios, bancos finais e métricas consolidadas. Traces, replay
buffers e logs reproduzíveis foram compactados em arquivos recuperáveis sob
`runs/*/.greenran_compacted/`.

Os manifestos de auditoria e compactação estão em:

- `runs/cleanup_audit_20260919T154757Z/cleanup_manifest.json`;
- `runs/cleanup_audit_20260919T155307Z/compaction_manifest.json`;
- `runs/cleanup_audit_20260919T155623Z/compaction_manifest.json`.
- `runs/cleanup_audit_20260919T182936Z/compaction_manifest.json` (auditoria
  final: a meta de espaço já estava atingida; nenhuma remoção adicional foi
  autorizada).

Após a compactação, havia aproximadamente 25,4 GiB livres. Nenhuma remoção
de artefato científico foi feita sem manifesto.

## Segurança operacional

Os endpoints mutáveis das aplicações usam `GREENRAN_API_TOKEN` quando há
exposição não local; os binds padrão são `127.0.0.1`. Credenciais de InfluxDB
não possuem mais valores padrão no código e devem ser fornecidas por
`GREENRAN_INFLUXDB_USER` e `GREENRAN_INFLUXDB_PASSWORD`. Qualquer chave que
tenha sido exposta antes desta mudança precisa ser revogada no serviço que a
emitiu; essa revogação externa não pode ser realizada pelo repositório.
