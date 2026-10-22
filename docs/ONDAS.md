> **Nota de publicação (out/2026):** este é o README de desenvolvimento original, mantido como histórico técnico das ondas.
> Algumas pastas citadas aqui **não estão neste repositório**: `frontend/` (código-fonte React — só o bundle compilado está em `web/`),
> `spec/` e `backend/tests/`. O `scripts/ci_gates.sh` depende de `backend/tests/` para o passo SELF-CHECK.
> Comandos com `.venv/Scripts/` são do Windows; no Linux/macOS use `.venv/bin/`.

# GPlanSiS|Planejamento — sistema

Implementação do PRO-GPS-PLN-001 (procedimento) e dos anexos GPS-PLN-ANX-I a V.

```
spec/SPEC-GPS-PLN-SYS-001.md   invariantes RFC-2119, regras de cálculo, gates
db/001_core.sql                DDL com as invariantes embutidas (PostgreSQL 16)
db/002_red_team.sql            tentativas de violação — todo bloco deve falhar
backend/app/dominio/           núcleo puro: sem I/O, sem now(), Decimal
backend/app/api/               FastAPI + Pydantic v2 (fronteira)
backend/app/infra/             SQLAlchemy 2.0 async
backend/tests/                 oráculos calculados à mão + propriedades
frontend/                      React 19 + TS strict + Tailwind v4 + ECharts
```

## Subir

Passo a passo completo, variáveis e armadilhas de rede em [INSTALL.md](INSTALL.md).
Resumo: `docker compose up -d db` (porta 5433), `alembic upgrade head`,
`scripts/ci_gates.sh`, `uvicorn --port 8001`, `npm run dev`.

## Red-team

```bash
psql -d gplansis -v ON_ERROR_STOP=0 -f - < db/002_red_team.sql 2>&1   | grep 'ERROR:' | grep -vc 'transaction is aborted'   # esperado: 18
```

O 002 roda como superusuário (alvo: invariantes do núcleo). O 005 faz
`SET ROLE gps_app` (alvo: isolamento) — esperado: 6.

## O que o sistema não faz de propósito

- Não importa `% concluída` do MS Project como avanço. Avanço entra por medição com evidência.
- Não corrige ponderação sozinho: EAP que não fecha em 1.000.000 não é aceita.
- Não apaga nem edita medição nem RDO. Correção é estorno.
- Não projeta realizado além da data de corte.

## Onda 2 — importação, multiempresa e documentos

```
db/003_multiempresa.sql          organizacao, usuario_organizacao, RLS forçada, papel gps_app
db/004_documentos_importacao.sql documentos ponderados manualmente + staging de importação
backend/app/importacao/          parsers MPP · XER · XML Project · XML P6 · XLSX máscara
backend/app/dominio/documentos.py estágios de emissão e avanço documental
frontend/src/pages/ImportarCronograma.tsx  achados e human-gate de aplicação
```

Isolamento por organização: toda requisição declara `X-Organizacao` (provisório, até
o Keycloak) e a sessão faz `SET LOCAL app.organizacao_id` dentro da transação.
Verificação obrigatória no CI:

```sql
SELECT * FROM plan.v_rls_auditoria;   -- deve vir vazio
```

Leitura de MPP exige MPXJ:

```bash
pip install jpype1 mpxj      # requer JVM no host
```

Sem JVM, o endpoint devolve 501 com a instrução, e o caminho é pedir o XML nativo
do Project — que o parser lê sem dependência externa.

## Onda 3 — migrations reversíveis, red-team de RLS e medição por etapas

```
backend/alembic/                 0001 core · 0002 multiempresa · 0003 documentos/importação
db/005_red_team_rls.sql          isolamento entre organizações + importação + documentos
backend/tests/test_banco.py      invariantes contra Postgres real (pula sem GPS_DSN_TESTE)
scripts/ci_gates.sh              gate binário de CI
frontend/src/pages/LancarMedicao.tsx  medição por critério de etapas
```

### Gate de CI

```bash
PSQL="docker compose -p gplansis exec -T db psql -U gps" PY=".venv/Scripts/" GPS_DSN_TESTE=postgresql://gps:gps@localhost:5433/gplansis bash scripts/ci_gates.sh
```

Ele reprova o build se: a migration não reverter, algum teste falhar, `mypy --strict`
ou `ruff` acusarem, `plan.v_rls_auditoria` retornar qualquer linha, ou o red-team
recusar menos violações do que o esperado.

