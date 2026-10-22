"""Status Report, exportações PDF/XLSX, templates do procedimento e catálogo do banco."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..infra import exportar
from ..infra.auth import Contexto
from ..infra.tenancy import contexto_autenticado, exigir, sessao_tenant
from . import relatorios as rel

router = APIRouter(prefix="/api/v1")

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
TIPOS = {
    "status-report": ("Status Report", None),
    "cronograma": ("Cronograma", rel.relatorio_cronograma),
    "medicoes": ("Medicoes", rel.relatorio_medicoes),
    "eap": ("EAP", rel.relatorio_eap),
    "recursos": ("Recursos", rel.relatorio_recursos),
    "custos": ("Custos", rel.relatorio_custos),
    "apontamentos": ("Apontamentos", rel.relatorio_apontamentos),
    "previsto-realizado": ("Previsto-x-Realizado", rel.relatorio_previsto_realizado),
}


@router.get("/contratos/{contrato_id}/status-report")
async def status_report(contrato_id: UUID, data_corte: date, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> dict:
    return await rel.status_report(s, contrato_id, data_corte)


@router.get("/contratos/{contrato_id}/exportar/{tipo}")
async def exportar_relatorio(
    contrato_id: UUID, tipo: str, data_corte: date,
    formato: str = Query(default="pdf", pattern="^(pdf|xlsx)$"),
    ctx: Contexto = Depends(contexto_autenticado), s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> Response:
    if tipo not in TIPOS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"relatório desconhecido: {tipo}")
    nome, montar = TIPOS[tipo]
    if montar is None:
        relatorio = rel.relatorio_status(await rel.status_report(s, contrato_id, data_corte), ctx.nome)
    else:
        relatorio = await montar(s, contrato_id, data_corte, ctx.nome)
    codigo = re.sub(r"[^A-Za-z0-9_-]+", "-", relatorio.cabecalho.contrato_codigo)
    arquivo = f"{codigo}_{nome}_{data_corte.isoformat()}.{formato}"
    if formato == "pdf":
        corpo, mime = exportar.pdf(relatorio), "application/pdf"
    else:
        corpo, mime = exportar.xlsx(relatorio), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(corpo, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{arquivo}"'})


# --------------------------------------------------------------- templates

def _templates() -> list[dict]:
    if not TEMPLATES.exists():
        return []
    out = []
    for p in sorted(TEMPLATES.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower().lstrip(".")
        grupo = "manual" if ext == "pdf" else "modelo"
        nome = p.stem.replace("_", " — ").replace("-", " ")
        out.append({"arquivo": p.name, "nome": nome, "extensao": ext, "grupo": grupo, "tamanho": p.stat().st_size,
                    "descricao": _descricao(p.name)})
    return out


def _descricao(nome: str) -> str:
    n = nome.lower()
    if "pro-gps-pln-001" in n:
        return "Procedimento de planejamento e controle de projetos — a fonte da verdade do sistema."
    if "onda-1" in n:
        return "Manual, onda 1: fundação da medição — EAP, ponderação, critérios de etapas e registro."
    if "onda-2" in n:
        return "Manual, onda 2: ciclo semanal de controle — corte, curva S, desvios e planos de ação."
    if "onda-3" in n:
        return "Manual, onda 3: pleito, auditoria e comissionamento."
    if "anx-i_" in n:
        return "Anexo I — modelo de EAP e curva S."
    if "anx-ii_" in n:
        return "Anexo II — histograma de recursos."
    if "anx-iii" in n:
        return "Anexo III — relatório mensal."
    if "anx-v_" in n or "anx-v-" in n or "diario-de-obra" in n:
        return "Anexo V — relatório diário de obra (RDO): o módulo RDO do sistema segue este modelo, com aprovação do planejamento e da gerência."
    if "gps-demo-v2" in n:
        return "Panorama do projeto DEMO v2 (CT-200-COGER): roteiro ponta a ponta, resultados e estrutura analítica."
    if "gps-med-v1" in n:
        return ("Avanço de tarefa, passo a passo: onde se lança o avanço físico "
                "— medição por etapas, boletim e planilha em lote —, por que não "
                "há campo de % no Gantt, e o que cada lançamento move.")
    if "gps-inv-v1" in n:
        return ("Dicionário das recusas: o que cada código INV significa, "
                "como sair dele e por que a regra existe. Procure aqui o código "
                "que apareceu na mensagem.")
    if "gps-pap-v1" in n:
        return ("O sistema pelo seu papel: as três ou quatro telas de cada um "
                "— apontador de campo, planejador, fiscal, gerente e admin —, a "
                "rotina e o que não precisa abrir.")
    if "gps-glo-v1" in n:
        return ("Glossário: EAP, EDT, folha, peso, base de ponderação, CPM, "
                "folga, BL0, LVP, curva S, EVM, IDP, ledger, estorno e os demais "
                "termos que o resto da documentação pressupõe.")
    if "gps-tour-v1" in n:
        return ("Tour das telas: doze capturas do sistema em ordem de uso, cada "
                "uma com o que se faz ali. Para reconhecer o sistema antes de "
                "operá-lo.")
    if "gps-ini-v1" in n:
        return ("Projeto novo, do zero: as oito etapas do contrato em branco "
                "até a linha de base aprovada, a regra que trava cada uma e a "
                "lista de verificação de quando o projeto já pode medir.")
    if "gps-act-0" in n:
        return ("Mapa das ações humanas: as 46 gravações do sistema, onde "
                "cada uma fica, o papel mínimo, e as cinco regras que valem "
                "em toda ação. Índice da série GPS-ACT.")
    if "gps-act-1" in n:
        return ("Ações do grupo Planejar: contrato, EAP, ponderação, tarefa, "
                "rede, recursos, custos e importação — campo a campo, com as "
                "invariantes que recusam a gravação.")
    if "gps-act-2" in n:
        return ("Ações do grupo Controlar: medição, estorno, boletim, RDO e "
                "apontamento de horas — os registros append-only do sistema.")
    if "gps-act-3" in n:
        return ("Ações de Reportar e administrar: cockpit, status report, "
                "modelos, usuários, papéis, organizações e conta própria.")
    if "gps-ds-v2" in n:
        return "Design system v2 do GPlanSiS: tokens, componentes, padrões de página e domínio."
    if "anx-iv" in n:
        return "Anexo IV — máscara de cronograma (importável na tela Importação)."
    if "anx-v" in n:
        return "Anexo V — relatório diário de obra (RDO)."
    return ""


@router.get("/templates")
async def listar_templates(_: Contexto = Depends(contexto_autenticado)) -> list[dict]:
    return _templates()


@router.get("/templates/{arquivo}")
async def baixar_template(arquivo: str, inline: bool = False, _: Contexto = Depends(contexto_autenticado)) -> FileResponse:
    if "/" in arquivo or "\\" in arquivo or arquivo.startswith("."):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nome inválido")
    p = TEMPLATES / arquivo
    if not p.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template não encontrado")
    disp = "inline" if inline else "attachment"
    return FileResponse(p, filename=arquivo, content_disposition_type=disp)


# ---------------------------------------------------------- base de dados

# A4 (auditoria 10/2026): o explorador só abre tabelas de CONTRATO, todas com RLS
# forçada. Lista explícita: tabela nova não aparece sozinha, e as de identidade
# (usuario, usuario_papel, sessao_login, login_falha, usuario_organizacao,
# organizacao) nunca, porque ficam fora da RLS e mostrariam todas as organizações.
TABELAS_EXPLORAVEIS = frozenset({
    "alocacao", "apontamento", "atividade", "baseline", "baseline_atividade", "boletim_medicao",
    "calculo_execucao", "contrato", "cronograma_calculo", "custo_item", "custo_realizado",
    "documento_engenharia", "documento_evento", "eap_no", "excecao_contratual", "feriado",
    "importacao", "lvp_avaliacao", "lvp_item", "medicao", "periodo_fechamento", "plano_acao",
    "previsto_semanal", "rdo", "rdo_evento", "recurso", "vinculo",
})


async def _exigir_exploravel(s: AsyncSession, tabela: str) -> None:
    """Recusa tabela fora da lista ou sem RLS forçada (dupla checagem no catálogo)."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", tabela):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nome inválido")
    if tabela not in TABELAS_EXPLORAVEIS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tabela não encontrada")
    existe = (await s.execute(text("""
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'plan' AND c.relkind = 'r' AND c.relname = :t
           AND c.relrowsecurity AND c.relforcerowsecurity"""), {"t": tabela})).first()
    if not existe:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tabela não encontrada")

