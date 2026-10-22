-- =====================================================================
-- RED-TEAM parte 3 — cronograma (006). Cada bloco tenta violar uma
-- invariante direto no banco; todo bloco deve falhar.
--   psql -d gplansis -v ON_ERROR_STOP=0 -f - < db/007_red_team_cronograma.sql
-- Roda como superusuário: o alvo são triggers e constraints, não RLS.
-- =====================================================================
SET search_path = plan, public;

-- ---------------------------------------------------------- preparação
BEGIN;
INSERT INTO organizacao (codigo, razao_social) VALUES ('RT-CRN', 'Red-team cronograma')
    ON CONFLICT DO NOTHING;
INSERT INTO contrato (organizacao_id, codigo, contratante, contratada, empreendimento,
                      modalidade, data_inicio, termino_contratual)
SELECT id, 'RT-CRN-1', 'X', 'Y', 'Rede', 'EPC', DATE '2026-01-05', DATE '2026-12-31'
  FROM organizacao WHERE codigo = 'RT-CRN';
WITH c AS (SELECT id FROM contrato WHERE codigo = 'RT-CRN-1')
INSERT INTO eap_no (contrato_id, parent_id, edt, descricao, nivel, tipo, folha, pontos)
SELECT c.id, NULL, '1', 'RAIZ', 1, 'RESUMO', false, 0 FROM c;
WITH c AS (SELECT id FROM contrato WHERE codigo = 'RT-CRN-1'),
     p AS (SELECT id FROM eap_no WHERE edt = '1' AND contrato_id = (SELECT id FROM c))
INSERT INTO eap_no (contrato_id, parent_id, edt, descricao, nivel, tipo, folha, pontos)
SELECT c.id, p.id, x.edt, x.d, 2, x.t::tipo_no, true, x.pts FROM c, p,
       (VALUES ('1.1', 'A', 'ATIVIDADE', 500000), ('1.2', 'B', 'ATIVIDADE', 500000),
               ('1.3', 'C', 'ATIVIDADE', 0), ('1.4', 'M', 'MARCO', 0)) AS x(edt, d, t, pts);
INSERT INTO atividade (contrato_id, eap_no_id, duracao_dias, inicio_previsto, termino_previsto, tipo)
SELECT n.contrato_id, n.id, 5, DATE '2026-01-05', DATE '2026-01-09', 'ATIVIDADE'
  FROM eap_no n JOIN contrato c ON c.id = n.contrato_id
 WHERE c.codigo = 'RT-CRN-1' AND n.edt IN ('1.1', '1.2', '1.3');
INSERT INTO atividade (contrato_id, eap_no_id, duracao_dias, inicio_previsto, termino_previsto, tipo)
SELECT n.contrato_id, n.id, 0, DATE '2026-01-05', DATE '2026-01-05', 'MARCO'
  FROM eap_no n JOIN contrato c ON c.id = n.contrato_id
 WHERE c.codigo = 'RT-CRN-1' AND n.edt = '1.4';
-- rede A -> B -> C
INSERT INTO vinculo (predecessora_id, sucessora_id, tipo, lag_dias)
SELECT a.id, b.id, 'TI', 0
  FROM atividade a JOIN eap_no na ON na.id = a.eap_no_id
  JOIN atividade b ON true JOIN eap_no nb ON nb.id = b.eap_no_id
  JOIN contrato c ON c.id = na.contrato_id AND c.id = nb.contrato_id
 WHERE c.codigo = 'RT-CRN-1' AND ((na.edt, nb.edt) IN (('1.1', '1.2'), ('1.2', '1.3')));
COMMIT;   -- deve passar

-- RT-30 | INV-CRN-006 — vínculo C -> A fecha ciclo
BEGIN;
INSERT INTO vinculo (predecessora_id, sucessora_id, tipo, lag_dias)
SELECT a.id, b.id, 'TI', 0
  FROM atividade a JOIN eap_no na ON na.id = a.eap_no_id
  JOIN atividade b ON true JOIN eap_no nb ON nb.id = b.eap_no_id
  JOIN contrato c ON c.id = na.contrato_id AND c.id = nb.contrato_id
 WHERE c.codigo = 'RT-CRN-1' AND na.edt = '1.3' AND nb.edt = '1.1';
COMMIT;   -- ESPERADO: falha INV-CRN-006

-- RT-31 | INV-CRN-009 — segunda atividade na mesma folha
BEGIN;
INSERT INTO atividade (contrato_id, eap_no_id, duracao_dias, inicio_previsto, termino_previsto, tipo)
SELECT n.contrato_id, n.id, 3, DATE '2026-01-05', DATE '2026-01-07', 'ATIVIDADE'
  FROM eap_no n JOIN contrato c ON c.id = n.contrato_id
 WHERE c.codigo = 'RT-CRN-1' AND n.edt = '1.1';
