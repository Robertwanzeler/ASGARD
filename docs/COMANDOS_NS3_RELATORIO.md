# Relatório de Comandos para Executar NS-3 - GreenRAN O-RAN

## Visão Geral

O NS-3 (Network Simulator 3) é utilizado neste projeto como simulador de rede 5G/O-RAN, integrado com o FlexRIC para criar um ambiente de teste completo para xApps e rApps. Este documento descreve todos os comandos utilizados para configurar, executar e monitorar o NS-3 no contexto do projeto GreenRAN.

## Arquitetura do Sistema

```
┌─────────────────────────────────────────────────────────────┐
│                         rApp (FUTURO)                       │
│  Coordena: Slicer ↔ Energy Saver                            │
│  Interface: /tmp/xapp_intents/*.txt                          │
└─────────────────────────────────────────────────────────────┘
                    ▲              ▲
                    │              │
         ┌─────────┴──┐    ┌─────┴─────────┐
         │ xApp      │    │ xApp          │
         │ Slicer    │    │ Energy Saver  │
         │ SLA-based │    │ Latency-based │
         └───────────┘    └───────────────┘
                    ▲              ▲
                    │   KPM/E2    │
         ┌─────────┴──────────────┴────────┐
         │          nearRT-RIC (flexric)     │
         └────────────────────────────────┘
                            ▲
                            │ E2
         ┌──────────────────┴──────────────────┐
         │    ns-3 (scenario-greenran)        │
         │  3 Câmaras (rajadas 120Mbps)      │
         │  9 UEs background (bursty)         │
         │  Faixa App3: IMSI 16-20 (5 veic.)  │
         │  S1-U: 15Mbps (gargalo)           │
         │  Buffer: 20MB                      │
         └───────────────────────────────────┘
```

## 1. Comandos Básicos de Execução do NS-3

### 1.1 Execução Manual Simples

```bash
cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran
./build/scratch/ns3.42-scenario-greenran-debug --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=60
```

**Explicação dos parâmetros:**
- `cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran`: Navega até o diretório do NS-3
- `./build/scratch/ns3.42-scenario-greenran-debug`: Executa o binário compilado do NS-3 com o cenário GreenRAN
- `--e2TermIp=127.0.0.1`: Define o IP do terminal E2 (RIC) como localhost
- `--e2lteEnabled=true`: Ativa a interface E2 para LTE
- `--e2nrEnabled=true`: Ativa a interface E2 para NR (5G)
- `--simTime=60`: Define o tempo de simulação em segundos

### 1.2 Execução com Script de Supervisor

```bash
cd /home/robert/orange_nuclear
./scripts/start_ns3_supervisor.sh
```

**Explicação:**
- Executa o script supervisor que gerencia automaticamente o NS-3
- Reinicia o NS-3 automaticamente em caso de falha
- Configura múltiplos parâmetros de simulação
- Mantém o sistema rodando continuamente

### 1.3 Execução com Script de Pipeline

```bash
cd /home/robert/orange_nuclear
./scripts/run_ns3_article00_pipeline.sh
```

**Explicação:**
- Executa um pipeline completo de coleta de dados e treinamento
- Inicia o runtime GreenRAN + NS-3
- Coleta conflitos de cenários específicos
- Treina pipeline temporal estilo article00

## 2. Parâmetros de Configuração do NS-3

### 2.1 Parâmetros Principais

| Parâmetro | Valor Padrão | Descrição |
|-----------|--------------|-----------|
| `--e2TermIp` | 127.0.0.1 | IP do terminal E2 (RIC) |
| `--simTime` | 60 | Tempo de simulação em segundos |
| `--e2lteEnabled` | true | Ativa interface E2 para LTE |
| `--e2nrEnabled` | true | Ativa interface E2 para NR (5G) |
| `--ueCount` | 20 | Número de UEs |
| `--cameraUeCount` | 3 | Número de UEs de câmera |
| `--vehicleUeCount` | 5 | Número de UEs veiculares |
| `--mmWaveEnbNodes` | 4 | Número de nós gNB mmWave |
| `--ueSpeedMin` | 2 | Velocidade mínima dos UEs (m/s) |
| `--ueSpeedMax` | 4 | Velocidade máxima dos UEs (m/s) |
| `--bandwidthMHz` | 100 | Largura de banda em MHz |

**Exemplo de uso:**
```bash
./build/scratch/ns3.42-scenario-greenran-debug \
  --e2TermIp=127.0.0.1 \
  --simTime=100 \
  --ueCount=20 \
  --cameraUeCount=3 \
  --vehicleUeCount=5 \
  --mmWaveEnbNodes=4 \
  --bandwidthMHz=100
```

### 2.2 Parâmetros de Tráfego

| Parâmetro | Valor Padrão | Descrição |
|-----------|--------------|-----------|
| `--cameraPacketSizeBytes` | 1000 | Tamanho do pacote de câmera em bytes |
| `--cameraPacketIntervalUs` | 320 | Intervalo entre pacotes de câmera em microsegundos |
| `--backgroundPacketSizeBytes` | 128 | Tamanho do pacote de background em bytes |
| `--backgroundPacketIntervalUs` | 10000 | Intervalo entre pacotes de background em microsegundos |
| `--vehiclePacketSizeBytes` | 800 | Tamanho do pacote veicular em bytes |
| `--vehiclePacketIntervalUs` | 4000 | Intervalo entre pacotes veiculares em microsegundos |

### 2.3 Parâmetros de Logging e Métricas

| Parâmetro | Valor Padrão | Descrição |
|-----------|--------------|-----------|
| `--enableTraces` | 1 | Ativa rastreamento de eventos |
| `--enableTracesAfterAttach` | 0 | Ativa rastreamento após anexação dos UEs |
| `--enableE2FileLogging` | false | Ativa logging de E2 em arquivo |
| `--bearerStatsEpochMs` | 100 | Período de coleta de estatísticas em milissegundos |

## 3. Comandos de Compilação

### 3.1 Configuração do NS-3

```bash
cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran
./ns3 configure
```

**Explicação:**
- Configura o ambiente de compilação do NS-3
- Verifica dependências e prepara o sistema de build
- Detecta bibliotecas e ferramentas necessárias

### 3.2 Compilação do NS-3

```bash
cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran
./ns3 build
```

**Explicação:**
- Compila o código fonte do NS-3
- Gera os binários executáveis na pasta build/
- Pode levar vários minutos dependendo do hardware

### 3.3 Compilação com Otimização

```bash
cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran
./ns3 configure --build-profile=optimized
./ns3 build
```

**Explicação:**
- Configura para compilação otimizada (mais rápida)
- Remove símbolos de debug
- Melhora performance da simulação

## 4. Comandos de Monitoramento

### 4.1 Monitoramento de Logs

```bash
tail -f /tmp/ns3.log              # Monitora logs do NS-3
tail -f /tmp/ric.log              # Monitora logs do RIC
tail -f /tmp/xapp_slicer.log      # Monitora logs do xApp Slicer
tail -f /tmp/xapp_energy.log      # Monitora logs do xApp Energy Saver
```

**Explicação:**
- `tail -f`: Mostra o final do arquivo em tempo real
- Permite monitorar a execução e identificar problemas
- Útil para debugging e verificação de funcionamento

### 4.2 Verificação de Processos

```bash
ps aux | grep ns3.42-scenario     # Verifica se o NS-3 está rodando
ps aux | grep nearRT-RIC          # Verifica se o RIC está rodando
ps aux | grep xapp_slicer         # Verifica se o xApp Slicer está rodando
ps aux | grep xapp_energy         # Verifica se o xApp Energy Saver está rodando
```

### 4.3 Monitoramento Avançado

```bash
# Ver uso de recursos do NS-3
top -p $(pgrep -f ns3.42-scenario)

# Ver conexões de rede
netstat -an | grep 36412  # Porta SCTP padrão do E2

# Ver arquivos abertos pelo NS-3
lsof -p $(pgrep -f ns3.42-scenario)
```

