## O que muda
<!-- Resumo da mudança e o porquê. Referencie a issue: "Fecha #123". -->

## Tipo
- [ ] Correção de bug
- [ ] Nova funcionalidade
- [ ] Documentação
- [ ] Refatoração / manutenção

## Como testei
<!-- Passos, banco usado (Postgres local/Docker), smokes rodados, navegadores. -->

## Capturas
<!-- Se mexeu na interface ou nos PDFs, antes/depois — só com dados fictícios. -->

## Checklist
- [ ] Regras novas estão no domínio (`backend/app/dominio`) e/ou como invariante no banco (`db/` + migration Alembic)
- [ ] Migration reversível (`alembic upgrade head` → `downgrade` → `upgrade`)
- [ ] Toda tabela nova tem RLS forçada (`SELECT * FROM plan.v_rls_auditoria` vazio)
- [ ] `ruff` e `mypy --strict` limpos no que foi alterado
- [ ] Sem segredos, `.env`, bancos ou dados reais no commit
- [ ] Textos da interface e mensagens em pt-BR
- [ ] `CHANGELOG.md` atualizado (se visível para o usuário)
