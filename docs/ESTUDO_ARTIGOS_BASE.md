# Estudo dos Artigos Base - GreenRAN UFPA

## Arquivos analisados

- `artigo00.pdf`: "Learning and Reconstructing Conflicts in O-RAN: A Graph Neural Network Approach", arXiv:2412.14119v2, 2025.
- `artigo01pdf`: "Conflict Detection in AI-RAN: Efficient Interaction Learning and Autonomous Graph Reconstruction", arXiv:2601.13213v2, 2026.
- Documentos de contexto: `19175_Proposta_Ajustada (3).pdf` e `19175_Apresentacao_dos_Casos_de_Uso (1).pdf`.

Os dois artigos-base nao tratam diretamente de cameras ou sensores. Eles tratam do problema estrutural que aparece no nosso projeto: multiplos xApps/rApps/agentes de IA podem alterar parametros da RAN para objetivos diferentes e gerar conflitos.

## Artigo00

### Ideia central

O artigo00 propoe reconstruir grafos de conflito em O-RAN usando Graph Neural Networks, especificamente GraphSAGE. O objetivo e aprender relacoes ocultas entre:

- xApps/agentes;
- parametros de controle da RAN;
- KPIs observados.

A motivacao e que muitos conflitos nao sao conhecidos antes da execucao. O relacionamento "xApp controla parametro" costuma ser conhecido pelo processo de subscription, mas o relacionamento "parametro afeta KPI" pode ser dinamico, nao-linear e dependente do cenario.

### Tipos de conflito

- Conflito direto: dois xApps tentam modificar o mesmo parametro.
- Conflito indireto: dois xApps modificam parametros diferentes que afetam o mesmo KPI.
- Conflito implicito: um xApp altera um parametro que muda um KPI usado por outro xApp para decidir uma segunda acao.

### Resultado tecnico

O artigo mostra que a reconstrucao do grafo permite identificar conflitos diretos, indiretos e implicitos. Ele tambem aponta limitacoes: abordagens data-driven podem gerar falsos negativos quando um xApp observa um KPI, mas nao o usa ativamente para modificar parametros.

### Parametros experimentais de referencia

No recorte que usamos como base metodologica, os numeros mais importantes do
artigo sao:

- tamanhos de dataset: `50`, `150`, `450`;
- threshold de reconstrucao: `0.5`;
- referencia de treino GNN: `600 epochs`.

No GreenRAN atual, os dois primeiros pontos ja foram incorporados ao protocolo
experimental. O terceiro ainda nao, porque o learner presente hoje nao faz
treino `GraphSAGE`; ele reconstrui o grafo por heuristica/estatistica.

### Implicacao para o GreenRAN

Nosso sistema tem exatamente a classe de conflito que o artigo descreve:

- `xApp1-RANSlicer` tenta preservar throughput/latencia dos slices.
- `xApp2-EnergySaver` tenta reduzir potencia/recursos.
- ML/DRL podem sugerir economia quando a rede parece saudavel globalmente.
- App1 e App2 possuem KPIs proprios que podem ser degradados por decisoes de energia.

Portanto, o rApp precisa funcionar como arbitro explicito de conflitos. A regra implementada no nosso rApp, em que camera e App2 bloqueiam ML/DRL/energia quando entram em risco, e coerente com o artigo00.

### Como o protocolo experimental atual se conecta ao artigo00

O projeto agora possui um runner proprio para coleta comparativa:

- `scripts/run_article00_experiments.py`

Esse runner:

- coleta por rodadas com janela temporal exata;
- exporta dataset e grafo sem misturar rodadas;
- aprende a matriz por rodada e por cenario;
- cria subsets `50`, `150` e `450`;
- suporta `--auto` para execucao sem prompts;
- suporta `--auto-switch` para trocar os cenarios automaticamente.

O `--auto-switch` escreve `/tmp/article00_scenario_control.json` e altera os
componentes abaixo para que o cenario mude de verdade durante o experimento:

- `src/rapp_orchestrator.py`
- `apps/app1_vigilancia/backend/services.py`
- `apps/app2_monitoramento/backend/simulate_sensors.py`

Isso permite automatizar:

- baseline saudavel;
- App1 degradado por throughput;
- App1 degradado por latencia;
- App2 degradado leve;
- App2 degradado critico;
- conflito implicito;
- recuperacao.

Importante: essa automacao atua no cenario logico de App1/App2/rApp. Ela nao
reprograma o `ns-3` em tempo real. Para a comparacao atual com o artigo, isso
e aceitavel porque o foco esta na deteccao e reconstrucao de conflitos.

## Artigo01

### Ideia central

O artigo01 generaliza o problema para AI-RAN e decompoe a deteccao de conflitos em tres etapas:

1. Interaction learning.
2. Graph reconstruction.
3. Conflict identification.

Ele substitui a abordagem GNN pesada por uma arquitetura two-tower encoder para aprender interacoes entre parametros e KPIs. Tambem usa uma abordagem baseada em sparsity para reconstruir o grafo sem ajuste manual de limiares.

### Ponto mais importante para o nosso projeto

O artigo afirma que essa deteccao de conflitos pode ser implementada como um ou mais rApps, ou como modulo nativo no Non-RT RIC, usando telemetria da RAN para aprender interacoes, reconstruir grafos e gerar alarmes ou politicas de mitigacao.

Isso encaixa diretamente com o nosso `rApp-ResourceOptimizer`.

### Resultado tecnico