## 5. Comandos de Reinicialização

### 5.1 Reinicialização do NS-3 Apenas

```bash
cd /home/robert/orange_nuclear
./scripts/restart_ns3_only.sh
```

**Explicação:**
- Reinicia apenas o processo NS-3
- Mantém o supervisor ativo
- Útil para recarregar configurações sem parar todo o sistema

### 5.2 Parada Completa do Sistema

```bash
cd /home/robert/orange_nuclear
./scripts/stop_all.sh
```

**Explicação:**
- Para todos os componentes do sistema
- Inclui NS-3, RIC, xApps e coletores
- Limpa processos zumbis e recursos

### 5.3 Parada Manual

```bash
pkill -f "ns3.42-scenario"       # Mata processo NS-3
pkill -f "nearRT-RIC"            # Mata processo RIC
pkill -f "xapp_slicer"           # Mata xApp Slicer
pkill -f "xapp_energy"           # Mata xApp Energy Saver
```

## 6. Comandos de Execução Completa (GreenRAN)

### 6.1 Execução Completa com Runtime

```bash
cd /home/robert/orange_nuclear
./run_greenran_complete.sh
```

**Explicação:**
- Inicia todos os componentes do sistema GreenRAN
- Inclui nearRT-RIC, NS-3, xApps, rApps e coletores
- Configura ambiente de execução completo
- Verifica dependências e saúde do sistema

### 6.2 Execução com Dashboard

```bash
cd /home/robert/orange_nuclear
./run_greenran_complete.sh --dashboard
```

**Explicação:**
- Inicia o sistema completo com dashboard Flask
- Permite monitoramento visual via interface web
- Dashboard disponível em http://localhost:5000

### 6.3 Execução com Watchdog

```bash
cd /home/robert/orange_nuclear
./run_greenran_complete.sh --watchdog
```

**Explicação:**
- Inicia o sistema com watchdog de monitoramento
- Reinicia automaticamente componentes que falharem
- Aumenta resiliência do sistema

### 6.4 Execução Completa com Todas as Funcionalidades

```bash
cd /home/robert/orange_nuclear
./run_greenran_complete.sh --all
```

**Explicação:**
- Inicia o sistema com dashboard e watchdog
- Fornece monitoramento completo e visual
- Máxima funcionalidade disponível

## 7. Comandos de Configuração de Ambiente

### 7.1 Configuração de Bibliotecas

```bash
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
```

**Explicação:**
- Configura o caminho das bibliotecas compartilhadas necessárias
- Necessário para execução do RIC e xApps
- Deve ser executado antes de iniciar componentes

### 7.2 Inicialização do RIC

```bash
cd /home/robert/orange_nuclear
./flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/
```

**Explicação:**
- Inicia o nearRT-RIC com arquivo de configuração
- Define o caminho das bibliotecas do FlexRIC
- Pré-requisito para execução do NS-3 com E2

### 7.3 Inicialização do xApp Slicer

```bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/
```

**Explicação:**
- Inicia o xApp Slicer para gerenciamento de fatias de rede
- Monitora latência e throughput
- Implementa políticas baseadas em SLA

### 7.4 Inicialização do xApp Energy Saver

```bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/
```

**Explicação:**
- Inicia o xApp Energy Saver para economia de energia
- Monitora utilização de células
- Implementa políticas de desligamento de células

## 8. Scripts Especializados

### 8.1 Execução de Cenários Específicos

```bash
cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran
./ns3 run "scratch/scenario-zero-with_parallel_loging.cc --e2TermIp=127.0.0.1 --hoSinrDifference=3 --indicationPeriodicity=0.1 --simTime=1000 --KPM_E2functionID=2 --RC_E2functionID=3 --N_MmWaveEnbNodes=4 --N_Ues=3 --CenterFrequency=3.5e9 --Bandwidth=20e6 --IntersideDistanceUEs=500 --IntersideDistanceCells=600"
```

**Explicação:**
- Executa um cenário específico do NS-3
- Configura parâmetros avançados de simulação
- Inclui handover, funções E2 e configuração de células

**Parâmetros adicionais:**
- `--hoSinrDifference=3`: Diferença de SINR para handover
- `--indicationPeriodicity=0.1`: Periodicidade de indicações em segundos
- `--KPM_E2functionID=2`: ID da função E2 para KPM
- `--RC_E2functionID=3`: ID da função E2 para RC
- `--CenterFrequency=3.5e9`: Frequência central em Hz
- `--IntersideDistanceUEs=500`: Distância entre UEs em metros
- `--IntersideDistanceCells=600`: Distância entre células em metros

### 8.2 Execução com GUI (RIC-TaaP Studio)

```bash
cd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran
python3 gui_trigger.py
```

**Explicação:**
- Inicia o script que envia KPIs do NS-3 para o banco de dados
- Necessário para funcionamento da GUI
- Permite monitoramento visual via navegador

### 8.3 Teste Rápido do Cenário

```bash
cd /home/robert/orange_nuclear
./test_cenario.sh
```

**Explicação:**
- Executa um teste rápido de 60 segundos
- Valida configuração básica do cenário
- Útil para verificações rápidas

## 9. Cenários de Simulação Disponíveis

### 9.1 Cenário Zero (Básico)

```bash
./build/scratch/ns3.42-scenario-zero-default --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=60
```

**Características:**
- 1 eNB LTE + 4 gNBs mmWave
- 5 UEs
- Interface E2 ativa com KPM e RC

### 9.2 Cenário GreenRAN (Congestionamento)

```bash
./build/scratch/ns3.42-scenario-greenran-default --e2TermIp=127.0.0.1 --simTime=100000
```

**Características:**
- 20 UEs com tráfego misto
- 3 câmeras com rajadas de 120Mbps
- 9 UEs background com tráfego bursty
- S1-U: 15Mbps (gargalo intencional)
- Buffer: 20MB

### 9.3 Cenário Energy Saving

```bash
./build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default --e2TermIp=127.0.0.1 --simTime=100000
```

**Características:**
- Focado em economia de energia
- Monitoramento de utilização de células
- Políticas de desligamento/ligamento de células

## 10. Resumo de Fluxo de Execução Típico

### 10.1 Preparação do Ambiente

```bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
```

### 10.2 Iniciação do RIC

```bash
./flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/ > /tmp/ric.log 2>&1 &
RIC_PID=$!
echo "RIC iniciado (PID: $RIC_PID)"
```

### 10.3 Iniciação do NS-3

```bash
cd ns-O-RAN-flexric/mmwave-LENA-oran
./build/scratch/ns3.42-scenario-greenran-debug --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=60 > /tmp/ns3.log 2>&1 &
NS3_PID=$!
echo "NS-3 iniciado (PID: $NS3_PID)"
```

### 10.4 Iniciação dos xApps

```bash
cd /home/robert/orange_nuclear
# xApp Slicer
./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp_slicer.log 2>&1 &
SLICER_PID=$!

# xApp Energy Saver
./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp_energy.log 2>&1 &
ENERGY_PID=$!

echo "xApps iniciados (Slicer: $SLICER_PID, Energy: $ENERGY_PID)"
```

### 10.5 Monitoramento

```bash
# Monitorar logs em terminais separados
tail -f /tmp/ns3.log &
tail -f /tmp/ric.log &
tail -f /tmp/xapp_slicer.log &
tail -f /tmp/xapp_energy.log &
```

### 10.6 Parada

```bash
# Matar processos na ordem inversa
kill $ENERGY_PID $SLICER_PID $NS3_PID $RIC_PID
./scripts/stop_all.sh
```

## 11. Solução de Problemas Comuns

### 11.1 NS-3 Não Inicia

**Sintoma:** Processo NS-3 encerra imediatamente

**Solução:**
```bash
# Verificar logs
cat /tmp/ns3.log

# Verificar se binário existe e tem permissão
ls -la /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-greenran-debug

# Verificar dependências
ldd /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-scenario-greenran-debug
```

