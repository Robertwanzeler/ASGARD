# CARLA 2D - Modelo Veicular do GreenRAN

## Objetivo

Este documento fixa o modelo veicular **2D** que sera usado no projeto para a trilha:

- `CARLA + ns-3 + GreenRAN`

A ideia e:

- usar o `CARLA` como mundo fisico e simulador viario;
- projetar esse mundo em um estado **2D logico**;
- usar esse estado para decisao de rede, risco e prioridade.

Nao e objetivo desta trilha:

- direcao autonoma 3D completa;
- percepcao visual fim a fim;
- sensor fusion pesada;
- planner de conducao industrial.

## Porque 2D

Para o GreenRAN, o que mais importa nao e o volume 3D completo da cena. O que importa e:

- onde o veiculo esta;
- para onde vai;
- em que faixa esta;
- qual sua velocidade;
- qual seu risco;
- como esta a rede;
- se precisa prioridade de SLA.

Tudo isso pode ser modelado muito bem com uma camada 2D.

## Representacao base

Cada veiculo e descrito no plano por:

- `x`
- `y`
- `heading_deg`
- `speed_mps`
- `lane_id`
- `waypoint_id`

Campos de estado:

- `vehicle_id`
- `vehicle_role`
- `autonomy_state`
- `risk_state`

Campos de rede:

- `latency_ms`
- `packet_loss_percent`
- `throughput_mbps`

## Estado minimo por veiculo

Formato minimo recomendado:

```json
{
  "vehicle_id": "veh-01",
  "vehicle_role": "ego",
  "x": -120.0,
  "y": -40.0,
  "heading_deg": 18.0,
  "speed_mps": 6.5,
  "lane_id": "lane-west-01",
  "waypoint_id": "wp-west-101",
  "autonomy_state": "normal",
  "risk_state": "low",
  "latency_ms": 18.0,
  "packet_loss_percent": 0.2,
  "throughput_mbps": 6.1
}
```

## Camada de ocupacao 2D

O estado 2D nao precisa ser so uma lista de coordenadas. Ele pode ser visto como:

- posicoes continuas `x,y`, ou
- uma grade 2D de ocupacao.

Versao recomendada para a primeira iteracao:

- manter `x,y` continuos como fonte principal;
- derivar uma grade simples so quando necessario para risco local.

### Grade recomendada

- area operacional projetada no plano;
- grade, por exemplo, `20 x 20`;
- cada celula pode ter:
  - livre;
  - veiculo;
  - pedestre;
  - obstaculo;
  - area critica;
  - cobertura degradada.

## Entidades observadas

Na camada 2D, queremos representar:

- `ego vehicle`
- demais veiculos
- pedestres
- obstaculos simples
- zonas viarias relevantes

Em termos de prioridade:

1. `ego vehicle`
2. veiculos em risco
3. trafego ao redor
4. pedestres e obstaculos projetados

## Estados operacionais

### Estado de autonomia

Valores iniciais recomendados:

- `normal`
- `degraded`
- `manual`

### Estado de risco

Valores iniciais recomendados:

- `low`
- `medium`
- `high`

Heuristica inicial:

- `low`
  - sem conflito local forte
  - rede ok
- `medium`
  - degradacao moderada
  - risco local intermediario
- `high`
  - perigo operacional
  - autonomia degradada
  - rede insuficiente para operacao segura

## Eventos 2D relevantes

Eventos que a camada 2D deve produzir:

- saida de faixa logica
- aproximacao de obstaculo
- proximidade de pedestre
- degradacao de conectividade em rota critica
- latencia excessiva para o `ego`
- perda acima do limite para veiculo prioritario

Formato sugerido:

```json
{
  "event_type": "vehicle_warning",
  "vehicle_id": "veh-01",
  "severity": "warning",
  "reason": "latencia elevada em rota critica",
  "timestamp_iso": "2026-05-08 11:00:00"
}
```

## O que vem do CARLA

Do `CARLA`, a camada 2D deve herdar:

- pose do veiculo
- heading
- velocidade
- lane
- waypoint
- mapa viario
- papel do veiculo

Isso ja e suficiente para a primeira versao do GreenRAN veicular.

## O que vem do ns-3

Do `ns-3`, a camada 2D deve receber:

- latencia por veiculo
- perda por veiculo
- throughput por veiculo
- estado de conectividade
- eventuais sinais de degradacao

## Estado combinado

O modelo final usado por `App3`, `xApp-VehicleSafety` e `rApp` deve ser a fusao:

- `CARLA` -> contexto fisico 2D
- `ns-3` -> contexto de rede

Fonte recomendada no estado atual do projeto:

- `/tmp/xapp_metrics/extended_metrics.json`

Porque ele ja pode carregar:

- `device_type = vehicle`
- `vehicle_id`
- `vehicle_role`
- `x,y,z`
- `speed_mps`
- `heading_deg`
- `lane_id`
- `waypoint_id`
- `autonomy_state`
- `risk_state`
- metricas de rede

## Uso pelo App3

O `App3-Veicular` deve usar o estado 2D para:

- listar veiculos ativos
- destacar o `ego`
- mostrar risco atual
- mostrar estado de autonomia
- mostrar saude da rede por veiculo
- gerar snapshot operacional

## Uso pelo xApp-VehicleSafety

O `xApp` deve usar o estado 2D para:

- detectar prioridade de rede para veiculo
- pedir protecao de SLA
- sinalizar `warning` ou `critical`
- reagir mais rapido que o `App3`

## Uso pelo rApp

O `rApp` deve usar o estado 2D para arbitrar prioridade global:

1. camera
2. vehicle
3. app2
4. economia geral

Estados esperados:

- `VEHICLE_WARNING`
  - `CONDITIONAL`
  - `FULL_POWER_GUARD`
- `VEHICLE_CRITICAL`
  - `BLOCKED`
  - `FULL_POWER`

## O que se perde por nao ir para 3D completo

Perdas aceitas nesta estrategia:

- oclusao visual detalhada
- geometria 3D rica
- volume fino dos objetos
- manobras muito complexas
- planejamento visual pesado

Mas isso nao compromete o objetivo principal do projeto:

- rede sensivel ao contexto veicular
- prioridade de SLA
- risco operacional
- integracao `vehicle <-> ns-3 <-> rApp`

## Escopo minimo viavel

Primeira iteracao recomendada:

- `x,y`
- `speed_mps`
- `heading_deg`
- `lane_id`
- `waypoint_id`
- `autonomy_state`
- `risk_state`
- `latency_ms`
- `packet_loss_percent`

Sem:

- percepcao visual
- ocupacao 3D
- planner de conducao completo

## Proxima implementacao recomendada

Ordem:

1. consolidar `vehicle` no pipeline atual
2. criar `App3-Veicular`
3. criar `xApp-VehicleSafety`
4. derivar eventos 2D simples de risco
5. ligar esses eventos ao `rApp`

## Veredito

Para o GreenRAN, o modelo `CARLA + 2D` e a escolha mais racional:

- muito mais realista que `mock`;
- muito mais viavel que um stack 3D completo;
- suficiente para o objetivo de rede, risco e prioridade veicular.
