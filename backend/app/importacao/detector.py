"""Detecção de formato e despacho para o parser correto."""
from __future__ import annotations

from .modelo import Formato, PlanoImportado
from .validador import validar

MAGIC_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # Compound File (MPP, XLS)
MAGIC_ZIP = b"PK\x03\x04"                           # OOXML (XLSX)


def detectar(nome: str, conteudo: bytes) -> Formato:
    cabeca = conteudo[:2048]
    if conteudo.startswith(MAGIC_OLE):
        return Formato.MPP
    if conteudo.startswith(MAGIC_ZIP):
        return Formato.XLSX_MASCARA
    if cabeca.startswith(b"ERMHDR") or b"%T\tPROJWBS" in cabeca or b"%T\tTASK" in cabeca:
        return Formato.P6_XER
    texto = cabeca.decode("utf-8", errors="ignore")
    if "schemas.microsoft.com/project" in texto:
        return Formato.MSPROJECT_XML
    if "APIBusinessObjects" in texto or ("<Project" in texto and "<WBS" in conteudo[:20000].decode(
        "utf-8", errors="ignore"
    )):
        return Formato.P6_XML
    raise ValueError(f"formato não reconhecido para '{nome}'")


def importar(
    nome: str, conteudo: bytes, *, excecoes: set[str] | None = None,
    edts_conhecidos: set[str] | None = None,
    vinculos_conhecidos: set[tuple[str, str]] | None = None,
) -> PlanoImportado:
    """Detecta, parseia e valida. Não escreve nada — escrita é ato humano (P4)."""
    formato = detectar(nome, conteudo)
    if formato is Formato.P6_XER:
        from .p6_xer import parse
    elif formato is Formato.MSPROJECT_XML:
        from .msproject_xml import parse
    elif formato is Formato.P6_XML:
        from .p6_xml import parse
    elif formato is Formato.XLSX_MASCARA:
        from .xlsx_mascara import parse
    else:
        from .mpp import parse
    plano = parse(conteudo, origem=nome)
    validar(plano, excecoes=excecoes, edts_conhecidos=edts_conhecidos,
            vinculos_conhecidos=vinculos_conhecidos)
    return plano
