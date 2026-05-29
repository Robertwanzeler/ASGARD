# Relatório Técnico da DRL no GreenRAN

## 1. Escopo

Este documento descreve a implementação real da trilha de DRL do GreenRAN, do jeito que ela existe hoje no repositório e no runtime. O objetivo não é apresentar apenas a ideia da solução, mas registrar:

- qual artigo é a base metodológica atual;
- quais trilhas antigas ainda existem no código;
- como o estado DRL é montado;
- como o treino é executado;
- como os checkpoints são classificados;
- como a política em `shadow mode` roda no runtime;
- como a comparação `live vs shadow` é persistida;
- como o `control gate` impede promoção prematura.

Este relatório foi escrito a partir da implementação em:

- `src/`
- `drlexp/`
- `scripts/`
- `config/`
- `docs/`

---

## 2. Visão geral

Hoje o GreenRAN possui três camadas históricas de DRL:

1. **Legado A3C/SBiLSTM**
2. **Transição SAC/AWAC single-agent**
3. **Linha oficial TA-SAM MARL article-aligned**

A linha **oficial** atual é a terceira. As duas primeiras continuam no repositório como benchmark, compatibilidade e fallback, mas não são mais a narrativa principal do projeto.

---

## 3. Artigo-base atual

O artigo-base adotado hoje como linha oficial do GreenRAN é:

- **Lotfi, F., Rajoli, H. & Afghah, F.**
- **"Task-Specific Sharpness-Aware O-RAN Resource Management using Multi-Agent Reinforcement Learning"**
- **IEEE Transactions on Machine Learning in Communications and Networking (TMLCN), 2025**
- **arXiv:2511.15002**

Esse alinhamento aparece explicitamente em:

- `docs/RELATORIO_GREENRAN_CONFLITOS.md`
- `src/greenran_marl_topology.py`
- `src/rapp_sac_resource_model.py`
- `src/rapp_rl_policy.py`
- `drlexp/src/drl/ta_sam_marl.py`
- `drlexp/training/train_sac.py`

A escolha do paper-base significa que a trilha principal do projeto deixou de ser:

- `SBiLSTM + A3C`
- ou `SAC/AWAC single-agent`

E passou a ser:

- **topologia por DUs lógicos**;
- **multi-agent RL (MARL)**;
- **crítico global compartilhado**;
- **seleção de atualização guiada por variância TD**;
- **Sharpness-Aware Minimization (SAM)**;
- **checkpoint em shadow runtime**;
- **gate operacional antes de qualquer controle real**.

---

### 3.1 Cenário do artigo (Lotfi et al. 2025)

O artigo propõe um framework MARL com SAM para **alocação de recursos compartilhados em O-RAN**.

#### Problema formulado

- **Alocação de Resource Blocks (RBs)** em uma arquitetura O-RAN com múltiplos DUs
- **3 slices de rede:** eMBB, MTC (mMTC) e URLLC, cada uma com requisitos de QoS distintos
- **Objetivo:** Maximizar a satisfação global de QoS respeitando restrições de recursos
- **Formulação MDP:** modelado como um Processo de Decisão de Markov com estado `s_t = {Q_l, N_u, a_{t-1}}`

#### Setup experimental (conforme seção VI-A)

| Parâmetro | Valor no artigo |
|-----------|----------------|
| **Número de DUs (agentes)** | `Nm = 6` DUs |
| **Total de UEs** | 200 usuários distribuídos uniformemente |
| **Slices** | 3: eMBB, MTC, URLLC |
| **Largura do RB** | 200 kHz (aprox. 180 kHz 3GPP NR µ=0) |
| **Ator** | MLP 3 camadas: **300 → 400 → 400**, ativação **tanh** |
| **Crítico** | MLP 3 camadas: 300 → 400 → 400, ativação tanh |
| **Learning rate** | **10⁻⁴** |
| **Otimizador** | Adam |
| **Framework** | PyTorch |
| **Mobilidade UEs** | 10 a 20 m/s, 7 direções possíveis |

#### Espaço de estados (artigo, seção IV-A1)

Cada agente (DU) observa no tempo `t`:
- `Q_l` — QoS atual de cada slice `l` (eMBB, MTC, URLLC)
- `N_u^l` — número de UEs em cada slice
- `a_{t-1}` — ação de alocação anterior

O estado completo: `s_t = {Q_l, N_u^l, a_{t-1} | ∀l ∈ L}`

O crítico global observa adicionalmente o estado agregado de todos os DUs.

#### Espaço de ações (artigo, seção IV-A2)

Cada DU produz um vetor contínuo `a_t = {e, b}` onde:
- `e` — alocação de recursos para slices (fatia do espectro)
- `b` — alocação por UE dentro de cada slice
- Saída com **ativação sigmoid** + normalização (soma das fatias = 1.0)

#### Função de recompensa (artigo, seção IV-A)

A recompensa combina três termos:

```
r_t = σ(α_l · Q_l) + P_res + P_minQ
```

Onde:
- `σ(x) = 1/(1 + e^(-x))` — função sigmoid
- `Q_l` — QoS da slice `l`
- `α_l` — parâmetro de sensibilidade adaptativo por slice
- `P_res` — penalidade por exceder recursos disponíveis
- `P_minQ` — penalidade por não atingir QoS mínima

O retorno acumulado: `R(t) = Σ γ^i · r_i,t` com fator de desconto `γ`.

#### Algoritmo (artigo, seção IV)

O artigo combina:
- **Soft Actor-Critic (SAC):** framework off-policy com maximização de entropia, temperatura `β`
- **Sharpness-Aware Minimization (SAM):** perturba os gradientes em um raio `ρ` para encontrar mínimos planos:
  ```
  θ_adv = θ + ρ · ∇L(θ) / ||∇L(θ)||
  θ_new = θ - η · ∇L(θ_adv)
  ```
- **SAM seletivo por variância TD:** só aplica SAM nos atores onde `σ²(δ_TD) >= λ_TD` (variância do TD-error como proxy de incerteza)
- **ρ dinâmico:** começa maior (exploração), reduz gradualmente durante o treino (exploitation)
- **Arquitetura MARL:** múltiplos atores distribuídos (1 por DU) + **1 crítico global** (xApp no near-RT RIC)
- **Replay buffer** `B` para experiências offline

#### Métricas de avaliação

- **QoS satisfaction por slice** (eMBB, MTC, URLLC)
- **Cumulative reward** médio
- **Curvatura do loss landscape** (auto-valor máximo da Hessiana `λ_max(∇²L)`)
- **CDF de throughput** por usuário
- **Quality-Weighted Network Capacity**

#### Resultados numéricos (seção VI)

| Resultado | Valor | Contexto |
|-----------|-------|----------|
| Ganho vs SAC sem SAM | **até 22%** | Eficiência de alocação |
| Ganho vs SAC + L2 | **7%** | Regularização padrão |
| Ganho vs PLASTIC [30] | eMBB: **+8.5%**, MTC: **+5.3%**, URLLC: **+23.6%** | Baseline SOTA |
| TA-SAM full (QoS) | eMBB: **23.67%**, MTC: **11.19%**, URLLC: **42.1%** | Melhoria absoluta |
| Sem SAM seletivo | Perda de **2.35%-3.5%** | Confirma seletor |
| ρ estático vs dinâmico | Degradação **5-10%** | ρ dinâmico é crucial |
| SAC MARL baseline | eMBB: 13.9%, MTC: 5.87%, URLLC: 25.1% | Sem SAM |
| Escalabilidade | Ótimo em **6 DUs** (testado 2 a 10) | Degrada após 6 |

#### Principais diferenças artigo vs nossa implementação

| Aspecto | Artigo (Lotfi et al.) | GreenRAN |
|---------|----------------------|----------|
| **DUs** | 6 | 3 |
| **UEs** | 200 | 12 + 5 veículos |
| **Rede ator** | 300→400→400 (tanh) | 300→400→400 (tanh) ✓ |
| **Learning rate** | 10⁻⁴ | 10⁻⁴ ✓ |
| **ρ SAM** | Dinâmico (decai no treino) | Fixo (0.05) |
| **Seletor SAM** | Variância do TD-error | Variância da ação (proxy) |
| **Recompensa** | `σ(α·Q) + P_res + P_minQ` | Média ponderada completion |
| **SAM no SAC** | Integrado | Só no TA-SAM; SAC é vanilla |
| **Treino** | Online (iteração ambiente) | Offline bootstrap (BC + SAC) |

### 3.2 Adaptação do artigo ao GreenRAN

O artigo foi adaptado ao cenário fixo do GreenRAN sem alterar a arquitetura existente:

| Aspecto | Artigo original (Lotfi et al.) | Adaptação GreenRAN |
|---------|-------------------------------|-------------------|
| Número de DUs | **6 DUs** | 3 DUs lógicos fixos |
| Total UEs | **200** | 12 UEs fixos + até 5 veículos |
| Slices | 3 (eMBB, MTC, URLLC) | eMBB (App1), mMTC (App2), URLLC (App3) |
| Budget | RBs dinâmicos (200 kHz) | Budget fixo (S1-U=15Mbps) |
| Rede do ator | **3 camadas: 300→400→400 (tanh)** | 64→64 (TA-SAM) ou 128→128 (SAC) |
| Learning rate | **10⁻⁴** | 3×10⁻⁴ |
| Algoritmo | **SAC + SAM integrado + ρ dinâmico** | SAC vanilla + TA-SAM shadow (ρ fixo 0.05) |
| Seletor | **Variância do TD-error** | Variância da ação |
| Recompensa | `σ(α·Q) + P_res + P_minQ` | Média ponderada completion |
| Treino | **Online com iteração ambiente** | Offline bootstrap (BC + SAC/AWAC) |
| Tráfego | Sintético | Real de 3 xApps |
| Estado | Observação direta | Derivado de KPIs reais do Data Lake |

### 3.3 Nota: Este artigo NÃO é o article00

**O `article00` é um artigo diferente** — sobre detecção de conflitos em O-RAN usando **GraphSAGE temporal** (referência local: `/home/robert/Downloads/artigo00.pdf`). Ele é uma trilha experimental separada que:
- Gera datasets sintéticos temporais
- Treina GraphSAGE para reconstruir relações Parâmetro↔KPI
- Rotula conflitos (direct, indirect, implicit)
- **Não integra o runtime do GreenRAN**

A confusão entre os dois artigos é comum no repositório porque o nome `article00` aparece em scripts, documentos e diretórios. Mas a **DRL em runtime** segue exclusivamente o **Lotfi et al. 2025 (TA-SAM MARL)**.

---

## 4. Cenário fixo preservado

A implementação atual não muda o cenário GreenRAN. Ela adapta o artigo ao cenário fixo do projeto.

Fonte de verdade:

- `config/greenran_fixed_scenario.json`

### 4.1 Invariantes mantidos

