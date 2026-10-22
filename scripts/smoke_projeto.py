"""Smoke do fluxo "projeto novo": contrato → estrutura → linha de base → cockpit.

    GPS_ORG=<uuid> python scripts/smoke_projeto.py [http://localhost:8001]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001") + "/api/v1"
ORG = os.environ["GPS_ORG"]
FALHAS: list[str] = []


def chamar(metodo: str, caminho: str, corpo: object | None = None) -> tuple[int, dict]:
    headers = {"X-Organizacao": ORG, "Accept": "application/json", "Content-Type": "application/json"}
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(BASE + caminho, data=dados, method=metodo, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def passo(nome: str, ok: bool, detalhe: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {nome}{'  — ' + detalhe if detalhe else ''}")
    if not ok:
        FALHAS.append(nome)


def no(edt: str, desc: str, tipo: str = "ATIVIDADE", pontos: int = 0, dur: int = 5, **extra: object) -> dict:
    return {"edt": edt, "descricao": desc, "tipo": tipo, "pontos": str(pontos), "memoria_peso": f"{pontos // 100} hh a 100 pontos/hh" if pontos else "",
            "duracao": dur, "criterio_medicao": None, "disciplina": "", "responsavel": "", "fase": None, "excecao": None, **extra}


def main() -> None:
    codigo = f"CT-NOVO-{date.today().strftime('%y%m%d')}-{os.urandom(2).hex()}"
    st, c = chamar("POST", "/contratos", {
        "codigo": codigo, "contratante": "Cliente Novo", "contratada": "Novo Plan Engenharia", "empreendimento": "Planta piloto",
        "modalidade": "EPC", "data_inicio": "2026-10-05", "termino_contratual": "2027-06-30", "base_ponderacao": "1000000"})
    passo("POST contrato → 201", st == 201 and c.get("codigo") == codigo, str(c.get("id")))
    cid = c["id"]
    st, _ = chamar("POST", "/contratos", {**{"codigo": codigo, "contratante": "x", "contratada": "y", "empreendimento": "z",
                                          "modalidade": "EPC", "data_inicio": "2026-10-05", "termino_contratual": "2027-06-30"}})
    passo("POST contrato com código repetido → 422", st == 422)

    nos = [
        no("1", "PLANTA PILOTO", "RESUMO", dur=0),
        no("1.1", "MILESTONES", "RESUMO", dur=0, fase="MILESTONES"),
        no("1.1.1", "Início", "MARCO", dur=0), no("1.1.2", "Aceitação", "MARCO", dur=0),
        no("1.2", "ENGENHARIA", "RESUMO", dur=0, fase="ENGENHARIA"),
        no("1.2.1", "Básica", pontos=300000, dur=10, criterio_medicao="DOCUMENTO"),
        no("1.3", "MONTAGEM", "RESUMO", dur=0, fase="MONTAGEM_ELETROMECANICA"),
        no("1.3.1", "Montagem mecânica", pontos=500000, dur=15, criterio_medicao="EQUIPAMENTO_MECANICO"),
        no("1.3.2", "Elétrica", pontos=200000, dur=8, criterio_medicao="ELETRICA_EQUIPAMENTO"),
    ]
    rede = [{"pred_edt": "1.1.1", "suc_edt": "1.2.1"}, {"pred_edt": "1.2.1", "suc_edt": "1.3.1"},
            {"pred_edt": "1.3.1", "suc_edt": "1.3.2", "tipo": "II", "lag": 5}, {"pred_edt": "1.3.2", "suc_edt": "1.1.2"},
            {"pred_edt": "1.3.1", "suc_edt": "1.1.2"}]   # marco de fim recebe todas as frentes: senão não é "fim"
    feriados = [{"data": "2026-11-02", "descricao": "Finados"}, {"data": "2026-11-20", "descricao": "Consciência Negra"}]
    estrutura = {"nos": nos, "vinculos": rede, "feriados": feriados, "gravado_por": "smoke"}

    st, r = chamar("POST", f"/contratos/{cid}/estrutura/validar", estrutura)
    passo("validar estrutura → 200 sem achados", st == 200 and r.get("achados") == [], f"fim {r.get('fim', {}).get('data')} · críticas {r.get('criticas')}")

    ruim = dict(estrutura, nos=[dict(n) for n in nos])
    ruim["nos"][5]["pontos"] = "1"
    st, r = chamar("PUT", f"/contratos/{cid}/estrutura", ruim)
    passo("gravar com soma aberta → 422 INV-EAP-001", st == 422 and "INV-EAP-001" in str(r.get("detail")))
    st, r = chamar("PUT", f"/contratos/{cid}/estrutura", dict(estrutura, vinculos=rede + [{"pred_edt": "1.3.2", "suc_edt": "1.2.1"}]))
    passo("gravar com ciclo → 422 INV-CRN-006", st == 422 and "INV-CRN-006" in str(r.get("detail")))
    st, r = chamar("PUT", f"/contratos/{cid}/estrutura", dict(estrutura, nos=nos + [no("1.4", "SOLTO", "RESUMO", dur=0)]))
    passo("resumo sem filhos → 422", st == 422)
    st, r = chamar("PUT", f"/contratos/{cid}/estrutura", dict(estrutura, nos=nos + [no("1.9.1", "sem pai", pontos=0)]))
    passo("nó sem pai → 422 INV-EAP-004", st == 422 and "INV-EAP-004" in str(r.get("detail")))

    st, r = chamar("PUT", f"/contratos/{cid}/estrutura", estrutura)
    passo("gravar estrutura → 200", st == 200 and len(r.get("atividades", [])) == 5 and len(r.get("vinculos", [])) == 5
          and r["calendario"]["feriados"] == ["2026-11-02", "2026-11-20"], f"fim {r.get('fim', {}).get('data')}")
    fim1 = r["fim"]["indice"]
    estrutura2 = dict(estrutura, nos=[dict(n) for n in nos])
    estrutura2["nos"][7]["duracao"] = 12
    st, r = chamar("PUT", f"/contratos/{cid}/estrutura", estrutura2)
    passo("regravar em elaboração encurta o fim", st == 200 and r["fim"]["indice"] < fim1, f"fim {r.get('fim', {}).get('data')}")

    st, r = chamar("POST", f"/contratos/{cid}/baselines", {"emissao": "1ª", "nota_lvp": "0.80", "avaliado_por": "fiscal", "aprovada_por": "gerente"})
    passo("baseline com LVP 0,80 → 422 (ck_lvp_nota_minima)", st == 422, str(r.get("detail", ""))[:70])
    st, b = chamar("POST", f"/contratos/{cid}/baselines", {"emissao": "1ª", "nota_lvp": "0.92", "avaliado_por": "fiscal", "aprovada_por": "gerente"})
    passo("baseline BL0 → 201", st == 201 and b.get("numero") == 0 and b.get("atividades") == 5, f"término {b.get('termino')} · {b.get('semanas')} semanas")
    st, r = chamar("PUT", f"/contratos/{cid}/estrutura", estrutura)
    passo("regravar após baseline → 409", st == 409)

    st, crn = chamar("GET", f"/contratos/{cid}/cronograma?data_status=2026-10-05")
    passo("cronograma do projeto novo com BL0", st == 200 and crn["baseline_numero"] == 0 and crn["fim_baseline"] is not None
          and all(a["baseline"] for a in crn["atividades"]))
    st, ck = chamar("GET", f"/contratos/{cid}/cockpit?data_corte=2026-10-10&referencia=BL0")
    passo("cockpit do projeto novo (nada medido)", st == 200 and float(ck["cartoes"]["real_ac"]) == 0 and len(ck["curva"]) > 20
          and abs(float(ck["curva"][-1]["previsto_bl0_ac"]) - 1) < 1e-6, f"previsto na última semana {ck['curva'][-1]['previsto_bl0_ac']}")

    a = {x["edt"]: x for x in crn["atividades"]}["1.3.2"]      # 1.3.2 é quem manda no fim (II+5, 12 dias)
    st, r = chamar("PUT", f"/atividades/{a['id']}", {"alteracao": {"duracao": 6}, "alterado_por": "smoke"})
    passo("gaveta continua funcionando após BL0 (fim antecipa vs BL0)", st == 200 and r["fim"]["indice"] < r["fim_baseline"]["indice"])
    st, b1 = chamar("POST", f"/contratos/{cid}/baselines", {"emissao": "2ª", "nota_lvp": "0.90", "avaliado_por": "fiscal", "aprovada_por": "gerente"})
    st2, crn2 = chamar("GET", f"/contratos/{cid}/cronograma")
    passo("BL1 supera BL0 e vira vigente", st == 201 and b1.get("numero") == 1 and crn2["baseline_numero"] == 1
          and crn2["fim_baseline"]["indice"] == crn2["fim"]["indice"])

    print()
    print("SMOKE PROJETO:", "PASS" if not FALHAS else f"FAIL em {FALHAS}")
    sys.exit(1 if FALHAS else 0)


if __name__ == "__main__":
    main()
