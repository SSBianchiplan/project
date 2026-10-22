"""Smoke da API contra um banco semeado: percorre todos os fluxos de escrita da UI.

    GPS_ORG=<uuid> GPS_CT=<uuid> python scripts/smoke_api.py [http://localhost:8001]

Não é teste de unidade: é o roteiro que a tela executa, na ordem, com o
resultado esperado de cada passo. Sai com código 1 se algum passo divergir.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001") + "/api/v1"
ORG = os.environ["GPS_ORG"]
CT = os.environ["GPS_CT"]
FALHAS: list[str] = []


def chamar(metodo: str, caminho: str, corpo: object | None = None, arquivo: tuple[str, bytes] | None = None,
           form: dict[str, str] | None = None) -> tuple[int, object]:
    headers = {"X-Organizacao": ORG, "Accept": "application/json"}
    dados: bytes | None = None
    if arquivo is not None:
        limite = uuid.uuid4().hex
        partes = []
        for k, v in (form or {}).items():
            partes.append(f"--{limite}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        partes.append(f"--{limite}\r\nContent-Disposition: form-data; name=\"arquivo\"; filename=\"{arquivo[0]}\"\r\n"
                      "Content-Type: application/octet-stream\r\n\r\n".encode() + arquivo[1] + b"\r\n")
        partes.append(f"--{limite}--\r\n".encode())
        dados = b"".join(partes)
        headers["Content-Type"] = f"multipart/form-data; boundary={limite}"
    elif corpo is not None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
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


def main() -> None:
    st, crn = chamar("GET", f"/contratos/{CT}/cronograma?data_status=2026-03-21")
    passo("GET cronograma", st == 200 and not crn["achados"], f"fim {crn['fim']['data']}, achados {len(crn['achados'])}")
    por_edt = {a["edt"]: a for a in crn["atividades"]}
    fim0 = crn["fim"]["indice"]

    # --- simulação --------------------------------------------------------
    st, sim = chamar("POST", f"/contratos/{CT}/cronograma/simular", {"atividades": [{"edt": "1.3.2", "duracao": 30}]})
    passo("simular dur 30 em 1.3.2 empurra o fim", st == 200 and sim["impacto"]["delta_dias"] > 0,
          f"delta {sim.get('impacto', {}).get('delta_dias')} · entram {sim.get('impacto', {}).get('entraram_criticas')}")
    st, _ = chamar("POST", f"/contratos/{CT}/cronograma/simular",
                   {"vinculos": [{"pred_edt": "1.2.1", "suc_edt": "1.2.2"}, {"pred_edt": "1.2.2", "suc_edt": "1.2.1"}]})
    passo("simular ciclo → 422", st == 422)
    st, sim = chamar("POST", f"/contratos/{CT}/cronograma/simular",
                     {"atividades": [{"edt": "1.5.2", "restricao": "MSO", "restricao_data": "2026-05-04"}]})
    passo("simular MSO gera INV-CRN-007", st == 200 and any(a["invariante"] == "INV-CRN-007" for a in sim["achados"]))

    # --- gravação de atividade ------------------------------------------
    a133 = por_edt["1.3.3"]
    st, r = chamar("PUT", f"/atividades/{a133['id']}?data_status=2026-03-21", {
        "alteracao": {"duracao": 12, "deadline": "2026-03-20", "mod_qtd": 12, "responsavel": "Transportadora X",
                      "notas": "frete especial", "disciplina": "Logística"},
        "alterado_por": "smoke"})
    a = {x["edt"]: x for x in r.get("atividades", [])}.get("1.3.3", {})
    passo("PUT atividade grava e recalcula", st == 200 and a.get("duracao") == 12 and a.get("mod_qtd") == 12
          and any(x["invariante"] == "INV-CRN-008" for x in r["achados"]),
          f"fim {r.get('fim', {}).get('data')} · deadline gera INV-CRN-008: "
          f"{[x['referencia'] for x in r.get('achados', []) if x['invariante'] == 'INV-CRN-008']}")
    st, r = chamar("PUT", f"/atividades/{a133['id']}", {"alteracao": {"duracao": 20}, "alterado_por": "smoke"})
    passo("PUT dur 20 sem exceção → 422 (ck_atv_duracao_max)", st == 422, str(r.get("detail", ""))[:90])
    st, r = chamar("PUT", f"/atividades/{a133['id']}", {
        "alteracao": {"duracao": 20, "excecao": {"documento": "Carta 55", "autorizada_por": "Fiscal",
                                                  "justificativa": "transporte marítimo indivisível"}},
        "alterado_por": "smoke"})
    a = {x["edt"]: x for x in r.get("atividades", [])}.get("1.3.3", {})
    passo("PUT dur 20 com exceção grava", st == 200 and a.get("duracao") == 20 and a.get("excecao") is not None
          and not any(x["invariante"] == "INV-CRN-001" for x in r["achados"]))
    st, r = chamar("PUT", f"/atividades/{a133['id']}", {"alteracao": {"duracao": 8, "limpar_deadline": True, "remover_excecao": True},
                                                        "alterado_por": "smoke"})
    passo("PUT volta 1.3.3 ao original", st == 200 and r["fim"]["indice"] == fim0 and not r["achados"],
          f"fim {r.get('fim', {}).get('data')}")

    # --- rede ---------------------------------------------------------------
    st, r = chamar("PUT", f"/contratos/{CT}/vinculos", {
        "vinculos": [{"pred_edt": "1.2.1", "suc_edt": "1.2.2"}, {"pred_edt": "1.2.2", "suc_edt": "1.2.1"}],
        "alterado_por": "smoke"})
    passo("PUT rede com ciclo → 422", st == 422)
    vincs = [dict(v) for v in crn["vinculos"]]
    for v in vincs:
        if v["pred_edt"] == "1.3.1" and v["suc_edt"] == "1.3.2":
            v["lag"] = 3
    st, r = chamar("PUT", f"/contratos/{CT}/vinculos", {"vinculos": vincs, "alterado_por": "smoke"})
    lag = [v["lag"] for v in r.get("vinculos", []) if v["pred_edt"] == "1.3.1" and v["suc_edt"] == "1.3.2"]
    passo("PUT rede grava lag 3", st == 200 and lag == [3], f"fim {r.get('fim', {}).get('data')}")
    for v in vincs:
        if v["pred_edt"] == "1.3.1" and v["suc_edt"] == "1.3.2":
            v["lag"] = 2
    st, r = chamar("PUT", f"/contratos/{CT}/vinculos", {"vinculos": vincs, "alterado_por": "smoke"})
    passo("PUT rede volta ao original", st == 200 and r["fim"]["indice"] == fim0)

    # --- EAP -----------------------------------------------------------------
    st, r = chamar("POST", f"/contratos/{CT}/eap/ponderar", {"folhas": [{"edt": "1.2.1", "pontos": "1"}], "alterado_por": "smoke"})
    passo("ponderar sem fechar → 422 INV-EAP-001", st == 422 and "INV-EAP-001" in str(r.get("detail")))
    st, r = chamar("POST", f"/contratos/{CT}/eap/ponderar", {"folhas": [
        {"edt": "1.2.1", "pontos": "30000", "memoria_peso": "300 hh a 100 pontos/hh"},
        {"edt": "1.2.2", "pontos": "50000", "memoria_peso": "500 hh a 100 pontos/hh"}], "alterado_por": "smoke"})
    passo("ponderar fechado grava", st == 200 and r["diferenca"].startswith("0")
          and {n["edt"]: n["pontos"] for n in r["nos"]}["1.2.2"].startswith("50000"))
    chamar("POST", f"/contratos/{CT}/eap/ponderar", {"folhas": [
        {"edt": "1.2.1", "pontos": "40000", "memoria_peso": "400 hh a 100 pontos/hh"},
        {"edt": "1.2.2", "pontos": "40000", "memoria_peso": "400 hh a 100 pontos/hh"}], "alterado_por": "smoke"})

    # --- medição ------------------------------------------------------------
    no151 = por_edt["1.5.1"]["eap_no_id"]
    st, m = chamar("POST", "/medicoes", {
        "eap_no_id": no151, "data_corte": "2026-03-28", "criterio": "EQUIPAMENTO_MECANICO",
        "etapas_concluidas": ["Levantamento topográfico", "Montagem sobre a base"],
        "evidencia": "RDO 201 e foto", "criado_por": "smoke"})
    passo("medição por etapas → 201 com 25%", st == 201 and m.get("percentual_ac", "").startswith("0.25"), str(m)[:80])
    st, m2 = chamar("POST", "/medicoes", {
        "eap_no_id": no151, "data_corte": "2026-04-04", "criterio": "EQUIPAMENTO_MECANICO",
        "etapas_concluidas": ["Levantamento topográfico"], "evidencia": "x", "criado_por": "smoke"})
    passo("medição que retrocede → 422 INV-MED-003", st == 422 and "INV-MED-003" in str(m2.get("detail")))
    st, e = chamar("POST", f"/medicoes/{m['id']}/estorno", {"justificativa": "recontagem de etapas após auditoria", "criado_por": "smoke"})
    passo("estorno → 201 restaura 0", st == 201 and float(e.get("percentual_ac", "1")) == 0, str(e)[:80])
    st, _ = chamar("POST", f"/medicoes/{m['id']}/estorno", {"justificativa": "recontagem de etapas após auditoria", "criado_por": "smoke"})
    passo("estorno repetido → 409", st == 409)
    st, lista = chamar("GET", f"/contratos/{CT}/medicoes?limite=3")
    passo("medicoes lista com hash encadeado", st == 200 and lista[0]["tipo"] == "ESTORNO" and lista[0]["hash_anterior"] == lista[1]["hash"],
          f"{lista[0]['tipo']} {lista[0]['hash'][:8]} <- {lista[1]['hash'][:8]}")
    st, ck = chamar("GET", f"/contratos/{CT}/cockpit?data_corte=2026-04-04&referencia=VIGENTE")
    passo("cockpit VIGENTE responde", st == 200 and ck["cartoes"]["situacao"] in ("PLANO_DE_ACAO", "PLANO_DE_RECUPERACAO", "ATENCAO", "EM_DIA"),
          f"prev {ck['cartoes']['previsto_ac'][:6]} real {ck['cartoes']['real_ac'][:6]} idp {ck['cartoes']['idp']}")

    # --- importação ---------------------------------------------------------
    sujo = ("ERMHDR\t19.12\n%T\tTASK\n%F\ttask_id\twbs_id\ttask_name\ttask_type\ttarget_drtn_hr_cnt\tcstr_type\tphys_complete_pct\n"
            "%R\t1.3.1\t100\tAquisicao\tTT_Task\t80\t\t100\n%R\t1.3.2\t100\tFabricacao\tTT_Task\t320\t\t45\n"
            "%R\t9.9.9\t100\tFora da EAP\tTT_Task\t80\tCS_MSO\t0\n%T\tTASKPRED\n%F\ttask_pred_id\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt\n"
            "%R\t1\t1.3.2\t1.3.1\tPR_FS\t0\n%R\t2\t9.9.9\t1.3.2\tPR_FS\t0\n%R\t3\t1.3.1\t9.9.9\tPR_FS\t0\n"
            "%R\t4\t1.3.2\t9.9.9\tPR_SS\t-80\n%E\n").encode("cp1252")
    st, imp = chamar("POST", "/importacoes", arquivo=("sujo.xer", sujo), form={"contrato_id": CT, "criado_por": "smoke"})
    invs = sorted({a["invariante"] for a in imp.get("achados", [])})
    passo("importação suja → REJEITADO", st == 201 and imp["estado"] == "REJEITADO"
          and {"INV-CRN-002", "INV-CRN-005", "INV-CRN-006", "INV-IMP-003", "INV-IMP-005"} <= set(invs), str(invs))
    st, r = chamar("POST", f"/importacoes/{imp['id']}/aplicar?aplicada_por=smoke")
    passo("aplicar rejeitada → 409", st == 409)
    limpo = ("ERMHDR\t19.12\n%T\tTASK\n%F\ttask_id\twbs_id\ttask_name\ttask_type\ttarget_drtn_hr_cnt\tcstr_type\tphys_complete_pct\n"
             "%R\t1.3.1\t100\tAquisicao de equipamentos\tTT_Task\t80\t\t0\n%R\t1.3.2\t100\tFabricacao\tTT_Task\t120\t\t0\n"
             "%R\t1.3.3\t100\tTransporte e entrega\tTT_Task\t48\t\t0\n%T\tTASKPRED\n%F\ttask_pred_id\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt\n"
             "%R\t1\t1.3.2\t1.3.1\tPR_FS\t16\n%R\t2\t1.3.3\t1.3.2\tPR_FS\t0\n%E\n").encode("cp1252")
    st, imp = chamar("POST", "/importacoes", arquivo=("limpo.xer", limpo), form={"contrato_id": CT, "criado_por": "smoke"})
    passo("importação limpa → VALIDADO (só CRN-003 do fragmento? não: rede do contrato completa)", st == 201 and imp["estado"] == "VALIDADO",
          str(sorted({a["invariante"] for a in imp.get('achados', [])})))
    st, r = chamar("POST", f"/importacoes/{imp['id']}/aplicar?aplicada_por=smoke")
    passo("aplicar validada → APLICADO e recalcula", st == 200 and r["estado"] == "APLICADO" and r["atividades_tocadas"] == 3,
          f"fim {r.get('fim', {}).get('data')} · tocadas {r.get('atividades_tocadas')}")
    st, crn2 = chamar("GET", f"/contratos/{CT}/cronograma")
    d132 = {a["edt"]: a for a in crn2["atividades"]}["1.3.2"]["duracao"]
    passo("1.3.2 passou a 15 dias (120 h / 8)", d132 == 15, f"dur {d132} · fim {crn2['fim']['data']}")

    st, hist = chamar("GET", f"/contratos/{CT}/importacoes")
    passo("histórico de importações", st == 200 and len(hist) >= 2)

    print()
    print("SMOKE:", "PASS" if not FALHAS else f"FAIL em {len(FALHAS)} passo(s): {FALHAS}")
    sys.exit(1 if FALHAS else 0)


if __name__ == "__main__":
    main()
