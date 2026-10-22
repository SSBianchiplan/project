-- =====================================================================
-- RED-TEAM parte 2 — isolamento entre organizações e importação.
-- Roda como o papel gps_app, que é o que a aplicação usa. Rodar como
-- superusuário invalida o teste: BYPASSRLS ignora toda política.
--   psql -d gplansis -v ON_ERROR_STOP=0 -f db/005_red_team_rls.sql
-- =====================================================================
SET search_path = plan, public;

-- ---------------------------------------------------------- preparação
INSERT INTO organizacao (codigo, razao_social) VALUES ('ORG-A', 'Cliente A') ON CONFLICT DO NOTHING;
INSERT INTO organizacao (codigo, razao_social) VALUES ('ORG-B', 'Cliente B') ON CONFLICT DO NOTHING;

INSERT INTO contrato (organizacao_id, codigo, contratante, contratada, empreendimento,
                      modalidade, data_inicio, termino_contratual)
SELECT id, 'CT-A', 'A', 'Fornecedor', 'Planta A', 'EPC', DATE '2026-01-04', DATE '2026-12-31'
  FROM organizacao WHERE codigo = 'ORG-A' ON CONFLICT DO NOTHING;

INSERT INTO contrato (organizacao_id, codigo, contratante, contratada, empreendimento,
                      modalidade, data_inicio, termino_contratual)
SELECT id, 'CT-B', 'B', 'Fornecedor', 'Planta B', 'EPC', DATE '2026-01-04', DATE '2026-12-31'
  FROM organizacao WHERE codigo = 'ORG-B' ON CONFLICT DO NOTHING;

SET ROLE gps_app;

-- RT-19 | INV-ORG-001 — sem organização declarada, nada é visível
RESET app.organizacao_id;
SELECT 'RT-19', count(*) AS contratos_visiveis_sem_org FROM contrato;
-- ESPERADO: 0

-- RT-20 | INV-ORG-001 — organização A não enxerga contrato de B
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-A'), true);  -- equivale a SET LOCAL
SELECT 'RT-20', count(*) FILTER (WHERE codigo = 'CT-B') AS vazamento FROM contrato;
-- ESPERADO: 0
COMMIT;

-- RT-21 | INV-ORG-001 — A não consegue inserir EAP no contrato de B
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-A'), true);  -- equivale a SET LOCAL
INSERT INTO eap_no (contrato_id, edt, descricao, nivel, tipo, folha, pontos)
SELECT id, '9', 'INVASÃO', 1, 'ATIVIDADE', true, 1000000
  FROM contrato WHERE codigo = 'CT-B';
COMMIT;   -- ESPERADO: falha (WITH CHECK da policy) ou zero linhas inseridas

-- RT-22 | INV-ORG-002 — SET sem LOCAL vaza para a próxima transação
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-B'), true);  -- equivale a SET LOCAL
COMMIT;
SELECT 'RT-22', count(*) AS contratos_apos_commit FROM contrato;
-- ESPERADO: 0 — se vier 1, alguém trocou is_local=true por false (SET em vez de SET LOCAL)

-- RT-23 | INV-ORG-001 — nenhuma tabela pode estar sem RLS forçada
RESET ROLE;
SELECT 'RT-23', count(*) AS tabelas_sem_rls FROM v_rls_auditoria;
-- ESPERADO: 0 — este é o gate de CI

SET ROLE gps_app;

-- ---------------------------------------------------- importação (004)
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-A'), true);  -- equivale a SET LOCAL

-- RT-24 | INV-IMP-003 — importação carregando avanço realizado
INSERT INTO importacao (contrato_id, formato, arquivo_nome, arquivo_sha256, arquivo_uri,
                        estado, plano_bruto, criado_por)
SELECT id, 'MSPROJECT_XML', 'c.xml', digest('a', 'sha256'), 's3://x', 'VALIDADO',
       '{"avanco_realizado": [{"edt": "1.1", "pct": 0.5}]}'::jsonb, 'rt'
  FROM contrato WHERE codigo = 'CT-A';
COMMIT;   -- ESPERADO: falha INV-IMP-003

-- RT-25 | INV-IMP-002 — aplicar importação com achado bloqueante
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-A'), true);  -- equivale a SET LOCAL
INSERT INTO importacao (contrato_id, formato, arquivo_nome, arquivo_sha256, arquivo_uri,
                        estado, plano_bruto, achados, aplicada_por, aplicada_em, criado_por)
