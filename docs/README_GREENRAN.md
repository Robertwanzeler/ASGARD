# GreenRAN O-RAN - Estado do Projeto

## Status: ✅ FUNCIONANDO - Cenário com Congestionamento

Data: 2026-03-20

## Documentos Prioritarios

Para a trilha `article00`, os documentos principais agora foram consolidados em:

- `docs/ARTICLE00_RESUMO_FINAL.md`
  - resumo executivo;
  - melhor cenario geral;
  - melhor cenario estrito em `threshold 0.5`;
  - comparacao final com o artigo;
  - localizacao dos graficos e artefatos.
- `docs/ARTICLE00_METODO_E_EXPERIMENTOS.md`
  - pipeline tecnico;
  - principais mudancas do metodo;
  - protocolo experimental consolidado;
  - organizacao das pastas de graficos;
  - limites e status experimental.

Documentacao historica/complementar:

- `docs/ARTICLE00_AUDITORIA_RESULTADOS.md`
- `docs/ARTICLE00_DATASET_AUDITORIA.md`
- `docs/ARTICLE00_VEREDITO_CONFORMIDADE.md`
- `docs/ARTICLE00_PROTOCOLO_EXPERIMENTAL.md`
- `docs/ARTICLE00_DESENHO_TECNICO.md`
- `docs/ARTICLE00_COMPARISON_FIGURES.md`
- `docs/ARTICLE00_EXPERIMENTOS.md`
- `docs/CONFLICT_DATASET_PIPELINE.md`
  - pipeline de exportacao do dataset de conflitos e grafo operacional.
- `docs/ESTUDO_ARTIGOS_BASE.md`
  - relacao entre artigo00, artigo01 e a hierarquia do rApp.
- `docs/DOCUMENTACAO_GREENRAN.md`
  - visao geral completa do projeto.

---

## Resumo das Alterações Recentes

### Cenário ns-3 Modificado (scenario-greenran.cc)

| Parâmetro | Valor Antigo | Valor Novo | Efeito |
|-----------|--------------|------------|---------|
| S1-U DataRate | 60 Mbps | **15 Mbps** | Gargalo intenso |
| S1-U Delay | 10 ms | 5 ms | Menos buffering base |
| P2P Link | 100 Mbps, 50ms | **30 Mbps, 20ms** | Mais restrição |
| Buffer RLC/PDCP | 10 MB | **20 MB** | Mais delay em congestionamento |
| Total UEs | 6 | **12** | Mais competição |
| Câmaras | OffTime=7s | **OffTime=3s** | Rajadas mais frequentes |
| Background | Constante | **Bursty (On=1s, Off=10s)** | Tráfego variável |

### Cenário de Tráfego Resultante

```
Topologia de Tráfego:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tempo    Câmaras (120Mbps)    UEs Extras    Total        S1-U
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1-2s     [ON ][off][off]    [off]x6       120 Mbps     15 Mbps ⚠
2-3s     [ON ][ON ][off]    [ON ]x3       300 Mbps     15 Mbps ⚠⚠
3-4s     [ON ][ON ][ON ]     [ON ]x6       540 Mbps     15 Mbps ⚠⚠⚠
4-5s     [off][ON ][ON ]     [ON ]x4       300 Mbps     15 Mbps ⚠⚠
5-6s     [off][off][ON ]     [ON ]x2       180 Mbps     15 Mbps ⚠⚠
6-10s    [off][off][off]    [ON ]x6       150 Mbps     15 Mbps ⚠⚠
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Resultado Esperado:
- Latência PDCP durante overload: 100-500ms
- Latência PDCP durante silêncio: 5-20ms
- Bitrate instantâneo: 0-25 Mbps durante congestionamento
```

---

## xApp Energy Saver (Código Limpo)
- ~350 linhas (vs ~1500 originais)
- Thresholds centralizados
- Interface para rApp implementada
- Estados: NORMAL → INTERVENTION → ENERGY_SAVE

## xApp Slicer (Código Limpo)
- ~350 linhas (vs ~970 originais)
- Focado em SLA (latência < 100ms)
- Interface para rApp implementada
- Estados: NORMAL → WARNING → CRITICAL → IDLE

---

## Arquitetura Atual

```
┌─────────────────────────────────────────────────────────────┐
│                         rApp (FUTURO)                       │
│  Coordena: Slicer ↔ Energy Saver                            │
│  Interface: /tmp/xapp_intents/*.txt                          │
└─────────────────────────────────────────────────────────────┘
                    ▲              ▲
                    │              │
         ┌─────────┴──┐    ┌─────┴─────────┐
         │ xApp      │    │ xApp          │
         │ Slicer    │    │ Energy Saver  │
         │ SLA-based │    │ Latency-based │
         └───────────┘    └───────────────┘
                    ▲              ▲
                    │   KPM/E2    │
         ┌─────────┴──────────────┴────────┐
         │          nearRT-RIC (flexric)     │
         └────────────────────────────────┘
                            ▲
                            │ E2
         ┌──────────────────┴──────────────────┐
         │    ns-3 (scenario-greenran)        │
         │  3 Câmaras (rajadas 120Mbps)      │
         │  6 UEs extras (rajadas 30-55Mbps) │
         │  3 UEs background (bursty)         │
         │  S1-U: 15Mbps (gargalo)           │
         │  Buffer: 20MB                      │
         └───────────────────────────────────┘
```

