"""Provas das correções da auditoria de segurança 10/2026 (A3, A4, M19 e erros do banco)."""
from __future__ import annotations

import pytest

from .conftest import entrar


def _hash_de(banco: object, uid: str) -> str:
    return banco.execute("SELECT senha_hash FROM plan.usuario WHERE id = %s", (uid,)).fetchone()[0]


def _papeis(banco: object, uid: str) -> set[str]:
    return {str(r[0]) for r in banco.execute("SELECT organizacao_id FROM plan.usuario_papel WHERE usuario_id = %s", (uid,))}


# ------------------------------------------------------------------ A3

def test_admin_de_a_nao_redefine_senha_de_usuario_de_b(cliente, banco, cenario):
    cab = entrar(cliente, cenario["admin_a"][1], cenario["senha"], cenario["org_a"])
    antes = _hash_de(banco, cenario["vitima_b"][0])
    r = cliente.post(f"/api/v1/usuarios/{cenario['vitima_b'][0]}/senha", headers=cab)
    assert r.status_code == 404
    assert "senha_provisoria" not in r.text
    assert _hash_de(banco, cenario["vitima_b"][0]) == antes


def test_admin_de_a_nao_altera_nem_puxa_usuario_de_b(cliente, banco, cenario):
    cab = entrar(cliente, cenario["admin_a"][1], cenario["senha"], cenario["org_a"])
    alvo = cenario["vitima_b"][0]
    assert cliente.put(f"/api/v1/usuarios/{alvo}", json={"nome": "x"}, headers=cab).status_code == 404
    assert cliente.put(f"/api/v1/usuarios/{alvo}", json={"ativo": False}, headers=cab).status_code == 404
    assert cliente.put(f"/api/v1/usuarios/{alvo}", json={"papel": "LEITOR"}, headers=cab).status_code == 404
    assert _papeis(banco, alvo) == {str(cenario["org_b"])}


def test_admin_de_a_nao_cadastra_email_existente_de_outra_org(cliente, banco, cenario):
    cab = entrar(cliente, cenario["admin_a"][1], cenario["senha"], cenario["org_a"])
    r = cliente.post("/api/v1/usuarios", json={"email": cenario["vitima_b"][1], "nome": "x", "papel": "LEITOR"}, headers=cab)
    assert r.status_code == 409
    assert _papeis(banco, cenario["vitima_b"][0]) == {str(cenario["org_b"])}


def test_cadeia_completa_de_tomada_de_conta_falha(cliente, banco, cenario):
    """O ataque do relatório: cadastrar o e-mail da vítima, redefinir a senha e entrar."""
    cab = entrar(cliente, cenario["admin_a"][1], cenario["senha"], cenario["org_a"])
    cliente.post("/api/v1/usuarios", json={"email": cenario["vitima_b"][1], "nome": "x", "papel": "LEITOR"}, headers=cab)
    r = cliente.post(f"/api/v1/usuarios/{cenario['vitima_b'][0]}/senha", headers=cab)
    assert r.status_code in (403, 404)
    # a vítima continua entrando com a senha dela
    entrar(cliente, cenario["vitima_b"][1], cenario["senha"])


def test_usuario_em_varias_orgs_so_master_mexe_no_global(cliente, banco, cenario):
    cab = entrar(cliente, cenario["admin_a"][1], cenario["senha"], cenario["org_a"])
    alvo = cenario["multi"][0]
    assert cliente.post(f"/api/v1/usuarios/{alvo}/senha", headers=cab).status_code == 403
    assert cliente.put(f"/api/v1/usuarios/{alvo}", json={"nome": "x"}, headers=cab).status_code == 403
    assert cliente.put(f"/api/v1/usuarios/{alvo}", json={"ativo": False}, headers=cab).status_code == 403
    # papel é da organização ativa: o ADMIN de A pode
    r = cliente.put(f"/api/v1/usuarios/{alvo}", json={"papel": "FISCAL"}, headers=cab)
    assert r.status_code == 200 and r.json()["papel"] == "FISCAL"
    cab_m = entrar(cliente, cenario["master"][1], cenario["senha"], cenario["org_a"])
    r = cliente.post(f"/api/v1/usuarios/{alvo}/senha", headers=cab_m)
    assert r.status_code == 200 and r.json()["senha_provisoria"]


def test_admin_continua_administrando_a_propria_org(cliente, banco, cenario):
    cab = entrar(cliente, cenario["admin_a"][1], cenario["senha"], cenario["org_a"])
    alvo = cenario["so_a"][0]
    r = cliente.post(f"/api/v1/usuarios/{alvo}/senha", headers=cab)
    assert r.status_code == 200 and r.json()["senha_provisoria"]
    assert cliente.put(f"/api/v1/usuarios/{alvo}", json={"nome": "Novo nome"}, headers=cab).status_code == 200
    novo = f"novo-{cenario['admin_a'][1]}"
    r = cliente.post("/api/v1/usuarios", json={"email": novo, "nome": "Novo", "papel": "LEITOR"}, headers=cab)
    assert r.status_code == 201 and r.json()["senha_provisoria"]
    # re-conceder papel a quem já é da organização continua valendo
    r = cliente.post("/api/v1/usuarios", json={"email": cenario["so_a"][1], "nome": "x", "papel": "FISCAL"}, headers=cab)
    assert r.status_code == 201


def test_admin_nao_mexe_em_master(cliente, cenario):
    cab = entrar(cliente, cenario["admin_a"][1], cenario["senha"], cenario["org_a"])
    assert cliente.post(f"/api/v1/usuarios/{cenario['master'][0]}/senha", headers=cab).status_code == 403


# ------------------------------------------------------------------ A4

