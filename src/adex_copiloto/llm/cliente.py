"""Cliente direto do GPU Hub (OpenAI-compatível, LiteLLM + vLLM).

Usado em todas as chamadas da biblioteca que não passam por agente (enquadrar,
carta, crítico, narrativa, avaliador...). Os agentes usam o ``ChatQwenHub``
(LangChain), que aplica as mesmas regras.

Garantias:
- ``max_retries=0`` no SDK: a política de nova tentativa é desta classe
  (backoff com variação, respeita ``Retry-After``, nunca retenta 400);
- regra de aceitação fixa (ver ``aceitacao``) com reparo dirigido:
  cortada → mais saída e menos raciocínio; JSON fora do esquema → devolve o erro
  ao modelo uma vez; vazia ou com raciocínio vazado → tenta de novo;
- orçamento conferido antes de cada chamada; uso registrado depois;
- custo lido do cabeçalho ``x-litellm-response-cost`` (parâmetro).
"""

from __future__ import annotations

import base64
import json
import logging
import random
import threading
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from adex_copiloto.config.modelos import Config, PerfilConfig
from adex_copiloto.erros import (
    HubErro,
    HubIndisponivelErro,
    HubRecusouErro,
    RespostaRecusadaErro,
)
from adex_copiloto.llm.aceitacao import Aceite, avaliar, esquema_para_hub, validar_esquema
from adex_copiloto.llm.perfis import limite_saida, montar_requisicao
from adex_copiloto.observabilidade.telemetria import RegistroUso, Telemetria, agora
from adex_copiloto.seguranca.redacao import mascarar_texto
from adex_copiloto.seguranca.segredos import Segredo

log = logging.getLogger("adex_copiloto.llm")

M = TypeVar("M", bound=BaseModel)

MAX_REPAROS = 2
CHARS_POR_TOKEN = 3.2  # estimativa conservadora para português
TOKENS_POR_IMAGEM = 1_600


