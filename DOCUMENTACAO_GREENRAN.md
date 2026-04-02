# GreenRAN O-RAN - Documentação Completa do Projeto

**Repositório**: [github.com/Robertwanzeler/-greenran-oran-.git](https://github.com/Robertwanzeler/-greenran-oran-.git)
**Última Atualização**: 02 de Abril de 2026
**Status**: Operacional

---

## Índice

1. [Visão Geral](#1-visão-geral)
2. [Arquitetura do Sistema](#2-arquitetura-do-sistema)
3. [Componentes Implementados](#3-componentes-implementados)
4. [Regras de Decisão](#4-regras-de-decisão)
5. [Sistema de Machine Learning](#5-sistema-de-machine-learning)
6. [Dashboard e Monitoramento](#6-dashboard-e-monitoramento)
7. [Scheduler e Automação](#7-scheduler-e-automação)
8. [Data Lake](#8-data-lake)
9. [Protocolo de Energia](#9-protocolo-de-energia)
10. [Interface A1](#10-interface-a1)
11. [xApps](#11-xapps)
12. [Simulação ns-3](#12-simulação-ns-3)
13. [Configurações](#13-configurações)
14. [Commits e Histórico](#14-commits-e-histórico)
15. [Status do Sistema](#15-status-do-sistema)
16. [Próximos Passos](#16-próximos-passos)

---

## 1. Visão Geral

O GreenRAN é um sistema de gerenciamento de energia para redes O-RAN (Open Radio Access Network) que implementa coordenação entre rApps (aplicações Non-RT RIC) e xApps (aplicações Near-RT RIC) para otimização de consumo energético em redes 5G.

### Objetivo Principal

Reduzir o consumo de energia da rede O-RAN enquanto mantém a qualidade de serviço (QoS) das câmeras de vigilância (latência < 100ms).

### Características Principais

- **Decisão hierárquica** com 6 estágios (Trend → Pattern → CVaR → ML → Agent → Arbiter)
- **Machine Learning** com 89.3% de acurácia e 3 classes
- **Monitoramento em tempo real** via Grafana e InfluxDB
- **Automação** com scheduler 4x por dia
- **Data Lake** otimizado com SQLite WAL mode
- **Structured logging** em formato JSONL

---

## 2. Arquitetura do Sistema

### Diagrama de Arquitetura

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Non-RT RIC Layer                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    rApp-ResourceOptimizer                              │  │
│  │                                                                        │  │
│  │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │  │
│  │   │ Data Lake    │  │ Pattern      │  │ Trend        │               │  │
│  │   │ (SQLite WAL) │  │ Engine (ML)  │  │ Analysis     │               │  │
│  │   └──────────────┘  └──────────────┘  └──────────────┘               │  │
│  │                                                                        │  │
│  │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │  │
│  │   │ ML Predictor │  │ Agent-Al     │  │ A1 Policy    │               │  │
│  │   │ (RF + XGB)   │  │ OpenRAN      │  │ Interface    │               │  │
│  │   └──────────────┘  └──────────────┘  └──────────────┘               │  │
│  │                                                                        │  │
│  │   ┌──────────────┐  ┌──────────────┐                                 │  │
│  │   │ XApp Manager │  │ Energy       │                                 │  │
│  │   │ (Lifecycle)  │  │ Protocol     │                                 │  │
│  │   └──────────────┘  └──────────────┘                                 │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│        │                           │                                        │
│        ▼                           ▼                                        │
│  ┌──────────────┐           ┌──────────────┐                               │
│  │ Dashboard    │           │ Scheduler    │                               │
│  │ Flask :5000  │           │ (4x/dia)     │                               │
│  └──────────────┘           └──────────────┘                               │
└─────────────────────────────────────────────────────────────────────────────┘
        │                           │
        ▼                           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Near-RT RIC Layer                                   │
│  ┌──────────────────────────┐      ┌──────────────────────────┐            │
│  │ xApp SLICER              │◄────►│ xApp ENERGY SAVER        │            │
│  │ (Monitoramento SLA)      │ rApp │ (Atuador de Energia)     │            │
│  │ SEMPRE ativo             │      │ Controlado pelo rApp     │            │
│  └──────────────────────────┘      └──────────────────────────┘            │
└─────────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           E2 Interface (E2AP v1)                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ nearRT-RIC (FlexRIC)                                                   │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Network Layer (ns-3)                                │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ ns-3 mmWave + LTE (scenario-greenran.cc)                               │  │
│  │                                                                        │  │
│  │ - 1 RU (LTE) + 1 mmWave                                                │  │
│  │ - 12 UEs: 3 câmeras + 9 UEs background                                 │  │
│  │ - S1-U: 15 Mbps (gargalo)                                              │  │
│  │ - Buffer RLC/PDCP: 20 MB                                               │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Monitoring Stack                                    │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │ InfluxDB     │◄───│ Push Stats   │◄───│ ns-3 Stats   │                  │
│  │ :8086        │    │ Python       │    │ Files        │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
│        │                                                                    │
│        ▼                                                                    │
│  ┌──────────────┐                                                          │
│  │ Grafana      │                                                          │
│  │ :3000        │                                                          │
│  └──────────────┘                                                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Fluxo de Dados

```
1. ns-3 Stats Files
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

---

## 3. Componentes Implementados

### 3.1 rApp Orchestrator

| Propriedade | Valor |
|-------------|-------|
| **Arquivo** | `rapp_orchestrator.py` |
| **Linhas** | 1290 |
| **Função** | Cérebro estratégico do Non-RT RIC |
| **Ciclo** | 1 segundo (mínimo O-RAN) |
| **Porta Dashboard** | 5000 |

**Cadeia de Inicialização:**

```python
# 1. Data Lake (SQLite WAL mode)
self.data_lake = DataLake()

# 2. Pattern Engine (ML moderado)
self.pattern_engine = PatternRecognition(self.data_lake)

# 3. Trend Analysis (Slope/Predição)
self.trend_analysis = TrendAnalysis(self.data_lake)

# 4. ML Predictor (Random Forest + XGBoost com acesso DB)
self.ml_predictor = MLPredictor(data_lake=self.data_lake)

# 5. Agent-Al (Tradução de intenções)
self.agent = AgentOpenRAN()

# 6. A1 Interface (Políticas JSON)
self.a1 = A1PolicyInterface()

# 7. XApp Manager (Ciclo de vida)
self.xapp_manager = XAppManager()
```

**Fluxo de Decisão Hierárquico:**

```python
# ETAPA 0: TREND ANALYSIS (Slope)
trend_decision = self.trend_analysis.analyze_latency_trend(
    window_minutes=5,
    cvar_threshold_us=CVAR_CRITICAL_US
)

# ETAPA 1: PATTERN ENGINE (ML)
pattern_analysis = self.pattern_engine.analyze_current()
ml_decision = self.pattern_engine.should_allow_energy_saving()

# ETAPA 2: CVaR/VARIANCE
network_health = self.data_lake.get_network_health(window_minutes=5)
cvar_us = network_health['cvar_us']

# ETAPA 2.3: PATTERN ENGINE INTEGRATION
# Ajusta decisão baseada no padrão detectado
if pattern == 'peak_hours' and decision['energy_saver'] == 'ALLOWED':
    decision['action'] = 'POWER_DOWN'
elif activity_level == 'low' and decision['energy_saver'] == 'ALLOWED':
    decision['action'] = 'POWER_DOWN_ECO'

# ETAPA 2.5: ML PREDICTION
ml_result = self.ml_predictor.predict_with_db_context(ml_metrics)

# ETAPA 3: AGENT-AL OVERRIDE
agent_intent = self.agent.read_intent()

# ETAPA 4: ARBITER FINAL
# Consolida todas as decisões
```

**Retreinamento Automático:**

```python
# Configurado para 2x por dia (12 em 12 horas)
self.ml_retrain_interval = 12 * 3600  # 43200 segundos

# Verificação no loop principal
def check_ml_retrain(self):
    now = time.time()
    elapsed = now - self.ml_last_retrain
    if elapsed >= self.ml_retrain_interval:
        self.retrain_ml()
```

---

### 3.2 ML Predictor

| Propriedade | Valor |
|-------------|-------|
| **Arquivo** | `rapp_ml_predictor.py` |
| **Linhas** | 319 |
| **Arquitetura** | Dual-model (Classifier + Regressor) |
| **Acurácia RF** | 89.3% |
| **Acurácia XGB** | 90.1% |
| **Classes** | 3 (ALLOWED, BLOCKED, CONDITIONAL) |
| **Janela DB** | 2 minutos |
| **Threshold Override** | 60% |

**Features (17 total):**

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

**Lógica de Override por Banco:**

```python
# Constantes de configuração
DB_OVERRIDE_THRESHOLD = 0.60  # 60% para override
DB_BOOST_THRESHOLD = 0.60     # 60% para boost de confiança
WINDOW_MINUTES = 2            # Janela de consulta ao banco

# Fluxo de predição com contexto DB
def predict_with_db_context(self, metrics):
    # 1. Predição do modelo ML
    ml_result = self.predict(metrics)
    
    # 2. Consulta banco de dados (últimos 2 min)
    db_stats = self.query_database_stats()
    
    # 3. Verifica distribuição de classes
    distribution = db_stats['distribution']
    
    # 4. OVERRIDE: Se BLOCKED ≥ 60% no banco
    if distribution.get('BLOCKED', 0) >= 60:
        ml_result['decision'] = 'BLOCKED'
        ml_result['confidence'] = distribution['BLOCKED'] / 100
        ml_result['source'] = 'database_override'
        return ml_result
    
    # 5. OVERRIDE: Se ALLOWED ≥ 60% no banco
    if distribution.get('ALLOWED', 0) >= 60:
        ml_result['decision'] = 'ALLOWED'
        ml_result['confidence'] = distribution['ALLOWED'] / 100
        ml_result['source'] = 'database_override'
        return ml_result
    
    # 6. BOOST: Se ML concorda com DB ≥ 60%
    if ml_result['decision'] in distribution:
        db_pct = distribution[ml_result['decision']]
        if db_pct >= 60:
            ml_result['confidence'] = min(0.95, ml_result['confidence'] * 1.2)
            ml_result['source'] = 'ml_boosted'
    
    return ml_result
```

---

### 3.3 Data Lake

| Propriedade | Valor |
|-------------|-------|
| **Arquivo** | `rapp_data_lake.py` |
| **Linhas** | 1359 |
| **Banco** | SQLite |
| **Localização** | `/tmp/rapp_data_lake.db` |
| **Modo WAL** | Ativo |
| **Cache** | 64 MB |

**Configuração WAL:**

```python
def _connect(self):
    self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
    self.conn.row_factory = sqlite3.Row
    
    # Otimizações de performance
    self.conn.execute("PRAGMA journal_mode=WAL")
    self.conn.execute("PRAGMA synchronous=NORMAL")
    self.conn.execute("PRAGMA cache_size=-64000")  # 64MB
    self.conn.execute("PRAGMA temp_store=MEMORY")
```

**Tabelas (7):**

| Tabela | Descrição |
|--------|-----------|
| `metrics_history` | Métricas básicas (latência, câmeras, estados) |
| `decisions_history` | Decisões do rApp com predições ML |
| `extended_metrics` | Métricas por UE (CVaR, variância, P95) |
| `ue_metrics` | Métricas individuais por UE (IMSI) |
| `hourly_stats` | Estatísticas agregadas por hora |
| `daily_stats` | Estatísticas agregadas por dia |
| `energy_commands` | Histórico de comandos de energia |

**Índices (9):**

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

**Métodos Principais:**

```python
# Cálculo de CVaR
def calculate_cvar(self, alpha=0.95, window_minutes=5):
    # Usa métricas POR UE (não agregado)
    # Retorna: CVaR em microsegundos

# Cálculo de variância
def calculate_variance(self, window_minutes=5):
    # Usa métricas POR UE
    # Retorna: variância em µs²

# Saúde da rede completa
def get_network_health(self, window_minutes=5):
    # Retorna: median, p95, cvar, variance, stability_score

# Limpeza automática
def cleanup_old_data(self, days=7):
    # Remove dados antigos e faz VACUUM
```

---

### 3.4 Dashboard

| Propriedade | Valor |
|-------------|-------|
| **Arquivo** | `rapp_dashboard.py` |
| **Linhas** | 505 |
| **Framework** | Flask |
| **Porta** | 5000 |
| **Host** | 0.0.0.0 |
| **Templates** | 7 arquivos HTML |

**Rotas:**

| Rota | Descrição |
|------|-----------|
| `/` | Dashboard principal (saúde da rede, tendência, xApps) |
| `/metrics` | Página de métricas detalhadas |
| `/decisions` | Histórico de decisões com filtros |
| `/pattern` | Análise de padrões (horária, diária, janelas de energia) |
| `/xapps` | Monitoramento de status dos xApps |
| `/ml` | Página de ML (status do modelo, predições, concordância) |
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

**Templates:**

| Template | Descrição |
|----------|-----------|
| `base.html` | Layout base |
| `dashboard.html` | Dashboard principal |
| `metrics.html` | Visão detalhada de métricas |
| `decisions.html` | Histórico de decisões |
| `pattern.html` | Análise de padrões |
| `xapps.html` | Status dos xApps |
| `ml.html` | Dashboard de ML |

---

## 4. Regras de Decisão

### Thresholds de Configuração

| Parâmetro | Valor | Descrição |
|-----------|-------|-----------|
| `CVAR_NORMAL_US` | 60,000 (60ms) | Limite superior da zona normal |
| `CVAR_CRITICAL_US` | 80,000 (80ms) | Zona crítica (risco de SLA) |
| `SLOPE_PREVENTION` | 2.0 ms/s | Threshold de prevenção por slope |
| `SLOPE_TOLERANCE` | 0.01 ms/s | Tolerância de estabilidade |
| `STABILITY_THRESHOLD` | 50 | Score mínimo de estabilidade |

### Regras Completas

| Regra | Condição | Decisão | Ação | Potência |
|-------|----------|---------|------|----------|
| **R1** | `slicer_state == CRITICAL` | BLOCKED | FULL_POWER | 100% |
| **R2** | `CVaR >= 80ms` | BLOCKED | FULL_POWER | 100% |
| **R3** | `slope > 2ms/s` | BLOCKED | FULL_POWER | 100% |
| **R4a** | `CVaR < 20ms + slope <= 0` | ALLOWED | POWER_DOWN_ECO | 25% |
| **R4b** | `CVaR < 60ms + slope < -0.01` | ALLOWED | POWER_DOWN | 50% |
| **R4c** | `CVaR < 60ms + slope ≈ 0` | ALLOWED | POWER_DOWN | 60% |
| **R4d** | `60-80ms + slope < -0.01` | ALLOWED | POWER_DOWN | 70% |
| **R4e** | `60-80ms + slope ≈ 0` | ALLOWED | POWER_DOWN | 80% |
| **R5** | `CVaR < 60ms + slope > 0.01` | CONDITIONAL | MONITOR | 70% |
| **R6** | `60-80ms + slope > 0.01` | CONDITIONAL | MONITOR | 90% |

### Classificação de Estado do Slope

| Range do Slope | Estado | Significado |
|----------------|--------|-------------|
| `< -0.01 ms/s` | `improving` | Latência diminuindo |
| `-0.01 a +0.01 ms/s` | `stable` | Latência estável |
| `> +0.01 ms/s` | `worsening` | Latência aumentando |

---

## 5. Sistema de Machine Learning

### Modelos Treinados

| Modelo | Arquivo | Função |
|--------|---------|--------|
| Random Forest Classifier | `rf_classifier.joblib` | Predição de decisão |
| XGBoost Classifier | `xgb_classifier.joblib` | Predição de decisão (melhor) |
| Random Forest Regressor | `rf_regressor.joblib` | Predição de valor CVaR |
| StandardScaler (classifier) | `rf_scaler.joblib` | Normalização de features |
| StandardScaler (regressor) | `reg_scaler.joblib` | Normalização de features |
| LabelEncoder | `label_encoder.joblib` | Encoding de classes |

### Relatório de Treinamento (02/04/2026)

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

### Feature Importance (Top 5)

| Feature | Importance |
|---------|------------|
| `sim_time_s` | 0.208 |
| `cvar_rolling_mean` | 0.179 |
| `hour_cos` | 0.157 |
| `hour_sin` | 0.131 |
| `cvar_ms` | 0.093 |

### Lógica de Override e Boost

```python
# 1. Obter predição ML
ml_result = self.predict(metrics)

# 2. Consultar banco (últimos 2 minutos)
db_stats = self.query_database_stats()

# 3. Verificar distribuição
distribution = db_stats['distribution']

# 4. OVERRIDE: Se BLOCKED ≥ 60%
if distribution.get('BLOCKED', 0) >= 60:
    ml_result['decision'] = 'BLOCKED'
    ml_result['confidence'] = distribution['BLOCKED'] / 100
    ml_result['source'] = 'database_override'

# 5. OVERRIDE: Se ALLOWED ≥ 60%
if distribution.get('ALLOWED', 0) >= 60:
    ml_result['decision'] = 'ALLOWED'
    ml_result['confidence'] = distribution['ALLOWED'] / 100
    ml_result['source'] = 'database_override'

# 6. BOOST: Se ML concorda com DB ≥ 60%
if ml_result['decision'] in distribution:
    db_pct = distribution[ml_result['decision']]
    if db_pct >= 60:
        ml_result['confidence'] = min(0.95, ml_result['confidence'] * 1.2)
        ml_result['source'] = 'ml_boosted'
```

---

## 6. Dashboard e Monitoramento

### Dashboard Flask (localhost:5000)

**Página Principal (`/`):**
- Latência P95
- Câmeras ativas
- Quality Score
- Status dos xApps
- Histórico de decisões (60 min)
- Estatísticas (24h)
- Decisão atual do rApp
- Regras de coordenação

**Página ML (`/ml`):**
- Status do modelo (ativo/inativo)
- Acurácia do modelo
- Última predição
- CVaR previsto
- Concordância ML vs Regras
- Histórico de predições
- Feature importance

### Dashboard Grafana (localhost:3000)

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

**Configuração de Cores:**

```json
{
  "UE Latency by IMSI": {
    "colorMode": "thresholds",
    "thresholds": [
      {"color": "green", "value": null},
      {"color": "yellow", "value": 60},
      {"color": "red", "value": 80}
    ],
    "thresholdsStyle": "line+area"
  },
  "UE Throughput TX/RX": {
    "colorMode": "thresholds",
    "thresholds": [
      {"color": "green", "value": null},
      {"color": "yellow", "value": 350000},
      {"color": "red", "value": 400000}
    ],
    "thresholdsStyle": "line+area"
  }
}
```

### Structured Logging

**Arquivo:** `/tmp/rapp_decisions.jsonl`

**Formato:**
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

## 7. Scheduler e Automação

### greenran_scheduler.sh

**Configuração:**

```bash
# Cron: 4x por dia às 00:00, 06:00, 12:00, 18:00
0 0,6,12,18 * * * /home/robert/orange_nuclear/greenran_scheduler.sh

# Variáveis
SIM_DURATION=600        # 10 minutos em segundos
RETRAIN_DELAY=300       # Retreinar após 5 minutos
```

**Fluxo de Execução:**

```bash
1. [0s]     Iniciar cenário (run_greenran_v2.sh)
2. [10s]    Aguardar inicialização
3. [300s]   Retreinar ML (train_ml_model.py)
4. [600s]   Parar todos os processos (stop_all.sh)
5. [final]  Salvar logs em /tmp/greenran_logs/
```

**Output:**
```
/tmp/greenran_logs/run_YYYYMMDD_HHMMSS.log
```

---

## 8. Data Lake

### Tabelas SQLite

**metrics_history:**
```sql
CREATE TABLE metrics_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    datetime TEXT NOT NULL,
    latency_us REAL NOT NULL,
    cameras_active INTEGER NOT NULL,
    critical_cameras INTEGER DEFAULT 0,
    energy_state TEXT,
    slicer_state TEXT,
    UNIQUE(timestamp)
)
```

**decisions_history:**
```sql
CREATE TABLE decisions_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    datetime TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    confidence REAL,
    pattern TEXT,
    agent_override INTEGER DEFAULT 0,
    energy_state TEXT,
    slicer_state TEXT,
    ml_decision TEXT,
    ml_confidence REAL,
    ml_predicted_cvar_ms REAL,
    ml_influenced INTEGER DEFAULT 0,
    UNIQUE(timestamp)
)
```

**extended_metrics:**
```sql
CREATE TABLE extended_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    datetime TEXT NOT NULL,
    sim_time_s REAL,
    cell_id INTEGER DEFAULT 0,
    global_worst_latency_us REAL,
    global_avg_latency_us REAL,
    global_min_latency_us REAL,
    global_max_latency_us REAL,
    global_jitter_us REAL,
    packet_loss_rate REAL,
    total_active_ues INTEGER,
    total_active_cameras INTEGER,
    total_critical_ues INTEGER,
    total_tx_bytes INTEGER,
    total_rx_bytes INTEGER,
    total_tx_pdus INTEGER,
    total_rx_pdus INTEGER,
    throughput_kbps REAL,
    energy_state TEXT,
    slicer_state TEXT,
    latency_p5_us REAL,
    latency_p95_us REAL,
    latency_min_nonzero_us REAL,
    valid_samples INTEGER,
    cvar_per_ue_us REAL,
    variance_per_ue_us2 REAL,
    latency_p95_per_ue_us REAL,
    UNIQUE(timestamp)
)
```

---

## 9. Protocolo de Energia

### Estados de Energia

| Estado | Potência | Descrição |
|--------|----------|-----------|
| FULL_POWER | 100% | Todos os recursos ativos |
| CONDITIONAL_REDUCE | 70% | Redução moderada |
| POWER_DOWN | 50% | Economia significativa |
| POWER_DOWN_ECO | 25% | Economia extrema |
| MAINTAIN | - | Manter estado atual |

### Comandos JSON

```json
{
  "action": "POWER_DOWN",
  "power_percent": 50,
  "reason": "ALLOWED: CVaR=40.1ms < 60ms + slope=0ms/s",
  "ttl": 5,
  "timestamp": 1775070943
}
```

### Watchdog TTL

```
DEFAULT_TTL = 5 segundos

Se Energy Saver não receber comando por 5s:
  → Automaticamente volta para FULL_POWER
  → Sistema de segurança
```

---

## 10. Interface A1

### Políticas A1

**Formato JSON:**
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

**Interface:**
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

## 11. xApps

### xApp SLICER

| Propriedade | Valor |
|-------------|-------|
| **Prioridade** | Sempre ativo (máxima) |
| **Função** | Monitoramento de SLA |
| **SLA** | < 100ms |
| **Estados** | NORMAL, CRITICAL, IDLE |
| **Arquivo** | flexric build |

**Quando CRITICAL:**
- Sinaliza para rApp
- rApp pode sobrescrever com BLOCKED

### xApp ENERGY SAVER

| Propriedade | Valor |
|-------------|-------|
| **Prioridade** | Controlado pelo rApp |
| **Função** | Execução de comandos de energia |
| **Estados** | FULL_POWER, CONDITIONAL_REDUCE, POWER_DOWN, POWER_DOWN_ECO |
| **Protocolo** | JSON via /tmp/xapp_intents/energy_command.json |

**Fluxo:**
```
rApp Orchestrator
    ↓
Energy Command (JSON)
    ↓
/tmp/xapp_intents/energy_command.json
    ↓
xApp Energy Saver
    ↓
Executa comando
    ↓
Registra resultado
```

---

## 12. Simulação ns-3

### Parâmetros do Cenário

| Parâmetro | Valor | Descrição |
|-----------|-------|-----------|
| S1-U DataRate | 15 Mbps | Gargalo da rede |
| S1-U Delay | 5 ms | Atraso do enlace |
| P2P Link | 30 Mbps, 20ms | Enlace ponto-a-ponto |
| Buffer RLC/PDCP | 20 MB | Tamanho do buffer |
| Total UEs | 12 | Dispositivos conectados |
| Câmeras | 3 | IMSI 1-3 |
| Background | 9 | IMSI 4-12 |
| Camera OffTime | 3s | Período de inatividade |
| Background Bursty | On=1s, Off=10s | Padrão de tráfego |

### Arquivos de Estatísticas

```
/home/robert/orange_nuclear/ns-O-RAN-flexric/mmwave-LENA-oran/
├── DlE2PdcpStats.txt          # PDCP stats por UE
├── DlMacStats.txt             # MAC stats
├── DlPhyTransmissionTrace.txt # PHY transmission
├── RxPacketTrace.txt          # Packet trace
├── cu-cp-cell-*.txt           # CU-CP stats
├── cu-up-cell-*.txt           # CU-UP stats
├── du-cell-*.txt              # DU stats
└── enbs.txt                   # eNB stats
```

---

## 13. Configurações

### Scheduler

```bash
# Cron: 4x por dia
0 0,6,12,18 * * * /home/robert/orange_nuclear/greenran_scheduler.sh

# Variáveis
SIM_DURATION=600        # 10 minutos
RETRAIN_DELAY=300       # Retreinar após 5 minutos
```

### ML

```python
WINDOW_MINUTES = 2                # Janela de consulta DB
DB_OVERRIDE_THRESHOLD = 0.60      # 60% para override
DB_BOOST_THRESHOLD = 0.60         # 60% para boost
ml_retrain_interval = 43200       # 12 horas (segundos)
```

### CVaR

```python
CVAR_NORMAL_US = 60000       # 60ms - Zona normal
CVAR_CRITICAL_US = 80000     # 80ms - Zona crítica
SLOPE_PREVENTION = 2.0       # 2ms/s - Prevenção
STABILITY_THRESHOLD = 50     # Score mínimo de estabilidade
SLOPE_TOLERANCE = 0.01       # Tolerância de slope
```

### Protocolo de Energia

```python
FULL_POWER:          100% (RU=1, mmWave=1)
CONDITIONAL_REDUCE:  70% (TTL=3s)
POWER_DOWN:          50% (TTL=5s)
POWER_DOWN_ECO:      25% (TTL=5s)
MAINTAIN:            Manter estado atual
DEFAULT_TTL = 5      Timeout watchdog (segundos)
```

### ns-3

```cpp
S1-U DataRate: 15 Mbps (gargalo)
S1-U Delay: 5 ms
P2P Link: 30 Mbps, 20ms
Buffer RLC/PDCP: 20 MB
Total UEs: 12 (3 câmeras + 9 background)
Camera OffTime: 3s (rajadas frequentes)
Background: Bursty (On=1s, Off=10s)
```

---

## 14. Commits e Histórico

| Hash | Tipo | Descrição |
|------|------|-----------|
| `083332d` | fix | Alterar janela ML de 10 para 2 minutos |
| `820dec8` | feat | Retreinar modelo ML com 3 classes |
| `e275209` | update | TX/RX Throughput thresholds |
| `072a18f` | update | UE Latency thresholds |
| `8d10b7a` | feat | Scheduler 4x/dia com retreinamento ML |
| `bef8ccf` | feat | ML com acesso DB em tempo real |
| `683cc32` | fix | Salvar predições ML no banco |
| `00bb10d` | feat | Otimizar Data Lake (WAL + índices) |
| `a0a5d23` | feat | Structured logging (JSONL) |
| `137c54b` | feat | Dashboard ML |
| `c29ab10` | feat | Retreinamento ML recente |
| `ece0c81` | feat | Refinar regras de decisão |
| `f5e67ba` | feat | Pipeline ML (Random Forest + XGBoost) |
| `fe5cc26` | chore | Script startup CVaR pusher |
| `6d38100` | fix | Filtro de tempo CVaR e query Grafana |
| `155bb72` | feat | Adicionar CVaR ao Dashboard |
| `06a91f3` | feat | Implementar coordenação rApp-xApp |
| `249de4c` | feat | Implementar métricas por UE |
| `130faac` | feat | Implementar economia 3 bandas |
| `91f8e75` | feat | rApp como ARBITER SUPERIOR |
| `ffb1954` | feat | Implementar lógica de mediana |

---

## 15. Status do Sistema

### Componentes Funcionais

| Componente | Status | Detalhes |
|------------|--------|----------|
| rApp Orchestrator | **Funcionando** | Ciclo 1s, 6 estágios operacionais |
| ML Predictor | **Funcionando** | 89.3% accuracy, 3 classes, DB tempo real |
| Data Lake | **Funcionando** | SQLite WAL, 7 tabelas, 9 índices |
| Dashboard | **Funcionando** | Flask porta 5000, 14 rotas |
| Pattern Engine | **Funcionando** | SMA, EMA, detecção sazonal |
| Trend Analysis | **Funcionando** | Regressão linear, slope, aceleração |
| Energy Protocol | **Funcionando** | Comandos JSON com TTL watchdog |
| Agent-Al | **Funcionando** | 6 templates, janelas de tempo |
| XApp Manager | **Funcionando** | start/stop/restart, cleanup zombies |
| Scheduler | **Funcionando** | 4x/dia, auto-retreinamento |
| Grafana/InfluxDB | **Funcionando** | Métricas em tempo real |
| nearRT-RIC | **Funcionando** | E2AP v1, FlexRIC |
| ns-3 Simulation | **Funcionando** | mmWave + LTE, 12 UEs |

### Métricas de Performance

```json
{
  "training_report": {
    "dataset_size": 1775,
    "rf_accuracy": 89.3%,
    "xgb_accuracy": 90.1%,
    "cv_mean": 91.1%,
    "regressor_r2": 0.99995
  },
  "data_lake": {
    "extended_metrics": 1896,
    "decisions_history": 1941,
    "ue_metrics": 2400+
  }
}
```

### Inventário de Arquivos

| Arquivo | Linhas | Função |
|---------|--------|--------|
| rapp_orchestrator.py | 1290 | Orquestrador principal |
| rapp_ml_predictor.py | 319 | Predição ML |
| rapp_data_lake.py | 1359 | Persistência de dados |
| rapp_dashboard.py | 505 | Dashboard web |
| rapp_pattern_engine.py | 1140 | Detecção de padrões |
| rapp_trend_analysis.py | 611 | Análise de tendência |
| train_ml_model.py | 482 | Treinamento ML |
| rapp_agent_openran.py | 516 | Interface agent |
| rapp_xapp_manager.py | 384 | Ciclo de vida xApp |
| energy_command_protocol.py | 298 | Protocolo de energia |
| greenran_scheduler.sh | 108 | Scheduler |
| push_stats_to_influx.py | 510 | Pusher InfluxDB |
| run_greenran_complete.sh | 431 | Execução completa |

---

## 16. Próximos Passos

### Melhorias Pendentes

1. **Melhorar modelo ML**
   - Adicionar mais features
   - Re-treinar com mais dados
   - Implementar validação cruzada

2. **Dashboard ML**
   - Mostrar mais detalhes de predições
   - Adicionar gráficos de concordância
   - Implementar filtros

3. **Testes Automatizados**
   - Implementar testes unitários
   - Testes de integração
   - Testes de performance

4. **Documentação API**
   - Documentar endpoints REST
   - Criar exemplos de uso
   - Implementar versionamento

5. **Deploy Produção**
   - Configurar para ambiente real
   - Implementar monitoramento avançado
   - Configurar alertas

### Melhorias de Curto Prazo

- [ ] Adicionar mais painéis ao Grafana
- [ ] Implementar log rotation
- [ ] Otimizar queries do Data Lake
- [ ] Adicionar mais métricas de ML

### Melhorias de Médio Prazo

- [ ] Implementar A/B testing de modelos
- [ ] Adicionar suporte a múltiplos cenários
- [ ] Implementar backup automático
- [ ] Criar interface de administração

---

## Conclusão

O projeto GreenRAN está **completamente funcional** com:

- **Sistema de decisão hierárquico** com 6 estágios (Trend → Pattern → CVaR → ML → Agent → Arbiter)
- **Machine Learning** com 89.3% de acurácia e 3 classes
- **Dashboard web** e **Grafana** para monitoramento
- **Scheduler automático** 4x por dia
- **Data Lake otimizado** com SQLite WAL mode
- **Structured logging** em formato JSONL

Todos os componentes estão integrados e funcionando corretamente. O sistema é capaz de:

1. Decidir quando economizar energia (ALLOWED)
2. Decidir quando proteger SLA (BLOCKED)
3. Monitorar em tempo real via dashboards
4. Aprender com dados históricos via ML
5. Adaptar-se automaticamente via retreinamento

---

**Documento criado em**: 02 de Abril de 2026
**Autor**: Assistente IA
**Projeto**: GreenRAN O-RAN
**Repositório**: [github.com/Robertwanzeler/-greenran-oran-.git](https://github.com/Robertwanzeler/-greenran-oran-.git)
