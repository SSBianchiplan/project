> **Nota de publicação (out/2026):** este é o guia de instalação original do desenvolvimento, mantido como referência.
> Algumas pastas citadas aqui **não estão neste repositório**: `frontend/` (código-fonte React — só o bundle compilado está em `web/`),
> `spec/` e `backend/tests/`. O `scripts/ci_gates.sh` depende de `backend/tests/` para o passo SELF-CHECK.
> Comandos com `.venv/Scripts/` são do Windows; no Linux/macOS use `.venv/bin/`.

# Subir o sistema do zero

Pré-requisitos: Docker, Python 3.12, Node 20+. A pasta `templates/` na raiz alimenta a tela Templates e manual. `psql` no host é opcional
(o gate usa o do container). JVM só para ler `.mpp`.

```bash
# 1. banco (porta 5433 no host: 5432 costuma estar ocupada por outro Postgres)
docker compose up -d db

# 2. backend
cd backend
python -m venv .venv
. .venv/Scripts/activate        # Linux/mac: . .venv/bin/activate
pip install -r requirements.txt
GPS_DSN_SYNC=postgresql+psycopg://gps:gps@localhost:5433/gplansis alembic upgrade head

# 3. gates (BUILD, SELF-CHECK, INV-ORG-001, RED-TEAM) — primeiro comando do projeto
cd ..
PSQL="docker compose -p gplansis exec -T db psql -U gps" PY=".venv/Scripts/" \
GPS_DSN_TESTE=postgresql://gps:gps@localhost:5433/gplansis \
bash scripts/ci_gates.sh

# 4. admin master (imprime senha provisória se --senha for omitido)
cd ..
GPS_DSN_SEED=postgresql://gps:gps@localhost:5433/gplansis backend/.venv/Scripts/python scripts/bootstrap_admin.py admin@exemplo.com

# 5. API (8000 costuma estar reservada pelo WSL/Hyper-V; use 8001).
#    GPS_PERMITIR_HEADER_ORG=1 só em dev: deixa os smokes antigos passarem sem login.
cd backend
GPS_DSN=postgresql+asyncpg://gps:gps@localhost:5433/gplansis GPS_PERMITIR_HEADER_ORG=1 uvicorn app.main:app --reload --port 8001

# 6. dados de demonstração
cd ..
GPS_DSN_SEED=postgresql://gps:gps@localhost:5433/gplansis backend/.venv/Scripts/python scripts/semear_demo.py

# 7. front (entre com o e-mail e a senha do admin master; a organização vem da sessão)
cd frontend
cp .env.example .env.local      # VITE_API_URL=http://localhost:8001
npm install
npm run dev -- --port 5175      # se 5173 já estiver em uso
```

Atenção: `scripts/ci_gates.sh` faz downgrade/upgrade do schema e apaga os dados.
Depois dele, rode o bootstrap do admin e a semeadura de novo.

## Variáveis

| Variável | Onde | Padrão | Uso |
|---|---|---|---|
| `GPS_DSN` | API | `postgresql+asyncpg://gps:gps@localhost:5432/gplansis` | conexão async |
| `GPS_DSN_SYNC` | Alembic, gate | `...+psycopg://...:5433/...` | migrations |
| `GPS_DSN_TESTE` | pytest | vazio (pula `test_banco.py`) | invariantes contra Postgres real |
| `GPS_DB_ROLE` | API | `gps_app` | papel assumido por transação (`SET LOCAL ROLE`) |
| `GPS_CORS_ORIGENS` | API | localhost:5173 | origens extras; em dev qualquer porta local passa |
| `GPS_PERMITIR_HEADER_ORG` | API | `0` | `1` aceita `X-Organizacao` sem token (só dev) |
| `GPS_SESSAO_HORAS` | API | `12` | validade do token de sessão |
| `PSQL`, `PY` | gate | `psql`, vazio | cliente psql e prefixo do venv |
| `VITE_API_URL`, `VITE_CONTRATO_ID` | front | — | ver `.env.example` |

## Rede corporativa com inspeção TLS

Se `npm install` travar sem erro e `curl` funcionar, é o Node recusando o
certificado raiz da empresa (`SELF_SIGNED_CERT_IN_CHAIN`). Node 24 lê a CA
do Windows com:

```powershell
$env:NODE_USE_SYSTEM_CA = "1"; npm install
```

Para tornar permanente: `setx NODE_USE_SYSTEM_CA 1`.

## Leitura de MPP

Exige JVM no host: `pip install jpype1 mpxj`. Sem JVM o endpoint responde
501 com a instrução; o caminho alternativo é o XML nativo do Project.

## Acesso

Login em `/auth/login` devolve um token opaco (12 h, revogável). Papéis por organização:
LEITOR lê; FISCAL lê e aprova linha de base; PLANEJADOR grava; ADMIN gerencia usuários;
ADMIN_MASTER (admin@exemplo.com) faz tudo e cria organizações. A política fica em
um só lugar, `backend/app/infra/tenancy.py` (INV-ACL-001..004).

## Isolamento por organização

Toda rota de dados exige sessão autenticada; a organização ativa vem da sessão. A
sessão faz `SET LOCAL ROLE gps_app` e `set_config('app.organizacao_id')`
dentro da transação: mesmo conectando como superusuário em dev, a RLS vale.
Sem header: 401. Organização errada: 404. Violação de invariante no banco: 422
com a mensagem do `RAISE`.

## Estado dos gates

| Gate | Estado |
|---|---|
| SPEC | PASS |
| BUILD | PASS — upgrade → downgrade → upgrade limpos |
| SELF-CHECK | PASS — 86 testes (+8 pulados sem banco) (CPM, recursos, custos com oráculos à mão), `mypy --strict` e `ruff` limpos; exportações PDF/XLSX verificadas por smoke |
| INV-ORG-001 | PASS — `v_rls_auditoria` vazia |
| RED-TEAM | PASS — 18 recusas no núcleo + 6 em RLS/importação + 8 no cronograma, todas por invariante |
| FRONT | PASS — `tsc --noEmit` estrito e `vite build` |
| SMOKE | PASS — `smoke_api.py` (27), `smoke_projeto.py` (17), `smoke_auth.py` (26), `smoke_custos.py` (12), `smoke_apontamentos.py` (15) |
| HUMAN-GATE | pendente |
| DONE | pendente do HUMAN-GATE |

Executado em 2026-09-18 contra Postgres 16 real. O que a primeira execução
encontrou e foi corrigido está no histórico do repositório: RAISE com `%%`
inválido, CHECK com subquery, extensões no schema errado, `gps_app` sem
GRANT nas tabelas do 004, red-team com `SET LOCAL` parametrizado, rotas do
núcleo sem tenant, avanço de folha sempre zero.

## Importação de MPP

No venv do backend: `pip install jpype1 mpxj install-jdk` e `python -c "import jdk; jdk.install('17', jre=True)"`. O JRE fica em `~/.jre` e o importador o encontra sem `JAVA_HOME`. Sem isso, o sistema pede o XML nativo do Project.
