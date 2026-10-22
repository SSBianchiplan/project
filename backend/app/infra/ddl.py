"""Carrega o DDL versionado em db/ dentro das migrations Alembic.

Vive em app.infra (e não em alembic/) porque a pasta alembic/ do projeto
não é pacote: `from alembic.x import` resolve para o pacote instalado.
"""
from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3] / "db"


def ler(nome: str) -> str:
    caminho = RAIZ / nome
    if not caminho.exists():
        raise FileNotFoundError(f"DDL ausente: {caminho}")
    return caminho.read_text(encoding="utf-8")
