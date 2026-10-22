-- =====================================================================
-- RED-TEAM — cada bloco TENTA violar uma invariante direto pelo banco.
-- Critério de PASS: todo bloco falha. Se algum INSERT passa, a invariante
-- não existe — existe só a intenção dela.
-- Rodar com: psql -v ON_ERROR_STOP=0 -f db/002_red_team.sql
-- =====================================================================
SET search_path = plan, public;

-- Roda como superusuário de propósito: aqui o alvo são as invariantes do
-- núcleo, não o isolamento (esse é o 005, como gps_app). Desde o 003 todo
-- contrato pertence a uma organização, então a fixture precisa de uma.
BEGIN;
INSERT INTO organizacao (codigo, razao_social) VALUES ('RT-ORG', 'Red-team') ON CONFLICT DO NOTHING;
INSERT INTO contrato (organizacao_id, codigo, contratante, contratada, empreendimento, modalidade, data_inicio, termino_contratual)
SELECT id, 'RT-001', 'X', 'Y', 'Teste', 'EPC', DATE '2026-01-04', DATE '2026-12-31'
  FROM organizacao WHERE codigo = 'RT-ORG';

WITH c AS (SELECT id FROM contrato WHERE codigo = 'RT-001')
INSERT INTO eap_no (contrato_id, parent_id, edt, descricao, nivel, tipo, folha, pontos)
SELECT c.id, NULL, '1', 'ÁREA', 1, 'RESUMO', false, 0 FROM c;

WITH c AS (SELECT id FROM contrato WHERE codigo = 'RT-001'),
     p AS (SELECT id FROM eap_no WHERE edt = '1')
INSERT INTO eap_no (contrato_id, parent_id, edt, descricao, nivel, tipo, folha, pontos)
SELECT c.id, p.id, '1.1', 'ENGENHARIA', 2, 'ATIVIDADE', true, 1000000 FROM c, p;
COMMIT;   -- deve passar: ponderação fecha

-- RT-1 | INV-EAP-001 — soma não fecha na base
BEGIN;
WITH c AS (SELECT id FROM contrato WHERE codigo = 'RT-001'),
     p AS (SELECT id FROM eap_no WHERE edt = '1')
INSERT INTO eap_no (contrato_id, parent_id, edt, descricao, nivel, tipo, folha, pontos)
SELECT c.id, p.id, '1.2', 'EXTRA', 2, 'ATIVIDADE', true, 1 FROM c, p;
COMMIT;   -- ESPERADO: falha INV-EAP-001

-- RT-2 | INV-EAP-002 — nó resumo com pontos
BEGIN;
WITH c AS (SELECT id FROM contrato WHERE codigo = 'RT-001'),
     p AS (SELECT id FROM eap_no WHERE edt = '1')
INSERT INTO eap_no (contrato_id, parent_id, edt, descricao, nivel, tipo, folha, pontos)
SELECT c.id, p.id, '1.3', 'RESUMO COM PESO', 2, 'RESUMO', false, 10 FROM c, p;
COMMIT;   -- ESPERADO: falha ck_eap_pontos_folha

-- RT-3 | INV-EAP-004 — EDT que não descende do pai
BEGIN;
WITH c AS (SELECT id FROM contrato WHERE codigo = 'RT-001'),
     p AS (SELECT id FROM eap_no WHERE edt = '1')
INSERT INTO eap_no (contrato_id, parent_id, edt, descricao, nivel, tipo, folha, pontos)
SELECT c.id, p.id, '2.1', 'ÓRFÃO', 2, 'ATIVIDADE', true, 0 FROM c, p;
COMMIT;   -- ESPERADO: falha INV-EAP-004

-- RT-4 | INV-MED-004 — data de corte que não é sábado
BEGIN;
WITH n AS (SELECT id, contrato_id FROM eap_no WHERE edt = '1.1')
INSERT INTO medicao (contrato_id, eap_no_id, data_corte, percentual_ac, memoria_calculo, evidencia, criado_por)
SELECT n.contrato_id, n.id, DATE '2026-02-11', 0.10, '{}'::jsonb, 'x', 'rt' FROM n;
COMMIT;   -- ESPERADO: falha ck_med_sabado

-- RT-5 | INV-MED-006 — medição em nó resumo
BEGIN;
WITH n AS (SELECT id, contrato_id FROM eap_no WHERE edt = '1')
INSERT INTO medicao (contrato_id, eap_no_id, data_corte, percentual_ac, memoria_calculo, evidencia, criado_por)
SELECT n.contrato_id, n.id, DATE '2026-02-07', 0.10, '{}'::jsonb, 'x', 'rt' FROM n;
COMMIT;   -- ESPERADO: falha INV-MED-006