### 11.2 Handshake E2 Falha

**Sintoma:** NS-3 não conecta ao RIC

**Solução:**
```bash
# Verificar se RIC está rodando
ps aux | grep nearRT-RIC

# Verificar porta SCTP
netstat -an | grep 36412

# Verificar logs de ambos
tail -f /tmp/ric.log /tmp/ns3.log

# Verificar configuração de IP
--e2TermIp=127.0.0.1
```

### 11.3 Performance Baixa

**Sintoma:** Simulação muito lenta

**Solução:**
```bash
# Compilar com otimização
./ns3 configure --build-profile=optimized
./ns3 build

# Reduzir número de UEs
--ueCount=10

# Reduzir tempo de simulação
--simTime=30

# Desativar traces desnecessários
--enableTraces=0
```

### 11.4 Memória Insuficiente

**Sintoma:** NS-3 é morto pelo OOM killer

**Solução:**
```bash
# Verificar uso de memória
free -h

# Aumentar swap (se necessário)
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Reduzir número de UEs ou buffers
--ueCount=10
--bearerStatsEpochMs=200
```

## 12. Boas Práticas

### 12.1 Antes de Executar

1. **Verificar dependências:**
   ```bash
   ./ns3 configure
   ```

2. **Limpar processos antigos:**
   ```bash
   ./scripts/stop_all.sh
   ```

3. **Verificar espaço em disco:**
   ```bash
   df -h
   ```

4. **Configurar ambiente:**
   ```bash
   export LD_LIBRARY_PATH=...
   ```

### 12.2 Durante a Execução

1. **Monitorar logs regularmente**
2. **Verificar uso de recursos:**
   ```bash
   top -p $(pgrep -f ns3.42-scenario)
   ```
3. **Verificar conexões E2:**
   ```bash
   netstat -an | grep 36412
   ```

### 12.3 Após a Execução

1. **Verificar logs de erros:**
   ```bash
   grep -i error /tmp/ns3.log
   ```

2. **Verificar arquivos de saída:**
   ```bash
   ls -la /home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran/*.txt
   ```

3. **Limpar recursos:**
   ```bash
    ./scripts/stop_all.sh
    ```

## 13. Execução de rApps e xApps no GreenRAN O-RAN

### 13.1 Visão Geral de rApps e xApps

No ecossistema O-RAN, existem dois tipos principais de aplicações que trabalham em conjunto com o NS-3:

**rApps (Non-RT RIC Applications):**
- Operam na camada Non-Real-Time RIC (tempos ≥ 1 segundo)
- Implementam lógica de orquestração estratégica
- Coordena múltiplos xApps
- Análise de padrões, Machine Learning, políticas de longo prazo
- Escritos em Python

**xApps (Near-RT RIC Applications):**
- Operam na camada Near-Real-Time RIC (tempos < 1 segundo)
- Implementam lógica de controle em tempo real
- Interação direta com o RIC através da interface E2
- Controle de rádio, handover, gestão de recursos
- Compilados em C (binários executáveis)

**Integração com NS-3:**
```
┌─────────────────────────────────────────────────────────────┐
│                    rApps (Non-RT RIC)                       │
│  - Orquestração estratégica                                  │
│  - Análise de padrões e ML                                   │
│  - Políticas de longo prazo                                  │
└─────────────────────────────────────────────────────────────┘
                    ▲              ▲
                    │  Coordena   │
         ┌─────────┴──┐    ┌─────┴─────────┐
         │ xApp      │    │ xApp          │
         │ Slicer    │    │ Energy Saver  │
         │ (SLA)     │    │ (Energia)     │
         └───────────┘    └───────────────┘
                    ▲              ▲
                    │  E2/KPM     │
         ┌─────────┴──────────────┴────────┐
         │      Near-RT RIC (FlexRIC)       │
         └─────────────────────────────────┘
                    ▲
                    │  E2
         ┌──────────┴──────────┐
         │      NS-3 Simulator │
         │  (Rede 5G/O-RAN)    │
         └─────────────────────┘
```

### 13.2 Localização e Estrutura de Arquivos

#### 13.2.1 Localização dos rApps

**Diretório Principal:**
```
/home/robert/orange_nuclear/src/
```

**Arquivos rApps Disponíveis (19 arquivos):**
- `rapp_orchestrator.py` (200KB) - Orquestrador principal
- `rapp_dashboard.py` (75KB) - Interface web Flask
- `rapp_data_lake.py` (136KB) - Gerenciamento de dados
- `rapp_judge.py` (26KB) - Avaliador de decisões
- `rapp_pattern_engine.py` (45KB) - Reconhecimento de padrões
- `rapp_a1_interface.py` (27KB) - Interface A1 para RIC
- `rapp_agent_openran.py` (17KB) - Agente OpenRAN
- `rapp_alerts.py` (19KB) - Sistema de alertas
- `rapp_armd_runtime.py` (23KB) - Runtime ARMD
- `rapp_control_trial.py` (21KB) - Controle de testes
- `rapp_marl_control_gate.py` (6KB) - Gate de controle MARL
- `rapp_marl_shadow.py` (31KB) - Avaliador shadow MARL
- `rapp_ml_predictor.py` (36KB) - Preditor ML
- `rapp_network_improvement.py` (2KB) - Melhorias de rede
- `rapp_online_retrain_runtime.py` (2KB) - Retreinamento online
- `rapp_policy_consumer.py` (4KB) - Consumidor de políticas
- `rapp_policy_source.py` (5KB) - Fonte de políticas
- `rapp_rl_policy.py` (3KB) - Políticas RL
- `rapp_sac_resource_model.py` (35KB) - Modelo de recursos SAC
- `rapp_trend_analysis.py` (22KB) - Análise de tendências
- `rapp_xapp_manager.py` (15KB) - Gerenciador de xApps

#### 13.2.2 Localização dos xApps

**Diretório Principal:**
```
/home/robert/orange_nuclear/flexric/build_e2ap_v1/examples/xApp/c/
```

**xApps Compilados Disponíveis:**
- `xapp_slicer` (~10MB) - Gerenciamento de fatias de rede
- `xapp_energy_saver` (~10MB) - Economia de energia em células

**Estrutura de Diretórios dos xApps:**
```
flexric/build_e2ap_v1/examples/xApp/c/
├── ctrl/                 # xApps de controle
├── helloworld/           # xApp exemplo
├── keysight/             # xApps Keysight
├── kpm_rc/              # xApps KPM/RC
├── monitor/             # xApps de monitoramento
├── orange/              # xApps Orange específicos
├── slice/               # xApps de slicing
├── tc/                  # xApps de teste
├── xapp_slicer          # Binário compilado
└── xapp_energy_saver    # Binário compilado
```

### 13.3 Execução de rApps

#### 13.3.1 rApp Orchestrator (Principal)

**Execução Manual:**
```bash
cd /home/robert/orange_nuclear
python3 ./src/rapp_orchestrator.py --interval 10 --synthetic 0
```

**Parâmetros:**
- `--interval 10`: Intervalo de execução em segundos (padrão: 10)
- `--synthetic 0`: Dias de dados sintéticos (0 = desativado, padrão: 0)
- `--agent_intent <intenção>`: Criar intenção do Agent-Al (opcional)

**Funcionalidades:**
- Coordena xApps (Slicer ↔ Energy Saver)
- Usa Data Lake para análise de padrões
- Detecta sazonalidade com Machine Learning
- Interface A1 para Near-RT RIC
- Traduz intenções do Agent-Al

**Interfaces de Arquivos:**
- `/tmp/xapp_intents/slicer.txt` → xApp SLICER escreve
- `/tmp/xapp_intents/energy_saver.txt` → xApp ENERGY SAVER escreve
- `/tmp/rapp_policies/energy_policy.json` ← rApp escreve (A1)
- `/tmp/rapp_policies/slice_policy.json` ← rApp escreve (A1)

#### 13.3.2 rApp Dashboard (Interface Web)

