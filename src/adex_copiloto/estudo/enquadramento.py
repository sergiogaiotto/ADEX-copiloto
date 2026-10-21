"""Enquadrar o pedido e montar a carta (nenhum dado é lido nesses passos)."""

from __future__ import annotations

import re
from typing import Any

from adex_copiloto.estudo.carta import Carta, Enquadramento, PropostaCarta, montar_carta
from adex_copiloto.llm import prompts

_CUSTO = re.compile(r"\b(valer a pena|vale a pena|valeu a pena|compensa|retorno|roi|custo|r\$|economi)", re.IGNORECASE)


def enquadrar(cliente, objetivo: str, *, vertical: str | None = None, lente: dict[str, Any] | None = None,
              modo: str = "exploration", max_perguntas: int = 3) -> Enquadramento:
    corpo = prompts.dados({"pedido_do_dev": objetivo, "area": vertical or "não informada", "modo": modo,
                           "lente_da_area": lente or {}}, "dev")
    msgs = prompts.mensagens("enquadrar", corpo, max_perguntas=max_perguntas)
    return cliente.completar("pensar", msgs, etapa="enquadrar", nome="enquadrar", esquema=Enquadramento).objeto


def construir_carta(cliente, *, objetivo: str, enquadramento: Enquadramento | None, respostas: list[str], cfg,
                    lente: dict[str, Any] | None = None) -> tuple[Carta, list[str], PropostaCarta]:
    textos_dev = [objetivo, *respostas]
    corpo = prompts.dados(
        {
            "pedido_original": objetivo,
            "enquadramento": enquadramento.model_dump() if enquadramento else None,
            "respostas_do_dev": respostas,
            "padroes_da_organizacao": {"min_group_size": cfg.data_policy.min_group_size,
                                       "teto_gb_lidos": cfg.data_policy.teto_gb_lidos, "acesso": "só leitura"},
            "lente_da_area": lente or {},
        },
        "dev",
    )
    msgs = prompts.mensagens("carta", corpo, min_grupo=cfg.data_policy.min_group_size)
    proposta: PropostaCarta = cliente.completar("pensar", msgs, etapa="carta", nome="carta", esquema=PropostaCarta).objeto
    tipo = enquadramento.tipo_pergunta if enquadramento else "descricao"
    carta, avisos = montar_carta(proposta, textos_dev, tipo)
    avisos.extend(revisao_rapida(carta, textos_dev))
    return carta, avisos, proposta


def revisao_rapida(carta: Carta, textos_dev: list[str]) -> list[str]:
    """Checagens sem IA sobre a carta (antes de qualquer dado)."""
    avisos = []
    texto = " ".join(textos_dev)
    if _CUSTO.search(texto) and not re.search(r"(custo|R\$).{0,40}\d", carta.valor("desfecho") + carta.valor("criterios_pronto"), re.IGNORECASE):
        avisos.append(
            "'Valer a pena' pede custo: o efeito sairá em unidades do desfecho. Para virar R$, informe o custo por "
            "unidade (ex.: por visita) — o valor vem de você, não da IA."
        )
    if not carta.valor("desfecho").strip():
        avisos.append("O desfecho ainda não está definido: defina antes de olhar os dados.")
    return avisos
