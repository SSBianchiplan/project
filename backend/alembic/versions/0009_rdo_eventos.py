"""RDO: eventos de aprovação (planejamento → gerência), append-only.

Revision ID: 0009_rdo_eventos
Revises: 0008_apontamentos
"""
from alembic import op
from app.infra.ddl import ler

revision = "0009_rdo_eventos"
down_revision = "0008_apontamentos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ler("012_rdo_eventos.sql"))


def downgrade() -> None:
    op.execute("""
        SET search_path = plan, public;
        DROP TABLE IF EXISTS rdo_evento;
        DROP FUNCTION IF EXISTS fn_rdo_evento_coerencia() CASCADE;
    """)
