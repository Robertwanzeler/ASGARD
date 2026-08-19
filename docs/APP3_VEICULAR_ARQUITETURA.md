# App3 Veicular - Arquitetura Proposta

## Objetivo

Este documento define a arquitetura recomendada para um novo aplicativo:

- `App3-Veicular`

O objetivo do `App3` e representar o dominio de:

- veiculos conectados;
- risco operacional;
- autonomia assistida por rede;
- prioridade de SLA para trafego veicular.

Ele nao substitui:

- `App1-Vigilancia`
- `App2-Monitoramento`

Ele adiciona um terceiro dominio ao GreenRAN.

## Porque um App3

Veiculo autonomo/conectado nao encaixa bem em:

- `App1`, que e orientado a cameras, eventos de vigilancia e SLA de video;
- `App2`, que e orientado a sensores e monitoramento ambiental.

O `App3` isola uma logica nova:

- estado por veiculo;
- `ego vehicle`;
- risco;
- autonomia;
- relacao entre rede e conducao;
- eventos de seguranca veicular.

## Separacao entre App e xApp

Arquitetura recomendada:

- `App3-Veicular`
  - contexto de alto nivel;
  - estado dos veiculos;
  - visualizacao;
  - historico;
  - integracao com CARLA e estado combinado.
- `xApp-VehicleSafety`
  - atuacao Near-RT;
  - protecao de SLA veicular;
  - priorizacao rapida de rede;
  - reacao a degradacao critica.

Resumo:

- `App3` pensa o dominio;
- `xApp` reage perto do radio;
- `rApp` arbitra prioridade global.

## Posicionamento na arquitetura GreenRAN

```text
CARLA
  -> pose, lane, rota, waypoint, risco

ns-3
  -> throughput, latencia, perda, handover

csv_to_metrics.py
  -> unifica contexto fisico + rede

App3-Veicular
  -> estado e API veicular

rApp
  -> decide prioridade entre camera, vehicle e sensores

xApp-VehicleSafety
  -> executa politica de rede para veiculo critico
```

## Papel do App3

O `App3` deve concentrar:

- lista de veiculos ativos;
- identificacao do `ego`;
- estado de risco:
  - `low`
  - `medium`
  - `high`
- estado de autonomia:
  - `normal`
  - `degraded`
  - `manual`
- estado de conectividade;
- eventos de seguranca veicular;
- historico curto de degradacao.

## Dados minimos por veiculo

Campos minimos recomendados:

- `vehicle_id`
- `vehicle_role`
- `imsi`
- `x`
- `y`
- `z`
- `speed_mps`
- `heading_deg`
- `lane_id`
- `waypoint_id`
- `autonomy_state`
- `risk_state`
- `latency_ms`
- `packet_loss_percent`
- `throughput_mbps`

Campos derivados uteis:

- `is_ego`
- `network_state`
- `sla_state`
- `priority_required`
- `last_update_seconds`

## Backend sugerido

Estrutura minima sugerida:

```text
apps/app3_veicular/
  backend/
    app.py
    services.py
    schemas.py
  tests/
    test_app3_api.py
```

### API minima sugerida

- `GET /api/vehicles`
  - lista veiculos ativos
- `GET /api/vehicles/ego`
  - estado do ego vehicle
- `GET /api/vehicles/summary`
  - resumo operacional
- `GET /api/vehicles/events`
  - eventos recentes
- `GET /api/vehicles/health`
  - saude do backend

## Snapshot operacional sugerido

Arquivo sugerido:

- `/tmp/app3_veicular/monitoring_snapshot.json`

Conteudo minimo:

```json
{
  "simulation": {
    "source": "carla_ns3_combined",
    "timestamp_iso": "2026-05-08 10:00:00"
  },
  "vehicles": {
    "total_vehicles": 5,
    "ego_present": true,
    "high_risk_vehicles": 0,
    "medium_risk_vehicles": 1,
    "degraded_autonomy_vehicles": 0
  },
  "network": {
    "max_latency_ms": 42.0,
    "max_packet_loss_percent": 0.8
  },
  "sla": {
    "runtime_status": "ok"
  }
}
```

## Fonte de dados

No estado atual do projeto, a melhor fonte para o `App3` e:

- `/tmp/xapp_metrics/extended_metrics.json`

porque ele ja contem:

- `device_type = vehicle`
- `vehicle_id`
- `vehicle_role`
- `speed_mps`
- `heading_deg`
- `lane_id`
- `waypoint_id`
- `autonomy_state`
- `risk_state`
- posicao
- metricas de rede por UE

Ou seja:

- o `App3` nao precisa falar direto com o `CARLA` no primeiro momento;
- ele pode nascer lendo o pipeline combinado que ja existe.

## Hierarquia de prioridade no rApp

A hierarquia recomendada fica:

1. `camera`
2. `vehicle`
3. `app2`
4. economia geral

### Matriz resumida de SLA por dominio

