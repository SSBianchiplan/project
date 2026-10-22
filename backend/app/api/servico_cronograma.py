"""Monta o cronograma a partir do banco, aplica alterações propostas e calcula.

Toda regra está em app.dominio.cronograma; aqui só há tradução de linhas em
objetos de domínio e de resultado em schema de saída.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..dominio import avanco as dom_avanco
from ..dominio import cronograma as cr
from ..dominio.tipos import ZERO, No, TipoNo, q
from ..infra import cronograma_repo as repo
from . import schemas_cronograma as sc

HORIZONTE = 420


@dataclass
class Plano:
    contrato: repo.LinhaContrato
    cal: cr.Calendario
    nos: list[dict[str, Any]]
    atividades: dict[str, dict[str, Any]]     # por edt
    vinculos: list[dict[str, Any]]
    progresso: dict[str, Decimal]
    baseline: dict[str, Any] | None


async def carregar(s: AsyncSession, contrato_id: UUID) -> Plano:
    c = await repo.contrato(s, contrato_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contrato não encontrado")
    feriados = frozenset(await repo.feriados(s, contrato_id))
    cal = cr.Calendario(c.data_inicio, feriados, HORIZONTE)
    ativs = {a["edt"]: a for a in await repo.atividades(s, contrato_id)}
    return Plano(
        contrato=c, cal=cal, nos=await repo.nos(s, contrato_id), atividades=ativs,
        vinculos=await repo.vinculos(s, contrato_id),
        progresso=await repo.medicao_vigente(s, contrato_id),
        baseline=await repo.baseline_vigente(s, contrato_id),
    )


# ------------------------------------------------------------- alterações

CAMPOS_ATIVIDADE = (
    "descricao", "restricao", "restricao_data", "restricao_justificativa", "deadline",
    "calendario", "disciplina", "responsavel", "prioridade", "notas", "situacao", "mod_qtd",
    "moi_qtd", "equipamentos_qtd", "hh_estimadas", "turno", "subcontratada",
    "criterio_medicao", "unidade", "qtd_prevista", "qtd_realizada", "pontos", "memoria_peso",
)


def aplicar_alteracao(linha: dict[str, Any], alt: sc.AtividadeAlteracao) -> dict[str, Any]:
    """Devolve uma cópia da linha com a alteração aplicada. Não escreve."""
    nova = dict(linha)
    definidos = alt.model_fields_set
    for campo in CAMPOS_ATIVIDADE:
        if campo in definidos:
            nova[campo] = getattr(alt, campo)
    if "duracao" in definidos and alt.duracao is not None:
        nova["duracao_dias"] = alt.duracao
    if "marco" in definidos and alt.marco is not None:
        nova["tipo"] = "MARCO" if alt.marco else "ATIVIDADE"
        if alt.marco:
            nova["duracao_dias"] = 0
            nova["pontos"] = ZERO
    if alt.limpar_deadline:
        nova["deadline"] = None
    if alt.restricao == "ASAP":
        nova["restricao_data"] = None
    if alt.excecao is not None:
        nova["excecao_id"] = nova.get("excecao_id") or "pendente"
        nova["excecao_documento"] = alt.excecao.documento
        nova["excecao_por"] = alt.excecao.autorizada_por
        nova["excecao_just"] = alt.excecao.justificativa
    if alt.remover_excecao:
        nova["excecao_id"] = None
    return nova


def com_alteracoes(plano: Plano, entrada: sc.SimulacaoEntrada) -> Plano:
    ativs = dict(plano.atividades)
    for alt in entrada.atividades:
        if not alt.edt or alt.edt not in ativs:
            raise HTTPException(422, f"atividade {alt.edt!r} não existe no contrato")
        ativs[alt.edt] = aplicar_alteracao(ativs[alt.edt], alt)
    vincs = plano.vinculos
    if entrada.vinculos is not None:
        vincs = [{"pred_edt": v.pred_edt, "suc_edt": v.suc_edt, "tipo": v.tipo, "lag": v.lag}
                 for v in entrada.vinculos]
    return replace(plano, atividades=ativs, vinculos=vincs)


# --------------------------------------------------------------- cálculo

def _plano_dominio(plano: Plano) -> tuple[list[cr.AtividadePlano], list[cr.VinculoCPM]]:
    ativs = []
    for a in plano.atividades.values():
        marco = a["tipo"] == "MARCO"
        ativs.append(cr.AtividadePlano(
            edt=a["edt"], descricao=a["descricao"], marco=marco,
            duracao=0 if marco else int(a["duracao_dias"]),
            pontos=Decimal(a["pontos"]), restricao=a["restricao"],
            restricao_data=a["restricao_data"], deadline=a["deadline"],
            excecao=a.get("excecao_id") is not None, memoria_peso=a.get("memoria_peso") or "",
        ))
    vincs = [cr.VinculoCPM(v["pred_edt"], v["suc_edt"], v["tipo"], int(v["lag"]))
             for v in plano.vinculos]
    return ativs, vincs


def calcular(plano: Plano) -> tuple[cr.ResultadoCPM, list[cr.AchadoCronograma]]:
    """Levanta HTTP 422 quando a rede é inválida (ciclo, lag)."""
    try:
        ativs, vincs = _plano_dominio(plano)
        cpm = cr.calcular(cr.montar_entrada(ativs, plano.cal), vincs)
    except cr.ErroCronograma as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    achs = cr.achados(ativs, vincs, cpm, plano.cal, plano.contrato.base_ponderacao)
    return cpm, achs


def cpm_baseline(plano: Plano) -> cr.ResultadoCPM | None:
    """Datas da baseline vigente, em índices do mesmo calendário."""
    if not plano.baseline or not plano.baseline["datas"]:
        return None
    datas: dict[str, cr.Datas] = {}
    for edt, d in plano.baseline["datas"].items():
        ie, te = plano.cal.indice_de(d["inicio"]), plano.cal.indice_de(d["termino"])
        datas[edt] = cr.Datas(edt, ie, te, ie, te, 0, 0, False)
    fim = max(d.te for d in datas.values())
    return cr.ResultadoCPM(datas, fim, tuple(datas), ())


def nos_dominio(plano: Plano) -> list[No]:
    return [No(n["edt"], n["descricao"], int(n["nivel"]), TipoNo(n["tipo"]), bool(n["folha"]),
               Decimal(n["pontos"])) for n in plano.nos]


def pontos_correntes(plano: Plano) -> list[No]:
    """Nós com os pontos das atividades (que podem ter sido alterados na simulação)."""
    out = []
    for n in plano.nos:
        pontos = Decimal(n["pontos"])
        if n["edt"] in plano.atividades:
            pontos = Decimal(plano.atividades[n["edt"]]["pontos"])
        out.append(No(n["edt"], n["descricao"], int(n["nivel"]), TipoNo(n["tipo"]),
                      bool(n["folha"]), pontos if n["folha"] else ZERO))
    return out


def avanco_ponderado(plano: Plano, progresso: dict[str, Decimal] | None = None) -> Decimal:
    valor, _ = dom_avanco.agregar("", pontos_correntes(plano), plano.contrato.base_ponderacao,
                                  progresso if progresso is not None else plano.progresso)
    return valor if valor is not None else ZERO


# ----------------------------------------------------------------- saída

def _marco(cal: cr.Calendario, i: int) -> sc.MarcoTemporal:
    return sc.MarcoTemporal(indice=i, data=cal.data_de(i))


def montar_saida(
    plano: Plano, cpm: cr.ResultadoCPM, achs: list[cr.AchadoCronograma], data_status: date,
    impacto: sc.ImpactoSaida | None = None,
) -> sc.CronogramaSaida:
    cal = plano.cal
    bl = cpm_baseline(plano)
    atividades: list[sc.AtividadeSaida] = []
    for edt, a in plano.atividades.items():
        r = cpm.atividades[edt]
        b = bl.atividades.get(edt) if bl else None
        exc = None
        if a.get("excecao_id") is not None:
            exc = sc.ExcecaoSaida(
                id=a["excecao_id"] if isinstance(a["excecao_id"], UUID) else UUID(int=0),
                documento=a.get("excecao_documento") or "",
                autorizada_por=a.get("excecao_por") or "",
                justificativa=a.get("excecao_just") or "")
        atividades.append(sc.AtividadeSaida(
            id=a["id"], eap_no_id=a["eap_no_id"], edt=edt,
            fase_edt=".".join(edt.split(".")[:2]),
            descricao=a["descricao"], marco=a["tipo"] == "MARCO",
            duracao=0 if a["tipo"] == "MARCO" else int(a["duracao_dias"]),
            pontos=Decimal(a["pontos"]), memoria_peso=a.get("memoria_peso") or "",
            codigo_externo=a.get("codigo_externo"), restricao=a["restricao"],
            restricao_data=a["restricao_data"],
            restricao_justificativa=a.get("restricao_justificativa") or "",
            deadline=a["deadline"], calendario=a["calendario"], disciplina=a["disciplina"],
            responsavel=a["responsavel"], prioridade=int(a["prioridade"]), notas=a["notas"],
            situacao=a["situacao"], mod_qtd=int(a["mod_qtd"]), moi_qtd=int(a["moi_qtd"]),
            equipamentos_qtd=int(a["equipamentos_qtd"]), hh_estimadas=Decimal(a["hh_estimadas"]),
            turno=int(a["turno"]), subcontratada=a["subcontratada"],
            criterio_medicao=a.get("criterio_medicao"), unidade=a["unidade"],
            qtd_prevista=Decimal(a["qtd_prevista"]), qtd_realizada=Decimal(a["qtd_realizada"]),
            excecao=exc, progresso=plano.progresso.get(edt, ZERO),
            datas=sc.DatasSaida(
                ie=r.ie, te=r.te, it=r.it, tt=r.tt, folga_total=r.folga_total,
                folga_livre=r.folga_livre, critica=r.critica,
                inicio=cal.data_de(r.ie), termino=cal.data_de(r.te),
                inicio_tarde=cal.data_de(r.it), termino_tarde=cal.data_de(r.tt)),
            baseline=sc.BaselineDatasSaida(
                inicio=cal.data_de(b.ie), termino=cal.data_de(b.te), ie=b.ie, te=b.te)
            if b else None,
        ))
    c = plano.contrato
    return sc.CronogramaSaida(
        contrato=sc.ContratoSaida(
            id=c.id, codigo=c.codigo, empreendimento=c.empreendimento, contratante=c.contratante,
            contratada=c.contratada, base_ponderacao=c.base_ponderacao,
            data_inicio=c.data_inicio, termino_contratual=c.termino_contratual),
        calendario=sc.CalendarioSaida(inicio=cal.inicio, feriados=sorted(cal.feriados),
                                      eixo=list(cal.eixo)),
        data_status=data_status,
        nos=[sc.NoSaida(id=n["id"], edt=n["edt"], parent_edt=n["parent_edt"],
                        descricao=n["descricao"], nivel=int(n["nivel"]), tipo=n["tipo"],
                        folha=bool(n["folha"]), pontos=Decimal(n["pontos"]),
                        memoria_peso=n["memoria_peso"] or "") for n in plano.nos],
        atividades=atividades,
        vinculos=[sc.VinculoSaida(pred_edt=v["pred_edt"], suc_edt=v["suc_edt"], tipo=v["tipo"],
                                  lag=int(v["lag"])) for v in plano.vinculos],
        fim=_marco(cal, cpm.fim),
        fim_baseline=_marco(cal, bl.fim) if bl else None,
        baseline_numero=plano.baseline["numero"] if plano.baseline else None,
        criticas=list(cpm.criticas),
        achados=[sc.AchadoSaida(invariante=a.invariante, severidade=a.severidade,
                                referencia=a.referencia, detalhe=a.detalhe) for a in achs],
        avanco_realizado=avanco_ponderado(plano),
        versao_regra=cpm.versao_regra,
        impacto=impacto,
    )


def montar_impacto(
    plano_antes: Plano, cpm_antes: cr.ResultadoCPM,
    plano_depois: Plano, cpm_depois: cr.ResultadoCPM,
) -> sc.ImpactoSaida:
    imp = cr.impacto(cpm_antes, cpm_depois)
    cal = plano_antes.cal
    return sc.ImpactoSaida(
        fim_antes=_marco(cal, imp.fim_antes), fim_depois=_marco(cal, imp.fim_depois),
        delta_dias=imp.delta_dias, entraram_criticas=list(imp.entraram_criticas),
        sairam_criticas=list(imp.sairam_criticas),
        avanco_antes=avanco_ponderado(plano_antes),
        avanco_depois=avanco_ponderado(plano_depois),
    )


def memoria_json(cpm: cr.ResultadoCPM, cal: cr.Calendario) -> list[dict[str, Any]]:
    return [{"edt": d.id, "ie": d.ie, "te": d.te, "it": d.it, "tt": d.tt,
             "inicio": cal.data_de(d.ie).isoformat(), "termino": cal.data_de(d.te).isoformat(),
             "folga_total": d.folga_total, "folga_livre": d.folga_livre, "critica": d.critica}
            for d in (cpm.atividades[i] for i in cpm.ordem)]


def achados_json(achs: list[cr.AchadoCronograma]) -> list[dict[str, str]]:
    return [{"invariante": a.invariante, "severidade": a.severidade,
             "referencia": a.referencia, "detalhe": a.detalhe} for a in achs]


# ------------------------------------------------------------------ cockpit

def _acumular(
    parcelas: dict[tuple[str, date], Decimal], semanas: list[date],
) -> dict[date, dict[str, Decimal]]:
    """{sábado: {edt: acumulado até o sábado}}."""
    por_edt: dict[str, list[tuple[date, Decimal]]] = {}
    for (edt, s), p in parcelas.items():
        por_edt.setdefault(edt, []).append((s, p))
    out: dict[date, dict[str, Decimal]] = {}
    for s in semanas:
        out[s] = {edt: q(sum((p for d, p in lst if d <= s), start=ZERO))
                  for edt, lst in por_edt.items()}
    return out


def _real_por_semana(
    lancamentos: list[dict[str, Any]], semanas: list[date],
) -> dict[date, dict[str, Decimal]]:
    out: dict[date, dict[str, Decimal]] = {}
    vigente: dict[str, Decimal] = {}
    i = 0
    for s in semanas:
        while i < len(lancamentos) and lancamentos[i]["data_corte"] <= s:
            vigente[lancamentos[i]["edt"]] = Decimal(lancamentos[i]["percentual_ac"])
            i += 1
        out[s] = dict(vigente)
    return out


def _idp(prev: Decimal, real: Decimal) -> Decimal | None:
    return None if prev == ZERO else q(real / prev)


def montar_cockpit(
    plano: Plano, cpm_vig: cr.ResultadoCPM, *, data_corte: date, referencia: str,
    semanas: list[date], previsto_bl0: dict[tuple[str, date], Decimal],
    lancamentos: list[dict[str, Any]], total_medicoes: int,
) -> sc.CockpitSaida:
    nos = nos_dominio(plano)
    base = plano.contrato.base_ponderacao
    prev_vig = cr.previsto_linear(cpm_vig, plano.cal, semanas)
    ac_bl0 = _acumular(previsto_bl0, semanas)
    ac_vig = _acumular(prev_vig, semanas)
    ac_ref = ac_bl0 if referencia == "BL0" and previsto_bl0 else ac_vig
    real = _real_por_semana(lancamentos, semanas)

    def agreg(edt: str, valores: dict[str, Decimal]) -> Decimal | None:
        v, _ = dom_avanco.agregar(edt, nos, base, valores)
        return v

    curva = []
    hist = []
    prev_ant = ZERO
    real_ant = ZERO
    for s in semanas:
        p_bl0 = agreg("", ac_bl0[s]) or ZERO
        p_vig = agreg("", ac_vig[s]) or ZERO
        p_ref = p_bl0 if ac_ref is ac_bl0 else p_vig
        r = (agreg("", real[s]) or ZERO) if s <= data_corte else None
        curva.append(sc.PontoCurva(fim_semana=s, previsto_bl0_ac=p_bl0, previsto_vigente_ac=p_vig,
                                   real_ac=r))
        hist.append(sc.BarraHistograma(fim_semana=s, previsto=q(p_ref - prev_ant),
                                       real=q(r - real_ant) if r is not None else None))
        prev_ant = p_ref
        if r is not None:
            real_ant = r

    corte = max((s for s in semanas if s <= data_corte), default=semanas[0])
    prev_c = agreg("", ac_ref[corte]) or ZERO
    real_c = agreg("", real[corte]) or ZERO
    absoluto, relativo = dom_avanco.desvios(prev_c, real_c)
    situacao = dom_avanco.classificar(prev_c, absoluto, relativo)
    idp = _idp(prev_c, real_c)
    bl = cpm_baseline(plano)
    fim_ref = bl.fim if (referencia == "BL0" and bl) else cpm_vig.fim
    proj = cr.projecao_termino(fim_ref, idp, len(plano.cal))

    fases = []
    for n in plano.nos:
        if int(n["nivel"]) != 2:
            continue
        folhas = dom_avanco.descendentes(n["edt"], nos)
        peso = q(sum((f.pontos for f in folhas), start=ZERO) / base)
        p = agreg(n["edt"], ac_ref[corte])
        r = agreg(n["edt"], real[corte])
        if p is None:
            fases.append(sc.FaseCockpit(edt=n["edt"], descricao=n["descricao"], peso=peso,
                                        previsto_ac=None, real_ac=None, desvio_abs=None,
                                        desvio_rel=None, idp=None, situacao="SEM_PESO",
                                        primeira_folha_edt=None))
            continue
        r = r or ZERO
        a, rl = dom_avanco.desvios(p, r)
        fases.append(sc.FaseCockpit(
            edt=n["edt"], descricao=n["descricao"], peso=peso, previsto_ac=p, real_ac=r,
            desvio_abs=a, desvio_rel=rl, idp=_idp(p, r),
            situacao=dom_avanco.classificar(p, a, rl).value,
            primeira_folha_edt=folhas[0].edt if folhas else None))

    return sc.CockpitSaida(
        data_corte=corte, referencia=referencia, semanas=semanas,
        cartoes=sc.CartoesCockpit(
            previsto_ac=prev_c, real_ac=real_c, desvio_abs=absoluto, desvio_rel=relativo,
            situacao=situacao.value, idp=idp, termino_referencia=plano.cal.data_de(fim_ref),
            termino_projetado=plano.cal.data_de(proj), delta_projetado_dias=proj - fim_ref,
            medicoes=total_medicoes),
        fases=fases, curva=curva, histograma=hist,
    )
