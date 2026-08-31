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

---

**Data:** 24 de Agosto de 2026
**Versão:** 1.1
**Projeto:** GreenRAN O-RAN
**Autor:** Gerado automaticamente com base na análise do sistema
**Atualizações:** Adicionada seção completa sobre rApps e xApps
