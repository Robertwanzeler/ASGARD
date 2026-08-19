# Reports

Diretório estável para relatórios finais que devem permanecer versionados.

## Objetivo

- preservar sumários finais de experimentos sem carregar datasets, checkpoints e figuras brutas de `runs/`;
- separar evidência curada de artefato transitório de execução;
- manter referências reproduzíveis em caminhos estáveis.

## Estrutura atual

- `article00/`: sumários finais e comparativos da trilha `article00`
- `experimentos_conflitos/`: manifestos, relatórios agregados e resumos CSV do protocolo de conflitos

## Regra prática

Se um arquivo é insumo bruto, checkpoint, dataset exportado ou figura regenerável, ele fica em `runs/`.
Se é um relatório final de referência para leitura, comparação ou citação no projeto, ele vai para `reports/`.
