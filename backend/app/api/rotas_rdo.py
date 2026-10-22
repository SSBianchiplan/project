"""RDO — Relatório Diário de Obra (anexo V do procedimento), com fluxo de aprovação.

O RDO é registro append-only (tabela rdo, 001): o que foi emitido não se edita. O estado vem do
último evento em rdo_evento (RGR-RDO-001):

    EMITIDO ──ENVIADO──▶ EM_APROVACAO_PLANEJAMENTO ──APROVADO_PLANEJAMENTO──▶ EM_APROVACAO_GERENCIA
    ──APROVADO_GERENCIA──▶ APROVADO.   REPROVADO_* devolve a EMITIDO com parecer; pode ser reenviado.

Papéis: emitir e enviar = PLANEJADOR+; aprovar pelo planejamento = PLANEJADOR+ (outro usuário que
não o emissor); aprovar pela gerência do contrato = ADMIN+ ou FISCAL (fiscalização do contratante).
Prazo e avanço do dia são congelados no RDO a partir do cockpit (snapshot auditável).
"""
from __future__ import annotations

import json
from datetime import date, datetime
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
from . import servico_cronograma as svc
from .rotas_cronograma import montar_cockpit

router = APIRouter(prefix="/api/v1", tags=["rdo"])

DIAS = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
ESTADO_POR_EVENTO = {"EMITIDO": "EMITIDO", "EDITADO": "EMITIDO", "ENVIADO": "EM_APROVACAO_PLANEJAMENTO", "APROVADO_PLANEJAMENTO": "EM_APROVACAO_GERENCIA",
                     "REPROVADO_PLANEJAMENTO": "REPROVADO", "APROVADO_GERENCIA": "APROVADO", "REPROVADO_GERENCIA": "REPROVADO"}
TRANSICOES = {"EMITIDO": {"ENVIADO", "EDITADO"}, "REPROVADO": {"ENVIADO", "EDITADO"}, "EM_APROVACAO_PLANEJAMENTO": {"APROVADO_PLANEJAMENTO", "REPROVADO_PLANEJAMENTO"},
              "EM_APROVACAO_GERENCIA": {"APROVADO_GERENCIA", "REPROVADO_GERENCIA"}, "APROVADO": set()}


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Turno(Base):
    turno: str
    inicio: str = ""
    termino: str = ""
    horas: Decimal = Decimal(0)
    periodo: str = ""
    clima: str = "Praticável"
    solo: str = "Praticável"
    paralisacao_h: Decimal = Decimal(0)
    motivo: str = ""


class LinhaEfetivo(Base):
    funcao: str
    presentes: int = 0
    ausentes: int = 0


class LinhaEquipamento(Base):
    nome: str
    previsto: int = 0
    real: int = 0


class Servico(Base):
    edt: str = ""
    disciplina: str = ""
    descricao: str = Field(min_length=1)
    unidade: str = ""
    qtd_dia: Decimal = Decimal(0)
    qtd_acum: Decimal = Decimal(0)
    local: str = ""
    evidencia: str = ""


class Ocorrencia(Base):
    tipo: str = "OCORRENCIA"          # OCORRENCIA | PENDENCIA | SS (solicitação de serviço) | SEGURANCA | QUALIDADE
    descricao: str = Field(min_length=1)
    impacto: str = ""
    acao: str = ""
    responsavel: str = ""
    prazo: date | None = None


class RdoEntrada(Base):
    data: date
    local_area: str = ""
    escopo: str = ""
    tema_dds: str = ""
    responsavel_tecnico: str = ""
    encarregado: str = ""
    turnos: list[Turno] = []
    pluviometria_mm: Decimal = Decimal(0)
    moi: list[LinhaEfetivo] = []
    mod: list[LinhaEfetivo] = []
    equipamentos: list[LinhaEquipamento] = []
    servicos: list[Servico] = []
    ocorrencias: list[Ocorrencia] = []
    observacoes: str = ""
    enviar: bool = False              # já envia para aprovação ao emitir


