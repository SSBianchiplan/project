"""Custos do projeto: orçamento por atividade, ledger de realizado, curva e EVM."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dominio import custos as cs
from ..dominio.tipos import ZERO, sabados
from ..infra.auth import Contexto
from ..infra.tenancy import contexto_autenticado, sessao_tenant
from . import servico_cronograma as svc

router = APIRouter(prefix="/api/v1")

CATEGORIAS = "^(MATERIAL|EQUIPAMENTO|MAQUINA|SERVICO|SUBCONTRATO|MAO_DE_OBRA|LOGISTICA|OUTRO)$"


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemEntrada(Base):
    categoria: str = Field(pattern=CATEGORIAS)
    descricao: str = Field(min_length=1)
    fornecedor: str = ""
    quantidade: Decimal = Field(default=Decimal(1), gt=0)
    unidade: str = "un"
    custo_unitario: Decimal = Field(ge=0)
    observacao: str = ""


class ItensAtividade(Base):
    itens: list[ItemEntrada]


class ItemSaida(ItemEntrada):
    id: UUID
    atividade_id: UUID
    edt: str
    total: Decimal


class RealizadoEntrada(Base):
    atividade_id: UUID
    categoria: str = Field(pattern=CATEGORIAS)
    data: date
    valor: Decimal
    descricao: str = Field(min_length=1)
    documento: str = ""


class RealizadoSaida(Base):
    id: UUID
    atividade_id: UUID
    edt: str
    categoria: str
    data: date
    valor: Decimal
    descricao: str
    documento: str
    criado_por: str


class LinhaAtividade(Base):
    atividade_id: UUID
    edt: str
    descricao: str
    fase_edt: str
    itens: Decimal
    recursos: Decimal
    bac: Decimal
    progresso: Decimal
    va: Decimal
    cr: Decimal
    inicio: date
    termino: date


class LinhaNo(Base):
    edt: str
    descricao: str
    nivel: int
    bac: Decimal
    va: Decimal
    cr: Decimal
    idc: Decimal | None


class LinhaCategoria(Base):
    categoria: str
    planejado: Decimal
    realizado: Decimal


class PontoCustoSaida(Base):
    fim_semana: date
    vp_ac: Decimal
    va_ac: Decimal
    cr_ac: Decimal | None


class EvmSaida(Base):
    bac: Decimal
    vp: Decimal
    va: Decimal
    cr: Decimal
    vc: Decimal
    vpr: Decimal
    idc: Decimal | None
    idp_custo: Decimal | None
    ent: Decimal | None
    etc: Decimal | None
    vac: Decimal | None


class PainelCustos(Base):
    data_corte: date
    evm: EvmSaida
    curva: list[PontoCustoSaida]
    atividades: list[LinhaAtividade]
    nos: list[LinhaNo]
    categorias: list[LinhaCategoria]
    itens: list[ItemSaida]
    realizados: list[RealizadoSaida]
    versao_regra: str


async def _itens(s: AsyncSession, contrato_id: UUID) -> list[dict]:
    rows = (await s.execute(text("""
        SELECT i.*, n.edt FROM plan.custo_item i
          JOIN plan.atividade a ON a.id = i.atividade_id JOIN plan.eap_no n ON n.id = a.eap_no_id
         WHERE a.contrato_id = :c ORDER BY string_to_array(n.edt, '.')::int[], i.categoria, i.criado_em"""), {"c": contrato_id})).mappings().all()
    return [dict(r) for r in rows]


async def _realizados(s: AsyncSession, contrato_id: UUID) -> list[dict]:
    rows = (await s.execute(text("""
        SELECT r.*, n.edt FROM plan.custo_realizado r
          JOIN plan.atividade a ON a.id = r.atividade_id JOIN plan.eap_no n ON n.id = a.eap_no_id
         WHERE r.contrato_id = :c ORDER BY r.data DESC, r.criado_em DESC"""), {"c": contrato_id})).mappings().all()
    return [dict(r) for r in rows]


async def _custo_recursos(s: AsyncSession, contrato_id: UUID, duracao: dict[str, int]) -> dict[str, Decimal]:
    """Custo dos recursos alocados por atividade: qtd/dia × dias úteis × horas/dia × custo/hora."""
    rows = (await s.execute(text("""
        SELECT n.edt, al.quantidade_dia, r.horas_dia, r.custo_hora
          FROM plan.alocacao al JOIN plan.atividade a ON a.id = al.atividade_id
          JOIN plan.eap_no n ON n.id = a.eap_no_id JOIN plan.recurso r ON r.id = al.recurso_id
         WHERE a.contrato_id = :c AND r.ativo"""), {"c": contrato_id})).all()
    out: dict[str, Decimal] = {}
    for r in rows:
        dias = duracao.get(r.edt, 0)
        out[r.edt] = out.get(r.edt, ZERO) + Decimal(r.quantidade_dia) * dias * Decimal(r.horas_dia) * Decimal(r.custo_hora)
    return out


def _item_saida(i: dict) -> ItemSaida:
    return ItemSaida(id=i["id"], atividade_id=i["atividade_id"], edt=i["edt"], categoria=i["categoria"], descricao=i["descricao"],
                     fornecedor=i["fornecedor"], quantidade=i["quantidade"], unidade=i["unidade"], custo_unitario=i["custo_unitario"],
                     observacao=i["observacao"], total=i["total"])


@router.get("/contratos/{contrato_id}/custos", response_model=PainelCustos)
async def painel(contrato_id: UUID, data_corte: date | None = None, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> PainelCustos:
    return await montar_painel_custos(s, contrato_id, data_corte)


async def montar_painel_custos(s: AsyncSession, contrato_id: UUID, data_corte: date | None) -> PainelCustos:
    plano = await svc.carregar(s, contrato_id)
    cpm, _ = svc.calcular(plano)
    corte = data_corte or date.today()
    itens = await _itens(s, contrato_id)
    reais = await _realizados(s, contrato_id)
    duracao = {edt: d.te - d.ie + 1 for edt, d in cpm.atividades.items()}
    rec = await _custo_recursos(s, contrato_id, {e: (0 if plano.atividades[e]["tipo"] == "MARCO" else n) for e, n in duracao.items()})

    custos: dict[str, cs.CustoAtividade] = {}
    for edt in plano.atividades:
        por_cat: dict[str, Decimal] = {}
        for i in itens:
            if i["edt"] == edt:
                por_cat[i["categoria"]] = por_cat.get(i["categoria"], ZERO) + Decimal(i["total"])
        custos[edt] = cs.CustoAtividade(edt, sum(por_cat.values(), start=ZERO), rec.get(edt, ZERO), Decimal(plano.progresso.get(edt, ZERO)), por_cat)
    lanc = [cs.Lancamento(r["edt"], r["data"], Decimal(r["valor"]), r["categoria"]) for r in reais]
    fim = max(plano.contrato.termino_contratual, plano.cal.data_de(cpm.fim)) + timedelta(days=7)
    semanas = sabados(plano.contrato.data_inicio, fim)
    pontos = cs.curva(custos, cpm, plano.cal, semanas, lanc, corte)
    e = cs.evm(custos, pontos, lanc, corte)

    linhas = []
    for edt, c in custos.items():
        a = plano.atividades[edt]
        d = cpm.atividades[edt]
        cr_a = sum((lc.valor for lc in lanc if lc.edt == edt and lc.data <= corte), start=ZERO)
        linhas.append(LinhaAtividade(
            atividade_id=a["id"], edt=edt, descricao=a["descricao"], fase_edt=".".join(edt.split(".")[:2]),
            itens=cs.r2(c.itens), recursos=cs.r2(c.recursos), bac=cs.r2(c.bac), progresso=c.progresso,
            va=cs.r2(c.bac * c.progresso), cr=cs.r2(cr_a), inicio=plano.cal.data_de(d.ie), termino=plano.cal.data_de(d.te)))
    nos = []
    for n in plano.nos:
        if n["folha"]:
            continue
        bac, va, cr = cs.por_no(n["edt"], custos, [lc for lc in lanc if lc.data <= corte])
        nos.append(LinhaNo(edt=n["edt"], descricao=n["descricao"], nivel=int(n["nivel"]), bac=bac, va=va, cr=cr,
                           idc=(va / cr).quantize(Decimal("0.01")) if cr > ZERO else None))
    cats = [LinhaCategoria(categoria=k, planejado=v[0], realizado=v[1]) for k, v in cs.por_categoria(custos, [lc for lc in lanc if lc.data <= corte]).items()]
    return PainelCustos(
        data_corte=corte, evm=EvmSaida(**asdict(e)), curva=[PontoCustoSaida(**asdict(p)) for p in pontos],
        atividades=linhas, nos=nos, categorias=cats, itens=[_item_saida(i) for i in itens],
        realizados=[RealizadoSaida(id=r["id"], atividade_id=r["atividade_id"], edt=r["edt"], categoria=r["categoria"], data=r["data"],
                                   valor=r["valor"], descricao=r["descricao"], documento=r["documento"], criado_por=r["criado_por"]) for r in reais],
        versao_regra=cs.VERSAO_REGRA)


@router.put("/atividades/{atividade_id}/custos", response_model=list[ItemSaida])
async def gravar_itens(atividade_id: UUID, e: ItensAtividade, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> list[ItemSaida]:
    """Substitui o orçamento direto da atividade. Marco não tem custo (INV-CST-001)."""
    a = (await s.execute(text("SELECT contrato_id, tipo FROM plan.atividade WHERE id = :id"), {"id": atividade_id})).mappings().one_or_none()
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "atividade não encontrada")
    if a["tipo"] == "MARCO" and e.itens:
        raise HTTPException(422, "INV-CST-001: marco não carrega custo; aloque na atividade que executa o trabalho")
    await s.execute(text("DELETE FROM plan.custo_item WHERE atividade_id = :id"), {"id": atividade_id})
    for i in e.itens:
        await s.execute(text("""
            INSERT INTO plan.custo_item (atividade_id, categoria, descricao, fornecedor, quantidade, unidade, custo_unitario, observacao)
            VALUES (:a, :cat, :d, :f, :q, :u, :cu, :o)"""), {
            "a": atividade_id, "cat": i.categoria, "d": i.descricao, "f": i.fornecedor, "q": i.quantidade,
            "u": i.unidade, "cu": i.custo_unitario, "o": i.observacao})
    itens = await _itens(s, a["contrato_id"])
    return [_item_saida(i) for i in itens if i["atividade_id"] == atividade_id]


@router.post("/contratos/{contrato_id}/custos-realizados", response_model=RealizadoSaida, status_code=status.HTTP_201_CREATED)
async def lancar_realizado(
    contrato_id: UUID, e: RealizadoEntrada, ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> RealizadoSaida:
    """Ledger: valor negativo corrige; nada é editado nem apagado (INV-CST-002)."""
    if e.valor == 0:
        raise HTTPException(422, "valor zero não é lançamento")
    row = (await s.execute(text("""
        INSERT INTO plan.custo_realizado (contrato_id, atividade_id, categoria, data, valor, descricao, documento, criado_por)
        VALUES (:c, :a, :cat, :d, :v, :desc, :doc, :por)
        RETURNING id"""), {"c": contrato_id, "a": e.atividade_id, "cat": e.categoria, "d": e.data, "v": e.valor,
                           "desc": e.descricao, "doc": e.documento, "por": ctx.nome})).one()
    r = next(x for x in await _realizados(s, contrato_id) if x["id"] == row.id)
    return RealizadoSaida(id=r["id"], atividade_id=r["atividade_id"], edt=r["edt"], categoria=r["categoria"], data=r["data"],
                          valor=r["valor"], descricao=r["descricao"], documento=r["documento"], criado_por=r["criado_por"])
