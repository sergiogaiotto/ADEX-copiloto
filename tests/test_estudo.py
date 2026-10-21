from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from adex_copiloto.erros import IntegridadeErro
from adex_copiloto.estudo.carta import (
    NOMES_CAMPOS,
    CampoProposto,
    PropostaCarta,
    citacao_existe,
    etapas_afetadas,
    montar_carta,
)
from adex_copiloto.estudo.dossie import Dossie
from adex_copiloto.estudo.evidencia import Afirmacao, Decisao, OpcaoDecisao, Registro, calcular_selo, menor_selo
from adex_copiloto.estudo.passaporte import ComparacaoUniverso, Passaporte
from adex_copiloto.estudo.plano import Plano, PassoPlano

TEXTO_DEV = (
    "Decidir se a onda 4 (14 cidades, uns R$ 21 mi) sai em 2027. O comitê decide em 28/11. "
    "Por cidade, em ondas: mar, jun e set/2025. A Engenharia escolheu a ordem, acho que pelas cidades com mais reclamação."
)


def _proposta(**sobrescrever):
    campos = []
    for nome in NOMES_CAMPOS:
        campos.append(CampoProposto(campo=nome, valor=f"valor de {nome}", origem="sugerido"))
    for nome, (origem, cit) in sobrescrever.items():
        idx = NOMES_CAMPOS.index(nome)
        campos[idx] = CampoProposto(campo=nome, valor="x", origem=origem, citacoes=cit)
    return PropostaCarta(campos=campos)


def test_selos():
    assert calcular_selo(base="ia", incerteza=True, premissas_ok=True, robustez_ok=True) == "D"
    assert calcular_selo(base="censo", incerteza=False, premissas_ok=True, robustez_ok=True) == "C"
    assert calcular_selo(base="censo", incerteza=True, premissas_ok=True, robustez_ok=None) == "A"
    assert calcular_selo(base="censo", incerteza=True, premissas_ok=True, robustez_ok=None, exige_robustez=True) == "B"
    assert calcular_selo(base="amostra_probabilistica", incerteza=True, premissas_ok=False, robustez_ok=True) == "C"
    assert calcular_selo(base="amostra_conveniencia", incerteza=True, premissas_ok=True, robustez_ok=True) == "C"
    assert menor_selo("A", "C", "B") == "C"


def test_ia_nao_promove_selo():
    a = Afirmacao(id="H-01", tipo="hipotese", texto="x")
    with pytest.raises(ValueError):
        a.mudar_selo("B", "achei", por="ia")
    a.mudar_selo("B", "padrão nos dados")
    assert a.selo == "B" and a.historico[0].de == "D"


def test_decisao_exige_opcao_e_motivo():
    d = Decisao(id="D-01", titulo="t", pergunta="?", opcoes=[OpcaoDecisao(codigo="a", rotulo="A")])
    with pytest.raises(ValueError):
        d.decidir("z", "porque sim", "dev")
    with pytest.raises(ValueError):
        d.decidir("a", "", "dev")
    d.decidir("a", "dados oficiais", "ana")
    assert d.status == "decidida" and d.opcao_escolhida.rotulo == "A"


def test_registro_de_ids():
    r = Registro()
    assert [r.proximo("H"), r.proximo("H"), r.proximo("D")] == ["H-01", "H-02", "D-01"]


def test_citacoes_conferidas():
    assert citacao_existe("acho que pelas cidades com mais reclamação", [TEXTO_DEV])
    assert citacao_existe("  ACHO que pelas cidades  com mais reclamação. ", [TEXTO_DEV])
    assert not citacao_existe("a ordem foi aleatória", [TEXTO_DEV])


def test_montar_carta_rebaixa_citacao_inventada():
    proposta = _proposta(
        pergunta_decisao=("voce_disse", ["Decidir se a onda 4 (14 cidades, uns R$ 21 mi) sai em 2027"]),
        riscos_conhecidos=("voce_disse", ["a ordem foi sorteada"]),
    )
    carta, avisos = montar_carta(proposta, [TEXTO_DEV], "efeito")
    assert carta.campos["pergunta_decisao"].origem == "voce_disse"
    assert carta.campos["riscos_conhecidos"].origem == "sugerido"
    assert any("Riscos conhecidos" in a for a in avisos)