@router.get("/admin/tabelas")
async def tabelas(ctx: Contexto = Depends(exigir("ADMIN")), s: AsyncSession = Depends(sessao_tenant, scope="function")) -> list[dict]:
    """Tabelas do schema plan com colunas, RLS e contagem visível na organização ativa."""
    rows = (await s.execute(text("""
        SELECT c.relname AS tabela, c.relrowsecurity AS rls, c.relforcerowsecurity AS forcada,
               obj_description(c.oid) AS comentario,
               (SELECT string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' ORDER BY a.attnum)
                  FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS colunas,
               (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS n_colunas
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'plan' AND c.relkind = 'r' ORDER BY c.relname"""))).mappings().all()
    out = []
    for r in rows:
        if r["tabela"] not in TABELAS_EXPLORAVEIS or not (r["rls"] and r["forcada"]):
            continue
        try:
            cnt = (await s.execute(text(f'SELECT count(*) FROM plan."{r["tabela"]}"'))).scalar_one()
        except Exception:
            cnt = None
        out.append({"tabela": r["tabela"], "rls": bool(r["rls"]), "forcada": bool(r["forcada"]), "colunas": r["colunas"],
                    "n_colunas": int(r["n_colunas"]), "linhas_visiveis": cnt, "comentario": r["comentario"]})
    return out


