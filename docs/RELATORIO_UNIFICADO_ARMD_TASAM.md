# Relatório Unificado — Arquitetura, Artigos, Seeds e Resultados de ARMD-GreenRAN e TA-SAM

## 1. Objetivo deste consolidado

Este arquivo reúne em um único documento:

- o cenário operacional canônico do GreenRAN;
- a arquitetura usada no GreenRAN;
- os artigos de referência usados nas duas trilhas;
- os seeds documentados;
- o contexto, os objetivos e os resultados das duas simulações centrais:
  `ARMD-GreenRAN` e `TA-SAM`.

O objetivo aqui não é substituir os relatórios originais, e sim consolidar em um
texto único o que foi efetivamente usado e obtido.

Este arquivo passa a ser o documento canônico do cenário.

---

## 2. Cenário operacional canônico do GreenRAN

### 2.1 Identidade do cenário

- `scenario_id`: `greenran_fixed_baseline_v1`
- binário `ns-3`: `scenario-greenran`
- arquitetura congelada: `keep_greenran_architecture = true`
- camada de conflitos congelada: `keep_armd_greenran = true`

### 2.2 Composição operacional

- `20 UEs` totais no cenário fixo atual
- `3` câmeras da `App1`
- `12` UEs de background (`IMSI 4-15`)
- `5` veículos da `App3` (`IMSI 16-20`)

### 2.3 Aplicações e SLAs

#### App1 — Vigilância

- domínio: `camera`
- câmeras ativas: `3`
- IMSIs reservados: `1-3`
- throughput alvo: `25 Mbps`
- warning de throughput: `30 Mbps`
- warning de latência: `60 ms`
- violação de latência: `80 ms`

#### App2 — Monitoramento

- domínio: `sensor`
- perfil funcional: `mMTC-like`
- a lógica operacional permanece alinhada ao runtime atual, sem mudança do ARMD

#### App3 — Veicular

- domínio: `vehicle`
- máximo operacional: `5` veículos
- faixa reservada: `IMSI 16-20`
- perfil funcional: `URLLC-like`

### 2.4 Parâmetros de rede congelados

- `S1-U DataRate`: `15 Mbps`
- `S1-U Delay`: `5 ms`
- `P2P Link`: `30 Mbps`
- `P2P Delay`: `20 ms`
- `RLC/PDCP Buffer`: `20 MB`
- `camera_off_time_s`: `3`
- `background_on_time_s`: `1`
- `background_off_time_s`: `10`

### 2.5 Topologia MARL do cenário fixo

O cenário GreenRAN atual usa `3 DUs lógicos`:

1. `du_camera_edge`
   - slice principal: `eMBB`
   - foco: `App1`
2. `du_sensor_mixed`
   - slice principal: `mMTC`
   - foco: `App2`
3. `du_vehicle_edge`
   - slice principal: `URLLC`
   - foco: `App3`

### 2.6 Mapeamento funcional

- `App1-Vigilancia` -> `eMBB-like`
- `App2-Monitoramento` -> `mMTC-like`
- `App3-Veicular` -> `URLLC-like`

---

## 3. Arquitetura consolidada do sistema

O projeto foi estruturado sobre arquitetura `O-RAN`, com separação entre
controle de alto nível, inferência near-real-time e simulação da rede.

### 3.1 Camadas O-RAN

- `Non-RT RIC`: camada de rApps e políticas de otimização acima de `1000 ms`.
- `Near-RT RIC`: camada de xApps e decisões entre `10 ms` e `1000 ms`.
- `O-DU`: execução distribuída da rede, onde as decisões de rádio impactam os slices.
- `O-RU`: camada de rádio.

### 3.2 Arquitetura lógica do GreenRAN

O GreenRAN foi organizado em uma pilha de decisão com seis estágios:

1. `Trend Analysis`
2. `Pattern Engine`
3. `CVaR / Variance`
4. `ML Predictor`
5. `MARL Shadow`
6. `Arbiter Final`

Dentro dessa arquitetura, os dois blocos centrais deste relatório cumprem papéis
distintos:

- `TA-SAM MARL`: política multiagente para alocação de recursos por DU lógico.
- `ARMD-GreenRAN`: trilha baseada em `GraphSAGE` para aprender, reconstruir e
  interpretar conflitos entre aplicações, parâmetros e KPIs.

### 3.3 Papel de cada trilha

#### ARMD-GreenRAN

