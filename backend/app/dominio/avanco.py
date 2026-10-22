"""RGR-AVC-001 e RGR-DSV-001/002 — avanço agregado e classificação de desvio."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .ponderacao import descendentes, peso
from .tipos import UM, ZERO, Memoria, No, SituacaoAvanco, q

VERSAO_REGRA = "RGR-AVC-001/1"

LIMITE_RELATIVO = Decimal("0.04")   # plano de recuperação
LIMITE_ABSOLUTO = Decimal("0.05")   # plano de ação


@dataclass(frozen=True, slots=True)
class Avanco:
    edt: str
    previsto_ac: Decimal
    real_ac: Decimal | None
    desvio_abs: Decimal | None
    desvio_rel: Decimal | None
    situacao: SituacaoAvanco
    memoria: Memoria


def agregar(
    edt: str,
    nos: list[No],
    base: Decimal,
    valores: dict[str, Decimal],
) -> tuple[Decimal | None, Memoria]:
    """RGR-AVC-001: média ponderada das folhas descendentes.

    `valores` mapeia EDT da folha para percentual acumulado [0,1]. Folha sem
    medição vale zero — ausência de medição é avanço zero, não dado faltante.
    Denominador zero devolve None (indefinido), nunca zero: um nó sem peso não
    tem avanço, e devolver 0% mentiria sobre uma frente que não existe.
    """
    folhas = descendentes(edt, nos)
    soma_peso = ZERO
    soma_valor = ZERO
    parcelas: list[tuple[str, Decimal]] = []
    for f in folhas:
        p = peso(f, base)
        v = valores.get(f.edt, ZERO)
        if not (ZERO <= v <= UM):
            raise ValueError(f"percentual fora de [0,1] em {f.edt}: {v}")
        soma_peso += p
        soma_valor += p * v
        parcelas.append((f.edt, q(p * v)))
    memoria = Memoria(
        regra="RGR-AVC-001",
        versao_regra=VERSAO_REGRA,
        entradas=tuple(f.edt for f in folhas),
        parcelas=tuple(parcelas),
    )
    if soma_peso == ZERO:
        return None, memoria
    return q(soma_valor / soma_peso), memoria


def desvios(previsto_ac: Decimal, real_ac: Decimal | None) -> tuple[Decimal | None, Decimal | None]:
    """RGR-DSV-001. Relativo é indefinido quando o previsto é zero."""
    if real_ac is None:
        return None, None
    absoluto = q(real_ac - previsto_ac)
    relativo = None if previsto_ac == ZERO else q(real_ac / previsto_ac - UM)
    return absoluto, relativo


def classificar(
    previsto_ac: Decimal, absoluto: Decimal | None, relativo: Decimal | None
) -> SituacaoAvanco:
    """RGR-DSV-002."""
    if previsto_ac == ZERO:
        return SituacaoAvanco.NAO_INICIADO
    if absoluto is None:
        return SituacaoAvanco.NAO_INICIADO
    if absoluto >= ZERO:
        return SituacaoAvanco.EM_DIA
    if abs(absoluto) > LIMITE_ABSOLUTO:
        return SituacaoAvanco.PLANO_DE_ACAO
    if relativo is not None and abs(relativo) > LIMITE_RELATIVO:
        return SituacaoAvanco.PLANO_DE_RECUPERACAO
    return SituacaoAvanco.ATENCAO


def avaliar(
    edt: str,
    nos: list[No],
    base: Decimal,
    previsto: dict[str, Decimal],
    realizado: dict[str, Decimal],
) -> Avanco:
    previsto_ac, mem_p = agregar(edt, nos, base, previsto)
    real_ac, mem_r = agregar(edt, nos, base, realizado)
    previsto_ac = previsto_ac if previsto_ac is not None else ZERO
    absoluto, relativo = desvios(previsto_ac, real_ac)
    return Avanco(
        edt=edt,
        previsto_ac=previsto_ac,
        real_ac=real_ac,
        desvio_abs=absoluto,
        desvio_rel=relativo,
        situacao=classificar(previsto_ac, absoluto, relativo),
        memoria=Memoria(
            regra="RGR-AVC-001+RGR-DSV-001",
            versao_regra=VERSAO_REGRA,
            entradas=mem_p.entradas,
            parcelas=mem_p.parcelas + mem_r.parcelas,
        ),
    )
