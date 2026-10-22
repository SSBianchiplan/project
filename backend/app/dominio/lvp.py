"""RGR-LVP-001/002 — Lista de Verificação de Planejamento."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .tipos import ZERO, q

VERSAO_REGRA = "RGR-LVP-001/1"
NOTA_MINIMA = Decimal("0.85")


class StatusItem(str, Enum):
    ATENDE = "ATENDE"
    NAO_ATENDE = "NAO_ATENDE"
    EM_ANALISE = "EM_ANALISE"


class Especie(str, Enum):
    LINHA_DE_BASE = "LINHA_DE_BASE"
    ATUALIZACAO = "ATUALIZACAO"


@dataclass(frozen=True, slots=True)
class ItemLVP:
    numero: int
    descricao: str
    peso: Decimal
    obrigatorio: bool
    status: StatusItem = StatusItem.EM_ANALISE


@dataclass(frozen=True, slots=True)
class ResultadoLVP:
    nota: Decimal
    aprovado: bool
    reprovadores: tuple[int, ...]


ITENS_LINHA_DE_BASE: tuple[ItemLVP, ...] = (
    ItemLVP(1, "Atende às datas contratuais do CFF", Decimal("0.1210"), False),
    ItemLVP(2, "Ponderação de peso conforme o item 9.4", Decimal("0.2120"), True),
    ItemLVP(3, "Toda atividade com predecessora e sucessora", Decimal("0.1110"), True),
    ItemLVP(4, "Caminho crítico visualizável, II e TT amarrados", Decimal("0.0505"), False),
    ItemLVP(5, "Calendário específico da fase de construção", Decimal("0.0505"), False),
    ItemLVP(6, "Livre de LAG negativo", Decimal("0.0510"), False),
    ItemLVP(7, "Livre de restrições além de 'não iniciar antes de'", Decimal("0.1615"), False),
    ItemLVP(8, "Rede aderente à metodologia construtiva", Decimal("0.0203"), False),
    ItemLVP(9, "Datas e sequência aprovadas pelo gestor técnico", Decimal("0.0202"), False),
    ItemLVP(10, "Duração ≤ 15 dias e latência ≤ 21 dias", Decimal("0.2020"), True),
)


def avaliar(itens: list[ItemLVP], especie: Especie) -> ResultadoLVP:
    """RGR-LVP-001 (linha de base) e RGR-LVP-002 (atualização).

    Na atualização não há nota mínima: qualquer item fora de ATENDE reprova.
    """
    nota = q(sum((i.peso for i in itens if i.status is StatusItem.ATENDE), start=ZERO))
    if especie is Especie.ATUALIZACAO:
        reprovadores = tuple(i.numero for i in itens if i.status is not StatusItem.ATENDE)
        return ResultadoLVP(nota=nota, aprovado=not reprovadores, reprovadores=reprovadores)

    obrigatorios_faltando = tuple(
        i.numero for i in itens if i.obrigatorio and i.status is not StatusItem.ATENDE
    )
    aprovado = nota >= NOTA_MINIMA and not obrigatorios_faltando
    reprovadores = obrigatorios_faltando or ((0,) if not aprovado else ())
    return ResultadoLVP(nota=nota, aprovado=aprovado, reprovadores=reprovadores)
