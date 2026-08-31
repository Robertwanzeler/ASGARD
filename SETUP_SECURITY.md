# Setup de segurança do ambiente OpenCode

Este documento descreve o setup e o procedimento de validação. Ele não armazena
credenciais, resultados de rede identificáveis ou dados de outros dispositivos.

## Estado e escopo

- Finalidade: trabalho acadêmico e pesquisa defensiva autorizada.
- Escopo: projeto local, fork local do OpenCode e alvos cuja autorização foi
  registrada pelo responsável pelo ambiente.
- Data desta revisão: 06/08/2026.
- Estado: documentação sanitizada; validações devem ser registradas em relatório
  separado, com horário, ferramenta, versão, comando e evidência.

Não testar redes, hosts, contas ou aplicações fora do escopo autorizado. Não
executar brute force, exploração ou teste de SQL injection sem alvo, janela,
conta e limite de requisições definidos.

## Credenciais

As credenciais anteriormente documentadas foram removidas. Como foram expostas,
devem ser revogadas e substituídas nos consoles dos provedores antes de qualquer
uso.

Usar variáveis de ambiente ou um gerenciador de segredos. Nunca registrar a
chave em Markdown, Git, logs, screenshots ou histórico de terminal. O arquivo
global do OpenCode deve ter permissões restritas e ser auditado separadamente.

Exemplo conceitual, sem valores reais:

```jsonc
{
  "provider": {
    "zai": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "https://api.z.ai/api/paas/v4"
      }
    },
    "groq": {
      "npm": "@ai-sdk/groq",
      "options": {}
    }
  }
}
```

## Fork do OpenCode

- Local: `/tmp/opencode-fork`.
- Dependência: Bun, conforme `package.json` do fork.
- Reprodutibilidade: registrar o commit usado e manter o `bun.lock`.
- Alterações locais devem ser mantidas como patch revisável; não depender de
  edições manuais não registradas em `/tmp`.

O modo `build` deve respeitar as permissões configuradas pelo usuário. O modo
`plan` deve continuar sem ferramentas de edição, salvo o diretório de planos
explicitamente permitido pelo projeto. Ações externas, leitura de segredos,
execução destrutiva e acesso a diretórios externos devem exigir confirmação.

## Validação autorizada

Antes do teste, registrar:

1. responsável e autorização;
2. CIDR, hostname, URL ou conta dentro do escopo;
3. janela de teste e limite de impacto;
4. versões das ferramentas;
5. método de interrupção e contato responsável.

### Reconhecimento não destrutivo

Substituir os placeholders pelos valores autorizados, sem publicar os valores
no relatório compartilhado:

```bash
AUTHORIZED_CIDR='<cidr-autorizado>'
nmap -sn "$AUTHORIZED_CIDR"
nmap -sV -p 22,80,443,8080 '<host-autorizado>'
```

Guardar a saída original com controle de acesso. Não declarar uma vulnerabilidade
apenas pela versão detectada.

### Validação web e TLS

Confirmar cada achado com resposta HTTP/TLS reproduzível. Para hardening web,
avaliar, conforme a aplicação:

- `X-Frame-Options` ou uma política `frame-ancestors` adequada;
- `X-Content-Type-Options: nosniff`;
- cookies sensíveis com `Secure`, `HttpOnly` e `SameSite` apropriado;
- certificado válido para o hostname usado;
- HSTS somente depois de corrigir certificado, HTTPS e subdomínios;
- ausência de arquivos de configuração, segredos e backups publicados.

Ausência de um header é uma observação de hardening até que o impacto seja
demonstrado. Não associar ETag ao CVE-2009-3555; esse CVE trata de renegociação
TLS e deve ser analisado separadamente.

### Testes intrusivos

Hydra, sqlmap e qualquer exploração ficam bloqueados até existir autorização
específica para o alvo, conta/parâmetro, limite de tentativas, horário e plano
de interrupção. O teste deve usar ambiente de laboratório ou staging sempre que
possível e não deve alterar dados nem causar indisponibilidade.

## Relatórios

Cada finding deve conter:

- identificador e severidade justificada;
- ativo afetado, com anonimização na versão compartilhada;
- data/hora, ferramenta, versão e comando;
- evidência mínima reproduzível;
- impacto e condições de exploração;
- falso positivo considerado;
- correção, validação pós-correção e responsável.

Separar relatório técnico restrito de resumo acadêmico. Logs, capturas, histórico
do agente e resultados de scan devem ter retenção e controle de acesso definidos.

## Checklist de aceite

- [ ] Credenciais antigas revogadas e novas não aparecem na documentação.
- [ ] Nenhum IP, hostname ou serviço real aparece na versão compartilhável.
- [ ] Fork fixado em commit conhecido e patch revisável.
- [ ] Permissões de `build` e `plan` permanecem restritivas.
- [ ] Scan possui autorização, escopo, horário, versão e saída preservada.
- [ ] Achados são confirmados por evidência e não por suposição de versão.
- [ ] Testes intrusivos têm autorização e limites específicos.
- [ ] Relatório não contém segredos ou dados pessoais desnecessários.
