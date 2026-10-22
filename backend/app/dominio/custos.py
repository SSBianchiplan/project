"""RGR-CST-001 — custo planejado distribuído pelo cronograma e valor agregado (EVM).

Orçamento da atividade (BAC_a) = itens diretos + recursos alocados. O planejado
no tempo segue o CPM vigente com perfil uniforme sobre os dias úteis. Valor
agregado = BAC_a × avanço medido. Custo real vem do ledger. Índices:
IDC = VA ÷ CR; ENT (EAC) = BAC ÷ IDC; VC = VA − CR; variação de prazo em custo
VPr = VA − VP. Tudo Decimal, tudo determinístico.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .cronograma import Calendario, ResultadoCPM, sabado_de
from .tipos import ZERO, q

VERSAO_REGRA = "RGR-CST-001/1"
CENT = Decimal("0.01")


def r2(v: Decimal) -> Decimal:
    return v.quantize(CENT)


@dataclass(frozen=True, slots=True)
class CustoAtividade:
    edt: str
    itens: Decimal                 # Σ itens diretos
    recursos: Decimal              # horas alocadas × custo/hora
    progresso: Decimal             # fração medida [0,1]
    por_categoria: dict[str, Decimal]

    @property
    def bac(self) -> Decimal:
        return self.itens + self.recursos


@dataclass(frozen=True, slots=True)
class Lancamento:
    edt: str
    data: date
    valor: Decimal
    categoria: str


@dataclass(frozen=True, slots=True)
class PontoCusto:
    fim_semana: date
    vp_ac: Decimal        # planejado acumulado
    va_ac: Decimal        # valor agregado acumulado (BAC × avanço, distribuído no previsto até o corte)
    cr_ac: Decimal | None  # real acumulado (None após a data de corte)


@dataclass(frozen=True, slots=True)
class Evm:
    bac: Decimal
    vp: Decimal
    va: Decimal
    cr: Decimal
    vc: Decimal            # VA − CR
    vpr: Decimal           # VA − VP
    idc: Decimal | None    # VA ÷ CR
    idp_custo: Decimal | None  # VA ÷ VP
    ent: Decimal | None    # BAC ÷ IDC
    etc: Decimal | None    # ENT − CR
    vac: Decimal | None    # BAC − ENT


def _semanas_da_atividade(d_ie: int, d_te: int, cal: Calendario, semanas: list[date]) -> list[tuple[date, int]]:
    """(sábado, dias úteis da atividade nessa semana)."""
    cont: dict[date, int] = {}
    for i in range(d_ie, d_te + 1):
        cont[sabado_de(cal.data_de(i))] = cont.get(sabado_de(cal.data_de(i)), 0) + 1
    return [(s, cont.get(s, 0)) for s in semanas]


def planejado_semanal(
    custos: dict[str, CustoAtividade], cpm: ResultadoCPM, cal: Calendario, semanas: list[date],
) -> dict[tuple[str, date], Decimal]:
    """Parcela (R$) de cada atividade por sábado — perfil uniforme por dia útil.
    Marco (duração zero) concentra no seu sábado. Última semana absorve o centavo."""
    out: dict[tuple[str, date], Decimal] = {}
    for edt, c in custos.items():
        d = cpm.atividades.get(edt)
        if d is None or c.bac == ZERO:
            continue
        dias = _semanas_da_atividade(d.ie, d.te, cal, semanas)
        total_dias = sum(n for _, n in dias) or 1
        acumulado = ZERO
        ultima = max((s for s, n in dias if n), default=None)
        for s, n in dias:
            if not n:
                continue
            p = r2(c.bac * Decimal(n) / Decimal(total_dias)) if s != ultima else r2(c.bac - acumulado)
            out[(edt, s)] = p
            acumulado += p
    return out


def curva(
    custos: dict[str, CustoAtividade], cpm: ResultadoCPM, cal: Calendario, semanas: list[date],
    lancamentos: list[Lancamento], data_corte: date,
) -> list[PontoCusto]:
    parc = planejado_semanal(custos, cpm, cal, semanas)
    bac_total = sum((c.bac for c in custos.values()), start=ZERO)
    # VA acumulado por semana: BAC_a × avanço, mas o avanço só existe na data de corte;
    # até lá, distribui-se proporcionalmente ao planejado (perfil do previsto) e trava no corte.
    va_final = sum((c.bac * c.progresso for c in custos.values()), start=ZERO)
    pontos: list[PontoCusto] = []
    vp = ZERO
    vp_no_corte = sum((v for (e, s), v in parc.items() if s <= data_corte), start=ZERO)
    for s in semanas:
        vp += sum((v for (e, ss), v in parc.items() if ss == s), start=ZERO)
        if s <= data_corte:
            frac = (vp / vp_no_corte) if vp_no_corte > ZERO else Decimal(1)
            va = r2(va_final * min(frac, Decimal(1)))
            cr = r2(sum((lc.valor for lc in lancamentos if lc.data <= s), start=ZERO))
        else:
            va = r2(va_final)
            cr = None
        pontos.append(PontoCusto(s, r2(vp), va, cr))
    if not pontos and bac_total:
        pontos.append(PontoCusto(data_corte, r2(bac_total), r2(va_final), r2(sum((lc.valor for lc in lancamentos), start=ZERO))))
    return pontos


def evm(custos: dict[str, CustoAtividade], pontos: list[PontoCusto], lancamentos: list[Lancamento], data_corte: date) -> Evm:
    bac = r2(sum((c.bac for c in custos.values()), start=ZERO))
    va = r2(sum((c.bac * c.progresso for c in custos.values()), start=ZERO))
    cr = r2(sum((lc.valor for lc in lancamentos if lc.data <= data_corte), start=ZERO))
    ate = [p for p in pontos if p.fim_semana <= data_corte]
    vp = ate[-1].vp_ac if ate else ZERO
    idc = q(va / cr) if cr > ZERO else None
    idp = q(va / vp) if vp > ZERO else None
    ent = r2(bac / idc) if idc and idc > ZERO else None
    return Evm(
        bac=bac, vp=vp, va=va, cr=cr, vc=r2(va - cr), vpr=r2(va - vp), idc=idc, idp_custo=idp,
        ent=ent, etc=r2(ent - cr) if ent is not None else None, vac=r2(bac - ent) if ent is not None else None,
    )


def por_categoria(custos: dict[str, CustoAtividade], lancamentos: list[Lancamento]) -> dict[str, tuple[Decimal, Decimal]]:
    """{categoria: (planejado, realizado)}"""
    out: dict[str, list[Decimal]] = {}
    for c in custos.values():
        for cat, v in c.por_categoria.items():
            out.setdefault(cat, [ZERO, ZERO])[0] += v
        if c.recursos:
            out.setdefault("RECURSOS_ALOCADOS", [ZERO, ZERO])[0] += c.recursos
    for lc in lancamentos:
        out.setdefault(lc.categoria, [ZERO, ZERO])[1] += lc.valor
    return {k: (r2(v[0]), r2(v[1])) for k, v in sorted(out.items())}


def por_no(prefixo: str, custos: dict[str, CustoAtividade], lancamentos: list[Lancamento]) -> tuple[Decimal, Decimal, Decimal]:
    """(BAC, VA, CR) das folhas sob um EDT (ou tudo, se prefixo vazio)."""
    sel = {e: c for e, c in custos.items() if not prefixo or e == prefixo or e.startswith(prefixo + ".")}
    bac = sum((c.bac for c in sel.values()), start=ZERO)
    va = sum((c.bac * c.progresso for c in sel.values()), start=ZERO)
    cr = sum((lc.valor for lc in lancamentos if lc.edt in sel), start=ZERO)
    return r2(bac), r2(va), r2(cr)
