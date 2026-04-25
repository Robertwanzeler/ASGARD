# GreenRAN O-RAN - Coordenação rApp-xApps

## Leitura Recomendada Atual

Para o estado atual do projeto, estes documentos devem ser lidos primeiro:

- `docs/CONFLICT_DATASET_PIPELINE.md`
  - exportacao do dataset de conflitos;
  - aprendizado da matriz operacional;
  - treino GraphSAGE por epocas;
  - estrutura da coleta por cenarios.
- `docs/ANALISE_CONFLITOS_GNN.md`
  - resumo do treino multiseed;
  - splits usados em `conflito_implicito` e `recuperacao`;
  - os 6 graficos finais e o que eles significam para o cenario.
- `docs/DOCUMENTACAO_GREENRAN.md`
  - documentacao ampla do sistema.

## Visão Geral

O projeto GreenRAN implementa um sistema de coordenação entre rApps (Non-RT RIC) e xApps (Near-RT RIC) para gerenciamento de energia em redes O-RAN, garantindo que câmeras de vigilância 4K nunca sofram latência alta.

## Arquitetura de Coordenação

### Hierarquia de Controle

```
┌─────────────────────────────────────────────────────────────────┐
│                    ARQUITETURA GREENRAN                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  rApp Orchestrator (Juiz)                                       │
│  ├── Visibilidade GLOBAL (Data Lake)                          │
│  ├── Calcula: CVaR/UE, Slope, Variância                       │
│  ├── Decide: ALLOWED/BLOCKED/CONDITIONAL                      │
│  └── Envia: Comandos para xApps                               │
│                                                                 │
│  xApp Slicer (Advogado 1)                                      │
│  ├── Monitora P95 latency por UE                              │
│  ├── Se câmeras > 80ms: CRITICAL → aloca PRBs                │
│  └── rApp decide sobre energia                                │
│                                                                 │
│  xApp Energy Saver (Advogado 2)                                │
│  ├── Lê comandos do rApp                                      │
│  ├── Executa: FULL_POWER/REDUCE/POWER_DOWN                   │
│  └── Watchdog: 5s → FULL_POWER                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Regras de Coordenação

### REGRA 1: Prioridade Máxima - Câmeras
```
Se Slicer CRITICAL:
├── rApp: BLOCKED
├── Energy Saver: FULL_POWER
└── Motivo: Proteger câmeras (prioridade absoluta)
```

### REGRA 2: Proteção de SLA
```
Se CVaR ≥ 80ms:
├── rApp: BLOCKED
├── Energy Saver: FULL_POWER
└── Motivo: SLA em risco
```

### REGRA 3: Prevenção de Problemas
```
Se Slope > 2ms/s:
├── rApp: BLOCKED (preventivo)
├── Energy Saver: FULL_POWER
└── Motivo: Latência vai subir
```

### REGRA 4: Economia Segura
```
Se CVaR < 60ms E Slope < 0:
├── rApp: ALLOWED
├── Energy Saver: POWER_DOWN
└── Motivo: Rede estável e melhorando
```

## Zonas de Operação

### Zona Verde (Normal)
```
Condição: CVaR < 60ms
Decisão: ALLOWED
Ação: Energy Saver pode economizar
Comportamento: Sistema funcionando normalmente
```

### Zona Amarela (Prevenção)
```
Condição: 60ms < CVaR < 80ms
Decisão: CONDITIONAL
Ação: Monitorar, não reduzir
Comportamento: rApp monitora tendência
```

### Zona Vermelha (Crítico)
```
Condição: CVaR > 80ms
Decisão: BLOCKED
Ação: FULL_POWER, não economizar
Comportamento: SLA em risco, proteger câmeras
```

## Dashboard de Coordenação

### Painéis Disponíveis
```
CVaR/UE:      Mostra CVaR atual e decisão
Slope:        Mostra tendência (subindo/descendo)
Decisão:      Mostra ALLOWED/BLOCKED/CONDITIONAL
Regras:       Tabela com status de cada regra
```

### Como Interpretar
```
CVaR < 60ms + Slope < 0:    REGRA 4 ATIVA → Energia pode economizar
CVaR > 80ms:                REGRA 2 ATIVA → SLA em risco
Slicer CRITICAL:            REGRA 1 ATIVA → Câmeras prioridade
```

## Implementação Técnica

### rApp Orchestrator
```python
def make_decision(self, slicer_intent, energy_intent):
    """
    Regras de coordenação rApp-xApps:
    1. Câmeras são prioridade MÁXIMA
    2. rApp decide baseado em CVaR/UE
    3. Prevenção via Slope
    """
    
    # REGRA 1: PRIORIDADE MÁXIMA
    if slicer_state == 'CRITICAL':
        return BLOCKED, "Câmeras em risco"
    
    # REGRA 2: SLA em risco
    if cvar_per_ue > 80:
        return BLOCKED, "CVaR ≥ 80ms"
    
    # REGRA 3: PREVENÇÃO
    if slope > 2:
        return BLOCKED, "Slope > 2ms/s"
    
    # REGRA 4: ECONOMIA segura
    if cvar_per_ue < 60 and slope < 0:
        return ALLOWED, "Rede estável"
```

### xApp Energy Saver
```c
// Atuador puro - executa comandos do rApp
void execute_command(char* command) {
    if (strcmp(command, "FULL_POWER") == 0) {
        // Potência máxima
    }
    if (strcmp(command, "POWER_DOWN") == 0) {
        // Economia
    }
}

// Watchdog - proteção
void watchdog() {
    if (no_command_for > 5_seconds) {
        execute_command("FULL_POWER");
    }
}
```

## Métricas de Monitoramento

### Data Lake
```sql
-- CVaR por UE
SELECT cvar_per_ue_us FROM extended_metrics ORDER BY timestamp DESC LIMIT 10

-- Decisões do rApp
SELECT decision, reason FROM decisions_history ORDER BY timestamp DESC LIMIT 10

-- Latência por UE
SELECT latency_p95_per_ue_us FROM extended_metrics ORDER BY timestamp DESC LIMIT 10
```

### Dashboard
```
URL: http://localhost:5000
Painéis:
├── CVaR/UE (44-55ms)
├── Slope (+0.0ms/s)
├── Decisão rApp
└── Regras de Coordenação
```

## Framework Científico

### Referências
```
O-RAN Alliance: https://oran-alliance.org
3GPP Standards: TS 28.541 (Network Slicing)
IEEE: "Energy-Efficient O-RAN" papers
```

### Técnicas Implementadas
```
✅ Token-based access (JSON commands)
✅ Guardrails (CVaR/Slope limits)
✅ Watchdog (5s timeout)
✅ Priority-based arbitration
✅ Trend analysis (Slope)
```

## Status do Projeto

### Implementado
```
✅ rApp Orchestrator com regras de coordenação
✅ Dashboard com painéis de coordenação
✅ xApps como atuadores puros
✅ Watchdog para proteção
✅ Análise de tendência (Slope)
```

### Em Desenvolvimento
```
⬜ Machine Learning para previsão
⬜ RL para otimização
⬜ Digital Twin para simulação
```

## Como Usar

### Iniciar Sistema
```bash
cd /home/robert/orange_nuclear
./run_greenran_v2.sh
```

### Acessar Dashboard
```
URL: http://localhost:5000
Painéis:
├── CVaR/UE (mostra decisão)
├── Slope (mostra tendência)
├── Decisão rApp
└── Regras de Coordenação
```

### Monitorar Simulação
```bash
./monitor_data_collection.sh
```

## Contato
Projeto: GreenRAN O-RAN
Instituição: UFPA
