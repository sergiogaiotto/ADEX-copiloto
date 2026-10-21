"""Fluxo completo 'do zero' com o hub falso: da pergunta vaga ao handoff publicado."""

from __future__ import annotations

import base64
import json
import os
import zipfile

import pytest

from adex_copiloto import exemplos
from adex_copiloto.api import AdexCopiloto
from adex_copiloto.config.carregador import ConfigEfetiva, EstadoAdmin
from adex_copiloto.erros import DecisaoPendenteErro, ForaDeOrdemErro, PortaoPendenteErro
from adex_copiloto.seguranca.segredos import Segredo
from falsos import config_teste
from roteiro_wifi import RESPOSTAS_DEV, hub_wifi


@pytest.fixture(scope="module")
def caso():
    return exemplos.wifi6(domicilios_por_cidade=(110, 160))


@pytest.fixture
def adex(tmp_path, monkeypatch, caso):
    monkeypatch.setenv("ADEX_TESTE_CK", base64.b64encode(os.urandom(32)).decode())
    cfg = config_teste()
    cfg.geral.diretorio_estado = str(tmp_path / "estado")
    cfg.checkpoint.chave.fonte, cfg.checkpoint.chave.env = "env", "ADEX_TESTE_CK"
    cfg.agente.deep_nodes = []
    cfg.langfuse.avaliacao.amostragem = 1.0  # exercita o juiz (Qwen)
    efetiva = ConfigEfetiva(config=cfg, origens={}, admin=EstadoAdmin())
    hub = hub_wifi()
    a = AdexCopiloto(efetiva, chave=Segredo("chave-falsa-0001", "hub.chave", "teste"), http_client=hub.http(), fonte=caso.fonte(cfg))
    a._hub = hub
    return a


def _decidir_tudo(estudo, *, evitar=("b",)):
    from adex_copiloto.harness import estado as E

    for d in E.decisoes(estudo._estado()):
        if d.status == "aberta":
            opcao = d.recomendada if d.recomendada and d.recomendada not in evitar else d.opcoes[0].codigo
            if d.titulo.startswith("Risco apontado"):
                opcao = "aceitar"
            estudo.decide(d.id, opcao, reason="decidido no teste ponta a ponta")


