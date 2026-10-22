-- =====================================================================
-- 004_documentos_importacao.sql
-- (a) Lista de documentos de engenharia com ponderação MANUAL em pontos.
-- (b) Staging de importação de cronograma (MPP, XER, XML, XLSX).
-- =====================================================================
SET search_path = plan, public;

-- --------------------------------------------- documentos de engenharia
CREATE TYPE estagio_documento AS ENUM (
    'NAO_INICIADO',            -- 0%
    'INICIADO',                -- 10%
    'EM_CALCULO',              -- 30%
    'DESENHO_CONCLUIDO',       -- 40%
    'EM_VERIFICACAO',          -- 50%
    'EMITIDO_GED_REV0',        -- 60%
    'CERTIFICADO',             -- 70%
    'CERTIFICADO_COM_PENDENCIA', -- 80%
    'CERTIFICADO_SEM_PENDENCIA'  -- 100%
);

CREATE TABLE documento_engenharia (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id  uuid NOT NULL REFERENCES contrato(id) ON DELETE RESTRICT,
    eap_no_id    uuid NOT NULL REFERENCES eap_no(id) ON DELETE RESTRICT,
    codigo       text NOT NULL,
    titulo       text NOT NULL,
    disciplina   text NOT NULL,
    -- ponderação manual: horas-homem estimadas, convertidas em pontos pelo
    -- planejador. Não há rateio automático — peso de documento é decisão,
    -- não consequência de contagem de folhas.
    hh_estimadas numeric(10,2) NOT NULL,
    pontos       numeric(14,4) NOT NULL,
    memoria_peso text NOT NULL,
    id_cronograma text,                    -- ID da atividade no Project/P6
    inicio_previsto date,
    termino_previsto date,
    estagio      estagio_documento NOT NULL DEFAULT 'NAO_INICIADO',
    revisao      text,
    atualizado_em timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_doc_codigo UNIQUE (contrato_id, codigo),
    CONSTRAINT ck_doc_pontos CHECK (pontos > 0),
    CONSTRAINT ck_doc_hh     CHECK (hh_estimadas > 0),
    CONSTRAINT ck_doc_memoria CHECK (length(btrim(memoria_peso)) >= 10)
);
CREATE INDEX ix_doc_no ON documento_engenharia (eap_no_id);

