# Analise do App1 e App2

## Objetivo

Este documento resume o estado atual dos dois aplicativos principais do GreenRAN:

- `App1-Vigilancia`
- `App2-Monitoramento`

O foco aqui nao e detalhar todo o codigo, e sim registrar:

- papel de cada app;
- grau de maturidade;
- integracao com GreenRAN/ns-3;
- pontos fortes;
- limites atuais.

## Localizacao no codigo

### App1

- backend principal: `apps/app1_vigilancia/backend/app.py`
- servicos: `apps/app1_vigilancia/backend/services.py`
- testes: `apps/app1_vigilancia/tests/test_app1_api.py`

### App2

- backend principal: `apps/app2_monitoramento/backend/app.py`
- servicos: `apps/app2_monitoramento/backend/services.py`
- testes: `apps/app2_monitoramento/tests/test_app2_api.py`

## Resumo executivo

| Criterio | App1 | App2 |
|---|---|---|
| Objetivo | Vigilancia com cameras e eventos | Monitoramento ambiental e sensores |
| Maturidade | Mais alto | Medio |
| Integracao com GreenRAN | Forte | Boa |
| Dados | Video, eventos, snapshot por camera | Sensores, snapshot ambiental, SLA |
| Estado atual | Mais proximo de produto | Mais proximo de MVP operacional |
| Maior influencia no rApp hoje | Sim | Nao, exceto sob degradacao dos sensores |

## App1

### O que ele faz

O `App1` representa o caso de uso de vigilancia. Ele concentra:

- cadastro e configuracao de cameras;
- ingestao e organizacao de midia;
- geracao de eventos;
- snapshot operacional por camera;
- exposicao de APIs e interface visual mais rica.

### Pontos fortes

- e o app mais completo do projeto;
- tem melhor acabamento funcional;
- conversa melhor com a narrativa principal do GreenRAN;
- hoje e o app que mais pesa nas decisoes do `rApp`;
- possui backend, frontend e testes mais consolidados.

### Limites atuais

- a parte de IA ainda esta mais simulada/orquestrada do que um pipeline de visao computacional de producao;
- o app e forte como demonstracao de sistema, mas ainda nao representa um stack real de inferencia pesada ponta a ponta.

## App2

### O que ele faz

O `App2` representa o monitoramento ambiental/mMTC. Ele concentra:

- snapshot ambiental agregado;
- lista de sensores;
- avaliacao de SLA dos sensores;
- historico e relatorios operacionais.

### Pontos fortes

- ficou tecnicamente muito melhor apos a integracao com sensores reais do pipeline `ns-3`;
- hoje ja exporta sensores com semantica coerente;
- o snapshot operacional ficou consistente para o `rApp`;
- a logica de `warning` e `blocked` ja funciona.

### Estado atual dos sensores

No estado atual, o `App2` opera com:

- `17` sensores;
- tipos semanticos reais, por exemplo:
  - `temperature`
  - `humidity`
  - `soil_moisture`
  - `soil_temp`
  - `soil_conductivity`
- conectividades:
  - `5g_native`
  - `5g_redcap`
- gateways:
  - `GW-5G-01`
  - `GW-5G-02`
  - `GW-5G-03`

O snapshot atual do App2 ja reflete um estado coerente de rede e disponibilidade.

### Limites atuais

- ainda e um app mais enxuto;
- continua mais focado em monitoramento e SLA do que em analitica mais profunda por sensor;
- tem menos profundidade de produto do que o `App1`.

## Relacao com o rApp

### Matriz de prioridade e SLA

Hoje a hierarquia pratica do `rApp` e:

1. `camera` (`App1`)
2. `vehicle` (`App3` / trilha veicular)
3. `sensors` (`App2`)
4. economia geral

| Dominio | Prioridade | Sinais principais | Critico | Guarda / Warning |
|---|---|---|---|---|
| `camera` | 1 | throughput + latencia | throughput `< 25 Mbps` ou latencia `>= 80 ms` | throughput em `25-30 Mbps` ou latencia em `60-80 ms` |
| `vehicle` | 2 | risco + autonomia + latencia + perda | `risk_state=high`, `autonomy_state!=normal`, latencia `>= 100 ms`, perda `>= 5%` | `risk_state=medium`, latencia `>= 50 ms`, perda `>= 2%` |
| `sensors` (`App2`) | 3 | conectividade + entrega + latencia + bateria | conectividade `< 85%`, perda `>= 10%`, entrega `< 90%`, latencia `>= 1000 ms`, bateria `< 15%` | conectividade `< 90%`, perda `>= 5%`, entrega `< 95%`, latencia `>= 500 ms`, bateria `< 25%` |

Leitura pratica:

- cameras continuam com prioridade maxima;
- veiculos entram logo abaixo das cameras;
- sensores do `App2` continuam relevantes, mas so decidem quando camera e vehicle estao saudaveis.

### App1

O `App1` domina a decisao do `rApp` na maior parte dos cenarios atuais porque:

- o cenario principal e orientado a cameras;
- as regras do orquestrador priorizam protecao de SLA das cameras.

### App2

O `App2` ja influencia a decisao quando os sensores degradam. Em particular:

- pode levar o sistema para `warning`;
- pode levar o sistema para `blocked`;
- mas em estado saudavel tende a ficar em segundo plano diante das cameras.

## Veredito

### App1

- mais maduro;
- mais forte como demonstracao principal;
- mais proximo de uma aplicacao final do projeto.

### App2

- tecnicamente correto e integrado;
- operacionalmente util;
- ainda mais MVP do que produto final.

## Resumo para apresentacao

- O `App1-Vigilancia` e hoje o app mais maduro do projeto e o principal driver das decisoes do `rApp`, porque o cenario atual e centrado em cameras e SLA de video.
- O `App2-Monitoramento` evoluiu de um mock para uma integracao real com sensores vindos do pipeline `ns-3`, com `17` sensores, tipos semanticos corretos e snapshot operacional coerente.
- O `App1` esta mais proximo de um produto final; o `App2` esta tecnicamente correto e funcional, mas ainda mais proximo de um MVP operacional.
- O `App2` ja consegue influenciar o `rApp` em estados de degradacao (`warning` e `blocked`), embora no cenario atual as cameras ainda dominem a logica de decisao.
- A proxima evolucao natural e fortalecer a IA real no `App1` e aprofundar a analitica por sensor no `App2`.

## Proximos passos recomendados

### App1

- fortalecer a parte de inferencia real de video;
- aproximar a camada de IA de um pipeline menos simulado.

### App2

- enriquecer analise por sensor;
- introduzir anomalia e correlacao temporal mais detalhadas;
- ampliar o peso do App2 em cenarios onde sensores tambem sejam gargalo real.
