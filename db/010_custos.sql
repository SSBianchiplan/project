-- =====================================================================
-- 010 — Custos do projeto vinculados ao cronograma e à EAP.
-- Orçamento por atividade (itens de custo) + custo dos recursos alocados;
-- custo realizado em ledger append-only. Curvas, VA, IDC e ENT no domínio.
-- =====================================================================
SET search_path = plan, public;

CREATE TYPE categoria_custo AS ENUM (
    'MATERIAL', 'EQUIPAMENTO', 'MAQUINA', 'SERVICO', 'SUBCONTRATO', 'MAO_DE_OBRA', 'LOGISTICA', 'OUTRO');

-- item de orçamento da atividade (custo planejado direto)
CREATE TABLE custo_item (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    atividade_id    uuid NOT NULL REFERENCES atividade(id) ON DELETE CASCADE,
    categoria       categoria_custo NOT NULL,
    descricao       text NOT NULL,
    fornecedor      text NOT NULL DEFAULT '',
    quantidade      numeric(14,3) NOT NULL DEFAULT 1,
    unidade         text NOT NULL DEFAULT 'un',
    custo_unitario  numeric(14,2) NOT NULL,
    total           numeric(16,2) GENERATED ALWAYS AS (quantidade * custo_unitario) STORED,
    observacao      text NOT NULL DEFAULT '',
    criado_em       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_custo_item_valores CHECK (quantidade > 0 AND custo_unitario >= 0)
);
CREATE INDEX ix_custo_item_atividade ON custo_item (atividade_id);
ALTER TABLE custo_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE custo_item FORCE ROW LEVEL SECURITY;
CREATE POLICY pol_org ON custo_item
    USING (EXISTS (SELECT 1 FROM atividade a JOIN contrato c ON c.id = a.contrato_id
                    WHERE a.id = atividade_id AND c.organizacao_id = org_corrente()))
    WITH CHECK (EXISTS (SELECT 1 FROM atividade a JOIN contrato c ON c.id = a.contrato_id
                    WHERE a.id = atividade_id AND c.organizacao_id = org_corrente()));

-- custo realizado: ledger. Correção é lançamento negativo, nunca edição (INV-CST-002).
CREATE TABLE custo_realizado (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id     uuid NOT NULL REFERENCES contrato(id) ON DELETE RESTRICT,
    atividade_id    uuid NOT NULL REFERENCES atividade(id) ON DELETE RESTRICT,
    categoria       categoria_custo NOT NULL,
    data            date NOT NULL,
    valor           numeric(16,2) NOT NULL,             -- negativo = estorno/ajuste
    descricao       text NOT NULL,
    documento       text NOT NULL DEFAULT '',           -- NF, medição de fornecedor, boletim
    criado_por      text NOT NULL,
    criado_em       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_custo_real_valor CHECK (valor <> 0)
);
CREATE INDEX ix_custo_real_contrato ON custo_realizado (contrato_id, data);
CREATE TRIGGER tg_custo_real_append_only
    BEFORE UPDATE OR DELETE ON custo_realizado
    FOR EACH ROW EXECUTE FUNCTION fn_append_only();
-- INV-CST-003: atividade do mesmo contrato
CREATE OR REPLACE FUNCTION fn_custo_real_coerencia() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE c uuid;
BEGIN
    SELECT contrato_id INTO c FROM atividade WHERE id = NEW.atividade_id;
    IF c IS DISTINCT FROM NEW.contrato_id THEN
        RAISE EXCEPTION 'INV-CST-003: atividade não pertence ao contrato do lançamento';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_custo_real_coerencia BEFORE INSERT ON custo_realizado
    FOR EACH ROW EXECUTE FUNCTION fn_custo_real_coerencia();
SELECT fn_aplicar_rls('custo_realizado', 'contrato_id');

GRANT SELECT, INSERT, UPDATE, DELETE ON custo_item TO gps_app;
GRANT SELECT, INSERT ON custo_realizado TO gps_app;
REVOKE UPDATE, DELETE ON custo_realizado FROM gps_app;

DO $$
DECLARE f record;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure AS assinatura
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'plan' AND p.prokind = 'f'
           AND p.oid NOT IN (SELECT objid FROM pg_depend WHERE deptype = 'e')
    LOOP
        EXECUTE format('ALTER FUNCTION %s SET search_path = plan, public', f.assinatura);
    END LOOP;
END $$;
