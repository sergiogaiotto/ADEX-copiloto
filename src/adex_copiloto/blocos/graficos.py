"""Gráficos dos blocos: matplotlib sem pyplot (sem estado global, funciona sem tela).

Cores com contraste alto e distinguíveis por quem tem daltonismo; cada gráfico
tem título e descrição em texto (para leitores de tela e para a IA).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

AZUL = "#1f5fa8"
LARANJA = "#d9822b"
CINZA = "#7a7f87"
VERDE = "#2e8540"
VERMELHO = "#b23b3b"
PALETA = [AZUL, LARANJA, VERDE, "#6f4fa0", "#a8781f", CINZA]


def nova(largura: float = 7.2, altura: float = 3.6) -> tuple[Figure, object]:
    fig = Figure(figsize=(largura, altura), dpi=110)
    ax = fig.subplots()
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    return fig, ax


def linhas(series: dict[str, pd.Series], *, titulo: str, eixo_y: str, marcar_x: Sequence | None = None) -> Figure:
    fig, ax = nova()
    for i, (nome, s) in enumerate(series.items()):
        ax.plot(s.index, s.values, label=nome, color=PALETA[i % len(PALETA)], linewidth=2)
    for x in marcar_x or []:
        ax.axvline(x, color=CINZA, linestyle=":", linewidth=1)
    ax.set_title(titulo, fontsize=11, loc="left")
    ax.set_ylabel(eixo_y)
    if len(series) > 1:
        ax.legend(frameon=False, fontsize=9)
    fig.autofmt_xdate()
    return fig


def evento(tabela: pd.DataFrame, *, titulo: str, eixo_y: str) -> Figure:
    fig, ax = nova()
    t = tabela.sort_values("e")
    ax.axhline(0, color=CINZA, linewidth=1)
    ax.axvline(-0.5, color=CINZA, linestyle=":", linewidth=1)
    if {"ic_inf", "ic_sup"} <= set(t.columns):
        ax.fill_between(t["e"], t["ic_inf"], t["ic_sup"], color=AZUL, alpha=0.18, linewidth=0)
    ax.plot(t["e"], t["att"], marker="o", color=AZUL, linewidth=2)
    ax.set_xlabel("períodos em relação à ação (0 = primeiro período com a ação)")
    ax.set_ylabel(eixo_y)
    ax.set_title(titulo, fontsize=11, loc="left")
    return fig


def barras(categorias: Sequence[str], valores: Sequence[float], *, titulo: str, eixo_y: str, destaque: int | None = None) -> Figure:
    fig, ax = nova()
    cores = [LARANJA if i == destaque else AZUL for i in range(len(valores))]
    ax.bar(list(categorias), list(valores), color=cores)
    ax.set_title(titulo, fontsize=11, loc="left")
    ax.set_ylabel(eixo_y)
    for rot in ax.get_xticklabels():
        rot.set_rotation(30)
        rot.set_ha("right")
    return fig


def pontos_com_ic(rotulos: Sequence[str], valores: Sequence[float], inf: Sequence[float], sup: Sequence[float], *, titulo: str, eixo_x: str) -> Figure:
    fig, ax = nova(altura=max(2.4, 0.4 * len(rotulos) + 1))
    y = np.arange(len(rotulos))
    ax.errorbar(valores, y, xerr=[np.subtract(valores, inf), np.subtract(sup, valores)], fmt="o", color=AZUL, capsize=3)
    ax.axvline(0, color=CINZA, linewidth=1)
    ax.set_yticks(y, list(rotulos))
    ax.set_xlabel(eixo_x)
    ax.set_title(titulo, fontsize=11, loc="left")
    ax.grid(axis="x", alpha=0.25)
    return fig
