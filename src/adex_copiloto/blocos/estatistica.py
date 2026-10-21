"""Métodos estatísticos dos blocos do ADEX Copiloto — módulo AUTOSSUFICIENTE.

Este arquivo é copiado para o pacote de handoff (``src/adex_metodos.py``) e
precisa rodar sem a biblioteca ``adex_copiloto``. Por isso:
- só importa numpy, pandas, scipy, statsmodels e scikit-learn;
- todas as funções são puras (entrada -> saída), com seed explícita.

Conteúdo:
- comparações e incerteza: Benjamini-Hochberg, Wilson, bootstrap, testes de grupos;
- amostragem: tamanho de amostra, alocação estratificada, PSI, SMD;
- efeito de ações em ondas: coortes, ATT(g,t) com controles ainda não tratados
  (Callaway & Sant'Anna), agregação, estudo de evento, placebo, bootstrap por grupo;
- modelos: métricas de classificação, calibração, lift, VIF;
- séries: sazonal ingênuo, suavização exponencial, backtest com origem móvel, MASE;
- anomalias: z robusto (mediana e MAD);
- BI: decomposição de variação em efeito mix e efeito taxa.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

__all__ = [
    "bh_ajuste", "wilson_ic", "bootstrap_ic", "teste_dois_grupos", "teste_proporcoes", "tamanho_amostra_proporcao",
    "tamanho_amostra_media", "alocacao_estratificada", "psi", "psi_contagens", "psi_categorico", "psi_numerico", "smd",
    "coortes_por_limiar", "att_gt", "agregar_att", "did_escalonado", "placebo_temporal", "pico_pre_tratamento",
    "metricas_classificacao", "tabela_calibracao", "lift_por_decil", "vif", "sazonal_ingenuo", "suavizacao_exponencial",
    "backtest_series", "mase", "smape", "z_robusto", "decomposicao_mix_taxa",
    "montar_painel", "perfil_de_contagens", "checar_qualidade", "estabilidade_categorias", "representatividade", "separar_temporal", "separar_por_grupo", "calcular_kpis", "reconciliar", "comparar_grupos", "ajustar_ols", "ajustar_gbm_regressao", "ajustar_classificador", "checar_vazamento", "segmentar_kmeans", "decompor_stl", "anomalias_serie", "anomalias_multivariadas", "comparar_proporcoes_agregadas", "cenarios_did", "remover_tendencia_coorte",
    "anomalias_por_grupo", "serie_total", "estabilidade_janelas", "sensibilidade_estavel",
]


# --- comparações e incerteza --------------------------------------------------------------


def bh_ajuste(pvalores: Sequence[float]) -> np.ndarray:
    """Correção de Benjamini-Hochberg (controle da taxa de falsas descobertas)."""
    p = np.asarray(pvalores, dtype=float)
    n = p.size
    if n == 0:
        return p
    ordem = np.argsort(p)
    ajust = np.empty(n)
    acumulado = 1.0
    for posicao in range(n - 1, -1, -1):
        i = ordem[posicao]
        acumulado = min(acumulado, p[i] * n / (posicao + 1))
        ajust[i] = acumulado
    return np.clip(ajust, 0, 1)


def wilson_ic(sucessos: int, total: int, confianca: float = 0.95) -> tuple[float, float]:
    if total <= 0:
        return (float("nan"), float("nan"))
    z = stats.norm.ppf(1 - (1 - confianca) / 2)
    p = sucessos / total
    denom = 1 + z**2 / total
    centro = (p + z**2 / (2 * total)) / denom
    meia = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denom
    return (max(0.0, centro - meia), min(1.0, centro + meia))


def bootstrap_ic(
    dados: np.ndarray | pd.DataFrame,
    estatistica: Callable[[Any], float],
    *,
    n: int = 999,
    seed: int = 42,
    confianca: float = 0.95,
    grupos: np.ndarray | None = None,
) -> dict[str, float]:
    """IC por bootstrap percentil. Com ``grupos``, reamostra grupos inteiros (cluster)."""
    rng = np.random.default_rng(seed)
    estimativa = float(estatistica(dados))
    replicas = np.empty(n)
    if grupos is None:
        tamanho = len(dados)
        for b in range(n):
            idx = rng.integers(0, tamanho, tamanho)
            amostra = dados.iloc[idx] if isinstance(dados, pd.DataFrame) else np.asarray(dados)[idx]
            replicas[b] = estatistica(amostra)
    else:
        unicos = np.unique(grupos)
        indices = {g: np.flatnonzero(grupos == g) for g in unicos}
        for b in range(n):
            escolhidos = rng.choice(unicos, size=len(unicos), replace=True)
            idx = np.concatenate([indices[g] for g in escolhidos])
            amostra = dados.iloc[idx] if isinstance(dados, pd.DataFrame) else np.asarray(dados)[idx]
            replicas[b] = estatistica(amostra)
    replicas = replicas[np.isfinite(replicas)]
    alfa = 1 - confianca
    if replicas.size < max(20, n // 5):
        return {"estimativa": estimativa, "ic_inf": float("nan"), "ic_sup": float("nan"), "ep": float("nan"), "n_boot": int(replicas.size)}
    return {
        "estimativa": estimativa,
        "ic_inf": float(np.quantile(replicas, alfa / 2)),
        "ic_sup": float(np.quantile(replicas, 1 - alfa / 2)),
        "ep": float(np.std(replicas, ddof=1)),
        "n_boot": int(replicas.size),
    }


def teste_dois_grupos(a: Sequence[float], b: Sequence[float], *, confianca: float = 0.95) -> dict[str, Any]:
    """Compara médias de dois grupos: Welch (sem supor variâncias iguais) e Mann-Whitney.

    Devolve a diferença (a - b), IC de Welch, p-valores, g de Hedges e checagens.
    """
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    x, y = x[np.isfinite(x)], y[np.isfinite(y)]
    nx, ny = x.size, y.size
    if nx < 2 or ny < 2:
        raise ValueError("cada grupo precisa de pelo menos 2 valores")
    dif = x.mean() - y.mean()
    vx, vy = x.var(ddof=1), y.var(ddof=1)
    ep = math.sqrt(vx / nx + vy / ny)
    gl = (vx / nx + vy / ny) ** 2 / ((vx / nx) ** 2 / (nx - 1) + (vy / ny) ** 2 / (ny - 1)) if ep > 0 else nx + ny - 2
    t = dif / ep if ep > 0 else 0.0
    p_welch = 2 * stats.t.sf(abs(t), gl) if ep > 0 else 1.0
    tc = stats.t.ppf(1 - (1 - confianca) / 2, gl)
    try:
        p_mw = float(stats.mannwhitneyu(x, y, alternative="two-sided").pvalue)
    except ValueError:
        p_mw = float("nan")
    dp = math.sqrt(((nx - 1) * vx + (ny - 1) * vy) / (nx + ny - 2)) if nx + ny > 2 else float("nan")
    d = dif / dp if dp and dp > 0 else 0.0
    correcao = 1 - 3 / (4 * (nx + ny) - 9) if nx + ny > 3 else 1.0
    normal = None
    if 8 <= min(nx, ny) <= 5000:
        normal = bool(stats.shapiro(x[:5000]).pvalue > 0.01 and stats.shapiro(y[:5000]).pvalue > 0.01)
    return {
        "media_a": float(x.mean()), "media_b": float(y.mean()), "diferenca": float(dif),
        "ic_inf": float(dif - tc * ep), "ic_sup": float(dif + tc * ep),
        "p_welch": float(p_welch), "p_mann_whitney": p_mw, "hedges_g": float(d * correcao),
        "n_a": int(nx), "n_b": int(ny), "aproximadamente_normal": normal,
        "recomendado": "welch" if (normal or min(nx, ny) >= 30) else "mann_whitney",
    }


def teste_proporcoes(sucessos: Sequence[int], totais: Sequence[int], *, confianca: float = 0.95) -> dict[str, Any]:
    """Duas proporções: diferença com IC de Newcombe e teste z; k > 2: qui-quadrado."""
    s = np.asarray(sucessos, dtype=float)
    n = np.asarray(totais, dtype=float)
    if (n <= 0).any() or (s < 0).any() or (s > n).any():
        raise ValueError("contagens inválidas")
    props = s / n
    if s.size == 2:
        p1, p2 = props
        l1, u1 = wilson_ic(int(s[0]), int(n[0]), confianca)
        l2, u2 = wilson_ic(int(s[1]), int(n[1]), confianca)
        dif = p1 - p2
        ic_inf = dif - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
        ic_sup = dif + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
        pool = s.sum() / n.sum()
        ep = math.sqrt(pool * (1 - pool) * (1 / n[0] + 1 / n[1]))
        z = dif / ep if ep > 0 else 0.0
        return {"proporcoes": props.tolist(), "diferenca": float(dif), "ic_inf": float(ic_inf), "ic_sup": float(ic_sup),
                "p_valor": float(2 * stats.norm.sf(abs(z))), "teste": "z de duas proporções"}
    tabela = np.vstack([s, n - s])
    chi2, p, gl, _ = stats.chi2_contingency(tabela)
    return {"proporcoes": props.tolist(), "qui2": float(chi2), "gl": int(gl), "p_valor": float(p), "teste": "qui-quadrado"}


# --- amostragem ---------------------------------------------------------------------------


def tamanho_amostra_proporcao(margem: float, *, p: float = 0.5, confianca: float = 0.95, populacao: int | None = None) -> int:
    z = stats.norm.ppf(1 - (1 - confianca) / 2)
    n0 = z**2 * p * (1 - p) / margem**2
    if populacao:
        n0 = n0 / (1 + (n0 - 1) / populacao)
    return int(math.ceil(n0))


def tamanho_amostra_media(margem: float, desvio: float, *, confianca: float = 0.95, populacao: int | None = None) -> int:
    z = stats.norm.ppf(1 - (1 - confianca) / 2)
    n0 = (z * desvio / margem) ** 2
    if populacao:
        n0 = n0 / (1 + (n0 - 1) / populacao)
    return int(math.ceil(n0))


def alocacao_estratificada(
    tamanhos: pd.Series, n: int, *, desvios: pd.Series | None = None, metodo: str = "proporcional", minimo: int = 0
) -> pd.Series:
    """Quantos sortear por estrato (proporcional ou de Neyman), com mínimo por estrato.

    O total é sempre ``n`` (ou o universo, se menor); nenhum estrato passa do seu tamanho.
    """
    tam = tamanhos.astype(float).clip(lower=0)
    n = int(min(n, tam.sum()))
    if metodo == "neyman":
        if desvios is None:
            raise ValueError("Neyman precisa dos desvios por estrato")
        pesos = tam * desvios.reindex(tam.index).fillna(desvios.mean()).astype(float).clip(lower=1e-9)
    else:
        pesos = tam.copy()
    base = np.minimum(tam, float(minimo))
    if base.sum() > n:  # mínimos não cabem: reparte n pelos mínimos proporcionalmente
        base = np.floor(n * base / base.sum())
    restante = n - base.sum()
    capacidade = tam - base
    alocado = base.copy()
    ativos = capacidade > 0
    while restante > 1e-9 and ativos.any():
        w = pesos.where(ativos, 0.0)
        if w.sum() <= 0:
            w = capacidade.where(ativos, 0.0)
        parte = restante * w / w.sum()
        acrescimo = np.minimum(parte, capacidade)
        alocado += acrescimo
        capacidade -= acrescimo
        restante = n - alocado.sum()
        ativos = capacidade > 1e-9
        if (acrescimo < parte - 1e-9).sum() == 0:
            break
    inteiro = np.floor(alocado)
    falta = int(round(n - inteiro.sum()))
    fracao = (alocado - inteiro).sort_values(ascending=False)
    for idx in fracao.index:
        if falta <= 0:
            break
        if inteiro[idx] < tam[idx]:
            inteiro[idx] += 1
            falta -= 1
    return inteiro.astype(int)


def psi(ref: Sequence[float], novo: Sequence[float], eps: float = 1e-4) -> float:
    """Índice de estabilidade populacional entre duas distribuições (proporções ou contagens por faixa).

    Normaliza para proporções ANTES do piso ``eps``: com o piso aplicado às contagens, o
    resultado dependia da escala (mesmas proporções davam 0,55 ×100 e 1,01 ×10⁶).
    """
    a = np.asarray(ref, dtype=float)
    b = np.asarray(novo, dtype=float)
    a = a / a.sum() if a.sum() > 0 else a
    b = b / b.sum() if b.sum() > 0 else b
    a, b = np.clip(a, eps, None), np.clip(b, eps, None)
    a, b = a / a.sum(), b / b.sum()
    return float(np.sum((b - a) * np.log(b / a)))


def psi_contagens(ref: pd.Series, novo: pd.Series) -> float:
    """PSI a partir de contagens por categoria (índice = categoria)."""
    cats = ref.index.union(novo.index)
    return psi(ref.reindex(cats, fill_value=0).to_numpy(dtype=float), novo.reindex(cats, fill_value=0).to_numpy(dtype=float))


def psi_categorico(ref: Sequence[Any], novo: Sequence[Any]) -> float:
    """PSI de uma variável categórica a partir dos valores (não das contagens)."""
    return psi_contagens(pd.Series(ref).astype(str).value_counts(), pd.Series(novo).astype(str).value_counts())


def psi_numerico(ref: Sequence[float], novo: Sequence[float], faixas: int = 10) -> float:
    r = np.asarray(ref, dtype=float)
    r = r[np.isfinite(r)]
    v = np.asarray(novo, dtype=float)
    v = v[np.isfinite(v)]
    cortes = np.unique(np.quantile(r, np.linspace(0, 1, faixas + 1)))
    if cortes.size < 3:
        return 0.0
    cortes[0], cortes[-1] = -np.inf, np.inf
    pa = np.histogram(r, cortes)[0]
    pb = np.histogram(v, cortes)[0]
    return psi(pa, pb)


def smd(x: Sequence[float], y: Sequence[float]) -> float:
    """Diferença média padronizada (|SMD| > 0,1 costuma indicar desequilíbrio)."""
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    s = math.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2) if a.size > 1 and b.size > 1 else 0.0
    return float((a.mean() - b.mean()) / s) if s > 0 else 0.0


# --- efeito de ações em ondas (diferença-em-diferenças escalonada) -----------------------------


def coortes_por_limiar(
    painel: pd.DataFrame, *, grupo: str, periodo: str, tratados: str, unidades: str, limiar: float = 0.5
) -> pd.DataFrame:
    """Primeiro período em que a fração de unidades tratadas do grupo atinge o limiar.

    Devolve uma linha por grupo: ``coorte`` (período) ou NaT quando nunca trata.
    """
    p = painel[[grupo, periodo, tratados, unidades]].copy()
    p["fracao"] = p[tratados] / p[unidades].where(p[unidades] > 0)
    p = p.sort_values([grupo, periodo])
    linhas = []
    for g, d in p.groupby(grupo, sort=True):
        atingiu = d[d["fracao"] >= limiar]
        coorte = atingiu[periodo].iloc[0] if len(atingiu) else pd.NaT
        linhas.append({grupo: g, "coorte": coorte, "fracao_final": float(d["fracao"].iloc[-1]) if len(d) else float("nan")})
    return pd.DataFrame(linhas)


def _indices_de_periodo(serie: pd.Series) -> tuple[pd.Series, list]:
    valores = sorted(pd.unique(serie.dropna()))
    mapa = {v: i for i, v in enumerate(valores)}
    return serie.map(mapa), valores


def _matrizes(painel: pd.DataFrame, *, y: str, grupo: str, periodo: str, coorte: str, peso: str | None,
              info: dict[str, Any] | None = None):
    """Painel -> (Y, W, coorte por grupo como índice de período ou +inf, rótulos de período, grupos).

    Coorte antes do primeiro período observado = grupo **sempre tratado**: sai da conta
    (contá-lo como "nunca tratado" atenua o efeito; medido: 59% com 5 de 20 grupos).
    Coorte depois do último período = ainda não tratado em todo o painel (+inf).
    Coorte dentro da janela que não coincide com nenhum período é erro (data desalinhada).
    Em ``info`` ficam os grupos sempre tratados (``sempre_tratados``).
    """
    _, rotulos = _indices_de_periodo(painel[periodo])
    mapa = {v: i for i, v in enumerate(rotulos)}
    primeira = painel.groupby(grupo)[coorte].first()
    sempre: list[Any] = []
    cg_de: dict[Any, float] = {}
    for g, c in primeira.items():
        if pd.isna(c):
            cg_de[g] = np.inf
        elif c in mapa:
            cg_de[g] = float(mapa[c])
        else:
            try:
                antes, depois = c < rotulos[0], c > rotulos[-1]
            except TypeError as e:
                raise ValueError(f"a coorte do grupo {g} ({c!r}) não é comparável aos períodos do painel") from e
            if antes:
                sempre.append(g)
            elif depois:
                cg_de[g] = np.inf
            else:
                raise ValueError(
                    f"a coorte do grupo {g} ({c}) não coincide com nenhum período do painel: alinhe as datas "
                    "(ex.: primeiro dia do mês) antes de estimar o efeito"
                )
    if info is not None:
        info["sempre_tratados"] = [str(g) for g in sempre]
    grupos = sorted(cg_de)
    gi = {g: i for i, g in enumerate(grupos)}
    p = painel[painel[grupo].isin(gi)]
    Y = np.full((len(grupos), len(rotulos)), np.nan)
    W = np.zeros((len(grupos), len(rotulos)))
    linhas = p[grupo].map(gi).to_numpy()
    colunas = p[periodo].map(mapa).to_numpy()
    Y[linhas, colunas] = p[y].to_numpy(dtype=float)
    W[linhas, colunas] = p[peso].to_numpy(dtype=float) if peso else 1.0
    W = np.nan_to_num(W, nan=0.0)
    W[~np.isfinite(Y)] = 0.0
    Y[W <= 0] = np.nan  # peso zero = sem dado naquele período
    cg = np.array([cg_de[g] for g in grupos], dtype=float)
    return Y, W, cg, rotulos, grupos


def _media_ponderada(Y: np.ndarray, W: np.ndarray, linhas: np.ndarray, col: int) -> float:
    w = W[linhas, col]
    if w.sum() <= 0:
        return float("nan")
    yy = np.where(w > 0, Y[linhas, col], 0.0)
    return float((yy * w).sum() / w.sum())


def _diferenca_ponderada(Y: np.ndarray, W: np.ndarray, linhas: np.ndarray, t: int, base: int) -> tuple[float, float]:
    """Média ponderada de Y_t − Y_base nos grupos com dado nos DOIS períodos, com o peso da base.

    Usar conjuntos diferentes de grupos em t e na base (painel desbalanceado, células
    cortadas pelo grupo mínimo) ou pesos que mudam no tempo mistura composição com
    efeito (medido: viés −2,15 com perda ligada ao nível; +0,66 com pesos crescendo).
    """
    yt, yb, w = Y[linhas, t], Y[linhas, base], W[linhas, base]
    ok = np.isfinite(yt) & np.isfinite(yb) & (w > 0)
    soma = float(w[ok].sum())
    if soma <= 0:
        return float("nan"), 0.0
    return float(((yt[ok] - yb[ok]) * w[ok]).sum() / soma), soma


def _att_gt_matriz(Y, W, cg, *, ignorar_primeiros: int = 0) -> list[tuple]:
    """Núcleo do ATT(g,t) sobre matrizes (rápido o bastante para o bootstrap)."""
    T = Y.shape[1]
    saida = []
    for g in np.unique(cg[np.isfinite(cg)]).astype(int):
        tratados = np.flatnonzero(cg == g)
        for t in range(T):
            # pós: diferença longa contra g-1; pré (inclui t = g-1): base variável t-1
            base = g - 1 if t >= g else t - 1
            if base < 0:
                continue
            if t >= g and (t - g) < ignorar_primeiros:
                continue
            controles = np.flatnonzero((cg > max(t, base)) & (cg != g))
            if controles.size == 0:
                continue
            dt_, peso_t = _diferenca_ponderada(Y, W, tratados, t, base)
            dc_, _ = _diferenca_ponderada(Y, W, controles, t, base)
            att = dt_ - dc_
            if np.isfinite(att):
                saida.append((g, t, t - g, att, peso_t, int(tratados.size), int(controles.size)))
    return saida


def att_gt(
    painel: pd.DataFrame,
    *,
    y: str,
    grupo: str,
    periodo: str,
    coorte: str,
    peso: str | None = None,
    ignorar_primeiros: int = 0,
) -> pd.DataFrame:
    """ATT(g, t) com controles "ainda não tratados" (Callaway & Sant'Anna, 2021).

    - pós-tratamento (t >= g): diferença longa contra a base g-1;
    - pré-tratamento (t < g): base variável t-1 (serve de teste de tendências);
    - controles: grupos com coorte > max(t, base) ou que nunca tratam;
    - médias ponderadas por ``peso`` (ex.: domicílios), quando informado.
    """
    Y, W, cg, rotulos, _ = _matrizes(painel, y=y, grupo=grupo, periodo=periodo, coorte=coorte, peso=peso)
    linhas = _att_gt_matriz(Y, W, cg, ignorar_primeiros=ignorar_primeiros)
    return pd.DataFrame(
        [{"coorte": rotulos[g], "periodo": rotulos[t], "e": e, "att": a, "peso": w, "n_tratados": nt, "n_controles": nc}
         for g, t, e, a, w, nt, nc in linhas],
        columns=["coorte", "periodo", "e", "att", "peso", "n_tratados", "n_controles"],
    )


def _agregar_linhas(linhas: list[tuple], janela: tuple[int, int] | None) -> tuple[float, dict[int, float]]:
    if not linhas:
        return float("nan"), {}
    arr = np.array([(e, a, w) for _, _, e, a, w, _, _ in linhas], dtype=float)
    pos = arr[arr[:, 0] >= 0]
    simples = float(np.average(pos[:, 1], weights=pos[:, 2])) if pos.size and pos[:, 2].sum() > 0 else float("nan")
    por_e: dict[int, float] = {}
    for e in np.unique(arr[:, 0]).astype(int):
        if janela and not (janela[0] <= e <= janela[1]):
            continue
        d = arr[arr[:, 0] == e]
        por_e[int(e)] = float(np.average(d[:, 1], weights=d[:, 2])) if d[:, 2].sum() > 0 else float(d[:, 1].mean())
    return simples, por_e


def agregar_att(tabela: pd.DataFrame, tipo: str = "simples", janela: tuple[int, int] | None = None) -> pd.DataFrame | float:
    """Agrega ATT(g,t): 'simples' (média pós ponderada) ou 'evento' (por tempo relativo e)."""
    if tabela.empty:
        return float("nan") if tipo == "simples" else pd.DataFrame(columns=["e", "att", "n_coortes"])
    zeros = [0] * len(tabela)
    linhas = list(zip(zeros, zeros, tabela["e"], tabela["att"], tabela["peso"].clip(lower=0), zeros, zeros))
    simples, por_e = _agregar_linhas(linhas, janela)
    if tipo == "simples":
        return simples
    contagem = tabela.groupby("e").size()
    return pd.DataFrame([{"e": e, "att": v, "n_coortes": int(contagem.get(e, 0))} for e, v in sorted(por_e.items())])


def did_escalonado(
    painel: pd.DataFrame,
    *,
    y: str,
    grupo: str,
    periodo: str,
    coorte: str,
    peso: str | None = None,
    janela_evento: tuple[int, int] = (-6, 12),
    n_boot: int = 499,
    seed: int = 42,
    confianca: float = 0.95,
    ignorar_primeiros: int = 0,
) -> dict[str, Any]:
    """Efeito médio da ação (ATT) com IC por bootstrap de grupos e estudo de evento.

    O bootstrap reamostra grupos inteiros (ex.: cidades), respeitando a correlação
    dentro do grupo. Também testa tendências antes da ação: as estimativas com
    e < 0 deveriam ser próximas de zero (sup-t com o próprio bootstrap), e informa a
    menor tendência que o teste detectaria (``mde``): "não rejeitar" sem poder não
    confirma a premissa.

    ``avisos``: poucos grupos (< 20) ou coortes pequenas (< 5 grupos) deixam o IC e o
    teste prévio menos confiáveis (medido: cobertura de 91,7% e 9% de rejeição a 5%
    com 3 grupos por coorte); grupos sempre tratados saem da conta.
    """
    info: dict[str, Any] = {}
    Y, W, cg, rotulos, grupos = _matrizes(painel, y=y, grupo=grupo, periodo=periodo, coorte=coorte, peso=peso, info=info)
    linhas = _att_gt_matriz(Y, W, cg, ignorar_primeiros=ignorar_primeiros)
    if not linhas:
        raise ValueError("não há pares tratado-controle suficientes para estimar o efeito")
    att, por_e = _agregar_linhas(linhas, janela_evento)
    rng = np.random.default_rng(seed)
    G = len(grupos)
    reps_att: list[float] = []
    reps_ev: list[dict[int, float]] = []
    for _ in range(n_boot):
        idx = rng.integers(0, G, G)
        b = _att_gt_matriz(Y[idx], W[idx], cg[idx], ignorar_primeiros=ignorar_primeiros)
        if not b:
            continue
        s_, e_ = _agregar_linhas(b, janela_evento)
        reps_att.append(s_)
        reps_ev.append(e_)
    alfa = 1 - confianca
    reps = np.array([r for r in reps_att if np.isfinite(r)])
    suficiente = reps.size >= min(20, max(5, n_boot // 2))
    ic = (float(np.quantile(reps, alfa / 2)), float(np.quantile(reps, 1 - alfa / 2))) if suficiente else (float("nan"), float("nan"))
    ep = float(np.std(reps, ddof=1)) if suficiente else float("nan")
    p_valor = float(2 * stats.norm.sf(abs(att / ep))) if suficiente and ep > 0 else float("nan")
    ev_reps = pd.DataFrame(reps_ev)
    evento = pd.DataFrame([{"e": e, "att": v} for e, v in sorted(por_e.items())])
    contagem = pd.Series([l[2] for l in linhas]).value_counts()
    if not evento.empty:
        evento["n_coortes"] = [int(contagem.get(e, 0)) for e in evento["e"]]
        evento["ic_inf"] = [float(ev_reps[e].quantile(alfa / 2)) if e in ev_reps else np.nan for e in evento["e"]]
        evento["ic_sup"] = [float(ev_reps[e].quantile(1 - alfa / 2)) if e in ev_reps else np.nan for e in evento["e"]]
    # Tendências antes da ação: teste sup-t com o próprio bootstrap (faixa uniforme).
    # Medido em simulação: o Wald com qui-quadrado rejeita ~20% a 5% nominal com poucos
    # grupos por coorte; o sup-t e o BH por período ficam calibrados.
    pre_e = [e for e in (evento["e"] if not evento.empty else []) if e < 0 and e in ev_reps.columns]
    teste_pre: dict[str, Any] = {"metodo": "sup-t bootstrap", "estatistica": float("nan"), "gl": 0, "p_valor": float("nan"),
                                 "periodos_significativos": [], "mde": float("nan"), "poder_ok": None}
    if pre_e:
        R = ev_reps[pre_e].dropna()
        if len(R) >= 20:
            b = evento.set_index("e").loc[pre_e, "att"].to_numpy()
            dp = R.std(ddof=1).to_numpy()
            dp = np.where(dp > 0, dp, np.nan)
            t_obs = float(np.nanmax(np.abs(b) / dp))
            centrado = R.to_numpy() - R.to_numpy().mean(axis=0)
            t_boot = np.nanmax(np.abs(centrado) / dp, axis=1)
            p_ind = [2 * min(float(np.mean(R[e] <= 0)), float(np.mean(R[e] >= 0))) for e in pre_e]
            ajust = bh_ajuste(p_ind)
            # Menor desvio por período que o teste pegaria com 80% de poder (2,8 erros-padrão):
            # se ele for grande perto do efeito, "passar" no teste prévio não diz muito.
            mde = float(2.8 * np.nanmedian(dp))
            poder_ok = bool(np.isfinite(mde) and np.isfinite(att) and abs(att) > 0 and mde <= 0.5 * abs(att))
            teste_pre = {
                "metodo": "sup-t bootstrap", "estatistica": t_obs, "gl": len(pre_e),
                "p_valor": float(np.mean(t_boot >= t_obs)),
                "periodos_significativos": [int(e) for e, q in zip(pre_e, ajust) if q < 0.05],
                "mde": mde, "poder_ok": poder_ok,
            }
    tabela = pd.DataFrame(
        [{"coorte": rotulos[g], "periodo": rotulos[t], "e": e, "att": a, "peso": w_, "n_tratados": nt, "n_controles": nc}
         for g, t, e, a, w_, nt, nc in linhas]
    )
    avisos: list[str] = []
    por_coorte = pd.Series(cg[np.isfinite(cg)]).value_counts()
    if G < 20:
        avisos.append(f"só {G} grupos: o intervalo e o teste de tendências ficam menos confiáveis (prefira 20 ou mais).")
    pequenas = [rotulos[int(c)] for c, n in por_coorte.items() if n < 5]
    if pequenas:
        avisos.append(f"{len(pequenas)} coorte(s) com menos de 5 grupos: o efeito delas pesa com pouca informação.")
    if info.get("sempre_tratados"):
        avisos.append(f"{len(info['sempre_tratados'])} grupo(s) já tratados antes do primeiro período ficaram fora "
                      "(sem período antes da ação para comparar).")
    return {
        "att": float(att), "ic_inf": ic[0], "ic_sup": ic[1], "ep": ep, "p_valor": p_valor, "n_boot": int(reps.size),
        "att_gt": tabela, "evento": evento, "teste_pre": teste_pre,
        "n_grupos": int(G), "n_coortes": int(len(np.unique(cg[np.isfinite(cg)]))),
        "grupos_sempre_tratados": info.get("sempre_tratados", []), "avisos": avisos,
    }


def placebo_temporal(
    painel: pd.DataFrame, *, y: str, grupo: str, periodo: str, coorte: str, peso: str | None = None,
    deslocamento: int = 6, n_boot: int = 299, seed: int = 7,
    att_referencia: float | None = None, margem_equivalencia: float = 0.5,
) -> dict[str, Any]:
    """Finge que a ação aconteceu ``deslocamento`` períodos antes, usando só dados pré-ação.

    Um efeito "placebo" diferente de zero indica que o método está captando outra coisa.

    - ``passou``: o IC do placebo contém zero (não achou efeito onde não houve ação);
    - ``equivalente``: o IC inteiro cabe em ±``margem_equivalencia`` × |efeito principal|
      (``att_referencia``). Um IC largo também "contém zero" e não confirma nada: só a
      equivalência mostra que o placebo teria visto um efeito do tamanho do estimado.
      ``None`` quando não há efeito de referência.
    """
    _, rotulos = _indices_de_periodo(painel[periodo])
    mapa = {v: i for i, v in enumerate(rotulos)}
    df = painel.copy()
    real = df[coorte].map(lambda c: mapa.get(c, np.nan) if pd.notna(c) else np.nan)
    t = df[periodo].map(mapa)
    df = df[real.isna() | (t < real)].copy()
    falso = (real - deslocamento).loc[df.index]
    df["_coorte_falsa"] = [rotulos[int(f)] if pd.notna(f) and 0 < f < len(rotulos) else pd.NaT for f in falso]
    if df.groupby(grupo)["_coorte_falsa"].first().notna().sum() == 0:
        raise ValueError("não há períodos antes da ação suficientes para o placebo")
    r = did_escalonado(df, y=y, grupo=grupo, periodo=periodo, coorte="_coorte_falsa", peso=peso,
                       n_boot=n_boot, seed=seed, janela_evento=(-3, deslocamento))
    passou = bool(np.isfinite(r["ic_inf"]) and r["ic_inf"] <= 0 <= r["ic_sup"])
    equivalente = None
    if att_referencia is not None and np.isfinite(att_referencia) and att_referencia != 0 and np.isfinite(r["ic_inf"]):
        limite = margem_equivalencia * abs(float(att_referencia))
        equivalente = bool(-limite <= r["ic_inf"] and r["ic_sup"] <= limite)
    return {"att_placebo": r["att"], "ic_inf": r["ic_inf"], "ic_sup": r["ic_sup"], "p_valor": r["p_valor"],
            "passou": passou, "equivalente": equivalente, "margem_equivalencia": margem_equivalencia,
            "deslocamento": deslocamento}


def pico_pre_tratamento(
    painel: pd.DataFrame, *, y: str, grupo: str, periodo: str, coorte: str, peso: str | None = None,
    janela_base: int = 6, meses_pico: int = 3, n_boot: int = 499, seed: int = 11, confianca: float = 0.95,
) -> pd.DataFrame:
    """Variação do desfecho nos ``meses_pico`` períodos logo antes da ação, contra a janela anterior.

    A janela de base termina antes dos meses do pico (senão o próprio pico dilui a base).

    Compara cada coorte com os grupos ainda não tratados no mesmo intervalo e dá
    um IC por bootstrap de grupos para a diferença. Um pico só nos tratados, com
    IC acima de zero, sugere seleção por pico (regressão à média).
    """
    Y, W, cg, rotulos, _ = _matrizes(painel, y=y, grupo=grupo, periodo=periodo, coorte=coorte, peso=peso)
    rng = np.random.default_rng(seed)
    alfa = 1 - confianca
    linhas = []
    for g in np.unique(cg[np.isfinite(cg)]).astype(int):
        inicio_pico = g - meses_pico
        if inicio_pico - janela_base < 0:
            # sem histórico para a base: a coorte fica na tabela, sem medida (não some calada)
            linhas.append({"coorte": rotulos[g], "n_grupos": int((cg == g).sum()), "variacao_tratados": np.nan,
                           "variacao_controles": np.nan, "diferenca": np.nan, "ic_inf": np.nan, "ic_sup": np.nan,
                           "observacao": f"menos de {janela_base + meses_pico} períodos antes da ação"})
            continue
        janela = list(range(inicio_pico - janela_base, inicio_pico))
        recentes = list(range(inicio_pico, g))

        def variacao(idx: np.ndarray) -> float:
            if idx.size == 0:
                return float("nan")
            antes = np.nanmean([_media_ponderada(Y, W, idx, c) for c in janela])
            ultimo = np.nanmean([_media_ponderada(Y, W, idx, c) for c in recentes])
            return float(ultimo / antes - 1) if antes else float("nan")

        tratados = np.flatnonzero(cg == g)
        controles = np.flatnonzero(cg > g)
        vt, vc = variacao(tratados), variacao(controles)
        reps = []
        if tratados.size and controles.size:
            for _ in range(n_boot):
                bt = rng.choice(tratados, tratados.size, replace=True)
                bc = rng.choice(controles, controles.size, replace=True)
                reps.append(variacao(bt) - variacao(bc))
        reps_ok = np.array([r for r in reps if np.isfinite(r)])
        ic = (float(np.quantile(reps_ok, alfa / 2)), float(np.quantile(reps_ok, 1 - alfa / 2))) if reps_ok.size >= 20 else (float("nan"), float("nan"))
        linhas.append({"coorte": rotulos[g], "n_grupos": int(tratados.size), "variacao_tratados": vt,
                       "variacao_controles": vc, "diferenca": vt - vc, "ic_inf": ic[0], "ic_sup": ic[1], "observacao": ""})
    return pd.DataFrame(linhas, columns=["coorte", "n_grupos", "variacao_tratados", "variacao_controles", "diferenca", "ic_inf",
                                         "ic_sup", "observacao"])


# --- modelos -------------------------------------------------------------------------------------


def metricas_classificacao(y: Sequence[int], p: Sequence[float]) -> dict[str, float]:
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

    yy = np.asarray(y, dtype=int)
    pp = np.asarray(p, dtype=float)
    if len(np.unique(yy)) < 2:
        return {"auc": float("nan"), "pr_auc": float("nan"), "brier": float(brier_score_loss(yy, pp)), "taxa_base": float(yy.mean())}
    return {
        "auc": float(roc_auc_score(yy, pp)),
        "pr_auc": float(average_precision_score(yy, pp)),
        "brier": float(brier_score_loss(yy, pp)),
        "taxa_base": float(yy.mean()),
    }


def tabela_calibracao(y: Sequence[int], p: Sequence[float], faixas: int = 10) -> pd.DataFrame:
    df = pd.DataFrame({"y": np.asarray(y, dtype=int), "p": np.asarray(p, dtype=float)})
    df["faixa"] = pd.qcut(df["p"].rank(method="first"), faixas, labels=False)
    return df.groupby("faixa").agg(previsto=("p", "mean"), observado=("y", "mean"), n=("y", "size")).reset_index()


def lift_por_decil(y: Sequence[int], p: Sequence[float]) -> pd.DataFrame:
    df = pd.DataFrame({"y": np.asarray(y, dtype=int), "p": np.asarray(p, dtype=float)})
    df["decil"] = 10 - pd.qcut(df["p"].rank(method="first"), 10, labels=False)
    base = df["y"].mean()
    t = df.groupby("decil").agg(taxa=("y", "mean"), n=("y", "size")).reset_index()
    t["lift"] = t["taxa"] / base if base > 0 else np.nan
    t["captura_acumulada"] = (t["taxa"] * t["n"]).cumsum() / max(1, df["y"].sum())
    return t


def vif(x: pd.DataFrame) -> pd.Series:
    """Fator de inflação da variância por coluna (> 10 indica colinearidade forte)."""
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    m = x.select_dtypes("number").dropna()
    m = m.loc[:, m.std() > 0]
    if m.shape[1] < 2:
        return pd.Series(dtype=float)
    valores = np.column_stack([np.ones(len(m)), m.to_numpy(dtype=float)])
    return pd.Series([variance_inflation_factor(valores, i + 1) for i in range(m.shape[1])], index=m.columns)


# --- séries temporais ----------------------------------------------------------------------------


def sazonal_ingenuo(historico: Sequence[float], horizonte: int, sazonalidade: int) -> np.ndarray:
    h = np.asarray(historico, dtype=float)
    if h.size < sazonalidade:
        return np.repeat(h[-1], horizonte)
    ciclo = h[-sazonalidade:]
    return np.array([ciclo[i % sazonalidade] for i in range(horizonte)])


def suavizacao_exponencial(historico: Sequence[float], horizonte: int, sazonalidade: int | None) -> np.ndarray:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    h = np.asarray(historico, dtype=float)
    sazonal = "add" if sazonalidade and h.size >= 2 * sazonalidade else None
    modelo = ExponentialSmoothing(h, trend="add", damped_trend=True, seasonal=sazonal,
                                  seasonal_periods=sazonalidade if sazonal else None, initialization_method="estimated")
    return np.asarray(modelo.fit(optimized=True).forecast(horizonte), dtype=float)


def mase(real: Sequence[float], previsto: Sequence[float], historico: Sequence[float], sazonalidade: int = 1) -> float:
    r, p, h = (np.asarray(v, dtype=float) for v in (real, previsto, historico))
    m = max(1, sazonalidade)
    escala = np.mean(np.abs(h[m:] - h[:-m])) if h.size > m else float("nan")
    return float(np.mean(np.abs(r - p)) / escala) if escala and escala > 0 else float("nan")


def smape(real: Sequence[float], previsto: Sequence[float]) -> float:
    r, p = np.asarray(real, dtype=float), np.asarray(previsto, dtype=float)
    den = (np.abs(r) + np.abs(p)) / 2
    ok = den > 0
    return float(np.mean(np.abs(r - p)[ok] / den[ok])) if ok.any() else 0.0


def backtest_series(
    serie: Sequence[float], *, horizonte: int, n_origens: int = 4, sazonalidade: int = 12,
    modelos: Sequence[str] = ("sazonal_ingenuo", "suavizacao_exponencial"),
) -> pd.DataFrame:
    """Backtest com origem móvel: cada modelo prevê ``horizonte`` passos a partir de várias origens."""
    y = np.asarray(serie, dtype=float)
    minimo = max(2 * sazonalidade, 8)
    linhas = []
    for k in range(n_origens, 0, -1):
        corte = y.size - k * horizonte
        if corte < minimo:
            continue
        hist, real = y[:corte], y[corte: corte + horizonte]
        for nome in modelos:
            try:
                if nome == "sazonal_ingenuo":
                    prev = sazonal_ingenuo(hist, real.size, sazonalidade)
                elif nome == "suavizacao_exponencial":
                    prev = suavizacao_exponencial(hist, real.size, sazonalidade)
                else:
                    raise ValueError(nome)
            except Exception:  # noqa: BLE001
                continue
            linhas.append({"origem": int(corte), "modelo": nome, "mase": mase(real, prev, hist, sazonalidade),
                           "smape": smape(real, prev), "horizonte": int(real.size)})
    return pd.DataFrame(linhas)


# --- anomalias e BI -------------------------------------------------------------------------------


def z_robusto(x: Sequence[float]) -> np.ndarray:
    """z baseado em mediana e MAD (resistente a outliers). |z| > 3,5 costuma ser anomalia.

    Com MAD zero (mais da metade dos valores iguais, ex.: série quase toda de zeros), a
    escala passa a ser o desvio absoluto médio × 1,2533 (equivale ao desvio-padrão na
    normal): um valor diferente da mediana nunca fica com z = 0.
    """
    v = np.asarray(x, dtype=float)
    med = np.nanmedian(v)
    mad = np.nanmedian(np.abs(v - med))
    if mad and np.isfinite(mad):
        return 0.6745 * (v - med) / mad
    media_abs = np.nanmean(np.abs(v - med))
    if media_abs and np.isfinite(media_abs):
        return (v - med) / (1.2533 * media_abs)
    return np.zeros_like(v)


def decomposicao_mix_taxa(
    antes: pd.DataFrame, depois: pd.DataFrame, *, segmento: str, numerador: str, denominador: str
) -> dict[str, Any]:
    """Separa a variação de uma taxa total em efeito mix (composição) e efeito taxa (desempenho)."""
    a = antes.groupby(segmento)[[numerador, denominador]].sum()
    b = depois.groupby(segmento)[[numerador, denominador]].sum()
    seg = a.index.union(b.index)
    a, b = a.reindex(seg, fill_value=0), b.reindex(seg, fill_value=0)
    pa, pb = a[denominador] / a[denominador].sum(), b[denominador] / b[denominador].sum()
    ta = (a[numerador] / a[denominador]).replace([np.inf, -np.inf], np.nan)
    tb = (b[numerador] / b[denominador]).replace([np.inf, -np.inf], np.nan)
    # Segmento que não existia (ou sumiu) num período: a taxa dele lá é a do outro período.
    # Assim um segmento novo entra inteiro no efeito mix (composição), e a soma continua exata.
    ta, tb = ta.fillna(tb).fillna(0), tb.fillna(ta).fillna(0)
    total_a = a[numerador].sum() / a[denominador].sum()
    total_b = b[numerador].sum() / b[denominador].sum()
    mix = float(((pb - pa) * ta).sum())
    taxa = float((pb * (tb - ta)).sum())
    por = pd.DataFrame({"peso_antes": pa, "peso_depois": pb, "taxa_antes": ta, "taxa_depois": tb,
                        "efeito_mix": (pb - pa) * ta, "efeito_taxa": pb * (tb - ta)})
    return {"total_antes": float(total_a), "total_depois": float(total_b), "variacao": float(total_b - total_a),
            "efeito_mix": mix, "efeito_taxa": taxa, "por_segmento": por.reset_index()}


# --- montagem e qualidade (usados pelos blocos e pelo pipeline do handoff) ----------------------


def montar_painel(
    populacao: pd.DataFrame,
    desfechos: dict[str, pd.DataFrame] | None = None,
    tratamento: pd.DataFrame | None = None,
    *,
    por: int = 1000,
) -> pd.DataFrame:
    """Junta população, desfechos e tratamento num painel grupo × período.

    Cada desfecho vira ``ev_<nome>`` (contagem) e ``taxa_<nome>`` (por ``por`` unidades).
    """
    painel = populacao[["grupo", "periodo", "unidades"]].copy()
    painel["periodo"] = pd.to_datetime(painel["periodo"])
    for nome, d in (desfechos or {}).items():
        dd = d[["grupo", "periodo", "eventos"]].copy()
        dd["periodo"] = pd.to_datetime(dd["periodo"])
        painel = painel.merge(dd.rename(columns={"eventos": f"ev_{nome}"}), on=["grupo", "periodo"], how="left")
        painel[f"ev_{nome}"] = painel[f"ev_{nome}"].fillna(0)
        painel[f"taxa_{nome}"] = painel[f"ev_{nome}"] / painel["unidades"].where(painel["unidades"] > 0) * por
    if tratamento is not None and not tratamento.empty:
        tt = tratamento[["grupo", "periodo", "tratadas"]].copy()
        tt["periodo"] = pd.to_datetime(tt["periodo"])
        painel = painel.merge(tt, on=["grupo", "periodo"], how="left")
        painel["tratadas"] = painel["tratadas"].fillna(0)
        painel["fracao_tratada"] = painel["tratadas"] / painel["unidades"].where(painel["unidades"] > 0)
    return painel.sort_values(["grupo", "periodo"]).reset_index(drop=True)


def perfil_de_contagens(linha: dict[str, Any], colunas: Sequence[str]) -> pd.DataFrame:
    """Transforma COUNT(*)/COUNT(col)/COUNT(DISTINCT col) numa tabela de perfil por coluna."""
    total = float(linha.get("linhas", 0) or 0)
    saida = []
    for i, c in enumerate(colunas):
        preenchidos = float(linha.get(f"nn_{i}", 0) or 0)
        distintos = float(linha.get(f"dist_{i}", 0) or 0)
        saida.append({
            "coluna": c,
            "preenchidos": int(preenchidos),
            "nulos_pct": (1 - preenchidos / total) if total else float("nan"),
            "distintos": int(distintos),
            "unicidade": (distintos / preenchidos) if preenchidos else float("nan"),
        })
    return pd.DataFrame(saida)


def checar_qualidade(perfil: pd.DataFrame, *, limite_nulos: float = 0.2, chaves: Sequence[str] = ()) -> pd.DataFrame:
    """Achados de qualidade a partir do perfil: nulos altos, constantes, chave não única."""
    achados = []
    for _, r in perfil.iterrows():
        if r["nulos_pct"] > limite_nulos:
            achados.append({"coluna": r["coluna"], "achado": "muitos nulos", "valor": float(r["nulos_pct"]), "severidade": "alerta"})
        if r["distintos"] <= 1 and r["preenchidos"] > 0:
            achados.append({"coluna": r["coluna"], "achado": "valor constante", "valor": float(r["distintos"]), "severidade": "nota"})
        if r["coluna"] in chaves and r["unicidade"] < 0.999:
            achados.append({"coluna": r["coluna"], "achado": "chave com repetição", "valor": float(r["unicidade"]), "severidade": "alerta"})
    return pd.DataFrame(achados, columns=["coluna", "achado", "valor", "severidade"])


def estabilidade_categorias(
    contagens: pd.DataFrame, *, periodo: str, categoria: str, n: str, corte: Any, limiar_psi: float = 0.1
) -> dict[str, Any]:
    """A distribuição das categorias (ex.: códigos de motivo) mudou entre antes e depois do corte?"""
    d = contagens.copy()
    d[periodo] = pd.to_datetime(d[periodo])
    antes = d[d[periodo] < pd.to_datetime(corte)].groupby(categoria)[n].sum()
    depois = d[d[periodo] >= pd.to_datetime(corte)].groupby(categoria)[n].sum()
    cats = antes.index.union(depois.index)
    pa = antes.reindex(cats, fill_value=0)
    pb = depois.reindex(cats, fill_value=0)
    valor = psi(pa.values, pb.values) if pa.sum() and pb.sum() else float("nan")
    tabela = pd.DataFrame({"antes": pa / max(pa.sum(), 1), "depois": pb / max(pb.sum(), 1)})
    tabela["variacao_pp"] = (tabela["depois"] - tabela["antes"]) * 100
    novas = [c for c in cats if pa[c] == 0 and pb[c] > 0]
    sumiram = [c for c in cats if pa[c] > 0 and pb[c] == 0]
    return {"psi": float(valor), "estavel": bool(np.isfinite(valor) and valor < limiar_psi), "categorias_novas": novas,
            "categorias_sumiram": sumiram, "tabela": tabela.reset_index().rename(columns={"index": categoria})}


def representatividade(
    universo: dict[str, pd.Series], amostra: pd.DataFrame, *, numericas: dict[str, tuple[float, float]] | None = None,
    pesos: str | None = None,
) -> pd.DataFrame:
    """Compara a amostra com o universo: PSI para categóricas, SMD para numéricas.

    ``universo``: para cada variável categórica, as contagens por categoria no universo.
    ``numericas``: (média, desvio) do universo para variáveis numéricas.
    ``pesos``: coluna com o peso amostral (inverso da fração sorteada em cada estrato).
    Numa amostra estratificada com mínimo por estrato, os estratos pequenos aparecem
    a mais de propósito; sem os pesos, a comparação (e qualquer média) sai torta.
    """
    w = amostra[pesos].astype(float) if pesos and pesos in amostra else pd.Series(1.0, index=amostra.index)
    linhas = []
    for var, contagens in universo.items():
        if var not in amostra:
            continue
        ref = contagens.astype(float)
        ref.index = ref.index.astype(str)
        valor = psi_contagens(ref, w.groupby(amostra[var].astype(str)).sum())
        situacao = "ok" if valor < 0.1 else ("atencao" if valor < 0.25 else "problema")
        linhas.append({"variavel": var, "psi": valor, "smd": None, "situacao": situacao})
    for var, (media, desvio) in (numericas or {}).items():
        if var not in amostra or not desvio:
            continue
        v = float((np.average(amostra[var].astype(float), weights=w) - media) / desvio)
        situacao = "ok" if abs(v) < 0.1 else ("atencao" if abs(v) < 0.2 else "problema")
        linhas.append({"variavel": var, "psi": None, "smd": v, "situacao": situacao})
    return pd.DataFrame(linhas, columns=["variavel", "psi", "smd", "situacao"])


def separar_temporal(df: pd.DataFrame, coluna_data: str, corte: Any) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Treino antes do corte, teste a partir do corte (sem misturar futuro no treino)."""
    datas = pd.to_datetime(df[coluna_data])
    corte = pd.to_datetime(corte)
    return df[datas < corte].copy(), df[datas >= corte].copy()


def separar_por_grupo(df: pd.DataFrame, coluna_grupo: str, fracao_teste: float = 0.25, seed: int = 42):
    """Grupos inteiros (ex.: lojas, cidades) ficam só no treino ou só no teste."""
    rng = np.random.default_rng(seed)
    grupos = np.array(sorted(df[coluna_grupo].astype(str).unique()))
    rng.shuffle(grupos)
    n_teste = max(1, int(round(len(grupos) * fracao_teste)))
    teste = set(grupos[:n_teste])
    marca = df[coluna_grupo].astype(str).isin(teste)
    return df[~marca].copy(), df[marca].copy()


# --- BI ----------------------------------------------------------------------------------------


def calcular_kpis(df: pd.DataFrame, medidas: dict[str, dict[str, str]], dimensoes: Sequence[str]) -> pd.DataFrame:
    """Medidas declaradas: {'nome': {'tipo': 'soma'|'razao', 'numerador': col, 'denominador': col, 'coluna': col}}.

    Razões são calculadas como soma/soma no nível pedido (nunca média de razões).
    """
    agg = df.groupby(list(dimensoes), dropna=False) if dimensoes else df.assign(_t=1).groupby("_t")
    colunas = sorted({c for m in medidas.values() for c in (m.get("coluna"), m.get("numerador"), m.get("denominador")) if c})
    somas = agg[colunas].sum()
    saida = pd.DataFrame(index=somas.index)
    for nome, m in medidas.items():
        if m.get("tipo") == "razao":
            saida[nome] = somas[m["numerador"]] / somas[m["denominador"]].where(somas[m["denominador"]] != 0)
        else:
            saida[nome] = somas[m["coluna"]]
    return saida.reset_index()


def reconciliar(total: pd.DataFrame, partes: pd.DataFrame, colunas: Sequence[str], *, tolerancia: float = 1e-6) -> pd.DataFrame:
    """Confere se a soma das partes bate com o total (medidas aditivas)."""
    linhas = []
    for c in colunas:
        t = float(total[c].sum())
        p = float(partes[c].sum())
        dif = p - t
        linhas.append({"medida": c, "total": t, "soma_partes": p, "diferenca": dif,
                       "bate": bool(abs(dif) <= tolerancia * max(1.0, abs(t)))})
    return pd.DataFrame(linhas)


# --- inferência ----------------------------------------------------------------------------------


def comparar_grupos(
    df: pd.DataFrame, *, grupo: str, medida: str, referencia: Any | None = None, binaria: bool = False, confianca: float = 0.95
) -> pd.DataFrame:
    """Compara cada grupo com a referência; p-valores corrigidos por Benjamini-Hochberg."""
    niveis = [g for g in df[grupo].dropna().unique()]
    if referencia is None:
        referencia = df[grupo].value_counts().idxmax()
    ref = df.loc[df[grupo] == referencia, medida]
    linhas = []
    for g in niveis:
        if g == referencia:
            continue
        x = df.loc[df[grupo] == g, medida]
        if binaria:
            r = teste_proporcoes([int(x.sum()), int(ref.sum())], [int(x.count()), int(ref.count())], confianca=confianca)
            linhas.append({"grupo": g, "referencia": referencia, "estimativa_grupo": r["proporcoes"][0],
                           "estimativa_ref": r["proporcoes"][1], "diferenca": r["diferenca"], "ic_inf": r["ic_inf"],
                           "ic_sup": r["ic_sup"], "p_valor": r["p_valor"], "n": int(x.count())})
        else:
            if x.count() < 2 or ref.count() < 2:
                continue
            # Um só estimando: diferença de MÉDIAS, com IC e p de Welch. Misturar o p do
            # Mann-Whitney (que testa distribuições) com o IC da média fazia p e IC discordarem
            # (medido: 16,8%) e rejeitava médias iguais em 24,6%. O Mann-Whitney fica como coluna informativa.
            r = teste_dois_grupos(x, ref, confianca=confianca)
            linhas.append({"grupo": g, "referencia": referencia, "estimativa_grupo": r["media_a"], "estimativa_ref": r["media_b"],
                           "diferenca": r["diferenca"], "ic_inf": r["ic_inf"], "ic_sup": r["ic_sup"], "p_valor": r["p_welch"],
                           "p_mann_whitney": r["p_mann_whitney"], "n": int(x.count()), "teste": "welch",
                           "aproximadamente_normal": r["aproximadamente_normal"], "hedges_g": r["hedges_g"]})
    t = pd.DataFrame(linhas)
    if not t.empty:
        t["p_ajustado_bh"] = bh_ajuste(t["p_valor"].fillna(1).to_numpy())
        t["significativo"] = t["p_ajustado_bh"] < (1 - confianca)
    return t


# --- regressão e classificação ----------------------------------------------------------------------


def _matriz_x(df: pd.DataFrame, x: Sequence[str]) -> pd.DataFrame:
    m = pd.get_dummies(df[list(x)], drop_first=True, dtype=float)
    return m.loc[:, m.std() > 0] if len(m) > 1 else m


def _codificador(treino: pd.DataFrame, x: Sequence[str]) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """Codificação aprendida SÓ no treino e aplicada igual em qualquer outro conjunto.

    Codificar o teste sozinho troca a categoria de referência (drop_first) e as colunas
    constantes: o modelo lê colunas trocadas (medido: AUC de 0,83 para 0,36). Categoria
    que só aparece fora do treino vira a de referência (todas as indicadoras em 0).
    """
    categoricas = [c for c in x if not (pd.api.types.is_numeric_dtype(treino[c]) or pd.api.types.is_bool_dtype(treino[c]))]
    niveis = {c: sorted(treino[c].dropna().astype(str).unique()) for c in categoricas}

    def bruta(df: pd.DataFrame) -> pd.DataFrame:
        d = df[list(x)].copy()
        for c, cats in niveis.items():
            d[c] = pd.Categorical(d[c].astype(str).where(d[c].notna()), categories=cats)
        return pd.get_dummies(d, drop_first=True, dtype=float)

    m = bruta(treino)
    colunas = list(m.columns[m.std() > 0]) if len(m) > 1 else list(m.columns)

    def aplicar(df: pd.DataFrame) -> pd.DataFrame:
        return bruta(df).reindex(columns=colunas, fill_value=0.0).astype(float)

    return aplicar


def ajustar_ols(
    df: pd.DataFrame, *, y: str, x: Sequence[str], cluster: str | None = None, pesos: str | None = None
) -> dict[str, Any]:
    """Regressão linear com erro-padrão robusto (HC3) ou por cluster, e diagnósticos."""
    import statsmodels.api as sm

    dados = df[[y, *x] + ([cluster] if cluster else []) + ([pesos] if pesos else [])].dropna()
    X = sm.add_constant(_matriz_x(dados, x), has_constant="add")
    Y = dados[y].astype(float)
    modelo = sm.WLS(Y, X, weights=dados[pesos]) if pesos else sm.OLS(Y, X)
    avisos: list[str] = []
    # use_t: t de Student (no cluster, com G−1 graus de liberdade). Com z, o IC por cluster cobria
    # 80% com 8 clusters e ~90% com 20 (medido).
    if cluster:
        codigos = dados[cluster].astype("category").cat.codes
        n_clusters = int(codigos.nunique())
        ajuste = modelo.fit(cov_type="cluster", cov_kwds={"groups": codigos}, use_t=True)
        if n_clusters < 20:
            avisos.append(f"só {n_clusters} clusters: o erro-padrão por cluster fica otimista; leia o IC com cautela.")
    else:
        ajuste = modelo.fit(cov_type="HC3", use_t=True)
        if len(dados) < 100:
            avisos.append(f"n = {len(dados)}: o erro-padrão robusto (HC3) pode ficar curto com heterocedasticidade forte.")
    ic = ajuste.conf_int()
    coef = pd.DataFrame({"termo": ajuste.params.index, "coef": ajuste.params.values, "ep": ajuste.bse.values,
                         "ic_inf": ic[0].values, "ic_sup": ic[1].values, "p_valor": ajuste.pvalues.values})
    residuos = ajuste.resid
    from statsmodels.stats.diagnostic import het_breuschpagan

    try:
        bp_p = float(het_breuschpagan(residuos, X)[1])
    except Exception:  # noqa: BLE001
        bp_p = float("nan")
    return {"coeficientes": coef, "r2": float(ajuste.rsquared), "r2_ajustado": float(ajuste.rsquared_adj), "n": int(ajuste.nobs),
            "vif": vif(X.drop(columns="const")), "heterocedasticidade_p": bp_p,
            "erro_padrao": "cluster" if cluster else "HC3", "avisos": avisos}


def ajustar_gbm_regressao(df: pd.DataFrame, *, y: str, x: Sequence[str], folds: int = 5, seed: int = 42) -> dict[str, Any]:
    """Boosting (não linear) contra baseline (média), com validação cruzada e importância por permutação."""
    from sklearn.dummy import DummyRegressor
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.inspection import permutation_importance
    from sklearn.model_selection import KFold, cross_val_score

    from sklearn.model_selection import train_test_split

    dados = df[[y, *x]].dropna()
    X = _matriz_x(dados, x)
    Y = dados[y].astype(float)
    cv = KFold(folds, shuffle=True, random_state=seed)
    modelo = HistGradientBoostingRegressor(random_state=seed, max_iter=300, early_stopping=True)
    base = DummyRegressor()
    mae_m = -cross_val_score(modelo, X, Y, cv=cv, scoring="neg_mean_absolute_error")
    mae_b = -cross_val_score(base, X, Y, cv=cv, scoring="neg_mean_absolute_error")
    r2 = cross_val_score(modelo, X, Y, cv=cv, scoring="r2")
    # Incerteza do ganho: espalhamento entre as dobras (t com k−1 graus de liberdade).
    ganhos = 1 - mae_m / np.where(mae_b > 0, mae_b, np.nan)
    ganho = float(1 - mae_m.mean() / mae_b.mean()) if mae_b.mean() else float("nan")
    ic_ganho = (float("nan"), float("nan"))
    if np.isfinite(ganhos).sum() >= 3:
        g_ = ganhos[np.isfinite(ganhos)]
        meia = stats.t.ppf(0.975, g_.size - 1) * g_.std(ddof=1) / math.sqrt(g_.size)
        ic_ganho = (float(g_.mean() - meia), float(g_.mean() + meia))
    # Importância por permutação num conjunto que o modelo não viu (no treino, ela premia o que foi decorado).
    Xtr, Xte, Ytr, Yte = train_test_split(X, Y, test_size=0.25, random_state=seed)
    modelo.fit(Xtr, Ytr)
    imp = permutation_importance(modelo, Xte, Yte, n_repeats=5, random_state=seed)
    importancia = pd.DataFrame({"variavel": X.columns, "importancia": imp.importances_mean}).sort_values("importancia", ascending=False)
    modelo.fit(X, Y)
    return {"mae_modelo": float(mae_m.mean()), "mae_baseline": float(mae_b.mean()), "r2_cv": float(r2.mean()),
            "ganho_sobre_baseline": ganho, "ganho_ic_inf": ic_ganho[0], "ganho_ic_sup": ic_ganho[1],
            "importancia": importancia, "importancia_em": "teste separado (25%)", "n": int(len(dados))}


def ajustar_classificador(
    treino: pd.DataFrame, teste: pd.DataFrame, *, y: str, x: Sequence[str], seed: int = 42
) -> dict[str, Any]:
    """Baseline (taxa base), logística e boosting, avaliados num teste separado."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    tr = treino[[y, *x]].dropna()
    te = teste[[y, *x]].dropna()
    codificar = _codificador(tr, x)
    Xtr, Xte = codificar(tr), codificar(te)
    ytr, yte = tr[y].astype(int), te[y].astype(int)
    resultados = {"baseline": {"auc": 0.5, "pr_auc": float(yte.mean()), "brier": float(((yte - ytr.mean()) ** 2).mean()), "taxa_base": float(yte.mean())}}
    previsoes = {}
    modelos = {
        "logistica": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
        "boosting": HistGradientBoostingClassifier(random_state=seed, max_iter=300, early_stopping=True),
    }
    for nome, m in modelos.items():
        m.fit(Xtr, ytr)
        p = m.predict_proba(Xte)[:, 1]
        previsoes[nome] = p
        resultados[nome] = metricas_classificacao(yte, p)
    melhor = max(modelos, key=lambda n: resultados[n]["auc"] if np.isfinite(resultados[n]["auc"]) else -1)
    return {"metricas": pd.DataFrame(resultados).T.reset_index().rename(columns={"index": "modelo"}),
            "melhor": melhor, "calibracao": tabela_calibracao(yte, previsoes[melhor]),
            "lift": lift_por_decil(yte, previsoes[melhor]), "n_treino": int(len(tr)), "n_teste": int(len(te))}


def _auc_simetrica(alvo: pd.Series, v: pd.Series) -> float:
    from sklearn.metrics import roc_auc_score

    if alvo.nunique() < 2 or v.nunique() < 2:
        return float("nan")
    auc = float(roc_auc_score(alvo, v.astype(float)))
    return max(auc, 1 - auc)


def _codificacao_fora_da_dobra(v: pd.Series, alvo: pd.Series, folds: int = 5, seed: int = 42) -> pd.Series:
    """Média do alvo por categoria calculada nas OUTRAS dobras (categoria rara não "decora" o alvo)."""
    rng = np.random.default_rng(seed)
    dobra = rng.integers(0, folds, len(v))
    v = v.astype(str).to_numpy()
    a = alvo.to_numpy(dtype=float)
    saida = np.empty(len(v))
    for k in range(folds):
        fora = dobra != k
        medias = pd.Series(a[fora]).groupby(v[fora]).mean()
        geral = a[fora].mean() if fora.any() else a.mean()
        saida[~fora] = pd.Series(v[~fora]).map(medias).fillna(geral).to_numpy()
    return pd.Series(saida, index=alvo.index)


def checar_vazamento(df: pd.DataFrame, *, y: str, x: Sequence[str], limiar_auc: float = 0.95) -> pd.DataFrame:
    """Variáveis que sozinhas preveem o alvo bem demais costumam vir do futuro (vazamento).

    Cada variável é olhada sozinha, nas linhas em que o alvo existe (um ``dropna`` conjunto
    escondia o vazamento clássico: campo preenchido só nos positivos). Duas medidas:
    - ``auc_valor``: o próprio valor (categóricas com a média do alvo calculada fora da
      dobra, para que milhares de categorias raras não pareçam vazamento);
    - ``auc_nulos``: só saber se o campo está preenchido ou não.
    ``auc_isolada`` é a maior das duas.
    """
    colunas = ["variavel", "auc_isolada", "auc_valor", "auc_nulos", "suspeita", "motivo"]
    dados = df[df[y].notna()]
    alvo = dados[y].astype(int)
    if alvo.nunique() < 2:
        return pd.DataFrame(columns=colunas)
    linhas = []
    for c in x:
        v = dados[c]
        preenchido = v.notna()
        auc_nulos = _auc_simetrica(alvo, preenchido.astype(float)) if not preenchido.all() else float("nan")
        auc_valor = float("nan")
        if preenchido.sum() >= 10 and alvo[preenchido].nunique() == 2:
            vv = v[preenchido]
            if not (pd.api.types.is_numeric_dtype(vv) or pd.api.types.is_bool_dtype(vv)):
                vv = _codificacao_fora_da_dobra(vv, alvo[preenchido])
            try:
                auc_valor = _auc_simetrica(alvo[preenchido], vv.astype(float))
            except ValueError:
                pass
        medidas = [m for m in (auc_valor, auc_nulos) if np.isfinite(m)]
        if not medidas:
            continue
        auc = max(medidas)
        motivo = "preenchimento" if np.isfinite(auc_nulos) and auc_nulos >= auc else "valor"
        linhas.append({"variavel": c, "auc_isolada": float(auc), "auc_valor": auc_valor, "auc_nulos": auc_nulos,
                       "suspeita": bool(auc >= limiar_auc), "motivo": motivo})
    return pd.DataFrame(linhas, columns=colunas).sort_values("auc_isolada", ascending=False)


def segmentar_kmeans(df: pd.DataFrame, *, x: Sequence[str], k_min: int = 2, k_max: int = 8, seed: int = 42) -> dict[str, Any]:
    """K-médias com variáveis padronizadas; escolhe k pela silhueta."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    dados = df[list(x)].dropna()
    Z = StandardScaler().fit_transform(_matriz_x(dados, x))
    amostra = min(len(Z), 5000)
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(Z), amostra, replace=False)
    notas = {}
    for k in range(k_min, min(k_max, len(Z) - 1) + 1):
        rotulos = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(Z)
        notas[k] = float(silhouette_score(Z[idx], rotulos[idx]))
    melhor = max(notas, key=notas.get)
    rotulos = KMeans(n_clusters=melhor, n_init=10, random_state=seed).fit_predict(Z)
    perfil = dados.assign(segmento=rotulos).groupby("segmento").agg(["mean", "size"])
    return {"k": int(melhor), "silhuetas": notas, "rotulos": rotulos, "perfil": perfil, "indice": dados.index}


