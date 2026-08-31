# Fonte externa de autoridade da rede

O rApp usa `config/greenran_external_network_authority.json` como fonte externa editável pelo operador.

## Papel

- ARMD e TA-SAM continuam sendo os decisores primários.
- A fonte externa só é consultada em empate, baixa confiança ou ausência de proposta válida.
- `live_allocator` permanece como fallback quando não há decisão segura.
- O arquivo não pode habilitar controle real nem usar métricas proxy.

## Campos principais

- `network_policy.priorities`: prioridade relativa de câmera, sensores, veículos, UEs e tráfego de fundo.
- `network_policy.shared_resources`: limites e metas compartilhadas de RAN, IA e SLA.
- `arbitration.last_resort`: vencedor permitido em conflito por domínio.
- `safety.allow_control`: deve permanecer `false` nesta fase.
- `safety.use_proxy`: deve permanecer `false`.

## Alteração pelo operador

Edite somente o JSON externo e preserve:

```json
{
  "mode": "shadow_only",
  "data_source": "real_only",
  "authority": {
    "precedence": "last_resort_only"
  },
  "safety": {
    "allow_control": false,
    "use_proxy": false
  }
}
```

O rApp recarrega o arquivo quando o timestamp é alterado. Se o novo conteúdo for inválido, mantém em memória a última versão válida e registra o erro para auditoria.

Para usar outro arquivo sem alterar o padrão:

```bash
GREENRAN_RAPP_POLICY_FILE=/caminho/para/autoridade.json
```
