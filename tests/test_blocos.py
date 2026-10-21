from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from adex_copiloto import exemplos
from adex_copiloto.blocos import REGISTRO, ContextoBloco, rodar_bloco
from adex_copiloto.blocos.registro import RegistroBlocos, bloco_do_dev
from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import BlocoErro
from adex_copiloto.estudo.carta import carta_em_branco
from adex_copiloto.estudo.evidencia import Registro
from adex_copiloto.estudo.mapeamento import validar_mapeamento
from adex_copiloto.estudo.plano import PassoPlano, Plano
from adex_copiloto.llm.marcadores import TabelaMarcadores
from falsos import telemetria_teste


@pytest.fixture(scope="module")
def wifi():
    return exemplos.wifi6(domicilios_por_cidade=(110, 170))


def _ctx(ex, tipo="efeito"):
    cfg = Config()
    fonte = ex.fonte(cfg)
    carta = carta_em_branco(tipo)
    ctx = ContextoBloco(estudo_id="EST-T", carta=carta, config=cfg, telemetria=telemetria_teste(cfg),
                        marcadores=TabelaMarcadores(), ids=Registro(), mapeamento=ex.mapeamento.model_copy(deep=True), fonte=fonte)
    assert validar_mapeamento(ctx.mapeamento, fonte.catalogo()) == []
    return ctx


def _rodar(ctx, nome, **params):
    r = rodar_bloco(REGISTRO.obter(nome), ctx, params, passo=nome)
    assert r.status == "feito", (nome, r.erro, r.avisos)
    for m in r.metricas:  # todo número calculado aparece como marcador, nunca cru
        if isinstance(m.valor, (int, float)) and abs(m.valor) >= 10 and m.texto not in ("—",):
            assert m.texto not in r.resumo.replace(f"⟦{m.id}⟧", ""), (m.texto, r.resumo)
    return r


def test_registro_tem_os_blocos_principais():
    nomes = {f["bloco"] for f in REGISTRO.listar()}
    assert {"painel_agregado", "did_escalonado", "plano_amostral", "classificacao", "medidas_kpi", "backtest_series"} <= nomes
    assert len(REGISTRO) >= 25
    with pytest.raises(BlocoErro):
        REGISTRO.obter("nao_existe")


def test_caso_wifi6_ponta_a_ponta(wifi):
    ctx = _ctx(wifi)
    r = _rodar(ctx, "painel_agregado")
    painel = ctx.dados["painel"]
    assert painel["grupo"].nunique() == 41 and painel["periodo"].nunique() == 33
    assert {"taxa_chamados_wifi", "fracao_tratada"} <= set(painel.columns)
    assert all(c.modo == "agregado" for c in r.consultas)
    r = _rodar(ctx, "definir_tratamento", ondas_declaradas=wifi.ondas_declaradas)
    coortes = ctx.dados["coortes"]
    assert set(coortes["mes"].dropna()) == {"2024-11", "2025-03", "2025-06", "2025-08", "2025-09"}
    assert coortes["coorte"].isna().sum() == 14
    assert len(r.decisoes) == 1 and set(r.decisoes[0].dados["meses"]) == {"2024-11", "2025-08"}
    r = _rodar(ctx, "estudo_evento_pre", n_boot=150)
    pico = ctx.dados["teste_pre"]["pico"].set_index("coorte")
    assert pico.loc[pd.Timestamp("2025-03-01"), "diferenca"] > 0.15  # o pico que motivou a 1ª onda
    assert pico.loc[pd.Timestamp("2025-03-01"), "ic_inf"] > 0
    assert any(a.tipo == "hipotese" for a in r.afirmacoes)
    assert sum("seleção por pico" in t for t in [r.resumo]) == 1 and r.resumo.count("Na coorte") == 1
    r = _rodar(ctx, "did_escalonado", n_boot=150)
    efeito = ctx.dados["efeito"]
    assert efeito["att"] < 0 and efeito["ic_sup"] < 0
    rel = next(m for m in r.metricas if m.nome == "efeito_relativo").valor
    assert -0.40 < rel < -0.10  # verdade: -25% nas ondas de 2025, -5% no piloto antigo
    _rodar(ctx, "placebo", n_boot=120, deslocamento=4)
    r = _rodar(ctx, "sensibilidade", n_boot=120, excluir_grupos=["C27"])
    sens = ctx.dados["sensibilidade"]
    assert len(sens) == 5 and "tendencia_coorte" in set(sens["cenario"])
    # sem tendência diferencial no dado sintético, tirar a tendência por coorte mantém o efeito
    assert sens.set_index("cenario").loc["tendencia_coorte", "att"] < 0
    r = _rodar(ctx, "desfecho_estavel")
    assert ctx.dados["estabilidade"]["psi"] >= 0


def test_universo_qualidade_ligacoes_perfil(wifi):
    ctx = _ctx(wifi)
    _rodar(ctx, "qualidade")
    assert ctx.dados["qualidade"]["repetidos"] == 0
    r = _rodar(ctx, "ligacoes")
    assert (ctx.dados["ligacoes"]["taxa"] > 0.99).all()
    r = _rodar(ctx, "perfilamento", colunas=["ID_DOM", "CPF", "COD_CIDADE", "MES"])
    assert set(ctx.dados["perfil"]["coluna"]) == {"ID_DOM", "CPF", "COD_CIDADE", "MES"}
    r = _rodar(ctx, "painel_agregado")
    assert r.figuras and r.figuras[0].png.startswith(b"\x89PNG")


