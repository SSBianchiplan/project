-- =====================================================================
-- GPlanSiS|Planejamento — 001_core.sql
-- PostgreSQL 16. Invariantes do SPEC-GPS-PLN-SYS-001 embutidas no DDL.
-- Aplicar como uma transação. Reversão em 001_core_down.sql.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS plan;
SET search_path = plan, public;

CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

-- ------------------------------------------------------------------ tipos
CREATE TYPE modalidade_contrato AS ENUM ('EPC', 'EPCM', 'EPS', 'SERVICO_ENGENHARIA', 'CONSTRUCAO_CIVIL', 'MONTAGEM', 'FORNECIMENTO');
CREATE TYPE tipo_no             AS ENUM ('RESUMO', 'ATIVIDADE', 'MARCO');
CREATE TYPE fase_eap            AS ENUM ('MILESTONES', 'ENGENHARIA', 'SUPRIMENTOS', 'CONSTRUCAO_CIVIL', 'MONTAGEM_ELETROMECANICA', 'COMISSIONAMENTO');
CREATE TYPE estado_baseline     AS ENUM ('RASCUNHO', 'SUBMETIDA', 'APROVADA', 'SUPERADA');
CREATE TYPE tipo_medicao        AS ENUM ('MEDICAO', 'ESTORNO');
CREATE TYPE status_lvp          AS ENUM ('ATENDE', 'NAO_ATENDE', 'EM_ANALISE');
CREATE TYPE especie_lvp         AS ENUM ('LINHA_DE_BASE', 'ATUALIZACAO');
CREATE TYPE estado_plano        AS ENUM ('RASCUNHO', 'SUBMETIDO', 'ACEITO', 'CONCLUIDO', 'CANCELADO');

-- ------------------------------------------------------------- contrato
CREATE TABLE contrato (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo             text NOT NULL UNIQUE,
    contratante        text NOT NULL,
    contratada         text NOT NULL,
    empreendimento     text NOT NULL,
    modalidade         modalidade_contrato NOT NULL,
    base_ponderacao    numeric(14,4) NOT NULL DEFAULT 1000000,
    data_inicio        date NOT NULL,
    termino_contratual date NOT NULL,
    fuso               text NOT NULL DEFAULT 'America/Sao_Paulo',
    criado_em          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_contrato_base   CHECK (base_ponderacao > 0),
    CONSTRAINT ck_contrato_prazo  CHECK (termino_contratual > data_inicio)
);

-- ------------------------------------------------------------------ EAP
-- INV-EAP-002/003/004/005
CREATE TABLE eap_no (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id  uuid NOT NULL REFERENCES contrato(id) ON DELETE RESTRICT,
    parent_id    uuid REFERENCES eap_no(id) ON DELETE RESTRICT,
    edt          text NOT NULL,
    descricao    text NOT NULL,
    nivel        smallint NOT NULL,
    tipo         tipo_no NOT NULL,
    fase         fase_eap,
    folha        boolean NOT NULL,
    pontos       numeric(14,4) NOT NULL DEFAULT 0,
    criado_em    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_eap_edt        UNIQUE (contrato_id, edt),
    CONSTRAINT ck_eap_nivel      CHECK (nivel BETWEEN 1 AND 7),
    CONSTRAINT ck_eap_pontos_pos CHECK (pontos >= 0),
    -- INV-EAP-002: só folha pontua
    CONSTRAINT ck_eap_pontos_folha CHECK ((folha AND pontos >= 0) OR (NOT folha AND pontos = 0)),
    -- INV-EAP-003: marco não pontua
    CONSTRAINT ck_eap_marco      CHECK (tipo <> 'MARCO' OR pontos = 0),
    CONSTRAINT ck_eap_edt_fmt    CHECK (edt ~ '^[0-9]+(\.[0-9]+)*$')
);
CREATE INDEX ix_eap_contrato ON eap_no (contrato_id, edt);
CREATE INDEX ix_eap_parent   ON eap_no (parent_id);
CREATE INDEX ix_eap_folha    ON eap_no (contrato_id) WHERE folha;

