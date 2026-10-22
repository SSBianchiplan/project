"""Projeto novo: contrato, estrutura (EAP + atividades + rede + calendário) e linha de base.

A estrutura entra inteira, em uma transação: a trigger diferida INV-EAP-001 confere
a soma no COMMIT, então não existe "EAP pela metade" gravada. Enquanto o contrato
não tem baseline aprovada nem medição, a estrutura pode ser regravada por completo
(fase de elaboração). Depois disso, o caminho é a gaveta e a reponderação.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dominio import cronograma as cr
from ..dominio.tipos import ZERO, sabados
from ..infra import cronograma_repo as repo
from ..infra.tenancy import sessao_tenant
from . import schemas_cronograma as sc
from . import servico_cronograma as svc
from .rotas_cronograma import recalcular_e_gravar

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------- contrato

@router.post("/contratos", response_model=sc.ContratoSaida, status_code=status.HTTP_201_CREATED)
async def criar_contrato(
    entrada: sc.ContratoEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.ContratoSaida:
    """A organização vem da sessão (RLS): o cliente não escolhe para quem cria."""
    if entrada.termino_contratual <= entrada.data_inicio:
        raise HTTPException(422, "término contratual deve ser posterior ao início")
    row = (await s.execute(text("""
        INSERT INTO plan.contrato (organizacao_id, codigo, contratante, contratada, empreendimento,
                                   modalidade, base_ponderacao, data_inicio, termino_contratual, fuso)
        VALUES (plan.org_corrente(), :codigo, :contratante, :contratada, :empreendimento,
                :modalidade, :base, :inicio, :termino, :fuso)
        RETURNING id"""), {
        "codigo": entrada.codigo.strip(), "contratante": entrada.contratante,
        "contratada": entrada.contratada, "empreendimento": entrada.empreendimento,
        "modalidade": entrada.modalidade, "base": entrada.base_ponderacao,
        "inicio": entrada.data_inicio, "termino": entrada.termino_contratual,
        "fuso": entrada.fuso})).one()
    c = await repo.contrato(s, UUID(str(row.id)))
    assert c is not None
    return sc.ContratoSaida(**asdict(c))


@router.put("/contratos/{contrato_id}", response_model=sc.ContratoSaida)
async def alterar_contrato(
    contrato_id: UUID, entrada: sc.ContratoEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.ContratoSaida:
    if entrada.termino_contratual <= entrada.data_inicio:
        raise HTTPException(422, "término contratual deve ser posterior ao início")
    if await _tem_baseline_aprovada(s, contrato_id) and await _base_diferente(s, contrato_id, entrada.base_ponderacao):
        raise HTTPException(409, "base de ponderação não muda com baseline aprovada")
    r = (await s.execute(text("""
        UPDATE plan.contrato
           SET codigo = :codigo, contratante = :contratante, contratada = :contratada,
               empreendimento = :empreendimento, modalidade = :modalidade,
               base_ponderacao = :base, data_inicio = :inicio, termino_contratual = :termino, fuso = :fuso
         WHERE id = :id RETURNING id"""), {
        "id": contrato_id, "codigo": entrada.codigo.strip(), "contratante": entrada.contratante,
        "contratada": entrada.contratada, "empreendimento": entrada.empreendimento,
        "modalidade": entrada.modalidade, "base": entrada.base_ponderacao,
        "inicio": entrada.data_inicio, "termino": entrada.termino_contratual,
        "fuso": entrada.fuso})).one_or_none()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contrato não encontrado")
    c = await repo.contrato(s, contrato_id)
    assert c is not None
    return sc.ContratoSaida(**asdict(c))


async def _tem_baseline_aprovada(s: AsyncSession, contrato_id: UUID) -> bool:
    return (await s.execute(text(
        "SELECT 1 FROM plan.baseline WHERE contrato_id = :c AND estado = 'APROVADA' LIMIT 1"),
        {"c": contrato_id})).first() is not None


async def _base_diferente(s: AsyncSession, contrato_id: UUID, base: Decimal) -> bool:
    atual = (await s.execute(text("SELECT base_ponderacao FROM plan.contrato WHERE id = :c"),
                             {"c": contrato_id})).scalar_one_or_none()
    return atual is not None and Decimal(atual) != base


async def _tem_medicao(s: AsyncSession, contrato_id: UUID) -> bool:
    return (await s.execute(text(
        "SELECT 1 FROM plan.medicao WHERE contrato_id = :c LIMIT 1"), {"c": contrato_id})).first() is not None


# --------------------------------------------------------------- estrutura

def _arvore(nos: list[sc.NoEstrutura]) -> list[sc.NoEstrutura]:
    """Ordena por EDT numérico e confere hierarquia, tipo e unicidade."""
    vistos: set[str] = set()
    for n in nos:
        if n.edt in vistos:
            raise HTTPException(422, f"INV-EAP-004: EDT duplicado {n.edt}")
        vistos.add(n.edt)
    ordenados = sorted(nos, key=lambda n: [int(p) for p in n.edt.split(".")])
    raizes = [n for n in ordenados if "." not in n.edt]
    if len(raizes) != 1:
        raise HTTPException(422, "INV-EAP-005: a EAP precisa de exatamente um nó raiz (nível 1)")
    for n in ordenados:
        if "." in n.edt:
            pai = n.edt.rsplit(".", 1)[0]
            if pai not in vistos:
                raise HTTPException(422, f"INV-EAP-004: {n.edt} não tem pai {pai} na estrutura")
    tem_filho = {n.edt.rsplit(".", 1)[0] for n in ordenados if "." in n.edt}
    for n in ordenados:
        folha = n.edt not in tem_filho
        if folha and n.tipo == "RESUMO":
            raise HTTPException(422, f"{n.edt}: nó RESUMO sem filhos — defina como ATIVIDADE ou MARCO")
        if not folha and n.tipo != "RESUMO":
            raise HTTPException(422, f"INV-EAP-002: {n.edt} tem filhos e precisa ser RESUMO")
        if not folha and n.pontos != ZERO:
            raise HTTPException(422, f"INV-EAP-002: nó resumo {n.edt} não pode ter pontos")
        if n.tipo == "MARCO" and (n.pontos != ZERO or n.duracao != 0):
            raise HTTPException(422, f"INV-EAP-003/INV-CRN-004: marco {n.edt} tem peso e duração zero")
    return ordenados


def _plano_temporario(
    contrato: repo.LinhaContrato, entrada: sc.EstruturaEntrada, ordenados: list[sc.NoEstrutura],
) -> svc.Plano:
    """Plano em memória para validar antes de gravar (CPM + achados)."""
    tem_filho = {n.edt.rsplit(".", 1)[0] for n in ordenados if "." in n.edt}
    cal = cr.Calendario(contrato.data_inicio, frozenset(f.data for f in entrada.feriados), svc.HORIZONTE)
    nos = []
    ativs: dict[str, dict] = {}
    for n in ordenados:
        folha = n.edt not in tem_filho
        nos.append({"id": UUID(int=0), "edt": n.edt, "parent_edt": n.edt.rsplit(".", 1)[0] if "." in n.edt else None,
                    "descricao": n.descricao, "nivel": n.edt.count(".") + 1, "tipo": n.tipo, "folha": folha,
                    "pontos": n.pontos, "memoria_peso": n.memoria_peso})
        if folha:
            ativs[n.edt] = {
                "id": UUID(int=0), "eap_no_id": UUID(int=0), "edt": n.edt, "descricao": n.descricao,
                "pontos": n.pontos, "memoria_peso": n.memoria_peso, "codigo_externo": None,
                "duracao_dias": n.duracao, "tipo": n.tipo, "restricao": "ASAP", "restricao_data": None,
                "restricao_justificativa": "", "deadline": None, "calendario": "5x8",
                "disciplina": n.disciplina, "responsavel": n.responsavel, "prioridade": 500, "notas": "",
                "situacao": "NAO_INICIADA", "mod_qtd": 0, "moi_qtd": 0, "equipamentos_qtd": 0,
                "hh_estimadas": ZERO, "turno": 1, "subcontratada": "", "criterio_medicao": n.criterio_medicao,
                "unidade": "hh", "qtd_prevista": ZERO, "qtd_realizada": ZERO,
                "excecao_id": "pendente" if n.excecao else None,
                "excecao_documento": n.excecao.documento if n.excecao else None,
                "excecao_por": n.excecao.autorizada_por if n.excecao else None,
                "excecao_just": n.excecao.justificativa if n.excecao else None,
            }
    for v in entrada.vinculos:
        if v.pred_edt not in ativs or v.suc_edt not in ativs:
            raise HTTPException(422, f"vínculo {v.pred_edt} → {v.suc_edt} aponta para nó que não é folha")
    return svc.Plano(
        contrato=contrato, cal=cal, nos=nos, atividades=ativs,
        vinculos=[{"pred_edt": v.pred_edt, "suc_edt": v.suc_edt, "tipo": v.tipo, "lag": v.lag} for v in entrada.vinculos],
        progresso={}, baseline=None,
    )


@router.post("/contratos/{contrato_id}/estrutura/validar", response_model=sc.CronogramaSaida)
async def validar_estrutura(
    contrato_id: UUID, entrada: sc.EstruturaEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.CronogramaSaida:
    """Calcula a estrutura proposta sem gravar: CPM, achados, término."""
    contrato = await repo.contrato(s, contrato_id)
    if contrato is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contrato não encontrado")
    ordenados = _arvore(entrada.nos)
    plano = _plano_temporario(contrato, entrada, ordenados)
    cpm, achs = svc.calcular(plano)
    return svc.montar_saida(plano, cpm, achs, contrato.data_inicio)


@router.put("/contratos/{contrato_id}/estrutura", response_model=sc.CronogramaSaida)
async def gravar_estrutura(
    contrato_id: UUID, entrada: sc.EstruturaEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.CronogramaSaida:
    """Substitui EAP, atividades, rede e feriados. Só em elaboração (sem baseline
    aprovada nem medição): depois, a estrutura muda pela gaveta e pela reponderação."""
    contrato = await repo.contrato(s, contrato_id)
    if contrato is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contrato não encontrado")
    if await _tem_baseline_aprovada(s, contrato_id):
        raise HTTPException(409, "contrato já tem linha de base aprovada; a estrutura não é regravada")
    if await _tem_medicao(s, contrato_id):
        raise HTTPException(409, "contrato já tem medição registrada; a estrutura não é regravada")

    ordenados = _arvore(entrada.nos)
    plano = _plano_temporario(contrato, entrada, ordenados)
    cpm, _ = svc.calcular(plano)      # 422 em ciclo/lag antes de tocar o banco
    soma = sum((n.pontos for n in ordenados if n.edt in plano.atividades), start=ZERO)
    if soma != contrato.base_ponderacao:
        raise HTTPException(422, f"INV-EAP-001: soma das folhas {soma} difere da base {contrato.base_ponderacao}")

    # apaga o que existia (ordem inversa de dependência)
    await s.execute(text("""
        DELETE FROM plan.vinculo v USING plan.atividade a
         WHERE a.id = v.predecessora_id AND a.contrato_id = :c"""), {"c": contrato_id})
    await s.execute(text("DELETE FROM plan.baseline_atividade ba USING plan.atividade a "
                         "WHERE a.id = ba.atividade_id AND a.contrato_id = :c"), {"c": contrato_id})
    await s.execute(text("DELETE FROM plan.atividade WHERE contrato_id = :c"), {"c": contrato_id})
    await s.execute(text("DELETE FROM plan.excecao_contratual WHERE contrato_id = :c"), {"c": contrato_id})
    await s.execute(text("DELETE FROM plan.eap_no WHERE contrato_id = :c AND folha"), {"c": contrato_id})
    for nivel in range(7, 0, -1):
        await s.execute(text("DELETE FROM plan.eap_no WHERE contrato_id = :c AND nivel = :n"),
                        {"c": contrato_id, "n": nivel})
    await s.execute(text("DELETE FROM plan.feriado WHERE contrato_id = :c"), {"c": contrato_id})

    for f in entrada.feriados:
        await s.execute(text("INSERT INTO plan.feriado (contrato_id, data, descricao) VALUES (:c, :d, :desc) "
                             "ON CONFLICT DO NOTHING"), {"c": contrato_id, "d": f.data, "desc": f.descricao})

    ids: dict[str, UUID] = {}
    for n in ordenados:
        folha = n.edt in plano.atividades
        pai = ids.get(n.edt.rsplit(".", 1)[0]) if "." in n.edt else None
        row = (await s.execute(text("""
            INSERT INTO plan.eap_no (contrato_id, parent_id, edt, descricao, nivel, tipo, fase, folha,
                                     pontos, memoria_peso)
            VALUES (:c, :pai, :edt, :desc, :nivel, :tipo, :fase, :folha, :pts, :mem) RETURNING id"""), {
            "c": contrato_id, "pai": pai, "edt": n.edt, "desc": n.descricao,
            "nivel": n.edt.count(".") + 1, "tipo": n.tipo, "fase": n.fase, "folha": folha,
            "pts": n.pontos if folha else ZERO, "mem": n.memoria_peso})).one()
        ids[n.edt] = UUID(str(row.id))
        if not folha:
            continue
        excecao_id = None
        if n.excecao is not None:
            r = (await s.execute(text("""
                INSERT INTO plan.excecao_contratual (contrato_id, invariante, justificativa, autorizada_por, documento)
                VALUES (:c, 'INV-CRN-001', :j, :por, :doc) RETURNING id"""), {
                "c": contrato_id, "j": n.excecao.justificativa, "por": n.excecao.autorizada_por,
                "doc": n.excecao.documento})).one()
            excecao_id = r.id
        d = cpm.atividades[n.edt]
        await s.execute(text("""
            INSERT INTO plan.atividade (contrato_id, eap_no_id, duracao_dias, inicio_previsto, termino_previsto,
                                        tipo, excecao_id, disciplina, responsavel, criterio_medicao)
            VALUES (:c, :no, :dur, :ini, :fim, :tipo, :exc, :disc, :resp, :crit)"""), {
            "c": contrato_id, "no": ids[n.edt], "dur": n.duracao, "ini": plano.cal.data_de(d.ie),
            "fim": plano.cal.data_de(d.te), "tipo": n.tipo, "exc": excecao_id,
            "disc": n.disciplina, "resp": n.responsavel, "crit": n.criterio_medicao})
    await repo.substituir_vinculos(
        s, contrato_id, [(v.pred_edt, v.suc_edt, v.tipo, v.lag) for v in entrada.vinculos])
    return await recalcular_e_gravar(
        s, contrato_id, por=entrada.gravado_por, motivo="estrutura do projeto", data_status=None)


# ---------------------------------------------------------------- baseline

@router.post("/contratos/{contrato_id}/baselines", response_model=sc.BaselineSaida,
             status_code=status.HTTP_201_CREATED)
async def aprovar_baseline(
    contrato_id: UUID, entrada: sc.BaselineEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.BaselineSaida:
    """LVP de linha de base + baseline APROVADA vigente + snapshot de datas +
    previsto semanal linear (RGR-CRV-002). A anterior vira SUPERADA; seu previsto
    permanece intocado (INV-BL-003). Nota abaixo de 0,85 é recusada pelo banco."""
    plano = await svc.carregar(s, contrato_id)
    if not plano.atividades:
        raise HTTPException(422, "contrato sem estrutura: grave a EAP e a rede antes da linha de base")
    cpm, achs = svc.calcular(plano)
    bloqueantes = [a for a in achs if a.severidade == "BLOQUEANTE"]
    if bloqueantes:
        raise HTTPException(422, "emissão com achado bloqueante não vira linha de base: "
                            + "; ".join(f"{a.invariante} {a.referencia}" for a in bloqueantes[:5]))
    lvp = (await s.execute(text("""
        INSERT INTO plan.lvp_avaliacao (contrato_id, especie, emissao, nota, aprovado, avaliado_por)
        VALUES (:c, 'LINHA_DE_BASE', :em, :nota, true, :por) RETURNING id"""), {
        "c": contrato_id, "em": entrada.emissao, "nota": entrada.nota_lvp, "por": entrada.avaliado_por})).one()
    numero = (await s.execute(text(
        "SELECT COALESCE(MAX(numero) + 1, 0) FROM plan.baseline WHERE contrato_id = :c"),
        {"c": contrato_id})).scalar_one()
    await s.execute(text("""
        UPDATE plan.baseline SET vigente = false,
               estado = CASE WHEN estado = 'APROVADA' THEN 'SUPERADA'::plan.estado_baseline ELSE estado END
         WHERE contrato_id = :c AND vigente"""), {"c": contrato_id})
    bl = (await s.execute(text("""
        INSERT INTO plan.baseline (contrato_id, numero, estado, lvp_id, aprovada_por, aprovada_em, vigente)
        VALUES (:c, :n, 'APROVADA', :lvp, :por, now(), true) RETURNING id"""), {
        "c": contrato_id, "n": numero, "lvp": lvp.id, "por": entrada.aprovada_por})).one()
    for edt, d in cpm.atividades.items():
        await s.execute(text("""
            INSERT INTO plan.baseline_atividade (baseline_id, atividade_id, inicio, termino, duracao_dias)
            VALUES (:b, :a, :i, :f, :d)"""), {
            "b": bl.id, "a": plano.atividades[edt]["id"], "i": plano.cal.data_de(d.ie),
            "f": plano.cal.data_de(d.te), "d": d.te - d.ie + 1})
    fim = max(plano.contrato.termino_contratual, plano.cal.data_de(cpm.fim)) + timedelta(days=7)
    semanas = sabados(plano.contrato.data_inicio, fim)
    parcelas = cr.previsto_linear(cpm, plano.cal, semanas)
    for (edt, sab), parcela in parcelas.items():
        await s.execute(text("""
            INSERT INTO plan.previsto_semanal (baseline_id, eap_no_id, fim_semana, parcela)
            VALUES (:b, :n, :s, :p)"""), {
            "b": bl.id, "n": plano.atividades[edt]["eap_no_id"], "s": sab, "p": parcela})
    await repo.gravar_calculo(
        s, contrato_id, por=entrada.aprovada_por, motivo=f"baseline BL{numero}", data_status=None,
        versao=cpm.versao_regra, fim_indice=cpm.fim, fim_data=plano.cal.data_de(cpm.fim),
        criticas=list(cpm.criticas), achados=svc.achados_json(achs), memoria=svc.memoria_json(cpm, plano.cal))
    return sc.BaselineSaida(id=UUID(str(bl.id)), numero=int(numero), termino=plano.cal.data_de(cpm.fim),
                            semanas=len({s_ for _, s_ in parcelas}), atividades=len(cpm.atividades))
