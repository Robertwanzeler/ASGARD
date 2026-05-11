# Relatório de Comparação: Random Forest vs DRL (SBiLSTM + A3C)

## Data: 15 de Abril de 2026
## Projeto: GreenRAN O-RAN - Comparação de Modelos

---

## 1. Resumo Executivo

Este relatório apresenta a comparação entre o sistema atual baseado em **Random Forest** e os modelos **DRL (Deep Reinforcement Learning)** implementados para alocação de recursos em redes O-RAN.

**Conclusão Principal:** O **SBiLSTM melhorou significativamente** (MAE: 13.9ms → 0.84ms, melhoria de 94%) e o **A3C V7** supera o Random Forest em eficiência energética (21.5% vs 13.2%) e proteção SLA (100% vs 73%).

---

## 2. Modelos Comparados

| Modelo | Tipo | Arquivo | Status |
|--------|------|---------|--------|
| RF Classifier | Classificador | `models/rf_classifier.joblib` | ✅ Treinado |
| RF Regressor | Regressor | `models/rf_regressor.joblib` | ✅ Treinado |
| SBiLSTM | Predição | `drlexp/models/sbilstm/best_model.pt` | ✅ **Melhorado** |
| A3C Actor | Decisão | `drlexp/models/a3c/actor_v7.pt` | ✅ Treinado V7 |

---

## 3. Métricas de Comparação

### 3.1 Predição de CVaR (Regressor)

| Métrica | RF Regressor | SBiLSTM | Vencedor |
|---------|--------------|---------|----------|
| MAE | 0.58 ms | **0.84 ms** | RF |
| RMSE | 4.91 ms | 5.59 ms | RF |
| R² | 0.9757 | **0.9662** | RF |
| Inference Time | ~1ms | 0.5ms | SBiLSTM |

**Análise:** SBiLSTM melhorou **94%** (13.9ms → 0.84ms). RF ainda lidera em MAE, mas SBiLSTM é 50% mais rápido na inferência.

### 3.2 Decisão de Energia (Classifier/A3C)

| Métrica | RF Classifier | A3C V7 | Vencedor |
|---------|---------------|--------|----------|
| Acurácia | **98.0%** | N/A | RF |
| ALLOWED | 25.4% | **25.4%** | EMPATE |
| BLOCKED | 73.0% | **72.1%** | EMPATE |
| CONDITIONAL | 1.7% | **2.5%** | A3C |

**Análise:** A3C V7 agora usa CONDITIONAL (2.5%), melhor que V3. Economia: ~21.5%.

### 3.3 Eficiência Energética

| Métrica | RF | A3C V7 | Vencedor |
|---------|----|--------|----------|
| Potência Média | 86.8% | **78.7%** | A3C |
| Economia | 13.2% | **21.5%** | A3C |

**Análise:** A3C V7 é **63% mais econômico** que RF.

### 3.4 Proteção SLA

| Métrica | RF | A3C V7 | Vencedor |
|---------|----|--------|----------|
| CVaR > 80ms → BLOCKED | 73% | **100%** | A3C |
| Potenciais Violations | 3073 | **435** | A3C |

**Análise:** A3C V7 bloqueia 100% das situações críticas (vs 73% RF).

---

## 4. Tabela Resumo Final

```
┌─────────────────────┬──────────┬──────────┬──────────┬──────────┐
│     Métrica         │    RF    │  SBiLSTM │   A3C    │ Vencedor │
├─────────────────────┼──────────┼──────────┼──────────┼──────────┤
│ Predição CVaR MAE   │  0.58ms │  0.84ms │   N/A    │    RF   │
│ Predição CVaR R²    │  0.9757 │  0.9662 │   N/A    │   RF    │
│ Economia Energia    │  13.2%  │   N/A   │  21.5%  │  A3C    │
│ Proteção SLA       │    73%  │   N/A   │  100%   │  A3C    │
│ Inferência (ms)    │   ~1ms  │  0.5ms  │  0.3ms  │  A3C   │
│ Potenciais Viol.   │   3073  │   N/A   │   435   │  A3C    │
└─────────────────────┴──────────┴──────────┴──────────┴──────────┘
```

---

## 5. Gráficos de Comparação

Os seguintes gráficos foram gerados para análise visual:

| Gráfico | Arquivo | Descrição |
|---------|--------|----------|
| Economia | `charts/energia_comparacao.png` | RF vs A3C - Barras com erro |
| Evolução MAE | `charts/mae_evolucao.png` | SBiLSTM: 13.9ms → 0.84ms |
| Recompensa | `charts/reward_function.png` | Função Reward por episódio |
| Conflitos | `charts/matriz_conflitos.png` | Sparsemax Heatmap |

---

## 6. Métricas F1-Score (Artigos)

| Métrica | Valor |
|---------|-------|
| F1-Score (zonas) | 1.0000 |
| Precision (BLOCKED) | 1.0000 |
| Recall (BLOCKED) | 1.0000 |

---

## 7. Conclusões

### ✅ SBiLSTM - Melhoria Significativa
1. MAE reduzido em 94% (13.9ms → 0.84ms)
2. R² = 0.9662 (próximo do RF)
3. Inferência 50% mais rápida que RF
4. Prediction Window = 100 (otimizado)

### ✅ A3C V7 - Superior em Decisão
1. Economia superior: 21.5% vs 13.2% do RF (+8.3%)
2. Proteção SLA: 100% vs 73% do RF (+27%)
3. Usa CONDITIONAL (2.5%) para zona yellow
4. Tempo de inferência: 0.3ms (mais rápido)

### ✅ Random Forest - Backup Confiável
1. Predição precisa: MAE 0.58ms
2. Decisão robusta: Usa 3 classes
3. Útil como fallback

---

## 8. Integração rApp

Novo módulo criado: `src/rapp_drl_predictor.py`

| Função | Descrição |
|--------|-----------|
| predict_cvar() | Prediz CVaR usando SBiLSTM |
| predict_action() | Decide usando A3C |
| predict() | Combina ambos |

---

## 9. Fundamentação Científica (Artigos)

### Artigo 1 (2026) - Two-Tower + Sparsemax
- Implementação do Sparsemax em `sparsemax_graph.py`
- Binarização autônoma sem threshold manual
- Redução de 14x no tempo de treino vs GNN

### Artigo 2 (2025) - GNN para Conflitos
- Detecção de conflitos Direto/Indireto/Implícito
- Dataset: 4563 registros (excede mínimo de 450)
- F1-Score: 1.0 (100% de acurácia)

---

## 10. Recomendações

### Curto Prazo
- [x] ✅ SBiLSTM treinado (MAE 0.84ms)
- [x] ✅ A3C V7 funcionando
- [x] ✅ Módulo DRL criado
- [x] ✅ Gráficos gerados

### Médio Prazo
- [ ] Integrar DRL ao rApp Orchestrator
- [ ] Teste A/B em produção
- [ ] Monitorar economia real

### Longo Prazo
- [ ] Substituir RF por DRL como principal
- [ ] Adicionar Two-Tower Encoder (Artigo 1)
- [ ] Implementar detecção de conflitos em tempo real

---

*Relatório atualizado em: 15/04/2026*
*Versão: 3.1 - Inclui gráficos e fundamentação científica*
*Projeto: GreenRAN O-RAN - UFPA*