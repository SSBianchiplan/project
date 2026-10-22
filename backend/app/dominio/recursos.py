"""RGR-REC-001 — histograma de recursos a partir do CPM e da alocação.

Perfil uniforme: a quantidade diária alocada vale em todos os dias úteis da
atividade (marco não consome). Demanda semanal = soma dos dias úteis da semana;
capacidade semanal = capacidade diária × dias úteis da semana. Superalocação
(INV-REC-001) é achado, não bloqueio: o planejador decide nivelar ou aceitar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .cronograma import Calendario, ResultadoCPM, sabado_de
from .tipos import ZERO, q

VERSAO_REGRA = "RGR-REC-001/1"


@dataclass(frozen=True, slots=True)
class Recurso:
    id: str
    codigo: str
    nome: str
    tipo: str
    unidade: str
    capacidade_dia: Decimal
    custo_hora: Decimal = ZERO
    horas_dia: Decimal = Decimal(8)


@dataclass(frozen=True, slots=True)
class Alocacao:
    edt: str
    recurso_id: str
    quantidade_dia: Decimal


@dataclass(frozen=True, slots=True)
class SemanaRecurso:
    fim_semana: date
    demanda: Decimal            # unidade-dia na semana
    capacidade: Decimal         # unidade-dia disponível na semana
    pico_dia: Decimal           # maior demanda diária da semana
    atividades: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistogramaRecurso:
    recurso: Recurso
    semanas: tuple[SemanaRecurso, ...]
    total_unidade_dia: Decimal
    total_horas: Decimal
    custo_total: Decimal
    superalocadas: tuple[date, ...]


@dataclass(frozen=True, slots=True)
class AchadoRecurso:
    invariante: str
    referencia: str
    detalhe: str
    severidade: str = "ALERTA"


def _dias_por_semana(cal: Calendario, ate_indice: int) -> dict[date, list[int]]:
    out: dict[date, list[int]] = {}
    for i in range(0, min(ate_indice + 1, len(cal))):
        out.setdefault(sabado_de(cal.data_de(i)), []).append(i)
    return out


def histograma(
    recursos: list[Recurso], alocacoes: list[Alocacao], cpm: ResultadoCPM, cal: Calendario,
) -> list[HistogramaRecurso]:
    """Um histograma por recurso, semana a semana até o fim do cronograma."""
    demanda_dia: dict[str, dict[int, Decimal]] = {r.id: {} for r in recursos}
    quem: dict[str, dict[int, set[str]]] = {r.id: {} for r in recursos}
    for a in alocacoes:
        d = cpm.atividades.get(a.edt)
        if d is None or a.recurso_id not in demanda_dia or d.te < d.ie:
            continue
        for i in range(d.ie, d.te + 1):
            demanda_dia[a.recurso_id][i] = demanda_dia[a.recurso_id].get(i, ZERO) + a.quantidade_dia
            quem[a.recurso_id].setdefault(i, set()).add(a.edt)
    semanas = _dias_por_semana(cal, cpm.fim)
    saida: list[HistogramaRecurso] = []
    for r in recursos:
        linhas: list[SemanaRecurso] = []
        total = ZERO
        super_: list[date] = []
        for sab, dias in sorted(semanas.items()):
            dem = sum((demanda_dia[r.id].get(i, ZERO) for i in dias), start=ZERO)
            pico = max((demanda_dia[r.id].get(i, ZERO) for i in dias), default=ZERO)
            cap = r.capacidade_dia * len(dias)
            ativs = sorted({e for i in dias for e in quem[r.id].get(i, set())})
            linhas.append(SemanaRecurso(sab, q(dem), q(cap), q(pico), tuple(ativs)))
            total += dem
            if r.capacidade_dia > ZERO and pico > r.capacidade_dia:
                super_.append(sab)
        horas = total * r.horas_dia
        saida.append(HistogramaRecurso(
            recurso=r, semanas=tuple(linhas), total_unidade_dia=q(total), total_horas=q(horas),
            custo_total=q(horas * r.custo_hora), superalocadas=tuple(super_)))
    return saida


def achados(hist: list[HistogramaRecurso]) -> list[AchadoRecurso]:
    out: list[AchadoRecurso] = []
    for h in hist:
        if h.recurso.capacidade_dia <= ZERO:
            continue
        for s in h.semanas:
            if s.pico_dia > h.recurso.capacidade_dia:
                out.append(AchadoRecurso(
                    "INV-REC-001", h.recurso.codigo,
                    f"{h.recurso.nome}: pico de {s.pico_dia} {h.recurso.unidade}/dia na semana de "
                    f"{s.fim_semana.isoformat()} para capacidade {h.recurso.capacidade_dia} — "
                    f"atividades {', '.join(s.atividades)}"))
    return out


def sugerir_nivelamento(hist: HistogramaRecurso) -> list[tuple[date, Decimal]]:
    """Excedente por semana (unidade-dia): quanto precisa sair da semana para caber."""
    return [(s.fim_semana, q(s.demanda - s.capacidade)) for s in hist.semanas
            if hist.recurso.capacidade_dia > ZERO and s.demanda > s.capacidade]
