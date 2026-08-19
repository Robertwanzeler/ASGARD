# App3-Veicular

Aplicacao veicular do GreenRAN.

Responsabilidades iniciais:

- ler o estado combinado dos veiculos a partir de `/tmp/xapp_metrics/extended_metrics.json`;
- expor resumo operacional por veiculo;
- destacar o `ego vehicle`;
- gerar snapshot proprio em `/tmp/app3_veicular/monitoring_snapshot.json`.

Rotas principais:

- `GET /api/health`
- `GET /api/vehicles`
- `GET /api/vehicles/ego`
- `GET /api/vehicles/summary`
- `GET /api/vehicles/events`
- `GET /api/monitoring`