-- base para os próximos: uma medição válida
BEGIN;
WITH n AS (SELECT id, contrato_id FROM eap_no WHERE edt = '1.1')
INSERT INTO medicao (contrato_id, eap_no_id, data_corte, percentual_ac, memoria_calculo, evidencia, criado_por)
SELECT n.contrato_id, n.id, DATE '2026-02-07', 0.30, '{}'::jsonb, 'foto+RDO', 'rt' FROM n;
COMMIT;   -- deve passar

-- RT-6 | INV-MED-003 — avanço retrocedendo sem estorno
BEGIN;
WITH n AS (SELECT id, contrato_id FROM eap_no WHERE edt = '1.1')
INSERT INTO medicao (contrato_id, eap_no_id, data_corte, percentual_ac, memoria_calculo, evidencia, criado_por)
SELECT n.contrato_id, n.id, DATE '2026-02-14', 0.20, '{}'::jsonb, 'x', 'rt' FROM n;
COMMIT;   -- ESPERADO: falha INV-MED-003

-- RT-7 | INV-MED-005 — segunda medição na mesma data de corte
BEGIN;
WITH n AS (SELECT id, contrato_id FROM eap_no WHERE edt = '1.1')
INSERT INTO medicao (contrato_id, eap_no_id, data_corte, percentual_ac, memoria_calculo, evidencia, criado_por)
SELECT n.contrato_id, n.id, DATE '2026-02-07', 0.35, '{}'::jsonb, 'x', 'rt' FROM n;
COMMIT;   -- ESPERADO: falha uq_medicao_vigente

-- RT-8 | INV-MED-001 — UPDATE em fato
UPDATE medicao SET percentual_ac = 0.99 WHERE data_corte = DATE '2026-02-07';
-- ESPERADO: falha append-only

-- RT-9 | INV-MED-001 — DELETE em fato
DELETE FROM medicao WHERE data_corte = DATE '2026-02-07';
-- ESPERADO: falha append-only

-- RT-10 | INV-BL-001 — baseline APROVADA sem quem aprovou
BEGIN;
INSERT INTO baseline (contrato_id, numero, estado)
SELECT id, 0, 'APROVADA' FROM contrato WHERE codigo = 'RT-001';
COMMIT;   -- ESPERADO: falha ck_baseline_aprovacao

-- RT-11 | INV-BL-002 — duas baselines vigentes
BEGIN;
INSERT INTO lvp_avaliacao (contrato_id, especie, emissao, nota, aprovado, avaliado_por)
SELECT id, 'LINHA_DE_BASE', '1ª', 0.90, true, 'fiscal' FROM contrato WHERE codigo = 'RT-001';
INSERT INTO baseline (contrato_id, numero, estado, lvp_id, aprovada_por, aprovada_em, vigente)
SELECT c.id, 0, 'APROVADA', l.id, 'gerente', now(), true
  FROM contrato c JOIN lvp_avaliacao l ON l.contrato_id = c.id WHERE c.codigo = 'RT-001';
INSERT INTO baseline (contrato_id, numero, estado, lvp_id, aprovada_por, aprovada_em, vigente)
SELECT c.id, 1, 'APROVADA', l.id, 'gerente', now(), true
  FROM contrato c JOIN lvp_avaliacao l ON l.contrato_id = c.id WHERE c.codigo = 'RT-001';
COMMIT;   -- ESPERADO: falha uq_baseline_vigente

-- RT-12 | RGR-LVP-001 — aprovação com nota abaixo de 85%
BEGIN;
INSERT INTO lvp_avaliacao (contrato_id, especie, emissao, nota, aprovado, avaliado_por)
SELECT id, 'LINHA_DE_BASE', '2ª', 0.80, true, 'fiscal' FROM contrato WHERE codigo = 'RT-001';
COMMIT;   -- ESPERADO: falha ck_lvp_nota_minima

-- RT-13 | RGR-LVP-001 — aprovação com item obrigatório fora de ATENDE
BEGIN;
WITH a AS (
  INSERT INTO lvp_avaliacao (contrato_id, especie, emissao, nota, aprovado, avaliado_por)
  SELECT id, 'LINHA_DE_BASE', '3ª', 0.95, true, 'fiscal' FROM contrato WHERE codigo = 'RT-001'
  RETURNING id)
