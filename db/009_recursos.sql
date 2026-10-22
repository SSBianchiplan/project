-- =====================================================================
-- 009 — Alocação de recursos (premissa de planejamento, item 9.5).
-- Catálogo por contrato, alocação por atividade e capacidade diária.
-- O histograma e a superalocação são calculados no domínio a partir do CPM.
-- =====================================================================
SET search_path = plan, public;

CREATE TYPE tipo_recurso AS ENUM ('MOD', 'MOI', 'EQUIPAMENTO', 'MATERIAL');

CREATE TABLE recurso (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id    uuid NOT NULL REFERENCES contrato(id) ON DELETE RESTRICT,
    codigo         text NOT NULL,
    nome           text NOT NULL,
    tipo           tipo_recurso NOT NULL,
    unidade        text NOT NULL DEFAULT 'pessoa',    -- pessoa, un, t, m³ ...
    capacidade_dia numeric(12,3) NOT NULL DEFAULT 0,  -- disponível por dia útil (0 = ilimitado/não controlado)
    custo_hora     numeric(14,2) NOT NULL DEFAULT 0,
    horas_dia      numeric(5,2) NOT NULL DEFAULT 8,
    ativo          boolean NOT NULL DEFAULT true,
    criado_em      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_recurso_codigo UNIQUE (contrato_id, codigo),
    CONSTRAINT ck_recurso_capacidade CHECK (capacidade_dia >= 0 AND custo_hora >= 0 AND horas_dia > 0)
);
SELECT fn_aplicar_rls('recurso', 'contrato_id');

-- quantidade do recurso mobilizada por dia útil ao longo da atividade (perfil uniforme)
CREATE TABLE alocacao (
    atividade_id   uuid NOT NULL REFERENCES atividade(id) ON DELETE CASCADE,
    recurso_id     uuid NOT NULL REFERENCES recurso(id) ON DELETE RESTRICT,
    quantidade_dia numeric(12,3) NOT NULL,
    observacao     text NOT NULL DEFAULT '',
    PRIMARY KEY (atividade_id, recurso_id),
    CONSTRAINT ck_alocacao_qtd CHECK (quantidade_dia > 0)
);
-- INV-REC-002: recurso e atividade do mesmo contrato
CREATE OR REPLACE FUNCTION fn_alocacao_coerencia() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE ca uuid; cr uuid;
BEGIN
    SELECT contrato_id INTO ca FROM atividade WHERE id = NEW.atividade_id;
    SELECT contrato_id INTO cr FROM recurso  WHERE id = NEW.recurso_id;
    IF ca IS DISTINCT FROM cr THEN
        RAISE EXCEPTION 'INV-REC-002: recurso e atividade pertencem a contratos diferentes';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_alocacao_coerencia BEFORE INSERT OR UPDATE ON alocacao
    FOR EACH ROW EXECUTE FUNCTION fn_alocacao_coerencia();
ALTER TABLE alocacao ENABLE ROW LEVEL SECURITY;
ALTER TABLE alocacao FORCE ROW LEVEL SECURITY;
CREATE POLICY pol_org ON alocacao
    USING (EXISTS (SELECT 1 FROM atividade a JOIN contrato c ON c.id = a.contrato_id
                    WHERE a.id = atividade_id AND c.organizacao_id = org_corrente()))
    WITH CHECK (EXISTS (SELECT 1 FROM atividade a JOIN contrato c ON c.id = a.contrato_id
                    WHERE a.id = atividade_id AND c.organizacao_id = org_corrente()));

GRANT SELECT, INSERT, UPDATE, DELETE ON recurso, alocacao TO gps_app;

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
