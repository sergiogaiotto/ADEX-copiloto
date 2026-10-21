"""Leitura de meses escritos pelo dev ("mar, jun e set/2025" -> 2025-03, 2025-06, 2025-09)."""

from __future__ import annotations

import re
import unicodedata

_MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
_TOKEN = re.compile(r"\b(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[a-z]*\.?(?:\s*(?:/|de)\s*(\d{4}))?|\b(\d{4})\b")


def extrair_meses(texto: str) -> list[str]:
    """Meses na ordem em que aparecem; o ano vale para os meses anteriores sem ano."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii").lower()
    saida: list[str] = []
    pendentes: list[int] = []
    for m in _TOKEN.finditer(t):
        mes, ano_colado, ano_solto = m.group(1), m.group(2), m.group(3)
        if mes:
            pendentes.append(_MESES[mes])
            if ano_colado:
                saida.extend(f"{ano_colado}-{p:02d}" for p in pendentes)
                pendentes = []
        elif ano_solto and pendentes:
            saida.extend(f"{ano_solto}-{p:02d}" for p in pendentes)
            pendentes = []
    vistos, unicos = set(), []
    for s in saida:
        if s not in vistos:
            vistos.add(s)
            unicos.append(s)
    return unicos
