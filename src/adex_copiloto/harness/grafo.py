"""Grafo do estudo (LangGraph): 13 passos determinísticos e 5 portões humanos.

    enquadrar → entrevista → carta → ⛔carta → [descobrir] → descobrir → [universo] → universo
    → ⛔passaporte → [plano] → plano → ⛔plano → [etapas] ⇄ etapas → crítico → ⛔aceite
    → [handoff] → handoff → ⛔handoff

``[x]`` = espera um comando do dev (``interrupt``); ``⛔`` = portão de aprovação.
Mudar a carta depois de aprovada volta ao ⛔carta e reabre só as etapas que
dependem do campo alterado.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from adex_copiloto.harness.estado import EstadoEstudo
from adex_copiloto.harness.nos import Nos

NOS_TRABALHO = ("enquadrar", "carta", "descobrir", "universo", "planejar", "etapas", "critico", "handoff")
NOS_ESPERA = ("entrevista", "aguardar_descoberta", "aguardar_universo", "aguardar_plano", "aguardar_etapas", "aguardar_handoff")
NOS_PORTAO = ("portao_carta", "portao_passaporte", "portao_plano", "portao_aceite", "portao_handoff")


def construir(nos: Nos, checkpointer):
    g = StateGraph(EstadoEstudo)
    for nome in NOS_TRABALHO + NOS_ESPERA + NOS_PORTAO:
        g.add_node(nome, getattr(nos, nome))
    g.add_edge(START, "enquadrar")
    g.add_edge("enquadrar", "entrevista")
    g.add_edge("entrevista", "carta")
    g.add_edge("carta", "portao_carta")
    g.add_conditional_edges(
        "portao_carta", Nos.rota_portao_carta,
        ["portao_carta", "aguardar_descoberta", "aguardar_universo", "portao_passaporte", "aguardar_plano", "portao_plano",
         "aguardar_etapas"],
    )
    g.add_edge("aguardar_descoberta", "descobrir")
    g.add_edge("descobrir", "aguardar_universo")
    g.add_edge("aguardar_universo", "universo")
    g.add_edge("universo", "portao_passaporte")
    g.add_conditional_edges("portao_passaporte", Nos.rota_portao("aguardar_plano", "passaporte"), ["portao_passaporte", "aguardar_plano"])
    g.add_edge("aguardar_plano", "planejar")
    g.add_edge("planejar", "portao_plano")
    g.add_conditional_edges("portao_plano", Nos.rota_portao("aguardar_etapas", "plano"), ["portao_plano", "aguardar_etapas"])
    g.add_conditional_edges("aguardar_etapas", Nos.rota_aguardar_etapas, ["etapas", "critico"])
    g.add_edge("etapas", "aguardar_etapas")
    g.add_edge("critico", "portao_aceite")
    g.add_conditional_edges("portao_aceite", Nos.rota_portao_aceite, ["aguardar_handoff", "aguardar_plano", "portao_aceite", END])
    g.add_edge("aguardar_handoff", "handoff")
    g.add_edge("handoff", "portao_handoff")
    g.add_conditional_edges("portao_handoff", Nos.rota_portao_handoff, ["portao_handoff", END])
    return g.compile(checkpointer=checkpointer)