-- INV-EAP-004/005: coerência pai/filho (EDT do pai é prefixo, nível = pai + 1)
CREATE OR REPLACE FUNCTION fn_eap_coerencia_pai() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE p record;
BEGIN
    IF NEW.parent_id IS NULL THEN
        IF NEW.nivel <> 1 THEN
            RAISE EXCEPTION 'INV-EAP-005: nó raiz deve ser nível 1 (edt=%)', NEW.edt;
        END IF;
        RETURN NEW;
    END IF;
    SELECT contrato_id, edt, nivel, folha INTO p FROM eap_no WHERE id = NEW.parent_id;
    IF p.contrato_id <> NEW.contrato_id THEN
        RAISE EXCEPTION 'INV-EAP-004: pai pertence a outro contrato';
    END IF;
    IF NEW.edt NOT LIKE p.edt || '.%' THEN
        RAISE EXCEPTION 'INV-EAP-004: EDT % não descende de %', NEW.edt, p.edt;
    END IF;
    IF NEW.nivel <> p.nivel + 1 THEN
        RAISE EXCEPTION 'INV-EAP-005: nível % incoerente com o pai (%)', NEW.nivel, p.nivel;
    END IF;
    IF p.folha THEN
        RAISE EXCEPTION 'INV-EAP-002: nó folha % não pode ter filhos', p.edt;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER tg_eap_coerencia_pai
    BEFORE INSERT OR UPDATE ON eap_no
    FOR EACH ROW EXECUTE FUNCTION fn_eap_coerencia_pai();

-- INV-EAP-001: soma dos pontos das folhas = base de ponderação.
-- Diferida: só cobra no COMMIT, para permitir montar a EAP dentro da transação.
CREATE OR REPLACE FUNCTION fn_eap_soma_pontos() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE c record;
BEGIN
    FOR c IN
        SELECT ct.id, ct.codigo, ct.base_ponderacao,
               COALESCE(SUM(n.pontos) FILTER (WHERE n.folha), 0) AS soma
          FROM contrato ct
          JOIN eap_no n ON n.contrato_id = ct.id
         GROUP BY ct.id, ct.codigo, ct.base_ponderacao
    LOOP
        IF c.soma <> c.base_ponderacao THEN
            RAISE EXCEPTION
                'INV-EAP-001: contrato % — soma dos pontos das folhas (%) difere da base (%)',
                c.codigo, c.soma, c.base_ponderacao;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER tg_eap_soma_pontos
    AFTER INSERT OR UPDATE OR DELETE ON eap_no
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_eap_soma_pontos();

