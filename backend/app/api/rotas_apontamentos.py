"""Apontamento de horas e painel Previsto × Realizado com análise de coerência."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dominio import apontamentos as ap
from ..dominio import coerencia as co
from ..dominio.tipos import ZERO, q, sabados
from ..infra import cronograma_repo as repo
from ..infra.auth import Contexto
from ..infra.tenancy import contexto_autenticado, sessao_tenant
from . import servico_cronograma as svc
from .rotas_custos import montar_painel_custos

router = APIRouter(prefix="/api/v1")


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApontamentoEntrada(Base):
    atividade_id: UUID
    data: date
    horas: Decimal = Field(gt=0, le=24)
    recurso_id: UUID | None = None
    descricao: str = ""


class EstornoApontamento(Base):
    justificativa: str = Field(min_length=5)


class ApontamentoSaida(Base):
    id: UUID
    atividade_id: UUID
    edt: str
    descricao_atividade: str
    recurso_id: UUID | None
    recurso_codigo: str | None
    data: date
    horas: Decimal
    descricao: str
    estorna_id: UUID | None
    criado_por: str
    criado_em: str


class ResumoAtividadeSaida(Base):
    atividade_id: UUID
    edt: str
    descricao: str
    marco: bool
    hh_previstas: Decimal
    hh_apontadas: Decimal
    progresso: Decimal
    fracao_horas: Decimal | None
    produtividade: Decimal | None
    hh_projetadas: Decimal | None
    desvio_hh_projetado: Decimal | None
    primeiro_dia: date | None
    ultimo_dia: date | None
    situacao: str
    inicio: date
    termino: date


class LinhaBurndown(Base):
    recurso_id: str | None
    codigo: str
    nome: str
    hh_previstas: Decimal
    horas: dict[str, Decimal]          # data iso -> horas apontadas
    total: Decimal


class BurndownSaida(Base):
    edt: str
    descricao: str
    inicio: date
    termino: date
    dias: list[date]                   # do início da tarefa até max(término, hoje, último apontamento)
    hh_previstas: Decimal
    hh_apontadas: Decimal
    saldo: Decimal
    previsto_restante: list[Decimal]   # queima linear por dia útil da tarefa
    realizado_ac: list[Decimal]
    burndown: list[Decimal]            # previstas − realizado acumulado
    linhas: list[LinhaBurndown]
    total_dia: dict[str, Decimal]
    progresso: Decimal


class PainelApontamentos(Base):
    semana: date                       # sábado da semana exibida
    dias: list[date]
    lancamentos: list[ApontamentoSaida]
    grade: dict[str, dict[str, Decimal]]   # edt -> data iso -> horas (semana)
    resumo: list[ResumoAtividadeSaida]
    horas_semana: dict[str, Decimal]       # sábado iso -> horas (histórico)
    horas_por_recurso: dict[str, Decimal]
    total_previsto: Decimal
    total_apontado: Decimal
    versao_regra: str


async def _lancamentos(s: AsyncSession, contrato_id: UUID, limite: int = 2000) -> list[dict]:
    rows = (await s.execute(text("""
        SELECT ap.*, n.edt, n.descricao AS descricao_atividade, r.codigo AS recurso_codigo
          FROM plan.apontamento ap
          JOIN plan.atividade a ON a.id = ap.atividade_id JOIN plan.eap_no n ON n.id = a.eap_no_id
          LEFT JOIN plan.recurso r ON r.id = ap.recurso_id
         WHERE ap.contrato_id = :c ORDER BY ap.data DESC, ap.criado_em DESC LIMIT :lim"""), {"c": contrato_id, "lim": limite})).mappings().all()
    return [dict(r) for r in rows]


async def _um(s: AsyncSession, apontamento_id: UUID) -> dict:
    row = (await s.execute(text("""
        SELECT ap.*, n.edt, n.descricao AS descricao_atividade, r.codigo AS recurso_codigo
          FROM plan.apontamento ap
          JOIN plan.atividade a ON a.id = ap.atividade_id JOIN plan.eap_no n ON n.id = a.eap_no_id
          LEFT JOIN plan.recurso r ON r.id = ap.recurso_id
         WHERE ap.id = :id"""), {"id": apontamento_id})).mappings().one()
    return dict(row)


async def _hh_previstas(s: AsyncSession, contrato_id: UUID, dias_uteis: dict[str, int]) -> dict[str, Decimal]:
    rows = (await s.execute(text("""
        SELECT n.edt, al.quantidade_dia, r.horas_dia
          FROM plan.alocacao al JOIN plan.atividade a ON a.id = al.atividade_id
          JOIN plan.eap_no n ON n.id = a.eap_no_id JOIN plan.recurso r ON r.id = al.recurso_id
         WHERE a.contrato_id = :c AND r.ativo"""), {"c": contrato_id})).all()
    out: dict[str, Decimal] = {}
    for r in rows:
        out[r.edt] = out.get(r.edt, ZERO) + Decimal(r.quantidade_dia) * Decimal(r.horas_dia) * dias_uteis.get(r.edt, 0)
    # atividade sem alocação: usa hh_estimadas declaradas na gaveta
    est = (await s.execute(text("""
        SELECT n.edt, a.hh_estimadas FROM plan.atividade a JOIN plan.eap_no n ON n.id = a.eap_no_id
         WHERE a.contrato_id = :c AND a.hh_estimadas > 0"""), {"c": contrato_id})).all()
    for r in est:
        out.setdefault(r.edt, Decimal(r.hh_estimadas))
    return out


def _saida(r: dict) -> ApontamentoSaida:
    return ApontamentoSaida(id=r["id"], atividade_id=r["atividade_id"], edt=r["edt"], descricao_atividade=r["descricao_atividade"],
                            recurso_id=r["recurso_id"], recurso_codigo=r["recurso_codigo"], data=r["data"], horas=r["horas"],
                            descricao=r["descricao"], estorna_id=r["estorna_id"], criado_por=r["criado_por"], criado_em=r["criado_em"].isoformat())


async def montar_painel_apontamentos(s: AsyncSession, contrato_id: UUID, semana: date | None) -> PainelApontamentos:
    plano = await svc.carregar(s, contrato_id)
    cpm, _ = svc.calcular(plano)
    sab = semana or date.today()
    sab = sab + timedelta(days=(5 - sab.weekday()) % 7)
    dias = [sab - timedelta(days=5 - i) for i in range(7)]      # dom..sáb
    lanc = await _lancamentos(s, contrato_id)
    dom = [ap.Apontamento(r["edt"], r["data"], Decimal(r["horas"]), str(r["recurso_id"]) if r["recurso_id"] else None, r["criado_por"]) for r in lanc]
    dias_uteis = {e: (0 if plano.atividades[e]["tipo"] == "MARCO" else d.te - d.ie + 1) for e, d in cpm.atividades.items()}
    prev = await _hh_previstas(s, contrato_id, dias_uteis)
    grade: dict[str, dict[str, Decimal]] = {}
    for r in lanc:
        if dias[0] <= r["data"] <= dias[-1]:
            grade.setdefault(r["edt"], {})
            grade[r["edt"]][r["data"].isoformat()] = grade[r["edt"]].get(r["data"].isoformat(), ZERO) + Decimal(r["horas"])
    resumo = []
    for edt, a in plano.atividades.items():
        rs = ap.resumir(edt, prev.get(edt, ZERO), Decimal(plano.progresso.get(edt, ZERO)), dom)
        d = cpm.atividades[edt]
        resumo.append(ResumoAtividadeSaida(atividade_id=a["id"], edt=edt, descricao=a["descricao"], marco=a["tipo"] == "MARCO",
                                           **{k: getattr(rs, k) for k in ("hh_previstas", "hh_apontadas", "progresso", "fracao_horas", "produtividade", "hh_projetadas", "desvio_hh_projetado", "primeiro_dia", "ultimo_dia", "situacao")},
                                           inicio=plano.cal.data_de(d.ie), termino=plano.cal.data_de(d.te)))
    fim = max(plano.contrato.termino_contratual, plano.cal.data_de(cpm.fim))
    semanas = sabados(plano.contrato.data_inicio, fim)
    rec_nomes = {str(r["id"]): r["codigo"] for r in (await s.execute(text("SELECT id, codigo FROM plan.recurso WHERE contrato_id = :c"), {"c": contrato_id})).mappings().all()}
    return PainelApontamentos(
        semana=sab, dias=dias, lancamentos=[_saida(r) for r in lanc[:300]], grade=grade, resumo=resumo,
        horas_semana={k.isoformat(): v for k, v in ap.horas_por_semana(dom, semanas).items()},
        horas_por_recurso={rec_nomes.get(k, k): v for k, v in ap.horas_por_recurso(dom).items()},
        total_previsto=q(sum(prev.values(), start=ZERO)), total_apontado=q(sum((a.horas for a in dom), start=ZERO)),
        versao_regra=ap.VERSAO_REGRA)


@router.post("/contratos/{contrato_id}/apontamentos", response_model=ApontamentoSaida, status_code=status.HTTP_201_CREATED)
async def apontar(contrato_id: UUID, e: ApontamentoEntrada, ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> ApontamentoSaida:
    if e.data > date.today() + timedelta(days=1):
        raise HTTPException(422, "INV-APT-004: não se aponta hora no futuro")
    row = (await s.execute(text("""
        INSERT INTO plan.apontamento (contrato_id, atividade_id, recurso_id, usuario_id, data, horas, descricao, criado_por)
        VALUES (:c, :a, :r, :u, :d, :h, :desc, :por) RETURNING id"""), {
        "c": contrato_id, "a": e.atividade_id, "r": e.recurso_id, "u": ctx.usuario_id, "d": e.data, "h": e.horas,
        "desc": e.descricao, "por": ctx.nome})).one()
    r = await _um(s, row.id)
    return _saida(r)


@router.post("/apontamentos/{apontamento_id}/estorno", response_model=ApontamentoSaida, status_code=status.HTTP_201_CREATED)
async def estornar(apontamento_id: UUID, e: EstornoApontamento, ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> ApontamentoSaida:
    o = (await s.execute(text("SELECT * FROM plan.apontamento WHERE id = :id"), {"id": apontamento_id})).mappings().one_or_none()
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "apontamento não encontrado")
    if Decimal(o["horas"]) < 0:
        raise HTTPException(422, "estorno não se estorna")
    ja = (await s.execute(text("SELECT 1 FROM plan.apontamento WHERE estorna_id = :id"), {"id": apontamento_id})).first()
    if ja:
        raise HTTPException(409, "apontamento já estornado")
    row = (await s.execute(text("""
        INSERT INTO plan.apontamento (contrato_id, atividade_id, recurso_id, usuario_id, data, horas, descricao, estorna_id, criado_por)
        VALUES (:c, :a, :r, :u, :d, :h, :desc, :est, :por) RETURNING id"""), {
        "c": o["contrato_id"], "a": o["atividade_id"], "r": o["recurso_id"], "u": ctx.usuario_id, "d": o["data"],
        "h": -Decimal(o["horas"]), "desc": f"estorno: {e.justificativa}", "est": apontamento_id, "por": ctx.nome})).one()
    r = await _um(s, row.id)
    return _saida(r)


# ------------------------------------------------------ previsto × realizado

class LinhaPR(Base):
    edt: str
    descricao: str
    nivel: int
    marco: bool
    inicio_bl: date | None
    termino_bl: date | None
    inicio_vigente: date
    termino_vigente: date
    inicio_real: date | None
    termino_real: date | None
    desvio_termino_dias: int | None
    previsto_ac: Decimal
    real_ac: Decimal
    hh_previstas: Decimal
    hh_apontadas: Decimal
    custo_previsto: Decimal
    custo_real: Decimal
    critica: bool


class Dimensao(Base):
    nome: str
    previsto: Decimal
    realizado: Decimal
    unidade: str
    indice: Decimal | None
    leitura: str


class AchadoCoerenciaSaida(Base):
    regra: str
    severidade: str
    referencia: str
    detalhe: str
    dimensao: str


class PainelPR(Base):
    data_corte: date
    dimensoes: list[Dimensao]
    atividades: list[LinhaPR]
    fases: list[dict]
    marcos: list[dict]
    recursos: list[dict]
    categorias_custo: list[dict]
    achados: list[AchadoCoerenciaSaida]
    indice_coerencia: Decimal
    versao_regra: str


async def montar_painel_pr(s: AsyncSession, contrato_id: UUID, data_corte: date) -> PainelPR:
    plano = await svc.carregar(s, contrato_id)
    cpm, _ = svc.calcular(plano)
    bl = svc.cpm_baseline(plano)
    lanc = await _lancamentos(s, contrato_id, 20000)
    dom_ap = [ap.Apontamento(r["edt"], r["data"], Decimal(r["horas"]), str(r["recurso_id"]) if r["recurso_id"] else None) for r in lanc if r["data"] <= data_corte]
    dias_uteis = {e: (0 if plano.atividades[e]["tipo"] == "MARCO" else d.te - d.ie + 1) for e, d in cpm.atividades.items()}
    prev_hh = await _hh_previstas(s, contrato_id, dias_uteis)
    custos = await montar_painel_custos(s, contrato_id, data_corte)
    custo_por = {a.edt: a for a in custos.atividades}
    meds = await repo.medicoes_por_semana(s, contrato_id)
    primeira_med: dict[str, date] = {}
    conclusao: dict[str, date] = {}
    ultima_med: dict[str, date] = {}
    for m in meds:
        if m["data_corte"] > data_corte:
            continue
        v = Decimal(m["percentual_ac"])
        if v > ZERO:
            primeira_med.setdefault(m["edt"], m["data_corte"])
            ultima_med[m["edt"]] = m["data_corte"]
        if v >= 1:
            conclusao.setdefault(m["edt"], m["data_corte"])
    # previsto BL acumulado por folha na data de corte
    prev_bl: dict[str, Decimal] = {}
    if plano.baseline:
        for (edt, sab), parc in (await repo.previsto_baseline(s, plano.baseline["id"])).items():
            if sab <= data_corte:
                prev_bl[edt] = prev_bl.get(edt, ZERO) + parc

    sinais: list[co.SinaisAtividade] = []
    linhas: list[LinhaPR] = []
    for edt, a in plano.atividades.items():
        d = cpm.atividades[edt]
        b = bl.atividades.get(edt) if bl else None
        meus = [x for x in dom_ap if x.edt == edt]
        hh = q(sum((x.horas for x in meus), start=ZERO))
        datas_ap = sorted(x.data for x in meus if x.horas > 0)
        prog = Decimal(plano.progresso.get(edt, ZERO))
        c = custo_por.get(edt)
        marco = a["tipo"] == "MARCO"
        ini_real = primeira_med.get(edt) or (datas_ap[0] if datas_ap else None)
        fim_real = conclusao.get(edt)
        s_ = co.SinaisAtividade(
            edt=edt, descricao=a["descricao"], marco=marco,
            inicio_bl=plano.cal.data_de(b.ie) if b else None, termino_bl=plano.cal.data_de(b.te) if b else None,
            inicio_vigente=plano.cal.data_de(d.ie), termino_vigente=plano.cal.data_de(d.te), progresso=prog,
            hh_previstas=prev_hh.get(edt, ZERO), hh_apontadas=hh, custo_previsto=c.bac if c else ZERO, custo_real=c.cr if c else ZERO,
            primeira_medicao=primeira_med.get(edt), ultima_medicao=ultima_med.get(edt),
            primeiro_apontamento=datas_ap[0] if datas_ap else None, ultimo_apontamento=datas_ap[-1] if datas_ap else None, critica=d.critica)
        sinais.append(s_)
        linhas.append(LinhaPR(
            edt=edt, descricao=a["descricao"], nivel=edt.count(".") + 1, marco=marco, inicio_bl=s_.inicio_bl, termino_bl=s_.termino_bl,
            inicio_vigente=s_.inicio_vigente, termino_vigente=s_.termino_vigente, inicio_real=ini_real, termino_real=fim_real,
            desvio_termino_dias=((fim_real or s_.termino_vigente) - s_.termino_bl).days if s_.termino_bl else None,
            previsto_ac=q(min(prev_bl.get(edt, ZERO), Decimal(1))), real_ac=prog, hh_previstas=q(prev_hh.get(edt, ZERO)), hh_apontadas=hh,
            custo_previsto=c.bac if c else ZERO, custo_real=c.cr if c else ZERO, critica=d.critica))
    achs = co.analisar(sinais, data_corte)

    nos = svc.nos_dominio(plano)
    from ..dominio import avanco as dom_avanco
    prev_tot, _ = dom_avanco.agregar("", nos, plano.contrato.base_ponderacao, {k: min(v, Decimal(1)) for k, v in prev_bl.items()})
    real_tot, _ = dom_avanco.agregar("", nos, plano.contrato.base_ponderacao, plano.progresso)
    hh_p = q(sum(prev_hh.values(), start=ZERO))
    hh_a = q(sum((x.horas for x in dom_ap), start=ZERO))
    fim_bl = plano.cal.data_de(bl.fim) if bl else None
    fim_vig = plano.cal.data_de(cpm.fim)
    dimensoes = [
        Dimensao(nome="Avanço físico", previsto=q(prev_tot or ZERO), realizado=q(real_tot or ZERO), unidade="fração",
                 indice=q((real_tot or ZERO) / prev_tot) if prev_tot else None, leitura="IDP = real ÷ previsto na data de corte"),
        Dimensao(nome="Prazo (dias úteis)", previsto=Decimal(bl.fim + 1 if bl else cpm.fim + 1), realizado=Decimal(cpm.fim + 1), unidade="dias úteis",
                 indice=q(Decimal(bl.fim + 1) / Decimal(cpm.fim + 1)) if bl and cpm.fim >= 0 else None,
                 leitura=f"término BL {fim_bl:%d/%m/%Y} · vigente {fim_vig:%d/%m/%Y}" if fim_bl else f"vigente {fim_vig:%d/%m/%Y}"),
        Dimensao(nome="Horas de trabalho", previsto=hh_p, realizado=hh_a, unidade="hh", indice=q(hh_p * (real_tot or ZERO) / hh_a) if hh_a > ZERO and hh_p > ZERO else None,
                 leitura="produtividade = hh previstas × avanço ÷ hh apontadas"),
        Dimensao(nome="Custo", previsto=custos.evm.bac, realizado=custos.evm.cr, unidade="R$", indice=custos.evm.idc, leitura="IDC = valor agregado ÷ custo real"),
    ]
    fases = []
    for n in plano.nos:
        if int(n["nivel"]) != 2:
            continue
        p_, _ = dom_avanco.agregar(n["edt"], nos, plano.contrato.base_ponderacao, {k: min(v, Decimal(1)) for k, v in prev_bl.items()})
        r_, _ = dom_avanco.agregar(n["edt"], nos, plano.contrato.base_ponderacao, plano.progresso)
        sub = [x for x in linhas if x.edt.startswith(n["edt"] + ".")]
        fases.append({"edt": n["edt"], "descricao": n["descricao"], "previsto_ac": str(q(p_)) if p_ is not None else None, "real_ac": str(q(r_)) if r_ is not None else None,
                      "hh_previstas": str(q(sum((x.hh_previstas for x in sub), start=ZERO))), "hh_apontadas": str(q(sum((x.hh_apontadas for x in sub), start=ZERO))),
                      "custo_previsto": str(sum((x.custo_previsto for x in sub), start=ZERO)), "custo_real": str(sum((x.custo_real for x in sub), start=ZERO)),
                      "termino_bl": max((x.termino_bl for x in sub if x.termino_bl), default=None), "termino_vigente": max((x.termino_vigente for x in sub), default=None)})
    marcos = [{"edt": x.edt, "descricao": x.descricao, "bl": x.inicio_bl, "vigente": x.inicio_vigente, "real": x.termino_real, "critica": x.critica}
              for x in linhas if x.marco]
    rec_rows = (await s.execute(text("""
        SELECT r.id, r.codigo, r.nome, r.horas_dia, r.custo_hora,
               COALESCE((SELECT sum(al.quantidade_dia) FROM plan.alocacao al WHERE al.recurso_id = r.id), 0) AS qtd
          FROM plan.recurso r WHERE r.contrato_id = :c ORDER BY r.codigo"""), {"c": contrato_id})).mappings().all()
    hh_rec = ap.horas_por_recurso(dom_ap)
    recursos = []
    for r in rec_rows:
        prev_r = sum((Decimal(al.quantidade_dia) * Decimal(r["horas_dia"]) * dias_uteis.get(al.edt, 0)
                      for al in (await s.execute(text("""SELECT al.quantidade_dia, n.edt FROM plan.alocacao al JOIN plan.atividade a ON a.id = al.atividade_id
                                                          JOIN plan.eap_no n ON n.id = a.eap_no_id WHERE al.recurso_id = :r"""), {"r": r["id"]})).all()), start=ZERO)
        recursos.append({"codigo": r["codigo"], "nome": r["nome"], "hh_previstas": str(q(prev_r)), "hh_apontadas": str(hh_rec.get(str(r["id"]), ZERO)),
                         "custo_previsto": str(q(prev_r * Decimal(r["custo_hora"]))), "custo_horas_apontadas": str(q(hh_rec.get(str(r["id"]), ZERO) * Decimal(r["custo_hora"])))})
    if "(sem recurso)" in hh_rec:
        recursos.append({"codigo": "—", "nome": "sem recurso vinculado", "hh_previstas": "0", "hh_apontadas": str(hh_rec["(sem recurso)"]), "custo_previsto": "0", "custo_horas_apontadas": "0"})
    return PainelPR(
        data_corte=data_corte, dimensoes=dimensoes, atividades=linhas, fases=fases, marcos=marcos, recursos=recursos,
        categorias_custo=[c_.model_dump(mode="json") for c_ in custos.categorias],
        achados=[AchadoCoerenciaSaida(**asdict(a)) for a in achs],
        indice_coerencia=co.indice_coerencia(achs, len([x for x in linhas if not x.marco])), versao_regra=co.VERSAO_REGRA)


@router.get("/contratos/{contrato_id}/apontamentos", response_model=PainelApontamentos)
async def painel(contrato_id: UUID, semana: date | None = None, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> PainelApontamentos:
    return await montar_painel_apontamentos(s, contrato_id, semana)


@router.get("/contratos/{contrato_id}/previsto-realizado", response_model=PainelPR)
async def previsto_realizado(contrato_id: UUID, data_corte: date = Query(...), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> PainelPR:
    return await montar_painel_pr(s, contrato_id, data_corte)


async def montar_burndown(s: AsyncSession, contrato_id: UUID, edt: str, recurso_id: UUID | None) -> BurndownSaida:
    """Burndown de horas da tarefa (RGR-APT-002): previsto queima linearmente por dia útil entre início e término
    vigentes; realizado é o acumulado dos apontamentos (todos os recursos ou um só). Linhas por função (recurso)."""
    plano = await svc.carregar(s, contrato_id)
    cpm, _ = svc.calcular(plano)
    if edt not in plano.atividades or edt not in cpm.atividades:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "atividade não encontrada")
    a = plano.atividades[edt]
    d = cpm.atividades[edt]
    ini, fim = plano.cal.data_de(d.ie), plano.cal.data_de(d.te)
    rows = (await s.execute(text("""
        SELECT r.id AS recurso_id, r.codigo, r.nome, al.quantidade_dia, r.horas_dia
          FROM plan.alocacao al JOIN plan.atividade at ON at.id = al.atividade_id JOIN plan.eap_no n ON n.id = at.eap_no_id
          JOIN plan.recurso r ON r.id = al.recurso_id WHERE at.contrato_id = :c AND n.edt = :e"""), {"c": contrato_id, "e": edt})).mappings().all()
    du = 0 if a["tipo"] == "MARCO" else d.te - d.ie + 1
    prev_rec = {str(r["recurso_id"]): (r["codigo"], r["nome"], Decimal(r["quantidade_dia"]) * Decimal(r["horas_dia"]) * du) for r in rows}
    if recurso_id is not None:
        prev_rec = {k: v for k, v in prev_rec.items() if k == str(recurso_id)}
    hh_prev = sum((v[2] for v in prev_rec.values()), start=ZERO)
    if hh_prev == ZERO and recurso_id is None:
        hh_prev = Decimal(a.get("hh_estimadas") or 0)
    lanc = [r for r in await _lancamentos(s, contrato_id, 20000) if r["edt"] == edt and (recurso_id is None or r["recurso_id"] == recurso_id)]
    ultimo = max((r["data"] for r in lanc), default=ini)
    fim_eixo = max(fim, date.today(), ultimo)
    dias = [ini + timedelta(days=i) for i in range((fim_eixo - ini).days + 1)]
    eixo = set(plano.cal.eixo)
    uteis = [x for x in dias if x <= fim and x in eixo]
    por_dia = hh_prev / len(uteis) if uteis else ZERO
    previsto, real, burn = [], [], []
    acc_prev = hh_prev
    acc_real = ZERO
    horas_dia: dict[date, Decimal] = {}
    for r in lanc:
        horas_dia[r["data"]] = horas_dia.get(r["data"], ZERO) + Decimal(r["horas"])
    for x in dias:
        if x in uteis:
            acc_prev -= por_dia
        acc_real += horas_dia.get(x, ZERO)
        previsto.append(max(acc_prev, ZERO).quantize(Decimal("0.01")))
        real.append(acc_real.quantize(Decimal("0.01")))
        burn.append((hh_prev - acc_real).quantize(Decimal("0.01")))
    linhas = []
    for rid, (cod, nome, hh) in prev_rec.items():
        hs: dict[str, Decimal] = {}
        for r in lanc:
            if str(r["recurso_id"]) == rid:
                hs[r["data"].isoformat()] = hs.get(r["data"].isoformat(), ZERO) + Decimal(r["horas"])
        linhas.append(LinhaBurndown(recurso_id=rid, codigo=cod, nome=nome, hh_previstas=q(hh), horas=hs, total=sum(hs.values(), start=ZERO)))
    sem = [r for r in lanc if r["recurso_id"] is None or str(r["recurso_id"]) not in prev_rec]
    if sem:
        hs = {}
        for r in sem:
            hs[r["data"].isoformat()] = hs.get(r["data"].isoformat(), ZERO) + Decimal(r["horas"])
        linhas.append(LinhaBurndown(recurso_id=None, codigo="—", nome="sem recurso / não alocado", hh_previstas=ZERO, horas=hs, total=sum(hs.values(), start=ZERO)))
    return BurndownSaida(edt=edt, descricao=str(a.get("descricao", "")), inicio=ini, termino=fim, dias=dias, hh_previstas=q(hh_prev), hh_apontadas=q(acc_real), saldo=q(hh_prev - acc_real),
                         previsto_restante=previsto, realizado_ac=real, burndown=burn, linhas=linhas, total_dia={k.isoformat(): q(v) for k, v in horas_dia.items()},
                         progresso=Decimal(plano.progresso.get(edt, ZERO)))


async def montar_burndown_global(s: AsyncSession, contrato_id: UUID, recurso_id: UUID | None) -> BurndownSaida:
    """Burndown do projeto inteiro: soma, dia a dia, o previsto linear de cada atividade e todas as horas apontadas."""
    plano = await svc.carregar(s, contrato_id)
    cpm, _ = svc.calcular(plano)
    rows = (await s.execute(text("""
        SELECT n.edt, r.id AS recurso_id, r.codigo, r.nome, al.quantidade_dia, r.horas_dia
          FROM plan.alocacao al JOIN plan.atividade at ON at.id = al.atividade_id JOIN plan.eap_no n ON n.id = at.eap_no_id
          JOIN plan.recurso r ON r.id = al.recurso_id WHERE at.contrato_id = :c"""), {"c": contrato_id})).mappings().all()
    prev_dia: dict[date, Decimal] = {}
    prev_rec: dict[str, tuple[str, str, Decimal]] = {}
    hh_prev = ZERO
    for edt, d in cpm.atividades.items():
        if plano.atividades[edt]["tipo"] == "MARCO":
            continue
        du = d.te - d.ie + 1
        aloc = [r for r in rows if r["edt"] == edt and (recurso_id is None or r["recurso_id"] == recurso_id)]
        hh = sum((Decimal(r["quantidade_dia"]) * Decimal(r["horas_dia"]) * du for r in aloc), start=ZERO)
        if hh == ZERO and recurso_id is None:
            hh = Decimal(plano.atividades[edt].get("hh_estimadas") or 0)
        for r in aloc:
            rid = str(r["recurso_id"])
            ant = prev_rec.get(rid, (r["codigo"], r["nome"], ZERO))
            prev_rec[rid] = (r["codigo"], r["nome"], ant[2] + Decimal(r["quantidade_dia"]) * Decimal(r["horas_dia"]) * du)
        hh_prev += hh
        if hh and du:
            por = hh / du
            for i in range(d.ie, d.te + 1):
                x = plano.cal.data_de(i)
                prev_dia[x] = prev_dia.get(x, ZERO) + por
    lanc = [r for r in await _lancamentos(s, contrato_id, 50000) if recurso_id is None or r["recurso_id"] == recurso_id]
    ini = plano.cal.data_de(0)
    fim = plano.cal.data_de(cpm.fim)
    ultimo = max((r["data"] for r in lanc), default=ini)
    fim_eixo = max(fim, date.today(), ultimo)
    dias = [ini + timedelta(days=i) for i in range((fim_eixo - ini).days + 1)]
    horas_dia: dict[date, Decimal] = {}
    for r in lanc:
        horas_dia[r["data"]] = horas_dia.get(r["data"], ZERO) + Decimal(r["horas"])
    previsto, real, burn = [], [], []
    acc_prev, acc_real = hh_prev, ZERO
    for x in dias:
        acc_prev -= prev_dia.get(x, ZERO)
        acc_real += horas_dia.get(x, ZERO)
        previsto.append(max(acc_prev, ZERO).quantize(Decimal("0.01")))
        real.append(acc_real.quantize(Decimal("0.01")))
        burn.append((hh_prev - acc_real).quantize(Decimal("0.01")))
    linhas = []
    for rid, (cod, nome, hh) in sorted(prev_rec.items(), key=lambda kv: -kv[1][2]):
        hs: dict[str, Decimal] = {}
        for r in lanc:
            if str(r["recurso_id"]) == rid:
                hs[r["data"].isoformat()] = hs.get(r["data"].isoformat(), ZERO) + Decimal(r["horas"])
        linhas.append(LinhaBurndown(recurso_id=rid, codigo=cod, nome=nome, hh_previstas=q(hh), horas=hs, total=sum(hs.values(), start=ZERO)))
    sem = [r for r in lanc if r["recurso_id"] is None or str(r["recurso_id"]) not in prev_rec]
    if sem:
        hs = {}
        for r in sem:
            hs[r["data"].isoformat()] = hs.get(r["data"].isoformat(), ZERO) + Decimal(r["horas"])
        linhas.append(LinhaBurndown(recurso_id=None, codigo="—", nome="sem recurso / não alocado", hh_previstas=ZERO, horas=hs, total=sum(hs.values(), start=ZERO)))
    pesos = sum((Decimal(plano.atividades[e]["pontos"]) for e in cpm.atividades), start=ZERO)
    prog = (sum((Decimal(plano.atividades[e]["pontos"]) * Decimal(plano.progresso.get(e, ZERO)) for e in cpm.atividades), start=ZERO) / pesos) if pesos else ZERO
    return BurndownSaida(edt="*", descricao="Projeto inteiro — todas as tarefas", inicio=ini, termino=fim, dias=dias, hh_previstas=q(hh_prev), hh_apontadas=q(acc_real), saldo=q(hh_prev - acc_real),
                         previsto_restante=previsto, realizado_ac=real, burndown=burn, linhas=linhas, total_dia={k.isoformat(): q(v) for k, v in horas_dia.items()}, progresso=prog.quantize(Decimal("0.0001")))


@router.get("/contratos/{contrato_id}/apontamentos/burndown", response_model=BurndownSaida)
async def burndown(contrato_id: UUID, edt: str, recurso_id: UUID | None = None, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> BurndownSaida:
    if edt == "*":
        return await montar_burndown_global(s, contrato_id, recurso_id)
    return await montar_burndown(s, contrato_id, edt, recurso_id)
