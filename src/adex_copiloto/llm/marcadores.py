"""Números marcados: a IA escreve ⟦M-01⟧, o Python põe o número.

Regra fixa: todo número de resultado vem de cálculo em Python. A IA escreve o
texto em volta, usando só marcadores. Um número "solto" no texto da IA (que não
seja marcador, identificador como H-01, ou rótulo permitido como "onda 4") faz
a narrativa ser recusada e refeita; se insistir, o copiloto usa um texto-modelo
sem IA.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

MARCADOR = re.compile(r"⟦(M-\d{2,4})⟧")
# Identificadores do estudo: H-01, D-07, PA-01, L-01, M-01, EST-2026-0142, C-03...
_ID = re.compile(r"\b[A-Z]{1,5}(?:-\d+)+[a-z]?\b")
_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass(frozen=True)
class ValorMarcado:
    id: str
    valor: float | int | str
    texto: str
    descricao: str = ""
    unidade: str = ""
    origem: str = ""  # bloco@versão que calculou


@dataclass
class TabelaMarcadores:
    """Registro de números do estudo. ``novo`` dá o próximo M-xx."""

    valores: dict[str, ValorMarcado] = field(default_factory=dict)

    def novo(self, valor: float | int | str, *, texto: str | None = None, descricao: str = "",
             unidade: str = "", origem: str = "", casas: int | None = None, compacto: bool = False,
             sinal: bool = False) -> ValorMarcado:
        mid = f"M-{len(self.valores) + 1:02d}"
        vm = ValorMarcado(
            id=mid,
            valor=valor,
            texto=texto if texto is not None else formatar(valor, casas=casas, unidade=unidade, compacto=compacto, sinal=sinal),
            descricao=descricao,
            unidade=unidade,
            origem=origem,
        )
        self.valores[mid] = vm
        return vm

    def __getitem__(self, mid: str) -> ValorMarcado:
        return self.valores[mid]

    def para_llm(self) -> list[dict]:
        """O que a IA vê: id e descrição. Nunca o número."""
        return [{"marcador": f"⟦{v.id}⟧", "o_que_e": v.descricao, "unidade": v.unidade} for v in self.valores.values()]


def _milhar(inteiro: str) -> str:
    sinal = ""
    if inteiro.startswith("-"):
        sinal, inteiro = "-", inteiro[1:]
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return sinal + ".".join(grupos)


def formatar(valor, *, casas: int | None = None, unidade: str = "", compacto: bool = False, sinal: bool = False) -> str:
    """Formata no padrão brasileiro: 1.353 · 1,62 mi · 34% · R$ 21 mi."""
    if isinstance(valor, str):
        return valor
    if valor is None or (isinstance(valor, float) and (math.isnan(valor) or math.isinf(valor))):
        return "—"
    v = float(valor)
    prefixo = "+" if sinal and v > 0 else ""
    if unidade == "%":  # sempre fração: 0,34 -> 34%
        texto = _decimal(v * 100, 0 if casas is None else casas)
        return f"{prefixo}{texto}%"
    if unidade == "pp":
        return f"{prefixo}{_decimal(v, 1 if casas is None else casas)} p.p."
    sufixo = ""
    if compacto and abs(v) >= 1_000_000_000:
        v, sufixo = v / 1e9, " bi"
    elif compacto and abs(v) >= 1_000_000:
        v, sufixo = v / 1e6, " mi"
    elif compacto and abs(v) >= 10_000:
        v, sufixo = v / 1e3, " mil"
    if casas is None:
        casas = 0 if (float(v).is_integer() and not sufixo) else (2 if abs(v) < 10 else 1)
        if sufixo and abs(v) >= 100:
            casas = 0
    texto = _decimal(v, casas)
    if unidade in ("R$", "BRL"):
        return f"{prefixo}R$ {texto}{sufixo}"
    if unidade and unidade not in ("", "n"):
        return f"{prefixo}{texto}{sufixo} {unidade}"
    return f"{prefixo}{texto}{sufixo}"


def _decimal(v: float, casas: int) -> str:
    bruto = f"{v:.{casas}f}"
    if "." in bruto:
        inteiro, frac = bruto.split(".")
        return f"{_milhar(inteiro)},{frac}"
    return _milhar(bruto)


def ids(texto: str) -> list[str]:
    return MARCADOR.findall(texto or "")


def numeros_soltos(texto: str, rotulos_permitidos: Iterable[str] = ()) -> list[str]:
    """Números que aparecem fora de marcadores, identificadores e rótulos permitidos."""
    limpo = MARCADOR.sub(" ", texto or "")
    for rotulo in sorted({r for r in rotulos_permitidos if r}, key=len, reverse=True):
        limpo = limpo.replace(rotulo, " ")
    limpo = _ID.sub(" ", limpo)
    return _NUMERO.findall(limpo)


def validar(
    texto: str,
    tabela: TabelaMarcadores,
    rotulos_permitidos: Iterable[str] = (),
    numeros_permitidos: Iterable[str] = (),
) -> list[str]:
    """Problemas do texto da IA: marcador inexistente ou número fora de marcador.

    ``numeros_permitidos``: números que o próprio dev escreveu ou que o Python pôs
    em textos (datas, rótulos) — repetir esses não é inventar número.
    """
    problemas = []
    desconhecidos = [m for m in ids(texto) if m not in tabela.valores]
    if desconhecidos:
        problemas.append("marcadores que não existem: " + ", ".join(sorted(set(desconhecidos))))
    permitidos = set(numeros_permitidos)
    soltos = [n for n in numeros_soltos(texto, rotulos_permitidos) if n not in permitidos]
    if soltos:
        problemas.append(
            "números escritos sem marcador (use só ⟦M-xx⟧): " + ", ".join(sorted(set(soltos))[:12])
        )
    return problemas


def preencher(texto: str, tabela: TabelaMarcadores, *, html: bool = False) -> str:
    def troca(m: re.Match[str]) -> str:
        mid = m.group(1)
        if mid not in tabela.valores:
            raise KeyError(f"marcador {mid} não existe")
        vm = tabela.valores[mid]
        if html:
            from html import escape

            titulo = escape(f"{vm.id} · {vm.descricao} · calculado por {vm.origem or 'Python'}")
            return f'<b class="adex-num" title="{titulo}">{escape(vm.texto)}</b>'
        return vm.texto

    return MARCADOR.sub(troca, texto or "")