@pytest.mark.parametrize("tabela", ["usuario", "usuario_papel", "sessao_login", "organizacao",
                                    "usuario_organizacao", "login_falha", "nao_existe"])
def test_explorador_recusa_tabelas_de_identidade(cliente, cenario, tabela):
    cab = entrar(cliente, cenario["admin_a"][1], cenario["senha"], cenario["org_a"])
    assert cliente.get(f"/api/v1/admin/tabelas/{tabela}/linhas", headers=cab).status_code == 404
    r = cliente.get(f"/api/v1/admin/tabelas/{tabela}/exportar", headers=cab)
    assert r.status_code == 404
    assert b"pbkdf2" not in r.content


def test_explorador_continua_abrindo_tabela_de_contrato(cliente, cenario):
    cab = entrar(cliente, cenario["admin_a"][1], cenario["senha"], cenario["org_a"])
    r = cliente.get("/api/v1/admin/tabelas/contrato/linhas", headers=cab)
    assert r.status_code == 200 and r.json()["tabela"] == "contrato"
    nomes = {t["tabela"] for t in cliente.get("/api/v1/admin/tabelas", headers=cab).json()}
    assert "contrato" in nomes
    assert nomes.isdisjoint({"usuario", "usuario_papel", "sessao_login", "organizacao", "login_falha"})


def test_papel_da_aplicacao_nao_le_identidade(banco):
    import psycopg
    for tabela in ("usuario", "usuario_papel", "sessao_login", "login_falha"):
        with banco.transaction():
            banco.execute("SET LOCAL ROLE gps_app")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                banco.execute(f"SELECT 1 FROM plan.{tabela} LIMIT 1")


def test_rls_auditoria_continua_vazia(banco):
    assert banco.execute("SELECT count(*) FROM plan.v_rls_auditoria").fetchone()[0] == 0


# ------------------------------------------------------------------ M19

def test_login_bloqueia_conta_apos_falhas(cliente, cenario, monkeypatch):
    from app.api import rotas_auth
    monkeypatch.setattr(rotas_auth, "LOGIN_MAX_CONTA", 3)
    email = cenario["so_a"][1]
    for _ in range(3):
        assert cliente.post("/api/v1/auth/login", json={"email": email, "senha": "errada"}).status_code == 401
    r = cliente.post("/api/v1/auth/login", json={"email": email, "senha": cenario["senha"]})
    assert r.status_code == 429 and r.headers.get("retry-after")
    # outra conta, mesmo IP, abaixo do limite de IP: entra
    entrar(cliente, cenario["admin_a"][1], cenario["senha"])


def test_login_bloqueia_email_inexistente_igual(cliente, cenario, monkeypatch):
    from app.api import rotas_auth
    monkeypatch.setattr(rotas_auth, "LOGIN_MAX_CONTA", 2)
    for _ in range(2):
        assert cliente.post("/api/v1/auth/login", json={"email": "ninguem@exemplo.com.br", "senha": "x"}).status_code == 401
    assert cliente.post("/api/v1/auth/login", json={"email": "ninguem@exemplo.com.br", "senha": "x"}).status_code == 429


def test_login_bloqueia_ip_apos_falhas(cliente, cenario, monkeypatch):
    from app.api import rotas_auth
    monkeypatch.setattr(rotas_auth, "LOGIN_MAX_IP", 4)
    for i in range(4):
        cliente.post("/api/v1/auth/login", json={"email": f"x{i}@exemplo.com.br", "senha": "x"})
    r = cliente.post("/api/v1/auth/login", json={"email": cenario["admin_a"][1], "senha": cenario["senha"]})
    assert r.status_code == 429


def test_login_com_sucesso_zera_contador_da_conta(cliente, banco, cenario, monkeypatch):
    from app.api import rotas_auth
    monkeypatch.setattr(rotas_auth, "LOGIN_MAX_CONTA", 3)
    email = cenario["so_a"][1]
    for _ in range(2):
        cliente.post("/api/v1/auth/login", json={"email": email, "senha": "errada"})
    entrar(cliente, email, cenario["senha"])
    n = banco.execute("SELECT count(*) FROM plan.login_falha WHERE chave = %s", (f"conta:{email}",)).fetchone()[0]
    assert n == 0


# ---------------------------------------------------- erros do banco

class _Orig(Exception):
    def __init__(self, msg: str, sqlstate: str) -> None:
        super().__init__(msg)
        self.sqlstate = sqlstate


def _resposta(msg: str, sqlstate: str) -> tuple[int, str]:
    import asyncio
    import json

    from sqlalchemy.exc import DBAPIError
    from starlette.requests import Request

    from app.main import invariante_violada
    exc = DBAPIError("SELECT ...", {}, _Orig(msg, sqlstate))
    req = Request({"type": "http", "method": "GET", "path": "/api/v1/x", "headers": [], "query_string": b""})
    r = asyncio.run(invariante_violada(req, exc))
    return r.status_code, json.loads(r.body)["detail"]


def test_erro_interno_do_banco_nao_vaza():
    status, detalhe = _resposta('relation "plan.segredo" does not exist', "42P01")
    assert status == 422 and "segredo" not in detalhe and "42P01" in detalhe
    status, detalhe = _resposta("permission denied for table usuario", "42501")
    assert status == 403 and "usuario" not in detalhe


def test_recusa_de_invariante_continua_explicada():
    assert _resposta("INV-MED-001: medição só com evidência", "P0001") == (422, "INV-MED-001: medição só com evidência")
    status, detalhe = _resposta('duplicate key value violates unique constraint "uq_contrato_codigo"', "23505")
    assert status == 422 and "uq_contrato_codigo" in detalhe