class RespostaHub(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    texto: str = ""
    raciocinio: str = ""
    finish_reason: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0
    tokens_raciocinio: int = 0
    tokens_cache: int = 0
    custo_usd: float | None = None
    latencia_s: float = 0.0
    modelo: str = ""
    id_resposta: str | None = None
    perfil: str = ""
    tentativas: int = 1
    chamadas_ferramenta: list[dict[str, Any]] = Field(default_factory=list)
    objeto: Any = None
    aceite: str = "ok"


def estimar_tokens(mensagens: list[dict[str, Any]]) -> int:
    total = 0
    for m in mensagens:
        conteudo = m.get("content")
        if isinstance(conteudo, str):
            total += int(len(conteudo) / CHARS_POR_TOKEN) + 4
        elif isinstance(conteudo, list):
            for parte in conteudo:
                if parte.get("type") == "text":
                    total += int(len(parte.get("text", "")) / CHARS_POR_TOKEN)
                elif parte.get("type") == "image_url":
                    total += TOKENS_POR_IMAGEM
        for tc in m.get("tool_calls") or []:
            total += int(len(json.dumps(tc)) / CHARS_POR_TOKEN)
    return total


def mensagem_com_imagem(texto: str, png: bytes) -> dict[str, Any]:
    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {"role": "user", "content": [{"type": "text", "text": texto}, {"type": "image_url", "image_url": {"url": url}}]}


def instrucao_esquema(esquema: type[BaseModel]) -> str:
    return (
        "Responda APENAS com um objeto JSON válido que siga este JSON Schema, sem texto antes ou depois "
        "e sem cercas de código:\n" + json.dumps(esquema_para_hub(esquema), ensure_ascii=False)
    )


def _com_instrucao(mensagens: list[dict[str, Any]], instrucao: str) -> list[dict[str, Any]]:
    saida = [dict(m) for m in mensagens]
    for m in saida:
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            m["content"] = m["content"].rstrip() + "\n\n" + instrucao
            return saida
    return [{"role": "system", "content": instrucao}, *saida]


def criar_http_client(cfg: Config) -> httpx.Client:
    verify: Any = cfg.hub.ca_bundle or True
    return httpx.Client(
        verify=verify,
        proxy=cfg.hub.proxy or None,
        timeout=httpx.Timeout(cfg.hub.timeout_s, connect=cfg.hub.timeout_conexao_s),
        follow_redirects=False,
    )


class ClienteHub:
    def __init__(
        self,
        cfg: Config,
        chave: Segredo,
        telemetria: Telemetria,
        *,
        http_client: httpx.Client | None = None,
        cliente_openai: Any | None = None,
        dormir=time.sleep,
    ) -> None:
        self.cfg = cfg
        self.telemetria = telemetria
        self._dormir = dormir
        self._semaforo = threading.BoundedSemaphore(cfg.hub.paralelismo)
        if cliente_openai is not None:
            self._openai = cliente_openai
        else:
            import openai

            from adex_copiloto.seguranca.segredos import conferir_endpoint_hub

            conferir_endpoint_hub(cfg)
            self._openai = openai.OpenAI(
                base_url=cfg.hub.base_url,
                api_key=chave.revelar(),
                max_retries=0,
                timeout=httpx.Timeout(cfg.hub.timeout_s, connect=cfg.hub.timeout_conexao_s),
                http_client=http_client or criar_http_client(cfg),
            )

    # --- API pública ------------------------------------------------------------------------

    def perfil(self, nome: str) -> PerfilConfig:
        if nome not in self.cfg.perfis:
            raise HubErro(f"Perfil desconhecido: {nome}. Perfis: {', '.join(self.cfg.perfis)}")
        if nome == "visao" and not self.cfg.hub.visao:
            raise HubErro(
                "A leitura de imagens está desligada (hub.visao = false).",
                explicacao="A Fase 0 ainda não confirmou que o hub aceita imagens.",
                como_resolver="Depois do teste C1 da Fase 0, o Admin pode ligar hub.visao.",
            )
        return self.cfg.perfis[nome]

    def completar(
        self,
        perfil: str,
        mensagens: list[dict[str, Any]],
        *,
        etapa: str,
        nome: str,
        esquema: type[M] | None = None,
        max_tokens: int | None = None,
        ferramentas: list[dict[str, Any]] | None = None,
        metadados: dict[str, Any] | None = None,
    ) -> RespostaHub:
        p = self.perfil(perfil)
        hub = self.cfg.hub
        params = montar_requisicao(p, hub, max_tokens=max_tokens)
        msgs = list(mensagens)
        response_format = None
        if esquema is not None:
            if hub.saida_estruturada == "json_schema":
                response_format = {
                    "type": "json_schema",
                    "json_schema": {"name": esquema.__name__[:64], "schema": esquema_para_hub(esquema), "strict": True},
                }
                msgs = _com_instrucao(msgs, "Responda só com o JSON pedido, sem texto em volta.")
            else:
                if hub.saida_estruturada == "json_object":
                    response_format = {"type": "json_object"}
                msgs = _com_instrucao(msgs, instrucao_esquema(esquema))
        entrada_prevista = estimar_tokens(msgs)
        if entrada_prevista > hub.contexto_util:
            raise HubErro(
                f"O pedido tem cerca de {entrada_prevista:,} tokens, acima do contexto útil ({hub.contexto_util:,}).".replace(",", "."),
                explicacao="O pedido não cabe na memória de trabalho do modelo com segurança.",
                como_resolver="O copiloto deve dividir o pedido em lotes; se este erro aparecer, avise o time ADEX.",
            )
        ultimo: Aceite | None = None
        for reparo in range(MAX_REPAROS + 1):
            saida_max = params.get(hub.campo_saida) or limite_saida(p, hub, max_tokens)
            resp = self._chamar(
                perfil, p, msgs, params, response_format, ferramentas,
                etapa=etapa, nome=nome, tentativa=reparo + 1, metadados=metadados,
            )
            aceite = avaliar(
                texto=resp.texto,
                finish_reason=resp.finish_reason,
                tokens_saida=resp.tokens_saida,
                max_tokens=saida_max,
                espera_ferramenta=bool(ferramentas),
                chamou_ferramenta=bool(resp.chamadas_ferramenta),
            )
            if aceite.ok and esquema is not None and not resp.chamadas_ferramenta:
                obj, erro = validar_esquema(resp.texto, esquema)
                if obj is None:
                    aceite = Aceite(False, "esquema_invalido", erro)
                else:
                    resp.objeto = obj
            resp.aceite = aceite.codigo
            resp.tentativas = reparo + 1
            self._registrar_uso(resp, etapa, nome)
            if aceite.ok:
                return resp
            ultimo = aceite
            log.info("resposta recusada (%s): %s", aceite.codigo, aceite.motivo)
            if reparo == MAX_REPAROS:
                break
            # Reparo dirigido conforme o motivo.
            if aceite.codigo in ("truncada", "vazia"):
                atual = params.get(hub.campo_saida, 0)
                if p.raciocinio:
                    novo_teto = hub.saida_max
                    params = montar_requisicao(
                        p, hub, max_tokens=novo_teto, esforco="low",
                        orcamento=max(256, min(p.orcamento_raciocinio or 2048, novo_teto // 4)),
                    )
                elif atual < hub.saida_max:
                    params = montar_requisicao(p, hub, max_tokens=hub.saida_max)
                else:
                    break  # sem raciocínio e já no teto: repetir não ajuda
            elif aceite.codigo == "esquema_invalido":
                msgs = [
                    *msgs,
                    {"role": "assistant", "content": resp.texto[:4000]},
                    {
                        "role": "user",
                        "content": f"Essa resposta não foi aceita: {aceite.motivo}. Responda de novo só com o JSON corrigido.",
                    },
                ]
        raise RespostaRecusadaErro(
            f"O Qwen não deu uma resposta aceitável em '{nome}' ({ultimo.codigo if ultimo else '?'}: "
            f"{ultimo.motivo if ultimo else ''}).",
            detalhes={"etapa": etapa, "nome": nome, "codigo": ultimo.codigo if ultimo else None},
        )

    # --- chamada com novas tentativas de transporte ----------------------------------------

    def _chamar(
        self,
        perfil_nome: str,
        perfil: PerfilConfig,
        msgs: list[dict[str, Any]],
        params: dict[str, Any],
        response_format: dict[str, Any] | None,
        ferramentas: list[dict[str, Any]] | None,
        *,
        etapa: str,
        nome: str,
        tentativa: int,
        metadados: dict[str, Any] | None,
    ) -> RespostaHub:
        import openai

        hub = self.cfg.hub
        saida = params.get(hub.campo_saida, perfil.max_tokens)
        self.telemetria.orcamento.verificar(estimar_tokens(msgs) + int(saida))
        kwargs: dict[str, Any] = {"model": hub.modelo, "messages": msgs, **params}
        if response_format is not None:
            kwargs["response_format"] = response_format
        if ferramentas:
            kwargs["tools"] = ferramentas
            kwargs["parallel_tool_calls"] = False
        timeout = perfil.timeout_s or hub.timeout_s
        parametros_visiveis = {k: v for k, v in kwargs.items() if k not in ("messages", "tools")}
        with self.telemetria.observar(
            nome, tipo="generation", etapa=etapa, entrada=msgs, modelo=hub.modelo,
            parametros=parametros_visiveis, metadados={"perfil": perfil_nome, "tentativa": tentativa, **(metadados or {})},
        ) as obs:
            erro_final: Exception | None = None
            for transporte in range(1, hub.tentativas + 1):
                inicio = time.perf_counter()
                try:
                    with self._semaforo:
                        bruto = self._openai.chat.completions.with_raw_response.create(timeout=timeout, **kwargs)
                    resposta = bruto.parse()
                    cabecalhos = getattr(bruto, "headers", {}) or {}
                    resp = self._converter(resposta, cabecalhos, perfil_nome, time.perf_counter() - inicio)
                    obs.uso = {
                        "input": resp.tokens_entrada,
                        "output": resp.tokens_saida,
                        "reasoning": resp.tokens_raciocinio,
                        "cache_read_input_tokens": resp.tokens_cache,
                    }
                    if resp.custo_usd is not None:
                        obs.custo = {"total": resp.custo_usd}
                    obs.finalizar(
                        saida={"texto": resp.texto, "ferramentas": resp.chamadas_ferramenta} if resp.chamadas_ferramenta else resp.texto,
                        finish_reason=resp.finish_reason,
                        transporte=transporte,
                        tokens_raciocinio=resp.tokens_raciocinio,
                    )
                    return resp
                except openai.APIStatusError as e:
                    status = e.status_code
                    corpo = mascarar_texto(str(getattr(e, "message", "") or e))[:400]
                    if status in (400, 404, 413, 422):
                        obs.finalizar(nivel="ERROR", status=f"HTTP {status}")
                        raise HubRecusouErro(
                            f"O hub recusou o pedido (HTTP {status}): {corpo}",
                            detalhes={"status": status, "perfil": perfil_nome},
                        ) from e
                    if status in (401, 403):
                        obs.finalizar(nivel="ERROR", status=f"HTTP {status}")
                        raise HubRecusouErro(
                            f"O hub negou acesso (HTTP {status}).",
                            explicacao="A chave não tem permissão para este modelo, ou foi revogada.",
                            como_resolver="Rode `adex-segredo smoke hub.chave`; se falhar, peça uma chave nova ao CCOE.",
                        ) from e
                    erro_final = e
                    espera = self._espera(transporte, getattr(e, "response", None))
                except (openai.APITimeoutError, openai.APIConnectionError) as e:
                    erro_final = e
                    espera = self._espera(transporte, None)
                if transporte < hub.tentativas:
                    log.info("hub indisponível (tentativa %s/%s); aguardando %.1fs", transporte, hub.tentativas, espera)
                    self._dormir(espera)
            obs.finalizar(nivel="ERROR", status=type(erro_final).__name__ if erro_final else "falha")
            raise HubIndisponivelErro(
                f"O hub não respondeu depois de {hub.tentativas} tentativa(s) ({type(erro_final).__name__}).",
                detalhes={"perfil": perfil_nome, "etapa": etapa},
            ) from erro_final

    def _espera(self, tentativa: int, resposta: Any) -> float:
        hub = self.cfg.hub
        if resposta is not None:
            try:
                valor = resposta.headers.get("retry-after")
                if valor:
                    return min(hub.espera_max_s, max(0.5, float(valor)))
            except (AttributeError, ValueError):
                pass
        base = min(hub.espera_max_s, hub.espera_base_s * (2 ** (tentativa - 1)))
        return base * (0.5 + random.random() / 2)

    def _converter(self, resposta: Any, cabecalhos: Any, perfil: str, latencia: float) -> RespostaHub:
        escolha = resposta.choices[0] if getattr(resposta, "choices", None) else None
        if escolha is None:
            raise HubErro("O hub devolveu uma resposta sem 'choices'.")
        msg = escolha.message
        extras = getattr(msg, "model_extra", None) or {}
        raciocinio = getattr(msg, "reasoning_content", None) or extras.get("reasoning_content") or extras.get("reasoning") or ""
        chamadas = []
        for tc in getattr(msg, "tool_calls", None) or []:
            chamadas.append(
                {
                    "id": tc.id or f"call_{random.getrandbits(64):016x}",
                    "nome": tc.function.name,
                    "argumentos": tc.function.arguments,
                }
            )
        uso = getattr(resposta, "usage", None)
        detalhes_saida = getattr(uso, "completion_tokens_details", None) if uso else None
        detalhes_entrada = getattr(uso, "prompt_tokens_details", None) if uso else None
        custo = None
        nome_cab = self.cfg.hub.cabecalho_custo
        try:
            bruto = cabecalhos.get(nome_cab) if hasattr(cabecalhos, "get") else None
            custo = float(bruto) if bruto not in (None, "") else None
        except (TypeError, ValueError):
            custo = None
        return RespostaHub(
            texto=msg.content or "",
            raciocinio=raciocinio,
            finish_reason=escolha.finish_reason,
            tokens_entrada=int(getattr(uso, "prompt_tokens", 0) or 0) if uso else 0,
            tokens_saida=int(getattr(uso, "completion_tokens", 0) or 0) if uso else 0,
            tokens_raciocinio=int(getattr(detalhes_saida, "reasoning_tokens", 0) or 0) if detalhes_saida else 0,
            tokens_cache=int(getattr(detalhes_entrada, "cached_tokens", 0) or 0) if detalhes_entrada else 0,
            custo_usd=custo,
            latencia_s=round(latencia, 3),
            modelo=getattr(resposta, "model", "") or "",
            id_resposta=getattr(resposta, "id", None),
            perfil=perfil,
            chamadas_ferramenta=chamadas,
        )

    def _registrar_uso(self, resp: RespostaHub, etapa: str, nome: str) -> None:
        self.telemetria.registrar_uso(
            RegistroUso(
                em=agora().isoformat(),
                etapa=etapa,
                nome=nome,
                perfil=resp.perfil,
                modelo=resp.modelo or self.cfg.hub.modelo,
                tokens_entrada=resp.tokens_entrada,
                tokens_saida=resp.tokens_saida,
                tokens_raciocinio=resp.tokens_raciocinio,
                tokens_cache=resp.tokens_cache,
                custo_usd=resp.custo_usd,
                latencia_s=resp.latencia_s,
                aceite=resp.aceite,
                tentativa=resp.tentativas,
            )
        )