O artigo mostra que a abordagem two-tower + sparsemax acelera a reconstrucao do grafo e a deteccao de conflitos em comparacao com GNNs e metodos com thresholds manuais. O ganho reportado inclui convergencia mais rapida para reconstrucao de grafos e identificacao de conflitos implicitos.

### Implicacao para o GreenRAN

Hoje nosso rApp usa regras explicitas de prioridade. O proximo passo cientificamente alinhado com o artigo01 e registrar historico suficiente para aprender automaticamente quais acoes geram degradacao:

- energia reduzida -> throughput de camera cai;
- PRB/slice alterado -> packet loss App2 sobe;
- economia ativada -> CVaR permanece bom, mas KPI de aplicacao piora;
- App2 degradado -> gateway/sensores perdem conectividade.

Com isso, poderiamos evoluir de regras fixas para um modulo de conflito que aprende relacoes parametro-KPI a partir dos logs.

## Mapeamento para o nosso rApp

### Agentes/xApps

- `xApp1-RANSlicer`: controla alocacao de recursos/slice.
- `xApp2-EnergySaver`: controla reducao de potencia, full power, eco mode.
- `rApp-ResourceOptimizer`: coordena as decisoes e arbitra conflitos.
- ML/DRL/Pattern Engine: agentes internos de recomendacao.

### Parametros de controle

- potencia/estado do EnergySaver;
- acao de economia: `POWER_DOWN`, `POWER_DOWN_ECO`, `FULL_POWER`, `FULL_POWER_GUARD`;
- estado de slice;
- possivel alocacao de PRBs/MCS em extensoes futuras.

### KPIs

- camera throughput minimo por camera;
- camera worst latency;
- App2 connected ratio;
- App2 packet loss;
- App2 delivery success;
- App2 average latency;
- CVaR/P95/slope globais;
- consumo/energia.

### Conflitos presentes no nosso sistema

- Direto: duas politicas tentando controlar energia/potencia ao mesmo tempo.
- Indireto: EnergySaver reduz potencia para economizar, mas o KPI de throughput de camera cai.
- Implicito: CVaR global esta bom, ML recomenda economia, mas uma camera especifica entra abaixo de 25 Mbps; o KPI global mascara degradacao de aplicacao.

## Hierarquia correta de decisao

1. App1-Vigilancia / eMBB cameras.
2. App2-Monitoramento / mMTC sensores e gateways.
3. Saude global da rede: CVaR, P95, slope, packet loss geral.
4. Pattern Engine, ML e DRL.
5. Economia de energia.

Essa ordem nao e apenas uma escolha operacional. Ela e uma politica de mitigacao de conflitos: quando um KPI de aplicacao critica esta em risco, agentes de otimizacao energetica e modelos preditivos nao podem sobrescrever a decisao.

## App1 segundo a proposta

Requisito forte:

- Cada camera 4K/H.265 demanda em media 25 Mbps.
- Latencia deve ficar abaixo de 100 ms.
- Como a latencia e instavel, usamos faixa de guarda:
  - `>=80 ms`: block.
  - `60-80 ms`: guard.
- Para throughput, usamos:
  - `<25 Mbps`: block.
  - `25-30 Mbps`: guard.
  - `>=30 Mbps`: liberado para outras logicas.

Na linguagem dos artigos, esses KPIs de App1 sao restricoes de seguranca que devem alimentar o grafo de conflito. Se uma acao de energia ou slicing degrada esses KPIs, o conflito precisa ser detectado ou mitigado.

## App2 segundo a proposta

Os sensores nao devem ser tratados como UEs individuais. A proposta fala em sensores IoT com RedCap, FWA, LoRa/BR5G-Gateways e conectividade agregada.

Metricas corretas para App2:

- sensores conectados;
- taxa de perda de pacotes;
- sucesso de entrega;
- consumo/bateria;
- latencia media por gateway;
- RSSI/utilizacao;
- anomalias ambientais ou de solo.

Regras atuais coerentes:

- guarda se conectividade `<95%`, packet loss `>=5%`, entrega `<95%`, latencia `>=500 ms`;
- bloqueio se conectividade `<85%`, packet loss `>=10%`, entrega `<90%`, latencia `>=1000 ms`.

Na linguagem dos artigos, App2 representa um conjunto de KPIs mMTC que podem entrar em conflito indireto com economia de energia e com alocacao de recursos para eMBB.

## Ajustes recomendados no projeto

- Registrar `priority_violation` como campo persistente no banco, nao apenas no texto do motivo.
- Criar uma tabela de eventos de conflito: `agent`, `action`, `parameter`, `affected_kpi`, `before`, `after`, `conflict_type`.
- Adicionar no dashboard uma visao "Conflitos O-RAN": direto, indireto e implicito.
- Tratar o conflito `EnergySaver vs Camera Throughput` como caso principal de demonstracao.
- Tratar o conflito `EnergySaver/Slicing vs App2 Packet Loss` como segundo caso.
- Criar testes unitarios do arbitro:
  - camera critica vence App2, CVaR, ML e DRL;
  - App2 critico vence CVaR, ML e DRL;
  - ML/DRL so influenciam quando nao existe conflito prioritario.
- Evoluir futuramente para um modulo aprendido inspirado no artigo01:
  - coletar historico de parametros e KPIs;
  - aprender interacoes parametro-KPI;
  - reconstruir grafo de conflito;
  - emitir alerta/politica de mitigacao no rApp.

## Documentacao operacional complementar

Para executar e interpretar a coleta alinhada ao artigo00:

- `docs/CONFLICT_DATASET_PIPELINE.md`
- `docs/ARTICLE00_EXPERIMENTOS.md`