---

## Como Executar

### 1. Execução Completa (10 min)
```bash
cd ~/orange_nuclear
./run_greenran_complete.sh
```

### 2. Teste Rápido do Cenário (60s)
```bash
cd ~/orange_nuclear
./test_cenario.sh
```

### 3. Monitorar Intenções (outro terminal)
```bash
./monitor_intents.sh
```

### 4. Ver Logs
```bash
tail -f /tmp/xapp_slicer.log    # SLICER
tail -f /tmp/xapp_energy.log    # ENERGY SAVER
tail -f /tmp/ns3.log           # ns-3
tail -f /tmp/ric.log            # RIC
```

### 5. Parar Tudo
```bash
./stop_all.sh
```

---

## Interface rApp (para implementação futura)

Cada xApp escreve sua intenção em arquivos em `/tmp/xapp_intents/`:

### slicer.txt
```
XAPP=slicer
INTENT=CRITICAL
STATE=CRITICAL
TIMESTAMP=1711000000
ALLOWED=pending
```

### energy_saver.txt
```
XAPP=energy_saver
INTENT=ENERGY_SAVE
TIMESTAMP=1711000000
ALLOWED=pending
```

### Regras de Prioridade (a implementar no rApp)
| Prioridade | xApp | Condição | Ação |
|------------|------|----------|------|
| 1 | Slicer | CRITICAL | Bloquear Energy Saver |
| 2 | Slicer | WARNING | Limitar Energy Saver |
| 3 | Energy Saver | INTERVENTION | Permitir CELL_ON |
| 4 | Energy Saver | ENERGY_SAVE | Permitir DRB release |

---

## Thresholds Configurados

### xApp Slicer
| Threshold | Valor | Descrição |
|-----------|-------|-----------|
| SLA_CRITICAL | 100ms | Latência máxima aceitável |
| SLA_WARNING | 50ms | Latência de alerta |
| CAMERA_MIN_PACKETS | 5 | Mínimo pacotes para ativar |

### xApp Energy Saver
| Threshold | Valor | Descrição |
|-----------|-------|-----------|
| INTERVENTION | 100ms | Latência SLA violado |
| ENERGY_SAVE_VOLUME | 100kb | Volume mínimo ativo |
| ENERGY_SAVE_LATENCY | 10ms | Latência para energy save |

---

## Validação Esperada

Depois de executar, verifique nos logs:

### xApp Slicer
```
[XAPP-SLICER] CRITICAL: Latência > 100ms detectada
[XAPP-SLICER] >>> SLA VIOLADO!
```

### xApp Energy Saver
```
[XAPP-ENERGY] Latência: 150000 us (acima de 100ms)
[XAPP-ENERGY] Estado: INTERVENTION
```

### Logs KPM
```
[XAPP-ENERGY] Latência: 150000 us (threshold: 100000 us)
[XAPP-ENERGY] Diagnóstico: LATÊNCIA_ALTA
```

---

## Arquivos Principais

| Arquivo | Descrição |
|---------|-----------|
| `ns-O-RAN-flexric/mmwave-LENA-oran/scratch/scenario-greenran.cc` | Cenário ns-3 com congestionamento |
| `flexric/examples/xApp/c/energy_saver/xapp_energy_saver.c` | xApp Energy Saver |
| `flexric/examples/xApp/c/slicer/xapp_slicer.c` | xApp Slicer |
| `run_greenran_complete.sh` | Script de execução completa |
| `test_cenario.sh` | Script de teste rápido |
| `stop_all.sh` | Script de parada |
| `monitor_intents.sh` | Monitor de intenções |

---

## Próximos Passos

### 1. Testar Cenário ✅
- [x] Compilar cenário modificado
- [ ] Executar teste rápido (60s)
- [ ] Verificar latência > 100ms nos logs

### 2. Testar xApps com Novo Cenário
- [ ] Executar SLICER e verificar detecção de CRITICAL
- [ ] Executar ENERGY SAVER e verificar detecção de ENERGY_SAVE

### 3. Implementar rApp
- [ ] Criar daemon rApp
- [ ] Ler intenções de ambos xApps
- [ ] Aplicar regras de prioridade
- [ ] Escrever decisão em rapp_decision.txt

### 4. Validação Final
- [ ] Comparar métricas com/sem O-RAN
- [ ] Documentar economia de energia
- [ ] Documentar conformidade SLA
