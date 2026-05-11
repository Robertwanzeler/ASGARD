# Orange Nuclear / GreenRAN

Entrada rápida para o projeto GreenRAN O-RAN.

## Onde começar

- visão atual do projeto: `docs/README.md`
- estado consolidado GreenRAN: `docs/README_GREENRAN.md`
- progresso histórico: `docs/PROGRESSO.md`

## Estrutura principal

- `src/`: núcleo do rApp, Data Lake, dashboard e integração de runtime
- `apps/`: apps finais (`App1-Vigilancia`, `App2-Monitoramento`, `App3-Veicular`)
- `scripts/`: automação de execução, coleta, treino e utilitários
- `training/`: treinamento ML/GraphSAGE do core
- `tests/`: testes centrais de integração/contrato
- `docs/`: documentação técnica e experimental
- `reports/`: relatórios finais curados, preservados fora de `runs/`
- `config/`: configuração versionável do runtime e parâmetros

## Comandos úteis

Executar a suíte Python consolidada:

```bash
bash scripts/run_python_tests.sh
```

Executar o runtime principal:

```bash
./scripts/run_greenran_v2.sh
```

Exportar dataset veicular e treinar o GraphSAGE direto para a trilha ARMD-GreenRAN:

```bash
python3 scripts/run_vehicle_graphsage_pipeline.py --hours 24
```

O runner prefere `drlexp/.venv/bin/python` para o treino quando essa virtualenv existir.

## Observações de repositório

- `carla/`, `flexric/` e `ns-O-RAN-flexric/` são dependências grandes e aumentam muito o volume do repositório.
- `runs/`, `charts/`, parte de `models/` e snapshots locais devem ser tratados como artefatos gerados, não como código-fonte principal.
- relatórios finais que valem versionamento devem ir para `reports/`, não para `runs/`.
- a documentação operacional mais atual está em `docs/`, não na raiz.