- `keep_greenran_architecture = true`
- `keep_armd_greenran = true`
- `freeze_camera_vehicle_ue_counts = true`

### 4.2 População fixa

- `12` UEs no `ns-3`
- `3` câmeras com IMSI `1-3`
- `9` UEs background com IMSI `4-12`
- até `5` veículos com IMSI `16-20`

### 4.3 Correspondência das slices

- `App1` → `eMBB-like`
- `App2` → `mMTC-like`
- `App3` → `URLLC-like`

Esse mapeamento não é apenas documental. Ele é usado pela topologia lógica do MARL e pelo pipeline de estado article-aligned.

---

## 5. Mapa das trilhas DRL existentes

## 5.1 Legado A3C/SBiLSTM

Arquivos principais:

- `src/rapp_rl_policy.py`
- `src/rapp_drl_predictor.py`
- `drlexp/training/train_a3c.py`
- `drlexp/training/train_sbilstm.py`
- `drlexp/config/drl_config.yaml`
- `drlexp/models/a3c/*`
- `drlexp/models/sbilstm/*`

Função atual:

- baseline histórico;
- fallback;
- compatibilidade com partes antigas do runtime;
- documentação de comparação.

## 5.2 Transição SAC/AWAC single-agent

Arquivos principais:

- `src/rapp_sac_resource_model.py` — modelo heurístico de demanda compartilhada
- `src/rapp_rl_policy.py` — `SACResourceAllocationPolicy`
- `drlexp/src/drl/caora_sac_environment.py` — ambiente Gymnasium SAC/AWAC
- `drlexp/training/train_sac.py` — trainer offline (BC + SAC/AWAC)
- `drlexp/config/sac_config.yaml` — hiperparâmetros SAC
- `scripts/export_sac_workload_trace.py` — exporta traces do Data Lake para CSV
- `scripts/run_fixed_awac_controlled_collection.py` — coleta controlada com AWAC
- `scripts/run_awac_refresh_cycle.py` — ciclo automático de retreino AWAC
- `scripts/prepare_awac_retrain_handoff.py` — prepara handoff para treino
- `docs/SAC_MIGRATION_PLAN.md` — plano de migração
- `docs/SAC_REAL_COLLECTION_PLAYBOOK.md` — playbook de coleta
- `docs/AWAC_CHECKPOINT_STATUS.md` — status dos checkpoints
- `runs/sac_bootstrap/*` — checkpoints treinados

Função atual:

- bootstrap do problema de alocação compartilhada RAN vs AI;
- coleta de conflito RAN/AI em cenário real;
- checkpoints offline single-agent (SAC e AWAC);
- runtime policy selecionável via `GREENRAN_RL_POLICY`;
- ciclo de refresh automático com retreino;
- ponte para a trilha article-aligned (MARL).

## 5.3 Linha oficial TA-SAM MARL

Arquivos principais:

- `src/greenran_marl_topology.py` — topologia dos 3 DUs lógicos
- `src/rapp_sac_resource_model.py` — geração do `article_marl_state`
- `src/rapp_marl_shadow.py` — shadow runtime evaluator
- `src/rapp_marl_control_gate.py` — gate operacional
- `src/rapp_data_lake.py` — tabelas MARL (`marl_*_history`)
- `scripts/export_marl_training_trace.py` — exporta traço de treino JSONL
- `scripts/evaluate_tasam_candidates.py` — classifica checkpoints
- `scripts/evaluate_marl_shadow_runtime.py` — avalia runtime do shadow
- `scripts/evaluate_marl_control_gate.py` — avalia gate de controle
- `scripts/watch_marl_runtime_gate.py` — watcher contínuo do gate
- `drlexp/src/drl/ta_sam_marl.py` — trainer TA-SAM MARL
- `drlexp/training/train_tasam_marl.py` — entry-point de treino
- `drlexp/config/tasam_marl_config.yaml` — hiperparâmetros TA-SAM
- `drlexp/config/drl_config.yaml` — config legada A3C (referência)

Função atual:

- geração de estado article-aligned por DU e global;
- treino multiagente com SAM e seletor por variância;
- classificação automática de checkpoint;
- execução em `shadow mode` sem atuação;
- avaliação runtime (live vs shadow);
- bloqueio de promoção via `control gate` multi-estágio;
- watcher operacional contínuo.

---

## 6. DRL legada: SBiLSTM + A3C

A camada legada foi encapsulada no runtime por `LegacyA3CPolicyAdapter`, em `src/rapp_rl_policy.py`.

Metadados dessa política:

- `policy_id = legacy_a3c_energy`
- `family = legacy_energy_control`
- `algorithm = SBiLSTM+A3C`
- `decision_domain = energy_control`
- `action_semantics = energy_command`
- `legacy_runtime_compatible = true`

Ela existe para impedir que o `rApp` fique preso a uma única implementação.

Interface exposta:

- `load_models()`
- `predict(state_dict)`
- `reset_history()`
- `is_available()`

Status atual:

- ainda serve como baseline e fallback;
- ainda aparece em dashboards e documentação antigas;
- não é mais a linha oficial do projeto.

---

## 7. DRL de transição: SAC/AWAC single-agent

## 7.1 Função dessa camada

A camada SAC/AWAC foi o primeiro passo para tirar o runtime da lógica exclusivamente heurística/legada e colocar o problema em uma formulação explícita de recurso compartilhado.

Ela trabalha com quatro variáveis principais:

- `d_ran`
- `d_ai`
- `r_ran`
- `r_ai`

## 7.2 Modelo de recurso compartilhado

O módulo central é `src/rapp_sac_resource_model.py`.

Funções principais:

- `estimate_ran_demand(...)`
- `estimate_ai_demand(...)`
- `compute_shared_resource_snapshot(...)`

### Parâmetros fixos do modelo heurístico

O bootstrap heurístico é parametrizado por constantes que definem o comportamento da alocação:

| Parâmetro | Valor | Descrição |
|-----------|-------|-----------|
| `ran_min_share` | 0.35 | Fração mínima do budget garantida para RAN |
| `ai_min_share` | 0.15 | Fração mínima do budget garantida para AI |
| `ran_priority_bias` | 1.35 | Fator de prioridade aplicado à demanda RAN |
| `allocation_smoothing` | 0.35 | Suavização temporal entre alocações consecutivas |
| `min_utilization` | 0.55 | Utilização mínima alvo para evitar sub-utilização |
| `r_max` | 1.0 | Budget máximo normalizado |

A alocação resultante é calculada como:

```
r_ran_raw = d_ran * ran_priority_bias
r_ai_raw  = d_ai
total_raw = r_ran_raw + r_ai_raw

r_ran = clamp(r_ran_raw / total_raw, ran_min_share, 1.0 - ai_min_share)
r_ai  = 1.0 - r_ran

# Suavização temporal:
r_ran = allocation_smoothing * r_ran + (1 - allocation_smoothing) * r_ran_anterior
r_ai  = allocation_smoothing * r_ai  + (1 - allocation_smoothing) * r_ai_anterior

# Ajuste ao budget:
r_ran *= usable_budget
r_ai  *= usable_budget
```

## 7.3 Estimativa de demanda RAN

A demanda RAN usa sinais de App1 e de saúde da rede:

- câmeras ativas;
- throughput observado;
- latência observada;
- CVaR;
- P95;
- observabilidade das câmeras;
- número de câmeras críticas;
- warmup.

Componentes internos persistidos:

- `camera_activity`
- `camera_observability`
- `critical_ratio`
- `throughput_pressure`
- `throughput_headroom_pressure`
- `latency_pressure`
- `latency_guard_pressure`
- `severe_throughput_pressure`
- `cvar_pressure`
- `p95_pressure`
- `warmup_pressure`
- `throughput_warning`
- `throughput_violation`
- `latency_warning`
- `latency_violation`

## 7.4 Estimativa de demanda AI

A demanda AI combina App2 e App3.

Lado App2:

- `connected_ratio`
- `delivery_success`
- `avg_latency_ms`
- `stale`

Lado App3:

- `high_risk_vehicles`
- `medium_risk_vehicles`
- `degraded_autonomy_vehicles`
- `max_latency_ms`
- `max_packet_loss_percent`

Componentes persistidos:

- `app2_pressure`
- `vehicle_pressure`
- `sensor_activity`
- `vehicle_activity`

## 7.5 Snapshot compartilhado

`compute_shared_resource_snapshot(...)` gera um snapshot com:

- `controller_id`
- `target_policy_id`
- `decision_domain`
- `action_semantics`
- `resource_budget`
- `usable_budget`
- `d_ran`
- `d_ai`
- `r_ran`
- `r_ai`
- `delta_r_ran`
- `delta_r_ai`
- `ran_completion_ratio`
- `ai_completion_ratio`
- `utilization_ratio`
- `ran_components`
- `ai_components`

Hoje, no caminho estável, o controlador vivo costuma aparecer como:

- `controller_id = caora_bootstrap_heuristic`

Esse ponto é importante:

- o runtime ainda aplica a política viva heurística/bootstrap de recurso;
- o `TA-SAM` ainda não substituiu essa política;
- o `TA-SAM` opera em `shadow mode`.

## 7.6 SACResourceAllocationPolicy no runtime

`src/rapp_rl_policy.py` contém `SACResourceAllocationPolicy`, o adapter que carrega checkpoints SAC/AWAC e os executa em runtime.

### Metadados da política

- `family = caora_ai_ran_coexistence`
- `algorithm = SAC` ou `AWAC`
- `decision_domain = resource_allocation`
- `action_semantics = resource_share_delta`
- `legacy_runtime_compatible = false`

### Estado usado pelo ator runtime (5 dimensões)

1. `d_ran` — demanda RAN estimada
2. `d_ai` — demanda AI estimada
3. `r_ran anterior` — alocação RAN do ciclo anterior
4. `r_ai anterior` — alocação AI do ciclo anterior
5. `usable_budget` — budget disponível no ciclo

### Blend runtime (ator + heurística)

O ator SAC/AWAC não substitui completamente a heurística. A saída final é um **blend** entre a ação do ator e a alocação heurística:

```python
actor_weight = GREENRAN_SAC_RUNTIME_BLEND  # default 0.85
r_ran_final = actor_weight * r_ran_actor + (1 - actor_weight) * r_ran_heuristic
r_ai_final  = actor_weight * r_ai_actor  + (1 - actor_weight) * r_ai_heuristic
```

Isso significa que mesmo com SAC/AWAC ativo, ~15% da decisão ainda vem da heurística bootstrap, garantindo estabilidade.

### Mecanismo de fallback

Se a completude RAN observada cair abaixo de um limiar seguro, o runtime reverte automaticamente para a heurística:

```python
if ran_completion + GREENRAN_SAC_RAN_COMPLETION_TOLERANCE < heuristic_ran_completion:
    # tolerance default = 0.03 (3%)
    usar alocação heurística pura
```

Isso impede que o ator cause degradação sustentada no tráfego prioritário (câmeras).

