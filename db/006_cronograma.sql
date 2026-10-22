-- =====================================================================
-- 006 — Cronograma avançado: atributos de atividade, calendário do
-- contrato, rede sem ciclo (INV-CRN-006), snapshot de baseline e
-- memória de cálculo do CPM (append-only).
-- =====================================================================
SET search_path = plan, public;

CREATE TYPE restricao_atividade AS ENUM ('ASAP', 'SNET', 'MSO');
CREATE TYPE situacao_atividade  AS ENUM ('NAO_INICIADA', 'EM_ANDAMENTO', 'CONCLUIDA', 'SUSPENSA');

-- ------------------------------------------------------------- EAP: memória do peso
-- INV-DOC-002 vale para a folha ponderada também: peso sem memória é achado.
ALTER TABLE eap_no ADD COLUMN memoria_peso text NOT NULL DEFAULT '';

-- exceção contratual passa a guardar o documento que a autoriza
ALTER TABLE excecao_contratual ADD COLUMN documento text NOT NULL DEFAULT '';

-- --------------------------------------------------------------- atividade
ALTER TABLE atividade
    ADD COLUMN contrato_id     uuid REFERENCES contrato(id) ON DELETE RESTRICT,
    ADD COLUMN restricao       restricao_atividade NOT NULL DEFAULT 'ASAP',
    ADD COLUMN restricao_data  date,
    ADD COLUMN restricao_justificativa text NOT NULL DEFAULT '',
    ADD COLUMN deadline        date,
    ADD COLUMN calendario      text NOT NULL DEFAULT '5x8',
    ADD COLUMN disciplina      text NOT NULL DEFAULT '',
    ADD COLUMN responsavel     text NOT NULL DEFAULT '',
    ADD COLUMN prioridade      smallint NOT NULL DEFAULT 500,
    ADD COLUMN notas           text NOT NULL DEFAULT '',
    ADD COLUMN situacao        situacao_atividade NOT NULL DEFAULT 'NAO_INICIADA',
    ADD COLUMN mod_qtd         integer NOT NULL DEFAULT 0,
    ADD COLUMN moi_qtd         integer NOT NULL DEFAULT 0,
    ADD COLUMN equipamentos_qtd integer NOT NULL DEFAULT 0,
    ADD COLUMN hh_estimadas    numeric(12,2) NOT NULL DEFAULT 0,
    ADD COLUMN turno           smallint NOT NULL DEFAULT 1,
    ADD COLUMN subcontratada   text NOT NULL DEFAULT '',
    ADD COLUMN criterio_medicao text,
    ADD COLUMN unidade         text NOT NULL DEFAULT 'hh',
    ADD COLUMN qtd_prevista    numeric(14,3) NOT NULL DEFAULT 0,
    ADD COLUMN qtd_realizada   numeric(14,3) NOT NULL DEFAULT 0,
    ADD COLUMN atualizado_em   timestamptz NOT NULL DEFAULT now();

UPDATE atividade a SET contrato_id = n.contrato_id FROM eap_no n WHERE n.id = a.eap_no_id;
ALTER TABLE atividade ALTER COLUMN contrato_id SET NOT NULL;

-- INV-CRN-009: uma atividade por folha
ALTER TABLE atividade ADD CONSTRAINT uq_atividade_no UNIQUE (eap_no_id);
-- restrição com data só faz sentido com data
ALTER TABLE atividade ADD CONSTRAINT ck_atv_restricao_data
    CHECK (restricao = 'ASAP' OR restricao_data IS NOT NULL);
ALTER TABLE atividade ADD CONSTRAINT ck_atv_prioridade CHECK (prioridade BETWEEN 0 AND 1000);
ALTER TABLE atividade ADD CONSTRAINT ck_atv_turno      CHECK (turno BETWEEN 1 AND 3);
ALTER TABLE atividade ADD CONSTRAINT ck_atv_recursos   CHECK (
    mod_qtd >= 0 AND moi_qtd >= 0 AND equipamentos_qtd >= 0 AND hh_estimadas >= 0
    AND qtd_prevista >= 0 AND qtd_realizada >= 0);

-- atividade pertence à mesma árvore que o nó, e o nó é folha; marco ↔ marco
CREATE OR REPLACE FUNCTION fn_atividade_coerencia() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE n record;
BEGIN
    SELECT contrato_id, folha, tipo INTO n FROM eap_no WHERE id = NEW.eap_no_id;
    IF n.contrato_id IS DISTINCT FROM NEW.contrato_id THEN
        RAISE EXCEPTION 'INV-CRN-009: atividade e nó da EAP pertencem a contratos diferentes';
    END IF;
    IF NOT n.folha THEN
        RAISE EXCEPTION 'INV-CRN-009: atividade só existe em nó folha';
    END IF;
    IF (n.tipo = 'MARCO') <> (NEW.tipo = 'MARCO') THEN
        RAISE EXCEPTION 'INV-CRN-004: tipo da atividade (%) difere do nó (%)', NEW.tipo, n.tipo;
    END IF;
    NEW.atualizado_em := now();
    RETURN NEW;
