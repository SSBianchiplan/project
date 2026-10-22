-- =====================================================================
-- 008 — Usuários, papéis por organização e sessões de login.
-- Tabelas de identidade ficam fora da RLS: são lidas antes de a organização
-- da sessão existir. O que é de organização (papel) continua filtrado na
-- aplicação pelo contexto autenticado.
-- =====================================================================
SET search_path = plan, public;

CREATE TYPE papel_usuario AS ENUM ('ADMIN_MASTER', 'ADMIN', 'PLANEJADOR', 'FISCAL', 'LEITOR');

CREATE TABLE usuario (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email            text NOT NULL,
    nome             text NOT NULL,
    senha_hash       text NOT NULL,             -- pbkdf2$iteracoes$salt$hash (hex)
    master           boolean NOT NULL DEFAULT false,
    ativo            boolean NOT NULL DEFAULT true,
    deve_trocar_senha boolean NOT NULL DEFAULT false,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    ultimo_login     timestamptz,
    CONSTRAINT ck_usuario_email CHECK (email = lower(email) AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$')
);
CREATE UNIQUE INDEX uq_usuario_email ON usuario (email);

-- papel do usuário em cada organização; master dispensa papel (age como ADMIN em todas)
CREATE TABLE usuario_papel (
    usuario_id      uuid NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    organizacao_id  uuid NOT NULL REFERENCES organizacao(id) ON DELETE CASCADE,
    papel           papel_usuario NOT NULL,
    concedido_por   uuid REFERENCES usuario(id),
    concedido_em    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (usuario_id, organizacao_id),
    CONSTRAINT ck_papel_nao_master CHECK (papel <> 'ADMIN_MASTER')
);

-- sessão opaca: o cliente guarda o token; o banco guarda só o hash
CREATE TABLE sessao_login (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      uuid NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    organizacao_id  uuid REFERENCES organizacao(id) ON DELETE SET NULL,
    token_hash      bytea NOT NULL UNIQUE,
    criada_em       timestamptz NOT NULL DEFAULT now(),
    expira_em       timestamptz NOT NULL,
    revogada_em     timestamptz,
    agente          text NOT NULL DEFAULT ''
);
CREATE INDEX ix_sessao_usuario ON sessao_login (usuario_id, expira_em DESC);

-- ------------------------------------------------------------ auditoria RLS
-- identidade não é dado de contrato: fora da exigência de RLS forçada
CREATE OR REPLACE VIEW v_rls_auditoria AS
SELECT c.relname AS tabela, c.relrowsecurity AS rls, c.relforcerowsecurity AS forcada
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'plan' AND c.relkind = 'r'
   AND c.relname NOT IN ('organizacao', 'usuario_organizacao', 'usuario', 'usuario_papel', 'sessao_login')
   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);

-- ------------------------------------------------------------ privilégios
GRANT SELECT, INSERT, UPDATE, DELETE ON usuario, usuario_papel, sessao_login TO gps_app;
GRANT SELECT, INSERT, UPDATE ON organizacao TO gps_app;

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
