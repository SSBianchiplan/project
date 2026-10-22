"""Smoke de Apontamentos e Previsto × Realizado contra a API viva.

Uso: python scripts/smoke_apontamentos.py [http://localhost:8001]
Exige admin semeado (bootstrap_admin.py) e contrato demo (semear_demo.py).
Cobre: apontar (201), marco (422, INV-APT-001), futuro (422, INV-APT-004),
estorno (201, horas negativas) e estorno repetido (409), painel semanal,
previsto × realizado, exportações PDF/XLSX dos dois módulos e status report
com bloco de coerência.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001") + "/api/v1"
EMAIL = os.environ.get("GPS_ADMIN_EMAIL", "admin@exemplo.com")
SENHA = os.environ.get("GPS_ADMIN_SENHA", "")


def chamar(metodo: str, caminho: str, corpo: object | None = None, token: str | None = None, bruto: bool = False) -> tuple[int, object]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(BASE + caminho, data=dados, method=metodo, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            b = r.read()
            return r.status, (b if bruto else (json.loads(b) if b else {}))
    except urllib.error.HTTPError as e:
        b = e.read()
        return e.code, (b if bruto else (json.loads(b) if b else {}))


def ok(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


def main() -> None:
    st, s = chamar("POST", "/auth/login", {"email": EMAIL, "senha": SENHA})
    ok(st == 200, f"login {EMAIL}")
    tk = s["token"]
    _, cs = chamar("GET", "/contratos", token=tk)
    cid = cs[0]["id"]
    _, crn = chamar("GET", f"/contratos/{cid}/cronograma", token=tk)
    ativs = crn["atividades"]
    por = {a["edt"]: a for a in ativs}
    tarefa, marco = por["1.3.2"], por["1.1.1"]      # demo: atividade folha e marco
    hoje = date.today().isoformat()

    st, r = chamar("POST", f"/contratos/{cid}/apontamentos", {"atividade_id": tarefa["id"], "data": hoje, "horas": "6.5", "descricao": "smoke"}, token=tk)
    ok(st == 201 and r["horas"] == "6.50", f"apontar 6,5 h em {tarefa['edt']}")
    apid = r["id"]
    st, _ = chamar("POST", f"/contratos/{cid}/apontamentos", {"atividade_id": marco["id"], "data": hoje, "horas": "1"}, token=tk)
    ok(st in (409, 422), "marco recusa horas (INV-APT-001)")
    st, _ = chamar("POST", f"/contratos/{cid}/apontamentos", {"atividade_id": tarefa["id"], "data": (date.today() + timedelta(days=2)).isoformat(), "horas": "1"}, token=tk)
    ok(st == 422, "data futura recusada (INV-APT-004)")
    st, e = chamar("POST", f"/apontamentos/{apid}/estorno", {"justificativa": "smoke: estorno de teste"}, token=tk)
    ok(st == 201 and e["horas"].startswith("-6.5"), "estorno gera lançamento negativo (INV-APT-002)")
    st, _ = chamar("POST", f"/apontamentos/{apid}/estorno", {"justificativa": "smoke: repetido"}, token=tk)
    ok(st == 409, "estorno repetido recusado")

    st, p = chamar("GET", f"/contratos/{cid}/apontamentos?semana={hoje}", token=tk)
    ok(st == 200 and len(p["dias"]) == 7 and "resumo" in p, f"painel semanal ({len(p['lancamentos'])} lançamentos)")
    st, pr = chamar("GET", f"/contratos/{cid}/previsto-realizado?data_corte={hoje}", token=tk)
    ok(st == 200 and len(pr["dimensoes"]) == 4, f"previsto × realizado: índice {pr['indice_coerencia']}, {len(pr['achados'])} achados")
    for tipo in ("apontamentos", "previsto-realizado"):
        for fmt, magic in (("pdf", b"%PDF"), ("xlsx", b"PK")):
            st, b = chamar("GET", f"/contratos/{cid}/exportar/{tipo}?data_corte={hoje}&formato={fmt}", token=tk, bruto=True)
            ok(st == 200 and isinstance(b, bytes) and b[:4].startswith(magic[:4]) and len(b) > 2000, f"exportar {tipo}.{fmt} ({len(b) // 1024} KiB)")
    st, sr = chamar("GET", f"/contratos/{cid}/status-report?data_corte={hoje}", token=tk)
    ok(st == 200 and "coerencia" in sr and "indice" in sr["coerencia"], f"status report com coerência {sr['coerencia']['indice']}")
    st, b = chamar("GET", f"/contratos/{cid}/exportar/status-report?data_corte={hoje}&formato=pdf", token=tk, bruto=True)
    ok(st == 200 and b[:4] == b"%PDF", "status report PDF")
    print("SMOKE APONTAMENTOS OK")


if __name__ == "__main__":
    main()
