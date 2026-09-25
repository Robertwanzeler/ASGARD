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
- `interval_*/ns3_energy/TasamControlObservations.csv`.

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

Os manifestos registram os seguintes commits observados:

| Componente | Commit |
| --- | --- |
| `flexric` | `93cd83249dba9a0bde6956fa074b6e05000bc835` |
| `ns-O-RAN-flexric` | `fd8b0a99e9b87e3bc7be03eb62d146b4a32d4603` |
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
