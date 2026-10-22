import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError

from .api.rotas import router
from .api.rotas_apontamentos import router as router_apontamentos
from .api.rotas_auth import router as router_auth
from .api.rotas_boletim import router as router_boletim
from .api.rotas_cronograma import router as router_cronograma
from .api.rotas_custos import router as router_custos
from .api.rotas_estrutura import router as router_estrutura
from .api.rotas_importacao import router as router_importacao
from .api.rotas_modelos import router as router_modelos
from .api.rotas_projeto import router as router_projeto
from .api.rotas_rdo import router as router_rdo
from .api.rotas_recursos import router as router_recursos
from .api.rotas_relatorios import router as router_relatorios
from .dominio.tipos import ErroDominio

app = FastAPI(
    title="GPlanSiS|Planejamento",
    version="2.0.0",
    description="GPlanSiS|Project — planejamento e controle de projetos.",
)

# Origens do front em desenvolvimento. Em produção, defina GPS_CORS_ORIGENS
# (separadas por vírgula) ou sirva o front pelo mesmo host.
_origens = os.environ.get(
    "GPS_CORS_ORIGENS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origens if o.strip()],
    # Vite muda de porta quando 5173 está ocupada; em dev qualquer porta local vale.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["WWW-Authenticate"],
)

app.include_router(router)
app.include_router(router_importacao)
app.include_router(router_cronograma)
app.include_router(router_projeto)
app.include_router(router_auth)
app.include_router(router_recursos)
app.include_router(router_custos)
app.include_router(router_relatorios)
app.include_router(router_apontamentos)
app.include_router(router_estrutura)
app.include_router(router_rdo)
app.include_router(router_modelos)
app.include_router(router_boletim)


@app.exception_handler(ErroDominio)
async def regra_de_negocio(_: Request, exc: ErroDominio) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/saude")
async def saude() -> dict[str, str]:
    return {"status": "ok"}


log = logging.getLogger("gplansis.db")

# Classes de SQLSTATE cuja mensagem é a resposta ao usuário: RAISE das invariantes
# (P0001, "INV-..."), integridade (23: CHECK, UNIQUE, FK) e dado inválido (22).
# O resto (sintaxe, permissão, objeto inexistente, conexão) é defeito interno:
# a mensagem do Postgres vai só para o log, não ao cliente (auditoria 10/2026).
_SQLSTATE_EXPOSTO = ("P0001", "23", "22")


def _sqlstate(exc: DBAPIError) -> str:
    causa = getattr(exc, "orig", None)
    for alvo in (causa, getattr(causa, "__cause__", None)):
        codigo = getattr(alvo, "sqlstate", None) or getattr(alvo, "pgcode", None)
        if codigo:
            return str(codigo)
    return ""


@app.exception_handler(IntegrityError)
@app.exception_handler(DBAPIError)
async def invariante_violada(request: Request, exc: DBAPIError) -> JSONResponse:
    """O banco é a última linha de defesa das invariantes. Quando ele recusa
    (RAISE EXCEPTION 'INV-...', CHECK, UNIQUE), a resposta é 422 com o motivo,
    não 500: o cliente errou, o sistema não quebrou."""
    causa = getattr(exc, "orig", None)
    mensagem = str(causa or exc).splitlines()[0] if (causa or exc) else "violação de invariante"
    mensagem = mensagem.split(">: ", 1)[-1]   # asyncpg embrulha em "<class '...'>: msg"
    codigo = _sqlstate(exc)
    if isinstance(exc, IntegrityError) or codigo.startswith(_SQLSTATE_EXPOSTO) or mensagem.startswith("INV-"):
        return JSONResponse(status_code=422, content={"detail": mensagem})
    log.error("erro do banco em %s %s [%s]: %s", request.method, request.url.path, codigo or "?", mensagem)
    if codigo == "42501":
        return JSONResponse(status_code=403, content={"detail": "operação não permitida"})
    return JSONResponse(status_code=422, content={"detail": f"operação recusada pelo banco (SQLSTATE {codigo or 'desconhecido'})"})