def test_precondicao_falha_vira_resultado_explicado(wifi):
    ctx = _ctx(wifi)
    r = rodar_bloco(REGISTRO.obter("did_escalonado"), ctx, {}, passo="efeito")
    assert r.status == "falhou" and "painel" in r.erro
    r = rodar_bloco(REGISTRO.obter("did_escalonado"), ctx, {}, passo="efeito", decisao_humana_se_falhar=True)
    assert r.status == "aguarda_decisao" and r.decisoes


def test_parametro_invalido(wifi):
    ctx = _ctx(wifi)
    r = rodar_bloco(REGISTRO.obter("painel_agregado"), ctx, {"desconhecido": 1}, passo="universo")
    assert r.status == "falhou" and "Parâmetros inválidos" in r.erro


def test_caso_churn_classificacao():
    ex = exemplos.churn(clientes=4000)
    ctx = _ctx(ex, "classificacao")
    _rodar(ctx, "painel_agregado", incluir_tratamento=False)
    r = _rodar(ctx, "plano_amostral", margem=0.02)
    pa = ctx.dados["passaporte"]
    assert pa.metodo in ("censo", "aleatoria_simples")
    pa.aprovar("teste")
    r = _rodar(ctx, "amostra_unidades", referencia="2026-06-01", colunas=["UF", "PLANO", "TEMPO_CASA_MESES", "VALOR_FATURA", "CHAMADOS_3M", "ATRASOS_6M"])
    amostra = ctx.dados["amostra"]
    assert "alvo" in amostra and amostra["alvo"].isin([0, 1]).all()
    assert all(c.modo == "detalhe" for c in r.consultas)
    _rodar(ctx, "representatividade")
    _rodar(ctx, "separacao", tipo="aleatoria")
    r = _rodar(ctx, "classificacao", x=["uf", "plano", "tempo_casa_meses", "valor_fatura", "chamados_3m", "atrasos_6m"])
    auc = next(m for m in r.metricas if m.nome == "auc").valor
    assert auc > 0.6


def test_caso_receita_regressao_e_kpis():
    ex = exemplos.receita()
    ctx = _ctx(ex, "regressao")
    r = _rodar(ctx, "medidas_kpi", tabela="VAREJO.VENDAS_LOJA_MES", dimensoes=["REGIAO"],
               medidas=[{"nome": "receita", "tipo": "soma", "coluna": "RECEITA_MIL"},
                        {"nome": "clientes", "tipo": "soma", "coluna": "CLIENTES"},
                        {"nome": "ticket", "tipo": "razao", "numerador": "receita", "denominador": "clientes"}])
    assert r.tabelas["reconciliacao"]["bate"].all()
    df = ex.tabelas["VAREJO.VENDAS_LOJA_MES"]
    ctx.dados["amostra"] = df.rename(columns=str.upper)
    r = _rodar(ctx, "regressao_linear", y="receita_mil", x=["area_m2", "promocoes", "regiao"], cluster="cod_loja")
    coef = ctx.dados["regressao"]["coeficientes"].set_index("termo")
    assert coef.loc["promocoes", "ic_inf"] < 12 < coef.loc["promocoes", "ic_sup"]


def test_validar_plano_e_bloco_do_dev(wifi):
    plano = Plano(passos=[
        PassoPlano(id="universo", bloco="painel_agregado", versao=""),
        PassoPlano(id="efeito", bloco="did_escalonado", versao="", parametros={"n_boot": 150}),
    ])
    problemas = REGISTRO.validar_plano(plano)
    assert any("coortes" in p for p in problemas)
    plano.add_step("tratamento", "definir_tratamento", before="efeito")
    assert REGISTRO.validar_plano(plano) == []
    assert plano.passo("universo").versao == "1.0"

    reg = RegistroBlocos()

    @bloco_do_dev(nome="calendario_minimo", versao="1.0", requer=("painel",), produz=("minimo",), registro=reg)
    def calendario_minimo(ctx, limite: int = 3) -> dict:
        """Grupos com poucos períodos."""
        p = ctx.dados["painel"]
        return {"minimo": p.groupby("grupo").size().loc[lambda s: s < limite]}

    b = reg.obter("calendario_minimo")
    assert b.ficha()["origem"] == "dev" and "limite" in b.ficha()["parametros"]
    ctx = _ctx(wifi)
    ctx.dados["painel"] = pd.DataFrame({"grupo": ["a", "a", "b"], "x": [1, 2, 3]})
    r = rodar_bloco(b, ctx, {"limite": 2}, passo="cal")
    assert r.status == "feito" and list(ctx.dados["minimo"].index) == ["b"]
    with pytest.raises(BlocoErro):
        b.validar_parametros({"limite": "x"})


def test_series_anomalias_e_grupos(wifi):
    ctx = _ctx(wifi, "previsao")
    _rodar(ctx, "painel_agregado")
    r = _rodar(ctx, "backtest_series", horizonte=3, n_origens=3)
    assert set(ctx.dados["backtest"]["resumo"]["modelo"]) >= {"sazonal_ingenuo"}
    ctx.carta.tipo_pergunta = "anomalia"
    r = _rodar(ctx, "anomalias_serie")
    assert {"grupo", "periodo", "z_robusto"} <= set(ctx.dados["anomalias"].columns)
    ctx.carta.tipo_pergunta = "descricao"
    df = wifi.tabelas["CAD.BASE_FIBRA_MENSAL"]
    ctx.dados["amostra"] = df[df["mes"] == df["mes"].max()].assign(
        tem_chamado=lambda d: (d["id_dom"] % 7 == 0).astype(int)
    ).rename(columns=str.upper)
    r = _rodar(ctx, "teste_grupos", origem="amostra", grupo="cod_cidade", medida="tem_chamado", binaria=True)
    assert "p_ajustado_bh" in ctx.dados["comparacao"]
