"""RGR-CRV-001 — curva S semanal previsto x realizado."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .ponderacao import peso
from .tipos import ZERO, ErroDominio, No, eh_sabado, q

VERSAO_REGRA = "RGR-CRV-001/1"


@dataclass(frozen=True, slots=True)
class PontoCurva:
    fim_semana: date
    previsto: Decimal
    previsto_ac: Decimal
    real: Decimal | None
    real_ac: Decimal | None
    desvio_ac: Decimal | None


def curva(
    semanas: list[date],
    nos: list[No],
    base: Decimal,
    previsto: dict[tuple[str, date], Decimal],
    realizado: dict[tuple[str, date], Decimal],
    data_corte: date,
) -> list[PontoCurva]:
    """Curva ponderada do contrato.

    `previsto` e `realizado` são parcelas incrementais por (EDT da folha, sábado).
    Depois da data de corte o realizado é None — projetar realizado é ficção, e
    ficção em curva S vira base de medição.
    """
    if any(not eh_sabado(s) for s in semanas):
        raise ErroDominio("INV-PRV-002: toda semana deve terminar em sábado")
    if list(semanas) != sorted(semanas):
        raise ErroDominio("semanas fora de ordem — cálculo sequencial exige ordem determinística")

    folhas = [n for n in nos if n.folha and n.pontos > ZERO]
    pesos = {n.edt: peso(n, base) for n in folhas}

    pontos: list[PontoCurva] = []
    acum_p = ZERO
    acum_r = ZERO
    for s in semanas:
        parcela_p = sum((pesos[e] * previsto.get((e, s), ZERO) for e in pesos), start=ZERO)
        acum_p += parcela_p
        if s <= data_corte:
            parcela_r = sum((pesos[e] * realizado.get((e, s), ZERO) for e in pesos), start=ZERO)
            acum_r += parcela_r
            pontos.append(PontoCurva(
                s, q(parcela_p), q(acum_p), q(parcela_r), q(acum_r), q(acum_r - acum_p)
            ))
        else:
            pontos.append(PontoCurva(s, q(parcela_p), q(acum_p), None, None, None))
    return pontos