def test_proposta_exige_dez_campos():
    with pytest.raises(ValidationError):
        PropostaCarta(campos=[CampoProposto(campo="objeto", valor="x", origem="sugerido")] * 10)


def test_versao_nova_ao_editar_carta_aprovada():
    carta, _ = montar_carta(_proposta(), [TEXTO_DEV], "efeito")
    carta.aprovar("ana")
    assert carta.aprovada and carta.versao == 1
    carta.editar("desfecho", "visitas técnicas por 1.000 domicílios", motivo="custo")
    assert carta.versao == 2 and not carta.aprovada
    alterados = carta.campos_alterados_desde(1)
    assert alterados == {"desfecho"}
    assert set(etapas_afetadas(alterados)) == {"descobrir", "universo", "plano", "etapas", "critico"}
    assert etapas_afetadas({"fora_escopo"}) == ["plano", "critico"]


def test_carta_nao_aprova_com_campo_vazio():
    carta, _ = montar_carta(_proposta(), [TEXTO_DEV], "efeito")
    carta.campos["desfecho"].valor = "  "
    with pytest.raises(ValueError):
        carta.aprovar("ana")


def test_passaporte_nao_declara_ausencia_de_vies():
    with pytest.raises(ValidationError):
        Passaporte(id="PA-01", populacao="p", fonte=["T"], janela="j", metodo="aleatoria_simples", riscos_vies=["sem viés"])
    pa = Passaporte(
        id="PA-01", populacao="p", fonte=["T"], janela="j", metodo="aleatoria_simples", riscos_vies=["sem viés aparente"],
        comparacao_universo=[ComparacaoUniverso(variavel="cidade", psi=0.01, situacao="ok")],
    )
    assert pa.riscos_vies
    conv = Passaporte(id="PA-02", populacao="p", fonte=["T"], janela="j", metodo="conveniencia")
    assert any("conveniência" in l for l in conv.limitacoes)
    assert "não avaliado" in conv.riscos_vies[0]


def test_dossie_encadeado_e_detecta_adulteracao(tmp_path):
    d = Dossie(tmp_path / "EST-1")
    d.registrar("estudo_criado", {"objetivo": "x", "contato": "ana@claro.com.br"}, ator="dev")
    d.registrar("decisao", {"id": "D-01"}, ator="dev")
    info = d.artefato("grafico.png", b"\x89PNG...", tipo="image/png", descricao="d")
    assert len(info["sha256"]) == 64
    eventos = list(d.eventos())
    assert len(eventos) == 3 and "ana@claro" not in json.dumps(eventos)
    assert Dossie(tmp_path / "EST-1").verificar()[0] == 3
    linhas = d.caminho.read_text(encoding="utf-8").splitlines()
    adulterado = json.loads(linhas[1])
    adulterado["dados"]["id"] = "D-99"
    linhas[1] = json.dumps(adulterado, ensure_ascii=False)
    d.caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    with pytest.raises(IntegridadeErro):
        Dossie(tmp_path / "EST-1")


def test_plano_add_set_diff():
    plano = Plano(passos=[PassoPlano(id="universo", bloco="painel_agregado", versao="2.3"), PassoPlano(id="efeito", bloco="did_escalonado", versao="1.0")])
    plano.aprovar("ana")
    plano.add_step("codigos_estaveis", "desfecho_estavel@1.0", before="efeito")
    plano.set("efeito", ponderar_por="domicilios")
    assert [p.id for p in plano.passos] == ["universo", "codigos_estaveis", "efeito"]
    assert not plano.aprovado and plano.versao == 2
    diff = plano.diff()
    assert any(d.startswith("+ codigos_estaveis") for d in diff)
    assert any(d.startswith("~ efeito") for d in diff)
    with pytest.raises(KeyError):
        plano.set("inexistente", a=1)
