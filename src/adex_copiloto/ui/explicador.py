"""Explicador de erros no notebook.

- Erros do copiloto (``AdexErro``) já trazem explicação e próximo passo: mostrados
  sem chamar a IA.
- Outros erros: com ``geral.explicar_erros``, o Qwen (perfil 'rapido') explica a
  partir do TIPO, da mensagem MASCARADA e das linhas de código — nunca de valores
  de variáveis.
"""

from __future__ import annotations

import traceback

from pydantic import BaseModel, Field

from adex_copiloto.erros import AdexErro
from adex_copiloto.seguranca.redacao import mascarar_texto
from adex_copiloto.ui import html as H
from adex_copiloto.ui.paineis import Painel


class Explicacao(BaseModel):
    o_que_houve: str = Field(max_length=500)
    causa_provavel: str = Field(max_length=500)
    como_resolver: str = Field(max_length=600)


def material_do_erro(exc: BaseException, limite_linhas: int = 6) -> dict:
    tb = traceback.extract_tb(exc.__traceback__)[-limite_linhas:]
    return {
        "tipo": type(exc).__name__,
        "mensagem": mascarar_texto(str(exc))[:800],
        "linhas": [{"arquivo": f.filename.split("/")[-1].split("\\")[-1], "linha": f.lineno, "codigo": mascarar_texto(f.line or "")} for f in tb],
    }


def explicar(exc: BaseException, cliente=None) -> Painel:
    if isinstance(exc, AdexErro):
        return Painel(H.cartao_erro(exc))
    material = material_do_erro(exc)
    if cliente is None:
        corpo = f"<p><code>{H.e(material['tipo'])}</code>: {H.e(material['mensagem'])}</p>"
        return Painel(H.cartao("Erro no Python", "sem explicação da IA", corpo))
    from adex_copiloto.llm import prompts

    try:
        msgs = prompts.mensagens("explicar_erro", prompts.dados(material, "erro"))
        r = cliente.completar("rapido", msgs, etapa="explicar_erro", nome="explicar_erro", esquema=Explicacao).objeto
        corpo = (f"<p>{H.e(r.o_que_houve)}</p><p class='sub'>Causa provável: {H.e(r.causa_provavel)}</p>"
                 + H.caixa(f"<b>Como resolver:</b> {H.e(r.como_resolver)}") + f"<p class='sub'><code>{H.e(material['tipo'])}</code></p>")
        return Painel(H.cartao("Erro explicado", "pelo Qwen, sem valores de variáveis", corpo))
    except AdexErro:
        return Painel(H.cartao("Erro no Python", "a IA não respondeu", f"<p><code>{H.e(material['tipo'])}</code>: {H.e(material['mensagem'])}</p>"))


def instalar(ipython, cliente=None) -> None:  # pragma: no cover - depende do IPython
    """Mostra o cartão explicado no lugar do traceback cru (o traceback continua disponível com %tb)."""
    from IPython.display import display

    def tratar(shell, etype, evalue, tb, tb_offset=None):
        shell.showtraceback((etype, evalue, tb), tb_offset=tb_offset)
        try:
            display(explicar(evalue, cliente))
        except Exception:  # noqa: BLE001
            pass
        return None

    ipython.set_custom_exc((Exception,), tratar)


def explicar_ultimo(shell, cliente=None) -> Painel:  # pragma: no cover - depende do IPython
    import sys

    exc = getattr(sys, "last_value", None)
    if exc is None:
        return Painel(H.cartao("Nada a explicar", "", "<p>Nenhum erro recente.</p>"))
    return explicar(exc, cliente)
