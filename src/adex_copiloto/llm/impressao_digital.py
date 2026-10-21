"""Impressão digital do modelo: detecta troca do que está por trás de 'qwen-latest'.

'qwen-latest' é um apelido do LiteLLM e pode passar a apontar para outro modelo
sem aviso. A lib calcula uma impressão a partir do que o hub informa (lista de
modelos e o campo ``model`` de uma resposta mínima) e compara com a impressão
homologada pelo Admin (``hub.modelo_impressao``). Política em
``fingerprint.on_change`` (Admin): ``bloquear`` ou ``avisar``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

from adex_copiloto.config.modelos import Config
from adex_copiloto.erros import HubIndisponivelErro, HubRecusouErro, ModeloTrocadoErro
from adex_copiloto.seguranca.segredos import Segredo

_CAMPOS_VOLATEIS = {"created", "object"}


@dataclass
class SituacaoModelo:
    impressao: str
    homologada: str
    situacao: str  # confere | divergente | sem_homologacao
    detalhes: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.situacao == "confere"

    def texto(self) -> str:
        if self.situacao == "confere":
            return "Modelo confere com a homologação."
        if self.situacao == "sem_homologacao":
            return (
                f"Modelo sem impressão homologada (atual: {self.impressao[:16]}…). Peça ao Admin para gravar "
                "hub.modelo_impressao."
            )
        return f"O modelo mudou: impressão atual {self.impressao[:16]}…, homologada {self.homologada[:16]}…"


def consultar_modelos(cfg: Config, chave: Segredo, *, http_client: httpx.Client | None = None) -> dict[str, Any]:
    from adex_copiloto.llm.cliente import criar_http_client
    from adex_copiloto.seguranca.segredos import conferir_endpoint_hub

    conferir_endpoint_hub(cfg)
    cliente = http_client or criar_http_client(cfg)
    try:
        r = cliente.get(
            cfg.hub.base_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {chave.revelar()}"},
            timeout=httpx.Timeout(min(30.0, cfg.hub.timeout_s), connect=cfg.hub.timeout_conexao_s),
        )
    except httpx.HTTPError as e:
        raise HubIndisponivelErro(f"Não foi possível listar os modelos do hub ({type(e).__name__}).") from e
    if r.status_code in (401, 403):
        raise HubRecusouErro(f"O hub negou acesso à lista de modelos (HTTP {r.status_code}).")
    if r.status_code >= 400:
        raise HubIndisponivelErro(f"O hub respondeu HTTP {r.status_code} ao listar modelos.")
    return r.json()


def calcular(info_modelos: dict[str, Any], modelo: str, modelo_resposta: str | None = None) -> tuple[str, dict[str, Any]]:
    entrada = next((m for m in info_modelos.get("data", []) if m.get("id") == modelo), None)
    estavel = {k: v for k, v in (entrada or {}).items() if k not in _CAMPOS_VOLATEIS}
    detalhes = {"lista": estavel, "modelo_resposta": modelo_resposta or ""}
    bruto = json.dumps(detalhes, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest(), detalhes


def verificar(cfg: Config, impressao: str, detalhes: dict[str, Any]) -> SituacaoModelo:
    homologada = (cfg.hub.modelo_impressao or "").strip().lower()
    if not homologada:
        return SituacaoModelo(impressao, "", "sem_homologacao", detalhes)
    if impressao == homologada:
        return SituacaoModelo(impressao, homologada, "confere", detalhes)
    situacao = SituacaoModelo(impressao, homologada, "divergente", detalhes)
    if cfg.fingerprint.on_change == "bloquear":
        raise ModeloTrocadoErro(situacao.texto(), detalhes={"atual": impressao, "homologada": homologada})
    return situacao
