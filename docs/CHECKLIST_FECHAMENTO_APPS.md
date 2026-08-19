# Checklist de Fechamento das Apps Finais

Este documento traduz a proposta revisada em uma lista objetiva do que falta
para considerar `App1-Vigilancia` e `App2-Monitoramento` como entregas
fechadas do projeto.

Escopo deliberadamente fora deste checklist:

- `GraphSAGE`
- trilha de conflitos em `drlexp/`
- integrações cientificas ainda nao incorporadas ao produto

---

## 1. App1-Vigilancia

### Objetivo da proposta

Entregar uma aplicacao final de vigilancia por video e IA para o campus,
integrada ao GreenRAN, com:

- video de cameras 4K;
- anonimização por padrao;
- deteccao de violencia;
- desanonimizacao condicional;
- alerta operacional;
- validacao com requisitos de rede:
  - throughput medio de `25 Mbps` por camera;
  - latencia de rede abaixo de `100 ms`.

### Estado atual resumido

- `Parcial avancado`
- aplicacao web/API existe;
- ingestao de cameras existe;
- upload e armazenamento de videos existe;
- artefatos e monitoramento existem;
- eventos e desanonimizacao condicional existem;
- contexto de rede do GreenRAN ja aparece junto da aplicacao.

### O que ja esta pronto

- cadastro e ingestao automatica de cameras;
- upload e persistencia de videos;
- geracao de thumbnails/previews;
- geracao de evento de risco;
- snapshot de monitoramento da aplicacao;
- integracao com estado de rede, politicas e xApps;
- rota de desanonimizacao condicional.

### Gap principal

O pipeline de "IA para violencia" ainda esta representado por heuristica de
frames, movimento e mudanca de cena, e nao por um modelo real de deteccao de
violencia.

### Checklist de fechamento

#### Prioridade alta

- [ ] Substituir o score heuristico por pipeline real de inferencia de violencia.
- [ ] Definir contrato claro de entrada:
  - camera ao vivo;
  - arquivo local;
  - stream de teste.
- [ ] Definir criterio operacional de "evento suspeito" alinhado com a proposta.
- [ ] Tornar a anonimização um artefato real da pipeline, nao apenas metadado.
- [ ] Garantir que o alerta final inclua:
  - camera;
  - horario;
  - score;
  - clip/preview;
  - contexto de rede;
  - decisao/politica GreenRAN vigente.
- [ ] Criar protocolo de validacao da App1:
  - video normal;
  - video com evento;
  - rede saudavel;
  - rede degradada;
  - verificacao de throughput/latencia.

#### Prioridade media

- [ ] Versionar datasets e videos de demonstracao usados na validacao.
- [ ] Criar pagina/resumo de eventos confirmados vs suspeitos.
- [ ] Diferenciar melhor "detected", "validated" e "dismissed".
- [ ] Registrar auditoria de desanonimizacao:
  - quem;
  - quando;
  - motivo.
- [ ] Exportar relatorio simples por periodo.

#### Prioridade baixa

- [ ] Refinar UX do painel operacional.
- [ ] Adicionar filtros por camera, local e severidade.
- [ ] Incluir estatisticas de efetividade da deteccao.

### Criterio de aceite sugerido

- um video/stream gera analise consistente;
- eventos suspeitos geram alerta com evidencias;
- o estado da rede aparece junto da ocorrencia;
- a politica do GreenRAN protege cameras quando SLA degrada;
- existe uma demonstracao reproduzivel com caso normal e caso critico.

---

## 2. App2-Monitoramento

### Objetivo da proposta

Entregar uma aplicacao final de monitoramento ambiental e do solo, integrada ao
GreenRAN, com:

- sensores ambientais e de solo;
- analise em tempo real;
- historico e alertas;
- uso de conectividade heterogenea;
- validacao com:
  - conectividade;
  - perda de pacotes;
  - consumo;
  - utilizacao da rede;
  - comportamento intermitente dos sensores.

### Estado atual resumido

- `Parcial inicial`
- app web/API existe;
- simulador de sensores existe;
- alertas por limiar existem;
- snapshot de rede/sensores existe;
- conectividade heterogenea aparece na simulacao;
- ainda falta transformar isso em aplicacao final convincente.

### O que ja esta pronto

