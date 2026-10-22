"""Usuários, papéis por organização e sessões de login.

Revision ID: 0005_usuarios
Revises: 0004_cronograma
"""
from alembic import op
from app.infra.ddl import ler

revision = "0005_usuarios"
down_revision = "0004_cronograma"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ler("008_usuarios.sql"))


def downgrade() -> None:
    op.execute("""
        SET search_path = plan, public;
        CREATE OR REPLACE VIEW v_rls_auditoria AS
        SELECT c.relname AS tabela, c.relrowsecurity AS rls, c.relforcerowsecurity AS forcada
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'plan' AND c.relkind = 'r'
           AND c.relname NOT IN ('organizacao', 'usuario_organizacao')
           AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);
        DROP TABLE IF EXISTS sessao_login;
        DROP TABLE IF EXISTS usuario_papel;
        DROP TABLE IF EXISTS usuario;
        DROP TYPE IF EXISTS papel_usuario;
    """)
