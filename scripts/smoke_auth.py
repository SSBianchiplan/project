"""Smoke de autenticação, permissões e recursos.

    GPS_ADMIN_EMAIL=... GPS_ADMIN_SENHA=... python scripts/smoke_auth.py [http://localhost:8001]
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
    st, _ = chamar("GET", "/contratos")
    passo("sem token → 401", st == 401)
    st, r = chamar("POST", "/auth/login", {"email": EMAIL, "senha": "errada"})
    passo("senha errada → 401", st == 401)
    st, adm = chamar("POST", "/auth/login", {"email": EMAIL, "senha": SENHA})
    passo("login master → 200", st == 200 and adm["papel"] == "ADMIN_MASTER" and adm["organizacao_ativa"], f"{len(adm.get('organizacoes', []))} organizações")
    tk = adm["token"]
    st, eu = chamar("GET", "/auth/eu", token=tk)
    passo("GET /auth/eu", st == 200 and eu["usuario"]["email"] == EMAIL)
    st, cs = chamar("GET", "/contratos", token=tk)
    passo("master lista contratos da organização ativa", st == 200 and len(cs) >= 1)
    cid = cs[0]["id"]

    # usuários: leitor e planejador na organização ativa
    sufixo = os.urandom(2).hex()
    st, leitor = chamar("POST", "/usuarios", {"email": f"leitor-{sufixo}@teste.dev", "nome": "Leitor Teste", "papel": "LEITOR", "senha": "Leitor@123"}, token=tk)
    passo("criar leitor → 201", st == 201 and leitor["papel"] == "LEITOR")
    st, plan = chamar("POST", "/usuarios", {"email": f"plan-{sufixo}@teste.dev", "nome": "Planejador Teste", "papel": "PLANEJADOR"}, token=tk)
    passo("criar planejador sem senha → 201 com provisória", st == 201 and plan.get("senha_provisoria"))
    st, lista = chamar("GET", "/usuarios", token=tk)
    passo("listar usuários", st == 200 and any(u["email"] == leitor["email"] for u in lista))

    st, sl = chamar("POST", "/auth/login", {"email": leitor["email"], "senha": "Leitor@123"})
    passo("login leitor", st == 200 and sl["papel"] == "LEITOR")
    tl = sl["token"]
    st, _ = chamar("GET", f"/contratos/{cid}/cronograma", token=tl)
    passo("leitor lê cronograma → 200", st == 200)
    st, r = chamar("POST", f"/contratos/{cid}/cronograma/simular", {"atividades": []}, token=tl)
    passo("leitor simula (POST) → 403 INV-ACL-002", st == 403 and "INV-ACL-002" in str(r))
    st, _ = chamar("GET", "/usuarios", token=tl)
    passo("leitor lista usuários → 403", st == 403)

    st, sp = chamar("POST", "/auth/login", {"email": plan["email"], "senha": plan["senha_provisoria"]})
    passo("login planejador com provisória (deve trocar)", st == 200 and sp["usuario"]["deve_trocar_senha"] is True)
    tp = sp["token"]
    st, _ = chamar("POST", "/auth/senha", {"atual": plan["senha_provisoria"], "nova": "NovaSenha@123"}, token=tp)
    passo("planejador troca senha → 204", st == 204)
    st, r = chamar("POST", f"/contratos/{cid}/cronograma/simular", {"atividades": []}, token=tp)
    passo("planejador simula → 200", st == 200)
    st, r = chamar("POST", f"/contratos/{cid}/baselines", {"emissao": "x", "nota_lvp": "0.9", "avaliado_por": "a", "aprovada_por": "b"}, token=tp)
    passo("planejador aprova baseline → 403 INV-ACL-003", st == 403 and "INV-ACL-003" in str(r))

    # recursos (como master)
    st, rec = chamar("POST", f"/contratos/{cid}/recursos", {"codigo": f"SOLD{sufixo[:2]}", "nome": "Soldador", "tipo": "MOD", "unidade": "pessoa", "capacidade_dia": "4", "custo_hora": "85", "horas_dia": "8", "ativo": True}, token=tk)
    passo("criar recurso → 201", st == 201, str(rec)[:80])
    st, crn = chamar("GET", f"/contratos/{cid}/cronograma", token=tk)
    ativs = [a for a in crn["atividades"] if not a["marco"]]
    a1, a2 = ativs[0], ativs[1]
    st, _ = chamar("PUT", f"/atividades/{a1['id']}/alocacoes", {"alocacoes": [{"recurso_id": rec["id"], "quantidade_dia": "3", "observacao": "frente A"}]}, token=tk)
    st2, _ = chamar("PUT", f"/atividades/{a2['id']}/alocacoes", {"alocacoes": [{"recurso_id": rec["id"], "quantidade_dia": "3", "observacao": "frente B"}]}, token=tk)
    passo("alocar recurso em duas atividades → 200", st == 200 and st2 == 200)
    st, painel = chamar("GET", f"/contratos/{cid}/recursos", token=tk)
    h = next(x for x in painel["histogramas"] if x["recurso"]["id"] == rec["id"])
    passo("histograma calculado", st == 200 and float(h["total_horas"]) > 0, f"{h['total_horas']} hh · R$ {h['custo_total']} · superalocadas {len(h['superalocadas'])}")
    marco = next(a for a in crn["atividades"] if a["marco"])
    st, r = chamar("PUT", f"/atividades/{marco['id']}/alocacoes", {"alocacoes": [{"recurso_id": rec["id"], "quantidade_dia": "1", "observacao": ""}]}, token=tk)
    passo("alocar em marco → 422 INV-REC-003", st == 422)
    st, r = chamar("DELETE", f"/recursos/{rec['id']}", token=tk)
    passo("excluir recurso alocado → 409", st == 409)
    st, _ = chamar("PUT", f"/atividades/{a1['id']}/alocacoes", {"alocacoes": []}, token=tk)
    st, _ = chamar("PUT", f"/atividades/{a2['id']}/alocacoes", {"alocacoes": []}, token=tk)
    st, _ = chamar("DELETE", f"/recursos/{rec['id']}", token=tk)
    passo("excluir recurso sem alocação → 204", st == 204)

    # limpeza: desativa e revoga os usuários de teste
    for u in (leitor, plan):
        chamar("DELETE", f"/usuarios/{u['id']}/papel", token=tk)
        chamar("PUT", f"/usuarios/{u['id']}", {"ativo": False}, token=tk)
    st, _ = chamar("GET", "/contratos", token=tl)
    passo("leitor desativado perde a sessão → 401", st == 401)
    st, _ = chamar("POST", "/auth/logout", token=tk)
    st2, _ = chamar("GET", "/contratos", token=tk)
    passo("logout revoga o token → 401", st == 204 and st2 == 401)

    print()
    print("SMOKE AUTH:", "PASS" if not FALHAS else f"FAIL em {FALHAS}")
    sys.exit(1 if FALHAS else 0)


if __name__ == "__main__":
    main()
