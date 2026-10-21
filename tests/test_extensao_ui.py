from __future__ import annotations

import base64
import json
import os

import pandas as pd
import pytest

from adex_copiloto import exemplos
from adex_copiloto.api import AdexCopiloto, conferir_endpoint_hub
from adex_copiloto.config.carregador import ConfigEfetiva, EstadoAdmin
from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import AdexErro, FerramentaErro, ImpressaoDivergenteErro, PorteiroRecusouErro
from adex_copiloto.estudo.datas import extrair_meses
from adex_copiloto.extensao.ferramentas import RegistroFerramentas
from adex_copiloto.extensao.io import IO_
from adex_copiloto.extensao.playbooks import Atalhos, aderencia
from adex_copiloto.seguranca.segredos import Segredo, calcular_impressao
from adex_copiloto.ui.explicador import explicar, material_do_erro
from falsos import FakeHub, completion, config_teste, tool_call
from roteiro_wifi import RESPOSTAS_DEV, hub_wifi


@pytest.fixture
def registro(tmp_path):
    return RegistroFerramentas(IO_())


def test_ferramenta_do_dev_passa_nos_contratos(tmp_path, registro):
    planilha = tmp_path / "ondas.csv"
    planilha.write_text("cod,onda\nC01,1\nC02,2\nC99,4\n", encoding="utf-8")
    io = registro.io

    @registro.registrar(name="calendario_ondas", reads=[str(planilha)], returns={"cod": "str", "onda": "int"},
                        exemplo={"cidades": ["C01", "C02", "C99"]})
    def calendario_ondas(cidades: list[str]) -> pd.DataFrame:
        """Onda oficial de cada cidade (planilha da Engenharia)."""
        with io.open(str(planilha)) as f:
            df = pd.read_csv(f)
        return df[df["cod"].isin(cidades)].reset_index(drop=True)

    painel = pd.DataFrame({"grupo": ["C01", "C02"]})
    r = registro.testar("calendario_ondas", painel=painel, coluna_grupo="cod")
    resultado = {t.nome: t.ok for t in r}
    assert resultado["Assinatura tipada vira esquema estrito"] and resultado["Lê só o que declarou"]
    assert resultado["Não escreve em lugar nenhum"] and resultado["Mesmo resultado em 2 execuções"]
    assert resultado["Coerente com os dados do painel"] is None  # C99 não está no painel: aviso, não reprovação
    f = registro.obter("calendario_ondas")
    assert f.aprovada
    assert registro.esquemas_ia()[0]["function"]["parameters"]["properties"]["cidades"]["type"] == "array"
    saida = registro.chamar("calendario_ondas", {"cidades": ["C01"]})
    assert list(saida["cod"]) == ["C01"]
    with pytest.raises(FerramentaErro):
        registro.chamar("calendario_ondas", {"cidades": "C01", "extra": 1})


def test_ferramenta_reprovada_por_leitura_escrita_e_pii(tmp_path, registro):
    segredo = tmp_path / "outra.csv"
    segredo.write_text("a\n1\n", encoding="utf-8")
    io = registro.io

    @registro.registrar(name="le_demais", reads=[str(tmp_path / "permitido.csv")])
    def le_demais() -> pd.DataFrame:
        """Lê o que não declarou."""
        with io.open(str(segredo)) as f:
            return pd.read_csv(f)

    @registro.registrar(name="escreve", reads=[])
    def escreve() -> pd.DataFrame:
        """Tenta gravar."""
        with open(tmp_path / "saida.txt", "w") as f:
            f.write("x")
        return pd.DataFrame({"a": [1]})

    @registro.registrar(name="vaza_email", returns={"responsavel": "str"})
    def vaza_email() -> pd.DataFrame:
        """Devolve e-mail do responsável."""
        return pd.DataFrame({"responsavel": ["maria.souza@claro.com.br"]})

    for nome in ("le_demais", "escreve", "vaza_email"):
        registro.testar(nome)
        assert not registro.obter(nome).aprovada, nome
    assert not (tmp_path / "saida.txt").exists()
    with pytest.raises(FerramentaErro):
        registro.chamar("vaza_email", {})


def test_ferramenta_sem_tipo_e_recusada(registro):
    with pytest.raises(FerramentaErro):
        @registro.registrar(name="sem_tipo")
        def sem_tipo(x):  # noqa: ANN001
            return pd.DataFrame()


def test_atalhos_aderencia_por_regra():
    at = Atalhos()
    assert {"classificacao", "regressao", "series_temporais", "inferencia", "bi_kpis", "anomalias", "perfilamento_qualidade"} <= set(at.playbooks)
    r = {l["atalho"]: l for l in at.ranquear(tipo="efeito", texto="A troca de roteadores valeu a pena?")}
    assert all(not l["serve"] for l in r.values())  # caso do zero: nenhum atalho serve
    r = {l["atalho"]: l for l in at.ranquear(tipo="classificacao", texto="Quem tem mais risco de cancelar?")}
    assert r["classificacao"]["aderencia"] >= 0.5 and at.lente("telecom") is not None


