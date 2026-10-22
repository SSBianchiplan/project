"""RGR-APT-001 — horas apontadas versus horas previstas e produtividade.

Previsto de horas da atividade = Σ alocações (qtd/dia × horas/dia) × dias úteis
do CPM vigente. Apontado = Σ horas do ledger (estornos negativos já somam).
Produtividade = avanço medido ÷ fração de horas consumidas. Projeção de horas no
término = apontadas ÷ avanço (quando há avanço). Tudo Decimal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .cronograma import sabado_de
from .tipos import ZERO, q

VERSAO_REGRA = "RGR-APT-001/1"


@dataclass(frozen=True, slots=True)
class Apontamento:
    edt: str
    data: date
    horas: Decimal
    recurso_id: str | None = None
    autor: str = ""


@dataclass(frozen=True, slots=True)
class ResumoAtividade:
    edt: str
    hh_previstas: Decimal
    hh_apontadas: Decimal
    progresso: Decimal
    fracao_horas: Decimal | None        # apontadas ÷ previstas
    produtividade: Decimal | None       # progresso ÷ fracao_horas (1 = no ritmo)
    hh_projetadas: Decimal | None       # apontadas ÷ progresso
    desvio_hh_projetado: Decimal | None  # projetadas − previstas
    primeiro_dia: date | None
    ultimo_dia: date | None
    situacao: str                        # SEM_DADOS | NO_RITMO | ESFORCO_ALTO | ESFORCO_BAIXO | HORAS_SEM_AVANCO | AVANCO_SEM_HORAS


def resumir(
    edt: str, hh_previstas: Decimal, progresso: Decimal, apontamentos: list[Apontamento],
) -> ResumoAtividade:
    meus = [a for a in apontamentos if a.edt == edt]
    hh = q(sum((a.horas for a in meus), start=ZERO))
    datas = sorted(a.data for a in meus if a.horas > 0)
    fracao = q(hh / hh_previstas) if hh_previstas > ZERO else None
    prod = q(progresso / fracao) if fracao and fracao > ZERO else None
    proj = q(hh / progresso) if progresso > ZERO and hh > ZERO else None
    desvio = q(proj - hh_previstas) if proj is not None and hh_previstas > ZERO else None
    if hh <= ZERO and progresso <= ZERO:
        situacao = "SEM_DADOS"
    elif hh > ZERO and progresso <= ZERO:
        situacao = "HORAS_SEM_AVANCO"
    elif hh <= ZERO and progresso > ZERO:
        situacao = "AVANCO_SEM_HORAS"
    elif prod is None:
        situacao = "SEM_DADOS"
    elif prod < Decimal("0.85"):
        situacao = "ESFORCO_ALTO"       # gastou mais hora do que o avanço justifica
    elif prod > Decimal("1.25"):
        situacao = "ESFORCO_BAIXO"      # avanço alto para poucas horas: medição ou apontamento em atraso
    else:
        situacao = "NO_RITMO"
    return ResumoAtividade(edt, q(hh_previstas), hh, progresso, fracao, prod, proj, desvio,
                           datas[0] if datas else None, datas[-1] if datas else None, situacao)


def horas_por_semana(apontamentos: list[Apontamento], semanas: list[date]) -> dict[date, Decimal]:
    out = dict.fromkeys(semanas, ZERO)
    for a in apontamentos:
        s = sabado_de(a.data)
        if s in out:
            out[s] += a.horas
    return {k: q(v) for k, v in out.items()}


def horas_por_recurso(apontamentos: list[Apontamento]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for a in apontamentos:
        k = a.recurso_id or "(sem recurso)"
        out[k] = out.get(k, ZERO) + a.horas
    return {k: q(v) for k, v in out.items()}
