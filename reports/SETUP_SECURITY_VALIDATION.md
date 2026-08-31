# Validação do setup de segurança

## Metadados

- Data/hora local: 06/08/2026, aproximadamente 22:13–22:14 (UTC−03:00).
- Autorização: fornecida pelo responsável pelo trabalho acadêmico.
- Ferramenta: Nmap 7.98; `curl`; OpenSSL.
- Escopo: CIDR e dois hosts já registrados no documento original. Os
  identificadores foram omitidos nesta versão compartilhável.
- Impacto: descoberta, identificação de serviço e requisições HTTP/TLS sem
  autenticação. Nenhum dado foi alterado.

## Resultado do reconhecimento

- Foram identificados **53 hosts ativos**, e não 51 como informado anteriormente.
- O primeiro serviço documentado respondeu em TCP/8080 como `nginx 1.31.3`.
- O segundo host apresentou TCP/22 com OpenSSH 9.6p1 e TCP/443 com um dashboard
  HTTPS. TCP/8080 estava fechado nesse host.

## Evidências e interpretação

### Serviço HTTP em TCP/8080

- Resposta: `200 OK`.
- `Server: nginx/1.31.3`.
- `ETag` presente.
- Não foi observado `X-Frame-Options` nessa resposta.

Conclusão: o ETag é uma observação para análise de cache e divulgação de
metadados, mas não demonstra sozinho vazamento de informação nem deve ser
associado ao CVE-2009-3555. A ausência de `X-Frame-Options` é uma lacuna de
hardening; deve ser avaliada junto com CSP `frame-ancestors`, conteúdo servido e
possibilidade real de enquadramento.

### Dashboard HTTPS

- Resposta inicial: `302 Found` para `/app/login`.
- `X-Frame-Options: sameorigin` presente.
- `Strict-Transport-Security` e `X-Content-Type-Options` não foram observados.
- O cookie observado era um cookie de remoção (`Max-Age=0`) e tinha `HttpOnly`,
  mas não `Secure`. Isso não permite concluir como os cookies de sessão são
  configurados sem autenticação e análise dos fluxos de login.
- O certificado apresentou CN `wazuh-dashboard` e SAN somente para `127.0.0.1`.
  Ele não corresponde ao hostname ou endereço usado no teste.

Conclusão: há um problema confirmado de identidade do certificado para o nome
de acesso utilizado. HSTS deve ser considerado somente depois de corrigir o
certificado e garantir HTTPS válido; a ausência de HSTS não é a causa do
certificado incompatível.

### Possíveis arquivos de configuração

As requisições sem autenticação a `/webserver.ini`, `/webserver.ini.bak` e
`/.env` retornaram `401` com uma resposta JSON curta. Não houve exposição de
conteúdo. Isso não elimina risco após autenticação ou em outras rotas, mas o
achado original de exposição não foi confirmado.

## Testes não executados

- Nenhuma tentativa de senha com Hydra.
- Nenhum teste de SQL injection com sqlmap.
- Nenhuma exploração, alteração de dados ou teste de indisponibilidade.
- Nenhuma tentativa de autenticação com as credenciais que estavam expostas.

Esses testes exigem alvo, conta/parâmetro, janela, limite e autorização
específicos; não fazem parte desta validação de baixo impacto.

## Recomendações

1. Corrigir o certificado para conter o hostname real e os nomes/IPs aprovados.
2. Avaliar `Secure`, `HttpOnly` e `SameSite` nos cookies de sessão reais.
3. Adicionar `X-Content-Type-Options: nosniff` quando compatível com a aplicação.
4. Adicionar HSTS somente após validar toda a cadeia HTTPS e os subdomínios.
5. Revisar o conteúdo servido em TCP/8080 e decidir se ETags são necessários.
6. Repetir a validação com logs preservados em armazenamento restrito.