- backend web/API;
- persistencia simples de leituras e alertas;
- simulador com sensores de:
  - temperatura;
  - umidade;
  - umidade do solo;
  - temperatura do solo;
  - condutividade do solo;
  - nitrogenio do solo;
  - qualidade do ar;
  - chuva;
  - radiacao solar;
- modelagem de conectividades:
  - `5g_native`;
  - `5g_redcap`;
  - `lora_fwa`;
  - `ntn_gateway`;
- snapshot com packet loss, latencia, entrega, bateria e potencia.

### Gaps principais

- a conectividade heterogenea ainda e majoritariamente simulada;
- nao ha evidencia tecnica forte de gateway real ou emulacao formal;
- a app ainda esta mais para painel de ingestao do que para aplicacao analitica;
- nao ha integracao forte das leituras da App2 com o `DataLake` central;
- faltam relatorios e transformacao de dados em informacao util.

### Checklist de fechamento

#### Prioridade alta

- [ ] Integrar leituras e snapshots da App2 ao `DataLake` central do GreenRAN.
- [ ] Criar pipeline normalizado de ingestao de sensores:
  - leitura bruta;
  - tipo normalizado;
  - metrica derivada;
  - alerta;
  - historico.
- [ ] Formalizar a camada de conectividade:
  - o que e 5G nativo;
  - o que e RedCap;
  - o que e LoRa via gateway/FWA;
  - o que e NTN gateway.
- [ ] Definir criterio de validacao da App2:
  - conectividade estabelecida;
  - perda de pacotes;
  - latencia;
  - consumo;
  - utilizacao;
  - entrega nos intervalos programados.
- [ ] Gerar saida analitica util:
  - alerta ambiental;
  - alerta de solo;
  - resumo por sensor;
  - resumo por gateway;
  - resumo por tecnologia.

#### Prioridade media

- [ ] Produzir relatorio simples de qualidade do solo.
- [ ] Produzir relatorio simples de monitoramento ambiental.
- [ ] Separar anomalias por severidade e dominio:
  - ambiental;
  - solo;
  - conectividade;
  - energia.
- [ ] Criar historico consultavel por API com janela temporal.
- [ ] Integrar dashboards App2 com visao mais explicita de rede e SLA.

#### Prioridade baixa

- [ ] Melhorar visualizacao geografica/local dos sensores.
- [ ] Adicionar comparacao entre modos de conectividade.
- [ ] Exportar CSV/JSON consolidado por execucao.

### Criterio de aceite sugerido

- sensores simulados ou reais geram dados continuamente;
- alertas ficam disponiveis por API e pagina;
- ha leitura clara de perda, entrega, consumo e uso da rede;
- ha separacao entre tipos de sensores e tecnologias de conectividade;
- existe demonstracao reproduzivel com falha e recuperacao.

---

## 3. Itens transversais

Estas pendencias nao sao exclusivas de App1 ou App2, mas impactam diretamente a
qualidade da entrega.

### Prioridade alta

- [ ] Consolidar um runner unico realmente reproduzivel do sistema.
- [ ] Organizar artefatos por execucao em `runs/`.
- [ ] Reduzir dependencias restantes de caminhos absolutos e scripts legados.
- [ ] Garantir que as apps finais aparecam no dashboard principal como entregas
      do produto, e nao apenas como servicos paralelos.

### Prioridade media

- [ ] Criar roteiro de demonstracao oficial:
  - subir sistema;
  - gerar evento App1;
  - gerar evento App2;
  - mostrar impacto no GreenRAN.
- [ ] Criar matriz final `proposta -> evidencia de codigo -> teste -> demo`.

### Prioridade baixa

- [ ] Produzir relatorio tecnico curto de fechamento das apps.
- [ ] Consolidar capturas de tela, videos e logs de demonstracao.

---

## 4. Ordem recomendada

### Fila de execucao

1. Fechar runtime e reproducibilidade do core.
2. Fechar App1 com pipeline real de deteccao.
3. Fechar App2 com ingestao/historico/analitica mais forte.
4. Consolidar validacao e demonstracao.

### Leitura pragmatica

- `App1` esta mais proxima de entrega final.
- `App2` ainda precisa ganhar substancia funcional.
- `Gateways IoT/LoRa/FWA` continuam como lacuna tecnica real e precisam de
  artefato proprio, mesmo que inicial.
