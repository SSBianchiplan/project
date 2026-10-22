"""Modelo canônico de importação. Todo parser converte para cá."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class Formato(str, Enum):
    MPP = "MPP"
    MSPROJECT_XML = "MSPROJECT_XML"
    P6_XER = "P6_XER"
    P6_XML = "P6_XML"
    XLSX_MASCARA = "XLSX_MASCARA"


class Severidade(str, Enum):
    BLOQUEANTE = "BLOQUEANTE"
    ALERTA = "ALERTA"
    INFORMATIVO = "INFORMATIVO"


@dataclass(frozen=True, slots=True)
class Achado:
    invariante: str
    severidade: Severidade
    referencia: str
    detalhe: str


@dataclass(frozen=True, slots=True)
class NoImportada:
    edt: str
    descricao: str
    nivel: int


@dataclass(frozen=True, slots=True)
class AtividadeImportada:
    id_externo: str
    edt: str
    nome: str
    duracao_dias: int
    inicio: date | None
    termino: date | None
    marco: bool
    restricao: str | None = None
    # lido do arquivo apenas para comparação; nunca vira medição (INV-IMP-003)
    percentual_arquivo: Decimal | None = None


@dataclass(frozen=True, slots=True)
class VinculoImportado:
    predecessora: str
    sucessora: str
    tipo: str          # TI, II, TT, IT
    lag_dias: int


@dataclass(slots=True)
class PlanoImportado:
    formato: Formato
    origem: str
    nos: list[NoImportada] = field(default_factory=list)
    atividades: list[AtividadeImportada] = field(default_factory=list)
    vinculos: list[VinculoImportado] = field(default_factory=list)
    achados: list[Achado] = field(default_factory=list)

    @property
    def bloqueado(self) -> bool:
        return any(a.severidade is Severidade.BLOQUEANTE for a in self.achados)
