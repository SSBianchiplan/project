-- =====================================================================
-- 013 — Boletim de medição (BM) e RDO editável antes do envio.
--
-- boletim_medicao: snapshot append-only da medição por período (quinzenal ou mensal),
-- consolidada até N níveis da EAP, com valor a medir por item (peso × valor total).
-- INV-BM-001: um número por contrato, sequencial; INV-BM-002: append-only.
--
-- RDO: o append-only rígido (001) passa a permitir UPDATE enquanto o RDO ainda não
-- foi enviado para aprovação (último evento EMITIDO/REPROVADO/EDITADO ou nenhum);
-- contrato, data e número não mudam; DELETE continua proibido. Toda edição gera
-- evento EDITADO (RGR-RDO-002).
-- =====================================================================
SET search_path = plan, public;

CREATE TABLE boletim_medicao (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id    uuid NOT NULL REFERENCES contrato(id) ON DELETE RESTRICT,
    numero         integer NOT NULL,
    tipo           text NOT NULL CHECK (tipo IN ('INICIAL', 'INTERMEDIARIO', 'FINAL')),
    periodicidade  text NOT NULL CHECK (periodicidade IN ('QUINZENAL', 'MENSAL')),
    niveis         smallint NOT NULL CHECK (niveis BETWEEN 1 AND 5),
    periodo_inicio date NOT NULL,
    periodo_fim    date NOT NULL,
    valor_total    numeric(16,2) NOT NULL,
    condicoes      text NOT NULL DEFAULT '',
    snapshot       jsonb NOT NULL,
    emitido_por    text NOT NULL,
    emitido_em     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_bm_numero UNIQUE (contrato_id, numero),
    CONSTRAINT ck_bm_periodo CHECK (periodo_fim >= periodo_inicio)
);
CREATE INDEX ix_bm_contrato ON boletim_medicao (contrato_id, numero);
CREATE TRIGGER tg_bm_append_only BEFORE UPDATE OR DELETE ON boletim_medicao
    FOR EACH ROW EXECUTE FUNCTION fn_append_only();
SELECT fn_aplicar_rls('boletim_medicao', 'contrato_id');
GRANT SELECT, INSERT ON boletim_medicao TO gps_app;
REVOKE UPDATE, DELETE ON boletim_medicao FROM gps_app;

-- RDO editável antes do envio
ALTER TABLE rdo_evento DROP CONSTRAINT IF EXISTS rdo_evento_evento_check;
ALTER TABLE rdo_evento ADD CONSTRAINT rdo_evento_evento_check CHECK (evento IN
    ('EMITIDO', 'EDITADO', 'ENVIADO', 'APROVADO_PLANEJAMENTO', 'REPROVADO_PLANEJAMENTO', 'APROVADO_GERENCIA', 'REPROVADO_GERENCIA'));

CREATE OR REPLACE FUNCTION fn_rdo_editavel() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE ultimo text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'INV-RDO-002: RDO não se apaga';
    END IF;
    IF NEW.contrato_id <> OLD.contrato_id OR NEW.data <> OLD.data OR NEW.numero <> OLD.numero OR NEW.criado_por <> OLD.criado_por THEN
        RAISE EXCEPTION 'INV-RDO-002: contrato, data, número e emissor do RDO não mudam';
    END IF;
    SELECT evento INTO ultimo FROM rdo_evento WHERE rdo_id = OLD.id ORDER BY em DESC, id DESC LIMIT 1;
    IF ultimo IS NOT NULL AND ultimo NOT IN ('EMITIDO', 'EDITADO', 'REPROVADO_PLANEJAMENTO', 'REPROVADO_GERENCIA') THEN
        RAISE EXCEPTION 'RGR-RDO-002: RDO em % não se edita; reprove para devolver ao emissor', ultimo;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tg_rdo_append_only ON rdo;
CREATE TRIGGER tg_rdo_editavel BEFORE UPDATE OR DELETE ON rdo
    FOR EACH ROW EXECUTE FUNCTION fn_rdo_editavel();
GRANT UPDATE ON rdo TO gps_app;