SELECT id, 'P6_XER', 'c.xer', digest('b', 'sha256'), 's3://y', 'APLICADO',
       '{"atividades": []}'::jsonb,
       '[{"invariante":"INV-CRN-002","severidade":"BLOQUEANTE","referencia":"1->2","detalhe":"lag -10"}]'::jsonb,
       'planejador', now(), 'rt'
  FROM contrato WHERE codigo = 'CT-A';
COMMIT;   -- ESPERADO: falha ck_importacao_achados

-- RT-26 | INV-IMP-001 — aplicar sem quem aplicou
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-A'), true);  -- equivale a SET LOCAL
INSERT INTO importacao (contrato_id, formato, arquivo_nome, arquivo_sha256, arquivo_uri,
                        estado, plano_bruto, criado_por)
SELECT id, 'P6_XER', 'c2.xer', digest('c', 'sha256'), 's3://z', 'APLICADO',
       '{"atividades": []}'::jsonb, 'rt'
  FROM contrato WHERE codigo = 'CT-A';
COMMIT;   -- ESPERADO: falha ck_importacao_aplicada

-- ---------------------------------------------------- documentos (004)
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-A'), true);  -- equivale a SET LOCAL
INSERT INTO eap_no (contrato_id, edt, descricao, nivel, tipo, folha, pontos)
SELECT id, '1', 'ENGENHARIA', 1, 'ATIVIDADE', true, 1000000 FROM contrato WHERE codigo = 'CT-A';
COMMIT;

-- RT-27 | INV-DOC-001 — documentos que não fecham nos pontos do nó
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-A'), true);  -- equivale a SET LOCAL
INSERT INTO documento_engenharia (contrato_id, eap_no_id, codigo, titulo, disciplina,
                                  hh_estimadas, pontos, memoria_peso)
SELECT n.contrato_id, n.id, 'FLX-001', 'Fluxograma', 'Processo', 100, 500000,
       'estimativa de 100 hh a 5000 pontos por hh'
  FROM eap_no n JOIN contrato c ON c.id = n.contrato_id WHERE c.codigo = 'CT-A' AND n.edt = '1';
COMMIT;   -- ESPERADO: falha INV-DOC-001 (500.000 de 1.000.000)

-- RT-28 | INV-DOC-002 — documento sem memória de cálculo do peso
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-A'), true);  -- equivale a SET LOCAL
INSERT INTO documento_engenharia (contrato_id, eap_no_id, codigo, titulo, disciplina,
                                  hh_estimadas, pontos, memoria_peso)
SELECT n.contrato_id, n.id, 'FLX-002', 'Fluxograma', 'Processo', 100, 1000000, 'ok'
  FROM eap_no n JOIN contrato c ON c.id = n.contrato_id WHERE c.codigo = 'CT-A' AND n.edt = '1';
COMMIT;   -- ESPERADO: falha ck_doc_memoria

-- base para RT-29: documento que fecha os pontos do nó e um evento de estágio
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-A'), true);  -- equivale a SET LOCAL
INSERT INTO documento_engenharia (contrato_id, eap_no_id, codigo, titulo, disciplina,
                                  hh_estimadas, pontos, memoria_peso)
SELECT n.contrato_id, n.id, 'FLX-003', 'Fluxograma', 'Processo', 200, 1000000,
       'estimativa de 200 hh a 5000 pontos por hh'
  FROM eap_no n JOIN contrato c ON c.id = n.contrato_id WHERE c.codigo = 'CT-A' AND n.edt = '1';
INSERT INTO documento_evento (documento_id, estagio, revisao, evidencia, registrado_por)
SELECT id, 'INICIADO', '0A', 'GED-0001', 'rt' FROM documento_engenharia WHERE codigo = 'FLX-003';
COMMIT;   -- deve passar

-- RT-29 | P3 — histórico de estágio não se altera
BEGIN;
SELECT set_config('app.organizacao_id', (SELECT id::text FROM organizacao WHERE codigo = 'ORG-A'), true);  -- equivale a SET LOCAL
UPDATE documento_evento SET estagio = 'CERTIFICADO_SEM_PENDENCIA';
COMMIT;   -- ESPERADO: falha (privilégio revogado; por baixo, trigger append-only)

RESET ROLE;
