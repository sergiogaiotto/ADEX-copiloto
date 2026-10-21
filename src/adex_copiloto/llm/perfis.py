"""Perfis do Qwen: cada perfil vira um corpo de requisição completo.

Por que completo: o ``ChatOpenAI`` faz merge raso do ``extra_body``; se um
perfil mandasse só parte de ``chat_template_kwargs``, o resto sumiria. Aqui cada
perfil gera tudo o que o hub precisa, sem depender de merge.

Fatos usados (Qwen3.8-27B, a confirmar na Fase 0):
- raciocínio ligado por padrão; desliga com ``chat_template_kwargs.enable_thinking=false``;
- ``reasoning_effort`` aceita low/medium/xhigh ("high" dá 400);
- raciocínio e resposta dividem o mesmo ``max_tokens``;
- sempre mandar ``top_k=20`` (a falta foi ligada a loops de raciocínio).
"""

from __future__ import annotations

from typing import Any

from adex_copiloto.config.modelos import Hub, PerfilConfig

# Margem mínima de saída depois do raciocínio, para a resposta não vir vazia.
MARGEM_RESPOSTA = 1_024


def limite_saida(perfil: PerfilConfig, hub: Hub, max_tokens: int | None = None) -> int:
    pedido = max_tokens or perfil.max_tokens
    return max(64, min(pedido, hub.saida_max))


def orcamento_raciocinio(perfil: PerfilConfig, saida: int) -> int | None:
    if not perfil.raciocinio or not perfil.orcamento_raciocinio:
        return None
    teto = saida - MARGEM_RESPOSTA
    if teto < 256:
        teto = max(128, saida // 2)
    return max(128, min(perfil.orcamento_raciocinio, teto))


def montar_requisicao(
    perfil: PerfilConfig,
    hub: Hub,
    *,
    max_tokens: int | None = None,
    esforco: str | None = None,
    orcamento: int | None = None,
) -> dict[str, Any]:
    """Parâmetros para ``chat.completions.create`` (inclui ``extra_body`` completo)."""
    saida = limite_saida(perfil, hub, max_tokens)
    params: dict[str, Any] = {
        "temperature": perfil.temperature,
        "top_p": perfil.top_p,
        "presence_penalty": perfil.presence_penalty,
        hub.campo_saida: saida,
    }
    template: dict[str, Any] = {"enable_thinking": bool(perfil.raciocinio)}
    extra: dict[str, Any] = {"top_k": perfil.top_k, "min_p": perfil.min_p, "chat_template_kwargs": template}
    nivel = esforco or perfil.esforco
    if perfil.raciocinio and nivel:
        if hub.esforco_em in ("chat_template_kwargs", "ambos"):
            template["reasoning_effort"] = nivel
        if hub.esforco_em in ("topo", "ambos"):
            params["reasoning_effort"] = nivel
    budget = orcamento if orcamento is not None else orcamento_raciocinio(perfil, saida)
    if perfil.raciocinio and budget:
        extra["thinking_token_budget"] = int(min(budget, max(128, saida - 256)))
    params["extra_body"] = extra
    return params


def descrever(perfil_nome: str, perfil: PerfilConfig) -> str:
    modo = f"raciocínio {perfil.esforco or 'padrão'}" if perfil.raciocinio else "sem raciocínio"
    return f"{perfil_nome}: {modo}, temp {perfil.temperature}, top_p {perfil.top_p}, top_k {perfil.top_k}, saída até {perfil.max_tokens}"
