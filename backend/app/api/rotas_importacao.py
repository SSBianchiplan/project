"""Importação de cronograma e lista de documentos ponderada."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dominio import documentos as dom_doc
from ..dominio.tipos import ErroDominio
from ..importacao import importar
from ..importacao.mpp import MpxjIndisponivel
from ..infra import cronograma_repo as repo
from ..infra.tenancy import sessao_tenant
from . import schemas_cronograma as sc
from . import servico_cronograma as svc
from .rotas_cronograma import recalcular_e_gravar

router = APIRouter(prefix="/api/v1")

LIMITE_BYTES = 64 * 1024 * 1024


@router.post("/importacoes", status_code=status.HTTP_201_CREATED)
async def receber_importacao(
    contrato_id: UUID = Form(...),
    criado_por: str = Form(...),
    arquivo: UploadFile = File(...),
    s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> dict:
    """Recebe MPP, XER, XML (Project ou P6) ou a máscara XLSX.

    Parseia, valida e guarda o plano normalizado. NÃO aplica nada: aplicar é
    ato humano (P4), e o banco recusa aplicação com achado bloqueante.
    """
    conteudo = await arquivo.read()
    if len(conteudo) > LIMITE_BYTES:
        raise HTTPException(413, "arquivo acima de 64 MB")

    edts = {r for (r,) in (await s.execute(text(
        "SELECT edt FROM plan.eap_no WHERE contrato_id = :c AND folha"), {"c": contrato_id})).all()}
    if not edts:
        raise HTTPException(404, "contrato sem EAP ou de outra organização")
    rede = {(v["pred_edt"], v["suc_edt"]) for v in await repo.vinculos(s, contrato_id)}
    try:
        plano = importar(arquivo.filename or "sem-nome", conteudo, edts_conhecidos=edts,
                         vinculos_conhecidos=rede)
    except MpxjIndisponivel as e:
        raise HTTPException(501, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    sha = hashlib.sha256(conteudo).digest()
    # dataclasses com slots não têm __dict__: asdict é o caminho
    bruto = {
        "nos": [asdict(n) for n in plano.nos],
        "atividades": [
            {**asdict(a),
             "inicio": a.inicio.isoformat() if a.inicio else None,
             "termino": a.termino.isoformat() if a.termino else None,
             "percentual_arquivo": str(a.percentual_arquivo) if a.percentual_arquivo else None}
            for a in plano.atividades
        ],
        "vinculos": [asdict(v) for v in plano.vinculos],
    }
    achados = [
        {"invariante": a.invariante, "severidade": a.severidade.value,
         "referencia": a.referencia, "detalhe": a.detalhe}
        for a in plano.achados
    ]
    estado = "REJEITADO" if plano.bloqueado else "VALIDADO"

    row = (await s.execute(text("""
        INSERT INTO plan.importacao
            (contrato_id, formato, arquivo_nome, arquivo_sha256, arquivo_uri,
             estado, plano_bruto, achados, criado_por)
        VALUES (:c, :f, :n, :sha, :uri, :e, CAST(:bruto AS jsonb), CAST(:ach AS jsonb), :por)
        RETURNING id"""), {
        "c": contrato_id, "f": plano.formato.value, "n": arquivo.filename,
        "sha": sha, "uri": f"s3://gps-importacoes/{sha.hex()}", "e": estado,
        "bruto": json.dumps(bruto, default=str), "ach": json.dumps(achados, default=str),
        "por": criado_por})).one()

    return {
        "id": row.id, "formato": plano.formato.value, "estado": estado,
        "atividades": len(plano.atividades), "vinculos": len(plano.vinculos),
        "achados": achados,
    }


@router.post("/importacoes/{importacao_id}/aplicar")
async def aplicar_importacao(
    importacao_id: UUID, aplicada_por: str, s: AsyncSession = Depends(sessao_tenant, scope="function")
) -> dict:
    """HUMAN-GATE. O banco recusa se houver achado bloqueante (ck_importacao_achados).

    Aplicar atualiza duração e rede das atividades cujo EDT existe na EAP;
    recalcula o CPM e grava memória. Avanço do arquivo nunca entra (INV-IMP-003).
    """
    row = (await s.execute(text("""
        UPDATE plan.importacao
           SET estado = 'APLICADO', aplicada_por = :por, aplicada_em = now()
         WHERE id = :id AND estado = 'VALIDADO'
        RETURNING id, contrato_id, plano_bruto"""),
        {"id": importacao_id, "por": aplicada_por})).mappings().one_or_none()
    if row is None:
        raise HTTPException(409, "importação inexistente ou não validada")
    contrato_id = row["contrato_id"]
    bruto = row["plano_bruto"]
    plano = await svc.carregar(s, contrato_id)

    def _edt(a: dict) -> str | None:
        for chave in ("id_externo", "edt"):
            if a.get(chave) in plano.atividades:
                return str(a[chave])
        return None

    alteracoes: list[sc.AtividadeAlteracao] = []
    mapa: dict[str, str] = {}
    for a in bruto.get("atividades", []):
        edt = _edt(a)
        if edt is None:
            continue
        mapa[str(a.get("id_externo"))] = edt
        alteracoes.append(sc.AtividadeAlteracao(
            edt=edt, duracao=0 if a.get("marco") else int(a.get("duracao_dias") or 0),
            descricao=a.get("nome") or None))
    vinculos = {(v["pred_edt"], v["suc_edt"]): v for v in plano.vinculos}
    for v in bruto.get("vinculos", []):
        p, q_ = mapa.get(str(v.get("predecessora"))), mapa.get(str(v.get("sucessora")))
        if p and q_:
            vinculos[(p, q_)] = {"pred_edt": p, "suc_edt": q_, "tipo": v.get("tipo", "TI"),
                                 "lag": int(v.get("lag_dias") or 0)}
    novos = [sc.VinculoEntrada(pred_edt=v["pred_edt"], suc_edt=v["suc_edt"], tipo=v["tipo"],
                               lag=int(v["lag"])) for v in vinculos.values()]
    entrada = sc.SimulacaoEntrada(atividades=alteracoes, vinculos=novos)
    svc.calcular(svc.com_alteracoes(plano, entrada))       # 422 se a rede ficar inválida

    for alt in alteracoes:
        linha = plano.atividades[alt.edt or ""]
        await s.execute(text("UPDATE plan.atividade SET duracao_dias = :d WHERE id = :id"),
                        {"d": alt.duracao, "id": linha["id"]})
    await repo.substituir_vinculos(
        s, contrato_id, [(v.pred_edt, v.suc_edt, v.tipo, v.lag) for v in novos])
    saida = await recalcular_e_gravar(
        s, contrato_id, por=aplicada_por, motivo=f"importação {importacao_id}", data_status=None)
    return {"id": row["id"], "estado": "APLICADO", "atividades_tocadas": len(alteracoes),
            "vinculos": len(novos), "fim": saida.fim.model_dump(mode="json")}


@router.post("/documentos", status_code=status.HTTP_201_CREATED)
async def cadastrar_documentos(
    contrato_id: UUID,
    eap_no_id: UUID,
    documentos: list[dict],
    s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> dict:
    """Lista de documentos com ponderação manual.

    O fechamento contra os pontos do nó é conferido aqui para dar mensagem
    antes do COMMIT, e novamente pelo banco (INV-DOC-001) — que é o que vale.
    """
    pontos_no = (await s.execute(
        text("SELECT pontos FROM plan.eap_no WHERE id = :id"), {"id": eap_no_id}
    )).scalar_one()

    try:
        ds = [
            dom_doc.Documento(
                codigo=d["codigo"], disciplina=d["disciplina"],
                hh_estimadas=Decimal(str(d["hh_estimadas"])),
                pontos=Decimal(str(d["pontos"])), memoria_peso=d["memoria_peso"],
            ) for d in documentos
        ]
        dom_doc.validar_fechamento(ds, pontos_no)
    except (ErroDominio, KeyError) as e:
        raise HTTPException(422, str(e)) from e

    for d in ds:
        await s.execute(text("""
            INSERT INTO plan.documento_engenharia
                (contrato_id, eap_no_id, codigo, titulo, disciplina,
                 hh_estimadas, pontos, memoria_peso)
            VALUES (:c, :n, :cod, :tit, :dis, :hh, :pts, :mem)"""), {
            "c": contrato_id, "n": eap_no_id, "cod": d.codigo, "tit": d.codigo,
            "dis": d.disciplina, "hh": d.hh_estimadas, "pts": d.pontos, "mem": d.memoria_peso})
    return {"documentos": len(ds), "pontos": str(pontos_no)}


@router.post("/documentos/{documento_id}/estagio")
async def registrar_estagio(
    documento_id: UUID, estagio: str, evidencia: str, registrado_por: str,
    s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> dict:
    atual = (await s.execute(
        text("SELECT estagio FROM plan.documento_engenharia WHERE id = :id"), {"id": documento_id}
    )).scalar_one()
    novo = dom_doc.Estagio(estagio)
    if not dom_doc.transicao_valida(dom_doc.Estagio(atual), novo):
        raise HTTPException(422, f"estágio não retrocede: {atual} -> {estagio}")
    await s.execute(text("""
        INSERT INTO plan.documento_evento (documento_id, estagio, evidencia, registrado_por)
        VALUES (:d, :e, :ev, :por)"""),
        {"d": documento_id, "e": estagio, "ev": evidencia, "por": registrado_por})
    await s.execute(text(
        "UPDATE plan.documento_engenharia SET estagio = :e, atualizado_em = now() WHERE id = :d"),
        {"d": documento_id, "e": estagio})
    return {"id": documento_id, "estagio": estagio}
