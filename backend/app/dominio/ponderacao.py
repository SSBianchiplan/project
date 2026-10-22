"""RGR-AVC-002 e INV-EAP-001 — ponderação da EAP."""
from __future__ import annotations

from decimal import Decimal

from .tipos import ZERO, Memoria, No, PonderacaoInvalida, q

VERSAO_REGRA = "RGR-AVC-002/1"


def validar_ponderacao(nos: list[No], base: Decimal) -> Decimal:
    """INV-EAP-001: a soma dos pontos das folhas fecha na base do contrato.

    Devolve a soma. Levanta PonderacaoInvalida quando não fecha — nunca corrige
    silenciosamente, porque ajuste automático de peso é como a curva S deixa de
    corresponder ao contrato.
    """
    if base <= ZERO:
        raise PonderacaoInvalida("base de ponderação deve ser positiva")
    soma = sum((n.pontos for n in nos if n.folha), start=ZERO)
    if soma != base:
        raise PonderacaoInvalida(
            f"INV-EAP-001: soma das folhas {soma} difere da base {base}"
        )
    return soma


def peso(no: No, base: Decimal) -> Decimal:
    """RGR-AVC-002: peso = pontos / base."""
    if base <= ZERO:
        raise PonderacaoInvalida("base de ponderação deve ser positiva")
    return q(no.pontos / base)


def pesos(nos: list[No], base: Decimal) -> dict[str, Decimal]:
    return {n.edt: peso(n, base) for n in nos if n.folha and n.pontos > ZERO}


def descendentes(edt_pai: str, nos: list[No]) -> list[No]:
    """Folhas ponderadas sob um nó. Raiz ('') devolve todas.

    A própria folha conta como descendente de si mesma: o avanço de um nó
    folha é a sua medição, não a média de um conjunto vazio.
    """
    pref = f"{edt_pai}." if edt_pai else ""
    return [
        n for n in nos
        if n.folha and n.pontos > ZERO
        and (not pref or n.edt == edt_pai or n.edt.startswith(pref))
    ]


def memoria_ponderacao(nos: list[No], base: Decimal) -> Memoria:
    return Memoria(
        regra="RGR-AVC-002",
        versao_regra=VERSAO_REGRA,
        entradas=tuple(n.edt for n in nos if n.folha),
        parcelas=tuple((n.edt, peso(n, base)) for n in nos if n.folha and n.pontos > ZERO),
    )