def test_extrair_meses():
    assert extrair_meses("Por cidade, em ondas: mar, jun e set/2025.") == ["2025-03", "2025-06", "2025-09"]
    assert extrair_meses("piloto em nov de 2024 e depois março 2025") == ["2024-11", "2025-03"]


def test_endpoint_do_hub_conferido():
    cfg = Config()
    conferir_endpoint_hub(cfg)  # oficial, sem impressão: aceito
    cfg.hub.base_url = "https://outro-endpoint.exemplo/v1"
    with pytest.raises(ImpressaoDivergenteErro):
        conferir_endpoint_hub(cfg)
    cfg.hub.base_url_salt = "s"
    cfg.hub.base_url_impressao = calcular_impressao("https://outro-endpoint.exemplo/v1", "s")
    conferir_endpoint_hub(cfg)


def test_explicador_sem_valores():
    try:
        senha = "sk-segredo-muito-longo-123456"  # noqa: F841
        raise KeyError("coluna 'cpf_cliente' de joao@x.com")
    except KeyError as e:
        material = material_do_erro(e)
    assert "joao@x.com" not in json.dumps(material)
    p = explicar(PorteiroRecusouErro("recusado", como_resolver="ajuste"))
    assert "Como resolver" in p.html


@pytest.fixture
def adex(tmp_path, monkeypatch):
    monkeypatch.setenv("ADEX_TESTE_CK", base64.b64encode(os.urandom(32)).decode())
    cfg = config_teste()
    cfg.geral.diretorio_estado = str(tmp_path / "estado")
    cfg.checkpoint.chave.fonte, cfg.checkpoint.chave.env = "env", "ADEX_TESTE_CK"
    cfg.agente.deep_nodes = []
    cfg.langfuse.avaliacao.amostragem = 0.0
    caso = exemplos.wifi6(domicilios_por_cidade=(60, 80))
    hub = hub_wifi()
    a = AdexCopiloto(ConfigEfetiva(config=cfg, origens={}, admin=EstadoAdmin()), chave=Segredo("x" * 12, "hub.chave", "t"),
                     http_client=hub.http(), fonte=caso.fonte(cfg))
    a._hub = hub
    return a


def test_paineis_renderizam(adex):
    assert "novo estudo" in adex.start().html
    assert "Configurações" in adex.admin().html and "🔒" in adex.admin().html
    estudo = adex.create_session(objective="A troca de roteadores por Wi-Fi 6 no ano passado valeu a pena?")
    html = estudo.frame()._repr_html_()
    assert "estudo.frame(answers=" in html  # sem widgets, o comando aparece
    carta = estudo.frame(answers=RESPOSTAS_DEV)
    assert "Uma frase sua muda o método" in carta._repr_html_()
    assert "Custos do estudo" in estudo.costs().html
    assert "Portões" in estudo.show().html
    assert "EST-" in repr(estudo)


def test_magic_sql_passa_pelo_porteiro(adex):
    from adex_copiloto.ui.magics import executar_sql

    estudo = adex.create_session(objective="Conhecer a base de domicílios por cidade")
    ns = {}
    df = executar_sql("SELECT b.cod_cidade, COUNT(*) n FROM cad.base_fibra_mensal b GROUP BY b.cod_cidade", destino="df", ns=ns)
    assert len(df) == 41 and "df" in ns
    with pytest.raises(PorteiroRecusouErro):
        executar_sql("SELECT b.cpf FROM cad.base_fibra_mensal b", modo="detalhe")
    assert any(ev["tipo"] == "consulta_dev" for ev in estudo._s.dossie.eventos())


def test_chat_chama_ferramenta_aprovada(adex, tmp_path):
    estudo = adex.create_session(objective="A troca de roteadores por Wi-Fi 6 no ano passado valeu a pena?")

    @adex.tool(name="ondas_oficiais", returns={"cod": "str", "onda": "int"}, exemplo={"cidades": ["C01"]})
    def ondas_oficiais(cidades: list[str]) -> pd.DataFrame:
        """Onda oficial por cidade."""
        return pd.DataFrame({"cod": cidades, "onda": [1] * len(cidades)})

    estudo.tools.test("ondas_oficiais")
    adex._hub.em_seguida(
        completion(None, finish="tool_calls", tool_calls=[tool_call("ondas_oficiais", {"cidades": ["C01", "C02"]}, "t1")]),
        completion("Pela planilha oficial, C01 e C02 estão na onda 1."),
    )
    p = estudo.chat("Confere C01 e C02 nas ondas oficiais")
    assert "onda 1" in p.html and "Resultado de ondas_oficiais" in p.html
    assert any(ev["tipo"] == "ferramenta_chamada" for ev in estudo._s.dossie.eventos())


def test_scaffold_nunca_executa(adex):
    adex._hub.em_seguida(completion("```python\nimport os\nos.remove('x')\n```"))
    p = adex.tools.scaffold("calendario", "ler a planilha")
    assert "os.remove" in p.dados["codigo"] and "nunca executado" in p.html
