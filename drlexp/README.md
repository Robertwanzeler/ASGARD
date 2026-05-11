# GreenRAN DRL Experiment
## Implementação de Deep Reinforcement Learning para Alocação de Recursos em Open RAN

Este projeto implementa o algoritmo **EE-DRL-RA** baseado no artigo da IEEE Transactions on Vehicular Technology (2022) para comparação com o sistema atual de Random Forest.

---

## 📁 Estrutura do Projeto

```
drlexp/
├── src/drl/
│   ├── __init__.py              # Package init
│   ├── gym_environment.py       # Gymnasium environment
│   ├── replay_buffer.py         # Experience replay buffer
│   ├── models/
│   │   ├── __init__.py
│   │   ├── sbilstm.py          # SBiLSTM (Large time-scale)
│   │   ├── actor.py            # Actor Network (A3C)
│   │   └── critic.py           # Critic Network (A3C)
├── training/
│   ├── train_sbilstm.py        # Treinar SBiLSTM
│   └── train_a3c.py            # Treinar A3C
├── config/
│   └── drl_config.yaml         # Configuração principal
├── models/                      # Modelos treinados
│   ├── sbilstm/
│   └── a3c/
├── requirements.txt             # Dependências
└── README.md                   # Este arquivo
```

---

## 🎯 Objetivos

1. **Comparar Random Forest vs DRL** para alocação de recursos
2. **Implementar estado da arte** (SBiLSTM + A3C + EE-PA)
3. **Otimizar eficiência energética** mantendo SLA de latência

---

## 🧠 Arquitetura DRL

### 1. SBiLSTM (Large Time-Scale)
- **Predição**: Recursos necessários para próximo Prediction Window
- **Input**: 18 features de estado
- **Arquitetura**: 2 camadas BiLSTM (128 → 64)
- **Target MSE**: < 0.001

### 2. A3C (Small Time-Scale)
- **Decisão**: ALLOWED/CONDITIONAL/BLOCKED + Ajuste de potência
- **Workers**: 8 (paralelo)
- **Action Space**: 9 classes híbridas
- **Convergência**: reward → 0

### 3. EE-PA (Power Allocation)
- **Otimização**: Gradient descent para energia
- **Target**: ηEE > 80%

---

## 🚀 Como Usar

### 1. Instalar dependências
```bash
cd drlexp
pip install -r requirements.txt
```

### 2. Treinar SBiLSTM
```bash
python training/train_sbilstm.py --db /tmp/rapp_data_lake.db --epochs 50
```

### 3. Treinar A3C
```bash
python training/train_a3c.py --workers 8 --db /tmp/rapp_data_lake.db
```

---

## 📊 Métricas de Comparação

| Métrica | RF Atual | DRL Target |
|---------|----------|------------|
| Acurácia | 98.01% | > 90% |
| MAE (CVaR) | 0.58ms | < 0.5ms |
| Eficiência Energética | N/A | > 80% |
| Latência Inferência | ~1ms | < 10ms |

---

## 📚 Referência

Azimi, Y., Yousefi, S., Kalbkhani, H., & Kunz, T. (2022). Energy-Efficient Deep Reinforcement Learning Assisted Resource Allocation for 5G-RAN Slicing. IEEE Transactions on Vehicular Technology, 71(1), 856-871.

---

## 👥 Equipe

**GreenRAN Team - UFPA**
- Projeto: Open RAN Sustentável para Agro e Campi Inteligentes
- Artigo de referência: IEEE TVT 2022