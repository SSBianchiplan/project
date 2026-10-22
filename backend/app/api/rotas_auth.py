"""Login, sessão, troca de organização, usuários e organizações.

Usuários são globais; o papel é por organização. ADMIN administra usuários da
própria organização; ADMIN_MASTER administra tudo, inclusive organizações.
"""
from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..infra import auth
from ..infra.db import Sessao
from ..infra.tenancy import contexto_autenticado, exigir

router = APIRouter(prefix="/api/v1")


async def sessao_livre() -> AsyncIterator[AsyncSession]:
    """Sessão sem tenant: identidade é lida antes da organização existir."""
    async with Sessao() as s:
        await s.begin()
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginEntrada(Base):
    email: EmailStr
    senha: str = Field(min_length=1)


class OrganizacaoResumo(Base):
    id: UUID
    codigo: str
    razao_social: str
    papel: str


class UsuarioSaida(Base):
    id: UUID
    email: str
    nome: str
    master: bool
    ativo: bool
    deve_trocar_senha: bool
    papel: str | None = None
    ultimo_login: str | None = None


class UsuarioCriado(UsuarioSaida):
    senha_provisoria: str | None = None


class SessaoSaida(Base):
    token: str
    usuario: UsuarioSaida
    organizacao_ativa: UUID | None
    papel: str
    organizacoes: list[OrganizacaoResumo]


class TrocaOrganizacao(Base):
    organizacao_id: UUID


class TrocaSenha(Base):
    atual: str
    nova: str = Field(min_length=8)


class UsuarioEntrada(Base):
    email: EmailStr
    nome: str = Field(min_length=1)
    papel: str = Field(pattern="^(ADMIN|PLANEJADOR|FISCAL|LEITOR)$")
    senha: str | None = Field(default=None, min_length=8)


class UsuarioAlteracao(Base):
    nome: str | None = None
    papel: str | None = Field(default=None, pattern="^(ADMIN|PLANEJADOR|FISCAL|LEITOR)$")
    ativo: bool | None = None


class OrganizacaoEntrada(Base):
    codigo: str = Field(min_length=2, max_length=30)
    razao_social: str = Field(min_length=1)


def _usuario(row: dict, papel: str | None = None) -> UsuarioSaida:
    return UsuarioSaida(
        id=row["id"], email=row["email"], nome=row["nome"], master=bool(row["master"]),
        ativo=bool(row["ativo"]), deve_trocar_senha=bool(row["deve_trocar_senha"]), papel=papel,
        ultimo_login=row["ultimo_login"].isoformat() if row.get("ultimo_login") else None)


async def _montar_sessao(s: AsyncSession, token: str) -> SessaoSaida:
    ctx = await auth.autenticar(s, token)
    assert ctx is not None and ctx.usuario_id is not None
    u = (await s.execute(text("SELECT * FROM plan.usuario WHERE id = :id"), {"id": ctx.usuario_id})).mappings().one()
    orgs = await auth.organizacoes_de(s, ctx.usuario_id, ctx.master)
    return SessaoSaida(
        token=token, usuario=_usuario(dict(u), ctx.papel), organizacao_ativa=ctx.organizacao_id, papel=ctx.papel,
        organizacoes=[OrganizacaoResumo(**o) for o in orgs])


async def _papeis_do_alvo(s: AsyncSession, usuario_id: UUID) -> list[UUID]:
    """Organizações em que o usuário-alvo tem papel."""
    rows = (await s.execute(text("SELECT organizacao_id FROM plan.usuario_papel WHERE usuario_id = :u"),
                            {"u": usuario_id})).scalars().all()
    return [UUID(str(o)) for o in rows]


async def _exigir_alvo_na_org(
    s: AsyncSession, ctx: auth.Contexto, usuario_id: UUID, *, atributo_global: bool,
) -> dict:
    """INV-ACL-004 (auditoria 10/2026, A3): ADMIN só administra quem tem papel na
    organização ativa. Atributo GLOBAL do usuário (senha, nome, ativo) só pode ser
    mexido por ADMIN se o alvo pertencer EXCLUSIVAMENTE à organização ativa; quem
    está em várias organizações (ou é master) só o master altera.
    O alvo de outra organização responde 404, como se não existisse."""
    u = (await s.execute(text("SELECT * FROM plan.usuario WHERE id = :id"), {"id": usuario_id})).mappings().one_or_none()
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário não encontrado")
    if ctx.master:
        return dict(u)
    if u["master"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "só master altera master")
    orgs = await _papeis_do_alvo(s, usuario_id)
    if ctx.organizacao_id is None or ctx.organizacao_id not in orgs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário não encontrado")
    if atributo_global and len(orgs) > 1:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "INV-ACL-004: usuário pertence a outras organizações; só o admin master altera senha, nome ou situação")
    return dict(u)


