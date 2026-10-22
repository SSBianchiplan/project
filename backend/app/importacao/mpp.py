"""MPP — formato binário proprietário do MS Project.

Não existe parser nativo em Python. A única leitura confiável é MPXJ (Java),
via JPype. Se o jar não estiver presente, este módulo falha com instrução,
nunca com tentativa parcial: MPP lido pela metade produz cronograma com
vínculo faltando, e vínculo faltando some do caminho crítico em silêncio.

Instalação:
    pip install jpype1 mpxj
    # o pacote mpxj já traz os jars

Alternativa sem Java: pedir ao fornecedor o XML nativo (Arquivo > Salvar como
> XML), que o parser msproject_xml lê sem dependência externa.
"""
from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from .modelo import AtividadeImportada, Formato, NoImportada, PlanoImportado, VinculoImportado

TIPO_VINCULO = {
    "FINISH_START": "TI", "START_START": "II",
    "FINISH_FINISH": "TT", "START_FINISH": "IT",
}


class MpxjIndisponivel(RuntimeError):
    pass


def _carregar_mpxj() -> Any:  # objeto Java, sem stub
    try:
        import jpype  # noqa: PLC0415
        import mpxj  # noqa: PLC0415, F401
    except ImportError as e:  # pragma: no cover - depende do ambiente
        raise MpxjIndisponivel(
            "leitura de MPP exige MPXJ: pip install jpype1 mpxj. "
            "Sem Java no host, peça o XML nativo do Project."
        ) from e
    if not jpype.isJVMStarted():  # pragma: no cover
        import jpype.imports  # noqa: PLC0415
        jvm = _localizar_jvm()
        if jvm:
            jpype.startJVM(jvmpath=jvm, classpath=jpype.getClassPath())
        else:
            jpype.startJVM(classpath=jpype.getClassPath())
    try:
        from org.mpxj.reader import UniversalProjectReader  # noqa: PLC0415  (MPXJ >= 14)
    except ImportError:  # pragma: no cover
        from net.sf.mpxj.reader import UniversalProjectReader  # noqa: PLC0415  (MPXJ <= 13)
    return UniversalProjectReader()


def _localizar_jvm() -> str | None:
    """JAVA_HOME ou o JRE instalado pelo pacote install-jdk (~/.jdk/*), sem exigir Java no PATH."""
    import os  # noqa: PLC0415
    candidatos = []
    if os.environ.get("JAVA_HOME"):
        candidatos.append(Path(os.environ["JAVA_HOME"]))
    for pasta in (".jdk", ".jre"):
        candidatos += sorted(Path.home().joinpath(pasta).glob("*"), reverse=True)
    for base in candidatos:
        for rel in ("bin/server/jvm.dll", "bin/client/jvm.dll", "lib/server/libjvm.so",
                    "lib/server/libjvm.dylib"):
            if (base / rel).exists():
                return str(base / rel)
    return None


def _para_date(v: Any) -> date | None:  # pragma: no cover - objeto Java
    return None if v is None else date(v.getYear(), v.getMonthValue(), v.getDayOfMonth())


def parse(conteudo: bytes, origem: str = "") -> PlanoImportado:  # pragma: no cover
    leitor = _carregar_mpxj()
    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "entrada.mpp"
        caminho.write_bytes(conteudo)
        projeto = leitor.read(str(caminho))

    plano = PlanoImportado(formato=Formato.MPP, origem=origem)
    for tarefa in projeto.getTasks():
        uid = str(tarefa.getUniqueID())
        if tarefa.getID() == 0:
            continue
        edt = str(tarefa.getWBS() or "")
        nome = str(tarefa.getName() or "")
        if tarefa.getSummary():
            plano.nos.append(NoImportada(edt, nome, int(tarefa.getOutlineLevel() or 1)))
            continue
        marco = bool(tarefa.getMilestone())
        dur = tarefa.getDuration()
        dias = 0 if marco or dur is None else int(Decimal(str(dur.getDuration())))
        pct = tarefa.getPercentageComplete()
        plano.atividades.append(AtividadeImportada(
            id_externo=uid,
            edt=edt,
            nome=nome,
            duracao_dias=dias,
            inicio=_para_date(tarefa.getStart()),
            termino=_para_date(tarefa.getFinish()),
            marco=marco,
            restricao=str(tarefa.getConstraintType() or "") or None,
            percentual_arquivo=None if pct is None else Decimal(str(pct)) / 100,
        ))
        for rel in tarefa.getPredecessors():
            lag = rel.getLag()
            plano.vinculos.append(VinculoImportado(
                predecessora=str((rel.getPredecessorTask() if hasattr(rel, "getPredecessorTask")
                                  else rel.getTargetTask()).getUniqueID()),
                sucessora=uid,
                tipo=TIPO_VINCULO.get(str(rel.getType()), "TI"),
                lag_dias=0 if lag is None else int(Decimal(str(lag.getDuration()))),
            ))
    return plano
