"""Boletim de medição (BM) append-only e RDO editável antes do envio.

Revision ID: 0010_boletim_e_rdo_editavel
Revises: 0009_rdo_eventos
"""
from alembic import op
from app.infra.ddl import ler

revision = "0010_boletim_e_rdo_editavel"
down_revision = "0009_rdo_eventos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ler("013_boletim_e_rdo_editavel.sql"))


def downgrade() -> None:
    op.execute("""
        SET search_path = plan, public;
        DROP TABLE IF EXISTS boletim_medicao;
        DROP TRIGGER IF EXISTS tg_rdo_editavel ON rdo;
        DROP FUNCTION IF EXISTS fn_rdo_editavel() CASCADE;
        REVOKE UPDATE ON rdo FROM gps_app;
        CREATE TRIGGER tg_rdo_append_only BEFORE UPDATE OR DELETE ON rdo FOR EACH ROW EXECUTE FUNCTION fn_append_only();
        ALTER TABLE rdo_evento DROP CONSTRAINT IF EXISTS rdo_evento_evento_check;
        ALTER TABLE rdo_evento ADD CONSTRAINT rdo_evento_evento_check CHECK (evento IN
            ('EMITIDO', 'ENVIADO', 'APROVADO_PLANEJAMENTO', 'REPROVADO_PLANEJAMENTO', 'APROVADO_GERENCIA', 'REPROVADO_GERENCIA'));
    """)