-- ------------------------------------------------------------ cronograma
CREATE TABLE excecao_contratual (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id  uuid NOT NULL REFERENCES contrato(id),
    invariante   text NOT NULL,
    justificativa text NOT NULL,
    autorizada_por text NOT NULL,
    autorizada_em  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE atividade (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    eap_no_id      uuid NOT NULL REFERENCES eap_no(id) ON DELETE RESTRICT,
    codigo_externo text,                       -- ID no MS Project / P6
    duracao_dias   integer NOT NULL,
    inicio_previsto date NOT NULL,
    termino_previsto date NOT NULL,
    tipo           tipo_no NOT NULL,
    excecao_id     uuid REFERENCES excecao_contratual(id),
    CONSTRAINT ck_atv_duracao_nao_neg CHECK (duracao_dias >= 0),
    -- INV-CRN-004
    CONSTRAINT ck_atv_marco_zero CHECK (tipo <> 'MARCO' OR duracao_dias = 0),
    -- INV-CRN-001: máximo 15 dias, salvo exceção contratual registrada
    CONSTRAINT ck_atv_duracao_max CHECK (duracao_dias <= 15 OR excecao_id IS NOT NULL),
    CONSTRAINT ck_atv_janela CHECK (termino_previsto >= inicio_previsto)
);
CREATE UNIQUE INDEX uq_atv_codigo_externo ON atividade (eap_no_id, codigo_externo)
    WHERE codigo_externo IS NOT NULL;

CREATE TABLE vinculo (
    predecessora_id uuid NOT NULL REFERENCES atividade(id) ON DELETE CASCADE,
    sucessora_id    uuid NOT NULL REFERENCES atividade(id) ON DELETE CASCADE,
    tipo            text NOT NULL,             -- TI, II, TT, IT
    lag_dias        integer NOT NULL DEFAULT 0,
    PRIMARY KEY (predecessora_id, sucessora_id, tipo),
    CONSTRAINT ck_vinculo_tipo CHECK (tipo IN ('TI','II','TT','IT')),
    -- INV-CRN-002: lag negativo proibido, teto de 21 dias
    CONSTRAINT ck_vinculo_lag  CHECK (lag_dias >= 0 AND lag_dias <= 21),
    CONSTRAINT ck_vinculo_self CHECK (predecessora_id <> sucessora_id)
);

-- -------------------------------------------------------------- baseline
CREATE TABLE baseline (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id   uuid NOT NULL REFERENCES contrato(id) ON DELETE RESTRICT,
    numero        smallint NOT NULL,           -- 0 = original
    estado        estado_baseline NOT NULL DEFAULT 'RASCUNHO',
    lvp_id        uuid,                        -- FK adiante
    aprovada_por  text,
    aprovada_em   timestamptz,
    vigente       boolean NOT NULL DEFAULT false,
    criada_em     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_baseline_numero UNIQUE (contrato_id, numero),
    CONSTRAINT ck_baseline_numero CHECK (numero >= 0),
    -- INV-BL-001: aprovação exige quem aprovou, quando e a LVP vinculada
    CONSTRAINT ck_baseline_aprovacao CHECK (
        estado <> 'APROVADA'
        OR (aprovada_por IS NOT NULL AND aprovada_em IS NOT NULL AND lvp_id IS NOT NULL)
    ),
    CONSTRAINT ck_baseline_vigente_aprovada CHECK (NOT vigente OR estado = 'APROVADA')
);
-- INV-BL-002: no máximo uma vigente por contrato
CREATE UNIQUE INDEX uq_baseline_vigente ON baseline (contrato_id) WHERE vigente;

-- ------------------------------------------------------------------ LVP
CREATE TABLE lvp_avaliacao (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id  uuid NOT NULL REFERENCES contrato(id),
    especie      especie_lvp NOT NULL,
    emissao      text NOT NULL,
    nota         numeric(5,4) NOT NULL,
    aprovado     boolean NOT NULL,
    avaliado_por text NOT NULL,
    avaliado_em  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_lvp_nota CHECK (nota >= 0 AND nota <= 1),
    -- RGR-LVP-001: na LVP de linha de base, aprovação exige nota mínima de 85%
    CONSTRAINT ck_lvp_nota_minima CHECK (
        especie <> 'LINHA_DE_BASE' OR NOT aprovado OR nota >= 0.85
    )
);

CREATE TABLE lvp_item (
    lvp_id      uuid NOT NULL REFERENCES lvp_avaliacao(id) ON DELETE CASCADE,
    item        smallint NOT NULL,
    peso        numeric(6,5) NOT NULL,
    obrigatorio boolean NOT NULL DEFAULT false,
    status      status_lvp NOT NULL DEFAULT 'EM_ANALISE',
    observacao  text,
    PRIMARY KEY (lvp_id, item),
    CONSTRAINT ck_lvp_item_peso CHECK (peso >= 0 AND peso <= 1)
);

-- RGR-LVP-001: item obrigatório não atendido impede aprovação
CREATE OR REPLACE FUNCTION fn_lvp_obrigatorios() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE pendentes integer;
BEGIN
    SELECT count(*) INTO pendentes
      FROM lvp_avaliacao a
      JOIN lvp_item i ON i.lvp_id = a.id
     WHERE a.aprovado AND i.obrigatorio AND i.status <> 'ATENDE';
    IF pendentes > 0 THEN
        RAISE EXCEPTION 'RGR-LVP-001: % item(ns) obrigatório(s) sem ATENDE em avaliação aprovada', pendentes;
    END IF;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER tg_lvp_obrigatorios
    AFTER INSERT OR UPDATE ON lvp_item
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_lvp_obrigatorios();

CREATE CONSTRAINT TRIGGER tg_lvp_obrigatorios_aval
    AFTER INSERT OR UPDATE ON lvp_avaliacao
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_lvp_obrigatorios();

ALTER TABLE baseline
    ADD CONSTRAINT fk_baseline_lvp FOREIGN KEY (lvp_id) REFERENCES lvp_avaliacao(id);

-- ----------------------------------------------------- previsto semanal
-- INV-PRV-001/002
CREATE TABLE previsto_semanal (
    baseline_id uuid NOT NULL REFERENCES baseline(id) ON DELETE CASCADE,
    eap_no_id   uuid NOT NULL REFERENCES eap_no(id) ON DELETE RESTRICT,
    fim_semana  date NOT NULL,
    parcela     numeric(12,8) NOT NULL,
    PRIMARY KEY (baseline_id, eap_no_id, fim_semana),
    CONSTRAINT ck_prv_parcela CHECK (parcela >= 0 AND parcela <= 1),
    -- sábado = 6 no extract(dow)
    CONSTRAINT ck_prv_sabado  CHECK (extract(dow FROM fim_semana) = 6)
);
CREATE INDEX ix_prv_semana ON previsto_semanal (baseline_id, fim_semana);

-- INV-PRV-001: soma = 1 por folha em baseline aprovada (diferida)
CREATE OR REPLACE FUNCTION fn_previsto_soma() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT b.id AS baseline_id, b.numero, n.edt, SUM(p.parcela) AS soma
          FROM previsto_semanal p
          JOIN baseline b ON b.id = p.baseline_id
          JOIN eap_no  n ON n.id = p.eap_no_id
         WHERE b.estado = 'APROVADA'
         GROUP BY b.id, b.numero, n.edt
    LOOP
        IF round(r.soma, 6) <> 1 THEN
            RAISE EXCEPTION 'INV-PRV-001: BL% — previsto de % soma % (esperado 1)', r.numero, r.edt, r.soma;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER tg_previsto_soma
    AFTER INSERT OR UPDATE OR DELETE ON previsto_semanal
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_previsto_soma();

-- INV-BL-003: previsto de baseline aprovada é imutável
CREATE OR REPLACE FUNCTION fn_previsto_imutavel() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE est estado_baseline;
BEGIN
    SELECT estado INTO est FROM baseline
     WHERE id = COALESCE(NEW.baseline_id, OLD.baseline_id);
    IF est = 'APROVADA' AND TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'INV-BL-003: previsto de baseline aprovada é imutável — crie nova baseline';
    END IF;
    RETURN COALESCE(NEW, OLD);
END $$;

CREATE TRIGGER tg_previsto_imutavel
    BEFORE UPDATE OR DELETE ON previsto_semanal
    FOR EACH ROW EXECUTE FUNCTION fn_previsto_imutavel();

-- ------------------------------------------------------------- medição
-- INV-MED-001..006. Append-only: sem UPDATE, sem DELETE, encadeamento SHA-256.
CREATE TABLE medicao (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id      uuid NOT NULL REFERENCES contrato(id),
    eap_no_id        uuid NOT NULL REFERENCES eap_no(id),
    data_corte       date NOT NULL,
    tipo             tipo_medicao NOT NULL DEFAULT 'MEDICAO',
    percentual_ac    numeric(12,8) NOT NULL,
    memoria_calculo  jsonb NOT NULL,
    evidencia        text NOT NULL,
    estorna_id       uuid REFERENCES medicao(id),
    justificativa    text,
    criado_por       text NOT NULL,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    hash_anterior    bytea,
    hash             bytea NOT NULL,
    CONSTRAINT ck_med_pct     CHECK (percentual_ac >= 0 AND percentual_ac <= 1),
    -- INV-MED-004
    CONSTRAINT ck_med_sabado  CHECK (extract(dow FROM data_corte) = 6),
    -- estorno exige referência e justificativa
    CONSTRAINT ck_med_estorno CHECK (
        tipo <> 'ESTORNO' OR (estorna_id IS NOT NULL AND justificativa IS NOT NULL)
    )
);
-- INV-MED-005: uma medição vigente por (nó, data de corte)
CREATE UNIQUE INDEX uq_medicao_vigente ON medicao (eap_no_id, data_corte)
    WHERE tipo = 'MEDICAO' AND estorna_id IS NULL;
CREATE INDEX ix_medicao_corte ON medicao (contrato_id, data_corte);

-- INV-MED-006 + INV-MED-003 + encadeamento
CREATE OR REPLACE FUNCTION fn_medicao_guarda() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n record;
    anterior record;   -- vigente cronológico: compara retrocesso
    ultimo   record;   -- último gravado: encadeia o hash (ledger segue a ordem de gravação)
BEGIN
    SELECT folha, edt, contrato_id INTO n FROM eap_no WHERE id = NEW.eap_no_id;
    IF NOT n.folha THEN
        RAISE EXCEPTION 'INV-MED-006: medição só é aceita em nó folha (% não é)', n.edt;
    END IF;
    IF n.contrato_id <> NEW.contrato_id THEN
        RAISE EXCEPTION 'INV-MED-006: nó % não pertence ao contrato informado', n.edt;
    END IF;

    SELECT id, percentual_ac, hash INTO anterior
      FROM medicao
     WHERE eap_no_id = NEW.eap_no_id AND data_corte <= NEW.data_corte
     ORDER BY data_corte DESC, criado_em DESC
     LIMIT 1;
    SELECT hash INTO ultimo
      FROM medicao
     WHERE eap_no_id = NEW.eap_no_id
     ORDER BY criado_em DESC, data_corte DESC
     LIMIT 1;

    -- INV-MED-003: não retrocede, exceto estorno
    IF anterior.id IS NOT NULL
       AND NEW.tipo = 'MEDICAO'
       AND NEW.percentual_ac < anterior.percentual_ac THEN
        RAISE EXCEPTION
            'INV-MED-003: avanço de % retrocede de % para % (fração acumulada) — use ESTORNO com justificativa',
            n.edt, anterior.percentual_ac, NEW.percentual_ac;
    END IF;

    NEW.hash_anterior := ultimo.hash;
    NEW.hash := digest(
        coalesce(encode(ultimo.hash, 'hex'), '') ||
        NEW.eap_no_id::text || NEW.data_corte::text || NEW.tipo::text ||
        NEW.percentual_ac::text || NEW.criado_por,
        'sha256');
    RETURN NEW;
END $$;

CREATE TRIGGER tg_medicao_guarda
    BEFORE INSERT ON medicao
    FOR EACH ROW EXECUTE FUNCTION fn_medicao_guarda();

-- INV-MED-001: append-only
CREATE OR REPLACE FUNCTION fn_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'append-only: % não é permitido em %', TG_OP, TG_TABLE_NAME;
END $$;

CREATE TRIGGER tg_medicao_append_only
    BEFORE UPDATE OR DELETE ON medicao
    FOR EACH ROW EXECUTE FUNCTION fn_append_only();

-- ------------------------------------------------------------ memória de cálculo
CREATE TABLE calculo_execucao (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id   uuid NOT NULL REFERENCES contrato(id),
    regra         text NOT NULL,              -- RGR-AVC-001, RGR-CRV-001, ...
    versao_regra  text NOT NULL,
    versao_motor  text NOT NULL,
    data_corte    date NOT NULL,
    entradas      jsonb NOT NULL,             -- identificadores, nunca cópia dos dados
    parcelas      jsonb NOT NULL,
    resultado     numeric(12,8),
    executado_em  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_calculo_idem UNIQUE (contrato_id, regra, versao_regra, data_corte)
);

-- --------------------------------------------------- desvios e planos
CREATE TABLE plano_acao (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id   uuid NOT NULL REFERENCES contrato(id),
    eap_no_id     uuid NOT NULL REFERENCES eap_no(id),
    data_corte    date NOT NULL,
    desvio_abs    numeric(12,8) NOT NULL,
    desvio_rel    numeric(12,8),
    estado        estado_plano NOT NULL DEFAULT 'RASCUNHO',
    causa_raiz    text NOT NULL,
    acao          text NOT NULL,
    responsavel   text NOT NULL,
    prazo         date NOT NULL,
    criado_em     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_plano_sabado CHECK (extract(dow FROM data_corte) = 6)
);
CREATE INDEX ix_plano_corte ON plano_acao (contrato_id, data_corte);

CREATE TABLE periodo_fechamento (
    contrato_id uuid NOT NULL REFERENCES contrato(id),
    data_corte  date NOT NULL,
    fechado_por text NOT NULL,
    fechado_em  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (contrato_id, data_corte),
    CONSTRAINT ck_fech_sabado CHECK (extract(dow FROM data_corte) = 6)
);

-- INV-DSV-001: não fecha período com desvio pior que -5% sem plano fora de RASCUNHO
CREATE OR REPLACE FUNCTION fn_fechamento_exige_plano() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE pendentes integer;
BEGIN
    SELECT count(*) INTO pendentes
      FROM plano_acao p
     WHERE p.contrato_id = NEW.contrato_id
       AND p.data_corte = NEW.data_corte
       AND p.desvio_abs < -0.05
       AND p.estado = 'RASCUNHO';
    IF pendentes > 0 THEN
        RAISE EXCEPTION
            'INV-DSV-001: % plano(s) de ação em RASCUNHO para desvio abaixo de -5%% em %',
            pendentes, NEW.data_corte;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER tg_fechamento_exige_plano
    BEFORE INSERT ON periodo_fechamento
    FOR EACH ROW EXECUTE FUNCTION fn_fechamento_exige_plano();

-- ------------------------------------------------------------------ RDO
CREATE TABLE rdo (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contrato_id  uuid NOT NULL REFERENCES contrato(id),
    data         date NOT NULL,
    numero       integer NOT NULL,
    emitido_em   timestamptz NOT NULL,
    valido       boolean NOT NULL,
    clima        jsonb NOT NULL,
    horas_paradas numeric(6,2) NOT NULL DEFAULT 0,
    motivo_parada text,
    efetivo      jsonb NOT NULL,
    servicos     jsonb NOT NULL,
    ocorrencias  jsonb NOT NULL DEFAULT '[]'::jsonb,
    criado_por   text NOT NULL,
    criado_em    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_rdo_dia    UNIQUE (contrato_id, data),
    CONSTRAINT uq_rdo_numero UNIQUE (contrato_id, numero),
    CONSTRAINT ck_rdo_parada CHECK (horas_paradas = 0 OR motivo_parada IS NOT NULL)
);
CREATE TRIGGER tg_rdo_append_only
    BEFORE UPDATE OR DELETE ON rdo
    FOR EACH ROW EXECUTE FUNCTION fn_append_only();

-- ------------------------------------------------- projeções de leitura
CREATE OR REPLACE VIEW v_avanco_folha AS
SELECT n.contrato_id,
       n.id  AS eap_no_id,
       n.edt,
       n.pontos / c.base_ponderacao AS peso,
       m.data_corte,
       m.percentual_ac
  FROM eap_no n
  JOIN contrato c ON c.id = n.contrato_id
  LEFT JOIN LATERAL (
        SELECT data_corte, percentual_ac
          FROM medicao
         WHERE eap_no_id = n.id AND tipo = 'MEDICAO' AND estorna_id IS NULL
         ORDER BY data_corte DESC
         LIMIT 1
  ) m ON true
 WHERE n.folha AND n.pontos > 0;

-- RGR-AVC-001 aplicado ao contrato inteiro
CREATE OR REPLACE VIEW v_avanco_contrato AS
SELECT contrato_id,
       SUM(peso * COALESCE(percentual_ac, 0)) / NULLIF(SUM(peso), 0) AS real_ac
  FROM v_avanco_folha
 GROUP BY contrato_id;

-- privilégios: a aplicação nunca altera fato
-- REVOKE UPDATE, DELETE ON plan.medicao, plan.rdo FROM app_role;

-- ------------------------------------------------------------ search_path
-- Funções PL/pgSQL resolvem nomes em tempo de execução pelo search_path da
-- sessão que as dispara. A aplicação não conecta com "plan" no caminho;
-- fixar aqui torna trigger e policy independentes de quem chama.
DO $$
DECLARE f record;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure AS assinatura
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'plan' AND p.prokind = 'f'
           AND p.oid NOT IN (SELECT objid FROM pg_depend WHERE deptype = 'e')  -- não é de extensão
    LOOP
        EXECUTE format('ALTER FUNCTION %s SET search_path = plan, public', f.assinatura);
    END LOOP;
END $$;
