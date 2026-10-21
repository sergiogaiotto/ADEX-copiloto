"""LLM como juiz com o próprio Qwen (uso amplo, amostragem definida pelo Admin).

Avalia textos do copiloto (narrativa, resumo do crítico) contra os critérios de
``langfuse.avaliacao.criterios`` e registra as notas como scores no Langfuse (ou
na fila local, sem rota). O juiz é uma chamada separada (perfil 'critico').
"""

from __future__ import annotations

import random

from pydantic import BaseModel, Field

from adex_copiloto.config.modelos import Config
from adex_copiloto.estudo.carta import Carta
from adex_copiloto.llm import prompts
from adex_copiloto.llm.marcadores import TabelaMarcadores


class NotaCriterio(BaseModel):
    criterio: str = Field(max_length=40)
    nota: int = Field(ge=1, le=5)
    justificativa: str = Field(max_length=400)


class RespostaJuiz(BaseModel):
    notas: list[NotaCriterio] = Field(min_length=1, max_length=8)


def deve_avaliar(cfg: Config, rng: random.Random | None = None) -> bool:
    av = cfg.langfuse.avaliacao
    if not av.habilitada or av.amostragem <= 0:
        return False
    return (rng or random).random() < av.amostragem


def avaliar_texto(cliente, telemetria, cfg: Config, *, texto_marcado: str, marcadores: TabelaMarcadores, carta: Carta,
                  nome: str = "narrativa") -> list[NotaCriterio]:
    criterios = cfg.langfuse.avaliacao.criterios
    corpo = prompts.dados({"carta": carta.para_llm(), "numeros": marcadores.para_llm(), "texto": texto_marcado}, "texto_avaliado")
    msgs = prompts.mensagens("avaliar", corpo, criterios=", ".join(criterios))
    resp = cliente.completar(cfg.langfuse.avaliacao.perfil, msgs, etapa="avaliacao", nome=f"juiz:{nome}", esquema=RespostaJuiz)
    notas = [n for n in resp.objeto.notas if n.criterio in criterios]
    for n in notas:
        telemetria.score(f"juiz_{n.criterio}", n.nota, tipo="NUMERIC", comentario=n.justificativa)
    return notas