def test_do_zero_ate_o_handoff(adex, caso):
    estudo = adex.create_session(objective=caso.objetivo, mode="exploration")
    # nada roda antes do frame
    assert adex._hub.pedidos == []
    p = estudo.frame()
    assert "Antes de abrir qualquer tabela" in p.html and "Atalhos" in p.html
    assert all(not a["serve"] for a in estudo._estado()["atalhos"])  # nenhum atalho serve: do zero
    # fora de ordem: descobrir antes da carta aprovada
    with pytest.raises((PortaoPendenteErro, ForaDeOrdemErro)):
        estudo.discover()
    carta = estudo.frame(answers=RESPOSTAS_DEV)
    c = carta.carta
    assert c.campos["riscos_conhecidos"].origem == "voce_disse"
    assert c.frases_que_mudam_metodo and "custo" in " ".join(estudo._estado()["avisos_carta"])
    carta.approve()
    painel = estudo.discover(hint="acho que tem algo no esquema OPS_RES")
    st = estudo._estado()
    assert st["mapeamento"]["populacao"] == "CAD.BASE_FIBRA_MENSAL"
    assert any("ONDA_WIFI6" in a for a in st["descoberta"]["avisos"])  # nome inventado pela IA foi descartado
    assert any("sem acesso" in l.lower() or "Sem acesso" in l for l in st["descoberta"]["lacunas"])
    codigos = st["descoberta"]["codigos"]["chamados_wifi"]
    assert {c["codigo"] for c in codigos if c["decisao"] == "incluir"} == {"3114", "3121", "3133", "3140", "3152"}
    assert "ligacoes" in st["resultados"]
    with pytest.raises(DecisaoPendenteErro):
        estudo.run("universo")
    _decidir_tudo(estudo)
    assert estudo._estado()["mapeamento"]["status"] == "confirmado"
    universo = estudo.run("universo")
    st = estudo._estado()
    assert st["resultados"]["universo"]["status"] == "feito"
    assert st["passaporte"]["metodo"] == "agregado_na_fonte"
    surpresa = [d for d in st["decisoes"] if d["titulo"] == "Datas fora das ondas declaradas"]
    assert surpresa and set(surpresa[0]["dados"]["meses"]) == {"2024-11", "2025-08"}
    estudo.passport().approve()
    plano = estudo.plan()
    p = plano.plano
    assert [x.id for x in p.passos] == ["tendencias_pre", "codigos_estaveis", "efeito", "placebo", "sensibilidade"]
    assert any("inventado" in a for a in p.avisos)  # bloco inexistente descartado pelo Python
    with pytest.raises(DecisaoPendenteErro):
        plano.approve()
    _decidir_tudo(estudo)
    plano.set("efeito", ponderar_por="unidades")
    plano.approve()
    r = estudo.run()
    st = estudo._estado()
    assert all(st["resultados"][x]["status"] == "feito" for x in ("tendencias_pre", "efeito", "placebo", "sensibilidade")), \
        {k: (v["status"], v.get("erro")) for k, v in st["resultados"].items()}
    efeito = next(m for m in st["metricas"].values() if m["descricao"].startswith("efeito relativo"))
    assert efeito["valor"] < 0
    revisao = estudo.review()
    st = estudo._estado()
    assert st["narrativa"]["origem"] == "ia" and "⟦" not in st["narrativa"]["texto"]
    assert any(a["origem"] == "ia" for a in st["achados"])
    with pytest.raises(PortaoPendenteErro):
        estudo.accept(reason="tentativa antes de decidir os riscos")
    _decidir_tudo(estudo)
    estudo.accept(reason="efeito robusto; limite do pico registrado")
    assert estudo._estado()["status"] == "aceito"
    h = estudo.export_handoff(incluir_dados="agregados")
    info = estudo._estado()["handoff"]
    assert info["verificacao"]["ok"] and info["verificacao"]["executado"], info["verificacao"]
    saida = json.loads(info["verificacao"]["saida"])
    assert saida["efeito"]["att"] < 0
    h.approve()
    info = estudo._estado()["handoff"]
    assert estudo._estado()["status"] == "entregue" and os.path.exists(info["zip"])
    nomes = zipfile.ZipFile(info["zip"]).namelist()
    for esperado in ("README.md", "manifest.json", "environment.lock", "src/adex_metodos.py", "src/pipeline.py",
                     "sampling_passport.json", "metrics.json", "decisions.md", "limitations.md", "reports/relatorio.html"):
        assert any(n.endswith(esperado) for n in nomes), esperado
    assert any("/sql/" in n for n in nomes) and any("/data_contracts/" in n for n in nomes)
    # dossiê íntegro e com a trilha completa
    tipos = [ev["tipo"] for ev in estudo._s.dossie.eventos()]
    for t in ("estudo_criado", "enquadramento", "carta", "portao", "descoberta", "consulta", "bloco", "plano", "decisao", "critica", "handoff", "uso_llm"):
        assert t in tipos, t
    estudo._s.dossie.verificar()
    # nenhum valor de CPF foi para o 'Qwen'
    enviados = json.dumps(adex._hub.pedidos, ensure_ascii=False)
    cpfs = caso.tabelas["CAD.BASE_FIBRA_MENSAL"]["cpf"].head(200)
    assert not any(c in enviados for c in cpfs)


def test_editar_carta_reabre_so_o_que_depende(adex, caso):
    estudo = adex.create_session(objective=caso.objetivo)
    estudo.frame()
    estudo.frame(answers=RESPOSTAS_DEV).approve()
    estudo.discover()
    _decidir_tudo(estudo)
    estudo.run("universo")
    estudo.passport().approve()
    # muda só 'fora do escopo': volta para a carta e, aprovada, segue para o plano (descoberta e universo seguem valendo)
    estudo.charter().edit("fora_escopo", "Cancelamento, receita, fornecedor e NPS", reason="ajuste do comitê")
    no, valor = estudo._pendente()
    assert no == "portao_carta" and valor["versao"] == 2
    estudo.charter().approve()
    no, _ = estudo._pendente()
    assert no == "aguardar_plano"


def test_retomar_em_outra_sessao(adex, caso):
    estudo = adex.create_session(objective=caso.objetivo)
    estudo.frame()
    estudo.frame(answers=RESPOSTAS_DEV)
    usados = estudo._s.telemetria.orcamento.chamadas
    de_novo = adex.resume(estudo.id)
    assert de_novo._pendente()[0] == "portao_carta"
    assert de_novo._s.telemetria.orcamento.chamadas == usados  # orçamento continua do dossiê
    de_novo.charter().approve()
    assert de_novo._pendente()[0] == "aguardar_descoberta"
    assert adex.studies()[0]["estudo_id"].startswith("EST-")
