# GreenRAN O-RAN Project Progress

## Data: 2026-03-18

## Status: ✅ FUNCIONANDO!

## Resumo

Sistema GreenRAN O-RAN completo com:
- nearRT-RIC (E2AP v1)
- ns-3 com cenário mmWave/LTE
- xApp Energy Saver coletando métricas KPM
- Métricas PDCP em tempo real

## Cenário: scenario-greenran.cc

Baseado no scenario-zero.cc com modificações:
- 1 torre LTE + 1 torre mmWave
- 6 UEs: 3 câmaras (25 Mbps) + 3 UEs (1-3 Mbps)
- Tráfego downlink: Remote Host → UEs via EPC
- EnableE2PdcpTraces() habilitado

## Métricas KPM Funcionando

| Dispositivo | Volume | Bitrate | Delay | Status |
|-------------|--------|---------|-------|--------|
| CAMERA 1 | 3489 bytes | 34898 kbps | 5.8 ms | CARGA_NORMAL |
| CAMERA 2 | 7671 bytes | 76713 kbps | 2.5 ms | CARGA_NORMAL |
| CAMERA 3 | 1613 bytes | 16139 kbps | 0.05 ms | CARGA_NORMAL |
| UE 1 | 1613 bytes | 16139 kbps | 0.05 ms | CARGA_NORMAL |
| UE 2 | 4192 bytes | 41920 kbps | 2.4 ms | CARGA_NORMAL |
| UE 3 | 5114 bytes | 51142 kbps | 6.0 ms | CARGA_NORMAL |

## Correções Feitas

### Timeout dos xApps
- `sync_ui.c`: Timeout de 5s → 15s
- `msg_handler_xapp.c`: Timeout de 5s → 15s

### xApp Energy Saver
- Parsing de métricas: DRB.PdcpSduVolumeDl, etc.
- Classificação CAMERA/UE por amf_ue_ngap_id
- Lógica de diagnóstico e ações

### ns-3
- EnableE2PdcpTraces() adicionado
- Configuração de rotas corrigida
- Tráfego downlink Remote Host → UEs

## Como Executar

```bash
cd ~/orange_nuclear
./run_greenran.sh
```

Ou manualmente:
```bash
# Terminal 1: RIC
export LD_LIBRARY_PATH=flexric/build_e2ap_v1/src/ric:flexric_lib:flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
nohup flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/ > /tmp/ric.log 2>&1 &

# Terminal 2: xApp
nohup flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp.log 2>&1 &

# Terminal 3: ns-3
cd ns-O-RAN-flexric/mmwave-LENA-oran
./build/scratch/ns3.42-scenario-greenran-debug --e2TermIp=127.0.0.1 --simTime=120
```

## Issues Conhecidos

1. **Classificação CAMERA/UE**: A ordem no xApp depende do amf_ue_ngap_id, não da posição no cenário. Algumas câmaras aparecem como "UE" e vice-versa.

2. **Timeout do xApp**: Após ~25 ciclos, o xApp Energy Saver pode expirar. Para uso prolongado, aumentar o timeout.

## Próximos Passos

1. Corrigir classificação CAMERA/UE usando IDs fixos
2. Aumentar timeout do xApp para simulações longas
3. Adicionar xApp Slicer para orquestração de slices
4. Implementar ações de controle (Energy Saving)

## Arquivos Principais

- `scratch/scenario-greenran.cc` - Cenário ns-3
- `examples/xApp/c/energy_saver/xapp_energy_saver.c` - xApp Energy Saver
- `src/xApp/sync_ui.c` - Timeout corrigido
- `run_greenran.sh` - Script de execução
