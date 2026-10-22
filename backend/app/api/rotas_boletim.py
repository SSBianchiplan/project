"""Boletim de medição (BM): consolida a medição do período por item da EAP até N níveis.

RGR-BM-001  período quinzenal (1–15 / 16–fim) ou mensal, contado a partir do início do contrato;
            avanço acumulado do item na data = Σ(pontos × % medido vigente até a data) ÷ Σ pontos;
            anterior = acumulado no dia anterior ao início do período; atual = acumulado − anterior;
            valor a medir do item = peso × valor total; peso = Σ pontos das folhas ÷ base do contrato.
INV-BM-001  numeração sequencial por contrato; INV-BM-002 snapshot append-only (o BM emitido não muda).
"""
from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..infra import cronograma_repo as repo
from ..infra import exportar
from ..infra.auth import Contexto
from ..infra.tenancy import contexto_autenticado, sessao_tenant
from .rotas_custos import montar_painel_custos

router = APIRouter(prefix="/api/v1", tags=["boletim"])
ZERO = Decimal(0)
Q2 = Decimal("0.01")
Q4 = Decimal("0.0001")


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LinhaBM(Base):
    edt: str
    descricao: str
    nivel: int
    folha: bool
    peso: Decimal
    valor: Decimal
    anterior_pct: Decimal
    anterior_valor: Decimal
    atual_pct: Decimal
    atual_valor: Decimal
    acumulado_pct: Decimal
    acumulado_valor: Decimal
    saldo_pct: Decimal
    saldo_valor: Decimal


class PreviaBM(Base):
    numero: int
    tipo: str
    periodicidade: str
    niveis: int
    periodo_inicio: date
    periodo_fim: date
    valor_total: Decimal
    origem_valor: str
    linhas: list[LinhaBM]
    total: LinhaBM
    periodos: list[dict]           # períodos disponíveis para escolher


class EmissaoBM(Base):
    periodicidade: str = Field(pattern="^(QUINZENAL|MENSAL)$")
    niveis: int = Field(default=3, ge=1, le=5)
    periodo_fim: date
    valor_total: Decimal | None = None
    tipo: str = Field(default="INTERMEDIARIO", pattern="^(INICIAL|INTERMEDIARIO|FINAL)$")
    condicoes: str = ""


class BMResumo(Base):
    id: UUID
    numero: int
    tipo: str
    periodicidade: str
    niveis: int
    periodo_inicio: date
    periodo_fim: date
    valor_total: Decimal
    atual_valor: Decimal
    acumulado_pct: Decimal
    emitido_por: str
    emitido_em: str


