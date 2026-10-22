"""Edição estrutural da EAP em contrato vivo (com baseline e medições): nova tarefa,
indentar, recuar e excluir. Diferente de PUT /estrutura, nada é apagado em bloco: a
árvore é reorganizada em memória e renumerada com dois passes (prefixo temporário →
prefixo final), o que respeita os triggers INV-EAP-004/005 e a unicidade do EDT em
cada UPDATE. Regras:

  RGR-EAP-EDIT-001  profundidade máxima 5 níveis (raiz = 1)
  RGR-EAP-EDIT-002  nó folha só vira resumo (ao receber filhos) se não tiver pontos,
                    medição, vínculo, alocação, custo nem apontamento
  RGR-EAP-EDIT-003  resumo que fica sem filhos volta a ser atividade (5 du, sem pontos)
  RGR-EAP-EDIT-004  só se exclui folha sem pontos e sem dados vinculados
  INV-EAP-001       continua valendo: novas folhas nascem com 0 pontos; a reponderação
                    é feita na EAP
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..infra.auth import Contexto
from ..infra.tenancy import contexto_autenticado, sessao_tenant
from . import schemas_cronograma as sc
from .rotas_cronograma import recalcular_e_gravar

router = APIRouter(prefix="/api/v1", tags=["estrutura"])
NIVEL_MAX = 5


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NovaTarefa(Base):
    referencia_id: UUID                       # nó de referência (eap_no)
    posicao: str = Field(pattern="^(abaixo|filha)$")
    descricao: str = Field(min_length=1, max_length=200)
    marco: bool = False
    duracao: int = Field(default=5, ge=0, le=15)
    alterado_por: str = "planejador"


class Motivo(Base):
    alterado_por: str = "planejador"


@dataclass
class No:
    id: UUID
    parent_id: UUID | None
    edt: str
    nivel: int
    tipo: str
    folha: bool
    pontos: float
    filhos: list[No] = field(default_factory=list)
    # destino
    novo_edt: str = ""
    novo_nivel: int = 0
    novo_parent: UUID | None = None
    novo_folha: bool = True
    novo_tipo: str = ""


async def _arvore(s: AsyncSession, contrato_id: UUID) -> tuple[No, dict[UUID, No]]:
    rows = (await s.execute(text("""
        SELECT id, parent_id, edt, nivel, tipo::text AS tipo, folha, pontos
          FROM plan.eap_no WHERE contrato_id = :c ORDER BY string_to_array(edt, '.')::int[]"""), {"c": contrato_id})).mappings().all()
    nos = {r["id"]: No(r["id"], r["parent_id"], r["edt"], r["nivel"], r["tipo"], r["folha"], float(r["pontos"])) for r in rows}
    raiz = None
    for n in nos.values():
        n.novo_tipo, n.novo_folha = n.tipo, n.folha
        if n.parent_id is None:
            raiz = n
        else:
            nos[n.parent_id].filhos.append(n)
    if raiz is None:
        raise HTTPException(422, "contrato sem EAP")
    return raiz, nos


async def _tem_dados(s: AsyncSession, no: No) -> str | None:
    """Motivo pelo qual uma folha não pode mudar de natureza (RGR-EAP-EDIT-002/004)."""
    if no.pontos > 0:
        return "tem pontos de ponderação (reponderar antes na EAP)"
    r = (await s.execute(text("""
        SELECT (SELECT count(*) FROM plan.medicao WHERE eap_no_id = :n) AS med,
               (SELECT count(*) FROM plan.vinculo v JOIN plan.atividade a ON a.id IN (v.predecessora_id, v.sucessora_id) WHERE a.eap_no_id = :n) AS vin,
               (SELECT count(*) FROM plan.alocacao al JOIN plan.atividade a ON a.id = al.atividade_id WHERE a.eap_no_id = :n) AS alo,
               (SELECT count(*) FROM plan.custo_item ci JOIN plan.atividade a ON a.id = ci.atividade_id WHERE a.eap_no_id = :n) AS cst,
               (SELECT count(*) FROM plan.custo_realizado cr JOIN plan.atividade a ON a.id = cr.atividade_id WHERE a.eap_no_id = :n) AS cre,
               (SELECT count(*) FROM plan.apontamento ap JOIN plan.atividade a ON a.id = ap.atividade_id WHERE a.eap_no_id = :n) AS apt"""), {"n": no.id})).mappings().one()
    nomes = {"med": "medições", "vin": "vínculos", "alo": "alocações", "cst": "itens de custo", "cre": "custo realizado", "apt": "apontamentos"}
    pend = [nomes[k] for k, v in r.items() if v]
    return ("tem " + ", ".join(pend)) if pend else None


def _renumerar(raiz: No, novo_raiz: str) -> None:
    def visita(n: No, edt: str, nivel: int, parent: UUID | None) -> None:
        n.novo_edt, n.novo_nivel, n.novo_parent = edt, nivel, parent
        if nivel > NIVEL_MAX:
            raise HTTPException(422, f"RGR-EAP-EDIT-001: a EAP não passa de {NIVEL_MAX} níveis ({edt})")
        for i, f in enumerate(n.filhos, start=1):
            visita(f, f"{edt}.{i}", nivel + 1, n.id)
    visita(raiz, novo_raiz, 1, None)


async def _aplicar(s: AsyncSession, raiz: No, nos: dict[UUID, No]) -> None:
    """Dois passes top-down: prefixo temporário (livre) e depois o definitivo."""
    ordem: list[No] = []

    def dfs(n: No) -> None:
        ordem.append(n)
        for f in n.filhos:
            dfs(f)
    dfs(raiz)
    temp = str(int(raiz.edt) + 1000)
    for prefixo in (temp, raiz.edt):
        _renumerar(raiz, prefixo)
        for n in ordem:
            await s.execute(text("""
                UPDATE plan.eap_no SET edt = :e, nivel = :n, parent_id = :p, folha = :f, tipo = CAST(:t AS plan.tipo_no)
                 WHERE id = :id"""), {"e": n.novo_edt, "n": n.novo_nivel, "p": n.novo_parent, "f": n.novo_folha, "t": n.novo_tipo, "id": n.id})


async def _virar_resumo(s: AsyncSession, no: No) -> None:
    if not no.folha:
        return
    motivo = await _tem_dados(s, no)
    if motivo:
        raise HTTPException(409, f"RGR-EAP-EDIT-002: {no.edt} {motivo}; não pode receber subtarefas")
    await s.execute(text("DELETE FROM plan.baseline_atividade WHERE atividade_id IN (SELECT id FROM plan.atividade WHERE eap_no_id = :n)"), {"n": no.id})
    await s.execute(text("DELETE FROM plan.atividade WHERE eap_no_id = :n"), {"n": no.id})
    # já vira resumo aqui: o trigger INV-EAP-002 recusa filho de nó ainda marcado como folha
    await s.execute(text("UPDATE plan.eap_no SET folha = false, tipo = 'RESUMO', pontos = 0 WHERE id = :n"), {"n": no.id})
    no.folha, no.novo_folha, no.novo_tipo = False, False, "RESUMO"


def _marcar_folha(no: No) -> None:
    """Resumo que ficou sem filhos volta a ser atividade (RGR-EAP-EDIT-003); a linha de atividade
    é criada depois de _aplicar, quando o nó já está gravado como folha (INV-CRN-009)."""
    no.novo_folha, no.novo_tipo = True, "ATIVIDADE"


async def _criar_atividade_de_folha(s: AsyncSession, contrato_id: UUID, no: No) -> None:
    ini = (await s.execute(text("SELECT data_inicio FROM plan.contrato WHERE id = :c"), {"c": contrato_id})).scalar_one()
    await s.execute(text("""
        INSERT INTO plan.atividade (contrato_id, eap_no_id, duracao_dias, inicio_previsto, termino_previsto, tipo)
        VALUES (:c, :n, 5, :d, :d, 'ATIVIDADE')"""), {"c": contrato_id, "n": no.id, "d": ini})


def _pai_de(nos: dict[UUID, No], n: No) -> No:
    if n.parent_id is None:
        raise HTTPException(422, "a raiz da EAP não se move")
    return nos[n.parent_id]


# ------------------------------------------------------------------ rotas
@router.post("/contratos/{contrato_id}/eap/tarefas", response_model=sc.CronogramaSaida, status_code=status.HTTP_201_CREATED)
async def nova_tarefa(contrato_id: UUID, e: NovaTarefa, ctx: Contexto = Depends(contexto_autenticado),
                      s: AsyncSession = Depends(sessao_tenant, scope="function")) -> sc.CronogramaSaida:
    raiz, nos = await _arvore(s, contrato_id)
    ref = nos.get(e.referencia_id)
    if ref is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nó de referência não encontrado")
    if e.posicao == "filha":
        pai, indice = ref, len(ref.filhos)
        if pai.folha:
            await _virar_resumo(s, pai)
    else:
        pai = _pai_de(nos, ref)
        indice = pai.filhos.index(ref) + 1
    if pai.nivel + 1 > NIVEL_MAX:
        raise HTTPException(422, f"RGR-EAP-EDIT-001: a EAP não passa de {NIVEL_MAX} níveis")
    tipo = "MARCO" if e.marco else "ATIVIDADE"
    ini = (await s.execute(text("SELECT data_inicio FROM plan.contrato WHERE id = :c"), {"c": contrato_id})).scalar_one()
    # nasce com EDT provisório único sob o pai; a renumeração dá o definitivo
    row = (await s.execute(text("""
        INSERT INTO plan.eap_no (contrato_id, parent_id, edt, descricao, nivel, tipo, folha, pontos, memoria_peso)
        VALUES (:c, :p, :e, :d, :n, CAST(:t AS plan.tipo_no), true, 0, '') RETURNING id"""), {
        "c": contrato_id, "p": pai.id, "e": f"{pai.edt}.{900 + len(pai.filhos)}", "d": e.descricao.strip(), "n": pai.nivel + 1, "t": tipo})).one()
    await s.execute(text("""
        INSERT INTO plan.atividade (contrato_id, eap_no_id, duracao_dias, inicio_previsto, termino_previsto, tipo)
        VALUES (:c, :n, :dur, :d, :d, CAST(:t AS plan.tipo_no))"""), {"c": contrato_id, "n": row.id, "dur": 0 if e.marco else max(e.duracao, 1), "d": ini, "t": tipo})
    novo = No(row.id, pai.id, "", pai.nivel + 1, tipo, True, 0.0)
    novo.novo_tipo, novo.novo_folha = tipo, True
    pai.filhos.insert(indice, novo)
    nos[novo.id] = novo
    await _aplicar(s, raiz, nos)
    return await recalcular_e_gravar(s, contrato_id, por=e.alterado_por or ctx.nome, motivo=f"nova tarefa {e.descricao[:40]}", data_status=None)


@router.post("/contratos/{contrato_id}/eap/{no_id}/indentar", response_model=sc.CronogramaSaida)
async def indentar(contrato_id: UUID, no_id: UUID, m: Motivo, ctx: Contexto = Depends(contexto_autenticado),
                   s: AsyncSession = Depends(sessao_tenant, scope="function")) -> sc.CronogramaSaida:
    """O nó passa a ser o último filho do irmão imediatamente anterior."""
    raiz, nos = await _arvore(s, contrato_id)
    n = nos.get(no_id)
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nó não encontrado")
    pai = _pai_de(nos, n)
    i = pai.filhos.index(n)
    if i == 0:
        raise HTTPException(422, "não há irmão anterior para receber a tarefa")
    novo_pai = pai.filhos[i - 1]
    if novo_pai.folha:
        await _virar_resumo(s, novo_pai)
    pai.filhos.pop(i)
    novo_pai.filhos.append(n)
    n.parent_id = novo_pai.id
    await _aplicar(s, raiz, nos)
    return await recalcular_e_gravar(s, contrato_id, por=m.alterado_por or ctx.nome, motivo=f"indentar {n.edt}", data_status=None)


@router.post("/contratos/{contrato_id}/eap/{no_id}/recuar", response_model=sc.CronogramaSaida)
async def recuar(contrato_id: UUID, no_id: UUID, m: Motivo, ctx: Contexto = Depends(contexto_autenticado),
                 s: AsyncSession = Depends(sessao_tenant, scope="function")) -> sc.CronogramaSaida:
    """O nó passa a ser irmão logo depois do pai (sobe um nível)."""
    raiz, nos = await _arvore(s, contrato_id)
    n = nos.get(no_id)
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nó não encontrado")
    pai = _pai_de(nos, n)
    if pai.parent_id is None:
        raise HTTPException(422, "já está no nível das fases; não recua acima da raiz")
    avo = nos[pai.parent_id]
    pai.filhos.remove(n)
    avo.filhos.insert(avo.filhos.index(pai) + 1, n)
    n.parent_id = avo.id
    virou_folha = not pai.filhos
    if virou_folha:
        _marcar_folha(pai)
    await _aplicar(s, raiz, nos)
    if virou_folha:
        await _criar_atividade_de_folha(s, contrato_id, pai)
    return await recalcular_e_gravar(s, contrato_id, por=m.alterado_por or ctx.nome, motivo=f"recuar {n.edt}", data_status=None)


@router.delete("/contratos/{contrato_id}/eap/{no_id}", response_model=sc.CronogramaSaida)
async def excluir(contrato_id: UUID, no_id: UUID, ctx: Contexto = Depends(contexto_autenticado),
                  s: AsyncSession = Depends(sessao_tenant, scope="function")) -> sc.CronogramaSaida:
    raiz, nos = await _arvore(s, contrato_id)
    n = nos.get(no_id)
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nó não encontrado")
    if not n.folha:
        raise HTTPException(409, "RGR-EAP-EDIT-004: exclua ou mova as subtarefas antes")
    motivo = await _tem_dados(s, n)
    if motivo:
        raise HTTPException(409, f"RGR-EAP-EDIT-004: {n.edt} {motivo}; não se exclui")
    pai = _pai_de(nos, n)
    pai.filhos.remove(n)
    del nos[n.id]
    await s.execute(text("DELETE FROM plan.baseline_atividade WHERE atividade_id IN (SELECT id FROM plan.atividade WHERE eap_no_id = :n)"), {"n": n.id})
    await s.execute(text("DELETE FROM plan.atividade WHERE eap_no_id = :n"), {"n": n.id})
    await s.execute(text("DELETE FROM plan.eap_no WHERE id = :n"), {"n": n.id})
    virou_folha = not pai.filhos
    if virou_folha:
        _marcar_folha(pai)
    await _aplicar(s, raiz, nos)
    if virou_folha:
        await _criar_atividade_de_folha(s, contrato_id, pai)
    return await recalcular_e_gravar(s, contrato_id, por=ctx.nome, motivo=f"excluir {n.edt}", data_status=None)
