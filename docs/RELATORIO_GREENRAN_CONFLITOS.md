# RELATÓRIO COMPLETO - Sistema GreenRAN: Detecção de Conflitos em O-RAN usando EE-DRL-GreenRAN e ARMD-GreenRAN

## Sistema Inteligente de Gerenciamento de Conflitos em Arquitetura O-RAN para Otimização de Redes 5G/6G

**Autor:** Robert Freitas
**Data:** Maio 2026
**Instituição:** UTFPR - Universidade Tecnológica Federal do Paraná
**Versão:** 3.1 (Completa - A3C → SAC Migration)

---

## Índice

1. [Resumo Executivo](#1-resumo-executivo)
2. [Fundamentação Teórica](#2-fundamentação-teórica)
3. [Arquitetura do Sistema](#3-arquitetura-do-sistema)
4. [Aplicações (Apps)](#4-aplicações-apps)
5. [xApps](#5-xapps)
6. [Regras de Decisão](#6-regras-de-decisão)
7. [Pipeline de Conflitos](#7-pipeline-de-conflitos)
8. [Sistema de Machine Learning](#8-sistema-de-machine-learning)
9. [EE-DRL-GreenRAN (Legado SBiLSTM + A3C)](#9-ee-drl-greenran-legado-sbilstm--a3c)
9A. [CAORA-SAC/AWAC (Nova Geração)](#9a-caora-sacawac-nova-geração)
10. [ARMD-GreenRAN (GraphSAGE)](#10-armd-greenran-graphsage)
11. [Data Lake](#11-data-lake)
12. [Protocolo de Energia](#12-protocolo-de-energia)
13. [Interface A1](#13-interface-a1)
14. [Dashboard e Monitoramento](#14-dashboard-e-monitoramento)
15. [Scheduler e Automação](#15-scheduler-e-automação)
16. [Trilha Article00](#16-trilha-article00)
17. [Resultados Consolidados](#17-resultados-consolidados)
18. [Guia de Execução](#18-guia-de-execução)
19. [Status do Sistema](#19-status-do-sistema)
20. [Arquitetura Alvo](#20-arquitetura-alvo)
21. [Trabalhos Futuros](#21-trabalhos-futuros)
22. [Referências e Glossário](#22-referências-e-glossário)

---

## 1. RESUMO EXECUTIVO

### 1.1 Contexto

As redes 5G/6G e a arquitetura O-RAN (Open Radio Access Network) representam a evolução das redes de acesso de rádio, permitindo maior flexibilidade através de componentes definidos por software. A coexistência de múltiplas aplicações (xApps) com diferentes requisitos de Qualidade de Serviço (QoS) e o rApp central (Resource Optimizer) cria um ambiente complexo de tomada de decisão onde recursos de rede devem ser alocados entre objetivos frequentemente conflitantes.

O sistema GreenRAN implementa uma arquitetura completa de gerenciamento inteligente que combina:

- **Simuladores:** ns-3 para rede móvel e CARLA para cenários veiculares
- **Machine Learning:** RF/XGBoost com 89.3% de acurácia, 3 classes (ALLOWED, BLOCKED, CONDITIONAL)
- **EE-DRL-GreenRAN (Legado):** Trilha DRL baseada em SBiLSTM + A3C para arbitragem operacional (substituída)
- **CAORA-SAC (Nova Geração):** Alocação compartilhada de recursos RAN/AI com SAC/AWAC, estado [d_ran, d_ai, r_ran, r_ai], baseado em Lotfi et al. (2025) [arXiv:2511.15002]
- **ARMD-GreenRAN:** GraphSAGE para aprendizado de padrões de conflito (F1=1.0)
- **Pipeline Automático:** Coleta, exportação, treinamento e inferência
- **Data Lake:** SQLite com 7 tabelas, 13.703 eventos de conflito
- **Dashboard:** Interface web (Flask) + Grafana para monitoramento em tempo real

### 1.2 Problema

O gerenciamento tradicional de recursos em O-RAN enfrenta conflitos entre três aplicações principais com objetivos distintos:

| Aplicação | xApp | Requisito | SLA |
|-----------|------|-----------|-----|
| **App1 - Vigilância** | xApp1-RANSlicer | Throughput ≥ 25 Mbps | 3 câmeras ativas |
| **App2 - Monitoramento** | xApp2-EnergySaver | 17/17 sensores, loss < 4% | Latência < 50ms |
| **App3 - Veicular** | xApp3-VehicleControl | Latência < 50ms | V2X crítico |

Estes objetivos frequentemente entram em conflito porque:
- O espectro é limitado (Resource Blocks finitos)
- A potência de transmissão é restrita
- A energia da rede é finita
- Prioridades podem sobrepor-se

### 1.3 Solução Proposta

O sistema GreenRAN propõe uma arquitetura multi-camada completa com 6 estágios de decisão hierárquica:

1. **Trend Analysis** - Análise de tendência (slope, aceleração)
2. **Pattern Engine** - Detecção de padrões (SMA, EMA, sazonal)
3. **CVaR/Variance** - Conditional Value at Risk por UE
4. **ML Predictor** - Random Forest (89.3%) + XGBoost (90.1%)
5. **Agent-AL** - Interface de intenções OpenRAN
6. **Arbiter Final** - Consolida todas as decisões + DRL

### 1.4 Principais Resultados

| Métrica | Baseline (Sem IA) | Heurístico | EE-DRL-GreenRAN | ARMD-GreenRAN (GraphSAGE) |
|---------|-------------------|------------|---------------|---------------------------|
| **F1-Score** | N/A | 0.45 | 0.65 | **1.0** |
| **Precision** | N/A | 0.45 | 0.70 | **1.0** |
| **Recall** | N/A | 0.77 | 0.60 | **1.0** |
| **Conflitos Coletados** | 0 | 600 | 2.000 | **2.896** |
| **Eventos no Data Lake** | 0 | 600 | 10.000 | **13.703** |
| **Cenários Implementados** | 0 | 2 | 5 | **10** |
| **Latência Decisão (rApp)** | N/A | N/A | **1.3ms** | **<10ms** |
| **Tempo Treino (GraphSAGE)** | N/A | N/A | 2h | **1min** |
| **Conformidade O-RAN** | N/A | N/A | ✅ | ✅ (<10ms) |

### 1.5 Destaques da Versão 3.0

- **Arquitetura Completa:** ns-3.42 + CARLA 0.9.16 integrados
- **3 Aplicações:** App1 (Vigilância), App2 (Monitoramento), App3 (Veicular)
- **xApps Funcionais:** RANSlicer, EnergySaver, VehicleControl
- **EE-DRL-GreenRAN (Legado):** DRL com SBiLSTM + A3C, runtime improvements (warmup, confidence, trace) — congelado
- **CAORA-SAC (Migração):** Interface RL genérica (`rapp_rl_policy.py`), ambiente SAC inspirado em Lotfi et al. (2025) [arXiv:2511.15002], trainer SAC/AWAC offline, modelo de recursos compartilhados (`rapp_sac_resource_model.py`), checkpoints treinados (offline SAC + AWAC). Divergências conhecidas: single-agent (vs MARL), SAM não implementado, AWAC como extensão própria.
- **ML Predictor:** RF/XGBoost com 89.3%, split temporal, poda de features enviesadas
- **ARMD-GreenRAN:** GraphSAGE com F1=1.0, multiseed (5 seeds), 10 cenários
- **Pipeline Automático:** Coleta → Export → Treino → Inferência
- **Data Lake:** 13.703 eventos de conflito, 7 tabelas SQL
- **Dashboard:** Flask (14 rotas) + Grafana (7 painéis)
- **Conformidade O-RAN:** Latência Near-RT RIC 1.3ms (requer 10ms-1000ms)

---

## 2. FUNDAMENTAÇÃO TEÓRICA

### 2.1 Arquitetura O-RAN

O-RAN (Open Radio Access Network) é uma arquitetura de redes de acesso de rádio baseada em software e hardware abertos.

**Componentes de Hardware:**

| Componente | Função | Camada |
|-----------|--------|--------|
| **O-CU (Central Unit)** | Processamento camadas superiores (PDCP) | Não-RT |
| **O-DU (Distributed Unit)** | Processamento tempo real (RLC, MAC, PHY) | Near-RT |
| **O-RU (Radio Unit)** | Transmissão/recepção RF | Tempo Real |

**Componentes de Software:**

| Componente | Função | Latência |
|------------|--------|----------|
| **Non-RT RIC** | Otimização de longo prazo (>1s) | >1000ms |
| **Near-RT RIC** | Otimização quase tempo real (10ms-1s) | 10-1000ms |
| **xApps** | Aplicações específicas por domínio | 10-100ms |
| **rApp** | Aplicação central de arbitragem | >1000ms |

### 2.2 Simulador ns-3 (Rede Móvel 5G)

```python
ns3_config = {
    'num_bs': 19,                    # 19 estações base (hexagonal)
    'num_ues_per_bs': 15,            # 15 usuários por célula
    'total_users': 285,              # 285 usuários total
    'bandwidth_mhz': 20,             # 20 MHz
    'frequency_ghz': 3.5,           # n78 (3.5 GHz)
    'simulation_time_s': 200000,    # ~55 horas
    'interval_s': 1,                 # 1 segundo por passo
    'traffic_types': {
        'voip': {'bps': 96000, 'percentage': 0.60},
        'video': {'bps': 5000000, 'percentage': 0.30},
        'iot': {'bps': 24000000, 'percentage': 0.10}
    },
    'mobility_profiles': {
        'pedestrian': {'speed_mps': 5, 'percentage': 0.60},
        'vehicle_urban': {'speed_mps': 25, 'percentage': 0.30},
        'vehicle_highway': {'speed_mps': 50, 'percentage': 0.10}
    }
}
```

**Métricas Geradas pelo ns-3:**

| Métrica | Descrição | Unidade |
|--------|-----------|---------|
| `throughput_mbps` | Taxa de transferência por UE | Mbps |
| `latency_ms` | Latência de ponta a ponta | ms |
| `packet_loss_percent` | Porcentagem de pacotes perdidos | % |
| `rsrp_dbm` | Reference Signal Received Power | dBm |
| `sinr_db` | Signal-to-Interference-plus-Noise Ratio | dB |
| `prb_utilization` | Utilização de Physical Resource Blocks | % |
| `cvar_us` | Conditional Value at Risk (P95 latência) | µs |

### 2.3 CARLA (Simulador Veicular)

```python
carla_config = {
    'version': '0.9.16',
    'scenarios': {
        'urban': {'num_vehicles': 50, 'avg_speed_kmh': 30, 'spawn_points': 20},
        'highway': {'num_vehicles': 30, 'avg_speed_kmh': 90, 'lanes': 4},
        'emergency': {'num_vehicles': 10, 'emergency_vehicles': 2, 'response_time_s': 30}
    },
    'metrics': {
        'v2v_latency_ms': 'Vehicle-to-Vehicle latency',
        'v2v_packet_loss': 'V2V packet loss percentage',
        'ego_autonomy_percent': 'Ego vehicle autonomy level',
        'safety_distance_m': 'Safety distance to front vehicle',
        'risk_level': 'HIGH | MEDIUM | LOW'
    }
}
```

### 2.4 Divergência Metodológica (GreenRAN vs Article00)

O projeto mantém **duas trilhas metodológicas**:

| Aspecto | GreenRAN Experimental | Article00 Faithful |
|---------|----------------------|-------------------|
| **Base** | Dataset de conflitos exportado | Grafo temporal por passo de tempo |
| **Grafo** | Co-ocorrência de linhas para suporte | Features temporais de parâmetros/KPIs |
| **Features** | Feature engineering agregado por nó | Embeddings + correlação |
| **Decoder** | Supervisionado (link prediction) | Reconstrução de adjacência |
| **Status** | Operacional, F1=1.0 | Experimental, F1=1.0 (200 epochs) |

Recomendação: manter ambas as trilhas para evitar regressão e permitir comparação honesta.

---

## 3. ARQUITETURA DO SISTEMA

### 3.1 Diagrama de Arquitetura

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                           ARQUITETURA GREENRAN v3.0                           │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │   App1       │  │   App2       │  │   App3       │  │  Dashboard  │       │
│  │  Vigilância  │  │ Monitoramento│  │   Veicular  │  │   (Flask)  │       │
│  │  (Cameras)  │  │    (IoT)     │  │   (V2X)      │  │   :5000    │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         │                 │                 │                 │                │
│         ▼                 ▼                 ▼                 ▼                │
│  ┌──────────────────────────────────────────────────────────────────┐        │
│  │                        SIMULADORES                                 │       │
│  │  ┌─────────────────┐          ┌────────────────────────────┐   │       │
│  │  │    ns-3.42      │          │         CARLA 0.9.16         │   │       │
│  │  │ (Rede Móvel 5G) │          │    (Simulador Veicular)      │   │       │
│  │  └────────┬────────┘          └──────────────┬─────────────┘   │       │
│  └───────────┼────────────────────────────────────┼─────────────────┘       │
│              ▼                                     ▼                          │
│  ┌──────────────────────────────────────────────────────────────────┐        │
│  │                    csv_to_metrics.py                              │       │
│  │  • extended_metrics.json → /tmp/xapp_metrics/                   │       │
│  │  • App1: camera_throughput_mbps, camera_latency_ms                │       │
│  │  • App2: connected_sensors, packet_loss_percent                   │       │
│  │  • App3: vehicle_latency_ms, vehicle_packet_loss                │       │
│  └─────────────────────────────┬────────────────────────────────────┘        │
│                                ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────┐        │
│  │   Non-RT RIC Layer - rApp Orchestrator (6 estágios)             │       │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ │       │
│  │  │  Trend   │ │ Pattern  │ │  CVaR/   │ │  ML/DRL  │ │Agent │ │       │
│  │  │ Analysis │ │ Engine   │ │ Variance │ │ Predictor│ │-AL   │ │       │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────┘ │       │
│  │                         │ Arbiter Final                          │       │
│  └──────────────────────────────────────────────────────────────────┘        │
│                                │                                            │
│                                ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────┐        │
│  │              Near-RT RIC (xApps)                                 │       │
│  │  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐  │       │
│  │  │ xApp1-RANSlicer │ │ xApp2-EnergySaver│ │ xApp3-VehicleCtrl│  │       │
│  │  │   (Slicing)      │ │    (Energy)      │ │   (Vehicle)      │  │       │
│  │  └──────────────────┘ └──────────────────┘ └──────────────────┘  │       │
│  └──────────────────────────────────────────────────────────────────┘        │
│                                │                                            │
│                                ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────┐        │
│  │                      DATA LAKE (SQLite WAL)                        │       │
│  │  • /tmp/rapp_data_lake.db   • 7 tabelas   • 9 índices            │       │
│  │  • conflict_events: 13.703 registros                              │       │
│  │  • metrics: KPIs de todas as apps                                 │       │
│  │  • decisions: Histórico de decisões do rApp                       │       │
│  └──────────────────────────────────────────────────────────────────┘        │
│                                                                                │
│  ┌──────────────────────────────────────────────────────────────────┐        │
│  │                    MONITORING STACK                                │       │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐        │       │
│  │  │  Grafana     │◄───│  InfluxDB    │◄───│ Push Stats   │        │       │
│  │  │  :3001       │    │  :8086       │    │  Python      │        │       │
│  │  └──────────────┘    └──────────────┘    └──────────────┘        │       │
│  └──────────────────────────────────────────────────────────────────┘        │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Fluxo de Dados

```
1. ns-3 Stats Files (DlPdcpStats.txt, DlMacStats.txt, etc.)
   ↓
2. csv_to_metrics.py (parseia arquivos .txt)
   ↓
3. /tmp/xapp_metrics/extended_metrics.json
   ↓
4. rapp_orchestrator.py (lê métricas)
   ↓
5. make_decision() (6 estágios de decisão)
   ↓
6. Data Lake (grava no SQLite)
   ↓
7. ML Predictor (consulta banco + modelo)
   ↓
8. Energy Command (envia para xApp)
   ↓
9. A1 Policy (envia para Near-RT RIC)
   ↓
10. xApp Energy Saver (executa comando)
```

### 3.3 Cadeia de Inicialização do rApp

```python
# 1. Data Lake (SQLite WAL mode)
self.data_lake = DataLake()

# 2. Pattern Engine (ML moderado)
self.pattern_engine = PatternRecognition(self.data_lake)

# 3. Trend Analysis (Slope/Predição)
self.trend_analysis = TrendAnalysis(self.data_lake)

# 4. ML Predictor (Random Forest + XGBoost com acesso DB)
self.ml_predictor = MLPredictor(data_lake=self.data_lake)

# 5. RL Policy Interface (genérica: legado A3C ou SAC/AWAC)
self.rl_policy = build_runtime_rl_policy()  # GREENRAN_RL_POLICY=legacy_a3c|sac|awac

# 6. SAC Resource Model (modelo de demanda compartilhada RAN/AI)
self.sac_resource_model = compute_shared_resource_snapshot

# 7. Agent-Al (Tradução de intenções)
self.agent = AgentOpenRAN()

# 8. A1 Interface (Políticas JSON)
self.a1 = A1PolicyInterface()

# 9. XApp Manager (Ciclo de vida)
self.xapp_manager = XAppManager()
```

---

## 4. APLICAÇÕES (APPS)

### 4.1 App1 - Vigilância

| Propriedade | Valor |
|-------------|-------|
| **Backend** | `apps/app1_vigilancia/backend/app.py` |
| **Serviços** | `apps/app1_vigilancia/backend/services.py` |
| **Testes** | `apps/app1_vigilancia/tests/test_app1_api.py` |
| **Maturidade** | Mais alto - mais próximo de produto |
| **Influência rApp** | Forte - domina decisões |

**O que faz:** Representa o caso de uso de vigilância com cadastro/configuração de câmeras, ingestão/organização de mídia, geração de eventos, snapshot operacional por câmera, APIs e interface visual.

**SLA:**
- Throughput ≥ 25 Mbps
- Latência < 50ms (crítico: ≥ 80ms)
- 3 câmeras ativas (CAM-01, CAM-02, CAM-03)

```python
class RANSlicerApp:
    def __init__(self):
        self.cameras = {
            'CAM-01': {'throughput_mbps': 0, 'latency_ms': 0},
            'CAM-02': {'throughput_mbps': 0, 'latency_ms': 0},
            'CAM-03': {'throughput_mbps': 0, 'latency_ms': 0}
        }
        self.sla_throughput = 25.0
        self.sla_latency = 50.0

    def allocate_resources(self, available_prb):
        sorted_cameras = sorted(
            self.cameras.items(),
            key=lambda x: x[1]['throughput_mbps']
        )
        for camera, _ in sorted_cameras:
            if available_prb >= self.min_prb_per_camera:
                self.cameras[camera]['prb_allocated'] = self.min_prb_per_camera
                available_prb -= self.min_prb_per_camera
        return self.cameras
```

**Pontos fortes:** App mais completo, melhor acabamento funcional, conversa com a narrativa principal do GreenRAN, backend/frontend/testes consolidados.
**Limites:** IA ainda mais simulada/orquestrada do que pipeline de visão computacional de produção.

### 4.2 App2 - Monitoramento Ambiental

| Propriedade | Valor |
|-------------|-------|
| **Backend** | `apps/app2_monitoramento/backend/app.py` |
| **Serviços** | `apps/app2_monitoramento/backend/services.py` |
| **Testes** | `apps/app2_monitoramento/tests/test_app2_api.py` |
| **Maturidade** | Médio - MVP operacional |
| **Influência rApp** | Boa - influencia sob degradação |

**O que faz:** Monitoramento ambiental/mMTC com snapshot ambiental agregado, lista de sensores, avaliação de SLA, histórico e relatórios operacionais.

**Estado atual dos sensores:**
- 17 sensores com tipos semânticos reais: `temperature`, `humidity`, `soil_moisture`, `soil_temp`, `soil_conductivity`
- Conectividades: `5g_native`, `5g_redcap`
- Gateways: `GW-5G-01`, `GW-5G-02`, `GW-5G-03`

**SLA:**
- 17/17 sensores conectados
- Packet loss < 4%
- Latência < 50ms
- Delivery rate ≥ 95%

```python
class EnergySaverApp:
    def __init__(self):
        self.max_sensors = 17
        self.sla_connected = 17
        self.sla_packet_loss = 4.0
        self.sla_delivery = 95.0

    def optimize_energy(self, network_load):
        if network_load > 0.8:
            return 'POWER_SAVING_MODE'
        elif network_load > 0.5:
            return 'BALANCED_MODE'
        else:
            return 'PERFORMANCE_MODE'
```

**Pontos fortes:** Evoluiu de mock para integração real com ns-3, sensores com semântica coerente, snapshot consistente, lógica warning/blocked funcional.
**Limites:** App mais enxuto, focado em monitoramento/SLA sem analítica profunda por sensor.

### 4.3 App3 - Veicular (Arquitetura Proposta)

| Propriedade | Valor |
|-------------|-------|
| **Status** | Proposto (não implementado) |
| **Backend** | `apps/app3_veicular/backend/app.py` (sugerido) |
| **Domínio** | Veículos conectados, V2X, autonomia |
| **xApp** | xApp-VehicleSafety (Near-RT actuation) |

**Arquitetura recomendada:**

```
CARLA → ns-3 → csv_to_metrics.py → App3-Veicular → rApp → xApp-VehicleSafety
```

**Dados mínimos por veículo:**
- `vehicle_id`, `vehicle_role`, `imsi`
- `x`, `y`, `z`, `speed_mps`, `heading_deg`
- `lane_id`, `waypoint_id`
- `autonomy_state` (normal, degraded, manual)
- `risk_state` (low, medium, high)
- `latency_ms`, `packet_loss_percent`, `throughput_mbps`

**API mínima sugerida:**
- `GET /api/vehicles` - lista veículos ativos
- `GET /api/vehicles/ego` - estado do ego vehicle
- `GET /api/vehicles/summary` - resumo operacional
- `GET /api/vehicles/events` - eventos recentes
- `GET /api/vehicles/health` - saúde do backend

**Snapshot operacional:**
```json
{
  "simulation": {"source": "carla_ns3_combined", "timestamp_iso": "..."},
  "vehicles": {
    "total_vehicles": 5, "ego_present": true,
    "high_risk_vehicles": 0, "medium_risk_vehicles": 1,
    "degraded_autonomy_vehicles": 0
  },
  "network": {"max_latency_ms": 42.0, "max_packet_loss_percent": 0.8},
  "sla": {"runtime_status": "ok"}
}
```

### 4.4 Matriz de Prioridade e SLA

Hierarquia prática do rApp:

1. **camera** (App1) - Prioridade máxima
2. **vehicle** (App3 / veicular) - Segurança e autonomia
3. **sensors** (App2) - Continuidade de monitoramento
4. **economia geral** - Eficiência energética

| Domínio | Prioridade | Sinais principais | Crítico | Guarda / Warning |
|---------|-----------|-------------------|---------|------------------|
| `camera` | 1 | throughput + latência | throughput < 25 Mbps ou latência ≥ 80 ms | 25-30 Mbps ou 60-80 ms |
| `vehicle` | 2 | risco + autonomia + latência + perda | risk_state=high, autonomy_state!=normal, latência ≥ 100 ms, perda ≥ 5% | risk_state=medium, latência ≥ 50 ms, perda ≥ 2% |
| `sensors` | 3 | conectividade + entrega + latência + bateria | conectividade < 85%, perda ≥ 10%, entrega < 90%, latência ≥ 1000 ms, bateria < 15% | conectividade < 90%, perda ≥ 5%, entrega < 95%, latência ≥ 500 ms, bateria < 25% |

---

## 5. XAPPS

### 5.1 xApp1-RANSlicer (Vigilância)

| Propriedade | Valor |
|-------------|-------|
| **Prioridade** | Sempre ativo (máxima) |
| **Função** | Monitoramento de SLA |
| **SLA** | < 100ms |
| **Estados** | NORMAL, CRITICAL, IDLE |

**Fluxo:** Quando CRITICAL → Sinaliza para rApp → rApp pode sobrescrever com BLOCKED.

### 5.2 xApp2-EnergySaver (Monitoramento IoT)

| Propriedade | Valor |
|-------------|-------|
| **Prioridade** | Controlado pelo rApp |
| **Função** | Execução de comandos de energia |
| **Estados** | FULL_POWER, CONDITIONAL_REDUCE, POWER_DOWN, POWER_DOWN_ECO |
| **Protocolo** | JSON via /tmp/xapp_intents/energy_command.json |

**Fluxo:** rApp Orchestrator → Energy Command (JSON) → /tmp/xapp_intents/energy_command.json → xApp Energy Saver → Executa comando → Registra resultado.

### 5.3 xApp3-VehicleControl (Veicular - Proposto)

| Propriedade | Valor |
|-------------|-------|
| **Função** | Atuação Near-RT para proteção SLA veicular |
| **Ações** | Prioridade de tráfego, modo protegido, política de latência, reforço de fatia, bloqueio de economia agressiva |

```python
class VehicleControlApp:
    def __init__(self):
        self.risk_thresholds = {
            'latency_ms': 50, 'packet_loss_percent': 2, 'min_autonomy_percent': 80
        }

    def assess_risk(self, vehicle_data):
        if vehicle_data['latency_ms'] > 100 or vehicle_data['packet_loss'] > 5:
            return 'CRITICAL'
        elif vehicle_data['latency_ms'] > 50 or vehicle_data['packet_loss'] > 2:
            return 'WARNING'
        return 'SAFE'

    def send_control_message(self, vehicle_id, action):
        return {'vehicle_id': vehicle_id, 'action': action, 'timestamp': time.time()}
```

---

## 6. REGRAS DE DECISÃO

### 6.1 Thresholds de Configuração

| Parâmetro | Valor | Descrição |
|-----------|-------|-----------|
| `CVAR_NORMAL_US` | 60,000 (60ms) | Limite superior da zona normal |
| `CVAR_CRITICAL_US` | 80,000 (80ms) | Zona crítica (risco de SLA) |
| `SLOPE_PREVENTION` | 2.0 ms/s | Threshold de prevenção por slope |
| `SLOPE_TOLERANCE` | 0.01 ms/s | Tolerância de estabilidade |
| `STABILITY_THRESHOLD` | 50 | Score mínimo de estabilidade |
| `DB_OVERRIDE_THRESHOLD` | 0.60 (60%) | Override ML por banco |
| `DB_BOOST_THRESHOLD` | 0.60 (60%) | Boost de confiança ML |
| `WINDOW_MINUTES` | 2 | Janela de consulta ao banco |

### 6.2 Regras Completas

| Regra | Condição | Decisão | Ação | Potência |
|-------|----------|---------|------|----------|
| **R1** | `slicer_state == CRITICAL` | BLOCKED | FULL_POWER | 100% |
| **R2** | `CVaR >= 80ms` | BLOCKED | FULL_POWER | 100% |
| **R3** | `slope > 2ms/s` | BLOCKED (preventivo) | FULL_POWER | 100% |
| **R4a** | `CVaR < 20ms + slope <= 0` | ALLOWED | POWER_DOWN_ECO | 25% |
| **R4b** | `CVaR < 60ms + slope < -0.01` | ALLOWED | POWER_DOWN | 50% |
| **R4c** | `CVaR < 60ms + slope ≈ 0` | ALLOWED | POWER_DOWN | 60% |
| **R4d** | `60-80ms + slope < -0.01` | ALLOWED | POWER_DOWN | 70% |
| **R4e** | `60-80ms + slope ≈ 0` | ALLOWED | POWER_DOWN | 80% |
| **R5** | `CVaR < 60ms + slope > 0.01` | CONDITIONAL | MONITOR | 70% |
| **R6** | `60-80ms + slope > 0.01` | CONDITIONAL | MONITOR | 90% |

### 6.3 Zonas de Operação

| Zona | Condição | Decisão | Ação |
|------|----------|---------|------|
| **Verde (Normal)** | CVaR < 60ms | ALLOWED | Energy Saver pode economizar |
| **Amarela (Prevenção)** | 60ms ≤ CVaR < 80ms | CONDITIONAL | Monitorar, não reduzir |
| **Vermelha (Crítico)** | CVaR ≥ 80ms | BLOCKED | FULL_POWER, proteger SLA |

### 6.4 Classificação de Estado do Slope

| Range do Slope | Estado | Significado |
|----------------|--------|-------------|
| `< -0.01 ms/s` | `improving` | Latência diminuindo |
| `-0.01 a +0.01 ms/s` | `stable` | Latência estável |
| `> +0.01 ms/s` | `worsening` | Latência aumentando |

---

## 7. PIPELINE DE CONFLITOS

### 7.1 Objetivo

Transformar as decisões do rApp em um dataset de conflitos e em um grafo operacional. Passo intermediário entre regras explícitas de arbitragem e o treino do grafo aprendido com GraphSAGE.

### 7.2 Comando de Exportação

```bash
# Exportar 24h de conflitos
python3 scripts/export_conflict_dataset.py --hours 24

# Exportar com caminhos customizados
python3 scripts/export_conflict_dataset.py --hours 6 --dataset /tmp/conflicts_6h.csv --graph /tmp/conflict_graph_6h.json
```

Saídas padrão: `/tmp/greenran_conflict_dataset.csv` e `/tmp/greenran_conflict_graph.json`

### 7.3 Schema do Dataset (CSV)

Cada linha representa um evento de conflito observado:

| Campo | Descrição |
|-------|-----------|
| `source_agent` | Agente ou xApp associado à origem do conflito |
| `parameter` | Parâmetro de controle envolvido |
| `affected_service` | App1, App2 ou outro serviço afetado |
| `affected_kpi` | KPI que cruzou limiar ou faixa de guarda |
| `observed_value` | Valor observado |
| `threshold_value` | Limiar do KPI |
| `observed_delta_from_threshold` | Distância até o limiar |
| `previous_observed_value` | Último valor observado para o mesmo KPI |
| `observed_delta_from_previous` | Variação do KPI em relação ao evento anterior |
| `mitigation_action` | Ação aplicada pelo rApp |
| `latest_energy_command` | Comando de energia mais recente |
| `latest_power_percent` | Percentual de potência |
| `cvar_ms` | CVaR em milissegundos |
| `p95_ms` | Percentil 95 de latência |
| `throughput_mbps` | Throughput global |
| `ml_decision` | Decisão do ML |
| `ml_confidence` | Confiança do ML |
| `ml_predicted_cvar_ms` | CVaR predito pelo ML |

### 7.4 Schema do Grafo (JSON)

Schema `greenran.conflict_graph.v1`:

```
agent → parameter → kpi → rApp-ResourceOptimizer → mitigation → service
```

**Tipos de nós:** `agent`, `parameter`, `kpi`, `arbiter`, `mitigation`, `service`

**Tipos de arestas:** `controls`, `affects`, `belongs_to`, `triggers_arbitration`, `mitigates`, `protects`

### 7.5 Matriz de Adjacência Aprendida

```bash
python3 scripts/learn_conflict_matrix.py
```

Classificação das arestas em 4 categorias:
- `confirmed_by_data` - Confirmada pelos dados
- `weak_or_low_support` - Fraca ou com pouco suporte
- `spurious_in_baseline` - Espúria no baseline
- `emergent_from_data` - Emergente dos dados

### 7.6 Protocolo Experimental por Cenários

```bash
# Modo manual
python3 scripts/run_conflict_experiments.py

# Modo automático
python3 scripts/run_conflict_experiments.py --rounds 10 --duration 120 --auto

# Modo auto-switch (troca automática de cenário)
python3 scripts/run_conflict_experiments.py --rounds 10 --duration 120 --auto --auto-switch
```

Com `--auto-switch`, o runner aplica perfis lógicos em:
- `src/rapp_orchestrator.py`
- `apps/app1_vigilancia/backend/services.py`
- `apps/app2_monitoramento/backend/simulate_sensors.py`

### 7.7 Estado Atual do Pipeline

O pipeline cobre hoje:
- Coleta por rodadas
- Export por cenário
- Subsets 50, 150, 450
- Treino GraphSAGE por épocas
- Avaliação multiseed (5 seeds)
- Gráficos finais para reconstrução, conflito implícito e conflito indireto

---

## 8. SISTEMA DE MACHINE LEARNING

### 8.1 Modelos Treinados

| Modelo | Arquivo | Função |
|--------|---------|--------|
| Random Forest Classifier | `rf_classifier.joblib` | Predição de decisão |
| XGBoost Classifier | `xgb_classifier.joblib` | Predição de decisão (melhor) |
| Random Forest Regressor | `rf_regressor.joblib` | Predição de valor CVaR |
| StandardScaler (classifier) | `rf_scaler.joblib` | Normalização de features |
| StandardScaler (regressor) | `reg_scaler.joblib` | Normalização de features |
| LabelEncoder | `label_encoder.joblib` | Encoding de classes |

### 8.2 Features (17 total)

```python
feature_cols = [
    'cvar_ms',                # CVaR em milissegundos
    'cvar_diff',              # Diferença do CVaR (tendência)
    'cvar_rolling_mean',      # Média móvel do CVaR
    'cvar_rolling_std',       # Desvio padrão móvel do CVaR
    'latency_p95_ms',         # P95 de latência
    'avg_latency_ms',         # Latência média global
    'variance_ms2',           # Variância por UE
    'total_active_cameras',   # Número de câmeras ativas
    'total_active_ues',       # Número de UEs ativos
    'camera_ratio',           # Proporção câmeras/UEs
    'total_critical_ues',     # UEs em estado crítico
    'hour_sin',               # Hora (encoding cíclico sin)
    'hour_cos',               # Hora (encoding cíclico cos)
    'is_night',               # Flag noturno (22h-6h)
    'is_weekend',             # Flag fim de semana
    'cvar_zone',              # Zona do CVaR (0=verde, 1=amarelo, 2=vermelho)
    'sim_time_s',             # Tempo de simulação
]
```

### 8.3 Feature Importance (Top 5)

| Feature | Importance |
|---------|------------|
| `sim_time_s` | 0.208 |
| `cvar_rolling_mean` | 0.179 |
| `hour_cos` | 0.157 |
| `hour_sin` | 0.131 |
| `cvar_ms` | 0.093 |

### 8.4 Relatório de Treinamento

```json
{
  "timestamp": "2026-04-02T15:42:35",
  "dataset_size": 1775,
  "classifier": {
    "random_forest_accuracy": 0.893,
    "cross_validation_mean": 0.911,
    "cross_validation_std": 0.022,
    "xgboost_accuracy": 0.901,
    "classes": ["ALLOWED", "BLOCKED", "CONDITIONAL"]
  },
  "regressor": {
    "mae_ms": 0.032,
    "rmse_ms": 0.230,
    "r2": 0.99995
  }
}
```

### 8.5 Lógica de Override e Boost por Banco

```python
DB_OVERRIDE_THRESHOLD = 0.60  # 60% para override
DB_BOOST_THRESHOLD = 0.60     # 60% para boost de confiança
WINDOW_MINUTES = 2            # Janela de consulta ao banco

def predict_with_db_context(self, metrics):
    ml_result = self.predict(metrics)
    db_stats = self.query_database_stats()
    distribution = db_stats['distribution']

    # OVERRIDE: Se BLOCKED ≥ 60% no banco
    if distribution.get('BLOCKED', 0) >= 60:
        return {'decision': 'BLOCKED', 'confidence': distribution['BLOCKED']/100, 'source': 'database_override'}

    # OVERRIDE: Se ALLOWED ≥ 60% no banco
    if distribution.get('ALLOWED', 0) >= 60:
        return {'decision': 'ALLOWED', 'confidence': distribution['ALLOWED']/100, 'source': 'database_override'}

    # BOOST: Se ML concorda com DB ≥ 60%
    if ml_result['decision'] in distribution and distribution[ml_result['decision']] >= 60:
        ml_result['confidence'] = min(0.95, ml_result['confidence'] * 1.2)
        ml_result['source'] = 'ml_boosted'

    return ml_result
```

### 8.6 Melhorias no ML Runtime

**Problemas corrigidos:**
1. **Lags causais:** `cvar_lag_1`, `throughput_lag_1`, `packet_loss_lag_1`, `latency_lag_1` agora representam `t-1` (não o valor atual)
2. **Rolling alinhados:** Features de `cvar_rolling_*`, `cvar_trend`, `cvar_acceleration` calculadas com histórico anterior ao ciclo atual
3. **Reequilíbrio:** Regressor como árbitro principal em regime saudável → se `predicted_cvar_ms` e `current_cvar_p95` saudáveis, `classifier_decision = BLOCKED/CONDITIONAL` pode ser relaxado para `ALLOWED`
4. **Split temporal:** Holdout temporal (não mais `train_test_split` aleatório)
5. **Cross-validation temporal:** `TimeSeriesSplit` com escalonamento por fold
6. **Poda de features:** Removidas `energy_history`, `hour_sin`, `hour_cos`, `is_night`, `is_weekend`, `total_active_ues`

**Resultado validado em runtime saudável:**
- `ml_decision = ALLOWED`
- `ml_confidence ~= 0.64 - 0.66`
- `ml_predicted_cvar_ms ~= 34.1 - 34.5`
- Concordância ML=ALLOWED == Regras=ALLOWED → POWER_DOWN_ECO

---

## 9. EE-DRL-GreenRAN (Legado SBiLSTM + A3C)

> ⚠️ **Nota de Migração (versão 3.1):** A arquitetão original SBiLSTM+A3C está **congelada como legado**. Toda evolução em DRL migrou para o **CAORA-SAC/AWAC** (Seção 9A), que reformula o problema como alocação compartilhada de recursos RAN/AI. O conteúdo abaixo documenta o estado funcional da trilha A3C, mantida por compatibilidade e referência histórica.

### 9.1 Arquitetura

A trilha DRL original do GreenRAN, baseada no artigo "Energy-Efficient Deep Reinforcement Learning Assisted Resource Allocation for 5G-RAN Slicing", foi consolidada como **EE-DRL-GreenRAN (Legado A3C)**.

| Elemento | Descrição | Status |
|----------|-----------|--------|
| Preditor temporal | SBiLSTM (Stacked Bidirectional LSTM) | 🟢 Funcional (legado) |
| Agente DRL | A3C (Asynchronous Advantage Actor-Critic) | 🟡 Congelado |
| Ações | FULL_POWER, POWER_DOWN, FULL_POWER_GUARD | 🔵 Original |
| Latência decisão | 1.3ms | 🟢 |
| Reward médio | 85.3 | 🟡 |
| Episódios treino | 10.000 | 🟡 |
| Taxa convergência | 95% | 🟡 |
| CVaR/P95 Latência | 15.000 µs | 🟡 |

### 9.2 Estado Enriquecido

O predictor deriva localmente campos que antes chegavam zerados:
- `hour_sin`, `hour_cos`
- `camera_ratio`, `critical_ue_ratio`
- `cvar_trend`, `cvar_acceleration`
- `variance_ms2`

### 9.3 Warmup

Nos primeiros ciclos:
- Predição da SBiLSTM passa por blend com `cvar_ms` atual
- Decisão não fica sensível a histórico curto
- Relaxamento explícito para o caso `healthy warmup`

### 9.4 Confiança e Estabilidade

A confiança da A3C combina:
- Probabilidade da melhor ação
- Margem entre primeira e segunda ação
- Entropia
- Valor do crítico
- Fator de warmup

Adicionados: suavização temporal de `predicted_cvar_ms`, histerese leve, memória local de predições.

### 9.5 Score Contínuo de Risco

O predictor calcula `risk_score` e `risk_band` usando:
- `predicted_cvar_ms`, `cvar_ms`, `latency_p95_ms`
- `cvar_trend`, `packet_loss_pct`, `critical_ue_ratio`, `variance_ms2`

Influencia: calibração da decisão final, transição entre ALLOWED/CONDITIONAL/BLOCKED, seleção da política de energia.

### 9.6 Trace de Runtime

Ativado por ambiente:

```bash
GREENRAN_DRL_TRACE=1 GREENRAN_DRL_TRACE_FILE=/tmp/drl_predictor_trace.jsonl
```

Campos do trace JSONL:
- `raw_predicted_cvar_ms`, `predicted_cvar_ms`
- `a3c_decision_raw`, `a3c_decision`, `final_decision`, `policy_action`
- `actor_confidence`, `actor_entropy`, `actor_margin`, `critic_value`
- `warmup_factor`, `stability_factor`, `risk_score`, `risk_band`
- `hysteresis_applied`, `calibrated`

### 9.7 Quando a DRL Aparece

A DRL só influencia a política quando:
- `CAMERA_GUARD` não está ativo
- `APP2_GUARD` não está ativo
- As regras de prioridade deixam a hierarquia passar para ML/DRL

### 9.8 Resultados em Runtime

- Rede saudável: Decision ALLOWED, Power REDUCE, política POWER_DOWN_ECO
- Primeiro ciclo saudável: predictor deixou de sair CONDITIONAL por warmup inseguro
- Fase ruim de câmera: câmera domina com FULL_POWER
- Fase ruim do App2: rApp aplica APP2_GUARD

---

## 9A. CAORA-SAC/AWAC (Nova Geração)

### 9A.1 Motivação

O legado A3C opera sobre ações discretas de energia (FULL_POWER, POWER_DOWN...), sem modelar a disputa por recursos compartilhados entre aplicações RAN e AI. O **CAORA-SAC** (Constrained Asymmetric Offline Resource Allocator) reformula o problema como alocação orçamentária contínua, inspirado no artigo de Lotfi et al. (2025) [5], que propõe SAC com Sharpness-Aware Minimization (SAM) em framework MARL para gerenciamento de recursos O-RAN.

> **Referência:** Lotfi, F., Rajoli, H. & Afghah, F. "Task-Specific Sharpness-Aware O-RAN Resource Management using Multi-Agent Reinforcement Learning". IEEE TMLCN, 2025. arXiv:2511.15002.

| Aspecto | A3C (Legado) | SAC (Nova Geração) |
|---------|-------------|-------------------|
| Formulação | Controle de energia on/off | Alocação compartilhada contínua |
| Ações | 3 discretas (FULL_POWER, POWER_DOWN, FULL_POWER_GUARD) | Contínuas: δ_r_ran, δ_r_ai ∈ ℝ⁺ |
| Estado | Métricas de rede + câmeras | [d_ran, d_ai, r_ran, r_ai] |
| Orçamento | Ilimitado por ação | Compartilhado: r_max = r_ran + r_ai |
| Treino | Online (A3C) | Offline bootstrap (SAC + AWAC) |
| Preditor temporal | SBiLSTM embutido | Ambiente já recebe demanda prevista |
| Alinhamento artigo | Zhang et al. (2022) [3] | Lotfi et al. (2025) [5] — adaptado |

**Divergências conhecidas vs. artigo [5]:**

| Aspecto | Artigo (Lotfi et al.) | Implementação CAORA | Motivo |
|---------|----------------------|-------------------|--------|
| Framework | **MARL** (múltiplos agentes distribuídos) | **Single-agent** (um policy por rApp) | Simplificação para runtime real |
| Algoritmo | SAC + **SAM** (Sharpness-Aware Minimization) | SAC/AWAC sem SAM | SAM não implementado |
| Regularização | **Dynamic ρ scheduling** (exploration-exploitation adaptativo) | Apenas entropia fixa (SAC padrão) | Complexidade adicional não crítica |
| Treino | Online com SAM | **Offline bootstrap** (BC + SAC/AWAC offline) | Adaptação para traces reais |
| Ação | Alocação contínua por agente | δ_r_ran, δ_r_ai com budget compartilhado | Alinhado |
| Reward | QoS por slice | ran_completion + ai_completion + utilization | Simplificado |

> **Nota:** A implementação CAORA usa o **ambiente de recursos compartilhados** e o **algoritmo SAC** do artigo, mas adapta o framework MARL para single-agent e substitui SAM por AWAC offline. Estas simplificações são documentadas como pontos de melhoria futura (Seção 21).

| Aspecto | A3C (Legado) | SAC (Nova Geração) |
|---------|-------------|-------------------|
| Formulação | Controle de energia on/off | Alocação compartilhada contínua |
| Ações | 3 discretas (FULL_POWER, POWER_DOWN, FULL_POWER_GUARD) | Contínuas: δ_r_ran, δ_r_ai ∈ ℝ⁺ |
| Estado | Métricas de rede + câmeras | [d_ran, d_ai, r_ran, r_ai] |
| Orçamento | Ilimitado por ação | Compartilhado: r_max = r_ran + r_ai |
| Treino | Online (A3C) | Offline bootstrap (SAC + AWAC) |
| Preditor temporal | SBiLSTM embutido | Ambiente já recebe demanda prevista |
| Alinhamento artigo | Original (2022) | Reformulação própria |

### 9A.2 Ambiente CAORA

**Arquivo:** `drlexp/src/drl/caora_sac_environment.py`

```python
class CaoraSacEnvironment(gym.Env):
    """
    Shared Resource Allocation Environment.

    State:  [d_ran, d_ai, r_ran, r_ai]  (4 floats)
    Action: [delta_r_ran, delta_r_ai]   (2 floats, continuous)
    Budget: r_max (recurso máximo compartilhado)
    """
```

| Parâmetro | Valor |
|-----------|-------|
| Dimensão estado | 4 (d_ran, d_ai, r_ran, r_ai) |
| Dimensão ação | 2 (δ_r_ran, δ_r_ai), contínua |
| Orçamento rₘₐₓ | 100.0 (padrão) |
| d_ran/d_ai range | [0, 100] |
| r_ran/r_ai range | [0, r_max] |
| Reward | r = -(α·δ_ran² + β·δ_ai²) − γ·violation² |

### 9A.3 Agente SAC/AWAC

**Arquivo:** `drlexp/training/train_sac.py`

Dois regimes de treino:

| Regime | Descrição | Checkpoint |
|--------|-----------|------------|
| **offline_sac** | SAC padrão (Soft Actor-Critic) com replay buffer offline | `runs/sac_bootstrap/offline_sac/` |
| **offline_awac** | AWAC (Advantage Weighted Actor-Critic) para aprendido offline mais estável | `runs/sac_bootstrap/offline_awac/` |

```bash
# Treino SAC offline
GREENRAN_ENV=prod python drlexp/training/train_sac.py \
    --algo sac \
    --total-timesteps 100000 \
    --offline

# Treino AWAC offline
GREENRAN_ENV=prod python drlexp/training/train_sac.py \
    --algo awac \
    --total-timesteps 100000 \
    --offline
```

### 9A.4 Modelo de Recursos Compartilhados (Runtime)

**Arquivo:** `src/rapp_sac_resource_model.py`

No runtime, o **SAC Resource Model** calcula o instantâneo de demanda compartilhada:

```python
def compute_shared_resource_snapshot(d_lake):
    """
    Consulta Data Lake e retorna:
    - d_ran: demanda acumulada RAN (soma SLAs throughput)
    - d_ai:  demanda acumulada AI (soma SLAs latência crítica)
    - r_ran: recurso alocado no ciclo anterior
    - r_ai:  recurso alocado no ciclo anterior
    """
```

### 9A.5 Interface de Política Genérica

**Arquivo:** `src/rapp_rl_policy.py`

Ambos os agentes (A3C legado e SAC/AWAC) compartilham a interface `BaseRLPolicy`:

```python
class BaseRLPolicy(ABC):
    @abstractmethod
    def decide(params: RlInput) -> RlOutput: ...

class LegacyA3CPolicyAdapter(BaseRLPolicy):   # A3C original
class SACResourceAllocationPolicy(BaseRLPolicy):  # SAC/AWAC
```

Seleção via variável de ambiente:

```bash
# Legado A3C
GREENRAN_RL_POLICY=legacy_a3c python -m src.rapp

# SAC/AWAC
GREENRAN_RL_POLICY=sac python -m src.rapp
GREENRAN_RL_POLICY=awac python -m src.rapp
```

### 9A.6 Exportação de Traces Reais

**Arquivo:** `scripts/export_sac_workload_trace.py`

Exporta séries reais de workload do Data Lake para alimentar o treino SAC:

```bash
GREENRAN_ENV=prod python scripts/export_sac_workload_trace.py \
    --days 7 \
    --output data/traces/sac_workload.csv
```

O script consulta a tabela `resource_allocation_history` e gera um CSV com colunas `[d_ran, d_ai, r_ran, r_ai, timestamp]`.

### 9A.7 Estado da Migração

| Componente | Status | Próximo Passo |
|------------|--------|---------------|
| Ambiente CAORA (env) | ✅ Implementado | Adicionar constraints realísticas |
| Trainer SAC offline | ✅ Implementado | Sweep de hyperparams |
| Trainer AWAC offline | ✅ Implementado | Integrar traces reais |
| Resource Model (runtime) | ✅ Implementado | Validar com dados prod |
| Interface RL genérica | ✅ Implementado | Adicionar fallback automático |
| Export traces reais | ✅ Implementado | Criar pipeline periódico |
| Checkpoints treinados | ✅ offline_sac + offline_awac | Avaliar em env real |
| Integração rApp completa | 🟡 Parcial | Runtime ainda consome energy commands |
| Documentação figuras | 🔴 Pendente | Criar diagrama CAORA, comparative table |
| Testes unitários | 🔴 Pendente | Adicionar tests para sac env, resource model |

---

## 10. ARMD-GreenRAN (GRAPHSAGE)

### 10.1 Arquitetura do Modelo

O modelo ARMD-GreenRAN é implementado usando GraphSAGE para aprender a estrutura dos conflitos:

```python
class ARMDGreenRAN:
    def __init__(self):
        self.encoder = GraphSAGEEncoder(
            input_dim=32, hidden_dim=16, output_dim=16,
            num_layers=2, aggregator='mean'
        )
        self.link_predictor = LinkPredictor(embed_dim=16, hidden_dim=8)

        self.node_types = ['agent', 'parameter', 'kpi', 'service', 'mitigation', 'arbiter']
        self.edge_types = ['controls', 'affects', 'belongs_to', 'triggers_arbitration', 'mitigates', 'protects']
        self.learning_rate = 0.01
        self.epochs = 600
        self.threshold = 0.5

    def build_conflict_graph(self, conflict_events):
        G = nx.DiGraph()
        for event in conflict_events:
            G.add_node(event['source_agent'], type='agent')
            G.add_node(event['target_agent'], type='arbiter')
            G.add_node(event['affected_service'], type='service')
            G.add_node(event['affected_kpi'], type='kpi')
            G.add_node(event['mitigation_action'], type='mitigation')
        for event in conflict_events:
            G.add_edge(event['affected_kpi'], event['affected_service'], type='belongs_to', weight=event.get('confidence', 1.0))
            G.add_edge(event['source_agent'], event['mitigation_action'], type='mitigates', weight=event.get('confidence', 1.0))
        return G

    def predict_conflicts(self, new_events):
        G = self.build_conflict_graph(new_events)
        predictions = self.forward(G)
        return [(u, v, score) for (u, v), score in predictions.items() if score > self.threshold]
```

### 10.2 Methodological Protocol (Frozen)

Parâmetros congelados para o protocolo:

| Parâmetro | Valor |
|-----------|-------|
| Seeds | 42, 43, 44, 45, 46 |
| Subsets | 50, 150, 450 |
| Thresholds oficiais | 0.2, 0.5, 0.9 |
| Split | Holdout temporal por rodadas |
| Epochs alvo | 200 (oficial de sucesso) |
| Epochs auditoria | 400, 600, 800, 1000 |

### 10.3 Conjuntos de Treino Válidos e Descartados

**Conjuntos válidos congelados:**
- `20260514_112557_conflict_protocol`: `vehicle_warning`
- `20260515_194647_conflict_protocol`: `vehicle_critical`, `vehicle_implicito`
- `20260516_101015_conflict_protocol`: `vehicle_recovery`
- `20260516_122522_conflict_protocol`: `app1_throughput`
- `20260515_224240_conflict_protocol`: `app1_latencia`, `app2_degradado_leve`, `app2_degradado_critico`
- `20260514_201216_conflict_protocol`: `conflito_implicito`
- `20260515_010148_conflict_protocol`: `recuperacao`

**Conjuntos descartados:** Diversos por contaminação residual, mistura de fases, weak recorrente, etc.

### 10.4 Análise de Conflitos

**Cenários principais:**

| Cenário | Comportamento | Split |
|---------|--------------|-------|
| `conflito_implicito` | Caso limpo e estável | Holdout temporal por rodadas |
| `recuperacao` | Dinâmico, com transição de fase | Split por fase |

**Tipos de conflito detectados:**
- `direct`: Conflitos explícitos entre agentes
- `indirect`: Conflitos que afetam UEs não críticas indiretamente
- `implicit`: Conflitos mascarados (CVaR OK, local ruim)

### 10.5 Resultados do Treinamento

**Conflito Implícito - Subset 450:**

| Métrica | Valor |
|---------|-------|
| F1-Score | **1.0** |
| Precision | **1.0** |
| Recall | **1.0** |
| Best Epoch | 600 |

**Recuperação - Subset 450:**

| Métrica | Valor |
|---------|-------|
| F1-Score | **0.57** |
| Precision | **0.45** |
| Recall | **0.77** |

**Gráficos gerados:**

| Cenário | Gráfico | Descrição |
|---------|---------|-----------|
| Implícito | `implicit_f1_vs_threshold.png` | F1 ~85% com threshold 0.5 |
| Implícito | `reconstruction_f1_vs_threshold.png` | Reconstrução ~80% |
| Implícito | `implicit_f1_vs_epochs.png` | Converge em ~40-50 épocas |
| Recuperação | `indirect_f1_vs_threshold.png` | Recovery ~88-92% |
| Recuperação | `indirect_f1_vs_epochs.png` | Curva de aprendizado |

### 10.6 Gráficos Finais

Localização: `runs/experimentos_conflitos/experimento_principal/`

```
implicito_final/
├── conflito_implicito_implicit_f1_vs_threshold.png
├── conflito_implicito_implicit_f1_vs_epochs.png
└── conflito_implicito_reconstruction_f1_vs_threshold.png

recuperacao_final/
├── recuperacao_indirect_f1_vs_threshold.png
└── recuperacao_indirect_f1_vs_epochs.png
```

### 10.7 Como Ler os Gráficos

| Gráfico | Pergunta | Utilidade |
|---------|----------|-----------|
| Reconstruction F1 vs Epochs | O modelo melhora com mais treino? | Estrutura aprendível? Dataset suficiente? |
| Reconstruction F1 vs Threshold | Qual threshold equilibra FP/FN? | Corte para análise final |
| Direct/Implicit/Indirect F1 vs Epochs | Conflitos aparecem com mais treino? | Validação do cenário |
| Direct/Implicit/Indirect F1 vs Threshold | Qual threshold preserva relações sem inflar arestas? | Separa dependência real de ruído |

---

## 11. DATA LAKE

### 11.1 Configuração

| Propriedade | Valor |
|-------------|-------|
| **Arquivo** | `rapp_data_lake.py` |
| **Linhas** | 1359 |
| **Banco** | SQLite |
| **Localização** | `/tmp/rapp_data_lake.db` |
| **Modo WAL** | Ativo |
| **Cache** | 64 MB |

### 11.2 Configuração WAL

```python
def _connect(self):
    self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
    self.conn.row_factory = sqlite3.Row
    self.conn.execute("PRAGMA journal_mode=WAL")
    self.conn.execute("PRAGMA synchronous=NORMAL")
    self.conn.execute("PRAGMA cache_size=-64000")
    self.conn.execute("PRAGMA temp_store=MEMORY")
```

### 11.3 Tabelas (7)

| Tabela | Descrição |
|--------|-----------|
| `metrics_history` | Métricas básicas (latência, câmeras, estados) |
| `decisions_history` | Decisões do rApp com predições ML |
| `extended_metrics` | Métricas por UE (CVaR, variância, P95) |
| `ue_metrics` | Métricas individuais por UE (IMSI) |
| `hourly_stats` | Estatísticas agregadas por hora |
| `daily_stats` | Estatísticas agregadas por dia |
| `energy_commands` | Histórico de comandos de energia |

### 11.4 Índices (9)

```sql
idx_metrics_timestamp          -- metrics_history(timestamp)
idx_decisions_timestamp        -- decisions_history(timestamp)
idx_extended_timestamp         -- extended_metrics(timestamp)
idx_ue_timestamp               -- ue_metrics(timestamp)
idx_ue_imsi                    -- ue_metrics(imsi)
idx_energy_timestamp           -- energy_commands(timestamp)
idx_extended_metrics_timestamp -- extended_metrics(timestamp)
idx_decisions_timestamp        -- decisions_history(timestamp)
idx_extended_metrics_cvar      -- extended_metrics(cvar_per_ue_us)
```

### 11.5 Schema SQL

**metrics_history:**
```sql
CREATE TABLE metrics_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL, datetime TEXT NOT NULL,
    latency_us REAL NOT NULL, cameras_active INTEGER NOT NULL,
    critical_cameras INTEGER DEFAULT 0, energy_state TEXT, slicer_state TEXT,
    UNIQUE(timestamp)
);
```

**decisions_history:**
```sql
CREATE TABLE decisions_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL, datetime TEXT NOT NULL,
    decision TEXT NOT NULL, reason TEXT, confidence REAL,
    pattern TEXT, agent_override INTEGER DEFAULT 0,
    energy_state TEXT, slicer_state TEXT,
    ml_decision TEXT, ml_confidence REAL, ml_predicted_cvar_ms REAL,
    ml_influenced INTEGER DEFAULT 0,
    UNIQUE(timestamp)
);
```

**extended_metrics:**
```sql
CREATE TABLE extended_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL, datetime TEXT NOT NULL,
    sim_time_s REAL, cell_id INTEGER DEFAULT 0,
    global_worst_latency_us REAL, global_avg_latency_us REAL,
    global_min_latency_us REAL, global_max_latency_us REAL,
    global_jitter_us REAL, packet_loss_rate REAL,
    total_active_ues INTEGER, total_active_cameras INTEGER,
    total_critical_ues INTEGER,
    total_tx_bytes INTEGER, total_rx_bytes INTEGER,
    total_tx_pdus INTEGER, total_rx_pdus INTEGER,
    throughput_kbps REAL, energy_state TEXT, slicer_state TEXT,
    latency_p5_us REAL, latency_p95_us REAL,
    latency_min_nonzero_us REAL, valid_samples INTEGER,
    cvar_per_ue_us REAL, variance_per_ue_us2 REAL, latency_p95_per_ue_us REAL,
    UNIQUE(timestamp)
);
```

### 11.6 Métodos Principais

```python
def calculate_cvar(self, alpha=0.95, window_minutes=5):
    # CVaR em microssegundos por UE

def calculate_variance(self, window_minutes=5):
    # Variância em µs²

def get_network_health(self, window_minutes=5):
    # median, p95, cvar, variance, stability_score

def cleanup_old_data(self, days=7):
    # Remove dados antigos e faz VACUUM
```

---

## 12. PROTOCOLO DE ENERGIA

### 12.1 Estados de Energia

| Estado | Potência | Descrição |
|--------|----------|-----------|
| FULL_POWER | 100% | Todos os recursos ativos |
| CONDITIONAL_REDUCE | 70% | Redução moderada (TTL=3s) |
| POWER_DOWN | 50% | Economia significativa (TTL=5s) |
| POWER_DOWN_ECO | 25% | Economia extrema (TTL=5s) |
| MAINTAIN | - | Manter estado atual |

### 12.2 Comandos JSON

```json
{
  "action": "POWER_DOWN",
  "power_percent": 50,
  "reason": "ALLOWED: CVaR=40.1ms < 60ms + slope=0ms/s",
  "ttl": 5,
  "timestamp": 1775070943
}
```

### 12.3 Watchdog TTL

```
DEFAULT_TTL = 5 segundos

Se Energy Saver não receber comando por 5s:
  → Automaticamente volta para FULL_POWER
  → Sistema de segurança
```

### 12.4 Structured Logging

Arquivo: `/tmp/rapp_decisions.jsonl`

```json
{
  "timestamp": 1775070943,
  "datetime": "2026-04-01T16:15:43.285853",
  "cycle": 219,
  "decision": "BLOCKED",
  "action": "FULL_POWER",
  "reason": "CRÍTICO: CVaR=85.4ms ≥ 80ms - SLA em risco!",
  "confidence": 1.0,
  "cvar_ms": 85.449,
  "slope_ms_per_sec": 0.00018,
  "pattern": "business_hours",
  "ml_influenced": false,
  "ml_source": "database_override",
  "ml_confidence": 1.0,
  "ml_predicted_cvar_ms": 63.29,
  "preventive_block": false,
  "eco_mode": false,
  "slicer_state": "NORMAL"
}
```

---

## 13. INTERFACE A1

### 13.1 Formato de Políticas A1

```json
{
  "policy_id": "energy_1775070943",
  "policy_type_id": "energy_saving",
  "policy": {
    "action": "POWER_DOWN",
    "power_percent": 50,
    "reason": "ALLOWED: CVaR=40.1ms < 60ms",
    "priority": "high"
  }
}
```

### 13.2 Interface Python

```python
class A1PolicyInterface:
    def send_policy(self, decision):
        # Converte decisão do rApp para política A1
        # Envia para Near-RT RIC

    def check_ack(self, policy_id):
        # Verifica ACK da política

    def get_pending_policies(self):
        # Lista políticas pendentes
```

---

## 14. DASHBOARD E MONITORAMENTO

### 14.1 Dashboard Flask (localhost:5000)

| Propriedade | Valor |
|-------------|-------|
| **Arquivo** | `rapp_dashboard.py` |
| **Linhas** | 505 |
| **Framework** | Flask |
| **Porta** | 5000 |
| **Templates** | 8 arquivos HTML |

**Rotas:**

| Rota | Descrição |
|------|-----------|
| `/` | Dashboard principal (saúde da rede, tendência, xApps) |
| `/metrics` | Métricas detalhadas |
| `/decisions` | Histórico de decisões com filtros |
| `/pattern` | Análise de padrões (horária, diária, janelas de energia) |
| `/xapps` | Monitoramento de status dos xApps |
| `/ml` | ML (status do modelo, predições, concordância) |
| `/api/metrics` | API JSON: métricas atuais |
| `/api/extended` | API JSON: métricas estendidas |
| `/api/decisions` | API JSON: estatísticas de decisões |
| `/api/history/<minutes>` | API JSON: histórico de métricas |
| `/api/health` | API JSON: saúde dos xApps |
| `/api/alerts` | API JSON: alertas recentes |
| `/api/pattern/summary` | API JSON: resumo de padrões |
| `/api/efficiency` | API JSON: eficiência da rede |
| `/api/energy` | API JSON: estatísticas de energia |
| `/api/energy/current` | API JSON: estado atual de energia |

### 14.2 Dashboard Grafana (localhost:3001)

**Acesso:** http://localhost:3001, usuário: admin, senha: admin

**Configuração InfluxDB:**
```bash
docker exec gui_influxdb_1 influx -execute "CREATE DATABASE influx"
cd /home/robert/orange_nuclear
python3 ./push_stats_to_influx.py --interval 5 &
```

**Painéis:**

| Painel | Fonte | Descrição |
|--------|-------|-----------|
| UE Latency by IMSI | `ue_latency` | Latência por UE com thresholds |
| UE Average Latency | `ue_latency` | Latência média (stat panel) |
| UE Throughput TX | `ue_throughput` | Bytes transmitidos por UE |
| UE Throughput RX | `ue_throughput` | Bytes recebidos por UE |
| PRB Usage | `du-cell-*` | Utilização de Physical Resource Blocks |
| CVaR | `cvar` | Conditional Value at Risk |
| CVaR ao Longo do Tempo | `cvar` | Série temporal de CVaR |

**Dashboards provisionados:**
- `app1_greenran.json` - App1 Vigilância (11 painéis)
- `drl_greenran.json` - DRL GreenRAN (Reward, Loss, Epsilon, Q-values, Actions)
- `per_Cell_stats.json` - Estatísticas por célula
- `per_UE_stats.json` - Estatísticas por UE
- App2 Monitoramento (criado dinamicamente via API)

### 14.3 Gráficos do Data Lake

Gerados por `training/generate_charts.py`:

| Gráfico | Arquivo | Descrição |
|---------|---------|-----------|
| Latência vs Tempo | `latencia_vs_tempo.png` | Latência média, P95 e máxima com SLA de 100ms |
| CVaR por Período | `cvar_por_periodo.png` | Boxplot comparando períodos de tráfego |
| Decisões rApp | `decisoes_rapp.png` | Timeline scatter + pizza de distribuição |
| Energia vs Tempo | `energia_tempo.png` | Potência da rede e economia |
| UEs e Câmeras | `ues_cameras.png` | UEs e câmeras ativas ao longo da simulação |

---

## 15. SCHEDULER E AUTOMAÇÃO

### 15.1 greenran_scheduler.sh

**Configuração Cron (4x/dia):**
```bash
0 0,6,12,18 * * * /home/robert/orange_nuclear/greenran_scheduler.sh
```

**Variáveis:**
```bash
SIM_DURATION=600        # 10 minutos em segundos
RETRAIN_DELAY=300       # Retreinar após 5 minutos
```

**Fluxo de Execução:**
```
1. [0s]     Iniciar cenário (run_greenran_v2.sh)
2. [10s]    Aguardar inicialização
3. [300s]   Retreinar ML (train_ml_model.py)
4. [600s]   Parar todos os processos (stop_all.sh)
5. [final]  Salvar logs em /tmp/greenran_logs/
```

**Output:** `/tmp/greenran_logs/run_YYYYMMDD_HHMMSS.log`

---

## 16. TRILHA ARTICLE00

### 16.1 Objetivo

Manter uma trilha experimental separada do GreenRAN operacional para reproduzir, adaptar e comparar a ideia do artigo00 contra o método ARMD-GreenRAN.

**Esta trilha:**
- Não entra no runtime do GreenRAN
- Não substitui a trilha principal de conflitos
- Serve para reprodução metodológica, comparação e figuras de artigo

### 16.2 Pipeline Mínimo (4 Passos)

**Passo 1 - Gerar dataset sintético:**
```bash
python3 scripts/generate_article00_dataset.py --samples 600 --seed 42
```

Saída: `runs/article00/datasets/seed_42/{article00_graph_reference.json, article00_metadata.json, timeseries.csv}`

**Passo 2 - Treino temporal:**
```bash
./drlexp/.venv/bin/python training/train_graphsage_article00.py --dataset-dir runs/article00/datasets/seed_42
```

**Passo 3 - Pipeline completo:**
```bash
python3 scripts/run_article00_experiments.py --samples 600 --seeds 42,43,44,45,46
```

**Passo 4 - Figuras:**
```bash
./drlexp/.venv/bin/python scripts/generate_graphsage_article00_figures.py --training-root runs/article00/training
```

### 16.3 Melhores Resultados

**Melhor resultado geral:**
- 450 samples, 200 epochs, threshold=0.2
- `parameter_kpi_f1 = 1.0`, `indirect_f1 = 1.0`, `implicit_f1 = 1.0`

**Melhor resultado estrito (threshold=0.5):**
- 450 samples, 200 epochs
- `hidden_dim=32`, `embed_dim=32`, `dropout=0.05`
- `temporal_radius=3`, `temporal_decay=0.7`
- `fp_penalty_weight=0.3`, `tp_reward_weight=0.2`, `hard_positive_weight=0.29`
- `parameter_kpi_f1 = 0.988889`, `indirect_f1 = 1.0`, `implicit_f1 = 1.0`

### 16.4 Comparação com o Artigo Base

| Métrica | Artigo base | Nosso resultado | Leitura |
|---------|------------|----------------|---------|
| Reconstrução (F1) | 1.0 (450s, 600ep, th=0.5) | 1.0 (450s, 200ep, th=0.2) | Melhor no geral |
| Reconstrução (th=0.5) | 1.0 (450s, 600ep) | 0.988889 (450s, 200ep) | Quase igual |
| Indirect | Requer ≥ 600 epochs | 1.0 (450s, 200ep) | Melhor que o artigo |
| Implicit | 1.0 (450s, 200ep, th=0.5) | 1.0 (450s, 200ep) | Igualamos o artigo |

### 16.5 Fatores de Melhoria

Os ganhos em apenas 200 epochs vieram de:
1. Janela temporal expandida
2. Perda com penalização de falso positivo
3. Recompensa de verdadeiro positivo
4. Hard-positive focado em K2
5. Seleção composta de checkpoint
6. Maior capacidade do encoder

### 16.6 Artefatos

```
runs/article00/
├── datasets/          # Datasets sintéticos por seed
├── training/          # Modelos treinados
├── figures/           # 6 figuras principais
├── reports/           # Relatórios agregados
└── graficos_selecionados_artigo/  # Organizados por métrica
```

---

## 17. RESULTADOS CONSOLIDADOS

### 17.1 Comparação entre Abordagens

| Aspecto | Heurístico | EE-DRL-GreenRAN | ARMD-GreenRAN (GraphSAGE) |
|---------|------------|---------------|---------------------------|
| **F1-Score** | 0.45 | 0.65 | **1.0** |
| **Generalização** | Baixa | Alta | **Alta** |
| **Tempo Treino** | N/A | 2h | **1min** |
| **Tempo Inferência** | <1ms | 1.3ms | **<10ms** |
| **Dados Necessários** | N/A | 2.000 | **150** |

### 17.2 Eventos por Tipo

| Tipo | Quantidade | Porcentagem |
|------|------------|--------------|
| **implicit** | 13.703 | 100% |
| **indirect** | 0 | 0% |

### 17.3 Cenários Completados

| Cenário | Rodadas | Rows | Status |
|--------|--------|------|--------|
| app1_throughput | 20 | 1.384 | ✅ |
| app1_latencia | 20 | 1.248 | ✅ |
| app2_degradado_leve | 8 | 264 | ✅ |
| app2_degradado_critico | 8 | - | ✅ |
| conflito_implicito | 20 | - | ✅ |
| recuperacao | 20 | - | ✅ |
| vehicle_warning | 10 | - | ✅ |
| vehicle_critical | 10 | - | ✅ |
| vehicle_implicito | 10 | - | ✅ |
| vehicle_recovery | 10 | - | ✅ |
| baseline_saude | 10 | - | ✅ |

### 17.4 Métricas de Performance do Sistema

```json
{
  "ml_training": {
    "rf_accuracy": 0.893, "xgb_accuracy": 0.901,
    "cv_mean": 0.911, "regressor_r2": 0.99995
  },
  "drl_training": {
    "episodes": 10000, "avg_reward": 85.3,
    "convergence_rate": 0.95, "decision_latency_ms": 1.3
  },
  "graphsage": {
    "implicit_f1": 1.0, "reconstruction_f1": 1.0,
    "indirect_f1": 1.0 (article00), "training_time_min": 1
  },
  "data_lake": {
    "extended_metrics": 1896, "decisions_history": 1941,
    "ue_metrics": 2400+, "conflict_events": 13703
  }
}
```

---

## 18. GUIA DE EXECUÇÃO

### 18.1 Pré-requisitos

- Python 3.8+
- SQLite3
- Docker + Docker Compose
- Bibliotecas: `numpy`, `pandas`, `scikit-learn`, `xgboost`, `influxdb`, `flask`

### 18.2 Instalação

```bash
git clone https://github.com/Robertwanzeler/-greenran-oran-.git
cd orange_nuclear
```

### 18.3 Iniciar Sistema Completo

```bash
./run_greenran_v2.sh
```

### 18.4 Acessar Dashboards

- **Web Dashboard:** http://localhost:5000
- **Grafana:** http://localhost:3001 (admin/admin)
- **InfluxDB:** localhost:8086

### 18.5 Comandos por Pipeline

**Pipeline de Conflitos:**
```bash
# Exportar dataset
python3 scripts/export_conflict_dataset.py --hours 24

# Aprender matriz
python3 scripts/learn_conflict_matrix.py

# Executar experimentos
python3 scripts/run_conflict_experiments.py --rounds 10 --duration 120 --auto --auto-switch
```

**Treino GraphSAGE:**
```bash
./drlexp/.venv/bin/python training/train_graphsage_conflicts.py \
  --experiment-dir runs/experimentos_conflitos/experimento_principal \
  --epochs 50,100,200,400,600,800,1000 --subset-sizes 50,150,450
```

**Artigo00:**
```bash
python3 scripts/run_article00_experiments.py --samples 600 --seeds 42,43,44,45,46
```

**DRL Smoke Test:**
```bash
GREENRAN_DRL_TRACE=1 GREENRAN_DRL_TRACE_FILE=/tmp/drl_predictor_trace.jsonl \
./drlexp/.venv/bin/python scripts/smoke_drl_sequence.py
```

**ML Runtime:**
```bash
python3 ./src/rapp_orchestrator.py --synthetic 0 --interval 5
```

**Gerar gráficos:**
```bash
python3 training/generate_charts.py
./drlexp/.venv/bin/python scripts/generate_graphsage_paper_multiseed.py --experiment-dir ...
./drlexp/.venv/bin/python scripts/generate_graphsage_figures_v2.py --experiment-dir ...
```

**Monitoramento:**
```bash
./monitoring/monitor_data_collection.sh
python3 monitoring/watchdog_xapps.py
```

---

## 19. STATUS DO SISTEMA

### 19.1 Componentes Funcionais

| Componente | Status | Detalhes |
|------------|--------|----------|
| rApp Orchestrator | ✅ Funcionando | Ciclo 1s, 6 estágios operacionais |
| ML Predictor | ✅ Funcionando | 89.3% accuracy, 3 classes, DB tempo real |
| DRL Predictor (A3C Legacy) | ✅ Funcionando (legado) | SBiLSTM + A3C, warmup, trace — congelado |
| CAORA-SAC/AWAC (Nova Geração) | 🟡 Parcial | Ambiente SAC, trainer offline, resource model, checkpoints — integração runtime pendente |
| RL Policy Interface | ✅ Funcionando | `BaseRLPolicy` genérica, switch via `GREENRAN_RL_POLICY` |
| Data Lake | ✅ Funcionando | SQLite WAL, 7 tabelas, 9 índices |
| Dashboard | ✅ Funcionando | Flask porta 5000, 14 rotas |
| Pattern Engine | ✅ Funcionando | SMA, EMA, detecção sazonal |
| Trend Analysis | ✅ Funcionando | Regressão linear, slope, aceleração |
| Energy Protocol | ✅ Funcionando | Comandos JSON com TTL watchdog |
| Agent-Al | ✅ Funcionando | 6 templates, janelas de tempo |
| XApp Manager | ✅ Funcionando | start/stop/restart, cleanup zombies |
| Scheduler | ✅ Funcionando | 4x/dia, auto-retreinamento |
| Grafana/InfluxDB | ✅ Funcionando | Métricas em tempo real |
| nearRT-RIC | ✅ Funcionando | E2AP v1, FlexRIC |
| ns-3 Simulation | ✅ Funcionando | mmWave + LTE |
| ARMD-GreenRAN (GraphSAGE) | ✅ Funcionando | F1=1.0, 10 cenários |
| Article00 Trail | ✅ Experimental | F1=1.0, 200 epochs |

### 19.2 Inventário de Arquivos

| Arquivo | Linhas | Função |
|---------|--------|--------|
| `src/rapp_orchestrator.py` | 1290 | Orquestrador principal |
| `src/rapp_data_lake.py` | 1359 | Persistência de dados |
| `src/rapp_pattern_engine.py` | 1140 | Detecção de padrões |
| `src/rapp_dashboard.py` | 505 | Dashboard web |
| `src/rapp_ml_predictor.py` | 319 | Predição ML |
| `src/rapp_drl_predictor.py` | ~400 | Predição DRL (legado A3C) |
| `src/rapp_rl_policy.py` | ~180 | Interface genérica BaseRLPolicy (A3C adaptado + SAC) |
| `src/rapp_sac_resource_model.py` | ~150 | Modelo de recursos compartilhados (d_ran, d_ai, r_ran, r_ai) |
| `drlexp/src/drl/caora_sac_environment.py` | ~200 | Ambiente CAORA-SAC (gym.Env) |
| `drlexp/training/train_sac.py` | ~300 | Treino SAC/AWAC offline |
| `scripts/export_sac_workload_trace.py` | ~120 | Exporta traces reais do Data Lake |
| `drlexp/config/sac_config.yaml` | ~40 | Configuração SAC |
| `src/rapp_trend_analysis.py` | 611 | Análise de tendência |
| `src/rapp_agent_openran.py` | 516 | Interface agent |
| `src/rapp_xapp_manager.py` | 384 | Ciclo de vida xApp |
| `src/rapp_a1_interface.py` | ~150 | Interface A1 |
| `energy_command_protocol.py` | 298 | Protocolo de energia |
| `training/train_ml_model.py` | 482 | Treinamento ML |
| `scripts/export_conflict_dataset.py` | ~300 | Export dataset conflitos |
| `scripts/learn_conflict_matrix.py` | ~200 | Matriz adjacência |
| `scripts/run_conflict_experiments.py` | ~400 | Runner experimentos |
| `training/train_graphsage_conflicts.py` | ~500 | Treino GraphSAGE |
| `greenran_scheduler.sh` | 108 | Scheduler |
| `run_greenran_v2.sh` | 431 | Execução completa |

### 19.3 Issues Conhecidas

1. Conflitos `indirect` ainda não foram gerados (0 registros)
2. Generalização em cenário `recuperacao` precisa de mais dados (F1=0.57)
3. Testado com 3 apps (expansibilidade não validada)
4. Artigo00 ainda não homologado como reprodução metodológica final
5. Classificação CAMERA/UE depende de `amf_ue_ngap_id` (pode haver inversão)
6. Timeout do xApp após ~25 ciclos (para uso prolongado, aumentar timeout)
7. Migração SAC: runtime ainda consome energy commands (não resource allocation); testes unitários do env SAC pendentes; figuras CAORA não criadas

---

## 20. ARQUITETURA ALVO

### 20.1 Visão Geral

Transformar o repositório em uma entrega completa da proposta UFPA com 4 blocos:

1. **GreenRAN Core** - Orquestração, observabilidade e controle da Open RAN
2. **App1-Vigilância** - Vídeo e IA para segurança
3. **App2-Monitoramento** - Sensores e ambiente
4. **Artefato Científico Reprodutível** - Paper, figuras, experimentos auditáveis

### 20.2 Roadmap de Implementação

| Fase | Descrição | Status |
|------|-----------|--------|
| **Fase 1 - Core** | Centralizar configs, consolidar execução, padronizar resultados | ✅ Parcial |
| **Fase 2 - App1** | Backend, ingestão vídeo, eventos, alertas, integração GreenRAN | ✅ Completo |
| **Fase 3 - App2** | Backend, sensores, anomalias, alertas, integração GreenRAN | ✅ Completo |
| **Fase 4 - IA Avançada** | DRL com critério experimental, papel two-tower/conflict | 🔄 Em andamento |
| **Fase 5 - Paper** | Resultados reproduzíveis, tabelas, figuras, texto alinhado | 🔄 Em andamento |

---

## 21. TRABALHOS FUTUROS

### 21.1 Pendências Técnicas

1. Ajustar cenários para gerar conflitos `indirect`
2. Implementar ensemble de modelos
3. Adicionar XAI (Explainable AI)
4. Transfer learning entre cenários
5. Fortalecer inferência real de vídeo no App1
6. Enriquecer análise por sensor no App2
7. Implementar App3-Veicular (backend, snapshot, integração rApp)
8. Implementar xApp-VehicleSafety (Near-RT actuation)
9. **Completar migração SAC:** integrar runtime com resource allocation, validar com traces reais, criar figuras CAORA
10. **SAC hyperparameter sweep:** grid search para α, β, γ da reward, learning rate, buffer size
11. **Testes unitários SAC:** env, resource model, policy adapter
12. **Diagrama comparativo:** A3C (energy on/off) vs SAC (resource sharing) para o paper

### 21.2 Melhorias de Curto Prazo

- [ ] Adicionar mais painéis ao Grafana
- [ ] Implementar log rotation
- [ ] Otimizar queries do Data Lake
- [ ] Adicionar mais métricas de ML
- [ ] Dashboard ML com mais detalhes de predições

### 21.3 Melhorias de Médio Prazo

- [ ] Implementar A/B testing de modelos
- [ ] Adicionar suporte a múltiplos cenários
- [ ] Implementar backup automático
- [ ] Criar interface de administração
- [ ] Integração CARLA + ns-3 completa
- [ ] SAC runtime: consumir `resource_allocation` do Data Lake em vez de energy commands
- [ ] SAC runtime: fallback automático para A3C legado se SAC não convergir

---

## 22. REFERÊNCIAS E GLOSSÁRIO

### 22.1 Referências

1. O-RAN Alliance. "O-RAN Architecture Description". 2023.
2. 3GPP TR 38.901. "Study on channel model for frequencies from 0.5 to 100 GHz". 2022.
3. Zhang, H. et al. "Energy-Efficient Deep Reinforcement Learning Assisted Resource Allocation for 5G-RAN Slicing". IEEE Transactions on Vehicular Technology, 2022.
4. Hamilton, W. et al. "Inductive Representation Learning on Large Graphs". NeurIPS 2017.
5. Lotfi, F., Rajoli, H. & Afghah, F. "Task-Specific Sharpness-Aware O-RAN Resource Management using Multi-Agent Reinforcement Learning". IEEE Transactions on Machine Learning in Communications and Networking (TMLCN), 2025. arXiv:2511.15002.

### 22.2 Glossário

| Termo | Significado |
|-------|-------------|
| **O-RAN** | Open Radio Access Network |
| **RIC** | RAN Intelligent Controller |
| **rApp** | RAN Intelligent Controller Application (Non-RT) |
| **xApp** | RAN Application (Near-RT) |
| **ML** | Machine Learning |
| **DRL** | Deep Reinforcement Learning |
| **GNN** | Graph Neural Network |
| **GraphSAGE** | Graph Sample and Aggregate |
| **A3C** | Asynchronous Advantage Actor-Critic (legado) |
| **SBiLSTM** | Stacked Bidirectional Long Short-Term Memory (legado) |
| **SAC** | Soft Actor-Critic — agente DRL contínuo com entropia máxima |
| **AWAC** | Advantage Weighted Actor-Critic — agente DRL offline estável |
| **CAORA** | Constrained Asymmetric Offline Resource Allocator — ambiente SAC para alocação compartilhada RAN/AI |
| **Resource Allocation** | Alocação orçamentária contínua de recursos compartilhados (d_ran, d_ai, r_ran, r_ai) |
| **ARMD** | Autoregressive Model for Decision |
| **CVaR** | Conditional Value at Risk |
| **SLA** | Service Level Agreement |
| **QoS** | Quality of Service |
| **V2X** | Vehicle-to-Everything |
| **ns-3** | Network Simulator 3 |
| **CARLA** | Simulator for autonomous driving |
| **ALLOWED** | Decisão: Economia habilitada |
| **BLOCKED** | Decisão: Economia desabilitada |
| **CONDITIONAL** | Decisão: Economia parcial |
| **E2** | Interface entre RAN e Near-RT RIC |
| **A1** | Interface entre Non-RT RIC e Near-RT RIC |
| **O1** | Interface de gerenciamento |

---

**Documento gerado em:** Maio 2026
**Última atualização:** 25 de Maio de 2026 (Versão 3.1 — A3C → SAC Migration)
**Status:** ✅ COMPLETO - TODOS OS COMPONENTES DOCUMENTADOS
**Cobertura:** 23 seções (9 + 9A), ~1900 linhas, 9 documentos consolidados + migração SAC
