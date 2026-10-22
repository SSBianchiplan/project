"""Multiempresa com RLS forçada por organização.

Revision ID: 0002_multiempresa
Revises: 0001_core
"""
from alembic import op
from app.infra.ddl import ler

revision = "0002_multiempresa"
down_revision = "0001_core"
branch_labels = None
depends_on = None

TABELAS_RLS = [
    "contrato", "eap_no", "baseline", "lvp_avaliacao", "lvp_item", "medicao",
    "calculo_execucao", "plano_acao", "periodo_fechamento", "rdo",
    "excecao_contratual", "previsto_semanal", "atividade", "vinculo",
]


def upgrade() -> None:
    op.execute(ler("003_multiempresa.sql"))


def downgrade() -> None:
    """Reverter RLS é a operação mais perigosa deste conjunto: entre o DROP
    POLICY e o fim da transação o isolamento entre organizações não existe.
    Rodar apenas com a aplicação parada."""
    for t in TABELAS_RLS:
        op.execute(f"DROP POLICY IF EXISTS pol_org ON plan.{t}")
        op.execute(f"ALTER TABLE plan.{t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE plan.{t} DISABLE ROW LEVEL SECURITY")
    op.execute("""
        SET search_path = plan, public;
        DROP VIEW IF EXISTS v_rls_auditoria;
        DROP FUNCTION IF EXISTS fn_aplicar_rls(text, text);
        DROP FUNCTION IF EXISTS org_corrente();
        ALTER TABLE contrato DROP CONSTRAINT IF EXISTS uq_contrato_codigo;
        ALTER TABLE contrato ADD CONSTRAINT contrato_codigo_key UNIQUE (codigo);
        DROP INDEX IF EXISTS ix_contrato_org;
        ALTER TABLE contrato DROP COLUMN IF EXISTS organizacao_id;
        DROP TABLE IF EXISTS usuario_organizacao;
        DROP TABLE IF EXISTS organizacao;
    """)
    # gps_app não é removida: outros bancos do cluster podem usar o mesmo papel.
