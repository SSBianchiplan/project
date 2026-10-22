"""Schemas de fronteira (Pydantic v2). Não confundir com entidades de domínio."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoEntrada(Base):
    edt: str = Field(pattern=r"^\d+(\.\d+)*$")
    descricao: str
    nivel: int = Field(ge=1, le=7)
    tipo: str
    folha: bool
    pontos: Decimal = Decimal("0")


class EapEntrada(Base):
    contrato_id: UUID
    nos: list[NoEntrada]


class AvancoSaida(Base):
    edt: str
    peso: Decimal
    previsto_ac: Decimal
    real_ac: Decimal | None
    desvio_abs: Decimal | None
    desvio_rel: Decimal | None
    situacao: str


class PontoCurvaSaida(Base):
    fim_semana: date
    previsto: Decimal
    previsto_ac: Decimal
    real: Decimal | None
    real_ac: Decimal | None
    desvio_ac: Decimal | None


class MedicaoEntrada(Base):
    eap_no_id: UUID
    data_corte: date
    criterio: str | None = None
    etapas_concluidas: list[str] = []
    percentual_ac: Decimal | None = None
    evidencia: str
    criado_por: str

    @field_validator("data_corte")
    @classmethod
    def sabado(cls, v: date) -> date:
        if v.weekday() != 5:
            raise ValueError("INV-MED-004: data de corte deve ser sábado")
        return v


class EstornoEntrada(Base):
    medicao_id: UUID
    justificativa: str = Field(min_length=10)
    criado_por: str


class ItemLVPEntrada(Base):
    numero: int
    status: str


class LVPEntrada(Base):
    contrato_id: UUID
    especie: str
    emissao: str
    itens: list[ItemLVPEntrada]
    avaliado_por: str


class LVPSaida(Base):
    nota: Decimal
    aprovado: bool
    reprovadores: list[int]