class EventoEntrada(Base):
    evento: str = Field(pattern="^(ENVIADO|APROVADO_PLANEJAMENTO|REPROVADO_PLANEJAMENTO|APROVADO_GERENCIA|REPROVADO_GERENCIA)$")
    parecer: str = ""


class EventoSaida(Base):
    evento: str
    parecer: str
    por: str
    papel: str
    em: datetime


class RdoResumo(Base):
    id: UUID
    numero: int
    data: date
    dia_semana: str
    estado: str
    emitido_por: str
    emitido_em: datetime
    horas_paradas: Decimal
    efetivo_total: int
    hh_dia: Decimal
    servicos: int
    ocorrencias: int
    ultimo_evento: EventoSaida | None


class RdoSaida(RdoResumo):
    contrato: dict
    prazo: dict
    identificacao: dict
    turnos: list[Turno]
    pluviometria_mm: Decimal
    moi: list[LinhaEfetivo]
    mod: list[LinhaEfetivo]
    equipamentos: list[LinhaEquipamento]
    lista_servicos: list[Servico]
    lista_ocorrencias: list[Ocorrencia]
    observacoes: str
    eventos: list[EventoSaida]
    pode: dict


class SugestaoRdo(Base):
    """Pré-preenchimento do dia a partir do cronograma vigente, alocações e catálogo."""
    data: date
    servicos: list[Servico]
    moi: list[LinhaEfetivo]
    mod: list[LinhaEfetivo]
    equipamentos: list[LinhaEquipamento]
    prazo: dict


# ------------------------------------------------------------------ helpers
def _js(v: object) -> object:
    return json.loads(json.dumps(v, default=str))


def _estado(eventos: list[dict]) -> str:
    return ESTADO_POR_EVENTO[eventos[-1]["evento"]] if eventos else "EMITIDO"


def _pode(ctx: Contexto, estado: str, emitido_por: str) -> dict:
    p = ctx.papel
    n = {"LEITOR": 0, "FISCAL": 1, "PLANEJADOR": 2, "ADMIN": 3, "ADMIN_MASTER": 4}.get(p, 0)
    return {
        "enviar": estado in ("EMITIDO", "REPROVADO") and n >= 2,
        "aprovar_planejamento": estado == "EM_APROVACAO_PLANEJAMENTO" and n >= 2 and ctx.nome != emitido_por,
        "aprovar_gerencia": estado == "EM_APROVACAO_GERENCIA" and (n >= 3 or p == "FISCAL"),
        "editar": estado in ("EMITIDO", "REPROVADO") and n >= 2,
    }


async def _eventos(s: AsyncSession, rdo_id: UUID) -> list[dict]:
    rows = (await s.execute(text("SELECT evento, parecer, por, papel, em FROM plan.rdo_evento WHERE rdo_id = :r ORDER BY em, id"), {"r": rdo_id})).mappings().all()
    return [dict(r) for r in rows]


async def _prazo_do_dia(s: AsyncSession, contrato_id: UUID, dia: date) -> dict:
    from datetime import timedelta
    sab = dia + timedelta(days=(5 - dia.weekday()) % 7)
    try:
        ck = await montar_cockpit(s, contrato_id, sab, "BL0")
        c = ck.cartoes
        contrato = await repo.contrato(s, contrato_id)
        assert contrato is not None
        return {"data_corte": sab.isoformat(), "termino_contratual": contrato.termino_contratual.isoformat(), "termino_projetado": c.termino_projetado.isoformat(),
                "previsto_ac": str(c.previsto_ac), "real_ac": str(c.real_ac), "desvio_abs": str(c.desvio_abs) if c.desvio_abs is not None else None,
                "situacao": c.situacao, "prazo_faltante_dias": (contrato.termino_contratual - dia).days, "dias_atraso": max(0, (c.termino_projetado - contrato.termino_contratual).days)}
    except Exception as e:   # RDO não pode deixar de ser emitido por falha do cockpit
        return {"erro": str(e)[:120]}