**Execução Manual:**
```bash
cd /home/robert/orange_nuclear
python3 ./src/rapp_dashboard.py --host 127.0.0.1 --port 5000
```

**Parâmetros:**
- `--host 127.0.0.1`: Endereço de host (padrão: 127.0.0.1)
- `--port 5000`: Porta HTTP (padrão: 5000)

**Acesso:**
- URL: `http://localhost:5000`
- Disponível quando executado com `--dashboard`

**Rotas Disponíveis:**
- `/` - Dashboard principal
- `/metrics` - Métricas em tempo real (JSON)
- `/decisions` - Histórico de decisões
- `/pattern` - Análise de padrões
- `/xapps` - Status dos xApps
- `/api/*` - API para dados

#### 13.3.3 Execução de rApps via Scripts

**Via Script Completo:**
```bash
cd /home/robert/orange_nuclear
./run_greenran_complete.sh              # Inclui rApp Orchestrator
./run_greenran_complete.sh --dashboard  # Inclui Dashboard também
./run_simulation.sh                     # Simulação com rApp
```

**Via Script Específico:**
```bash
# O rApp Orchestrator é iniciado automaticamente pelos scripts:
# - run_greenran_complete.sh
# - run_simulation.sh
# - run_tasam_article_ns3_collection.sh
```

### 13.4 Execução de xApps

#### 13.4.1 xApp Slicer

**Execução Manual:**
```bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/
```

**Via Script Dedicado:**
```bash
cd /home/robert/orange_nuclear
./scripts/run_slicer.sh
```

**Funcionalidades:**
- Gerenciamento de fatias de rede
- Monitoramento de SLA (latência < 100ms)
- Controle de latência e throughput
- Estados: NORMAL → WARNING → CRITICAL → IDLE
- Interface para rApp implementada

**Thresholds Configurados:**
- `SLA_CRITICAL`: 100ms - Latência máxima aceitável
- `SLA_WARNING`: 50ms - Latência de alerta
- `CAMERA_MIN_PACKETS`: 5 - Mínimo pacotes para ativar

#### 13.4.2 xApp Energy Saver

**Execução Manual:**
```bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/
```

**Via Script Dedicado:**
```bash
cd /home/robert/orange_nuclear
./scripts/run_energy.sh
```

**Funcionalidades:**
- Economia de energia em células
- Monitoramento de utilização de células
- Políticas de desligamento/ligamento de células
- Estados: NORMAL → INTERVENTION → ENERGY_SAVE
- Interface para rApp implementada

**Thresholds Configurados:**
- `INTERVENTION`: 100ms - Latência SLA violado
- `ENERGY_SAVE_VOLUME`: 100kb - Volume mínimo ativo
- `ENERGY_SAVE_LATENCY`: 10ms - Latência para energy save

#### 13.4.3 Execução com Auto-Restart

**Via Script Avançado:**
```bash
cd /home/robert/orange_nuclear
./scripts/run_xapps_auto.sh start     # Inicia ambos com auto-restart
./scripts/run_xapps_auto.sh status    # Verifica status
./scripts/run_xapps_auto.sh health    # Verifica saúde detalhada
./scripts/run_xapps_auto.sh stop      # Para os xApps
./scripts/run_xapps_auto.sh clean     # Limpa contadores
./scripts/run_xapps_auto.sh restart   # Reinicia xApps
```

**Funcionalidades do Auto-Restart:**
- Reinicia automaticamente xApps quando param
- Health tracking em `/tmp/xapp_health.json`
- Log de restarts em `/tmp/xapp_restarts.log`
- Detecção de tipo de falha (timeout vs erro)
- Heartbeat para monitoramento

**Monitoramento:**
```bash
# Ver status dos xApps
./scripts/run_xapps_auto.sh status

# Ver health detalhado
./scripts/run_xapps_auto.sh health

# Monitorar logs de restart
tail -f /tmp/xapp_restarts.log

# Ver health JSON
cat /tmp/xapp_health.json
```

### 13.5 Comparação Detalhada: rApps vs xApps

| Característica | rApps | xApps |
|----------------|-------|-------|
| **Tipo** | Scripts Python | Binários C compilados |
| **Camada RIC** | Non-RT RIC (≥1 segundo) | Near-RT RIC (<1 segundo) |
| **Tempo de Resposta** | Estratégico (segundos/minutos) | Tempo real (milissegundos) |
| **Localização** | `/home/robert/orange_nuclear/src/` | `/flexric/build_e2ap_v1/examples/xApp/c/` |
| **Execução** | `python3 ./src/rapp_*.py` | Direta (binário executável) |
| **Dependências** | Python, bibliotecas ML | LD_LIBRARY_PATH, bibliotecas C |
| **Interface com RIC** | A1 Policy Interface | E2 Interface (KPM/RC) |
| **Comunicação** | Arquivos JSON, sockets | Mensagens E2AP diretas |
| **Caso de Uso** | Orquestração, ML, análise | Controle de rádio, handover |
| **Exemplos** | rapp_orchestrator, rapp_dashboard | xapp_slicer, xapp_energy_saver |
| **Tamanho** | 2KB - 200KB (Python) | ~10MB (binários compilados) |
| **Logs** | `$GREENRAN_RAPP_LOG` | `/tmp/xapp_*.log` |
| **Auto-restart** | Via scripts ou manual | Via `run_xapps_auto.sh` |
| **Desenvolvimento** | Mais fácil (Python) | Mais complexo (C) |
| **Performance** | Adequado para ML/análise | Otimizado para tempo real |
| **Persistência** | Data Lake SQLite | Memória + logs |

### 13.6 Integração Completa do Sistema

#### 13.6.1 Ordem de Inicialização Recomendada

**1. Preparação do Ambiente:**
```bash
cd /home/robert/orange_nuclear
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
```

**2. Iniciar nearRT-RIC:**
```bash
./flexric/build_e2ap_v1/examples/ric/nearRT-RIC -c flexric/flexric.conf -p flexric_lib/ > /tmp/ric.log 2>&1 &
RIC_PID=$!
echo "RIC iniciado (PID: $RIC_PID)"
```

**3. Iniciar NS-3:**
```bash
cd ns-O-RAN-flexric/mmwave-LENA-oran
./build/scratch/ns3.42-scenario-greenran-debug --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime=60 > /tmp/ns3.log 2>&1 &
NS3_PID=$!
echo "NS-3 iniciado (PID: $NS3_PID)"
```

**4. Iniciar xApps:**
```bash
cd /home/robert/orange_nuclear
# xApp Slicer
./flexric/build_e2ap_v1/examples/xApp/c/xapp_slicer -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp_slicer.log 2>&1 &
SLICER_PID=$!

# xApp Energy Saver
./flexric/build_e2ap_v1/examples/xApp/c/xapp_energy_saver -c flexric/flexric.conf -p flexric_lib/ > /tmp/xapp_energy.log 2>&1 &
ENERGY_PID=$!

echo "xApps iniciados (Slicer: $SLICER_PID, Energy: $ENERGY_PID)"
```

**5. Iniciar rApps:**
```bash
# rApp Orchestrator
python3 ./src/rapp_orchestrator.py --interval 10 --synthetic 0 > /tmp/rapp_orchestrator.log 2>&1 &
RAPP_PID=$!

# rApp Dashboard (opcional)
python3 ./src/rapp_dashboard.py --host 127.0.0.1 --port 5000 > /tmp/rapp_dashboard.log 2>&1 &
DASHBOARD_PID=$!

echo "rApps iniciados (Orchestrator: $RAPP_PID, Dashboard: $DASHBOARD_PID)"
```

#### 13.6.2 Scripts de Execução Completa

**Script Principal:**
```bash
cd /home/robert/orange_nuclear
./run_greenran_complete.sh               # Sistema completo
./run_greenran_complete.sh --dashboard   # Com dashboard web
./run_greenran_complete.sh --watchdog    # Com watchdog
./run_greenran_complete.sh --all         # Com tudo
```

