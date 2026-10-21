"""``ChatQwenHub``: o ``ChatOpenAI`` ajustado para o Qwen3.8 no GPU Hub.

Por que uma subclasse (achados nos fontes do langchain-openai 1.7.2):
- o ``ChatOpenAI`` descarta ``reasoning_content``/``reasoning`` (só segue a API
  oficial da OpenAI) — aqui o raciocínio vai para ``additional_kwargs``;
- ele sempre manda ``max_completion_tokens``; o hub pode querer ``max_tokens``
  (``hub.campo_saida``), porque o LiteLLM trata os dois de jeitos diferentes;
- o merge de ``extra_body`` é raso — cada instância recebe o corpo completo do perfil;
- ids de chamada de ferramenta vazios (deepagents #3587) recebem id próprio;
- corte por teto (vLLM #61206 diz ``tool_calls`` mesmo cortado) é marcado em
  ``response_metadata['adex_truncada']`` para o guarda do agente recusar;
- cabeçalhos: só os úteis (custo, id) seguem; o resto é descartado;
- ``parallel_tool_calls`` sempre falso; streaming desligado (o guarda precisa da
  resposta inteira para aplicar a regra de aceitação).
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from adex_copiloto.config.modelos import Config
from adex_copiloto.llm.perfis import montar_requisicao
from adex_copiloto.seguranca.segredos import Segredo

PROVEDOR_LS = "adex_gpu_hub"
_CABECALHOS_UTEIS = ("x-litellm-response-cost", "x-request-id", "x-litellm-call-id", "x-litellm-model-id")


def _float(valor: Any) -> float | None:
    try:
        return float(valor) if valor not in (None, "") else None
    except (TypeError, ValueError):
        return None


class ChatQwenHub(ChatOpenAI):
    """Cliente LangChain do qwen-latest. Use :func:`criar_chat_qwen`."""

    perfil_adex: str = "acao"
    campo_saida: str = "max_tokens"
    cabecalho_custo: str = "x-litellm-response-cost"
    reenviar_raciocinio: bool = False

    @property
    def _llm_type(self) -> str:
        return "adex-qwen-hub"

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any):
        params = super()._get_ls_params(stop=stop, **kwargs)
        params["ls_provider"] = PROVEDOR_LS
        return params

    def bind_tools(self, tools, *, tool_choice=None, strict=None, parallel_tool_calls=None, response_format=None, **kwargs):
        # Uma ferramenta por vez: o guarda e o auditor conferem cada chamada.
        return super().bind_tools(
            tools,
            tool_choice=tool_choice,
            strict=strict,
            parallel_tool_calls=False,
            response_format=response_format,
            **kwargs,
        )

    def _get_request_payload(self, input_, *, stop=None, **kwargs) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # O ChatOpenAI sempre troca para max_completion_tokens; o hub decide qual campo vale.
        if self.campo_saida == "max_tokens" and "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        if self.reenviar_raciocinio and "messages" in payload:
            mensagens = self._convert_input(input_).to_messages()
            for msg, d in zip(mensagens, payload["messages"]):
                if isinstance(msg, AIMessage) and d.get("role") == "assistant":
                    raciocinio = msg.additional_kwargs.get("reasoning_content")
                    if raciocinio:
                        d["reasoning_content"] = raciocinio
        return payload

    def _create_chat_result(self, response, generation_info: dict | None = None):
        info = dict(generation_info or {})
        cabecalhos = {k.lower(): v for k, v in (info.pop("headers", None) or {}).items()}
        uteis = {k: cabecalhos[k] for k in _CABECALHOS_UTEIS if k in cabecalhos}
        if uteis:
            info["adex_cabecalhos"] = uteis
        resultado = super()._create_chat_result(response, info)
        dados = (
            response
            if isinstance(response, dict)
            else response.model_dump(exclude={"choices": {"__all__": {"message": {"parsed"}}}}, warnings=False)
        )
        uso = dados.get("usage") or {}
        teto = self.max_tokens
        custo = _float(cabecalhos.get(self.cabecalho_custo.lower()))
        for gen, escolha in zip(resultado.generations, dados.get("choices") or []):
            msg = gen.message
            bruto = escolha.get("message") or {}
            raciocinio = bruto.get("reasoning_content") or bruto.get("reasoning")
            if raciocinio:
                msg.additional_kwargs["reasoning_content"] = raciocinio
            if isinstance(msg, AIMessage):
                for chamada in msg.tool_calls:
                    if not chamada.get("id"):
                        chamada["id"] = f"call_{uuid.uuid4().hex[:24]}"
            meta: dict[str, Any] = {"adex_perfil": self.perfil_adex}
            if custo is not None:
                meta["adex_custo_usd"] = custo
            saida = uso.get("completion_tokens")
            if escolha.get("finish_reason") == "length" or (teto and saida is not None and saida >= teto):
                meta["adex_truncada"] = True
            texto = msg.content if isinstance(msg.content, str) else ""
            if "<think>" in texto or "</think>" in texto:
                meta["adex_vazamento_raciocinio"] = True
            msg.response_metadata.update(meta)
            gen.generation_info = {**(gen.generation_info or {}), **meta}
        return resultado


def criar_chat_qwen(cfg: Config, chave: Segredo, perfil: str = "acao", *, http_client=None, callbacks=None) -> ChatQwenHub:
    """Uma instância por perfil, com o corpo de requisição completo do perfil."""
    from adex_copiloto.seguranca.segredos import conferir_endpoint_hub

    conferir_endpoint_hub(cfg)
    p = cfg.perfis[perfil]
    req = montar_requisicao(p, cfg.hub)
    saida = req.pop(cfg.hub.campo_saida)
    extra_body = req.pop("extra_body")
    esforco_topo = req.pop("reasoning_effort", None)
    if http_client is None:
        from adex_copiloto.llm.cliente import criar_http_client

        http_client = criar_http_client(cfg)
    kwargs: dict[str, Any] = dict(
        model=cfg.hub.modelo,
        base_url=cfg.hub.base_url,
        api_key=SecretStr(chave.revelar()),
        max_retries=0,
        timeout=p.timeout_s or cfg.hub.timeout_s,
        use_responses_api=False,
        stream_usage=True,
        include_response_headers=True,
        disable_streaming=True,
        temperature=req["temperature"],
        top_p=req["top_p"],
        presence_penalty=req["presence_penalty"],
        max_tokens=saida,
        extra_body=extra_body,
        http_client=http_client,
        perfil_adex=perfil,
        campo_saida=cfg.hub.campo_saida,
        cabecalho_custo=cfg.hub.cabecalho_custo,
        reenviar_raciocinio=cfg.hub.reenviar_raciocinio,
        profile={
            "max_input_tokens": cfg.hub.contexto_util,
            "max_output_tokens": cfg.hub.saida_max,
            "tool_calling": True,
            "structured_output": True,
            "image_inputs": bool(cfg.hub.visao),
            "reasoning_output": bool(p.raciocinio),
        },
    )
    if esforco_topo:
        kwargs["reasoning_effort"] = esforco_topo
    if callbacks:
        kwargs["callbacks"] = list(callbacks)
    return ChatQwenHub(**kwargs)
