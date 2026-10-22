"""Cria (ou redefine) o administrador master e o vincula a nada — master age em todas as organizações.

    GPS_DSN_SEED=postgresql://gps:gps@localhost:5433/gplansis python scripts/bootstrap_admin.py admin@exemplo.com [--senha X]

Sem --senha, gera uma provisória, imprime e marca troca obrigatória no primeiro login.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.infra.auth import gerar_hash  # noqa: E402

DSN = os.environ.get("GPS_DSN_SEED", "postgresql://gps:gps@localhost:5433/gplansis")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    email = args[0].lower()
    senha = args[args.index("--senha") + 1] if "--senha" in args else None
    provisoria = senha is None
    senha = senha or secrets.token_urlsafe(10)
    nome = args[args.index("--nome") + 1] if "--nome" in args else email.split("@")[0]
    with psycopg.connect(DSN) as con, con.cursor() as cur:
        cur.execute("""
            INSERT INTO plan.usuario (email, nome, senha_hash, master, ativo, deve_trocar_senha)
            VALUES (%s, %s, %s, true, true, %s)
            ON CONFLICT (email) DO UPDATE
               SET senha_hash = EXCLUDED.senha_hash, master = true, ativo = true,
                   deve_trocar_senha = EXCLUDED.deve_trocar_senha
            RETURNING id""", (email, nome, gerar_hash(senha), provisoria))
        uid = cur.fetchone()[0]
        con.commit()
    print(f"admin master pronto: {email} ({uid})")
    print(f"senha {'provisória (troque no primeiro login)' if provisoria else 'definida'}: {senha}")


if __name__ == "__main__":
    main()