# --- séries e anomalias ---------------------------------------------------------------------------


def decompor_stl(serie: pd.Series, sazonalidade: int = 12, *, robusto: bool = True) -> pd.DataFrame:
    from statsmodels.tsa.seasonal import STL

    r = STL(serie.astype(float), period=sazonalidade, robust=robusto).fit()
    return pd.DataFrame({"observado": serie.values, "tendencia": r.trend, "sazonal": r.seasonal, "residuo": r.resid}, index=serie.index)


def _z_huber(s: pd.Series, sazonalidade: int | None) -> np.ndarray:
    """Resíduo de uma regressão robusta (Huber) com tendência linear e mês, dividido pela escala robusta."""
    import statsmodels.api as sm

    t = np.arange(len(s), dtype=float)
    colunas = [t]
    if sazonalidade:
        colunas.append(pd.get_dummies(pd.Series(t.astype(int) % sazonalidade), drop_first=True, dtype=float).to_numpy())
    X = sm.add_constant(np.column_stack(colunas))
    ajuste = sm.RLM(s.to_numpy(dtype=float), X, M=sm.robust.norms.HuberT()).fit()
    escala = float(ajuste.scale)
    return ajuste.resid / escala if escala > 0 else z_robusto(ajuste.resid)


def anomalias_serie(serie: pd.Series, *, sazonalidade: int | None = 12, limiar: float = 4.0, metodo: str = "huber") -> pd.DataFrame:
    """Anomalias pelo z do resíduo depois de tirar tendência e sazonalidade.

    ``metodo="huber"`` (padrão): regressão robusta com tendência + mês. Medido em 300
    séries de 48 pontos, limiar 4: 0,17 alarme falso por série e 91% dos picos de 6
    desvios detectados, contra 0,20 e 63% do STL não robusto (``metodo="stl"``). O STL
    robusto ajusta tão de perto que o z dispara em pontos normais (~13 alarmes por série).
    Sem histórico para a sazonalidade, só a tendência sai; com poucos pontos, z robusto direto.
    """
    s = serie.astype(float)
    com_sazonal = bool(sazonalidade) and len(s) >= 2 * int(sazonalidade) + 1
    z = None
    if metodo == "huber" and len(s) >= 8:
        try:
            z = _z_huber(s, sazonalidade if com_sazonal else None)
        except Exception:  # noqa: BLE001 - matriz singular etc.: cai para o STL / z direto
            z = None
    if z is None:
        base = decompor_stl(s, sazonalidade, robusto=False)["residuo"].to_numpy() if com_sazonal else s.to_numpy()
        z = z_robusto(base)
    return pd.DataFrame({"valor": s.values, "z_robusto": z, "anomalia": np.abs(z) > limiar}, index=s.index)


