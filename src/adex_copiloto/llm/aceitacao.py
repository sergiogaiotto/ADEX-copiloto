"""Regra de aceitação da resposta do Qwen (fixa, não é parâmetro).

Uma resposta só é usada se:
- ``finish_reason`` é ``stop`` (ou ``tool_calls`` quando se espera ferramenta);
- não foi cortada: ``completion_tokens < max_tokens`` (o vLLM pode dizer
  ``tool_calls`` mesmo cortado — issue vLLM #61206 — então o teto conta sempre);
- não está vazia;
- não traz marcas de raciocínio vazadas (``<think>``/``</think>``);
- quando se pede JSON, o JSON é válido e obedece ao esquema.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)

_MARCAS_RACIOCINIO = ("<think>", "</think>")


@dataclass(frozen=True)
class Aceite:
    ok: bool
    codigo: str = "ok"
    motivo: str = ""

    @classmethod
    def aceita(cls) -> "Aceite":
        return cls(True)


def avaliar(
    *,
    texto: str,
    finish_reason: str | None,
    tokens_saida: int | None,
    max_tokens: int,
    espera_ferramenta: bool = False,
    chamou_ferramenta: bool = False,
) -> Aceite:
    if finish_reason == "length" or (tokens_saida is not None and tokens_saida >= max_tokens):
        return Aceite(False, "truncada", f"a resposta foi cortada no teto de {max_tokens} tokens")
    if chamou_ferramenta:
        if not espera_ferramenta:
            return Aceite(False, "ferramenta_inesperada", "o modelo chamou ferramenta onde não havia ferramenta")
        if finish_reason not in ("tool_calls", "stop"):
            return Aceite(False, "finish_inesperado", f"finish_reason={finish_reason!r}")
        return Aceite.aceita()
    if finish_reason != "stop":
        return Aceite(False, "finish_inesperado", f"finish_reason={finish_reason!r} (esperado 'stop')")
    if not (texto or "").strip():
        return Aceite(False, "vazia", "a resposta veio vazia (o raciocínio pode ter consumido toda a saída)")
    if any(m in texto for m in _MARCAS_RACIOCINIO):
        return Aceite(False, "vazamento_raciocinio", "a resposta trouxe marcas de raciocínio (<think>)")
    return Aceite.aceita()


_CERCA = re.compile(r"^```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def extrair_json(texto: str) -> Any:
    """Extrai o JSON da resposta: aceita cerca ```json``` e texto em volta curto."""
    bruto = (texto or "").strip()
    m = _CERCA.match(bruto)
    if m:
        bruto = m.group(1).strip()
    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        pass
    # Último recurso: do primeiro { ou [ até o último } ou ] correspondente.
    inicio = min((i for i in (bruto.find("{"), bruto.find("[")) if i >= 0), default=-1)
    if inicio < 0:
        raise ValueError("não há JSON na resposta")
    fim = max(bruto.rfind("}"), bruto.rfind("]"))
    if fim <= inicio:
        raise ValueError("JSON incompleto na resposta")
    return json.loads(bruto[inicio : fim + 1])


def validar_esquema(texto: str, esquema: type[M]) -> tuple[M | None, str]:
    """Devolve (objeto, "") ou (None, motivo legível)."""
    try:
        dados = extrair_json(texto)
    except (ValueError, json.JSONDecodeError) as e:
        return None, f"JSON inválido: {e}"
    try:
        return esquema.model_validate(dados), ""
    except ValidationError as e:
        partes = []
        for err in e.errors()[:8]:
            caminho = ".".join(str(x) for x in err["loc"]) or "(raiz)"
            partes.append(f"{caminho}: {err['msg']}")
        return None, "JSON fora do esquema: " + "; ".join(partes)


def esquema_para_hub(esquema: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema do pydantic com $ref resolvidos (mais compatível com o vLLM)."""
    bruto = esquema.model_json_schema()
    defs = bruto.pop("$defs", {})

    def resolver(no: Any, pilha: tuple[str, ...] = ()) -> Any:
        if isinstance(no, dict):
            if "$ref" in no:
                nome = no["$ref"].split("/")[-1]
                if nome in pilha:  # recursão: deixa aberto
                    return {"type": "object"}
                alvo = dict(defs.get(nome, {}))
                extras = {k: v for k, v in no.items() if k != "$ref"}
                alvo.update(extras)
                return resolver(alvo, pilha + (nome,))
            return {k: resolver(v, pilha) for k, v in no.items()}
        if isinstance(no, list):
            return [resolver(v, pilha) for v in no]
        return no

    return resolver(bruto)
