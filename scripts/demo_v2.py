"""Projeto DEMO v2 — CT-200-COGER (Inovação Controle Novo Plan), semeado pela API e verificado ponta a ponta.

Uso:
    PYTHONUTF8=1 python scripts/demo_v2.py --somente      # remove todos os contratos, semeia via API, roda o roteiro, grava panorama_demo.html
    PYTHONUTF8=1 python scripts/demo_v2.py --sem-limpar   # adiciona o DEMO v2 ao lado dos existentes
    PYTHONUTF8=1 python scripts/demo_v2.py --so-roteiro   # só verificações e panorama sobre o demo já semeado

Exige API viva (padrão http://localhost:8001) e admin master (bootstrap_admin.py). A limpeza usa GPS_DSN_SEED
(padrão postgresql://gps:gps@localhost:5433/gplansis) e é a única escrita fora da API.
Início do projeto: 13/07/2026. Data de status do roteiro: último sábado até hoje.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
BASE = os.environ.get("GPS_API", "http://127.0.0.1:8001") + "/api/v1"
EMAIL = os.environ.get("GPS_ADMIN_EMAIL", "admin@exemplo.com")
SENHA = os.environ.get("GPS_ADMIN_SENHA", "")
DSN = os.environ.get("GPS_DSN_SEED", "postgresql://gps:gps@localhost:5433/gplansis")

INICIO = date(2026, 7, 13)
TERMINO_CONTRATUAL = date(2027, 11, 30)
HOJE = date.today()
DATA_STATUS = HOJE - timedelta(days=(HOJE.weekday() - 5) % 7)          # último sábado
CONTRATO = {"codigo": "CT-200-COGER", "contratante": "Inovação Controle Novo Plan", "contratada": "Novo Plan Engenharia",
            "empreendimento": "UNIDADE DE COGERAÇÃO A BIOMASSA 24 MW", "modalidade": "EPC",
            "data_inicio": INICIO.isoformat(), "termino_contratual": TERMINO_CONTRATUAL.isoformat()}
FERIADOS = ["2026-09-07", "2026-10-12", "2026-11-02", "2026-11-15", "2026-12-25", "2027-01-01", "2027-03-26", "2027-04-21",
            "2027-05-01", "2027-05-27", "2027-09-07", "2027-10-12", "2027-11-02", "2027-11-15"]
FASE_ENUM = {"1": "MILESTONES", "2": "ENGENHARIA", "3": "SUPRIMENTOS", "4": "CONSTRUCAO_CIVIL", "5": "MONTAGEM_ELETROMECANICA",
             "6": "MONTAGEM_ELETROMECANICA", "7": "COMISSIONAMENTO", "8": None}
CATEGORIA_FASE = {"2": "SERVICO", "3": "EQUIPAMENTO", "4": "SUBCONTRATO", "5": "SERVICO", "6": "MATERIAL", "7": "SERVICO", "8": "OUTRO"}
MATERIAL_3 = {"1.3.8", "1.3.9", "1.3.10", "1.3.11"}

# edt, tarefa, dur, pred, recursos, itens R$ mil, peso  (dur 0 = marco; 2 campos = fase)
EAP: list[tuple] = [
("1", "MARCOS CONTRATUAIS"),
("1.1", "Assinatura do contrato", 0, "", "", 0, 0), ("1.2", "Liberação do canteiro", 0, "8.4", "", 0, 0),
("1.3", "Aprovação do projeto básico", 0, "2.1,2.2,2.3", "", 0, 0), ("1.4", "Chegada da caldeira ao site", 0, "3.2", "", 0, 0),
("1.5", "Energização da subestação", 0, "7.4", "", 0, 0), ("1.6", "Aceite provisório (TAP)", 0, "7.10", "", 0, 0),
("2", "ENGENHARIA"),
("2.1", "Projeto básico de processo", 20, "1.1", "ENGP 2", 180, 40), ("2.2", "Projeto básico civil", 15, "1.1", "ENGC 2", 90, 25),
("2.3", "Projeto básico elétrico", 15, "1.1", "ENGE 1", 90, 25), ("2.4", "Detalhamento mecânico", 30, "1.3", "ENGM 2, PROJ 2", 260, 60),
("2.5", "Detalhamento de tubulação", 35, "2.4II+10", "ENGM 1, PROJ 3", 240, 60), ("2.6", "Detalhamento elétrico", 30, "1.3", "ENGE 2, PROJ 1", 200, 50),
("2.7", "Detalhamento de automação", 25, "2.6II+5", "ENGE 1, PROJ 1", 160, 40), ("2.8", "Lista de sobressalentes", 10, "2.4,2.6", "ENGM 1", 20, 10),
("2.9", "As-built", 15, "7.9", "PROJ 2", 60, 20), ("2.10", "Data book", 15, "2.9II", "ENGP 1, PROJ 1", 40, 20),
("3", "SUPRIMENTOS E FABRICAÇÃO"),
("3.1", "Especificação e compra da caldeira", 15, "2.1", "ENGP 1, SUPR 1", 120, 20), ("3.2", "Fabricação da caldeira (fornecedor)", 120, "3.1", "SUPR 1", 11800, 180),
("3.3", "Especificação e compra do turbogerador", 15, "2.1", "ENGM 1, SUPR 1", 120, 20), ("3.4", "Fabricação da turbina", 140, "3.3", "SUPR 1", 9600, 160),
("3.5", "Fabricação do gerador", 110, "3.3", "", 5200, 100), ("3.6", "Transformadores", 90, "2.3", "SUPR 1", 2400, 60),
("3.7", "Painéis de média tensão", 80, "2.6", "SUPR 1", 1500, 50), ("3.8", "Materiais de tubulação", 45, "2.5", "SUPR 1", 1900, 60),
("3.9", "Estruturas metálicas", 60, "2.4", "SUPR 1", 1300, 50), ("3.10", "Instrumentos e válvulas de controle", 50, "2.7", "SUPR 1", 1100, 40),
("3.11", "Cabos MT e BT", 40, "2.6", "", 800, 30), ("3.12", "Sistema de tratamento de água", 70, "2.1", "SUPR 1", 1700, 50),
("4", "OBRAS CIVIS"),
("4.1", "Mobilização de canteiro", 10, "1.2", "MOI 2, PEDR 4, AJUD 6, RETRO 1", 120, 20), ("4.2", "Terraplenagem e drenagem", 20, "4.1", "OPER 3, RETRO 2, CAMB 4, TOPO 1", 380, 40),
("4.3", "Fundações da caldeira", 0, "", "", 0, 0),
("4.3.1", "Escavação e lastro", 6, "4.2,2.2", "OPER 2, RETRO 1, AJUD 6", 90, 12),
("4.3.2", "Armação e formas", 0, "", "", 0, 0),
("4.3.2.1", "Armação bloco A (fornalha)", 7, "4.3.1", "ARM 4, CARP 3, AJUD 4", 140, 20),
("4.3.2.2", "Armação bloco B (tambor)", 7, "4.3.2.1II+3", "ARM 4, CARP 3, AJUD 4", 130, 18),
("4.3.3", "Concretagem dos blocos", 6, "4.3.2.1,4.3.2.2", "PEDR 8, AJUD 6, GUIN 1", 200, 20),
("4.3.4", "Cura e liberação para montagem", 6, "4.3.3", "PEDR 2, AJUD 2", 60, 10), ("4.4", "Fundações do turbogerador", 25, "4.2,2.2", "PEDR 6, ARM 6, AJUD 8, CARP 4", 480, 60),
("4.5", "Bases de equipamentos auxiliares", 20, "4.4II+10", "PEDR 4, ARM 3, AJUD 6", 210, 40), ("4.6", "Estrutura da casa de força", 45, "4.4", "MONT 8, SOLD 4, AJUD 6, GUIN 1", 900, 90),
("4.7", "Subestação — obras civis", 20, "4.2", "PEDR 4, ARM 3, AJUD 6", 260, 40), ("4.8", "Fundações do pipe-rack", 15, "4.3.3II+5", "PEDR 4, ARM 3, AJUD 4", 180, 30),
("4.9", "Prédio de controle e operação", 40, "4.7", "PEDR 6, CARP 4, AJUD 6, ELET 2", 560, 60), ("4.10", "Pavimentação e vias internas", 15, "6.10", "OPER 2, CAMB 3, AJUD 4", 300, 30),
("4.11", "Urbanização e paisagismo", 10, "4.10", "AJUD 4", 60, 10), ("4.12", "Desmobilização de canteiro civil", 5, "4.11", "MOI 1, AJUD 4", 30, 5),
("5", "MONTAGEM ELETROMECÂNICA"),
("5.1", "Descarga e posicionamento da caldeira", 5, "1.4,4.3.4", "MONT 6, GUIN 2, AJUD 6", 180, 20), ("5.2", "Montagem da caldeira", 0, "", "", 0, 0),
("5.2.1", "Estrutura e escadas da caldeira", 15, "5.1", "MONT 10, SOLD 4, AJUD 6, GUIN 1", 350, 45),
("5.2.2", "Tambor, paredes de água e superaquecedor", 25, "5.2.1II+10", "MONT 12, SOLD 8, AJUD 6, GUIN 1", 650, 80),
("5.2.3", "Grelha, alimentação de biomassa e cinzas", 15, "5.2.2II+10", "MONT 8, SOLD 4, AJUD 4", 260, 35),
("5.2.4", "Ventiladores, dutos e chaminé", 12, "5.2.3II+5", "MONT 6, SOLD 3, GUIN 1", 140, 20),
("5.3", "Montagem da turbina", 30, "3.4,4.4", "MONT 8, MEC 4, GUIN 1", 700, 90), ("5.4", "Montagem do gerador", 20, "3.5,5.3II+10", "MONT 6, ELET 2, GUIN 1", 400, 60),
("5.5", "Alinhamento do turbogerador", 10, "5.3,5.4", "MEC 4", 90, 30), ("5.6", "Condensador e sistema de vácuo", 15, "4.5", "MONT 6, SOLD 3", 320, 40),
("5.7", "Bombas e equipamentos auxiliares", 15, "4.5,3.12", "MONT 4, MEC 2", 260, 40), ("5.8", "Montagem do pipe-rack", 25, "4.8,3.9", "MONT 6, SOLD 3, GUIN 1", 380, 50),
("5.9", "Tubulação de vapor e condensado", 45, "5.8,3.8,5.2.2TT", "MONT 8, SOLD 8, AJUD 4", 960, 120), ("5.10", "Tubulação de água e utilidades", 35, "5.8,3.8", "MONT 6, SOLD 4", 520, 70),
("5.11", "Isolamento térmico", 30, "5.9TT+10", "ISOL 8", 480, 50), ("5.12", "Teste hidrostático e END", 10, "5.9,5.10", "MEC 2, SOLD 2", 220, 30),
("6", "ELÉTRICA E AUTOMAÇÃO"),
("6.1", "Montagem eletromecânica da subestação", 30, "4.7,3.6", "ELET 6, MONT 2, GUIN 1", 420, 60), ("6.2", "Instalação dos transformadores", 10, "6.1TT,3.6", "ELET 4, GUIN 1", 120, 30),
("6.3", "Painéis de média tensão", 20, "4.9,3.7", "ELET 6", 240, 40), ("6.4", "Painéis de baixa tensão e CCMs", 20, "6.3II+5", "ELET 6", 220, 40),
("6.5", "Lançamento de cabos MT", 25, "6.1,3.11", "ELET 8, AJUD 4", 280, 50), ("6.6", "Lançamento de cabos BT e controle", 30, "6.4,3.11", "ELET 10, AJUD 4", 320, 60),
("6.7", "Iluminação e tomadas industriais", 20, "6.6II+10", "ELET 4", 110, 20), ("6.8", "Instrumentação de campo", 30, "5.9,3.10", "INST 6", 360, 60),
("6.9", "SDCD e sistema de automação", 25, "6.8II+10,2.7", "INST 4, ENGE 1", 680, 60), ("6.10", "Malha de aterramento e SPDA", 15, "4.2", "ELET 4, AJUD 4", 130, 20),
("6.11", "Testes elétricos e ensaios", 15, "6.5,6.6,6.2", "ELET 4, INST 2", 160, 30),
("7", "COMISSIONAMENTO E PARTIDA"),
("7.1", "Pré-operacional mecânico", 15, "5.12,5.11", "MEC 4, MONT 4", 180, 40), ("7.2", "Pré-operacional elétrico", 10, "6.11", "ELET 4, INST 2", 120, 30),
("7.3", "Limpeza química e sopragem de vapor", 10, "7.1,5.2.4", "MEC 4, OPER 2", 260, 40), ("7.4", "Energização da subestação", 5, "7.2", "ELET 4, ENGE 1", 60, 20),
("7.5", "Comissionamento da caldeira", 15, "7.3,7.4", "MEC 4, INST 3, OPER 3", 340, 60), ("7.6", "Comissionamento do turbogerador", 10, "7.5,5.5", "MEC 4, INST 2, OPER 3", 300, 50),
("7.7", "Sincronismo com a rede", 5, "7.6,1.5", "ELET 2, ENGE 1, OPER 2", 90, 20), ("7.8", "Teste de performance (72 h)", 10, "7.7", "OPER 4, INST 2, ENGP 1", 150, 40),
("7.9", "Treinamento da operação", 10, "7.6II", "ENGP 1, OPER 4", 80, 20), ("7.10", "Entrega e termo de aceite", 5, "7.8,7.9,2.10", "ENGP 1, MOI 1", 20, 10),
("8", "GESTÃO, HSE E LICENÇAS"),
("8.1", "Plano de execução do projeto", 10, "1.1", "MOI 2, ENGP 1", 40, 10), ("8.2", "Plano de HSE e treinamentos iniciais", 10, "1.1", "HSE 2", 50, 10),
("8.3", "Plano da qualidade e inspeção", 10, "1.1", "MOI 1", 30, 10), ("8.4", "Licenciamento ambiental de instalação", 40, "1.1", "MOI 1", 180, 20),
("8.5", "Auditoria de fase — engenharia", 5, "2.4,2.6", "MOI 1", 20, 5), ("8.6", "Auditoria de fase — montagem", 5, "5.12", "MOI 1", 20, 5),
("8.7", "Desmobilização geral", 10, "7.10", "MOI 1, AJUD 4", 60, 10),
]

def E(edt: str) -> str:
    """EDT com a raiz: a EAP exige exatamente um nó de nível 1 (INV-EAP-005); as fases ficam no nível 2."""
    return "1." + edt


def _prefixar_preds(s: str) -> str:
    return ",".join(("1." + x.strip()) if x.strip() else "" for x in s.split(",")) if s else ""


RAIZ_EAP = ("1", "UNIDADE DE COGERAÇÃO A BIOMASSA 24 MW")
EAP = [RAIZ_EAP] + [((E(l[0]), l[1]) if len(l) == 2 else (E(l[0]), l[1], l[2], _prefixar_preds(l[3]), l[4], l[5], l[6])) for l in EAP]


def fase_de(edt: str) -> str:
    return edt.split(".")[1] if edt.count(".") >= 1 else ""


# sigla, nome, tipo, capacidade/dia, R$/h
RECURSOS = [("ENGP", "Engenheiro de processo", "MOI", 3, 180), ("ENGC", "Engenheiro civil", "MOI", 2, 170), ("ENGE", "Engenheiro eletricista", "MOI", 3, 170),
            ("ENGM", "Engenheiro mecânico", "MOI", 3, 170), ("PROJ", "Projetista", "MOI", 6, 95), ("SUPR", "Comprador técnico", "MOI", 2, 110),
            ("MOI", "Gestão / planejamento / qualidade", "MOI", 5, 150), ("HSE", "Técnico de segurança", "MOI", 2, 80),
            ("PEDR", "Pedreiro", "MOD", 24, 48), ("ARM", "Armador", "MOD", 16, 50), ("CARP", "Carpinteiro", "MOD", 12, 50), ("AJUD", "Ajudante", "MOD", 36, 32),
            ("MONT", "Montador mecânico", "MOD", 20, 62), ("SOLD", "Soldador qualificado", "MOD", 16, 78), ("MEC", "Mecânico de manutenção", "MOD", 8, 70),
            ("ELET", "Eletricista industrial", "MOD", 22, 64), ("INST", "Instrumentista", "MOD", 10, 72), ("ISOL", "Isolador térmico", "MOD", 8, 55),
            ("OPER", "Operador de máquina/planta", "MOD", 8, 58), ("TOPO", "Topógrafo", "MOD", 1, 90),
            ("GUIN", "Guindaste 80 t", "EQUIPAMENTO", 3, 420), ("RETRO", "Retroescavadeira", "EQUIPAMENTO", 2, 160), ("CAMB", "Caminhão basculante", "EQUIPAMENTO", 4, 120)]
# enredo: fator de avanço real sobre o previsto; None = segue o plano
ENREDO_AVANCO = {E(k): v for k, v in {"2.1": 1.12, "2.2": 1.15, "2.3": 1.10, "2.4": 0.9, "2.6": 0.9, "3.2": 0.5, "3.6": 0.7, "3.12": 0.7, "4.1": 0.8, "4.2": 0.75, "8.4": 0.6, "3.1": 0.85, "3.3": 0.9}.items()}
SEM_MEDICAO_DESDE = {E("4.2"): date(2026, 8, 22)}                      # medição desatualizada (apontamentos continuam)
ENREDO_HORAS = {E(k): v for k, v in {"4.1": 1.35, "4.2": 1.1, "2.1": 0.9, "2.2": 0.85, "8.4": 1.0}.items()}

RESULT: list[dict] = []
NUM: dict[str, object] = {}
TOKEN = ""


def chamar(metodo: str, caminho: str, corpo: object | None = None, bruto: bool = False, token: str | None = None) -> tuple[int, object]:
    headers = {"Content-Type": "application/json"}
    tk = TOKEN if token is None else token
    if tk:
        headers["Authorization"] = f"Bearer {tk}"
    dados = json.dumps(corpo, default=str).encode() if corpo is not None else None
    req = urllib.request.Request(BASE + caminho, data=dados, method=metodo, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            b = r.read()
            return r.status, (b if bruto else (json.loads(b) if b else {}))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, (b if bruto else json.loads(b))
        except json.JSONDecodeError:
            return e.code, {"detail": b.decode(errors="replace")[:200]}


def passo(n: int, nome: str, ok: bool, detalhe: str = "") -> None:
    RESULT.append({"n": n, "nome": nome, "ok": ok, "detalhe": detalhe})
    print(f"{'PASS' if ok else 'FAIL'}  {n:>2} {nome}{' — ' + detalhe if detalhe else ''}", flush=True)


def d(v: object) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def parse_pred(s: str) -> list[dict]:
    out = []
    for tok in filter(None, s.split(",")):
        m = re.fullmatch(r"(\d+(?:\.\d+)*)(TI|II|TT|IT)?(?:\+(\d+))?", tok.strip())
        assert m, tok
        v: dict = {"pred_edt": m.group(1)}
        if m.group(2):
            v["tipo"] = m.group(2)
        if m.group(3):
            v["lag"] = int(m.group(3))
        out.append(v)
    return out


def parse_rec(s: str) -> list[tuple[str, int]]:
    return [(a.split()[0], int(a.split()[1])) for a in s.split(",") if a.strip()]


# ------------------------------------------------------------------ passo 0
def limpar() -> None:
    import psycopg
    with psycopg.connect(DSN, autocommit=True) as con, con.cursor() as cur:
        cur.execute("SET search_path = plan, public")
        cur.execute("SELECT id, codigo FROM contrato")
        contratos = cur.fetchall()
        if not contratos:
            print("limpeza: banco sem contratos, nada a remover")
            return
        try:
            cur.execute("SET session_replication_role = replica")  # desliga os triggers append-only (superusuário)
        except psycopg.errors.InsufficientPrivilege:
            raise SystemExit("limpeza exige superusuário (triggers append-only). Em produção, remova os contratos com o dono do cluster ou use --sem-limpar.") from None
        for cid, codigo in contratos:
            for sql in [
                "DELETE FROM rdo_evento WHERE contrato_id = %s", "DELETE FROM apontamento WHERE contrato_id = %s", "DELETE FROM custo_realizado WHERE contrato_id = %s",
                "DELETE FROM custo_item WHERE atividade_id IN (SELECT id FROM atividade WHERE contrato_id = %s)",
                "DELETE FROM alocacao WHERE atividade_id IN (SELECT id FROM atividade WHERE contrato_id = %s)",
                "DELETE FROM recurso WHERE contrato_id = %s",
                "DELETE FROM cronograma_calculo WHERE contrato_id = %s", "DELETE FROM medicao WHERE contrato_id = %s",
                "DELETE FROM baseline_atividade WHERE baseline_id IN (SELECT id FROM baseline WHERE contrato_id = %s)",
                "DELETE FROM previsto_semanal WHERE baseline_id IN (SELECT id FROM baseline WHERE contrato_id = %s)",
                "DELETE FROM baseline WHERE contrato_id = %s",
                "DELETE FROM lvp_item WHERE lvp_id IN (SELECT id FROM lvp_avaliacao WHERE contrato_id = %s)",
                "DELETE FROM lvp_avaliacao WHERE contrato_id = %s",
                "DELETE FROM vinculo WHERE predecessora_id IN (SELECT id FROM atividade WHERE contrato_id = %s)",
                "DELETE FROM atividade WHERE contrato_id = %s", "DELETE FROM excecao_contratual WHERE contrato_id = %s",
                "DELETE FROM importacao WHERE contrato_id = %s",
                "DELETE FROM documento_evento WHERE documento_id IN (SELECT id FROM documento_engenharia WHERE contrato_id = %s)",
                "DELETE FROM documento_engenharia WHERE contrato_id = %s", "DELETE FROM plano_acao WHERE contrato_id = %s",
                "DELETE FROM periodo_fechamento WHERE contrato_id = %s", "DELETE FROM rdo WHERE contrato_id = %s",
                "DELETE FROM feriado WHERE contrato_id = %s",
                "DELETE FROM eap_no WHERE contrato_id = %s AND nivel = 3", "DELETE FROM eap_no WHERE contrato_id = %s AND nivel = 2",
                "DELETE FROM eap_no WHERE contrato_id = %s", "DELETE FROM contrato WHERE id = %s",
            ]:
                cur.execute(sql, (cid,))
            print(f"      removido {codigo}")
        cur.execute("SET session_replication_role = origin")
    st, lista = chamar("GET", "/contratos")
    passo(0, "limpar todos os contratos (usuários preservados)", st == 200 and lista == [], f"{len(contratos)} removidos")


# ------------------------------------------------------------------ passos 1-3
def estrutura_payload() -> dict:
    nos, vinc = [], []
    sucessores: dict[str, int] = {}
    for l in EAP:
        if len(l) == 2:
            nos.append({"edt": l[0], "descricao": l[1], "tipo": "RESUMO", "pontos": "0", "memoria_peso": "", "duracao": 0, "criterio_medicao": None,
                        "disciplina": "", "responsavel": "", "fase": FASE_ENUM.get(fase_de(l[0])) if l[0] != "1" else None, "excecao": None})
            continue
        edt, desc, dur, pred, rec, itens, peso = l
        if any(x[0].startswith(edt + ".") for x in EAP):          # tem filhos: resumo (níveis 4 e 5 onde a lógica pede)
            nos.append({"edt": edt, "descricao": desc, "tipo": "RESUMO", "pontos": "0", "memoria_peso": "", "duracao": 0, "criterio_medicao": None,
                        "disciplina": "", "responsavel": "", "fase": None, "excecao": None})
            continue
        tipo = "MARCO" if dur == 0 else "ATIVIDADE"
        nos.append({"edt": edt, "descricao": desc, "tipo": tipo, "pontos": str(peso), "memoria_peso": f"{peso} pontos · fase {fase_de(edt)}" if peso else "",
                    "duracao": dur, "criterio_medicao": None, "disciplina": {"2": "Engenharia", "3": "Suprimentos", "4": "Civil", "5": "Mecânica", "6": "Elétrica", "7": "Comissionamento", "8": "Gestão"}.get(fase_de(edt), ""),
                    "responsavel": rec.split(",")[0].split()[0] if rec else "", "fase": None,
                    "excecao": None if dur <= 15 else {"documento": f"CE-{edt.replace('.', '')}", "autorizada_por": "Gerente do contrato",
                                                        "justificativa": f"{desc}: pacote indivisível de {dur} dias úteis (fornecimento/fabricação ou frente contínua); acompanhamento por medição semanal (INV-CRN-001)."}})
        for v in parse_pred(pred):
            vinc.append({**v, "suc_edt": edt})
            sucessores[v["pred_edt"]] = sucessores.get(v["pred_edt"], 0) + 1
    # o marco de fim recebe toda frente sem sucessora (senão o CPM tem vários fins)
    resumos = {l[0] for l in EAP if any(x[0].startswith(l[0] + ".") for x in EAP)}
    folhas = [l[0] for l in EAP if len(l) > 2 and l[0] != E("1.6") and l[0] not in resumos]
    for edt in folhas:
        if sucessores.get(edt, 0) == 0:
            vinc.append({"pred_edt": edt, "suc_edt": E("1.6")})
    return {"nos": nos, "vinculos": vinc, "feriados": [{"data": f, "descricao": "feriado"} for f in FERIADOS], "gravado_por": "demo_v2"}


def criar_projeto() -> tuple[str, dict]:
    base = sum(l[6] for l in EAP if len(l) > 2)
    st, c = chamar("POST", "/contratos", {**CONTRATO, "base_ponderacao": str(base)})
    if st == 422 and "existe" in str(c).lower():
        st2, lista = chamar("GET", "/contratos")
        c = next(x for x in lista if x["codigo"] == CONTRATO["codigo"])
        st = 201
    passo(1, "criar contrato CT-200-COGER (13/07/2026 → 30/11/2027)", st == 201, str(c.get("id", c))[:60])
    cid = c["id"]
    est = estrutura_payload()
    NUM["n_nos"] = len(est["nos"]); NUM["n_vinculos"] = len(est["vinculos"]); NUM["base"] = base
    st, r = chamar("POST", f"/contratos/{cid}/estrutura/validar", est)
    fim = r.get("fim", {}).get("data") if st == 200 else None
    passo(2, f"validar estrutura: {len(est['nos'])} nós, {len(est['vinculos'])} vínculos", st == 200 and not [a for a in r.get("achados", []) if a.get("severidade") == "BLOQUEANTE"],
          f"término CPM {fim} · achados {len(r.get('achados', []))} · {str(r.get('detail', ''))[:80]}")
    NUM["fim_cpm"] = fim
    ciclo = dict(est, vinculos=est["vinculos"] + [{"pred_edt": E("5.2"), "suc_edt": E("3.1")}])
    st, r = chamar("PUT", f"/contratos/{cid}/estrutura", ciclo)
    passo(3, "vínculo circular é recusado (INV-CRN-006)", st == 422, str(r.get("detail", ""))[:80])
    st, r = chamar("PUT", f"/contratos/{cid}/estrutura", est)
    passo(4, "gravar estrutura (INV-EAP-001 fechada)", st == 200 and len(r.get("atividades", [])) == 87, f"{len(r.get('atividades', []))} atividades · fim {r.get('fim', {}).get('data')}")
    if st != 200:
        sys.exit(f"estrutura recusada: {str(r)[:400]}")
    st, b = chamar("POST", f"/contratos/{cid}/baselines", {"emissao": "1ª emissão — BL0", "nota_lvp": "0.93", "avaliado_por": "Fiscal do contrato", "aprovada_por": "Gerente do contrato"})
    passo(5, "aprovar BL0 (LVP 0,93)", st == 201 and b.get("numero") == 0, f"término BL0 {b.get('termino')} · {b.get('semanas')} semanas · {str(b.get('detail', ''))[:200]}")
    st, crn = chamar("GET", f"/contratos/{cid}/cronograma")
    return cid, crn


# ------------------------------------------------------------------ passos 6-7
def recursos_e_custos(cid: str, crn: dict) -> dict[str, str]:
    ids: dict[str, str] = {}
    for sig, nome, tipo, cap, custo in RECURSOS:
        st, r = chamar("POST", f"/contratos/{cid}/recursos", {"codigo": sig, "nome": nome, "tipo": tipo, "unidade": "pessoa" if tipo != "EQUIPAMENTO" else "máquina",
                                                              "capacidade_dia": str(cap), "custo_hora": str(custo), "horas_dia": "8", "ativo": True})
        if st == 201:
            ids[sig] = r["id"]
    por = {a["edt"]: a for a in crn["atividades"]}
    n_aloc = 0
    for l in EAP:
        if len(l) == 2 or l[2] == 0 or not l[4]:
            continue
        aloc = [{"recurso_id": ids[s], "quantidade_dia": str(q), "observacao": ""} for s, q in parse_rec(l[4]) if s in ids]
        st, r = chamar("PUT", f"/atividades/{por[l[0]]['id']}/alocacoes", {"alocacoes": aloc})
        n_aloc += len(r) if st == 200 else 0
    st, p = chamar("GET", f"/contratos/{cid}/recursos")
    NUM["recursos"] = len(ids); NUM["alocacoes"] = n_aloc; NUM["superalocacoes"] = len(p.get("achados", [])) if st == 200 else -1
    NUM["horas_recursos"] = p.get("horas_total"); NUM["custo_recursos"] = p.get("custo_total")
    passo(6, f"catálogo de {len(ids)} recursos e {n_aloc} alocações", len(ids) == len(RECURSOS) and n_aloc > 100,
          f"{fnum(NUM['horas_recursos'], 0)} hh · {fbrl(NUM['custo_recursos'])} · superalocações {NUM['superalocacoes']}")
    n_itens = 0
    for l in EAP:
        if len(l) == 2 or l[2] == 0 or not l[5]:
            continue
        edt, desc, _, _, _, itens, _ = l
        fase = fase_de(edt)
        cat = "MATERIAL" if edt in MATERIAL_3 else CATEGORIA_FASE[fase]
        lista = [{"categoria": cat, "descricao": f"{desc} — fornecimento/serviço principal", "fornecedor": "a contratar" if fase != "3" else "fornecedor selecionado",
                  "quantidade": "1", "unidade": "vb", "custo_unitario": str(itens * 1000 * (0.85 if fase in "3456" else 1.0)), "observacao": ""}]
        if fase in "3456":
            lista.append({"categoria": "LOGISTICA" if fase == "3" else "MATERIAL", "descricao": "frete, seguro e consumíveis", "fornecedor": "", "quantidade": "1", "unidade": "vb",
                          "custo_unitario": str(itens * 1000 * 0.15), "observacao": ""})
        st, r = chamar("PUT", f"/atividades/{por[edt]['id']}/custos", {"itens": lista})
        n_itens += len(r) if st == 200 else 0
    st, r = chamar("PUT", f"/atividades/{por[E('1.4')]['id']}/custos", {"itens": [{"categoria": "OUTRO", "descricao": "x", "custo_unitario": "1"}]})
    passo(7, f"orçamento: {n_itens} itens; custo em marco recusado (INV-CST-001)", n_itens > 70 and st == 422)
    return ids


# ------------------------------------------------------------------ passo 8: medições
def dias_uteis(a: date, b: date, feriados: set[date]) -> int:
    n, x = 0, a
    while x <= b:
        if x.weekday() < 5 and x not in feriados:
            n += 1
        x += timedelta(days=1)
    return n


def medicoes(cid: str, crn: dict) -> None:
    feriados = {date.fromisoformat(f) for f in FERIADOS}
    sabados = []
    s = INICIO + timedelta(days=(5 - INICIO.weekday()) % 7)
    while s <= DATA_STATUS:
        sabados.append(s); s += timedelta(days=7)
    NUM["semanas_medidas"] = len(sabados)
    ultimo: dict[str, Decimal] = {}
    n = 0
    for sab in sabados:
        for a in crn["atividades"]:
            if a["marco"]:
                continue
            ini, fim = date.fromisoformat(a["datas"]["inicio"]), date.fromisoformat(a["datas"]["termino"])
            if ini > sab:
                continue
            total = max(dias_uteis(ini, fim, feriados), 1)
            feito = dias_uteis(ini, min(sab, fim), feriados)
            prev = Decimal(feito) / Decimal(total)
            fator = Decimal(str(ENREDO_AVANCO.get(a["edt"], 1.0)))
            real = min(Decimal(1), (prev * fator).quantize(Decimal("0.01")))
            if a["edt"] in SEM_MEDICAO_DESDE and sab > SEM_MEDICAO_DESDE[a["edt"]]:
                continue
            if real <= ultimo.get(a["edt"], Decimal(-1)):
                continue
            st, r = chamar("POST", "/medicoes", {"eap_no_id": a["eap_no_id"], "data_corte": sab.isoformat(), "percentual_ac": str(real),
                                                 "evidencia": f"RDO semana {sab.strftime('%d/%m')} · boletim de medição", "criado_por": "Fiscal do contrato"})
            if st == 201:
                ultimo[a["edt"]] = real; n += 1
        # marcos cumpridos: 1.1 na 1ª semana, 1.3 quando 2.1-2.3 a 100%
        for m, cond in ((E("1.1"), sab >= INICIO), (E("1.3"), all(ultimo.get(E(e), 0) >= 1 for e in ("2.1", "2.2", "2.3")))):
            if cond and ultimo.get(m, Decimal(0)) < 1:
                no = next(x for x in crn["atividades"] if x["edt"] == m)
                st, _ = chamar("POST", "/medicoes", {"eap_no_id": no["eap_no_id"], "data_corte": sab.isoformat(), "percentual_ac": "1", "evidencia": "marco cumprido", "criado_por": "Fiscal do contrato"})
                if st == 201:
                    ultimo[m] = Decimal(1); n += 1
    no21 = next(x for x in crn["atividades"] if x["edt"] == E("2.1"))
    st, r = chamar("POST", "/medicoes", {"eap_no_id": no21["eap_no_id"], "data_corte": DATA_STATUS.isoformat(), "percentual_ac": "0.5", "evidencia": "x", "criado_por": "teste"})
    NUM["medicoes"] = n
    passo(8, f"{n} medições em {len(sabados)} cortes semanais; retrocesso recusado (INV-MED-003)", n > 50 and st == 422, f"até {DATA_STATUS:%d/%m/%Y}")


# ------------------------------------------------------------------ passo 9: replanejamento + BL1
def replanejar(cid: str, crn: dict) -> dict:
    a32 = next(x for x in crn["atividades"] if x["edt"] == E("3.2"))
    st, r = chamar("PUT", f"/atividades/{a32['id']}", {"alteracao": {"duracao": 145}, "alterado_por": "Planejador — aviso formal do fornecedor da caldeira"})
    fim_novo = r.get("fim", {}).get("data") if st == 200 else None
    st2, b1 = chamar("POST", f"/contratos/{cid}/baselines", {"emissao": "2ª emissão — BL1 (atraso da caldeira)", "nota_lvp": "0.90", "avaliado_por": "Fiscal do contrato", "aprovada_por": "Gerente do contrato"})
    import time
    time.sleep(1.5)
    st3, crn2 = chamar("GET", f"/contratos/{cid}/cronograma?data_status={DATA_STATUS}")
    if crn2.get("baseline_numero") != 1:
        time.sleep(3)
        st3, crn2 = chamar("GET", f"/contratos/{cid}/cronograma")
    NUM["fim_vigente"] = crn2.get("fim", {}).get("data"); NUM["fim_bl0"] = crn.get("fim_baseline", {}).get("data") if crn.get("fim_baseline") else None
    NUM["fim_bl0"] = NUM["fim_bl0"] or NUM.get("fim_cpm")
    passo(9, "replanejar 3.2 (+25 du) e aprovar BL1", st == 200 and st2 == 201 and b1.get("numero") == 1 and crn2.get("baseline_numero") == 1,
          f"término BL0 {NUM['fim_bl0']} → vigente {fim_novo} · put {st} · bl {st2} nº {b1.get('numero')} · vigente nº {crn2.get('baseline_numero')} · {str(b1.get('detail', ''))[:120]}")
    return crn2


# ------------------------------------------------------------------ passo 10: apontamentos
def apontamentos(cid: str, crn: dict, rec_ids: dict[str, str]) -> None:
    feriados = {date.fromisoformat(f) for f in FERIADOS}
    por = {a["edt"]: a for a in crn["atividades"]}
    n = 0
    for l in EAP:
        if len(l) == 2 or l[2] == 0 or not l[4]:
            continue
        edt = l[0]
        a = por[edt]
        ini, fim = date.fromisoformat(a["datas"]["inicio"]), date.fromisoformat(a["datas"]["termino"])
        if ini > HOJE or fase_de(edt) not in ("2", "3", "4", "8"):
            continue
        fator = ENREDO_HORAS.get(edt, 1.0)
        x = ini
        dias = 0
        while x <= min(fim, HOJE) and dias < 60:
            if x.weekday() < 5 and x not in feriados:
                dias += 1
                for sig, q in parse_rec(l[4]):
                    if sig not in rec_ids:
                        continue
                    horas = round(q * 8 * fator * (0.9 + 0.2 * ((x.day * 7 + dias) % 3) / 2), 1)
                    horas = min(24.0, max(0.5, horas / max(q, 1)))    # horas por pessoa-dia (o recurso já carrega a quantidade)
                    st, _ = chamar("POST", f"/contratos/{cid}/apontamentos", {"atividade_id": a["id"], "data": x.isoformat(), "horas": str(horas), "recurso_id": rec_ids[sig], "descricao": f"{l[1]} — frente {sig}"})
                    if st == 201:
                        n += 1
            x += timedelta(days=1)
    # estorno + regras
    st, r = chamar("POST", f"/contratos/{cid}/apontamentos", {"atividade_id": por[E("4.1")]["id"], "data": (INICIO + timedelta(days=1)).isoformat(), "horas": "4", "descricao": "lançamento em duplicidade (será estornado)"})
    st2, e = chamar("POST", f"/apontamentos/{r.get('id')}/estorno", {"justificativa": "duplicidade identificada na conferência semanal"})
    st3, _ = chamar("POST", f"/apontamentos/{r.get('id')}/estorno", {"justificativa": "tentativa repetida"})
    st4, _ = chamar("POST", f"/contratos/{cid}/apontamentos", {"atividade_id": por[E("1.1")]["id"], "data": INICIO.isoformat(), "horas": "1"})
    st5, _ = chamar("POST", f"/contratos/{cid}/apontamentos", {"atividade_id": por[E("4.1")]["id"], "data": (HOJE + timedelta(days=3)).isoformat(), "horas": "1"})
    NUM["apontamentos"] = n + 2
    passo(10, f"{n} apontamentos de horas; estorno 201, repetido 409, marco 422, futuro 422", n > 100 and st2 == 201 and st3 == 409 and st4 in (409, 422) and st5 == 422)


# ------------------------------------------------------------------ passo 11: custo realizado
def custo_real(cid: str, crn: dict) -> None:
    por = {a["edt"]: a for a in crn["atividades"]}
    lanc = [
        ("3.2", "EQUIPAMENTO", date(2026, 8, 14), 11800 * 1000 * 0.30, "Caldeira — 1ª parcela (30% na assinatura do pedido)", "NF 10231"),
        ("3.4", "EQUIPAMENTO", date(2026, 8, 21), 9600 * 1000 * 0.20, "Turbina — sinal de 20%", "NF 20044"),
        ("3.6", "EQUIPAMENTO", date(2026, 9, 4), 2400 * 1000 * 0.25, "Transformadores — adiantamento", "NF 3310"),
        ("2.1", "SERVICO", date(2026, 8, 7), 180 * 1000 * 0.9, "Engenharia básica de processo — medição 1", "BM 01"),
        ("2.2", "SERVICO", date(2026, 8, 7), 90 * 1000, "Engenharia básica civil — medição única", "BM 01"),
        ("2.3", "SERVICO", date(2026, 8, 7), 90 * 1000, "Engenharia básica elétrica — medição única", "BM 01"),
        ("4.1", "SUBCONTRATO", date(2026, 8, 28), 120 * 1000 * 1.30, "Mobilização — medição com aditivo de horas", "BM 02"),
        ("4.1", "MAO_DE_OBRA", date(2026, 8, 28), 48 * 1000, "MOD mobilização — folha agosto", "FOLHA 08/26"),
        ("4.2", "SUBCONTRATO", date(2026, 9, 11), 380 * 1000 * 0.6, "Terraplenagem — 1ª medição", "BM 03"),
        ("8.4", "OUTRO", date(2026, 7, 31), 180 * 1000 * 0.5, "Taxa de licenciamento ambiental", "GRU 7781"),
        ("8.1", "OUTRO", date(2026, 7, 24), 40 * 1000, "Plano de execução — consultoria", "NF 88"),
        ("4.1", "SUBCONTRATO", date(2026, 9, 4), -6000, "Correção: glosa de horas não comprovadas", "BM 02-R"),
    ]
    n = 0
    for edt, cat, dt, valor, desc, doc in lanc:
        st, _ = chamar("POST", f"/contratos/{cid}/custos-realizados", {"atividade_id": por[E(edt)]["id"], "categoria": cat, "data": dt.isoformat(), "valor": str(round(valor, 2)), "descricao": desc, "documento": doc})
        n += st == 201
    st, p = chamar("GET", f"/contratos/{cid}/custos", None) if False else chamar("GET", f"/contratos/{cid}/custos?data_corte={DATA_STATUS}")
    e = p.get("evm", {}) if st == 200 else {}
    NUM["evm"] = e
    passo(11, f"{n} lançamentos de custo real (com correção negativa)", n == len(lanc) and st == 200, f"ONT {fbrl(e.get('bac'))} · VA {fbrl(e.get('va'))} · CR {fbrl(e.get('cr'))} · IDC {fnum(e.get('idc'))}")


# ------------------------------------------------------------------ passos 12-15: leituras, exportações, segurança
def leituras(cid: str) -> None:
    st, ck = chamar("GET", f"/contratos/{cid}/cockpit?data_corte={DATA_STATUS}&referencia=BL0")
    c = ck.get("cartoes", {}) if st == 200 else {}
    NUM["cockpit"] = c
    passo(12, "cockpit na data de status", st == 200 and "real_ac" in c, f"previsto {fpct(c.get('previsto_ac'))} · real {fpct(c.get('real_ac'))} · IDP {fnum(c.get('idp'))} · {c.get('situacao')} · projetado {fdata(c.get('termino_projetado'))}")
    st, pr = chamar("GET", f"/contratos/{cid}/previsto-realizado?data_corte={DATA_STATUS}")
    NUM["pr"] = {"indice": pr.get("indice_coerencia"), "achados": pr.get("achados", []), "dimensoes": pr.get("dimensoes", [])} if st == 200 else {}
    regras = sorted({a["regra"] for a in pr.get("achados", [])})
    passo(13, "previsto × realizado e análise de coerência", st == 200 and len(pr.get("dimensoes", [])) == 4, f"índice {pr.get('indice_coerencia')} · {len(pr.get('achados', []))} achados · {', '.join(regras)[:160]}")
    st, sr = chamar("GET", f"/contratos/{cid}/status-report?data_corte={DATA_STATUS}")
    passo(14, "status report consolidado", st == 200 and "coerencia" in sr, f"tarefas {sr.get('tarefas')}")
    st, ap = chamar("GET", f"/contratos/{cid}/apontamentos?semana={DATA_STATUS}")
    NUM["painel_apt"] = {"total_previsto": ap.get("total_previsto"), "total_apontado": ap.get("total_apontado"), "situacoes": sorted({r["situacao"] for r in ap.get("resumo", [])})} if st == 200 else {}
    passo(15, "painel semanal de apontamentos", st == 200 and len(ap.get("dias", [])) == 7, f"{fnum(ap.get('total_apontado'), 0)} h apontadas de {fnum(ap.get('total_previsto'), 0)} hh previstas")


def exportacoes(cid: str) -> None:
    ok, det = 0, []
    saida = RAIZ / "demo_v2_exportacoes"
    try:
        saida.mkdir(exist_ok=True)
    except OSError:  # produção: raiz somente leitura → exporta para o diretório temporário
        import tempfile
        saida = Path(tempfile.gettempdir()) / "demo_v2_exportacoes"; saida.mkdir(exist_ok=True)
    for tipo in ("status-report", "cronograma", "medicoes", "eap", "recursos", "custos", "apontamentos", "previsto-realizado"):
        for fmt, magic in (("pdf", b"%PDF"), ("xlsx", b"PK")):
            st, b = chamar("GET", f"/contratos/{cid}/exportar/{tipo}?data_corte={DATA_STATUS}&formato={fmt}", bruto=True)
            bom = st == 200 and isinstance(b, bytes) and b[:len(magic)] == magic
            ok += bom
            if bom:
                (saida / f"{tipo}.{fmt}").write_bytes(b)
            det.append(f"{tipo}.{fmt}:{'ok' if bom else st}")
    NUM["exportacoes"] = ok
    passo(16, "16 exportações PDF/XLSX válidas", ok == 16, f"salvas em demo_v2_exportacoes/ · {' '.join(x for x in det if not x.endswith(':ok'))}")


def seguranca(cid: str) -> None:
    st, _ = chamar("GET", f"/contratos/{cid}/cronograma", token="")
    st2, _ = chamar("POST", f"/contratos/{cid}/apontamentos", {"atividade_id": "00000000-0000-0000-0000-000000000000", "data": HOJE.isoformat(), "horas": "1"}, token="token-invalido")
    passo(17, "sem token → 401; token inválido → 401", st == 401 and st2 == 401)


# ------------------------------------------------------------------ panorama
def fpct(v: object, casas: int = 1) -> str:
    return "—" if v in (None, "") else f"{float(str(v)) * 100:.{casas}f}%".replace(".", ",")


def fnum(v: object, casas: int = 2) -> str:
    return "—" if v in (None, "") else f"{float(str(v)):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fbrl(v: object) -> str:
    return "—" if v in (None, "") else "R$ " + fnum(v, 0)


def fdata(v: object) -> str:
    return "—" if not v else date.fromisoformat(str(v)).strftime("%d/%m/%Y")


def panorama(cid: str) -> None:
    p = RAIZ / "panorama_demo.html"
    if not p.exists():  # produção: o HTML do panorama não é enviado ao servidor
        ok_n = sum(1 for r in RESULT if r["ok"])
        print(f"panorama_demo.html ausente — seed concluído ({ok_n}/{len(RESULT)} passos PASS)")
        return
    t = p.read_text(encoding="utf-8")
    c = NUM.get("cockpit", {}); e = NUM.get("evm", {}); pr = NUM.get("pr", {})
    ok_n = sum(1 for r in RESULT if r["ok"])
    linhas = "".join(f'<tr><td class="num">{r["n"]}</td><td>{r["nome"]}</td><td class="{"ok" if r["ok"] else "ac"}">{"PASS" if r["ok"] else "FAIL"}</td><td class="mono" style="font-size:11px">{r["detalhe"]}</td></tr>' for r in RESULT)
    def fdim(v: object, un: str) -> str:
        return fpct(v) if un == "fração" else fbrl(v) if un == "R$" else f"{fnum(v, 0)} {un}"
    dims = "".join(f'<tr><td>{x["nome"]}</td><td class="num">{fdim(x["previsto"], x["unidade"])}</td><td class="num">{fdim(x["realizado"], x["unidade"])}</td><td class="num">{fnum(x["indice"]) if x["indice"] is not None else "—"}</td><td>{x["leitura"]}</td></tr>' for x in pr.get("dimensoes", []))
    achs = "".join(f'<tr><td class="mono">{a["regra"]}</td><td class="{"ac" if a["severidade"]=="ALERTA" else "at" if a["severidade"]=="ATENCAO" else ""}">{a["severidade"].lower()}</td><td class="num">{a["referencia"]}</td><td>{a["detalhe"]}</td></tr>' for a in pr.get("achados", [])[:25])
    sit = c.get("situacao", "")
    bloco = f'''
  <h2 id="resultado">Resultado da execução — {HOJE:%d/%m/%Y}</h2>
  <p class="lead">Semeado pela API em {HOJE:%d/%m/%Y}. Contrato <code>{cid}</code>. Data de status do roteiro: <b>{DATA_STATUS:%d/%m/%Y}</b> (último sábado). {ok_n} de {len(RESULT)} passos PASS.</p>
  <div class="cartoes">
    <div class="cartao"><div class="rot">Avanço físico</div><div class="val" style="font-size:22px">{fpct(c.get("real_ac"))}</div><div class="nota">previsto {fpct(c.get("previsto_ac"))} · desvio {fpct(c.get("desvio_abs"))}</div></div>
    <div class="cartao"><div class="rot">IDP · situação</div><div class="val {"ok" if sit=="EM_DIA" else "at" if sit=="ATENCAO" else "ac"}" style="font-size:22px">{fnum(c.get("idp"))}</div><div class="nota">{sit.lower().replace("_", " ")}</div></div>
    <div class="cartao"><div class="rot">Término</div><div class="val" style="font-size:16px">BL0 {fdata(NUM.get("fim_bl0"))}</div><div class="nota">vigente {fdata(NUM.get("fim_vigente"))} · projetado {fdata(c.get("termino_projetado"))}</div></div>
    <div class="cartao"><div class="rot">EVM</div><div class="val" style="font-size:16px">IDC {fnum(e.get("idc"))}</div><div class="nota">ONT {fbrl(e.get("bac"))} · VA {fbrl(e.get("va"))} · CR {fbrl(e.get("cr"))}</div></div>
    <div class="cartao"><div class="rot">Coerência</div><div class="val" style="font-size:22px">{fnum(pr.get("indice"))}</div><div class="nota">{len(pr.get("achados", []))} achados</div></div>
    <div class="cartao"><div class="rot">Volume</div><div class="val" style="font-size:16px">{NUM.get("medicoes")} medições</div><div class="nota">{NUM.get("apontamentos")} apontamentos · {NUM.get("alocacoes")} alocações · {NUM.get("recursos")} recursos · {NUM.get("exportacoes")}/16 exportações</div></div>
  </div>
  <h3>Passos do roteiro</h3>
  <table><thead><tr><th style="width:36px">#</th><th>Passo</th><th style="width:60px">Estado</th><th>Detalhe</th></tr></thead><tbody>{linhas}</tbody></table>
  <div class="grid2" style="margin-top:14px">
    <div><h3>Dimensões previsto × realizado</h3><table><thead><tr><th>Dimensão</th><th class="num">Previsto</th><th class="num">Realizado</th><th class="num">Índice</th><th>Leitura</th></tr></thead><tbody>{dims}</tbody></table></div>
    <div><h3>Achados de coerência (RGR-COE-001)</h3><table><thead><tr><th>Regra</th><th>Sev.</th><th>EDT</th><th>Diagnóstico</th></tr></thead><tbody>{achs or '<tr><td colspan="4">nenhum</td></tr>'}</tbody></table></div>
  </div>
'''
    st, crn = chamar("GET", f"/contratos/{cid}/cronograma?data_status={DATA_STATUS}")
    if st == 200:
        linhas_m = []
        for a in crn["atividades"]:
            if not a["marco"]:
                continue
            bl = (a.get("baseline") or {}).get("inicio")
            real = "cumprido" if d(a.get("progresso")) >= 1 else "—"
            vig = a["datas"]["inicio"]
            estado = "cumprido" if real == "cumprido" else ("vencido" if vig < DATA_STATUS.isoformat() else ("desliza vs BL" if bl and vig > bl else "no plano"))
            cls = "ok" if estado == "cumprido" else "ac" if estado in ("vencido", "desliza vs BL") else ""
            linhas_m.append(f'<tr><td>{a["edt"]} {a["descricao"]}</td><td class="num">{fdata(bl)}</td><td class="num">{fdata(vig)}</td><td class="num">{real}</td><td class="{cls}">{estado}</td></tr>')
        t = re.sub(r'(<table id="marcos">.*?<tbody>).*?(</tbody>)', lambda m: m.group(1) + "".join(linhas_m) + m.group(2), t, flags=re.S)
    t = re.sub(r'\n  <h2 id="resultado">.*?(?=\n  <h2>1\. O que é</h2>)', "", t, flags=re.S)
    t = t.replace("\n  <h2>1. O que é</h2>", bloco + "\n  <h2>1. O que é</h2>", 1)
    t = t.replace("plano para aprovação · revisão 2 · gerado em 19/09/2026", f"executado em {HOJE:%d/%m/%Y} · início do projeto 13/07/2026")
    t = t.replace("28/09/2026 → 28/01/2028", f"13/07/2026 → {TERMINO_CONTRATUAL:%d/%m/%Y}")
    t = t.replace("≈ 335 dias úteis · 14 feriados cadastrados", f"término CPM {NUM.get('fim_cpm')} · 14 feriados")
    t = t.replace('<div class="rot">Data de status do roteiro</div><div class="val" style="font-size:17px">20/03/2027</div><div class="nota">≈ 36% do prazo decorrido · 25 semanas medidas (ver seção 6)</div>',
                  f'<div class="rot">Data de status</div><div class="val" style="font-size:17px">{DATA_STATUS:%d/%m/%Y}</div><div class="nota">{NUM.get("semanas_medidas")} semanas medidas desde 13/07/2026</div>')
    try:
        p.write_text(t, encoding="utf-8")
        (RAIZ / "demo_v2_resultado.json").write_text(json.dumps({"contrato_id": cid, "passos": RESULT, "numeros": NUM}, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    except OSError as e:  # produção: /opt é somente leitura para o serviço; o banco já está semeado
        print(f"aviso: panorama não gravado ({e}); o seed no banco está completo")
    print(f"\npanorama_demo.html atualizado · {ok_n}/{len(RESULT)} PASS")


def main() -> None:
    global TOKEN
    st, s = chamar("POST", "/auth/login", {"email": EMAIL, "senha": SENHA})
    if st != 200:
        sys.exit(f"login falhou: {s}")
    TOKEN = s["token"]
    if not s.get("organizacao_ativa"):
        # Banco novo (produção): o admin master cria a organização da demo e a ativa na sessão.
        _, orgs = chamar("GET", "/organizacoes")
        if not orgs:
            st, org = chamar("POST", "/organizacoes", {"codigo": "NOVOPLAN", "razao_social": "Inovação Controle Novo Plan"})
            if st != 201:
                sys.exit(f"não consegui criar organização: {org}")
            orgs = [org]
        st, s = chamar("POST", "/auth/organizacao", {"organizacao_id": orgs[0]["id"]})
        if st != 200:
            sys.exit(f"não consegui ativar organização: {s}")
        print(f"organização ativa: {orgs[0]['codigo']}")
    so_roteiro = "--so-roteiro" in sys.argv
    if so_roteiro:
        _, lista = chamar("GET", "/contratos")
        cid = next(x["id"] for x in lista if x["codigo"] == CONTRATO["codigo"])
    else:
        if "--sem-limpar" not in sys.argv:
            limpar()
        cid, crn = criar_projeto()
        rec = recursos_e_custos(cid, crn)
        medicoes(cid, crn)
        crn2 = replanejar(cid, crn)
        apontamentos(cid, crn2, rec)
        custo_real(cid, crn2)
    leituras(cid)
    exportacoes(cid)
    seguranca(cid)
    panorama(cid)
    sys.exit(0 if all(r["ok"] for r in RESULT) else 1)


if __name__ == "__main__":
    main()
