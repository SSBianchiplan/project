# Como contribuir com o GPlanSiS|Project

Obrigado pelo interesse! Este guia explica como propor mudanças de forma que a revisão seja rápida.

## Antes de começar
- Leia o [código de conduta](CODE_OF_CONDUCT.md).
- Para mudanças maiores que uma correção pontual, **abra uma issue primeiro** (modelos "Ideia" ou "Bug") e combine a abordagem.
- Falhas de segurança **não** vão em issue pública: siga a [política de segurança](SECURITY.md).

## Ambiente de desenvolvimento
Requisitos: Python 3.12 e PostgreSQL 14+ (produção usa 16). Passo a passo completo no [README](README.md#como-rodar-localmente).

```bash
docker compose up -d db                       # Postgres 16 em 127.0.0.1:5433 (ou use o seu)
python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd backend && GPS_DSN_SYNC=postgresql+psycopg://gps:gps@127.0.0.1:5433/gplansis ../.venv/bin/alembic upgrade head && cd ..
GPS_DSN=postgresql+asyncpg://gps:gps@127.0.0.1:5433/gplansis \
  .venv/bin/uvicorn app.main:app --app-dir backend --port 8001 --reload
```

## Organização do código
- `backend/app/dominio/` — núcleo puro (sem I/O, sem `now()`, `Decimal`): CPM, curva S, ponderação, custos, recursos, apontamentos, coerência.
- `backend/app/api/` — rotas FastAPI + Pydantic v2 (fronteira).
- `backend/app/infra/` — SQLAlchemy async, autenticação, multiempresa (RLS) e exportação PDF/XLSX.
- `backend/app/importacao/` — parsers de MS Project (MPP/XML), Primavera P6 (XER/XML) e máscara XLSX.
- `backend/alembic/` + `db/` — schema com invariantes (triggers, CHECK, RLS) e scripts de red-team.
- `web/` — **bundle compilado** do frontend (React 19 + Vite + PWA). O código-fonte do frontend ainda não está neste repositório (veja o README).

## Regras do projeto
- **Interface e mensagens em português do Brasil**, textos curtos e diretos.
- **Cálculo no servidor**: o navegador só desenha. Regra nova vai no domínio, com teste/oráculo calculado à mão.
- **Invariante no banco**: o que não pode acontecer é recusado pelo PostgreSQL (trigger, CHECK, constraint), não só pela tela.
- **Ledgers não se editam**: medição, apontamento, custo real, boletim e eventos de RDO são append-only — correção é estorno.
- **Multiempresa**: toda tabela nova precisa de RLS forçada; `plan.v_rls_auditoria` deve continuar vazia.
- **Migrations reversíveis** (Alembic sobre SQL explícito, sem autogenerate).
- Nunca inclua segredos, `.env`, dumps de banco ou dados reais de clientes em commits ou capturas.
- Python: `ruff check app alembic` e `mypy --strict app/dominio app/importacao` antes de enviar.

## Pull requests
1. Faça um fork e crie uma branch a partir de `main` (`feat/…`, `fix/…`, `docs/…`).
2. Commits pequenos, com mensagem no imperativo explicando o porquê.
3. Preencha o modelo do PR: o que mudou, como testou, capturas se mexer na interface ou nos PDFs.
4. Atualize o `CHANGELOG.md` quando a mudança for visível para quem usa.

## Dúvidas
Abra uma issue com o modelo "Pergunta" ou escreva para contato@gplansis.com.
