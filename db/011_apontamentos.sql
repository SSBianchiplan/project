-- =====================================================================
-- 011 — Apontamento de horas por atividade (ledger append-only).
-- Quem apontou, em que atividade, com que recurso (opcional), em que dia,
-- quantas horas. Correção é lançamento negativo (INV-APT-002), como medição
-- e custo: número de esforço precisa ser auditável.
-- =====================================================================
SET search_path = plan, public;

CREATE TABLE apontamento (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id   uuid NOT NULL REFERENCES contrato(id) ON DELETE RESTRICT,
    atividade_id  uuid NOT NULL REFERENCES atividade(id) ON DELETE RESTRICT,
    recurso_id    uuid REFERENCES recurso(id) ON DELETE RESTRICT,
    usuario_id    uuid REFERENCES usuario(id),
    data          date NOT NULL,
    horas         numeric(6,2) NOT NULL,            -- negativo = estorno
    descricao     text NOT NULL DEFAULT '',
    estorna_id    uuid REFERENCES apontamento(id),
    criado_por    text NOT NULL,
    criado_em     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_apt_horas CHECK (horas <> 0 AND horas BETWEEN -24 AND 24),
    CONSTRAINT ck_apt_estorno CHECK (horas > 0 OR estorna_id IS NOT NULL)
);
CREATE INDEX ix_apontamento_contrato_data ON apontamento (contrato_id, data);
CREATE INDEX ix_apontamento_atividade ON apontamento (atividade_id, data);

CREATE TRIGGER tg_apontamento_append_only
    BEFORE UPDATE OR DELETE ON apontamento
    FOR EACH ROW EXECUTE FUNCTION fn_append_only();

-- INV-APT-001: marco não recebe horas; INV-APT-003: mesmo contrato
CREATE OR REPLACE FUNCTION fn_apontamento_coerencia() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE a record; cr uuid;
BEGIN
    SELECT contrato_id, tipo INTO a FROM atividade WHERE id = NEW.atividade_id;
    IF a.contrato_id IS DISTINCT FROM NEW.contrato_id THEN
        RAISE EXCEPTION 'INV-APT-003: atividade não pertence ao contrato do apontamento';
    END IF;
    IF a.tipo = 'MARCO' THEN
        RAISE EXCEPTION 'INV-APT-001: marco não recebe horas — aponte na atividade que executa o trabalho';
    END IF;
    IF NEW.recurso_id IS NOT NULL THEN
        SELECT contrato_id INTO cr FROM recurso WHERE id = NEW.recurso_id;
        IF cr IS DISTINCT FROM NEW.contrato_id THEN
            RAISE EXCEPTION 'INV-APT-003: recurso de outro contrato';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_apontamento_coerencia BEFORE INSERT ON apontamento
    FOR EACH ROW EXECUTE FUNCTION fn_apontamento_coerencia();

SELECT fn_aplicar_rls('apontamento', 'contrato_id');
GRANT SELECT, INSERT ON apontamento TO gps_app;
REVOKE UPDATE, DELETE ON apontamento FROM gps_app;

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
