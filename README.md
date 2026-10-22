<p align="center">
  <a href="https://project.gplansis.com"><img src=".github/capa.png" alt="GPlanSiS|Project — o cronograma vira controle de verdade" width="100%"></a>
</p>

<h1 align="center">GPlanSiS|Project</h1>

<p align="center">
  <strong>Planejamento e controle de projetos industriais: EAP ponderada, CPM no servidor, medição por etapas, recursos, custos (EVM), horas, boletim de medição e RDO — com memória de cálculo e registros auditáveis.</strong><br>
  O navegador só desenha; o cálculo e as regras ficam no servidor e no banco.
</p>

<p align="center">
  <a href="https://project.gplansis.com"><img alt="No ar" src="https://img.shields.io/badge/no%20ar-project.gplansis.com-5aa9c6?style=flat-square"></a>
  <img alt="Versão" src="https://img.shields.io/badge/vers%C3%A3o-v0.12-2d3336?style=flat-square">
  <a href="LICENSE"><img alt="licença MIT" src="https://img.shields.io/badge/licen%C3%A7a-MIT-2ea44f?style=flat-square"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-2d3336?style=flat-square&logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-Pydantic%20v2-2d3336?style=flat-square&logo=fastapi&logoColor=white">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-16%20%2B%20RLS-2d3336?style=flat-square&logo=postgresql&logoColor=white">
  <img alt="PWA" src="https://img.shields.io/badge/PWA-instal%C3%A1vel-2d3336?style=flat-square">
  <a href="CONTRIBUTING.md"><img alt="Contribuições bem-vindas" src="https://img.shields.io/badge/contribui%C3%A7%C3%B5es-bem--vindas-2f7d4f?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://project.gplansis.com"><b>➜ Abrir o GPlanSiS|Project em project.gplansis.com</b></a>
</p>

---

## O que é

O **GPlanSiS|Project** é um sistema web para **planejar e controlar empreendimentos** (EPC, EPCM, montagem, construção, comissionamento) do jeito que um procedimento de planejamento exige: a EAP é ponderada e fecha em uma base de pontos, o cronograma é calculado por **CPM no servidor**, o avanço físico só entra por **medição com evidência**, e cada número que aparece no Gantt, no cockpit ou no PDF pode ser reproduzido pela memória de cálculo.

O sistema implementa o procedimento **PRO-GPS-PLN-001 — Planejamento e Controle de Projetos** e seus anexos (modelo de EAP e curva S, histograma, relatório mensal, máscara de cronograma e RDO), que acompanham o repositório em [`templates/`](templates/).

É **multiempresa**: cada organização só enxerga os próprios contratos, garantido por **Row-Level Security forçada no PostgreSQL**, e não apenas pela tela. Regras que não podem ser quebradas viram **invariantes no banco** (triggers, CHECK e constraints): o que a API errar, o banco recusa com o código da regra (`INV-…`).

### O que o sistema não faz de propósito
- Não importa `% concluída` do MS Project como avanço: avanço entra por medição com evidência.
- Não corrige ponderação sozinho: EAP que não fecha na base de pontos não é aceita.
- Não apaga nem edita medição, apontamento, custo real ou boletim: correção é **estorno**.
- Não projeta realizado além da data de corte.

## Funcionalidades

### Planejar
- **Projeto novo** em 4 etapas: contrato → EAP e ponderação → rede e calendário → revisão, gravação e linha de base BL0 (com lista de verificação do planejamento, nota mínima 0,85).
- **Cronograma (Gantt)** com até 5 níveis de EAP, vínculos TI/II/TT/IT com defasagem, restrições SNET/MSO, folgas, **caminho crítico**, linha de base, tela cheia, atalhos de teclado e menu de contexto (nova tarefa, subtarefa, marco, indentar, recuar, excluir).
- **Gaveta da atividade** com 9 guias (geral, datas, rede, restrições, recursos, custos, horas, peso e medição, impacto) e **simulação**: o servidor recalcula as alterações propostas sem gravar; gravar é um ato humano separado.
- **Linha do tempo** de marcos e fases; **EAP e ponderação** por pontos com memória de peso.
- **Recursos**: catálogo, alocação por atividade, histograma empilhado, mapa de carga por semana e **superalocações** com nivelamento por adiamento ou aceite justificado.
- **Custos**: orçamento por atividade e categoria, ledger de custo real e curva VP/VA/CR.
- **Importação** de **MS Project** (`.mpp` via MPXJ e XML), **Primavera P6** (`.xer` e XML) e máscara XLSX, com validador de achados e aplicação ao cronograma; **modelos XLSX por módulo** (baixar com dados, editar e reenviar, tudo-ou-nada).