### Confidence scoring

O runtime calcula um score de confiança para a decisão do ator:

```python
confidence = 0.55
    + 0.20 * heuristic_ran_completion
    + 0.15 * (ai_completion_atual - ai_completion_anterior)
    + 0.10 * (1.0 if r_ran_match else 0.0)
confidence = min(confidence, 0.99)
```

### Caminhos dos checkpoints

Os checkpoints são carregados de:

- **Default AWAC:** `runs/sac_bootstrap/offline_awac_<data>_refresh/sac_actor_offline.pt`
- **Default SAC:** `runs/sac_bootstrap/offline_sac_<data>_conservative/sac_actor_offline.pt`
- **Override:** variável `GREENRAN_SAC_ACTOR_CHECKPOINT`

### Seleção de política em runtime

A política ativa é selecionada pela variável de ambiente `GREENRAN_RL_POLICY`:

| Valor | Política |
|-------|----------|
| `legacy_a3c`, `a3c`, `eedrl` | `LegacyA3CPolicyAdapter` (A3C legado) |
| `sac`, `caora_sac`, `resource_sac` | `SACResourceAllocationPolicy(algorithm="SAC")` |
| `awac`, `caora_awac`, `resource_awac` | `SACResourceAllocationPolicy(algorithm="AWAC")` |

O runtime só usa SAC/AWAC se o checkpoint existir e carregar. Caso contrário, cai para a heurística pura.

Ela continua importante como trilha de transição e referência, mas não é a linha article-faithful principal.

---

## 7.7 SAC/AWAC: Treinamento offline detalhado

### Pipeline completo

```
Export trace (export_sac_workload_trace.py)
  → CSV com colunas: timestamp, d_ran, d_ai, r_ran, r_ai, usable_budget,
                      delta_r_ran, delta_r_ai, ran_completion, ai_completion,
                      utilization
  → BC warm-start (80 epochs)
    → Offline SAC refinement (140 epochs)
      → sac_actor_offline.pt + sac_critic{1,2}_offline.pt + summary.json
```

### 7.7.1 Export do trace

`scripts/export_sac_workload_trace.py` lê a tabela `resource_allocation_history` do Data Lake SQLite e gera um CSV com as colunas:

- `timestamp`, `datetime`
- `controller_id`, `target_policy_id`
- `decision_domain`, `action_semantics`
- `d_ran`, `d_ai`, `r_ran`, `r_ai`, `usable_budget`
- `delta_r_ran`, `delta_r_ai`
- `ran_completion_ratio`, `ai_completion_ratio`, `utilization_ratio`

Cada linha representa um ciclo de decisão completo, formando uma transição `(s, a, r, s')` para treino offline.

### 7.7.2 Ambiente CAORA-SAC

`drlexp/src/drl/caora_sac_environment.py` define o ambiente Gymnasium:

- **Estado (5 dims):** `[d_ran(t), d_ai(t), r_ran(t-1), r_ai(t-1), usable_budget(t)]`
- **Ação (2 contínuas):** `[delta_r_ran, delta_r_ai]` em `[-1.0, 1.0]`, escalado por `delta_step=0.1`
- **Reward (artigo Lotfi et al. 2025):**
  ```python
  sig_ran = sigmoid(alpha_ran * ran_completion)
  sig_ai  = sigmoid(alpha_ai  * ai_completion)
  qos_term = (sig_ran + sig_ai) / 2
  
  usado = r_ran + r_ai
  excesso = max(0, usado - usable_budget)
  res_penalty = -beta * excesso
  
  abaixo = max(0, qos_min_ran - ran_completion)
         + max(0, qos_min_ai  - ai_completion)
  min_qos_penalty = -gamma * abaixo
  
  reward = qos_term + res_penalty + min_qos_penalty
  ```
  Com `alpha_ran=4.0, alpha_ai=2.5, beta=2.0, gamma=5.0, qos_min_ran=0.7, qos_min_ai=0.5`
  O parâmetro `reward_fn="article"` (default) seleciona esta função; `reward_fn="legacy"` mantém a formulação anterior.
- **Restrição:** `r_ran + r_ai <= usable_budget` (aplicada via scaling proporcional)

O ambiente carrega pontos de carga reais de um CSV via `WorkloadPoint` dataclass.

### 7.7.3 Arquitetura das redes

**GaussianActor:**
```python
backbone = Linear(5 → 300, Tanh) → Linear(300 → 400, Tanh) → Linear(400 → 400, Tanh)
mean_head = Linear(400 → 2)   # ação determinística
log_std_head = Linear(400 → 2) # log-desvio padrão
# Saída: Tanh squashed para [-1, 1]
```

**QNetwork (twin critics):**
```python
backbone = Linear(7 → 300, Tanh) → Linear(300 → 400, Tanh) → Linear(400 → 400, Tanh)
output = Linear(400 → 1)  # Q(s,a)
# Duas instâncias: Q1 e Q2 (target networks com soft-update)
```

### 7.7.4 Hiperparâmetros de treino

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `bc_epochs` | 80 | Behavior Cloning warm-start |
| `sac_epochs` | 140 | Refinação SAC/AWAC offline |
| `batch_size` | 32 | Tamanho do mini-batch |
| `actor_lr` | 1e-4 | Learning rate do ator (artigo) |
| `critic_lr` | 1e-4 | Learning rate dos críticos (artigo) |
| `alpha_lr` | 1e-4 | Learning rate da temperatura de entropia (artigo) |
| `gamma` | 0.99 | Fator de desconto |
| `tau` | 0.01 | Soft-update dos target networks |
| `bc_weight` | 0.15 | Peso do BC loss (regularização) |
| `bc_anchor_weight` | 1.5 | Ancoragem à política BC |
| `critic_warmup_epochs` | 12 | Só treina críticos no início |
| `actor_update_interval` | 4 | Ator atualiza a cada N passos do crítico |

### 7.7.5 BC Warm-start

O pré-treino por Behavior Cloning (80 epochs) usa:
- **Loss:** MSE entre `actor.deterministic(states)` e ações do dataset
- **Seleção:** melhor modelo por validation MAE
- **Retorno:** `train_curve`, `val_curve`, `best_val_loss`, `action_mae`

### 7.7.6 Refinação SAC offline (140 epochs)

Após o BC, a refinação SAC usa:
- **Twin critics** (Q1, Q2) para reduzir overestimation bias
- **Target networks** com soft-update (`tau=0.01`)
- **Entropia ajustável:** `log_alpha` com target `= -action_size * target_entropy_scale`
  - `alpha_init=0.03`, `target_entropy_scale=0.25`
  - Clamp: `alpha_min=1e-4`, `alpha_max=0.5`
- **Critic warmup** (12 epochs): usa ator BC como referência para Q target
- **Pós-warmup:** blend de 75% ator referência + 25% ator corrente nos targets Q

**Loss do ator (modo SAC):**
```python
actor_loss = (alpha * log_prob - min_Q).mean()
           + bc_weight * BC_loss
           + bc_anchor_weight * anchor_loss
```

### 7.7.7 Refinação AWAC (alternativa)

No modo AWAC, a refinação usa **advantage-weighted regression**:

```python
advantages = min_Q - V(s)  # ou Q_target - value
weights = exp(advantages / awac_lambda)  # awac_lambda=0.25
weights = clamp(weights, max=awac_max_weight)  # max=12.0

actor_loss = (weights * BC_loss).mean()
           + BC_loss
           + anchor_loss
```

Isso prioriza transições onde o ator teve vantagem positiva, sem precisar de amostragem online.

### 7.7.8 Avaliação

Após o treino, o ator determinístico é avaliado com rollout no `CAORASACEnv`:
- `rollout_reward`
- `avg_utilization`
- `avg_ran_completion`
- `avg_ai_completion`

### 7.7.9 Artefatos de saída

O treino exporta para `runs/sac_bootstrap/`:
- `sac_actor_offline.pt` — ator treinado (inferência)
- `sac_critic1_offline.pt` — primeiro crítico
- `sac_critic2_offline.pt` — segundo crítico
- `sac_actor_bc.pt` — ator só BC (pré-refinação)
- `sac_offline_summary.json` — métricas de treino

---

## 8. Topologia MARL article-aligned

A topologia MARL atual está em `src/greenran_marl_topology.py`.

## 8.1 DUs lógicos

A topologia padrão usa 3 DUs lógicos:

1. `du_camera_edge`
2. `du_sensor_mixed`
3. `du_vehicle_edge`

## 8.2 Papéis e mistura por slice

### `du_camera_edge`

- `role = camera_edge`
- `primary_slice = eMBB`
- `camera_imsis = [1,2,3]`
- `background_imsis = [4,5,6]`
- `slice_mix = {eMBB: 0.85, mMTC: 0.10, URLLC: 0.05}`

### `du_sensor_mixed`

- `role = sensor_mixed`
- `primary_slice = mMTC`
- `background_imsis = [7,8,9]`
- `slice_mix = {eMBB: 0.35, mMTC: 0.55, URLLC: 0.10}`

### `du_vehicle_edge`

- `role = vehicle_edge`
- `primary_slice = URLLC`
- `background_imsis = [10,11,12]`
- `vehicle_imsi_range = [16,20]`
- `slice_mix = {eMBB: 0.20, mMTC: 0.15, URLLC: 0.65}`

## 8.3 Slice profiles

- `eMBB` → App1 → `qos_target = throughput`
- `mMTC` → App2 → `qos_target = delivery`
- `URLLC` → App3 → `qos_target = latency`

---

## 9. Estado article-aligned

## 9.1 Estado por slice

`build_slice_state(...)` gera, por slice:

- `slice_id`
- `ue_count`
- `demand`
- `allocation`
- `qos_pressure`
- `completion_ratio`
- `min_qos_met`
- `budget_share`

### eMBB

Derivado principalmente de:

- throughput das câmeras;
- latência das câmeras;
- pressão de rede.

### mMTC

Derivado principalmente de:

- sensores conectados;
- sucesso de entrega;
- latência de App2.

### URLLC

Derivado principalmente de:

- latência máxima veicular;
- perda veicular;
- risco veicular.

## 9.2 Estado por DU

Cada DU contém:

- `du_id`
- `role`
- `primary_slice`
- `ue_count`
- `slice_mix`
- `demand_share`
- `allocation_share`
- `state_vector`

O `state_vector` por DU possui 10 dimensões:

1. `eMBB qos_pressure`
2. `mMTC qos_pressure`
3. `URLLC qos_pressure`
4. `ue_count normalized`
5. `eMBB weight`
6. `mMTC weight`
7. `URLLC weight`
8. `demand_share normalized`
9. `allocation_share normalized`
10. `allocation_share / demand_share`

## 9.3 Estado global

O crítico global recebe um estado com 10 dimensões:

1. demanda `eMBB`
2. demanda `mMTC`
3. demanda `URLLC`
4. alocação `eMBB`
5. alocação `mMTC`
6. alocação `URLLC`
7. `ran_completion_ratio`
8. `ai_completion_ratio`
9. `utilization_ratio`
10. `total_demand / usable_budget`

## 9.4 Encapsulamento no snapshot vivo

O snapshot compartilhado de recurso inclui:

- `article_marl_state`

Isso é importante porque o mesmo snapshot alimenta:

- a persistência no Data Lake;
- o export de treino;
- o `shadow runtime`.

---

## 10. Persistência no Data Lake

A persistência da trilha DRL está concentrada em `src/rapp_data_lake.py`.

## 10.1 Tabela `resource_allocation_history`

Persistida por `record_resource_allocation_snapshot(...)`.

Campos principais:

- `timestamp`
- `datetime`
- `controller_id`
- `target_policy_id`
- `decision_domain`
- `action_semantics`
- `resource_budget`
- `usable_budget`
- `d_ran`
- `d_ai`
- `r_ran`
- `r_ai`
- `delta_r_ran`
- `delta_r_ai`
- `ran_completion_ratio`
- `ai_completion_ratio`
- `utilization_ratio`
- `snapshot_json`

## 10.2 Tabela `marl_global_state_history`

Persistida por `record_article_marl_state(...)`.

Campos principais:

- `timestamp`
- `datetime`
- `topology_id`
- `logical_du_count`
- `total_demand`
- `usable_budget`
- `state_vector_json`
- `snapshot_json`

## 10.3 Tabela `marl_slice_state_history`

Campos principais:

- `timestamp`
- `datetime`
- `slice_id`
- `ue_count`
- `demand`
- `allocation`
- `qos_pressure`
- `completion_ratio`
- `min_qos_met`
- `budget_share`
- `snapshot_json`

## 10.4 Tabela `marl_du_state_history`

Campos principais:

- `timestamp`
- `datetime`
- `du_id`
- `role`
- `primary_slice`
- `ue_count`
- `demand_share`
- `allocation_share`
- `slice_mix_json`
- `state_vector_json`
- `snapshot_json`

## 10.5 Tabela `marl_shadow_comparison_history`

Persistida por `record_marl_shadow_comparison(...)`.

Campos principais:

- `timestamp`
- `datetime`
- `policy_id`
- `source`
- `checkpoint_readiness`
- `available`
- `recommend_shadow`
- `live_score`
- `shadow_score`
- `score_delta`
- `live_ran_completion_est`
- `shadow_ran_completion_est`
- `live_ai_completion_est`
- `shadow_ai_completion_est`
- `live_total_shortfall`
- `shadow_total_shortfall`
- `live_budget_gap`
- `shadow_budget_gap`
- `delta_r_ran`
- `delta_r_ai`
- `snapshot_json`

Essa tabela é a base da avaliação operacional do `shadow`.

---

## 11. Exportação do traço de treino

O export está em `scripts/export_marl_training_trace.py`.

## 11.1 Fonte

O export lê:

- `marl_global_state_history`
- `marl_slice_state_history`
- `marl_du_state_history`

## 11.2 Registro exportado

Cada linha do `JSONL` contém:

- `timestamp`
- `datetime`
- `topology_id`
- `global_state`
- `slice_state`
- `du_states`
- `reward_hint`

## 11.3 Reward bootstrap

O `reward_hint` atual é calculado como a média dos `completion_ratio` das slices.

Isso é um reward operacional simples, suficiente para o bootstrap atual, mas ainda não é uma formulação teórica completa do artigo.

---

## 12. Trainer TA-SAM MARL

Implementação principal:

- `drlexp/src/drl/ta_sam_marl.py`

Entry-point:

- `drlexp/training/train_tasam_marl.py`

Config:

- `drlexp/config/tasam_marl_config.yaml`

## 12.1 Ordem das slices

```
SLICE_ORDER = [eMBB, mMTC, URLLC]
```

Essa ordem é fixa em todo o pipeline MARL (topologia, estado, ação, target).

## 12.2 Estrutura de dados

Cada exemplo de treino é um `MARLRecord` com:

- `global_state` — vetor global (10 dims)
- `du_states` — lista de vetores de estado por DU (10 dims cada)
- `reward` — escalar observado
- `target_actions` — lista de vetores alvo por DU (3 dims cada, soma=1)

## 12.3 Target actions

Os alvos de ação são derivados por `_derive_target_actions(...)` a partir de:

- `slice_mix` do DU (ex: `{eMBB: 0.85, mMTC: 0.10, URLLC: 0.05}`)
- `budget_share` das slices observada

Fallback:

- se o vetor bruto zera, usa `primary_slice` como dominância principal (ex: eMBB=0.85 para camera_edge).

Os targets representam a **política heurística observada** — o que o controlador vivo fez naquele ciclo.

## 12.4 Arquitetura dos atores

```python
ActorNetwork(input_dim=du_state_dim, hidden_dim=64, action_dim=3)
```

Cada ator é um MLP de 2 camadas:

```
du_state (10) → Linear(10→64) → ReLU → Linear(64→64) → ReLU → Linear(64→3) → Sigmoid
                                                                                  │
                                                                         normalização L1 (soma=1)
```

A saída é um vetor contínuo de mistura entre slices:
- `[eMBB_share, mMTC_share, URLLC_share]`
- Garantia: `sum(ação) == 1.0`

Existe 1 ator por DU (3 atores no total), cada um com pesos independentes.

## 12.5 Crítico global

```python
GlobalCritic(input_dim=global_state_dim + du_count * action_dim, hidden_dim=96)
```

Arquitetura:

```
[global_state(10) + ações_de_todos_DUs(9)] → Linear(19→96) → ReLU → Linear(96→96) → ReLU → Linear(96→1)
```

Saída: valor escalar V(s, a₁, a₂, a₃)

O crítico é **compartilhado** entre todos os DUs — uma única rede que avalia o estado conjunto.

## 12.6 SAMOptimizer

O `SAMOptimizer` implementa **Sharpness-Aware Minimization**:

```python
class SAMOptimizer:
    rho = 0.05   # raio de perturbação
    base_optimizer = Adam(lr=3e-4)

    step():
        1. forward → loss.backward()
        2. ε = rho * grad / ||grad||  (perturbação)
        3. θ += ε  (ascende no gradiente)
        4. forward → loss.backward()  (nova loss no ponto perturbado)
        5. θ -= ε  (restaura)
        6. base_optimizer.step()  (aplica gradiente da loss perturbada)
```

A intuição: ao invés de minimizar a loss no ponto exato, o SAM minimiza a loss na **vizinhança** do ponto, encontrando mínimos mais planos que generalizam melhor.

## 12.7 Seletor por variância

A seleção seletiva de atualização dos atores é o mecanismo central que implementa a ideia de "task-specific" do artigo.

### Algoritmo (`calibrate_td_variance_threshold`)

```
Entrada:
  - action_variances: variância da ação de cada DU no batch
  - threshold: limiar base (default 0.01)
  - min_selected_fraction: fração mínima de atores atualizados (0.10)
  - warmup: se True, threshold = 0.0

1. Ordenar variâncias descendente
2. threshold_effective = max(threshold, variância no ponto min_selected_fraction)
3. Para cada DU: update_selected = action_variance >= threshold_effective
```

Isso garante que:
- Em cada epoch, pelo menos 10% dos atores são atualizados
- A atualização foca nos DUs com maior incerteza (alta variância = oportunidade de aprendizado)
- DUs estáveis (baixa variância) não são perturbados desnecessariamente

### Warmup

Nos primeiros `warmup_epochs=2`:

- `threshold_effective = 0.0`
- **todos** os atores são atualizados

Objetivo: evitar que o seletor bloqueie o aprendizado cedo demais, antes dos atores começarem a produzir ações diferenciadas.

## 12.8 Perdas do treino

### Crítico

```python
critic_loss = MSE(GlobalCritic(global_state, all_actions), reward)
```

Simples erro quadrático entre o valor estimado e a recompensa observada.

### Ator

```python
actor_loss = bc_weight * BC_loss + value_weight * (-critic_value)
```

Onde:
- `BC_loss = MSE(actor_action, target_action)` — behavior cloning: aprenda a imitar a política viva
- `critic_value = GlobalCritic(global_state, all_actions_with_current_action)` — aprenda a maximizar o valor estimado
- `bc_weight = 1.0` (peso do BC)
- `value_weight = 0.10` (peso do termo guiado por valor)

Na prática, o treino atual é um híbrido de:
- **behavior cloning** da política viva (estabilidade, segurança)
- **ajuste guiado pelo crítico** (exploração, melhoria sobre a heurística)

## 12.9 Fluxo de treino por epoch

```
Para cada epoch:
  1. Calibrar threshold de variância TD
  2. Para cada record no batch:
     a. Actor forward: cada DU → ação
     b. Concatenar ações → GlobalCritic forward
     c. Critic loss: MSE(value, reward)
     d. Se variância da ação >= threshold:
        - BC loss: MSE(actor_action, target_action)
        - Actor loss = bc_weight * BC_loss + value_weight * (-critic_value)
        - SAM step (perturbação + gradiente)
  3. Logging: actor_loss, critic_loss, bc_loss, action_var_mean,
              selected_fraction, effective_threshold
```

## 12.10 Hiperparâmetros consolidados

Do `tasam_marl_config.yaml` e defaults do entry-point:

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `epochs` | 25 | Total de epochs de treino |
| `lr` | 1e-4 | Learning rate (Adam, alinhado artigo) |
| `sam_rho` | 0.05 | Raio de perturbação SAM (artigo: dinâmico) |
| `td_var_threshold` | 0.01 | Limiar base de variância TD |
| `min_selected_fraction` | 0.10 | Fração mínima de atores atualizados |
| `warmup_epochs` | 2 | Épocas iniciais com threshold=0 |
| `bc_weight` | 1.0 | Peso do Behavior Cloning loss |
| `value_weight` | 0.10 | Peso do termo guiado por valor |
| `hidden_dim` | 64 | Dimensão oculta dos atores |
| `critic_hidden_dim` | 96 | Dimensão oculta do crítico global |

## 12.11 Métricas geradas por epoch

- `actor_loss` — perda combinada do ator
- `critic_loss` — erro quadrático do crítico
- `bc_loss` — erro de behavior cloning
- `action_var_mean` — variância média das ações no batch
- `selected_agents` — número de agentes selecionados para update
- `selected_fraction` — fração de agentes selecionados
- `effective_td_var_threshold` — limiar efetivo após calibração
- `td_var_mean` — média da variância TD
- `td_var_max` — máximo da variância TD

## 12.12 Parâmetros do entry-point

`train_tasam_marl.py` aceita via argumentos de linha de comando:

- `--trace-jsonl` — caminho do traço de treino
- `--output-dir` — diretório de saída
- `--epochs` — número de epochs
- `--lr` — learning rate
- `--sam-rho` — raio SAM
- `--td-var-threshold` — limiar de variância
- `--min-selected-fraction` — fração mínima selecionada
- `--warmup-epochs` — epochs de warmup
- `--bc-weight` — peso do BC loss
- `--value-weight` — peso do termo de valor

