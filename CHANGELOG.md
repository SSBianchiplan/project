# Changelog

Histórico resumido a partir das "ondas" de desenvolvimento (detalhes técnicos em [docs/ONDAS.md](docs/ONDAS.md)).
O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [Não publicado]
### Segurança
- Administração de usuários restrita à organização ativa: ADMIN só altera, redefine senha ou concede papel a quem
  já tem papel na organização; usuário que pertence a várias organizações só o admin master altera.
- Explorador de tabelas com lista explícita de tabelas de contrato (todas com RLS forçada); as tabelas de identidade
  ficam fora, e o papel `gps_app` perde o acesso a elas (migration `0011`).
- Limite de tentativas de login por conta e por IP (`GPS_LOGIN_MAX_CONTA`, `GPS_LOGIN_MAX_IP`, `GPS_LOGIN_JANELA_MIN`).
- Erros internos do Postgres deixam de ir ao cliente (só recusas de invariante, integridade e dado inválido).
- Pillow fixado em 13.0.0; modelo do Caddy com HSTS, Permissions-Policy e teto de corpo da requisição.
- Testes de integração em `backend/tests/` (exigem Postgres migrado; ver `tests/conftest.py`).
### Adicionado
- Publicação do código no GitHub: README, capa, guias de contribuição, licença MIT, `.env.example`,
  `docker-compose.yml` (Postgres de desenvolvimento) e `scripts/servir_web.py` (servidor local que faz o papel do Caddy).
### Alterado
- Scripts de demonstração, smokes e deploy deixam de ter e-mail, servidor e senha padrão embutidos: tudo vem de variáveis de ambiente.
- Capturas da página de apresentação (`web/apresentacao/`) e do tour refeitas com usuário fictício.

## v0.12 — em produção em project.gplansis.com (setembro de 2026)
### Onda 13 — burndown, boletim de medição, MPP e RDO em passos
- Burndown de horas por tarefa e do projeto inteiro, com filtro por função.
- Boletim de medição (BM) append-only, quinzenal ou mensal, até 5 níveis, com PDF.
- Leitura de `.mpp` via MPXJ; RDO editável antes do envio e emissão em 6 passos.
### Onda 12 — GPlanSiS|Project e modelos por módulo
- Nome do produto passa a ser GPlanSiS|Project.
- Modelos XLSX por módulo (recursos, alocações, custos, atributos, rede, custo real, medições, apontamentos), com validação tudo-ou-nada.
### Onda 11 — RDO, salvar global, nivelamento e apresentação
- RDO com fluxo de aprovação (planejamento → gerência) e PDF; superalocações com aceite justificado e nivelamento por adiamento.
- PDFs com gráficos e Gantt desenhado; botão global "Salvar alterações"; página de apresentação pública.
### Onda 10 — design system v2 e projeto DEMO v2
- Tokens visuais, preferências do usuário, toasts e diálogos próprios; EAP viva (criar, indentar, recuar, excluir); demo CT-200 semeado pela API.
### Onda 9 — apontamentos de horas e Previsto × Realizado
- Ledger de apontamentos com estorno; 14 regras de coerência entre prazo, avanço, esforço e custo.
### Onda 8 — Status Report e exportações
- Status Report gerencial; exportação PDF (reportlab) e XLSX (openpyxl) de 8 relatórios; templates do procedimento.
### Onda 7 — custos
- Orçamento por atividade, ledger de custo real e indicadores de valor agregado (VP, VA, CR, IDC, ENT).
### Onda 6 — acesso e recursos
- Usuários, papéis por organização e sessões; catálogo de recursos, alocação, histograma e carga; tema claro/escuro.
### Onda 5 — projeto do zero
- Assistente em 4 etapas (contrato → EAP → rede e calendário → linha de base BL0 com LVP).
### Onda 4 — cronograma avançado
- CPM no servidor (calendário de dias úteis, vínculos TI/II/TT/IT, restrições, folgas, caminho crítico), simulação sem gravação, cockpit com curva S e IDP.
### Ondas 1 a 3 — fundação
- Núcleo com invariantes no PostgreSQL, multiempresa com RLS forçada, importação de MS Project/Primavera, medição por etapas e migrations reversíveis.
