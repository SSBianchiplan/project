"""Leitura e escrita do cronograma. SQL explícito; o cálculo fica no domínio."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class LinhaContrato:
    id: UUID
    codigo: str
    empreendimento: str
    contratante: str
    contratada: str
    base_ponderacao: Decimal
    data_inicio: date
    termino_contratual: date


async def contratos(s: AsyncSession) -> list[LinhaContrato]:
    rows = (await s.execute(text("""
        SELECT id, codigo, empreendimento, contratante, contratada, base_ponderacao,
               data_inicio, termino_contratual
          FROM plan.contrato ORDER BY codigo"""))).all()
    return [LinhaContrato(*r) for r in rows]


async def contrato(s: AsyncSession, contrato_id: UUID) -> LinhaContrato | None:
    r = (await s.execute(text("""
        SELECT id, codigo, empreendimento, contratante, contratada, base_ponderacao,
               data_inicio, termino_contratual
          FROM plan.contrato WHERE id = :id"""), {"id": contrato_id})).one_or_none()
    return LinhaContrato(*r) if r else None


async def nos(s: AsyncSession, contrato_id: UUID) -> list[dict[str, Any]]:
    rows = (await s.execute(text("""
        SELECT n.id, n.edt, n.descricao, n.nivel, n.tipo, n.folha, n.pontos, n.memoria_peso,
               p.edt AS parent_edt
          FROM plan.eap_no n LEFT JOIN plan.eap_no p ON p.id = n.parent_id
         WHERE n.contrato_id = :id
         ORDER BY string_to_array(n.edt, '.')::int[]"""), {"id": contrato_id})).mappings().all()
    return [dict(r) for r in rows]


async def atividades(s: AsyncSession, contrato_id: UUID) -> list[dict[str, Any]]:
    rows = (await s.execute(text("""
        SELECT a.id, a.eap_no_id, n.edt, n.descricao, n.pontos, n.memoria_peso,
               a.codigo_externo, a.duracao_dias, a.inicio_previsto, a.termino_previsto, a.tipo,
               a.restricao, a.restricao_data, a.restricao_justificativa, a.deadline,
               a.calendario, a.disciplina, a.responsavel, a.prioridade, a.notas, a.situacao,
               a.mod_qtd, a.moi_qtd, a.equipamentos_qtd, a.hh_estimadas, a.turno,
               a.subcontratada, a.criterio_medicao, a.unidade, a.qtd_prevista, a.qtd_realizada,
               a.excecao_id, e.documento AS excecao_documento,
               e.autorizada_por AS excecao_por, e.justificativa AS excecao_just
          FROM plan.atividade a
          JOIN plan.eap_no n ON n.id = a.eap_no_id
          LEFT JOIN plan.excecao_contratual e ON e.id = a.excecao_id
         WHERE a.contrato_id = :id
         ORDER BY string_to_array(n.edt, '.')::int[]"""), {"id": contrato_id})).mappings().all()
    return [dict(r) for r in rows]


async def vinculos(s: AsyncSession, contrato_id: UUID) -> list[dict[str, Any]]:
    rows = (await s.execute(text("""
        SELECT np.edt AS pred_edt, ns.edt AS suc_edt, v.tipo, v.lag_dias AS lag
          FROM plan.vinculo v
          JOIN plan.atividade ap ON ap.id = v.predecessora_id
          JOIN plan.atividade "as" ON "as".id = v.sucessora_id
          JOIN plan.eap_no np ON np.id = ap.eap_no_id
          JOIN plan.eap_no ns ON ns.id = "as".eap_no_id
         WHERE ap.contrato_id = :id
         ORDER BY np.edt, ns.edt"""), {"id": contrato_id})).mappings().all()
    return [dict(r) for r in rows]


async def feriados(s: AsyncSession, contrato_id: UUID) -> list[date]:
    rows = (await s.execute(text(
        "SELECT data FROM plan.feriado WHERE contrato_id = :id ORDER BY data"),
        {"id": contrato_id})).scalars().all()
    return list(rows)


async def baseline_vigente(s: AsyncSession, contrato_id: UUID) -> dict[str, Any] | None:
    b = (await s.execute(text("""
        SELECT id, numero, aprovada_em FROM plan.baseline
         WHERE contrato_id = :id AND vigente ORDER BY numero DESC LIMIT 1"""),
        {"id": contrato_id})).mappings().one_or_none()
    if b is None:
        return None
    datas = (await s.execute(text("""
        SELECT n.edt, ba.inicio, ba.termino, ba.duracao_dias
          FROM plan.baseline_atividade ba
          JOIN plan.atividade a ON a.id = ba.atividade_id
          JOIN plan.eap_no n ON n.id = a.eap_no_id
         WHERE ba.baseline_id = :b"""), {"b": b["id"]})).mappings().all()
    return {"id": b["id"], "numero": b["numero"], "aprovada_em": b["aprovada_em"],
            "datas": {d["edt"]: dict(d) for d in datas}}


async def previsto_baseline(
    s: AsyncSession, baseline_id: UUID,
) -> dict[tuple[str, date], Decimal]:
    rows = (await s.execute(text("""
        SELECT n.edt, p.fim_semana, p.parcela
          FROM plan.previsto_semanal p JOIN plan.eap_no n ON n.id = p.eap_no_id
         WHERE p.baseline_id = :b"""), {"b": baseline_id})).all()
    return {(r.edt, r.fim_semana): r.parcela for r in rows}


async def medicao_vigente(
    s: AsyncSession, contrato_id: UUID, ate: date | None = None,
) -> dict[str, Decimal]:
    """Último lançamento por folha até a data (inclusive). Estorno conta: ele
    grava o valor restaurado, então 'último' é sempre o vigente."""
    rows = (await s.execute(text("""
        SELECT DISTINCT ON (m.eap_no_id) n.edt, m.percentual_ac
          FROM plan.medicao m JOIN plan.eap_no n ON n.id = m.eap_no_id
         WHERE m.contrato_id = :id AND (CAST(:ate AS date) IS NULL OR m.data_corte <= :ate)
         ORDER BY m.eap_no_id, m.data_corte DESC, m.criado_em DESC"""),
        {"id": contrato_id, "ate": ate})).all()
    return {r.edt: r.percentual_ac for r in rows}


async def medicoes_por_semana(
    s: AsyncSession, contrato_id: UUID,
) -> list[dict[str, Any]]:
    """Todos os lançamentos, para reconstruir o vigente em qualquer sábado."""
    rows = (await s.execute(text("""
        SELECT n.edt, m.data_corte, m.percentual_ac, m.criado_em
          FROM plan.medicao m JOIN plan.eap_no n ON n.id = m.eap_no_id
         WHERE m.contrato_id = :id
         ORDER BY m.data_corte, m.criado_em"""), {"id": contrato_id})).mappings().all()
    return [dict(r) for r in rows]


async def medicoes(s: AsyncSession, contrato_id: UUID, limite: int = 80) -> list[dict[str, Any]]:
    rows = (await s.execute(text("""
        SELECT m.id, m.data_corte, n.edt, n.descricao, m.tipo, m.percentual_ac, m.evidencia,
               m.criado_por, m.criado_em, m.estorna_id, m.justificativa, m.memoria_calculo,
               encode(m.hash, 'hex') AS hash, encode(m.hash_anterior, 'hex') AS hash_anterior
          FROM plan.medicao m JOIN plan.eap_no n ON n.id = m.eap_no_id
         WHERE m.contrato_id = :id
         ORDER BY m.criado_em DESC LIMIT :lim"""),
        {"id": contrato_id, "lim": limite})).mappings().all()
    return [dict(r) for r in rows]


async def gravar_calculo(
    s: AsyncSession, contrato_id: UUID, *, por: str, motivo: str, data_status: date | None,
    versao: str, fim_indice: int, fim_data: date, criticas: list[str],
    achados: list[dict[str, Any]], memoria: list[dict[str, Any]],
) -> UUID:
    corpo = json.dumps({"fim": fim_indice, "criticas": criticas, "memoria": memoria},
                       sort_keys=True, default=str)
    h = hashlib.sha256(corpo.encode()).digest()
    row = (await s.execute(text("""
        INSERT INTO plan.cronograma_calculo
            (contrato_id, calculado_por, motivo, data_status, versao_regra, termino_indice,
             termino_data, criticas, achados, memoria, hash)
        VALUES (:c, :por, :motivo, :ds, :v, :fi, :fd, CAST(:cr AS jsonb), CAST(:ach AS jsonb),
                CAST(:mem AS jsonb), :h)
        RETURNING id"""), {
        "c": contrato_id, "por": por, "motivo": motivo, "ds": data_status, "v": versao,
        "fi": fim_indice, "fd": fim_data, "cr": json.dumps(criticas),
        "ach": json.dumps(achados, default=str), "mem": json.dumps(memoria, default=str),
        "h": h})).one()
    return UUID(str(row.id))


async def atualizar_datas(
    s: AsyncSession, contrato_id: UUID, datas: dict[str, tuple[date, date]],
) -> None:
    """Grava início/término calculados em cada atividade (cache do vigente)."""
    for edt, (ini, fim) in datas.items():
        await s.execute(text("""
            UPDATE plan.atividade a SET inicio_previsto = :i, termino_previsto = :f
              FROM plan.eap_no n
             WHERE n.id = a.eap_no_id AND a.contrato_id = :c AND n.edt = :edt"""),
            {"i": ini, "f": fim, "c": contrato_id, "edt": edt})


async def substituir_vinculos(
    s: AsyncSession, contrato_id: UUID, novos: list[tuple[str, str, str, int]],
) -> None:
    await s.execute(text("""
        DELETE FROM plan.vinculo v USING plan.atividade a
         WHERE a.id = v.predecessora_id AND a.contrato_id = :c"""), {"c": contrato_id})
    for pred, suc, tipo, lag in novos:
        await s.execute(text("""
            INSERT INTO plan.vinculo (predecessora_id, sucessora_id, tipo, lag_dias)
            SELECT ap.id, "as".id, :tipo, :lag
              FROM plan.atividade ap JOIN plan.eap_no np ON np.id = ap.eap_no_id
              JOIN plan.atividade "as" ON "as".contrato_id = ap.contrato_id
              JOIN plan.eap_no ns ON ns.id = "as".eap_no_id
             WHERE ap.contrato_id = :c AND np.edt = :pred AND ns.edt = :suc"""),
            {"c": contrato_id, "pred": pred, "suc": suc, "tipo": tipo, "lag": lag})
