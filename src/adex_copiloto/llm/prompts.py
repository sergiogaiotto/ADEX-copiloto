"""Prompts do copiloto, guardados em ``recursos/prompts/*.md``.

Modelos usam ``{{nome}}``, para não confundir com chaves de JSON. Conteúdo que
vem de fora (texto do dev, metadados de tabelas, saída de ferramentas) entra
sempre embrulhado por :func:`dados`, e o prompt de sistema diz que o que está ali
dentro é dado, não instrução.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any

_VAR = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


@lru_cache(maxsize=64)
def _bruto(prompt: str, /) -> str:
    return resources.files("adex_copiloto.recursos").joinpath("prompts", f"{prompt}.md").read_text("utf-8")


def carregar(prompt: str, /, **variaveis: Any) -> str:
    texto = _bruto(prompt)
    faltam = sorted({v for v in _VAR.findall(texto) if v not in variaveis})
    if faltam:
        raise KeyError(f"prompt {prompt}: faltam variáveis {faltam}")

    def troca(m: re.Match[str]) -> str:
        valor = variaveis[m.group(1)]
        if isinstance(valor, (dict, list)):
            return json.dumps(valor, ensure_ascii=False, indent=1)
        return str(valor)

    return _VAR.sub(troca, texto)


def dados(conteudo: Any, origem: str) -> str:
    """Embrulha conteúdo não confiável. Remove tentativas de fechar o envelope."""
    if not isinstance(conteudo, str):
        conteudo = json.dumps(conteudo, ensure_ascii=False, indent=1, default=str)
    conteudo = conteudo.replace("</dados_nao_confiaveis", "</dados_nao_confiaveis_")
    return f'<dados_nao_confiaveis origem="{origem}">\n{conteudo}\n</dados_nao_confiaveis>'


def sistema(tarefa: str, /, **variaveis: Any) -> str:
    return carregar("sistema") + "\n\n" + carregar(tarefa, **variaveis)


def mensagens(tarefa: str, usuario: str, /, **variaveis: Any) -> list[dict[str, str]]:
    return [{"role": "system", "content": sistema(tarefa, **variaveis)}, {"role": "user", "content": usuario}]