COMMIT;   -- ESPERADO: falha uq_atividade_no

-- RT-32 | INV-CRN-009 — atividade em nó resumo
BEGIN;
INSERT INTO atividade (contrato_id, eap_no_id, duracao_dias, inicio_previsto, termino_previsto, tipo)
SELECT n.contrato_id, n.id, 3, DATE '2026-01-05', DATE '2026-01-07', 'ATIVIDADE'
  FROM eap_no n JOIN contrato c ON c.id = n.contrato_id
 WHERE c.codigo = 'RT-CRN-1' AND n.edt = '1';
COMMIT;   -- ESPERADO: falha INV-CRN-009 (nó folha)

-- RT-33 | INV-CRN-009 — atividade apontando para contrato de outro nó
BEGIN;
UPDATE atividade a SET contrato_id = (SELECT id FROM contrato WHERE codigo <> 'RT-CRN-1' LIMIT 1)
  FROM eap_no n WHERE n.id = a.eap_no_id AND n.edt = '1.1'
   AND n.contrato_id = (SELECT id FROM contrato WHERE codigo = 'RT-CRN-1');
COMMIT;   -- ESPERADO: falha INV-CRN-009 (contratos diferentes)

-- RT-34 | INV-CRN-004 — marco da EAP com atividade do tipo ATIVIDADE
BEGIN;
UPDATE atividade a SET tipo = 'ATIVIDADE', duracao_dias = 2, termino_previsto = DATE '2026-01-06'
  FROM eap_no n WHERE n.id = a.eap_no_id AND n.edt = '1.4'
   AND n.contrato_id = (SELECT id FROM contrato WHERE codigo = 'RT-CRN-1');
COMMIT;   -- ESPERADO: falha INV-CRN-004 (tipo difere do nó)

-- RT-35 | restrição SNET sem data
BEGIN;
UPDATE atividade a SET restricao = 'SNET', restricao_data = NULL
  FROM eap_no n WHERE n.id = a.eap_no_id AND n.edt = '1.1'
   AND n.contrato_id = (SELECT id FROM contrato WHERE codigo = 'RT-CRN-1');
COMMIT;   -- ESPERADO: falha ck_atv_restricao_data

-- base para RT-36: uma memória de cálculo gravada
BEGIN;
INSERT INTO cronograma_calculo (contrato_id, calculado_por, motivo, versao_regra,
                                termino_indice, termino_data, memoria, hash)
SELECT id, 'rt', 'teste', 'RGR-CPM-001/1', 10, DATE '2026-01-19', '[]'::jsonb, '\x00'::bytea
  FROM contrato WHERE codigo = 'RT-CRN-1';
COMMIT;   -- deve passar

-- RT-36 | memória de cálculo é append-only
UPDATE cronograma_calculo SET termino_indice = 99
 WHERE contrato_id = (SELECT id FROM contrato WHERE codigo = 'RT-CRN-1');
-- ESPERADO: falha append-only

-- RT-37 | INV-BL-003 — datas de baseline aprovada não mudam
BEGIN;
INSERT INTO lvp_avaliacao (contrato_id, especie, emissao, nota, aprovado, avaliado_por)
SELECT id, 'LINHA_DE_BASE', 'RT-37', 0.95, true, 'fiscal' FROM contrato WHERE codigo = 'RT-CRN-1';
INSERT INTO baseline (contrato_id, numero, estado, lvp_id, aprovada_por, aprovada_em, vigente)
SELECT c.id, 0, 'APROVADA', l.id, 'gerente', now(), true
  FROM contrato c JOIN lvp_avaliacao l ON l.contrato_id = c.id
 WHERE c.codigo = 'RT-CRN-1' AND l.emissao = 'RT-37';
INSERT INTO baseline_atividade (baseline_id, atividade_id, inicio, termino, duracao_dias)
SELECT b.id, a.id, a.inicio_previsto, a.termino_previsto, a.duracao_dias
  FROM baseline b JOIN atividade a ON a.contrato_id = b.contrato_id
 WHERE b.contrato_id = (SELECT id FROM contrato WHERE codigo = 'RT-CRN-1') AND b.numero = 0;
COMMIT;   -- deve passar

BEGIN;
UPDATE baseline_atividade SET termino = termino + 30
 WHERE baseline_id IN (SELECT id FROM baseline
                        WHERE contrato_id = (SELECT id FROM contrato WHERE codigo = 'RT-CRN-1'));
COMMIT;   -- ESPERADO: falha INV-BL-003
