"""Sessão SQLAlchemy 2.0 async.

A aplicação não altera fato: medicao e rdo são append-only no banco.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DSN = os.environ.get("GPS_DSN", "postgresql+asyncpg://gps:gps@localhost:5432/gplansis")

# search_path também na conexão: SQL da própria API usa "plan." explícito,
# mas isso protege qualquer statement que esqueça o prefixo.
engine = create_async_engine(
    DSN, pool_pre_ping=True, echo=False,
    connect_args={"server_settings": {"search_path": "plan,public"}},
)
Sessao = async_sessionmaker(engine, expire_on_commit=False)


async def obter_sessao() -> AsyncIterator[AsyncSession]:
    async with Sessao() as s:
        yield s
