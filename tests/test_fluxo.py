"""Regressões da revisão adversarial do fluxo do estudo (portões, voltas e reexecuções)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from adex_copiloto.erros import AdexErro, ForaDeOrdemErro
from adex_copiloto.harness import estado as E
from roteiro_wifi import RESPOSTAS_DEV
from test_ponta_a_ponta import _decidir_tudo, adex, caso  # noqa: F401 - fixtures


def _ate_passaporte(adex, caso):
    estudo = adex.create_session(objective=caso.objetivo)
    estudo.frame()
    estudo.frame(answers=RESPOSTAS_DEV).approve()
    estudo.discover()
    _decidir_tudo(estudo)
    estudo.run("universo")
    assert estudo._pendente()[0] == "portao_passaporte"
    return estudo


def _ate_etapas(adex, caso):
    estudo = _ate_passaporte(adex, caso)
    estudo.passport().approve()
    plano = estudo.plan()
    _decidir_tudo(estudo)
    plano.approve()
    assert estudo._pendente()[0] == "aguardar_etapas"
    return estudo


def _no(estudo):
    return estudo._pendente()[0]


# --- 1. a carta não pula o portão do passaporte ----------------------------------------------------------


def test_editar_carta_antes_do_passaporte_volta_ao_passaporte(adex, caso):
    estudo = _ate_passaporte(adex, caso)
    estudo.charter().edit("fora_escopo", "Cancelamento, receita e NPS", reason="ajuste do comitê")
    estudo.charter().approve()
    assert _no(estudo) == "portao_passaporte"
    assert E.passaporte(estudo._estado()).status != "aprovado"


def test_editar_carta_com_o_mesmo_valor_nao_reabre_nada(adex, caso):
    estudo = _ate_passaporte(adex, caso)
    carta = estudo.charter().carta
    portoes = len(estudo._estado().get("portoes") or [])
    estudo.charter().edit("fora_escopo", carta.valor("fora_escopo"))
    assert _no(estudo) == "portao_passaporte"
    assert estudo.charter().carta.versao == carta.versao
    assert len(estudo._estado().get("portoes") or []) == portoes


def test_campo_vazio_na_carta_e_recusado_antes_do_portao(adex, caso):
    estudo = _ate_passaporte(adex, caso)
    with pytest.raises(AdexErro):
        estudo.charter().edit("desfecho", "  ")


# --- 5. mapeamento sem confirmação não monta universo ------------------------------------------------------


def test_mapeamento_editado_depois_do_universo_volta_e_exige_confirmacao(adex, caso):
    estudo = _ate_passaporte(adex, caso)
    estudo.mapping().set()
    assert _no(estudo) == "aguardar_universo"
    assert estudo._estado()["mapeamento"]["status"] == "rascunho"
    with pytest.raises(AdexErro, match="não foi confirmado"):
        estudo.run("universo")
    estudo.confirm_mapping(reason="conferi a versão editada")
    estudo.run("universo")
    assert _no(estudo) == "portao_passaporte"


def test_revisar_o_mapeamento_e_confirmar_depois(adex, caso):
    estudo = adex.create_session(objective=caso.objetivo)
    estudo.frame()
    estudo.frame(answers=RESPOSTAS_DEV).approve()
    estudo.discover()
    d = next(d for d in E.decisoes(estudo._estado()) if d.dados.get("efeito") == "confirmar_mapeamento")
    estudo.decide(d.id, "b", reason="quero revisar antes")
    _decidir_tudo(estudo)
    with pytest.raises(AdexErro, match="não foi confirmado"):
        estudo.run("universo")
    estudo.confirm_mapping(reason="revisado")
    estudo.run("universo")
    assert _no(estudo) == "portao_passaporte"


# --- 4. parâmetro novo não muda o plano aprovado por baixo ----------------------------------------------------


def test_run_com_parametro_gera_versao_nova_do_plano(adex, caso):
    estudo = _ate_etapas(adex, caso)
    v = E.plano(estudo._estado()).versao
    estudo.run("efeito", n_boot=np.int64(150))  # tipo do numpy também é aceito
    plano = E.plano(estudo._estado())
    assert plano.versao == v + 1 and not plano.aprovado
    assert plano.passo("efeito").parametros["n_boot"] == 150
    assert _no(estudo) == "portao_plano"


def test_comando_com_datas_do_pandas_vira_json(adex, caso):
    estudo = adex.create_session(objective=caso.objetivo)
    estudo.frame()
    estudo.frame(answers=RESPOSTAS_DEV).approve()
    estudo.discover()
    _decidir_tudo(estudo)
    estudo.run("universo", ondas=[pd.Timestamp("2025-03-01"), pd.Timestamp("2025-06-01")])
    assert _no(estudo) == "portao_passaporte"


# --- 2, 3, 6, 7: o fluxo inteiro com as voltas que quebravam -------------------------------------------------------


def test_fluxo_com_voltas_reproduz_no_pacote(adex, caso):
    estudo = _ate_passaporte(adex, caso)
    # 6: excluir as cidades com datas fora das ondas (opção b)
    fora = next(d for d in E.decisoes(estudo._estado()) if d.titulo == E.TITULO_DATAS_FORA)
    if fora.status == "aberta":
        estudo.decide(fora.id, "b", reason="tirar as cidades com datas fora das ondas")
    estudo.passport().approve()
    plano = estudo.plan()
    _decidir_tudo(estudo)
    plano.approve()
    estudo.run()
    st = estudo._estado()
    assert st["resultados"]["efeito"]["status"] == "feito"
    excluidos = E.grupos_excluidos(st)
    assert excluidos and st["resultados"]["efeito"]["codigo"].count("isin(") == 1
    # 7: rodar o efeito de novo não duplica a afirmação
    estudo.run("efeito")
    st = estudo._estado()
    efeitos = [a for a in st["afirmacoes"] if a["texto"].startswith("Efeito médio da ação")]
    assert len(efeitos) == 1
    # 2: segunda revisão não trava o aceite
    estudo.review()
    _decidir_tudo(estudo)
    estudo.review()
    estudo.accept(reason="riscos aceitos e registrados")
    assert estudo._estado()["status"] == "aceito"
    # 3: editar o plano depois do aceite volta ao portão do plano e reabre o estudo
    estudo.plan_view().set("placebo", n_boot=150)
    assert _no(estudo) == "portao_plano" and estudo._estado()["status"] == "em_andamento"
    with pytest.raises(ForaDeOrdemErro):
        estudo.export_handoff()
    estudo.plan_view().approve()
    estudo.run()
    estudo.review()
    _decidir_tudo(estudo)
    estudo.accept(reason="plano v novo conferido")
    # 6: o pacote refaz o mesmo número (com as mesmas cidades fora)
    estudo.export_handoff(incluir_dados="agregados")
    info = estudo._estado()["handoff"]
    assert info["verificacao"]["ok"] and info["verificacao"].get("reproduz") is True, info["verificacao"]
    saida = json.loads(info["verificacao"]["saida"])
    assert saida["efeito"]["att"] == pytest.approx(adex_efeito(estudo), rel=1e-6)
    estudo.handoff().approve()
    assert estudo._estado()["status"] == "entregue"
    # depois de entregue, nada muda
    with pytest.raises(ForaDeOrdemErro, match="entregue"):
        estudo.discover()


def adex_efeito(estudo) -> float:
    return float(estudo._s.armazem["efeito"]["att"])


def test_descartado_nao_ressuscita(adex, caso):
    estudo = _ate_etapas(adex, caso)
    estudo.run()
    estudo.review()
    estudo.discard(reason="pergunta mudou")
    with pytest.raises(ForaDeOrdemErro, match="descartado"):
        estudo.charter().edit("fora_escopo", "outra coisa")
    with pytest.raises(ForaDeOrdemErro):
        estudo.run()