### Testes contra banco real

```bash
GPS_DSN_TESTE=postgresql://gps:gps@localhost:5433/gplansis pytest tests/test_banco.py
```

Roda contra o banco de dev já migrado; o gate faz downgrade/upgrade antes,
então o schema está limpo quando os testes chegam.

Rodar o red-team como superusuário invalida o teste: `BYPASSRLS` ignora toda política.
Use `SET ROLE gps_app`, como o script já faz.

## Onda 4 — cronograma avançado, cockpit, medição e EAP na tela

```
spec: INV-CRN-006..009, INV-IMP-005, RGR-CPM-001/002, RGR-CRV-002, RGR-IDP-001
backend/app/dominio/cronograma.py     calendário de dias úteis, CPM (TI/II/TT/IT, SNET/MSO),
                                      folgas, caminho crítico, achados, impacto, previsto linear
backend/app/api/rotas_cronograma.py   /cronograma · /simular · PUT /atividades · PUT /vinculos ·
                                      /cockpit · /eap · /eap/ponderar · /medicoes · /estorno
backend/app/api/servico_cronograma.py monta plano, aplica alterações propostas, traduz para schema
db/006_cronograma.sql                 atributos de atividade, feriado, baseline_atividade,
                                      cronograma_calculo (append-only), trigger anticiclo
db/007_red_team_cronograma.sql        8 tentativas de violação
frontend/src/                         Gantt com arraste e gaveta de 7 guias, cockpit (curva S,
                                      IDP, histograma), medição por etapas com estorno,
                                      importação com aplicação ao cronograma, EAP editável
scripts/semear_demo.py                contrato ÁREA 100 — ETANOL DE MILHO, BL0 e medições
scripts/smoke_api.py                  roteiro ponta a ponta dos fluxos de escrita (27 passos)
```

Simulação é `POST /simular`: o servidor calcula sobre as alterações propostas e nada é
gravado. Gravar (`PUT`) é ato humano separado, recalcula tudo e deixa memória de cálculo em
`cronograma_calculo`. O navegador não tem CPM próprio.

```bash
GPS_DSN_SEED=postgresql://gps:gps@localhost:5433/gplansis python scripts/semear_demo.py --reset
GPS_ORG=<uuid> GPS_CT=<uuid> python scripts/smoke_api.py
```

## Onda 5 — projeto do zero

```
backend/app/api/rotas_projeto.py   POST /contratos · PUT /contratos/{id} ·
                                   POST /contratos/{id}/estrutura/validar · PUT /contratos/{id}/estrutura ·
                                   POST /contratos/{id}/baselines
frontend/src/pages/NovoProjeto.tsx assistente em 4 etapas: contrato → EAP e ponderação →
                                   rede e calendário → revisão, gravação e BL0
scripts/smoke_projeto.py           roteiro ponta a ponta do fluxo (17 passos)
```

A estrutura (EAP, atividades, rede, feriados) entra inteira em uma transação; a trigger
diferida INV-EAP-001 confere a soma no COMMIT. Enquanto o contrato está em elaboração
(sem baseline aprovada nem medição) ela pode ser regravada; depois, mudanças passam pela
gaveta do Gantt e pela reponderação. Aprovar a linha de base registra a LVP (nota ≥ 0,85),
congela as datas em `baseline_atividade`, distribui o previsto semanal (RGR-CRV-002) e
supera a baseline anterior sem tocar no previsto dela (INV-BL-003). O modelo do Anexo I
(seis fases, dois marcos, distribuição sugerida 8/47/15/25/5) é o ponto de partida padrão.

## Onda 6 — acesso, recursos, linha do tempo e consolidação da interface

```
db/008_usuarios.sql · 009_recursos.sql     usuário, papel por organização, sessão; recurso e alocação
backend/app/infra/auth.py · tenancy.py     pbkdf2, token opaco, política INV-ACL centralizada
backend/app/api/rotas_auth.py              /auth/login|logout|eu|organizacao|senha · /usuarios · /organizacoes
backend/app/api/rotas_recursos.py          catálogo, alocação por atividade, histograma e carga
backend/app/dominio/recursos.py            RGR-REC-001 com oráculos em tests/test_recursos.py
frontend: login com Gantt 3D animado (three + gsap, carregado sob demanda), menu lateral
          recolhível com ícones (lucide), avatar do usuário no topo, tema claro/escuro/sistema,
          Linha do tempo, Recursos (catálogo, carga de trabalho, histograma), Usuários,
          Configurações, painel de saúde no Cockpit, barras do Gantt por fase com responsável e %
scripts/bootstrap_admin.py · smoke_auth.py
```

