"""Semeia o contrato de demonstração (Área 100 — Etanol de Milho) num banco já migrado.

    GPS_DSN_SEED=postgresql://gps:gps@localhost:5433/gplansis python scripts/semear_demo.py [--reset]

Roda como superusuário (dev). Cria organização, contrato, EAP ponderada em
1.000.000, atividades, rede, feriados, LVP + baseline BL0 aprovada com previsto
semanal linear (RGR-CRV-002) e um histórico de medições até 21/03/2026.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.dominio import cronograma as cr  # noqa: E402
from app.dominio.tipos import sabados  # noqa: E402

DSN = os.environ.get("GPS_DSN_SEED", "postgresql://gps:gps@localhost:5433/gplansis")
ORG = ("DEMO", "Novo Plan — demonstração")
CONTRATO = {
    "codigo": "CT-100-ETANOL", "contratante": "Inovação Controle Novo Plan",
    "contratada": "Novo Plan Engenharia", "empreendimento": "ÁREA 100 — ETANOL DE MILHO",
    "modalidade": "EPC", "data_inicio": date(2026, 1, 5), "termino_contratual": date(2026, 12, 31),
}
FERIADOS = [date(2026, 2, 16), date(2026, 2, 17), date(2026, 4, 3), date(2026, 4, 21), date(2026, 5, 1)]
FASE = {"1.1": "MILESTONES", "1.2": "ENGENHARIA", "1.3": "SUPRIMENTOS", "1.4": "CONSTRUCAO_CIVIL",
        "1.5": "MONTAGEM_ELETROMECANICA", "1.6": "COMISSIONAMENTO"}

# edt, descrição, nível, folha, pontos, marco, dur, critério, memória, responsável, disciplina
EAP = [
    ("1", "ÁREA 100 — ETANOL DE MILHO", 1, False, 0, False, 0, None, "", "", ""),
    ("1.1", "MILESTONES", 2, False, 0, False, 0, None, "", "", ""),
    ("1.1.1", "Notificação de início", 3, True, 0, True, 0, None, "", "Gerenciamento", "Gerenciamento"),
    ("1.1.2", "Liberação das bases civis", 3, True, 0, True, 0, None, "", "Civil", "Civil"),
    ("1.1.3", "Aceitação final (ECA assinado)", 3, True, 0, True, 0, None, "", "Gerenciamento",
     "Gerenciamento"),
    ("1.2", "ENGENHARIA", 2, False, 0, False, 0, None, "", "", ""),
    ("1.2.1", "Engenharia básica", 3, True, 40000, False, 10, "DOCUMENTO",
     "400 hh a 100 pontos/hh", "Processo", "Processo"),
    ("1.2.2", "Engenharia de detalhamento", 3, True, 40000, False, 15, "DOCUMENTO",
     "400 hh a 100 pontos/hh", "Tubulação", "Tubulação"),
    ("1.3", "SUPRIMENTOS & MANUFATURA", 2, False, 0, False, 0, None, "", "", ""),
    ("1.3.1", "Aquisição de equipamentos", 3, True, 120000, False, 10, "SUPRIMENTOS",
     "custo do pacote sobre o total", "Suprimentos", "Suprimentos"),
    ("1.3.2", "Fabricação", 3, True, 260000, False, 20, "EQUIPAMENTO_MECANICO",
     "custo do pacote sobre o total", "Fornecedor", "Mecânica"),
    ("1.3.3", "Transporte e entrega", 3, True, 90000, False, 8, "SUPRIMENTOS",
     "custo do frete e seguro", "Logística", "Logística"),
    ("1.4", "CONSTRUÇÃO CIVIL", 2, False, 0, False, 0, None, "", "", ""),
    ("1.4.1", "Mobilização e terraplanagem", 3, True, 60000, False, 12, "CIVIL",
     "600 hh a 100 pontos/hh", "Civil", "Civil"),
    ("1.4.2", "Fundações e estruturas", 3, True, 90000, False, 15, "CIVIL",
     "900 hh a 100 pontos/hh", "Civil", "Civil"),
    ("1.5", "MONTAGEM ELETROMECÂNICA", 2, False, 0, False, 0, None, "", "", ""),
    ("1.5.1", "Montagem de equipamentos", 3, True, 110000, False, 15, "EQUIPAMENTO_MECANICO",
     "1100 hh a 100 pontos/hh", "Mecânica", "Mecânica"),
    ("1.5.2", "Tubulação e suportes", 3, True, 90000, False, 12, "TUBULACAO_MONTAGEM",
     "peso em TON x hh/TON", "Tubulação", "Tubulação"),
    ("1.5.3", "Elétrica e instrumentação", 3, True, 50000, False, 10, "ELETRICA_EQUIPAMENTO",
     "500 hh a 100 pontos/hh", "EIA", "Elétrica"),
    ("1.6", "COMISSIONAMENTO", 2, False, 0, False, 0, None, "", "", ""),
    ("1.6.1", "Testes a frio", 3, True, 20000, False, 6, "COMISSIONAMENTO",
     "200 hh a 100 pontos/hh", "Comissionamento", "Comissionamento"),
    ("1.6.2", "Testes a quente e ECA", 3, True, 30000, False, 8, "COMISSIONAMENTO",
     "300 hh a 100 pontos/hh", "Comissionamento", "Comissionamento"),
]
VINCULOS = [
    ("1.1.1", "1.2.1", "TI", 0), ("1.2.1", "1.2.2", "TI", 0), ("1.2.1", "1.3.1", "TI", 0),
    ("1.3.1", "1.3.2", "TI", 2), ("1.3.2", "1.3.3", "TI", 0), ("1.2.2", "1.4.1", "TI", 0),
    ("1.4.1", "1.4.2", "TI", 0), ("1.4.2", "1.1.2", "TI", 0), ("1.1.2", "1.5.1", "TI", 0),
    ("1.3.3", "1.5.1", "TI", 0), ("1.5.1", "1.5.2", "II", 5), ("1.5.1", "1.5.3", "TI", 0),
    ("1.5.2", "1.6.1", "TI", 0), ("1.5.3", "1.6.1", "TI", 0), ("1.6.1", "1.6.2", "TI", 0),
    ("1.6.2", "1.1.3", "TI", 0),
]
# a Fabricação (20 dias) excede 15: exceção contratual registrada, como no procedimento
EXCECAO = ("1.3.2", "Carta CT-100/2026-014", "Fiscal da CONTRATANTE",
           "prazo de fabricação do fornecedor é indivisível (equipamento monobloco)")
DESEMPENHO = {"1.2": Decimal("0.97"), "1.3": Decimal("0.85"), "1.4": Decimal("0.92"),
              "1.5": Decimal(0), "1.6": Decimal(0), "1.1": Decimal(1)}
DATA_STATUS = date(2026, 3, 21)


def resetar(cur: psycopg.Cursor, contrato_id: str) -> None:
    cur.execute("SET session_replication_role = replica")   # desliga triggers: dev, com flag
    for sql in [
        "DELETE FROM cronograma_calculo WHERE contrato_id = %s",
        "DELETE FROM medicao WHERE contrato_id = %s",
        "DELETE FROM baseline_atividade WHERE baseline_id IN (SELECT id FROM baseline WHERE contrato_id = %s)",
        "DELETE FROM previsto_semanal WHERE baseline_id IN (SELECT id FROM baseline WHERE contrato_id = %s)",
        "DELETE FROM baseline WHERE contrato_id = %s",
        "DELETE FROM lvp_item WHERE lvp_id IN (SELECT id FROM lvp_avaliacao WHERE contrato_id = %s)",
        "DELETE FROM lvp_avaliacao WHERE contrato_id = %s",
        "DELETE FROM vinculo WHERE predecessora_id IN (SELECT id FROM atividade WHERE contrato_id = %s)",
        "DELETE FROM atividade WHERE contrato_id = %s",
        "DELETE FROM excecao_contratual WHERE contrato_id = %s",
        "DELETE FROM importacao WHERE contrato_id = %s",
        "DELETE FROM documento_evento WHERE documento_id IN (SELECT id FROM documento_engenharia WHERE contrato_id = %s)",
        "DELETE FROM documento_engenharia WHERE contrato_id = %s",
        "DELETE FROM plano_acao WHERE contrato_id = %s",
        "DELETE FROM periodo_fechamento WHERE contrato_id = %s",
        "DELETE FROM rdo WHERE contrato_id = %s",
        "DELETE FROM feriado WHERE contrato_id = %s",
        "DELETE FROM eap_no WHERE contrato_id = %s AND nivel = 3",
        "DELETE FROM eap_no WHERE contrato_id = %s AND nivel = 2",
        "DELETE FROM eap_no WHERE contrato_id = %s",
        "DELETE FROM contrato WHERE id = %s",
    ]:
        cur.execute(sql, (contrato_id,))
    cur.execute("SET session_replication_role = DEFAULT")


def main() -> None:
    reset = "--reset" in sys.argv
    with psycopg.connect(DSN) as con, con.cursor() as cur:
        cur.execute("SET search_path = plan, public")
        cur.execute("INSERT INTO organizacao (codigo, razao_social) VALUES (%s, %s) "
                    "ON CONFLICT (codigo) DO UPDATE SET razao_social = EXCLUDED.razao_social "
                    "RETURNING id", ORG)
        org_id = cur.fetchone()[0]
        cur.execute("SELECT id FROM contrato WHERE codigo = %s", (CONTRATO["codigo"],))
        existente = cur.fetchone()
        if existente and not reset:
            print(f"contrato {CONTRATO['codigo']} já existe ({existente[0]}); use --reset para refazer")
            print(f"VITE_ORGANIZACAO_ID={org_id}\nVITE_CONTRATO_ID={existente[0]}")
            return
        if existente:
            resetar(cur, existente[0])
            con.commit()

        cur.execute("""
            INSERT INTO contrato (organizacao_id, codigo, contratante, contratada, empreendimento,
                                  modalidade, data_inicio, termino_contratual)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (org_id, CONTRATO["codigo"], CONTRATO["contratante"], CONTRATO["contratada"],
             CONTRATO["empreendimento"], CONTRATO["modalidade"], CONTRATO["data_inicio"],
             CONTRATO["termino_contratual"]))
        cid = cur.fetchone()[0]
        for d in FERIADOS:
            cur.execute("INSERT INTO feriado (contrato_id, data, descricao) VALUES (%s, %s, %s)",
                        (cid, d, "feriado nacional"))

        # EAP (a trigger diferida confere a soma no COMMIT)
        ids: dict[str, str] = {}
        for edt, desc, nivel, folha, pontos, marco, *_ , memoria, _r, _d in [
                (e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[8], e[9], e[10]) for e in EAP]:
            pai = ids.get(".".join(edt.split(".")[:-1])) if "." in edt else None
            tipo = "RESUMO" if not folha else ("MARCO" if marco else "ATIVIDADE")
            cur.execute("""
                INSERT INTO eap_no (contrato_id, parent_id, edt, descricao, nivel, tipo, fase, folha,
                                    pontos, memoria_peso)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (cid, pai, edt, desc, nivel, tipo, FASE.get(".".join(edt.split(".")[:2])) if nivel >= 2 else None,
                 folha, pontos, memoria))
            ids[edt] = cur.fetchone()[0]

        # exceção contratual da Fabricação
        cur.execute("""
            INSERT INTO excecao_contratual (contrato_id, invariante, justificativa, autorizada_por, documento)
            VALUES (%s, 'INV-CRN-001', %s, %s, %s) RETURNING id""",
            (cid, EXCECAO[3], EXCECAO[2], EXCECAO[1]))
        excecao_id = cur.fetchone()[0]

        # CPM para datas iniciais
        cal = cr.Calendario(CONTRATO["data_inicio"], frozenset(FERIADOS), 420)
        plano = [cr.AtividadePlano(e[0], e[1], e[5], e[6], Decimal(e[4]), excecao=(e[0] == EXCECAO[0]),
                                   memoria_peso=e[8]) for e in EAP if e[3]]
        vincs = [cr.VinculoCPM(*v) for v in VINCULOS]
        cpm = cr.calcular(cr.montar_entrada(plano, cal), vincs)

        aids: dict[str, str] = {}
        for i, e in enumerate(x for x in EAP if x[3]):
            edt, desc, _n, _f, pontos, marco, dur, criterio, _m, resp, disc = e
            r = cpm.atividades[edt]
            cur.execute("""
                INSERT INTO atividade (contrato_id, eap_no_id, codigo_externo, duracao_dias,
                                       inicio_previsto, termino_previsto, tipo, excecao_id,
                                       disciplina, responsavel, criterio_medicao, hh_estimadas,
                                       qtd_prevista, unidade)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (cid, ids[edt], f"A{1000 + i}", dur, cal.data_de(r.ie), cal.data_de(r.te),
                 "MARCO" if marco else "ATIVIDADE", excecao_id if edt == EXCECAO[0] else None,
                 disc, resp, criterio, pontos / 100 if pontos else 0, pontos / 100 if pontos else 0,
                 "hh"))
            aids[edt] = cur.fetchone()[0]
        for pred, suc, tipo, lag in VINCULOS:
            cur.execute("INSERT INTO vinculo (predecessora_id, sucessora_id, tipo, lag_dias) "
                        "VALUES (%s, %s, %s, %s)", (aids[pred], aids[suc], tipo, lag))

        # LVP aprovada + BL0 vigente + snapshot de datas + previsto semanal linear
        cur.execute("""
            INSERT INTO lvp_avaliacao (contrato_id, especie, emissao, nota, aprovado, avaliado_por)
            VALUES (%s, 'LINHA_DE_BASE', '1ª emissão', 0.92, true, 'fiscalização') RETURNING id""", (cid,))
        lvp_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO baseline (contrato_id, numero, estado, lvp_id, aprovada_por, aprovada_em, vigente)
            VALUES (%s, 0, 'APROVADA', %s, 'gerente do contrato', now(), true) RETURNING id""",
            (cid, lvp_id))
        bl_id = cur.fetchone()[0]
        for edt, r in cpm.atividades.items():
            cur.execute("""
                INSERT INTO baseline_atividade (baseline_id, atividade_id, inicio, termino, duracao_dias)
                VALUES (%s, %s, %s, %s, %s)""",
                (bl_id, aids[edt], cal.data_de(r.ie), cal.data_de(r.te), r.te - r.ie + 1))
        semanas = sabados(CONTRATO["data_inicio"], CONTRATO["termino_contratual"])
        for (edt, s), parcela in cr.previsto_linear(cpm, cal, semanas).items():
            cur.execute("""
                INSERT INTO previsto_semanal (baseline_id, eap_no_id, fim_semana, parcela)
                VALUES (%s, %s, %s, %s)""", (bl_id, ids[edt], s, parcela))

        # memória de cálculo inicial
        memoria = [{"edt": d.id, "ie": d.ie, "te": d.te, "folga_total": d.folga_total,
                    "critica": d.critica} for d in cpm.atividades.values()]
        cur.execute("""
            INSERT INTO cronograma_calculo (contrato_id, calculado_por, motivo, data_status,
                versao_regra, termino_indice, termino_data, criticas, memoria, hash)
            VALUES (%s, 'semeadura', 'BL0', %s, %s, %s, %s, %s, %s, sha256(%s::bytea))""",
            (cid, DATA_STATUS, cpm.versao_regra, cpm.fim, cal.data_de(cpm.fim),
             json.dumps(list(cpm.criticas)), json.dumps(memoria), json.dumps(memoria).encode()))
        con.commit()

        # medições: previsto degradado pelo desempenho da frente, semana a semana
        cortes = [s for s in semanas if s <= DATA_STATUS]
        vigente: dict[str, Decimal] = {}
        total = 0
        for s in cortes:
            for e in (x for x in EAP if x[3] and x[4] > 0):
                edt = e[0]
                d = DESEMPENHO.get(edt[:3], Decimal(0))
                if d == 0:
                    continue
                r = cpm.atividades[edt]
                s_ini, s_fim = cr.sabado_de(cal.data_de(r.ie)), cr.sabado_de(cal.data_de(r.te))
                janela = [x for x in semanas if s_ini <= x <= s_fim]
                if not janela or s < janela[0]:
                    continue
                decorridas = len([x for x in janela if x <= s])
                v = min(Decimal(decorridas) / Decimal(len(janela)) * d, Decimal(1))
                v = v.quantize(Decimal("0.0001"))
                if v <= vigente.get(edt, Decimal(0)):
                    continue
                vigente[edt] = v
                cur.execute("""
                    INSERT INTO medicao (contrato_id, eap_no_id, data_corte, percentual_ac,
                                         memoria_calculo, evidencia, criado_por)
                    VALUES (%s, %s, %s, %s, %s, %s, 'planejador')""",
                    (cid, ids[edt], s, v,
                     json.dumps({"origem": "semeadura", "regra": "previsto x desempenho",
                                 "desempenho": str(d)}),
                     f"RDO da semana {s.strftime('%d/%m/%Y')}"))
                total += 1
                con.commit()   # hash encadeado depende da ordem de gravação
        # marcos concluídos
        for edt in ("1.1.1",):
            cur.execute("""
                INSERT INTO medicao (contrato_id, eap_no_id, data_corte, percentual_ac,
                                     memoria_calculo, evidencia, criado_por)
                VALUES (%s, %s, %s, 1, '{"origem": "semeadura"}', 'ordem de serviço assinada', 'planejador')""",
                (cid, ids[edt], semanas[0]))
        con.commit()
        print(f"contrato {CONTRATO['codigo']} semeado: {len(aids)} atividades, {len(VINCULOS)} vínculos, "
              f"{total} medições, término BL0 em {cal.data_de(cpm.fim)}")
        print(f"VITE_ORGANIZACAO_ID={org_id}\nVITE_CONTRATO_ID={cid}")


if __name__ == "__main__":
    main()