# ------------------------------------------------------------------ períodos
def _periodos(inicio: date, ate: date, periodicidade: str) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    if periodicidade == "MENSAL":
        a = date(inicio.year, inicio.month, 1)
        while a <= ate:
            f = date(a.year, a.month, calendar.monthrange(a.year, a.month)[1])
            out.append((max(a, inicio), f))
            a = date(a.year + a.month // 12, a.month % 12 + 1, 1)
    else:
        a = date(inicio.year, inicio.month, 1 if inicio.day <= 15 else 16)
        while a <= ate:
            f = date(a.year, a.month, 15) if a.day == 1 else date(a.year, a.month, calendar.monthrange(a.year, a.month)[1])
            out.append((max(a, inicio), f))
            a = date(a.year, a.month, 16) if a.day == 1 else date(a.year + a.month // 12, a.month % 12 + 1, 1)
    return out


async def _progresso_ate(s: AsyncSession, contrato_id: UUID, dia: date) -> dict[UUID, Decimal]:
    rows = (await s.execute(text("""
        SELECT DISTINCT ON (eap_no_id) eap_no_id, percentual_ac FROM plan.medicao
         WHERE contrato_id = :c AND data_corte <= :d ORDER BY eap_no_id, data_corte DESC, criado_em DESC"""), {"c": contrato_id, "d": dia})).all()
    return {r.eap_no_id: Decimal(r.percentual_ac) for r in rows}


async def montar_previa(s: AsyncSession, contrato_id: UUID, periodicidade: str, niveis: int, periodo_fim: date | None, valor_total: Decimal | None, tipo: str | None = None) -> PreviaBM:
    c = await repo.contrato(s, contrato_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contrato não encontrado")
    hoje = date.today()
    per = _periodos(c.data_inicio, max(hoje, c.termino_contratual), periodicidade)
    if not per:
        raise HTTPException(422, "contrato sem período")
    escolhido = next((p for p in per if p[1] == periodo_fim), None) if periodo_fim else None
    if escolhido is None:
        escolhido = next((p for p in reversed(per) if p[1] <= hoje), per[0])
    p_ini, p_fim = escolhido
    origem = "informado"
    if valor_total is None:
        painel = await montar_painel_custos(s, contrato_id, p_fim)
        if painel.evm.bac > 0:
            valor_total, origem = Decimal(painel.evm.bac), "orçamento (ONT) do módulo Custos"
        else:
            valor_total, origem = Decimal(c.base_ponderacao), "base de ponderação (sem orçamento cadastrado)"
    nos = (await s.execute(text("SELECT id, edt, descricao, nivel, folha, pontos FROM plan.eap_no WHERE contrato_id = :c ORDER BY string_to_array(edt, '.')::int[]"), {"c": contrato_id})).mappings().all()
    prog_ant = await _progresso_ate(s, contrato_id, p_ini - timedelta(days=1))
    prog_ac = await _progresso_ate(s, contrato_id, p_fim)
    folhas = [n for n in nos if n["folha"]]
    base = sum((Decimal(n["pontos"]) for n in folhas), start=ZERO) or Decimal(c.base_ponderacao)
    linhas: list[LinhaBM] = []

    def agreg(prefixo: str, folha: bool, no_id: UUID) -> tuple[Decimal, Decimal, Decimal]:
        sel = [n for n in folhas if n["edt"] == prefixo] if folha else [n for n in folhas if n["edt"].startswith(prefixo + ".")]
        pts = sum((Decimal(n["pontos"]) for n in sel), start=ZERO)
        if pts == 0:
            return ZERO, ZERO, ZERO
        ant = sum((Decimal(n["pontos"]) * prog_ant.get(n["id"], ZERO) for n in sel), start=ZERO) / pts
        ac = sum((Decimal(n["pontos"]) * prog_ac.get(n["id"], ZERO) for n in sel), start=ZERO) / pts
        return pts, ant, ac

    for n in nos:
        if n["nivel"] > niveis:
            continue
        pts, ant, ac = agreg(n["edt"], bool(n["folha"]), n["id"])
        peso = (pts / base) if base else ZERO
        valor = (peso * valor_total).quantize(Q2)
        atual = ac - ant
        linhas.append(LinhaBM(edt=n["edt"], descricao=n["descricao"], nivel=int(n["nivel"]), folha=bool(n["folha"]), peso=peso.quantize(Q4), valor=valor,
                              anterior_pct=ant.quantize(Q4), anterior_valor=(valor * ant).quantize(Q2), atual_pct=atual.quantize(Q4), atual_valor=(valor * atual).quantize(Q2),
                              acumulado_pct=ac.quantize(Q4), acumulado_valor=(valor * ac).quantize(Q2), saldo_pct=(1 - ac).quantize(Q4), saldo_valor=(valor * (1 - ac)).quantize(Q2)))
    raiz = next((x for x in linhas if x.nivel == 1), None)
    total = LinhaBM(edt="", descricao="TOTAL MEDIÇÃO", nivel=0, folha=False, peso=Decimal(1), valor=valor_total.quantize(Q2),
                    anterior_pct=raiz.anterior_pct if raiz else ZERO, anterior_valor=raiz.anterior_valor if raiz else ZERO, atual_pct=raiz.atual_pct if raiz else ZERO,
                    atual_valor=raiz.atual_valor if raiz else ZERO, acumulado_pct=raiz.acumulado_pct if raiz else ZERO, acumulado_valor=raiz.acumulado_valor if raiz else ZERO,
                    saldo_pct=raiz.saldo_pct if raiz else Decimal(1), saldo_valor=raiz.saldo_valor if raiz else valor_total)
    numero = (await s.execute(text("SELECT coalesce(max(numero), 0) + 1 FROM plan.boletim_medicao WHERE contrato_id = :c"), {"c": contrato_id})).scalar_one()
    tipo_calc = tipo or ("INICIAL" if numero == 1 else "FINAL" if (raiz and raiz.acumulado_pct >= 1) else "INTERMEDIARIO")
    return PreviaBM(numero=numero, tipo=tipo_calc, periodicidade=periodicidade, niveis=niveis, periodo_inicio=p_ini, periodo_fim=p_fim, valor_total=valor_total.quantize(Q2),
                    origem_valor=origem, linhas=linhas, total=total,
                    periodos=[{"inicio": a.isoformat(), "fim": f.isoformat(), "rotulo": f"{a:%d/%m/%Y} a {f:%d/%m/%Y}", "fechado": f <= hoje} for a, f in per])


# ------------------------------------------------------------------ rotas
@router.get("/contratos/{contrato_id}/boletins/previa", response_model=PreviaBM)
async def previa(contrato_id: UUID, periodicidade: str = "MENSAL", niveis: int = 3, periodo_fim: date | None = None, valor_total: Decimal | None = None,
                 s: AsyncSession = Depends(sessao_tenant, scope="function")) -> PreviaBM:
    if periodicidade not in ("QUINZENAL", "MENSAL") or not 1 <= niveis <= 5:
        raise HTTPException(422, "periodicidade QUINZENAL|MENSAL e níveis 1..5")
    return await montar_previa(s, contrato_id, periodicidade, niveis, periodo_fim, valor_total)


@router.get("/contratos/{contrato_id}/boletins", response_model=list[BMResumo])
async def listar(contrato_id: UUID, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> list[BMResumo]:
    rows = (await s.execute(text("SELECT * FROM plan.boletim_medicao WHERE contrato_id = :c ORDER BY numero DESC"), {"c": contrato_id})).mappings().all()
    out = []
    for r in rows:
        t = r["snapshot"].get("total", {})
        out.append(BMResumo(id=r["id"], numero=r["numero"], tipo=r["tipo"], periodicidade=r["periodicidade"], niveis=r["niveis"], periodo_inicio=r["periodo_inicio"], periodo_fim=r["periodo_fim"],
                            valor_total=r["valor_total"], atual_valor=Decimal(str(t.get("atual_valor", 0))), acumulado_pct=Decimal(str(t.get("acumulado_pct", 0))), emitido_por=r["emitido_por"], emitido_em=r["emitido_em"].isoformat()))
    return out


@router.post("/contratos/{contrato_id}/boletins", response_model=BMResumo, status_code=status.HTTP_201_CREATED)
async def emitir(contrato_id: UUID, e: EmissaoBM, ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> BMResumo:
    p = await montar_previa(s, contrato_id, e.periodicidade, e.niveis, e.periodo_fim, e.valor_total, e.tipo)
    if p.periodo_fim != e.periodo_fim:
        raise HTTPException(422, "período não corresponde à periodicidade escolhida")
    dup = (await s.execute(text("SELECT numero FROM plan.boletim_medicao WHERE contrato_id = :c AND periodo_fim = :f AND periodicidade = :p"), {"c": contrato_id, "f": p.periodo_fim, "p": p.periodicidade})).first()
    if dup:
        raise HTTPException(409, f"já existe o BM nº {dup.numero} para este período")
    snap = p.model_dump(mode="json")
    snap.pop("periodos", None)
    row = (await s.execute(text("""
        INSERT INTO plan.boletim_medicao (contrato_id, numero, tipo, periodicidade, niveis, periodo_inicio, periodo_fim, valor_total, condicoes, snapshot, emitido_por)
        VALUES (:c, :n, :t, :p, :nv, :pi, :pf, :vt, :cond, CAST(:snap AS jsonb), :por) RETURNING id, emitido_em"""), {
        "c": contrato_id, "n": p.numero, "t": p.tipo, "p": p.periodicidade, "nv": p.niveis, "pi": p.periodo_inicio, "pf": p.periodo_fim, "vt": p.valor_total, "cond": e.condicoes,
        "snap": json.dumps(snap, default=str), "por": ctx.nome})).one()
    return BMResumo(id=row.id, numero=p.numero, tipo=p.tipo, periodicidade=p.periodicidade, niveis=p.niveis, periodo_inicio=p.periodo_inicio, periodo_fim=p.periodo_fim, valor_total=p.valor_total,
                    atual_valor=p.total.atual_valor, acumulado_pct=p.total.acumulado_pct, emitido_por=ctx.nome, emitido_em=row.emitido_em.isoformat())


@router.get("/boletins/{bm_id}")
async def detalhe(bm_id: UUID, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> dict:
    r = (await s.execute(text("SELECT * FROM plan.boletim_medicao WHERE id = :id"), {"id": bm_id})).mappings().first()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BM não encontrado")
    d = dict(r)
    d["emitido_em"] = d["emitido_em"].isoformat()
    return json.loads(json.dumps(d, default=str))


@router.get("/boletins/{bm_id}/pdf")
async def pdf_bm(bm_id: UUID, ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> Response:
    r = (await s.execute(text("SELECT * FROM plan.boletim_medicao WHERE id = :id"), {"id": bm_id})).mappings().first()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "BM não encontrado")
    c = await repo.contrato(s, r["contrato_id"])
    assert c is not None
    snap = r["snapshot"]
    d = Decimal
    linhas = [[x["edt"], ("    " * (x["nivel"] - 1)) + x["descricao"], exportar.pct(d(x["peso"]), 2), d(x["valor"]), exportar.pct(d(x["anterior_pct"]), 2), d(x["anterior_valor"]),
               exportar.pct(d(x["atual_pct"]), 2), d(x["atual_valor"]), exportar.pct(d(x["acumulado_pct"]), 2), d(x["acumulado_valor"]), exportar.pct(d(x["saldo_pct"]), 2), d(x["saldo_valor"])] for x in snap["linhas"]]
    t = snap["total"]
    linhas.append(["", "TOTAL MEDIÇÃO", "100,00%", d(t["valor"]), exportar.pct(d(t["anterior_pct"]), 2), d(t["anterior_valor"]), exportar.pct(d(t["atual_pct"]), 2), d(t["atual_valor"]),
                   exportar.pct(d(t["acumulado_pct"]), 2), d(t["acumulado_valor"]), exportar.pct(d(t["saldo_pct"]), 2), d(t["saldo_valor"])])
    tipo = {"INICIAL": "Inicial", "INTERMEDIARIO": "Intermediário", "FINAL": "Final"}[r["tipo"]]
    cab = exportar.Cabecalho(titulo=f"Boletim de Medição BM-{r['numero']:03d} · {tipo}", contrato_codigo=c.codigo, empreendimento=c.empreendimento, contratante=c.contratante, contratada=c.contratada,
                             data_corte=r["periodo_fim"], emitido_por=ctx.nome, baseline="", paisagem=True)
    secoes = [
        exportar.Secao("Identificação", cartoes=[("Boletim nº", str(r["numero"])), ("Tipo", tipo), ("Período de referência", f"{r['periodo_inicio']:%d/%m/%Y} a {r['periodo_fim']:%d/%m/%Y}"),
                                                 ("Periodicidade", r["periodicidade"].lower()), ("Valor total do contrato", exportar.moeda(d(r["valor_total"]))), ("Total a receber neste BM", exportar.moeda(d(t["atual_valor"])))]),
        exportar.Secao(f"Medição por item da EAP (até {r['niveis']} níveis)", cabecalhos=["Item EAP", "Descrição dos itens", "Peso", "Valor a medir", "Anterior %", "Anterior R$", "Atual %", "Atual R$", "Acumulado %", "Acumulado R$", "Saldo %", "Saldo R$"],
                       linhas=linhas, larguras=[0.7, 3.2, 0.7, 1.2, 0.7, 1.1, 0.7, 1.1, 0.8, 1.2, 0.7, 1.2],
                       nota="Avanço por item = Σ(pontos × % medido vigente) ÷ Σ pontos das folhas; valor = peso × valor total do contrato (RGR-BM-001). Medições são o ledger append-only com hash encadeado."),
    ]
    if r["condicoes"]:
        secoes.append(exportar.Secao("Condições comerciais", nota=r["condicoes"]))
    secoes.append(exportar.Secao("Assinaturas", cabecalhos=["Contratada", "Fiscalização de campo", "Gerenciadora", "Contratante"], linhas=[["", "", "", ""], ["Nome:", "Nome:", "Nome:", "Nome:"], ["Assinatura:", "Assinatura:", "Assinatura:", "Assinatura:"]], larguras=[1, 1, 1, 1],
                                  nota=f"Emitido por {r['emitido_por']} em {r['emitido_em']:%d/%m/%Y %H:%M}."))
    corpo = exportar.pdf(exportar.Relatorio(cab, secoes))
    return Response(corpo, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="BM_{r["numero"]:03d}_{c.codigo}.pdf"'})
