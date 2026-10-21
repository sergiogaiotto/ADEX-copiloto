from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adex_copiloto.blocos import estatistica as est


def painel_sintetico(efeito=-0.25, pico=0.0, seed=0, n_grupos=30, n_periodos=24):
    """Painel grupo × mês com coortes em ondas e efeito multiplicativo conhecido."""
    rng = np.random.default_rng(seed)
    periodos = pd.date_range("2024-01-01", periods=n_periodos, freq="MS")
    ondas = {0: periodos[8], 1: periodos[12], 2: periodos[16]}
    linhas = []
    for g in range(n_grupos):
        coorte = ondas.get(g % 4)  # grupos com g % 4 == 3 nunca tratam
        nivel = rng.uniform(20, 40)
        unidades = int(rng.integers(500, 3000))
        for i, p in enumerate(periodos):
            y = nivel + 0.3 * i + rng.normal(0, 0.6)  # tendência comum (paralela em nível)
            if coorte is not None and p >= coorte:
                y *= 1 + efeito
            if coorte is not None and p == coorte - pd.DateOffset(months=1):
                y *= 1 + pico
            linhas.append({"grupo": f"G{g:02d}", "periodo": p, "y": y, "unidades": unidades,
                           "tratadas": unidades if (coorte is not None and p >= coorte) else 0})
    return pd.DataFrame(linhas)


def test_bh_igual_ao_statsmodels():
    from statsmodels.stats.multitest import multipletests

    p = np.array([0.001, 0.01, 0.03, 0.04, 0.2, 0.5])
    assert np.allclose(est.bh_ajuste(p), multipletests(p, method="fdr_bh")[1])


