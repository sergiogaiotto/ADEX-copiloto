"""Crítico com o Qwen: uma chamada separada de quem fez o trabalho (perfil 'critico').

A IA recebe carta, plano, resumos (só marcadores), descrição dos números e os
achados do Python. Ela pode acrescentar achados e propor mudar severidades, mas
não remove achados do Python. Números soltos nos textos da IA são retirados.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from adex_copiloto.critico.checagens import Achado, EntradaCritica
from adex_copiloto.estudo.evidencia import Registro
from adex_copiloto.llm import prompts
from adex_copiloto.llm.marcadores import TabelaMarcadores, numeros_soltos


class AchadoProposto(BaseModel):
    severidade: Literal["bloqueio", "alerta", "nota"]
    texto: str = Field(max_length=600)
    refere: str = Field(default="", max_length=80)
    o_que_fazer: str = Field(default="", max_length=400)


class RespostaCritico(BaseModel):
    achados: list[AchadoProposto] = Field(default_factory=list, max_length=10)
    resumo: str = Field(default="", max_length=600)


def _sem_numeros_soltos(texto: str, permitidos: set[str]) -> tuple[str, bool]:
    soltos = [n for n in numeros_soltos(texto) if n not in permitidos]
    if not soltos:
        return texto, False
    limpo = texto
    for n in sorted(set(soltos), key=len, reverse=True):
        limpo = re.sub(rf"(?<![\w⟦-]){re.escape(n)}(?![\w⟧])", "[número removido]", limpo)
    return limpo, True


def montar_entrada_ia(e: EntradaCritica, marcadores: TabelaMarcadores, achados_python: list[Achado]) -> str:
    plano = None
    if e.plano is not None:
        plano = [{"passo": p.id, "bloco": f"{p.bloco}@{p.versao}", "parametros": p.parametros, "status": p.status} for p in e.plano.passos]
    resumos = {passo: {"status": r.get("status"), "resumo": r.get("resumo"), "avisos": r.get("avisos", [])[:5]}
               for passo, r in e.resultados.items()}
    afirmacoes = [{"id": a.id, "tipo": a.tipo, "texto": a.texto, "selo": a.selo} for a in e.afirmacoes]
    corpo = {
        "carta": e.carta.para_llm(),
        "plano": plano,
        "resultados": resumos,
        "numeros": marcadores.para_llm(),
        "afirmacoes": afirmacoes,
        "decisoes": [{"id": d.id, "titulo": d.titulo, "status": d.status, "escolha": d.escolha} for d in e.decisoes],
        "lacunas": [{"id": l.id, "descricao": l.descricao, "status": l.status} for l in e.lacunas],
        "passaporte": e.passaporte.model_dump(include={"metodo", "riscos_vies", "limitacoes", "cobertura"}) if e.passaporte else None,
        "achados_do_python": [{"id": a.id, "severidade": a.severidade, "texto": a.texto} for a in achados_python],
    }
    return prompts.dados(corpo, "estudo")


def criticar(cliente, e: EntradaCritica, marcadores: TabelaMarcadores, achados_python: list[Achado], ids: Registro,
             *, etapa: str = "critico") -> tuple[list[Achado], str]:
    """Devolve (achados da IA, resumo da IA). Falha da IA não derruba o estudo: devolve vazio."""
    msgs = prompts.mensagens("critico", montar_entrada_ia(e, marcadores, achados_python))
    resp = cliente.completar("critico", msgs, etapa=etapa, nome="critico", esquema=RespostaCritico)
    obj: RespostaCritico = resp.objeto
    permitidos = {m for t in [e.carta.valor(c) for c in e.carta.campos] for m in numeros_soltos(t)}
    saida = []
    existentes = {a.texto.strip().lower() for a in achados_python}
    for p in obj.achados:
        if p.texto.strip().lower() in existentes:
            continue
        texto, tirou = _sem_numeros_soltos(p.texto, permitidos)
        fazer, _ = _sem_numeros_soltos(p.o_que_fazer, permitidos)
        saida.append(Achado(id=ids.proximo("C"), severidade=p.severidade, texto=texto + (" (números da IA removidos)" if tirou else ""),
                            refere=p.refere, o_que_fazer=fazer, origem="ia", regra="critico_ia", aceitavel=True))
    resumo, _ = _sem_numeros_soltos(obj.resumo, permitidos)
    return saida, resumo