**Script de Simulação:**
```bash
cd /home/robert/orange_nuclear
./run_simulation.sh                      # Simulação integrada
```

**Scripts Especializados:**
```bash
./scripts/run_xapps_auto.sh start        # xApps com auto-restart
./scripts/run_all_xapps_tmux.sh          # xApps em tmux
./scripts/run_tasam_article_ns3_collection.sh  # Coleta de dados
```

#### 13.6.3 Monitoramento e Debugging

**Verificar Processos:**
```bash
# Ver todos os componentes
ps aux | grep -E "(nearRT-RIC|ns3.42|xapp_|rapp_)" | grep -v grep

# Ver componente específico
ps aux | grep xapp_slicer
ps aux | grep rapp_orchestrator
```

**Monitorar Logs:**
```bash
# Logs principais
tail -f /tmp/ric.log              # RIC
tail -f /tmp/ns3.log              # NS-3
tail -f /tmp/xapp_slicer.log      # xApp Slicer
tail -f /tmp/xapp_energy.log      # xApp Energy Saver
tail -f /tmp/rapp_orchestrator.log # rApp Orchestrator
tail -f /tmp/rapp_dashboard.log    # rApp Dashboard

# Logs de auto-restart
tail -f /tmp/xapp_restarts.log    # Restart dos xApps
cat /tmp/xapp_health.json         # Health dos xApps
```

**Verificar Conexões:**
```bash
# Ver conexões E2 (SCTP)
netstat -an | grep 36412

# Ver portas do dashboard
netstat -an | grep 5000

# Ver soquetes dos xApps
lsof -p $(pgrep -f xapp_slicer)
```

**Ver Arquivos de Interface:**
```bash
# Intenções dos xApps
cat /tmp/xapp_intents/slicer.txt
cat /tmp/xapp_intents/energy_saver.txt

# Políticas do rApp
cat /tmp/rapp_policies/energy_policy.json
cat /tmp/rapp_policies/slice_policy.json

# Decisões do rApp
cat /tmp/rapp_intents/rapp_decision.txt
```

### 13.7 Boas Práticas para rApps e xApps

#### 13.7.1 Antes da Execução

1. **Verificar dependências:**
   ```bash
   # Para rApps
   python3 -c "import flask, sqlite3, pandas"

   # Para xApps
   ls -la /home/robert/orange_nuclear/flexric/build_e2ap_v1/examples/xApp/c/xapp_*
   ```

2. **Configurar ambiente:**
   ```bash
   export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
   ```

3. **Limpar processos antigos:**
   ```bash
   ./scripts/stop_all.sh
   ```

4. **Verificar espaço em disco:**
   ```bash
   df -h
   ```

5. **Criar diretórios necessários:**
   ```bash
   mkdir -p /tmp/xapp_intents /tmp/rapp_policies
   ```

#### 13.7.2 Durante a Execução

1. **Monitorar recursos:**
   ```bash
   top -p $(pgrep -f "xapp_|rapp_")
   ```

2. **Verificar health dos xApps:**
   ```bash
   ./scripts/run_xapps_auto.sh health
   ```

3. **Monitorar logs em tempo real:**
   ```bash
   tail -f /tmp/xapp_slicer.log /tmp/xapp_energy.log /tmp/rapp_orchestrator.log
   ```

4. **Verificar decisões do rApp:**
   ```bash
   watch -n 5 'cat /tmp/rapp_intents/rapp_decision.txt'
   ```

5. **Verificar políticas geradas:**
   ```bash
   watch -n 5 'cat /tmp/rapp_policies/*.json'
   ```

#### 13.7.3 Após a Execução

1. **Verificar logs de erros:**
   ```bash
   grep -i error /tmp/xapp_*.log
   grep -i error /tmp/rapp_*.log
   ```

2. **Verificar estatísticas de restart:**
   ```bash
   cat /tmp/xapp_restarts.log
   ```

3. **Verificar health final:**
   ```bash
   cat /tmp/xapp_health.json
   ```

4. **Backup de logs importantes:**
   ```bash
   cp /tmp/xapp_restarts.log /tmp/xapp_restarts.log.$(date +%Y%m%d)
   ```

5. **Limpar recursos:**
   ```bash
   ./scripts/stop_all.sh
   ```

#### 13.7.4 Segurança e Performance

**Segurança:**
- Nunca execute rApps/xApps como root
- Use ambientes virtuais Python para rApps
- Valide entradas de arquivos de configuração
- Monitore uso de recursos para evitar ataques DoS

**Performance:**
- Use auto-restart apenas quando necessário
- Configure intervalos apropriados para rApps
- Monitore uso de memória dos xApps
- Limpe logs antigos regularmente
- Use compilação otimizada para xApps

**Manutenção:**
- Atualize regularmente dependências Python
- Recompile xApps após mudanças no FlexRIC
- Monitore health metrics continuamente
- Documente configurações personalizadas
- Faça backup de configurações importantes

## 14. Referências e Documentação Adicional

### 14.1 Documentação do Projeto

- `docs/README_SIMULATION.md` - Documentação de simulação
- `docs/README_GREENRAN.md` - Documentação do GreenRAN
- `docs/COMANDOS_NS3_RELATORIO.md` - Este documento
- `ns-O-RAN-flexric/README.md` - Documentação do ns-O-RAN-flexric
- `docs/CARLA_NS3_INTEGRACAO.md` - Integração CARLA + NS-3
- `docs/APP1_APP2_ANALISE.md` - Análise dos aplicativos principais

### 14.2 Scripts Úteis

**Scripts NS-3:**
- `scripts/run_ns3.sh` - Execução básica do NS-3
- `scripts/start_ns3_supervisor.sh` - Supervisor do NS-3
- `scripts/restart_ns3_only.sh` - Reinicialização do NS-3
- `scripts/run_ns3_article00_pipeline.sh` - Pipeline completo

**Scripts xApps:**
- `scripts/run_xapp.sh` - Execução genérica de xApp
- `scripts/run_slicer.sh` - Execução do xApp Slicer
- `scripts/run_energy.sh` - Execução do xApp Energy Saver
- `scripts/run_xapps_auto.sh` - xApps com auto-restart
- `scripts/run_all_xapps_tmux.sh` - xApps em tmux

**Scripts Sistema Completo:**
- `scripts/run_simulation.sh` - Simulação integrada
- `scripts/run_greenran_complete.sh` - Execução completa GreenRAN
- `scripts/stop_all.sh` - Parar todos os componentes

### 14.3 Logs e Arquivos de Saída

**Logs NS-3 e RIC:**
- `/tmp/ns3.log` - Logs do NS-3
- `/tmp/ric.log` - Logs do RIC

**Logs xApps:**
- `/tmp/xapp_slicer.log` - Logs do xApp Slicer
- `/tmp/xapp_energy.log` - Logs do xApp Energy Saver
- `/tmp/xapp_restarts.log` - Log de restarts dos xApps
- `/tmp/xapp_health.json` - Health status dos xApps

**Logs rApps:**
- `/tmp/rapp_orchestrator.log` - Logs do rApp Orchestrator
- `/tmp/rapp_dashboard.log` - Logs do rApp Dashboard

**Arquivos de Interface:**
- `/tmp/xapp_intents/slicer.txt` - Intenções do xApp Slicer
- `/tmp/xapp_intents/energy_saver.txt` - Intenções do xApp Energy Saver
- `/tmp/rapp_intents/rapp_decision.txt` - Decisões do rApp
- `/tmp/rapp_policies/energy_policy.json` - Políticas de energia (A1)
- `/tmp/rapp_policies/slice_policy.json` - Políticas de slicing (A1)

**Arquivos de Estatísticas:**
- `runs/tasam_article_ns3_collection/` - Diretório de execução
- `ns-O-RAN-flexric/mmwave-LENA-oran/*.txt` - Arquivos de estatísticas NS-3
- `runs/*/greenran.db` - Data Lake SQLite

## 15. Conclusão