def test_wilson_e_proporcoes():
    lo, hi = est.wilson_ic(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    r = est.teste_proporcoes([120, 80], [1000, 1000])
    assert r["diferenca"] == pytest.approx(0.04) and r["ic_inf"] > 0 and r["p_valor"] < 0.01


def test_dois_grupos():
    rng = np.random.default_rng(3)
    r = est.teste_dois_grupos(rng.normal(10.5, 2, 400), rng.normal(10, 2, 400))
    assert r["ic_inf"] < r["diferenca"] < r["ic_sup"] and r["p_welch"] < 0.01
    assert r["recomendado"] == "welch"


def test_alocacao_estratificada():
    tam = pd.Series({"A": 5000, "B": 3000, "C": 50})
    aloc = est.alocacao_estratificada(tam, 800, minimo=30)
    assert aloc.sum() == 800 and aloc["C"] >= 30 and aloc["C"] <= 50
    ney = est.alocacao_estratificada(tam, 800, desvios=pd.Series({"A": 1, "B": 5, "C": 1}), metodo="neyman")
    assert ney["B"] > aloc["B"]


def test_psi_e_smd():
    rng = np.random.default_rng(1)
    a = rng.normal(0, 1, 5000)
    assert est.psi_numerico(a, rng.normal(0, 1, 5000)) < 0.02
    assert est.psi_numerico(a, rng.normal(1, 1, 5000)) > 0.25
    assert abs(est.smd(rng.normal(0, 1, 1000), rng.normal(0, 1, 1000))) < 0.1
    s1 = pd.Series(["a"] * 50 + ["b"] * 50)
    assert est.psi_categorico(s1, pd.Series(["a"] * 90 + ["b"] * 10)) > 0.25


def test_tamanho_amostra():
    assert est.tamanho_amostra_proporcao(0.03) == 1068
    assert est.tamanho_amostra_proporcao(0.03, populacao=2000) < 1068


def test_coortes_por_limiar():
    p = painel_sintetico()
    c = est.coortes_por_limiar(p, grupo="grupo", periodo="periodo", tratados="tratadas", unidades="unidades")
    assert c["coorte"].isna().sum() == 7  # grupos 3, 7, ..., 27
    assert set(c["coorte"].dropna().dt.month) == {9, 1, 5}


def test_did_recupera_efeito_conhecido():
    p = painel_sintetico(efeito=-0.25, seed=2)
    coortes = est.coortes_por_limiar(p, grupo="grupo", periodo="periodo", tratados="tratadas", unidades="unidades")
    p = p.merge(coortes[["grupo", "coorte"]], on="grupo")
    r = est.did_escalonado(p, y="y", grupo="grupo", periodo="periodo", coorte="coorte", peso="unidades", n_boot=80, seed=1)
    pos = p["coorte"].notna() & (p["periodo"] >= p["coorte"])
    efeito_verdadeiro = (p.loc[pos, "y"] / 0.75 * -0.25).mean()  # média do efeito nos tratados
    efeito_relativo = -0.25 * r["att"] / efeito_verdadeiro
    assert efeito_relativo == pytest.approx(-0.25, abs=0.05)
    assert r["ic_inf"] < r["att"] < r["ic_sup"] < 0
    assert r["teste_pre"]["p_valor"] > 0.05  # sem tendência prévia
    assert r["teste_pre"]["periodos_significativos"] == []
    assert {"e", "att", "ic_inf", "ic_sup"} <= set(r["evento"].columns)
    assert -1 in set(r["evento"]["e"])  # o período logo antes da ação é estimado


def test_teste_de_tendencia_previa_calibrado():
    """Sem tendência prévia, o teste deve rejeitar perto de 5% (aqui: no máximo 3 de 12)."""
    rejeicoes = 0
    for seed in range(10, 22):
        p = painel_sintetico(efeito=-0.25, seed=seed)
        c = est.coortes_por_limiar(p, grupo="grupo", periodo="periodo", tratados="tratadas", unidades="unidades")
        p = p.merge(c[["grupo", "coorte"]], on="grupo")
        r = est.did_escalonado(p, y="y", grupo="grupo", periodo="periodo", coorte="coorte", peso="unidades", n_boot=120, seed=1)
        rejeicoes += r["teste_pre"]["p_valor"] < 0.05
    assert rejeicoes <= 3


def test_teste_de_tendencia_previa_detecta_pico():
    p = painel_sintetico(efeito=-0.25, pico=0.30, seed=2)
    c = est.coortes_por_limiar(p, grupo="grupo", periodo="periodo", tratados="tratadas", unidades="unidades")
    p = p.merge(c[["grupo", "coorte"]], on="grupo")
    r = est.did_escalonado(p, y="y", grupo="grupo", periodo="periodo", coorte="coorte", peso="unidades", n_boot=120, seed=1)
    assert r["teste_pre"]["p_valor"] < 0.05 and -1 in r["teste_pre"]["periodos_significativos"]


def test_pico_pre_tratamento_detecta_regressao_a_media():
    p = painel_sintetico(efeito=0.0, pico=0.30)
    coortes = est.coortes_por_limiar(p, grupo="grupo", periodo="periodo", tratados="tratadas", unidades="unidades")
    p = p.merge(coortes[["grupo", "coorte"]], on="grupo")
    pico = est.pico_pre_tratamento(p, y="y", grupo="grupo", periodo="periodo", coorte="coorte", peso="unidades", meses_pico=1, n_boot=200)
    assert (pico["variacao_tratados"] > 0.2).all()
    assert (pico["ic_inf"] > 0).all()
    assert (pico["variacao_controles"].abs() < 0.1).all()


def test_placebo_passa_sem_efeito_falso():
    p = painel_sintetico(efeito=-0.25)
    coortes = est.coortes_por_limiar(p, grupo="grupo", periodo="periodo", tratados="tratadas", unidades="unidades")
    p = p.merge(coortes[["grupo", "coorte"]], on="grupo")
    r = est.placebo_temporal(p, y="y", grupo="grupo", periodo="periodo", coorte="coorte", peso="unidades", deslocamento=4, n_boot=60)
    assert r["passou"], r


def test_classificacao_metricas_e_lift():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 2000)
    p = np.clip(y * 0.3 + rng.uniform(0, 0.7, 2000), 0, 1)
    m = est.metricas_classificacao(y, p)
    assert 0.6 < m["auc"] <= 1
    lift = est.lift_por_decil(y, p)
    assert lift.iloc[0]["lift"] > 1 and len(lift) == 10
    cal = est.tabela_calibracao(y, p)
    assert len(cal) == 10


