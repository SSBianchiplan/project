"""Primavera P6 — arquivo XER (texto tabulado, seções %T/%F/%R)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from .modelo import AtividadeImportada, Formato, NoImportada, PlanoImportado, VinculoImportado

TIPO_VINCULO = {"PR_FS": "TI", "PR_SS": "II", "PR_FF": "TT", "PR_SF": "IT"}


def _data(v: str) -> date | None:
    v = v.strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _tabelas(texto: str) -> dict[str, list[dict[str, str]]]:
    """Quebra o XER em tabelas. Encoding do P6 costuma ser cp1252."""
    tabelas: dict[str, list[dict[str, str]]] = {}
    atual: str | None = None
    campos: list[str] = []
    for linha in texto.splitlines():
        if not linha:
            continue
        partes = linha.split("\t")
        marca = partes[0]
        if marca == "%T":
            atual = partes[1].strip()
            tabelas[atual] = []
            campos = []
        elif marca == "%F" and atual:
            campos = [c.strip() for c in partes[1:]]
        elif marca == "%R" and atual and campos:
            valores = partes[1:]
            valores += [""] * (len(campos) - len(valores))
            tabelas[atual].append(dict(zip(campos, valores, strict=False)))
    return tabelas


def parse(conteudo: bytes, origem: str = "") -> PlanoImportado:
    texto = conteudo.decode("cp1252", errors="replace")
    t = _tabelas(texto)
    plano = PlanoImportado(formato=Formato.P6_XER, origem=origem)

    wbs_por_id: dict[str, str] = {}
    for w in t.get("PROJWBS", []):
        edt = w.get("wbs_short_name", "").strip()
        wbs_por_id[w.get("wbs_id", "")] = edt
        plano.nos.append(NoImportada(
            edt=edt, descricao=w.get("wbs_name", "").strip(),
            nivel=max(1, len(edt.split("."))),
        ))

    for a in t.get("TASK", []):
        tipo = a.get("task_type", "")
        marco = tipo in ("TT_Mile", "TT_FinMile")
        horas = a.get("target_drtn_hr_cnt", "0").strip() or "0"
        pct = a.get("phys_complete_pct", "").strip()
        plano.atividades.append(AtividadeImportada(
            id_externo=a.get("task_id", ""),
            edt=wbs_por_id.get(a.get("wbs_id", ""), ""),
            nome=a.get("task_name", "").strip(),
            duracao_dias=0 if marco else round(Decimal(horas) / 8),
            inicio=_data(a.get("target_start_date", "")),
            termino=_data(a.get("target_end_date", "")),
            marco=marco,
            restricao=a.get("cstr_type", "").strip() or None,
            percentual_arquivo=Decimal(pct) / 100 if pct else None,
        ))

    for r in t.get("TASKPRED", []):
        lag = r.get("lag_hr_cnt", "0").strip() or "0"
        plano.vinculos.append(VinculoImportado(
            predecessora=r.get("pred_task_id", ""),
            sucessora=r.get("task_id", ""),
            tipo=TIPO_VINCULO.get(r.get("pred_type", ""), "TI"),
            lag_dias=int(Decimal(lag) / 8),
        ))
    return plano