Este documento fornece um guia completo para executar o NS-3, rApps e xApps no contexto do projeto GreenRAN O-RAN. Os comandos e procedimentos descritos aqui permitem:

- Configurar e compilar o NS-3
- Executar simulações com diferentes cenários
- Monitorar e debugar a execução
- Integrar com o FlexRIC, xApps e rApps
- Executar e gerenciar rApps (Non-RT RIC)
- Executar e gerenciar xApps (Near-RT RIC)
- Solucionar problemas comuns
- Implementar estratégias completas de O-RAN

Para dúvidas adicionais, consulte a documentação do projeto e os scripts disponíveis nos diretórios `scripts/` e `src/`.

## 16. Como Construir um xApp do Zero

Esta seção ensina, passo a passo, como criar um xApp novo em C para o FlexRIC (E2AP v1) do projeto GreenRAN. O exemplo constrói um xApp chamado `meu_xapp` que se conecta ao nearRT-RIC, lista os nós E2 conectados e finaliza de forma limpa. **Cada linha de código está explicada**, para que qualquer pessoa consiga entender e construir sozinha.

O caminho de referência no repositório é o exemplo `helloworld`, que serve de modelo:

- `flexric/examples/xApp/c/helloworld/hw.c` — código-fonte mínimo de um xApp
- `flexric/examples/xApp/c/helloworld/CMakeLists.txt` — regras de compilação do exemplo

### 16.1 Pré-requisitos

Antes de começar, verifique se o ambiente está pronto:

```bash
gcc --version
```
**Explicação linha a linha:**
- `gcc --version` → verifica se o compilador C está instalado (necessário para compilar o xApp). Deve retornar algo como `gcc (Ubuntu ...) 11.x` ou superior.

```bash
cmake --version
```
**Explicação linha a linha:**
- `cmake --version` → verifica se o CMake está instalado. Ele é o sistema de build do FlexRIC e o responsável por gerar os comandos de compilação.

```bash
ls /home/robert/orange_nuclear/flexric/build_e2ap_v1/examples/ric/nearRT-RIC
```
**Explicação linha a linha:**
- `ls <caminho>` → confirma que o binário do nearRT-RIC já foi compilado. Se o arquivo não existir, o FlexRIC ainda não foi construído e você precisa compilá-lo antes.

```bash
ls /home/robert/orange_nuclear/flexric/flexric.conf
```
**Explicação linha a linha:**
- `ls <caminho>` → confirma que o arquivo de configuração do FlexRIC existe. É ele que o xApp lê para descobrir o IP/porta do RIC.

```bash
ls /home/robert/orange_nuclear/flexric_lib/ | head
```
**Explicação linha a linha:**
- `ls <caminho>` → lista as bibliotecas de Service Models (`.so`) do FlexRIC (KPM, RC etc.).
- `| head` → mostra apenas as 10 primeiras, para não poluir o terminal.

**Importante:** para testar o xApp, o nearRT-RIC precisa estar rodando (seção 7.2 deste documento). Sem o RIC ativo, o xApp não encontra nós E2.

### 16.2 Entendendo a Anatomia de um xApp

Todo xApp do FlexRIC segue o mesmo ciclo de vida, em 5 etapas:

```
1. init_fr_args()      → lê os argumentos da linha de comando (IP, portas, config)
2. init_xapp_api()     → conecta o xApp ao RIC via socket SCTP
3. e2_nodes_xapp_api() → descobre quais nós E2 (gNBs do NS-3) estão conectados
4. [corpo do xApp]     → aqui entra a lógica: ler KPIs, subscrever KPM, tomar decisões
5. try_stop_xapp_api() → desconecta e libera memória de forma segura
```

Nos próximos passos vamos construir esse ciclo linha por linha.

### 16.3 Passo 1 — Criar o Diretório e o Código-Fonte

```bash
mkdir -p /home/robert/orange_nuclear/flexric/examples/xApp/c/meu_xapp
```
**Explicação linha a linha:**
- `mkdir -p` → cria o diretório (e qualquer diretório pai que não exista, sem erro se já existir).
- `/home/robert/orange_nuclear/flexric/examples/xApp/c/meu_xapp` → caminho onde ficam todos os xApps do projeto. Criar aqui garante que o xApp siga o padrão do repositório.

```bash
nano /home/robert/orange_nuclear/flexric/examples/xApp/c/meu_xapp/xapp_meu_xapp.c
```
**Explicação linha a linha:**
- `nano <arquivo>` → abre o editor de texto para criar o arquivo do xApp. Você pode usar `vim` ou `code` se preferir.

**Conteúdo completo do arquivo `xapp_meu_xapp.c`:**

```c
#include "../../../../src/xApp/e42_xapp_api.h"
#include "../../../../src/util/alg_ds/alg/defer.h"
#include "../../../../src/util/ngran_types.h"

#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <signal.h>
#include <assert.h>

int main(int argc, char *argv[])
{
  fr_args_t args = init_fr_args(argc, argv);

  init_xapp_api(&args);
  sleep(1);

  e2_node_arr_xapp_t nodes = e2_nodes_xapp_api();
  defer({ free_e2_node_arr_xapp(&nodes); });

  assert(nodes.len > 0);

  printf("xApp conectado! Nos E2 = %d\n", nodes.len);

  for (size_t i = 0; i < nodes.len; i++) {
    ngran_node_t ran_type = nodes.n[i].id.type;
    printf("No E2 %ld: nb_id %d, mcc %d, mnc %d, tipo %s\n",
           i,
           nodes.n[i].id.nb_id.nb_id,
           nodes.n[i].id.plmn.mcc,
           nodes.n[i].id.plmn.mnc,
           get_ngran_name(ran_type));

    printf("Funcoes RAN suportadas:");
    for (size_t j = 0; j < nodes.n[i].len_rf; j++)
      printf(", %d", nodes.n[i].rf[j].id);
    printf("\n");
  }

  while(try_stop_xapp_api() == false)
    usleep(1000);

  printf("xApp finalizado com SUCESSO\n");
  return 0;
}
```

**Explicação linha a linha — bloco dos includes:**

- `#include "../../../../src/xApp/e42_xapp_api.h"` → importa a API principal do xApp: é daqui que vêm `fr_args_t`, `init_fr_args()`, `init_xapp_api()`, `e2_nodes_xapp_api()` e `try_stop_xapp_api()`. O caminho relativo (`../../../../`) sobe do diretório do xApp até a pasta `src/` do FlexRIC.
- `#include "../../../../src/util/alg_ds/alg/defer.h"` → importa a macro `defer`, que executa um trecho de código automaticamente quando a função termina (parecido com o `defer` de Go). Usamos ela para liberar memória sem esquecer.
- `#include "../../../../src/util/ngran_types.h"` → importa os tipos de nó RAN (`ngran_node_t`) e a função `get_ngran_name()`, que traduz o tipo do nó (gNB, gNB-CU, gNB-DU) em texto legível.
- `#include <stdlib.h>` → biblioteca padrão C (conversões, `exit`, alocação).
- `#include <stdio.h>` → biblioteca de entrada/saída: nos dá o `printf`.
- `#include <unistd.h>` → nos dá `sleep()` e `usleep()` (pausas em segundos e microssegundos).
- `#include <signal.h>` → permite tratar sinais do sistema (Ctrl+C). A API do xApp usa por baixo dos panos.
- `#include <assert.h>` → nos dá o `assert()`, que aborta o programa se uma condição for falsa (usado para garantir que há nós E2 conectados).

**Explicação linha a linha — início do main e conexão:**

- `int main(int argc, char *argv[])` → ponto de entrada do programa. `argc`/`argv` recebem os argumentos da linha de comando (como `-c flexric.conf`).
- `fr_args_t args = init_fr_args(argc, argv);` → cria a estrutura `args` que guarda todas as configurações, preenchida lendo os argumentos da linha de comando. É o que faz o `-c flexric/flexric.conf` e o `-p flexric_lib/` funcionarem quando você executar o xApp.
- `init_xapp_api(&args);` → **conecta o xApp ao nearRT-RIC** via SCTP. Recebe o endereço de `args` (`&args`). A partir daqui o xApp é um cliente RIC válido.
- `sleep(1);` → espera 1 segundo para dar tempo de a conexão E2 se estabelecer antes de consultar os nós.

