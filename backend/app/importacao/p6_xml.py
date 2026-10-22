"""Primavera P6 — XML (PMXML). Estrutura WBS/Activity/Relationship."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from xml.etree import ElementTree as ET

from .modelo import AtividadeImportada, Formato, NoImportada, PlanoImportado, VinculoImportado

TIPO_VINCULO = {
    "Finish to Start": "TI", "Start to Start": "II",
    "Finish to Finish": "TT", "Start to Finish": "IT",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _txt(el: ET.Element, nome: str) -> str:
    for filho in el:
        if _local(filho.tag) == nome and filho.text:
            return filho.text.strip()
    return ""


def _data(v: str) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse(conteudo: bytes, origem: str = "") -> PlanoImportado:
    raiz = ET.fromstring(conteudo)
    plano = PlanoImportado(formato=Formato.P6_XML, origem=origem)
    wbs_por_id: dict[str, str] = {}

    for el in raiz.iter():
        nome = _local(el.tag)
        if nome == "WBS":
            code = _txt(el, "Code")
            wbs_por_id[_txt(el, "ObjectId")] = code
            plano.nos.append(NoImportada(
                edt=code, descricao=_txt(el, "Name"),
                nivel=max(1, len(code.split("."))),
            ))
        elif nome == "Activity":
            tipo = _txt(el, "Type")
            marco = "Milestone" in tipo
            horas = _txt(el, "PlannedDuration") or "0"
            pct = _txt(el, "PercentComplete")
            plano.atividades.append(AtividadeImportada(
                id_externo=_txt(el, "ObjectId"),
                edt=wbs_por_id.get(_txt(el, "WBSObjectId"), ""),
                nome=_txt(el, "Name"),
                duracao_dias=0 if marco else int(Decimal(horas) / 8),
                inicio=_data(_txt(el, "PlannedStartDate")),
                termino=_data(_txt(el, "PlannedFinishDate")),
                marco=marco,
                restricao=_txt(el, "PrimaryConstraintType") or None,
                percentual_arquivo=Decimal(pct) / 100 if pct else None,
            ))
        elif nome == "Relationship":
            lag = _txt(el, "Lag") or "0"
            plano.vinculos.append(VinculoImportado(
                predecessora=_txt(el, "PredecessorActivityObjectId"),
                sucessora=_txt(el, "SuccessorActivityObjectId"),
                tipo=TIPO_VINCULO.get(_txt(el, "Type"), "TI"),
                lag_dias=int(Decimal(lag) / 8),
            ))
    return plano
