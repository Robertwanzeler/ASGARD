# O-RAN Simulation Project - Orange Nuclear

## Estado Atual: ✅ FUNCIONANDO

A simulação está rodando com sucesso! Ambos os xApps (Slicer e Energy Saver) estão recebendo métricas KPM simultaneamente.

---

## ✅ Implementado

### 1. Correção do iApp (Intermediate App)
**Problema:** O iApp do flexric enviava indicações KPM apenas para UM xApp, mesmo quando múltiplos xApps subscreviam ao mesmo serviço.

**Solução:** Modificamos o código para fazer broadcast das indicações KPM para TODOS os xApps subscritos ao mesmo RAN function ID.

**Arquivos modificados:**
- `flexric/src/ric/iApp/xapp_ric_id.h`
- `flexric/src/ric/iApp/map_ric_id.c` 
- `flexric/src/ric/iApp/msg_handler_iapp.c`

### 2. Cenário ns-3 com Câmaras
**Arquivo criado:** `ns-O-RAN-flexric/mmwave-LENA-oran/scratch/scenario10_oran_cameras.cc`

**Nota:** O cenário de câmaras ainda tem problemas de configuração com o EPC. Recomendamos usar o scenario-zero para testes.

---

## 🚀 Em Desenvolvimento

### 3. xApp Energy Saver com E2 CONTROL
- O xApp já tem código para enviar CONTROL
- Necessário adaptar para "Energy state control"

### 4. xApp Slicer - Detecção CRÍTICO
- Monitoramento de SLA para câmaras
- Detecção de violações de latência/throughput

### 5. Lógica de Mitigação no Near-RT RIC
- Interceptar comandos dos xApps
- Decidir baseado em prioridade (Slicer > Energy Saver)

---

## 📋 Cenário do Projeto (12 UEs ns-3 + 3 Câmaras + até 5 Veículos)

### Topologia
| Componente | Quantidade |
|------------|-----------|
| gNB | 1 (com E2 + Energia) |
| Câmaras | 3 (25 Mbps cada) |
| UEs ns-3 | 12 (3 câmeras + 9 background) |
| Veículos App3 | até 5 (IMSI 16-20) |

### Arquitetura de Controle
```
┌─────────────────────────────────────────────────────┐
│                    Near-RT RIC                     │
│  ┌─────────────────────────────────────────────┐    │
│  │         MITIGATION LAYER                    │    │
│  │  SE Slicer.CRÍTICO → BLOQUEAR Energy    │    │
│  │  SENÃO → PERMITIR Energy                │    │
│  └─────────────────────────────────────────────┘    │
│                    ▲              ▲                  │
│               KPM │         CONTROL │                 │
│  ┌───────────────┴┐    ┌─────────┴────────┐        │
│  │ xApp Slicer   │    │ xApp Energy Saver│        │
│  └────────────────┘    └──────────────────┘        │
└─────────────────────────────────────────────────────┘
```

---

## 🎯 Como Executar

### Opção 1: Script Automático
```bash
cd /home/robert/orange_nuclear
./run_scenario_zero.sh
```

### Opção 2: Execução Manual

1. **Iniciar RIC:**
```bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=... (ver abaixo)
./flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/
```

2. **Iniciar ns3 (scenario-zero):**
```bash
cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran
./build/scratch/ns3.42-scenario-zero-default --e2TermIp=127.0.0.1
```

3. **Iniciar xApps:**
```bash
./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/
./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/
```

---

## 📁 Arquivos Importantes

| Arquivo | Descrição |
|---------|-----------|
| `run_scenario_zero.sh` | Script para executar cenário completo |
| `scenario10_oran_cameras.cc` | Cenário com câmaras (em desenvolvimento) |
| `src/ric/iApp/msg_handler_iapp.c` | Código de broadcast KPM modificado |
| `flexric/examples/xApp/c/slicer/xapp_slicer.c` | xApp Slicer |
| `flexric/examples/xApp/c/energy_saver/xapp_energy_saver.c` | xApp Energy Saver |

---

## 📊 Métricas de Validação (Próximos Passos)

1. **Sem O-RAN**: Rede estática (baseline)
2. **Com xApps (Sem Mitigação)**: Demonstrar conflito
3. **Com Mitigação**: SLA mantido + economia de energia

---

**Data: 14/03/2026**
**Status: Em desenvolvimento ativo**
