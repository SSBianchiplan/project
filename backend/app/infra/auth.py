"""Autenticação: hash de senha (stdlib), tokens opacos e contexto do usuário.

Sem dependência externa: pbkdf2_hmac para senha, sha256 do token para a
sessão. O token nunca é gravado — só o hash.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ITERACOES = 210_000
VALIDADE = timedelta(hours=int(os.environ.get("GPS_SESSAO_HORAS", "12")))

# hierarquia de papéis: quem está acima pode o que quem está abaixo pode
NIVEL = {"LEITOR": 1, "FISCAL": 2, "PLANEJADOR": 3, "ADMIN": 4, "ADMIN_MASTER": 5}


def gerar_hash(senha: str, iteracoes: int = ITERACOES) -> str:
    sal = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), iteracoes)
    return f"pbkdf2${iteracoes}${sal}${h.hex()}"


def conferir_senha(senha: str, armazenado: str) -> bool:
    try:
        _, it, sal, h = armazenado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        return False


def novo_token() -> tuple[str, bytes]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).digest()


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass(frozen=True, slots=True)
class Contexto:
    usuario_id: UUID | None
    email: str
    nome: str
    master: bool
    organizacao_id: UUID | None
    papel: str                 # papel efetivo na organização ativa
    sessao_id: UUID | None = None

    def pelo_menos(self, papel: str) -> bool:
        return NIVEL.get(self.papel, 0) >= NIVEL[papel]


async def autenticar(s: AsyncSession, token: str) -> Contexto | None:
    """Resolve o token em contexto. None se inválido, expirado ou revogado."""
    row = (await s.execute(text("""
        SELECT se.id AS sessao_id, se.organizacao_id, u.id AS usuario_id, u.email, u.nome, u.master,
               p.papel
          FROM plan.sessao_login se
          JOIN plan.usuario u ON u.id = se.usuario_id AND u.ativo
          LEFT JOIN plan.usuario_papel p
            ON p.usuario_id = u.id AND p.organizacao_id = se.organizacao_id
         WHERE se.token_hash = :h AND se.revogada_em IS NULL AND se.expira_em > now()"""),
        {"h": hash_token(token)})).mappings().one_or_none()
    if row is None:
        return None
    papel = "ADMIN_MASTER" if row["master"] else (row["papel"] or "LEITOR")
    return Contexto(
        usuario_id=row["usuario_id"], email=row["email"], nome=row["nome"], master=bool(row["master"]),
        organizacao_id=row["organizacao_id"], papel=str(papel), sessao_id=row["sessao_id"])


async def abrir_sessao(
    s: AsyncSession, usuario_id: UUID, organizacao_id: UUID | None, agente: str = "",
) -> str:
    token, h = novo_token()
    await s.execute(text("""
        INSERT INTO plan.sessao_login (usuario_id, organizacao_id, token_hash, expira_em, agente)
        VALUES (:u, :o, :h, :exp, :ag)"""), {
        "u": usuario_id, "o": organizacao_id, "h": h,
        "exp": datetime.now(UTC) + VALIDADE, "ag": agente[:200]})
    await s.execute(text("UPDATE plan.usuario SET ultimo_login = now() WHERE id = :u"), {"u": usuario_id})
    return token


async def organizacoes_de(s: AsyncSession, usuario_id: UUID, master: bool) -> list[dict]:
    if master:
        rows = (await s.execute(text("""
            SELECT o.id, o.codigo, o.razao_social, 'ADMIN_MASTER' AS papel
              FROM plan.organizacao o WHERE o.ativa ORDER BY o.codigo"""))).mappings().all()
    else:
        rows = (await s.execute(text("""
            SELECT o.id, o.codigo, o.razao_social, p.papel::text AS papel
              FROM plan.usuario_papel p JOIN plan.organizacao o ON o.id = p.organizacao_id
             WHERE p.usuario_id = :u AND o.ativa ORDER BY o.codigo"""), {"u": usuario_id})).mappings().all()
    return [dict(r) for r in rows]
