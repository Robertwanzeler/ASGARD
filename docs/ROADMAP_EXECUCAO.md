# Roadmap de Execucao

## Fase 1 - Consolidacao do GreenRAN Core

### Objetivo

Deixar o nucleo operacional, reproduzivel e pronto para servir de base das aplicacoes finais.

### Tarefas

- Centralizar caminhos e configuracoes
- Reduzir dependencias de `/tmp` e caminhos absolutos
- Criar runner unico do sistema
- Padronizar outputs por experimento em `runs/`
- Organizar logs, banco e figuras por execucao
- Revisar dashboard do core

### Criterio de aceite

- Um comando sobe o sistema principal
- Uma execucao gera artefatos organizados em `runs/`
- O dashboard continua funcional

## Fase 2 - Implementacao da App1-Vigilancia

### Objetivo

Entregar um MVP funcional da aplicacao de vigilancia baseada em video.

### Tarefas

- Criar backend da aplicacao
- Criar pipeline de ingestao de video/stream
- Implementar deteccao simples de evento suspeito
- Criar evento de alerta
- Integrar estado da rede e eventos do GreenRAN
- Expor pagina ou API da aplicacao

### Criterio de aceite

- Um video/stream gera eventos
- Um alerta pode ser consultado por API ou pagina
- O estado de rede relevante aparece junto ao evento

## Fase 3 - Implementacao da App2-Monitoramento

### Objetivo

Entregar um MVP funcional da aplicacao de monitoramento ambiental e do solo.

### Tarefas

- Criar backend da aplicacao
- Criar simulador/ingestor de sensores
- Normalizar tipos de sensores
- Implementar anomalias simples
- Criar historico, alertas e relatorio
- Integrar estado da rede e metricas do GreenRAN

### Criterio de aceite

- Sensores simulados ou reais geram dados
- Alertas e metricas ficam disponiveis por API ou pagina
- O sistema registra condicoes de rede associadas

## Fase 4 - IA avancada e coerencia cientifica

### Objetivo

Alinhar o que sera entrega de produto e o que sera trilha cientifica.

### Tarefas

- Reposicionar `Agentic AI` para escopo realista
- Melhorar integracao online do DRL
- Decidir se `two-tower/conflict` entra no produto ou fica isolado
- Congelar protocolo de avaliacao

### Criterio de aceite

- Cada modulo tem papel claro
- O artigo principal tem uma contribuicao central unica

## Fase 5 - Empacotamento para artigo e demonstracao

### Objetivo

Deixar o projeto apresentavel, auditavel e publicavel.

### Tarefas

- Gerar matriz final `proposta -> implementacao`
- Gerar figuras automaticas
- Consolidar tabelas de resultados
- Criar script de reproducao
- Escrever estrutura do artigo e do relatorio tecnico

### Criterio de aceite

- O projeto pode ser demonstrado
- O experimento pode ser reproduzido
- O texto do artigo bate com o estado do codigo

## Prioridade imediata

1. Fase 1
2. Fase 2
3. Fase 3
4. Fase 4
5. Fase 5