END $$;
CREATE TRIGGER tg_atividade_coerencia
    BEFORE INSERT OR UPDATE ON atividade
    FOR EACH ROW EXECUTE FUNCTION fn_atividade_coerencia();

-- ------------------------------------------------------------ INV-CRN-006: ciclo
-- Se a partir da sucessora se alcança a predecessora, o vínculo fecha um ciclo.
-- UNION (sem ALL) garante término mesmo com ciclo pré-existente.
CREATE OR REPLACE FUNCTION fn_vinculo_sem_ciclo() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE fecha boolean;
BEGIN
    WITH RECURSIVE alcance(id) AS (
        SELECT v.sucessora_id FROM vinculo v WHERE v.predecessora_id = NEW.sucessora_id
        UNION
        SELECT v.sucessora_id FROM vinculo v JOIN alcance a ON v.predecessora_id = a.id
    )
    SELECT EXISTS (SELECT 1 FROM alcance WHERE id = NEW.predecessora_id) INTO fecha;
    IF fecha THEN
        RAISE EXCEPTION 'INV-CRN-006: vínculo % -> % fecha um ciclo de precedência',
            NEW.predecessora_id, NEW.sucessora_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tg_vinculo_sem_ciclo
    BEFORE INSERT OR UPDATE ON vinculo
    FOR EACH ROW EXECUTE FUNCTION fn_vinculo_sem_ciclo();

-- ---------------------------------------------------------------- calendário
CREATE TABLE feriado (
    contrato_id uuid NOT NULL REFERENCES contrato(id) ON DELETE CASCADE,
    data        date NOT NULL,
    descricao   text NOT NULL,
    PRIMARY KEY (contrato_id, data)
);
SELECT fn_aplicar_rls('feriado', 'contrato_id');

-- ------------------------------------------------ snapshot de datas por baseline
-- INV-BL-003 vale aqui também: datas de baseline aprovada não mudam.
CREATE TABLE baseline_atividade (
    baseline_id   uuid NOT NULL REFERENCES baseline(id) ON DELETE CASCADE,
    atividade_id  uuid NOT NULL REFERENCES atividade(id) ON DELETE RESTRICT,
    inicio        date NOT NULL,
    termino       date NOT NULL,
    duracao_dias  integer NOT NULL,
    PRIMARY KEY (baseline_id, atividade_id),
    CONSTRAINT ck_blatv_janela CHECK (termino >= inicio)
);
CREATE OR REPLACE FUNCTION fn_baseline_atividade_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE est estado_baseline;
BEGIN
    SELECT estado INTO est FROM baseline WHERE id = COALESCE(OLD.baseline_id, NEW.baseline_id);
    IF est = 'APROVADA' THEN
        RAISE EXCEPTION 'INV-BL-003: datas de baseline aprovada são imutáveis — crie nova baseline';
    END IF;
    RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER tg_baseline_atividade_imutavel
    BEFORE UPDATE OR DELETE ON baseline_atividade
    FOR EACH ROW EXECUTE FUNCTION fn_baseline_atividade_imutavel();
ALTER TABLE baseline_atividade ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_atividade FORCE ROW LEVEL SECURITY;
CREATE POLICY pol_org ON baseline_atividade
    USING (EXISTS (SELECT 1 FROM baseline b JOIN contrato c ON c.id = b.contrato_id
                    WHERE b.id = baseline_id AND c.organizacao_id = org_corrente()))
    WITH CHECK (EXISTS (SELECT 1 FROM baseline b JOIN contrato c ON c.id = b.contrato_id
                    WHERE b.id = baseline_id AND c.organizacao_id = org_corrente()));

-- ------------------------------------------------- memória de cálculo do CPM
-- Append-only: número de prazo precisa ser reproduzível anos depois.
CREATE TABLE cronograma_calculo (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id    uuid NOT NULL REFERENCES contrato(id) ON DELETE RESTRICT,
    calculado_em   timestamptz NOT NULL DEFAULT now(),
    calculado_por  text NOT NULL,
    motivo         text NOT NULL,                 -- gravação de atividade, vínculo, importação
    data_status    date,
    versao_regra   text NOT NULL,
    termino_indice integer NOT NULL,
    termino_data   date NOT NULL,
    criticas       jsonb NOT NULL DEFAULT '[]'::jsonb,
    achados        jsonb NOT NULL DEFAULT '[]'::jsonb,
    memoria        jsonb NOT NULL,                -- datas por atividade
    hash           bytea NOT NULL
);
CREATE INDEX ix_cronograma_calculo_contrato ON cronograma_calculo (contrato_id, calculado_em DESC);
CREATE TRIGGER tg_cronograma_calculo_append_only
    BEFORE UPDATE OR DELETE ON cronograma_calculo
    FOR EACH ROW EXECUTE FUNCTION fn_append_only();
SELECT fn_aplicar_rls('cronograma_calculo', 'contrato_id');

-- ------------------------------------------------------------ privilégios
GRANT SELECT, INSERT, UPDATE, DELETE ON feriado, baseline_atividade TO gps_app;
GRANT SELECT, INSERT ON cronograma_calculo TO gps_app;
REVOKE UPDATE, DELETE ON cronograma_calculo FROM gps_app;

-- ------------------------------------------------------------ search_path
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
