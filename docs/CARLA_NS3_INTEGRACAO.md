# Integracao CARLA + ns-3 + GreenRAN

## Objetivo

Este documento define a trilha recomendada para integrar:

- `CARLA` como simulador veicular e ambiental;
- `ns-3` como simulador de rede;
- `GreenRAN` como camada de observabilidade, xApps e decisao do `rApp`.

O objetivo nao e substituir o `ns-3`, e sim complementar o projeto com:

- veiculos dirigidos por rota;
- ambiente viario mais realista;
- telemetria veicular mais rica;
- decisao de rede sensivel ao contexto de conducao.

## Estado atual do repositorio

Hoje o projeto ja possui:

- cenario `ns-3` principal em `scenario-greenran.cc`;
- coletor de metricas em `src/csv_to_metrics.py`;
- `rApp` em `src/rapp_orchestrator.py`;
- `App1` e `App2` operacionais;
- carros no `ns-3`, mas ainda como UEs moveis simples, sem autonomia real.

Hoje o projeto **nao possui**:

- bridge com `CARLA`;
- sincronizacao de tempo `CARLA <-> ns-3`;
- ingestao de estado do ego-veiculo a partir do `CARLA`;
- telemetria de controle autonomo integrada ao `rApp`.

## Principio de arquitetura

A arquitetura recomendada e:

1. `CARLA` governa o mundo fisico dos veiculos:
   - pose;
   - velocidade;
   - rota;
   - obstaculos;
   - atores da cena.
2. `ns-3` governa a rede:
   - latencia;
   - perda;
   - throughput;
   - handover;
   - qualidade de enlace.
3. o `GreenRAN` governa a decisao:
   - protecao de SLA;
   - politica de energia;
   - priorizacao de trafego;
   - interpretacao de contexto veicular.

## Arquitetura minima

```text
CARLA
  -> estado dos veiculos
  -> eventos de mobilidade e risco

Bridge CARLA/ns-3
  -> traduz atores do CARLA em entidades de rede e telemetria
  -> sincroniza tick do CARLA com janelas do ns-3

ns-3
  -> calcula metricas de rede por veiculo
  -> exporta traces

csv_to_metrics.py
  -> unifica metricas de rede + contexto veicular

rApp / Data Lake / Apps
  -> consomem o estado combinado
  -> decidem prioridade, economia e politicas
```

## Modos de execucao

Hoje a integracao suporta dois modos:

- `mock`
  - gera `5` veiculos sinteticos;
  - e o modo mais estavel para desenvolvimento local.
- `carla`
  - consome um servidor `CARLA` real em `host:porta`.

Wrapper principal:

- `scripts/run_greenran_carla_ns3.sh`

Variaveis uteis:

- `GREENRAN_CARLA_MODE=mock|carla`
- `GREENRAN_CARLA_HOST=127.0.0.1`
- `GREENRAN_CARLA_PORT=2000`
- `GREENRAN_CARLA_FALLBACK_TO_MOCK=1`

Comportamento de fallback:

- se `GREENRAN_CARLA_MODE=carla` e o servidor RPC nao responder em `host:porta`,
  o wrapper cai automaticamente para `mock` por padrao;
- para desabilitar isso e falhar de forma explicita:

```bash
GREENRAN_CARLA_MODE=carla GREENRAN_CARLA_FALLBACK_TO_MOCK=0 ./scripts/run_greenran_carla_ns3.sh
```

## Escopo minimo viavel

### Fase 1: Veiculos conectados dirigidos por rota

Entregas:

- usar o `CARLA` para gerar:
  - posicao;
  - heading;
  - velocidade;
  - waypoint atual;
  - estado de obstaculo simples.
- manter o `ns-3` como simulador de rede.
- mapear cada veiculo do `CARLA` para um `vehicle_id` conhecido pelo `ns-3`.
- exportar metadados de veiculo para o pipeline do `GreenRAN`.

Status no repositorio:

- implementada;
- bridge em `src/carla_bridge.py`;
- mapeamento em `src/carla_ns3_mapper.py`;
- wrapper em `scripts/run_greenran_carla_ns3.sh`;
- enriquecimento do coletor em `src/csv_to_metrics.py`.

Execucao local recomendada:

```bash
./scripts/run_greenran_carla_ns3.sh
```

Execucao com CARLA remoto:

```bash
GREENRAN_CARLA_MODE=carla \
GREENRAN_CARLA_HOST=<ip-do-servidor-carla> \
GREENRAN_CARLA_PORT=2000 \
./scripts/run_greenran_carla_ns3.sh
```

Nao inclui ainda:

- controle autonomo completo;
- decisao visual fim a fim;
- fusao de sensores complexa;
- V2X full stack.

### Fase 2: Veiculos com risco operacional

Entregas:

- calcular por veiculo:
  - distancia a obstaculo;
  - velocidade relativa;
  - risco local;
  - necessidade de prioridade.
- fazer o `rApp` enxergar `vehicle` como classe propria.
- politicas separadas para:
  - camera;
  - sensor;
  - veiculo.

Status no repositorio:

