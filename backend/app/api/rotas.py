"""Contrato de API — onda 1. Persistência real depende da migration 001_core.sql."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dominio import avanco as dom_avanco
from ..dominio import curva_s as dom_curva
from ..dominio import etapas as dom_etapas
from ..dominio import lvp as dom_lvp
from ..dominio import ponderacao as dom_pond
from ..dominio.tipos import ErroDominio, No, TipoNo, sabados
from ..infra.tenancy import sessao_tenant
from . import schemas

router = APIRouter(prefix="/api/v1")


async def _carregar_eap(s: AsyncSession, contrato_id: UUID) -> tuple[list[No], Decimal]:
    base = (await s.execute(
        text("SELECT base_ponderacao FROM plan.contrato WHERE id = :id"), {"id": contrato_id}
    )).scalar_one_or_none()
    if base is None:
        # inexistente ou de outra organização: a RLS não distingue, e nem a API deve
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contrato não encontrado")
    linhas = (await s.execute(text(
        "SELECT edt, descricao, nivel, tipo, folha, pontos FROM plan.eap_no "
        "WHERE contrato_id = :id ORDER BY edt"), {"id": contrato_id})).all()
    nos = [No(r.edt, r.descricao, r.nivel, TipoNo(r.tipo), r.folha, r.pontos) for r in linhas]
    return nos, base


@router.post("/contratos/{contrato_id}/eap/validar", response_model=dict)
async def validar_eap(contrato_id: UUID, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> dict:
    """INV-EAP-001 checada também aqui, para dar mensagem antes do COMMIT."""
    nos, base = await _carregar_eap(s, contrato_id)
    try:
        soma = dom_pond.validar_ponderacao(nos, base)
    except ErroDominio as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return {"soma_pontos": soma, "base": base, "folhas": len(dom_pond.pesos(nos, base))}


@router.get("/contratos/{contrato_id}/avanco", response_model=list[schemas.AvancoSaida])
async def avanco_por_no(
    contrato_id: UUID, data_corte: date, s: AsyncSession = Depends(sessao_tenant, scope="function")
) -> list[schemas.AvancoSaida]:
    nos, base = await _carregar_eap(s, contrato_id)
    previsto = dict((r.edt, r.pct) for r in (await s.execute(text("""
        SELECT n.edt, COALESCE(SUM(p.parcela), 0) AS pct
          FROM plan.eap_no n
          JOIN plan.previsto_semanal p ON p.eap_no_id = n.id
          JOIN plan.baseline b ON b.id = p.baseline_id AND b.vigente
         WHERE n.contrato_id = :id AND p.fim_semana <= :dc
         GROUP BY n.edt"""), {"id": contrato_id, "dc": data_corte})).all())
    realizado = dict((r.edt, r.pct) for r in (await s.execute(text("""
        SELECT n.edt, m.percentual_ac AS pct
          FROM plan.eap_no n
          JOIN LATERAL (
                SELECT percentual_ac FROM plan.medicao
                 WHERE eap_no_id = n.id AND tipo = 'MEDICAO' AND estorna_id IS NULL
                   AND data_corte <= :dc
                 ORDER BY data_corte DESC LIMIT 1) m ON true
         WHERE n.contrato_id = :id"""), {"id": contrato_id, "dc": data_corte})).all())

    saida: list[schemas.AvancoSaida] = []
    pesos = dom_pond.pesos(nos, base)
    for n in nos:
        a = dom_avanco.avaliar(n.edt, nos, base, previsto, realizado)
        saida.append(schemas.AvancoSaida(
            edt=n.edt,
            peso=pesos.get(n.edt, Decimal("0")),
            previsto_ac=a.previsto_ac, real_ac=a.real_ac,
            desvio_abs=a.desvio_abs, desvio_rel=a.desvio_rel, situacao=a.situacao.value,
        ))
    return saida


@router.get("/contratos/{contrato_id}/curva-s", response_model=list[schemas.PontoCurvaSaida])
async def curva_s(
    contrato_id: UUID, inicio: date, fim: date, data_corte: date,
    s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> list[schemas.PontoCurvaSaida]:
    nos, base = await _carregar_eap(s, contrato_id)
    prev = {(r.edt, r.fim_semana): r.parcela for r in (await s.execute(text("""
        SELECT n.edt, p.fim_semana, p.parcela
          FROM plan.previsto_semanal p
          JOIN plan.eap_no n ON n.id = p.eap_no_id
          JOIN plan.baseline b ON b.id = p.baseline_id AND b.vigente
         WHERE n.contrato_id = :id AND p.fim_semana BETWEEN :i AND :f
         ORDER BY p.fim_semana"""), {"id": contrato_id, "i": inicio, "f": fim})).all()}
    real = {(r.edt, r.data_corte): r.parcela for r in (await s.execute(text("""
        SELECT n.edt, m.data_corte,
               m.percentual_ac - COALESCE(LAG(m.percentual_ac) OVER (
                   PARTITION BY m.eap_no_id ORDER BY m.data_corte), 0) AS parcela
          FROM plan.medicao m
          JOIN plan.eap_no n ON n.id = m.eap_no_id
         WHERE n.contrato_id = :id AND m.tipo = 'MEDICAO' AND m.estorna_id IS NULL
           AND m.data_corte BETWEEN :i AND :f
         ORDER BY m.data_corte"""), {"id": contrato_id, "i": inicio, "f": fim})).all()}
    pontos = dom_curva.curva(sabados(inicio, fim), nos, base, prev, real, data_corte)
    return [schemas.PontoCurvaSaida(**asdict(p)) for p in pontos]


@router.post("/medicoes", status_code=status.HTTP_201_CREATED)
async def lancar_medicao(
    entrada: schemas.MedicaoEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function")
) -> dict:
    """Medição por critério de etapas (RGR-MED-001) ou percentual direto com evidência."""
    if entrada.criterio:
        pct, parcelas = dom_etapas.avancar(entrada.criterio, set(entrada.etapas_concluidas))
        memoria = {"criterio": entrada.criterio,
                   "parcelas": [[n, str(p)] for n, p in parcelas],
                   "versao_regra": dom_etapas.VERSAO_REGRA}
    elif entrada.percentual_ac is not None:
        pct = entrada.percentual_ac
        memoria = {"origem": "percentual_direto", "evidencia": entrada.evidencia}
    else:
        raise HTTPException(422, "informe critério com etapas ou percentual_ac")

    row = (await s.execute(text("""
        INSERT INTO plan.medicao
            (contrato_id, eap_no_id, data_corte, percentual_ac,
             memoria_calculo, evidencia, criado_por)
        SELECT n.contrato_id, n.id, :dc, :pct, CAST(:mem AS jsonb), :ev, :por
          FROM plan.eap_no n WHERE n.id = :no
        RETURNING id, percentual_ac"""), {
        "dc": entrada.data_corte, "pct": pct, "mem": json.dumps(memoria, default=str),
        "ev": entrada.evidencia, "por": entrada.criado_por, "no": entrada.eap_no_id})).one()
    # commit é da sessao_tenant: fechar aqui encerraria o SET LOCAL antes da hora
    return {"id": row.id, "percentual_ac": row.percentual_ac}


@router.post("/lvp", response_model=schemas.LVPSaida)
async def avaliar_lvp(entrada: schemas.LVPEntrada) -> schemas.LVPSaida:
    mapa = {i.numero: dom_lvp.StatusItem(i.status) for i in entrada.itens}
    itens = [dom_lvp.ItemLVP(i.numero, i.descricao, i.peso, i.obrigatorio,
                             mapa.get(i.numero, dom_lvp.StatusItem.EM_ANALISE))
             for i in dom_lvp.ITENS_LINHA_DE_BASE]
    r = dom_lvp.avaliar(itens, dom_lvp.Especie(entrada.especie))
    return schemas.LVPSaida(nota=r.nota, aprovado=r.aprovado, reprovadores=list(r.reprovadores))
