"""Núcleo: contrato, EAP, cronograma, baseline, LVP, medição, RDO.

Revision ID: 0001_core
Revises:
"""
from alembic import op
from app.infra.ddl import ler

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ler("001_core.sql"))


def downgrade() -> None:
    # Ordem inversa de dependência. DROP SCHEMA CASCADE seria mais curto e
    # levaria junto qualquer objeto criado fora da migration — inclusive dado.
    op.execute("""
        SET search_path = plan, public;
        DROP VIEW IF EXISTS v_avanco_contrato;
        DROP VIEW IF EXISTS v_avanco_folha;
        DROP TABLE IF EXISTS rdo;
        DROP TABLE IF EXISTS periodo_fechamento;
        DROP TABLE IF EXISTS plano_acao;
        DROP TABLE IF EXISTS calculo_execucao;
        DROP TABLE IF EXISTS medicao;
        DROP TABLE IF EXISTS previsto_semanal;
        ALTER TABLE IF EXISTS baseline DROP CONSTRAINT IF EXISTS fk_baseline_lvp;
        DROP TABLE IF EXISTS lvp_item;
        DROP TABLE IF EXISTS lvp_avaliacao;
        DROP TABLE IF EXISTS baseline;
        DROP TABLE IF EXISTS vinculo;
        DROP TABLE IF EXISTS atividade;
        DROP TABLE IF EXISTS excecao_contratual;
        DROP TABLE IF EXISTS eap_no;
        DROP TABLE IF EXISTS contrato;
        DROP FUNCTION IF EXISTS fn_append_only() CASCADE;
        DROP FUNCTION IF EXISTS fn_medicao_guarda() CASCADE;
        DROP FUNCTION IF EXISTS fn_previsto_imutavel() CASCADE;
        DROP FUNCTION IF EXISTS fn_previsto_soma() CASCADE;
        DROP FUNCTION IF EXISTS fn_fechamento_exige_plano() CASCADE;
        DROP FUNCTION IF EXISTS fn_lvp_obrigatorios() CASCADE;
        DROP FUNCTION IF EXISTS fn_eap_soma_pontos() CASCADE;
        DROP FUNCTION IF EXISTS fn_eap_coerencia_pai() CASCADE;
        DROP TYPE IF EXISTS estado_plano;
        DROP TYPE IF EXISTS especie_lvp;
        DROP TYPE IF EXISTS status_lvp;
        DROP TYPE IF EXISTS tipo_medicao;
        DROP TYPE IF EXISTS estado_baseline;
        DROP TYPE IF EXISTS fase_eap;
        DROP TYPE IF EXISTS tipo_no;
        DROP TYPE IF EXISTS modalidade_contrato;
        -- extensões ficam em public (compartilhadas); removidas aqui só se
        -- nada mais depender delas — o banco é dedicado ao GPlanSiS.
        DROP EXTENSION IF EXISTS btree_gist;
        DROP EXTENSION IF EXISTS pgcrypto;
        DROP SCHEMA IF EXISTS plan;
    """)