- implementada no `rApp`;
- leitura de `vehicle_metrics` em `src/rapp_orchestrator.py`;
- regras:
  - `VEHICLE_CRITICAL` -> `BLOCKED` + `FULL_POWER`
  - `VEHICLE_WARNING` -> `CONDITIONAL` + `FULL_POWER_GUARD`

Validacao rapida sem subir runtime:

```bash
python3 ./scripts/test_rapp_vehicle_mock.py
```

O script imprime 3 casos:

- `healthy`
- `warning`
- `critical`

### Fase 3: Conducao assistida por rede

Entregas:

- feed de comandos ou alertas para o veiculo:
  - prioridade;
  - modo seguro;
  - degradacao de autonomia;
  - perda de conectividade critica.

## Componentes novos

### 1. Bridge CARLA -> GreenRAN

Arquivo sugerido:

- `src/carla_bridge.py`

Responsabilidade:

- conectar no servidor do `CARLA`;
- ler atores veiculares;
- serializar um snapshot periodico.

Saida sugerida:

- `/tmp/carla_state/vehicles.json`

Formato base:

```json
{
  "timestamp": 0.0,
  "vehicles": [
    {
      "vehicle_id": "veh-01",
      "role": "ego",
      "x": 0.0,
      "y": 0.0,
      "z": 0.0,
      "speed_mps": 0.0,
      "heading_deg": 0.0,
      "lane_id": "lane-01",
      "waypoint_id": "wp-101",
      "autonomy_state": "normal",
      "risk_state": "low"
    }
  ]
}
```

### 2. Mapeador CARLA -> ns-3

Arquivo sugerido:

- `src/carla_ns3_mapper.py`

Responsabilidade:

- mapear `vehicle_id` do `CARLA` para `IMSI`/`UE` do `ns-3`;
- preservar identidade entre sim fisica e sim de rede.

Saida sugerida:

- `/tmp/carla_state/vehicle_network_map.json`

### 3. Enriquecimento do coletor

Arquivo a ampliar:

- `src/csv_to_metrics.py`

Responsabilidade nova:

- ler o estado do `CARLA`;
- juntar:
  - metricas de rede do `ns-3`
  - contexto fisico do `CARLA`
- exportar `device_type = vehicle`.

Campos sugeridos por veiculo:

- `vehicle_id`
- `role`
- `speed_mps`
- `heading_deg`
- `lane_id`
- `autonomy_state`
- `risk_state`
- `distance_to_obstacle_m`
- `network_latency_ms`
- `packet_loss_percent`
- `throughput_mbps`

### 4. Politicas no rApp

Arquivo a ampliar:

- `src/rapp_orchestrator.py`

Responsabilidade nova:

- incluir regras para `vehicle`.

Exemplos:

- veiculo em `risk_state = high`:
  - bloquear economia agressiva;
  - elevar prioridade de rede.
- latencia alta para veiculo critico:
  - forcar `FULL_POWER`
  - e reforcar fatia prioritaria.

Estado atual:

- a regra veicular ja entra na hierarquia abaixo de `camera` e acima de `App2`;
- snapshots stale geram `warning`;
- `risk_state=high`, autonomia degradada, latencia alta ou perda alta geram `blocked`.

## Sincronizacao recomendada

Nao recomendamos sincronizacao forte de simulador por evento logo no inicio.

A primeira versao deve usar sincronizacao fraca por janelas:

- `CARLA` produz snapshots a cada `100-200 ms`;
- `ns-3` continua produzindo traces;
- `csv_to_metrics.py` agrega por janela temporal.

Isso simplifica:

- implementacao;
- depuracao;
- estabilidade do runtime.

## Modelo operacional recomendado

### O que o CARLA fornece

- geometria e dinamica dos veiculos;
- cena viaria;
- rota;
- obstaculos;
- estado do ego-veiculo.

### O que o ns-3 fornece

- latencia;
- perda;
- throughput;
- qualidade de enlace;
- impacto de concorrencia e congestionamento.

### O que o GreenRAN decide

- quando proteger veiculos;
- quando manter economia;
- quando priorizar trafego;
- quando degradar modo autonomo por risco de rede.

## Primeira implementacao recomendada

1. criar `carla_bridge.py`;
2. criar `vehicle_network_map.json`;
3. ampliar `csv_to_metrics.py` para `device_type = vehicle`;
4. ampliar `rapp_orchestrator.py` com regras minimas de veiculo;
5. criar wrapper:
   - `scripts/run_greenran_carla_ns3.sh`

## O que nao fazer agora

- nao tentar autonomia visual completa;
- nao tentar replicar `UniAD` no projeto;
- nao tentar sincronizacao forte `tick-by-tick`;
- nao tentar `LoRa/NTN/V2X` completos na primeira iteracao.

## Veredito

A integracao `CARLA + ns-3` e viavel e faz sentido para o projeto.

O caminho correto e:

- `CARLA` para mundo fisico dos veiculos;
- `ns-3` para rede;
- `GreenRAN` para politica e decisao.

O primeiro marco tecnico deve ser **veiculo conectado por rota com contexto combinado de mobilidade + rede**, nao autonomia completa.