## 12.13 Artefatos do treino

O treino exporta para o diretório de saída:

- `tasam_marl_actors.pt` — state_dict dos atores (um por DU)
- `tasam_marl_critic.pt` — state_dict do crítico global
- `tasam_marl_checkpoint_meta.json` — metadados (du_count, state_dims, hiperparâmetros)
- `tasam_marl_summary.json` — sumário com métricas finais

---

## 13. Avaliação de checkpoints TA-SAM

Script principal:

- `scripts/evaluate_tasam_candidates.py`

## 13.1 Sinais avaliados

Para cada run, o avaliador usa:

- `critic_loss`
- `selected_fraction`
- `bc_loss`
- `action_var_mean`
- epochs com atualização
- epochs pós-warmup com atualização
- uso efetivo de threshold dinâmico

## 13.2 Classes produzidas

- `not_ready`
- `bootstrap_partial`
- `shadow_ready`
- `control_candidate`

## 13.3 Promoções derivadas

- `promote_shadow = readiness in {shadow_ready, control_candidate}`
- `promote_control_candidate = readiness == control_candidate`

## 13.4 Manifesto gerado

- `runs/sac_bootstrap/tasam_candidate_evaluation_latest.json`

Esse manifesto informa ao runtime qual checkpoint é o melhor candidato disponível.

---

## 14. Integração no rApp

A integração do DRL vivo com o `rApp` está em `src/rapp_orchestrator.py`.

Fluxo relevante:

1. calcula `resource_allocation` com `compute_shared_resource_snapshot(...)`;
2. se existir política não-legada disponível (`SAC/AWAC`), ela pode substituir o snapshot base;
3. o `TA-SAM shadow` avalia o `article_marl_state` do snapshot;
4. o shadow é anexado ao snapshot vivo;
5. o snapshot é persistido no Data Lake.

Trecho lógico importante:

- o `TA-SAM` não assume controle;
- o resultado vai para `resource_allocation['marl_shadow']`;
- o `decision['rl_policy_runtime']['marl_shadow']` também recebe esse payload.

Portanto, a política viva e a política `shadow` coexistem no mesmo ciclo de decisão.

---

## 15. Shadow runtime

Implementação principal:

- `src/rapp_marl_shadow.py`

## 15.1 Filosofia

O módulo é **checkpoint-backed shadow-only**.

Ou seja:

- carrega o melhor checkpoint aprovado para sombra;
- roda em paralelo ao vivo;
- gera recomendação alternativa;
- estima impacto;
- nunca substitui a alocação real sozinho.

## 15.2 Carregamento do checkpoint

O runtime consulta:

- `runs/sac_bootstrap/tasam_candidate_evaluation_latest.json`

Se `best_run.promote_shadow = true`, ele tenta carregar:

- `tasam_marl_checkpoint_meta.json`
- `tasam_marl_actors.pt`

## 15.3 Fallback heurístico

Se o checkpoint não carregar, o módulo usa o **ator heurístico** `_heuristic_du_action()` que calcula a ação com base nas pressões observadas:

```python
embb_score  = embb_mix  * (0.5 + embb_pressure  + embb_demand + max(0, 1 - embb_completion))
mmtc_score  = mmtc_mix  * (0.5 + mmtc_pressure  + 0.7 * mmtc_demand)
urllc_score = urllc_mix * (0.5 + urllc_pressure + 1.2 * max(0, 1 - urllc_completion))

total = embb_score + mmtc_score + urllc_score
action = [embb_score/total, mmtc_score/total, urllc_score/total]
```

Características:
- O termo `max(0, 1-completion)` só ativa quando a slice não está sendo completamente atendida (aprendizado por déficit)
- mMTC tem fator de demanda reduzido (0.7) — prioridade menor
- URLLC tem fator de déficit ampliado (1.2) — prioridade maior em emergência
- Isso produz uma política heurística **consciente de déficit**, não apenas proporcional à demanda

## 15.4 Prioridade contextual

`_scenario_priority(...)` infere a prioridade do cenário atual:

### Saídas possíveis

| Prioridade | Quando | Peso RAN no score |
|------------|--------|-------------------|
| `ran_camera` | `d_ran > d_ai * 1.5` e fase contém `camera` | RAN = 0.50, AI = 0.10 |
| `ran_balanced` | `d_ran > d_ai * 1.5` mas sem fase de câmera | RAN = 0.40, AI = 0.20 |
| `mixed` | Demanda equilibrada | RAN = 0.35, AI = 0.25 |
| `ai_guarded` | Sem dominância clara de RAN | RAN = 0.30, AI = 0.30 |

### Dependências

- **Fase atual** do `scenario_control` (se disponível)
- **Relação** entre `d_ran` e `d_ai` (dominância de demanda)
- A prioridade muda dinamicamente conforme o cenário evolui

## 15.5 Partilha desejada de RAN

`_desired_ran_share(...)` calcula a partilha desejada para `r_ran` combinando múltiplos sinais:

```python
# Componentes da share desejada:
live_share    = r_ran / usable_budget       # o que o live está fazendo
demand_share  = d_ran / (d_ran + d_ai)     # o que a demanda pede
action_share  = mean_actor_action[embb]    # o que o checkpoint recomenda
embb_pressure = pressão observada em eMBB  # urgência real
ai_pressure   = pressão observada em AI    # urgência do outro lado

# Se RAN dominante:
desired = action_share (se checkpoint disponível)
          senão demand_share (se demanda clara)
          senão live_share (conservador)

# Ajuste fino:
desired = clamp(desired + correção_de_pressão, ran_min_share, 1 - ai_min_share)
```

A partilha final alimenta:
```python
shadow_r_ran = usable_budget * desired_share
shadow_r_ai  = usable_budget - shadow_r_ran
```

## 15.6 Score proxy

`_score_proxy(...)` calcula um score comparativo entre live e shadow:

```python
score = w_ran  * ran_completion
      + w_ai   * ai_completion
      - w_def  * total_shortfall
      - w_surp * total_surplus
      - w_gap  * budget_gap
```

### Pesos contextuais

| Contexto | w_ran | w_ai | w_def | w_surp | w_gap |
|----------|-------|------|-------|--------|-------|
| `ran_camera` | 0.50 | 0.10 | 0.20 | 0.10 | 0.10 |
| `ran_balanced` | 0.40 | 0.20 | 0.20 | 0.10 | 0.10 |
| `mixed` | 0.35 | 0.25 | 0.20 | 0.10 | 0.10 |
| `ai_guarded` | 0.30 | 0.30 | 0.20 | 0.10 | 0.10 |

Isso é importante: a avaliação do shadow é **contextual**, não uma métrica fixa e única. Em cenário de câmera (`ran_camera`), o shadow precisa priorizar RAN pesadamente (0.50). Em cenário misto, o peso de AI sobe para 0.25.

---

## 16. Comparação live vs shadow

A função `build_shadow_comparison(...)` produz:

- `live_score`
- `shadow_score`
- `score_delta`
- `live_ran_completion_est`
- `shadow_ran_completion_est`
- `live_ai_completion_est`
- `shadow_ai_completion_est`
- `live_total_shortfall`
- `shadow_total_shortfall`
- `live_budget_gap`
- `shadow_budget_gap`
- `priority`
- `recommend_shadow`

Critério local de recomendação:

- fonte `checkpoint` ou `mixed`
- `checkpoint_readiness` em `shadow_ready` ou `control_candidate`
- `score_delta > 0.01`

---

## 17. Avaliação operacional do shadow

Script principal:

- `scripts/evaluate_marl_shadow_runtime.py`

## 17.1 Janela analisada

A avaliação usa uma janela dos últimos registros da tabela `marl_shadow_comparison_history`.

Parâmetros padrão:

- `window = 300`
- `min_samples = 120`
- `min_checkpoint_coverage = 0.90`

## 17.2 Métricas consolidadas

- `sample_count`
- `checkpoint_coverage`
- `positive_score_rate`
- `recommend_rate`
- `avg_score_delta`
- `avg_ran_completion_delta`
- `avg_ai_completion_delta`
- `latest_policy_id`
- `latest_source`
- `latest_readiness`
- `latest_score_delta`
- `latest_recommend_shadow`

## 17.3 Classes possíveis

- `no_data`
- `insufficient_data`
- `insufficient_checkpoint_coverage`
- `promising`
- `shadow_outperforming`
- `control_trial_candidate`
- `not_beating_live`

### Critério forte para promoção operacional

Para `control_trial_candidate`, o avaliador exige simultaneamente:

- `positive_score_rate >= 0.60`
- `avg_score_delta > 0.01`
- `avg_ran_completion_delta >= 0.0`
- `avg_ai_completion_delta >= -0.02`
- `recommend_rate >= 0.50`

Ou seja, o runtime pede consistência forte, não apenas um pequeno ganho médio.

---

## 18. Control gate

Módulo principal:

- `src/rapp_marl_control_gate.py`

Script operacional:

- `scripts/evaluate_marl_control_gate.py`

## 18.1 Entradas do gate

- manifesto de avaliação de treino TA-SAM;
- manifesto de avaliação runtime do shadow;
- arquivo opcional de aprovação manual.

## 18.2 Estados do gate

- `blocked`
- `shadow_only`
- `trial_candidate`
- `trial_approved`

## 18.3 Condições de bloqueio

O gate não libera tentativa de controle real quando:

- o melhor treino não está aprovado para `shadow`;
- o runtime não usa o checkpoint esperado;
- o treino ainda não é `control_candidate`;
- o runtime ainda não está em `shadow_outperforming` ou `control_trial_candidate`;
- a aprovação manual está ausente, inválida ou expirada.

### 18.3.1 Aprovação manual

Para promover de `trial_candidate` → `trial_approved`, é necessário um arquivo JSON de aprovação manual:

```json
{
  "approved": true,
  "approved_policy_id": "tasam_marl_20260524_v1",
  "approved_run_dir": "runs/sac_bootstrap/tasam_training_20260524/run_001",
  "expires_at": "2026-06-01T00:00:00Z"
}
```

O gate verifica:
1. `approved == true`
2. `approved_policy_id` corresponde ao checkpoint em uso
3. `expires_at` não expirou (timestamp atual < expires_at)

Se qualquer condição falhar, o gate recua para `shadow_only`.

## 18.4 Manifesto final

O script escreve:

- `runs/sac_bootstrap/marl_control_gate_latest.json`

---

## 19. Automação operacional

Scripts principais:

- `scripts/watch_marl_runtime_gate.py`
- `scripts/run_marl_runtime_gate_watcher.sh`
- `scripts/watch_stable_collection.py`

## 19.1 Watcher do gate

Esse watcher atualiza periodicamente:

- `marl_shadow_runtime_eval_latest.json`
- `marl_control_gate_latest.json`
- `marl_runtime_gate_watch_status.json`

## 19.2 Monitor da coleta estável

`watch_stable_collection.py` mostra ao vivo:

- progresso de linhas;
- `sim_time`;
- fase atual do cenário;
- snapshot de alocação;
- decisão viva;
- SLA da App1;
- estado resumido do shadow.

## 19.3 Caminho operacional estável

O runtime estável foi consolidado para esta máquina com launcher dedicado:

- `scripts/run_greenran_scenario_stable.sh`

Esse caminho executa o cenário em modo estável `no-RIC`, com:

- stack viva;
- watcher do gate ligado;
- shadow ativo;
- Data Lake persistindo todo o pipeline DRL.

---

## 20. AWAC Refresh Cycle

Script principal:

- `scripts/run_awac_refresh_cycle.py`

### 20.1 Propósito

Automatizar o ciclo de retreino do checkpoint AWAC sem intervenção manual, mas **sem nunca auto-promover** o novo checkpoint ao runtime.

### 20.2 Pipeline

```
1. Exportar trace mais recente do Data Lake
   → export_sac_workload_trace.py → workload_trace_refresh.csv

2. Verificar crescimento vs baseline anterior
   └── Se novas_linhas < min_new_rows (default 5000) → SKIP

3. Executar train_sac.py (modo AWAC)
   ├── BC warm-start (80 epochs)
   └── Offline AWAC (140 epochs)
       → runs/sac_bootstrap/offline_awac_<data>_refresh/

4. Comparar candidato vs baseline
   ├── val_action_mae
   ├── ran_completion tolerance = 0.0025
   └── ai_completion tolerance = 0.005

5. Emitir decisão
   └── awac_refresh_cycle_latest.json
       ├── candidate_is_better: bool
       ├── action_mae_delta: float
       ├── candidate_path: str
       └── recommend_promotion: false  (NUNCA true automático)
```

### 20.3 Critérios de comparação

| Métrica | Critério | Peso |
|---------|----------|------|
| `val_action_mae` | Menor é melhor | Primário |
| `avg_ran_completion` | >= baseline - 0.0025 | Secundário |
| `avg_ai_completion` | >= baseline - 0.005 | Secundário |

### 20.4 Saída

- `awac_refresh_cycle_latest.json` — decisão do ciclo
- Novo checkpoint em `runs/sac_bootstrap/offline_awac_<data>_refresh/`

O runtime **não** troca automaticamente para o novo checkpoint. A promoção requer validação manual.

---

## 21. AWAC Controlled Collection

Script principal:

- `scripts/run_fixed_awac_controlled_collection.py`

### 21.1 Propósito

Executar uma coleta controlada de dados para treino AWAC, com perfis de pressão pré-definidos que alternam entre estados de carga para gerar diversidade no dataset.

### 21.2 Perfis de pressão

O script define **5 perfis** com sequências temporais de estágios:

| Perfil | Estágios | Descrição |
|--------|----------|-----------|
| `drl_article_v1` | 13 estágios, 600s | Cobertura completa: saudável → sobrecarga câmera → recuperação → sensor stress → veicular stress |
| `drl_article_camera_focus` | 8 estágios, 300s | Foco em variação de câmera (baseline → overload → recovery) |
| `drl_article_ai_focus` | 8 estágios, 300s | Foco em variação de sensores + veículos |
| `drl_article_mixed_short` | 6 estágios, 180s | Ciclo rápido misto |
| `drl_article_conflict_smoke_v1` | 6 estágios, 240s | Smoke test para conflitos: saudável → camera_overload → recuperação |

### 21.3 Estágios típicos

Cada estágio define:
- `stage_id` — identificador
- `duration` — duração em segundos
- `app1_scale` — fator de escala para App1 (câmeras)
- `app2_scale` — fator de escala para App2 (sensores)
- `app3_scale` — fator de escala para App3 (veículos)

Exemplo de sequência `drl_article_v1`:
```
 1. baseline_healthy     (60s)  → tudo normal
 2. app1_warning         (45s)  → câmeras em warning de throughput
 3. app1_guard           (45s)  → câmeras em violação
 4. recovery             (60s)  → tudo normal
 5. app2_stressed_safe   (45s)  → sensores com carga alta (sem risco)
 6. vehicle_stressed_safe(45s)  → veículos em stress controlado
 7. recovery_short       (30s)  → tudo normal
 8. app1_overload        (60s)  → câmeras em sobrecarga severa
 9. recover_app1         (45s)  → recuperação câmeras
10. app2_stressed_risk   (45s)  → sensores em risco
11. mixed_heavy          (60s)  → todos os apps sob carga
12. recovery             (60s)  → tudo normal
13. finish               (0s)   → finaliza coleta
```

### 21.4 Execução

O script inicia o runtime GreenRAN com:
```bash
GREENRAN_RL_POLICY=awac
GREENRAN_COLLECTION_EVENT_PROFILE=drl_article_v1
GREENRAN_COLLECTION_EVENT_CYCLES=1
```

E monitora:
- Progresso de linhas no Data Lake
- `sim_time` atual
- Fase atual do cenário
- SLA de throughput/latência da App1
- Estado do shadow (se disponível)

### 21.5 Sistema de checkpoints

O script reporta readiness em milestones:
- **1000 linhas** — dataset mínimo
- **2500 linhas** — dataset intermediário
- **5000 linhas** — dataset para treino forte
- **sim_time 145s** — checkpoint temporal
- **sim_time 300s** — checkpoint temporal
- **sim_time 600s** — checkpoint temporal

---

## 22. Interpretação da coleta e métricas

### 22.1 Métricas do shadow runtime

Quando o shadow runtime está ativo, o avaliador operacional (`evaluate_marl_shadow_runtime.py`) produz estas métricas consolidadas:

| Métrica | Descrição | Interpretação |
|---------|-----------|---------------|
| `sample_count` | Nº de amostras na janela | Precisa >= 120 para análise |
| `checkpoint_coverage` | Fração com checkpoint carregado | Precisa >= 0.90 |
| `positive_score_rate` | Fração onde shadow_score > live_score | Quanto maior, melhor |
| `recommend_rate` | Fração onde `recommend_shadow = true` | Deve acompanhar positive_score_rate |
| `avg_score_delta` | Média de (shadow_score - live_score) | > 0 significa shadow melhor em média |
| `avg_ran_completion_delta` | Média da diferença em completude RAN | Shadow deve manter ou melhorar RAN |
| `avg_ai_completion_delta` | Média da diferença em completude AI | Pode sacrificar AI se RAN melhorar muito |

### 22.2 Classes de readiness do shadow

A avaliação produz uma classe que determina o que pode ser feito:

| Classe | Condição | Significado |
|--------|----------|-------------|
| `no_data` | `sample_count == 0` | Sem dados para avaliar |
| `insufficient_data` | `sample_count < 120` | Poucas amostras, esperar |
| `insufficient_checkpoint_coverage` | `checkpoint_coverage < 0.90` | Checkpoint não carregou o suficiente |
| `promising` | `positive_score_rate >= 0.60` | Shadow mostra potencial, mas inconsistente |
| `shadow_outperforming` | `positive_score_rate >= 0.50` e `avg_score_delta > 0` | Shadow consistentemente melhor |
| `control_trial_candidate` | `positive_rate >= 0.60`, `avg_score_delta > 0.01`, `ran_delta >= 0`, `ai_delta >= -0.02` | Pronto para trial de controle |
| `not_beating_live` | Nenhuma das acima | Shadow ainda não supera o live |

### 22.3 Transição típica

```
insufficient_data (<120 amostras)
  → promising (shadow > live em ~60%+)
    → shadow_outperforming (consistente)
      → control_trial_candidate (forte em RAN + AI)
        → trial_approved (aprovação manual)
```

### 22.4 Exemplo real

Em uma coleta com `drl_article_conflict_smoke_v1` (240s sim_time):

```
sample_count: 138
positive_score_rate: 0.8261
avg_score_delta: +0.011145
avg_ran_completion_delta: +0.0279  (RAN melhora ~2.8%)
avg_ai_completion_delta:  -0.0404  (AI sacrifica ~4%)
runtime_readiness: promising
gate_status: shadow_only
```

Interpretação:
- Shadow melhora RAN em ~2.8% (bom)
- Mas sacrifica AI em ~4% (não ideal)
- Por isso ainda é `promising`, não `control_trial_candidate`
- O gate permanece `shadow_only`

### 22.5 Distribuição de decisões na coleta

Durante uma coleta controlada, é esperado ver uma mistura de decisões:

| Decisão | Significado | Ideal na coleta |
|---------|-------------|-----------------|
| `ALLOWED` | Recursos suficientes, tráfego saudável | ~20-40% |
| `CONDITIONAL` | Warning, recursos apertados | ~10-20% |
| `BLOCKED` | Violação de SLA, corte necessário | ~40-60% |

Ter ambos os estados (ALLOWED e BLOCKED) é essencial para um treino equilibrado. Uma coleta com 100% BLOCKED não ensina o modelo quando liberar recursos.

### 22.6 Quando parar a coleta

Continue coletando se:
- `CSV rows < 1000` — dataset ainda pequeno
- `ran_completion_ratio` com apenas 1 valor único — sem diversidade
- `d_ran` com pouca variação — sem cobertura de demanda
- Só um tipo de decisão (só ALLOWED ou só BLOCKED)

Pare e exporte o trace quando:
- Pelo menos 1000-5000 linhas
- Diversidade em `ran_completion_ratio`
- Ambos os estados de decisão presentes
- Variação em `d_ran` e `d_ai`

---

## 23. Papel atual do TA-SAM no runtime

Hoje, no GreenRAN implementado:

- a política viva ainda é a política de alocação bootstrap/heurística ou a linha de recurso já ativa no runtime;
- o `TA-SAM` roda em `shadow mode`;
- o `shadow` é comparado contra o `live` continuamente;
- só depois de treino bom + runtime bom + aprovação manual ele pode virar candidato a `control_trial`.

Isso significa que o `TA-SAM` já está integrado ao runtime, mas ainda não é o dono da decisão aplicada.

---

## 24. O que já está implementado de ponta a ponta

Hoje já existe no GreenRAN:

- cenário fixo alinhado ao artigo;
- topologia por 3 DUs lógicos;
- estado por slice e por DU;
- estado global persistido;
- export de traço MARL real em `JSONL`;
- trainer TA-SAM funcional;
- export de checkpoints e metadata;
- classificador automático de checkpoints;
- checkpoint carregado em shadow runtime;
- comparação `live vs shadow` persistida no SQLite;
- avaliador de runtime por janela;
- `control gate` com aprovação manual opcional;
- watcher operacional do gate;
- monitor de coleta estável;
- AWAC refresh cycle automatizado;
- coleta controlada com perfis de pressão;
- pipeline completo de interpretação de métricas.