def _resumo(r: dict, eventos: list[dict], ctx: Contexto | None = None) -> RdoResumo:
    ef = r["efetivo"] or {}
    moi = ef.get("moi", [])
    mod = ef.get("mod", [])
    total = sum(int(x.get("presentes", 0)) for x in moi + mod)
    horas = sum(Decimal(str(t.get("horas", 0))) for t in (r["clima"] or {}).get("turnos", [])) or Decimal(8)
    return RdoResumo(id=r["id"], numero=r["numero"], data=r["data"], dia_semana=DIAS[r["data"].weekday()], estado=_estado(eventos), emitido_por=r["criado_por"],
                     emitido_em=r["emitido_em"], horas_paradas=r["horas_paradas"], efetivo_total=total, hh_dia=Decimal(total) * horas,
                     servicos=len(r["servicos"] or []), ocorrencias=len(r["ocorrencias"] or []),
                     ultimo_evento=EventoSaida(**eventos[-1]) if eventos else None)


async def _saida(s: AsyncSession, r: dict, ctx: Contexto) -> RdoSaida:
    eventos = await _eventos(s, r["id"])
    base = _resumo(r, eventos)
    contrato = await repo.contrato(s, r["contrato_id"])
    assert contrato is not None
    ef = r["efetivo"] or {}
    cl = r["clima"] or {}
    return RdoSaida(**base.model_dump(), contrato={"codigo": contrato.codigo, "empreendimento": contrato.empreendimento, "contratante": contrato.contratante, "contratada": contrato.contratada},
                    prazo=ef.get("prazo", {}), identificacao={k: ef.get(k, "") for k in ("local_area", "escopo", "tema_dds", "responsavel_tecnico", "encarregado")},
                    turnos=[Turno(**t) for t in cl.get("turnos", [])], pluviometria_mm=Decimal(str(cl.get("pluviometria_mm", 0))),
                    moi=[LinhaEfetivo(**x) for x in ef.get("moi", [])], mod=[LinhaEfetivo(**x) for x in ef.get("mod", [])],
                    equipamentos=[LinhaEquipamento(**x) for x in ef.get("equipamentos", [])],
                    lista_servicos=[Servico(**x) for x in (r["servicos"] or [])], lista_ocorrencias=[Ocorrencia(**x) for x in (r["ocorrencias"] or [])],
                    observacoes=ef.get("observacoes", ""), eventos=[EventoSaida(**e) for e in eventos], pode=_pode(ctx, base.estado, r["criado_por"]))


async def _carregar(s: AsyncSession, rdo_id: UUID) -> dict:
    row = (await s.execute(text("SELECT * FROM plan.rdo WHERE id = :r"), {"r": rdo_id})).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "RDO não encontrado")
    return dict(row)


