-- =====================================================================
-- 012 — RDO: fluxo de aprovação (planejamento → gerência do contrato).
-- O RDO (tabela rdo, 001) é append-only; o estado vem do último evento.
-- INV-RDO-001: só existe evento para RDO do mesmo contrato.
-- INV-RDO-002: eventos são append-only (parecer não se edita).
-- RGR-RDO-001: EMITIDO → ENVIADO → APROVADO_PLANEJAMENTO → APROVADO_GERENCIA;
--              REPROVADO_* volta o RDO para EMITIDO (pode ser reenviado com parecer).
-- =====================================================================
SET search_path = plan, public;

CREATE TABLE rdo_evento (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id uuid NOT NULL REFERENCES contrato(id) ON DELETE RESTRICT,
    rdo_id      uuid NOT NULL REFERENCES rdo(id) ON DELETE RESTRICT,
    evento      text NOT NULL CHECK (evento IN ('EMITIDO', 'ENVIADO', 'APROVADO_PLANEJAMENTO', 'REPROVADO_PLANEJAMENTO',
                                              'APROVADO_GERENCIA', 'REPROVADO_GERENCIA')),
    parecer     text NOT NULL DEFAULT '',
    por         text NOT NULL,
    papel       text NOT NULL,
    em          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_rdo_evento_rdo ON rdo_evento (rdo_id, em);

CREATE TRIGGER tg_rdo_evento_append_only
    BEFORE UPDATE OR DELETE ON rdo_evento
    FOR EACH ROW EXECUTE FUNCTION fn_append_only();

CREATE OR REPLACE FUNCTION fn_rdo_evento_coerencia() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE c uuid;
BEGIN
    SELECT contrato_id INTO c FROM rdo WHERE id = NEW.rdo_id;
    IF c IS DISTINCT FROM NEW.contrato_id THEN
        RAISE EXCEPTION 'INV-RDO-001: RDO não pertence ao contrato do evento';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_rdo_evento_coerencia BEFORE INSERT ON rdo_evento
    FOR EACH ROW EXECUTE FUNCTION fn_rdo_evento_coerencia();

SELECT fn_aplicar_rls('rdo_evento', 'contrato_id');
GRANT SELECT, INSERT ON rdo_evento TO gps_app;
REVOKE UPDATE, DELETE ON rdo_evento FROM gps_app;
GRANT SELECT, INSERT ON rdo TO gps_app;
REVOKE UPDATE, DELETE ON rdo FROM gps_app;