---

## 25. Limitações atuais

Apesar de funcional, a implementação ainda possui limites claros.

### 25.1 SAM integrado ao SAC offline (implementado)

O `SAMOptimizer` agora roda dentro do `run_offline_sac()` no `train_sac.py`, integrado ao gradiente do ator. O parâmetro `--sam-rho` controla o raio de perturbação (0.05 default, 0 para desligar), e `--sam-rho-decay` ativa o decaimento linear durante o treino. Esta seção está alinhada ao artigo.

### 25.2 SAC single-agent é simplificação vs MARL do artigo

O artigo propõe MARL com múltiplos agentes distribuídos. A linha SAC/AWAC atual é single-agent (um ator para todos os recursos). O TA-SAM MARL (multi-agente) já existe mas opera apenas em shadow mode.

### 25.3 AWAC não está no artigo original

O AWAC (Advantage-Weighted Actor-Critic) é uma extensão própria do projeto. O artigo usa SAC + SAM. O AWAC foi adicionado como alternativa experimental para melhorar a eficiência de amostras em treino offline.

### 25.4 Treino offline vs online

O artigo prevê treino online com iteração ambiente. O GreenRAN usa treino offline (bootstrap BC + SAC/AWAC sobre traços reais). Isso limita a capacidade do modelo de explorar estados não observados.

### 25.5 Behavior cloning forte

A política ainda aprende muito a partir da alocação viva observada, o que ajuda estabilidade, mas limita divergência exploratória. O ator tende a reproduzir a heurística em vez de descobrir estratégias radicalmente melhores.

### 25.6 Shadow ainda sem promoção operacional

O `shadow` já se aproximou bastante do `live`, mas a promoção exige consistência forte (positive_score_rate >= 0.60, avg_score_delta > 0.01, ran_delta >= 0, ai_delta >= -0.02), e não apenas média ligeiramente positiva.

---

## 26. Conclusão

A DRL do GreenRAN hoje é uma arquitetura completa e em camadas:

- **legado**: `SBiLSTM + A3C`
- **transição**: `SAC/AWAC single-agent`
- **oficial**: `TA-SAM MARL article-aligned`

O ponto central da implementação atual é este:

- o projeto já não para no treino offline;
- ele injeta o estado MARL no runtime;
- roda o checkpoint em paralelo;
- mede sistematicamente `live vs shadow`;
- persiste essa evidência;
- e só aceita promoção quando treino, runtime e gate convergirem.

Essa é, hoje, a implementação real da DRL no GreenRAN.

---

## 27. Gerador sintético de workloads com cadeia de Markov + ruído Gaussiano

### 27.1 Motivação

O ambiente atual de treino (`CAORASACEnv`) carrega dados de um CSV real exportado do Data Lake. Com apenas ~1641 pontos e **9 combinações únicas** de demanda, o agente vê pouca variedade e não generaliza bem.

Para seguir o artigo de Lotfi et al. (2025) com **treino online**, precisamos de um ambiente que gere infinitas combinações realistas sem depender do ns-3 ou Data Lake.

### 27.2 Análise dos dados reais

Os 1641 pontos da coleta `greenran_marl_diverse` revelaram:

| Estágio (scenario_stage) | d_ran | d_ai | budget | Decisão | Amostras |
|--------------------------|-------|------|--------|---------|----------|
| `baseline_healthy` | 0.225 | 0.171 | 0.682 | ALLOWED | 21 |
| (transição) | 0.320 | 0.150 | 0.707 | ALLOWED | 3 |
| `camera_overload` | 0.899 | 0.171 | 0.907 | BLOCKED | 322 |
| `camera_overload_repeat` | 0.902 | 0.171 | 0.908 | BLOCKED | 316 |
| `background_overload` | 0.832 | 0.198 | 0.893 | BLOCKED | 369 |
| `background_overload` (outlier) | 0.832 | 0.215 | 0.899 | BLOCKED | 1 |
| `mixed_overload` | 0.906 | 0.316 | 0.958 | BLOCKED | 421 |
| `mixed_overload` (outlier) | 0.906 | 0.272 | 0.943 | BLOCKED | 1 |
| `recovery_window` | 0.448 | 0.171 | 0.756 | CONDITIONAL | 227 |

**Total: 9 combinações únicas** em 1641 amostras. As transições entre passos consecutivos são muito suaves: 99,8% têm `|Δd_ran| < 0,1` e `|Δd_ai| < 0,004`.

### 27.3 Estrutura do gerador

O gerador proposto é uma **cadeia de Markov** sobre os 6 estágios (mais transição), cada um com um **vetor base** de demandas. A cada passo, pequeno **ruído Gaussiano** é adicionado para criar variação.

#### Estados da cadeia

```
[baseline_healthy]
       |
       ▼
[camera_overload] ──► [mixed_overload] ──► [background_overload]
       |                    |                      |
       ▼                    ▼                      ▼
[camera_overload_repeat]  [recovery_window] ◄─────┘
```

#### Matriz de transição (aprendida dos dados)

| De \ Para | healthy | camera | mixed | background | recovery | cam_repeat |
|-----------|---------|--------|-------|------------|----------|------------|
| healthy | 0,90 | 0,10 | 0 | 0 | 0 | 0 |
| camera | 0 | 0,85 | 0,05 | 0 | 0,10 | 0 |
| mixed | 0 | 0 | 0,90 | 0,05 | 0,05 | 0 |
| background | 0 | 0 | 0 | 0,88 | 0,10 | 0,02 |
| recovery | 0 | 0 | 0 | 0 | 0,80 | 0,20 |
| cam_repeat | 0 | 0,10 | 0 | 0 | 0,10 | 0,80 |

#### Valores base por estágio

```python
STAGE_BASES = {
    "baseline_healthy":   (0.225, 0.171, 0.682),
    "transition":         (0.320, 0.150, 0.707),
    "recovery_window":    (0.448, 0.171, 0.756),
    "background_overload":(0.832, 0.198, 0.893),
    "camera_overload":    (0.899, 0.171, 0.907),
    "camera_overload_repeat": (0.902, 0.171, 0.908),
    "mixed_overload":     (0.906, 0.316, 0.958),
}
```

#### Algoritmo de geração

```python
class MarkovWorkloadGenerator:
    """
    Gera workloads sintéticos realistas usando cadeia de Markov + ruído.
    Cada reset() inicia em baseline_healthy.
    Cada step() pode transicionar para outro estágio conforme a matriz.
    Ruído Gaussiano N(0, σ) é adicionado aos valores base.
    """

    def __init__(self, sigma_ran=0.03, sigma_ai=0.02, sigma_budget=0.01):
        self.sigma_ran = sigma_ran
        self.sigma_ai = sigma_ai
        self.sigma_budget = sigma_budget
        self.stage = "baseline_healthy"
        self.transition_matrix = {...}  # matriz acima

    def reset(self):
        self.stage = "baseline_healthy"
        return self._sample()

    def step(self):
        # Transiciona probabilisticamente
        probs = self.transition_matrix[self.stage]
        stages = list(probs.keys())
        self.stage = np.random.choice(stages, p=list(probs.values()))
        return self._sample()

    def _sample(self):
        base_ran, base_ai, base_budget = STAGE_BASES[self.stage]
        d_ran = base_ran + np.random.normal(0, self.sigma_ran)
        d_ai = base_ai + np.random.normal(0, self.sigma_ai)
        budget = base_budget + np.random.normal(0, self.sigma_budget)
        # Clip para valores válidos
        return (
            np.clip(d_ran, 0.0, 1.0),
            np.clip(d_ai, 0.0, 1.0),
            np.clip(budget, 0.0, 1.0),
        )
```

### 27.4 O que é ruído Gaussiano

Ruído Gaussiano (ou ruído branco) é uma perturbação aleatória seguindo uma **distribuição normal** (curva de sino) com:

- **Média (μ) = 0**: o ruído não desloca o valor médio
- **Desvio padrão (σ)**: controla a intensidade do ruído

```python
# Exemplo: d_ran base = 0.899, σ = 0.03
d_ran_final = 0.899 + np.random.normal(0, 0.03)
# Possíveis resultados: 0.912, 0.887, 0.903, 0.869, 0.924...
# 68% das amostras ficam entre 0.899 ± 0.03
# 95% das amostras ficam entre 0.899 ± 0.06
```

Sem ruído, a IA decora os 9 valores fixos e falha se encontrar algo diferente. Com ruído, ela aprende a **função contínua** e generaliza para qualquer valor dentro da faixa.

### 27.5 Resultado esperado

| Métrica | Sem ruído (9 estados) | Com ruído Markov + Gaussiano |
|---------|-----------------------|------------------------------|
| Estados únicos vistos | 9 | ~10⁶+ combinações |
| Generalização | Nenhuma fora dos 9 | Toda faixa 0,2-0,9 (d_ran) |
| Overfitting | Alto (decora valores) | Baixo (aprende função) |
| Performance em produção | Frágil | Robusta |

---

## 28. Função de recompensa do artigo (Lotfi et al. 2025)

### 28.1 Definição original

A recompensa proposta no artigo (seção IV-A) é:

```
r_t = σ(α_l · Q_l) + P_res + P_minQ
```

Onde:

- `σ(x) = 1 / (1 + e^(-x))` — função sigmoid
- `Q_l` — QoS atual da slice `l` (eMBB, MTC, URLLC)
- `α_l` — parâmetro de sensibilidade adaptativo por slice
- `P_res` — penalidade por exceder recursos disponíveis
- `P_minQ` — penalidade por não atingir QoS mínima

### 28.2 Componente 1: Satisfação de QoS com sigmoid

A sigmoid mapeia a QoS medida para uma recompensa entre 0 e 1:

```python
α_embb   = 4.0    # prioridade máxima (câmeras)
α_mtc    = 2.0    # sensores
α_urllc  = 3.0    # veículos

qos_embb  = alocacao_embb / max(demanda_embb, 1e-6)
qos_mtc   = alocacao_mtc  / max(demanda_mtc, 1e-6)
qos_urllc = alocacao_urllc / max(demanda_urllc, 1e-6)

sig_embb  = 1 / (1 + exp(-α_embb  * qos_embb))
sig_mtc   = 1 / (1 + exp(-α_mtc   * qos_mtc))
sig_urllc = 1 / (1 + exp(-α_urllc * qos_urllc))

qos_termo = (sig_embb + sig_mtc + sig_urllc) / 3
```

Características da sigmoid:
- **QoS = 0** → σ ≈ 0,5 (neutro, não penaliza nem recompensa)
- **QoS = 0,5** → σ ≈ 0,73 (começa a recompensar)
- **QoS = 1,0** → σ ≈ 0,98 (saturado, recompensa máxima)
- **QoS negativo** → σ < 0,5 (penaliza)

O α controla a inclinação: α maior = resposta mais abrupta.

### 28.3 Componente 2: Penalidade por excesso de recurso