| Dominio | Sinais principais | Critico | Guarda / Warning |
|---|---|---|---|
| `camera` | throughput + latencia | throughput `< 25 Mbps` ou latencia `>= 80 ms` | throughput em `25-30 Mbps` ou latencia em `60-80 ms` |
| `vehicle` | risco + autonomia + latencia + perda | `risk_state=high`, `autonomy_state!=normal`, latencia `>= 100 ms`, perda `>= 5%` | `risk_state=medium`, latencia `>= 50 ms`, perda `>= 2%` |
| `app2` / sensores | conectividade + entrega + latencia + bateria | conectividade `< 85%`, perda `>= 10%`, entrega `< 90%`, latencia `>= 1000 ms`, bateria `< 15%` | conectividade `< 90%`, perda `>= 5%`, entrega `< 95%`, latencia `>= 500 ms`, bateria `< 25%` |

### Significado dos termos veiculares

- `risk_state=high`
  - o veiculo entrou em risco operacional alto;
  - indica um contexto que exige protecao imediata da rede;
  - exemplos futuros:
    - proximidade perigosa;
    - conflito de trajetoria;
    - evento critico de seguranca.

- `autonomy_state!=normal`
  - o modo de autonomia deixou de estar saudavel;
  - estados esperados:
    - `normal`
    - `degraded`
    - `manual`
  - se estiver diferente de `normal`, o `rApp` trata isso como degradacao do dominio veicular.

- `latencia >= 100 ms`
  - a comunicacao do veiculo esta lenta demais para o servico esperado;
  - isso aumenta o risco de atraso em telemetria, contexto e controle assistido por rede.

- `perda >= 5%`
  - pelo menos `5%` dos pacotes do veiculo estao sendo perdidos;
  - isso indica perda relevante de confiabilidade da comunicacao.

Motivo:

- camera protege SLA de video critico;
- vehicle protege seguranca e autonomia;
- App2 protege continuidade de monitoramento;
- economia entra por ultimo.

## Estados de decisao esperados

Para veiculo:

- `VEHICLE_WARNING`
  - `CONDITIONAL`
  - `FULL_POWER_GUARD`
- `VEHICLE_CRITICAL`
  - `BLOCKED`
  - `FULL_POWER`

Isso ja esta alinhado com a logica atual do `rApp`.

## O que vai para o xApp

O `xApp-VehicleSafety` deve receber do `rApp` algo como:

- prioridade de trafego;
- modo protegido;
- politica de latencia;
- reforco de fatia;
- bloqueio de economia agressiva.

O xApp nao precisa conhecer o dominio completo do veiculo. Ele precisa:

- reagir rapido;
- aplicar a politica.

## Adaptacao futura para ML e DRL

O dominio veicular pode ser incorporado no pipeline de:

- `ML` do `rApp`
- `DRL` do `rApp`

### Adaptacao para ML

Features naturais futuras:

- `total_vehicles`
- `ego_present`
- `high_risk_vehicles`
- `medium_risk_vehicles`
- `degraded_autonomy_vehicles`
- `vehicle_max_latency_ms`
- `vehicle_max_packet_loss_percent`
- `vehicle_max_speed_mps`

Objetivos possiveis:

- prever degradacao veicular antes do estado critico;
- modular a politica de energia com base no risco acumulado;
- aprender combinacoes entre camera, veiculo e sensores.

### Adaptacao para DRL

O estado da DRL pode ser expandido com:

- risco agregado dos veiculos;
- latencia maxima do `ego`;
- perda maxima veicular;
- quantidade de veiculos em `warning` ou `critical`;
- contexto combinado `camera + vehicle + app2`.

Objetivos possiveis:

- aprender niveis de energia mais conservadores quando o dominio veicular estiver sob risco;
- evitar degradacao de SLA antes de `VEHICLE_WARNING` ou `VEHICLE_CRITICAL`;
- tornar a politica mais sensivel ao `ego vehicle`.

### Regra de negocio recomendada

Mesmo com ML e DRL:

1. `camera`
2. `vehicle`
3. `app2`
4. economia geral

Ou seja:

- ML e DRL podem modular a decisao;
- mas nao devem quebrar a prioridade absoluta de camera nem a protecao forte do dominio veicular.

## Escopo minimo viavel

Primeira versao do `App3`:

- backend simples;
- leitura de `extended_metrics.json`;
- snapshot proprio;
- resumo por veiculo;
- um painel do ego vehicle;
- sem UI complexa no inicio.

## Proxima implementacao recomendada

Ordem sugerida:

1. criar `apps/app3_veicular/backend/app.py`
2. criar `apps/app3_veicular/backend/services.py`
3. gerar `/tmp/app3_veicular/monitoring_snapshot.json`
4. expor `GET /api/vehicles/summary`
5. integrar esse snapshot ao `rApp` e ao dashboard

## Veredito

Para tratar veiculo autonomo/conectado corretamente no GreenRAN:

- sim, vale criar um novo `App3`;
- sim, tambem vale uma trilha `xApp-VehicleSafety`;
- o `App3` deve nascer em cima do estado combinado `CARLA + ns-3` ja exportado pelo pipeline atual.