### Controlar
- **Medição por critério de etapas**, cortes semanais aos sábados, evidência obrigatória e estorno; ledger com hash encadeado.
- **Boletim de medição (BM)** quinzenal ou mensal, por nível de EAP, com medição anterior, do período, acumulada e saldo em R$.
- **RDO — diário de obra** pré-preenchido pelo cronograma (serviços, efetivo, equipamentos), aprovação em duas etapas (planejamento → gerência) e PDF no modelo do anexo V.
- **Apontamento de horas** em grade semanal, produtividade, projeção de esforço e **burndown** por tarefa ou do projeto.
- **Previsto × Realizado** em quatro dimensões (avanço, prazo, horas e custo) com **14 regras de coerência** e índice de coerência.

### Reportar
- **Cockpit**: previsto × realizado por linha de base, IDP, término projetado pelo ritmo, saúde do projeto e avanço por fase.
- **Status Report** gerencial consolidado (cronograma, cockpit, recursos, custos e coerência).
- **Exportação PDF e XLSX** de Status Report, cronograma (Gantt desenhado), medições, EAP, recursos, custos, apontamentos e previsto × realizado, além dos PDFs de boletim e RDO.
- **Templates e manual**: anexos do procedimento, guias por papel, glossário, dicionário das recusas e tour das telas.

### Plataforma
- Login com token opaco revogável (12 h por padrão), senhas com PBKDF2 e troca obrigatória da senha provisória.
- Papéis por organização: **Leitor**, **Fiscal** (aprova linha de base), **Planejador** (grava), **Admin** (usuários) e **Admin master** (cria organizações) — política centralizada em `backend/app/infra/tenancy.py`.
- PWA instalável (Workbox): o shell fica em cache, a API **nunca** é cacheada. Tema claro, escuro ou do sistema.

## Telas

| Cronograma (Gantt + CPM) | Cockpit |
|---|---|
| ![Cronograma](.github/telas/cronograma.png) | ![Cockpit](.github/telas/cockpit.png) |
| **Recursos e histograma** | **Boletim de medição** |
| ![Recursos](.github/telas/recursos.png) | ![Boletim de medição](.github/telas/boletim-medicao.png) |

> Capturas feitas numa instância local com o projeto de demonstração **CT-200-COGER** (dados fictícios, gerados por `scripts/demo_v2.py`).

## Arquitetura

```mermaid
flowchart LR
  U[Navegador / PWA<br/>React 19 · bundle em web/] -->|HTTPS| C[Caddy<br/>arquivos estáticos + CSP]
  C -->|/api/* · /saude| A[FastAPI · uvicorn<br/>127.0.0.1:8800]
  subgraph API [backend/app]
    A --> R[api/<br/>rotas + Pydantic v2]
    R --> D[dominio/<br/>CPM · curva S · EVM · coerência<br/>puro, Decimal, sem I/O]
    R --> I[importacao/<br/>MPP · XER · XML · XLSX]
    R --> X[infra/<br/>sessão · RLS · PDF/XLSX]
  end
  X -->|SET LOCAL ROLE gps_app<br/>app.organizacao_id| P[(PostgreSQL 16<br/>schema plan<br/>RLS forçada + invariantes)]
  I -.->|.mpp| J[JVM + MPXJ<br/>opcional]
```

- **Cálculo só no servidor**: `POST /simular` calcula sobre alterações propostas sem gravar; `PUT` grava, recalcula e guarda a memória de cálculo (`cronograma_calculo`, append-only).
- **Isolamento**: cada requisição autenticada abre uma transação com `SET LOCAL ROLE gps_app` e `app.organizacao_id`; a view `plan.v_rls_auditoria` lista tabelas sem RLS forçada e deve ficar vazia.
- **Red-team**: `db/002_red_team.sql`, `db/005_red_team_rls.sql` e `db/007_red_team_cronograma.sql` tentam violar as invariantes — todo bloco deve falhar.

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (asyncio) + asyncpg, Alembic |
| Banco | PostgreSQL 16 (extensões `btree_gist` e `pgcrypto`), RLS, triggers e constraints diferidas |
| Relatórios | reportlab (PDF) e openpyxl (XLSX) |
| Importação | MPXJ + JPype (MS Project `.mpp`, opcional), parsers próprios de XER/XML/XLSX |
| Frontend | React 19, TypeScript, Vite, PWA (Workbox), lucide; fundo do login com three.js + GSAP |
| Implantação | systemd + uvicorn atrás do Caddy ([`deploy/vps/`](deploy/vps/)) |

## Como rodar localmente

Requisitos: **Python 3.12** e **PostgreSQL 14+** (produção usa 16; o caminho abaixo foi testado também com 14). Docker é opcional.

