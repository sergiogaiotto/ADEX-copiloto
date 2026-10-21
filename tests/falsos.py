"""Hub falso no nível HTTP: exercita o SDK openai e o ChatOpenAI de verdade."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import httpx

from adex_copiloto.config.modelos import Config
from adex_copiloto.observabilidade.orcamento import Orcamento
from adex_copiloto.observabilidade.telemetria import Telemetria
from adex_copiloto.seguranca.segredos import Segredo, calcular_impressao

CHAVE_TESTE = "chave-falsa-de-teste-0001"
URL_FALSA = "https://hub-falso.teste/v1"


def completion(
    content: str | None = "ok",
    *,
    reasoning: str | None = None,
    finish: str = "stop",
    tool_calls: list[dict] | None = None,
    prompt_tokens: int = 120,
    completion_tokens: int = 40,
    model: str = "Qwen/Qwen3.8-27B",
) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-teste",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def tool_call(nome: str, argumentos: dict, id_: str | None = "call_1") -> dict:
    return {"id": id_, "type": "function", "function": {"name": nome, "arguments": json.dumps(argumentos, ensure_ascii=False)}}


class Erro:
    def __init__(self, status: int, corpo: str = "erro", cabecalhos: dict | None = None) -> None:
        self.status, self.corpo, self.cabecalhos = status, corpo, cabecalhos or {}


Resposta = dict | Erro | Callable[[dict], "dict | Erro"]


class FakeHub:
    """Responde por roteiro (fila) ou por regras (regex no prompt de sistema)."""

    def __init__(self, custo: str | None = "0.0010") -> None:
        self.fila: list[Resposta] = []
        self.regras: list[tuple[re.Pattern[str], Resposta]] = []
        self.pedidos: list[dict] = []
        self.custo = custo
        self.modelos = {"object": "list", "data": [{"id": "qwen-latest", "object": "model", "created": 1, "owned_by": "openai"}]}

    def em_seguida(self, *respostas: Resposta) -> "FakeHub":
        self.fila.extend(respostas)
        return self

    def quando(self, padrao: str, resposta: Resposta) -> "FakeHub":
        self.regras.append((re.compile(padrao, re.S), resposta))
        return self

    def _escolher(self, corpo: dict) -> Resposta:
        if self.fila:
            return self.fila.pop(0)
        mensagens = corpo.get("messages", [])
        if not isinstance(mensagens, list):
            return Erro(400, "messages precisa ser uma lista")
        texto = "\n".join(
            m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
            for m in mensagens if isinstance(m, dict)
        )
        for padrao, resposta in self.regras:
            if padrao.search(texto):
                return resposta
        return completion("ok")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/models"):
            return httpx.Response(200, json=self.modelos)
        corpo = json.loads(request.content or b"{}")
        self.pedidos.append(corpo)
        resposta = self._escolher(corpo)
        if callable(resposta) and not isinstance(resposta, (dict, Erro)):
            resposta = resposta(corpo)
        if isinstance(resposta, Erro):
            return httpx.Response(resposta.status, json={"error": {"message": resposta.corpo}}, headers=resposta.cabecalhos)
        cab = {"x-litellm-response-cost": self.custo} if self.custo else {}
        return httpx.Response(200, json=resposta, headers=cab)

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def config_teste(**ajustes) -> Config:
    cfg = Config()
    cfg.hub.base_url = URL_FALSA
    # O endereço falso é "homologado" como o Admin faria: a chave só vai para endereço com impressão.
    cfg.hub.base_url_salt = "sal-de-teste"
    cfg.hub.base_url_impressao = calcular_impressao(URL_FALSA, "sal-de-teste")
    cfg.hub.espera_base_s = 0.0
    for caminho, valor in ajustes.items():
        alvo = cfg
        partes = caminho.split("__")
        for p in partes[:-1]:
            alvo = getattr(alvo, p)
        setattr(alvo, partes[-1], valor)
    return cfg


def telemetria_teste(cfg: Config | None = None) -> Telemetria:
    cfg = cfg or Config()
    return Telemetria("EST-TESTE", Orcamento(cfg.orcamento))


def chave_teste() -> Segredo:
    return Segredo(CHAVE_TESTE, "hub.chave", "teste")