# ------------------------------------------------------------------- login

# M19 (auditoria 10/2026): limite de tentativas falhas por conta e por IP numa
# janela deslizante. O contador fica no banco (plan.login_falha) para valer entre
# os workers do uvicorn. Vale igual para e-mail inexistente: o 429 não revela conta.
LOGIN_MAX_CONTA = int(os.environ.get("GPS_LOGIN_MAX_CONTA", "5"))
LOGIN_MAX_IP = int(os.environ.get("GPS_LOGIN_MAX_IP", "30"))
LOGIN_JANELA_MIN = int(os.environ.get("GPS_LOGIN_JANELA_MIN", "15"))
# hash de referência: e-mail inexistente custa o mesmo pbkdf2 que senha errada
_HASH_FALSO = auth.gerar_hash(secrets.token_hex(16))


def _ip(request: Request) -> str:
    # uvicorn roda com --proxy-headers --forwarded-allow-ips 127.0.0.1: client.host
    # já é o IP que o Caddy viu (o Caddy sobrescreve o X-Forwarded-For recebido).
    return request.client.host if request.client else "desconhecido"


async def _bloqueado(s: AsyncSession, conta: str, ip: str) -> bool:
    row = (await s.execute(text("""
        SELECT count(*) FILTER (WHERE chave = :c) AS conta, count(*) FILTER (WHERE chave = :i) AS ip
          FROM plan.login_falha
         WHERE chave IN (:c, :i) AND em > now() - make_interval(mins => :j)"""),
        {"c": conta, "i": ip, "j": LOGIN_JANELA_MIN})).mappings().one()
    return int(row["conta"]) >= LOGIN_MAX_CONTA or int(row["ip"]) >= LOGIN_MAX_IP


async def _registrar_falha(conta: str, ip: str) -> None:
    # transação própria: a da rota é desfeita quando a rota responde 401
    async with Sessao() as s2, s2.begin():
        await s2.execute(text("INSERT INTO plan.login_falha (chave) VALUES (:c), (:i)"), {"c": conta, "i": ip})
        await s2.execute(text("DELETE FROM plan.login_falha WHERE em < now() - interval '1 day'"))


@router.post("/auth/login", response_model=SessaoSaida)
async def login(entrada: LoginEntrada, request: Request, s: AsyncSession = Depends(sessao_livre, scope="function")) -> SessaoSaida:
    email = entrada.email.lower()
    chave_conta, chave_ip = f"conta:{email}", f"ip:{_ip(request)}"
    if await _bloqueado(s, chave_conta, chave_ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"muitas tentativas; aguarde {LOGIN_JANELA_MIN} minutos e tente de novo",
                            headers={"Retry-After": str(LOGIN_JANELA_MIN * 60)})
    u = (await s.execute(text("SELECT * FROM plan.usuario WHERE email = :e"),
                         {"e": email})).mappings().one_or_none()
    senha_ok = auth.conferir_senha(entrada.senha, u["senha_hash"] if u is not None else _HASH_FALSO)
    if u is None or not u["ativo"] or not senha_ok:
        await _registrar_falha(chave_conta, chave_ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "e-mail ou senha inválidos")
    await s.execute(text("DELETE FROM plan.login_falha WHERE chave = :c"), {"c": chave_conta})
    orgs = await auth.organizacoes_de(s, u["id"], bool(u["master"]))
    org_ativa = UUID(str(orgs[0]["id"])) if orgs else None
    token = await auth.abrir_sessao(s, u["id"], org_ativa, request.headers.get("user-agent", ""))
    return await _montar_sessao(s, token)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(authorization: str | None = Header(default=None), s: AsyncSession = Depends(sessao_livre, scope="function")) -> None:
    if authorization and authorization.lower().startswith("bearer "):
        await s.execute(text("UPDATE plan.sessao_login SET revogada_em = now() WHERE token_hash = :h"),
                        {"h": auth.hash_token(authorization[7:].strip())})


@router.get("/auth/eu", response_model=SessaoSaida)
async def eu(authorization: str | None = Header(default=None), s: AsyncSession = Depends(sessao_livre, scope="function")) -> SessaoSaida:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "autenticação necessária")
    token = authorization[7:].strip()
    if await auth.autenticar(s, token) is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessão inválida ou expirada")
    return await _montar_sessao(s, token)