```bash
python scripts/bootstrap_admin.py admin@exemplo.com --senha SENHA   # admin master
GPS_ADMIN_SENHA=SENHA python scripts/smoke_auth.py                          # 26 passos
```

Os smokes antigos (`smoke_api.py`, `smoke_projeto.py`) usam `X-Organizacao` e exigem a API
com `GPS_PERMITIR_HEADER_ORG=1`. Em produção essa chave fica desligada.

### PWA e identidade

`frontend/public/favicon.svg` e `icones/` trazem a marca: três barras de Gantt (a crítica em
vermelho) atravessadas pela curva S que sobe sobre a linha do tempo. `vite-plugin-pwa` gera
`manifest.webmanifest` e o service worker: a casca da aplicação fica em cache e atualiza
sozinha; a API nunca é cacheada (número de planejamento não pode vir velho). Atalhos do
ícone instalado abrem `/?aba=cronograma` e `/?aba=medicao`. Ícones regeneram com
`scripts`-free: o gerador está no histórico da sessão (Pillow) e pode ser recriado a partir
do SVG.

## Onda 7 — custos do projeto e consolidação do topo

```
db/010_custos.sql                         custo_item (orçamento direto por atividade) e custo_realizado (ledger)
backend/app/dominio/custos.py             RGR-CST-001: distribuição pelo CPM, VA, IDC, ENT, por fase e por categoria
backend/app/api/rotas_custos.py           GET /contratos/{id}/custos · PUT /atividades/{id}/custos · POST /custos-realizados
frontend/src/pages/Custos.tsx             cartões EVM, curva VP/VA/CR, por fase da EAP, por categoria, por atividade, ledger
frontend/src/components/CustosAtividade.tsx  guia Custos da gaveta do Gantt
scripts/smoke_custos.py                   12 passos
```

Menu lateral só navega; conta, organização, tema, configurações e usuários vivem no avatar do
topo. O botão de tema no topo é só ícone e alterna sistema → claro → escuro.

## Onda 8 — Status Report, exportação, templates, base de dados e gráficos

```
backend/app/infra/exportar.py             modelo Relatorio → PDF (reportlab) e XLSX (openpyxl); cabeçalho com logo,
                                          sistema e projeto em toda página; retrato (Status Report) ou paisagem (tabelas)
backend/app/api/relatorios.py             status_report(): consolida cronograma, cockpit, recursos e custos; 6 relatórios
backend/app/api/rotas_relatorios.py       /status-report · /exportar/{tipo}?formato=pdf|xlsx · /templates · /admin/tabelas
frontend/src/pages/StatusReport.tsx       dashboard gerencial gráfico e analítico, exportável
frontend/src/pages/Templates.tsx          anexos do procedimento (download) e manual de planejamento (PDF no leitor)
frontend/src/components/Graficos.tsx      gráficos redesenhados: largura real, área, grade, marcador no corte, legendas HTML
frontend/src/components/Exportar.tsx      botões PDF/XLSX em Cronograma, Cockpit, Medição, EAP, Recursos, Custos e Status
Configurações → Base de dados             catálogo do schema plan (RLS, colunas, linhas visíveis) com XLSX por tabela
```

Relatórios: `status-report`, `cronograma`, `medicoes`, `eap`, `recursos`, `custos`, `apontamentos`, `previsto-realizado`. O PDF repete
cabeçalho e rodapé (emissor, data, página) em todas as páginas; tabelas longas fluem com o
cabeçalho repetido. O XLSX traz capa e uma aba por seção, com os mesmos números.


## Onda 9 — apontamentos de horas e Previsto × Realizado

