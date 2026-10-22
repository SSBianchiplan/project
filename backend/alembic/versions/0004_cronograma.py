"""Cronograma avançado: atributos de atividade, calendário, rede sem ciclo, memória do CPM.

Revision ID: 0004_cronograma
Revises: 0003_documentos_importacao
"""
from alembic import op
from app.infra.ddl import ler

revision = "0004_cronograma"
down_revision = "0003_documentos_importacao"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ler("006_cronograma.sql"))


def downgrade() -> None:
    op.execute("""
        SET search_path = plan, public;
        DROP TABLE IF EXISTS cronograma_calculo;
        DROP TABLE IF EXISTS baseline_atividade;
        DROP FUNCTION IF EXISTS fn_baseline_atividade_imutavel() CASCADE;
        DROP TABLE IF EXISTS feriado;
        DROP TRIGGER IF EXISTS tg_vinculo_sem_ciclo ON vinculo;
        DROP FUNCTION IF EXISTS fn_vinculo_sem_ciclo() CASCADE;
        DROP TRIGGER IF EXISTS tg_atividade_coerencia ON atividade;
        DROP FUNCTION IF EXISTS fn_atividade_coerencia() CASCADE;
        ALTER TABLE atividade
            DROP CONSTRAINT IF EXISTS uq_atividade_no,
            DROP CONSTRAINT IF EXISTS ck_atv_restricao_data,
            DROP CONSTRAINT IF EXISTS ck_atv_prioridade,
            DROP CONSTRAINT IF EXISTS ck_atv_turno,
            DROP CONSTRAINT IF EXISTS ck_atv_recursos,
            DROP COLUMN IF EXISTS contrato_id,
            DROP COLUMN IF EXISTS restricao,
            DROP COLUMN IF EXISTS restricao_data,
            DROP COLUMN IF EXISTS restricao_justificativa,
            DROP COLUMN IF EXISTS deadline,
            DROP COLUMN IF EXISTS calendario,
            DROP COLUMN IF EXISTS disciplina,
            DROP COLUMN IF EXISTS responsavel,
            DROP COLUMN IF EXISTS prioridade,
            DROP COLUMN IF EXISTS notas,
            DROP COLUMN IF EXISTS situacao,
            DROP COLUMN IF EXISTS mod_qtd,
            DROP COLUMN IF EXISTS moi_qtd,
            DROP COLUMN IF EXISTS equipamentos_qtd,
            DROP COLUMN IF EXISTS hh_estimadas,
            DROP COLUMN IF EXISTS turno,
            DROP COLUMN IF EXISTS subcontratada,
            DROP COLUMN IF EXISTS criterio_medicao,
            DROP COLUMN IF EXISTS unidade,
            DROP COLUMN IF EXISTS qtd_prevista,
            DROP COLUMN IF EXISTS qtd_realizada,
            DROP COLUMN IF EXISTS atualizado_em;
        ALTER TABLE eap_no DROP COLUMN IF EXISTS memoria_peso;
        ALTER TABLE excecao_contratual DROP COLUMN IF EXISTS documento;
        DROP TYPE IF EXISTS situacao_atividade;
        DROP TYPE IF EXISTS restricao_atividade;
    """)
