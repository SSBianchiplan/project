"""Cronograma, cockpit, EAP e registro de medições — onda 4.

Leitura calcula sempre a partir do banco. Simulação calcula sobre alterações
propostas e não grava nada. Gravação é ato humano separado, recalcula tudo e
deixa memória de cálculo append-only (cronograma_calculo).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dominio import avanco as dom_avanco
from ..dominio import cronograma as cr
from ..dominio.etapas import CRITERIOS
from ..dominio.tipos import ZERO, q, sabados
from ..infra import cronograma_repo as repo
from ..infra.tenancy import sessao_tenant
from . import schemas_cronograma as sc
from . import servico_cronograma as svc

router = APIRouter(prefix="/api/v1")


@router.get("/contratos", response_model=list[sc.ContratoSaida])
async def listar_contratos(s: AsyncSession = Depends(sessao_tenant, scope="function")) -> list[sc.ContratoSaida]:
    return [sc.ContratoSaida(**asdict(c)) for c in await repo.contratos(s)]


# ------------------------------------------------------------- cronograma

@router.get("/contratos/{contrato_id}/cronograma", response_model=sc.CronogramaSaida)
async def cronograma(
    contrato_id: UUID, data_status: date | None = None,
    s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.CronogramaSaida:
    plano = await svc.carregar(s, contrato_id)
    cpm, achs = svc.calcular(plano)
    return svc.montar_saida(plano, cpm, achs, data_status or plano.contrato.data_inicio)


@router.post("/contratos/{contrato_id}/cronograma/simular", response_model=sc.CronogramaSaida)
async def simular(
    contrato_id: UUID, entrada: sc.SimulacaoEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.CronogramaSaida:
    """RGR-CPM-002. Nada é gravado."""
    plano = await svc.carregar(s, contrato_id)
    cpm_antes, _ = svc.calcular(plano)
    proposto = svc.com_alteracoes(plano, entrada)
    cpm, achs = svc.calcular(proposto)
    impacto = svc.montar_impacto(plano, cpm_antes, proposto, cpm)
    return svc.montar_saida(proposto, cpm, achs, entrada.data_status or plano.contrato.data_inicio,
                            impacto)


COLUNAS = {
    "descricao": None,  # vai para eap_no
    "restricao": "restricao", "restricao_data": "restricao_data",
    "restricao_justificativa": "restricao_justificativa", "deadline": "deadline",
    "calendario": "calendario", "disciplina": "disciplina", "responsavel": "responsavel",
    "prioridade": "prioridade", "notas": "notas", "situacao": "situacao", "mod_qtd": "mod_qtd",
    "moi_qtd": "moi_qtd", "equipamentos_qtd": "equipamentos_qtd", "hh_estimadas": "hh_estimadas",
    "turno": "turno", "subcontratada": "subcontratada", "criterio_medicao": "criterio_medicao",
    "unidade": "unidade", "qtd_prevista": "qtd_prevista", "qtd_realizada": "qtd_realizada",
}


async def _persistir_atividade(
    s: AsyncSession, linha: dict, alt: sc.AtividadeAlteracao, contrato_id: UUID,
) -> None:
    definidos = alt.model_fields_set
    sets: dict[str, object] = {}
    for campo, coluna in COLUNAS.items():
        if coluna and campo in definidos:
            sets[coluna] = getattr(alt, campo)
    if "duracao" in definidos and alt.duracao is not None:
        sets["duracao_dias"] = alt.duracao
    if "marco" in definidos and alt.marco is not None:
        sets["tipo"] = "MARCO" if alt.marco else "ATIVIDADE"
        if alt.marco:
            sets["duracao_dias"] = 0
    if alt.limpar_deadline:
        sets["deadline"] = None
    if alt.restricao == "ASAP":
        sets["restricao_data"] = None
    if alt.excecao is not None:
        row = (await s.execute(text("""
            INSERT INTO plan.excecao_contratual
                (contrato_id, invariante, justificativa, autorizada_por, documento)
            VALUES (:c, 'INV-CRN-001', :j, :por, :doc) RETURNING id"""), {
            "c": contrato_id, "j": alt.excecao.justificativa,
            "por": alt.excecao.autorizada_por, "doc": alt.excecao.documento})).one()
        sets["excecao_id"] = row.id
    if alt.remover_excecao:
        sets["excecao_id"] = None
    if sets:
        clausula = ", ".join(f"{k} = :{k}" for k in sets)
        await s.execute(text(f"UPDATE plan.atividade SET {clausula} WHERE id = :id"),
                        {**sets, "id": linha["id"]})
    # atributos que moram no nó da EAP
    no_sets: dict[str, object] = {}
    if "descricao" in definidos and alt.descricao is not None:
        no_sets["descricao"] = alt.descricao
    if "pontos" in definidos and alt.pontos is not None:
        no_sets["pontos"] = alt.pontos
    if "memoria_peso" in definidos and alt.memoria_peso is not None:
        no_sets["memoria_peso"] = alt.memoria_peso
    if "marco" in definidos and alt.marco is not None:
        no_sets["tipo"] = "MARCO" if alt.marco else "ATIVIDADE"
        if alt.marco:
            no_sets["pontos"] = ZERO
    if no_sets:
        clausula = ", ".join(f"{k} = :{k}" for k in no_sets)
        await s.execute(text(f"UPDATE plan.eap_no SET {clausula} WHERE id = :id"),
                        {**no_sets, "id": linha["eap_no_id"]})


async def recalcular_e_gravar(
    s: AsyncSession, contrato_id: UUID, *, por: str, motivo: str, data_status: date | None,
) -> sc.CronogramaSaida:
    plano = await svc.carregar(s, contrato_id)
    cpm, achs = svc.calcular(plano)
    await repo.atualizar_datas(s, contrato_id, {
        edt: (plano.cal.data_de(d.ie), plano.cal.data_de(d.te)) for edt, d in cpm.atividades.items()
    })
    await repo.gravar_calculo(
        s, contrato_id, por=por, motivo=motivo, data_status=data_status,
        versao=cpm.versao_regra, fim_indice=cpm.fim, fim_data=plano.cal.data_de(cpm.fim),
        criticas=list(cpm.criticas), achados=svc.achados_json(achs),
        memoria=svc.memoria_json(cpm, plano.cal))
    return svc.montar_saida(plano, cpm, achs, data_status or plano.contrato.data_inicio)


@router.put("/atividades/{atividade_id}", response_model=sc.CronogramaSaida)
async def gravar_atividade(
    atividade_id: UUID, entrada: sc.GravacaoAtividade, data_status: date | None = None,
    s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.CronogramaSaida:
    """HUMAN-GATE: efetiva no cronograma vigente. Valida no domínio antes de
    escrever; o banco confere de novo (constraints e triggers)."""
    row = (await s.execute(
        text("SELECT contrato_id FROM plan.atividade WHERE id = :id"), {"id": atividade_id}
    )).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "atividade não encontrada")
    contrato_id = row.contrato_id
    plano = await svc.carregar(s, contrato_id)
    linha = next((a for a in plano.atividades.values() if a["id"] == atividade_id), None)
    if linha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "atividade não encontrada")
    alt = entrada.alteracao.model_copy(update={"edt": linha["edt"]})
    svc.calcular(svc.com_alteracoes(plano, sc.SimulacaoEntrada(atividades=[alt])))
    await _persistir_atividade(s, linha, alt, contrato_id)
    return await recalcular_e_gravar(
        s, contrato_id, por=entrada.alterado_por, motivo=f"atividade {linha['edt']}",
        data_status=data_status)


@router.put("/contratos/{contrato_id}/vinculos", response_model=sc.CronogramaSaida)
async def gravar_vinculos(
    contrato_id: UUID, entrada: sc.GravacaoVinculos, data_status: date | None = None,
    s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.CronogramaSaida:
    """Substitui a rede inteira. Ciclo é recusado aqui (domínio) e no banco (trigger)."""
    plano = await svc.carregar(s, contrato_id)
    svc.calcular(svc.com_alteracoes(plano, sc.SimulacaoEntrada(vinculos=entrada.vinculos)))
    await repo.substituir_vinculos(
        s, contrato_id, [(v.pred_edt, v.suc_edt, v.tipo, v.lag) for v in entrada.vinculos])
    return await recalcular_e_gravar(
        s, contrato_id, por=entrada.alterado_por, motivo="rede de precedência",
        data_status=data_status)


# ---------------------------------------------------------------- cockpit

def _semanas(plano: svc.Plano, fim_indice: int) -> list[date]:
    fim = max(plano.contrato.termino_contratual, plano.cal.data_de(fim_indice)) + timedelta(days=14)
    return sabados(plano.contrato.data_inicio, fim)


@router.get("/contratos/{contrato_id}/cockpit", response_model=sc.CockpitSaida)
async def cockpit(
    contrato_id: UUID, data_corte: date,
    referencia: str = Query(default="BL0", pattern="^(BL0|VIGENTE)$"),
    s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.CockpitSaida:
    return await montar_cockpit(s, contrato_id, data_corte, referencia)


async def montar_cockpit(s: AsyncSession, contrato_id: UUID, data_corte: date, referencia: str = "BL0") -> sc.CockpitSaida:
    plano = await svc.carregar(s, contrato_id)
    cpm, _ = svc.calcular(plano)
    bl = svc.cpm_baseline(plano)
    semanas = _semanas(plano, max(cpm.fim, bl.fim if bl else 0))
    previsto_bl0 = await repo.previsto_baseline(s, plano.baseline["id"]) if plano.baseline else {}
    lanc = await repo.medicoes_por_semana(s, contrato_id)
    return svc.montar_cockpit(
        plano, cpm, data_corte=data_corte, referencia=referencia, semanas=semanas,
        previsto_bl0=previsto_bl0, lancamentos=lanc, total_medicoes=len(lanc))


# -------------------------------------------------------------------- EAP

@router.get("/contratos/{contrato_id}/eap", response_model=sc.EapSaida)
async def eap(contrato_id: UUID, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> sc.EapSaida:
    plano = await svc.carregar(s, contrato_id)
    _, achs = svc.calcular(plano)
    nos = svc.nos_dominio(plano)
    base = plano.contrato.base_ponderacao
    saida = []
    for n in plano.nos:
        no = next(x for x in nos if x.edt == n["edt"])
        folhas = dom_avanco.descendentes(n["edt"], nos)
        pontos = Decimal(n["pontos"]) if n["folha"] else sum((f.pontos for f in folhas), start=ZERO)
        peso = q(pontos / base)
        av, _ = dom_avanco.agregar(n["edt"], nos, base, plano.progresso)
        saida.append(sc.NoEapSaida(
            id=n["id"], edt=n["edt"], parent_edt=n["parent_edt"], descricao=n["descricao"],
            nivel=int(n["nivel"]), tipo=n["tipo"], folha=bool(n["folha"]), pontos=pontos,
            memoria_peso=n["memoria_peso"] or "", peso=peso, avanco=av,
            contribuicao=q(peso * (av or ZERO)), marco=no.tipo.value == "MARCO"))
    soma = sum((Decimal(n["pontos"]) for n in plano.nos if n["folha"]), start=ZERO)
    fases = []
    for n in plano.nos:
        if int(n["nivel"]) == 2:
            folhas = dom_avanco.descendentes(n["edt"], nos)
            peso = q(sum((f.pontos for f in folhas), start=ZERO) / base)
            av, _ = dom_avanco.agregar(n["edt"], nos, base, plano.progresso)
            fases.append(sc.FaseEap(edt=n["edt"], descricao=n["descricao"], peso=peso,
                                    avanco=av or ZERO))
    relevantes = [a for a in achs if a.invariante in ("INV-EAP-001", "INV-EAP-003", "INV-DOC-002")]
    return sc.EapSaida(
        base=base, soma=soma, diferenca=soma - base,
        folhas_ponderadas=sum(1 for n in plano.nos if n["folha"] and Decimal(n["pontos"]) > ZERO),
        nos=saida, fases=fases,
        achados=[sc.AchadoSaida(invariante=a.invariante, severidade=a.severidade,
                                referencia=a.referencia, detalhe=a.detalhe) for a in relevantes])


@router.post("/contratos/{contrato_id}/eap/ponderar", response_model=sc.EapSaida)
async def ponderar(
    contrato_id: UUID, entrada: sc.PonderacaoEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> sc.EapSaida:
    """Reponderação em uma transação: a trigger diferida (INV-EAP-001) recusa
    o COMMIT se a soma não fechar. Não existe EAP "quase fechada"."""
    plano = await svc.carregar(s, contrato_id)
    for f in entrada.folhas:
        if f.edt not in plano.atividades and not any(
                n["edt"] == f.edt and n["folha"] for n in plano.nos):
            raise HTTPException(422, f"{f.edt} não é folha deste contrato")
        await s.execute(text("""
            UPDATE plan.eap_no SET pontos = :p,
                   memoria_peso = COALESCE(:m, memoria_peso)
             WHERE contrato_id = :c AND edt = :edt"""),
            {"p": f.pontos, "m": f.memoria_peso, "c": contrato_id, "edt": f.edt})
    soma = (await s.execute(text(
        "SELECT COALESCE(SUM(pontos), 0) FROM plan.eap_no WHERE contrato_id = :c AND folha"),
        {"c": contrato_id})).scalar_one()
    if soma != plano.contrato.base_ponderacao:
        raise HTTPException(
            422,
            f"INV-EAP-001: soma das folhas {soma} difere da base {plano.contrato.base_ponderacao}",
        )
    return await eap(contrato_id, s)


# ---------------------------------------------------------------- medições

@router.get("/contratos/{contrato_id}/medicoes", response_model=list[sc.MedicaoSaida])
async def listar_medicoes(
    contrato_id: UUID, limite: int = Query(default=80, ge=1, le=500),
    s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> list[sc.MedicaoSaida]:
    return [sc.MedicaoSaida(**m) for m in await repo.medicoes(s, contrato_id, limite)]


@router.post("/medicoes/{medicao_id}/estorno", status_code=status.HTTP_201_CREATED)
async def estornar(
    medicao_id: UUID, entrada: sc.EstornoMedicao, s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> dict:
    """Correção não apaga: entra ESTORNO com o valor anterior, apontando para
    o lançamento estornado. O lançamento original fica no histórico."""
    m = (await s.execute(text("""
        SELECT id, contrato_id, eap_no_id, data_corte, criado_em, tipo
          FROM plan.medicao WHERE id = :id"""), {"id": medicao_id})).mappings().one_or_none()
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "medição não encontrada")
    if m["tipo"] != "MEDICAO":
        raise HTTPException(422, "só medição pode ser estornada; estorno não se estorna")
    ja = (await s.execute(text(
        "SELECT 1 FROM plan.medicao WHERE estorna_id = :id"), {"id": medicao_id})).first()
    if ja:
        raise HTTPException(409, "medição já estornada")
    anterior = (await s.execute(text("""
        SELECT percentual_ac FROM plan.medicao
         WHERE eap_no_id = :n AND (data_corte, criado_em) < (:dc, :em)
         ORDER BY data_corte DESC, criado_em DESC LIMIT 1"""),
        {"n": m["eap_no_id"], "dc": m["data_corte"], "em": m["criado_em"]})).scalar_one_or_none()
    valor = anterior if anterior is not None else ZERO
    row = (await s.execute(text("""
        INSERT INTO plan.medicao
            (contrato_id, eap_no_id, data_corte, tipo, percentual_ac, memoria_calculo,
             evidencia, estorna_id, justificativa, criado_por)
        VALUES (:c, :n, :dc, 'ESTORNO', :pct, CAST(:mem AS jsonb), :ev, :est, :just, :por)
        RETURNING id, percentual_ac, encode(hash, 'hex') AS hash"""), {
        "c": m["contrato_id"], "n": m["eap_no_id"], "dc": m["data_corte"], "pct": valor,
        "mem": json.dumps({"estorno_de": str(medicao_id), "valor_restaurado": str(valor)}),
        "ev": f"estorno de {str(medicao_id)[:8]}", "est": medicao_id,
        "just": entrada.justificativa, "por": entrada.criado_por})).one()
    return {"id": row.id, "percentual_ac": row.percentual_ac, "hash": row.hash}


@router.get("/criterios")
async def criterios() -> dict[str, list[list[str]]]:
    """Tabelas 14 a 16 do procedimento, para a tela de medição."""
    return {k: [[n, str(p)] for n, p in v] for k, v in CRITERIOS.items()}


@router.get("/contratos/{contrato_id}/importacoes")
async def listar_importacoes(
    contrato_id: UUID, s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> list[dict]:
    rows = (await s.execute(text("""
        SELECT id, formato, arquivo_nome, estado, achados, criado_por, criado_em,
               aplicada_por, aplicada_em,
               jsonb_array_length(plano_bruto->'atividades') AS atividades,
               jsonb_array_length(plano_bruto->'vinculos') AS vinculos
          FROM plan.importacao WHERE contrato_id = :c ORDER BY criado_em DESC LIMIT 30"""),
        {"c": contrato_id})).mappings().all()
    return [dict(r) for r in rows]


__all__ = ["cr", "router"]
