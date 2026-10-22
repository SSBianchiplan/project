"""Importação de cronograma nos formatos aceitos pelo procedimento.

Regra que atravessa todos os parsers: a importação traz estrutura, datas,
durações e vínculos. Ela NÃO traz avanço realizado (INV-IMP-003) — `%
concluída` do arquivo entra apenas como achado de comparação.
"""
from .detector import detectar, importar
from .modelo import (
    Achado,
    AtividadeImportada,
    Formato,
    NoImportada,
    PlanoImportado,
    Severidade,
    VinculoImportado,
)

__all__ = [
    "Achado", "AtividadeImportada", "Formato", "NoImportada", "PlanoImportado",
    "Severidade", "VinculoImportado", "detectar", "importar",
]
