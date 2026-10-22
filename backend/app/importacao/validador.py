"""Validação do plano importado contra as invariantes de cronograma.

Roda antes de qualquer escrita. Achado BLOQUEANTE impede a aplicação — e o
banco recusa a aplicação mesmo que a API erre (ck_importacao_achados).
"""
from __future__ import annotations

from ..dominio import cronograma as cr
from .modelo import Achado, PlanoImportado, Severidade

DURACAO_MAXIMA = 15
LAG_MAXIMO = 21


def _ciclo(plano: PlanoImportado) -> Achado | None:
    """INV-CRN-006 só é detectável rodando a rede. Lags inválidos já foram
    apontados; ficam fora para não mascarar o ciclo."""
    try:
        cr.calcular(
            [cr.AtividadeCPM(a.id_externo, 0 if a.marco else max(a.duracao_dias, 0))
             for a in plano.atividades],
            [cr.VinculoCPM(v.predecessora, v.sucessora, v.tipo, v.lag_dias)
             for v in plano.vinculos
             if 0 <= v.lag_dias <= LAG_MAXIMO and v.predecessora != v.sucessora],
        )
    except cr.ErroCronograma as e:
        if "INV-CRN-006" in str(e):
            return Achado("INV-CRN-006", Severidade.BLOQUEANTE, "rede",
                          str(e).replace("INV-CRN-006: ", ""))
    return None


def validar(
    plano: PlanoImportado, *, excecoes: set[str] | None = None,
    edts_conhecidos: set[str] | None = None,
    vinculos_conhecidos: set[tuple[str, str]] | None = None,
) -> list[Achado]:
    """`vinculos_conhecidos` é a rede já gravada no contrato: o arquivo pode
    cobrir um trecho do escopo, e acusar falta de predecessora num fragmento
    seria ruído. INV-CRN-003 vale para a rede resultante, não para o arquivo."""
    excecoes = excecoes or set()
    achados: list[Achado] = []

    por_id = {a.id_externo: a for a in plano.atividades}
    com_predecessora = {v.sucessora for v in plano.vinculos}
    com_sucessora = {v.predecessora for v in plano.vinculos}
    for pred, suc in vinculos_conhecidos or set():
        com_predecessora.add(suc)
        com_sucessora.add(pred)

    for a in plano.atividades:
        if a.marco:
            if a.duracao_dias != 0:
                achados.append(Achado("INV-CRN-004", Severidade.BLOQUEANTE, a.id_externo,
                                      f"marco '{a.nome}' com duração {a.duracao_dias}"))
            continue
        if a.duracao_dias > DURACAO_MAXIMA and a.id_externo not in excecoes:
            achados.append(Achado("INV-CRN-001", Severidade.BLOQUEANTE, a.id_externo,
                                  f"duração {a.duracao_dias} dias acima de {DURACAO_MAXIMA}"))
        if a.id_externo not in com_predecessora:
            achados.append(Achado("INV-CRN-003", Severidade.BLOQUEANTE, a.id_externo,
                                  f"'{a.nome}' sem predecessora"))
        if a.id_externo not in com_sucessora:
            achados.append(Achado("INV-CRN-003", Severidade.BLOQUEANTE, a.id_externo,
                                  f"'{a.nome}' sem sucessora"))
        if a.restricao and a.restricao not in ("O mais breve possível", "As Soon As Possible",
                                               "Não iniciar antes de", "Start No Earlier Than"):
            achados.append(Achado("INV-CRN-005", Severidade.ALERTA, a.id_externo,
                                  f"restrição '{a.restricao}' fora do permitido"))
        if a.percentual_arquivo is not None and a.percentual_arquivo > 0:
            achados.append(Achado("INV-IMP-003", Severidade.INFORMATIVO, a.id_externo,
                                  f"arquivo traz {a.percentual_arquivo:%} — ignorado, "
                                  "avanço entra por medição"))
        if (edts_conhecidos is not None and a.id_externo not in edts_conhecidos
                and a.edt not in edts_conhecidos):
            achados.append(Achado("INV-IMP-005", Severidade.ALERTA, a.id_externo,
                                  f"'{a.nome}': EDT não existe na EAP aprovada do contrato"))

    for v in plano.vinculos:
        if v.lag_dias < 0:
            achados.append(Achado("INV-CRN-002", Severidade.BLOQUEANTE,
                                  f"{v.predecessora}->{v.sucessora}",
                                  f"lag negativo de {v.lag_dias} dias"))
        elif v.lag_dias > LAG_MAXIMO:
            achados.append(Achado("INV-CRN-002", Severidade.BLOQUEANTE,
                                  f"{v.predecessora}->{v.sucessora}",
                                  f"lag de {v.lag_dias} dias acima de {LAG_MAXIMO}"))
        for lado in (v.predecessora, v.sucessora):
            if lado not in por_id:
                achados.append(Achado("INV-IMP-004", Severidade.BLOQUEANTE, lado,
                                      "vínculo aponta para atividade inexistente no arquivo"))

    ciclo = _ciclo(plano)
    if ciclo is not None:
        achados.append(ciclo)

    plano.achados = achados
    return achados