```
db/011_apontamentos.sql                   ledger plan.apontamento (append-only, estorno = horas negativas vinculadas),
                                          triggers INV-APT-001 (marco não recebe horas) e INV-APT-003 (mesmo contrato), RLS
backend/alembic/versions/0008_apontamentos.py
backend/app/dominio/apontamentos.py       RGR-APT-001: horas previstas × apontadas, produtividade, projeção e situação da tarefa
backend/app/dominio/coerencia.py          RGR-COE-001: 14 regras que confrontam prazo, avanço, esforço e custo; índice de coerência
backend/app/api/rotas_apontamentos.py     /contratos/{id}/apontamentos (painel semanal) · POST apontar · POST /apontamentos/{id}/estorno
                                          /contratos/{id}/previsto-realizado?data_corte=
frontend/src/pages/Apontamentos.tsx       grade semanal dom–sáb com clique para apontar, formulário, produtividade e ledger
frontend/src/pages/PrevistoRealizado.tsx  dimensões (avanço, prazo, horas, custo), achados de coerência, fases, marcos,
                                          recursos, categorias e atividades BL × vigente × real
frontend/src/components/HorasAtividade.tsx aba "Horas" na gaveta da atividade (apontamento rápido + resumo)
scripts/smoke_apontamentos.py             15 passos: apontar, marco, futuro, estorno, painel, PR, 4 exportações, status report
```

Horas previstas de uma tarefa = Σ alocações (qtd/dia × horas/dia do recurso × dias úteis); sem alocação,
usa `hh_estimadas` da gaveta. O Status Report ganhou o bloco "Coerência previsto × realizado" e os dois
módulos exportam PDF (paisagem) e XLSX. O índice de coerência vai de 1,00 (nenhum achado) a 0,00;
alertas pesam 1, atenções 0,5 e informações 0, sobre o número de atividades.

## Onda 10 — design system aplicado e projeto DEMO v2

```
designsystem.html                         design system v2 (tokens, primitivos, composição, domínio, governança) com painel ao vivo
frontend/src/estilos.css                  tokens v2: fundos suaves -f, --texto-2, 8 fases, --g-*, densidade/raio/contraste/movimento
frontend/src/estado/preferencias.ts       preferências visuais do usuário (acento, densidade, cantos, contraste, movimento) em <html data-*>
frontend/src/estado/toast.ts · Toasts.tsx toasts de ação concluída; Modal.tsx · Dialogo.tsx substituem prompt/confirm/alert
frontend/src/components/MenuLateral.tsx   menu agrupado Planejar / Controlar / Reportar
scripts/demo_v2.py                        DEMO v2 pela API: limpa contratos, semeia CT-200-COGER (80 linhas, 23 recursos, BL0+BL1,
                                          medições, apontamentos, custo real), verifica 18 passos, grava panorama_demo.html
panorama_demo.html                        panorama do demo com a seção "Resultado da execução" regravada a cada rodada
backend/app/api/rotas_estrutura.py        EAP viva: POST eap/tarefas · eap/{no}/indentar · eap/{no}/recuar · DELETE eap/{no} (RGR-EAP-EDIT-001..004)
frontend/src/components/MenuContexto.tsx  botão direito no Gantt: abrir, nova tarefa/subtarefa/marco, indentar, recuar, excluir
```

Gantt: rolagem única (grade presa à esquerda, escala presa ao topo), cores por nível n1–n5, até 5 níveis de EAP, tela cheia (F) e atalhos de teclado (?).
Recursos: abas (visão geral com histograma empilhado e tooltip, heatmap compacto, histogramas, alocações, catálogo); superalocações em modal com nivelamento por adiamento (POST recursos/nivelar, RGR-REC-002), aceite com justificativa e reabertura.

Correções descobertas pelo roteiro: FastAPI ≥ 0.118 fecha dependências `yield` depois de enviar a resposta, então o
commit da sessão chegava após o cliente ler a resposta — todas as rotas usam `Depends(sessao_*, scope="function")`;
o POST de apontamento buscava a linha recém-inserida entre as 50 mais recentes por data (falhava em datas antigas).
Scripts falam com `http://127.0.0.1:8001` (no Windows, `localhost` tenta `::1` primeiro e custa ~1,6 s por chamada).

## Onda 11 — RDO, salvar global, nivelamento, PDFs com gráficos e apresentação

