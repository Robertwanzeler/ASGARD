# O-RAN Simulation - Resumo do Projeto

## Estado Atual: ✅ FUNCIONANDO

A simulação está rodando com sucesso! Ambos os xApps (Slicer e Energy Saver) estão recebendo métricas KPM simultaneamente.

---

## O que foi implementado

### 1. Correção do iApp (Intermediate App)

**Problema identificado:** O iApp do flexric enviava indicações KPM apenas para UM xApp, mesmo quando múltiplos xApps subscreviam ao mesmo serviço.

**Solução implementada:** Modificamos o código para fazer broadcast das indicações KPM para TODOS os xApps subscritos ao mesmo RAN function ID.

**Arquivos modificados:**
- `flexric/src/ric/iApp/xapp_ric_id.h` - Estrutura modificada para suportar lista de xApps
- `flexric/src/ric/iApp/map_ric_id.c` - Nova função `find_all_xapps_map_ran_func()`
- `flexric/src/ric/iApp/msg_handler_iapp.c` - Handler de indicação modificado para broadcast

---

## Como executar a simulação

### Opção 1: Script automático
```bash
cd /home/robert/orange_nuclear
./run_simulation.sh
```

### Opção 2: Execução manual

1. **Iniciar RIC:**
```bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
./flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/
```

2. **Iniciar ns3 (em outro terminal):**
```bash
cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran
./build/scratch/ns3.42-scenario-zero-default --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=60
```

3. **Iniciar xApp Slicer (em outro terminal):**
```bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/
```

4. **Iniciar xApp Energy Saver (em outro terminal):**
```bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/
```

---

## Cenário atual

- **Cenário usado:** `scenario-zero` (padrão do repositório mmwave-LENA-oran)
- **Componentes:** 3 gNBs mmWave + 5 UEs
- **Interface E2:** Ativada com KPM e RC
- **xApps em execução:** Slicer + Energy Saver

---

## Monitoramento dos logs

```bash
# Ver logs do xApp Slicer
tail -f /tmp/xapp_slicer.log

# Ver logs do xApp Energy Saver
tail -f /tmp/xapp_energy.log

# Ver logs do ns3
tail -f /tmp/ns3.log

# Ver logs do RIC
tail -f /tmp/ric.log
```

---

## Arquivos importantes

| Arquivo | Descrição |
|---------|-----------|
| `run_simulation.sh` | Script para iniciar toda a simulação automaticamente |
| `flexric/src/ric/iApp/msg_handler_iapp.c` | Código modificado para broadcast de KPM |
| `ns-O-RAN-flexric/mmwave-LENA-oran/scratch/scenario10_oran.cc` | Novo cenário em desenvolvimento (ainda não funcional) |

---

## Próximos passos (se necessário)

1. **Cenário com câmeras:** O arquivo `scenario10_oran.cc` foi criado mas ainda tem problemas de configuração com o ns-3 mmwave EPC. Pode ser necessário mais debug.

2. **Testes adicionais:** Verificar se as decisões dos xApps estão sendo aplicadas corretamente na rede.

3. **Integração com outros serviços:** Adicionar outros xApps ou serviços RIC.

---

## Compilação do flexric (se precisar)

```bash
cd /home/robert/orange_nuclear/flexric/build_e2ap_v1
make -j4 nearRT-RIC xapp_slicer xapp_energy_saver
```

---

**Data da última modificação:** 14/03/2026
**Status:** ✅ FUNCIONANDO
