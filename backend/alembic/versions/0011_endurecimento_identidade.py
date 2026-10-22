"""Endurecimento da identidade: gps_app sem acesso às tabelas de usuário e
contador de tentativas de login (auditoria de segurança 10/2026).

Revision ID: 0011_endurecimento_identidade
Revises: 0010_boletim_e_rdo_editavel
"""
from alembic import op
from app.infra.ddl import ler

revision = "0011_endurecimento_identidade"
down_revision = "0010_boletim_e_rdo_editavel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ler("014_endurecimento_identidade.sql"))


def downgrade() -> None:
    op.execute("""
        SET search_path = plan, public;
        CREATE OR REPLACE VIEW v_rls_auditoria AS
        SELECT c.relname AS tabela, c.relrowsecurity AS rls, c.relforcerowsecurity AS forcada
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'plan' AND c.relkind = 'r'
           AND c.relname NOT IN ('organizacao', 'usuario_organizacao', 'usuario', 'usuario_papel', 'sessao_login')
           AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);
        DROP TABLE IF EXISTS login_falha;
        GRANT SELECT, INSERT, UPDATE, DELETE ON usuario, usuario_papel, sessao_login, usuario_organizacao TO gps_app;
        GRANT SELECT, INSERT, UPDATE ON organizacao TO gps_app;
    """)