- aprende o grafo de conflitos;
- reconstrói relações entre parâmetros e KPIs;
- mede conflitos `direct`, `indirect` e `implicit`;
- apoia interpretação e auditoria das decisões do sistema.

#### TA-SAM

- executa gerenciamento de recursos via `SAC + SAM`;
- opera como política multiagente por DU;
- foi usado para reprodução metodológica do paper e para a trilha MARL em shadow mode.

---

## 4. Artigos e referências usados

### 4.1 Base de arquitetura

1. `O-RAN Alliance — O-RAN Architecture Description`

Essa referência foi usada para a organização da arquitetura em `Non-RT RIC`,
`Near-RT RIC`, `O-DU` e `O-RU`.

### 4.2 Base do TA-SAM

2. `Task Specific Sharpness Aware O-RAN Resource Management using Multi Agent Reinforcement Learning`
   - identificador: `arXiv:2511.15002`

Esse artigo foi a referência metodológica para a trilha `TA-SAM`, incluindo o
uso de `SAC`, `SAM`, múltiplos agentes e comparação contra baseline sem SAM.

### 4.3 Base do ARMD-GreenRAN

3. `Learning and Reconstructing Conflicts in O-RAN: A Graph Neural Network Approach`
   - referência local: `article00.pdf`

Esse artigo foi a referência metodológica da trilha `article00`, usada como base
para a formulação temporal do `ARMD-GreenRAN`.

4. `Hamilton, Ying e Leskovec — Inductive Representation Learning on Large Graphs`

Essa referência sustenta o uso de `GraphSAGE` como encoder de grafos.

---

## 5. Seeds usados

### 5.1 Seeds documentados para ARMD-GreenRAN

Existem dois conjuntos documentados no repositório:

- protocolo GreenRAN GraphSAGE por cenários: `42, 43, 44, 45, 46`
- agregado multiseed da trilha `article00`: `42, 43, 44, 45, 46, 47`

### 5.2 Seeds documentados para TA-SAM

Para a evidência consolidada do pacote externo `resultados_experimentos_tasam.zip`,
o seed da rodada final **não está explicitado** no artefato importado.

O que está documentado no repositório é:

- seed padrão dos runners article-aligned: `42`
- sweep multiseed offline previsto no pipeline: `42, 43, 44, 45, 46`

Portanto, para o `TA-SAM`, o resultado final consolidado abaixo é
`single-run documentado`, mas **sem seed explícita no bundle externo**.

---

## 6. Estado atual do runtime

### 6.1 Política viva

O runtime atual do cenário opera assim:

- alocador vivo principal: `heuristic`
- `ARMD-GreenRAN`: ativo como camada de proteção no `rApp`
- `TA-SAM`: ativo como `shadow advisor`

### 6.2 Papel do ARMD-GreenRAN no runtime

O `ARMD-GreenRAN` já ajuda o `rApp` diretamente no cenário atual:

- classifica o cenário vivo com base no pacote híbrido validado offline;
- identifica cenários como `app1_throughput`, `app1_latencia`,
  `conflito_implicito`, `recuperacao`, `vehicle_warning` e `vehicle_critical`;
- pode escalar proteção no `rApp` quando detecta um cenário validado com
  confiança suficiente.

Modo operacional atual:

- `ARMD mode = assist`
- ele só aumenta a severidade da proteção, não reduz proteção já aplicada

### 6.3 Papel do TA-SAM no runtime

O `TA-SAM` também ajuda o `rApp`, mas hoje de forma paralela:

- recebe o estado MARL exportado pelo runtime;
- calcula recomendações por DU;
- compara `live allocation` versus `shadow allocation`;
- gera `resource_advice`, `energy_advice` e score de arbitragem;
- persiste sinais de shadow no Data Lake.

Modo operacional atual:

- `TA-SAM advisor = enabled`
- `mode = shadow`
- `control gate = shadow_only`

Na prática:

- o `TA-SAM` ainda não assume o controle vivo;
- ele observa, recomenda e acumula evidência para futura promoção.

### 6.4 Leitura operacional do cenário hoje

Hoje, quando o cenário roda:

- o `rApp` continua decidindo ao vivo com a política principal;
- o `ARMD-GreenRAN` pode reforçar a proteção do `rApp`;
- o `TA-SAM` acompanha em shadow e mede se teria desempenho melhor.

---

## 7. Simulação 1 — ARMD-GreenRAN

### 7.1 Contexto