def anomalias_multivariadas(df: pd.DataFrame, *, x: Sequence[str], contaminacao: float = 0.01, seed: int = 42) -> pd.Series:
    from sklearn.ensemble import IsolationForest

    dados = _matriz_x(df[list(x)].dropna(), x)
    modelo = IsolationForest(contamination=contaminacao, random_state=seed)
    return pd.Series(-modelo.fit(dados).score_samples(dados), index=dados.index, name="pontuacao_anomalia")


# --- funções de pipeline usadas pelo handoff (mesma lógica dos blocos) ------------------------


def comparar_proporcoes_agregadas(
    agregado: pd.DataFrame, *, grupo: str = "grupo", sucessos: str = "sucessos", total: str = "total",
    referencia: Any | None = None, confianca: float = 0.95,
) -> pd.DataFrame:
    """Cada grupo contra a referência a partir de contagens agregadas; BH nos p-valores."""
    if referencia is None:
        referencia = agregado.sort_values(total).iloc[-1][grupo]
    ref = agregado[agregado[grupo].astype(str) == str(referencia)].iloc[0]
    linhas = []
    for _, g in agregado.iterrows():
        if str(g[grupo]) == str(referencia):
            continue
        t = teste_proporcoes([int(g[sucessos]), int(ref[sucessos])], [int(g[total]), int(ref[total])], confianca=confianca)
        linhas.append({"grupo": g[grupo], "referencia": referencia, "estimativa_grupo": t["proporcoes"][0],
                       "estimativa_ref": t["proporcoes"][1], "diferenca": t["diferenca"], "ic_inf": t["ic_inf"],
                       "ic_sup": t["ic_sup"], "p_valor": t["p_valor"], "n": int(g[total])})
    tabela = pd.DataFrame(linhas)
    if not tabela.empty:
        tabela["p_ajustado_bh"] = bh_ajuste(tabela["p_valor"].to_numpy())
        tabela["significativo"] = tabela["p_ajustado_bh"] < (1 - confianca)
    return tabela