def test_series_backtest():
    t = np.arange(60)
    serie = 100 + 0.5 * t + 10 * np.sin(2 * np.pi * t / 12)
    bt = est.backtest_series(serie, horizonte=6, n_origens=3, sazonalidade=12)
    assert set(bt["modelo"]) == {"sazonal_ingenuo", "suavizacao_exponencial"}
    assert (bt["mase"] >= 0).all()


def test_z_robusto_e_decomposicao():
    z = est.z_robusto([10, 11, 9, 10, 50])
    assert abs(z[-1]) > 3.5 and abs(z[0]) < 1
    antes = pd.DataFrame({"seg": ["a", "b"], "num": [10, 40], "den": [100, 200]})
    depois = pd.DataFrame({"seg": ["a", "b"], "num": [30, 20], "den": [200, 100]})
    r = est.decomposicao_mix_taxa(antes, depois, segmento="seg", numerador="num", denominador="den")
    assert r["efeito_mix"] + r["efeito_taxa"] == pytest.approx(r["variacao"])


def test_modulo_e_autossuficiente():
    import ast
    import inspect

    arvore = ast.parse(inspect.getsource(est))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])
    assert importados <= {"__future__", "math", "collections", "typing", "numpy", "pandas", "scipy", "statsmodels", "sklearn"}


def test_montar_painel_e_taxas():
    pop = pd.DataFrame({"grupo": ["A", "A"], "periodo": ["2025-01-01", "2025-02-01"], "unidades": [1000, 2000]})
    ev = pd.DataFrame({"grupo": ["A"], "periodo": ["2025-01-01"], "eventos": [50]})
    tr = pd.DataFrame({"grupo": ["A", "A"], "periodo": ["2025-01-01", "2025-02-01"], "tratadas": [0, 1500]})
    p = est.montar_painel(pop, {"wifi": ev}, tr)
    assert list(p["taxa_wifi"]) == [50.0, 0.0]
    assert list(p["fracao_tratada"]) == [0.0, 0.75]


def test_perfil_e_qualidade():
    perfil = est.perfil_de_contagens({"linhas": 100, "nn_0": 100, "dist_0": 100, "nn_1": 60, "dist_1": 1}, ["ID", "X"])
    q = est.checar_qualidade(perfil, chaves=["ID"])
    assert set(q["achado"]) == {"muitos nulos", "valor constante"}


def test_estabilidade_de_codigos():
    c = pd.DataFrame({"periodo": ["2025-01-01"] * 2 + ["2025-06-01"] * 3, "cod": [1, 2, 1, 2, 3], "n": [50, 50, 30, 30, 40]})
    r = est.estabilidade_categorias(c, periodo="periodo", categoria="cod", n="n", corte="2025-03-01")
    assert r["categorias_novas"] == [3] and not r["estavel"]


def test_representatividade_e_separacoes():
    rng = np.random.default_rng(0)
    amostra = pd.DataFrame({"cidade": rng.choice(["A", "B"], 1000, p=[0.5, 0.5]), "renda": rng.normal(10, 2, 1000)})
    r = est.representatividade({"cidade": pd.Series({"A": 5000, "B": 5000})}, amostra, numericas={"renda": (10, 2)})
    assert set(r["situacao"]) == {"ok"}
    df = pd.DataFrame({"d": pd.date_range("2025-01-01", periods=10, freq="MS"), "g": list("aabbccddee")})
    tr, te = est.separar_temporal(df, "d", "2025-08-01")
    assert len(tr) == 7 and len(te) == 3
    tr, te = est.separar_por_grupo(df, "g", 0.2, seed=1)
    assert set(tr["g"]).isdisjoint(set(te["g"]))


