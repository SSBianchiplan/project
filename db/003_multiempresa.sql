-- =====================================================================
-- 003_multiempresa.sql — isolamento por organização com RLS.
-- Decisão: UM cluster, UM schema, organizacao_id em tudo + RLS forçada.
-- Banco por cliente multiplicaria migration, backup e upgrade por N, e a
-- primeira consolidação de portfólio entre contratos ficaria impossível.
-- O preço é que TODA política precisa estar certa: uma tabela sem RLS
-- vaza dado entre clientes, e o vazamento é silencioso.
-- =====================================================================
SET search_path = plan, public;

CREATE TABLE organizacao (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo    text NOT NULL UNIQUE,
    razao_social text NOT NULL,
    ativa     boolean NOT NULL DEFAULT true,
    criada_em timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE usuario_organizacao (
    subject   text NOT NULL,              -- 'sub' do OIDC (Keycloak)
    organizacao_id uuid NOT NULL REFERENCES organizacao(id) ON DELETE RESTRICT,
    papel     text NOT NULL,              -- PLANEJADOR, GESTOR, FISCAL, LEITOR
    PRIMARY KEY (subject, organizacao_id),
    CONSTRAINT ck_papel CHECK (papel IN ('PLANEJADOR','GESTOR','FISCAL','LEITOR'))
);

-- contrato ganha o dono; as demais tabelas herdam por contrato_id.
ALTER TABLE contrato ADD COLUMN organizacao_id uuid REFERENCES organizacao(id);
UPDATE contrato SET organizacao_id = (SELECT id FROM organizacao LIMIT 1) WHERE organizacao_id IS NULL;
ALTER TABLE contrato ALTER COLUMN organizacao_id SET NOT NULL;
CREATE INDEX ix_contrato_org ON contrato (organizacao_id);

-- código do contrato é único DENTRO da organização, não globalmente
ALTER TABLE contrato DROP CONSTRAINT contrato_codigo_key;
ALTER TABLE contrato ADD CONSTRAINT uq_contrato_codigo UNIQUE (organizacao_id, codigo);

-- ------------------------------------------------------------- papel app
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gps_app') THEN
        CREATE ROLE gps_app NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA plan TO gps_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA plan TO gps_app;
-- tabelas criadas depois (004+) pelo mesmo papel de migração herdam o acesso;
-- cada DDL posterior ainda REVOGA explicitamente o que for append-only.
ALTER DEFAULT PRIVILEGES IN SCHEMA plan GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gps_app;
-- P3: fato não se altera nem pela aplicação
REVOKE UPDATE, DELETE ON medicao, rdo FROM gps_app;

-- ------------------------------------------------------------------ RLS
-- A sessão declara a organização corrente: SET LOCAL app.organizacao_id = '<uuid>'
CREATE OR REPLACE FUNCTION org_corrente() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.organizacao_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION fn_aplicar_rls(p_tabela text, p_coluna_contrato text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format('ALTER TABLE plan.%I ENABLE ROW LEVEL SECURITY', p_tabela);
    EXECUTE format('ALTER TABLE plan.%I FORCE ROW LEVEL SECURITY', p_tabela);
    EXECUTE format($f$
        CREATE POLICY pol_org ON plan.%I
        USING (EXISTS (SELECT 1 FROM plan.contrato c
                        WHERE c.id = %I AND c.organizacao_id = plan.org_corrente()))
        WITH CHECK (EXISTS (SELECT 1 FROM plan.contrato c
                        WHERE c.id = %I AND c.organizacao_id = plan.org_corrente()))
    $f$, p_tabela, p_coluna_contrato, p_coluna_contrato);
END $$;

ALTER TABLE contrato ENABLE ROW LEVEL SECURITY;
ALTER TABLE contrato FORCE ROW LEVEL SECURITY;
CREATE POLICY pol_org ON contrato
    USING (organizacao_id = org_corrente())
    WITH CHECK (organizacao_id = org_corrente());

SELECT fn_aplicar_rls('eap_no', 'contrato_id');
SELECT fn_aplicar_rls('baseline', 'contrato_id');
SELECT fn_aplicar_rls('lvp_avaliacao', 'contrato_id');
SELECT fn_aplicar_rls('medicao', 'contrato_id');
SELECT fn_aplicar_rls('calculo_execucao', 'contrato_id');
SELECT fn_aplicar_rls('plano_acao', 'contrato_id');
SELECT fn_aplicar_rls('periodo_fechamento', 'contrato_id');
SELECT fn_aplicar_rls('rdo', 'contrato_id');
SELECT fn_aplicar_rls('excecao_contratual', 'contrato_id');

-- tabelas que só alcançam contrato por join: política via EXISTS explícito
ALTER TABLE previsto_semanal ENABLE ROW LEVEL SECURITY;
ALTER TABLE previsto_semanal FORCE ROW LEVEL SECURITY;
CREATE POLICY pol_org ON previsto_semanal
    USING (EXISTS (SELECT 1 FROM baseline b JOIN contrato c ON c.id = b.contrato_id
                    WHERE b.id = baseline_id AND c.organizacao_id = org_corrente()))
    WITH CHECK (EXISTS (SELECT 1 FROM baseline b JOIN contrato c ON c.id = b.contrato_id
                    WHERE b.id = baseline_id AND c.organizacao_id = org_corrente()));

ALTER TABLE atividade ENABLE ROW LEVEL SECURITY;
ALTER TABLE atividade FORCE ROW LEVEL SECURITY;
CREATE POLICY pol_org ON atividade
    USING (EXISTS (SELECT 1 FROM eap_no n JOIN contrato c ON c.id = n.contrato_id
                    WHERE n.id = eap_no_id AND c.organizacao_id = org_corrente()))
    WITH CHECK (EXISTS (SELECT 1 FROM eap_no n JOIN contrato c ON c.id = n.contrato_id
                    WHERE n.id = eap_no_id AND c.organizacao_id = org_corrente()));

ALTER TABLE lvp_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE lvp_item FORCE ROW LEVEL SECURITY;
CREATE POLICY pol_org ON lvp_item
    USING (EXISTS (SELECT 1 FROM lvp_avaliacao a JOIN contrato c ON c.id = a.contrato_id
                    WHERE a.id = lvp_id AND c.organizacao_id = org_corrente()))
    WITH CHECK (EXISTS (SELECT 1 FROM lvp_avaliacao a JOIN contrato c ON c.id = a.contrato_id
                    WHERE a.id = lvp_id AND c.organizacao_id = org_corrente()));

ALTER TABLE vinculo ENABLE ROW LEVEL SECURITY;
ALTER TABLE vinculo FORCE ROW LEVEL SECURITY;
CREATE POLICY pol_org ON vinculo
    USING (EXISTS (SELECT 1 FROM atividade a JOIN eap_no n ON n.id = a.eap_no_id
                     JOIN contrato c ON c.id = n.contrato_id
                    WHERE a.id = predecessora_id AND c.organizacao_id = org_corrente()))
    WITH CHECK (EXISTS (SELECT 1 FROM atividade a JOIN eap_no n ON n.id = a.eap_no_id
                     JOIN contrato c ON c.id = n.contrato_id
                    WHERE a.id = predecessora_id AND c.organizacao_id = org_corrente()));

-- guarda de regressão: nenhuma tabela de plan pode ficar sem RLS
CREATE OR REPLACE VIEW v_rls_auditoria AS
SELECT c.relname AS tabela, c.relrowsecurity AS rls, c.relforcerowsecurity AS forcada
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'plan' AND c.relkind = 'r'
   AND c.relname NOT IN ('organizacao', 'usuario_organizacao')
   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);

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