@router.post("/auth/organizacao", response_model=SessaoSaida)
async def trocar_organizacao(
    entrada: TrocaOrganizacao, ctx: auth.Contexto = Depends(contexto_autenticado),
    authorization: str | None = Header(default=None), s: AsyncSession = Depends(sessao_livre, scope="function"),
) -> SessaoSaida:
    if ctx.usuario_id is None or not authorization:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessão sem usuário")
    orgs = await auth.organizacoes_de(s, ctx.usuario_id, ctx.master)
    if not any(UUID(str(o["id"])) == entrada.organizacao_id for o in orgs):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "usuário não pertence a essa organização")
    await s.execute(text("UPDATE plan.sessao_login SET organizacao_id = :o WHERE id = :id"),
                    {"o": entrada.organizacao_id, "id": ctx.sessao_id})
    return await _montar_sessao(s, authorization[7:].strip())


@router.post("/auth/senha", status_code=status.HTTP_204_NO_CONTENT)
async def trocar_senha(
    entrada: TrocaSenha, ctx: auth.Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_livre, scope="function"),
) -> None:
    u = (await s.execute(text("SELECT senha_hash FROM plan.usuario WHERE id = :id"),
                         {"id": ctx.usuario_id})).mappings().one_or_none()
    if u is None or not auth.conferir_senha(entrada.atual, u["senha_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "senha atual não confere")
    await s.execute(text("UPDATE plan.usuario SET senha_hash = :h, deve_trocar_senha = false WHERE id = :id"),
                    {"h": auth.gerar_hash(entrada.nova), "id": ctx.usuario_id})


# ---------------------------------------------------------------- usuários

@router.get("/usuarios", response_model=list[UsuarioSaida])
async def listar_usuarios(ctx: auth.Contexto = Depends(exigir("ADMIN")), s: AsyncSession = Depends(sessao_livre, scope="function")) -> list[UsuarioSaida]:
    if ctx.master and ctx.organizacao_id is None:
        rows = (await s.execute(text("SELECT *, NULL::text AS papel FROM plan.usuario ORDER BY email"))).mappings().all()
    else:
        rows = (await s.execute(text("""
            SELECT u.*, p.papel::text AS papel FROM plan.usuario u
            LEFT JOIN plan.usuario_papel p ON p.usuario_id = u.id AND p.organizacao_id = :o
            WHERE u.master OR p.usuario_id IS NOT NULL ORDER BY u.email"""), {"o": ctx.organizacao_id})).mappings().all()
    return [_usuario(dict(r), r["papel"] or ("ADMIN_MASTER" if r["master"] else None)) for r in rows]


@router.post("/usuarios", response_model=UsuarioCriado, status_code=status.HTTP_201_CREATED)
async def criar_usuario(
    entrada: UsuarioEntrada, ctx: auth.Contexto = Depends(exigir("ADMIN")), s: AsyncSession = Depends(sessao_livre, scope="function"),
) -> UsuarioCriado:
    """Cria o usuário (se não existe) e concede o papel na organização ativa.
    Sem senha informada, gera uma provisória e devolve em `senha_provisoria`."""
    if ctx.organizacao_id is None:
        raise HTTPException(409, "escolha uma organização ativa antes de conceder papéis")
    email = entrada.email.lower()
    u = (await s.execute(text("SELECT * FROM plan.usuario WHERE email = :e"), {"e": email})).mappings().one_or_none()
    provisoria = None
    if u is None:
        senha = entrada.senha or secrets.token_urlsafe(9)
        provisoria = None if entrada.senha else senha
        u = (await s.execute(text("""
            INSERT INTO plan.usuario (email, nome, senha_hash, deve_trocar_senha)
            VALUES (:e, :n, :h, :t) RETURNING *"""), {
            "e": email, "n": entrada.nome, "h": auth.gerar_hash(senha), "t": provisoria is not None})).mappings().one()
    elif not ctx.master and ctx.organizacao_id not in await _papeis_do_alvo(s, u["id"]):
        # A3: e-mail já cadastrado em OUTRA organização. Vincular conta existente
        # é ato do master; senão o ADMIN de A puxa o usuário de B para A e depois
        # redefine a senha dele.
        raise HTTPException(409, "e-mail já cadastrado; peça ao admin master para vincular esse usuário à organização")
    if u["master"]:
        raise HTTPException(409, "usuário master não recebe papel por organização")
    await s.execute(text("""
        INSERT INTO plan.usuario_papel (usuario_id, organizacao_id, papel, concedido_por)
        VALUES (:u, :o, :p, :por)
        ON CONFLICT (usuario_id, organizacao_id) DO UPDATE SET papel = EXCLUDED.papel, concedido_por = EXCLUDED.concedido_por"""),
        {"u": u["id"], "o": ctx.organizacao_id, "p": entrada.papel, "por": ctx.usuario_id})
    return UsuarioCriado(**_usuario(dict(u), entrada.papel).model_dump(), senha_provisoria=provisoria)


@router.put("/usuarios/{usuario_id}", response_model=UsuarioSaida)
async def alterar_usuario(
    usuario_id: UUID, entrada: UsuarioAlteracao, ctx: auth.Contexto = Depends(exigir("ADMIN")),
    s: AsyncSession = Depends(sessao_livre, scope="function"),
) -> UsuarioSaida:
    u = await _exigir_alvo_na_org(s, ctx, usuario_id,
                                  atributo_global=entrada.nome is not None or entrada.ativo is not None)
    if entrada.nome is not None:
        await s.execute(text("UPDATE plan.usuario SET nome = :n WHERE id = :id"), {"n": entrada.nome, "id": usuario_id})
    if entrada.ativo is not None:
        if usuario_id == ctx.usuario_id and not entrada.ativo:
            raise HTTPException(409, "não desative a si mesmo")
        await s.execute(text("UPDATE plan.usuario SET ativo = :a WHERE id = :id"), {"a": entrada.ativo, "id": usuario_id})
        if not entrada.ativo:
            await s.execute(text("UPDATE plan.sessao_login SET revogada_em = now() WHERE usuario_id = :id AND revogada_em IS NULL"),
                            {"id": usuario_id})
    if entrada.papel is not None and ctx.organizacao_id is not None and not u["master"]:
        await s.execute(text("""
            INSERT INTO plan.usuario_papel (usuario_id, organizacao_id, papel, concedido_por)
            VALUES (:u, :o, :p, :por)
            ON CONFLICT (usuario_id, organizacao_id) DO UPDATE SET papel = EXCLUDED.papel, concedido_por = EXCLUDED.concedido_por"""),
            {"u": usuario_id, "o": ctx.organizacao_id, "p": entrada.papel, "por": ctx.usuario_id})
    u = (await s.execute(text("SELECT * FROM plan.usuario WHERE id = :id"), {"id": usuario_id})).mappings().one()
    papel = (await s.execute(text("SELECT papel::text FROM plan.usuario_papel WHERE usuario_id = :u AND organizacao_id = :o"),
                             {"u": usuario_id, "o": ctx.organizacao_id})).scalar_one_or_none()
    return _usuario(dict(u), "ADMIN_MASTER" if u["master"] else papel)


@router.delete("/usuarios/{usuario_id}/papel", status_code=status.HTTP_204_NO_CONTENT)
async def revogar_papel(
    usuario_id: UUID, ctx: auth.Contexto = Depends(exigir("ADMIN")), s: AsyncSession = Depends(sessao_livre, scope="function"),
) -> None:
    await s.execute(text("DELETE FROM plan.usuario_papel WHERE usuario_id = :u AND organizacao_id = :o"),
                    {"u": usuario_id, "o": ctx.organizacao_id})


@router.post("/usuarios/{usuario_id}/senha", response_model=dict)
async def redefinir_senha(
    usuario_id: UUID, ctx: auth.Contexto = Depends(exigir("ADMIN")), s: AsyncSession = Depends(sessao_livre, scope="function"),
) -> dict:
    """Gera senha provisória; o usuário troca no próximo login."""
    await _exigir_alvo_na_org(s, ctx, usuario_id, atributo_global=True)
    senha = secrets.token_urlsafe(9)
    await s.execute(text("UPDATE plan.usuario SET senha_hash = :h, deve_trocar_senha = true WHERE id = :id"),
                    {"h": auth.gerar_hash(senha), "id": usuario_id})
    await s.execute(text("UPDATE plan.sessao_login SET revogada_em = now() WHERE usuario_id = :id AND revogada_em IS NULL"),
                    {"id": usuario_id})
    return {"senha_provisoria": senha}


# ------------------------------------------------------------ organizações

@router.get("/organizacoes", response_model=list[OrganizacaoResumo])
async def listar_organizacoes(ctx: auth.Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_livre, scope="function")) -> list[OrganizacaoResumo]:
    if ctx.usuario_id is None:
        return []
    return [OrganizacaoResumo(**o) for o in await auth.organizacoes_de(s, ctx.usuario_id, ctx.master)]


@router.post("/organizacoes", response_model=OrganizacaoResumo, status_code=status.HTTP_201_CREATED)
async def criar_organizacao(
    entrada: OrganizacaoEntrada, ctx: auth.Contexto = Depends(exigir("ADMIN_MASTER")), s: AsyncSession = Depends(sessao_livre, scope="function"),
) -> OrganizacaoResumo:
    row = (await s.execute(text("""
        INSERT INTO plan.organizacao (codigo, razao_social) VALUES (:c, :r) RETURNING id, codigo, razao_social"""),
        {"c": entrada.codigo.strip().upper(), "r": entrada.razao_social})).mappings().one()
    return OrganizacaoResumo(id=row["id"], codigo=row["codigo"], razao_social=row["razao_social"], papel="ADMIN_MASTER")


__all__ = ["date", "router"]