-- histórico de estágio é fato: append-only (P3)
CREATE TABLE documento_evento (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    documento_id  uuid NOT NULL REFERENCES documento_engenharia(id) ON DELETE RESTRICT,
    estagio       estagio_documento NOT NULL,
    revisao       text,
    evidencia     text NOT NULL,            -- protocolo no GED, número da transmittal
    registrado_por text NOT NULL,
    registrado_em timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_doc_evento ON documento_evento (documento_id, registrado_em DESC);
CREATE TRIGGER tg_doc_evento_append_only
    BEFORE UPDATE OR DELETE ON documento_evento
    FOR EACH ROW EXECUTE FUNCTION fn_append_only();

-- INV-DOC-001: a soma dos pontos dos documentos de um nó folha de engenharia
-- deve fechar nos pontos do próprio nó. Diferida.
CREATE OR REPLACE FUNCTION fn_doc_soma_pontos() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT n.edt, n.pontos, SUM(d.pontos) AS soma
          FROM documento_engenharia d
          JOIN eap_no n ON n.id = d.eap_no_id
         GROUP BY n.edt, n.pontos
    LOOP
        IF r.soma <> r.pontos THEN
            RAISE EXCEPTION
                'INV-DOC-001: documentos de % somam % pontos, nó vale %',
                r.edt, r.soma, r.pontos;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER tg_doc_soma_pontos
    AFTER INSERT OR UPDATE OR DELETE ON documento_engenharia
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_doc_soma_pontos();

-- Avanço do nó de engenharia derivado dos documentos (RGR-DOC-001).
CREATE OR REPLACE FUNCTION pct_estagio(e estagio_documento) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE e
        WHEN 'NAO_INICIADO' THEN 0.00
        WHEN 'INICIADO' THEN 0.10
        WHEN 'EM_CALCULO' THEN 0.30
        WHEN 'DESENHO_CONCLUIDO' THEN 0.40
        WHEN 'EM_VERIFICACAO' THEN 0.50
        WHEN 'EMITIDO_GED_REV0' THEN 0.60
        WHEN 'CERTIFICADO' THEN 0.70
        WHEN 'CERTIFICADO_COM_PENDENCIA' THEN 0.80
        WHEN 'CERTIFICADO_SEM_PENDENCIA' THEN 1.00
    END::numeric
$$;

CREATE OR REPLACE VIEW v_avanco_documental AS
SELECT d.contrato_id,
       d.eap_no_id,
       SUM(d.pontos * pct_estagio(d.estagio)) / NULLIF(SUM(d.pontos), 0) AS percentual_ac,
       count(*) AS documentos,
       count(*) FILTER (WHERE d.estagio = 'CERTIFICADO_SEM_PENDENCIA') AS certificados
  FROM documento_engenharia d
 GROUP BY d.contrato_id, d.eap_no_id;

-- ------------------------------------------------- importação de cronograma
CREATE TYPE formato_importacao AS ENUM ('MPP', 'MSPROJECT_XML', 'P6_XER', 'P6_XML', 'XLSX_MASCARA');
CREATE TYPE estado_importacao  AS ENUM ('RECEBIDO', 'PARSEADO', 'VALIDADO', 'REJEITADO', 'APLICADO');

CREATE TABLE importacao (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id   uuid NOT NULL REFERENCES contrato(id) ON DELETE RESTRICT,
    formato       formato_importacao NOT NULL,
    arquivo_nome  text NOT NULL,
    arquivo_sha256 bytea NOT NULL,
    arquivo_uri   text NOT NULL,            -- objeto no MinIO; o arquivo original é evidência
    estado        estado_importacao NOT NULL DEFAULT 'RECEBIDO',
    plano_bruto   jsonb,                    -- plano normalizado do parser
    achados       jsonb NOT NULL DEFAULT '[]'::jsonb,   -- violações de INV-CRN
    aplicada_por  text,
    aplicada_em   timestamptz,
    criado_por    text NOT NULL,
    criado_em     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_importacao_arquivo UNIQUE (contrato_id, arquivo_sha256),
    -- P4/INV-IMP-001: aplicar é ato humano. Parser propõe, planejador decide.
    CONSTRAINT ck_importacao_aplicada CHECK (
        estado <> 'APLICADO' OR (aplicada_por IS NOT NULL AND aplicada_em IS NOT NULL)
    ),
    -- INV-IMP-002: importação com achado bloqueante não pode ser aplicada
    -- CHECK não aceita subquery; contenção jsonb (@>) expressa a mesma regra.
    CONSTRAINT ck_importacao_achados CHECK (
        estado <> 'APLICADO'
        OR NOT (achados @> '[{"severidade": "BLOQUEANTE"}]'::jsonb)
    )
);
CREATE INDEX ix_importacao_contrato ON importacao (contrato_id, criado_em DESC);

-- INV-IMP-003: importação NUNCA escreve avanço realizado.
-- O parser lê % concluída só para comparação; a coluna existe como achado,
-- não como dado aplicável.
CREATE OR REPLACE FUNCTION fn_importacao_sem_avanco() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.plano_bruto ? 'avanco_realizado' THEN
        RAISE EXCEPTION 'INV-IMP-003: importação não pode carregar avanço realizado';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER tg_importacao_sem_avanco
    BEFORE INSERT OR UPDATE ON importacao
    FOR EACH ROW EXECUTE FUNCTION fn_importacao_sem_avanco();

-- ------------------------------------------------------------ privilégios
-- O GRANT ... ON ALL TABLES do 003 só alcança tabelas que já existiam.
GRANT SELECT, INSERT, UPDATE, DELETE ON documento_engenharia, importacao TO gps_app;
GRANT SELECT, INSERT ON documento_evento TO gps_app;   -- P3: histórico é append-only
-- o ALTER DEFAULT PRIVILEGES do 003 já concedeu UPDATE/DELETE na criação; revoga.
REVOKE UPDATE, DELETE ON documento_evento FROM gps_app;

-- ------------------------------------------------------------ search_path
-- Funções PL/pgSQL resolvem nomes em tempo de execução pelo search_path da
-- sessão que as dispara. A aplicação não conecta com "plan" no caminho;
-- fixar aqui torna trigger e policy independentes de quem chama.
DO $$
DECLARE f record;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure AS assinatura
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'plan' AND p.prokind = 'f'
           AND p.oid NOT IN (SELECT objid FROM pg_depend WHERE deptype = 'e')  -- não é de extensão
    LOOP
        EXECUTE format('ALTER FUNCTION %s SET search_path = plan, public', f.assinatura);
    END LOOP;
END $$;
