"""Documentos de engenharia ponderados manualmente e staging de importação.

Revision ID: 0003_documentos_importacao
Revises: 0002_multiempresa
"""
from alembic import op
from app.infra.ddl import ler

revision = "0003_documentos_importacao"
down_revision = "0002_multiempresa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ler("004_documentos_importacao.sql"))
    # as tabelas novas também precisam de RLS — INV-ORG-001
    op.execute("""
        SELECT plan.fn_aplicar_rls('documento_engenharia', 'contrato_id');
        SELECT plan.fn_aplicar_rls('importacao', 'contrato_id');
        ALTER TABLE plan.documento_evento ENABLE ROW LEVEL SECURITY;
        ALTER TABLE plan.documento_evento FORCE ROW LEVEL SECURITY;
        CREATE POLICY pol_org ON plan.documento_evento
            USING (EXISTS (SELECT 1 FROM plan.documento_engenharia d
                             JOIN plan.contrato c ON c.id = d.contrato_id
                            WHERE d.id = documento_id
                              AND c.organizacao_id = plan.org_corrente()))
            WITH CHECK (EXISTS (SELECT 1 FROM plan.documento_engenharia d
                             JOIN plan.contrato c ON c.id = d.contrato_id
                            WHERE d.id = documento_id
                              AND c.organizacao_id = plan.org_corrente()));
    """)


def downgrade() -> None:
    op.execute("""
        SET search_path = plan, public;
        DROP VIEW IF EXISTS v_avanco_documental;
        DROP TABLE IF EXISTS importacao;
        DROP TABLE IF EXISTS documento_evento;
        DROP TABLE IF EXISTS documento_engenharia;
        DROP FUNCTION IF EXISTS fn_importacao_sem_avanco() CASCADE;
        DROP FUNCTION IF EXISTS fn_doc_soma_pontos() CASCADE;
        DROP FUNCTION IF EXISTS pct_estagio(estagio_documento);
        DROP TYPE IF EXISTS estado_importacao;
        DROP TYPE IF EXISTS formato_importacao;
        DROP TYPE IF EXISTS estagio_documento;
    """)
