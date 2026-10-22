"""Alembic sobre SQL explícito.

Não há autogenerate: o schema tem trigger, policy de RLS e constraint trigger
diferida, e autogenerate não enxerga nada disso — ele apagaria invariante
achando que é diferença de modelo. As migrations carregam o DDL escrito à mão.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

DSN = os.environ.get("GPS_DSN_SYNC", "postgresql+psycopg://gps:gps@localhost:5432/gplansis")
config.set_main_option("sqlalchemy.url", DSN)

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=DSN, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    conectavel = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with conectavel.connect() as conexao:
        context.configure(connection=conexao, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
