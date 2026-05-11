# App1-Vigilancia

Aplicacao final de vigilancia do campus por video e IA.

## Escopo inicial

- ingestao de video ou stream de teste
- deteccao de evento suspeito
- anonimização por padrao
- desanonimizacao condicional
- emissao de alerta
- integracao com metricas do GreenRAN

## Proxima estrutura prevista

```text
apps/app1_vigilancia/
  README.md
  backend/
  pipelines/
  static/
  templates/
  tests/
```

## MVP implementado

- backend Flask da aplicacao
- persistencia simples de eventos
- leitura de contexto do GreenRAN
- API de eventos
- rota de desanonimizacao condicional
- pagina web basica da App1

## Como executar

```bash
./scripts/run_app1_vigilancia.sh
```

## Como gerar um evento de teste

```bash
python3 apps/app1_vigilancia/backend/mock_video_pipeline.py
```