```bash
git clone https://github.com/SSBianchiplan/project.git
cd project

# 1. banco (Postgres 16 em 127.0.0.1:5433) — ou aponte as variáveis para um Postgres seu
docker compose up -d db

# 2. backend
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
export GPS_DSN=postgresql+asyncpg://gps:gps@127.0.0.1:5433/gplansis
export GPS_DSN_SYNC=postgresql+psycopg://gps:gps@127.0.0.1:5433/gplansis
export GPS_DSN_SEED=postgresql://gps:gps@127.0.0.1:5433/gplansis
(cd backend && ../.venv/bin/alembic upgrade head)

# 3. admin master (dados fictícios)
.venv/bin/python scripts/bootstrap_admin.py admin@exemplo.com --senha 'TroqueEstaSenha#1' --nome Administrador

# 4. API
.venv/bin/uvicorn app.main:app --app-dir backend --port 8001 &

# 5. frontend (bundle de web/ + repasse de /api para a API, como o Caddy faz em produção)
.venv/bin/python scripts/servir_web.py           # http://127.0.0.1:8080
```

Abra <http://127.0.0.1:8080/?entrar> e entre com o e-mail e a senha do passo 3.

**Projeto de demonstração** (contrato fictício CT-200-COGER, 81 atividades, 23 recursos, BL0 + BL1, medições, horas e custos — leva segundos):

```bash
GPS_API=http://127.0.0.1:8001 GPS_ADMIN_EMAIL=admin@exemplo.com GPS_ADMIN_SENHA='TroqueEstaSenha#1' \
  .venv/bin/python scripts/demo_v2.py --somente     # ATENÇÃO: --somente apaga os contratos existentes
```

Outros roteiros de verificação contra a API viva: `scripts/smoke_auth.py`, `smoke_apontamentos.py`, `smoke_custos.py`, `smoke_api.py` e `smoke_projeto.py` (estes dois exigem `GPS_PERMITIR_HEADER_ORG=1`). Leitura de `.mpp` exige JVM: `pip install jpype1 mpxj` (sem JVM a rota responde 501 com a instrução e o caminho é o XML do Project).

Notas técnicas, gates de CI e o histórico de cada onda estão em [`docs/ONDAS.md`](docs/ONDAS.md) e [`docs/INSTALL.md`](docs/INSTALL.md).

### Em produção
`deploy/vps/instalar.sh` (idempotente, roda no servidor) cria usuário de sistema, banco com papel `gps_app`, venv Python 3.12, `.env`, serviço systemd e bloco do Caddy; `deploy/vps/enviar.sh` empacota e envia o código a partir da máquina de desenvolvimento (`VPS=usuario@servidor ADMIN_EMAIL=… bash deploy/vps/enviar.sh`).

## Estrutura de pastas

```
project/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI: rotas, CORS, tratamento de invariantes (422)
│   │   ├── api/               # rotas e schemas (contratos, cronograma, medição, RDO, BM, relatórios…)
│   │   ├── dominio/           # núcleo puro: CPM, curva S, ponderação, custos, recursos, coerência
│   │   ├── importacao/        # MPP, XER, XML (Project e P6), máscara XLSX, validador
│   │   ├── infra/             # banco, autenticação, multiempresa (RLS), exportação PDF/XLSX
│   │   └── estaticos/         # logo dos relatórios
│   ├── alembic/               # migrations 0001…0010 (SQL explícito, reversíveis)
│   ├── requirements.txt       # dev (inclui pytest, mypy, ruff)
│   └── requirements-prod.txt  # produção, versões fixadas
├── db/                        # DDL de referência (001…013) e scripts de red-team
├── scripts/                   # bootstrap do admin, demo, smokes, gates de CI, servidor local
├── templates/                 # procedimento PRO-GPS-PLN-001, anexos e guias (servidos pela API)
├── web/                       # bundle compilado do frontend (PWA) + capturas da apresentação
├── deploy/vps/                # instalar.sh, enviar.sh, serviço systemd e bloco do Caddy
├── docs/                      # notas de desenvolvimento por onda e guia de instalação original
├── docker-compose.yml         # Postgres 16 de desenvolvimento
└── .env.example
```

> **Sobre o frontend:** este repositório traz o **bundle compilado** do frontend (`web/`), idêntico ao que está em produção. O código-fonte React/TypeScript (`frontend/src`) e a suíte `backend/tests` ainda não foram publicados; por isso o passo SELF-CHECK de `scripts/ci_gates.sh` não roda aqui. Publicá-los é uma das prioridades — veja **Contribua**.

## Variáveis de ambiente

