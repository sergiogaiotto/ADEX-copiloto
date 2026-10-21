"""Regressões da revisão estatística (cada teste reproduz um achado medido em simulação)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adex_copiloto.blocos import estatistica as est
from adex_copiloto.critico.checagens import EntradaCritica, checar, limite_do_efeito, reavaliar_selos, robustez_do_efeito
from adex_copiloto.estudo.carta import Carta
from adex_copiloto.estudo.evidencia import Afirmacao, Registro, calcular_selo


def _painel(rng, *, G=40, T=24, perda=False, pesos_crescendo=0.0, tendencia_dif=0.0, efeito=0.0):
    coortes = np.array([8] * 10 + [12] * 10 + [16] * 10 + [np.inf] * 10)[:G]
    nivel = rng.normal(10, 3, G)
    linhas = []
    for g in range(G):
        e = rng.normal(0, 0.5)
        tratado = np.isfinite(coortes[g])
        for t in range(T):
            e = 0.5 * e + rng.normal(0, 0.5)
            y = nivel[g] + 0.1 * t + 0.05 * t * (nivel[g] > 10) + e
            y += tendencia_dif * t * tratado
            if tratado and t >= coortes[g]:
                y += efeito
            un = 1000.0 * ((1 + pesos_crescendo) ** t if (pesos_crescendo and tratado and nivel[g] > 10) else 1.0)
            if perda and nivel[g] > 11 and rng.random() < 0.15 + 0.02 * t:
                continue  # célula cortada (ex.: grupo mínimo), mais nos grupos de nível alto
            linhas.append({"grupo": f"G{g:02d}", "periodo": t, "y": y, "unidades": un,
                           "coorte": coortes[g] if tratado else np.nan})
    return pd.DataFrame(linhas)


_ARGS = dict(y="y", grupo="grupo", periodo="periodo", coorte="coorte", peso="unidades")


def _vies(reps=25, **kw):
    rng = np.random.default_rng(11)
    return float(np.mean([est.agregar_att(est.att_gt(_painel(rng, **kw), **_ARGS)) for _ in range(reps)]))


# --- 1 e 2: painel desbalanceado e pesos que mudam no tempo ------------------------------------


def test_did_sem_vies_com_celulas_cortadas():
    # antes da correção: média em t e na base com grupos diferentes (viés −2,15 no relatório)
    assert abs(_vies(perda=True)) < 0.15


def test_did_sem_vies_com_pesos_crescendo():
    # antes: +0,455 com 6%/mês (peso de cada período); agora o peso da base vale nos dois períodos
    assert abs(_vies(pesos_crescendo=0.06)) < 0.15


# --- 4 e 5: poucos grupos e coorte antes da janela ------------------------------------------------


def test_did_avisa_poucos_grupos_e_tira_sempre_tratados():
    rng = np.random.default_rng(2)
    p = _painel(rng, G=12, efeito=-1.0)
    p.loc[p["grupo"].isin(["G00", "G01"]), "coorte"] = -3  # tratados antes do 1º período observado
    r = est.did_escalonado(p, **_ARGS, n_boot=60)
    assert set(r["grupos_sempre_tratados"]) == {"G00", "G01"}
    assert any("grupos" in a for a in r["avisos"]) and any("sempre" in a or "antes do primeiro" in a for a in r["avisos"])
    assert r["n_grupos"] == 10


def test_did_coorte_desalinhada_e_erro():
    p = _painel(np.random.default_rng(3), G=12)
    p.loc[p["grupo"] == "G00", "coorte"] = 7.5
    with pytest.raises(ValueError, match="não coincide"):
        est.did_escalonado(p, **_ARGS, n_boot=20)


def test_did_informa_poder_do_teste_previo():
    r = est.did_escalonado(_painel(np.random.default_rng(4), efeito=-2.0), **_ARGS, n_boot=80)
    tp = r["teste_pre"]
    assert np.isfinite(tp["mde"]) and tp["mde"] > 0 and tp["poder_ok"] in (True, False)


# --- 3: selo, placebo e sensibilidade -------------------------------------------------------------


def test_robustez_exige_placebo_e_sensibilidade():
    assert robustez_do_efeito({"placebo_passou": True}) is None  # só um rodou: não é aprovação
    assert robustez_do_efeito({"sensibilidade_estavel": True}) is None
    assert robustez_do_efeito({"placebo_passou": True, "sensibilidade_estavel": True}) is True
    assert robustez_do_efeito({"placebo_passou": True, "sensibilidade_estavel": True, "placebo_equivalente": False}) is None
    assert robustez_do_efeito({"placebo_passou": True, "sensibilidade_estavel": True, "estabilidade_ok": False}) is False


def test_teste_previo_sem_poder_limita_selo_em_b():
    def selo(flags):
        a = Afirmacao(id="F-01", tipo="fato", texto="efeito", selo="B", bloco="did_escalonado@1.0")
        reavaliar_selos([a], flags)
        return a.selo

    ok = {"teste_pre_p": 0.4, "placebo_passou": True, "sensibilidade_estavel": True}
    assert selo(ok) == "B"  # "não rejeitou" sem poder reportado não confirma a premissa
    assert selo({**ok, "teste_pre_poder_ok": True}) == "A"
    assert selo({**ok, "teste_pre_poder_ok": True, "poucos_grupos": True}) == "B"
    assert limite_do_efeito({"teste_pre_p": 0.4, "teste_pre_poder_ok": True}) is None
    assert calcular_selo(base="censo", incerteza=True, premissas_ok=True, robustez_ok=True, limite="B") == "B"


def test_placebo_reprovado_vira_alerta_aceitavel():
    carta = Carta.model_validate({"tipo_pergunta": "efeito", "campos": {}, "status": "aprovada"})
    e = EntradaCritica(carta=carta, plano=None, resultados={"placebo": {"bloco": "placebo@1.0", "status": "feito"},
                                                          "efeito": {"bloco": "did_escalonado@1.0", "status": "feito"},
                                                          "pre": {"bloco": "estudo_evento_pre@1.0", "status": "feito"}},
                       afirmacoes=[], decisoes=[], lacunas=[], passaporte=None, flags={"placebo_passou": False})
    achado = next(a for a in checar(e, Registro()) if a.regra == "placebo_falhou")
    assert achado.severidade == "alerta" and achado.aceitavel


def test_placebo_por_equivalencia():
    p = _painel(np.random.default_rng(5), efeito=-2.0)
    r = est.placebo_temporal(p, **_ARGS, deslocamento=4, n_boot=80, att_referencia=-2.0)
    assert r["equivalente"] in (True, False) and "passou" in r
    assert est.placebo_temporal(p, **_ARGS, deslocamento=4, n_boot=40)["equivalente"] is None


def test_cenario_tendencia_coorte_desfaz_tendencia_diferencial():
    rng = np.random.default_rng(6)
    principal, sem_tendencia = [], []
    for _ in range(4):
        p = _painel(rng, tendencia_dif=0.08)  # efeito verdadeiro 0, tendência diferencial nos tratados
        t = est.cenarios_did(p, y="y", cenarios=["tendencia_coorte"], n_boot=40, peso="unidades",
                             base=est.did_escalonado(p, **_ARGS, n_boot=40)).set_index("cenario")
        principal.append(t.loc["principal", "att"])
        sem_tendencia.append(t.loc["tendencia_coorte", "att"])
    assert np.mean(principal) > 0.3  # o DiD puro vê "efeito"
    assert abs(np.mean(sem_tendencia)) < 0.5 * np.mean(principal)


def test_cenarios_usam_a_base_da_analise_principal():
    p = _painel(np.random.default_rng(7), efeito=-1.0)
    t = est.cenarios_did(p, y="y", cenarios=["sem_ponderacao", "ignorar_primeiro"], n_boot=30, peso=None, ignorar_primeiros=2,
                         janela_evento=(-3, 6))
    assert list(t["cenario"]) == ["sem_ponderacao", "ignorar_primeiro"] and "contem_principal" in t


# --- 8 e 9: classificação e vazamento --------------------------------------------------------------


def test_classificador_codifica_teste_com_colunas_do_treino():
    rng = np.random.default_rng(8)
    n = 3000
    cat = rng.choice(["a", "b", "c"], n)
    y = (rng.uniform(size=n) < np.where(cat == "a", 0.15, np.where(cat == "b", 0.45, 0.75))).astype(int)
    df = pd.DataFrame({"cat": cat, "x": rng.normal(0, 1, n), "y": y})
    treino = df.iloc[:2000]
    teste = df.iloc[2000:][lambda d: d["cat"] != "a"]  # o teste não tem a categoria de referência
    m = est.ajustar_classificador(treino, teste, y="y", x=["cat", "x"])["metricas"].set_index("modelo")
    assert m.loc["logistica", "auc"] > 0.6  # antes: 0,36 (colunas trocadas)


def test_vazamento_por_preenchimento_e_sem_falso_positivo_de_alta_cardinalidade():
    rng = np.random.default_rng(9)
    n = 3000
    y = rng.integers(0, 2, n)
    so_positivos = np.where(y == 1, rng.normal(0, 1, n), np.nan)  # campo só preenchido depois do evento
    df = pd.DataFrame({"y": y, "so_positivos": so_positivos, "id_qualquer": [f"k{i}" for i in range(n)],
                       "x": rng.normal(0, 1, n)})
    v = est.checar_vazamento(df, y="y", x=["so_positivos", "id_qualquer", "x"]).set_index("variavel")
    assert v.loc["so_positivos", "suspeita"] and v.loc["so_positivos", "motivo"] == "preenchimento"
    assert not v.loc["id_qualquer", "suspeita"]  # antes: AUC 0,97 (a categoria "decorava" o alvo)
    assert not v.loc["x", "suspeita"]


# --- 10 e 11: comparação de grupos e OLS -----------------------------------------------------------


def test_comparar_grupos_p_e_ic_do_mesmo_estimando():
    rng = np.random.default_rng(10)
    concorda = []
    for _ in range(40):
        df = pd.DataFrame({"g": ["ref"] * 20 + ["a"] * 20, "y": np.r_[rng.normal(1, 0.3, 20), rng.exponential(1, 20)]})
        t = est.comparar_grupos(df, grupo="g", medida="y", referencia="ref").iloc[0]
        concorda.append((t["p_valor"] < 0.05) == (t["ic_inf"] > 0 or t["ic_sup"] < 0))
        assert t["teste"] == "welch"
    assert all(concorda)


def test_ols_t_de_student_e_aviso_com_poucos_clusters():
    rng = np.random.default_rng(11)
    df = pd.DataFrame({"x": rng.normal(0, 1, 160), "c": np.repeat(np.arange(8), 20)})
    df["y"] = df["x"] + rng.normal(0, 1, 160)
    r = est.ajustar_ols(df, y="y", x=["x"], cluster="c")
    assert any("8 clusters" in a for a in r["avisos"])


# --- 12 a 17 ---------------------------------------------------------------------------------------


def test_z_robusto_com_mad_zero():
    z = est.z_robusto([0] * 47 + [40])
    assert z[-1] > 4 and (z[:-1] == 0).all()


def test_anomalias_huber_acha_pico():
    rng = np.random.default_rng(12)
    t = np.arange(48)
    s = pd.Series(100 + 0.3 * t + 8 * np.sin(2 * np.pi * t / 12) + rng.normal(0, 2, 48))
    s[30] += 14
    a = est.anomalias_serie(s, sazonalidade=12)
    assert a["anomalia"].iloc[30] and a["anomalia"].sum() <= 2


def test_mix_taxa_segmento_novo_e_efeito_mix():
    antes = pd.DataFrame({"seg": ["a", "b"], "num": [10, 20], "den": [100, 100]})
    depois = pd.DataFrame({"seg": ["a", "b", "novo"], "num": [10, 20, 60], "den": [100, 100, 200]})
    r = est.decomposicao_mix_taxa(antes, depois, segmento="seg", numerador="num", denominador="den")
    assert r["efeito_taxa"] == pytest.approx(0.0, abs=1e-12)  # as taxas antigas não mudaram
    assert r["efeito_mix"] == pytest.approx(r["variacao"])


def test_psi_nao_depende_da_escala():
    a, b = np.array([50, 30, 20, 0]), np.array([40, 30, 20, 10])
    assert est.psi(a * 100, b * 100) == pytest.approx(est.psi(a * 1_000_000, b * 1_000_000))
    assert est.psi(a / a.sum(), b / b.sum()) == pytest.approx(est.psi(a, b))


def test_representatividade_com_peso_amostral():
    universo = {"estrato": pd.Series({"grande": 9000, "pequeno": 1000})}
    amostra = pd.DataFrame({"estrato": ["grande"] * 200 + ["pequeno"] * 200,
                            "peso_amostral": [45.0] * 200 + [5.0] * 200})
    sem = est.representatividade(universo, amostra).iloc[0]["psi"]
    com = est.representatividade(universo, amostra, pesos="peso_amostral").iloc[0]["psi"]
    assert sem > 0.25 and com < 0.01


def test_gbm_ganho_com_ic_e_importancia_fora_do_treino():
    rng = np.random.default_rng(13)
    df = pd.DataFrame({"x1": rng.normal(0, 1, 600), "x2": rng.normal(0, 1, 600)})
    df["y"] = 2 * df["x1"] + rng.normal(0, 1, 600)
    g = est.ajustar_gbm_regressao(df, y="y", x=["x1", "x2"], folds=3)
    assert g["ganho_ic_inf"] <= g["ganho_sobre_baseline"] <= g["ganho_ic_sup"]
    assert g["importancia_em"].startswith("teste") and g["importancia"].iloc[0]["variavel"] == "x1"