O `ARMD-GreenRAN` foi usado para modelar conflitos em `O-RAN` como um problema
de grafo heterogêneo e temporal. A trilha foi organizada para aprender relações
entre:

- `A`: aplicações / xApps;
- `P`: parâmetros de controle;
- `K`: KPIs.

No contexto GreenRAN, isso foi aplicado principalmente aos cenários de:

- `conflito_implicito`
- `recuperacao`

e também à trilha isolada `article00`, usada como referência metodológica.

### 7.2 Objetivo

O objetivo da simulação `ARMD-GreenRAN` foi:

- reconstruir o grafo de conflitos a partir dos dados;
- detectar conflitos `implicit` e `indirect`;
- verificar se a trilha GreenRAN conseguia igualar ou superar a referência do
  artigo-base com menos épocas.

### 7.3 Configuração principal

- modelo: `GraphSAGE`
- amostra principal: `450 samples`
- épocas de sucesso: `200`
- thresholds oficiais: `0.2`, `0.5`, `0.9`
- split: `holdout temporal por rodadas`
- agregado multiseed principal: `42, 43, 44, 45, 46, 47`

### 7.4 Resultados

#### Melhor resultado geral da trilha article00

- `samples = 450`
- `epochs = 200`
- `selection_threshold = 0.2`
- `parameter_kpi_f1 = 1.0`
- `indirect_f1 = 1.0`
- `implicit_f1 = 1.0`

#### Melhor resultado estrito em `threshold = 0.5`

- `samples = 450`
- `epochs = 200`
- `parameter_kpi_f1 = 0.988889`
- `indirect_f1 = 1.0`
- `implicit_f1 = 1.0`

#### Resultado por seed no agregado multiseed `article00`

| Seed | Best Epoch | parameter_kpi_f1 | indirect_f1 | implicit_f1 |
|---|---:|---:|---:|---:|
| 42 | 50 | 0.777778 | 0.666667 | 1.0 |
| 43 | 200 | 0.842105 | 0.400000 | 0.8 |
| 44 | 50 | 0.933333 | 1.0 | 1.0 |
| 45 | 200 | 0.700000 | 0.333333 | 0.888889 |
| 46 | 200 | 0.933333 | 1.0 | 1.0 |
| 47 | 200 | 0.933333 | 1.0 | 1.0 |

#### Resultados operacionais do GreenRAN por cenário

- `conflito_implicito`: `F1 = 1.0`, `precision = 1.0`, `recall = 1.0`, `best_epoch = 600`
- `recuperacao`: `F1 = 0.57`, `precision = 0.45`, `recall = 0.77`

### 7.5 Leitura final da simulação ARMD-GreenRAN

O principal resultado dessa trilha foi mostrar que o `ARMD-GreenRAN` conseguiu:

- atingir `F1 = 1.0` no melhor caso geral;
- manter `indirect_f1 = 1.0` e `implicit_f1 = 1.0` em `threshold = 0.5`;
- fechar a trilha principal em `200 épocas`, enquanto a referência comparativa
  do artigo exigia `600 épocas`.

Em resumo, foi a trilha com resultado mais forte e mais estável em termos de
reconstrução de conflitos.

---

## 8. Simulação 2 — TA-SAM

### 8.1 Contexto

A simulação `TA-SAM` foi conduzida como reprodução metodológica do artigo
`arXiv:2511.15002`, comparando:

- `SAC puro`
- `TA-SAM`, isto é, `SAC + selective SAM`

O cenário reproduzido foi de `network slicing` em `O-RAN` com `ns-3`.

### 8.2 Objetivo

O objetivo da simulação `TA-SAM` foi:

- reproduzir a dinâmica do paper em um cenário controlado;
- verificar se o `TA-SAM` supera o `SAC` padrão;
- medir se `SAM` reduz `catastrophic forgetting` no treinamento.

### 8.3 Configuração principal

- `6 DUs`
- `200 UEs`
- `100 RBs` por DU
- `20 MHz`
- `1200 steps`
- tráfego reduzido para `37%` da configuração original do paper
- `actor/critic hidden dims`: `300-400-400`
- `learning rate = 1e-4`
- `gamma = 0.99`
- `tau = 0.005`
- `batch_size = 128`
- `rho`: `0.5 -> 0.01`
- seletor SAM: variância do `TD-error`
- `selector_threshold = 0.1`

### 8.4 Seeds

Para esta simulação consolidada:

- o bundle externo com os resultados finais **não explicita o seed da execução**;
- o pipeline local do repositório usa `seed = 42` por padrão;
- o sweep multiseed previsto no código é `42, 43, 44, 45, 46`.

### 8.5 Resultados

#### Resultado parcial do treino online fiel ao artigo no cenário GreenRAN

Além da reprodução controlada do artigo, foi executado um treino `online`
article-faithful no cenário lógico atual do GreenRAN, preservando:

- `du_camera_edge`
- `du_sensor_mixed`
- `du_vehicle_edge`

Esse treino foi rodado até `60 episódios` no diretório oficial
`runs/sac_bootstrap/online_tasam_marl`, gerando histórico real do processo e
figura parcial de acompanhamento.

Métricas consolidadas dos `60 episódios`:

- `completed_episodes = 60`
- `last_global_step = 12000`
- `mean_return_all = 85.0772`
- `mean_return_last_5 = 87.4338`
- `mean_return_last_10 = 87.1450`
- `mean_return_last_20 = 86.0092`
- `best_episode_return = 88.3605`
- `worst_episode_return = 79.2513`
- `last_episode_return = 86.1525`
- `last_eval_return_proxy = 20.6031`
- `last_critic_loss = 0.2549`
- `last_td_var_mean = 0.0708`
- `last_alpha = 0.0094`
- `last_rho_actor = 0.4807`

Leitura desse resultado parcial:

- o `TA-SAM` mostrou aprendizado estável em retorno no cenário GreenRAN;
- os `60 episódios` já são válidos para gráficos de comportamento do treino;
- esse histórico não preservou checkpoint de retomada, mas preservou a
  evidência experimental do processo.

Artefatos já materializados para esse recorte:

- `online_tasam_marl_partial_summary.json`
- `online_tasam_marl_partial_training_curves.png`

#### Janela final (`last100`)

- `SAC q0 = 0.1571`
- `TA-SAM q0 = 0.1996`
- vantagem final do `TA-SAM`: `+27.0%`
- `SAC alpha final = 0.5772`
- `TA-SAM alpha final = 1.5251`

#### Resultado global

- `SAC q0 global = 0.1607`
- `TA-SAM q0 global = 0.1799`
- vantagem global do `TA-SAM`: `+12.0%`

#### Esquecimento catastrófico

- `SAC`: queda de `-14.3%`
- `TA-SAM`: queda de `-6.7%`

Leitura:

- o `SAC` aprendeu e depois perdeu parte do ganho;
- o `TA-SAM` preservou melhor o desempenho aprendido.

#### Alocação final de RBs nos últimos 200 passos

| Método | eMBB | mMTC | URLLC | Total |
|---|---:|---:|---:|---:|
| SAC | 200 | 201 | 199 | 600 |
| TA-SAM | 223 | 253 | 124 | 600 |

#### Continuação em andamento para ampliar a evidência

Para melhorar a leitura estatística do comportamento do `TA-SAM`, foram
iniciados novos treinos paralelos com `2 seeds` ao mesmo tempo:

- `seed_0042`
- `seed_0043`

Cada seed está rodando com:

- `4 threads` de CPU por processo
- `checkpoint_interval = 25`
- `eval_interval = 50`

Na última leitura consolidada:

- `seed_0042`: `12 episódios`, `2400 passos`, `last_return = 83.3622`
- `seed_0043`: `12 episódios`, `2400 passos`, `last_return = 85.7460`

O objetivo dessa continuação é:

- aumentar a quantidade de episódios observados;
- verificar estabilidade do `critic_loss` em execuções independentes;
- comparar o comportamento do treino entre seeds;
- preparar uma leitura mais robusta para figuras e comparação final.

### 8.6 Comparação direta entre TA-SAM e SAC

Para remover ambiguidade, a comparação `TA-SAM vs SAC` foi consolidada em três
níveis distintos: `artigo`, `replay controlado do nosso cenário` e `treino
online parcial do nosso cenário atual`.

#### Resultado do artigo base

No artigo de referência, o `TA-SAM` superou o `SAC` de forma clara:

- `q0` nos últimos `100 passos`: `SAC = 0.1571`, `TA-SAM = 0.1996`
- vantagem final do `TA-SAM`: `+27.0%`
- `q0` global: `SAC = 0.1607`, `TA-SAM = 0.1799`
- vantagem global do `TA-SAM`: `+12.0%`
- esquecimento catastrófico: `SAC = -14.3%`, `TA-SAM = -6.7%`