def remover_tendencia_coorte(
    painel: pd.DataFrame, *, y: str, grupo: str = "grupo", periodo: str = "periodo", coorte: str = "coorte",
    peso: str | None = None, minimo_pre: int = 3,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Tira de cada coorte a tendência linear que ela já tinha, antes da ação, em relação aos controles.

    Para cada coorte g: a diferença (média da coorte − média da referência) em cada
    período antes de g é ajustada por uma reta; a inclinação, prolongada a partir de
    g−1, sai do desfecho dos grupos da coorte em todos os períodos. Uma tendência
    diferencial (que o teste prévio, sem poder, deixa passar) deixa de virar "efeito".
    Referência: os grupos que nunca recebem a ação (2 ou mais); sem eles, os ainda
    não tratados — que podem ter a mesma tendência das coortes futuras e subestimar a
    inclinação. Devolve o painel ajustado e a inclinação por coorte (coortes com menos
    de ``minimo_pre`` períodos antes ficam sem ajuste).
    """
    idx, rotulos = _indices_de_periodo(painel[periodo])
    mapa = {v: i for i, v in enumerate(rotulos)}
    d = painel.copy()
    d["_t"] = idx.to_numpy()
    d["_cg"] = d[coorte].map(lambda c: mapa.get(c, np.nan) if pd.notna(c) else np.inf)
    d["_w"] = d[peso].astype(float) if peso else 1.0

    def media(x: pd.DataFrame) -> float:
        w = x["_w"].where(np.isfinite(x[y]), 0.0)
        return float((x[y].fillna(0) * w).sum() / w.sum()) if w.sum() > 0 else float("nan")

    nunca = d.loc[np.isinf(d["_cg"]), grupo].nunique()
    inclinacoes: dict[str, float] = {}
    for g in sorted(c for c in d["_cg"].dropna().unique() if np.isfinite(c)):
        g = int(g)
        pre = [t_ for t_ in range(g) if t_ >= 0]
        difs, ts = [], []
        for t_ in pre:
            em_t = d[d["_t"] == t_]
            referencia = em_t[np.isinf(em_t["_cg"])] if nunca >= 2 else em_t[em_t["_cg"] > t_]
            dt_ = media(em_t[em_t["_cg"] == g]) - media(referencia)
            if np.isfinite(dt_):
                difs.append(dt_)
                ts.append(t_)
        if len(ts) < minimo_pre:
            continue
        b = float(np.polyfit(ts, difs, 1)[0])
        inclinacoes[str(rotulos[g])] = b
        linhas = d["_cg"] == g
        d.loc[linhas, y] = d.loc[linhas, y] - b * (d.loc[linhas, "_t"] - (g - 1))
    return d.drop(columns=["_t", "_cg", "_w"]), inclinacoes


def cenarios_did(
    painel: pd.DataFrame, *, y: str, cenarios: Sequence[str], excluir_grupos: Sequence[str] = (), base: dict[str, Any] | None = None,
    n_boot: int = 199, seed: int = 42, grupo: str = "grupo", periodo: str = "periodo", coorte: str = "coorte",
    peso: str | None = "unidades", ignorar_primeiros: int = 0, janela_evento: tuple[int, int] | None = None,
) -> pd.DataFrame:
    """Efeito em cenários de sensibilidade.

    ``peso``, ``ignorar_primeiros`` e ``janela_evento`` são os da análise principal e valem
    como BASE de todos os cenários; cada cenário muda só o que ele testa:

    - ``ignorar_primeiro``: ignora pelo menos o 1º período depois da ação;
    - ``sem_ponderacao``: médias sem peso;
    - ``excluir_grupos``: tira os grupos indicados;
    - ``tendencia_coorte``: tira de cada coorte a tendência linear que ela já tinha antes
      da ação (:func:`remover_tendencia_coorte`). É o cenário que ataca tendências
      diferenciais e pico, que os outros não atacam.

    ``contem_principal``: o IC do cenário contém o efeito principal (mais exigente que
    "mesmo sinal").
    """
    variacoes = {
        "ignorar_primeiro": dict(ignorar_primeiros=max(int(ignorar_primeiros), 1)),
        "sem_ponderacao": dict(peso=None),
        "excluir_grupos": dict(excluir=list(excluir_grupos)),
        "tendencia_coorte": dict(tendencia=True),
    }
    extra = {"janela_evento": tuple(janela_evento)} if janela_evento is not None else {}
    linhas = []
    if base is not None:
        linhas.append({"cenario": "principal", "att": base["att"], "ic_inf": base["ic_inf"], "ic_sup": base["ic_sup"],
                       "contem_principal": True})
    for nome in cenarios:
        if nome not in variacoes or (nome == "excluir_grupos" and not excluir_grupos):
            continue
        v = variacoes[nome]
        p = painel[~painel[grupo].astype(str).isin([str(x) for x in v.get("excluir", [])])] if v.get("excluir") else painel
        peso_c = v.get("peso", peso) if "peso" in v else peso
        if v.get("tendencia"):
            p, _ = remover_tendencia_coorte(p, y=y, grupo=grupo, periodo=periodo, coorte=coorte, peso=peso_c)
        r = did_escalonado(p, y=y, grupo=grupo, periodo=periodo, coorte=coorte, peso=peso_c,
                           n_boot=n_boot, seed=seed, ignorar_primeiros=v.get("ignorar_primeiros", ignorar_primeiros), **extra)
        contem = None
        if base is not None and np.isfinite(r["ic_inf"]):
            contem = bool(r["ic_inf"] <= base["att"] <= r["ic_sup"])
        linhas.append({"cenario": nome, "att": r["att"], "ic_inf": r["ic_inf"], "ic_sup": r["ic_sup"], "contem_principal": contem})
    return pd.DataFrame(linhas, columns=["cenario", "att", "ic_inf", "ic_sup", "contem_principal"])


def sensibilidade_estavel(tabela) -> bool:
    """Mesmo sinal em todos os cenários e, quando a tabela traz, o IC de cada cenário contém o efeito principal."""
    sinais = tabela["att"].dropna().map(lambda v: v > 0)
    estavel = bool(len(sinais) and sinais.nunique() <= 1)
    if "contem_principal" in tabela:
        estavel = estavel and bool(tabela["contem_principal"].fillna(True).astype(bool).all())
    return estavel


def anomalias_por_grupo(
    painel: pd.DataFrame, *, y: str, grupo: str = "grupo", periodo: str = "periodo", unidades: str = "unidades",
    sazonalidade: int | None = 12, limiar: float = 4.0,
) -> pd.DataFrame:
    """Períodos fora do padrão em cada grupo (z robusto do resíduo)."""
    achados = []
    for g, d in painel.groupby(grupo):
        s = d.set_index(periodo)[y].sort_index()
        if s.notna().sum() < 6:
            continue
        a = anomalias_serie(s.fillna(s.median()), sazonalidade=sazonalidade, limiar=limiar)
        for per, linha in a[a["anomalia"]].iterrows():
            achados.append({"grupo": g, "periodo": per, "valor": linha["valor"], "z_robusto": linha["z_robusto"],
                            "unidades": int(d.set_index(periodo).loc[per, unidades]) if unidades in d else None})
    return pd.DataFrame(achados, columns=["grupo", "periodo", "valor", "z_robusto", "unidades"])


def serie_total(painel: pd.DataFrame, *, desfecho: str, medida: str = "eventos", por: int = 1000) -> pd.Series:
    g = painel.groupby("periodo")
    if medida == "taxa":
        return (g[f"ev_{desfecho}"].sum() / g["unidades"].sum() * por).sort_index()
    return g[f"ev_{desfecho}"].sum().sort_index()


def estabilidade_janelas(contagens: pd.DataFrame, *, categoria: str = "codigo", janela: str = "janela", n: str = "n",
                         limiar_psi: float = 0.1, pode_ter_supressao: bool = True) -> dict[str, Any]:
    """Estabilidade entre as janelas 'antes' e 'depois' (contagens já agregadas na fonte).

    Com ``pode_ter_supressao`` (padrão: os dados vieram com grupo mínimo), uma
    categoria ausente num dos lados é **indeterminada** — pode ser zero ou célula
    cortada — e fica fora do PSI. Sem dados dos dois lados: 'inconclusivo', nunca 'mudou'.
    """
    d = contagens.copy()
    d[categoria] = d[categoria].astype(str)
    antes = d[d[janela] == "antes"].groupby(categoria)[n].sum()
    depois = d[d[janela] == "depois"].groupby(categoria)[n].sum()
    cats = antes.index.union(depois.index)
    pa, pb = antes.reindex(cats, fill_value=0), depois.reindex(cats, fill_value=0)
    so_um_lado = [c for c in cats if (pa[c] == 0) != (pb[c] == 0)]
    comuns = [c for c in cats if pa[c] > 0 and pb[c] > 0] if pode_ter_supressao else list(cats)
    vazio = {"psi": float("nan"), "estavel": None, "situacao": "inconclusivo", "categorias_novas": [], "categorias_sumiram": [],
             "categorias_indeterminadas": so_um_lado if pode_ter_supressao else [],
             "tabela": pd.DataFrame(columns=[categoria, "antes", "depois", "variacao_pp"])}
    if not comuns or pa[comuns].sum() == 0 or pb[comuns].sum() == 0:
        return vazio
    a_, b_ = pa[comuns], pb[comuns]
    valor = psi(a_.values, b_.values)
    tabela = pd.DataFrame({"antes": a_ / a_.sum(), "depois": b_ / b_.sum()})
    tabela["variacao_pp"] = (tabela["depois"] - tabela["antes"]) * 100
    estavel = bool(valor < limiar_psi)
    novas = [] if pode_ter_supressao else [c for c in cats if pa[c] == 0 and pb[c] > 0]
    sumiram = [] if pode_ter_supressao else [c for c in cats if pa[c] > 0 and pb[c] == 0]
    return {"psi": float(valor), "estavel": estavel, "situacao": "estavel" if estavel else "mudou",
            "categorias_novas": novas, "categorias_sumiram": sumiram,
            "categorias_indeterminadas": so_um_lado if pode_ter_supressao else [],
            "tabela": tabela.reset_index().rename(columns={"index": categoria})}
