from __future__ import annotations

import logging
import pickle
import sys

import pytest

from adex_copiloto.config.modelos import Config, RefSegredo
from adex_copiloto.erros import ImpressaoDivergenteErro, SegredoErro
from adex_copiloto.seguranca import calcular_impressao, confere, contem_pii, ler_segredo, mascarar, mascarar_texto
from adex_copiloto.seguranca.redacao import FiltroRedacao
from adex_copiloto.seguranca.segredos import conferir_url, dpapi_cifrar, dpapi_decifrar


def test_impressao_deterministica():
    a = calcular_impressao("valor-secreto", "salt1")
    assert a == calcular_impressao("valor-secreto", "salt1")
    assert a != calcular_impressao("valor-secreto", "salt2")
    assert confere("valor-secreto", a, "salt1")
    assert not confere("outro", a, "salt1")


def test_ler_segredo_confere(monkeypatch):
    monkeypatch.setenv("X_TESTE", "chave-de-teste-123")
    ref = RefSegredo(fonte="env", env="X_TESTE", salt="s1", impressao=calcular_impressao("chave-de-teste-123", "s1"))
    seg = ler_segredo(ref, "hub.chave", Config())
    assert seg.revelar() == "chave-de-teste-123"
    assert "chave-de-teste" not in repr(seg)
    with pytest.raises(TypeError):
        pickle.dumps(seg)


def test_ler_segredo_divergente(monkeypatch):
    monkeypatch.setenv("X_TESTE", "valor-trocado-999")
    ref = RefSegredo(fonte="env", env="X_TESTE", salt="s1", impressao=calcular_impressao("original", "s1"))
    with pytest.raises(ImpressaoDivergenteErro):
        ler_segredo(ref, "hub.chave", Config())


def test_ler_segredo_sem_impressao(monkeypatch):
    monkeypatch.setenv("X_TESTE", "qualquer-valor")
    with pytest.raises(SegredoErro) as e:
        ler_segredo(RefSegredo(fonte="env", env="X_TESTE"), "hub.chave", Config())
    assert "impressão homologada" in str(e.value)


def test_env_vazia():
    with pytest.raises(SegredoErro):
        ler_segredo(RefSegredo(fonte="env", env="NAO_EXISTE_X"), "x", Config(), exigir_impressao=False)


def test_conferir_url():
    url = "https://langfuse.exemplo.com.br"
    imp = calcular_impressao(url, "s")
    conferir_url(url + "/", imp, "s", "langfuse.base_url")
    with pytest.raises(ImpressaoDivergenteErro):
        conferir_url("https://cloud.langfuse.com", imp, "s", "langfuse.base_url")


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI só no Windows")
def test_dpapi_ida_e_volta():
    assert dpapi_decifrar(dpapi_cifrar(b"abc-123")) == b"abc-123"


def test_mascara_segredo_carregado(monkeypatch):
    monkeypatch.setenv("X_TESTE", "zz-valor-unico-777")
    ref = RefSegredo(fonte="env", env="X_TESTE")
    ler_segredo(ref, "x", Config(), exigir_impressao=False)
    assert "zz-valor-unico-777" not in mascarar_texto("a chave é zz-valor-unico-777, ok")


def test_mascara_padroes():
    texto = (
        "contato joao.silva@empresa.com.br, cpf 529.982.247-25, fone (11) 98765-4321, "
        "token sk-abcdefghijklmnop1234, Bearer abcdefghijklmnopqrstu"
    )
    m = mascarar_texto(texto)
    for vazado in ("joao.silva", "529.982.247-25", "98765-4321", "sk-abcdefghijklmnop1234", "abcdefghijklmnopqrstu"):
        assert vazado not in m, vazado


def test_nao_mascara_numeros_de_negocio():
    texto = "1.353 células, 41 cidades × 33 meses, +34% em 2025, onda 4, M-01"
    assert mascarar_texto(texto) == texto


def test_cpf_sem_pontos_so_se_valido():
    assert "52998224725" not in mascarar_texto("id 52998224725")
    assert "12345678901" in mascarar_texto("id 12345678901")


def test_mascara_estruturas():
    obj = {"a": ["x@y.com", {"b": "ok"}], "n": 3}
    assert mascarar(obj) == {"a": ["▇▇▇", {"b": "ok"}], "n": 3}


def test_contem_pii():
    assert "email" in contem_pii("resp: maria@claro.com.br")
    assert contem_pii("cidade A, onda 2") == []


def test_filtro_logging(caplog):
    logger = logging.getLogger("adex_copiloto.teste")
    logger.addFilter(FiltroRedacao())
    with caplog.at_level(logging.INFO, logger="adex_copiloto.teste"):
        logger.info("enviando para %s", "fulano@x.com")
    assert "fulano@x.com" not in caplog.text
