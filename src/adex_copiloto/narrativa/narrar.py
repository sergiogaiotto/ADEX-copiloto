"""Narrativa: a IA escreve o texto, o Python põe os números.

1. O Qwen (perfil 'narrar') escreve usando só marcadores ⟦M-xx⟧.
2. O Python valida: marcador inexistente ou número solto -> recusa e pede de novo
   (com o motivo); depois de ``tentativas``, usa um texto-modelo sem IA.
3. O Python preenche os marcadores com os números formatados (pt-BR).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from adex_copiloto.erros import AdexErro
from adex_copiloto.estudo.carta import Carta
from adex_copiloto.estudo.evidencia import Afirmacao
from adex_copiloto.llm import prompts
from adex_copiloto.llm.marcadores import TabelaMarcadores, numeros_soltos, preencher, validar


class RespostaNarrativa(BaseModel):
    texto: str = Field(max_length=4000)


@dataclass
class Narrativa:
    texto_marcado: str
    texto: str
    html: str
    origem: str  # ia | modelo
    tentativas: int
    problemas: list[str]


def numeros_permitidos(carta: Carta, textos_python: list[str]) -> set[str]:
    """Números que aparecem no texto do dev ou em textos do Python (datas, rótulos)."""
    fontes = [carta.valor(c) for c in carta.campos] + list(textos_python)
    return {n for t in fontes for n in numeros_soltos(t or "")}


def texto_modelo(afirmacoes: list[Afirmacao]) -> str:
    """Texto sem IA: as próprias afirmações do Python, agrupadas por tipo."""
    partes = []
    for tipo, titulo in (("fato", "O que os dados mostram"), ("hipotese", "O que ainda é hipótese"), ("recomendacao", "Recomendações")):
        itens = [a for a in afirmacoes if a.tipo == tipo]
        if itens:
            partes.append(titulo + ":\n" + "\n".join(f"- {a.texto} (selo {a.selo})" for a in itens))
    return "\n\n".join(partes) or "Ainda não há resultados para narrar."


def narrar(
    cliente,
    *,
    carta: Carta,
    marcadores: TabelaMarcadores,
    afirmacoes: list[Afirmacao],
    textos_python: list[str] | None = None,
    rotulos: list[str] | None = None,
    tamanho: str = "até 3 parágrafos curtos",
    tentativas: int = 2,
    etapa: str = "narrativa",
) -> Narrativa:
    permitidos = numeros_permitidos(carta, textos_python or [])
    rotulos = rotulos or []
    corpo = prompts.dados(
        {
            "carta": carta.para_llm(),
            "numeros_disponiveis": marcadores.para_llm(),
            "afirmacoes": [{"id": a.id, "tipo": a.tipo, "texto": a.texto, "selo": a.selo} for a in afirmacoes],
        },
        "estudo",
    )
    msgs = prompts.mensagens("narrar", corpo, rotulos=", ".join(rotulos) or "(nenhum)", tamanho=tamanho)
    problemas: list[str] = []
    for i in range(tentativas):
        try:
            resp = cliente.completar("narrar", msgs, etapa=etapa, nome="narrativa", esquema=RespostaNarrativa)
        except AdexErro as e:
            problemas.append(f"IA indisponível: {e.mensagem[:200]}")
            break
        texto = resp.objeto.texto.strip()
        erros = validar(texto, marcadores, rotulos, permitidos)
        if not erros:
            return Narrativa(texto, preencher(texto, marcadores), preencher(texto, marcadores, html=True), "ia", i + 1, problemas)
        problemas.extend(erros)
        msgs = [*msgs, {"role": "assistant", "content": texto[:3000]},
                {"role": "user", "content": "Texto recusado: " + "; ".join(erros) + ". Reescreva usando SÓ os marcadores para números."}]
    modelo = texto_modelo(afirmacoes)
    return Narrativa(modelo, preencher(modelo, marcadores), preencher(modelo, marcadores, html=True), "modelo", tentativas, problemas)