# ------------------------------------------------------------------ rotas
@router.get("/contratos/{contrato_id}/rdos", response_model=list[RdoResumo])
async def listar(contrato_id: UUID, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> list[RdoResumo]:
    rows = (await s.execute(text("SELECT * FROM plan.rdo WHERE contrato_id = :c ORDER BY data DESC, numero DESC"), {"c": contrato_id})).mappings().all()
    ev = (await s.execute(text("SELECT rdo_id, evento, parecer, por, papel, em FROM plan.rdo_evento WHERE contrato_id = :c ORDER BY em, id"), {"c": contrato_id})).mappings().all()
    por_rdo: dict[UUID, list[dict]] = {}
    for e in ev:
        d = dict(e)
        rid = d.pop("rdo_id")
        por_rdo.setdefault(rid, []).append(d)
    return [_resumo(dict(r), por_rdo.get(r["id"], [])) for r in rows]


@router.get("/contratos/{contrato_id}/rdos/sugestao", response_model=SugestaoRdo)
async def sugestao(contrato_id: UUID, data: date, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> SugestaoRdo:
    """Pré-preenche o RDO do dia: atividades em andamento, efetivo alocado (por função) e equipamentos."""
    plano = await svc.carregar(s, contrato_id)
    cpm, _ = svc.calcular(plano)
    idx = plano.cal.indice_de(data)
    ativas = [edt for edt, d in cpm.atividades.items() if d.ie <= idx <= d.te and plano.atividades[edt].get("tipo") != "MARCO" and plano.progresso.get(edt, Decimal(0)) < 1]
    servicos = [Servico(edt=edt, disciplina=str(plano.atividades[edt].get("disciplina") or ""), descricao=str(plano.atividades[edt].get("descricao") or edt),
                        unidade=str(plano.atividades[edt].get("unidade") or "%"), qtd_acum=Decimal(str(plano.progresso.get(edt, Decimal(0)))) * 100 if str(plano.atividades[edt].get("unidade") or "%") == "%" else Decimal(0)) for edt in ativas]
    rows = (await s.execute(text("""
        SELECT r.codigo, r.nome, r.tipo::text AS tipo, r.capacidade_dia, n.edt, al.quantidade_dia
          FROM plan.alocacao al JOIN plan.atividade a ON a.id = al.atividade_id JOIN plan.eap_no n ON n.id = a.eap_no_id
          JOIN plan.recurso r ON r.id = al.recurso_id WHERE a.contrato_id = :c AND r.ativo"""), {"c": contrato_id})).mappings().all()
    moi: dict[str, int] = {}
    mod: dict[str, int] = {}
    eq: dict[str, int] = {}
    for r in rows:
        if r["edt"] not in ativas:
            continue
        q = int(Decimal(r["quantidade_dia"]))
        alvo = moi if r["tipo"] == "MOI" else mod if r["tipo"] == "MOD" else eq
        alvo[r["nome"]] = alvo.get(r["nome"], 0) + q
    return SugestaoRdo(data=data, servicos=servicos,
                       moi=[LinhaEfetivo(funcao=k, presentes=v) for k, v in sorted(moi.items())],
                       mod=[LinhaEfetivo(funcao=k, presentes=v) for k, v in sorted(mod.items())],
                       equipamentos=[LinhaEquipamento(nome=k, previsto=v, real=v) for k, v in sorted(eq.items())],
                       prazo=await _prazo_do_dia(s, contrato_id, data))


@router.post("/contratos/{contrato_id}/rdos", response_model=RdoSaida, status_code=status.HTTP_201_CREATED)
async def emitir(contrato_id: UUID, e: RdoEntrada, ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> RdoSaida:
    if e.data > date.today():
        raise HTTPException(422, "RDO não se emite para data futura")
    existe = (await s.execute(text("SELECT numero FROM plan.rdo WHERE contrato_id = :c AND data = :d"), {"c": contrato_id, "d": e.data})).first()
    if existe:
        raise HTTPException(409, f"já existe o RDO nº {existe.numero} para {e.data:%d/%m/%Y}; o RDO é registro único do dia (append-only)")
    paradas = sum((t.paralisacao_h for t in e.turnos), start=Decimal(0))
    motivo = "; ".join(t.motivo for t in e.turnos if t.paralisacao_h > 0 and t.motivo) or None
    if paradas > 0 and not motivo:
        raise HTTPException(422, "horas paradas exigem motivo da paralisação (ck_rdo_parada)")
    numero = (await s.execute(text("SELECT coalesce(max(numero), 0) + 1 FROM plan.rdo WHERE contrato_id = :c"), {"c": contrato_id})).scalar_one()
    prazo = await _prazo_do_dia(s, contrato_id, e.data)
    efetivo = {"moi": [x.model_dump() for x in e.moi], "mod": [x.model_dump() for x in e.mod], "equipamentos": [x.model_dump() for x in e.equipamentos],
               "local_area": e.local_area, "escopo": e.escopo, "tema_dds": e.tema_dds, "responsavel_tecnico": e.responsavel_tecnico, "encarregado": e.encarregado,
               "observacoes": e.observacoes, "prazo": prazo}
    clima = {"turnos": [t.model_dump() for t in e.turnos], "pluviometria_mm": str(e.pluviometria_mm)}
    row = (await s.execute(text("""
        INSERT INTO plan.rdo (contrato_id, data, numero, emitido_em, valido, clima, horas_paradas, motivo_parada, efetivo, servicos, ocorrencias, criado_por)
        VALUES (:c, :d, :n, now(), true, CAST(:clima AS jsonb), :hp, :mot, CAST(:ef AS jsonb), CAST(:sv AS jsonb), CAST(:oc AS jsonb), :por) RETURNING id"""), {
        "c": contrato_id, "d": e.data, "n": numero, "clima": json.dumps(_js(clima)), "hp": paradas, "mot": motivo, "ef": json.dumps(_js(efetivo)),
        "sv": json.dumps(_js([x.model_dump() for x in e.servicos])), "oc": json.dumps(_js([x.model_dump() for x in e.ocorrencias])), "por": ctx.nome})).one()
    await s.execute(text("INSERT INTO plan.rdo_evento (contrato_id, rdo_id, evento, parecer, por, papel) VALUES (:c, :r, 'EMITIDO', '', :por, :papel)"),
                    {"c": contrato_id, "r": row.id, "por": ctx.nome, "papel": ctx.papel})
    if e.enviar:
        await s.execute(text("INSERT INTO plan.rdo_evento (contrato_id, rdo_id, evento, parecer, por, papel) VALUES (:c, :r, 'ENVIADO', 'enviado na emissão', :por, :papel)"),
                        {"c": contrato_id, "r": row.id, "por": ctx.nome, "papel": ctx.papel})
    return await _saida(s, await _carregar(s, row.id), ctx)


@router.get("/rdos/{rdo_id}", response_model=RdoSaida)
async def detalhe(rdo_id: UUID, ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> RdoSaida:
    return await _saida(s, await _carregar(s, rdo_id), ctx)


@router.post("/rdos/{rdo_id}/eventos", response_model=RdoSaida, status_code=status.HTTP_201_CREATED)
async def registrar_evento(rdo_id: UUID, e: EventoEntrada, ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> RdoSaida:
    r = await _carregar(s, rdo_id)
    eventos = await _eventos(s, rdo_id)
    estado = _estado(eventos)
    if e.evento not in TRANSICOES.get(estado, set()):
        raise HTTPException(409, f"RGR-RDO-001: RDO em {estado} não aceita {e.evento}")
    pode = _pode(ctx, estado, r["criado_por"])
    if e.evento == "ENVIADO" and not pode["enviar"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "enviar para aprovação exige papel PLANEJADOR")
    if e.evento.endswith("_PLANEJAMENTO") and not pode["aprovar_planejamento"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "aprovação do planejamento: papel PLANEJADOR e usuário diferente do emissor")
    if e.evento.endswith("_GERENCIA") and not pode["aprovar_gerencia"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "aprovação da gerência do contrato: papel ADMIN ou FISCAL")
    if e.evento.startswith("REPROVADO") and len(e.parecer.strip()) < 10:
        raise HTTPException(422, "reprovação exige parecer (mínimo 10 caracteres)")
    await s.execute(text("INSERT INTO plan.rdo_evento (contrato_id, rdo_id, evento, parecer, por, papel) VALUES (:c, :r, :ev, :p, :por, :papel)"),
                    {"c": r["contrato_id"], "r": rdo_id, "ev": e.evento, "p": e.parecer.strip(), "por": ctx.nome, "papel": ctx.papel})
    return await _saida(s, r, ctx)


@router.get("/rdos/{rdo_id}/pdf")
async def pdf_rdo(rdo_id: UUID, ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> Response:
    d = await _saida(s, await _carregar(s, rdo_id), ctx)
    pz = d.prazo
    est = d.estado.replace("_", " ").lower()
    cab = exportar.Cabecalho(titulo=f"RDO nº {d.numero} — {d.data:%d/%m/%Y} ({d.dia_semana})", contrato_codigo=d.contrato["codigo"], empreendimento=d.contrato["empreendimento"],
                             contratante=d.contrato["contratante"], contratada=d.contrato["contratada"], data_corte=d.data, emitido_por=ctx.nome, baseline="", paisagem=False)
    secoes = [
        exportar.Secao("Identificação", cartoes=[("RDO nº", str(d.numero)), ("Data", d.data.strftime("%d/%m/%Y")), ("Estado", est), ("Emitido por", d.emitido_por),
                                                 ("Local / área", d.identificacao.get("local_area") or "—"), ("Tema do DDS", d.identificacao.get("tema_dds") or "—"),
                                                 ("Responsável técnico", d.identificacao.get("responsavel_tecnico") or "—"), ("Encarregado geral", d.identificacao.get("encarregado") or "—")]),
        exportar.Secao("Prazo e avanço físico (snapshot do cockpit)", cartoes=[("Término contratual", pz.get("termino_contratual", "—")), ("Projeção de término", pz.get("termino_projetado", "—")),
                                                                                ("% previsto", exportar.pct(Decimal(pz["previsto_ac"])) if pz.get("previsto_ac") else "—"), ("% real", exportar.pct(Decimal(pz["real_ac"])) if pz.get("real_ac") else "—"),
                                                                                ("Prazo faltante", f"{pz.get('prazo_faltante_dias', '—')} dias"), ("Dias de atraso", str(pz.get("dias_atraso", "—"))),
                                                                                ("Desvio absoluto", exportar.pct(Decimal(pz["desvio_abs"])) if pz.get("desvio_abs") else "—"), ("Situação", str(pz.get("situacao", "—")).replace("_", " ").lower())]),
        exportar.Secao("Jornada, clima e condições", cabecalhos=["Turno", "Início", "Término", "Horas", "Período", "Clima", "Solo", "Paralisação (h)", "Motivo"],
                       linhas=[[t.turno, t.inicio, t.termino, t.horas, t.periodo, t.clima, t.solo, t.paralisacao_h, t.motivo] for t in d.turnos] or [["—"] * 9],
                       nota=f"Pluviometria do dia: {d.pluviometria_mm} mm · total de horas paradas: {d.horas_paradas}", larguras=[0.9, 0.7, 0.7, 0.6, 0.8, 1, 1, 0.9, 2.4]),
        exportar.Secao("Efetivo — mão de obra indireta", cabecalhos=["Função", "Presentes", "Ausentes", "Total"], linhas=[[x.funcao, x.presentes, x.ausentes, x.presentes + x.ausentes] for x in d.moi] or [["—", 0, 0, 0]], larguras=[4, 1, 1, 1]),
        exportar.Secao("Efetivo — mão de obra direta", cabecalhos=["Função", "Presentes", "Ausentes", "Total"], linhas=[[x.funcao, x.presentes, x.ausentes, x.presentes + x.ausentes] for x in d.mod] or [["—", 0, 0, 0]], larguras=[4, 1, 1, 1],
                       nota=f"Efetivo total (MOI + MOD): {d.efetivo_total} · HH do dia: {d.hh_dia}"),
        exportar.Secao("Equipamentos", cabecalhos=["Equipamento", "Previsto", "Real", "Disponibilidade"], linhas=[[x.nome, x.previsto, x.real, exportar.pct(Decimal(x.real) / Decimal(x.previsto), 0) if x.previsto else "—"] for x in d.equipamentos] or [["—", 0, 0, ""]], larguras=[4, 1, 1, 1.2]),
        exportar.Secao("Serviços executados no dia", cabecalhos=["EDT", "Disciplina", "Descrição do serviço", "Unid.", "Qtd. dia", "Qtd. acum.", "Local / tag", "Evidência"],
                       linhas=[[x.edt, x.disciplina, x.descricao, x.unidade, x.qtd_dia, x.qtd_acum, x.local, x.evidencia] for x in d.lista_servicos] or [["—", "", "nenhum serviço registrado", "", "", "", "", ""]], larguras=[0.7, 1, 3, 0.6, 0.8, 0.9, 1.4, 1.6]),
        exportar.Secao("Ocorrências, pendências e SS", cabecalhos=["Tipo", "Descrição", "Impacto", "Ação adotada", "Responsável", "Prazo"],
                       linhas=[[x.tipo, x.descricao, x.impacto, x.acao, x.responsavel, x.prazo] for x in d.lista_ocorrencias] or [["—", "sem ocorrências", "", "", "", None]], larguras=[1, 3, 1.4, 2.2, 1.2, 0.9],
                       nota="Desvios de escopo, prazo ou custo registrados aqui alimentam o Previsto × Realizado e o Status Report."),
        exportar.Secao("Aprovação (RGR-RDO-001)", cabecalhos=["Evento", "Por", "Papel", "Em", "Parecer"], linhas=[[e.evento.replace("_", " ").lower(), e.por, e.papel, e.em, e.parecer] for e in d.eventos], larguras=[1.6, 1.6, 1, 1.3, 3.5],
                       nota="Assinaturas: CONTRATADA (emissor) · PLANEJAMENTO · GERÊNCIA DO CONTRATO / FISCALIZAÇÃO — registradas como eventos, com data e hora."),
    ]
    if d.observacoes:
        secoes.append(exportar.Secao("Observações", nota=d.observacoes))
    corpo = exportar.pdf(exportar.Relatorio(cab, secoes))
    return Response(corpo, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="RDO_{d.numero:03d}_{d.data:%Y-%m-%d}.pdf"'})


@router.put("/rdos/{rdo_id}", response_model=RdoSaida)
async def editar(rdo_id: UUID, e: RdoEntrada, ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> RdoSaida:
    """Edição antes do envio (RGR-RDO-002): só em EMITIDO/REPROVADO; data não muda; gera evento EDITADO."""
    r = await _carregar(s, rdo_id)
    estado = _estado(await _eventos(s, rdo_id))
    if not _pode(ctx, estado, r["criado_por"])["editar"]:
        raise HTTPException(409 if estado not in ("EMITIDO", "REPROVADO") else status.HTTP_403_FORBIDDEN, f"RGR-RDO-002: RDO em {estado} não se edita por este papel")
    if e.data != r["data"]:
        raise HTTPException(422, "a data do RDO não muda; emita outro RDO para outro dia")
    paradas = sum((t.paralisacao_h for t in e.turnos), start=Decimal(0))
    motivo = "; ".join(t.motivo for t in e.turnos if t.paralisacao_h > 0 and t.motivo) or None
    if paradas > 0 and not motivo:
        raise HTTPException(422, "horas paradas exigem motivo da paralisação (ck_rdo_parada)")
    prazo = (r["efetivo"] or {}).get("prazo") or await _prazo_do_dia(s, r["contrato_id"], e.data)
    efetivo = {"moi": [x.model_dump() for x in e.moi], "mod": [x.model_dump() for x in e.mod], "equipamentos": [x.model_dump() for x in e.equipamentos],
               "local_area": e.local_area, "escopo": e.escopo, "tema_dds": e.tema_dds, "responsavel_tecnico": e.responsavel_tecnico, "encarregado": e.encarregado,
               "observacoes": e.observacoes, "prazo": prazo}
    clima = {"turnos": [t.model_dump() for t in e.turnos], "pluviometria_mm": str(e.pluviometria_mm)}
    await s.execute(text("""
        UPDATE plan.rdo SET clima = CAST(:clima AS jsonb), horas_paradas = :hp, motivo_parada = :mot, efetivo = CAST(:ef AS jsonb),
               servicos = CAST(:sv AS jsonb), ocorrencias = CAST(:oc AS jsonb) WHERE id = :id"""), {
        "clima": json.dumps(_js(clima)), "hp": paradas, "mot": motivo, "ef": json.dumps(_js(efetivo)), "sv": json.dumps(_js([x.model_dump() for x in e.servicos])),
        "oc": json.dumps(_js([x.model_dump() for x in e.ocorrencias])), "id": rdo_id})
    await s.execute(text("INSERT INTO plan.rdo_evento (contrato_id, rdo_id, evento, parecer, por, papel) VALUES (:c, :r, 'EDITADO', 'conteúdo revisado antes do envio', :por, :papel)"),
                    {"c": r["contrato_id"], "r": rdo_id, "por": ctx.nome, "papel": ctx.papel})
    if e.enviar:
        await s.execute(text("INSERT INTO plan.rdo_evento (contrato_id, rdo_id, evento, parecer, por, papel) VALUES (:c, :r, 'ENVIADO', 'enviado após revisão', :por, :papel)"),
                        {"c": r["contrato_id"], "r": rdo_id, "por": ctx.nome, "papel": ctx.papel})
    return await _saida(s, await _carregar(s, rdo_id), ctx)
