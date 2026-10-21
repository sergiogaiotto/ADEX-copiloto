"""Planejamento: jeitos de responder, método composto por blocos e pré-mortem.

Fontes do plano, em ordem de preferência do dev:
1. um playbook escolhido (atalho, só se a aderência for ≥ 0,70 ou o dev pedir);
2. o agente de plano (Deep Agent com especialistas como subagentes);
3. o modo direto (uma chamada 'pensar' com as fichas dos blocos) — também o recuo.

O Python confere tudo no registro de blocos (nome, versão, parâmetros e ordem dos
dados), pede um reparo com os problemas e, se ainda sobrar erro, descarta o passo
inválido e os que dependem dele, com aviso.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adex_copiloto.blocos.registro import RegistroBlocos
from adex_copiloto.dados.catalogo import Catalogo
from adex_copiloto.erros import AdexErro, AgenteErro
from adex_copiloto.estudo.carta import Carta
from adex_copiloto.estudo.evidencia import Afirmacao, Decisao
from adex_copiloto.estudo.mapeamento import Mapeamento
from adex_copiloto.estudo.plano import ItemPreMortem, Plano, PropostaPlano, plano_de_proposta
from adex_copiloto.llm import prompts

ESPECIALISTAS = [
    ("estatistico", "Estatístico: premissas, testes, incerteza, causalidade e robustez (placebo, sensibilidade).",
     "Você é o estatístico do time. Revise o desenho proposto: premissas, comparações, incerteza e robustez. Responda curto."),
    ("amostragem", "Especialista de amostragem: censo, agregado na fonte ou amostra; representatividade e viés.",
     "Você é o especialista de amostragem. Diga se o método precisa de linhas por unidade e como amostrar sem viés."),
    ("cientista_dados", "Cientista de dados: features, modelos, validação sem vazamento e métricas contra baseline.",
     "Você é o cientista de dados. Revise validação, vazamento e baseline. Responda curto."),
    ("bi", "BI: medidas, granularidade, reconciliação de totais e leitura para quem decide.",
     "Você é o especialista de BI. Revise medidas, granularidade e como o resultado vira indicador. Responda curto."),
]


@dataclass
class ResultadoPlano:
    plano: Plano
    avisos: list[str] = field(default_factory=list)
    sugestoes: list[dict[str, str]] = field(default_factory=list)
    modo: str = "direto"


def _contexto(carta: Carta, mapeamento: Mapeamento | None, afirmacoes: list[Afirmacao], decisoes: list[Decisao],
              produzidos: set[str], registro: RegistroBlocos) -> dict[str, Any]:
    return {
        "carta": carta.para_llm(),
        "mapeamento": mapeamento.model_dump(mode="json") if mapeamento else None,
        "hipoteses": [{"id": a.id, "texto": a.texto, "selo": a.selo} for a in afirmacoes if a.tipo == "hipotese"],
        "decisoes_tomadas": [{"id": d.id, "titulo": d.titulo, "escolha": d.opcao_escolhida.rotulo if d.opcao_escolhida else None,
                              "dados": d.dados} for d in decisoes if d.status == "decidida"],
        "dados_ja_produzidos": sorted(produzidos),
        "blocos": registro.listar(carta.tipo_pergunta),
    }


def _limpar(plano: Plano, registro: RegistroBlocos, produzidos: set[str]) -> list[str]:
    """Descarta passos inválidos e os que dependem deles (até o plano ficar válido)."""
    avisos = []
    for _ in range(len(plano.passos) + 1):
        problemas = registro.validar_plano(plano, produzidos_antes=produzidos)
        if not problemas:
            return avisos
        ruins = {pr.split("'")[1] for pr in problemas if pr.startswith("passo '")}
        if not ruins:
            avisos.extend(problemas)
            return avisos
        for id_ in ruins:
            try:
                plano.passos.remove(plano.passo(id_))
                avisos.append(f"passo '{id_}' descartado: {next(p for p in problemas if p.startswith(f'passo {chr(39)}{id_}{chr(39)}'))}")
            except (KeyError, StopIteration):
                pass
    return avisos


def pre_mortem_python(carta: Carta, afirmacoes: list[Afirmacao], plano: Plano) -> list[ItemPreMortem]:
    """Riscos que o Python já sabe (carta e hipóteses com evidência) e a proteção no plano."""
    itens = []
    blocos = {p.bloco for p in plano.passos}
    for a in afirmacoes:
        if a.tipo == "hipotese" and "pico" in a.texto.lower():
            protecao = "sensibilidade excluindo a coorte com pico" if "sensibilidade" in blocos else "FALTA: incluir sensibilidade"
            itens.append(ItemPreMortem(risco=a.texto, protecao=protecao, passo="sensibilidade", origem="python"))
    if carta.tipo_pergunta == "efeito" and "estudo_evento_pre" not in blocos:
        itens.append(ItemPreMortem(risco="tratados e controles podem não andar juntos antes da ação",
                                   protecao="FALTA: incluir estudo_evento_pre", origem="python"))
    if carta.tipo_pergunta in ("classificacao", "previsao") and "separacao" not in blocos:
        itens.append(ItemPreMortem(risco="avaliar o modelo com dados do futuro (vazamento)", protecao="FALTA: incluir separacao temporal",
                                   origem="python"))
    return itens


def planejar(
    cliente,
    chat_agente,
    *,
    carta: Carta,
    mapeamento: Mapeamento | None,
    afirmacoes: list[Afirmacao],
    decisoes: list[Decisao],
    produzidos: set[str],
    registro: RegistroBlocos,
    catalogo: Catalogo | None,
    cfg,
    modo_agente: bool,
    playbook=None,
    atalho_texto: str = "",
    callbacks=None,
) -> ResultadoPlano:
    avisos: list[str] = []
    sugestoes: list[dict[str, str]] = []
    if playbook is not None:
        plano = playbook.plano(carta.versao)
        plano.jeitos = []
        avisos.extend(_limpar(plano, registro, produzidos))
        plano.pre_mortem = pre_mortem_python(carta, afirmacoes, plano)
        return ResultadoPlano(plano, avisos, sugestoes, f"playbook:{playbook.nome}")
    ctx = _contexto(carta, mapeamento, afirmacoes, decisoes, produzidos, registro)
    proposta: PropostaPlano | None = None
    modo = "agente" if modo_agente else "direto"
    if modo_agente:
        try:
            from adex_copiloto.agentes.deep import criar_agente, rodar_agente
            from adex_copiloto.agentes.ferramentas import ferramentas_plano

            ferramentas = ferramentas_plano(registro, catalogo or Catalogo(), ctx["mapeamento"], carta.tipo_pergunta, produzidos, sugestoes)
            leitura = [f for f in ferramentas if f.name in ("listar_blocos", "ficha_bloco", "ver_mapeamento")]
            subagentes = [{"name": n, "description": d, "system_prompt": prompts.carregar("sistema") + "\n\n" + p, "tools": leitura}
                          for n, d, p in ESPECIALISTAS]
            agente = criar_agente(cfg, chat_agente, nome="plano", prompt_sistema=prompts.sistema("plano", atalho=atalho_texto),
                                  ferramentas=ferramentas, esquema_resposta=PropostaPlano, subagentes=subagentes)
            pedido = {k: v for k, v in ctx.items() if k != "blocos"}
            proposta = rodar_agente(agente, prompts.dados(pedido, "estudo"), cfg=cfg, callbacks=callbacks)
        except AgenteErro as e:
            avisos.append(f"o agente de plano parou ({e.mensagem}); usei o modo direto.")
            modo = "direto (recuo)"
    if proposta is None:
        msgs = prompts.mensagens("plano", prompts.dados(ctx, "estudo"), atalho=atalho_texto)
        proposta = cliente.completar("pensar", msgs, etapa="plano", nome="plano", esquema=PropostaPlano).objeto
    plano = plano_de_proposta(proposta, carta.versao, origem=modo)
    problemas = registro.validar_plano(plano, produzidos_antes=produzidos)
    if problemas:
        try:
            msgs = prompts.mensagens("plano", prompts.dados({**ctx, "proposta_anterior": proposta.model_dump(),
                                                              "problemas_encontrados_pelo_python": problemas}, "estudo"), atalho=atalho_texto)
            proposta = cliente.completar("pensar", msgs, etapa="plano", nome="plano:reparo", esquema=PropostaPlano).objeto
            plano = plano_de_proposta(proposta, carta.versao, origem=modo)
        except AdexErro as e:
            avisos.append(f"o reparo do plano falhou ({e.mensagem[:120]}).")
    avisos.extend(_limpar(plano, registro, produzidos))
    if not plano.passos:
        raise AdexErro("O plano ficou sem passos válidos.", como_resolver="Tente de novo ou escolha um atalho com estudo.plan(playbook=...).")
    if not any(j.recomendado for j in plano.jeitos) and plano.jeitos:
        plano.jeitos[0].recomendado = True
    plano.pre_mortem.extend(pre_mortem_python(carta, afirmacoes, plano))
    return ResultadoPlano(plano, avisos, sugestoes, modo)
