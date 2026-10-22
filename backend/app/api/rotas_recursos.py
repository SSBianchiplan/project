"""Recursos: catálogo por contrato, alocação por atividade e histograma semanal."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dominio import recursos as rec
from ..dominio.cronograma import sabado_de
from ..infra.auth import Contexto
from ..infra.tenancy import contexto_autenticado, sessao_tenant
from . import schemas_cronograma as sc
from . import servico_cronograma as svc
from .rotas_cronograma import recalcular_e_gravar

router = APIRouter(prefix="/api/v1")


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecursoEntrada(Base):
    codigo: str = Field(min_length=1, max_length=30)
    nome: str = Field(min_length=1)
    tipo: str = Field(pattern="^(MOD|MOI|EQUIPAMENTO|MATERIAL)$")
    unidade: str = "pessoa"
    capacidade_dia: Decimal = Field(default=Decimal(0), ge=0)
    custo_hora: Decimal = Field(default=Decimal(0), ge=0)
    horas_dia: Decimal = Field(default=Decimal(8), gt=0)
    ativo: bool = True


class RecursoSaida(RecursoEntrada):
    id: UUID
    alocacoes: int = 0


class AlocacaoEntrada(Base):
    recurso_id: UUID
    quantidade_dia: Decimal = Field(gt=0)
    observacao: str = ""


class AlocacoesAtividade(Base):
    alocacoes: list[AlocacaoEntrada]


class AlocacaoSaida(Base):
    atividade_id: UUID
    edt: str
    descricao: str
    recurso_id: UUID
    recurso_codigo: str
    quantidade_dia: Decimal
    observacao: str


class SemanaSaida(Base):
    fim_semana: date
    demanda: Decimal
    capacidade: Decimal
    pico_dia: Decimal
    atividades: list[str]


class HistogramaSaida(Base):
    recurso: RecursoSaida
    semanas: list[SemanaSaida]
    total_unidade_dia: Decimal
    total_horas: Decimal
    custo_total: Decimal
    superalocadas: list[date]
    excedentes: list[tuple[date, Decimal]]


class AchadoRec(Base):
    invariante: str
    severidade: str
    referencia: str
    detalhe: str
    recurso_id: str = ""
    recurso_nome: str = ""
    fim_semana: date | None = None
    pico_dia: Decimal = Decimal(0)
    capacidade_dia: Decimal = Decimal(0)
    atividades: list[str] = []
    aceite: str | None = None          # justificativa registrada (excecao_contratual INV-REC-001)
    aceito_por: str | None = None


class AceiteEntrada(Base):
    recurso_id: UUID
    fim_semana: date
    justificativa: str = Field(min_length=10)


class ReaberturaEntrada(Base):
    recurso_id: UUID
    fim_semana: date


class NivelamentoEntrada(Base):
    recurso_id: UUID | None = None      # None = todos
    permitir_criticas: bool = False     # adiar atividade crítica estende o término
    max_movimentos: int = Field(default=200, ge=1, le=1000)
    alterado_por: str = "planejador"


class Movimento(Base):
    edt: str
    descricao: str
    recurso: str
    semana: date
    de: date
    para: date
    folga_antes: int


class NivelamentoSaida(Base):
    movimentos: list[Movimento]
    nao_resolvidas: list[str]
    superalocacoes_antes: int
    superalocacoes_depois: int
    termino_antes: date
    termino_depois: date
    cronograma: sc.CronogramaSaida | None


class PainelRecursos(Base):
    recursos: list[RecursoSaida]
    alocacoes: list[AlocacaoSaida]
    histogramas: list[HistogramaSaida]
    achados: list[AchadoRec]
    custo_total: Decimal
    horas_total: Decimal


async def _recursos(s: AsyncSession, contrato_id: UUID) -> list[dict]:
    rows = (await s.execute(text("""
        SELECT r.*, (SELECT count(*) FROM plan.alocacao a WHERE a.recurso_id = r.id) AS alocacoes
          FROM plan.recurso r WHERE r.contrato_id = :c ORDER BY r.tipo, r.codigo"""), {"c": contrato_id})).mappings().all()
    return [dict(r) for r in rows]


async def _alocacoes(s: AsyncSession, contrato_id: UUID) -> list[dict]:
    rows = (await s.execute(text("""
        SELECT al.atividade_id, n.edt, n.descricao, al.recurso_id, r.codigo AS recurso_codigo,
               al.quantidade_dia, al.observacao
          FROM plan.alocacao al
          JOIN plan.atividade a ON a.id = al.atividade_id
          JOIN plan.eap_no n ON n.id = a.eap_no_id
          JOIN plan.recurso r ON r.id = al.recurso_id
         WHERE a.contrato_id = :c
         ORDER BY string_to_array(n.edt, '.')::int[], r.codigo"""), {"c": contrato_id})).mappings().all()
    return [dict(r) for r in rows]


def _saida_recurso(r: dict) -> RecursoSaida:
    return RecursoSaida(id=r["id"], codigo=r["codigo"], nome=r["nome"], tipo=r["tipo"], unidade=r["unidade"],
                        capacidade_dia=r["capacidade_dia"], custo_hora=r["custo_hora"], horas_dia=r["horas_dia"],
                        ativo=bool(r["ativo"]), alocacoes=int(r.get("alocacoes") or 0))


@router.get("/contratos/{contrato_id}/recursos", response_model=PainelRecursos)
async def painel(contrato_id: UUID, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> PainelRecursos:
    return await montar_painel_recursos(s, contrato_id)


async def montar_painel_recursos(s: AsyncSession, contrato_id: UUID) -> PainelRecursos:
    plano = await svc.carregar(s, contrato_id)
    cpm, _ = svc.calcular(plano)
    recursos = await _recursos(s, contrato_id)
    alocs = await _alocacoes(s, contrato_id)
    dom_rec = [rec.Recurso(str(r["id"]), r["codigo"], r["nome"], r["tipo"], r["unidade"],
                           Decimal(r["capacidade_dia"]), Decimal(r["custo_hora"]), Decimal(r["horas_dia"]))
               for r in recursos if r["ativo"]]
    dom_aloc = [rec.Alocacao(a["edt"], str(a["recurso_id"]), Decimal(a["quantidade_dia"])) for a in alocs
                if plano.atividades.get(a["edt"], {}).get("tipo") != "MARCO"]
    hist = rec.histograma(dom_rec, dom_aloc, cpm, plano.cal)
    aceites = await _aceites(s, contrato_id)
    achs = _achados_detalhados(hist, aceites)
    por_id = {str(r["id"]): r for r in recursos}
    return PainelRecursos(
        recursos=[_saida_recurso(r) for r in recursos],
        alocacoes=[AlocacaoSaida(**a) for a in alocs],
        histogramas=[HistogramaSaida(
            recurso=_saida_recurso(por_id[h.recurso.id]),
            semanas=[SemanaSaida(fim_semana=w.fim_semana, demanda=w.demanda, capacidade=w.capacidade,
                                 pico_dia=w.pico_dia, atividades=list(w.atividades)) for w in h.semanas],
            total_unidade_dia=h.total_unidade_dia, total_horas=h.total_horas, custo_total=h.custo_total,
            superalocadas=list(h.superalocadas), excedentes=rec.sugerir_nivelamento(h)) for h in hist],
        achados=achs,
        custo_total=sum((h.custo_total for h in hist), start=Decimal(0)),
        horas_total=sum((h.total_horas for h in hist), start=Decimal(0)),
    )


@router.post("/contratos/{contrato_id}/recursos", response_model=RecursoSaida, status_code=status.HTTP_201_CREATED)
async def criar_recurso(contrato_id: UUID, e: RecursoEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> RecursoSaida:
    row = (await s.execute(text("""
        INSERT INTO plan.recurso (contrato_id, codigo, nome, tipo, unidade, capacidade_dia, custo_hora, horas_dia, ativo)
        VALUES (:c, :codigo, :nome, :tipo, :unidade, :cap, :custo, :horas, :ativo) RETURNING *"""), {
        "c": contrato_id, "codigo": e.codigo.strip().upper(), "nome": e.nome, "tipo": e.tipo, "unidade": e.unidade,
        "cap": e.capacidade_dia, "custo": e.custo_hora, "horas": e.horas_dia, "ativo": e.ativo})).mappings().one()
    return _saida_recurso(dict(row))


@router.put("/recursos/{recurso_id}", response_model=RecursoSaida)
async def alterar_recurso(recurso_id: UUID, e: RecursoEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> RecursoSaida:
    row = (await s.execute(text("""
        UPDATE plan.recurso SET codigo = :codigo, nome = :nome, tipo = :tipo, unidade = :unidade,
               capacidade_dia = :cap, custo_hora = :custo, horas_dia = :horas, ativo = :ativo
         WHERE id = :id RETURNING *"""), {
        "id": recurso_id, "codigo": e.codigo.strip().upper(), "nome": e.nome, "tipo": e.tipo, "unidade": e.unidade,
        "cap": e.capacidade_dia, "custo": e.custo_hora, "horas": e.horas_dia, "ativo": e.ativo})).mappings().one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recurso não encontrado")
    return _saida_recurso(dict(row))


@router.delete("/recursos/{recurso_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir_recurso(recurso_id: UUID, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> None:
    usados = (await s.execute(text("SELECT count(*) FROM plan.alocacao WHERE recurso_id = :id"), {"id": recurso_id})).scalar_one()
    if usados:
        raise HTTPException(409, f"recurso alocado em {usados} atividade(s); remova as alocações ou desative-o")
    await s.execute(text("DELETE FROM plan.recurso WHERE id = :id"), {"id": recurso_id})


@router.put("/atividades/{atividade_id}/alocacoes", response_model=list[AlocacaoSaida])
async def gravar_alocacoes(
    atividade_id: UUID, e: AlocacoesAtividade, s: AsyncSession = Depends(sessao_tenant, scope="function"),
) -> list[AlocacaoSaida]:
    """Substitui as alocações da atividade. Marco não recebe recurso."""
    a = (await s.execute(text("""
        SELECT a.contrato_id, a.tipo FROM plan.atividade a WHERE a.id = :id"""), {"id": atividade_id})).mappings().one_or_none()
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "atividade não encontrada")
    if a["tipo"] == "MARCO" and e.alocacoes:
        raise HTTPException(422, "INV-REC-003: marco não consome recurso")
    await s.execute(text("DELETE FROM plan.alocacao WHERE atividade_id = :id"), {"id": atividade_id})
    for al in e.alocacoes:
        await s.execute(text("""
            INSERT INTO plan.alocacao (atividade_id, recurso_id, quantidade_dia, observacao)
            VALUES (:a, :r, :q, :o)"""), {"a": atividade_id, "r": al.recurso_id, "q": al.quantidade_dia, "o": al.observacao})
    rows = await _alocacoes(s, a["contrato_id"])
    return [AlocacaoSaida(**r) for r in rows if r["atividade_id"] == atividade_id]


# ------------------------------------------------------------------ superalocação: aceite e nivelamento
async def _aceites(s: AsyncSession, contrato_id: UUID) -> dict[str, tuple[str, str]]:
    """documento 'recurso_id|YYYY-MM-DD' -> (justificativa, autorizada_por)."""
    rows = (await s.execute(text("""
        SELECT documento, justificativa, autorizada_por FROM plan.excecao_contratual
         WHERE contrato_id = :c AND invariante = 'INV-REC-001'"""), {"c": contrato_id})).all()
    return {r.documento: (r.justificativa, r.autorizada_por) for r in rows}


def _achados_detalhados(hist: list[rec.HistogramaRecurso], aceites: dict[str, tuple[str, str]]) -> list[AchadoRec]:
    out: list[AchadoRec] = []
    for h in hist:
        if h.recurso.capacidade_dia <= 0:
            continue
        for w in h.semanas:
            if w.pico_dia > h.recurso.capacidade_dia:
                chave = f"{h.recurso.id}|{w.fim_semana.isoformat()}"
                ac = aceites.get(chave)
                out.append(AchadoRec(
                    invariante="INV-REC-001", severidade="ATENCAO" if ac else "ALERTA", referencia=h.recurso.codigo,
                    detalhe=f"{h.recurso.nome}: pico de {w.pico_dia.normalize()} {h.recurso.unidade}/dia na semana de {w.fim_semana.strftime('%d/%m/%Y')} para capacidade {h.recurso.capacidade_dia.normalize()} — atividades {', '.join(w.atividades)}",
                    recurso_id=h.recurso.id, recurso_nome=h.recurso.nome, fim_semana=w.fim_semana, pico_dia=w.pico_dia,
                    capacidade_dia=h.recurso.capacidade_dia, atividades=list(w.atividades),
                    aceite=ac[0] if ac else None, aceito_por=ac[1] if ac else None))
    return out


@router.post("/contratos/{contrato_id}/recursos/superalocacoes/aceitar", response_model=PainelRecursos)
async def aceitar_superalocacao(contrato_id: UUID, e: AceiteEntrada, ctx: Contexto = Depends(contexto_autenticado),
                                s: AsyncSession = Depends(sessao_tenant, scope="function")) -> PainelRecursos:
    """Aceite com justificativa: fica registrado como exceção contratual INV-REC-001 (auditável)."""
    doc = f"{e.recurso_id}|{e.fim_semana.isoformat()}"
    await s.execute(text("DELETE FROM plan.excecao_contratual WHERE contrato_id = :c AND invariante = 'INV-REC-001' AND documento = :d"), {"c": contrato_id, "d": doc})
    await s.execute(text("""
        INSERT INTO plan.excecao_contratual (contrato_id, invariante, justificativa, autorizada_por, documento)
        VALUES (:c, 'INV-REC-001', :j, :por, :d)"""), {"c": contrato_id, "j": e.justificativa.strip(), "por": ctx.nome, "d": doc})
    return await montar_painel_recursos(s, contrato_id)


@router.post("/contratos/{contrato_id}/recursos/superalocacoes/reabrir", response_model=PainelRecursos)
async def reabrir_superalocacao(contrato_id: UUID, e: ReaberturaEntrada, s: AsyncSession = Depends(sessao_tenant, scope="function")) -> PainelRecursos:
    await s.execute(text("DELETE FROM plan.excecao_contratual WHERE contrato_id = :c AND invariante = 'INV-REC-001' AND documento = :d"),
                    {"c": contrato_id, "d": f"{e.recurso_id}|{e.fim_semana.isoformat()}"})
    return await montar_painel_recursos(s, contrato_id)


@router.post("/contratos/{contrato_id}/recursos/nivelar", response_model=NivelamentoSaida)
async def nivelar(contrato_id: UUID, e: NivelamentoEntrada, ctx: Contexto = Depends(contexto_autenticado),
                  s: AsyncSession = Depends(sessao_tenant, scope="function")) -> NivelamentoSaida:
    """Nivelamento por adiamento (RGR-REC-002), em memória: enquanto houver semana com pico acima da
    capacidade (e sem aceite), adia a atividade daquela semana com maior folga sobrante para a primeira
    segunda-feira em que ela inteira cabe na capacidade, gravando restrição SNET com justificativa.
    Sem permitir_criticas só se adia dentro da folga total (término inalterado). Atividade já iniciada
    não se move. Tudo é calculado em memória e persistido uma vez no fim."""
    aceites = await _aceites(s, contrato_id)
    plano = await svc.carregar(s, contrato_id)
    recursos = await _recursos(s, contrato_id)
    alocs = await _alocacoes(s, contrato_id)
    dom_rec = [rec.Recurso(str(r["id"]), r["codigo"], r["nome"], r["tipo"], r["unidade"], Decimal(r["capacidade_dia"]), Decimal(r["custo_hora"]), Decimal(r["horas_dia"]))
               for r in recursos if r["ativo"] and (e.recurso_id is None or r["id"] == e.recurso_id)]
    dom_aloc = [rec.Alocacao(a["edt"], str(a["recurso_id"]), Decimal(a["quantidade_dia"])) for a in alocs if plano.atividades.get(a["edt"], {}).get("tipo") != "MARCO"]
    hoje_idx = plano.cal.indice_de(date.today())
    movimentos: list[Movimento] = []
    nao_resolvidas: set[str] = set()
    tentativas: set[str] = set()
    pendentes_snet: dict[str, tuple[date, str]] = {}
    cpm, _achs = svc.calcular(plano)
    termino_antes = plano.cal.data_de(cpm.fim)
    antes: int | None = None
    for _ in range(e.max_movimentos * 3 + 1):
        hist = rec.histograma(dom_rec, dom_aloc, cpm, plano.cal)
        pend = [(h, w) for h in hist if h.recurso.capacidade_dia > 0 for w in h.semanas
                if w.pico_dia > h.recurso.capacidade_dia and f"{h.recurso.id}|{w.fim_semana.isoformat()}" not in aceites]
        if antes is None:
            antes = len(pend)
        pend = [(h, w) for h, w in pend if f"{h.recurso.id}|{w.fim_semana.isoformat()}" not in nao_resolvidas]
        if not pend or len(movimentos) >= e.max_movimentos:
            break
        h, w = min(pend, key=lambda hw: hw[1].fim_semana)
        chave = f"{h.recurso.id}|{w.fim_semana.isoformat()}"
        pico_sem = {x.fim_semana: x.pico_dia for x in h.semanas}
        qtd_de = {a.edt: a.quantidade_dia for a in dom_aloc if a.recurso_id == h.recurso.id}
        cap = h.recurso.capacidade_dia
        cal = plano.cal

        def cabe(i_ini: int, dur: int, qtd: Decimal, _pico: dict = pico_sem, _cap: Decimal = cap, _cal: object = cal) -> bool:
            semanas_ocupadas = {sabado_de(_cal.data_de(i)) for i in range(i_ini, i_ini + dur)}  # type: ignore[attr-defined]
            return all(_pico.get(sb, Decimal(0)) + qtd <= _cap for sb in semanas_ocupadas)

        candidatas = []
        for edt in w.atividades:
            d = cpm.atividades.get(edt)
            if d is None or f"{chave}|{edt}" in tentativas:
                continue
            if plano.progresso.get(edt, Decimal(0)) > 0 or d.ie <= hoje_idx:      # o passado não nivela
                continue
            if d.critica and not e.permitir_criticas:
                continue
            dur = d.te - d.ie + 1
            qtd = qtd_de.get(edt, Decimal(0))
            limite = d.folga_total if not e.permitir_criticas else 260
            alvo = None
            prox = w.fim_semana + timedelta(days=2)
            while True:
                i_prox = cal.indice_de(prox)
                desloc = i_prox - d.ie
                if desloc <= 0:
                    prox += timedelta(days=7)
                    continue
                if desloc > limite or i_prox >= len(cal) - dur:
                    break
                if cabe(i_prox, dur, qtd):
                    alvo = (i_prox, desloc)
                    break
                prox += timedelta(days=7)
            if alvo is None:
                continue
            candidatas.append((d.folga_total - alvo[1], -d.ie, edt, alvo[0]))
        if not candidatas:
            nao_resolvidas.add(chave)
            continue
        _, _, edt, i_para = max(candidatas)
        d = cpm.atividades[edt]
        tentativas.add(f"{chave}|{edt}")
        de, para = cal.data_de(d.ie), cal.data_de(i_para)
        just = f"nivelamento de {h.recurso.codigo}: pico {w.pico_dia.normalize()} > {cap.normalize()} na semana de {w.fim_semana:%d/%m/%Y}"
        plano.atividades[edt]["restricao"] = "SNET"
        plano.atividades[edt]["restricao_data"] = para
        plano.atividades[edt]["restricao_justificativa"] = just
        pendentes_snet[edt] = (para, just)
        movimentos.append(Movimento(edt=edt, descricao=str(plano.atividades[edt].get("descricao", "")), recurso=h.recurso.codigo, semana=w.fim_semana, de=de, para=para, folga_antes=d.folga_total))
        cpm, _achs = svc.calcular(plano)
    cronograma = None
    if pendentes_snet:
        for edt, (para, just) in pendentes_snet.items():
            await s.execute(text("""
                UPDATE plan.atividade a SET restricao = 'SNET', restricao_data = :d, restricao_justificativa = :j
                  FROM plan.eap_no n WHERE n.id = a.eap_no_id AND a.contrato_id = :c AND n.edt = :e"""), {"d": para, "j": just, "c": contrato_id, "e": edt})
        cronograma = await recalcular_e_gravar(s, contrato_id, por=e.alterado_por or ctx.nome, motivo=f"nivelamento de recursos ({len(movimentos)} adiamentos)", data_status=None)
    hist = rec.histograma(dom_rec, dom_aloc, cpm, plano.cal)
    depois = len([1 for h in hist if h.recurso.capacidade_dia > 0 for w in h.semanas if w.pico_dia > h.recurso.capacidade_dia and f"{h.recurso.id}|{w.fim_semana.isoformat()}" not in aceites])
    return NivelamentoSaida(movimentos=movimentos, nao_resolvidas=sorted(nao_resolvidas), superalocacoes_antes=antes or 0, superalocacoes_depois=depois,
                            termino_antes=termino_antes, termino_depois=plano.cal.data_de(cpm.fim), cronograma=cronograma)