INSERT INTO lvp_item (lvp_id, item, peso, obrigatorio, status)
SELECT a.id, 2, 0.2120, true, 'NAO_ATENDE' FROM a;
COMMIT;   -- ESPERADO: falha RGR-LVP-001

-- RT-14 | INV-CRN-002 — lag negativo
BEGIN;
INSERT INTO atividade (eap_no_id, duracao_dias, inicio_previsto, termino_previsto, tipo)
SELECT id, 10, DATE '2026-01-05', DATE '2026-01-16', 'ATIVIDADE' FROM eap_no WHERE edt = '1.1';
INSERT INTO vinculo (predecessora_id, sucessora_id, tipo, lag_dias)
SELECT a.id, a.id, 'TI', -10 FROM atividade a LIMIT 1;
COMMIT;   -- ESPERADO: falha ck_vinculo_lag (e ck_vinculo_self)

-- RT-15 | INV-CRN-001 — duração acima de 15 dias sem exceção registrada
BEGIN;
INSERT INTO atividade (eap_no_id, duracao_dias, inicio_previsto, termino_previsto, tipo)
SELECT id, 40, DATE '2026-01-05', DATE '2026-03-01', 'ATIVIDADE' FROM eap_no WHERE edt = '1.1';
COMMIT;   -- ESPERADO: falha ck_atv_duracao_max

-- RT-16 | INV-DSV-001 — fechar período com plano de ação em RASCUNHO
BEGIN;
INSERT INTO plano_acao (contrato_id, eap_no_id, data_corte, desvio_abs, estado, causa_raiz, acao, responsavel, prazo)
SELECT n.contrato_id, n.id, DATE '2026-02-07', -0.12, 'RASCUNHO', 'atraso de fabricação', 'turno extra', 'gerente', DATE '2026-03-07'
  FROM eap_no n WHERE n.edt = '1.1';
INSERT INTO periodo_fechamento (contrato_id, data_corte, fechado_por)
SELECT id, DATE '2026-02-07', 'planejador' FROM contrato WHERE codigo = 'RT-001';
COMMIT;   -- ESPERADO: falha INV-DSV-001

-- RT-17 | INV-RDO-001 — dois RDO no mesmo dia
BEGIN;
INSERT INTO rdo (contrato_id, data, numero, emitido_em, valido, clima, efetivo, servicos, criado_por)
SELECT id, DATE '2026-02-09', 1, now(), true, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 'rt' FROM contrato WHERE codigo = 'RT-001';
INSERT INTO rdo (contrato_id, data, numero, emitido_em, valido, clima, efetivo, servicos, criado_por)
SELECT id, DATE '2026-02-09', 2, now(), true, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 'rt' FROM contrato WHERE codigo = 'RT-001';
COMMIT;   -- ESPERADO: falha uq_rdo_dia

-- base para RT-18: baseline aprovada com previsto que fecha em 1
BEGIN;
INSERT INTO lvp_avaliacao (contrato_id, especie, emissao, nota, aprovado, avaliado_por)
SELECT id, 'LINHA_DE_BASE', 'RT-18', 0.90, true, 'fiscal' FROM contrato WHERE codigo = 'RT-001';
INSERT INTO baseline (contrato_id, numero, estado, lvp_id, aprovada_por, aprovada_em, vigente)
SELECT c.id, 0, 'APROVADA', l.id, 'gerente', now(), true
  FROM contrato c JOIN lvp_avaliacao l ON l.contrato_id = c.id
 WHERE c.codigo = 'RT-001' AND l.emissao = 'RT-18';
INSERT INTO previsto_semanal (baseline_id, eap_no_id, fim_semana, parcela)
SELECT b.id, n.id, s.fim_semana, 0.5
  FROM baseline b
  JOIN eap_no n ON n.contrato_id = b.contrato_id AND n.edt = '1.1'
  CROSS JOIN (VALUES (DATE '2026-01-10'), (DATE '2026-01-17')) AS s(fim_semana)
 WHERE b.numero = 0 AND b.estado = 'APROVADA';
COMMIT;   -- deve passar: 0.5 + 0.5 = 1

-- RT-18 | INV-BL-003 — alterar previsto de baseline aprovada
BEGIN;
UPDATE previsto_semanal SET parcela = 0.99
 WHERE baseline_id IN (SELECT id FROM baseline WHERE estado = 'APROVADA');
COMMIT;   -- ESPERADO: falha INV-BL-003
