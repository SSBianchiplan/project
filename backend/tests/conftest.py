"""Fixtures dos testes de integração: exigem um Postgres com as migrations
aplicadas (alembic upgrade head) e GPS_DSN / GPS_DSN_SYNC apontando para ele.

    GPS_DSN=postgresql+asyncpg://gps:gps@localhost:5433/gplansis \\
    GPS_DSN_SYNC=postgresql+psycopg://gps:gps@localhost:5433/gplansis pytest -q

Sem GPS_DSN definido, os testes de integração são pulados.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

DSN_SYNC = os.environ.get("GPS_DSN_SYNC", "")
if not os.environ.get("GPS_DSN") or not DSN_SYNC:
    pytest.skip("defina GPS_DSN e GPS_DSN_SYNC (Postgres com migrations aplicadas)", allow_module_level=True)


def _psycopg_dsn() -> str:
    return DSN_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def cliente() -> Iterator[object]:
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def banco() -> Iterator[object]:
    import psycopg
    with psycopg.connect(_psycopg_dsn(), autocommit=True) as conn:
        yield conn


@pytest.fixture
def cenario(banco: object) -> Iterator[dict]:
    """Duas organizações, um ADMIN em A, uma vítima só em B, um usuário em A e B,
    um usuário só em A e um master. Tudo com sufixo aleatório e apagado no fim."""
    from app.infra import auth

    suf = uuid.uuid4().hex[:8]
    senha = "Senha-Teste-123"
    h = auth.gerar_hash(senha, iteracoes=1000)
    cur = banco.cursor()

    def org(codigo: str) -> str:
        return cur.execute("INSERT INTO plan.organizacao (codigo, razao_social) VALUES (%s, %s) RETURNING id",
                           (f"{codigo}-{suf}".upper(), codigo)).fetchone()[0]

    def usuario(nome: str, master: bool = False) -> tuple[str, str]:
        email = f"{nome}-{suf}@exemplo.com.br"
        uid = cur.execute("INSERT INTO plan.usuario (email, nome, senha_hash, master) VALUES (%s, %s, %s, %s) RETURNING id",
                          (email, nome, h, master)).fetchone()[0]
        return uid, email

    def papel(uid: str, oid: str, p: str) -> None:
        cur.execute("INSERT INTO plan.usuario_papel (usuario_id, organizacao_id, papel) VALUES (%s, %s, %s)", (uid, oid, p))

    org_a, org_b = org("TA"), org("TB")
    admin_a = usuario("admin-a")
    vitima_b = usuario("vitima-b")
    multi = usuario("multi")
    so_a = usuario("so-a")
    master = usuario("master", master=True)
    papel(admin_a[0], org_a, "ADMIN")
    papel(vitima_b[0], org_b, "ADMIN")
    papel(multi[0], org_a, "LEITOR")
    papel(multi[0], org_b, "PLANEJADOR")
    papel(so_a[0], org_a, "LEITOR")
    cur.execute("DELETE FROM plan.login_falha")
    yield {"org_a": org_a, "org_b": org_b, "admin_a": admin_a, "vitima_b": vitima_b, "multi": multi,
           "so_a": so_a, "master": master, "senha": senha, "hash": h}
    ids = [admin_a[0], vitima_b[0], multi[0], so_a[0], master[0]]
    padrao = f"%-{suf}@exemplo.com.br"
    cur.execute("SELECT array_agg(id) FROM plan.usuario WHERE email LIKE %s", (padrao,))
    ids = list({*ids, *(cur.fetchone()[0] or [])})
    cur.execute("DELETE FROM plan.sessao_login WHERE usuario_id = ANY(%s)", (ids,))
    cur.execute("DELETE FROM plan.usuario_papel WHERE usuario_id = ANY(%s) OR concedido_por = ANY(%s)", (ids, ids))
    cur.execute("DELETE FROM plan.usuario WHERE id = ANY(%s)", (ids,))
    cur.execute("DELETE FROM plan.organizacao WHERE id = ANY(%s)", ([org_a, org_b],))
    cur.execute("DELETE FROM plan.login_falha")


def entrar(cliente: object, email: str, senha: str, org: str | None = None) -> dict:
    r = cliente.post("/api/v1/auth/login", json={"email": email, "senha": senha})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    cab = {"Authorization": f"Bearer {token}"}
    if org is not None:
        r = cliente.post("/api/v1/auth/organizacao", json={"organizacao_id": str(org)}, headers=cab)
        assert r.status_code == 200, r.text
    return cab
