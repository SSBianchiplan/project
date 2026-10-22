"""MS Project — XML nativo (schema http://schemas.microsoft.com/project)."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from xml.etree import ElementTree as ET

from .modelo import AtividadeImportada, Formato, NoImportada, PlanoImportado, VinculoImportado

NS = {"p": "http://schemas.microsoft.com/project"}
TIPO_VINCULO = {"0": "TT", "1": "TI", "2": "II", "3": "IT"}
RESTRICAO = {
    "0": "O mais breve possível", "1": "O mais tarde possível",
    "4": "Não iniciar antes de", "5": "Não terminar antes de",
    "6": "Não iniciar depois de", "7": "Não terminar depois de",
    "2": "Deve iniciar em", "3": "Deve terminar em",
}


def _txt(el: ET.Element, tag: str) -> str:
    filho = el.find(f"p:{tag}", NS)
    return (filho.text or "").strip() if filho is not None and filho.text else ""


def _data(v: str) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _duracao_dias(iso: str, horas_dia: int = 8) -> int:
    """PT80H0M0S -> 10 dias. Duração do Project vem em ISO-8601 de trabalho."""
    if not iso:
        return 0
    m = re.match(r"PT?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    horas = Decimal(m.group(1) or 0) + Decimal(m.group(2) or 0) / 60
    return int(horas / horas_dia)


def parse(conteudo: bytes, origem: str = "") -> PlanoImportado:
    raiz = ET.fromstring(conteudo)
    plano = PlanoImportado(formato=Formato.MSPROJECT_XML, origem=origem)
    vistos: set[str] = set()

    for tarefa in raiz.findall(".//p:Tasks/p:Task", NS):
        uid = _txt(tarefa, "UID")
        if uid == "0":            # linha de projeto
            continue
        edt = _txt(tarefa, "WBS")
        marco = _txt(tarefa, "Milestone") == "1"
        resumo = _txt(tarefa, "Summary") == "1"
        nome = _txt(tarefa, "Name")
        nivel = int(_txt(tarefa, "OutlineLevel") or 1)
        if resumo:
            if edt not in vistos:
                plano.nos.append(NoImportada(edt=edt, descricao=nome, nivel=nivel))
                vistos.add(edt)
            continue
        pct = _txt(tarefa, "PercentComplete")
        plano.atividades.append(AtividadeImportada(
            id_externo=uid,
            edt=edt,
            nome=nome,
            duracao_dias=0 if marco else _duracao_dias(_txt(tarefa, "Duration")),
            inicio=_data(_txt(tarefa, "Start")),
            termino=_data(_txt(tarefa, "Finish")),
            marco=marco,
            restricao=RESTRICAO.get(_txt(tarefa, "ConstraintType")),
            percentual_arquivo=Decimal(pct) / 100 if pct else None,
        ))
        for link in tarefa.findall("p:PredecessorLink", NS):
            lag_centesimos = Decimal(_txt(link, "LinkLag") or 0)   # centésimos de minuto
            plano.vinculos.append(VinculoImportado(
                predecessora=_txt(link, "PredecessorUID"),
                sucessora=uid,
                tipo=TIPO_VINCULO.get(_txt(link, "Type"), "TI"),
                lag_dias=int(lag_centesimos / 100 / 60 / 8),
            ))
    return plano