```python
r_used = alocacao_embb + alocacao_mtc + alocacao_urllc
excesso = max(0, r_used - budget)
P_res = -β * excesso    # β = 2.0
```

Só é ativada quando a alocação total excede o budget disponível. É proporcional ao excesso.

### 28.4 Componente 3: Penalidade por QoS mínima

```python
Qmin_embb  = 0.7   # câmeras: mínimo 70%
Qmin_mtc   = 0.5   # sensores: mínimo 50%
Qmin_urllc = 0.8   # veículos: mínimo 80%

P_minQ = -γ * (
    max(0, Qmin_embb  - qos_embb)
    + max(0, Qmin_mtc   - qos_mtc)
    + max(0, Qmin_urllc - qos_urllc)
)   # γ = 5.0
```

Penaliza fortemente QoS abaixo do mínimo aceitável. É zero quando todas as slices estão acima do limiar.

### 28.5 Implementação completa

```python
def recompensa_artigo(self, alocacao, demandas):
    EPS = 1e-6

    # QoS por slice
    qos_embb  = alocacao[0] / max(demandas[0], EPS)
    qos_mtc   = alocacao[1] / max(demandas[1], EPS)
    qos_urllc = alocacao[2] / max(demandas[2], EPS)

    # 1. Sigmoid de satisfação
    alpha = [4.0, 2.0, 3.0]
    sigmas = [1/(1+np.exp(-a*q)) for a, q in zip(alpha, [qos_embb, qos_mtc, qos_urllc])]
    qos_termo = np.mean(sigmas)

    # 2. Penalidade de recurso
    usado = sum(alocacao)
    excesso = max(0, usado - self.budget)
    res_penalty = -2.0 * excesso

    # 3. Penalidade de QoS mínima
    qos_min = [0.7, 0.5, 0.8]
    abaixo = sum(max(0, qmin - q) for qmin, q in zip(qos_min, [qos_embb, qos_mtc, qos_urllc]))
    min_qos_penalty = -5.0 * abaixo

    return qos_termo + res_penalty + min_qos_penalty
```

### 28.6 Diferença para a recompensa atual

| Aspecto | Atual (média ponderada) | Artigo (sigmoid + penalidades) |
|---------|------------------------|-------------------------------|
| Faixa | ~0 a 3,5 | ~-5 a +1 |
| Formato | Linear | Não-linear (sigmoidal) |
| QoS mínima | Sem proteção | Penalidade forte abaixo do limiar |
| Excesso recurso | Penalidade fixa -1 | Proporcional ao excesso |
| Prioridade por slice | Peso único 2x RAN | α separado por slice (4, 2, 3) |
| Sensibilidade a QoS baixa | Baixa | Alta (sigmoid + penalidade mínima) |

---

## 29. Plano de treino online SAC + SAM

### 29.1 Arquitetura do novo pipeline

Será criada uma trilha paralela que não interfere no pipeline offline existente:

```
drlexp/
├── training/
│   ├── train_sac.py              ← intocado (offline atual)
│   └── train_online_sam.py       ← NOVO: treino online SAC+SAM
└── src/drl/
    ├── caora_sac_environment.py   ← intocado
    ├── ta_sam_marl.py             ← intocado
    └── online_marl_env.py         ← NOVO: ambiente com gerador Markov
```

### 29.2 Ambiente online (`online_marl_env.py`)

```python
class OnlineMARLEnv(gym.Env):
    """
    Ambiente de treino online com gerador sintético Markov + ruído.
    Não depende de Data Lake, ns-3 ou CSV.
    """

    def __init__(self, n_du=3, n_slices=3):
        self.workload_gen = MarkovWorkloadGenerator()
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(n_du * n_slices,))
        self.observation_space = spaces.Box(low=0.0, high=np.inf, shape=(10,))
```

### 29.3 Loop de treino online SAC + SAM

```python
def train_online_sac_sam(args):
    env = OnlineMARLEnv()
    actor = GaussianActor(state_dim, action_dim)
    critic1, critic2 = QNetwork(...), QNetwork(...)
    replay_buffer = ReplayBuffer(capacity=100000)

    rho = args.sam_rho_init  # 0.05

    for episode in range(args.num_episodes):
        state, _ = env.reset()
        episode_reward = 0

        while not terminated:
            action = actor.sample(state)
            next_state, reward, terminated, ... = env.step(action)
            replay_buffer.push(state, action, reward, next_state, terminated)
            state = next_state
            episode_reward += reward

        # Treino com batch do replay buffer
        batch = replay_buffer.sample(args.batch_size)

        # 1. Atualiza críticos (SAC twin-Q, sem SAM)
        critic_loss = atualiza_criticos(batch, actor, critic1, critic2)

        # 2. Atualiza ator COM SAM (sharpness-aware)
        # 2a. Forward pass
        actions_pred = actor(batch.states)
        q1 = critic1(batch.states, actions_pred)
        q2 = critic2(batch.states, actions_pred)
        min_q = torch.min(q1, q2)
        actor_loss = (alpha * log_prob - min_q).mean()

        # 2b. SAM: perturba gradientes
        actor_loss.backward()
        grad_norm = calcula_norma_gradientes(actor)
        if grad_norm > 0:
            with torch.no_grad():
                for p in actor.parameters():
                    if p.grad is not None:
                        p.add_(rho * p.grad / grad_norm)  # sobe
            # 2c. Recalcula loss na posição perturbada
            actor_loss_adv = calcula_loss_ator(actor, batch, critic1, critic2)
            actor_opt.zero_grad()
            actor_loss_adv.backward()  # gradiente na posição avançada
            # 2d. Restaura parâmetros e aplica gradiente corrigido
            with torch.no_grad():
                for p in actor.parameters():
                    p.sub_(rho * p.grad / grad_norm)  # desce
            optimizer.step()

        # 3. Decaimento de ρ
        rho = max(args.rho_min, rho * args.rho_decay)
```

### 29.4 SAM integrado — detalhe do algoritmo

O SAM (Sharpness-Aware Minimization) do artigo funciona em 3 passos:

```
1. Calcular gradiente ∇L(θ) na posição atual
2. Avançar para θ_adv = θ + ρ · ∇L(θ) / ||∇L(θ)||
3. Calcular gradiente em θ_adv e usar esse gradiente para atualizar θ
```

Isso encontra **mínimos planos** (flat minima) que generalizam melhor que mínimos pontiagudos (sharp minima). O ρ controla o "raio" de perturbação.

### 29.5 ρ dinâmico

O artigo usa ρ que decai durante o treino:

```python
# Estratégia de decaimento
rho_inicial = 0.05    # exploratório no começo
rho_final   = 0.005   # conservador no fim
rho = rho_inicial * (1 - episode / total_episodes) + rho_final * (episode / total_episodes)
```

- **Começo (ρ ≈ 0.05)**: SAM força o ator a explorar regiões planas
- **Meio (ρ ≈ 0.025)**: equilíbrio entre exploração e refinamento
- **Final (ρ ≈ 0.005)**: refino fino, quase SAC vanilla

### 29.6 Seletor SAM (opcional pós-implementação)

O artigo aplica SAM **seletivamente**: só atualiza atores onde a variância do TD-error excede um limiar `λ_TD`. Isso reduz custo computacional e foca as atualizações nos agentes mais incertos.

```python
td_errors = [abs(q - reward) for q, reward in zip(q_values, batch_rewards)]
td_variance = np.var(td_errors)
if td_variance >= lambda_td:
    aplicar_sam = True
else:
    aplicar_sam = False  # só atualização vanilla
```

---

## 30. Comparação completa: GreenRAN vs Artigo Lotfi et al. 2025

### 30.1 Tabela geral

| Aspecto | Artigo (Lotfi et al.) | GreenRAN hoje | Alvo pós-alinhamento |
|---------|----------------------|---------------|---------------------|
| **Nº DUs** | 6 | 3 | 3 (manter) |
| **Nº UEs** | 200 | 12 + 5 veículos | 12 + 5 (manter) |
| **Slices** | eMBB, MTC, URLLC | eMBB, mMTC, URLLC | ✓ igual |
| **Rede ator** | 300→400→400 tanh | **300→400→400 tanh** ✓ | ✓ |
| **Rede crítico** | 300→400→400 tanh | **300→400→400 tanh** ✓ | ✓ |
| **Learning rate** | 1e-4 | **1e-4** ✓ | ✓ |
| **Otimizador** | Adam | Adam + SAM ✓ | ✓ |
| **Algoritmo** | SAC + SAM integrado | **SAC + SAM integrado** ✓ | ✓ |
| **ρ SAM** | Dinâmico (decai) | **Dinâmico (decai)** ✓ | ✓ |
| **Seletor SAM** | Variância TD-error | **Variância TD-error** ✓ | ✓ |
| **Recompensa** | `σ(α·Q) + P_res + P_minQ` | **`σ(α·Q) + P_res + P_minQ`** ✓ | ✓ |
| **Treino** | Online (iteração ambiente) | Offline (BC + SAC/AWAC) + Online (Markov) ✓ | ✓ |
| **Ambiente** | Sintético próprio | CSV real Data Lake | Gerador Markov + ruído |
| **Crítico** | Global centralizado | Twin-Q SAC | Twin-Q SAC + SAM |
| **Fonte de dados** | UEs virtuais | ns-3 com xApps reais | Distribuições dos traços |

### 30.2 Status de implementação

| Item | Status | Prioridade |
|------|--------|------------|
| Ambiente Markov + ruído | **Implementado** ✓ | Alta |
| Recompensa sigmoid + penalidades | **Implementado** ✓ | Alta |
| SAC + SAM integrado | **Implementado** ✓ | Alta |
| ρ dinâmico | **Implementado** ✓ | Média |
| Seletor por variância TD-error | **Implementado** ✓ | Média |
| Rede 300→400→400 tanh | **Implementado** ✓ | Média |
| Learning rate 1e-4 | **Implementado** ✓ | Baixa |
| Treino online | **Implementado** ✓ (`train_online_sam.py` + `online_marl_env.py`) | Alta |

### 30.3 Arquivos novos necessários

| Arquivo | Descrição |
|---------|-----------|
| `drlexp/src/drl/online_marl_env.py` | Ambiente Gymnasium com gerador Markov + ruído e recompensa do artigo ✅ |
| `drlexp/training/train_online_sam.py` | Loop de treino online SAC + SAM com ρ dinâmico ✅ |

### 30.4 Benefício esperado

Completar o alinhamento com o artigo trará:

1. **Generalização**: agente treinado em milhões de estados sintéticos vs 9 fixos
2. **Qualidade**: recompensa sigmoid + penalidades alinhada com QoS real
3. **Exploração**: treino online permite que o agente descubra estratégias não observadas
4. **Platôs**: SAM força mínimos planos, reduzindo overfitting
5. **Shadow consistente**: checkpoint mais robusto deve manter positive_rate > 60% mesmo em sobrecarga