**Explicação linha a linha — descoberta dos nós E2:**

- `e2_node_arr_xapp_t nodes = e2_nodes_xapp_api();` → pergunta ao RIC quais nós E2 estão conectados e guarda a lista em `nodes`. Cada elemento contém o ID do nó, o tipo (gNB monolítica ou split CU/DU) e as funções RAN que ele suporta (KPM, RC...).
- `defer({ free_e2_node_arr_xapp(&nodes); });` → registra que, ao sair da função `main` (de qualquer forma, até com `return`), a memória de `nodes` será liberada. Evita vazamento de memória sem precisar chamar `free` manualmente em cada ponto de saída.
- `assert(nodes.len > 0);` → verifica se pelo menos um nó E2 está conectado. Se `nodes.len` for 0 (nenhum gNB do NS-3 conectado ao RIC), o programa aborta aqui com mensagem de erro — é o sinal clássico de que o NS-3 não está rodando.

**Explicação linha a linha — impressão das informações dos nós:**

- `printf("xApp conectado! Nos E2 = %d\n", nodes.len);` → mostra quantos nós E2 foram encontrados. `%d` é substituído pelo número de nós.
- `for (size_t i = 0; i < nodes.len; i++) {` → laço que percorre cada nó da lista. `i` é o índice do nó atual.
- `ngran_node_t ran_type = nodes.n[i].id.type;` → guarda o tipo do nó atual (gNB, gNB-DU etc.) na variável `ran_type`.
- `printf("No E2 %ld: nb_id %d, mcc %d, mnc %d, tipo %s\n", ...)` → imprime a identidade do nó: índice, ID da estação (`nb_id`), código de país (`mcc`), operadora (`mnc`) e o nome do tipo.
- `i,` → argumento do `%ld` (índice do nó).
- `nodes.n[i].id.nb_id.nb_id,` → argumento do `%d`: o identificador numérico da estação base.
- `nodes.n[i].id.plmn.mcc,` → argumento do `%d`: o Mobile Country Code da rede.
- `nodes.n[i].id.plmn.mnc,` → argumento do `%d`: o Mobile Network Code (operadora).
- `get_ngran_name(ran_type));` → argumento do `%s`: converte o tipo numérico do nó em texto (ex: `ng-eNB`, `gNB`).
- `printf("Funcoes RAN suportadas:");` → imprime o início da linha das funções RAN (sem quebrar linha ainda).
- `for (size_t j = 0; j < nodes.n[i].len_rf; j++)` → laço interno que percorre as funções RAN que o nó suporta (ex: KPM com ID 2, RC com ID 3).
- `printf(", %d", nodes.n[i].rf[j].id);` → imprime o ID de cada função RAN, separado por vírgula.
- `printf("\n");` → finaliza a linha das funções RAN com quebra de linha.

**Explicação linha a linha — encerramento:**

- `while(try_stop_xapp_api() == false)` → tenta encerrar o xApp de forma limpa. A função pode falhar se ainda houver mensagens em trânsito, então ela é chamada em laço.
- `usleep(1000);` → espera 1 milissegundo entre cada tentativa de parada, sem consumir CPU à toa.
- `printf("xApp finalizado com SUCESSO\n");` → mensagem final confirmando que tudo terminou bem.
- `return 0;` → retorna 0 para o sistema operacional, indicando execução sem erros.

### 16.4 Passo 2 — Criar o CMakeLists.txt Local

Agora criamos o arquivo que ensina o CMake a compilar o xApp:

```bash
nano /home/robert/orange_nuclear/flexric/examples/xApp/c/meu_xapp/CMakeLists.txt
```
**Explicação linha a linha:**
- `nano <arquivo>` → cria/abre o arquivo de build do nosso xApp. O nome `CMakeLists.txt` é obrigatório — é o nome que o CMake procura.

**Conteúdo completo do arquivo `CMakeLists.txt`:**

```cmake
add_executable(xapp_meu_xapp
  xapp_meu_xapp.c
  ../../../../src/util/alg_ds/alg/defer.c
  )

target_link_libraries(xapp_meu_xapp
  PUBLIC
  e42_xapp
  -pthread
  -lsctp
  -ldl
  )
```

**Explicação linha a linha:**

- `add_executable(xapp_meu_xapp` → diz ao CMake: "quero gerar um programa executável chamado `xapp_meu_xapp`". O nome que você der aqui será o nome do binário.
- `xapp_meu_xapp.c` → primeiro arquivo-fonte do executável: o nosso código principal criado no Passo 1.
- `../../../../src/util/alg_ds/alg/defer.c` → segundo arquivo-fonte: a implementação da macro `defer` que usamos no código (é um arquivo C compartilhado com os outros exemplos, por isso o caminho relativo).
- `)` → fecha a lista de fontes do `add_executable`.
- `target_link_libraries(xapp_meu_xapp` → inicia a lista de bibliotecas que o executável precisa na hora de linkar (conectar as funções externas que ele usa).
- `PUBLIC` → visibilidade das dependências: público significa que quem usar este alvo também herda essas bibliotecas.
- `e42_xapp` → a biblioteca principal do FlexRIC (E2AP v1). Contém toda a implementação da API que importamos no `e42_xapp_api.h`.
- `-pthread` → ativa a biblioteca de threads POSIX (a API do xApp roda threads internas para gerenciar a conexão).
- `-lsctp` → ativa a biblioteca SCTP, o protocolo de transporte usado pela interface E2 entre RIC e gNBs.
- `-ldl` → ativa a biblioteca de carregamento dinâmico (`dlopen`), usada pelo FlexRIC para carregar os Service Models (`.so`) em tempo de execução.
- `)` → fecha a lista de bibliotecas.

### 16.5 Passo 3 — Registrar o xApp no CMakeLists Pai

O CMake precisa saber que o novo diretório existe. Registramos ele no arquivo pai:

```bash
nano /home/robert/orange_nuclear/flexric/examples/xApp/c/CMakeLists.txt
```
**Explicação linha a linha:**
- `nano <arquivo>` → abre o CMakeLists que registra todos os xApps do projeto (é o mesmo arquivo que já contém `xapp_slicer`, `xapp_energy_saver` e `xapp_tasam_actuator`).

**Linha a adicionar ao final do arquivo:**

```cmake
add_subdirectory(meu_xapp)
```

**Explicação linha a linha:**

- `add_subdirectory(meu_xapp)` → diz ao CMake: "entre no diretório `meu_xapp` e processe o `CMakeLists.txt` de lá também". É o que faz nosso xApp entrar no build do projeto.

**Alternativa (estilo usado pelos xApps do GreenRAN):** em vez de criar `CMakeLists.txt` no subdiretório, você pode registrar direto no arquivo pai:

```cmake
add_executable(xapp_meu_xapp meu_xapp/xapp_meu_xapp.c)
target_link_libraries(xapp_meu_xapp e42_xapp sctp pthread)
```

**Explicação linha a linha:**
- `add_executable(xapp_meu_xapp meu_xapp/xapp_meu_xapp.c)` → cria o executável apontando direto para o caminho do código-fonte, sem precisar de `CMakeLists.txt` local.
- `target_link_libraries(xapp_meu_xapp e42_xapp sctp pthread)` → linka as mesmas bibliotecas essenciais em uma única linha (forma compacta).
- **Observação:** se usar esta alternativa, o `defer.c` precisa entrar na lista de fontes ou o `defer` não vai linkar. Por isso a forma do `helloworld` (com CMakeLists local) é a recomendada para iniciantes.

Escolha **uma** das duas formas, não as duas.

### 16.6 Passo 4 — Compilar o xApp

