"""RGR-CPM-001/002 — calendário de dias úteis, CPM, achados e impacto.

Núcleo puro: sem I/O, sem now(). Datas viram índice de dia útil sobre o
calendário do contrato; o resto é aritmética inteira. Isso é o que faz o
mesmo cronograma dar o mesmo número hoje e daqui a cinco anos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .tipos import ZERO, ErroDominio, q

VERSAO_REGRA = "RGR-CPM-001/1"

DURACAO_MAXIMA = 15
LAG_MAXIMO = 21
TIPOS_VINCULO = ("TI", "II", "TT", "IT")
RESTRICOES = ("ASAP", "SNET", "MSO")


class ErroCronograma(ErroDominio):
    pass


# ------------------------------------------------------------------ calendário

class Calendario:
    """Eixo de dias úteis a partir de uma data. Índice 0 = primeiro dia útil.

    `dias_semana` usa a convenção de `date.weekday()` (0 = segunda).
    """

    __slots__ = ("_indice", "dias_semana", "eixo", "feriados", "inicio")

    def __init__(
        self,
        inicio: date,
        feriados: frozenset[date] = frozenset(),
        horizonte: int = 400,
        dias_semana: frozenset[int] = frozenset({0, 1, 2, 3, 4}),
    ) -> None:
        if horizonte < 1:
            raise ErroCronograma("horizonte deve ser positivo")
        self.inicio = inicio
        self.feriados = feriados
        self.dias_semana = dias_semana
        eixo: list[date] = []
        d = inicio
        while len(eixo) < horizonte:
            if d.weekday() in dias_semana and d not in feriados:
                eixo.append(d)
            d += timedelta(days=1)
        self.eixo: tuple[date, ...] = tuple(eixo)
        self._indice = {d: i for i, d in enumerate(self.eixo)}

    def indice_de(self, d: date) -> int:
        """Primeiro dia útil >= d. Antes do eixo devolve 0; depois, o último."""
        if d in self._indice:
            return self._indice[d]
        if d < self.eixo[0]:
            return 0
        for i, x in enumerate(self.eixo):
            if x >= d:
                return i
        return len(self.eixo) - 1

    def data_de(self, i: int) -> date:
        return self.eixo[max(0, min(i, len(self.eixo) - 1))]

    def __len__(self) -> int:
        return len(self.eixo)


# ------------------------------------------------------------------- entradas

@dataclass(frozen=True, slots=True)
class AtividadeCPM:
    id: str
    duracao: int                 # dias úteis; marco = 0
    piso: int | None = None      # SNET: índice mínimo de início
    fixo: int | None = None      # MSO: índice cravado de início

    def __post_init__(self) -> None:
        if self.duracao < 0:
            raise ErroCronograma(f"{self.id}: duração negativa")


@dataclass(frozen=True, slots=True)
class VinculoCPM:
    pred: str
    suc: str
    tipo: str = "TI"
    lag: int = 0

    def __post_init__(self) -> None:
        if self.tipo not in TIPOS_VINCULO:
            raise ErroCronograma(f"tipo de vínculo desconhecido: {self.tipo}")
        if self.pred == self.suc:
            raise ErroCronograma(f"INV-CRN-006: {self.pred} depende de si mesma")


@dataclass(frozen=True, slots=True)
class Datas:
    id: str
    ie: int
    te: int
    it: int
    tt: int
    folga_total: int
    folga_livre: int
    critica: bool


@dataclass(frozen=True, slots=True)
class ResultadoCPM:
    atividades: dict[str, Datas]
    fim: int
    ordem: tuple[str, ...]
    criticas: tuple[str, ...]
    versao_regra: str = VERSAO_REGRA


def _desl(a: AtividadeCPM) -> int:
    return max(a.duracao - 1, 0)


def _grafo(
    atividades: list[AtividadeCPM], vinculos: list[VinculoCPM],
) -> tuple[dict[str, AtividadeCPM], dict[str, list[VinculoCPM]], dict[str, list[VinculoCPM]]]:
    por_id = {a.id: a for a in atividades}
    if len(por_id) != len(atividades):
        raise ErroCronograma("atividade duplicada no cronograma")
    entram: dict[str, list[VinculoCPM]] = {a.id: [] for a in atividades}
    saem: dict[str, list[VinculoCPM]] = {a.id: [] for a in atividades}
    for v in vinculos:
        if v.pred not in por_id or v.suc not in por_id:
            continue
        if v.lag < 0:
            raise ErroCronograma(f"INV-CRN-002: lag negativo em {v.pred} → {v.suc}")
        if v.lag > LAG_MAXIMO:
            raise ErroCronograma(
                f"INV-CRN-002: lag de {v.lag} dias em {v.pred} → {v.suc} (máximo {LAG_MAXIMO})")
        entram[v.suc].append(v)
        saem[v.pred].append(v)
    return por_id, entram, saem


def _ordem_topologica(
    por_id: dict[str, AtividadeCPM], entram: dict[str, list[VinculoCPM]],
    saem: dict[str, list[VinculoCPM]],
) -> list[str]:
    """Kahn com fila ordenada: ordem determinística, mesmo resultado sempre."""
    grau = {i: len(vs) for i, vs in entram.items()}
    fila = sorted(i for i, g in grau.items() if g == 0)
    ordem: list[str] = []
    while fila:
        atual = fila.pop(0)
        ordem.append(atual)
        for v in saem[atual]:
            grau[v.suc] -= 1
            if grau[v.suc] == 0:
                fila.append(v.suc)
                fila.sort()
    if len(ordem) != len(por_id):
        no_ciclo = [i for i in por_id if i not in ordem]
        raise ErroCronograma(
            "INV-CRN-006: ciclo de precedência envolvendo " + ", ".join(no_ciclo[:4]))
    return ordem


def _passo_a_frente(
    ordem: list[str], por_id: dict[str, AtividadeCPM], entram: dict[str, list[VinculoCPM]],
) -> tuple[dict[str, int], dict[str, int]]:
    ie: dict[str, int] = {}
    te: dict[str, int] = {}
    for i in ordem:
        a = por_id[i]
        inicio = a.piso if a.piso is not None else 0
        for v in entram[i]:
            p = v.pred
            if v.tipo == "TI":
                inicio = max(inicio, te[p] + 1 + v.lag)
            elif v.tipo == "II":
                inicio = max(inicio, ie[p] + v.lag)
            elif v.tipo == "TT":
                inicio = max(inicio, te[p] + v.lag - _desl(a))
            else:
                inicio = max(inicio, ie[p] + v.lag - _desl(a))
        if a.fixo is not None:
            inicio = a.fixo
        ie[i] = max(inicio, 0)
        te[i] = ie[i] + _desl(a)
    return ie, te


def _passo_atras(
    ordem: list[str], por_id: dict[str, AtividadeCPM], saem: dict[str, list[VinculoCPM]], fim: int,
) -> tuple[dict[str, int], dict[str, int]]:
    it: dict[str, int] = {}
    tt: dict[str, int] = {}
    for i in reversed(ordem):
        a = por_id[i]
        termino = fim
        for v in saem[i]:
            s = v.suc
            if v.tipo == "TI":
                termino = min(termino, it[s] - 1 - v.lag)
            elif v.tipo == "II":
                termino = min(termino, it[s] - v.lag + _desl(a))
            elif v.tipo == "TT":
                termino = min(termino, tt[s] - v.lag)
            else:
                termino = min(termino, tt[s] - v.lag + _desl(a))
        tt[i] = termino
        it[i] = termino - _desl(a)
    return it, tt


def calcular(atividades: list[AtividadeCPM], vinculos: list[VinculoCPM]) -> ResultadoCPM:
    """CPM completo. Levanta ErroCronograma em lag inválido (INV-CRN-002) e
    ciclo (INV-CRN-006). Vínculo para atividade fora da lista é ignorado —
    quem valida rede incompleta é a importação (INV-IMP-004)."""
    if not atividades:
        return ResultadoCPM({}, 0, (), ())
    por_id, entram, saem = _grafo(atividades, vinculos)
    ordem = _ordem_topologica(por_id, entram, saem)
    ie, te = _passo_a_frente(ordem, por_id, entram)
    fim = max(te.values())
    it, tt = _passo_atras(ordem, por_id, saem, fim)

    resultado: dict[str, Datas] = {}
    for i in ordem:
        sucs = saem[i]
        if sucs:
            folga_livre = min(
                (ie[v.suc] - te[i] - 1 - v.lag) if v.tipo == "TI" else (ie[v.suc] - ie[i] - v.lag)
                for v in sucs
            )
        else:
            folga_livre = fim - te[i]
        folga_total = it[i] - ie[i]
        resultado[i] = Datas(
            id=i, ie=ie[i], te=te[i], it=it[i], tt=tt[i],
            folga_total=folga_total, folga_livre=max(folga_livre, 0),
            critica=folga_total <= 0,
        )
    criticas = tuple(i for i in ordem if resultado[i].critica)
    return ResultadoCPM(resultado, fim, tuple(ordem), criticas)


# --------------------------------------------------------------- plano/achados

@dataclass(frozen=True, slots=True)
class AtividadePlano:
    """Folha da EAP com os atributos de cronograma que o CPM e os achados leem."""
    edt: str
    descricao: str
    marco: bool
    duracao: int
    pontos: Decimal = ZERO
    restricao: str = "ASAP"
    restricao_data: date | None = None
    deadline: date | None = None
    excecao: bool = False
    memoria_peso: str = ""

    def __post_init__(self) -> None:
        if self.restricao not in RESTRICOES:
            raise ErroCronograma(f"{self.edt}: restrição desconhecida {self.restricao}")
        if self.marco and self.duracao != 0:
            raise ErroCronograma(f"INV-CRN-004: marco {self.edt} com duração {self.duracao}")


def montar_entrada(plano: list[AtividadePlano], cal: Calendario) -> list[AtividadeCPM]:
    out: list[AtividadeCPM] = []
    for t in plano:
        piso = fixo = None
        if t.restricao_data is not None:
            idx = cal.indice_de(t.restricao_data)
            if t.restricao == "SNET":
                piso = idx
            elif t.restricao == "MSO":
                fixo = idx
        out.append(AtividadeCPM(t.edt, 0 if t.marco else t.duracao, piso, fixo))
    return out


@dataclass(frozen=True, slots=True)
class AchadoCronograma:
    invariante: str
    referencia: str
    detalhe: str
    severidade: str = "BLOQUEANTE"


def achados(
    plano: list[AtividadePlano],
    vinculos: list[VinculoCPM],
    cpm: ResultadoCPM,
    cal: Calendario,
    base: Decimal,
) -> list[AchadoCronograma]:
    """Tudo que reprova a emissão na LVP. Não decide nada: lista."""
    out: list[AchadoCronograma] = []
    tem_pred = {v.suc for v in vinculos}
    tem_suc = {v.pred for v in vinculos}
    for t in plano:
        r = cpm.atividades.get(t.edt)
        if not t.marco and t.duracao > DURACAO_MAXIMA and not t.excecao:
            out.append(AchadoCronograma(
                "INV-CRN-001", t.edt,
                f"{t.descricao}: {t.duracao} dias úteis, acima do máximo de {DURACAO_MAXIMA} "
                "sem exceção contratual"))
        if t.restricao == "MSO":
            out.append(AchadoCronograma(
                "INV-CRN-007", t.edt,
                f"{t.descricao}: restrição \"deve iniciar em\" trava a atividade contra a rede "
                "— item 7 da LVP"))
        # marcos de início e término do projeto são os únicos sem pred/suc
        extremo_inicio = t.marco and r is not None and r.ie == 0
        extremo_fim = t.marco and r is not None and r.te == cpm.fim
        if t.edt not in tem_pred and not extremo_inicio:
            out.append(AchadoCronograma("INV-CRN-003", t.edt, f"{t.descricao}: sem predecessora"))
        if t.edt not in tem_suc and not extremo_fim:
            out.append(AchadoCronograma("INV-CRN-003", t.edt, f"{t.descricao}: sem sucessora"))
        if t.deadline is not None and r is not None and cal.data_de(r.te) > t.deadline:
            out.append(AchadoCronograma(
                "INV-CRN-008", t.edt,
                f"{t.descricao}: término em {cal.data_de(r.te).isoformat()} passa da data "
                f"limite {t.deadline.isoformat()}"))
        if t.marco and t.pontos > ZERO:
            out.append(AchadoCronograma(
                "INV-EAP-003", t.edt,
                f"{t.descricao}: marco com peso {t.pontos} — marco tem peso zero"))
        if t.pontos > ZERO and len(t.memoria_peso.strip()) < 10:
            out.append(AchadoCronograma(
                "INV-DOC-002", t.edt, f"{t.descricao}: peso sem memória de cálculo", "ALERTA"))
    for v in vinculos:
        if v.lag > LAG_MAXIMO:
            out.append(AchadoCronograma(
                "INV-CRN-002", f"{v.pred}→{v.suc}",
                f"latência de {v.lag} dias, acima de {LAG_MAXIMO}"))
    soma = sum((t.pontos for t in plano), start=ZERO)
    if soma != base:
        out.append(AchadoCronograma(
            "INV-EAP-001", "EAP", f"soma das folhas é {soma}, difere da base {base}"))
    return out


# -------------------------------------------------------------------- impacto

@dataclass(frozen=True, slots=True)
class Impacto:
    fim_antes: int
    fim_depois: int
    delta_dias: int
    entraram_criticas: tuple[str, ...]
    sairam_criticas: tuple[str, ...]


def impacto(antes: ResultadoCPM, depois: ResultadoCPM) -> Impacto:
    a, d = set(antes.criticas), set(depois.criticas)
    return Impacto(
        fim_antes=antes.fim, fim_depois=depois.fim, delta_dias=depois.fim - antes.fim,
        entraram_criticas=tuple(x for x in depois.ordem if x in d - a),
        sairam_criticas=tuple(x for x in antes.ordem if x in a - d),
    )


# ------------------------------------------------------- previsto vigente (RGR-CRV-002)

def sabado_de(d: date) -> date:
    """Sábado que fecha a semana de d (o próprio d se for sábado)."""
    return d + timedelta(days=(5 - d.weekday()) % 7)


def previsto_linear(
    cpm: ResultadoCPM, cal: Calendario, semanas: list[date],
) -> dict[tuple[str, date], Decimal]:
    """Parcela semanal (fração da folha) distribuída linearmente entre o
    sábado do início e o do término. Marco (duração 0) concentra tudo no
    sábado do seu dia. Chave: (edt, sábado)."""
    out: dict[tuple[str, date], Decimal] = {}
    for edt, r in cpm.atividades.items():
        s_ini = sabado_de(cal.data_de(r.ie))
        s_fim = sabado_de(cal.data_de(r.te))
        janela = [s for s in semanas if s_ini <= s <= s_fim]
        if not janela:
            continue
        parcela = q(Decimal(1) / Decimal(len(janela)))
        acumulado = ZERO
        for i, s in enumerate(janela):
            # última semana absorve o arredondamento: soma fecha exata em 1
            p = parcela if i < len(janela) - 1 else q(Decimal(1) - acumulado)
            out[(edt, s)] = p
            acumulado += p
    return out


def projecao_termino(fim: int, idp: Decimal | None, horizonte: int) -> int:
    """RGR-IDP-001. IDP indefinido ou zero: devolve o próprio fim."""
    if idp is None or idp <= ZERO:
        return fim
    return min(round(Decimal(fim) / idp), horizonte - 1)


@dataclass(frozen=True, slots=True)
class MemoriaCPM:
    """Memória de cálculo persistível (L3)."""
    versao_regra: str
    fim: int
    criticas: tuple[str, ...]
    datas: tuple[Datas, ...] = field(default=())


def memoria(cpm: ResultadoCPM) -> MemoriaCPM:
    return MemoriaCPM(cpm.versao_regra, cpm.fim, cpm.criticas,
                      tuple(cpm.atividades[i] for i in cpm.ordem))
