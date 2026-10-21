"""Guardas dos agentes (Deep Agents com rédea curta).

- :class:`ContadorUso` (callback no modelo): confere o orçamento antes de TODA
  chamada — inclusive a sumarização interna do Deep Agents, que não passa pelo
  laço do agente — e registra tokens e custo depois.
- :class:`GuardaQwen` (middleware): aplica a regra de aceitação no laço do
  agente; resposta cortada ou com raciocínio vazado é refeita uma vez e, se
  persistir, o agente para (falha fechada).
- :class:`AuditorFerramentas` (middleware): se aparecer ferramenta fora da lista
  permitida (execute, edit, delete, plug-in desconhecido), o agente para.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage

from adex_copiloto.erros import AgenteErro
from adex_copiloto.observabilidade.telemetria import RegistroUso, Telemetria, agora

log = logging.getLogger("adex_copiloto.agentes")


class ContadorUso(BaseCallbackHandler):
    raise_error = True  # exceção aqui interrompe a chamada (orçamento)

    def __init__(self, telemetria: Telemetria, *, etapa: str, max_tokens: int) -> None:
        self.telemetria = telemetria
        self.etapa = etapa
        self.max_tokens = max_tokens

    def on_chat_model_start(self, serialized, messages, **kwargs: Any) -> None:
        caracteres = sum(len(str(getattr(m, "content", ""))) for lote in messages for m in lote)
        self.telemetria.orcamento.verificar(int(caracteres / 3.2) + self.max_tokens)

    def on_llm_end(self, response, **kwargs: Any) -> None:
        for lote in response.generations:
            for g in lote:
                msg = getattr(g, "message", None)
                uso = getattr(msg, "usage_metadata", None) or {}
                meta = getattr(msg, "response_metadata", None) or {}
                detalhes = uso.get("output_token_details") or {}
                self.telemetria.registrar_uso(RegistroUso(
                    em=agora().isoformat(), etapa=self.etapa, nome="agente", perfil=str(meta.get("adex_perfil", "acao")),
                    modelo=str(meta.get("model_name", "")), tokens_entrada=int(uso.get("input_tokens", 0) or 0),
                    tokens_saida=int(uso.get("output_tokens", 0) or 0), tokens_raciocinio=int(detalhes.get("reasoning", 0) or 0),
                    custo_usd=meta.get("adex_custo_usd"), aceite="truncada" if meta.get("adex_truncada") else "ok",
                    caminho="agente",
                ))


def _mensagem_final(resposta: Any) -> AIMessage | None:
    resultado = getattr(resposta, "result", None)
    if isinstance(resposta, AIMessage):
        return resposta
    if isinstance(resultado, list):
        for m in reversed(resultado):
            if isinstance(m, AIMessage):
                return m
    return None


class GuardaQwen(AgentMiddleware):
    """Regra de aceitação no laço do agente (uma nova tentativa, depois falha fechada)."""

    def __init__(self, *, tentativas: int = 2) -> None:
        super().__init__()
        self.tentativas = tentativas

    @property
    def name(self) -> str:
        return "GuardaQwen"

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]):
        motivo = ""
        for _ in range(self.tentativas):
            resposta = handler(request)
            msg = _mensagem_final(resposta)
            if msg is None:
                return resposta
            meta = msg.response_metadata or {}
            if meta.get("adex_truncada"):
                motivo = "resposta cortada no teto de saída"
                continue
            if meta.get("adex_vazamento_raciocinio"):
                motivo = "raciocínio vazado na resposta"
                continue
            if msg.invalid_tool_calls:
                motivo = f"chamada de ferramenta inválida ({msg.invalid_tool_calls[0].get('error', '')[:80]})"
                continue
            return resposta
        raise AgenteErro(f"O agente parou: {motivo} (regra de aceitação).", detalhes={"motivo": motivo})


class AuditorFerramentas(AgentMiddleware):
    """Falha fechada se o conjunto de ferramentas oferecido ao modelo sair da lista permitida."""

    def __init__(self, permitidas: set[str]) -> None:
        super().__init__()
        self.permitidas = set(permitidas)

    @property
    def name(self) -> str:
        return "AuditorFerramentas"

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]):
        nomes = set()
        for t in request.tools or []:
            nome = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
            if nome is None and isinstance(t, dict):
                nome = (t.get("function") or {}).get("name")
            nomes.add(str(nome))
        extras = nomes - self.permitidas
        if extras:
            raise AgenteErro(
                f"Ferramentas fora da lista permitida apareceram no agente: {', '.join(sorted(extras))}.",
                detalhes={"extras": sorted(extras)},
            )
        return handler(request)

    def wrap_tool_call(self, request, handler):
        nome = getattr(request, "tool_call", {}).get("name") if hasattr(request, "tool_call") else None
        if nome is not None and nome not in self.permitidas:
            raise AgenteErro(f"O agente tentou chamar '{nome}', que não é permitida.")
        return handler(request)
