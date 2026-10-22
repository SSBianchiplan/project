-- =====================================================================
-- 014 — Endurecimento da identidade (auditoria de segurança 10/2026).
--
-- A4: o papel da aplicação (gps_app), sob o qual roda toda transação de
-- request com organização (SET LOCAL ROLE), não lê mais as tabelas de
-- identidade. Login, sessão e administração de usuários usam a conexão do
-- serviço (dono do schema) fora do SET ROLE, então nada muda para elas; o
-- que deixa de funcionar é só ler e-mail, senha_hash, papéis e sessões de
-- TODAS as organizações pelo explorador de tabelas.
-- organizacao continua legível por gps_app (red-team 005 e a própria RLS
-- resolvem a organização por código), mas sem INSERT/UPDATE.
--
-- M19: contador de tentativas de login falhas (por conta e por IP), fora
-- da RLS como as demais tabelas de identidade.
-- =====================================================================
SET search_path = plan, public;

REVOKE ALL ON usuario, usuario_papel, sessao_login, usuario_organizacao FROM gps_app;
REVOKE INSERT, UPDATE, DELETE ON organizacao FROM gps_app;

CREATE TABLE login_falha (
    id     bigserial PRIMARY KEY,
    chave  text NOT NULL,                       -- 'conta:<email>' ou 'ip:<endereço>'
    em     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_login_falha_chave ON login_falha (chave, em DESC);
CREATE INDEX ix_login_falha_em ON login_falha (em);
-- o ALTER DEFAULT PRIVILEGES do 003 concederia a gps_app: identidade não é dado de contrato
REVOKE ALL ON login_falha FROM gps_app;

CREATE OR REPLACE VIEW v_rls_auditoria AS
SELECT c.relname AS tabela, c.relrowsecurity AS rls, c.relforcerowsecurity AS forcada
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'plan' AND c.relkind = 'r'
   AND c.relname NOT IN ('organizacao', 'usuario_organizacao', 'usuario', 'usuario_papel', 'sessao_login',
                         'login_falha')
   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);