```bash
cd /home/robert/orange_nuclear
```
**Explicação linha a linha:**
- `cd <diretório>` → vai para a raiz do projeto, de onde os caminhos relativos (`flexric/...`) funcionam.

```bash
cmake --build flexric/build_e2ap_v1 --target xapp_meu_xapp -j$(nproc)
```
**Explicação linha a linha:**
- `cmake --build` → comando moderno do CMake para compilar (equivale a rodar `make` no diretório de build).
- `flexric/build_e2ap_v1` → diretório de build do FlexRIC já configurado (build com E2AP v1, o mesmo usado por todos os xApps do projeto).
- `--target xapp_meu_xapp` → compila apenas o nosso alvo, sem recompilar o FlexRIC inteiro.
- `-j$(nproc)` → usa todos os núcleos de CPU disponíveis para compilar mais rápido (`nproc` retorna o número de núcleos).

**Forma equivalente com `make` (também funciona):**

```bash
make -C flexric/build_e2ap_v1 xapp_meu_xapp -j$(nproc)
```
**Explicação linha a linha:**
- `make -C <dir>` → roda o `make` dentro do diretório de build sem precisar sair da raiz do projeto.
- `xapp_meu_xapp` → nome do alvo (o mesmo definido no `add_executable`).
- **Nota:** o CMake detecta sozinho que os `CMakeLists.txt` mudaram e reconfigura o build automaticamente antes de compilar. Não precisa rodar `cmake ..` manualmente.

**Saída esperada (resumida):**

```
[ 50%] Building C object examples/xApp/c/meu_xapp/CMakeFiles/xapp_meu_xapp.dir/xapp_meu_xapp.c.o
[100%] Linking C executable ../../../../../build_e2ap_v1/examples/xApp/c/xapp_meu_xapp
[100%] Built target xapp_meu_xapp
```

### 16.7 Passo 5 — Executar e Testar o xApp

Verifique que o binário foi gerado:

```bash
ls -la /home/robert/orange_nuclear/flexric/build_e2ap_v1/examples/xApp/c/xapp_meu_xapp
```
**Explicação linha a linha:**
- `ls -la <arquivo>` → lista o arquivo com detalhes. Se aparecer na listagem (com `x` de executável), o build funcionou.

Com o **nearRT-RIC rodando** (seção 7.2) e o **NS-3 conectado** (seção 1.1), execute:

```bash
cd /home/robert/orange_nuclear
```
**Explicação linha a linha:**
- `cd <diretório>` → volta para a raiz do projeto.

```bash
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
```
**Explicação linha a linha:**
- `export LD_LIBRARY_PATH=...` → informa ao sistema onde encontrar as bibliotecas `.so` em tempo de execução.
- `flexric/build_e2ap_v1/src/ric` → bibliotecas internas do RIC.
- `flexric_lib` → bibliotecas dos Service Models (KPM, RC).
- `flexric/build_e2ap_v1/src/xApp` → bibliotecas da API do xApp.
- `:$LD_LIBRARY_PATH` → preserva o que já existia na variável, adicionando no início.
- **Nota:** este comando é o mesmo da seção 7.1 e vale apenas para o terminal atual.

```bash
./flexric/build_e2ap_v1/examples/xApp/c/xapp_meu_xapp -c flexric/flexric.conf -p flexric_lib/
```
**Explicação linha a linha:**
- `./flexric/build_e2ap_v1/examples/xApp/c/xapp_meu_xapp` → executa o binário recém-compilado.
- `-c flexric/flexric.conf` → (config) aponta o arquivo de configuração, de onde o xApp descobre o IP/porta do RIC. Lido pelo `init_fr_args()`.
- `-p flexric_lib/` → (path) aponta o diretório dos Service Models `.so` que o xApp carrega dinamicamente.

**Saída esperada:**

```
xApp conectado! Nos E2 = 1
No E2 0: nb_id 10, mcc 1, mnc 1, tipo gNB
Funcoes RAN suportadas: 2, 3
xApp finalizado com SUCESSO
```

Se aparecer `Assertion nodes.len > 0 failed`, o NS-3 não está conectado ao RIC — suba o NS-3 primeiro (seção 1.1).

### 16.8 Evoluindo o xApp (Próximos Passos)

O xApp do exemplo conecta e lista nós, mas ainda não faz nada útil. Os próximos níveis de evolução, na ordem:

| Nível | O que fazer | Referência no projeto |
|-------|-------------|----------------------|
| 1. Ler KPIs | Subscrever relatórios KPM periódicos (latência, throughput, PRB) | `flexric/examples/xApp/c/slicer/subscription_slicer.c` |
| 2. Tomar decisões | Aplicar regras sobre os KPIs (ex: SLA de latência) | `flexric/examples/xApp/c/slicer/xapp_slicer.c` |
| 3. Atuar na rede | Enviar comandos de controle RAN (RC) para o NS-3 | `flexric/examples/xApp/c/energy_saver/xapp_energy_saver.c` |
| 4. Integrar com rApp | Ler/escrever intents em `/tmp/xapp_intents/*.txt` | `flexric/examples/xApp/c/tasam_actuator/xapp_tasam_actuator.c` |

**Dica de estudo:** compare o `xapp_meu_xapp.c` com o `xapp_slicer.c` — a estrutura do `main` é idêntica; a diferença é a assinatura de serviços KPM (`report_service_style`) e o laço de processamento das indicações que chegam do RIC.

### 16.9 Solução de Problemas no Build

| Erro | Causa provável | Solução |
|------|----------------|---------|
| `fatal error: e42_xapp_api.h: No such file` | Caminho relativo do include errado | Confira que o `#include` usa `../../../../src/xApp/...` (4 níveis acima) |
| `undefined reference to defer` | `defer.c` fora da lista de fontes | Adicione `../../../../src/util/alg_ds/alg/defer.c` no `add_executable` |
| `cannot find -lsctp` | Biblioteca SCTP não instalada | `sudo apt install libsctp-dev` e rode o build de novo |
| `No rule to make target 'xapp_meu_xapp'` | xApp não registrado no CMakeLists pai | Refaça o Passo 3 (seção 16.5) |
| `Assertion nodes.len > 0 failed` | Build ok, mas sem nós E2 | NS-3 não está rodando/conectado — veja seções 1.1 e 11.2 |
| `error while loading shared libraries` | `LD_LIBRARY_PATH` não exportado | Rode o `export` da seção 16.7 antes de executar |

### 16.10 Checklist Final

Resumo dos 5 passos para construir qualquer xApp do zero:

```bash
# 1. Criar o código
mkdir -p flexric/examples/xApp/c/meu_xapp
nano flexric/examples/xApp/c/meu_xapp/xapp_meu_xapp.c

# 2. Criar o CMakeLists local
nano flexric/examples/xApp/c/meu_xapp/CMakeLists.txt

# 3. Registrar no CMakeLists pai (adicionar add_subdirectory)
nano flexric/examples/xApp/c/CMakeLists.txt

# 4. Compilar
cmake --build flexric/build_e2ap_v1 --target xapp_meu_xapp -j$(nproc)

# 5. Executar (com RIC e NS-3 rodando)
export LD_LIBRARY_PATH=/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/ric:/home/robert/orange_nuclear/flexric_lib:/home/robert/orange_nuclear/flexric/build_e2ap_v1/src/xApp:$LD_LIBRARY_PATH
./flexric/build_e2ap_v1/examples/xApp/c/xapp_meu_xapp -c flexric/flexric.conf -p flexric_lib/
```

**Explicação linha a linha:**
- Cada bloco `#` → comentário indicando o passo correspondente às seções 16.3 a 16.7.
- Os comandos → resumo exato do que foi detalhado nas seções anteriores desta seção 16.

---

**Data:** 21 de Setembro de 2026
**Versão:** 1.2
**Projeto:** GreenRAN O-RAN
**Autor:** Gerado automaticamente com base na análise do sistema
**Atualizações:** Adicionada seção completa sobre rApps e xApps; adicionada seção 16 — tutorial de construção de xApp do zero com explicação linha a linha
