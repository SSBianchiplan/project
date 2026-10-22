"""RGR-COE-001 — análise de coerência entre previsto e realizado.

Cada regra olha uma atividade (ou o contrato) e devolve um achado quando os
sinais não batem entre si: avanço sem esforço, esforço sem avanço, custo sem
avanço, conclusão fora da janela, medição parada com horas correndo, etc.
Achado é diagnóstico para o planejador, não bloqueio.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .tipos import ZERO

VERSAO_REGRA = "RGR-COE-001/1"


@dataclass(frozen=True, slots=True)
class SinaisAtividade:
    edt: str
    descricao: str
    marco: bool
    inicio_bl: date | None
    termino_bl: date | None
    inicio_vigente: date
    termino_vigente: date
    progresso: Decimal
    hh_previstas: Decimal
    hh_apontadas: Decimal
    custo_previsto: Decimal
    custo_real: Decimal
    primeira_medicao: date | None
    ultima_medicao: date | None
    primeiro_apontamento: date | None
    ultimo_apontamento: date | None
    critica: bool


@dataclass(frozen=True, slots=True)
class AchadoCoerencia:
    regra: str
    severidade: str        # ALERTA | ATENCAO | INFO
    referencia: str
    detalhe: str
    dimensao: str          # PRAZO | AVANCO | ESFORCO | CUSTO


def analisar(sinais: list[SinaisAtividade], data_corte: date) -> list[AchadoCoerencia]:
    out: list[AchadoCoerencia] = []
    for a in sinais:
        if a.marco:
            if a.progresso >= 1 and a.primeira_medicao and a.inicio_bl and a.primeira_medicao < a.inicio_bl - timedelta(days=14):
                out.append(AchadoCoerencia("COE-MARCO-ANTECIPADO", "INFO", a.edt, f"{a.descricao}: marco concluído em {a.primeira_medicao:%d/%m/%Y}, {(a.inicio_bl - a.primeira_medicao).days} dias antes da BL", "PRAZO"))
            if a.progresso < 1 and a.inicio_vigente < data_corte:
                out.append(AchadoCoerencia("COE-MARCO-VENCIDO", "ALERTA", a.edt, f"{a.descricao}: marco previsto para {a.inicio_vigente:%d/%m/%Y} sem conclusão medida", "PRAZO"))
            continue
        # PRAZO
        if a.progresso < 1 and a.termino_vigente < data_corte:
            out.append(AchadoCoerencia("COE-VENCIDA", "ALERTA", a.edt, f"{a.descricao}: término vigente {a.termino_vigente:%d/%m/%Y} já passou e o avanço é {a.progresso * 100:.0f}%", "PRAZO"))
        if a.progresso > ZERO and a.inicio_vigente > data_corte:
            out.append(AchadoCoerencia("COE-AVANCO-ANTES-DO-INICIO", "ATENCAO", a.edt, f"{a.descricao}: avanço medido de {a.progresso * 100:.0f}% com início vigente em {a.inicio_vigente:%d/%m/%Y}, depois do corte — a rede não reflete o que está acontecendo", "PRAZO"))
        if a.progresso <= ZERO and a.inicio_vigente < data_corte - timedelta(days=14) and a.hh_apontadas <= ZERO:
            out.append(AchadoCoerencia("COE-NAO-INICIADA-ATRASADA", "ALERTA", a.edt, f"{a.descricao}: deveria ter começado em {a.inicio_vigente:%d/%m/%Y} e não tem medição nem horas", "PRAZO"))
        if a.termino_bl and a.termino_vigente > a.termino_bl + timedelta(days=21):
            out.append(AchadoCoerencia("COE-DERIVA-BL", "ATENCAO", a.edt, f"{a.descricao}: término vigente {a.termino_vigente:%d/%m/%Y} está {(a.termino_vigente - a.termino_bl).days} dias além da BL ({a.termino_bl:%d/%m/%Y})", "PRAZO"))
        # AVANÇO × ESFORÇO
        if a.hh_previstas > ZERO:
            frac = a.hh_apontadas / a.hh_previstas
            if a.progresso > ZERO and a.hh_apontadas <= ZERO:
                out.append(AchadoCoerencia("COE-AVANCO-SEM-HORAS", "ATENCAO", a.edt, f"{a.descricao}: {a.progresso * 100:.0f}% medido sem nenhuma hora apontada ({a.hh_previstas:.0f} hh previstas)", "ESFORCO"))
            elif a.hh_apontadas > ZERO and a.progresso <= ZERO and a.hh_apontadas >= a.hh_previstas * Decimal("0.1"):
                out.append(AchadoCoerencia("COE-HORAS-SEM-AVANCO", "ALERTA", a.edt, f"{a.descricao}: {a.hh_apontadas:.0f} hh apontadas ({frac * 100:.0f}% do previsto) e avanço zero", "ESFORCO"))
            elif a.progresso > ZERO and frac > ZERO:
                prod = a.progresso / frac
                if prod < Decimal("0.7"):
                    out.append(AchadoCoerencia("COE-PRODUTIVIDADE-BAIXA", "ALERTA", a.edt, f"{a.descricao}: consumiu {frac * 100:.0f}% das horas para {a.progresso * 100:.0f}% de avanço (produtividade {prod:.2f}) — projeção {a.hh_apontadas / a.progresso:.0f} hh contra {a.hh_previstas:.0f} previstas", "ESFORCO"))
                elif prod > Decimal("1.5") and a.progresso < 1:
                    out.append(AchadoCoerencia("COE-PRODUTIVIDADE-IMPROVAVEL", "ATENCAO", a.edt, f"{a.descricao}: {a.progresso * 100:.0f}% de avanço com {frac * 100:.0f}% das horas — medição adiantada ou apontamento em atraso", "ESFORCO"))
            if a.hh_apontadas > a.hh_previstas and a.progresso < 1:
                out.append(AchadoCoerencia("COE-HORAS-ESTOURADAS", "ALERTA", a.edt, f"{a.descricao}: {a.hh_apontadas:.0f} hh apontadas já superam as {a.hh_previstas:.0f} previstas com {a.progresso * 100:.0f}% concluído", "ESFORCO"))
        # CUSTO
        if a.custo_previsto > ZERO:
            if a.custo_real > ZERO and a.progresso <= ZERO:
                out.append(AchadoCoerencia("COE-CUSTO-SEM-AVANCO", "ATENCAO", a.edt, f"{a.descricao}: R$ {a.custo_real:,.0f} de custo real sem avanço medido (adiantamento ou medição pendente)".replace(",", "."), "CUSTO"))
            elif a.progresso > ZERO and a.custo_real > a.custo_previsto * a.progresso * Decimal("1.2"):
                out.append(AchadoCoerencia("COE-CUSTO-ACIMA-DO-VA", "ALERTA", a.edt, f"{a.descricao}: custo real R$ {a.custo_real:,.0f} está {(a.custo_real / (a.custo_previsto * a.progresso) - 1) * 100:.0f}% acima do valor agregado".replace(",", "."), "CUSTO"))
        # MEDIÇÃO PARADA COM HORAS CORRENDO
        if a.ultima_medicao and a.ultimo_apontamento and a.progresso < 1 and a.ultimo_apontamento > a.ultima_medicao + timedelta(days=14):
            out.append(AchadoCoerencia("COE-MEDICAO-DESATUALIZADA", "ATENCAO", a.edt, f"{a.descricao}: horas apontadas até {a.ultimo_apontamento:%d/%m/%Y}, última medição em {a.ultima_medicao:%d/%m/%Y}", "AVANCO"))
    return out


def indice_coerencia(achados: list[AchadoCoerencia], n_atividades: int) -> Decimal:
    """0..1 — 1 é totalmente coerente. Alerta pesa 1, atenção 0,5, info 0."""
    if n_atividades <= 0:
        return Decimal(1)
    peso = sum(Decimal(1) if a.severidade == "ALERTA" else Decimal("0.5") if a.severidade == "ATENCAO" else ZERO for a in achados)
    return max(ZERO, Decimal(1) - peso / Decimal(n_atividades)).quantize(Decimal("0.01"))