def test_kpis_razao_e_reconciliacao():
    df = pd.DataFrame({"uf": ["SP", "SP", "RJ"], "num": [10, 30, 5], "den": [100, 100, 100]})
    k = est.calcular_kpis(df, {"taxa": {"tipo": "razao", "numerador": "num", "denominador": "den"}}, ["uf"])
    assert k.set_index("uf").loc["SP", "taxa"] == pytest.approx(0.2)  # soma/soma, não média de razões
    total = est.calcular_kpis(df, {"num": {"tipo": "soma", "coluna": "num"}}, [])
    rec = est.reconciliar(total, df, ["num"])
    assert rec["bate"].all()


def test_comparar_grupos_com_bh():
    rng = np.random.default_rng(2)
    df = pd.DataFrame({"g": np.repeat(["ref", "a", "b"], 300), "y": np.r_[rng.normal(0, 1, 300), rng.normal(0.5, 1, 300), rng.normal(0, 1, 300)]})
    t = est.comparar_grupos(df, grupo="g", medida="y", referencia="ref").set_index("grupo")
    assert t.loc["a", "significativo"] and not t.loc["b", "significativo"]
    assert (t["p_ajustado_bh"] >= t["p_valor"]).all()


def test_ols_e_gbm():
    rng = np.random.default_rng(4)
    n = 800
    df = pd.DataFrame({"x1": rng.normal(0, 1, n), "x2": rng.normal(0, 1, n), "c": rng.integers(0, 20, n)})
    df["y"] = 2 * df["x1"] - 1 * df["x2"] + rng.normal(0, 1, n)
    r = est.ajustar_ols(df, y="y", x=["x1", "x2"], cluster="c")
    coef = r["coeficientes"].set_index("termo")
    assert coef.loc["x1", "ic_inf"] < 2 < coef.loc["x1", "ic_sup"] and r["erro_padrao"] == "cluster"
    g = est.ajustar_gbm_regressao(df, y="y", x=["x1", "x2"], folds=3)
    assert g["ganho_sobre_baseline"] > 0.3 and g["importancia"].iloc[0]["variavel"] == "x1"


def test_classificador_e_vazamento():
    rng = np.random.default_rng(5)
    n = 2000
    df = pd.DataFrame({"x": rng.normal(0, 1, n), "ruido": rng.normal(0, 1, n)})
    df["y"] = (rng.uniform(size=n) < 1 / (1 + np.exp(-1.5 * df["x"]))).astype(int)
    df["vazou"] = df["y"] + rng.normal(0, 0.01, n)  # informação do futuro
    tr, te = df.iloc[:1500], df.iloc[1500:]
    r = est.ajustar_classificador(tr, te, y="y", x=["x", "ruido"])
    m = r["metricas"].set_index("modelo")
    assert m.loc["logistica", "auc"] > 0.7 and m.loc["baseline", "auc"] == 0.5
    v = est.checar_vazamento(df, y="y", x=["x", "ruido", "vazou"]).set_index("variavel")
    assert v.loc["vazou", "suspeita"] and not v.loc["x", "suspeita"]


def test_segmentacao_e_anomalias():
    rng = np.random.default_rng(6)
    df = pd.DataFrame({"a": np.r_[rng.normal(0, 0.3, 200), rng.normal(5, 0.3, 200)], "b": np.r_[rng.normal(0, 0.3, 200), rng.normal(5, 0.3, 200)]})
    r = est.segmentar_kmeans(df, x=["a", "b"], k_max=5)
    assert r["k"] == 2
    s = pd.Series(10 + np.sin(np.arange(48) * 2 * np.pi / 12) + rng.normal(0, 0.15, 48))
    s.iloc[30] += 4
    a = est.anomalias_serie(s, sazonalidade=12)
    assert a["anomalia"].iloc[30] and a["anomalia"].sum() <= 3
