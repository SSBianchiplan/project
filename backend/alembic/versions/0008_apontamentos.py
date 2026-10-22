"""Apontamento de horas por atividade (ledger append-only).

Revision ID: 0008_apontamentos
Revises: 0007_custos
"""
from alembic import op
from app.infra.ddl import ler

revision = "0008_apontamentos"
down_revision = "0007_custos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ler("011_apontamentos.sql"))


def downgrade() -> None:
    op.execute("""
        SET search_path = plan, public;
        DROP TABLE IF EXISTS apontamento;
        DROP FUNCTION IF EXISTS fn_apontamento_coerencia() CASCADE;
    """)
