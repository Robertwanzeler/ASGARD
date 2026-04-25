# Arquitetura-Alvo do Projeto GreenRAN

## Objetivo

Transformar o repositório atual em uma entrega completa da proposta UFPA, com quatro blocos claramente separados:

1. `GreenRAN Core`
2. `App1-Vigilancia`
3. `App2-Monitoramento`
4. `Artefato Cientifico e Reprodutivel`

## 1. GreenRAN Core

Camada responsavel pela infraestrutura de orquestracao, observabilidade e controle da Open RAN.

### Escopo

- Coleta de metricas do `ns-3` e FlexRIC
- Data Lake e historico de decisoes
- Orquestracao `rApp -> xApps`
- Dashboard operacional
- Exportacao para Grafana/InfluxDB
- Predicao e suporte a decisao com RF/DRL
- Interface de intencoes e politica

### Modulos atuais

- `src/csv_to_metrics.py`
- `src/rapp_orchestrator.py`
- `src/rapp_data_lake.py`
- `src/rapp_dashboard.py`
- `src/rapp_a1_interface.py`
- `src/rapp_xapp_manager.py`
- `src/rapp_ml_predictor.py`
- `src/rapp_drl_predictor.py`
- `src/rapp_agent_openran.py`
- `scripts/smoke_drl_sequence.py`
- `scripts/summarize_drl_trace.py`

### Meta de pronto

- Execucao fim a fim com comando unico
- Configuracao centralizada
- Sem caminhos absolutos codificados
- Logs e resultados por experimento
- Dashboard consistente para demo
- DRL com warmup/control loop mais estavel sem alterar a politica do `rApp`

## 2. App1-Vigilancia

Aplicacao final voltada ao caso de uso de vigilancia do campus por video e IA.

### Requisitos funcionais

- Ingestao de video de teste ou stream
- Detecao de evento suspeito/violento
- Anonimizacao de faces por padrao
- Desanonimizacao condicional em evento validado
- Emissao de alerta
- Exposicao de estado e eventos para dashboard/API

### Requisitos de integracao com GreenRAN

- Medir relacao entre evento e condicao da rede
- Associar camera, taxa e latencia ao estado do `rApp`
- Permitir cenarios com SLA `< 100 ms`

### Entregavel tecnico minimo

- Backend da aplicacao
- Pipeline de eventos
- Endpoint/JSON de alertas
- Dashboard ou pagina dedicada

## 3. App2-Monitoramento

Aplicacao final voltada ao monitoramento ambiental e do solo.

### Requisitos funcionais

- Ingestao de dados de sensores reais ou simulados
- Suporte a multiplos tipos de sensores
- Deteccao simples de anomalias
- Emissao de alertas e relatorios
- Historico consultavel

### Requisitos de integracao com GreenRAN

- Relacionar conectividade e perda de pacotes com sensores
- Permitir cenarios mMTC e gateways de entrada
- Expor consumo/qualidade de servico por dispositivo ou grupo

### Entregavel tecnico minimo

- Backend da aplicacao
- Simulador ou ingestor de sensores
- Endpoint/JSON de metricas e alertas
- Dashboard ou pagina dedicada

## 4. Artefato Cientifico e Reprodutivel

Camada responsavel por transformar o projeto em artigo, demonstracao e experimento auditavel.

### Escopo

- Protocolo experimental fixo
- Seeds e configuracoes registradas
- Relatorios de execucao
- Tabelas de resultados
- Figuras reproduziveis
- Matriz `proposta -> implementacao`

## Estrutura de Diretorios Desejada

```text
apps/
  app1_vigilancia/
  app2_monitoramento/
  common/

config/
  app1/
  app2/
  core/

docs/
  ARQUITETURA_ALVO.md
  MATRIZ_PROPOSTA_IMPLEMENTACAO.md
  ROADMAP_EXECUCAO.md

runs/
  .gitkeep
```

## Roadmap de Implementacao

### Fase 1 - Core

- Centralizar configuracoes e caminhos
- Consolidar execucao do sistema
- Padronizar resultados por experimento

### Fase 2 - App1

- Scaffold backend
- Ingestao de video
- Eventos e alertas
- Integracao com GreenRAN

### Fase 3 - App2

- Scaffold backend
- Ingestao/simulacao de sensores
- Anomalias e alertas
- Integracao com GreenRAN

### Fase 4 - IA avancada

- Reposicionar `Agentic AI`
- Integrar DRL com criterio experimental
- Decidir o papel da trilha `two-tower/conflict`

### Fase 5 - Paper e demonstracao

- Resultados reproduziveis
- Tabelas e figuras
- Texto alinhado ao software existente
