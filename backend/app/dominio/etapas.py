"""RGR-MED-001 — avanço por critério de etapas (seções 14 a 16 do procedimento).

Etapa parcial não pontua: o avanço só entra quando a etapa está concluída e
evidenciada. Percentual parcial por etapa é como a medição vira negociação.
"""
from __future__ import annotations

from decimal import Decimal

from .tipos import UM, ZERO, MedicaoInvalida, q

VERSAO_REGRA = "RGR-MED-001/1"

D = Decimal

CRITERIOS: dict[str, tuple[tuple[str, Decimal], ...]] = {
    "EQUIPAMENTO_MECANICO": (
        ("Levantamento topográfico", D("0.05")),
        ("Montagem sobre a base", D("0.20")),
        ("Alinhamento e nivelamento", D("0.20")),
        ("Grauteamento do skid", D("0.20")),
        ("Montagem local e acessórios", D("0.30")),
        ("Inspeção final", D("0.05")),
    ),
    "TANQUE": (
        ("Preparação das chapas de base", D("0.02")),
        ("Montagem da base/piso", D("0.15")),
        ("Montagem do teto", D("0.10")),
        ("Montagem do costado", D("0.63")),
        ("Montagem dos bocais", D("0.05")),
        ("Teste hidrostático", D("0.03")),
        ("Inspeção final e punch list", D("0.02")),
    ),
    "SPOOL_FABRICACAO": (
        ("Separação e preparação do material", D("0.10")),
        ("Corte e biselamento", D("0.20")),
        ("Acoplamento das partes", D("0.20")),
        ("Execução das soldas", D("0.30")),
        ("Liberação do controle de qualidade", D("0.10")),
        ("Entrega do spool na obra", D("0.10")),
    ),
    "TUBULACAO_MONTAGEM": (
        ("Preparação do material/spool", D("0.10")),
        ("Posicionamento do spool", D("0.20")),
        ("Acoplamento dos spools", D("0.10")),
        ("Execução das soldas", D("0.20")),
        ("Acessórios e suportação", D("0.20")),
        ("Liberação do controle de qualidade", D("0.10")),
        ("Teste da linha", D("0.08")),
        ("Inspeção final e punch list", D("0.02")),
    ),
    "ESTRUTURA_METALICA": (
        ("Separação e preparação", D("0.10")),
        ("Pré-montagem", D("0.52")),
        ("Montagem", D("0.33")),
        ("Inspeção final e relatório", D("0.05")),
    ),
    "ELETRICA_EQUIPAMENTO": (
        ("Preparação do material e da base", D("0.05")),
        ("Equipamento sobre a base", D("0.45")),
        ("Acoplamento de painéis", D("0.30")),
        ("Aterramento", D("0.05")),
        ("Testes", D("0.10")),
        ("Inspeção final e relatório", D("0.05")),
    ),
    "COMISSIONAMENTO": (
        ("Divisão em sistemas e subsistemas", D("0.01")),
        ("Identificação dos objetos comissionáveis", D("0.01")),
        ("Predecessoras e sucessoras definidas", D("0.01")),
        ("Cronograma de comissionamento certificado", D("0.01")),
        ("Check lists certificados", D("0.01")),
        ("Completação eletromecânica e testes a frio", D("0.02")),
        ("Comissionamento e testes a quente", D("0.88")),
        ("ECA assinado com pendências acordadas", D("0.05")),
    ),
}


def validar_criterios() -> None:
    for nome, etapas in CRITERIOS.items():
        soma = sum((p for _, p in etapas), start=ZERO)
        if soma != UM:
            raise MedicaoInvalida(f"critério {nome} soma {soma}, esperado 1")


def avancar(criterio: str, concluidas: set[str]) -> tuple[Decimal, tuple[tuple[str, Decimal], ...]]:
    """Devolve o percentual acumulado e as parcelas que o compõem."""
    if criterio not in CRITERIOS:
        raise MedicaoInvalida(f"critério desconhecido: {criterio}")
    etapas = CRITERIOS[criterio]
    nomes = {n for n, _ in etapas}
    desconhecidas = concluidas - nomes
    if desconhecidas:
        raise MedicaoInvalida(f"etapas fora do critério {criterio}: {sorted(desconhecidas)}")
    parcelas = tuple((n, p) for n, p in etapas if n in concluidas)
    return q(sum((p for _, p in parcelas), start=ZERO)), parcelas