| Variável | Uso | Padrão |
|---|---|---|
| `GPS_DSN` | Conexão async da API (`postgresql+asyncpg://…`) | `…@localhost:5432/gplansis` |
| `GPS_DSN_SYNC` | Conexão das migrations Alembic (`postgresql+psycopg://…`) | `…@localhost:5432/gplansis` |
| `GPS_DB_ROLE` | Papel assumido por transação para a RLS valer | `gps_app` |
| `GPS_CORS_ORIGENS` | Origens extras do frontend, separadas por vírgula | `http://localhost:5173,…` |
| `GPS_PERMITIR_HEADER_ORG` | `1` aceita `X-Organizacao` sem token (**só desenvolvimento**) | `0` |
| `GPS_SESSAO_HORAS` | Validade do token de sessão | `12` |
| `GPS_PORT`, `GPS_WORKERS` | Porta e workers do uvicorn no serviço systemd | `8800`, `2` |
| `JAVA_HOME` | JVM para ler `.mpp` (opcional) | — |
| `GPS_DSN_SEED`, `GPS_API`, `GPS_ADMIN_EMAIL`, `GPS_ADMIN_SENHA` | Scripts de bootstrap, demo e smokes | — |
| `GPS_WEB_PORTA`, `GPS_WEB_DIR` | `scripts/servir_web.py` | `8080`, `web/` |

Modelo em [`.env.example`](.env.example). Nunca versione o `.env`.

## Contribua

O GPlanSiS|Project quer ser a ferramenta de planejamento que um planejador sênior confia: cálculo verificável, regras no banco e nada de número digitado onde deveria haver número calculado. **Toda ajuda conta** — de uma correção de texto a um novo importador.

**Por onde começar (boas primeiras issues):**
- 🧪 **Testes automatizados** do domínio (`backend/app/dominio`) com `pytest` e oráculos calculados à mão: CPM, curva S, ponderação, custos, coerência.
- 🛠️ **CI no GitHub Actions**: Postgres de serviço, `alembic upgrade → downgrade → upgrade`, red-team (`db/*red_team*.sql`), `ruff` e `mypy --strict`.
- 🧹 **Lint e tipos**: há 2 avisos de `ruff` (linhas longas em `alembic/versions/0010_…`) e 1 de `mypy` (stub ausente do MPXJ em `importacao/mpp.py`).
- 🔁 **Interoperabilidade**: testar importação de arquivos reais (anonimizados) do MS Project e do Primavera P6 e corrigir o que não abrir bem.
- 📄 **Relatórios**: novos PDFs/XLSX (ex.: curva S por disciplina, relatório mensal do anexo III preenchido).
- 🌐 **Internacionalização**: hoje tudo é pt-BR; extrair textos da API e dos relatórios para inglês e espanhol.
- 📚 **Documentação**: tutoriais do ciclo semanal (medir → boletim → status report) e do red-team.
- 🧭 **Frontend**: assim que o código-fonte for publicado, acessibilidade do Gantt por teclado e uso em tablet no canteiro.

Leia o [guia de contribuição](CONTRIBUTING.md), abra uma [issue](../../issues/new/choose) para combinar a ideia e mande seu pull request. Siga o [código de conduta](CODE_OF_CONDUCT.md). Encontrou uma falha de segurança? Veja a [política de segurança](SECURITY.md) — não abra issue pública.

## Licença

Código próprio sob a **[licença MIT](LICENSE)** — Copyright (c) 2026 GPlanSiS.

Componentes de terceiros incluídos no bundle compilado (`web/assets/`) mantêm suas próprias licenças: **React** (MIT), **three.js** (MIT), **lucide** (ISC), **Workbox** (MIT) e **GSAP 3.15** (GreenSock Standard License — <https://gsap.com/standard-license>, gratuita; o aviso de copyright está preservado no arquivo). Dependências Python instaladas por `pip`: FastAPI (MIT), uvicorn (BSD-3-Clause), Pydantic (MIT), SQLAlchemy (MIT), asyncpg (Apache-2.0), Alembic (MIT), psycopg (LGPL-3.0, usado como biblioteca, não incluído no repositório), openpyxl (MIT), reportlab (BSD), JPype (Apache-2.0) e MPXJ (LGPL-2.1, opcional, não incluído).

Os documentos do procedimento em `templates/` (PDF, DOCX, XLSX e HTML) são material da GPlanSiS distribuído com o sistema sob a mesma licença.

## Créditos

Desenvolvido pela **[GPlanSiS|Group](https://gplansis.com)** · contato: [contato@gplansis.com](mailto:contato@gplansis.com)

<p align="center"><a href="https://project.gplansis.com"><b>project.gplansis.com</b></a></p>