@router.get("/admin/tabelas/{tabela}/exportar")
async def exportar_tabela(
    tabela: str, ctx: Contexto = Depends(exigir("ADMIN")), s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> Response:
    """XLSX da tabela como a organização ativa a enxerga (RLS aplicada)."""
    await _exigir_exploravel(s, tabela)
    rows = (await s.execute(text(f'SELECT * FROM plan."{tabela}" LIMIT 50000'))).mappings().all()
    cabec = list(rows[0].keys()) if rows else []
    if not rows:
        cols = (await s.execute(text("""
            SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'plan' AND c.relname = :t AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"""), {"t": tabela})).scalars().all()
        cabec = list(cols)
    linhas = [[_celula(v) for v in r.values()] for r in rows]
    cab = exportar.Cabecalho(titulo=f"Tabela plan.{tabela}", contrato_codigo="—", empreendimento="catálogo do banco", contratante="", contratada="",
                             data_corte=date.today(), emitido_por=ctx.nome, paisagem=True)
    corpo = exportar.xlsx(exportar.Relatorio(cab, [exportar.Secao(tabela, cabecalhos=cabec, linhas=linhas,
                                                                 nota="Extraído com a RLS da organização ativa; limite de 50.000 linhas.")]))
    return Response(corpo, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="plan_{tabela}.xlsx"'})


@router.get("/admin/tabelas/{tabela}/linhas")
async def linhas_tabela(
    tabela: str, limite: int = Query(default=100, ge=1, le=500), deslocamento: int = Query(default=0, ge=0),
    ctx: Contexto = Depends(exigir("ADMIN")), s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> dict:
    """Prévia paginada da tabela como a organização ativa a enxerga (RLS aplicada), para abrir dentro do sistema."""
    await _exigir_exploravel(s, tabela)
    cols = list((await s.execute(text("""
        SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'plan' AND c.relname = :t AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"""), {"t": tabela})).scalars().all())
    total = (await s.execute(text(f'SELECT count(*) FROM plan."{tabela}"'))).scalar_one()
    ordem = '"' + cols[0] + '"' if cols else "1"
    rows = (await s.execute(text(f'SELECT * FROM plan."{tabela}" ORDER BY {ordem} LIMIT :lim OFFSET :off'), {"lim": limite, "off": deslocamento})).mappings().all()
    def js(v: object) -> object:
        v = _celula(v)
        return v if v is None or isinstance(v, str | int | float | bool) else str(v)
    return {"tabela": tabela, "colunas": cols, "total": int(total), "linhas": [[js(v) for v in r.values()] for r in rows]}


def _celula(v: object) -> object:
    if isinstance(v, bytes | bytearray | memoryview):
        return bytes(v).hex()
    if isinstance(v, dict | list):
        return json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, UUID):
        return str(v)
    return v
