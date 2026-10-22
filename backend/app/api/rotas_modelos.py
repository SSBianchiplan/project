"""Modelos XLSX por módulo: baixar (já com os dados atuais) e enviar preenchido para criar/atualizar em lote.

Cada modelo tem uma aba "Dados" (cabeçalho = chaves das colunas) e uma aba "Instruções". O envio é
tudo-ou-nada: qualquer linha inválida devolve 422 com a lista de erros e nada é gravado (a sessão
faz rollback). Ledgers (medição, custo real, apontamento) só aceitam inclusão — append-only vale
também por planilha. Estrutura (EAP/rede) segue pela importação MPP/XER/XLSX já existente e pela
edição no Gantt; aqui entram atributos e pesos das atividades existentes.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..infra import cronograma_repo as repo
from ..infra.auth import Contexto
from ..infra.tenancy import contexto_autenticado, sessao_tenant
from .rotas_cronograma import recalcular_e_gravar

router = APIRouter(prefix="/api/v1", tags=["modelos"])
LIMITE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Coluna:
    chave: str
    rotulo: str
    tipo: str = "texto"           # texto | int | dec | data | pct | bool
    obrigatoria: bool = False
    opcoes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Modelo:
    id: str
    nome: str
    grupo: str
    modo: str                     # upsert | substituir_por_edt | anexar | atualizar
    chave: str
    descricao: str
    colunas: tuple[Coluna, ...]


MODELOS: dict[str, Modelo] = {m.id: m for m in [
    Modelo("recursos", "Recursos — catálogo", "Planejar", "upsert", "codigo",
           "Cria ou atualiza recursos pelo código. Inativar: ativo = não. Não exclui.",
           (Coluna("codigo", "Código", obrigatoria=True), Coluna("nome", "Nome", obrigatoria=True), Coluna("tipo", "Tipo", opcoes=("MOD", "MOI", "EQUIPAMENTO", "MATERIAL"), obrigatoria=True),
            Coluna("unidade", "Unidade"), Coluna("capacidade_dia", "Capacidade/dia", "dec"), Coluna("custo_hora", "Custo/hora (R$)", "dec"), Coluna("horas_dia", "Horas/dia", "dec"), Coluna("ativo", "Ativo", "bool"))),
    Modelo("alocacoes", "Recursos — alocação por atividade", "Planejar", "substituir_por_edt", "edt",
           "Para cada EDT presente na planilha, substitui todas as alocações da atividade pelas linhas enviadas. EDT ausente fica como está.",
           (Coluna("edt", "EDT", obrigatoria=True), Coluna("recurso_codigo", "Recurso (código)", obrigatoria=True), Coluna("quantidade_dia", "Qtd/dia", "dec", obrigatoria=True), Coluna("observacao", "Observação"))),
    Modelo("atividades", "Cronograma — atributos das atividades", "Planejar", "atualizar", "edt",
           "Atualiza atributos das atividades existentes (folhas). Célula vazia mantém o valor atual. Estrutura e rede não mudam aqui.",
           (Coluna("edt", "EDT", obrigatoria=True), Coluna("descricao", "Descrição"), Coluna("duracao_dias", "Duração (du)", "int"), Coluna("disciplina", "Disciplina"), Coluna("responsavel", "Responsável"),
            Coluna("hh_estimadas", "HH estimadas", "dec"), Coluna("restricao", "Restrição", opcoes=("ASAP", "SNET", "MSO")), Coluna("restricao_data", "Data da restrição", "data"),
            Coluna("restricao_justificativa", "Justificativa da restrição"), Coluna("deadline", "Deadline", "data"), Coluna("criterio_medicao", "Critério de medição"), Coluna("notas", "Notas"))),
    Modelo("pontos", "EAP — ponderação (pontos)", "Planejar", "atualizar", "edt",
           "Atualiza pontos e memória de peso das folhas. A soma das folhas deve fechar com a base do contrato (INV-EAP-001); marco fica com 0.",
           (Coluna("edt", "EDT", obrigatoria=True), Coluna("pontos", "Pontos", "dec", obrigatoria=True), Coluna("memoria_peso", "Memória de cálculo do peso"))),
    Modelo("vinculos", "Cronograma — rede de precedência", "Planejar", "substituir", "pred_edt",
           "Substitui TODA a rede pelas linhas enviadas (envie a rede completa). Ciclos e lags inválidos são recusados.",
           (Coluna("pred_edt", "Predecessora (EDT)", obrigatoria=True), Coluna("suc_edt", "Sucessora (EDT)", obrigatoria=True), Coluna("tipo", "Tipo", opcoes=("TI", "II", "TT", "IT")), Coluna("lag", "Lag (du)", "int"))),
    Modelo("custos_itens", "Custos — itens de orçamento", "Planejar", "substituir_por_edt", "edt",
           "Para cada EDT presente, substitui os itens de orçamento da atividade. Marco não recebe custo (INV-CST-001).",
           (Coluna("edt", "EDT", obrigatoria=True), Coluna("categoria", "Categoria", opcoes=("MATERIAL", "EQUIPAMENTO", "MAQUINA", "SERVICO", "SUBCONTRATO", "MAO_DE_OBRA", "LOGISTICA", "OUTRO"), obrigatoria=True),
            Coluna("descricao", "Descrição", obrigatoria=True), Coluna("fornecedor", "Fornecedor"), Coluna("quantidade", "Quantidade", "dec"), Coluna("unidade", "Unidade"), Coluna("custo_unitario", "Custo unitário (R$)", "dec", obrigatoria=True), Coluna("observacao", "Observação"))),
    Modelo("custos_realizados", "Custos — realizado (ledger)", "Controlar", "anexar", "edt",
           "Só inclui lançamentos (append-only, INV-CST-002). Correção é valor negativo em nova linha.",
           (Coluna("edt", "EDT", obrigatoria=True), Coluna("categoria", "Categoria", opcoes=("MATERIAL", "EQUIPAMENTO", "MAQUINA", "SERVICO", "SUBCONTRATO", "MAO_DE_OBRA", "LOGISTICA", "OUTRO"), obrigatoria=True),
            Coluna("data", "Data", "data", obrigatoria=True), Coluna("valor", "Valor (R$)", "dec", obrigatoria=True), Coluna("descricao", "Descrição", obrigatoria=True), Coluna("documento", "Documento"))),
    Modelo("medicoes", "Medição — percentual acumulado (ledger)", "Controlar", "anexar", "edt",
           "Só inclui medições: data de corte deve ser sábado (INV-MED-004) e o percentual não pode retroceder (INV-MED-003).",
           (Coluna("edt", "EDT", obrigatoria=True), Coluna("data_corte", "Data de corte (sábado)", "data", obrigatoria=True), Coluna("percentual_ac", "% acumulado (0 a 100)", "pct", obrigatoria=True), Coluna("evidencia", "Evidência", obrigatoria=True))),
    Modelo("apontamentos", "Apontamentos de horas (ledger)", "Controlar", "anexar", "edt",
           "Só inclui horas (append-only, INV-APT-002). Marco não recebe horas; data não pode ser futura.",
           (Coluna("edt", "EDT", obrigatoria=True), Coluna("data", "Data", "data", obrigatoria=True), Coluna("horas", "Horas", "dec", obrigatoria=True), Coluna("recurso_codigo", "Recurso (código)"), Coluna("descricao", "Descrição"))),
]}


# ------------------------------------------------------------------ dados atuais (pré-preenchimento)
async def _dados_atuais(s: AsyncSession, contrato_id: UUID, m: Modelo) -> list[list[object]]:
    q = {
        "recursos": "SELECT codigo, nome, tipo::text, unidade, capacidade_dia, custo_hora, horas_dia, ativo FROM plan.recurso WHERE contrato_id = :c ORDER BY tipo, codigo",
        "alocacoes": """SELECT n.edt, r.codigo, al.quantidade_dia, al.observacao FROM plan.alocacao al JOIN plan.atividade a ON a.id = al.atividade_id
                        JOIN plan.eap_no n ON n.id = a.eap_no_id JOIN plan.recurso r ON r.id = al.recurso_id WHERE a.contrato_id = :c ORDER BY string_to_array(n.edt, '.')::int[], r.codigo""",
        "atividades": """SELECT n.edt, n.descricao, a.duracao_dias, a.disciplina, a.responsavel, a.hh_estimadas, a.restricao::text, a.restricao_data, a.restricao_justificativa, a.deadline, a.criterio_medicao, a.notas
                         FROM plan.atividade a JOIN plan.eap_no n ON n.id = a.eap_no_id WHERE a.contrato_id = :c ORDER BY string_to_array(n.edt, '.')::int[]""",
        "pontos": "SELECT edt, pontos, memoria_peso FROM plan.eap_no WHERE contrato_id = :c AND folha ORDER BY string_to_array(edt, '.')::int[]",
        "vinculos": """SELECT p.edt, su.edt, v.tipo, v.lag FROM plan.vinculo v JOIN plan.atividade a1 ON a1.id = v.predecessora_id JOIN plan.eap_no p ON p.id = a1.eap_no_id
                       JOIN plan.atividade a2 ON a2.id = v.sucessora_id JOIN plan.eap_no su ON su.id = a2.eap_no_id WHERE a1.contrato_id = :c ORDER BY string_to_array(su.edt, '.')::int[], p.edt""",
        "custos_itens": """SELECT n.edt, ci.categoria, ci.descricao, ci.fornecedor, ci.quantidade, ci.unidade, ci.custo_unitario, ci.observacao FROM plan.custo_item ci
                           JOIN plan.atividade a ON a.id = ci.atividade_id JOIN plan.eap_no n ON n.id = a.eap_no_id WHERE a.contrato_id = :c ORDER BY string_to_array(n.edt, '.')::int[], ci.categoria""",
        "custos_realizados": """SELECT n.edt, cr.categoria, cr.data, cr.valor, cr.descricao, cr.documento FROM plan.custo_realizado cr
                                JOIN plan.atividade a ON a.id = cr.atividade_id JOIN plan.eap_no n ON n.id = a.eap_no_id WHERE cr.contrato_id = :c ORDER BY cr.data, n.edt""",
        "medicoes": """SELECT n.edt, m.data_corte, m.percentual_ac * 100, m.evidencia FROM plan.medicao m JOIN plan.eap_no n ON n.id = m.eap_no_id
                       WHERE m.contrato_id = :c AND m.tipo = 'MEDICAO' ORDER BY m.data_corte, n.edt""",
        "apontamentos": """SELECT n.edt, ap.data, ap.horas, r.codigo, ap.descricao FROM plan.apontamento ap JOIN plan.atividade a ON a.id = ap.atividade_id
                           JOIN plan.eap_no n ON n.id = a.eap_no_id LEFT JOIN plan.recurso r ON r.id = ap.recurso_id WHERE ap.contrato_id = :c ORDER BY ap.data, n.edt""",
    }[m.id]
    rows = (await s.execute(text(q), {"c": contrato_id})).all()
    return [list(r) for r in rows]


def _xlsx(m: Modelo, linhas: list[list[object]], contrato: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Dados"
    cab = Font(bold=True, color="FFFFFF")
    fundo = PatternFill("solid", fgColor="2D3336")
    for j, col in enumerate(m.colunas, start=1):
        c = ws.cell(row=1, column=j, value=col.chave)
        c.font, c.fill, c.alignment = cab, fundo, Alignment(horizontal="center")
        ws.cell(row=2, column=j, value=col.rotulo + (" *" if col.obrigatoria else "")).font = Font(italic=True, color="7D8286")
        ws.column_dimensions[get_column_letter(j)].width = max(14, min(42, len(col.rotulo) + 6))
    for i, linha in enumerate(linhas, start=3):
        for j, v in enumerate(linha, start=1):
            valor: object = ("sim" if v else "não") if isinstance(v, bool) else float(v) if isinstance(v, Decimal) else v
            ws.cell(row=i, column=j, value=valor)
    ws.freeze_panes = "A3"
    wi = wb.create_sheet("Instruções")
    wi.column_dimensions["A"].width = 26
    wi.column_dimensions["B"].width = 90
    linhas_i = [("Modelo", m.nome), ("Contrato", contrato), ("Módulo", m.grupo), ("Modo de aplicação", {"upsert": "cria ou atualiza pela chave", "substituir_por_edt": "substitui as linhas de cada EDT presente", "anexar": "só inclui (ledger append-only)", "atualizar": "atualiza registros existentes pela chave", "substituir": "substitui o conjunto inteiro"}[m.modo]),
                ("Chave", m.chave), ("Regra", m.descricao), ("", ""), ("Como usar", "1) A aba Dados já vem com os dados atuais. 2) Edite, inclua ou remova linhas a partir da linha 3 (linha 1 = chave técnica, linha 2 = rótulo; não altere). 3) Envie o arquivo em Importação → Modelos por módulo. 4) Erros são listados por linha e nada é gravado até estar tudo válido."),
                ("Formatos", "Datas: célula de data do Excel ou AAAA-MM-DD. Números: use vírgula ou ponto. Booleanos: sim/não. Percentual: 0 a 100. Listas: valores exatamente como nas opções abaixo."), ("", "")]
    for col in m.colunas:
        linhas_i.append((col.chave, f"{col.rotulo}{' (obrigatória)' if col.obrigatoria else ''} · tipo {col.tipo}" + (f" · opções: {', '.join(col.opcoes)}" if col.opcoes else "")))
    for i, (a, b) in enumerate(linhas_i, start=1):
        wi.cell(row=i, column=1, value=a).font = Font(bold=True)
        wi.cell(row=i, column=2, value=b).alignment = Alignment(wrap_text=True, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ leitura e validação
def _conv(col: Coluna, v: object, linha: int, erros: list[str]) -> object:
    if v is None or (isinstance(v, str) and not v.strip()):
        if col.obrigatoria:
            erros.append(f"linha {linha}: '{col.rotulo}' é obrigatória")
        return None
    try:
        if col.tipo == "int":
            return int(Decimal(str(v).replace(",", ".")))
        if col.tipo in ("dec", "pct"):
            d = Decimal(str(v).replace(".", "").replace(",", ".")) if isinstance(v, str) and "," in str(v) else Decimal(str(v))
            return d / 100 if col.tipo == "pct" else d
        if col.tipo == "data":
            if isinstance(v, datetime):
                return v.date()
            if isinstance(v, date):
                return v
            return date.fromisoformat(str(v).strip()[:10])
        if col.tipo == "bool":
            return str(v).strip().lower() in ("sim", "s", "true", "1", "x", "verdadeiro")
        s = str(v).strip()
        if col.opcoes and s.upper() not in col.opcoes:
            erros.append(f"linha {linha}: '{col.rotulo}' = {s!r} não está em {', '.join(col.opcoes)}")
        return s.upper() if col.opcoes else s
    except (InvalidOperation, ValueError):
        erros.append(f"linha {linha}: '{col.rotulo}' = {v!r} inválido para {col.tipo}")
        return None


def _ler(m: Modelo, conteudo: bytes) -> tuple[list[dict], list[str]]:
    try:
        wb = load_workbook(io.BytesIO(conteudo), data_only=True, read_only=True)
    except Exception as e:
        raise HTTPException(422, f"arquivo não é um XLSX válido: {str(e)[:80]}") from e
    ws = wb["Dados"] if "Dados" in wb.sheetnames else wb.worksheets[0]
    linhas = list(ws.iter_rows(values_only=True))
    if not linhas:
        raise HTTPException(422, "planilha vazia")
    cabecalho = [str(c).strip() if c is not None else "" for c in linhas[0]]
    pos = {c.chave: cabecalho.index(c.chave) for c in m.colunas if c.chave in cabecalho}
    faltam = [c.chave for c in m.colunas if c.obrigatoria and c.chave not in pos]
    if faltam:
        raise HTTPException(422, f"cabeçalho sem as colunas obrigatórias: {', '.join(faltam)} (linha 1 deve ter as chaves técnicas do modelo)")
    erros: list[str] = []
    saida: list[dict] = []
    for i, r in enumerate(linhas[1:], start=2):
        if i == 2 and any(isinstance(v, str) and v.strip().endswith("*") for v in r if v is not None):
            continue                                    # linha de rótulos
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in r):
            continue
        d = {c.chave: _conv(c, r[pos[c.chave]] if c.chave in pos and pos[c.chave] < len(r) else None, i, erros) for c in m.colunas}
        d["_linha"] = i
        saida.append(d)
    return saida, erros


async def _edts(s: AsyncSession, contrato_id: UUID) -> dict[str, dict]:
    rows = (await s.execute(text("""
        SELECT n.edt, n.id AS no_id, a.id AS atividade_id, a.tipo::text AS tipo FROM plan.eap_no n
          LEFT JOIN plan.atividade a ON a.eap_no_id = n.id WHERE n.contrato_id = :c"""), {"c": contrato_id})).mappings().all()
    return {r["edt"]: dict(r) for r in rows}


# ------------------------------------------------------------------ aplicação por modelo
async def _aplicar(s: AsyncSession, contrato_id: UUID, m: Modelo, linhas: list[dict], ctx: Contexto) -> dict:
    erros: list[str] = []
    ins = upd = 0
    edts = await _edts(s, contrato_id)

    def ativ(d: dict) -> dict | None:
        e = edts.get(str(d.get("edt", "")).strip())
        if e is None:
            erros.append(f"linha {d['_linha']}: EDT {d.get('edt')!r} não existe no contrato")
            return None
        if e["atividade_id"] is None:
            erros.append(f"linha {d['_linha']}: EDT {d['edt']} é resumo, não atividade")
            return None
        return e

    if m.id == "recursos":
        atuais = {r.codigo: r.id for r in (await s.execute(text("SELECT id, codigo FROM plan.recurso WHERE contrato_id = :c"), {"c": contrato_id})).all()}
        for d in linhas:
            cod = str(d["codigo"]).strip().upper()
            params = {"c": contrato_id, "codigo": cod, "nome": d["nome"], "tipo": d["tipo"], "unidade": d.get("unidade") or ("máquina" if d["tipo"] == "EQUIPAMENTO" else "pessoa"),
                      "cap": d.get("capacidade_dia") or 0, "custo": d.get("custo_hora") or 0, "horas": d.get("horas_dia") or 8, "ativo": True if d.get("ativo") is None else d["ativo"]}
            if cod in atuais:
                await s.execute(text("UPDATE plan.recurso SET nome=:nome, tipo=:tipo, unidade=:unidade, capacidade_dia=:cap, custo_hora=:custo, horas_dia=:horas, ativo=:ativo WHERE id=:id"), {**params, "id": atuais[cod]})
                upd += 1
            else:
                await s.execute(text("INSERT INTO plan.recurso (contrato_id, codigo, nome, tipo, unidade, capacidade_dia, custo_hora, horas_dia, ativo) VALUES (:c, :codigo, :nome, :tipo, :unidade, :cap, :custo, :horas, :ativo)"), params)
                ins += 1
    elif m.id == "alocacoes":
        rec = {r.codigo: r.id for r in (await s.execute(text("SELECT id, codigo FROM plan.recurso WHERE contrato_id = :c AND ativo"), {"c": contrato_id})).all()}
        por_edt: dict[str, list[dict]] = {}
        for d in linhas:
            e = ativ(d)
            if e is None:
                continue
            if e["tipo"] == "MARCO":
                erros.append(f"linha {d['_linha']}: marco {d['edt']} não recebe recurso (INV-REC-003)")
                continue
            if str(d["recurso_codigo"]).strip().upper() not in rec:
                erros.append(f"linha {d['_linha']}: recurso {d['recurso_codigo']!r} não existe ou está inativo")
                continue
            por_edt.setdefault(d["edt"], []).append(d)
        if not erros:
            for edt, ls in por_edt.items():
                aid = edts[edt]["atividade_id"]
                await s.execute(text("DELETE FROM plan.alocacao WHERE atividade_id = :a"), {"a": aid})
                for d in ls:
                    await s.execute(text("INSERT INTO plan.alocacao (atividade_id, recurso_id, quantidade_dia, observacao) VALUES (:a, :r, :q, :o)"),
                                    {"a": aid, "r": rec[str(d["recurso_codigo"]).strip().upper()], "q": d["quantidade_dia"], "o": d.get("observacao") or ""})
                    ins += 1
    elif m.id == "atividades":
        for d in linhas:
            e = ativ(d)
            if e is None:
                continue
            sets = {k: d[k] for k in ("duracao_dias", "disciplina", "responsavel", "hh_estimadas", "restricao", "restricao_data", "restricao_justificativa", "deadline", "criterio_medicao", "notas") if d.get(k) is not None}
            if sets.get("restricao") == "ASAP":
                sets["restricao_data"] = None
            if sets:
                clausula = ", ".join(f"{k} = :{k}" for k in sets)
                await s.execute(text(f"UPDATE plan.atividade SET {clausula} WHERE id = :id"), {**sets, "id": e["atividade_id"]})
            if d.get("descricao"):
                await s.execute(text("UPDATE plan.eap_no SET descricao = :d WHERE id = :id"), {"d": d["descricao"], "id": e["no_id"]})
            upd += 1
    elif m.id == "pontos":
        base = (await s.execute(text("SELECT base_ponderacao FROM plan.contrato WHERE id = :c"), {"c": contrato_id})).scalar_one()
        atuais = {r.edt: Decimal(r.pontos) for r in (await s.execute(text("SELECT edt, pontos FROM plan.eap_no WHERE contrato_id = :c AND folha"), {"c": contrato_id})).all()}
        for d in linhas:
            e = ativ(d)
            if e is None:
                continue
            if e["tipo"] == "MARCO" and d["pontos"] and d["pontos"] > 0:
                erros.append(f"linha {d['_linha']}: marco {d['edt']} não pontua (INV-EAP-003)")
                continue
            atuais[d["edt"]] = Decimal(d["pontos"] or 0)
        soma = sum(atuais.values(), start=Decimal(0))
        if not erros and soma != Decimal(base):
            erros.append(f"INV-EAP-001: soma das folhas após a planilha = {soma} difere da base {base}")
        if not erros:
            for d in linhas:
                await s.execute(text("UPDATE plan.eap_no SET pontos = :p, memoria_peso = coalesce(:m, memoria_peso) WHERE id = :id"), {"p": d["pontos"] or 0, "m": d.get("memoria_peso"), "id": edts[d["edt"]]["no_id"]})
                upd += 1
    elif m.id == "vinculos":
        vs = []
        for d in linhas:
            for k in ("pred_edt", "suc_edt"):
                if str(d[k]).strip() not in edts or edts[str(d[k]).strip()]["atividade_id"] is None:
                    erros.append(f"linha {d['_linha']}: {k} {d[k]!r} não é atividade do contrato")
            lag = d.get("lag") or 0
            if not 0 <= lag <= 21:
                erros.append(f"linha {d['_linha']}: lag {lag} fora de 0..21")
            vs.append((str(d["pred_edt"]).strip(), str(d["suc_edt"]).strip(), d.get("tipo") or "TI", lag))
        if not erros:
            await repo.substituir_vinculos(s, contrato_id, vs)
            ins = len(vs)
    elif m.id == "custos_itens":
        por_edt = {}
        for d in linhas:
            e = ativ(d)
            if e is None:
                continue
            if e["tipo"] == "MARCO":
                erros.append(f"linha {d['_linha']}: marco {d['edt']} não recebe custo (INV-CST-001)")
                continue
            por_edt.setdefault(d["edt"], []).append(d)
        if not erros:
            for edt, ls in por_edt.items():
                aid = edts[edt]["atividade_id"]
                await s.execute(text("DELETE FROM plan.custo_item WHERE atividade_id = :a"), {"a": aid})
                for d in ls:
                    await s.execute(text("""INSERT INTO plan.custo_item (atividade_id, categoria, descricao, fornecedor, quantidade, unidade, custo_unitario, observacao)
                                            VALUES (:a, :cat, :d, :f, :q, :u, :cu, :o)"""), {"a": aid, "cat": d["categoria"], "d": d["descricao"], "f": d.get("fornecedor") or "", "q": d.get("quantidade") or 1, "u": d.get("unidade") or "un", "cu": d["custo_unitario"], "o": d.get("observacao") or ""})
                    ins += 1
    elif m.id == "custos_realizados":
        for d in linhas:
            e = ativ(d)
            if e is None:
                continue
            await s.execute(text("""INSERT INTO plan.custo_realizado (contrato_id, atividade_id, categoria, data, valor, descricao, documento, criado_por)
                                    VALUES (:c, :a, :cat, :dt, :v, :d, :doc, :por)"""), {"c": contrato_id, "a": e["atividade_id"], "cat": d["categoria"], "dt": d["data"], "v": d["valor"], "d": d["descricao"], "doc": d.get("documento") or "", "por": ctx.nome})
            ins += 1
    elif m.id == "medicoes":
        import json as _json
        for d in linhas:
            e = ativ(d)
            if e is None:
                continue
            if d["data_corte"].weekday() != 5:
                erros.append(f"linha {d['_linha']}: data de corte {d['data_corte']} não é sábado (INV-MED-004)")
                continue
            if not (Decimal(0) <= d["percentual_ac"] <= Decimal(1)):
                erros.append(f"linha {d['_linha']}: percentual {d['percentual_ac'] * 100} fora de 0..100")
                continue
            await s.execute(text("""INSERT INTO plan.medicao (contrato_id, eap_no_id, data_corte, percentual_ac, memoria_calculo, evidencia, criado_por)
                                    VALUES (:c, :n, :dc, :p, CAST(:mem AS jsonb), :ev, :por)"""), {"c": contrato_id, "n": e["no_id"], "dc": d["data_corte"], "p": d["percentual_ac"],
                                                                                                    "mem": _json.dumps({"origem": "planilha_modelo", "evidencia": d["evidencia"]}), "ev": d["evidencia"], "por": ctx.nome})
            ins += 1
    elif m.id == "apontamentos":
        rec = {r.codigo: r.id for r in (await s.execute(text("SELECT id, codigo FROM plan.recurso WHERE contrato_id = :c"), {"c": contrato_id})).all()}
        for d in linhas:
            e = ativ(d)
            if e is None:
                continue
            if d["data"] > date.today():
                erros.append(f"linha {d['_linha']}: data futura (INV-APT-004)")
                continue
            rc = str(d.get("recurso_codigo") or "").strip().upper()
            if rc and rc not in rec:
                erros.append(f"linha {d['_linha']}: recurso {rc!r} não existe")
                continue
            await s.execute(text("""INSERT INTO plan.apontamento (contrato_id, atividade_id, recurso_id, usuario_id, data, horas, descricao, criado_por)
                                    VALUES (:c, :a, :r, :u, :dt, :h, :d, :por)"""), {"c": contrato_id, "a": e["atividade_id"], "r": rec.get(rc), "u": ctx.usuario_id, "dt": d["data"], "h": d["horas"], "d": d.get("descricao") or "", "por": ctx.nome})
            ins += 1
    if erros:
        raise HTTPException(422, {"mensagem": f"{len(erros)} erro(s); nada foi gravado", "erros": erros[:60]})
    recalculo = None
    if m.id in ("atividades", "vinculos", "pontos", "alocacoes"):
        saida = await recalcular_e_gravar(s, contrato_id, por=ctx.nome, motivo=f"planilha modelo {m.id} ({len(linhas)} linhas)", data_status=None)
        recalculo = saida.fim.data.isoformat()
    return {"modelo": m.id, "linhas": len(linhas), "inseridos": ins, "atualizados": upd, "termino": recalculo}


# ------------------------------------------------------------------ rotas
@router.get("/modelos")
async def listar_modelos(ctx: Contexto = Depends(contexto_autenticado)) -> list[dict]:
    return [{"id": m.id, "nome": m.nome, "grupo": m.grupo, "modo": m.modo, "chave": m.chave, "descricao": m.descricao,
             "colunas": [{"chave": c.chave, "rotulo": c.rotulo, "tipo": c.tipo, "obrigatoria": c.obrigatoria, "opcoes": list(c.opcoes)} for c in m.colunas]} for m in MODELOS.values()]


@router.get("/contratos/{contrato_id}/modelos/{modelo}")
async def baixar_modelo(contrato_id: UUID, modelo: str, vazio: bool = False, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> Response:
    m = MODELOS.get(modelo)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "modelo desconhecido")
    c = await repo.contrato(s, contrato_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contrato não encontrado")
    linhas = [] if vazio else await _dados_atuais(s, contrato_id, m)
    corpo = _xlsx(m, linhas, c.codigo)
    return Response(corpo, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="modelo_{m.id}_{c.codigo}.xlsx"'})


@router.post("/contratos/{contrato_id}/modelos/{modelo}")
async def enviar_modelo(contrato_id: UUID, modelo: str, arquivo: UploadFile = File(...), ctx: Contexto = Depends(contexto_autenticado),
                        s: AsyncSession = Depends(sessao_tenant, scope="function")) -> dict:
    m = MODELOS.get(modelo)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "modelo desconhecido")
    conteudo = await arquivo.read()
    if len(conteudo) > LIMITE_BYTES:
        raise HTTPException(413, "arquivo acima de 8 MB")
    linhas, erros = _ler(m, conteudo)
    if erros:
        raise HTTPException(422, {"mensagem": f"{len(erros)} erro(s) de leitura; nada foi gravado", "erros": erros[:60]})
    if not linhas:
        raise HTTPException(422, "planilha sem linhas de dados")
    return await _aplicar(s, contrato_id, m, linhas, ctx)
