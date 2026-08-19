# Trilha `article00`: Status Experimental

Este documento fixa o escopo atual da trilha `article00`.

## Decisao Atual

A trilha `article00` deve permanecer em modo **experimental**, mesmo ja tendo
dataset sintetico, grafo temporal exportado e trainer temporal implementado, e
sem qualquer integracao ao runtime do GreenRAN.

## O Que Ja Pode Existir

Nesta fase, e permitido manter:

- documentacao da trilha;
- gerador sintetico de dataset;
- scripts de orquestracao e manifests;
- estrutura de pastas em `runs/article00/`;
- comparadores entre a trilha atual e a trilha `article00`;
- treino temporal e geracao de figuras, desde que permaneçam isolados do
  GreenRAN operacional.

Esses itens servem para reproducibilidade e validacao experimental isolada da
trilha, nao para uso operacional no sistema.

## O Que Nao Deve Ser Feito Agora

Nesta fase, **nao** deve ser feito:

- integracao da trilha `article00` ao `rApp`;
- uso do `article00` no fluxo operacional do GreenRAN;
- substituicao de `train_graphsage_conflicts.py`;
- acoplamento com decisao online, dashboard operacional ou xApps;
- alegacao de fidelidade metodologica completa ao paper;
- uso dos resultados `article00` como evidencias finais de artigo sem validacao.

## Estado Esperado dos Arquivos Novos

Os arquivos da trilha `article00` devem continuar com carater de:

- `experimental_temporal_graphsage`
- `experimental`
- `not runtime integrated`

Sempre que possivel, manifests e relatorios devem explicitar esse estado.

## Criterio de Saida do Modo Experimental

A trilha so deve sair do modo experimental quando houver, no minimo:

1. validacao das equacoes e da topologia do dataset sintetico contra o paper;
2. reproducao multiseed estavel no protocolo experimental oficial;
3. leitura metodologica defensavel das curvas e metricas geradas;
4. documentacao clara das limitacoes frente ao `artigo00`;
5. decisao explicita de integrar ou nao essa trilha ao restante do projeto.

## Leitura Pratica

Hoje a trilha `article00` e:

- uma base organizada;
- uma implementacao experimental forte;
- uma trilha paralela ao GreenRAN atual;
- um experimento ainda nao homologado metodologicamente, e nao uma
  funcionalidade do sistema.

## Documentos de Controle Experimental

Os documentos que governam esta fase experimental sao:

- [ARTICLE00_AUDITORIA_RESULTADOS.md](/home/robert/orange_nuclear/docs/ARTICLE00_AUDITORIA_RESULTADOS.md:1)
- [ARTICLE00_DATASET_AUDITORIA.md](/home/robert/orange_nuclear/docs/ARTICLE00_DATASET_AUDITORIA.md:1)
- [ARTICLE00_PROTOCOLO_EXPERIMENTAL.md](/home/robert/orange_nuclear/docs/ARTICLE00_PROTOCOLO_EXPERIMENTAL.md:1)
- [ARTICLE00_DESENHO_TECNICO.md](/home/robert/orange_nuclear/docs/ARTICLE00_DESENHO_TECNICO.md:1)
- [ARTICLE00_EXPERIMENTO_D.md](/home/robert/orange_nuclear/docs/ARTICLE00_EXPERIMENTO_D.md:1)
- [ARTICLE00_VEREDITO_CONFORMIDADE.md](/home/robert/orange_nuclear/docs/ARTICLE00_VEREDITO_CONFORMIDADE.md:1)
