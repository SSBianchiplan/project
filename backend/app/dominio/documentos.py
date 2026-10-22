"""RGR-DOC-001 — avanço de engenharia por lista de documentos ponderada.

A ponderação é MANUAL: o planejador atribui pontos a cada documento a partir
das horas-homem estimadas, com memória de cálculo escrita. O sistema não
distribui peso sozinho — peso de documento é decisão de quem conhece o escopo,
e rateio automático por contagem faz um índice de linhas valer o mesmo que um
fluxograma de processo.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .tipos import ZERO, ErroDominio, Memoria, q

VERSAO_REGRA = "RGR-DOC-001/1"


class Estagio(str, Enum):
    NAO_INICIADO = "NAO_INICIADO"
    INICIADO = "INICIADO"
    EM_CALCULO = "EM_CALCULO"
    DESENHO_CONCLUIDO = "DESENHO_CONCLUIDO"
    EM_VERIFICACAO = "EM_VERIFICACAO"
    EMITIDO_GED_REV0 = "EMITIDO_GED_REV0"
    CERTIFICADO = "CERTIFICADO"
    CERTIFICADO_COM_PENDENCIA = "CERTIFICADO_COM_PENDENCIA"
    CERTIFICADO_SEM_PENDENCIA = "CERTIFICADO_SEM_PENDENCIA"


PERCENTUAL: dict[Estagio, Decimal] = {
    Estagio.NAO_INICIADO: Decimal("0.00"),
    Estagio.INICIADO: Decimal("0.10"),
    Estagio.EM_CALCULO: Decimal("0.30"),
    Estagio.DESENHO_CONCLUIDO: Decimal("0.40"),
    Estagio.EM_VERIFICACAO: Decimal("0.50"),
    Estagio.EMITIDO_GED_REV0: Decimal("0.60"),
    Estagio.CERTIFICADO: Decimal("0.70"),
    Estagio.CERTIFICADO_COM_PENDENCIA: Decimal("0.80"),
    Estagio.CERTIFICADO_SEM_PENDENCIA: Decimal("1.00"),
}

ORDEM: tuple[Estagio, ...] = tuple(PERCENTUAL)


@dataclass(frozen=True, slots=True)
class Documento:
    codigo: str
    disciplina: str
    hh_estimadas: Decimal
    pontos: Decimal
    memoria_peso: str
    estagio: Estagio = Estagio.NAO_INICIADO

    def __post_init__(self) -> None:
        if self.pontos <= ZERO:
            raise ErroDominio(f"{self.codigo}: pontos devem ser positivos")
        if self.hh_estimadas <= ZERO:
            raise ErroDominio(f"{self.codigo}: HH estimadas devem ser positivas")
        if len(self.memoria_peso.strip()) < 10:
            raise ErroDominio(
                f"{self.codigo}: memória de cálculo do peso é obrigatória — "
                "peso sem justificativa é peso indefensável na auditoria"
            )


def validar_fechamento(documentos: list[Documento], pontos_do_no: Decimal) -> Decimal:
    """INV-DOC-001: a soma dos documentos fecha nos pontos do nó folha."""
    soma = sum((d.pontos for d in documentos), start=ZERO)
    if soma != pontos_do_no:
        raise ErroDominio(
            f"INV-DOC-001: documentos somam {soma} pontos, nó vale {pontos_do_no}"
        )
    return soma


def avanco(documentos: list[Documento]) -> tuple[Decimal | None, Memoria]:
    """Avanço ponderado do nó de engenharia, com as parcelas por documento."""
    total = sum((d.pontos for d in documentos), start=ZERO)
    parcelas = tuple((d.codigo, q(d.pontos * PERCENTUAL[d.estagio])) for d in documentos)
    memoria = Memoria(
        regra="RGR-DOC-001",
        versao_regra=VERSAO_REGRA,
        entradas=tuple(d.codigo for d in documentos),
        parcelas=parcelas,
    )
    if total == ZERO:
        return None, memoria
    return q(sum((p for _, p in parcelas), start=ZERO) / total), memoria


def transicao_valida(de: Estagio, para: Estagio) -> bool:
    """Estágio não retrocede: revisão que volta para comentário é novo documento
    de revisão, não regressão do mesmo (mesma lógica de INV-MED-003)."""
    return ORDEM.index(para) >= ORDEM.index(de)
