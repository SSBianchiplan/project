"""Custos do projeto: itens de orçamento por atividade e ledger de realizado.

Revision ID: 0007_custos
Revises: 0006_recursos
"""
from alembic import op
from app.infra.ddl import ler

revision = "0007_custos"
down_revision = "0006_recursos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ler("010_custos.sql"))


def downgrade() -> None:
    op.execute("""
        SET search_path = plan, public;
        DROP TABLE IF EXISTS custo_realizado;
        DROP FUNCTION IF EXISTS fn_custo_real_coerencia() CASCADE;
        DROP TABLE IF EXISTS custo_item;
        DROP TYPE IF EXISTS categoria_custo;
    """)