```
db/012_rdo_eventos.sql · alembic 0009          eventos de aprovação do RDO (append-only, RLS)
backend/app/api/rotas_rdo.py                    GET/POST rdos · sugestão do dia · eventos · PDF (RGR-RDO-001)
backend/app/api/rotas_recursos.py               superalocações com aceite (excecao_contratual INV-REC-001) e nivelar em memória (RGR-REC-002)
backend/app/infra/exportar.py                   gráficos de barras (VerticalBarChart) e Gantt desenhado, paginado em fatias de 36 linhas
frontend/src/pages/Rdo.tsx                      lista, emissão pré-preenchida em abas, detalhe com aprovação e PDF
frontend/src/pages/Recursos.tsx                 abas · histograma geral com tooltip · heatmap compacto · modal de superalocações
frontend/src/estado/rascunho.ts · BotaoSalvar   "Salvar alterações (n)" no topo: gaveta, simulação, custos e alocações pendentes; sem pendência vira "Sincronizado"
frontend/src/pages/Apresentacao.tsx             página de apresentação (marketing) com prints reais e CTA; abre antes do login
frontend/scripts/capturar_telas.mjs             gera os prints (puppeteer-core + Chrome) em frontend/public/apresentacao/
```

Gantt: tela cheia (F), atalhos (?), foco por teclado, menu de contexto. Exportações: botões só com ícone em todas as páginas.

## Onda 12 — GPlanSiS|Project e modelos por módulo

Nome do sistema: **GPlanSiS|Project** (produto GPlanSiS|Group, gplansis.com). O código do procedimento não aparece mais na interface nem no PDF.
`backend/app/api/rotas_modelos.py`: `GET /modelos`, `GET /contratos/{id}/modelos/{modelo}[?vazio=1]` (XLSX com aba Dados pré-preenchida e aba Instruções),
`POST /contratos/{id}/modelos/{modelo}` (multipart; tudo-ou-nada; 422 com erros por linha). Modelos: recursos (upsert), alocações e itens de custo
(substituir por EDT), atributos de atividades e pontos (atualizar), vínculos (substituir a rede), custo realizado, medições e apontamentos (anexar, ledgers).
Tela: Importação → "Modelos por módulo" (`frontend/src/components/ModelosModulos.tsx`).

## Onda 13 — burndown por tarefa, boletim de medição, MPP e RDO em passos

```
backend/app/api/rotas_apontamentos.py   GET apontamentos/burndown?edt=&recurso_id= (RGR-APT-002; edt=* = projeto inteiro)
backend/app/api/rotas_boletim.py        GET boletins/previa · GET/POST boletins · GET /boletins/{id}(/pdf) (RGR-BM-001, INV-BM-001/002)
db/013 · alembic 0010                   boletim_medicao (append-only) · RDO editável antes do envio (fn_rdo_editavel, evento EDITADO)
backend/app/api/rotas_rdo.py            PUT /rdos/{id} (RGR-RDO-002)
backend/app/importacao/mpp.py           MPP via MPXJ 14 (org.mpxj) com JRE do usuário (~/.jre, pacote install-jdk) — sem Java no PATH
frontend: Apontamentos → aba Burndown por tarefa · Medição → aba Boletim de medição (avançar folhas pelo BM) · RDO em 6 passos com revisão e edição
```

MPP: `pip install jpype1 mpxj install-jdk` e `python -c "import jdk; jdk.install('17', jre=True)"` instalam o leitor e um JRE em `~/.jre`; `mpp.py` acha a JVM sozinho (JAVA_HOME também vale).
Gaveta lateral: conteúdo sempre dentro da largura e acima do Gantt em tela cheia.

## Deploy (project.gplansis.com)

Mesmo método do GPlanSiS|ML: `tar | ssh` para o VPS do hub , Postgres do host, uvicorn atrás do Caddy.
Arquivos em `deploy/vps/`: `enviar.sh` (do Windows), `instalar.sh` (idempotente, roda no VPS), `gpsproject.caddy`, `gpsproject.service`.

```bash
cd frontend && npm run build && cd ..            # .env.production → API na mesma origem (/api)
PRIMEIRA=1 DEMO=1 bash deploy/vps/enviar.sh      # 1ª vez: instala tudo e semeia o DEMO v2
bash deploy/vps/enviar.sh                        # atualizações: código + dist, alembic upgrade, restart
```

No VPS: `/opt/gplansis-project` (código, `.venv` Python 3.12 via uv, `web/`), banco `gplansis_project`
(dono `gpsproject`, papel `gps_app` para RLS), serviço `gpsproject` em 127.0.0.1:8800, bloco Caddy em
`/etc/caddy/gpsproject.caddy`. Operar: `systemctl status gpsproject`, `journalctl -u gpsproject -f`.
