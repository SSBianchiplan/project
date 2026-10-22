"""Tipos e vocabulário de cálculo (SPEC-GPS-PLN-SYS-001, seção 3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

ESCALA = Decimal("0.00000001")   # 8 casas — fração, não percentual
ZERO = Decimal("0")
UM = Decimal("1")


def q(valor: Decimal) -> Decimal:
    """Quantiza na escala de armazenamento. Arredondamento só aqui."""
    return valor.quantize(ESCALA, rounding=ROUND_HALF_UP)


class ErroDominio(Exception):
    """Erro de regra de negócio. Nunca dict com chave 'error'."""


class PonderacaoInvalida(ErroDominio):
    pass


class MedicaoInvalida(ErroDominio):
    pass


class SituacaoAvanco(str, Enum):
    NAO_INICIADO = "NAO_INICIADO"
    EM_DIA = "EM_DIA"
    ATENCAO = "ATENCAO"
    PLANO_DE_RECUPERACAO = "PLANO_DE_RECUPERACAO"
    PLANO_DE_ACAO = "PLANO_DE_ACAO"


class TipoNo(str, Enum):
    RESUMO = "RESUMO"
    ATIVIDADE = "ATIVIDADE"
    MARCO = "MARCO"


@dataclass(frozen=True, slots=True)
class No:
    """Nó da EAP. `pontos` só é maior que zero em folha (INV-EAP-002)."""
    edt: str
    descricao: str
    nivel: int
    tipo: TipoNo
    folha: bool
    pontos: Decimal = ZERO

    def __post_init__(self) -> None:
        if not self.folha and self.pontos != ZERO:
            raise PonderacaoInvalida(f"INV-EAP-002: nó resumo {self.edt} não pode ter pontos")
        if self.tipo is TipoNo.MARCO and self.pontos != ZERO:
            raise PonderacaoInvalida(f"INV-EAP-003: marco {self.edt} deve ter peso zero")
        if self.pontos < ZERO:
            raise PonderacaoInvalida(f"{self.edt}: pontos negativos")
        if not 1 <= self.nivel <= 7:
            raise PonderacaoInvalida(f"INV-EAP-005: nível {self.nivel} fora de 1..7 em {self.edt}")


@dataclass(frozen=True, slots=True)
class Memoria:
    """Memória de cálculo — L3. Parcelas, entradas e a regra aplicada."""
    regra: str
    versao_regra: str
    entradas: tuple[str, ...]
    parcelas: tuple[tuple[str, Decimal], ...] = field(default=())


def sabados(inicio: date, fim: date) -> list[date]:
    """Datas de corte do período, em intervalo fechado à esquerda e à direita."""
    if fim < inicio:
        raise ErroDominio("janela invertida")
    primeiro = inicio + timedelta(days=(5 - inicio.weekday()) % 7)
    out: list[date] = []
    d = primeiro
    while d <= fim:
        out.append(d)
        d += timedelta(days=7)
    return out


def eh_sabado(d: date) -> bool:
    return d.weekday() == 5
