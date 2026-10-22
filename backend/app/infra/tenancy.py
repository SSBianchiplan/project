"""Sessão com usuário autenticado e organização corrente declarada para a RLS.

`SET LOCAL` vale só dentro da transação — é o que impede que uma conexão
devolvida ao pool carregue a organização do request anterior. Sem o LOCAL,
o pool vira vazamento entre clientes, e o vazamento é silencioso.

Política de acesso (INV-ACL-001..003) aplicada aqui, num só lugar:
  - leitura (GET)              → qualquer papel
  - escrita (POST/PUT/DELETE)  → PLANEJADOR ou acima
  - linha de base (/baselines) → FISCAL, ADMIN ou ADMIN_MASTER
  - usuários e organizações    → tratados nas próprias rotas (ADMIN / master)
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Contexto, autenticar
from .db import Sessao

# Papel sem BYPASSRLS sob o qual toda transação de request roda. A conexão do
# pool pode até ser de superusuário (dev); o SET LOCAL ROLE derruba o
# privilégio dentro da transação e a RLS passa a valer de fato.
PAPEL_APP = os.environ.get("GPS_DB_ROLE", "gps_app")

# Modo de desenvolvimento/teste: aceita X-Organizacao sem token, como ADMIN.
# Nunca ligar em produção — por isso o padrão é desligado.
PERMITIR_HEADER_ORG = os.environ.get("GPS_PERMITIR_HEADER_ORG", "0") == "1"

ESCRITA = {"POST", "PUT", "PATCH", "DELETE"}


async def contexto_autenticado(
    request: Request,
    authorization: str | None = Header(default=None),
    x_organizacao: str | None = Header(default=None, alias="X-Organizacao"),
) -> Contexto:
    """Resolve quem chama. Token Bearer é o caminho normal; o header de
    organização só existe para desenvolvimento, com a chave ligada."""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        async with Sessao() as s:
            ctx = await autenticar(s, token)
        if ctx is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessão inválida ou expirada")
        return ctx
    if PERMITIR_HEADER_ORG and x_organizacao:
        try:
            org = UUID(x_organizacao)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "organização inválida") from e
        return Contexto(None, "dev@local", "modo dev", False, org, "ADMIN")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "autenticação necessária",
                        headers={"WWW-Authenticate": "Bearer"})


def _autorizar(ctx: Contexto, request: Request) -> None:
    caminho = request.url.path
    if request.method in ESCRITA:
        if "/baselines" in caminho:
            if ctx.papel not in ("FISCAL", "ADMIN", "ADMIN_MASTER"):
                raise HTTPException(status.HTTP_403_FORBIDDEN,
                                    "INV-ACL-003: aprovar linha de base é ato de FISCAL ou ADMIN")
        elif not ctx.pelo_menos("PLANEJADOR"):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"INV-ACL-002: papel {ctx.papel} não grava; mínimo PLANEJADOR")


async def sessao_tenant(
    request: Request, ctx: Contexto = Depends(contexto_autenticado),
) -> AsyncIterator[AsyncSession]:
    if ctx.organizacao_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "nenhuma organização ativa na sessão — escolha uma em /auth/organizacao")
    _autorizar(ctx, request)
    async with Sessao() as s:
        await s.begin()
        # SET não aceita parâmetro de bind; set_config(..., true) é o SET LOCAL
        # parametrizável. O nome do papel é configuração, não entrada do usuário.
        await s.execute(text(f"SET LOCAL ROLE {PAPEL_APP}"))
        await s.execute(
            text("SELECT set_config('app.organizacao_id', :org, true)"), {"org": str(ctx.organizacao_id)}
        )
        await s.execute(
            text("SELECT set_config('app.usuario', :u, true)"), {"u": ctx.email}
        )
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


def exigir(papel: str):
    async def _dep(ctx: Contexto = Depends(contexto_autenticado)) -> Contexto:
        if not ctx.pelo_menos(papel):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"INV-ACL-002: requer papel {papel}")
        return ctx
    return _dep
