# App2-Monitoramento

Aplicacao final de monitoramento ambiental e do solo.

## Escopo inicial

- ingestao de sensores reais ou simulados
- consolidacao de metricas
- deteccao de anomalias
- emissao de alerta
- relatorios simples
- integracao com metricas e estado do GreenRAN

## Proxima estrutura prevista

```text
apps/app2_monitoramento/
  README.md
  backend/
  simulators/
  static/
  templates/
  tests/
```

## MVP implementado

- backend Flask da aplicacao
- persistencia simples de leituras e alertas
- leitura de contexto do GreenRAN
- API de leituras e alertas
- pagina web basica da App2
- avaliacao inicial de anomalias por limiar

## Como executar

```bash
./scripts/run_app2_monitoramento.sh
```

## Como gerar uma leitura de teste

```bash
python3 apps/app2_monitoramento/backend/mock_sensor_pipeline.py
```
