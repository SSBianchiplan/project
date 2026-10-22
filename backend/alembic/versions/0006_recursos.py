"""Alocação de recursos: catálogo por contrato, alocação por atividade.

Revision ID: 0006_recursos
Revises: 0005_usuarios
"""
from alembic import op
from app.infra.ddl import ler

revision = "0006_recursos"
down_revision = "0005_usuarios"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ler("009_recursos.sql"))


def downgrade() -> None:
    op.execute("""
        SET search_path = plan, public;
        DROP TABLE IF EXISTS alocacao;
        DROP FUNCTION IF EXISTS fn_alocacao_coerencia() CASCADE;
        DROP TABLE IF EXISTS recurso;
        DROP TYPE IF EXISTS tipo_recurso;
    """)
