from __future__ import annotations

import json

import pytest

from adex_copiloto import exemplos
from adex_copiloto.agentes.deep import FERRAMENTAS_ARQUIVO, auditar_plugins, criar_agente, rodar_agente
from adex_copiloto.agentes.ferramentas import ferramentas_descoberta, ferramentas_plano
from adex_copiloto.agentes.guardas import AuditorFerramentas
from adex_copiloto.blocos import REGISTRO
from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import AgenteErro
from adex_copiloto.estudo.descoberta import PropostaMapeamento
from adex_copiloto.llm.chat_langchain import criar_chat_qwen
from falsos import FakeHub, chave_teste, completion, config_teste, tool_call
from roteiro_wifi import MAPEAMENTO


def _nomes_ferramentas(pedido: dict) -> set[str]:
    return {t["function"]["name"] for t in pedido.get("tools", [])}


@pytest.fixture(scope="module")
def catalogo():
    ex = exemplos.wifi6(domicilios_por_cidade=(60, 80))
    return ex.fonte(Config()).catalogo()


def test_deep_agent_descoberta_com_ferramentas_e_resposta_estruturada(catalogo):
    hub = FakeHub().em_seguida(
        completion(None, finish="tool_calls", tool_calls=[tool_call("listar_tabelas", {"termo": "chamado"}, "c1")]),
        completion(None, finish="tool_calls", tool_calls=[tool_call("ler_ficha", {"tabela": "OPS_RES.CHAMADO_TECNICO"}, "c2")]),
        completion(None, finish="tool_calls", tool_calls=[tool_call("write_file", {"file_path": "/rascunho/notas.md", "content": "ok"}, "c3")]),
        completion(None, finish="tool_calls", tool_calls=[tool_call("write_file", {"file_path": "/fora.md", "content": "x"}, "c4")]),
        completion(None, finish="tool_calls", tool_calls=[tool_call("PropostaMapeamento", MAPEAMENTO, "c5")]),
    )
    cfg = config_teste()
    chat = criar_chat_qwen(cfg, chave_teste(), "acao", http_client=hub.http())
    agente = criar_agente(cfg, chat, nome="descoberta", prompt_sistema="teste", ferramentas=ferramentas_descoberta(catalogo),
                          esquema_resposta=PropostaMapeamento)
    resp = rodar_agente(agente, "descubra", cfg=cfg)
    assert isinstance(resp, PropostaMapeamento) and resp.populacao == "CAD.BASE_FIBRA_MENSAL"
    oferecidas = _nomes_ferramentas(hub.pedidos[0])
    assert {"listar_tabelas", "ler_ficha", "PropostaMapeamento"} <= oferecidas
    assert not ({"execute", "edit_file", "delete", "task"} & oferecidas), oferecidas
    assert oferecidas <= set(FERRAMENTAS_ARQUIVO) | {"listar_tabelas", "buscar_colunas", "ler_ficha", "PropostaMapeamento"}
    # a ferramenta de catálogo respondeu com a ficha (só metadados, CPF marcado)
    mensagens_tool = [m for m in hub.pedidos[2]["messages"] if m.get("role") == "tool"]
    assert any("CHAMADO_TECNICO" in m["content"] for m in mensagens_tool)
    # escrita fora de /rascunho/ foi negada pela permissão
    respostas = [m for m in hub.pedidos[4]["messages"] if m.get("role") == "tool"]
    assert any("ermission" in m["content"] or "negad" in m["content"].lower() or "denied" in m["content"].lower() for m in respostas), respostas[-1]
    # cada pedido do agente seguiu o perfil (top_k, uma ferramenta por vez, teto de saída)
    assert all(p["top_k"] == 20 and p.get("parallel_tool_calls") is False for p in hub.pedidos)


def test_agente_para_quando_resposta_vem_cortada(catalogo):
    hub = FakeHub().em_seguida(*(completion("parcial", finish="length", completion_tokens=8192) for _ in range(3)))
    cfg = config_teste()
    chat = criar_chat_qwen(cfg, chave_teste(), "acao", http_client=hub.http())
    agente = criar_agente(cfg, chat, nome="descoberta", prompt_sistema="teste", ferramentas=ferramentas_descoberta(catalogo),
                          esquema_resposta=PropostaMapeamento)
    with pytest.raises(AgenteErro) as e:
        rodar_agente(agente, "descubra", cfg=cfg)
    assert "cortada" in e.value.mensagem


def test_limite_de_chamadas_do_agente(catalogo):
    hub = FakeHub().em_seguida(*(completion(None, finish="tool_calls", tool_calls=[tool_call("listar_tabelas", {}, f"c{i}")]) for i in range(10)))
    cfg = config_teste()
    cfg.agente.max_chamadas_modelo = 3
    chat = criar_chat_qwen(cfg, chave_teste(), "acao", http_client=hub.http())
    agente = criar_agente(cfg, chat, nome="descoberta", prompt_sistema="teste", ferramentas=ferramentas_descoberta(catalogo),
                          esquema_resposta=PropostaMapeamento)
    with pytest.raises(AgenteErro):
        rodar_agente(agente, "descubra", cfg=cfg)
    assert len(hub.pedidos) <= 4


def test_auditor_falha_fechado():
    from langchain.agents.middleware.types import ModelRequest

    auditor = AuditorFerramentas({"ler_ficha"})

    class F:
        name = "execute"

    req = ModelRequest(model=None, messages=[], tools=[F()])
    with pytest.raises(AgenteErro):
        auditor.wrap_model_call(req, lambda r: None)


def test_plugins_de_perfil_auditados(monkeypatch):
    class EP:
        name = "perfil_estranho"

    monkeypatch.setattr("adex_copiloto.agentes.deep.entry_points", lambda group: [EP()] if group.endswith("harness_profiles") else [])
    with pytest.raises(AgenteErro):
        auditar_plugins(Config())
    cfg = Config()
    cfg.agente.entry_points_permitidos = ["perfil_estranho"]
    assert auditar_plugins(cfg) == ["deepagents.harness_profiles:perfil_estranho"]


def test_ferramentas_de_plano_nao_executam_nada(catalogo):
    sugestoes = []
    fs = {f.name: f for f in ferramentas_plano(REGISTRO, catalogo, {"populacao": "X"}, "efeito", {"painel"}, sugestoes)}
    blocos = json.loads(fs["listar_blocos"].invoke({"tipo": "efeito"}))
    assert any(b["bloco"] == "did_escalonado" for b in blocos)
    r = json.loads(fs["validar_plano"].invoke({"passos_json": json.dumps([{"id": "e", "bloco": "did_escalonado"}])}))
    assert not r["ok"] and any("coortes" in p for p in r["problemas"])
    fs["sugerir_celula"].invoke({"codigo": "print(1)", "explicacao": "teste"})
    assert sugestoes == [{"codigo": "print(1)", "explicacao": "teste"}]
