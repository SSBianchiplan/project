"""Smoke do módulo de custos: orçamento por atividade, ledger, curva e EVM.

    GPS_ADMIN_SENHA=... python scripts/smoke_custos.py [http://localhost:8001]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001") + "/api/v1"
EMAIL = os.environ.get("GPS_ADMIN_EMAIL", "admin@exemplo.com")
SENHA = os.environ.get("GPS_ADMIN_SENHA", "")
FALHAS: list[str] = []


def chamar(metodo: str, caminho: str, corpo: object | None = None, token: str | None = None) -> tuple[int, dict | list]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(BASE + caminho, data=dados, method=metodo, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw.decode("utf-8")) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def passo(nome: str, ok: bool, detalhe: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {nome}{'  — ' + detalhe if detalhe else ''}")
    if not ok:
        FALHAS.append(nome)


def main() -> None:
    _, s = chamar("POST", "/auth/login", {"email": EMAIL, "senha": SENHA})
    tk = s["token"]
    _, cs = chamar("GET", "/contratos", token=tk)
    cid = cs[0]["id"]
    _, crn = chamar("GET", f"/contratos/{cid}/cronograma", token=tk)
    por = {a["edt"]: a for a in crn["atividades"]}
    a132, marco = por["1.3.2"], por["1.1.1"]

    st, itens = chamar("PUT", f"/atividades/{a132['id']}/custos", {"itens": [
        {"categoria": "EQUIPAMENTO", "descricao": "Trocador de calor TC-101", "fornecedor": "Metalúrgica X", "quantidade": "2", "unidade": "un", "custo_unitario": "185000"},
        {"categoria": "MAQUINA", "descricao": "Guindaste 50 t (locação)", "quantidade": "20", "unidade": "dia", "custo_unitario": "3200"},
        {"categoria": "SERVICO", "descricao": "Inspeção END", "quantidade": "1", "unidade": "vb", "custo_unitario": "18000"}]}, token=tk)
    passo("PUT itens de custo → 200, totais calculados", st == 200 and len(itens) == 3 and float(itens[0]["total"]) == 370000, str(st))
    st, r = chamar("PUT", f"/atividades/{marco['id']}/custos", {"itens": [{"categoria": "OUTRO", "descricao": "x", "custo_unitario": "1"}]}, token=tk)
    passo("custo em marco → 422 INV-CST-001", st == 422)

    st, l1 = chamar("POST", f"/contratos/{cid}/custos-realizados", {"atividade_id": a132["id"], "categoria": "EQUIPAMENTO", "data": "2026-03-06",
                                                                    "valor": "190000", "descricao": "NF 4471 — 1º trocador", "documento": "NF 4471"}, token=tk)
    passo("lançar realizado → 201", st == 201 and l1.get("edt") == "1.3.2", str(l1)[:60])
    st, _ = chamar("POST", f"/contratos/{cid}/custos-realizados", {"atividade_id": a132["id"], "categoria": "EQUIPAMENTO", "data": "2026-03-13",
                                                                   "valor": "-5000", "descricao": "ajuste: desconto negociado"}, token=tk)
    passo("lançamento negativo (ajuste) → 201", st == 201)
    st, _ = chamar("POST", f"/contratos/{cid}/custos-realizados", {"atividade_id": a132["id"], "categoria": "EQUIPAMENTO", "data": "2026-03-13",
                                                                   "valor": "0", "descricao": "zero"}, token=tk)
    passo("valor zero → 422", st == 422)

    st, p = chamar("GET", f"/contratos/{cid}/custos?data_corte=2026-03-21", token=tk)
    e = p["evm"]
    bac_132 = 370000 + 64000 + 18000
    lin = next(x for x in p["atividades"] if x["edt"] == "1.3.2")
    passo("painel: BAC da atividade = itens + recursos", st == 200 and float(lin["itens"]) == bac_132, f"itens {lin['itens']} recursos {lin['recursos']} bac {lin['bac']}")
    passo("VA = BAC × avanço (0,85)", abs(float(lin["va"]) - float(lin["bac"]) * 0.85) < 0.01, f"va {lin['va']} prog {lin['progresso']}")
    passo("CR da atividade = 190000 − 5000", float(lin["cr"]) == 185000)
    passo("EVM: IDC e ENT calculados", e["idc"] is not None and e["ent"] is not None, f"bac {e['bac']} vp {e['vp']} va {e['va']} cr {e['cr']} idc {e['idc']} ent {e['ent']}")
    fase = next(x for x in p["nos"] if x["edt"] == "1.3")
    passo("consolidação por fase 1.3", float(fase["bac"]) >= bac_132 and float(fase["cr"]) == 185000)
    cats = {c["categoria"]: c for c in p["categorias"]}
    passo("por categoria: EQUIPAMENTO planejado 370.000 e realizado 185.000", float(cats["EQUIPAMENTO"]["planejado"]) == 370000 and float(cats["EQUIPAMENTO"]["realizado"]) == 185000)
    ult = [x for x in p["curva"] if x["cr_ac"] is not None][-1]
    passo("curva: VP acumulado fecha no BAC e CR só até o corte", abs(float(p["curva"][-1]["vp_ac"]) - float(e["bac"])) < 0.05 and ult["fim_semana"] <= "2026-03-21",
          f"vp final {p['curva'][-1]['vp_ac']} · último CR em {ult['fim_semana']}")

    # limpeza: zera orçamento da atividade (o ledger fica: é append-only por desenho)
    chamar("PUT", f"/atividades/{a132['id']}/custos", {"itens": []}, token=tk)
    chamar("POST", f"/contratos/{cid}/custos-realizados", {"atividade_id": a132["id"], "categoria": "EQUIPAMENTO", "data": "2026-03-14",
                                                           "valor": "-185000", "descricao": "estorno do smoke"}, token=tk)
    chamar("POST", "/auth/logout", token=tk)
    print()
    print("SMOKE CUSTOS:", "PASS" if not FALHAS else f"FAIL em {FALHAS}")
    sys.exit(1 if FALHAS else 0)


if __name__ == "__main__":
    main()
