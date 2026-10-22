# Política de segurança

## Versões suportadas
| Versão | Suporte |
|---|---|
| v0.12 (em produção em project.gplansis.com) | ✅ |
| anteriores | ❌ |

## Como relatar uma vulnerabilidade
**Não abra issue pública.** Envie um e-mail para **contato@gplansis.com** com o assunto `[SEGURANÇA] GPlanSiS|Project`, contendo:
- descrição do problema e impacto;
- passos para reproduzir (ou prova de conceito);
- versão, navegador e ambiente usados.

Confirmamos o recebimento assim que possível, mantemos você informado sobre a correção e damos crédito na publicação, se desejar.

## Escopo
Autenticação e sessão (token opaco, PBKDF2), papéis por organização (INV-ACL), isolamento entre organizações (RLS forçada no PostgreSQL), invariantes do banco, ledgers append-only (medição, apontamentos, custos, boletim, RDO), importação de arquivos (MPP, XER, XML, XLSX) e cabeçalhos/CSP do proxy.

Testes devem ser feitos numa instância local — **não** teste contra project.gplansis.com nem acesse dados de outras organizações.
