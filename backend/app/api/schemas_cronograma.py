"""Schemas de fronteira do cronograma, cockpit, EAP e medições (Pydantic v2)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ leitura

class ContratoSaida(Base):
    id: UUID
    codigo: str
    empreendimento: str
    contratante: str
    contratada: str
    base_ponderacao: Decimal
    data_inicio: date
    termino_contratual: date


class CalendarioSaida(Base):
    inicio: date
    feriados: list[date]
    eixo: list[date]


class NoSaida(Base):
    id: UUID
    edt: str
    parent_edt: str | None
    descricao: str
    nivel: int
    tipo: str
    folha: bool
    pontos: Decimal
    memoria_peso: str


class ExcecaoSaida(Base):
    id: UUID
    documento: str
    autorizada_por: str
    justificativa: str


class DatasSaida(Base):
    ie: int
    te: int
    it: int
    tt: int
    folga_total: int
    folga_livre: int
    critica: bool
    inicio: date
    termino: date
    inicio_tarde: date
    termino_tarde: date


class BaselineDatasSaida(Base):
    inicio: date
    termino: date
    ie: int
    te: int


class AtividadeSaida(Base):
    id: UUID
    eap_no_id: UUID
    edt: str
    fase_edt: str
    descricao: str
    marco: bool
    duracao: int
    pontos: Decimal
    memoria_peso: str
    codigo_externo: str | None
    restricao: str
    restricao_data: date | None
    restricao_justificativa: str
    deadline: date | None
    calendario: str
    disciplina: str
    responsavel: str
    prioridade: int
    notas: str
    situacao: str
    mod_qtd: int
    moi_qtd: int
    equipamentos_qtd: int
    hh_estimadas: Decimal
    turno: int
    subcontratada: str
    criterio_medicao: str | None
    unidade: str
    qtd_prevista: Decimal
    qtd_realizada: Decimal
    excecao: ExcecaoSaida | None
    progresso: Decimal                 # fração [0,1] vigente pela medição
    datas: DatasSaida
    baseline: BaselineDatasSaida | None


class VinculoSaida(Base):
    pred_edt: str
    suc_edt: str
    tipo: str
    lag: int


class AchadoSaida(Base):
    invariante: str
    severidade: str
    referencia: str
    detalhe: str


class MarcoTemporal(Base):
    indice: int
    data: date


class ImpactoSaida(Base):
    fim_antes: MarcoTemporal
    fim_depois: MarcoTemporal
    delta_dias: int
    entraram_criticas: list[str]
    sairam_criticas: list[str]
    avanco_antes: Decimal
    avanco_depois: Decimal


class CronogramaSaida(Base):
    contrato: ContratoSaida
    calendario: CalendarioSaida
    data_status: date
    nos: list[NoSaida]
    atividades: list[AtividadeSaida]
    vinculos: list[VinculoSaida]
    fim: MarcoTemporal
    fim_baseline: MarcoTemporal | None
    baseline_numero: int | None
    criticas: list[str]
    achados: list[AchadoSaida]
    avanco_realizado: Decimal
    versao_regra: str
    impacto: ImpactoSaida | None = None


# ------------------------------------------------------------------ escrita

class ExcecaoEntrada(Base):
    documento: str = Field(min_length=1)
    autorizada_por: str = Field(min_length=1)
    justificativa: str = Field(min_length=10)


class AtividadeAlteracao(Base):
    """Campos opcionais: só o que vier é alterado."""
    edt: str | None = None                     # identifica a atividade na simulação
    descricao: str | None = None
    marco: bool | None = None
    duracao: int | None = Field(default=None, ge=0, le=365)
    restricao: str | None = Field(default=None, pattern="^(ASAP|SNET|MSO)$")
    restricao_data: date | None = None
    restricao_justificativa: str | None = None
    deadline: date | None = None
    limpar_deadline: bool = False
    calendario: str | None = None
    disciplina: str | None = None
    responsavel: str | None = None
    prioridade: int | None = Field(default=None, ge=0, le=1000)
    notas: str | None = None
    situacao: str | None = Field(
        default=None, pattern="^(NAO_INICIADA|EM_ANDAMENTO|CONCLUIDA|SUSPENSA)$")
    mod_qtd: int | None = Field(default=None, ge=0)
    moi_qtd: int | None = Field(default=None, ge=0)
    equipamentos_qtd: int | None = Field(default=None, ge=0)
    hh_estimadas: Decimal | None = Field(default=None, ge=0)
    turno: int | None = Field(default=None, ge=1, le=3)
    subcontratada: str | None = None
    criterio_medicao: str | None = None
    unidade: str | None = None
    qtd_prevista: Decimal | None = Field(default=None, ge=0)
    qtd_realizada: Decimal | None = Field(default=None, ge=0)
    pontos: Decimal | None = Field(default=None, ge=0)
    memoria_peso: str | None = None
    excecao: ExcecaoEntrada | None = None
    remover_excecao: bool = False


class VinculoEntrada(Base):
    pred_edt: str
    suc_edt: str
    tipo: str = Field(default="TI", pattern="^(TI|II|TT|IT)$")
    lag: int = Field(default=0, ge=0, le=21)


class SimulacaoEntrada(Base):
    data_status: date | None = None
    atividades: list[AtividadeAlteracao] = []
    vinculos: list[VinculoEntrada] | None = None   # lista completa substitui a rede


class GravacaoAtividade(Base):
    alteracao: AtividadeAlteracao
    alterado_por: str = Field(min_length=1)


class GravacaoVinculos(Base):
    vinculos: list[VinculoEntrada]
    alterado_por: str = Field(min_length=1)


class PonderacaoFolha(Base):
    edt: str
    pontos: Decimal = Field(ge=0)
    memoria_peso: str | None = None


class PonderacaoEntrada(Base):
    folhas: list[PonderacaoFolha]
    alterado_por: str = Field(min_length=1)


class EstornoMedicao(Base):
    justificativa: str = Field(min_length=10)
    criado_por: str = Field(min_length=1)


# ------------------------------------------------------------------- cockpit

class CartoesCockpit(Base):
    previsto_ac: Decimal
    real_ac: Decimal
    desvio_abs: Decimal | None
    desvio_rel: Decimal | None
    situacao: str
    idp: Decimal | None
    termino_referencia: date
    termino_projetado: date
    delta_projetado_dias: int
    medicoes: int


class FaseCockpit(Base):
    edt: str
    descricao: str
    peso: Decimal
    previsto_ac: Decimal | None
    real_ac: Decimal | None
    desvio_abs: Decimal | None
    desvio_rel: Decimal | None
    idp: Decimal | None
    situacao: str
    primeira_folha_edt: str | None


class PontoCurva(Base):
    fim_semana: date
    previsto_bl0_ac: Decimal
    previsto_vigente_ac: Decimal
    real_ac: Decimal | None


class BarraHistograma(Base):
    fim_semana: date
    previsto: Decimal
    real: Decimal | None


class CockpitSaida(Base):
    data_corte: date
    referencia: str
    semanas: list[date]
    cartoes: CartoesCockpit
    fases: list[FaseCockpit]
    curva: list[PontoCurva]
    histograma: list[BarraHistograma]


# ----------------------------------------------------------------------- EAP

class NoEapSaida(NoSaida):
    peso: Decimal
    avanco: Decimal | None
    contribuicao: Decimal
    marco: bool


class FaseEap(Base):
    edt: str
    descricao: str
    peso: Decimal
    avanco: Decimal


class EapSaida(Base):
    base: Decimal
    soma: Decimal
    diferenca: Decimal
    folhas_ponderadas: int
    nos: list[NoEapSaida]
    fases: list[FaseEap]
    achados: list[AchadoSaida]


# ------------------------------------------------------------------ medições

class MedicaoSaida(Base):
    id: UUID
    data_corte: date
    edt: str
    descricao: str
    tipo: str
    percentual_ac: Decimal
    evidencia: str
    criado_por: str
    criado_em: datetime
    estorna_id: UUID | None
    justificativa: str | None
    memoria_calculo: dict | list
    hash: str
    hash_anterior: str | None


# ------------------------------------------------------------ projeto novo

class ContratoEntrada(Base):
    codigo: str = Field(min_length=1, max_length=40)
    contratante: str = Field(min_length=1)
    contratada: str = Field(min_length=1)
    empreendimento: str = Field(min_length=1)
    modalidade: str = Field(
        pattern="^(EPC|EPCM|EPS|SERVICO_ENGENHARIA|CONSTRUCAO_CIVIL|MONTAGEM|FORNECIMENTO)$")
    data_inicio: date
    termino_contratual: date
    base_ponderacao: Decimal = Field(default=Decimal(1_000_000), gt=0)
    fuso: str = "America/Sao_Paulo"


class NoEstrutura(Base):
    """Um nó da EAP com os atributos de cronograma da folha. Nível e pai vêm do EDT."""
    edt: str = Field(pattern=r"^[0-9]+(\.[0-9]+)*$")
    descricao: str = Field(min_length=1)
    tipo: str = Field(pattern="^(RESUMO|ATIVIDADE|MARCO)$")
    pontos: Decimal = Field(default=Decimal(0), ge=0)
    memoria_peso: str = ""
    duracao: int = Field(default=0, ge=0, le=365)
    criterio_medicao: str | None = None
    disciplina: str = ""
    responsavel: str = ""
    fase: str | None = Field(
        default=None,
        pattern="^(MILESTONES|ENGENHARIA|SUPRIMENTOS|CONSTRUCAO_CIVIL|MONTAGEM_ELETROMECANICA|COMISSIONAMENTO)$")
    excecao: ExcecaoEntrada | None = None


class FeriadoEntrada(Base):
    data: date
    descricao: str = "feriado"


class EstruturaEntrada(Base):
    nos: list[NoEstrutura] = Field(min_length=1)
    vinculos: list[VinculoEntrada] = []
    feriados: list[FeriadoEntrada] = []
    gravado_por: str = Field(min_length=1)


class BaselineEntrada(Base):
    emissao: str = Field(min_length=1)
    nota_lvp: Decimal = Field(ge=0, le=1)
    avaliado_por: str = Field(min_length=1)
    aprovada_por: str = Field(min_length=1)


class BaselineSaida(Base):
    id: UUID
    numero: int
    termino: date
    semanas: int
    atividades: int

