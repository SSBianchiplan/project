"""Monta relatórios (Status Report e exportações) a partir dos painéis já calculados.

Nada é recalculado aqui: cronograma, cockpit, recursos e custos vêm dos
mesmos serviços das telas, então o PDF mostra exatamente o que a tela mostra.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..dominio.tipos import ZERO
from ..infra import cronograma_repo as repo
from ..infra.exportar import Cabecalho, Grafico, Relatorio, Secao, Serie, moeda, pct
from . import servico_cronograma as svc
from .rotas_apontamentos import montar_painel_apontamentos, montar_painel_pr
from .rotas_cronograma import montar_cockpit
from .rotas_custos import montar_painel_custos
from .rotas_recursos import montar_painel_recursos

ROTULO_SIT = {"NAO_INICIADO": "não iniciado", "EM_DIA": "em dia", "ATENCAO": "atenção",
              "PLANO_DE_RECUPERACAO": "plano de recuperação", "PLANO_DE_ACAO": "plano de ação", "SEM_PESO": "sem peso"}


def _num(v: Decimal | None) -> str:
    return "—" if v is None else f"{float(v):.2f}".replace(".", ",")


async def status_report(s: AsyncSession, contrato_id: UUID, data_corte: date) -> dict:
    """JSON consolidado do estado do projeto na data de corte."""
    plano = await svc.carregar(s, contrato_id)
    cpm, achs = svc.calcular(plano)
    saida = svc.montar_saida(plano, cpm, achs, data_corte)
    ck = await montar_cockpit(s, contrato_id, data_corte, "BL0")
    rec = await montar_painel_recursos(s, contrato_id)
    cst = await montar_painel_custos(s, contrato_id, data_corte)
    meds = await repo.medicoes(s, contrato_id, 15)

    ativs = [a for a in saida.atividades if not a.marco]
    vencidas = [a for a in ativs if a.progresso < 1 and a.datas.termino < data_corte]
    criticas = [a for a in ativs if a.datas.critica and a.progresso < 1]
    proximos = sorted([a for a in saida.atividades if a.marco and a.datas.inicio >= data_corte], key=lambda a: a.datas.inicio)[:6]
    delta = (saida.fim.indice - saida.fim_baseline.indice) if saida.fim_baseline else 0
    c = ck.cartoes
    pr = await montar_painel_pr(s, contrato_id, data_corte)
    return {
        "contrato": saida.contrato.model_dump(mode="json"),
        "data_corte": data_corte.isoformat(),
        "baseline_numero": saida.baseline_numero,
        "prazo": {"termino_vigente": saida.fim.data.isoformat(), "termino_baseline": saida.fim_baseline.data.isoformat() if saida.fim_baseline else None,
                  "delta_dias": delta, "termino_projetado": c.termino_projetado.isoformat(), "delta_projetado": c.delta_projetado_dias,
                  "termino_contratual": saida.contrato.termino_contratual.isoformat()},
        "avanco": {"previsto": str(c.previsto_ac), "real": str(c.real_ac), "desvio_abs": str(c.desvio_abs) if c.desvio_abs is not None else None,
                   "desvio_rel": str(c.desvio_rel) if c.desvio_rel is not None else None, "situacao": c.situacao, "idp": str(c.idp) if c.idp is not None else None},
        "tarefas": {"total": len(ativs), "concluidas": sum(1 for a in ativs if a.progresso >= 1), "andamento": sum(1 for a in ativs if 0 < a.progresso < 1),
                    "nao_iniciadas": sum(1 for a in ativs if a.progresso == 0), "vencidas": len(vencidas), "criticas": len(criticas)},
        "custos": cst.evm.model_dump(mode="json"),
        "recursos": {"superalocacoes": len(rec.achados), "horas_total": str(rec.horas_total), "custo_total": str(rec.custo_total),
                     "recursos": len(rec.recursos), "achados": [a.model_dump(mode="json") for a in rec.achados[:8]]},
        "achados": [a.model_dump(mode="json") for a in saida.achados],
        "fases": [f.model_dump(mode="json") for f in ck.fases],
        "curva": [p.model_dump(mode="json") for p in ck.curva],
        "curva_custo": [p.model_dump(mode="json") for p in cst.curva],
        "vencidas": [{"edt": a.edt, "descricao": a.descricao, "termino": a.datas.termino.isoformat(), "progresso": str(a.progresso), "responsavel": a.responsavel} for a in vencidas[:12]],
        "criticas": [{"edt": a.edt, "descricao": a.descricao, "inicio": a.datas.inicio.isoformat(), "termino": a.datas.termino.isoformat(), "progresso": str(a.progresso), "responsavel": a.responsavel} for a in criticas[:12]],
        "proximos_marcos": [{"edt": a.edt, "descricao": a.descricao, "data": a.datas.inicio.isoformat(), "critica": a.datas.critica} for a in proximos],
        "medicoes_recentes": [{"data_corte": m["data_corte"].isoformat(), "edt": m["edt"], "descricao": m["descricao"], "tipo": m["tipo"], "percentual_ac": str(m["percentual_ac"]), "evidencia": m["evidencia"], "criado_por": m["criado_por"]} for m in meds],
        "categorias_custo": [c_.model_dump(mode="json") for c_ in cst.categorias],
        "custos_fases": [n.model_dump(mode="json") for n in cst.nos if n.nivel <= 2],
        "coerencia": {"indice": str(pr.indice_coerencia), "alertas": sum(1 for a in pr.achados if a.severidade == "ALERTA"),
                      "atencoes": sum(1 for a in pr.achados if a.severidade == "ATENCAO"), "achados": [a.model_dump(mode="json") for a in pr.achados[:10]],
                      "dimensoes": [d.model_dump(mode="json") for d in pr.dimensoes],
                      "hh_previstas": str(sum((x.hh_previstas for x in pr.atividades), start=ZERO)), "hh_apontadas": str(sum((x.hh_apontadas for x in pr.atividades), start=ZERO))},
    }


def _cabecalho(sr: dict, titulo: str, emitido_por: str, paisagem: bool) -> Cabecalho:
    c = sr["contrato"]
    return Cabecalho(titulo=titulo, contrato_codigo=c["codigo"], empreendimento=c["empreendimento"], contratante=c["contratante"],
                     contratada=c["contratada"], data_corte=date.fromisoformat(sr["data_corte"]), emitido_por=emitido_por,
                     baseline=str(sr["baseline_numero"]) if sr["baseline_numero"] is not None else "", paisagem=paisagem)


def _d(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


def relatorio_status(sr: dict, emitido_por: str) -> Relatorio:
    p, av, t, cst, r = sr["prazo"], sr["avanco"], sr["tarefas"], sr["custos"], sr["recursos"]
    secoes = [
        Secao("Resumo executivo", cartoes=[
            ("Avanço realizado", pct(_d(av["real"]))), ("Avanço previsto", pct(_d(av["previsto"]))),
            ("Desvio", f"{pct(_d(av['desvio_abs']))} · {ROTULO_SIT.get(av['situacao'], av['situacao'])}"),
            ("IDP (prazo)", _num(_d(av["idp"]))), ("IDC (custo)", _num(_d(cst["idc"]))),
            ("Término vigente", date.fromisoformat(p["termino_vigente"]).strftime("%d/%m/%Y")),
            ("Término BL" + str(sr["baseline_numero"] or 0), date.fromisoformat(p["termino_baseline"]).strftime("%d/%m/%Y") if p["termino_baseline"] else "—"),
            ("Desvio de prazo", f"{p['delta_dias']:+d} dias úteis"), ("Término projetado (ritmo)", date.fromisoformat(p["termino_projetado"]).strftime("%d/%m/%Y")),
            ("Tarefas", f"{t['concluidas']}/{t['total']} concluídas · {t['vencidas']} vencidas"),
            ("Orçamento (ONT)", moeda(_d(cst["bac"]))), ("Estimativa no término", moeda(_d(cst["ent"]))),
        ]),
        Secao("Curva S — avanço físico", grafico=Grafico("previsto BL0 · vigente · realizado", [
            Serie("previsto BL0", [(date.fromisoformat(x["fim_semana"]), Decimal(x["previsto_bl0_ac"])) for x in sr["curva"]], "#b9bfc2"),
            Serie("vigente", [(date.fromisoformat(x["fim_semana"]), Decimal(x["previsto_vigente_ac"])) for x in sr["curva"]], "#7d8286", True),
            Serie("realizado", [(date.fromisoformat(x["fim_semana"]), _d(x["real_ac"])) for x in sr["curva"]], "#a11d1d"),
        ], "pct")),
        Secao("Avanço por fase — gráfico", grafico=Grafico("previsto × real por fase", tipo="barras", formato_y="pct",
              barras=[(f["edt"], [("previsto", Decimal(str(f["previsto_ac"] or 0)), "#b9bfc2"), ("real", Decimal(str(f["real_ac"] or 0)), "#2f7d4f")]) for f in sr["fases"]])),
        Secao("Avanço por fase", cabecalhos=["EDT", "Fase", "Peso", "Previsto", "Real", "Desvio", "IDP", "Situação"],
              linhas=[[f["edt"], f["descricao"], pct(_d(f["peso"])), pct(_d(f["previsto_ac"])), pct(_d(f["real_ac"])), pct(_d(f["desvio_abs"])),
                       _num(_d(f["idp"])), ROTULO_SIT.get(f["situacao"], f["situacao"])] for f in sr["fases"]],
              larguras=[0.6, 3, 0.8, 0.9, 0.9, 0.9, 0.7, 1.6]),
        Secao("Custos — valor agregado", cartoes=[
            ("ONT", moeda(_d(cst["bac"]))), ("VP", moeda(_d(cst["vp"]))), ("VA", moeda(_d(cst["va"]))), ("CR", moeda(_d(cst["cr"]))),
            ("VC", moeda(_d(cst["vc"]))), ("IDC", _num(_d(cst["idc"]))), ("ENT", moeda(_d(cst["ent"]))), ("VNT", moeda(_d(cst["vac"])))],
            grafico=Grafico("planejado · valor agregado · real", [
                Serie("planejado (VP)", [(date.fromisoformat(x["fim_semana"]), Decimal(x["vp_ac"])) for x in sr["curva_custo"]], "#b9bfc2"),
                Serie("valor agregado (VA)", [(date.fromisoformat(x["fim_semana"]), Decimal(x["va_ac"])) for x in sr["curva_custo"] if x["fim_semana"] <= sr["data_corte"]], "#2f7d4f"),
                Serie("custo real (CR)", [(date.fromisoformat(x["fim_semana"]), _d(x["cr_ac"])) for x in sr["curva_custo"]], "#a11d1d"),
            ], "moeda") if any(Decimal(x["vp_ac"]) > 0 for x in sr["curva_custo"]) else None,
            cabecalhos=["EDT", "Nó", "Orçamento", "VA", "CR", "IDC"],
            linhas=[[n["edt"], n["descricao"], moeda(_d(n["bac"])), moeda(_d(n["va"])), moeda(_d(n["cr"])), _num(_d(n["idc"]))] for n in sr["custos_fases"]],
            larguras=[0.6, 3, 1.3, 1.3, 1.3, 0.7]),
        Secao("Atividades vencidas (término anterior à data de corte sem conclusão)",
              cabecalhos=["EDT", "Atividade", "Término", "Avanço", "Responsável"],
              linhas=[[a["edt"], a["descricao"], date.fromisoformat(a["termino"]), pct(_d(a["progresso"]), 0), a["responsavel"] or "—"] for a in sr["vencidas"]] or [["—", "nenhuma", None, "", ""]],
              larguras=[0.7, 3.5, 1, 0.8, 1.6]),
        Secao("Caminho crítico em aberto", cabecalhos=["EDT", "Atividade", "Início", "Término", "Avanço", "Responsável"],
              linhas=[[a["edt"], a["descricao"], date.fromisoformat(a["inicio"]), date.fromisoformat(a["termino"]), pct(_d(a["progresso"]), 0), a["responsavel"] or "—"] for a in sr["criticas"]],
              larguras=[0.7, 3.5, 1, 1, 0.8, 1.6]),
        Secao("Próximos marcos", cabecalhos=["EDT", "Marco", "Data", "Crítico"],
              linhas=[[m["edt"], m["descricao"], date.fromisoformat(m["data"]), "sim" if m["critica"] else "não"] for m in sr["proximos_marcos"]] or [["—", "nenhum", None, ""]],
              larguras=[0.7, 4, 1, 0.8]),
        Secao("Recursos", cartoes=[("Recursos cadastrados", str(r["recursos"])), ("Horas mobilizadas", _num(_d(r["horas_total"]))),
                                  ("Custo de recursos", moeda(_d(r["custo_total"]))), ("Superalocações", str(r["superalocacoes"]))],
              cabecalhos=["Invariante", "Recurso", "Detalhe"], linhas=[[a["invariante"], a["referencia"], a["detalhe"]] for a in r["achados"]] or [["—", "nenhuma superalocação", ""]],
              larguras=[1, 1, 5]),
        Secao("Coerência previsto × realizado (RGR-COE-001)", cartoes=[("Índice de coerência", _num(_d(sr["coerencia"]["indice"]))), ("Alertas", str(sr["coerencia"]["alertas"])),
                                                                     ("Atenções", str(sr["coerencia"]["atencoes"])), ("hh previstas", _num(_d(sr["coerencia"]["hh_previstas"]))), ("hh apontadas", _num(_d(sr["coerencia"]["hh_apontadas"])))],
              cabecalhos=["Regra", "Severidade", "EDT", "Dimensão", "Diagnóstico"],
              linhas=[[a["regra"], a["severidade"].lower(), a["referencia"], a["dimensao"].lower(), a["detalhe"]] for a in sr["coerencia"]["achados"]] or [["—", "", "", "", "previsto e realizado coerentes"]],
              larguras=[1.6, 0.8, 0.6, 0.8, 4.2]),
        Secao("Achados de invariante (LVP)", cabecalhos=["Invariante", "Severidade", "Referência", "Detalhe"],
              linhas=[[a["invariante"], a["severidade"], a["referencia"], a["detalhe"]] for a in sr["achados"]] or [["—", "", "", "emissão sem achados"]],
              larguras=[1, 0.9, 1, 5]),
        Secao("Medições recentes", cabecalhos=["Corte", "EDT", "Atividade", "Tipo", "Acumulado", "Evidência", "Por"],
              linhas=[[date.fromisoformat(m["data_corte"]), m["edt"], m["descricao"], m["tipo"].lower(), pct(_d(m["percentual_ac"])), m["evidencia"], m["criado_por"]] for m in sr["medicoes_recentes"]],
              larguras=[0.9, 0.6, 2.6, 0.8, 0.9, 2.4, 1]),
    ]
    return Relatorio(_cabecalho(sr, "Status Report", emitido_por, paisagem=False), secoes)


async def relatorio_cronograma(s: AsyncSession, contrato_id: UUID, data_corte: date, emitido_por: str) -> Relatorio:
    plano = await svc.carregar(s, contrato_id)
    cpm, achs = svc.calcular(plano)
    sd = svc.montar_saida(plano, cpm, achs, data_corte)
    sr = {"contrato": sd.contrato.model_dump(mode="json"), "data_corte": data_corte.isoformat(), "baseline_numero": sd.baseline_numero}
    linhas = []
    for n in sd.nos:
        a = next((x for x in sd.atividades if x.edt == n.edt), None)
        if a:
            linhas.append([n.edt, ("   " * (n.nivel - 1)) + a.descricao, "marco" if a.marco else a.duracao, a.datas.inicio, a.datas.termino,
                           a.baseline.inicio if a.baseline else None, a.baseline.termino if a.baseline else None,
                           a.datas.folga_total, "sim" if a.datas.critica else "", pct(a.progresso, 0), a.pontos, a.responsavel, a.restricao if a.restricao != "ASAP" else ""])
        else:
            linhas.append([n.edt, ("   " * (n.nivel - 1)) + n.descricao, "", None, None, None, None, "", "", "", "", "", ""])
    vinc = [[v.pred_edt, v.suc_edt, v.tipo, v.lag] for v in sd.vinculos]
    cores = ["#3b6ea5", "#2f7d4f", "#8a6d1f", "#7a4fa3", "#b4560f", "#1f8a8a", "#a13d7a", "#6b7175"]
    fases = [n.edt for n in sd.nos if n.nivel == 2]
    gantt = []
    for n in sd.nos[:90]:
        a = next((x for x in sd.atividades if x.edt == n.edt), None)
        fase = ".".join(n.edt.split(".")[:2])
        cor = cores[fases.index(fase) % len(cores)] if fase in fases else "#6b7175"
        if a:
            rot = ("  " * (n.nivel - 1)) + f"{n.edt} {a.descricao}" + (" ◆" if a.marco else "")
            gantt.append((rot, a.datas.inicio, a.datas.termino, bool(a.datas.critica), cor, Decimal(a.progresso)))
        else:
            desc = [x for x in sd.atividades if x.edt.startswith(n.edt + ".")]
            if desc:
                gantt.append(("*" + ("  " * (n.nivel - 1)) + f"{n.edt} {n.descricao}", min(x.datas.inicio for x in desc), max(x.datas.termino for x in desc), False, "#2d3336", Decimal(0)))
    return Relatorio(_cabecalho(sr, "Cronograma — CPM vigente", emitido_por, paisagem=True), [
        Secao("Gantt do cronograma vigente", grafico=Grafico("barras por fase · crítica em vermelho · sombra = avanço medido", tipo="gantt", gantt=gantt, marcador=data_corte),
              nota="Linhas em negrito são resumos (raiz, fases e pacotes); losango é marco. Até 90 linhas; a tabela abaixo traz a EAP completa."),
        Secao("Síntese", cartoes=[("Término vigente", sd.fim.data.strftime("%d/%m/%Y")), ("Término BL", sd.fim_baseline.data.strftime("%d/%m/%Y") if sd.fim_baseline else "—"),
                                  ("Atividades críticas", str(len(sd.criticas))), ("Avanço realizado", pct(sd.avanco_realizado)), ("Achados", str(len(sd.achados))), ("Regra", sd.versao_regra)]),
        Secao("Estrutura e datas", cabecalhos=["EDT", "Atividade", "Dur.", "Início", "Término", "Início BL", "Término BL", "Folga", "Crít.", "Avanço", "Pontos", "Responsável", "Restr."],
              linhas=linhas, larguras=[0.7, 3.2, 0.5, 0.9, 0.9, 0.9, 0.9, 0.5, 0.5, 0.6, 0.9, 1.4, 0.6]),
        Secao("Rede de precedência", cabecalhos=["Predecessora", "Sucessora", "Tipo", "Lag (dias)"], linhas=vinc, larguras=[1, 1, 0.5, 0.6]),
        Secao("Achados de invariante", cabecalhos=["Invariante", "Severidade", "Referência", "Detalhe"],
              linhas=[[a.invariante, a.severidade, a.referencia, a.detalhe] for a in sd.achados] or [["—", "", "", "emissão sem achados"]], larguras=[1, 0.8, 1, 5]),
    ])


async def relatorio_medicoes(s: AsyncSession, contrato_id: UUID, data_corte: date, emitido_por: str) -> Relatorio:
    c = await repo.contrato(s, contrato_id)
    assert c is not None
    meds = await repo.medicoes(s, contrato_id, 500)
    sr = {"contrato": {"codigo": c.codigo, "empreendimento": c.empreendimento, "contratante": c.contratante, "contratada": c.contratada}, "data_corte": data_corte.isoformat(), "baseline_numero": None}
    return Relatorio(_cabecalho(sr, "Registro de medições", emitido_por, paisagem=True), [
        Secao("Lançamentos (append-only, hash encadeado)", cabecalhos=["Corte", "EDT", "Atividade", "Tipo", "Acumulado", "Evidência", "Justificativa", "Por", "Em", "Hash"],
              linhas=[[m["data_corte"], m["edt"], m["descricao"], m["tipo"].lower(), pct(Decimal(m["percentual_ac"])), m["evidencia"], m["justificativa"] or "", m["criado_por"], m["criado_em"], (m["hash"] or "")[:12]] for m in meds],
              larguras=[0.8, 0.6, 2.4, 0.7, 0.8, 2, 1.6, 1, 1.2, 1], nota="Estorno grava o valor restaurado e aponta para o lançamento original. Nada é editado."),
    ])


async def relatorio_eap(s: AsyncSession, contrato_id: UUID, data_corte: date, emitido_por: str) -> Relatorio:
    plano = await svc.carregar(s, contrato_id)
    sr = {"contrato": {"codigo": plano.contrato.codigo, "empreendimento": plano.contrato.empreendimento, "contratante": plano.contrato.contratante, "contratada": plano.contrato.contratada}, "data_corte": data_corte.isoformat(), "baseline_numero": None}
    base = plano.contrato.base_ponderacao
    linhas = []
    for n in plano.nos:
        pontos = Decimal(n["pontos"]) if n["folha"] else sum((Decimal(a["pontos"]) for e, a in plano.atividades.items() if e.startswith(n["edt"] + ".")), start=ZERO)
        prog = plano.progresso.get(n["edt"])
        linhas.append([n["edt"], ("   " * (int(n["nivel"]) - 1)) + n["descricao"], int(n["nivel"]), n["tipo"].lower(), pontos, pct(pontos / base), pct(prog) if prog is not None else "", n["memoria_peso"] or ""])
    soma = sum((Decimal(n["pontos"]) for n in plano.nos if n["folha"]), start=ZERO)
    return Relatorio(_cabecalho(sr, "EAP e ponderação", emitido_por, paisagem=True), [
        Secao("Fechamento", cartoes=[("Base de ponderação", f"{base:,.0f}".replace(",", ".")), ("Soma das folhas", f"{soma:,.0f}".replace(",", ".")), ("Diferença", f"{soma - base:,.0f}".replace(",", ".")), ("Folhas ponderadas", str(sum(1 for n in plano.nos if n["folha"] and Decimal(n["pontos"]) > 0)))]),
        Secao("Nós", cabecalhos=["EDT", "Descrição", "Nível", "Tipo", "Pontos", "Peso", "Avanço", "Memória de cálculo do peso"], linhas=linhas, larguras=[0.7, 3, 0.5, 0.8, 1, 0.7, 0.7, 3]),
    ])


async def relatorio_recursos(s: AsyncSession, contrato_id: UUID, data_corte: date, emitido_por: str) -> Relatorio:
    c = await repo.contrato(s, contrato_id)
    assert c is not None
    p = await montar_painel_recursos(s, contrato_id)
    sr = {"contrato": {"codigo": c.codigo, "empreendimento": c.empreendimento, "contratante": c.contratante, "contratada": c.contratada}, "data_corte": data_corte.isoformat(), "baseline_numero": None}
    semanas = sorted({w.fim_semana for h in p.histogramas for w in h.semanas if w.pico_dia > 0})
    carga = [[h.recurso.codigo, h.recurso.nome, h.recurso.tipo, h.recurso.capacidade_dia] + [next((w.pico_dia for w in h.semanas if w.fim_semana == sd), ZERO) for sd in semanas] for h in p.histogramas if h.total_unidade_dia > 0]
    top = sorted([h for h in p.histogramas if h.total_unidade_dia > 0], key=lambda h: -h.total_horas)[:8]
    barras_rec = [(h.recurso.codigo, [("horas (hh)", h.total_horas, "#3b6ea5")]) for h in top]
    picos: dict = {}
    for h in p.histogramas:
        for w in h.semanas:
            picos[w.fim_semana] = picos.get(w.fim_semana, Decimal(0)) + w.pico_dia
    sem_ativas = [k for k in sorted(picos) if picos[k] > 0]
    passo = max(1, len(sem_ativas) // 26)
    barras_sem = [(k.strftime("%d/%m"), [("pico total (un/dia)", picos[k], "#b4560f")]) for k in sem_ativas[::passo]]
    return Relatorio(_cabecalho(sr, "Recursos — catálogo, alocação e carga", emitido_por, paisagem=True), [
        Secao("Síntese", cartoes=[("Recursos", str(len(p.recursos))), ("Horas mobilizadas", _num(p.horas_total)), ("Custo de recursos", moeda(p.custo_total)), ("Superalocações", str(len([a for a in p.achados if not a.aceite]))), ("Aceitas", str(len([a for a in p.achados if a.aceite])))],
              grafico=Grafico("histograma geral — pico de unidades/dia por semana", tipo="barras", barras=barras_sem, formato_y="numero") if barras_sem else None),
        Secao("Recursos mais mobilizados (hh)", grafico=Grafico("horas por recurso", tipo="barras", barras=barras_rec, formato_y="numero") if barras_rec else None),
        Secao("Catálogo", cabecalhos=["Código", "Recurso", "Tipo", "Unidade", "Capacidade/dia", "R$/h", "h/dia", "Ativo", "Alocações"],
              linhas=[[r.codigo, r.nome, r.tipo, r.unidade, r.capacidade_dia, r.custo_hora, r.horas_dia, r.ativo, r.alocacoes] for r in p.recursos], larguras=[0.8, 2.5, 1, 0.8, 1, 0.8, 0.6, 0.5, 0.7]),
        Secao("Alocação por atividade", cabecalhos=["EDT", "Atividade", "Recurso", "Qtd/dia", "Observação"],
              linhas=[[a.edt, a.descricao, a.recurso_codigo, a.quantidade_dia, a.observacao] for a in p.alocacoes], larguras=[0.7, 3, 1, 0.7, 2.5]),
        Secao("Carga de trabalho — pico diário por semana", cabecalhos=["Código", "Recurso", "Tipo", "Cap."] + [sd.strftime("%d/%m") for sd in semanas], linhas=carga) if semanas else Secao("Carga de trabalho", nota="sem alocações."),
        Secao("Superalocações (INV-REC-001)", cabecalhos=["Recurso", "Semana", "Pico", "Cap.", "Estado", "Detalhe / justificativa"],
              linhas=[[a.referencia, a.fim_semana, a.pico_dia, a.capacidade_dia, "aceita" if a.aceite else "pendente", a.aceite or ", ".join(a.atividades)] for a in p.achados] or [["—", None, "", "", "", "nenhuma"]], larguras=[0.8, 0.9, 0.5, 0.5, 0.7, 4.5]),
    ])


async def relatorio_custos(s: AsyncSession, contrato_id: UUID, data_corte: date, emitido_por: str) -> Relatorio:
    c = await repo.contrato(s, contrato_id)
    assert c is not None
    p = await montar_painel_custos(s, contrato_id, data_corte)
    e = p.evm
    sr = {"contrato": {"codigo": c.codigo, "empreendimento": c.empreendimento, "contratante": c.contratante, "contratada": c.contratada}, "data_corte": data_corte.isoformat(), "baseline_numero": None}
    return Relatorio(_cabecalho(sr, "Custos — orçamento, valor agregado e realizado", emitido_por, paisagem=True), [
        Secao("EVM", cartoes=[("ONT", moeda(e.bac)), ("VP", moeda(e.vp)), ("VA", moeda(e.va)), ("CR", moeda(e.cr)), ("VC", moeda(e.vc)), ("VPr", moeda(e.vpr)),
                              ("IDC", _num(e.idc)), ("IDP (custo)", _num(e.idp_custo)), ("ENT", moeda(e.ent)), ("ETC", moeda(e.etc)), ("VNT", moeda(e.vac))],
              grafico=Grafico("planejado · valor agregado · real", [
                  Serie("planejado (VP)", [(x.fim_semana, x.vp_ac) for x in p.curva], "#b9bfc2"),
                  Serie("valor agregado (VA)", [(x.fim_semana, x.va_ac) for x in p.curva if x.fim_semana <= p.data_corte], "#2f7d4f"),
                  Serie("custo real (CR)", [(x.fim_semana, x.cr_ac) for x in p.curva], "#a11d1d")], "moeda") if e.bac > 0 else None),
        Secao("Por nó da EAP", cabecalhos=["EDT", "Nó", "Orçamento", "VA", "CR", "IDC"], linhas=[[n.edt, ("   " * (n.nivel - 1)) + n.descricao, n.bac, n.va, n.cr, n.idc] for n in p.nos], larguras=[0.7, 3, 1.2, 1.2, 1.2, 0.6]),
        Secao("Por categoria", cabecalhos=["Categoria", "Planejado", "Realizado"], linhas=[[x.categoria, x.planejado, x.realizado] for x in p.categorias], larguras=[2, 1, 1]),
        Secao("Por atividade", cabecalhos=["EDT", "Atividade", "Início", "Término", "Itens", "Recursos", "Orçamento", "Avanço", "VA", "CR"],
              linhas=[[a.edt, a.descricao, a.inicio, a.termino, a.itens, a.recursos, a.bac, pct(a.progresso, 0), a.va, a.cr] for a in p.atividades if a.bac > 0 or a.cr != 0], larguras=[0.7, 2.6, 0.9, 0.9, 1, 1, 1, 0.6, 1, 1]),
        Secao("Itens de orçamento", cabecalhos=["EDT", "Categoria", "Descrição", "Fornecedor", "Qtd", "Un.", "Unitário", "Total"],
              linhas=[[i.edt, i.categoria, i.descricao, i.fornecedor, i.quantidade, i.unidade, i.custo_unitario, i.total] for i in p.itens], larguras=[0.6, 1, 2.6, 1.6, 0.6, 0.5, 1, 1]),
        Secao("Ledger de custo real", cabecalhos=["Data", "EDT", "Categoria", "Descrição", "Documento", "Valor", "Por"],
              linhas=[[r.data, r.edt, r.categoria, r.descricao, r.documento, r.valor, r.criado_por] for r in p.realizados], larguras=[0.8, 0.6, 1, 2.6, 1.2, 1, 1],
              nota="Ledger append-only: correção é lançamento negativo (INV-CST-002)."),
    ])


DIAS_PT = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
ROTULO_HORAS = {"SEM_DADOS": "sem dados", "NO_RITMO": "no ritmo", "ESFORCO_ALTO": "esforço alto", "ESFORCO_BAIXO": "esforço baixo",
                "HORAS_SEM_AVANCO": "horas sem avanço", "AVANCO_SEM_HORAS": "avanço sem horas"}


async def relatorio_apontamentos(s: AsyncSession, contrato_id: UUID, data_corte: date, emitido_por: str) -> Relatorio:
    c = await repo.contrato(s, contrato_id)
    assert c is not None
    p = await montar_painel_apontamentos(s, contrato_id, data_corte)
    sr = {"contrato": {"codigo": c.codigo, "empreendimento": c.empreendimento, "contratante": c.contratante, "contratada": c.contratada}, "data_corte": data_corte.isoformat(), "baseline_numero": None}
    semanas = sorted(p.horas_semana)
    return Relatorio(_cabecalho(sr, "Apontamentos de horas por tarefa", emitido_por, paisagem=True), [
        Secao("Síntese", cartoes=[("hh previstas", _num(p.total_previsto)), ("hh apontadas", _num(p.total_apontado)),
                                  ("Consumo", pct(p.total_apontado / p.total_previsto) if p.total_previsto else "—"), ("Lançamentos", str(len(p.lancamentos)))],
              grafico=Grafico("horas apontadas por semana", [Serie("horas", [(date.fromisoformat(k), p.horas_semana[k]) for k in semanas], "#2f7d4f")], "numero") if semanas else None),
        Secao("Grade da semana " + p.semana.strftime("%d/%m/%Y"), cabecalhos=["EDT", "Tarefa"] + [f"{DIAS_PT[d.weekday()]} {d.strftime('%d/%m')}" for d in p.dias] + ["Semana"],
              linhas=[[r.edt, r.descricao] + [p.grade.get(r.edt, {}).get(d.isoformat(), ZERO) for d in p.dias] + [sum(p.grade.get(r.edt, {}).values(), start=ZERO)]
                      for r in p.resumo if r.edt in p.grade] or [["—", "sem horas nesta semana"] + [""] * 8], larguras=[0.6, 3] + [0.7] * 7 + [0.8]),
        Secao("Produtividade por tarefa (RGR-APT-001)", cabecalhos=["EDT", "Tarefa", "Início", "Término", "hh prev.", "hh apont.", "Avanço", "Produtiv.", "Projeção hh", "Desvio", "Situação"],
              linhas=[[r.edt, r.descricao, r.inicio, r.termino, r.hh_previstas, r.hh_apontadas, pct(r.progresso, 0), _num(r.produtividade), _num(r.hh_projetadas), _num(r.desvio_hh_projetado), ROTULO_HORAS.get(r.situacao, r.situacao)]
                      for r in p.resumo if not r.marco], larguras=[0.6, 2.6, 0.9, 0.9, 0.7, 0.7, 0.6, 0.7, 0.8, 0.7, 1.2]),
        Secao("Horas por recurso", cabecalhos=["Recurso", "Horas"], linhas=[[k, v] for k, v in sorted(p.horas_por_recurso.items(), key=lambda kv: -kv[1])] or [["—", "nenhuma"]], larguras=[3, 1]),
        Secao("Ledger de apontamentos", cabecalhos=["Data", "EDT", "Tarefa", "Recurso", "Horas", "Descrição", "Por"],
              linhas=[[a.data, a.edt, a.descricao_atividade, a.recurso_codigo or "—", a.horas, a.descricao, a.criado_por] for a in p.lancamentos], larguras=[0.8, 0.6, 2.4, 0.8, 0.6, 2.6, 1],
              nota="Ledger append-only: estorno é lançamento negativo vinculado ao original (INV-APT-002)."),
    ])


async def relatorio_previsto_realizado(s: AsyncSession, contrato_id: UUID, data_corte: date, emitido_por: str) -> Relatorio:
    c = await repo.contrato(s, contrato_id)
    assert c is not None
    p = await montar_painel_pr(s, contrato_id, data_corte)
    sr = {"contrato": {"codigo": c.codigo, "empreendimento": c.empreendimento, "contratante": c.contratante, "contratada": c.contratada}, "data_corte": data_corte.isoformat(), "baseline_numero": None}

    def fmt(v: Decimal, un: str) -> str:
        return pct(v) if un == "fração" else moeda(v) if un == "R$" else f"{_num(v)} {un}"

    ck = await montar_cockpit(s, contrato_id, data_corte, "BL0")
    barras_dim = [(d_.nome.split(" ")[0], [("previsto", d_.previsto / (d_.previsto or 1), "#b9bfc2"), ("realizado", d_.realizado / (d_.previsto or 1), "#a11d1d")]) for d_ in p.dimensoes]
    barras_fase = [(f_["edt"], [("previsto", Decimal(str(f_["previsto_ac"] or 0)), "#b9bfc2"), ("real", Decimal(str(f_["real_ac"] or 0)), "#2f7d4f")]) for f_ in p.fases]
    return Relatorio(_cabecalho(sr, "Previsto × Realizado e análise de coerência", emitido_por, paisagem=True), [
        Secao("Curva S — previsto (BL0) × vigente × realizado", grafico=Grafico("avanço físico acumulado", [
            Serie("previsto BL0", [(x.fim_semana, x.previsto_bl0_ac) for x in ck.curva], "#b9bfc2"),
            Serie("vigente", [(x.fim_semana, x.previsto_vigente_ac) for x in ck.curva], "#7d8286", tracejada=True),
            Serie("realizado", [(x.fim_semana, x.real_ac) for x in ck.curva if x.real_ac is not None], "#a11d1d")], "pct")),
        Secao("Avanço por fase — previsto × real", grafico=Grafico("acumulado na data de corte", tipo="barras", barras=barras_fase, formato_y="pct")),
        Secao("Dimensões (realizado como fração do previsto)", grafico=Grafico("1,0 = igual ao previsto", tipo="barras", barras=barras_dim, formato_y="numero")),
        Secao("Dimensões", cartoes=[("Índice de coerência", _num(p.indice_coerencia))] + [(d.nome, f"{fmt(d.previsto, d.unidade)} → {fmt(d.realizado, d.unidade)}") for d in p.dimensoes],
              cabecalhos=["Dimensão", "Previsto", "Realizado", "Índice", "Leitura"], linhas=[[d.nome, fmt(d.previsto, d.unidade), fmt(d.realizado, d.unidade), _num(d.indice), d.leitura] for d in p.dimensoes], larguras=[1.6, 1.2, 1.2, 0.7, 4]),
        Secao("Análise de coerência (RGR-COE-001)", cabecalhos=["Regra", "Severidade", "EDT", "Dimensão", "Diagnóstico"],
              linhas=[[a.regra, a.severidade.lower(), a.referencia, a.dimensao.lower(), a.detalhe] for a in p.achados] or [["—", "", "", "", "nenhuma incoerência"]], larguras=[1.6, 0.8, 0.6, 0.8, 4.6]),
        Secao("Por fase", cabecalhos=["EDT", "Fase", "Prev.", "Real", "hh prev.", "hh apont.", "Custo prev.", "Custo real", "Término BL", "Término vig."],
              linhas=[[f["edt"], f["descricao"], pct(_d(f["previsto_ac"])), pct(_d(f["real_ac"])), _d(f["hh_previstas"]), _d(f["hh_apontadas"]), moeda(_d(f["custo_previsto"])), moeda(_d(f["custo_real"])), f["termino_bl"], f["termino_vigente"]] for f in p.fases],
              larguras=[0.6, 2.6, 0.7, 0.7, 0.8, 0.8, 1.1, 1.1, 0.9, 0.9]),
        Secao("Marcos — BL × vigente × real", cabecalhos=["EDT", "Marco", "BL", "Vigente", "Real", "Crítico"],
              linhas=[[m["edt"], m["descricao"], m["bl"], m["vigente"], m["real"] or "previsto", "sim" if m["critica"] else "não"] for m in p.marcos] or [["—", "nenhum", None, None, "", ""]], larguras=[0.6, 3.5, 1, 1, 1, 0.7]),
        Secao("Recursos — horas previstas × apontadas", cabecalhos=["Código", "Recurso", "hh prev.", "hh apont.", "Custo prev.", "Custo horas"],
              linhas=[[r["codigo"], r["nome"], _d(r["hh_previstas"]), _d(r["hh_apontadas"]), moeda(_d(r["custo_previsto"])), moeda(_d(r["custo_horas_apontadas"]))] for r in p.recursos] or [["—", "sem recursos", "", "", "", ""]], larguras=[0.8, 3, 0.9, 0.9, 1.2, 1.2]),
        Secao("Custo por categoria", cabecalhos=["Categoria", "Planejado", "Realizado"], linhas=[[x["categoria"], moeda(_d(x["planejado"])), moeda(_d(x["realizado"]))] for x in p.categorias_custo] or [["—", "", ""]], larguras=[2, 1, 1]),
        Secao("Por atividade", cabecalhos=["EDT", "Atividade", "Início BL", "Início vig.", "Início real", "Término BL", "Término vig.", "Término real", "Desvio", "Prev.", "Real", "hh prev.", "hh apont.", "Custo prev.", "Custo real"],
              linhas=[[a.edt, a.descricao, a.inicio_bl, a.inicio_vigente, a.inicio_real, a.termino_bl, a.termino_vigente, a.termino_real,
                       "" if a.desvio_termino_dias is None else f"{a.desvio_termino_dias:+d} d", "" if a.marco else pct(a.previsto_ac, 0), pct(a.real_ac, 0),
                       "" if a.marco else a.hh_previstas, "" if a.marco else a.hh_apontadas, "" if a.marco else moeda(a.custo_previsto), "" if a.marco else moeda(a.custo_real)] for a in p.atividades],
              larguras=[0.5, 2.2, 0.75, 0.75, 0.75, 0.75, 0.75, 0.75, 0.55, 0.5, 0.5, 0.6, 0.6, 0.9, 0.9],
              nota="Início real = primeira medição com avanço ou primeiro apontamento; término real = primeira medição a 100%; desvio = (real ou vigente) − BL em dias corridos."),
    ])