Portanto, na referência metodológica, o `TA-SAM` foi superior ao `SAC`.

#### Resultado no nosso replay controlado

No arquivo
`runs/tasam_greenran_comparison/tasam_scenario_comparison.csv`, o baseline
`SAC` local aparece como `mode = no_sam`, e o `TA-SAM` principal aparece como
`mode = tasam_selective`.

Nos cenários mais úteis para comparação local, o quadro ficou assim:

| Cenário | SAC (`eval_return`) | TA-SAM (`eval_return`) | Diferença |
|---|---:|---:|---:|
| `equal` | 21.7010 | 21.9600 | `+1.19%` |
| `non_equal` | 21.7010 | 21.9803 | `+1.29%` |
| `dynamic` | 21.7010 | 20.0366 | `-7.67%` |

Leitura:

- em `equal`, o `TA-SAM` ficou levemente acima do `SAC`;
- em `non_equal`, o `TA-SAM` também ficou levemente acima do `SAC`;
- em `dynamic`, que é a forma mais próxima do agendamento do artigo
  (`rho 0.5 -> 0.01`), o `TA-SAM` ainda ficou abaixo do `SAC` no nosso cenário.

Mesmo quando o ganho em retorno foi pequeno, o `TA-SAM` mostrou política mais
estável que o baseline em `equal` e `non_equal`, com redução forte de
`action_var_mean`:

- `equal`: de `0.02978` para `0.00398`
- `non_equal`: de `0.02978` para `0.00444`

#### Resultado no treino online parcial de 60 episódios

No treino online fiel ao artigo, executado em
`runs/sac_bootstrap/online_tasam_marl`, o que existe hoje é apenas a trilha
`TA-SAM`. O resumo parcial dos `60 episódios` foi:

- `mean_return_all = 85.0772`
- `mean_return_last_10 = 87.1450`
- `best_episode_return = 88.3605`
- `last_episode_return = 86.1525`

Leitura:

- o `TA-SAM` mostrou aprendizado estável no nosso cenário GreenRAN atual;
- os últimos episódios ficaram acima da média geral, sinal de melhora ao longo
  do treino;
- esse recorte continua válido como evidência preliminar, mas não como
  comparação final `TA-SAM vs SAC`.

#### Rodada pareada 60 vs 60

Para fechar a comparação de forma justa, foi executada a rodada pareada em
`60 episódios` no mesmo cenário, mesma seed e mesmo pipeline:

- `SAC` em `runs/sac_bootstrap/online_sac_match_tasam_60ep`
- `TA-SAM` em `runs/sac_bootstrap/online_tasam_parallel_match60_v1`

Resumo consolidado:

| Método | `mean_return_last_100` | `final_eval.mean_return` |
|---|---:|---:|
| `SAC` | `89.4145` | `96.2304` |
| `TA-SAM` seed `42` | `84.2045` | `92.7165` |
| `TA-SAM` seed `43` | `84.9366` | `92.5664` |
| `TA-SAM` média | `84.5705` | `92.6415` |

Leitura direta:

- na janela dos últimos `100 passos`, o `SAC` ficou `+5.42%` acima da média
  dos dois `TA-SAM`;
- na avaliação final, o `SAC` ficou `+3.73%` acima da média dos dois `TA-SAM`;
- nas duas seeds do `TA-SAM`, o comportamento foi consistente, sem colapso,
  mas ainda abaixo do baseline `SAC`.

#### Por que o SAC ganhou

Os dados apontam que a diferença não veio de um `critic` pior no `TA-SAM`.
O padrão foi outro:

- `critic_loss` médio menor no `TA-SAM` que no `SAC`:
  - `SAC = 3.9841`
  - `TA-SAM seed 42 = 1.1582`
  - `TA-SAM seed 43 = 1.2763`
- `td_var_mean` médio também menor no `TA-SAM`, sinal de atualização mais
  conservadora:
  - `SAC = 1.9778`
  - `TA-SAM seed 42 = 0.5730`
  - `TA-SAM seed 43 = 0.6311`
- `action_var_mean` muito mais baixo no `TA-SAM`, sugerindo política mais
  restrita:
  - `SAC = 0.0172`
  - `TA-SAM seed 42 = 0.0041`
  - `TA-SAM seed 43 = 0.0111`

Por faixa de episódio, o comportamento também foi claro:

- o `SAC` evoluiu de `83.92` nos `10` primeiros episódios para `92.32` nos
  `10` últimos;
- o `TA-SAM` evoluiu menos e ficou em torno de `84.35` a `84.61` no fim;
- a diferença cresceu na metade final do treino.

Leitura técnica:

- o `TA-SAM` ficou mais estável, mas também mais conservador;
- `selected_fraction = 1.0` nas duas seeds mostra que o modo seletivo não foi
  realmente seletivo nesta corrida;
- com `rho` ainda agressivo no começo e `action_var_mean` baixo, o `TA-SAM`
  perdeu capacidade de exploração e não atingiu o retorno do `SAC`.

Conclusão da comparação online:

- `no artigo`, o `TA-SAM` vence o `SAC` com folga;
- `no nosso replay controlado`, o `TA-SAM` já vence o `SAC` em `equal` e
  `non_equal`, mas ainda perde em `dynamic`;
- `no nosso online match60`, o `SAC` venceu o `TA-SAM` por margem moderada.

### 8.7 Resumo das rodadas online

Para fechar a leitura, a tabela abaixo concentra as rodadas online que
importam para o cenário atual:

| Rodada | Métrica principal | Valor | Leitura |
|---|---|---:|---|
| `TA-SAM parcial 60` | `mean_return_all` | `85.08` | Evidência inicial boa, mas ainda preliminar. |
| `SAC 60` | `mean_return_last_100` / `final_eval.mean_return` | `89.41` / `96.23` | Melhor baseline online do cenário atual. |
| `TA-SAM match60` | Média das duas seeds | `84.57` / `92.64` | Rodada justa, mas abaixo do `SAC`. |
| `TA-SAM selective_v3` | Média das duas seeds | `84.57` / `92.64` | Mesma leitura do `match60`; seletividade não mudou o resultado. |

Leitura final:

- o `TA-SAM` antigo continua válido como evidência preliminar;
- o `SAC 60` foi o melhor resultado online na comparação justa;
- as variantes `match60` e `selective_v3` do `TA-SAM` ficaram estáveis, mas
  não superaram o `SAC`.

### 8.8 Leitura final da simulação TA-SAM

O resultado principal da trilha `TA-SAM` foi:

- vantagem final de `27%` sobre o `SAC` no `q0`;
- vantagem global de `12%`;
- redução clara de `catastrophic forgetting`;
- aumento do `alpha` final, indicando dinâmica de exploração diferente do baseline.

Em resumo, a reprodução do artigo confirmou a superioridade do `TA-SAM` sobre
o `SAC` na referência externa, enquanto o nosso `online match60` mostrou que,
no cenário GreenRAN atual, o `SAC` ainda ficou à frente. O treino `online`
continua útil como evidência de comportamento, mas a vantagem do artigo ainda
não apareceu de forma consistente no cenário atual.

---

## 9. Conclusão unificada

As duas simulações cumprem papéis complementares no GreenRAN:

- `ARMD-GreenRAN` foi a trilha mais forte para aprendizado e reconstrução de
  conflitos, com `F1 = 1.0` no melhor caso geral e desempenho superior à
  referência com apenas `200 épocas`.
- `TA-SAM` foi a trilha de política de controle, com vantagem clara no artigo
  e nos cenários controlados `equal`/`non_equal`, mas perdeu para `SAC` no
  `online match60` do cenário GreenRAN atual.

Em termos de arquitetura, o sistema final fica coerente assim:

- `ARMD-GreenRAN` interpreta e aprende os conflitos;
- `TA-SAM` aprende a política multiagente de alocação;
- ambos operam dentro da mesma arquitetura `O-RAN` do GreenRAN.

---

## 10. Fontes técnicas usadas para este consolidado

- `config/greenran_fixed_scenario.json`
- `reports/article00/aggregate_report.json`
- `reports/article00/article00_final_reference_summary.json`
- `reports/experimentos_conflitos/aggregate_report.json`
- `runs/external_references/resultados_experimentos_tasam/tasam_external_article_reference.json`
- `config/core/runtime.json`
- `Downloads/relatorio_metodologia_tasam.md`
- `runs/sac_bootstrap/online_tasam_marl/online_tasam_marl_partial_summary.json`
- `runs/sac_bootstrap/online_tasam_marl/online_tasam_marl_partial_training_curves.png`
- `runs/sac_bootstrap/online_tasam_parallel/parallel_launch_manifest.json`
